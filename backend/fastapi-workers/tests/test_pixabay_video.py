import pytest

from app.providers.real.pixabay_video import (
    PixabayVideoUnavailableError,
    search_candidates,
)


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_search_requires_api_key(monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    with pytest.raises(PixabayVideoUnavailableError):
        search_candidates("empty supermarket aisle")


def test_search_sends_key_as_query_param(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(200, {"hits": []})

    monkeypatch.setattr("app.providers.real.pixabay_video.requests.get", fake_get)

    result = search_candidates("empty supermarket aisle at dusk")

    assert result == []
    assert captured["url"] == "https://pixabay.com/api/videos/"
    assert captured["params"]["key"] == "test-key"
    assert captured["params"]["q"] == "empty supermarket aisle at dusk"


def test_search_picks_the_largest_landscape_resolution_tier(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    payload = {
        "hits": [
            {
                "id": 21117,
                "pageURL": "https://pixabay.com/videos/id-21117/",
                "duration": 6,
                "isAiGenerated": False,
                "isLowQuality": False,
                "tags": "grocery, store, shelves",
                "videos": {
                    "large": {"url": "large.mp4", "width": 2560, "height": 1440, "thumbnail": "t.jpg"},
                    "medium": {"url": "medium.mp4", "width": 1920, "height": 1080, "thumbnail": "t.jpg"},
                    "small": {"url": "small.mp4", "width": 1280, "height": 720, "thumbnail": "t.jpg"},
                    "tiny": {"url": "tiny.mp4", "width": 960, "height": 540, "thumbnail": "t.jpg"},
                },
            }
        ]
    }
    monkeypatch.setattr(
        "app.providers.real.pixabay_video.requests.get",
        lambda *a, **k: _FakeResponse(200, payload),
    )

    results = search_candidates("busy grocery store daytime")

    assert len(results) == 1
    assert results[0]["download_url"] == "large.mp4"
    assert results[0]["resolution_tier"] == "large"
    assert results[0]["pixabay_id"] == 21117
    assert results[0]["tags"] == "grocery, store, shelves"


def test_search_excludes_ai_generated_and_low_quality_clips(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    payload = {
        "hits": [
            {
                "id": 1, "duration": 10, "isAiGenerated": True, "isLowQuality": False,
                "videos": {"large": {"url": "ai.mp4", "width": 1920, "height": 1080}},
            },
            {
                "id": 2, "duration": 10, "isAiGenerated": False, "isLowQuality": True,
                "videos": {"large": {"url": "lowq.mp4", "width": 1920, "height": 1080}},
            },
            {
                "id": 3, "duration": 10, "isAiGenerated": False, "isLowQuality": False,
                "videos": {"large": {"url": "real.mp4", "width": 1920, "height": 1080}},
            },
        ]
    }
    monkeypatch.setattr(
        "app.providers.real.pixabay_video.requests.get",
        lambda *a, **k: _FakeResponse(200, payload),
    )

    results = search_candidates("test query")

    assert [r["download_url"] for r in results] == ["real.mp4"]


def test_search_filters_out_clips_shorter_than_minimum_duration(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    payload = {
        "hits": [
            {
                "id": 1, "duration": 1, "isAiGenerated": False, "isLowQuality": False,
                "videos": {"large": {"url": "short.mp4", "width": 1920, "height": 1080}},
            },
        ]
    }
    monkeypatch.setattr(
        "app.providers.real.pixabay_video.requests.get",
        lambda *a, **k: _FakeResponse(200, payload),
    )

    assert search_candidates("test query", min_duration_seconds=3) == []


def test_search_returns_multiple_candidates_in_pixabay_order(monkeypatch):
    """2026-10-14 사용자 결정: 어떤 후보가 맞는지는 Claude가 내레이션 맥락으로
    고른다 — 이 함수는 더 이상 자체적으로 1개만 추려서 반환하지 않는다."""
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    payload = {
        "hits": [
            {
                "id": 1, "duration": 10, "isAiGenerated": False, "isLowQuality": False,
                "tags": "city, people, walking",
                "videos": {"large": {"url": "a.mp4", "width": 1920, "height": 1080}},
            },
            {
                "id": 2, "duration": 10, "isAiGenerated": False, "isLowQuality": False,
                "tags": "ankle, monitor, bracelet, leg",
                "videos": {"large": {"url": "b.mp4", "width": 1920, "height": 1080}},
            },
        ]
    }
    monkeypatch.setattr(
        "app.providers.real.pixabay_video.requests.get",
        lambda *a, **k: _FakeResponse(200, payload),
    )

    results = search_candidates("close up ankle monitor bracelet leg")

    assert [r["download_url"] for r in results] == ["a.mp4", "b.mp4"]


def test_non_200_response_raises_unavailable_error(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.providers.real.pixabay_video.requests.get",
        lambda *a, **k: _FakeResponse(429, text="rate limited"),
    )

    with pytest.raises(PixabayVideoUnavailableError):
        search_candidates("test query")
