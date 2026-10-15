import json

from app import runtime_config
from app.services.scene_preparation import prepare_job_scenes


def test_restores_scenes_from_script_meta_sections(monkeypatch):
    """2026-10-15: images_worker.py와 slides_worker.py가 공유하는 장면 준비
    단계 — script_meta_json의 sections에서 장면 목록을 복원해야 한다."""
    monkeypatch.setitem(runtime_config._state, "article_evidence_auto_enabled", False)
    script_meta_json = json.dumps({
        "sections": [
            {"content": "첫 번째 장면입니다.", "start_seconds": 0, "end_seconds": 4},
            {"content": "두 번째 장면입니다.", "start_seconds": 4, "end_seconds": 8},
        ],
    })

    prepared = prepare_job_scenes(
        job_id=1, tts_meta_json=None, script_meta_json=script_meta_json,
        scenes_meta=None, budget_limit_krw=None,
    )

    assert len(prepared.scenes_meta) == 2
    assert prepared.scenes_meta[0]["content"] == "첫 번째 장면입니다."
    assert prepared.tts_subtitle_sync == {"passed": None, "reason": "tts_metadata_not_supplied"}
    assert prepared.evidence_audit == {}


def test_still_image_pilot_clamps_budget_to_test_limit(monkeypatch):
    monkeypatch.setitem(runtime_config._state, "article_evidence_auto_enabled", False)
    monkeypatch.setitem(runtime_config._state, "gemini_test_image_budget_krw", 1000)
    script_meta_json = json.dumps({
        "sections": [{"content": "장면.", "start_seconds": 0, "end_seconds": 4}],
        "still_image_pilot": True,
    })

    prepared = prepare_job_scenes(
        job_id=1, tts_meta_json=None, script_meta_json=script_meta_json,
        scenes_meta=None, budget_limit_krw=5000,
    )

    assert prepared.still_image_pilot is True
    assert prepared.budget_limit_krw == 1000


def test_empty_script_meta_returns_no_scenes(monkeypatch):
    monkeypatch.setitem(runtime_config._state, "article_evidence_auto_enabled", False)

    prepared = prepare_job_scenes(
        job_id=1, tts_meta_json=None, script_meta_json=None,
        scenes_meta=None, budget_limit_krw=None,
    )

    assert prepared.scenes_meta == []
    assert prepared.script_data == {}
