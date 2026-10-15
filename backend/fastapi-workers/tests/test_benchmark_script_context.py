"""벤치마크 참고 자료가 대본 프롬프트·플래너에 전달되는 방식을 시각 모드별로 고정한다.

2026-10-15 운영자 결정: 벤치마크 영상과 "결"(톤·구성 순서·전개 리듬)을 맞추는
완화된 정책은 슬라이드형(visual_mode=SLIDE) Job에만 적용한다 — "기존 코드는
캐릭터형으로 갈 때 나오는 거고, 슬라이드형 코드를 새로 만들자는 것"이었다.
캐릭터형(기존 경로, visual_mode가 CHARACTER 또는 미지정)은 이전의 더 보수적인
"훅 유형만 참고, 나머지는 독자적으로" 정책을 그대로 유지한다. 둘 다 저작권 때문에
벤치마크 영상의 문장 자체를 그대로/거의 그대로 복제하는 것은 계속 금지한다."""
from __future__ import annotations

import json

from app.utils.narrative_planner import plan_narrative
from app.workers.script_worker import ScriptWorker, _candidate_evidence_context

ANALYSIS = {"topic_keyword": "부산상어 재유행", "reasons": ["익숙한 소재의 재유행"], "hook_type": "direct_question", "title_pattern": "결과 먼저"}


def _script(scene_count):
    blocks = []
    for index in range(1, scene_count + 1):
        blocks.append(
            f"## 장면 {index:03d}\n[대사]\n부산상어 재유행 흐름과 배경을 아주 자세히 설명합니다 {index:02d}\n"
            "[비주얼 설명 (한국어)]\n캐릭터\n[비주얼 프롬프트 (영어)]\nscene\n[감정]\nneutral"
        )
    meta = "\n\n## 메타데이터\n[추천 제목]\n제목\n[추천 썸네일]\nbg\n[더보기 설명]\n설명\n[쇼츠 대본]\n요약"
    return "\n\n".join(blocks) + meta


def _capture_prompt(monkeypatch, benchmark_analysis, visual_mode=None):
    worker = ScriptWorker()
    worker._llm_provider_log = []
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured.setdefault("user", messages[-1]["content"])
        return _script(20)

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_call)
    worker._generate_with_verified_facts(
        keyword="부산상어 재유행", category_label="CUSTOM", target_minutes=1, target_chars=360,
        verified_facts=[{"fact": "사실", "figure": "1", "source_field": "테스트", "confidence": 0.9}],
        market_data={}, selected_terms=["부산상어 재유행"], keyword_news=[],
        length_contract={"target_seconds": 60}, narrative_plan={"plan_id": "t", "story_beats": []},
        benchmark_analysis=benchmark_analysis,
        visual_mode=visual_mode,
    )
    return captured["user"]


def test_slide_mode_prompt_requires_matching_benchmark_feel(monkeypatch):
    prompt = _capture_prompt(monkeypatch, ANALYSIS, visual_mode="SLIDE")

    assert "<benchmark_points>" in prompt and "익숙한 소재의 재유행" in prompt
    assert "최대한 비슷하게 맞추세요" in prompt
    assert "그대로 또는 거의 그대로 옮겨 쓰지는 마세요" in prompt
    assert "사실 근거가 아닙니다" in prompt


def test_character_mode_prompt_keeps_old_conservative_rule(monkeypatch):
    prompt = _capture_prompt(monkeypatch, ANALYSIS, visual_mode="CHARACTER")

    assert "<benchmark_points>" in prompt and "익숙한 소재의 재유행" in prompt
    assert "참고하기 위한 자료입니다" in prompt
    assert "최대한 비슷하게 맞추세요" not in prompt
    assert "사실 근거가 아닙니다" in prompt


def test_unspecified_visual_mode_defaults_to_old_conservative_rule(monkeypatch):
    prompt = _capture_prompt(monkeypatch, ANALYSIS, visual_mode=None)

    assert "참고하기 위한 자료입니다" in prompt
    assert "최대한 비슷하게 맞추세요" not in prompt


def test_prompt_omits_benchmark_block_when_absent(monkeypatch):
    assert "<benchmark_points>" not in _capture_prompt(monkeypatch, None, visual_mode="SLIDE")


def test_candidate_context_passes_benchmark_analysis_through():
    assert _candidate_evidence_context([], {"benchmark_analysis": ANALYSIS})["benchmark_analysis"] == ANALYSIS
    assert _candidate_evidence_context([], None)["benchmark_analysis"] is None
    assert _candidate_evidence_context([], {"source_videos": []})["benchmark_analysis"] is None


def _run_planner(visual_mode):
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured["system"] = system
        return json.dumps({"plan_id": "p", "hook_type": "human_stake", "story_beats": [
            {"role": "훅", "fact_ids": ["F1"], "transition_goal": "x"},
            {"role": "전개", "fact_ids": ["F1"], "transition_goal": "y"},
            {"role": "결말", "fact_ids": ["F1"], "transition_goal": "z"}]})

    plan_narrative(fake_call, selected_terms=["상어"], verified_facts=[{"fact": "사실", "figure": ""}],
                   candidate_context={"benchmark_points": ANALYSIS}, format_name="longform",
                   visual_mode=visual_mode)
    return captured["system"]


def test_slide_mode_planner_matches_benchmark_flow_but_forbids_sentence_copy():
    system = _run_planner("SLIDE")

    assert "그대로 복제하지 마세요" in system
    assert "구성 순서·정보 공개 타이밍·전개 리듬" in system
    assert "결\"이 같아야 한다" in system
    assert "benchmark_points" in system


def test_character_mode_planner_keeps_old_hook_only_reference():
    system = _run_planner("CHARACTER")

    assert "그대로 복제하지 마세요" in system
    assert "hook_type 선택의 참고로만" in system
    assert "구성 순서·정보 공개 타이밍·전개 리듬" not in system
    assert "benchmark_points" in system


def test_unspecified_visual_mode_planner_defaults_to_old_hook_only_reference():
    system = _run_planner(None)

    assert "hook_type 선택의 참고로만" in system
    assert "구성 순서·정보 공개 타이밍·전개 리듬" not in system
