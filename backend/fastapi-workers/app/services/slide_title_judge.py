"""데이터 그래픽 제목 검수 — 제목이 그 숫자·항목의 대상을 정확히 가리키는지 Claude가 한 번 더 확인한다.

2026-10-18 운영자 피드백(3분 15초): 삼성전자 실적(195조·107조 4000억)인데 제목이 "엔비디아 분기 실적"이었다. 제목은 직전 두 장면의 글자도
근거로 허용하는 기존 검증을 통과했다. 대상(회사·지표)이 어긋났는지는 규칙이 아니라 의미 판단이라 Claude가 현재 장면 기준으로 확인한다.
어긋났으면 현재 장면 내레이션에 글자 그대로 있는 구절로 고치고, 없으면 제목을 뺀다(숫자와 항목은 그대로)."""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 18

_SYSTEM = (
    "너는 한국 경제 해설 영상의 그래픽 제목 검수자다. 각 항목은 화면에 뜰 제목, 그래픽의 항목·값, 그 장면 내레이션(현재), 직전 장면 내레이션이다. "
    "제목이 현재 장면 내레이션이 말하는 대상(회사·지표·시점)을 정확히 가리키면 ok다. 제목에 다른 대상의 이름이 들어 있거나(예: 삼성전자 실적인데 "
    "'엔비디아 분기 실적') 값이 속한 대상과 다르면 고쳐야 한다. 고칠 때 새 제목은 '현재 장면 내레이션'에 글자 그대로 있는 "
    f"{MAX_TITLE_CHARS}자 이내 구절만 쓴다(직전 장면 글자는 쓰지 않는다). 알맞은 구절이 없으면 빈 문자열을 쓴다. 확신이 없으면 고친다.\n"
    '출력은 JSON만: {"results":[{"id":번호,"ok":true|false,"title":"교체 제목(ok면 생략)"}]}'
)


def _compact(text: object) -> str:
    return re.sub(r"[\s.,!?\"'…·\-]+", "", str(text or ""))


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 3000)
    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6", max_tokens=3000, system=system, messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def judge_titles(items: list[dict], *, llm_call=None) -> dict[int, str | None]:
    """items[{id, title, values, narration, context}] → {id: None(그대로 둔다) | 교체 제목("" 이면 제목을 뺀다)}.

    판정이 실패하면 아무것도 바꾸지 않는다(제목 검증은 보조 단계이고, 규칙 검증은 이미 통과했다)."""
    if not items:
        return {}
    lines = [
        f'{i["id"]}. 제목="{i["title"]}" / 항목·값={i["values"]} / 현재="{i["narration"]}" / 직전="{i.get("context", "")}"' for i in items
    ]
    try:
        raw = _call_llm(_SYSTEM, "\n".join(lines), llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
    except Exception as exc:
        logger.warning("그래픽 제목 검수 실패(제목은 그대로 둔다): %s", exc)
        return {}
    by_id = {i["id"]: i for i in items}
    out: dict[int, str | None] = {}
    for result in data.get("results") or []:
        try:
            ident = int(result["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if ident not in by_id or result.get("ok") is not False:
            continue
        new = str(result.get("title") or "").strip()
        narration = by_id[ident]["narration"]
        # 새 제목은 현재 장면 내레이션 원문 그대로여야 한다(아니면 제목을 뺀다).
        out[ident] = new if new and len(new) <= MAX_TITLE_CHARS and _compact(new) in _compact(narration) else ""
    return out


def _values_of(content: dict) -> list[str]:
    values = []
    for item in content.get("items") or []:
        if isinstance(item, dict):
            values.append(f'{item.get("label", "")}={item.get("value_text", item.get("value", ""))}')
        else:
            values.append(str(item))
    for row in content.get("rows") or []:
        values.append(f'{row.get("label", "")}={row.get("value", "")}')
    return values


_TITLE_KEYS = {"stat_chart": "title", "bullet_list": "title", "compare_numbers": "headline"}


def fix_titles_in_plan(plan: dict, *, llm_call=None) -> dict:
    """계획서 장면 후편집(막대·목록·비교)의 제목을 검수해 어긋난 제목을 고치거나 뺀다."""
    scenes = plan.get("scenes", [])
    uses = []
    for position, scene in enumerate(scenes):
        overlay = scene.get("overlay") or {}
        key = _TITLE_KEYS.get(overlay.get("type"))
        if key and overlay.get(key):
            context = scenes[position - 1]["narration"] if position else ""
            uses.append((overlay, key, {"id": len(uses), "title": overlay[key], "values": _values_of(overlay),
                                        "narration": scene["narration"], "context": context}))
    verdicts = judge_titles([u[2] for u in uses], llm_call=llm_call)
    changed = 0
    for overlay, key, item in uses:
        if item["id"] not in verdicts:
            continue
        if verdicts[item["id"]] == "" and overlay.get("type") == "compare_numbers":
            continue  # 비교 카드는 헤드라인이 없으면 구성이 깨진다 — 알맞은 교체 구절이 없으면 그대로 둔다
        overlay[key] = verdicts[item["id"]]
        changed += 1
    plan["title_judgement"] = {"checked": len(uses), "changed": changed}
    return plan


_TOPIC_TITLE_KEYS = {"bars": "title", "list": "title", "rows": "headline", "title_number": "title"}


def fix_titles_in_topics(topics: list, run: list[dict], *, llm_call=None) -> list:
    """시퀀스 설계(템플릿 주제)의 제목(막대·목록·숫자 카드)도 같은 기준으로 검수한다(회색 칠판 구간의 막대 제목이 어긋났었다)."""
    narrations = {s["index"]: s["narration"] for s in run}
    order = [s["index"] for s in run]
    uses = []
    for topic in topics or []:
        if not isinstance(topic, dict):
            continue
        key = _TOPIC_TITLE_KEYS.get(str(topic.get("template") or ""))
        if not key or not topic.get(key):
            continue
        scene = topic.get("scene")
        previous = order[order.index(scene) - 1] if scene in order and order.index(scene) > 0 else None
        values = _values_of(topic) + ([str(topic["number"].get("value"))] if isinstance(topic.get("number"), dict) else [])
        uses.append((topic, key, {"id": len(uses), "title": topic[key], "values": values,
                                  "narration": narrations.get(scene, " ".join(narrations.values())),
                                  "context": narrations.get(previous, "")}))
    verdicts = judge_titles([u[2] for u in uses], llm_call=llm_call)
    for topic, key, item in uses:
        if item["id"] in verdicts and not (verdicts[item["id"]] == "" and topic.get("template") in ("rows", "title_number")):
            topic[key] = verdicts[item["id"]]
    return topics
