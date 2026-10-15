"""슬라이드형(SLIDE) 대본 구조: 하이라이트 → (고지 배너) → 기승전결.

2026-10-16 운영자 결정: 레퍼런스 영상은 핵심 결과를 먼저 압축해 보여주는 하이라이트가 있고,
그 직후 투자 고지 배너가 화면에만 뜬다(내레이션에는 고지 문구를 쓰지 않는다).
이 구조는 슬라이드형 Job에만 적용하며 캐릭터형 대본 경로는 바꾸지 않는다."""
from __future__ import annotations

import json

from app.utils.narrative_planner import HIGHLIGHT_ROLE, fallback_plan, plan_narrative
from app.workers.script_worker import (
    ScriptWorker,
    _build_highlight_meta,
    _highlight_lead_sections,
    _parse_sections,
)

FACTS = [{"fact": f"사실 {i}", "figure": str(i)} for i in range(1, 6)]


def _planner(visual_mode, beats):
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured["system"] = system
        captured["prompt"] = messages[-1]["content"]
        return json.dumps({
            "plan_id": "p", "hook_type": "number_context", "checkpoint_fact_ids": ["F1", "F2"],
            "story_beats": beats,
        })

    plan = plan_narrative(
        fake_call, selected_terms=["코스피"], verified_facts=FACTS, candidate_context={},
        format_name="longform", visual_mode=visual_mode,
    )
    return plan, captured


PLAIN_BEATS = [
    {"role": "훅", "fact_ids": ["F1"], "transition_goal": "a"},
    {"role": "배경", "fact_ids": ["F2"], "transition_goal": "b"},
    {"role": "전환", "fact_ids": ["F3"], "transition_goal": "c"},
    {"role": "결말", "fact_ids": ["F4"], "transition_goal": "d"},
]


def test_slide_planner_requests_highlight_structure_and_injects_missing_beat():
    plan, captured = _planner("SLIDE", PLAIN_BEATS)

    assert "하이라이트 → 기 → 승 → 전 → 결" in captured["system"]
    assert plan["structure"] == "highlight_gi_seung_jeon_gyeol"
    assert plan["story_beats"][0]["role"] == HIGHLIGHT_ROLE
    assert plan["story_beats"][0]["fact_ids"] == ["F1", "F2"]  # 체크포인트 사실로 결정론적 보강
    assert [b["role"] for b in plan["story_beats"][1:]] == ["훅", "배경", "전환", "결말"]


def test_slide_planner_keeps_claude_highlight_beat_without_duplicating():
    beats = [{"role": "하이라이트", "fact_ids": ["F5"], "transition_goal": "결과 미리보기"}, *PLAIN_BEATS[:3]]

    plan, _ = _planner("SLIDE", beats)

    assert [b["role"] for b in plan["story_beats"]].count(HIGHLIGHT_ROLE) == 1
    assert plan["story_beats"][0]["fact_ids"] == ["F5"]


def test_character_planner_is_unchanged():
    plan, captured = _planner("CHARACTER", PLAIN_BEATS)

    assert "하이라이트" not in captured["system"]
    assert "structure" not in plan
    assert [b["role"] for b in plan["story_beats"]] == ["훅", "배경", "전환", "결말"]


def test_fallback_plan_gets_highlight_beat_only_for_slide():
    slide = fallback_plan(["코스피"], FACTS, "longform", "SLIDE")
    plain = fallback_plan(["코스피"], FACTS, "longform")

    assert slide["story_beats"][0]["role"] == HIGHLIGHT_ROLE
    assert plain["story_beats"][0]["role"] == "hook"
    assert "structure" not in plain


def _body(first_title):
    def block(title, text):
        return f"## {title}\n[대사]\n{text}\n[비주얼 설명 (한국어)]\n배경\n[비주얼 프롬프트 (영어)]\nscene\n[감정]\nneutral"

    return "\n\n".join([
        block(first_title, "결론부터 말합니다. 코스피는 7퍼센트 빠졌습니다. 원인은 두 가지입니다."),
        block("기", "그날 시장을 먼저 봅니다."),
        block("승", "미국 물가가 변수였습니다."),
    ])


def test_parse_sections_marks_only_highlight_sections():
    sections = _parse_sections(_body("하이라이트"))

    assert [bool(s.get("highlight")) for s in sections] == [True, False, False]
    assert [s["title"] for s in _highlight_lead_sections(sections)] == ["하이라이트"]


def test_parse_sections_leaves_character_scripts_untouched():
    sections = _parse_sections(_body("장면 001"))

    assert all("highlight" not in s for s in sections)
    assert _highlight_lead_sections(sections) == []


def test_highlight_meta_locates_the_end_of_the_highlight_in_the_final_script():
    sections = _parse_sections(_body("하이라이트"))
    narration = "\n\n".join(s["content"] for s in sections)

    meta = _build_highlight_meta(_highlight_lead_sections(sections), narration, visual_mode="SLIDE")

    assert meta["applicable"] and meta["present"] and meta["passed"]
    assert meta["sentence_count"] == 3
    assert meta["end_located_in_final_script"] is True
    assert meta["end_text"].endswith("두 가지입니다.")
    squashed = narration.replace("\n", "").replace(" ", "")
    assert squashed[: meta["end_char"]].endswith("두가지입니다.")


def test_highlight_meta_flags_missing_or_wrong_sized_highlight_without_failing_the_script():
    plain = _parse_sections(_body("장면 001"))
    narration = "\n\n".join(s["content"] for s in plain)

    missing = _build_highlight_meta(_highlight_lead_sections(plain), narration, visual_mode="SLIDE")
    character = _build_highlight_meta([], narration, visual_mode="CHARACTER")

    assert missing["applicable"] and not missing["present"] and not missing["passed"]
    assert missing["reasons"] == ["highlight_section_missing"]
    assert character == {"version": "slide-highlight-v1", "applicable": False}


def _capture_prompt(monkeypatch, visual_mode):
    worker = ScriptWorker()
    worker._llm_provider_log = []
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured.setdefault("user", messages[-1]["content"])
        blocks = [
            f"## 장면 {i:03d}\n[대사]\n코스피 흐름과 배경을 아주 자세히 설명합니다 {i:02d}\n"
            "[비주얼 설명 (한국어)]\n배경\n[비주얼 프롬프트 (영어)]\nscene\n[감정]\nneutral"
            for i in range(1, 21)
        ]
        return "\n\n".join(blocks) + "\n\n## 메타데이터\n[추천 제목]\n제목\n[추천 썸네일]\nbg\n[더보기 설명]\n설명\n[쇼츠 대본]\n요약"

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_call)
    worker._generate_with_verified_facts(
        keyword="코스피", category_label="CUSTOM", target_minutes=1, target_chars=360,
        verified_facts=[{"fact": "사실", "figure": "1", "source_field": "테스트", "confidence": 0.9}],
        market_data={}, selected_terms=["코스피"], keyword_news=[],
        length_contract={"target_seconds": 60}, narrative_plan={"plan_id": "t", "story_beats": []},
        visual_mode=visual_mode,
    )
    return captured["user"]


def test_slide_prompt_asks_for_highlight_section_and_keeps_disclaimer_off_the_narration(monkeypatch):
    prompt = _capture_prompt(monkeypatch, "SLIDE")

    assert "## 하이라이트" in prompt
    assert "고지 문구는 화면 배너로" in prompt


def test_character_prompt_has_no_highlight_rule(monkeypatch):
    assert "## 하이라이트" not in _capture_prompt(monkeypatch, "CHARACTER")
    assert "## 하이라이트" not in _capture_prompt(monkeypatch, None)
