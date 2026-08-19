"""
video_transcripts.py - dohvaćanje YouTube titlova (ne zvuka/videa - samo
javno dostupni titlovi, uključujući YouTube-ove automatske titlove).
Ako titlovi ne postoje, vraća None - taj video se tad šalje samo kao link,
bez sažetka (vidi news_check.py).
"""

def get_transcript_text(video_id, max_chars=9000):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
    except ImportError:
        print("[upozorenje] youtube_transcript_api nije instaliran - preskačem sažetke videa.")
        return None

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        transcript = None
        # radije engleski (najviše FPL sadržaja), inače prvi dostupan
        try:
            transcript = transcript_list.find_transcript(["en", "en-GB", "en-US"])
        except Exception:
            for t in transcript_list:
                transcript = t
                break
        if transcript is None:
            return None
        entries = transcript.fetch()
        text = " ".join(e.get("text", "") for e in entries)
        text = " ".join(text.split())
        return text[:max_chars] if text else None
    except (TranscriptsDisabled, NoTranscriptFound):
        return None
    except Exception as e:
        print(f"[upozorenje] Transkript nije uspio za video {video_id}: {e}")
        return None
