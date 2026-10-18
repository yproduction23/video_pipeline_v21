"""아이콘 적합성 판정 — Claude가 고른 아이콘이 그 순간 말하는 대상을 직접 그린 것인지 한 번 더 확인한다.

2026-10-17 운영자 피드백: 톱니바퀴(구조적 취약점)·벨(5.3% 금리)처럼 뜻이 어긋난 장식 아이콘이 나온다. 단어 겹침 같은 규칙이 아니라
Claude가 의미로 판정한다(내용 적합성은 규칙이 아니라 판단이 필요하다). 판정이 실패하면 아무 아이콘도 통과시키지 않는다(글만 남긴다).
회사 로고는 이름이 정확히 일치할 때만 쓰므로 판정하지 않는다."""
from __future__ import annotations

import json
import logging
import os
import re

from app.services.slide_icons import ICON_HINTS
from app.services.slide_logos import PREFIX as LOGO_PREFIX
from app.services.slide_motifs import PREFIX as MOTIF_PREFIX

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM = (
    "너는 한국 경제 해설 영상의 아이콘 검수자다. 각 항목은 화면에 뜰 아이콘 이름, 아이콘 옆에 쓰일 글, 그 장면의 내레이션이다. "
    "아이콘이 지금 말하는 대상을 직접 그린 그림이면 ok다(예: '반도체 업종'→chip, '은행'→bank, '하락'→chart_down). "
    "상징·장식·막연한 연상(예: '구조적 취약점'→톱니바퀴, '금리 5.3%'→벨, '진단'→돋보기)이거나 뜻이 약간이라도 어긋나면 거절한다. "
    "확신이 없으면 거절한다.\n"
    '출력은 JSON만: {"ok": [통과한 항목 번호, ...]}'
)


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 2000)
    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6", max_tokens=2000, system=system, messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def _hint(icon: str) -> str:
    if icon.startswith(MOTIF_PREFIX):
        return icon[len(MOTIF_PREFIX):]
    return ICON_HINTS.get(icon, "")


def judge_icons(items: list[dict], *, llm_call=None) -> set[int]:
    """items[{id, icon, text, narration}] 중 아이콘이 적합하다고 판정된 id 집합. 실패하면 빈 집합."""
    if not items:
        return set()
    lines = [
        f'{item["id"]}. 아이콘={item["icon"]}({_hint(item["icon"])}) / 글="{item["text"]}" / 내레이션="{item["narration"]}"'
        for item in items
    ]
    try:
        raw = _call_llm(_JUDGE_SYSTEM, "\n".join(lines), llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        valid = {item["id"] for item in items}
        return {int(i) for i in data.get("ok", []) if int(i) in valid}
    except Exception as exc:
        logger.warning("아이콘 적합성 판정 실패(아이콘 없이 진행): %s", exc)
        return set()


def _is_judged(icon: object) -> bool:
    return isinstance(icon, str) and bool(icon) and not icon.startswith(LOGO_PREFIX)


def strip_unjudged_icons_from_plan(plan: dict, *, llm_call=None) -> dict:
    """계획서 장면 후편집의 아이콘(강조·큰 숫자·비교·계산식·흐름 노드)을 판정하고 어긋난 것은 뺀다. 글은 그대로다."""
    uses: list[tuple[dict, str, str, str]] = []  # (컨테이너, 키, 글, 내레이션)
    for scene in plan.get("scenes", []):
        overlay = scene.get("overlay") or {}
        text = str(overlay.get("text") or overlay.get("label") or overlay.get("headline") or overlay.get("title") or "")
        if _is_judged(overlay.get("icon")):
            uses.append((overlay, "icon", text, scene["narration"]))
        for node in overlay.get("nodes") or []:
            if _is_judged(node.get("icon")):
                uses.append((node, "icon", str(node.get("label") or ""), scene["narration"]))
    items = [{"id": n, "icon": box[key], "text": text, "narration": narration} for n, (box, key, text, narration) in enumerate(uses)]
    approved = judge_icons(items, llm_call=llm_call)
    removed = 0
    for n, (box, key, _text, _narration) in enumerate(uses):
        if n not in approved:
            box[key] = None
            removed += 1
    # 흐름은 아이콘이 일부만 있으면 구조가 깨지므로 하나라도 빠지면 모두 뺀다.
    for scene in plan.get("scenes", []):
        nodes = (scene.get("overlay") or {}).get("nodes") or []
        if nodes and any(not n.get("icon") for n in nodes):
            for node in nodes:
                node["icon"] = None
    plan["icon_judgement"] = {"checked": len(uses), "removed": removed}
    return plan


def strip_unjudged_icons_from_topics(topics: list, run: list[dict], *, llm_call=None) -> list:
    """시퀀스 설계(템플릿 주제)의 아이콘을 판정해, 어긋난 아이콘을 뺀다. 같은 구조 규칙은 템플릿이 다시 맞춘다."""
    narrations = {s["index"]: s["narration"] for s in run}
    uses: list[tuple[dict, str, str, str]] = []
    flow_groups: list[list[int]] = []
    for topic in topics or []:
        if not isinstance(topic, dict):
            continue
        narration = narrations.get(topic.get("scene"), " ".join(narrations.values()))
        if _is_judged(topic.get("icon")):
            number = topic.get("number") if isinstance(topic.get("number"), dict) else {}
            label = str(topic.get("title") or number.get("label") or topic.get("caption") or "")
            uses.append((topic, "icon", label, narration))
        for side in ("a", "b"):
            item = topic.get(side)
            if isinstance(item, dict) and _is_judged(item.get("icon")):
                uses.append((item, "icon", str(item.get("label") or ""), narration))
        group: list[int] = []
        for node in topic.get("nodes") or []:
            if isinstance(node, dict) and _is_judged(node.get("icon")):
                group.append(len(uses))
                uses.append((node, "icon", str(node.get("label") or ""), narration))
        if group:
            flow_groups.append(group)
    items = [{"id": n, "icon": box[key], "text": text, "narration": narration} for n, (box, key, text, narration) in enumerate(uses)]
    approved = judge_icons(items, llm_call=llm_call)
    for n, (box, key, _text, _narration) in enumerate(uses):
        if n not in approved:
            box[key] = None
    for group in flow_groups:          # 흐름은 일부만 통과하면 모두 뺀다(같은 구조)
        if any(n not in approved for n in group):
            for n in group:
                uses[n][0]["icon"] = None
    return topics
