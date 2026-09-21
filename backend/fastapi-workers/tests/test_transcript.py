import sys
import types

from app.services.discovery import transcript


class Snip:
    def __init__(self, text):
        self.text = text


def _install(monkeypatch, fetch):
    module = types.ModuleType("youtube_transcript_api")

    class Api:
        def fetch(self, video_id, languages=None):
            return fetch(video_id, languages)

    module.YouTubeTranscriptApi = Api
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", module)
    transcript._fetch.cache_clear()


def test_cleans_noise_and_joins(monkeypatch):
    _install(monkeypatch, lambda vid, langs: [Snip("계란 하나만 주세요. >>"), Snip("[음악] 안 팔아요.")])
    assert transcript.fetch_transcript("abc") == "계란 하나만 주세요. 안 팔아요."


def test_prefers_korean_then_english(monkeypatch):
    seen = {}

    def fetch(vid, langs):
        seen["langs"] = langs
        return [Snip("x")]

    _install(monkeypatch, fetch)
    transcript.fetch_transcript("abc")
    assert seen["langs"] == ["ko", "en"]


def test_any_failure_returns_none(monkeypatch):
    def boom(vid, langs):
        raise RuntimeError("blocked")

    _install(monkeypatch, boom)
    assert transcript.fetch_transcript("abc") is None


def test_missing_video_id_returns_none(monkeypatch):
    _install(monkeypatch, lambda vid, langs: [Snip("x")])
    assert transcript.fetch_transcript(None) is None
    assert transcript.fetch_transcript("  ") is None


def test_truncates_long_transcripts(monkeypatch):
    _install(monkeypatch, lambda vid, langs: [Snip("가" * 9000)])
    assert len(transcript.fetch_transcript("abc")) == transcript.MAX_CHARS
