#!/usr/bin/env python3
"""
chat_bot.py - pokreće se često (npr. svakih 5 min) da "osluškuje" nove
Telegram poruke i odgovara na pitanja uživo (npr. "Haaland ili Salah za
kapetana?"). Koristi svježe FPL podatke + tvoju ekipu (ako je postavljen
FPL_TEAM_ID) + sažetke prikupljene ovaj tjedan kao kontekst za AI (Sonnet).

Podržava i SLIKE - ako pošalješ botu screenshot svoje FPL momčadi (npr.
prije sezone, dok API još ne otkriva tim preko Team ID-a), AI "pročita"
igrače sa slike i da ocjenu/preporuke koristeći dostupne podatke o formi
i rasporedu. Dodaj poruku (caption) uz sliku za konkretnije pitanje, ili
je pošalji bez teksta za opću analizu.

VAŽNO ZA SIGURNOST: bot odgovara SAMO na poruke iz chata čiji ID odgovara
TELEGRAM_CHAT_ID - inače bi netko drugi tko sazna korisničko ime tvog bota
mogao trošiti tvoj Anthropic API budžet slanjem poruka/slika.
"""

import os
import sys
import base64

from common import (
    fetch_fpl, upcoming_fixtures_by_team, score_players, fetch_team_squad,
    send_telegram, get_telegram_updates, get_telegram_file_bytes,
    load_state, save_state, anthropic_call, SONNET_MODEL,
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


def collect_items(results, chat_id):
    """Vrati listu {'type': 'text', 'text': ...} ili {'type': 'photo', 'file_id': ..., 'caption': ...}
    samo za poruke iz ovlaštenog chata, i najveći update_id obrađen."""
    items = []
    max_update_id = 0
    for u in results:
        max_update_id = max(max_update_id, u["update_id"])
        msg = u.get("message") or u.get("edited_message")
        if not msg:
            continue
        incoming_chat_id = str(msg["chat"]["id"])
        if incoming_chat_id != str(chat_id):
            print(f"[sigurnost] Ignoriram poruku iz neovlaštenog chata {incoming_chat_id}.")
            continue
        if "photo" in msg and msg["photo"]:
            largest = msg["photo"][-1]  # Telegram šalje uzlazno po veličini
            items.append({"type": "photo", "file_id": largest["file_id"], "caption": msg.get("caption", "")})
        elif "text" in msg:
            items.append({"type": "text", "text": msg["text"]})
    return items, max_update_id


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not bot_token or not chat_id or not api_key:
        print("GREŠKA: nedostaje TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID ili ANTHROPIC_API_KEY.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    last_update_id = state.get("last_telegram_update_id", 0)

    updates = get_telegram_updates(bot_token, offset=last_update_id + 1 if last_update_id else None)
    results = updates.get("result", [])
    if not results:
        print("Nema novih poruka.")
        return

    items, max_update_id = collect_items(results, chat_id)
    state["last_telegram_update_id"] = max(last_update_id, max_update_id)
    save_state(state)

    if not items:
        print("Nema poruka od ovlaštenog korisnika.")
        return

    print(f"Dohvaćam FPL podatke za {len(items)} poruku/a...")
    bootstrap, fixtures = fetch_fpl()
    weekly_summaries = state.get("weekly_summaries", [])
    context = build_context(bootstrap, fixtures, weekly_summaries)

    for item in items:
        if item["type"] == "photo":
            try:
                img_bytes, mime = get_telegram_file_bytes(bot_token, item["file_id"])
                b64_data = base64.b64encode(img_bytes).decode("utf-8")
                user_prompt = item["caption"] or (
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
            print("Odgovoreno na: [slika]")
        else:
            prompt = f"KONTEKST:\n{context}\n\nPITANJE KORISNIKA: {item['text']}"
            try:
                answer = anthropic_call(api_key, SYSTEM_PROMPT, prompt, model=SONNET_MODEL, max_tokens=700)
            except Exception as e:
                answer = f"Ups, nešto je pošlo po zlu: {e}"
            print(f"Odgovoreno na: {item['text'][:60]}")
        send_telegram(answer, bot_token, chat_id)


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
