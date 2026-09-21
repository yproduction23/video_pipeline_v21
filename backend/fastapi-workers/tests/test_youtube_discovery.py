"""YouTube 조회 서비스: 카테고리 파라미터, 오류 매핑, 채널 신작 계산."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.discovery import youtube_discovery as yd
from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(yd, "_get_redis_client", lambda: None)
    monkeypatch.setattr(yd, "_consume_quota", lambda *args, **kwargs: True)


def install(monkeypatch, handler):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        return handler(url, params or {})

    monkeypatch.setattr(yd.requests, "get", fake_get)
    return calls


def video_item(video_id, title, hours_ago, views, live="none", channel_id="UC1"):
    return {
        "id": video_id,
        "snippet": {"title": title, "channelId": channel_id, "channelTitle": "채널", "publishedAt": iso(hours_ago),
                    "tags": ["태그"], "categoryId": "25", "liveBroadcastContent": live, "description": "설명" * 10},
        "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "2"},
        "contentDetails": {"duration": "PT1M30S"},
    }


def chart_handler(url, params):
    if url.endswith("/videos"):
        return FakeResponse(200, {"items": [video_item("v1", "부산상어 챌린지", 5, 5000), video_item("v2", "라이브", 3, 9000, live="live")]})
    if url.endswith("/channels"):
        return FakeResponse(200, {"items": [{"id": "UC1", "statistics": {"subscriberCount": "1200"}}]})
    raise AssertionError(url)


def test_all_category_omits_video_category_id(monkeypatch):
    calls = install(monkeypatch, chart_handler)

    YouTubeDiscovery().hot_keywords("ALL", "48h")

    chart_params = next(p for u, p in calls if u.endswith("/videos"))
    assert "videoCategoryId" not in chart_params
    assert chart_params["chart"] == "mostPopular" and chart_params["regionCode"] == "KR"


def test_specific_category_sends_id_and_excludes_live_videos(monkeypatch):
    calls = install(monkeypatch, chart_handler)

    result = YouTubeDiscovery().hot_keywords("NEWS", "48h")

    chart_params = next(p for u, p in calls if u.endswith("/videos"))
    assert chart_params["videoCategoryId"] == "25"
    assert [v["videoId"] for v in result["videos"]] == ["v1"]
    assert result["videos"][0]["subscribers"] == 1200
    assert {"key": "NEWS", "label": "뉴스·정치"} in result["categories"]


def test_unsupported_category_maps_to_error(monkeypatch):
    install(monkeypatch, lambda url, params: FakeResponse(404, {"error": {"errors": [{"reason": "notFound"}]}}))

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("TECH", "48h")

    assert error.value.code == "UNSUPPORTED_CATEGORY"


def test_unknown_category_key_is_rejected():
    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("EDU", "48h")

    assert error.value.code == "UNSUPPORTED_CATEGORY"


def test_missing_api_key_raises_without_calling_youtube(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("ALL", "48h")

    assert error.value.code == "NO_API_KEY"


def test_quota_exhaustion_raises(monkeypatch):
    monkeypatch.setattr(yd, "_consume_quota", lambda *args, **kwargs: False)

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("ALL", "48h")

    assert error.value.code == "QUOTA"


def test_recent_uploads_computes_outperformance_and_filters_by_days(monkeypatch):
    def handler(url, params):
        if url.endswith("/channels"):
            return FakeResponse(200, {"items": [{"id": "UC1", "snippet": {"title": "채널1"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}, "statistics": {"subscriberCount": "1000"}}]})
        if url.endswith("/playlistItems"):
            return FakeResponse(200, {"items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}]})
        if url.endswith("/videos"):
            return FakeResponse(200, {"items": [video_item("v1", "새 영상", 10, 3000), video_item("v2", "옛 영상", 24 * 20, 1000)]})
        raise AssertionError(url)

    install(monkeypatch, handler)

    result = YouTubeDiscovery().recent_uploads(["UC1"], days=7)

    assert [v["videoId"] for v in result["videos"]] == ["v1"]
    assert result["videos"][0]["outperformanceIndex"] == 1.5
    assert result["channels"] == [{"channelId": "UC1", "title": "채널1", "subscribers": 1000}]


def test_fetch_video_returns_description_and_not_found_raises(monkeypatch):
    install(monkeypatch, lambda url, params: FakeResponse(200, {"items": [video_item("abcdefghijk", "제목", 5, 100)]}))
    row = YouTubeDiscovery().fetch_video("abcdefghijk")
    assert row["videoId"] == "abcdefghijk" and row["description"].startswith("설명")

    install(monkeypatch, lambda url, params: FakeResponse(200, {"items": []}))
    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().fetch_video("abcdefghijk")
    assert error.value.code == "NOT_FOUND"


def test_fetch_video_rejects_malformed_id_without_calling_youtube(monkeypatch):
    calls = install(monkeypatch, lambda url, params: FakeResponse(200, {"items": []}))

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().fetch_video("../etc/passwd")

    assert error.value.code == "NOT_FOUND" and calls == []
