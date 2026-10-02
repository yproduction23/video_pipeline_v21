"""외부 이미지 요청 단위의 예산 예약과 감사 기록을 검증한다."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.utils import budget
from app.utils.budget import (
    ProviderRequestAudit,
    ProviderRequestBudgetExceeded,
    can_charge_openai_image,
    record_cost,
    record_openai_image_cost,
)
from app.providers.real.image import GeminiImageGenerationError, NanaBananaProvider
from app.utils.image_request_control import ImageRequestHeld


def _audit(tmp_path, model="gemini-3-pro-image"):
    return ProviderRequestAudit.for_path(path=tmp_path / "audit.json", scene_key="scene-1", model=model,
        unit_usd=0.1, usd_krw=1000, budget_limit_krw=300)


def test_each_http_attempt_is_reserved_and_persisted(tmp_path: Path):
    path = tmp_path / "request_ledger.json"
    audit = ProviderRequestAudit.for_path(
        path=path,
        scene_key="scene-01",
        model="gemini-3-pro-image",
        unit_usd=0.1,
        usd_krw=1000,
        budget_limit_krw=300,
        request_metadata={"reference_contract": "v2_textless", "reference_artifacts": [{"role": "character", "sha256": "abc"}]},
    )

    first = audit.before_attempt(attempt=1)
    audit.after_attempt(first, status_code=503, request_id="req-503", outcome="http_503")
    second = audit.before_attempt(attempt=2)
    audit.after_attempt(second, status_code=200, request_id="req-200", outcome="http_200")

    ledger = json.loads(path.read_text(encoding="utf-8"))

    # 성공 추정 합계만 100원이다. 503의 실제 청구 여부는 이 테스트로 확인할 수 없다.
    assert ledger["total_krw"] == 100

    assert [(item["attempt"], item["status"], item["status_code"]) for item in ledger["items"]] == [
        (1, "http_503", 503),
        (2, "http_200", 200),
    ]

    # 503 항목: 성공 추정 합계에서만 제외한다.
    assert ledger["items"][0]["amount_krw"] == 0
    assert ledger["items"][0]["cost_status"] == "excluded_from_success_estimate_billing_unverified"

    # 200 항목: 성공 → 과금 미확정 상태로 유지
    assert ledger["items"][1]["cost_status"] == "unverified_until_console_reconciliation"

    assert ledger["items"][0]["request_metadata"]["reference_contract"] == "v2_textless"


def test_reservation_blocks_post_before_a_network_call(tmp_path: Path):
    path = tmp_path / "request_ledger.json"
    audit = ProviderRequestAudit.for_path(
        path=path,
        scene_key="scene-01",
        model="gemini-3-pro-image",
        unit_usd=0.1,
        usd_krw=1000,
        budget_limit_krw=99,
    )

    try:
        audit.before_attempt(attempt=1)
        assert False, "예약 상한을 넘는 HTTP 요청은 차단돼야 합니다."
    except ProviderRequestBudgetExceeded:
        pass
    assert not path.exists()


def test_needs_review_lock_blocks_a_normal_for_path_retry(tmp_path: Path):
    path = tmp_path / "request_ledger.json"

    def make_audit():
        return ProviderRequestAudit.for_path(
            path=path, scene_key="character_library:channel_b:explorer",
            model="gemini-3-pro-image", unit_usd=0.1, usd_krw=1000, budget_limit_krw=300,
        )

    token = make_audit().before_attempt(attempt=1)
    make_audit().after_attempt(token, outcome="rejected", retryable=False, permanent=True)

    with pytest.raises(ImageRequestHeld, match="영구 오류"):
        make_audit().before_attempt(attempt=1)


def test_enforce_request_lock_false_lets_admin_retry_past_a_needs_review_lock(tmp_path: Path):
    """캐릭터 라이브러리 포즈 재생성처럼 관리자가 직접 결과를 검수하는 호출은
    영상 Job 장면 생성과 같은 영구 잠금 상태 기계를 공유하지 않아야 한다."""
    path = tmp_path / "request_ledger.json"

    def make_audit():
        return ProviderRequestAudit.for_path(
            path=path, scene_key="character_library:channel_b:explorer",
            model="gemini-3-pro-image", unit_usd=0.1, usd_krw=1000, budget_limit_krw=300,
            enforce_request_lock=False,
        )

    token = make_audit().before_attempt(attempt=1)
    make_audit().after_attempt(token, outcome="rejected", retryable=False, permanent=True)

    # 잠금 상태 기계를 전혀 거치지 않으므로 같은 scene_key로도 바로 재시도할 수 있다.
    retry_token = make_audit().before_attempt(attempt=1)
    make_audit().after_attempt(retry_token, status_code=200, outcome="http_200")

    ledger = json.loads(path.read_text(encoding="utf-8"))
    assert [item["request_control"] for item in ledger["items"]] == [{}, {}]


def test_v4_success_record_does_not_duplicate_a_reserved_gemini_attempt(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(budget, "_job_path", lambda _job_id, _name: tmp_path / _name)
    monkeypatch.setattr(budget.runtime_config, "get", lambda: {
        "img_cost_flash_1k_usd": 0.05,
        "img_cost_pro_2k_usd": 0.1,
        "kling_cost_per_clip_usd": 0.35,
        "usd_krw": 1000,
        "max_budget_per_video_krw": 1000,
    })
    audit = ProviderRequestAudit.for_job(
        job_id=1,
        scene_key="image:0",
        model="gemini-3-pro-image",
    )
    token = audit.before_attempt(attempt=1)
    audit.after_attempt(token, status_code=200, outcome="http_200")

    ledger = record_cost(1, "pro", scene_key="image:0")
    assert ledger["total_krw"] == 100
    assert len(ledger["items"]) == 1
    assert ledger["items"][0]["kind"] == "gemini_pro_request"


def test_flash_2k_attempt_uses_its_own_model_rate_and_kind(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(budget, "_job_path", lambda _job_id, _name: tmp_path / _name)
    monkeypatch.setattr(budget.runtime_config, "get", lambda: {
        "img_cost_flash_1k_usd": 0.067,
        "img_cost_flash_2k_usd": 0.101,
        "img_cost_pro_2k_usd": 0.134,
        "kling_cost_per_clip_usd": 0.35,
        "usd_krw": 1400,
        "max_budget_per_video_krw": 1000,
    })

    audit = ProviderRequestAudit.for_job(
        job_id=54,
        scene_key="pilot:image:1",
        model="gemini-3.1-flash-image",
    )
    token = audit.before_attempt(attempt=1)
    audit.after_attempt(token, status_code=200, outcome="http_200")

    ledger = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert ledger["total_krw"] == 141
    assert ledger["items"][0]["model"] == "gemini-3.1-flash-image"
    assert ledger["items"][0]["kind"] == "gemini_flash_image_request"
    assert ledger["items"][0]["estimated_usd"] == 0.101


def test_can_charge_openai_image_blocks_once_projected_cost_exceeds_budget(tmp_path: Path, monkeypatch):
    """2026-09-29 사용자 재현: OpenAI 이미지 생성엔 예산 게이트가 없어서
    1분 테스트 job 하나에 $10 넘게 청구된 사고가 있었다. 이 게이트가 영상
    전체 예산(₩40,000)을 넘기 전에 다음 요청을 막는지 고정한다."""
    monkeypatch.setattr(budget, "_job_path", lambda _job_id, _name: tmp_path / _name)
    monkeypatch.setattr(budget.runtime_config, "get", lambda: {
        "usd_krw": 1400,
        "max_budget_per_video_krw": 1000,
    })

    assert can_charge_openai_image(1, 0.25) is True
    record_openai_image_cost(1, 0.25, scene_key="image:0")
    # 350원(0.25달러) 기록 후, 남은 예산(650원)보다 비싼 다음 요청은 막는다.
    assert can_charge_openai_image(1, 0.5) is False
    # 남은 예산 안에 드는 요청은 계속 허용한다.
    assert can_charge_openai_image(1, 0.4) is True


def test_record_openai_image_cost_appends_to_the_shared_job_ledger(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(budget, "_job_path", lambda _job_id, _name: tmp_path / _name)
    monkeypatch.setattr(budget.runtime_config, "get", lambda: {
        "usd_krw": 1400,
        "max_budget_per_video_krw": 40000,
    })

    ledger = record_openai_image_cost(7, 0.186, scene_key="image:3")

    assert ledger["items"][0]["kind"] == "openai_image"
    assert ledger["items"][0]["provider"] == "openai"
    assert ledger["items"][0]["scene_key"] == "image:3"
    assert ledger["total_krw"] == round(0.186 * 1400)
    saved = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert saved["total_krw"] == ledger["total_krw"]


def test_pro_priority_attempt_uses_priority_rate_and_records_requested_tier(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(budget, "_job_path", lambda _job_id, _name: tmp_path / _name)
    monkeypatch.setattr(budget.runtime_config, "get", lambda: {
        "gemini_service_tier": "standard",
        "img_cost_flash_2k_usd": 0.101,
        "img_cost_pro_2k_usd": 0.134,
        "img_cost_pro_priority_2k_usd": 0.24192,
        "usd_krw": 1400,
        "max_budget_per_video_krw": 1000,
    })

    audit = ProviderRequestAudit.for_job(
        job_id=55,
        scene_key="image:0",
        model="gemini-3-pro-image",
        service_tier="priority",
    )
    token = audit.before_attempt(attempt=1)
    audit.after_attempt(token, status_code=200, outcome="http_200")

    ledger = json.loads((tmp_path / "cost_ledger.json").read_text(encoding="utf-8"))
    assert ledger["total_krw"] == 339
    assert ledger["items"][0]["estimated_usd"] == 0.24192
    assert ledger["items"][0]["request_metadata"]["service_tier_requested"] == "priority"


def test_kling_success_record_does_not_duplicate_a_reserved_request(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(budget, "_job_path", lambda _job_id, _name: tmp_path / _name)
    monkeypatch.setattr(budget.runtime_config, "get", lambda: {
        "img_cost_flash_1k_usd": 0.05,
        "img_cost_pro_2k_usd": 0.1,
        "kling_cost_per_clip_usd": 0.35,
        "usd_krw": 1000,
        "max_budget_per_video_krw": 1000,
    })
    audit = ProviderRequestAudit.for_kling(job_id=1, scene_key="kling:0")
    token = audit.before_attempt(attempt=1)
    audit.after_attempt(token, status_code=200, outcome="succeeded")

    ledger = record_cost(1, "kling", scene_key="kling:0")
    assert ledger["total_krw"] == 350
    assert len(ledger["items"]) == 1
    assert ledger["items"][0]["kind"] == "kling_request"


def test_provider_records_one_post_and_defers_instead_of_nested_retry(tmp_path: Path, monkeypatch):
    class Response:
        def __init__(self, status_code: int, request_id: str):
            self.status_code = status_code
            self.headers = {"x-goog-request-id": request_id}
            self.text = "provider test response"

        def json(self):
            return {"error": {"status": "UNAVAILABLE" if self.status_code == 503 else "NOT_FOUND"}}

    responses = [Response(503, "retry-1"), Response(404, "final-2")]
    calls = []

    def fake_post(*_args, **_kwargs):
        calls.append(_kwargs)
        return responses.pop(0)

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    path = tmp_path / "request_ledger.json"
    audit = ProviderRequestAudit.for_path(
        path=path,
        scene_key="scene-01",
        model="gemini-3-pro-image",
        unit_usd=0.1,
        usd_krw=1000,
        budget_limit_krw=300,
    )

    with pytest.raises(ImageRequestHeld, match="HTTP 503"):
        NanaBananaProvider()._generate_gemini_api(
            "test", str(tmp_path / "unused.png"), "not-a-real-key", [],
            model="gemini-3-pro-image", image_size="2K", max_attempts=2,
            retry_base_seconds=1, request_audit=audit,
        )

    ledger = json.loads(path.read_text(encoding="utf-8"))

    # 내부 max_attempts=2가 전달돼도 POST는 한 번만 수행한다.
    assert len(calls) == 1
    assert all(call["timeout"] == (20.0, 300.0) for call in calls)
    assert all(call["headers"]["X-Server-Timeout"] == "285" for call in calls)
    assert len(ledger["items"]) == 1

    assert [(item["status"], item["request_id"]) for item in ledger["items"]] == [
        ("http_503", "retry-1"),
    ]

    # 503은 성공 추정 합계에서 제외되지만 보수적 요청 노출액은 유지된다.
    assert ledger["total_krw"] == 0

    assert ledger["reserved_exposure_krw"] == 100
    assert all(item["cost_status"] == "excluded_from_success_estimate_billing_unverified" for item in ledger["items"])
    assert all(item["amount_krw"] == 0 for item in ledger["items"])


def test_reference_payload_order_matches_the_v3_declared_contract(tmp_path: Path, monkeypatch):
    import base64
    from io import BytesIO

    from PIL import Image

    source = BytesIO()
    Image.new("RGB", (2, 2), "white").save(source, "PNG")
    generated = base64.b64encode(source.getvalue()).decode()
    captured = {}

    class Response:
        status_code = 200
        headers = {}
        text = "ok"

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": generated}}]}}]}

    def fake_post(_endpoint, *, json, **_kwargs):
        captured["payload"] = json
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    references = []
    for name, payload in (("character", b"character"), ("style", b"style")):
        path = tmp_path / f"{name}.png"
        path.write_bytes(payload)
        references.append(str(path))

    output = tmp_path / "result.png"
    assert NanaBananaProvider()._generate_gemini_api(
        "contract prompt", str(output), "not-a-real-key", references,
        model="gemini-3-pro-image", image_size="2K", max_attempts=1,
        reference_contract_declared=True,
        request_audit=_audit(tmp_path),
    )
    parts = captured["payload"]["contents"][0]["parts"]
    assert [base64.b64decode(part["inlineData"]["data"]) for part in parts[:2]] == [
        b"character", b"style",
    ]
    assert len(parts) == 3  # 두 참조 이미지와 하나의 텍스트 프롬프트


@pytest.mark.parametrize("reference_contract_declared", [False, True])
def test_operational_generate_content_injects_the_same_face_reference_contract_as_canary(
    tmp_path: Path,
    monkeypatch,
    reference_contract_declared: bool,
):
    """영상 생성 버튼의 실제 HTTP 경로도 canary와 같은 얼굴 계약을 보내야 한다."""
    import base64
    from io import BytesIO

    from PIL import Image

    source = BytesIO()
    Image.new("RGB", (2, 2), "white").save(source, "PNG")
    generated = base64.b64encode(source.getvalue()).decode()
    captured = {}

    class Response:
        status_code = 200
        headers = {}
        text = "ok"

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": generated}}]}}]}

    def fake_post(_endpoint, *, json, **_kwargs):
        captured["payload"] = json
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    references = []
    for name in (
        "channel_character_face_range_v2.png",
        "channel_character_face_scene05_v1.png",
        "channel_style_job52_briefing.png",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode())
        references.append(str(path))

    output = tmp_path / "result.png"
    assert NanaBananaProvider()._generate_gemini_api(
        "white lab coat and scientist goggles in a data laboratory",
        str(output),
        "not-a-real-key",
        references,
        model="gemini-3-pro-image",
        image_size="2K",
        max_attempts=1,
        reference_contract_declared=reference_contract_declared,
        request_audit=_audit(tmp_path),
    )

    prompt = captured["payload"]["contents"][0]["parts"][-1]["text"]
    assert "38% to 58% of the full visible eye width" in prompt
    assert "white sclera is a surrounding eye region" in prompt
    assert "warm brown iris inside the sclera" in prompt
    assert "darker pupil inside that iris" in prompt
    assert "white catchlights inside the iris or pupil" in prompt
    assert "solid black oval does not count as sclera" in prompt
    assert "larger role-matched face crop" in prompt
    assert "Do not copy or freeze its expression, costume, goggles, pose" in prompt


def test_priority_service_tier_is_a_top_level_generate_content_field(tmp_path: Path, monkeypatch):
    import base64
    from io import BytesIO

    from PIL import Image

    source = BytesIO()
    Image.new("RGB", (2, 2), "white").save(source, "PNG")
    generated = base64.b64encode(source.getvalue()).decode()
    captured = {}

    class Response:
        status_code = 200
        headers = {"x-gemini-service-tier": "priority"}
        text = "ok"

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": generated}}]}}]}

    def fake_post(_endpoint, *, json, **_kwargs):
        captured["payload"] = json
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    output = tmp_path / "priority.png"

    assert NanaBananaProvider()._generate_gemini_api(
        "priority contract", str(output), "not-a-real-key", [],
        model="gemini-3-pro-image", image_size="2K", service_tier="priority",
        max_attempts=1,
        request_audit=_audit(tmp_path),
    )
    assert captured["payload"]["serviceTier"] == "priority"
    assert "serviceTier" not in captured["payload"]["generationConfig"]
    ledger = json.loads((tmp_path / "audit.json").read_text(encoding="utf-8"))
    assert ledger["items"][0]["service_tier_observed"] == "priority"


def test_flash_high_uses_official_thinking_level_enum(tmp_path: Path, monkeypatch):
    import base64
    from io import BytesIO

    from PIL import Image

    source = BytesIO()
    Image.new("RGB", (2, 2), "white").save(source, "PNG")
    generated = base64.b64encode(source.getvalue()).decode()
    captured = {}

    class Response:
        status_code = 200
        headers = {"x-gemini-service-tier": "standard"}
        text = "ok"

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": generated}}]}}]}

    def fake_post(_endpoint, *, json, **_kwargs):
        captured["payload"] = json
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    output = tmp_path / "flash-high.png"

    assert NanaBananaProvider()._generate_gemini_api(
        "flash high contract", str(output), "not-a-real-key", [],
        model="gemini-3.1-flash-image", image_size="2K", thinking_level="high",
        max_attempts=1, request_audit=_audit(tmp_path, "gemini-3.1-flash-image"),
    )
    assert captured["payload"]["generationConfig"]["thinkingConfig"] == {
        "thinkingLevel": "High",
    }


def test_flash_image_uses_the_same_reference_edit_contract(tmp_path: Path, monkeypatch):
    import base64
    from io import BytesIO

    from PIL import Image

    source = BytesIO()
    Image.new("RGB", (2, 2), "white").save(source, "PNG")
    generated = base64.b64encode(source.getvalue()).decode()
    captured = {}

    class Response:
        status_code = 200
        headers = {}
        text = "ok"

        @staticmethod
        def json():
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": generated}}]}}]}

    def fake_post(endpoint, *, json, headers, timeout):
        captured.update(endpoint=endpoint, payload=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr("requests.post", fake_post)
    reference = tmp_path / "rejected.png"
    reference.write_bytes(b"failed-frame")
    output = tmp_path / "flash.png"

    assert NanaBananaProvider()._generate_gemini_api(
        "같은 프레임에서 잘못된 글자만 수정", str(output), "not-a-real-key", [str(reference)],
        model="gemini-3.1-flash-image", image_size="2K", max_attempts=1,
        reference_contract_declared=True,
        request_audit=_audit(tmp_path, "gemini-3.1-flash-image"),
    )
    assert captured["endpoint"].endswith("/gemini-3.1-flash-image:generateContent")
    assert captured["payload"]["generationConfig"]["imageConfig"] == {
        "aspectRatio": "16:9", "imageSize": "2K",
    }
    assert captured["timeout"] == (20.0, 180.0)
    assert captured["headers"]["X-Server-Timeout"] == "150"
