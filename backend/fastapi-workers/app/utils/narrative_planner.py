"""선택 키워드와 검증 사실에 맞는 오리지널 내러티브 플랜을 만든다."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

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


HIGHLIGHT_ROLE = "하이라이트"
SLIDE_STRUCTURE = "highlight_gi_seung_jeon_gyeol"
_SLIDE_MAX_BEATS = 9


def _is_slide(visual_mode: str | None) -> bool:
    return (visual_mode or "").upper() == "SLIDE"


def _with_slide_structure(plan: dict[str, Any], fact_ids: list[str]) -> dict[str, Any]:
    """슬라이드형(레퍼런스 구조): 첫 비트는 하이라이트(핵심 결과 미리보기), 이후 기승전결.

    Claude가 하이라이트 비트를 빠뜨리면 체크포인트 사실로 결정론적으로 보강한다.
    하이라이트의 사실은 본론에서 새 역할(근거·원인)로 다시 쓰일 수 있다.
    """
    beats = list(plan.get("story_beats") or [])
    if not beats or HIGHLIGHT_ROLE not in str(beats[0].get("role", "")):
        checkpoint = [value for value in plan.get("checkpoint_fact_ids", []) if value in fact_ids][:3]
        preview_ids = checkpoint or (beats[0].get("fact_ids", []) if beats else fact_ids[:2])
        beats.insert(0, {
            "role": HIGHLIGHT_ROLE,
            "fact_ids": list(preview_ids)[:3],
            "transition_goal": "영상 전체의 핵심 결과를 먼저 압축해 보여주고 본론으로 넘김",
        })
    plan["story_beats"] = beats[:_SLIDE_MAX_BEATS]
    plan["structure"] = SLIDE_STRUCTURE
    return plan


def fallback_plan(
    selected_terms: list[str], verified_facts: list[dict[str, Any]], format_name: str,
    visual_mode: str | None = None,
) -> dict[str, Any]:
    """LLM 응답 장애 시 사실을 꾸미지 않는 최소 플랜을 반환한다."""
    fact_ids = [f"F{index + 1}" for index, _ in enumerate(verified_facts)]
    plan = _fallback_plan_body(selected_terms, verified_facts, format_name, fact_ids)
    if _is_slide(visual_mode):
        plan = _with_slide_structure(plan, fact_ids)
    return plan


def _fallback_plan_body(
    selected_terms: list[str], verified_facts: list[dict[str, Any]], format_name: str, fact_ids: list[str],
) -> dict[str, Any]:
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
    visual_mode: str | None = None,
) -> dict[str, Any]:
    """Claude가 소재의 성격에 맞춰 구조를 선택하되, 사실을 새로 만들지 못하게 한다."""
    indexed_facts = [
        {"id": f"F{index + 1}", "fact": row.get("fact", ""), "figure": row.get("figure", "")}
        for index, row in enumerate(verified_facts)
    ]
    # 2026-10-15 사용자 결정: 벤치마크와 "결"을 맞추는 완화된 정책은 슬라이드형
    # (visual_mode=SLIDE) Job에만 적용한다. 캐릭터형(기존) 경로는 이전의 보수적
    # "훅 유형만 참고" 정책을 그대로 유지한다.
    slide = _is_slide(visual_mode)
    if slide:
        benchmark_line = (
            '후보 맥락에 benchmark_points가 있으면, 그 벤치마크 영상의 구성 순서·정보 공개 타이밍·전개 리듬(=영상 흐름)과 '
            '훅 유형을 최대한 가깝게 따라가도록 story_beats를 설계하세요. "결"이 같아야 한다는 뜻입니다 — 다만 그 영상의 '
            '문장·표현을 그대로 또는 거의 그대로 옮기지는 마세요(저작권).'
        )
    else:
        benchmark_line = (
            '후보 맥락에 benchmark_points가 있으면 그 영상이 뜨는 이유와 훅 유형을 hook_type 선택의 참고로만 쓰고, '
            '그 안의 표현을 대본에 옮기지 마세요.'
        )
    structure_line = ""
    if slide:
        structure_line = (
            f'\n이 영상은 "{HIGHLIGHT_ROLE} → 기 → 승 → 전 → 결" 구조입니다. story_beats의 첫 비트 role은 반드시 '
            f'"{HIGHLIGHT_ROLE}"로 하고, 영상 전체에서 시청자가 얻게 될 핵심 결과·반전 1~3개를 담은 사실 ID를 배정하세요'
            '(질문만 던지고 끝나는 도입이 아니라 결론의 방향을 미리 보여주는 구간). 이어지는 비트는 기승전결의 흐름에 맞는 역할로 '
            '만들되 소재에 맞게 합치거나 줄일 수 있습니다. 하이라이트에서 쓴 사실은 본론에서 근거·원인이라는 새 역할로 다시 쓸 수 있습니다. '
            'benchmark_points.outline이 있으면 그것이 벤치마크 영상의 실제 구성입니다. 본론 비트의 수·순서·역할·비중(share_pct)을 '
            'outline.sections와 최대한 같게 맞추되, outline의 주장·수치는 사실이 아니므로 fact_ids는 제공된 검증 사실에서만 배정하세요.'
        )
    system = f"""당신은 한국 경제 영상의 내러티브 기획자입니다.
