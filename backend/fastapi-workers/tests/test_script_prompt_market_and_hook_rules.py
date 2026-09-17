"""2026-09-17 사용자 피드백 재현: 삼성전자 '월세깡' 대본처럼 주제가 시장과
무관한데도, 글자 수를 채우려고 코스피·S&P 지수 얘기를 억지로 붙이는 문제와
훅 없이 배경 설명으로 시작하는 문제를 고쳤다. 이 테스트는 그 두 지시가
실제 프롬프트에 반영되어 있는지, 그리고 예전의 "글자수 채우려면 market
implications를 반드시 추가하라"는 문구가 되살아나지 않는지 고정한다."""
from __future__ import annotations

import json

from app.utils.narrative_planner import plan_narrative
from app.workers.script_worker import ScriptWorker


def test_narrative_planner_system_prompt_bans_forced_market_bridge_and_prioritizes_hook():
    captured = {}

    def fake_call(system, _messages, _max_tokens):
        captured["system"] = system
        return json.dumps({
            "plan_id": "test-plan",
            "hook_type": "human_stake",
            "selection_reason": "가장 구체적인 사건으로 연다",
            "story_beats": [
                {"role": "훅", "fact_ids": ["F1"], "transition_goal": "사건 제시"},
                {"role": "전개", "fact_ids": ["F2"], "transition_goal": "배경 설명"},
                {"role": "정리", "fact_ids": ["F3"], "transition_goal": "마무리"},
            ],
            "checkpoint_fact_ids": ["F1"],
            "transition_rules": [],
        }, ensure_ascii=False)

    plan_narrative(
        fake_call,
        selected_terms=["삼성전자 월세깡"],
        verified_facts=[{"fact": "월세 지원금 편법 수령 정황", "figure": "월 70만 원"}],
        candidate_context={"category": "KOSPI"},
        format_name="longform",
    )

    system_prompt = captured["system"]
    assert "가장 눈길을 끄는 사실" in system_prompt
    assert "시장 지수" in system_prompt and "끼워 넣지 마세요" in system_prompt


def _minimal_valid_script(scene_count: int) -> str:
    blocks = []
    for index in range(1, scene_count + 1):
        blocks.append(
            f"## 장면 {index:03d}\n"
            f"[대사]\n삼성전자 월세깡 사건의 세부 내용을 설명합니다 {index:02d}\n"
            "[비주얼 설명 (한국어)]\n뉴스 화면 앞에 선 캐릭터\n"
            "[비주얼 프롬프트 (영어)]\nanalyst in front of a news broadcast\n"
            "[감정]\nneutral"
        )
    body = "\n\n".join(blocks)
    meta = (
        "\n\n## 메타데이터\n"
        "[추천 제목]\n삼성전자 월세깡 논란\n"
        "[추천 썸네일]\nOffice building background\n"
        "[더보기 설명]\n상세 설명\n"
        "[쇼츠 대본]\n요약 대본"
    )
    return body + meta


def test_generate_with_verified_facts_prompt_forbids_padding_with_market_talk(monkeypatch):
    worker = ScriptWorker()
    worker._llm_provider_log = []
    captured = {}

    def fake_call(system, messages, max_tokens):
        # 첫 호출(대본 생성)만 캡처하면 충분하다.
        captured.setdefault("system", system)
        captured.setdefault("user", messages[-1]["content"])
        return _minimal_valid_script(20)

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_call)

    worker._generate_with_verified_facts(
        keyword="삼성전자 월세깡",
        category_label="KOSPI",
        target_minutes=1,
        target_chars=360,
        verified_facts=[{"fact": "월세 지원금 편법 수령 정황", "figure": "월 70만 원",
                          "source_field": "테스트", "confidence": 0.9}],
        market_data={},
        selected_terms=["삼성전자 월세깡"],
        keyword_news=[],
        length_contract={"target_seconds": 60},
        narrative_plan={"plan_id": "test", "story_beats": []},
    )

    prompt = captured["user"]
    # 옛 지시("글자수를 채우려면 market implications를 반드시 추가하라")가
    # 되살아나지 않아야 한다.
    assert "MUST add helpful background context, causal explanations, or market implications" not in prompt
    # 근거 없는 시장 언급을 명시적으로 금지해야 한다.
    assert "Do NOT add market-wide index" in prompt
    # 훅은 배경 설명이 아니라 가장 눈길을 끄는 사실로 열어야 한다.
    assert "첫 비트(훅)는 검증 사실 중 가장 눈길을 끄는 사실" in prompt
