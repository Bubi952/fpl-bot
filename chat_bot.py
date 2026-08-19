#!/usr/bin/env python3
"""
chat_bot.py - pokreće se često (npr. svakih 5 min) da "osluškuje" nove
Telegram poruke i odgovara na pitanja uživo (npr. "Haaland ili Salah za
kapetana?"). Koristi svježe FPL podatke + tvoju ekipu (ako je postavljen
FPL_TEAM_ID) + sažetke prikupljene ovaj tjedan kao kontekst za AI (Sonnet).

VAŽNO ZA SIGURNOST: bot odgovara SAMO na poruke iz chata čiji ID odgovara
TELEGRAM_CHAT_ID - inače bi netko drugi tko sazna korisničko ime tvog bota
mogao trošiti tvoj Anthropic API budžet slanjem poruka.
"""

import os
import sys

from common import (
    fetch_fpl, upcoming_fixtures_by_team, score_players, fetch_team_squad,
    send_telegram, get_telegram_updates, load_state, save_state,
    anthropic_call, SONNET_MODEL,
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
    squad_text = "Korisnik nije povezao svoj FPL tim."
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
        f"TVOJA EKIPA:\n{squad_text}\n\n"
        f"NEDAVNA MIŠLJENJA ANALITIČARA:\n{recent_opinions}"
    )


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

    questions = []
    max_update_id = last_update_id
    for u in results:
        max_update_id = max(max_update_id, u["update_id"])
        msg = u.get("message") or u.get("edited_message")
        if not msg or "text" not in msg:
            continue
        incoming_chat_id = str(msg["chat"]["id"])
        if incoming_chat_id != str(chat_id):
            print(f"[sigurnost] Ignoriram poruku iz neovlaštenog chata {incoming_chat_id}.")
            continue
        questions.append(msg["text"])

    state["last_telegram_update_id"] = max_update_id
    save_state(state)

    if not questions:
        print("Nema pitanja od ovlaštenog korisnika.")
        return

    print(f"Dohvaćam FPL podatke za {len(questions)} pitanje/a...")
    bootstrap, fixtures = fetch_fpl()
    weekly_summaries = state.get("weekly_summaries", [])
    context = build_context(bootstrap, fixtures, weekly_summaries)

    for question in questions:
        prompt = f"KONTEKST:\n{context}\n\nPITANJE KORISNIKA: {question}"
        try:
            answer = anthropic_call(
                api_key,
                "Ti si FPL (Fantasy Premier League) asistent na hrvatskom jeziku. Odgovaraj kratko, "
                "konkretno i korisno, koristeći dostupni kontekst. Drži odgovor unutar otprilike "
                "120-150 riječi - budi sažet radije nego da nabrajaš sve moguće opcije. Ako nešto ne "
                "znaš iz konteksta, iskreno reci da nemaš taj podatak umjesto da izmišljaš.",
                prompt, model=SONNET_MODEL, max_tokens=700,
            )
        except Exception as e:
            answer = f"Ups, nešto je pošlo po zlu: {e}"
        send_telegram(answer, bot_token, chat_id)
        print(f"Odgovoreno na: {question[:60]}")


if __name__ == "__main__":
    main()
