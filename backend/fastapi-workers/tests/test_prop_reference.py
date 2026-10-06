import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.services import prop_reference


def _png_bytes(width: int = 300, height: int = 300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeAnthropic:
    def __init__(self, response_text, *, raise_exc: Exception | None = None):
        self._response_text = response_text
        self._raise_exc = raise_exc
        self.messages = self

    def create(self, **kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeMessage(self._response_text)


def test_extract_prop_reference_terms_parses_claude_json_response(monkeypatch):
    monkeypatch.setattr(prop_reference, "ANTHROPIC_API_KEY", "test-key")
    fake_client = _FakeAnthropic(json.dumps({"terms": ["전역모", "계급장"]}))
    monkeypatch.setattr("anthropic.Anthropic", lambda api_key: fake_client)

    terms = prop_reference.extract_prop_reference_terms("오늘은 전역모를 쓴 병장이 부대를 떠났다.", max_terms=1)

    assert terms == ["전역모"]


def test_extract_prop_reference_terms_returns_empty_without_api_key(monkeypatch):
    monkeypatch.setattr(prop_reference, "ANTHROPIC_API_KEY", "")
    assert prop_reference.extract_prop_reference_terms("전역모를 썼다") == []


def test_extract_prop_reference_terms_swallows_claude_errors(monkeypatch):
    monkeypatch.setattr(prop_reference, "ANTHROPIC_API_KEY", "test-key")
    fake_client = _FakeAnthropic("", raise_exc=RuntimeError("boom"))
    monkeypatch.setattr("anthropic.Anthropic", lambda api_key: fake_client)

    assert prop_reference.extract_prop_reference_terms("전역모를 썼다") == []


def test_extract_prop_reference_terms_swallows_unparseable_response(monkeypatch):
    monkeypatch.setattr(prop_reference, "ANTHROPIC_API_KEY", "test-key")
    fake_client = _FakeAnthropic("이건 JSON이 아닙니다")
    monkeypatch.setattr("anthropic.Anthropic", lambda api_key: fake_client)

    assert prop_reference.extract_prop_reference_terms("전역모를 썼다") == []


class _FakeHttpResponse:
    def __init__(self, content: bytes, content_type: str, status_ok: bool = True):
        self.content = content
        self.headers = {"content-type": content_type}
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("http error")


def test_search_prop_reference_image_downloads_first_valid_candidate(monkeypatch, tmp_path: Path):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def search_images(self, query, **kwargs):
            assert query == "전역모"
            return {"items": [
                {"link": "https://example.com/a.jpg", "sizewidth": "500", "sizeheight": "500"},
            ]}

    monkeypatch.setattr(prop_reference, "NaverApiHubClient", FakeClient)
    monkeypatch.setattr(
        prop_reference.httpx, "get",
        lambda url, **kwargs: _FakeHttpResponse(_png_bytes(), "image/jpeg"),
    )

    path = prop_reference.search_prop_reference_image("전역모", cache_dir=tmp_path)

    assert path is not None
    assert path.is_file()
    assert path.name.startswith("prop_reference_")
    assert path.read_bytes() == _png_bytes()


def test_search_prop_reference_image_skips_too_small_and_falls_back_to_next(monkeypatch, tmp_path: Path):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def search_images(self, query, **kwargs):
            return {"items": [
                {"link": "https://example.com/too-small.jpg", "sizewidth": "50", "sizeheight": "50"},
                {"link": "https://example.com/ok.jpg", "sizewidth": "600", "sizeheight": "600"},
            ]}

    requested_urls = []

    def fake_get(url, **kwargs):
        requested_urls.append(url)
        return _FakeHttpResponse(_png_bytes(), "image/jpeg")

    monkeypatch.setattr(prop_reference, "NaverApiHubClient", FakeClient)
    monkeypatch.setattr(prop_reference.httpx, "get", fake_get)

    path = prop_reference.search_prop_reference_image("전역모", cache_dir=tmp_path)

    assert path is not None
    assert requested_urls == ["https://example.com/ok.jpg"]


def test_search_prop_reference_image_returns_none_when_naver_unavailable(monkeypatch, tmp_path: Path):
    def raise_unavailable(*a, **k):
        raise prop_reference.NaverApiHubUnavailable("not configured")

    monkeypatch.setattr(prop_reference, "NaverApiHubClient", raise_unavailable)

    assert prop_reference.search_prop_reference_image("전역모", cache_dir=tmp_path) is None


def test_search_prop_reference_image_returns_none_when_search_raises(monkeypatch, tmp_path: Path):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def search_images(self, query, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(prop_reference, "NaverApiHubClient", FakeClient)

    assert prop_reference.search_prop_reference_image("전역모", cache_dir=tmp_path) is None


def test_search_prop_reference_image_rejects_non_image_content_type(monkeypatch, tmp_path: Path):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def search_images(self, query, **kwargs):
            return {"items": [{"link": "https://example.com/a.html"}]}

    monkeypatch.setattr(prop_reference, "NaverApiHubClient", FakeClient)
    monkeypatch.setattr(
        prop_reference.httpx, "get",
        lambda url, **kwargs: _FakeHttpResponse(b"<html></html>", "text/html"),
    )

    assert prop_reference.search_prop_reference_image("전역모", cache_dir=tmp_path) is None


def test_resolve_prop_reference_paths_caches_and_skips_repeat_search(monkeypatch, tmp_path: Path):
    search_calls = []

    def fake_extract(scene_text, *, max_terms=1):
        return ["전역모"]

    def fake_search(term, *, cache_dir):
        search_calls.append(term)
        out = cache_dir / f"prop_reference_{prop_reference._slugify(term)}.png"
        out.write_bytes(_png_bytes())
        return out

    monkeypatch.setattr(prop_reference, "extract_prop_reference_terms", fake_extract)
    monkeypatch.setattr(prop_reference, "search_prop_reference_image", fake_search)

    first = prop_reference.resolve_prop_reference_paths("씬1", cache_dir=tmp_path)
    second = prop_reference.resolve_prop_reference_paths("씬2", cache_dir=tmp_path)

    assert len(first) == 1 and len(second) == 1
    assert search_calls == ["전역모"]  # 두 번째 호출은 캐시를 사용해 재검색하지 않음


def test_resolve_prop_reference_paths_returns_empty_on_any_failure(monkeypatch, tmp_path: Path):
    def raise_exc(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(prop_reference, "extract_prop_reference_terms", raise_exc)

    assert prop_reference.resolve_prop_reference_paths("씬1", cache_dir=tmp_path) == []
