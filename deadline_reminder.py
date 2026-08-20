#!/usr/bin/env python3
"""
deadline_reminder.py - pokreće se često (npr. svakih 30 min, uz chat provjeru).
Šalje JEDNU podsjetničku poruku otprilike 3 sata prije svakog gameweek roka -
stanje tima (bank, upitni igrači), da ne propustiš napraviti transfer na
vrijeme. Pamti u state.json koji je rok već obrađen da ne šalje duplo.
"""

import os
import sys
import datetime as dt

from common import (
    esc, fetch_fpl, upcoming_fixtures_by_team, score_players,
    fetch_team_squad, send_telegram, load_state, save_state,
)

REMINDER_WINDOW_HOURS = 3


def get_next_event(bootstrap):
    return next((e for e in bootstrap["events"] if e.get("is_next")), None)


def parse_deadline(iso_str):
    try:
        return dt.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def build_reminder_message(bootstrap, fixtures, next_event, hours_left):
    fx_map = upcoming_fixtures_by_team(fixtures)
    players = score_players(bootstrap, fx_map)
    players_by_id = {p["id"]: p for p in players}

    lines = [
        f"<b>⏰ ROK ZA {esc(next_event['name'].upper())} JE ZA {hours_left:.1f}h!</b>",
        "Zadnja prilika za transfere, kapetana i chipove prije zaključavanja.",
        "",
    ]

    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if team_id:
        try:
            squad_data = fetch_team_squad(int(team_id), bootstrap, players_by_id)
        except Exception as e:
            squad_data = None
            print(f"[upozorenje] Ne mogu dohvatiti tim: {e}", file=sys.stderr)
        if squad_data:
            info = squad_data["info"]
            bank = info.get("last_deadline_bank", 0) / 10
            lines.append(f"💰 Bank: £{bank:.1f}m")
            problems = [
                s["player"] for s in squad_data["squad"]
                if s["player"] and (s["player"].get("news") or s["player"].get("status") != "a")
            ]
            if problems:
                lines.append("⚠️ Upitni igrači u tvojoj ekipi:")
                for p in problems:
                    chance = p.get("chance_of_playing_next_round")
                    chance_s = f"{chance}%" if chance is not None else "?"
                    lines.append(f"• {esc(p['web_name'])} — {chance_s}" + (f" — {esc(p['news'])}" if p.get("news") else ""))
            else:
                lines.append("✅ Nema upitnih igrača u tvojoj ekipi.")
        else:
            lines.append("(Tim još nije dostupan preko API-ja - vjerojatno prije prvog roka sezone.)")
    else:
        lines.append("Poveži FPL_TEAM_ID da bot ovdje prikaže stanje tvoje ekipe i bank.")

    return "\n".join(lines)


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("GREŠKA: nedostaju TELEGRAM_BOT_TOKEN i/ili TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    print("Dohvaćam FPL podatke...")
    bootstrap, fixtures = fetch_fpl()

    next_event = get_next_event(bootstrap)
    if not next_event:
        print("Nema nadolazećeg gameweeka (sezona možda još nije počela ili je gotova).")
        return

    deadline = parse_deadline(next_event.get("deadline_time", ""))
    if not deadline:
        print("Ne mogu pročitati deadline_time.")
        return

    now = dt.datetime.now(dt.timezone.utc)
    hours_left = (deadline - now).total_seconds() / 3600

    if not (0 < hours_left <= REMINDER_WINDOW_HOURS):
        print(f"Van prozora za podsjetnik (do roka: {hours_left:.1f}h) - ne šaljem.")
        return

    if state.get("last_deadline_reminder_event") == next_event["id"]:
        print("Podsjetnik za ovaj rok je već poslan.")
        return

    print("Šaljem podsjetnik za rok...")
    message = build_reminder_message(bootstrap, fixtures, next_event, hours_left)
    send_telegram(message, bot_token, chat_id)

    state["last_deadline_reminder_event"] = next_event["id"]
    save_state(state)
    print("Gotovo.")


if __name__ == "__main__":
    main()
