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


def test_search_picks_the_tier_closest_to_1920_width_not_the_largest(monkeypatch):
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
    # 2026-10-16: 최종 영상은 1920x1080이다. 4K를 받으면 파일이 4배 커지고 병렬 합성이 메모리 부족으로 멈췄다.
    assert results[0]["download_url"] == "medium.mp4"
    assert results[0]["resolution_tier"] == "medium"
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


def test_search_uses_the_largest_available_tier_when_nothing_reaches_1920(monkeypatch):
    monkeypatch.setenv("PIXABAY_API_KEY", "test-key")
    payload = {"hits": [{
        "id": 1, "pageURL": "p", "duration": 6, "isAiGenerated": False, "isLowQuality": False, "tags": "t",
        "videos": {
            "small": {"url": "small.mp4", "width": 1280, "height": 720, "thumbnail": "t.jpg"},
            "tiny": {"url": "tiny.mp4", "width": 960, "height": 540, "thumbnail": "t.jpg"},
        },
    }]}
    monkeypatch.setattr("app.providers.real.pixabay_video.requests.get", lambda *a, **k: _FakeResponse(200, payload))

    results = search_candidates("city")

    assert results[0]["resolution_tier"] == "small"


def test_download_retries_when_the_cdn_rate_limits_the_request(monkeypatch, tmp_path):
    """2026-10-16 9020 시험 실행: 스톡 클립을 한꺼번에 받다 HTTP 429가 나 13장면이 실패했다. 429·5xx는 대기 후 재시도한다."""
    from app.providers.real import pixabay_video

    calls = []

    class Response:
        def __init__(self, status):
            self.status_code = status
            self.headers = {"Retry-After": "0"}

        def iter_content(self, chunk_size):
            yield b"video-bytes"

    def fake_get(url, **kwargs):
        calls.append(url)
        return Response(429 if len(calls) < 3 else 200)

    monkeypatch.setattr(pixabay_video.requests, "get", fake_get)
    monkeypatch.setattr(pixabay_video.time, "sleep", lambda seconds: None)
    out = tmp_path / "clip.mp4"

    pixabay_video.download_clip("https://example/v.mp4", str(out))

    assert len(calls) == 3 and out.read_bytes() == b"video-bytes"


def test_download_gives_up_after_the_retry_budget(monkeypatch, tmp_path):
    import pytest

    from app.providers.real import pixabay_video

    class Response:
        status_code = 429
        headers = {}

    monkeypatch.setattr(pixabay_video.requests, "get", lambda *a, **k: Response())
    monkeypatch.setattr(pixabay_video.time, "sleep", lambda seconds: None)

    with pytest.raises(pixabay_video.PixabayVideoUnavailableError):
        pixabay_video.download_clip("https://example/v.mp4", str(tmp_path / "c.mp4"))
