"""2026-09-29 사용자 결정: 회사 Gemini 계정이 무기명 카드로 결제 등록이
구조적으로 불가능해(Google 정책), OpenAI 이미지 생성을 메인 provider로
두고 Fal.ai/Gemini를 fallback으로 쓰기로 했다. 파일럿(채널 참조 이미지로
gpt-image-1 edits 호출)에서 캐릭터 얼굴과 한국적 배경 모두 사용자 승인을
받았다.

이 테스트는:
1. OpenAI를 image_provider로 설정할 수 있게 하드락이 풀렸는지
   (기존에는 config.py/runtime_config.py 둘 다 "gemini만 허용"이었다)
2. OpenAI 실패 시 Fal→Gemini로 순서대로 넘어가는지
를 고정한다.
"""
from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

from app import runtime_config
from app.utils import budget
from app.utils.budget import ProviderRequestBudgetExceeded
from app.providers.real.image import NanaBananaProvider


def _tiny_png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), "green").save(buf, "PNG")
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.headers = {}

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


def test_runtime_config_accepts_openai_as_image_provider():
    original = runtime_config.value("image_provider")
    try:
        result = runtime_config.update(image_provider="openai")
        assert result["image_provider"] == "openai"
    finally:
        runtime_config.update(image_provider=original)


def test_runtime_config_still_rejects_unknown_provider():
    with pytest.raises(ValueError):
        runtime_config.update(image_provider="not-a-real-provider")


def test_config_module_default_image_provider_is_openai_and_does_not_crash():
    from app import config
    assert config.IMAGE_PROVIDER == "openai"
    assert hasattr(config, "OPENAI_API_KEY")


def test_openai_success_returns_immediately_without_trying_fal(tmp_path, monkeypatch):
    calls = {"openai": 0, "fal": 0}

    def fake_post(url, *args, **kwargs):
        if "api.openai.com" in url:
            calls["openai"] += 1
            b64 = base64.b64encode(_tiny_png_bytes()).decode()
            return _FakeResponse(200, {"data": [{"b64_json": b64}], "usage": {"total_tokens": 100}})
        if "queue.fal.run" in url:
            calls["fal"] += 1
            return _FakeResponse(200, {"images": [{"url": "https://example.com/fake.png"}]})
        raise AssertionError(f"unexpected POST to {url}")

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key")
    monkeypatch.setenv("FAL_KEY", "fake-fal-key")

    provider = NanaBananaProvider()
    output_path = str(tmp_path / "scene.png")
    result = provider.generate("a scene", output_path, image_provider="openai")

    assert result == output_path
    assert calls["openai"] == 1
    assert calls["fal"] == 0


def test_openai_account_error_falls_back_to_fal(tmp_path, monkeypatch):
    calls = {"openai": 0, "fal": 0}

    def fake_post(url, *args, **kwargs):
        if "api.openai.com" in url:
            calls["openai"] += 1
            return _FakeResponse(401, {"error": {"code": "invalid_api_key", "message": "bad key"}})
        if "queue.fal.run" in url:
            calls["fal"] += 1
            return _FakeResponse(200, {"images": [{"url": "https://example.com/fake.png"}]})
        raise AssertionError(f"unexpected POST to {url}")

    def fake_get(url, *args, **kwargs):
        class _Img:
            content = _tiny_png_bytes()
        return _Img()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr("requests.get", fake_get)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key")
    monkeypatch.setenv("FAL_KEY", "fake-fal-key")

    provider = NanaBananaProvider()
    provider.__class__._fal_disabled = False
    output_path = str(tmp_path / "scene.png")
    result = provider.generate("a scene", output_path, image_provider="openai")

    assert result == output_path
    assert calls["openai"] == 1
    assert calls["fal"] == 1


def test_openai_over_budget_raises_without_spending(tmp_path, monkeypatch):
    """2026-09-29 사용자 재현: 예산 게이트가 없어 1분 테스트 job 하나에
    $10 넘게 청구됐다. 예산이 이미 다 찬 job에서는 실제 POST 없이
    ProviderRequestBudgetExceeded로 즉시 막혀야 한다."""
    monkeypatch.setattr(budget, "_job_path", lambda _job_id, _name: tmp_path / _name)
    monkeypatch.setattr(budget.runtime_config, "get", lambda: {
        "usd_krw": 1400,
        "max_budget_per_video_krw": 100,
    })
    calls = {"openai": 0}

    def fake_post(*args, **kwargs):
        calls["openai"] += 1
        raise AssertionError("예산 초과 상태에서는 실제 요청을 보내면 안 된다")

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key")

    provider = NanaBananaProvider()
    with pytest.raises(ProviderRequestBudgetExceeded):
        provider._generate_openai_image(
            "a scene", str(tmp_path / "scene.png"), "sk-fake-key", job_id=99,
        )
    assert calls["openai"] == 0


def test_openai_and_fal_failure_falls_back_to_gemini(tmp_path, monkeypatch):
    calls = {"openai": 0, "fal": 0, "gemini": 0}

    def fake_post(url, *args, **kwargs):
        if "api.openai.com" in url:
            calls["openai"] += 1
            return _FakeResponse(401, {"error": {"code": "invalid_api_key"}})
        if "queue.fal.run" in url:
            calls["fal"] += 1
            return _FakeResponse(403, {"error": "account locked"})
        if "generativelanguage.googleapis.com" in url:
            calls["gemini"] += 1
            b64 = base64.b64encode(_tiny_png_bytes()).decode()
            return _FakeResponse(200, {
                "candidates": [{"content": {"parts": [{"inlineData": {"data": b64}}]}}],
                "usageMetadata": {"promptTokenCount": 1},
            })
        raise AssertionError(f"unexpected POST to {url}")

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-key")
    monkeypatch.setenv("FAL_KEY", "fake-fal-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")

    class _Audit:
        def before_attempt(self, **kwargs):
            return "token"

        def after_attempt(self, *args, **kwargs):
            return {"status": "needs_review", "next_allowed_at": 0}

        def assert_dispatchable(self, token):
            return None

    provider = NanaBananaProvider()
    provider.__class__._fal_disabled = False
    provider.__class__._gemini_disabled = False
    output_path = str(tmp_path / "scene.png")
    result = provider.generate(
        "a scene", output_path, image_provider="openai",
        gemini_model="gemini-3.1-flash-image", gemini_request_audit=_Audit(),
    )

    assert result == output_path
    assert calls["openai"] == 1
    assert calls["fal"] == 1
    assert calls["gemini"] == 1
