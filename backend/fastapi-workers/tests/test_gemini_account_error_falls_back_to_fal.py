"""2026-09-29 사용자 승인 결정: 회사 Gemini 계정이 무기명 카드 결제 등록 자체가
막혀 있어(Google 정책상 구조적으로 불가능) job 12의 모든 씬이 계속
"HTTP 403 PERMISSION_DENIED" / "HTTP 429 RESOURCE_EXHAUSTED"로 실패했다.
Gemini Pro 요청은 원래 "절대 조용히 다른 렌더러로 대체하지 않는다"는 정책이라
그대로 하드 실패했는데(image.py:270-281), 사용자가 이 계정 자체가 영구히
막혀있다는 걸 확인한 뒤 "이 경우에만" Fal.ai로 대체 생성해도 좋다고 명시적으로
승인했다.

이 테스트는 그 승인 범위를 정확히 고정한다:
- Gemini가 계정 수준 오류(PERMISSION_DENIED/RESOURCE_EXHAUSTED)로 실패하면
  Fal.ai로 대체 생성한다(계정이 근본적으로 막혀 항상 그럴 것이므로, 조용한
  다운그레이드가 아니라 사용자가 이미 알고 승인한 상태다).
- 그 외의 실패(5xx 등 일시적 오류, 기존 예산/쿼터 재시도 계약 위반)는 여전히
  Fal로 우회하지 않고 그대로 실패한다 — 기존 비용/재시도 안전장치를 건드리지
  않는다.
"""
from __future__ import annotations

from PIL import Image
from io import BytesIO
import base64

from app.providers.real.image import NanaBananaProvider


def _tiny_png_b64() -> str:
    buf = BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _FakeResponse:
    def __init__(self, status_code: int, body: dict, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}
        self.text = text or str(body)

    def json(self):
        return self._body


def test_gemini_permission_denied_falls_back_to_fal(tmp_path, monkeypatch):
    calls = {"gemini": 0, "fal": 0}

    def fake_post(url, *args, **kwargs):
        if "generativelanguage.googleapis.com" in url:
            calls["gemini"] += 1
            return _FakeResponse(403, {"error": {"status": "PERMISSION_DENIED"}})
        if "queue.fal.run" in url:
            calls["fal"] += 1
            return _FakeResponse(200, {"images": [{"url": "https://example.com/fake.png"}]})
        raise AssertionError(f"unexpected POST to {url}")

    def fake_get(url, *args, **kwargs):
        class _Img:
            content = base64.b64decode(_tiny_png_b64())
        return _Img()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setenv("FAL_KEY", "fake-fal-key")

    provider = NanaBananaProvider()
    provider.__class__._fal_disabled = False
    output_path = str(tmp_path / "scene.png")

    class _Audit:
        def before_attempt(self, **kwargs):
            return "token"

        def after_attempt(self, *args, **kwargs):
            return {"status": "needs_review", "next_allowed_at": 0}

        def assert_dispatchable(self, token):
            return None

    result = provider.generate(
        "a scene", output_path, image_provider="gemini",
        gemini_model="gemini-3-pro-image", gemini_request_audit=_Audit(),
    )

    assert result == output_path
    assert calls["gemini"] == 1
    assert calls["fal"] == 1


def test_gemini_transient_5xx_does_not_fall_back_to_fal(tmp_path, monkeypatch):
    calls = {"gemini": 0, "fal": 0}

    def fake_post(url, *args, **kwargs):
        if "generativelanguage.googleapis.com" in url:
            calls["gemini"] += 1
            return _FakeResponse(503, {"error": {"status": "UNAVAILABLE"}})
        if "queue.fal.run" in url:
            calls["fal"] += 1
            return _FakeResponse(200, {"images": [{"url": "https://example.com/fake.png"}]})
        raise AssertionError(f"unexpected POST to {url}")

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setenv("FAL_KEY", "fake-fal-key")

    provider = NanaBananaProvider()
    provider.__class__._fal_disabled = False
    output_path = str(tmp_path / "scene.png")

    class _Audit:
        def before_attempt(self, **kwargs):
            return "token"

        def after_attempt(self, *args, **kwargs):
            return {"status": "deferred", "next_allowed_at": 0}

        def assert_dispatchable(self, token):
            return None

    import pytest
    from app.utils.image_request_control import ImageRequestHeld

    with pytest.raises(ImageRequestHeld):
        provider.generate(
            "a scene", output_path, image_provider="gemini",
            gemini_model="gemini-3-pro-image", gemini_request_audit=_Audit(),
        )

    assert calls["gemini"] == 1
    assert calls["fal"] == 0
