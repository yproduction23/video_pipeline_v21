"""2026-10-13 사용자 요청: UI → Spring → FastAPI 실제 연결을 검증한다.
accept_held_scene_as_is() 자체 계약은 test_accept_held_scene.py가 다루고,
여기서는 HTTP 라우팅·요청 파싱·오류 변환만 확인한다."""
from fastapi.testclient import TestClient

import app.main as main_module
from app.utils.image_request_control import ImageRequestHeld

client = TestClient(main_module.app)


def test_accept_endpoint_calls_through_with_path_and_body_params(monkeypatch):
    captured = {}

    def fake_accept(job_id, index, *, text, accepted_by=None, section=None):
        captured.update(job_id=job_id, index=index, text=text, accepted_by=accepted_by, section=section)
        return {
            "status": "COMPLETE", "job_id": job_id,
            "scenes": [{"index": index, "image_path": "/app/data/jobs/14/images/scene_002.png",
                        "text": text, "generation_method": "manual_accept_held"}],
            "held_scenes": [], "requires_manual_review": False, "review_reasons": [],
        }

    monkeypatch.setattr(main_module, "accept_held_scene_as_is", fake_accept)

    response = client.post(
        "/workers/images/14/scenes/2/accept",
        json={"job_id": 14, "accepted_by": "jam", "text": "전역모 내레이션", "section": "intro"},
    )

    assert response.status_code == 200
    assert captured == {
        "job_id": 14, "index": 2, "text": "전역모 내레이션", "accepted_by": "jam", "section": "intro",
    }
    body = response.json()
    assert body["requires_manual_review"] is False
    assert body["scenes"][0]["generation_method"] == "manual_accept_held"


def test_accept_endpoint_requires_narration_text_in_the_request_body():
    response = client.post("/workers/images/14/scenes/2/accept", json={"job_id": 14})

    assert response.status_code == 422


def test_accept_endpoint_turns_image_request_held_into_409(monkeypatch):
    def fake_accept(job_id, index, *, text, accepted_by=None, section=None):
        raise ImageRequestHeld(f"scene {index}은 현재 검토 대기 목록에 없습니다")

    monkeypatch.setattr(main_module, "accept_held_scene_as_is", fake_accept)

    response = client.post(
        "/workers/images/14/scenes/2/accept", json={"job_id": 14, "text": "전역모 내레이션"},
    )

    assert response.status_code == 409
    assert "검토 대기 목록" in response.json()["detail"]
