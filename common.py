"""
common.py - dijeljene funkcije za sve dijelove FPL bota.
Koristi samo Python standardnu biblioteku (bez vanjskih paketa) osim gdje
je izričito naznačeno (youtube_transcript_api u video_sources.py).
"""

import os
import sys
import json
import html
import datetime as dt
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

FPL_API = "https://fantasy.premierleague.com/api/"
UA = "Mozilla/5.0 (compatible; FPLDailyReportBot/2.0)"
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

POS_LABEL = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-5"


# --------------------------------------------------------------------------
# Niska razina - HTTP
# --------------------------------------------------------------------------

def http_get_json(url, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_get_text(url, headers=None):
    h = {"User-Agent": UA}
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=25) as resp:
        raw = resp.read()
        return raw.decode("utf-8", errors="replace")


def http_post_json(url, payload, headers=None):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers=h, method="POST")
    with urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------
# FPL API
# --------------------------------------------------------------------------

def fetch_fpl():
    bootstrap = http_get_json(FPL_API + "bootstrap-static/")
    fixtures = http_get_json(FPL_API + "fixtures/")
    return bootstrap, fixtures


def build_team_maps(bootstrap):
    return {t["id"]: t for t in bootstrap["teams"]}


def current_and_next_event(bootstrap):
    events = bootstrap["events"]
    current = next((e for e in events if e.get("is_current")), None)
    nxt = next((e for e in events if e.get("is_next")), None)
    return current, nxt or current


def upcoming_fixtures_by_team(fixtures):
    m = {}
    future = [f for f in fixtures if not f.get("finished") and f.get("event")]
    future.sort(key=lambda f: f["event"])
    for f in future:
        m.setdefault(f["team_h"], []).append(
            {"event": f["event"], "opp": f["team_a"], "home": True, "diff": f["team_h_difficulty"]}
        )
        m.setdefault(f["team_a"], []).append(
            {"event": f["event"], "opp": f["team_h"], "home": False, "diff": f["team_a_difficulty"]}
        )
    return m


def avg_difficulty(team_id, fx_map, n=3):
    fx = fx_map.get(team_id, [])[:n]
    if not fx:
        return 3.0
    return sum(f["diff"] for f in fx) / len(fx)


def score_players(bootstrap, fx_map):
    teams = build_team_maps(bootstrap)
    scored = []
    for p in bootstrap["elements"]:
        try:
            form = float(p.get("form") or 0)
        except ValueError:
            form = 0.0
        try:
            ppg = float(p.get("points_per_game") or 0)
        except ValueError:
            ppg = 0.0
        ict = float(p.get("ict_index") or 0)
        price = p["now_cost"] / 10
        fdr = avg_difficulty(p["team"], fx_map, 3)
        fixture_factor = (6 - fdr) / 5
        available = p.get("status") == "a"
        raw_score = (form * 2 + ict / 10 + ppg) * fixture_factor
        p2 = dict(p)
        p2["_form"] = form
        p2["_price"] = price
        p2["_fdr"] = fdr
        p2["_score"] = raw_score if available else raw_score * 0.15
        p2["_available"] = available
        p2["_pos"] = POS_LABEL.get(p["element_type"], "?")
        p2["_team"] = teams.get(p["team"], {}).get("short_name", "?")
        scored.append(p2)
    return scored


def fetch_team_squad(team_id, bootstrap, players_by_id):
    current, _ = current_and_next_event(bootstrap)
    gw_candidates = []
    if current:
        gw_candidates.append(current["id"])
    for e in bootstrap["events"]:
        if e.get("is_next") and e["id"] not in gw_candidates:
            gw_candidates.append(e["id"])
    if not gw_candidates:
        gw_candidates = [1]
    picks = None
    for gw in gw_candidates:
        try:
            picks = http_get_json(FPL_API + f"entry/{team_id}/event/{gw}/picks/")
            break
        except (URLError, HTTPError):
            continue
    if not picks:
        return None
    info = http_get_json(FPL_API + f"entry/{team_id}/")
    squad = []
    for pk in picks["picks"]:
        player = players_by_id.get(pk["element"])
        if player:
            squad.append({**pk, "player": player})
    return {"info": info, "squad": squad}


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def esc(s):
    return html.escape(str(s), quote=False)


