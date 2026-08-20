#!/usr/bin/env python3
"""
news_check.py - pokreće se svaka 3 sata.
Provjerava nove YouTube videe i nove članke. Za svaki novi video pokušava
dohvatiti titlove i napraviti sažetak; ako titlova nema, šalje samo naslov
i link. Za svaki novi članak dohvaća tekst i pravi sažetak.
Šalje Telegram poruku SAMO ako ima nešto stvarno novo (bez spama).
Svaki obrađeni video/članak se sprema u state["weekly_summaries"] da ga
weekly_review.py može iskoristiti za tjedni pregled i konsenzus.
"""

import os
import sys
import datetime as dt

from common import esc, send_telegram, load_state, save_state, summarize_text, fetch_fpl, upcoming_fixtures_by_team, score_players, fetch_team_squad
from sources import fetch_recent_youtube_videos, fetch_recent_articles, fetch_full_article_text
from video_transcripts import get_transcript_text


def check_squad_alerts(state):
    """Provjerava JESU LI se promijenili status/vijesti kod igrača u korisnikovoj
    stvarnoj ekipi (ne svih igrača u ligi) - da se sazna odmah, ne tek u
    sljedećem dnevnom izvještaju. Vraća (alert_blocks, updated_status_map)."""
    team_id = os.environ.get("FPL_TEAM_ID", "").strip()
    if not team_id:
        return [], state.get("squad_status", {})

    try:
        bootstrap, fixtures = fetch_fpl()
        fx_map = upcoming_fixtures_by_team(fixtures)
        players = score_players(bootstrap, fx_map)
        players_by_id = {p["id"]: p for p in players}
        squad_data = fetch_team_squad(int(team_id), bootstrap, players_by_id)
    except Exception as e:
        print(f"[upozorenje] Provjera ekipe nije uspjela: {e}")
        return [], state.get("squad_status", {})

    if not squad_data:
        return [], state.get("squad_status", {})

    old_status = state.get("squad_status", {})
    new_status = {}
    alerts = []
    for s in squad_data["squad"]:
        p = s["player"]
        if not p:
            continue
        pid = str(p["id"])
        current = {"status": p.get("status"), "news": p.get("news", "")}
        new_status[pid] = current
        prev = old_status.get(pid)
        if prev is None:
            continue  # prvi put vidimo ovog igrača, nema "promjene" za javiti
        changed_bad = (prev.get("status") == "a" and current["status"] != "a") or \
                      (current["news"] and current["news"] != prev.get("news"))
        if changed_bad:
            chance = p.get("chance_of_playing_next_round")
            chance_s = f"{chance}%" if chance is not None else "?"
            alerts.append(
                f"🚨 <b>PROMJENA KOD TVOG IGRAČA:</b> {esc(p['web_name'])} — {chance_s} "
                f"šanse za igranje" + (f" — {esc(current['news'])}" if current["news"] else "")
            )
    return alerts, new_status


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    lookback = int(os.environ.get("RSS_LOOKBACK_HOURS", "4"))

    if not bot_token or not chat_id:
        print("GREŠKA: nedostaju TELEGRAM_BOT_TOKEN i/ili TELEGRAM_CHAT_ID.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    seen_videos = set(state.get("seen_video_ids", []))
    seen_articles = set(state.get("seen_article_urls", []))
    weekly_summaries = state.get("weekly_summaries", [])

    message_blocks = []

    # ---- Upozorenja za TVOJU ekipu (prioritet, provjerava se prvo) ----
    squad_alerts, updated_squad_status = check_squad_alerts(state)
    message_blocks.extend(squad_alerts)

    # ---- Videi ----
    videos = fetch_recent_youtube_videos(lookback_hours=lookback)
    new_videos = [v for v in videos if v["video_id"] not in seen_videos]
    for v in new_videos:
        seen_videos.add(v["video_id"])
        block = f"🎥 <b>{esc(v['channel'])}</b>: {esc(v['title'])}\n{v['link']}"
        summary = None
        if api_key:
            transcript = get_transcript_text(v["video_id"])
            if transcript:
                try:
                    summary = summarize_text(api_key, v["channel"], v["title"], transcript)
                except Exception as e:
                    print(f"[upozorenje] Sažetak videa nije uspio: {e}")
        if summary:
            block += f"\n<i>{esc(summary)}</i>"
            weekly_summaries.append({
                "type": "video", "source": v["channel"], "title": v["title"],
                "url": v["link"], "summary": summary,
                "date": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
        message_blocks.append(block)

    # ---- Članci ----
    articles = fetch_recent_articles(lookback_hours=lookback)
    new_articles = [a for a in articles if a["link"] not in seen_articles]
    for a in new_articles:
        seen_articles.add(a["link"])
        block = f"📰 <b>{esc(a['source'])}</b>: {esc(a['title'])}\n{a['link']}"
        summary = None
        if api_key:
            body = a.get("body") or ""
            if len(body) < 400:
                fetched = fetch_full_article_text(a["link"])
                if fetched:
                    body = fetched
            if body:
                try:
                    summary = summarize_text(api_key, a["source"], a["title"], body)
                except Exception as e:
                    print(f"[upozorenje] Sažetak članka nije uspio: {e}")
        if summary:
            block += f"\n<i>{esc(summary)}</i>"
            weekly_summaries.append({
                "type": "article", "source": a["source"], "title": a["title"],
                "url": a["link"], "summary": summary,
                "date": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
        message_blocks.append(block)

    state["seen_video_ids"] = list(seen_videos)
    state["seen_article_urls"] = list(seen_articles)
    state["weekly_summaries"] = weekly_summaries
    state["squad_status"] = updated_squad_status
    save_state(state)

    if not message_blocks:
        print("Nema novih videa ni članaka - ne šaljem poruku.")
        return

    header = "<b>🆕 NOVO OD FPL ZAJEDNICE</b>"
    full_message = header + "\n\n" + "\n\n".join(message_blocks)
    send_telegram(full_message, bot_token, chat_id)
    print(f"Poslano: {len(new_videos)} video(a), {len(new_articles)} članak(a).")


if __name__ == "__main__":
    main()
