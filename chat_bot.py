#!/usr/bin/env python3
"""
chat_bot.py - obrađuje JEDNU Telegram poruku po pokretanju, proslijeđenu
izravno kroz environment varijable (MSG_CHAT_ID, MSG_TEXT, MSG_PHOTO_FILE_ID,
MSG_CAPTION). Te varijable postavlja GitHub Actions iz repository_dispatch
eventa koji šalje Cloudflare Worker čim Telegram webhook primi novu poruku -
zato ovaj skript NE zove getUpdates (Telegram ne dopušta webhook i getUpdates
istovremeno na istom botu).

Podržava i SLIKE - ako korisnik pošalje screenshot svoje FPL momčadi, AI
"pročita" igrače sa slike i da ocjenu/preporuke.

Bot također PRETRAŽUJE CIJELU BAZU IGRAČA (ne samo top 5 po poziciji) za bilo
koje ime spomenuto u pitanju, tako da može odgovoriti i o manje istaknutim
igračima, ne samo o formulom-favoriziranim top izborima.

VAŽNO ZA SIGURNOST: bot odgovara SAMO ako MSG_CHAT_ID odgovara
TELEGRAM_CHAT_ID secretu.
"""

import os
import re
import sys
import base64

from common import (
    fetch_fpl, upcoming_fixtures_by_team, score_players, fetch_team_squad,
    send_telegram, get_telegram_file_bytes, anthropic_call, SONNET_MODEL,
    load_state, save_state, build_team_maps,
)

SYSTEM_PROMPT = (
    "Ti si FPL (Fantasy Premier League) asistent na hrvatskom jeziku. Odgovaraj kratko, "
    "konkretno i korisno, koristeći dostupni kontekst. Drži odgovor unutar otprilike "
    "120-180 riječi - budi sažet radije nego da nabrajaš sve moguće opcije. Ako igrač kojeg "
    "korisnik pita NIJE u sekciji 'PRONAĐENI IGRAČI IZ PITANJA' niti u ostalom kontekstu, "
    "iskreno reci da ga ne prepoznaješ u trenutnim FPL podacima umjesto da nagađaš ili izmišljaš "
    "informacije o njemu.\n\n"
    "Ako korisnik pošalje SLIKU svoje FPL momčadi (npr. screenshot iz aplikacije), pažljivo "
    "pročitaj imena igrača i formaciju sa slike, prepoznaj kapetana (obično oznaka 'C') i "
    "vice-kapetana ('VC'), te daj kratku ocjenu momčadi i 2-3 konkretna prijedloga "
    "(transfer, kapetan, ili formacija) koristeći podatke o formi i rasporedu iz konteksta."
)

_WORD_RE = re.compile(r"[A-Za-zÀ-žĐđŠšČčĆćŽž]{3,}")


def find_mentioned_players(text, bootstrap, fx_map):
    """Pretraži CIJELU bazu igrača (ne samo top 5 po poziciji) za bilo koje
    ime spomenuto u pitanju - tako bot može odgovoriti i o manje poznatim
    igračima, ne samo formulom-favoriziranim top izborima."""
    if not text:
        return []
    words = {w.lower() for w in _WORD_RE.findall(text)}
    if not words:
        return []

    teams = build_team_maps(bootstrap)
    matches = []
    seen_ids = set()
    for p in bootstrap["elements"]:
        candidates = [p.get("web_name", ""), p.get("second_name", ""), p.get("first_name", "")]
        for name in candidates:
            if name and name.lower() in words and p["id"] not in seen_ids:
                matches.append(p)
                seen_ids.add(p["id"])
                break
    return matches[:6]


def format_player_detail(p, bootstrap, fx_map):
    teams = build_team_maps(bootstrap)
    team_name = teams.get(p["team"], {}).get("short_name", "?")
    chance = p.get("chance_of_playing_next_round")
    chance_s = f"{chance}%" if chance is not None else "nepoznato (pretpostavi 100% ako nema vijesti)"
    news = p.get("news") or "nema posebnih vijesti/ozljeda"
    fx = fx_map.get(p["team"], [])[:3]
    fx_str = ", ".join(f"vs opp{f['opp']}({'D' if f['home'] else 'G'}, FDR{f['diff']})" for f in fx) or "nepoznato"
    return (
        f"- {p.get('web_name')} ({p.get('first_name','')} {p.get('second_name','')}), "
        f"klub: {team_name}, pozicija: {p.get('element_type')}, cijena: £{p['now_cost']/10:.1f}m, "
        f"forma: {p.get('form','0')}, ukupno bodova: {p.get('total_points',0)}, "
        f"status dostupnosti: {p.get('status','a')}, šansa igranja: {chance_s}, vijesti: {news}, "
        f"iduće utakmice (FDR): {fx_str}"
    )