def send_telegram(text, bot_token, chat_id):
    max_len = 3800
    chunks = []
    current = ""
    for block in text.split("\n\n"):
        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for chunk in chunks:
        try:
            http_post_json(url, {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        except (URLError, HTTPError) as e:
            print(f"[greška] Slanje na Telegram nije uspjelo: {e}", file=sys.stderr)
            raise


def get_telegram_updates(bot_token, offset=None, timeout=0):
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates?timeout={timeout}"
    if offset is not None:
        url += f"&offset={offset}"
    return http_get_json(url)


# --------------------------------------------------------------------------
# Anthropic API
# --------------------------------------------------------------------------

PARAPHRASE_SYSTEM = (
    "Ti si asistent koji sažima FPL (Fantasy Premier League) sadržaj na hrvatskom jeziku. "
    "PRAVILA: piši isključivo vlastitim riječima, nikad ne prepisuj rečenice doslovno iz izvora "
    "(nijedan citat duži od 15 riječi, u praksi radije nemoj citirati uopće). "
    "Budi kratak, konkretan i koristi FPL terminologiju (gameweek, kapetan, transfer, chip). "
    "Ne izmišljaj podatke koji nisu u tekstu - ako nešto nije jasno, preskoči to."
)


def anthropic_call(api_key, system, user_content, model=HAIKU_MODEL, max_tokens=300):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_content}],
    }
    data = http_post_json(url, payload, headers=headers)
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(parts).strip()


def summarize_text(api_key, source_name, title, text, max_chars=6000):
    text = text[:max_chars]
    prompt = (
        f"Izvor: {source_name}\nNaslov: {title}\n\nTekst:\n{text}\n\n"
        "Ako tekst iznad NE sadrži stvaran sadržaj članka (npr. samo izbornik, kolačiće, "
        "navigaciju, reklame ili slično 'smeće' sa stranice), odgovori TOČNO ovom jednom riječi: "
        "NEMA_SADRZAJA - ništa drugo, bez objašnjenja.\n\n"
        "Inače, sažmi članak u 2-3 rečenice na hrvatskom, fokusiran na FPL relevantne informacije "
        "(igrači, transferi, ozljede, preporuke, kapetan). Piši prirodnim tonom, bez uvoda "
        "tipa 'ovaj članak govori o...' - idi ravno na suštinu."
    )
    result = anthropic_call(api_key, PARAPHRASE_SYSTEM, prompt, model=HAIKU_MODEL, max_tokens=200)
    if "NEMA_SADRZAJA" in result.upper():
        return None
    return result


# --------------------------------------------------------------------------
# Trajno stanje (state.json, commita se natrag u repo u GitHub Actionu)
# --------------------------------------------------------------------------

DEFAULT_STATE = {
    "seen_video_ids": [],
    "seen_article_urls": [],
    "weekly_summaries": [],
    "last_weekly_gw_sent": 0,
    "last_telegram_update_id": 0,
}


def load_state():
    if not os.path.exists(STATE_PATH):
        return dict(DEFAULT_STATE)
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_STATE)
        merged.update(data)
        return merged
    except Exception:
        return dict(DEFAULT_STATE)


def save_state(state):
    # drži liste razumne veličine da state.json ne raste unedogled
    state["seen_video_ids"] = state.get("seen_video_ids", [])[-500:]
    state["seen_article_urls"] = state.get("seen_article_urls", [])[-500:]
    state["weekly_summaries"] = state.get("weekly_summaries", [])[-200:]
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
