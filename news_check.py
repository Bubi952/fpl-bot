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

from common import esc, send_telegram, load_state, save_state, summarize_text
from sources import fetch_recent_youtube_videos, fetch_recent_articles, fetch_full_article_text
from video_transcripts import get_transcript_text


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