def build_context(bootstrap, fixtures, weekly_summaries, question_text=""):
    fx_map = upcoming_fixtures_by_team(fixtures)
    players = score_players(bootstrap, fx_map)
    players_by_id = {p["id"]: p for p in players}

    top_by_pos = []
    for et in (1, 2, 3, 4):
        pool = sorted([p for p in players if p["element_type"] == et and p["_available"]],
                      key=lambda p: p["_score"], reverse=True)[:5]
        top_by_pos.append(", ".join(f"{p['web_name']} (£{p['_price']:.1f}m, forma {p['_form']:.1f}, FDR {p['_fdr']:.1f})" for p in pool))

    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    squad_text = "Korisnik nije povezao svoj FPL tim (ili sezona još nije počela pa API ne otkriva postavu)."
    if team_id:
        try:
            squad_data = fetch_team_squad(int(team_id), bootstrap, players_by_id)
            if squad_data:
                starters = [s for s in squad_data["squad"] if s["position"] <= 11]
                bench = [s for s in squad_data["squad"] if s["position"] > 11]
                squad_text = "Postava: " + ", ".join(
                    f"{s['player']['web_name']}{'(C)' if s.get('is_captain') else ''}" for s in starters
                )
                squad_text += "\nKlupa: " + ", ".join(s["player"]["web_name"] for s in bench)
                squad_text += f"\nBank: £{squad_data['info'].get('last_deadline_bank', 0) / 10:.1f}m"
        except Exception as e:
            print(f"[upozorenje] Ne mogu dohvatiti tim: {e}", file=sys.stderr)

    recent_opinions = "\n".join(
        f"[{s['source']}] {s['summary']}" for s in weekly_summaries[-15:]
    ) or "Nema nedavno prikupljenih članaka/videa."

    # NOVO: pretraži cijelu bazu za bilo koje ime spomenuto u pitanju
    mentioned = find_mentioned_players(question_text, bootstrap, fx_map)
    mentioned_text = "Nijedan igrač iz pitanja nije prepoznat u bazi."
    if mentioned:
        mentioned_text = "\n".join(format_player_detail(p, bootstrap, fx_map) for p in mentioned)

    return (
        f"PRONAĐENI IGRAČI IZ PITANJA (pretraga cijele FPL baze, ~600+ igrača):\n{mentioned_text}\n\n"
        f"TOP IGRAČI PO POZICIJI (forma+ICT+fixture formula, samo 5 po poziciji):\n"
        f"GK: {top_by_pos[0]}\nDEF: {top_by_pos[1]}\nMID: {top_by_pos[2]}\nFWD: {top_by_pos[3]}\n\n"
        f"TVOJA EKIPA (preko FPL API-ja, ako je dostupno):\n{squad_text}\n\n"
        f"NEDAVNA MIŠLJENJA ANALITIČARA:\n{recent_opinions}"
    )


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not bot_token or not chat_id or not api_key:
        print("GREŠKA: nedostaje TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID ili ANTHROPIC_API_KEY.", file=sys.stderr)
        sys.exit(1)

    msg_chat_id = os.environ.get("MSG_CHAT_ID", "").strip()
    msg_text = os.environ.get("MSG_TEXT", "").strip()
    msg_photo_file_id = os.environ.get("MSG_PHOTO_FILE_ID", "").strip()
    msg_caption = os.environ.get("MSG_CAPTION", "").strip()

    if not msg_chat_id:
        print("Nema poruke za obraditi (MSG_CHAT_ID prazan) - vjerojatno ručno pokretanje bez inputa.")
        return

    if msg_chat_id != str(chat_id):
        print(f"[sigurnost] Ignoriram poruku iz neovlaštenog chata {msg_chat_id}.")
        return

    if not msg_text and not msg_photo_file_id:
        print("Poruka nema ni tekst ni sliku - ništa za odgovoriti.")
        return

    print("Dohvaćam FPL podatke...")
    bootstrap, fixtures = fetch_fpl()
    state = load_state()
    weekly_summaries = state.get("weekly_summaries", [])

    question_for_lookup = msg_text or msg_caption
    context = build_context(bootstrap, fixtures, weekly_summaries, question_for_lookup)

    if msg_photo_file_id:
        try:
            img_bytes, mime = get_telegram_file_bytes(bot_token, msg_photo_file_id)
            b64_data = base64.b64encode(img_bytes).decode("utf-8")
            user_prompt = msg_caption or (
                "Ovo je screenshot moje FPL momčadi. Pročitaj igrače sa slike, "
                "daj mi ocjenu momčadi i konkretne prijedloge za poboljšanje."
            )
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64_data}},
                {"type": "text", "text": f"KONTEKST:\n{context}\n\n{user_prompt}"},
            ]
            answer = anthropic_call(api_key, SYSTEM_PROMPT, content, model=SONNET_MODEL, max_tokens=800)
        except Exception as e:
            answer = f"Ups, ne mogu analizirati sliku: {e}"
        print("Odgovaram na: [slika]")
    else:
        prompt = f"KONTEKST:\n{context}\n\nPITANJE KORISNIKA: {msg_text}"
        try:
            answer = anthropic_call(api_key, SYSTEM_PROMPT, prompt, model=SONNET_MODEL, max_tokens=700)
        except Exception as e:
            answer = f"Ups, nešto je pošlo po zlu: {e}"
        print(f"Odgovaram na: {msg_text[:60]}")

    send_telegram(answer, bot_token, chat_id)
    print("Gotovo.")


if __name__ == "__main__":
    main()
