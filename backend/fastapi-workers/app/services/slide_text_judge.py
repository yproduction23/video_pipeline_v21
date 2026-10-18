"""화면 문구 의미 검수 — 원문에서 그대로 떼어 낸 구절이라도 화면에 혼자 떠 있을 때 뜻이 뒤집히거나 오해를 부르면 쓰지 않는다.

2026-10-18 운영자 피드백(52초): 내레이션은 "단순 조정이라고 보기 어렵습니다"인데 화면에는 "단순 조정이라고"만 떠서 정반대로 읽혔다.
글자 일치 검증(원문 그대로)은 통과하지만 문장 끝의 부정·유보("보기 어렵다", "아니다")가 잘려 나가는 문제는 규칙이 아니라 의미 판단이라
Claude가 한 번에 검수한다. 어긋나면 현재 장면 내레이션에 글자 그대로 있는 구절로 바꾸고, 알맞은 구절이 없으면 쓰지 않는다."""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 16

_SYSTEM = (
    "너는 한국 경제 해설 영상의 화면 문구 검수자다. 각 항목은 화면에 혼자 크게 뜰 문구(내레이션에서 그대로 떼어 낸 구절)와 그 장면 내레이션이다. "
    "시청자는 이 문구만 읽어도 내레이션의 뜻과 어긋나지 않게 이해해야 한다. 문구가 문장 중간에서 끊겨 부정·유보·반전이 잘려 나가거나"
    "(예: '단순 조정이라고 보기 어렵다'인데 '단순 조정이라고'만 표시), 인용·가정을 단정처럼 보이게 하거나, 방향(오른다/내린다)을 "
    "알 수 없게 하면 어긋난 것이다. 어긋났으면 '현재 내레이션'에 글자 그대로 있는 "
    f"{MAX_TEXT_CHARS}자 이내이면서 뜻이 완결된 구절로 바꾼다. 알맞은 구절이 없으면 빈 문자열을 쓴다. 확신이 없으면 어긋난 것으로 본다.\n"
    '출력은 JSON만: {"results":[{"id":번호,"ok":true|false,"text":"교체 구절(ok면 생략)"}]}'
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


def judge_fragments(items: list[dict], *, llm_call=None) -> dict[int, str]:
    """items[{id, text, narration}] → {id: 교체 구절("" 이면 쓰지 않는다)}. 어긋나지 않은 항목은 결과에 없다.

    판정 호출이 실패하면 아무것도 바꾸지 않는다(규칙 검증은 이미 통과했다)."""
    if not items:
        return {}
    lines = [f'{i["id"]}. 문구="{i["text"]}" / 현재 내레이션="{i["narration"]}"' for i in items]
    try:
        raw = _call_llm(_SYSTEM, "\n".join(lines), llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
    except Exception as exc:
        logger.warning("화면 문구 의미 검수 실패(문구는 그대로 둔다): %s", exc)
        return {}
    by_id = {i["id"]: i for i in items}
    out: dict[int, str] = {}
    for result in data.get("results") or []:
        try:
            ident = int(result["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if ident not in by_id or result.get("ok") is not False:
            continue
        new = str(result.get("text") or "").strip()
        narration = by_id[ident]["narration"]
        # 새 구절은 현재 장면 내레이션 원문 그대로여야 한다(아니면 쓰지 않는다).
        out[ident] = new if new and len(new) <= MAX_TEXT_CHARS and _compact(new) in _compact(narration) else ""
    return out


def fix_key_texts_in_plan(plan: dict, *, llm_call=None) -> dict:
    """계획서 장면의 강조 문구(key_text)를 검수해 뜻이 어긋난 문구를 바꾸거나 뺀다.

    뺀 칠판 장면의 빈 구간은 시퀀스 설계가 그 구간을 말하는 순간의 원문 구절로 채운다(`_fill_blanks`)."""
    uses = []
    for scene in plan.get("scenes", []):
        overlay = scene.get("overlay") or {}
        if overlay.get("type") == "key_text" and overlay.get("text"):
            uses.append((scene, {"id": len(uses), "text": overlay["text"], "narration": scene["narration"]}))
    verdicts = judge_fragments([u[1] for u in uses], llm_call=llm_call)
    changed = 0
    for scene, item in uses:
        if item["id"] not in verdicts:
            continue
        new = verdicts[item["id"]]
        if new:
            scene["overlay"]["text"] = new
        else:
            scene["overlay"] = None  # 알맞은 구절이 없으면 쓰지 않는다(판정 없는 문장 전체를 대신 얹으면 자막과 같은 글이 된다)
        changed += 1
    plan["text_judgement"] = {"checked": len(uses), "changed": changed}
    return plan


_TOPIC_TEXT_KEYS = ("headline", "sub")


def fix_key_texts_in_topics(topics: list, run: list[dict], *, llm_call=None) -> list:
    """시퀀스 설계(headline_sub 주제)의 문구도 같은 기준으로 검수한다. 헤드라인은 바꾸기만 하고(없으면 주제가 깨진다), 보조 문구는 뺄 수 있다."""
    narrations = {s["index"]: s["narration"] for s in run}
    uses = []
    for topic in topics or []:
        if not isinstance(topic, dict) or topic.get("template") != "headline_sub":
            continue
        narration = narrations.get(topic.get("scene"))
        if not narration:
            continue
        for key in _TOPIC_TEXT_KEYS:
            text = topic.get(key)
            if isinstance(text, str) and text.strip() and _compact(text) in _compact(narration):
                uses.append((topic, key, {"id": len(uses), "text": text.strip(), "narration": narration}))
    verdicts = judge_fragments([u[2] for u in uses], llm_call=llm_call)
    for topic, key, item in uses:
        if item["id"] not in verdicts:
            continue
        new = verdicts[item["id"]]
        if new:
            topic[key] = new
        elif key == "sub":
            topic.pop(key, None)
    return topics
