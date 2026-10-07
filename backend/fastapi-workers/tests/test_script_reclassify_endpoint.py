"""2026-10-07 사용자 재현(job 14, 씬 0): 분류 버그 수정 이전에 승인된
대본은 옛 scene_type·art_direction을 그대로 쓴다. 이미 승인된 대본의
분류 메타데이터만 최신 코드로 다시 계산하는 엔드포인트를 추가한다."""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _stale_scene() -> dict:
    return {
        "title": "장면 001",
        "section": "intro",
        "content": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "text": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "text_for_tts": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "screen_texts": ["저 군대 안 갑니다!"],
        "archetype": "briefing_podium",
        "scene_type": "metric",
        "art_direction": {
            "family": "hero_metaphor",
            "topic": "finance",
            "setting": "premium Korean finance editorial studio",
            "props": ["financial chart silhouette", "briefing screen"],
        },
    }


def test_reclassify_endpoint_refreshes_metadata_without_touching_narration():
    response = client.post("/workers/script/reclassify-scenes", json={"sections": [_stale_scene()]})

    assert response.status_code == 200
    scene = response.json()["sections"][0]
    assert scene["scene_type"] == "general"
    assert scene["art_direction"]["topic"] != "finance"
    assert scene["text_for_tts"] == _stale_scene()["text_for_tts"]
    assert scene["archetype"] == "briefing_podium"


def test_reclassify_endpoint_rejects_empty_sections():
    response = client.post("/workers/script/reclassify-scenes", json={"sections": []})

    assert response.status_code == 400
