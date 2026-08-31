#!/usr/bin/env python3
"""
chat_bot.py - obrađuje JEDNU Telegram poruku po pokretanju, proslijeđenu
izravno kroz environment varijable (MSG_CHAT_ID, MSG_TEXT, MSG_PHOTO_FILE_ID,
MSG_CAPTION). Te varijable postavlja GitHub Actions iz repository_dispatch
eventa koji šalje Cloudflare Worker čim Telegram webhook primi novu poruku -
zato ovaj skript NE zove getUpdates (Telegram ne dopušta webhook i getUpdates
istovremeno na istom botu).

Podržava i SLIKE - ako korisnik pošalje screenshot svoje FPL momčadi (npr.
prije sezone, dok API još ne otkriva tim preko Team ID-a), AI "pročita"
igrače sa slike i da ocjenu/preporuke koristeći dostupne podatke o formi
i rasporedu.

VAŽNO ZA SIGURNOST: bot odgovara SAMO ako MSG_CHAT_ID odgovara
TELEGRAM_CHAT_ID secretu - inače bi netko drugi mogao trošiti tvoj
Anthropic API budžet (dodatna zaštita uz onu koju već radi Cloudflare Worker
provjerom tajnog tokena).
"""

import os
import sys
import base64

from common import (
    fetch_fpl, upcoming_fixtures_by_team, score_players, fetch_team_squad,
    send_telegram, get_telegram_file_bytes, anthropic_call, SONNET_MODEL,
    load_state, save_state,
)

SYSTEM_PROMPT = (
    "Ti si FPL (Fantasy Premier League) asistent na hrvatskom jeziku. Odgovaraj kratko, "
    "konkretno i korisno, koristeći dostupni kontekst. Drži odgovor unutar otprilike "
    "120-180 riječi - budi sažet radije nego da nabrajaš sve moguće opcije. Ako nešto ne "
    "znaš iz konteksta, iskreno reci da nemaš taj podatak umjesto da izmišljaš.\n\n"
    "Ako korisnik pošalje SLIKU svoje FPL momčadi (npr. screenshot iz aplikacije), pažljivo "
    "pročitaj imena igrača i formaciju sa slike, prepoznaj kapetana (obično oznaka 'C') i "
    "vice-kapetana ('VC'), te daj kratku ocjenu momčadi i 2-3 konkretna prijedloga "
    "(transfer, kapetan, ili formacija) koristeći podatke o formi i rasporedu iz konteksta."
)


def build_context(bootstrap, fixtures, weekly_summaries):
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

    return (
        f"TOP IGRAČI PO POZICIJI (forma+ICT+fixture formula):\n"
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
    context = build_context(bootstrap, fixtures, weekly_summaries)

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


if __name__ == "__main__":
    main()
