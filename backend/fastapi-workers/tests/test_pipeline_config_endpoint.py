"""2026-10-01 사용자 결정: 1분짜리 테스트 영상에서 TTS 실측 길이가 목표
대비 44% 초과(60초 목표, 86.6초 실측)해 자동으로 스크립트 단계로
되돌아갔다. 사용자는 "5분·10분·15분 영상도 목표에 정확히 맞출 필요
없다"며 허용 범위를 25%에서 50%로 넓히기로 했다.

POST /pipeline/config로 이 값을 바꾸려 했으나 반영되지 않았다 — 원인은
runtime_config.py의 _state/_TYPES에는 tts_duration_tolerance가 이미
있었지만, PipelineConfigUpdate Pydantic 모델에는 이 필드가 빠져 있어
요청 바디에서 조용히 버려졌다(Pydantic 기본 동작: 모델에 없는 필드는
무시). GET /pipeline/config는 값을 보여주므로 운영자가 "설정이 있다"고
믿기 쉬운데, POST로는 바꿀 수 없는 비대칭 상태였다.
"""
from fastapi.testclient import TestClient

from app.main import app
from app import runtime_config

client = TestClient(app)


def test_tts_duration_tolerance_can_be_updated_via_the_config_endpoint():
    original = runtime_config.value("tts_duration_tolerance")
    try:
        response = client.post("/pipeline/config", json={"tts_duration_tolerance": 0.5})
        assert response.status_code == 200
        assert response.json()["config"]["tts_duration_tolerance"] == 0.5
        assert runtime_config.value("tts_duration_tolerance") == 0.5
    finally:
        runtime_config.update(tts_duration_tolerance=original)
