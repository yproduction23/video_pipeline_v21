import pytest

from app.services.naver_api_hub import NaverApiHubClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_news_uses_api_hub_endpoint_and_headers(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse({"items": []})

    monkeypatch.setattr("app.services.naver_api_hub.NAVER_API_HUB_ENABLED", True)
    monkeypatch.setattr("app.services.naver_api_hub.httpx.get", fake_get)
    client = NaverApiHubClient("client-id", "client-secret")

    assert client.search_news("반도체", display=20) == {"items": []}
    assert captured["url"] == "https://naverapihub.apigw.ntruss.com/search/v1/news"
    assert captured["headers"] == {
        "X-NCP-APIGW-API-KEY-ID": "client-id",
        "X-NCP-APIGW-API-KEY": "client-secret",
    }
    assert captured["params"]["query"] == "반도체"
    assert captured["params"]["format"] == "json"


def test_trend_uses_api_hub_request_shape(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse({"results": []})

    monkeypatch.setattr("app.services.naver_api_hub.NAVER_API_HUB_ENABLED", True)
    monkeypatch.setattr("app.services.naver_api_hub.httpx.post", fake_post)
    client = NaverApiHubClient("client-id", "client-secret")

    result = client.search_trend(
        start_date="2026-01-01",
        end_date="2026-01-31",
        time_unit="week",
        keyword_groups=[{"groupName": "반도체", "keywords": ["반도체", "AI 반도체"]}],
    )

    assert result == {"results": []}
    assert captured["url"] == "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
    assert captured["headers"]["X-NCP-APIGW-API-KEY-ID"] == "client-id"
    assert captured["json"]["keywordGroups"][0]["groupName"] == "반도체"


@pytest.mark.parametrize("display,start,sort", [(0, 1, "date"), (101, 1, "date"), (1, 1001, "date"), (1, 1, "latest")])
def test_news_rejects_invalid_request_values(monkeypatch, display, start, sort):
    monkeypatch.setattr("app.services.naver_api_hub.NAVER_API_HUB_ENABLED", True)
    client = NaverApiHubClient("client-id", "client-secret")
    with pytest.raises(ValueError):
        client.search_news("반도체", display=display, start=start, sort=sort)


def test_search_images_uses_api_hub_endpoint_and_headers(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse({"items": [{"link": "https://example.com/a.jpg"}]})

    monkeypatch.setattr("app.services.naver_api_hub.NAVER_API_HUB_ENABLED", True)
    monkeypatch.setattr("app.services.naver_api_hub.httpx.get", fake_get)
    client = NaverApiHubClient("client-id", "client-secret")

    result = client.search_images("전역모", display=5)

    assert result == {"items": [{"link": "https://example.com/a.jpg"}]}
    assert captured["url"] == "https://naverapihub.apigw.ntruss.com/search/v1/image"
    assert captured["headers"] == {
        "X-NCP-APIGW-API-KEY-ID": "client-id",
        "X-NCP-APIGW-API-KEY": "client-secret",
    }
    assert captured["params"]["query"] == "전역모"
    assert captured["params"]["filter"] == "all"
    assert captured["params"]["format"] == "json"


@pytest.mark.parametrize(
    "display,start,sort,filter_",
    [(0, 1, "sim", "all"), (101, 1, "sim", "all"), (1, 1001, "sim", "all"), (1, 1, "oldest", "all"), (1, 1, "sim", "huge")],
)
def test_search_images_rejects_invalid_request_values(monkeypatch, display, start, sort, filter_):
    monkeypatch.setattr("app.services.naver_api_hub.NAVER_API_HUB_ENABLED", True)
    client = NaverApiHubClient("client-id", "client-secret")
    with pytest.raises(ValueError):
        client.search_images("전역모", display=display, start=start, sort=sort, filter=filter_)
