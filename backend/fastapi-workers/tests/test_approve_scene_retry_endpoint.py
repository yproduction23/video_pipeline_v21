"""2026-10-14 사용자 재현(job 18 scene 11): needs_review 상태를 풀 수 있는
API 경로가 전혀 없었다(기존 review_retry_of/review_reopen_of 감사 체인은
budget.py의 단일 503 재시도 시나리오에서만 호출됨). 이 테스트는 그 공백을
메운 /approve-retry 엔드포인트의 HTTP 라우팅·요청 파싱·오류 변환만
확인한다. ImageRequestControl.operator_reopen() 자체 계약은
test_image_request_control.py가 다룬다."""
from fastapi.testclient import TestClient

import app.main as main_module
from app.utils.image_request_control import ImageRequestHeld

client = TestClient(main_module.app)


def test_approve_retry_endpoint_calls_through_with_path_and_body_params(monkeypatch):
    captured = {}

    def fake_approve(job_id, index, *, approved_by, override_cooldown=False):
        captured.update(job_id=job_id, index=index, approved_by=approved_by, override_cooldown=override_cooldown)
        return {"scene": f"scene:{index}", "previous_status": "needs_review",
                "previous_count": 4, "review_cycle": 1, "override_cooldown_used": override_cooldown}

    monkeypatch.setattr(main_module, "approve_scene_retry", fake_approve)

    response = client.post(
        "/workers/images/18/scenes/11/approve-retry",
        json={"job_id": 18, "approved_by": "jam", "override_cooldown": True},
    )

    assert response.status_code == 200
    assert captured == {"job_id": 18, "index": 11, "approved_by": "jam", "override_cooldown": True}
    body = response.json()
    assert body["status"] == "ok"
    assert body["review_cycle"] == 1


def test_approve_retry_endpoint_requires_approved_by_in_the_request_body():
    response = client.post("/workers/images/18/scenes/11/approve-retry", json={"job_id": 18})

    assert response.status_code == 422


def test_approve_retry_endpoint_turns_image_request_held_into_409(monkeypatch):
    def fake_approve(job_id, index, *, approved_by, override_cooldown=False):
        raise ImageRequestHeld("24시간 냉각 미경과; 즉시 재도전은 명시적 승인(override_cooldown)이 필요함")

    monkeypatch.setattr(main_module, "approve_scene_retry", fake_approve)

    response = client.post(
        "/workers/images/18/scenes/11/approve-retry", json={"job_id": 18, "approved_by": "jam"},
    )

    assert response.status_code == 409
    assert "24시간 냉각" in response.json()["detail"]


def test_approve_retry_endpoint_defaults_override_cooldown_to_false(monkeypatch):
    captured = {}

    def fake_approve(job_id, index, *, approved_by, override_cooldown=False):
        captured["override_cooldown"] = override_cooldown
        return {"scene": "scene:11", "previous_status": "needs_review",
                "previous_count": 4, "review_cycle": 1, "override_cooldown_used": False}

    monkeypatch.setattr(main_module, "approve_scene_retry", fake_approve)

    client.post("/workers/images/18/scenes/11/approve-retry", json={"job_id": 18, "approved_by": "jam"})

    assert captured["override_cooldown"] is False
