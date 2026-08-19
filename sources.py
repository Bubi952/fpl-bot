"""
sources.py - popis izvora (YouTube kanali + stranice s člancima) i funkcije
za dohvaćanje njihovog najnovijeg sadržaja.

Slobodno dodaj/makni redove u YOUTUBE_CHANNELS i ARTICLE_FEEDS - to je sve
što treba za dodavanje novog izvora.
"""

import re
import datetime as dt
import xml.etree.ElementTree as ET
from urllib.error import URLError, HTTPError

from common import http_get_text

# (channel_id, prikazano ime) - YouTube channel ID se nalazi preko:
# web pretraga "<ime kanala> youtube channel_id"
YOUTUBE_CHANNELS = [
    ("UCxeOc7eFxq37yW_Nc-69deA", "Let's Talk FPL (FPL Andy)"),
    ("UCcPWnCj5AKC19HaySZjb25g", "FPL Harry"),
    ("UC72QokPHXQ9r98ROfNZmaDw", "FPL Focal"),
    ("UC54QLWzsMifTRjNQ02z5pCw", "FPL Raptor"),
    ("UCtIPFexB6PLKNNl0XH3SKKw", "The FPL Wire"),
    ("UCwt39viL_ZHxF1Ggk-_CrDw", "FPL Dylan"),
]

# (rss_url, prikazano ime) - stranice s FPL analizom/vijestima.
# Ako RSS feed ne postoji na nekoj stranici, to izbaci grešku u logu i
# jednostavno se preskoči za taj krug - ne ruši ostatak skripte.
ARTICLE_FEEDS = [
    ("https://www.fantasyfootballscout.co.uk/feed/", "Fantasy Football Scout"),
    ("https://www.fantasyfootballhub.co.uk/feed", "Fantasy Football Hub"),
    ("https://www.bbc.co.uk/sport/football/fantasy-football/rss.xml", "BBC Sport FPL"),
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text):
    text = _TAG_RE.sub(" ", text or "")
    text = html_unescape_basic(text)
    return _WS_RE.sub(" ", text).strip()


def html_unescape_basic(text):
    import html as _html
    return _html.unescape(text)


def parse_rfc822_or_3339(s):
    if not s:
        return None
    s = s.strip()
    # RFC 3339 (YouTube): 2026-08-16T09:00:00+00:00
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # RFC 822 (standardni RSS): Mon, 16 Aug 2026 09:00:00 +0000
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            parsed = dt.datetime.strptime(s, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
        except Exception:
            continue
    return None


def fetch_recent_youtube_videos(lookback_hours=4, max_per_channel=3):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback_hours)
    ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    results = []
    for channel_id, name in YOUTUBE_CHANNELS:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            xml_text = http_get_text(url)
            root = ET.fromstring(xml_text)
            entries = root.findall("atom:entry", ns)
            for entry in entries[:max_per_channel]:
                published_el = entry.find("atom:published", ns)
                title_el = entry.find("atom:title", ns)
                link_el = entry.find("atom:link", ns)
                video_id_el = entry.find("yt:videoId", ns)
                if published_el is None or title_el is None or link_el is None:
                    continue
                published = parse_rfc822_or_3339(published_el.text)
                if published is None or published < cutoff:
                    continue
                results.append({
                    "channel": name,
                    "title": title_el.text,
                    "link": link_el.get("href"),
                    "video_id": video_id_el.text if video_id_el is not None else link_el.get("href"),
                    "published": published,
                })
        except (URLError, HTTPError, ET.ParseError) as e:
            print(f"[upozorenje] RSS nije uspio za {name}: {e}")
            continue
    results.sort(key=lambda r: r["published"], reverse=True)
    return results


def fetch_recent_articles(lookback_hours=4, max_per_feed=5):
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback_hours)
    results = []
    for feed_url, name in ARTICLE_FEEDS:
        try:
            xml_text = http_get_text(feed_url)
            root = ET.fromstring(xml_text)
            items = root.findall(".//item")[:max_per_feed]
            for item in items:
                title_el = item.find("title")
                link_el = item.find("link")
                pub_el = item.find("pubDate")
                desc_el = item.find("description")
                content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
                if title_el is None or link_el is None:
                    continue
                published = parse_rfc822_or_3339(pub_el.text if pub_el is not None else None)
                if published is None or published < cutoff:
                    continue
                body = ""
                if content_el is not None and content_el.text:
                    body = content_el.text
                elif desc_el is not None and desc_el.text:
                    body = desc_el.text
                results.append({
                    "source": name,
                    "title": title_el.text,
                    "link": link_el.text,
                    "body": strip_html(body),
                    "published": published,
                })
        except (URLError, HTTPError, ET.ParseError) as e:
            print(f"[upozorenje] RSS nije uspio za {name}: {e}")
            continue
    results.sort(key=lambda r: r["published"], reverse=True)
    return results


def fetch_full_article_text(url, min_len_from_rss=400):
    """Pokušaj dohvatiti puni tekst stranice ako RSS opis izgleda prekratak.
    Vraća None ako ne uspije - tad se koristi ono što je već iz RSS-a."""
    try:
        html_text = http_get_text(url)
        text = strip_html(html_text)
        # heuristika: uzmi najduži "blok" teksta - stvarni članak je obično
        # daleko najduži kontinuirani tekst na stranici
        return text[:8000]
    except (URLError, HTTPError):
        return None
