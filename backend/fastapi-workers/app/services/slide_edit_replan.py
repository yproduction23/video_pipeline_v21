"""편집 계획서의 "문제 장면 정리 → 해당 장면만 다시 계획".

2026-10-16 운영자 요청: 계획서를 보고 문제 되는 부분·수정할 부분을 정리할 수 있어야 한다. 다만 자동화가
목적이라 필드를 손으로 고치는 편집기가 아니라, 문제 장면에 말로 수정 지시를 적으면 Claude가 그 장면만 다시
계획하는 방식으로 한다. 다시 계획한 오버레이도 원문 일치·계산 검산을 같은 검증기로 다시 통과해야 한다.
"""
from __future__ import annotations

import json
import logging
import re
import time

from app.services.slide_edit_plan import (
    BACKGROUNDS,
    MAX_PHOTO_SCENE_RATIO,
    _SYSTEM_PROMPT,
    _call_llm,
    _clean_queries,
    fallback_key_phrase,
    refresh_plan,
)
from app.services.slide_overlay import validate_overlay_plan

logger = logging.getLogger(__name__)

NL = chr(10)
MAX_NOTE_CHARS = 200

_REPLAN_NOTE = (
    "아래는 이미 만든 편집 계획서에서 운영자가 '문제'로 표시한 장면과 수정 지시다. 지시를 반영해 그 장면들만 "
    "다시 계획한다(다른 장면은 건드리지 않는다). 같은 규칙과 출력 형식을 쓴다."
)


def _overlay_label(overlay: dict | None) -> str:
    overlay = overlay or {}
    return str(overlay.get("text") or overlay.get("value") or overlay.get("title") or overlay.get("headline") or "")


def replan_scenes(plan: dict, items: list[dict], *, llm_call=None) -> dict | None:
    """운영자가 표시한 장면(index, note)만 다시 계획한다. 실패하면 None(기존 계획서를 유지한다)."""
    scenes = {s["index"]: s for s in plan.get("scenes", [])}
    notes: dict[int, str] = {}
    for item in items or []:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        if index in scenes:
            notes[index] = str(item.get("note") or "").strip()[:MAX_NOTE_CHARS]
    if not notes:
        return None

    ordered = sorted(scenes)
    briefs: dict[int, dict] = {}
    for position, index in enumerate(ordered):
        context = " ".join(scenes[i]["narration"] for i in ordered[max(0, position - 2):position])
        briefs[index] = {
            "narration": scenes[index]["narration"], "context": context, "duration": scenes[index]["duration"],
        }

    lines = [f"{i} ({scenes[i]['duration']:.1f}초): {scenes[i]['narration']}" for i in ordered]
    targets = []
    for index, note in sorted(notes.items()):
        current = scenes[index]
        overlay = current.get("overlay") or {}
        targets.append(
            f"- 장면 {index}: 현재 배경={current['background']}, 현재 후편집={overlay.get('type')}"
            f"({_overlay_label(overlay)}) / 운영자 지시: {note or '(문제 표시만 함 — 더 나은 구성으로)'}"
        )
    prompt = (
        "승인 내레이션 전체(번호 (길이): 문장):" + NL + NL.join(lines)
        + NL + NL + _REPLAN_NOTE + NL + NL.join(targets)
        + NL + NL + "다시 계획할 장면만 JSON으로 반환:"
    )
    try:
        raw = _call_llm(_SYSTEM_PROMPT, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        entries = {int(e["index"]): e for e in (data.get("scenes") or []) if isinstance(e, dict) and "index" in e}
    except Exception as exc:
        logger.warning("편집 계획서 부분 재계획 실패: %s", exc)
        return None
    entries = {i: e for i, e in entries.items() if i in notes}
    if not entries:
        return None

    raw_overlays = [{**e["overlay"], "index": i} for i, e in entries.items() if isinstance(e.get("overlay"), dict)]
    validated = validate_overlay_plan(raw_overlays, {i: briefs[i] for i in entries})
    updated = json.loads(json.dumps(plan))  # 깊은 복사: 실패해도 원본은 그대로
    by_index = {s["index"]: s for s in updated["scenes"]}
    for index, entry in entries.items():
        scene = by_index[index]
        background = str(entry.get("background") or "").strip().lower()
        if background not in BACKGROUNDS:
            background = "board"
        board = background == "board"
        overlay = validated.get(index)
        if board and not overlay:
            overlay = {"type": "key_text", "text": fallback_key_phrase(scene["narration"]), "icon": None,
                       "source": "fallback_phrase"}
        if overlay:
            overlay = dict(overlay)
            overlay["explain"] = board
        scene.update(
            background=background,
            stock_queries=_clean_queries(entry.get("stock_queries")) if background == "stock" else [],
            photo_brief=str(entry.get("photo_brief") or "").strip()[:240] if background == "photo" else "",
            reason=("운영자 수정: " + (notes[index] or "다시 계획"))[:60],
            overlay=overlay,
            revised=True,
        )
        if background == "stock":
            scene["fallback_key_text"] = fallback_key_phrase(scene["narration"])
        else:
            scene.pop("fallback_key_text", None)

    # 사진·스톡 상한은 전체 기준으로 다시 적용한다(운영자가 수정한 장면을 먼저 지킨다).
    all_scenes = updated["scenes"]
    limit = max(1, int(len(all_scenes) * MAX_PHOTO_SCENE_RATIO))
    footage = [s for s in all_scenes if s["background"] != "board"]
    demoted = list((updated.get("summary") or {}).get("demoted_to_board", []))
    if len(footage) > limit:
        highlight_end = updated.get("highlight_end_index")

        def keep_priority(scene: dict) -> tuple:
            return (
                0 if scene.get("revised") else 1,
                0 if highlight_end is not None and scene["index"] <= highlight_end else 1,
                0 if scene["background"] == "stock" else 1,
                scene["index"],
            )

        for scene in sorted(footage, key=keep_priority)[limit:]:
            scene.update(background="board", stock_queries=[], photo_brief="")
            scene.pop("fallback_key_text", None)
            if scene.get("overlay"):
                scene["overlay"]["explain"] = True
            else:
                scene["overlay"] = {"type": "key_text", "text": fallback_key_phrase(scene["narration"]),
                                    "icon": None, "explain": True, "source": "fallback_phrase"}
            demoted.append(scene["index"])

    updated["review_notes"] = {**(plan.get("review_notes") or {}), **{str(i): n for i, n in notes.items()}}
    updated["revised_at"] = time.time()
    refresh_plan(updated, demoted)
    return updated
