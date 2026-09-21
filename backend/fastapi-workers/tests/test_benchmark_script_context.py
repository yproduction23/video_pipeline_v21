"""벤치마크 참고 자료가 대본 프롬프트·플래너에 '참고용'으로만 전달되는지 고정한다."""
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


def _capture_prompt(monkeypatch, benchmark_analysis):
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
    )
    return captured["user"]


def test_prompt_includes_benchmark_points_and_no_copy_rule(monkeypatch):
    prompt = _capture_prompt(monkeypatch, ANALYSIS)

    assert "<benchmark_points>" in prompt and "익숙한 소재의 재유행" in prompt
    assert "그대로 또는 거의 그대로 쓰지 마세요" in prompt
    assert "사실 근거가 아닙니다" in prompt


def test_prompt_omits_benchmark_block_when_absent(monkeypatch):
    assert "<benchmark_points>" not in _capture_prompt(monkeypatch, None)


def test_candidate_context_passes_benchmark_analysis_through():
    assert _candidate_evidence_context([], {"benchmark_analysis": ANALYSIS})["benchmark_analysis"] == ANALYSIS
    assert _candidate_evidence_context([], None)["benchmark_analysis"] is None
    assert _candidate_evidence_context([], {"source_videos": []})["benchmark_analysis"] is None


def test_planner_allows_hook_pattern_reference_but_forbids_sentence_copy():
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured["system"] = system
        return json.dumps({"plan_id": "p", "hook_type": "human_stake", "story_beats": [
            {"role": "훅", "fact_ids": ["F1"], "transition_goal": "x"},
            {"role": "전개", "fact_ids": ["F1"], "transition_goal": "y"},
            {"role": "결말", "fact_ids": ["F1"], "transition_goal": "z"}]})

    plan_narrative(fake_call, selected_terms=["상어"], verified_facts=[{"fact": "사실", "figure": ""}],
                   candidate_context={"benchmark_points": ANALYSIS}, format_name="longform")

    assert "그대로 복제하지 마세요" in captured["system"]
    assert "유형·구조·리듬은 참고할 수 있습니다" in captured["system"]
    assert "benchmark_points" in captured["system"]
