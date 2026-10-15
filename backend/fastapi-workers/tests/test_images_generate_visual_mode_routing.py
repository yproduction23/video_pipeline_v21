"""2026-10-15 사용자 결정: /workers/images/generate가 visual_mode에 따라
images_worker(기존 캐릭터 경로) 또는 slides_worker(신규 슬라이드형)로
분기해야 한다. 이 테스트는 HTTP 라우팅만 확인한다 — 실제 생성 로직은
각 워커 자신의 테스트(test_slides_worker.py 등)가 다룬다."""
from fastapi.testclient import TestClient

import app.main as main_module

client = TestClient(main_module.app)


def _request_body(**overrides):
    body = {"tts_meta": "{}", "script_meta": "{}", "job_id": 18}
    body.update(overrides)
    return body


def test_blank_visual_mode_routes_to_images_worker(monkeypatch):
    captured = {}

    class FakeImagesWorker:
        def generate(self, **kwargs):
            captured["worker"] = "images"
            captured["kwargs"] = kwargs
            return {"job_id": 18, "scenes": [], "scene_count": 0}

    monkeypatch.setattr(main_module, "get_images_worker", lambda: FakeImagesWorker())

    response = client.post("/workers/images/generate", json=_request_body())

    assert response.status_code == 200
    assert captured["worker"] == "images"


def test_slide_visual_mode_routes_to_slides_worker(monkeypatch):
    captured = {}

    class FakeSlidesWorker:
        def generate(self, **kwargs):
            captured["worker"] = "slides"
            captured["kwargs"] = kwargs
            return {"job_id": 18, "scenes": [], "scene_count": 0}

    monkeypatch.setattr(main_module, "get_slides_worker", lambda: FakeSlidesWorker())

    response = client.post("/workers/images/generate", json=_request_body(visual_mode="SLIDE"))

    assert response.status_code == 200
    assert captured["worker"] == "slides"
    assert captured["kwargs"]["job_id"] == 18
    assert captured["kwargs"]["tts_meta_json"] == "{}"
    assert captured["kwargs"]["script_meta_json"] == "{}"


def test_visual_mode_is_case_insensitive(monkeypatch):
    captured = {}

    class FakeSlidesWorker:
        def generate(self, **kwargs):
            captured["worker"] = "slides"
            return {"job_id": 18, "scenes": [], "scene_count": 0}

    monkeypatch.setattr(main_module, "get_slides_worker", lambda: FakeSlidesWorker())

    response = client.post("/workers/images/generate", json=_request_body(visual_mode="slide"))

    assert response.status_code == 200
    assert captured["worker"] == "slides"


def test_character_visual_mode_routes_to_images_worker(monkeypatch):
    captured = {}

    class FakeImagesWorker:
        def generate(self, **kwargs):
            captured["worker"] = "images"
            return {"job_id": 18, "scenes": [], "scene_count": 0}

    monkeypatch.setattr(main_module, "get_images_worker", lambda: FakeImagesWorker())

    response = client.post("/workers/images/generate", json=_request_body(visual_mode="CHARACTER"))

    assert response.status_code == 200
    assert captured["worker"] == "images"
