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
    MAX_BARE_STOCK_SECONDS,
    MAX_BOARD_RUN_SECONDS,
    MAX_PHOTO_SCENE_RATIO,
    MIN_FOOTAGE_RATIO,
    MIN_MEAN_BG_RUN_SECONDS,
    MIN_STOCK_OVERLAY_RATIO,
    _SYSTEM_PROMPT,
    _call_llm,
    _clean_queries,
    build_edit_plan,
    fallback_key_phrase,
    refresh_plan,
)
from app.services.slide_overlay import validate_overlay_plan
from app.services.slide_sequence import attach_sequences

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


def replan_scenes(plan: dict, items: list[dict], *, llm_call=None, source: str = "operator") -> dict | None:
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
            reason=(("운영자 수정: " if source == "operator" else "자동 보정: ") + (notes[index] or "다시 계획"))[:60],
            overlay=overlay,
        )
        scene["revised" if source == "operator" else "auto_repaired"] = True
        if background == "stock":
            scene["fallback_key_text"] = fallback_key_phrase(scene["narration"])
        else:
            scene.pop("fallback_key_text", None)

    # 사진·스톡 상한은 전체 기준으로 다시 적용한다(운영자가 수정한 장면을 먼저 지킨다).
    all_scenes = updated["scenes"]
    limit = max(1, int(len(all_scenes) * MAX_PHOTO_SCENE_RATIO))
    footage = [s for s in all_scenes if s["background"] == "photo"]
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

    if source == "operator":
        updated["review_notes"] = {**(plan.get("review_notes") or {}), **{str(i): n for i, n in notes.items()}}
        updated["revised_at"] = time.time()
    refresh_plan(updated, demoted)
    return attach_sequences(updated, llm_call=llm_call)


# ---- 계획서 자동 보정 -----------------------------------------------------------------

MAX_REPAIR_ROUNDS = 2
_REPAIR_NOTE = (
    "칠판(어두운 회색 배경)이 {seconds}초 이상 연속돼 지루하다(레퍼런스는 최대 30초). 이 장면은 주제·분위기에 어울리는 "
    "상징적인 스톡 영상(stock_queries 2개)으로 바꾼다. 후편집은 문장에 수치·주장·목록이 있을 때만 가볍게 얹는다."
)
_REPAIR_FOOTAGE_NOTE = (
    "스톡 영상 비중이 레퍼런스(약 2/3)보다 낮다. 이 장면은 주제·분위기에 어울리는 상징적인 스톡 영상(stock_queries "
    "2개)으로 바꾼다. 후편집은 문장에 수치·주장·목록이 있을 때만 가볍게 얹는다."
)


_REPAIR_STOCK_OVERLAY_NOTE = (
    "이 장면은 스톡 영상(background는 stock 그대로, stock_queries 유지)인데 후편집이 없어 밋밋하다. 문장의 숫자·핵심 주장·"
    "목록·비교·막대 중 하나를 overlay로 얹는다(기사 증거가 있으면 article_card). 숫자가 없으면 핵심어 key_text(16자 이내)를 쓴다."
)

_REPAIR_MERGE_NOTE = (
    "앞뒤가 스톡 영상이라 이 장면만 칠판으로 끼어 배경이 너무 잦게 바뀐다(레퍼런스는 평균 12초 이상 유지). 이 장면도 "
    "같은 흐름의 스톡 영상(stock_queries 2개)으로 이어 붙인다. 후편집은 문장에 수치·주장이 있으면 영상 위에 얹는다."
)


