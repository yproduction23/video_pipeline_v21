"""2026-10-13 사용자 재현(job 14): "대사가 많아서 지루하다, 씬을 더 잘게
쪼개야 한다"는 피드백을 받고 scene_duration_sec을 낮췄다. 그런데
_calc_scene_count()(초기 LLM 목표 씬수)는 runtime_config의 scene_duration_sec을
따르는 반면, 실제 장면 경계를 정하는 pace_sections_for_runtime() 호출은
자신만의 고정 기본값(5.5초)을 따로 써서 scene_duration_sec을 아무리 낮춰도
실제 출력 장면 길이가 안 바뀌는 상태였다. 이 테스트는 두 경로가 같은 설정값
하나로 묶여 있는지 검증한다."""
from app import runtime_config
from app.workers.script_worker import _pace_sections_using_configured_scene_duration


def _source_sections(count: int) -> list[dict]:
    return [
        {"content": f"짧은 문장 {i}번입니다.", "title": f"Scene {i}"}
        for i in range(count)
    ]


def test_pacing_uses_the_configured_scene_duration_not_a_hardcoded_default(monkeypatch):
    monkeypatch.setitem(runtime_config._state, "scene_duration_sec", 2.0)
    monkeypatch.setitem(runtime_config._state, "subtitle_max_chars", 18)

    sections = _source_sections(20)
    paced = _pace_sections_using_configured_scene_duration(sections, target_seconds=40)

    assert all(scene["runtime_pacing"]["target_seconds_per_scene"] == 2.0 for scene in paced)


def test_lowering_scene_duration_produces_more_shorter_scenes(monkeypatch):
    sections = _source_sections(20)
    monkeypatch.setitem(runtime_config._state, "subtitle_max_chars", 18)

    monkeypatch.setitem(runtime_config._state, "scene_duration_sec", 5.5)
    long_paced = _pace_sections_using_configured_scene_duration(sections, target_seconds=40)

    monkeypatch.setitem(runtime_config._state, "scene_duration_sec", 2.0)
    short_paced = _pace_sections_using_configured_scene_duration(sections, target_seconds=40)

    assert len(short_paced) > len(long_paced)
