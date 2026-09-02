#!/usr/bin/env python3
"""
weekly_review.py - pokreće se svaki dan (jednom, ujutro), ali stvarno šalje
poruku samo u dva moguća trenutka:

  - PONEDJELJAK ujutro, AKO tog dana više nema neodigranih utakmica
    trenutnog gameweeka (npr. Monday Night Football) - tad se čeka.
  - UTORAK ujutro - uvijek šalje, bez obzira je li sve odigrano
    (sigurnosna mreža za slučaj odgođenih utakmica).

Svaka odgođena/neodigrana utakmica se jasno navede u poruci, umjesto da
bot čeka unedogled (utakmica se može odgoditi i za mjesece).

Sadržaj: pregled prošlog kola + konsenzus analitičara (iz članaka/videa
prikupljenih tijekom tjedna) + personalizirani AI prijedlog za tvoju ekipu
(jači model - Sonnet) + pregled idućeg kola + grafikon ranga/vrijednosti.

NAPOMENA o poznatom ograničenju: FPL-ov javni API pokazuje tvoju postavu
onakvu kakva je bila zaključana za zadnji prošli rok, ne transfere napravljene
nakon tog roka. Ako si nedavno mijenjao/la ekipu, koristi chat i pošalji
screenshot trenutne postave za točniju sliku.
"""

import os
import sys
import datetime as dt

from common import (
    esc, fetch_fpl, current_and_next_event, upcoming_fixtures_by_team,
    avg_difficulty, score_players, fetch_team_squad, send_telegram,
    send_telegram_photo, load_state, save_state, anthropic_call, SONNET_MODEL, POS_LABEL,
)
from chart import render_rank_value_chart


def get_current_event(bootstrap):
    return next((e for e in bootstrap["events"] if e.get("is_current")), None)


def unplayed_fixtures_for_event(fixtures, event_id):
    return [f for f in fixtures if f.get("event") == event_id and not f.get("finished")]


def should_send_today(bootstrap, fixtures, state, today_utc):
    current = get_current_event(bootstrap)
    if not current:
        return None
    if state.get("last_weekly_gw_sent", 0) >= current["id"]:
        return None  # već poslano za ovo kolo

    weekday = today_utc.weekday()  # Monday = 0
    unplayed = unplayed_fixtures_for_event(fixtures, current["id"])
    today_str = today_utc.date().isoformat()
    todays_unplayed = [f for f in unplayed if (f.get("kickoff_time") or "").startswith(today_str)]

    if weekday == 0:  # ponedjeljak
        if todays_unplayed:
            print("Ponedjeljak, ali još ima utakmica danas (npr. MNF) - čekam do utorka.")
            return None
        return current, unplayed
    elif weekday == 1:  # utorak - sigurnosna mreža, šalje uvijek
        return current, unplayed
    else:
        return None


def build_consensus_and_advice(api_key, weekly_summaries, squad_data, players, teams_by_id):
    if not weekly_summaries:
        consensus_text = "Nije prikupljeno dovoljno članaka/videa ovaj tjedan za konsenzus pregled."
    else:
        joined = "\n\n".join(
            f"[{s['source']}] {s['title']}: {s['summary']}" for s in weekly_summaries[-25:]
        )
        prompt = (
            "Ovo su sažetci članaka i videa FPL analitičara prikupljeni tijekom tjedna:\n\n"
            f"{joined}\n\n"
            "Napravi kratak KONSENZUS pregled na hrvatskom u ovom formatu:\n"
            "1. Prvo jedan red zbirnog konsenzusa za kapetana ako se većina slaže (npr. 'X od Y izvora "
            "spominje [igrač] kao top kapetan izbor').\n"
            "2. Zatim za svaki izvor koji se izjašnjava o kapetanu/transferima, jedan kratak red: "
            "'[Izvor]: [njihov stav i ukratko zašto]'.\n"
            "Budi sažet, bez uvoda, samo suština. Ako izvori nemaju jasan zajednički stav, to jasno reci."
        )
        try:
            consensus_text = anthropic_call(
                api_key,
                "Sažimaš FPL analize na hrvatskom, vlastitim riječima, bez doslovnog prepisivanja.",
                prompt, model=SONNET_MODEL, max_tokens=500,
            )
        except Exception as e:
            consensus_text = f"(Konsenzus nije uspio: {e})"

    advice_text = None
    if squad_data and api_key:
        starters = [s for s in squad_data["squad"] if s["position"] <= 11]
        owned_names = ", ".join(s["player"]["web_name"] for s in squad_data["squad"] if s["player"])
        squad_desc = "; ".join(
            f"{s['player']['web_name']} ({s['player']['_pos']}, forma {s['player']['_form']:.1f}, "
            f"FDR {s['player']['_fdr']:.1f})" for s in starters
        )
        summaries_short = "\n".join(f"[{s['source']}] {s['summary']}" for s in weekly_summaries[-15:])
        prompt = (
            f"Moja trenutna postava (11): {squad_desc}\n\n"
            f"SVI igrači koje već posjedujem (15-orica, uključujući klupu): {owned_names}\n\n"
            f"Mišljenja analitičara ovaj tjedan:\n{summaries_short}\n\n"
            "Na temelju MOJE postave, FPL podataka o formi/fixtureima, i mišljenja analitičara, "
            "daj mi 2-4 konkretna prijedloga za idući gameweek na hrvatskom "
            "(transferi, kapetan, i eventualno chip ako je trenutak za to). "
            "VAŽNO: nikad ne predlaži kao 'novi transfer' igrača kojeg već posjedujem (vidi listu iznad) - "
            "to bi bio besmislen prijedlog. Ako predlažeš transfer, predloži RAZLIČITOG igrača za svaku "
            "poziciju koju mijenjaš, nikad istog igrača kao zamjenu na dva mjesta. "
            "Budi konkretan i kratak - liste, ne duga proza. Objasni ukratko 'zašto' za svaki prijedlog."
        )
        try:
            advice_text = anthropic_call(
                api_key,
                "Ti si FPL savjetnik koji daje kratke, konkretne, dobro obrazložene prijedloge na hrvatskom.",
                prompt, model=SONNET_MODEL, max_tokens=500,
            )
        except Exception as e:
            advice_text = f"(Prijedlog nije uspio: {e})"

    return consensus_text, advice_text


