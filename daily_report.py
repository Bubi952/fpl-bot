#!/usr/bin/env python3
"""
daily_report.py - pokreće se jednom dnevno ujutro.
Puni pregled: ozljede, cijene, forma, preporuke po poziciji, raspored,
dvostruki/prazni gameweekovi, chip savjeti, i (ako je postavljen
FPL_TEAM_ID) osnovna analiza tvoje ekipe s heurističkim prijedlozima
transfera (bez AI poziva - besplatno, temelji se samo na FPL podacima).
"""

import os
import sys
import datetime as dt

from common import (
    esc, fetch_fpl, build_team_maps, current_and_next_event,
    upcoming_fixtures_by_team, avg_difficulty, score_players,
    fetch_team_squad, send_telegram, POS_LABEL,
)


def build_message(bootstrap, fixtures):
    fx_map = upcoming_fixtures_by_team(fixtures)
    players = score_players(bootstrap, fx_map)
    players_by_id = {p["id"]: p for p in players}
    current, nxt = current_and_next_event(bootstrap)

    lines = []
    lines.append(f"<b>⚽ FPL DNEVNI IZVJEŠTAJ</b> — {dt.date.today().strftime('%d.%m.%Y.')}")
    if nxt:
        lines.append(f"<i>Rok za {esc(nxt['name'])}: {esc(nxt.get('deadline_time', '?'))}</i>")
    lines.append("")

    injuries = sorted([p for p in players if p.get("news")],
                       key=lambda p: float(p.get("selected_by_percent") or 0), reverse=True)[:8]
    lines.append("<b>🚑 OZLJEDE / UPITNI</b>")
    if not injuries:
        lines.append("Nema aktualnih upozorenja.")
    for p in injuries:
        chance = p.get("chance_of_playing_next_round")
        chance_s = f"{chance}%" if chance is not None else "?"
        lines.append(f"• <b>{esc(p['web_name'])}</b> ({esc(p['_team'])}) — {chance_s} — {esc(p['news'])}")
    lines.append("")

    risers = sorted([p for p in players if p.get("cost_change_event", 0) > 0],
                     key=lambda p: p["cost_change_event"], reverse=True)[:5]
    fallers = sorted([p for p in players if p.get("cost_change_event", 0) < 0],
                      key=lambda p: p["cost_change_event"])[:5]
    lines.append("<b>💰 PROMJENE CIJENA</b>")
    if risers:
        lines.append("Poskupili: " + ", ".join(f"{esc(p['web_name'])} (£{p['_price']:.1f}m)" for p in risers))
    if fallers:
        lines.append("Pojeftinili: " + ", ".join(f"{esc(p['web_name'])} (£{p['_price']:.1f}m)" for p in fallers))
    if not risers and not fallers:
        lines.append("Nema promjena cijena danas.")
    lines.append("")

    in_form = sorted([p for p in players if p["_available"]], key=lambda p: p["_form"], reverse=True)[:6]
    lines.append("<b>🔥 U FORMI</b>")
    for p in in_form:
        lines.append(f"• {esc(p['web_name'])} ({esc(p['_team'])}) — forma {p['_form']:.1f}")
    lines.append("")

    lines.append("<b>⭐ PREPORUKE PO POZICIJI</b> <i>(forma + ICT + fixturei — naša formula)</i>")
    for et, label in POS_LABEL.items():
        pool = sorted([p for p in players if p["element_type"] == et and p["_available"]],
                      key=lambda p: p["_score"], reverse=True)[:3]
        names = ", ".join(f"{esc(p['web_name'])} (£{p['_price']:.1f}m)" for p in pool)
        lines.append(f"{label}: {names}")
    lines.append("")

    rows = sorted([(t["short_name"], avg_difficulty(t["id"], fx_map, 5)) for t in bootstrap["teams"]],
                   key=lambda r: r[1])
    lines.append("<b>📅 NAJLAKŠI RASPORED (iduće 5 kola)</b>")
    lines.append(", ".join(f"{esc(n)} ({f:.1f})" for n, f in rows[:6]))
    lines.append("")

    upcoming_ids = [e["id"] for e in bootstrap["events"] if not e.get("finished")][:6]
    count_by_event_team = {}
    for f in fixtures:
        if not f.get("event") or f["event"] not in upcoming_ids:
            continue
        count_by_event_team.setdefault(f["event"], {})
        count_by_event_team[f["event"]][f["team_h"]] = count_by_event_team[f["event"]].get(f["team_h"], 0) + 1
        count_by_event_team[f["event"]][f["team_a"]] = count_by_event_team[f["event"]].get(f["team_a"], 0) + 1
    dgw, bgw = [], []
    for eid in upcoming_ids:
        for t in bootstrap["teams"]:
            c = count_by_event_team.get(eid, {}).get(t["id"], 0)
            if c >= 2:
                dgw.append(f"GW{eid} {t['short_name']}")
            if c == 0:
                bgw.append(f"GW{eid} {t['short_name']}")
    if dgw or bgw:
        lines.append("<b>⚡ DVOSTRUKI / PRAZNI GAMEWEEKOVI</b>")
        if dgw:
            lines.append("Dvostruki: " + ", ".join(dgw))
        if bgw:
            lines.append("Prazni: " + ", ".join(bgw))
        lines.append("")

    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if team_id:
        try:
            squad_data = fetch_team_squad(int(team_id), bootstrap, players_by_id)
        except Exception as e:
            squad_data = None
            print(f"[upozorenje] Ne mogu dohvatiti tim {team_id}: {e}", file=sys.stderr)
        if squad_data:
            info = squad_data["info"]
            lines.append(f"<b>👤 MOJA EKIPA — {esc(info.get('name', ''))}</b>")
            lines.append(f"Bodovi ukupno: {info.get('summary_overall_points')} · Rang: {info.get('summary_overall_rank')}")
            starters = [s for s in squad_data["squad"] if s["position"] <= 11]
            weak = []
            for s in starters:
                p = s["player"]
                pool = sorted([pp for pp in players if pp["element_type"] == p["element_type"] and pp["_available"]],
                               key=lambda pp: pp["_score"], reverse=True)
                best = pool[0] if pool else None
                gap = (best["_score"] - p["_score"]) if best else 0
                if gap > 3 and (p["_form"] < 3.5 or p["_fdr"] >= 3.6 or not p["_available"]):
                    weak.append((p, best))
            if weak:
                lines.append("Brzi prijedlozi transfera:")
                for p, best in weak[:4]:
                    alt = f" → {esc(best['web_name'])} (£{best['_price']:.1f}m)" if best else ""
                    lines.append(f"• {esc(p['web_name'])} (forma {p['_form']:.1f}){alt}")
            else:
                lines.append("Ekipa izgleda solidno ovaj tjedan — nema očitih slabih karika.")
            lines.append("")

    lines.append("<b>🃏 PODSJETNIK ZA CHIPOVE</b>")
    lines.append("Wildcard: kad struktura tima ne odgovara idućim kolima. Bench Boost: kad cijela klupa ima "
                  "dobre fixtureve. Triple Captain: lagan fixture ili dvostruki GW. Free Hit: za prazni gameweek.")
    lines.append("")
    lines.append("<i>Napomena: preporuke i FDR su naša heuristika iz FPL API podataka, ne kladioničke kvote.</i>")

    return "\n".join(lines)


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("GREŠKA: nedostaju TELEGRAM_BOT_TOKEN i/ili TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    print("Dohvaćam FPL podatke...")
    bootstrap, fixtures = fetch_fpl()

    print("Sastavljam izvještaj...")
    message = build_message(bootstrap, fixtures)

    print("Šaljem na Telegram...")
    send_telegram(message, bot_token, chat_id)
    print("Gotovo.")


if __name__ == "__main__":
    main()