다른 창작자의 문장을 그대로 복제하지 마세요. 훅의 유형·구조·리듬은 참고할 수 있습니다. 제공 사실 밖의 수치, 인과, 예측을 만들지 마세요.
{benchmark_line}{structure_line}
고정 순서를 강요하지 말고 소재의 성격에 맞춰 훅과 전환 위치를 선택하세요.
첫 비트(훅)에는 제공된 사실 중 가장 눈길을 끄는 사실(가장 큰 수치, 가장 의외인 반전, 가장 구체적인 인물·사건)을 배정하세요.
소재가 시장·지수와 직접 관련이 없다면, 카테고리가 경제 채널이라는 이유만으로 시장 지수·거시 지표를 story_beats에 끼워 넣지 마세요.
관련 지표를 여러 개 다뤄야 한다면, 모두 나열부터 한 뒤 마지막에 주제를 설명하지 마세요. 각 지표는 주제에 대한 하나의 주장을 뒷받침하는 근거로, 주제 설명과 번갈아 나오게 배치하세요.
전체 플랜은 하나의 긴장(시청자가 끝까지 보게 만드는 질문이나 불확실성)을 중심으로 짜고, 그 긴장을 어느 비트에서 해소하는지 명확히 하세요. 사실을 순서대로 나열하기만 하는 정보 목록이 되면 안 됩니다.
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
  "tension":"이 영상을 끝까지 보게 만드는 긴장·질문 한 문장",
  "story_beats":[{{"role":"자유로운 역할명","fact_ids":["F1"],"transition_goal":"앞 문장과 어떻게 이어지는지(80자 이내)"}}],
  "checkpoint_fact_ids":["F1"],
  "transition_rules":["질문 직후에는 근거로 답한다"],
  "avoid":["같은 수치를 새 역할 없이 반복"]
}}
story_beats는 {"4~9" if slide else "3~6"}개로 만들고 역할명은 내용에 맞게 정하세요. fact_ids는 제공 ID만 사용하세요.
마지막 비트의 transition_goal에는 tension을 어떻게 해소하는지 명시하세요."""
    # 슬라이드형은 비트가 많고 설명이 길어져 1,400토큰에서 JSON이 잘려 조용히 폴백된 적이 있다(20번 Job).
    raw = llm_call(system, [{"role": "user", "content": prompt}], 3000 if slide else 1400)
    plan = _json_object(raw)
    if not plan:
        logger.warning("내러티브 플랜 응답을 JSON으로 읽지 못해 폴백 플랜 사용(응답 %d자, 잘림 가능)", len(raw or ""))
    valid_ids = {row["id"] for row in indexed_facts}
    beats = plan.get("story_beats")
    max_beats = _SLIDE_MAX_BEATS if slide else 6
    if plan.get("hook_type") not in _HOOK_TYPES or not isinstance(beats, list) or not 3 <= len(beats) <= max_beats:
        return fallback_plan(selected_terms, verified_facts, format_name, visual_mode)
    normalized_beats = []
    for beat in beats:
        if not isinstance(beat, dict) or not str(beat.get("role", "")).strip():
            return fallback_plan(selected_terms, verified_facts, format_name, visual_mode)
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
    if slide:
        plan = _with_slide_structure(plan, sorted(valid_ids))
    return plan