def build_message(bootstrap, fixtures, current_event, unplayed, api_key, weekly_summaries):
    fx_map = upcoming_fixtures_by_team(fixtures)
    players = score_players(bootstrap, fx_map)
    players_by_id = {p["id"]: p for p in players}
    teams_by_id = {t["id"]: t for t in bootstrap["teams"]}

    lines = [f"<b>📊 TJEDNI PREGLED — {esc(current_event['name'])}</b>", ""]

    if unplayed:
        lines.append("<b>⚠️ Napomena:</b> sljedeće utakmice ovog kola još nisu odigrane (odgođene ili u tijeku):")
        for f in unplayed:
            h = teams_by_id.get(f["team_h"], {}).get("short_name", "?")
            a = teams_by_id.get(f["team_a"], {}).get("short_name", "?")
            lines.append(f"• {h} - {a}")
        lines.append("Pregled ispod je temeljen na podacima dostupnim do sad.")
        lines.append("")

    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    squad_data = None
    if team_id:
        try:
            squad_data = fetch_team_squad(int(team_id), bootstrap, players_by_id)
            for s in squad_data["squad"]:
                s["player"].update(players_by_id.get(s["player"]["id"], {}))
        except Exception as e:
            print(f"[upozorenje] Ne mogu dohvatiti tim: {e}", file=sys.stderr)

    consensus_text, advice_text = build_consensus_and_advice(api_key, weekly_summaries, squad_data, players, teams_by_id)

    lines.append("<b>🗣️ KONSENZUS ANALITIČARA</b>")
    lines.append(consensus_text)
    lines.append("")

    if advice_text:
        lines.append("<b>🎯 PRIJEDLOG ZA TVOJU EKIPU</b>")
        lines.append(advice_text)
        lines.append("")

    _, nxt = current_and_next_event(bootstrap)
    if nxt:
        rows = sorted([(t["short_name"], avg_difficulty(t["id"], fx_map, 3)) for t in bootstrap["teams"]],
                       key=lambda r: r[1])
        lines.append(f"<b>📅 PREGLED IDUĆEG KOLA — {esc(nxt['name'])}</b>")
        lines.append("Najlakši fixturei: " + ", ".join(f"{esc(n)} ({f:.1f})" for n, f in rows[:6]))
        lines.append("")

    lines.append("<i>Konsenzus i prijedlozi su AI sažetak javno dostupnih analiza, ne financijski savjet. "
                  "Postava odražava zadnji zaključani rok - ako si nedavno mijenjao/la tim, pošalji botu "
                  "screenshot za točniju sliku.</i>")

    return "\n".join(lines)


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not bot_token or not chat_id:
        print("GREŠKA: nedostaju TELEGRAM_BOT_TOKEN i/ili TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    print("Dohvaćam FPL podatke...")
    bootstrap, fixtures = fetch_fpl()

    today_utc = dt.datetime.now(dt.timezone.utc)
    result = should_send_today(bootstrap, fixtures, state, today_utc)
    if not result:
        print("Danas se ne šalje tjedni pregled (nije pon/uto, ili je već poslano, ili se čeka MNF).")
        return

    current_event, unplayed = result
    weekly_summaries = state.get("weekly_summaries", [])

    print("Sastavljam tjedni pregled (ovo poziva AI, može potrajati malo dulje)...")
    message = build_message(bootstrap, fixtures, current_event, unplayed, api_key, weekly_summaries)

    print("Šaljem na Telegram...")
    send_telegram(message, bot_token, chat_id)

    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if team_id:
        try:
            fx_map = upcoming_fixtures_by_team(fixtures)
            players = score_players(bootstrap, fx_map)
            players_by_id = {p["id"]: p for p in players}
            squad_data = fetch_team_squad(int(team_id), bootstrap, players_by_id)
            if squad_data:
                info = squad_data["info"]
                history = state.get("history", [])
                history = [h for h in history if h["gw"] != current_event["id"]]
                history.append({
                    "gw": current_event["id"],
                    "rank": info.get("summary_overall_rank") or 0,
                    "value": (info.get("last_deadline_value") or 1000) / 10,
                    "points": info.get("summary_overall_points") or 0,
                })
                history.sort(key=lambda h: h["gw"])
                state["history"] = history[-20:]

                chart_bytes = render_rank_value_chart(state["history"])
                if chart_bytes:
                    send_telegram_photo(bot_token, chat_id, chart_bytes,
                                         caption="📈 Tvoj rang i vrijednost tima kroz sezonu")
        except Exception as e:
            print(f"[upozorenje] Grafikon ranga nije uspio: {e}", file=sys.stderr)

    state["last_weekly_gw_sent"] = current_event["id"]
    state["weekly_summaries"] = []  # reset za idući tjedan
    save_state(state)
    print("Gotovo.")


if __name__ == "__main__":
    main()