def repair_targets(plan: dict) -> list[dict]:
    """레퍼런스 기준 위반(칠판 연속, 스톡 하한)을 바로잡을 장면과 지시를 정한다. 결정론적이다."""
    quality = plan.get("quality") or {}
    scenes = {s["index"]: s for s in plan["scenes"]}
    marked: dict[int, str] = {}
    for run in quality.get("long_board_runs", []):
        acc = 0.0
        for index in run["scenes"]:
            acc += scenes[index]["duration"]
            if acc > MAX_BOARD_RUN_SECONDS:
                marked[index] = _REPAIR_NOTE.format(seconds=int(run["seconds"]))
                acc = 0.0
    total = sum(s["duration"] for s in scenes.values()) or 1.0
    footage = sum(s["duration"] for s in scenes.values() if s["background"] != "board")
    footage += sum(scenes[i]["duration"] for i in marked)
    if footage / total < MIN_FOOTAGE_RATIO:
        ordered = plan["scenes"]
        by_pos = {s["index"]: p for p, s in enumerate(ordered)}

        def next_to_footage(scene: dict) -> bool:
            p = by_pos[scene["index"]]
            near = [ordered[q] for q in (p - 1, p + 1) if 0 <= q < len(ordered)]
            return any(n["background"] != "board" or n["index"] in marked for n in near)

        # 이미 스톡인 덩어리에 이어 붙여 스톡 구간을 길게 만든다(레퍼런스는 평균 17초 연속). 없으면 하나 건너 하나.
        for scene in [s for s in ordered if s["background"] == "board" and next_to_footage(s)] +                 [s for s in ordered if s["background"] == "board"][1::2]:
            if footage / total >= MIN_FOOTAGE_RATIO:
                break
            if scene["index"] in marked:
                continue
            marked[scene["index"]] = _REPAIR_FOOTAGE_NOTE
            footage += scene["duration"]
    # 배경이 너무 잦게 바뀌면(스톡 사이에 낀 1장면짜리 칠판) 그 장면도 앞뒤 스톡 흐름에 이어 붙인다.
    if "bg_switch_too_frequent" in quality.get("violations", []):
        ordered = plan["scenes"]
        block_total = 1 + sum(1 for a, b in zip(ordered, ordered[1:]) if a["background"] != b["background"])
        for position in range(1, len(ordered) - 1):
            scene = ordered[position]
            prev_bg, next_bg = ordered[position - 1]["background"], ordered[position + 1]["background"]
            if scene["background"] != "board" or scene["index"] in marked:
                continue
            if prev_bg == "board" or next_bg == "board" or marked.get(ordered[position - 1]["index"]):
                continue
            if total / max(block_total, 1) >= MIN_MEAN_BG_RUN_SECONDS:
                break
            marked[scene["index"]] = _REPAIR_MERGE_NOTE
            block_total -= 2  # 양쪽 스톡 덩어리가 하나로 합쳐진다
    # 후편집 없이 스톡만 길게 이어지거나(12초 초과) 스톡 위 후편집 비율이 낮으면, 배경은 그대로 두고 후편집만 얹을 장면을 고른다.
    bare_scenes = [s for s in plan["scenes"] if s["background"] == "stock" and not s.get("overlay")]
    for run in quality.get("bare_stock_runs", []):
        acc = 0.0
        for index in run["scenes"]:
            acc += scenes[index]["duration"]
            if acc > MAX_BARE_STOCK_SECONDS and index not in marked:
                marked[index] = _REPAIR_STOCK_OVERLAY_NOTE
                acc = 0.0
    stock_scenes = [s for s in plan["scenes"] if s["background"] == "stock"]
    if len(stock_scenes) >= 3:
        have = sum(1 for s in stock_scenes if s.get("overlay") or s["index"] in marked)
        # 남은 빈 스톡 장면 중 하나 건너 하나씩 골라 비율 하한을 채운다(연속해서 몰리지 않게).
        for scene in [s for s in bare_scenes if s["index"] not in marked][::2] + \
                [s for s in bare_scenes if s["index"] not in marked][1::2]:
            if have / len(stock_scenes) >= MIN_STOCK_OVERLAY_RATIO:
                break
            marked[scene["index"]] = _REPAIR_STOCK_OVERLAY_NOTE
            have += 1
    return [{"index": i, "note": note} for i, note in sorted(marked.items())]


def build_edit_plan_with_repair(
    briefs: dict[int, dict], *, job_id: int, highlight_end_index: int | None = None, llm_call=None,
) -> dict | None:
    """계획서를 만든 뒤 레퍼런스 기준 위반을 최대 MAX_REPAIR_ROUNDS번 부분 재계획으로 바로잡는다.

    끝까지 남은 위반은 plan["quality"]에 그대로 기록한다(조용히 통과시키지 않는다)."""
    plan = build_edit_plan(briefs, job_id=job_id, highlight_end_index=highlight_end_index, llm_call=llm_call)
    if not plan:
        return None
    rounds = 0
    for _ in range(MAX_REPAIR_ROUNDS):
        items = repair_targets(plan)
        if not items:
            break
        repaired = replan_scenes(plan, items, llm_call=llm_call, source="auto")
        if not repaired:
            break
        plan = repaired
        rounds += 1
    plan["auto_repair_rounds"] = rounds
    # 회색 배경 구간마다 장면 경계를 넘어 이어지는 모션 시퀀스를 설계한다(실패해도 장면별 후편집으로 대체).
    return attach_sequences(plan, llm_call=llm_call)
