from fastapi.testclient import TestClient

from app.main import app
from app.services.discovery import benchmark_analysis as ba
from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

client = TestClient(app)


def test_hot_keywords_success(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)
    monkeypatch.setattr(YouTubeDiscovery, "hot_keywords", lambda self, category, window: {"window": window, "category": category, "keywords": [], "videos": [], "categories": []})

    response = client.get("/workers/discovery/hot-keywords", params={"category": "GAMING", "window": "7d"})

    assert response.status_code == 200 and response.json()["category"] == "GAMING"


def test_hot_keywords_error_mapping(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)

    def raise_quota(self, category, window):
        raise DiscoveryError("QUOTA", "쿼터 소진")

    monkeypatch.setattr(YouTubeDiscovery, "hot_keywords", raise_quota)

    assert client.get("/workers/discovery/hot-keywords").status_code == 429


def test_hot_keywords_invalid_window_is_400(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)
    monkeypatch.setattr(YouTubeDiscovery, "_chart_rows", lambda self, key, category_id: [])

    assert client.get("/workers/discovery/hot-keywords", params={"window": "30d"}).status_code == 400


def test_recent_uploads_splits_channel_ids(monkeypatch):
    seen = {}
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)

    def fake(self, channel_ids, days):
        seen["ids"], seen["days"] = channel_ids, days
        return {"videos": [], "channels": []}

    monkeypatch.setattr(YouTubeDiscovery, "recent_uploads", fake)

    response = client.get("/workers/discovery/recent-uploads", params={"channel_ids": "UC1, UC2", "days": 3})

    assert response.status_code == 200 and seen == {"ids": ["UC1", "UC2"], "days": 3}


def test_video_not_found_is_404(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)

    def missing(self, video_id):
        raise DiscoveryError("NOT_FOUND", "없음")

    monkeypatch.setattr(YouTubeDiscovery, "fetch_video", missing)

    assert client.get("/workers/discovery/video/abcdefghijk").status_code == 404


def test_analyze_endpoint_returns_analysis_and_502_on_bad_output(monkeypatch):
    monkeypatch.setattr(ba, "claude_llm_call", lambda system, user, max_tokens=800: '{"topic_keyword":"상어","reasons":["a"],"hook_type":null,"title_pattern":"x"}')
    ok = client.post("/workers/benchmark/analyze", json={"video": {"title": "제목"}})
    assert ok.status_code == 200 and ok.json()["topic_keyword"] == "상어"

    monkeypatch.setattr(ba, "claude_llm_call", lambda system, user, max_tokens=800: "형식 오류")
    bad = client.post("/workers/benchmark/analyze", json={"video": {"title": "제목"}})
    assert bad.status_code == 502
