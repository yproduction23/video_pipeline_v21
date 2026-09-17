"""선택 키워드와 검증 사실에 맞는 오리지널 내러티브 플랜을 만든다."""
from __future__ import annotations

import json
import re
from typing import Any, Callable


_HOOK_TYPES = {"number_context", "belief_reversal", "time_contrast", "hidden_context", "direct_question", "human_stake"}


def _json_object(raw: str) -> dict[str, Any]:
    match = re.search(r"\{[\s\S]*\}", raw or "")
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def fallback_plan(selected_terms: list[str], verified_facts: list[dict[str, Any]], format_name: str) -> dict[str, Any]:
    """LLM 응답 장애 시 사실을 꾸미지 않는 최소 플랜을 반환한다."""
    fact_ids = [f"F{index + 1}" for index, _ in enumerate(verified_facts)]
    return {
        "plan_id": "evidence_led_fallback",
        "planner": "fallback",
        "hook_type": "number_context" if verified_facts else "human_stake",
        "selection_reason": "검증된 사실을 우선 설명하는 안전 플랜",
        "story_beats": [
            {"role": "hook", "fact_ids": fact_ids[:1], "transition_goal": "주제를 한 문장으로 선명하게 제시"},
            {"role": "context", "fact_ids": fact_ids[1:3], "transition_goal": "첫 문장의 배경을 설명"},
            {"role": "turn", "fact_ids": fact_ids[3:4], "transition_goal": "앞 설명을 바꾸는 조건을 제시"},
            {"role": "resolution", "fact_ids": fact_ids[-2:], "transition_goal": "예측 대신 다음 확인 지점을 남김"},
        ],
        "checkpoint_fact_ids": fact_ids[:3],
        "transition_rules": ["질문 뒤에는 바로 답 또는 근거를 둔다", "같은 사실은 새 역할이 없으면 반복하지 않는다"],
        "format": format_name,
    }


def plan_narrative(
    llm_call: Callable[[str, list[dict[str, str]], int], str],
    *,
    selected_terms: list[str],
    verified_facts: list[dict[str, Any]],
    candidate_context: dict[str, Any],
    format_name: str,
) -> dict[str, Any]:
    """Claude가 소재의 성격에 맞춰 구조를 선택하되, 사실을 새로 만들지 못하게 한다."""
    indexed_facts = [
        {"id": f"F{index + 1}", "fact": row.get("fact", ""), "figure": row.get("figure", "")}
        for index, row in enumerate(verified_facts)
    ]
    system = """당신은 한국 경제 영상의 내러티브 기획자입니다.
특정 창작자나 채널의 문장·훅을 모사하지 마세요. 제공 사실 밖의 수치, 인과, 예측을 만들지 마세요.
고정 순서를 강요하지 말고 소재의 성격에 맞춰 훅과 전환 위치를 선택하세요.
첫 비트(훅)에는 제공된 사실 중 가장 눈길을 끄는 사실(가장 큰 수치, 가장 의외인 반전, 가장 구체적인 인물·사건)을 배정하세요.
소재가 시장·지수와 직접 관련이 없다면, 카테고리가 경제 채널이라는 이유만으로 시장 지수·거시 지표를 story_beats에 끼워 넣지 마세요.
JSON 객체만 반환하세요."""
    prompt = f"""선택 키워드: {json.dumps(selected_terms, ensure_ascii=False)}
형식: {format_name}
후보 지표·뉴스 맥락: {json.dumps(candidate_context, ensure_ascii=False)}
검증 사실: {json.dumps(indexed_facts, ensure_ascii=False)}

아래 스키마로 기획하세요.
{{
  "plan_id":"간결한 영문 식별자",
  "hook_type":"number_context|belief_reversal|time_contrast|hidden_context|direct_question|human_stake 중 하나",
  "selection_reason":"이 소재에 이 훅이 자연스러운 이유",
  "story_beats":[{{"role":"자유로운 역할명","fact_ids":["F1"],"transition_goal":"앞 문장과 어떻게 이어지는지"}}],
  "checkpoint_fact_ids":["F1"],
  "transition_rules":["질문 직후에는 근거로 답한다"],
  "avoid":["같은 수치를 새 역할 없이 반복"]
}}
story_beats는 3~6개로 만들고 역할명은 내용에 맞게 정하세요. fact_ids는 제공 ID만 사용하세요."""
    raw = llm_call(system, [{"role": "user", "content": prompt}], 1400)
    plan = _json_object(raw)
    valid_ids = {row["id"] for row in indexed_facts}
    beats = plan.get("story_beats")
    if plan.get("hook_type") not in _HOOK_TYPES or not isinstance(beats, list) or not 3 <= len(beats) <= 6:
        return fallback_plan(selected_terms, verified_facts, format_name)
    normalized_beats = []
    for beat in beats:
        if not isinstance(beat, dict) or not str(beat.get("role", "")).strip():
            return fallback_plan(selected_terms, verified_facts, format_name)
        ids = [value for value in beat.get("fact_ids", []) if value in valid_ids]
        normalized_beats.append({
            "role": str(beat["role"]).strip()[:40],
            "fact_ids": ids,
            "transition_goal": str(beat.get("transition_goal", "")).strip()[:180],
        })
    plan["plan_id"] = re.sub(r"[^a-z0-9_-]", "", str(plan.get("plan_id", "adaptive_plan")).lower()) or "adaptive_plan"
    plan["planner"] = "claude"
    plan["story_beats"] = normalized_beats
    plan["checkpoint_fact_ids"] = [value for value in plan.get("checkpoint_fact_ids", []) if value in valid_ids][:4]
    plan["transition_rules"] = [str(value)[:180] for value in plan.get("transition_rules", []) if str(value).strip()][:5]
    plan["avoid"] = [str(value)[:180] for value in plan.get("avoid", []) if str(value).strip()][:5]
    plan["format"] = format_name
    return plan
