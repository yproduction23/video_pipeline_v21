"""슬라이드형 "모션 시퀀스" — 회색 배경 구간을 장면 경계를 넘어 이어지는 하나의 애니메이션으로 만든다.

2026-10-16 운영자 요청: 레퍼런스(부동산 영상 10~33초)는 회색 배경이 약 23초간 끊기지 않고 이어지면서 요소가
문장 흐름에 맞춰 하나씩 등장·유지·교체된다("10억 원" + 돈뭉치 → "현재·상위 11.8%" → "과거·상위 7.9%"가 이어서 추가 →
건물 → 화살표 → 돈뭉치 → 큰 숫자). 장면마다 독립 카드를 얹던 방식에는 이 개념이 없었다.

- 계획: 연속된 회색 배경 장면(run)마다 Claude가 요소 타임라인(종류·위치·등장 구절·동작)을 설계한다. 화면 글자는
  기존 검증기(원문 일치·계산 검산)를 요소마다 다시 통과해야 하고, 실패하면 장면별 후편집에서 결정론적으로 만든다.
- 렌더: 시퀀스 전체 시간축(t)에서 프레임을 Pillow로 직접 그려(등장 모션·화살표 그리기·숫자 카운트업·막대 성장)
  장면 구간만 잘라 장면마다 mp4로 만든다. 장면 클립 계약(TTS·자막·합성)은 그대로이고 화면만 이어진다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw

from app.services.slide_icons import ACCENT, ICON_HINTS, ICON_NAMES, draw_icon
from app.services.slide_templates import expand_topic
from app.services.slide_overlay import (
    _FONT_BOLD, _FONT_DISPLAY, EMPHASIS_RED, _font, _in_narration, spell_like_narration as _spell_like_narration,
    to_percent_symbol, validate_overlay_plan,
)

logger = logging.getLogger(__name__)

CANVAS = (1920, 1080)
FPS = 30
SEQUENCE_VERSION = "slide-sequence-v3"
RED = EMPHASIS_RED
GRAY_TEXT = (190, 192, 198)
WHITE = (255, 255, 255)

# 화면 위치(중심 좌표). 레퍼런스: 문구는 위·가운데, 아이콘+숫자는 좌우, 행은 오른쪽에 쌓인다.
SLOTS = {
    "center": (960, 540), "top": (960, 250), "bottom": (960, 780),
    "left": (520, 540), "right": (1400, 540),
    "upper_left": (520, 320), "upper_right": (1400, 320),
    "lower_left": (520, 740), "lower_right": (1400, 740),
}
KINDS = {"text", "number", "icon", "arrow", "bars", "list", "rows", "flow"}
ANIMS = {"rise", "draw", "count", "grow"}  # 튀어나옴(pop)은 산만해 쓰지 않는다
# 한 요소가 나타나는 데 걸리는 시간(초)
ENTER_SECONDS = {"text": 0.45, "number": 0.9, "icon": 0.4, "arrow": 0.7, "bars": 0.9, "row": 0.5, "node": 0.45,
                 "item": 0.4, "title": 0.4}
EXIT_FADE_SECONDS = 0.25
# 하단 자막(영상 하단 약 y 900 이후)과 겹치지 않도록 요소 아래쪽 한계, 좌우·모서리 자리의 최대 폭.
SUBTITLE_SAFE_BOTTOM = 890
SIDE_SLOT_MAX_WIDTH = 820
WIDE_SLOT_MAX_WIDTH = 1500
_SIDE_SLOTS = {"left", "right", "upper_left", "upper_right", "lower_left", "lower_right"}
OPENING_LATEST_SECONDS = 0.3
# 2026-10-16 운영자 요청: 구조와 애니메이션 순서는 복잡하지 않고 단순하며 보기 편해야 한다.
# 20번 Job 1:17~1:33 재현(2초마다 같은 자리에서 서로 다른 아이콘 교체, 빈 화면) 뒤 레퍼런스 10~33초를 다시 분석해
# 개수 제한이 아니라 "주제 단위로 쌓고, 주제가 바뀔 때만 비운다"로 바꿨다(레퍼런스는 5~6개가 동시에 있어도 짝지어져 읽힌다).
MIN_TOPIC_SECONDS = 4.0              # 주제(한 가지 이야기)의 최소 길이: 이보다 짧으면 앞 주제에 합친다
MAX_TOPIC_SECONDS = 11.0             # 한 주제가 이보다 길게 정지해 있으면 재설계·보충한다
FIXED_TOPIC_MIN_SECONDS = 2.5        # 템플릿 주제는 합치지 않으므로 이보다 짧으면 버린다
MAX_ELEMENTS_PER_TEMPLATE = 8
MIN_ELEMENT_SECONDS = 1.5            # 주제가 끝나기 전 이만큼도 안 남으면 새 요소를 만들지 않는다
MIN_UPDATE_SECONDS = 1.5             # 같은 종류 숫자를 제자리에서 바꿀 때 앞 값이 머무는 최소 시간
MAX_ELEMENTS_PER_TOPIC = 5
RECT_MARGIN = 24                     # 요소 사이 최소 간격(px)
_WIDE_KINDS = {"bars", "list", "rows", "flow"}            # 화면 전체를 쓰는 요소(center 전용)
_ANCHOR_KINDS = {"text", "number", "bars", "list", "rows", "flow"}  # 주제에는 글자·숫자가 하나 이상 있어야 한다
_UPDATABLE_KINDS = {"number", "text"}                     # 같은 종류끼리만 제자리 교체 허용(아이콘은 교체 금지)
_SLOT_FALLBACKS = ["left", "right", "upper_left", "upper_right", "lower_left", "lower_right", "top", "bottom"]
MAX_TEXT_CHARS = 16                  # 자막과 같은 문장을 되풀이하지 않도록 핵심어만

_KIND_TO_OVERLAY = {
    "text": "key_text", "number": "big_number", "bars": "stat_chart", "list": "bullet_list",
    "rows": "compare_numbers", "flow": "flow",
}
_OVERLAY_TO_KIND = {value: key for key, value in _KIND_TO_OVERLAY.items()}


# ---------------------------------------------------------------- 계획(Claude 설계 + 검증) ----

_SEQUENCE_SYSTEM = (
    "너는 한국 경제 해설 유튜브의 모션그래픽 연출가다. 어두운 회색 배경이 여러 장면(약 4~6초씩)에 걸쳐 끊기지 않고 "
    "이어지는 구간을 받아, 그 구간을 '주제' 단위로 나누고 주제마다 구성 템플릿 하나와 채울 내용을 정한다. "
    "자리·크기·정렬은 템플릿이 정하므로 너는 정하지 않는다.\n\n"
    "[연출 원칙 — 레퍼런스 영상 분석 + 운영자 피드백]\n"
    "1. 주제 하나는 '한 가지 이야기'이고 4~9초다(장면 경계와 무관). 주제가 바뀔 때만 화면이 한꺼번에 바뀐다.\n"
    "2. 주제마다 시청자가 기억해야 할 핵심 하나만 보여 준다. 나열하지 않는다. 설명이 눈에 안 들어오는 요소 나열이 가장 나쁘다.\n"
    "3. 내레이션에 숫자(%·원·배·거래일 등)가 나오면 그 숫자를 큰 숫자로 보여 주는 것이 최우선이다.\n"
    "4. 같은 템플릿 안의 항목은 항상 같은 구조여야 한다(둘 다 아이콘+글, 또는 둘 다 숫자+글, 또는 둘 다 글).\n"
    "5. 아이콘은 그 순간 말하는 대상을 직접 그린 것만 쓴다(예: '반도체'→chip, '외국인'→foreigner). 뜻이 정확히 맞지 않으면 "
    "아이콘 없이 글만 쓴다. 같은 아이콘을 구간에서 두 번 쓰지 않는다. 장식용 아이콘은 쓰지 않는다.\n"
    "6. 글은 핵심어(2~3단어)만. 자막 문장을 그대로 되풀이하지 않는다.\n\n"
    "[템플릿] 주제 객체의 필드: template, scene(구간에 포함된 장면 번호), anchor(그 장면 내레이션에 글자 그대로 있는 구절 — "
    "이 말을 하는 순간에 주제가 시작된다) + 템플릿별 필드.\n"
    '- "title_number": 제목 + 큰 숫자. {"title": 선택, 4~16자 핵심어, "number": {"value": 내레이션에 글자 그대로 있는 숫자 구절 16자 이내, '
    '"label": 선택, 8자 이내}, "icon": 선택, "caption": 선택, 보조 한 줄 2~16자}\n'
    '- "compare_pair": 두 항목을 기호로 잇는 비교/결합(예: 외국인 이탈 + 자사주 매입 종료, 현재 vs 과거). '
    '{"op": "+" | "vs" | "→" | "=", "a": {"label": 2~14자, "icon": 선택, "value": 선택(숫자 구절)}, '
    '"b": {같은 구조, "anchor": 선택(이 항목을 말하는 구절)}, "title": 선택}. a와 b는 같은 구조로 쓴다.\n'
    '- "flow": 2~3단계 연결(원인→결과). {"nodes": [{"label": 2~8자, "icon": 선택}, …]} 아이콘은 모두 쓰거나 모두 쓰지 않는다.\n'
    '- "headline_sub": 굵은 빨간 한 줄(핵심 주장) + 작은 보조 문구. {"headline": 4~16자, "sub": 선택, 2~16자}\n'
    '- "bars": 막대 비교. {"title": 18자 이내(숫자 금지), "items": [{"label": 대상, "value_text": 숫자+단위 그대로}] 2~4개}\n'
    '- "list": 번호 목록. {"title": 18자 이내(숫자 금지), "items": [24자 이내 구절 2~4개]}\n'
    '- "rows": 숫자 행 비교. {"headline": 12자 이내 구절, "rows": [{"label": 6자 이내(숫자 금지), "value": 숫자 구절}] 2~3개}\n'
    "아이콘 이름: " + ", ".join(f"{name}({ICON_HINTS[name]})" for name in ICON_NAMES) + "\n\n"
    "규칙:\n"
    "- 모든 글자·숫자는 해당 장면 내레이션(또는 직전 두 장면)에 적힌 연속된 구절을 한 글자도 바꾸지 말고 복사한다"
    "(조사·어순·표기 그대로, 요약·바꿔쓰기·물음표 추가 금지). 숫자를 계산·반올림·추정·새로 만들지 않는다. 같은 문구를 두 번 쓰지 않는다.\n"
    "- 구간은 첫 장면 시작과 함께 첫 주제가 나와야 하고, 주제가 바뀔 때마다 다음 주제가 바로 이어져 빈 화면이 없어야 한다.\n"
    "- 주제 순서는 시간 순서이며 anchor가 앞 주제보다 뒤여야 한다.\n"
    '출력은 JSON만: {"topics":[{"template":"...","scene":번호,"anchor":"...", ...}]}'
)


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 5000)
    import os

    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6", max_tokens=5000, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def board_runs(plan: dict) -> list[list[dict]]:
    """연속된 회색 배경 장면 묶음(장면 번호 순). 1장면짜리도 시퀀스로 만든다."""
    runs: list[list[dict]] = []
    for scene in plan.get("scenes", []):
        if scene["background"] == "board":
            if runs and runs[-1][-1]["index"] == scene["index"] - 1:
                runs[-1].append(scene)
            else:
                runs.append([scene])
    return runs


def _scene_starts(plan: dict) -> dict[int, float]:
    starts, acc = {}, 0.0
    for scene in plan["scenes"]:
        starts[scene["index"]] = acc
        acc += scene["duration"]
    return starts


def _anchor_time(scene: dict, scene_start: float, anchor: str) -> float:
    """장면 내레이션에서 구절 위치 비율로 등장 시각을 정한다(문자 수 비례 근사, 장면 범위로 한정)."""
    narration = scene["narration"]
    position = narration.find(anchor) if anchor else -1
    if position < 0:
        compact = re.sub(r"\s+", "", narration)
        position = compact.find(re.sub(r"\s+", "", anchor or "")) if anchor else -1
        ratio = (position / max(len(compact), 1)) if position >= 0 else 0.05
    else:
        ratio = position / max(len(narration), 1)
    offset = min(max(ratio * scene["duration"], 0.15), max(scene["duration"] - 0.6, 0.15))
    return round(scene_start + offset, 2)


def _validate_content(kind: str, content: dict, scene_index: int, scenes: dict[int, dict]) -> dict | None:
    """요소 내용을 기존 오버레이 검증기로 확인한다(원문 일치·계산 검산). 통과한 내용만 돌려준다."""
    if kind in ("icon", "arrow"):
        if kind == "icon":
            return {"name": content.get("name")} if content.get("name") in ICON_NAMES else None
        direction = str(content.get("direction") or "").lower()
        return {"direction": direction} if direction in {"up", "down", "right"} else None
    overlay_type = _KIND_TO_OVERLAY.get(kind)
    scene = scenes.get(scene_index)
    if not overlay_type or not scene:
        return None
    ordered = sorted(scenes)
    position = ordered.index(scene_index)
    context = " ".join(scenes[i]["narration"] for i in ordered[max(0, position - 2):position])
    if kind == "number" and content.get("label"):
        # 소제목은 8자 이내: 단어 중간에서 잘리지 않게 단어 경계에서 줄이고, 못 줄이면 뺀다.
        label = str(content["label"]).strip()
        if len(label) > 8:
            label = label[:9].rsplit(" ", 1)[0] if " " in label[:9] else ""
        content = {**content, "label": label}
    content = _spell_like_narration(content, scene["narration"] + " " + context)
    raw = {**content, "type": overlay_type, "index": scene_index}
    if kind == "text":
        raw = {"type": "key_text", "index": scene_index, "text": content.get("text")}
    validated = validate_overlay_plan(
        [raw], {scene_index: {"narration": scene["narration"], "context": context}},
    ).get(scene_index)
    if not validated:
        return None
    if kind == "text":
        if len(validated["text"]) > MAX_TEXT_CHARS:
            return None  # 자막 문장을 그대로 되풀이하는 긴 문구는 핵심어로 줄여 다시 설계해야 한다
        return {"text": validated["text"], "color": "white" if str(content.get("color")) == "white" else "red"}
    if kind == "number":
        return {"value": validated["value"], "label": validated.get("label", "")}
    if kind == "bars":
        return {"title": validated.get("title", ""), "items": validated.get("items", [])}
    if kind == "list":
        return {"title": validated["title"], "items": validated["items"]}
    if kind == "rows":
        return {"headline": validated["headline"], "rows": validated["rows"]}
    if kind == "flow":
        return {"nodes": validated["nodes"]}
    return None


def _normalize_elements(raw_elements: list, run: list[dict], plan: dict) -> list[dict]:
    scenes = {s["index"]: s for s in plan["scenes"]}
    starts = _scene_starts(plan)
    run_indexes = {s["index"] for s in run}
    seq_start = starts[run[0]["index"]]
    elements: list[dict] = []
    used_text: set[str] = set()
    topics_possible = max(1, int(sum(s["duration"] for s in run) // MIN_TOPIC_SECONDS))
    limit = MAX_ELEMENTS_PER_TOPIC * topics_possible
    for position, raw in enumerate(raw_elements or []):
        if not isinstance(raw, dict) or len(elements) >= limit:
            continue
        kind = str(raw.get("kind") or "").strip()
        try:
            scene_index = int(raw.get("scene"))
        except (TypeError, ValueError):
            continue
        content = raw.get("content")
        if kind not in KINDS or scene_index not in run_indexes or not isinstance(content, dict):
            continue
        validated = _validate_content(kind, content, scene_index, scenes)
        if not validated:
            continue
        key = json.dumps(validated, ensure_ascii=False, sort_keys=True)
        if key in used_text:
            continue
        used_text.add(key)
        slot = str(raw.get("slot") or "center")
        anim = str(raw.get("anim") or "rise")
        try:
            topic = max(1, int(raw.get("topic") or 1))
        except (TypeError, ValueError):
            topic = 1
        elements.append({
            "id": str(raw.get("id") or f"e{position + 1}"), "kind": kind, "scene": scene_index, "topic": topic,
            "replaces": str(raw.get("replaces") or "") or None,
            "anchor": str(raw.get("anchor") or "")[:60],
            "slot": slot if slot in SLOTS else "center",
            "anim": anim if anim in ANIMS else {"number": "count", "arrow": "draw", "bars": "grow"}.get(kind, "rise"),
            "at": round(_anchor_time(scenes[scene_index], starts[scene_index], str(raw.get("anchor") or "")) - seq_start, 2),
            "content": validated,
        })
    elements.sort(key=lambda e: e["at"])
    if elements and elements[0]["at"] > OPENING_LATEST_SECONDS:
        # 구간이 시작하자마자 첫 요소가 나와야 한다(레퍼런스: 장면 전환 직후 바로 등장).
        elements[0]["at"] = OPENING_LATEST_SECONDS
    return elements


def _fallback_elements(run: list[dict], plan: dict) -> list[dict]:
    """Claude 설계가 실패하면 장면별 후편집(이미 검증됨)을 장면 시작 시점의 요소로 이어 붙인다."""
    starts = _scene_starts(plan)
    seq_start = starts[run[0]["index"]]
    elements: list[dict] = []
    for scene in run:
        element = _fallback_scene_element(scene, starts, seq_start, f"e{len(elements) + 1}")
        if element:
            element["topic"] = len(elements) + 1
            elements.append(element)
    return elements


def _fallback_scene_element(scene: dict, starts: dict, seq_start: float, element_id: str) -> dict | None:
    """장면 하나의 (이미 검증된) 후편집을 시퀀스 요소로 바꾼다."""
    overlay = scene.get("overlay") or {}
    kind = _OVERLAY_TO_KIND.get(overlay.get("type"))
    if not kind:
        return None
    if kind == "text":
        content = {"text": overlay["text"], "color": "red"}
    elif kind == "number":
        content = {"value": overlay["value"], "label": overlay.get("label", "")}
    elif kind == "bars":
        content = {"title": overlay.get("title", ""), "items": overlay.get("items", [])}
    elif kind == "list":
        content = {"title": overlay["title"], "items": overlay["items"]}
    elif kind == "rows":
        content = {"headline": overlay["headline"], "rows": overlay["rows"]}
    else:
        content = {"nodes": overlay["nodes"]}
    return {
        "id": element_id, "kind": kind, "scene": scene["index"], "anchor": "", "slot": "center", "replaces": None,
        "anim": {"number": "count", "bars": "grow"}.get(kind, "rise"),
        "at": round(starts[scene["index"]] - seq_start + 0.3, 2), "content": content,
    }


def _tight_rect(prim: dict) -> tuple[float, float, float, float]:
    """기본 요소가 실제로 차지하는 화면 사각형(투명 여백 제외)."""
    sprite = _sprite_for(prim, 1.0)
    box = sprite.getchannel("A").getbbox() or (0, 0, sprite.width, sprite.height)
    cx, cy = prim["pos"]
    left, top = cx - sprite.width / 2, cy - sprite.height / 2
    top = max(min(top, SUBTITLE_SAFE_BOTTOM - sprite.height), 20)  # render_frame과 같은 보정
    return (left + box[0], top + box[1], left + box[2], top + box[3])


def element_rect(element: dict) -> tuple[float, float, float, float]:
    """요소(합성 요소 포함)가 차지하는 화면 사각형. 자리·종류·내용으로 결정론적으로 계산한다."""
    probe = {**element, "until": element.get("until", 1e6)}
    rects = [_tight_rect(prim) for prim in expand_primitives({"elements": [probe]})]
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def _intersects(a: tuple, b: tuple) -> bool:
    return not (a[2] + RECT_MARGIN <= b[0] or b[2] + RECT_MARGIN <= a[0]
                or a[3] + RECT_MARGIN <= b[1] or b[3] + RECT_MARGIN <= a[1])


def _is_fixed(group: list[dict]) -> bool:
    """템플릿이 자리를 정한 주제인지."""
    return bool(group) and all(e.get("fixed") for e in group)


def _place_fixed(head: dict, rest: list[dict], end: float) -> list[dict]:
    """템플릿 주제: 자리는 그대로 두고, 늦게 나올 항목은 버리지 않고 주제 끝 전에 나오도록 당긴다(구조가 깨지지 않게).
    서로 겹치는 항목은 뒤에 나온 쪽을 버린다."""
    kept = [head]
    rects = [element_rect(head)]
    latest = max(end - MIN_ELEMENT_SECONDS, head["at"] + 0.15)
    for element in rest[:MAX_ELEMENTS_PER_TEMPLATE - 1]:
        element["at"] = round(min(element["at"], latest), 2)
        rect = element_rect(element)
        if any(_intersects(rect, other) for other in rects):
            continue
        kept.append(element)
        rects.append(rect)
    return kept


def _group_topics(elements: list[dict]) -> list[list[dict]]:
    """주제 번호로 묶어 시작 시각 순서로 돌려준다(시작 시각이 겹치는 주제는 같은 주제로 본다)."""
    groups: dict[int, list[dict]] = {}
    for element in sorted(elements, key=lambda e: e["at"]):
        groups.setdefault(int(element.get("topic") or 1), []).append(element)
    return sorted(groups.values(), key=lambda group: group[0]["at"])


def _place_topic(group: list[dict]) -> list[dict]:
    """한 주제의 요소를 서로 겹치지 않게 놓는다. 같은 종류 숫자·문구만 제자리에서 값이 바뀌고, 나머지는 쌓인다.
    자리가 없으면 요소를 버린다(겹쳐 그리지 않는다)."""
    placed: list[tuple[dict, tuple]] = []  # 지금 화면에 살아 있는(겹침 검사 대상) 요소
    kept: list[dict] = []  # 값이 바뀌어 퇴장한 요소까지 포함한 결과
    for element in sorted(group, key=lambda e: e["at"]):
        target = next((p for p, _ in placed if p["id"] == element.get("replaces")), None)
        if target is not None and target["kind"] == element["kind"] and target["kind"] in _UPDATABLE_KINDS \
                and element["at"] - target["at"] >= MIN_UPDATE_SECONDS:
            element["slot"] = target["slot"]
            target["until"] = element["at"]
            placed = [(p, r) for p, r in placed if p is not target]
            others = [r for _, r in placed]
            rect = element_rect(element)
            if not any(_intersects(rect, other) for other in others):
                placed.append((element, rect))
                kept.append(element)
            continue
        element["replaces"] = None
        candidates = ["center"] if element["kind"] in _WIDE_KINDS else [
            element["slot"], *[s for s in ("center", *_SLOT_FALLBACKS) if s != element["slot"]],
        ]
        for slot in candidates:
            element["slot"] = slot
            rect = element_rect(element)
            if rect[0] < 20 or rect[2] > CANVAS[0] - 20:
                continue
            if all(not _intersects(rect, other) for _, other in placed):
                placed.append((element, rect))
                kept.append(element)
                break
    return kept


def resolve_lifetimes(elements: list[dict], duration: float, *, merge_short: bool = True) -> list[dict]:
    """주제 단위로 요소의 등장·유지·퇴장을 정하고, 살아남은 요소를 돌려준다.

    - 주제(topic)가 바뀔 때만 앞 주제의 요소가 한꺼번에 사라지고, 그 순간 새 주제의 첫 요소가 나온다(빈 화면 없음).
    - 같은 주제 안의 요소는 쌓이며 주제가 끝날 때까지 남는다. 같은 종류 숫자·문구만 제자리에서 값이 바뀐다(replaces).
    - 5초보다 짧은 주제는 앞 주제에 합치고, 글자·숫자가 없는 주제(아이콘·화살표만)는 버린다.
    - 주제가 끝나기 직전(MIN_ELEMENT_SECONDS 이내)에 나올 요소와, 다른 요소와 겹치는 요소는 만들지 않는다."""
    topics = _group_topics(elements)
    if merge_short:
        merged: list[list[dict]] = []
        for k, group in enumerate(topics):
            end = topics[k + 1][0]["at"] if k + 1 < len(topics) else duration
            short = end - group[0]["at"] < MIN_TOPIC_SECONDS
            # 템플릿 주제는 자리가 정해져 있어 다른 주제와 합치면 겹친다 — 합치지 않고, 너무 짧으면 버린다.
            if merged and short and not _is_fixed(group) and not _is_fixed(merged[-1]):
                merged[-1].extend(group)
            elif _is_fixed(group) and end - group[0]["at"] < FIXED_TOPIC_MIN_SECONDS and merged:
                continue
            else:
                merged.append(group)
        topics = merged
    topics = [g for g in topics if any(e["kind"] in _ANCHOR_KINDS for e in g)]
    survivors: list[list[dict]] = []
    for k, group in enumerate(topics):
        end = topics[k + 1][0]["at"] if k + 1 < len(topics) else duration
        group.sort(key=lambda e: e["at"])
        head, rest = group[0], group[1:]
        if _is_fixed(group):
            placed = _place_fixed(head, rest, end)
        else:
            rest = [e for e in rest if end - e["at"] >= MIN_ELEMENT_SECONDS]
            placed = _place_topic([head, *rest][:MAX_ELEMENTS_PER_TOPIC * 2])[:MAX_ELEMENTS_PER_TOPIC + 2]
        if placed:
            survivors.append(placed)
    result: list[dict] = []
    for k, group in enumerate(survivors):
        end = survivors[k + 1][0]["at"] if k + 1 < len(survivors) else duration
        for element in group:
            element["topic"] = k + 1
            element["until"] = round(min(element["until"], end), 2) if "until" in element else round(end, 2)
            element["until"] = round(max(element["until"], element["at"] + 0.6), 2)
            result.append(element)
    result.sort(key=lambda e: e["at"])
    return result


class _TemplateContext:
    """slide_templates가 쓰는 검증·시각 도구. 글자·숫자는 기존 검증기(원문 일치·계산 검산)를 그대로 통과해야 한다."""

    def __init__(self, run: list[dict], plan: dict, approved: set[tuple[int, str]] | None = None):
        self.scenes = {s["index"]: s for s in plan["scenes"]}
        self.starts = _scene_starts(plan)
        self.seq_start = self.starts[run[0]["index"]]
        self.run_indexes = {s["index"] for s in run}
        self.approved = approved or set()  # 내레이션을 그대로 옮기지 않았지만 검수를 통과한 요약 문구(숫자 없음)
        self.failures: list[str] = []  # 거절된 문구와 이유(재설계 요청에 쓴다)

    def _scene(self, scene: int) -> dict | None:
        return self.scenes.get(scene) if scene in self.run_indexes else None

    def label(self, scene: int, text: object) -> str | None:
        target = self._scene(scene)
        value = str(text or "").strip()
        if not target or not (2 <= len(value) <= MAX_TEXT_CHARS):
            return None
        value = _spell_like_narration(value, target["narration"])
        if _in_narration(value, target["narration"]) or (scene, value) in self.approved:
            return to_percent_symbol(value)
        self.failures.append(f'"{value}": 내레이션에 없고 요약 검수도 통과하지 못함(숫자가 든 요약은 불가)')
        return None

    def text(self, scene: int, text: object, color: str) -> dict | None:
        if not self._scene(scene):
            return None
        value = str(text or "").strip()
        if (scene, value) in self.approved and 2 <= len(value) <= MAX_TEXT_CHARS:
            return {"text": value, "color": "white" if color in ("white", "gray") else "red"}
        validated = _validate_content("text", {"text": value, "color": color}, scene, self.scenes)
        if not validated:
            self.failures.append(f'"{value}": 내레이션 구절이 아니거나 {MAX_TEXT_CHARS}자 초과')
        return validated

    def content(self, kind: str, content: dict, scene: int) -> dict | None:
        if not self._scene(scene) or not isinstance(content, dict):
            return None
        return _validate_content(kind, content, scene, self.scenes)

    def icon(self, name: object) -> str | None:
        return name if isinstance(name, str) and name in ICON_NAMES else None

    def time(self, scene: int, anchor: object) -> float:
        target = self._scene(scene)
        if not target:
            return 0.0
        return max(_anchor_time(target, self.starts[scene], str(anchor or "")) - self.seq_start, 0.0)


_JUDGE_SYSTEM = (
    "너는 영상 자막·후편집의 사실 확인 검수자다. 각 항목은 영상 화면에 뜰 짧은 문구이고, 그 장면의 내레이션(과 직전 두 장면)과 함께 "
    "주어진다. 문구가 내레이션이 말한 내용을 왜곡·과장 없이 줄여 쓴 핵심어/요약이면 ok다. 내레이션에 없는 새 사실·원인·주장, "
    "방향(오른다/내린다)이나 긍정/부정의 반전, 새 대상(회사·인물)이 들어 있으면 거절한다. 확신이 없으면 거절한다.\n"
    '출력은 JSON만: {"ok": [통과한 항목 번호, ...]}'
)


def _label_candidates(topic: dict) -> list[str]:
    values: list[object] = [topic.get(key) for key in ("title", "caption", "headline", "sub")]
    for side in ("a", "b"):
        if isinstance(topic.get(side), dict):
            values.append(topic[side].get("label"))
    for node in topic.get("nodes") or []:
        if isinstance(node, dict):
            values.append(node.get("label"))
    return [str(v).strip() for v in values if isinstance(v, str) and v.strip()]


def _approve_paraphrases(raw_topics: list, run: list[dict], plan: dict, llm_call=None) -> set[tuple[int, str]]:
    """내레이션을 그대로 옮기지 않은 요약 문구를 Claude가 한 번에 검수한다(숫자가 든 문구는 검수 대상이 아니라 거절).

    2026-10-16 운영자 피드백: 화면 문구는 자막을 되풀이하지 않는 핵심어여야 한다. 원문 그대로만 허용하면 핵심어를 못 쓰고 자막 반복이
    남는다. 숫자·단위는 계속 원문 일치 검증을 거치고, 요약 문구만 의미 검수(왜곡·새 사실 금지)를 거친다."""
    scenes = {s["index"]: s for s in plan["scenes"]}
    ordered = sorted(scenes)
    run_indexes = {s["index"] for s in run}
    items: list[tuple[int, str]] = []
    for raw in (raw_topics or [])[:12]:
        try:
            scene = int(raw.get("scene")) if isinstance(raw, dict) else -1
        except (TypeError, ValueError):
            continue
        if scene not in run_indexes:
            continue
        narration = scenes[scene]["narration"]
        for text in _label_candidates(raw):
            spelled = _spell_like_narration(text, narration)
            if not 2 <= len(spelled) <= MAX_TEXT_CHARS or re.search(r"\d", spelled) or _in_narration(spelled, narration):
                continue
            if (scene, spelled) not in items:
                items.append((scene, spelled))
    if not items:
        return set()
    lines = []
    for number, (scene, text) in enumerate(items, start=1):
        position = ordered.index(scene)
        context = " ".join(scenes[i]["narration"] for i in ordered[max(0, position - 2):position + 1])
        lines.append(f"{number}. 문구: \"{text}\" / 내레이션: {context}")
    try:
        raw = _call_llm(_JUDGE_SYSTEM, "\n".join(lines) + "\n\nJSON만 반환:", llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        ok = {int(n) for n in (json.loads(match.group(0) if match else cleaned).get("ok") or [])}
    except Exception as exc:
        logger.warning("요약 문구 검수 실패(원문 그대로인 문구만 쓴다): %s", exc)
        return set()
    return {items[n - 1] for n in ok if 1 <= n <= len(items)}


def _topics_to_elements(
    raw_topics: list, run: list[dict], plan: dict, llm_call=None, failures: list[str] | None = None,
) -> list[dict]:
    """Claude가 정한 주제(템플릿+내용)를 위치가 정해진 요소로 풀어 시간순으로 돌려준다."""
    ctx = _TemplateContext(run, plan, _approve_paraphrases(raw_topics, run, plan, llm_call))
    if failures is not None:
        failures.extend(ctx.failures)  # 같은 리스트를 계속 갱신하도록 아래에서 다시 합친다
    elements: list[dict] = []
    seen: set[str] = set()
    number = 0
    for raw in (raw_topics or [])[:12]:
        if not isinstance(raw, dict):
            continue
        expanded = expand_topic(raw, ctx)
        if not expanded:
            continue
        core = json.dumps(
            [(e["kind"], e["content"]) for e in expanded if e["kind"] in _ANCHOR_KINDS], ensure_ascii=False, sort_keys=True,
        )
        if core in seen:
            continue
        seen.add(core)
        number += 1
        for element in expanded:
            element["topic"] = number
            element["id"] = f"e{len(elements) + 1}"
            element["replaces"] = None
            elements.append(element)
    if failures is not None:
        failures.extend(f for f in ctx.failures if f not in failures)
    elements.sort(key=lambda e: (e["topic"], e["at"]))
    # 첫 주제는 구간이 시작하자마자 나온다(주제 안의 상대 시각은 그대로 둔다).
    first = [e for e in elements if e["topic"] == 1]
    if first:
        shift = min(e["at"] for e in first) - OPENING_LATEST_SECONDS
        if shift > 0:
            for element in first:
                element["at"] = round(max(element["at"] - shift, 0.0), 2)
    return elements


def _longest_gap(elements: list[dict], duration: float) -> float:
    """주제 시작 시각 사이(마지막은 구간 끝까지)의 가장 긴 간격 — 화면이 한 주제로 오래 멈춰 있는 정도."""
    starts = sorted({e["topic"]: e["at"] for e in elements}.values()) if elements else []
    if not starts:
        return duration
    points = [0.0, *starts, duration] if starts[0] > MAX_TOPIC_SECONDS else [*starts, duration]
    return max(b - a for a, b in zip(points, points[1:]))


def _fill_gaps(elements: list[dict], run: list[dict], plan: dict) -> list[dict]:
    """템플릿 주제가 거절돼 한 화면이 MAX_TOPIC_SECONDS보다 오래 멈추면, 그 사이 장면의 후편집(이미 검증됨)을 새 주제로 보충한다."""
    if not elements:
        return elements
    starts = _scene_starts(plan)
    seq_start = starts[run[0]["index"]]
    topic_starts = sorted({e["topic"]: e["at"] for e in elements}.values())
    last = topic_starts[0]
    next_topic = max(e["topic"] for e in elements) + 1
    added: list[dict] = []
    for scene in run:
        offset = starts[scene["index"]] - seq_start
        covering = max([t for t in topic_starts if t <= offset + 1e-6] + [last])
        if offset - covering <= MAX_TOPIC_SECONDS - 3 or not scene.get("overlay"):
            continue
        fallback = _fallback_scene_element(scene, starts, seq_start, f"g{len(added) + 1}")
        if not fallback:
            continue
        if fallback["kind"] == "text" and len(fallback["content"]["text"]) > MAX_TEXT_CHARS:
            continue  # 자막과 같은 절을 되풀이하는 긴 문구는 보충하지 않는다
        key = str(fallback["content"].get("value") or fallback["content"].get("text") or "")
        shown = " ".join(json.dumps(e["content"], ensure_ascii=False) for e in [*elements, *added])
        if (key and key in shown) or any(
            e["kind"] == fallback["kind"] and e["content"] == fallback["content"] for e in [*elements, *added]
        ):
            continue  # 이미 화면에 나온 내용을 다시 띄우지 않는다
        fallback["topic"] = next_topic
        next_topic += 1
        added.append(fallback)
        topic_starts.append(fallback["at"])
        topic_starts.sort()
    for index, element in enumerate(added, start=1):
        element["id"] = f"e{len(elements) + index}"
    return elements + added


def sequence_signature(run: list[dict]) -> str:
    payload = json.dumps(
        [SEQUENCE_VERSION, [[s["index"], s["narration"], round(s["duration"], 2),
                             (s.get("overlay") or {}).get("type"), (s.get("overlay") or {}).get("text")] for s in run]],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def design_sequence(run: list[dict], plan: dict, *, llm_call=None) -> dict | None:
    """한 회색 배경 구간의 시퀀스(요소 타임라인)를 만든다. Claude 설계가 비면 장면별 후편집으로 대체한다."""
    starts = _scene_starts(plan)
    seq_start = starts[run[0]["index"]]
    duration = round(sum(s["duration"] for s in run), 2)
    lines = [
        f"{s['index']} (시작 +{starts[s['index']] - seq_start:.1f}초, {s['duration']:.1f}초): {s['narration']}" for s in run
    ]
    prompt = (
        f"회색 배경이 이어지는 구간(총 {duration}초, 장면 {len(run)}개):" + chr(10) + chr(10).join(lines)
        + chr(10) + chr(10) + "이 구간의 요소 타임라인을 JSON으로만 반환:"
    )
    designed: list[dict] = []
    source = "claude"
    failures: list[str] = []

    def attempt(extra: str = "") -> list[dict]:
        raw = _call_llm(_SEQUENCE_SYSTEM, prompt + extra, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        if data.get("topics"):
            return _topics_to_elements(data.get("topics") or [], run, plan, llm_call=llm_call, failures=failures)
        return _normalize_elements(data.get("elements") or [], run, plan)  # 이전 형식(자유 배치 요소)도 받는다

    try:
        designed = attempt()
        gap = _longest_gap(designed, duration)
        if designed and gap > MAX_TOPIC_SECONDS:
            # 한 화면이 오래 멈춰 있으면(주제가 너무 길거나 일부 주제가 거절됨) 이유를 알려 주고 한 번 더 설계한다.
            retry = (
                chr(10) * 2 + "[재설계 요청] 앞 설계에서 한 화면이 " + f"{gap:.0f}초 동안 멈춰 있다. 주제를 4~9초로 나눠 다시 설계해라."
                + (" 아래 문구는 거절됐으니 내레이션 구절을 그대로 복사하거나 숫자 없는 짧은 핵심어를 써라:" + chr(10)
                   + chr(10).join(f"- {item}" for item in failures[:8]) if failures else "")
            )
            failures_before = list(failures)
            second = attempt(retry)
            if second and _longest_gap(second, duration) < gap:
                designed = second
            failures[:] = failures_before
        designed = _fill_gaps(designed, run, plan)
    except Exception as exc:
        logger.warning("시퀀스 설계 실패(장면별 후편집으로 대체): %s", exc)
    # 구간의 모든 장면에 움직임이 있어야 한다(요소가 한 장면도 못 채우면 장면별 후편집을 보충한다).
    if not designed:
        designed = _fallback_elements(run, plan)
        source = "fallback_overlays"
    if not designed:
        return None
    designed = resolve_lifetimes(designed, duration, merge_short=source == "claude")
    if not designed and source == "claude":
        # 설계가 규칙(주제 길이·겹침)을 통과하지 못해 비면 장면별 후편집으로 대체한다.
        source = "fallback_overlays"
        designed = resolve_lifetimes(_fallback_elements(run, plan), duration, merge_short=False)
    if not designed:
        return None
    return {
        "id": 0, "version": SEQUENCE_VERSION, "scenes": [s["index"] for s in run], "start": round(seq_start, 2),
        # 장면별 시퀀스 시간축 시작점: 장면 클립은 이 구간만 잘라 낸다
        "offsets": {str(s["index"]): round(starts[s["index"]] - seq_start, 3) for s in run},
        "duration": duration, "end": round(seq_start + duration, 2), "source": source,
        "signature": sequence_signature(run), "elements": designed,
    }


def attach_sequences(plan: dict, *, llm_call=None, workers: int = 4) -> dict:
    """계획서의 모든 회색 배경 구간에 시퀀스를 붙인다. 구간이 그대로면(서명 동일) 이전 시퀀스를 재사용한다."""
    previous = {seq.get("signature"): seq for seq in plan.get("sequences", [])}
    runs = board_runs(plan)

    def build(run: list[dict]) -> dict | None:
        signature = sequence_signature(run)
        reused = previous.get(signature)
        if reused:
            return json.loads(json.dumps(reused))
        return design_sequence(run, plan, llm_call=llm_call)

    if len(runs) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            built = list(pool.map(build, runs))
    else:
        built = [build(run) for run in runs]
    sequences = [seq for seq in built if seq]
    for number, seq in enumerate(sequences, start=1):
        seq["id"] = number
    by_scene = {index: seq["id"] for seq in sequences for index in seq["scenes"]}
    for scene in plan["scenes"]:
        if scene["index"] in by_scene:
            scene["sequence_id"] = by_scene[scene["index"]]
        else:
            scene.pop("sequence_id", None)
    plan["sequences"] = sequences
    return plan


# ---------------------------------------------------------------- 렌더(프레임 단위 애니메이션) ----

def _ease_out(p: float) -> float:
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _text_sprite(
    text: str, size: int, color: tuple, *, stroke: int = 0, max_width: int = 1500, font_path: str = _FONT_BOLD,
) -> Image.Image:
    font = _font(font_path, size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    lines = []
    # 원문에 줄바꿈이 있으면 그 줄을 먼저 나누고, 각 줄 안에서만 폭에 맞춰 단어 단위로 감는다.
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for word in paragraph.split(" "):
            trial = f"{current} {word}".strip()
            if current and probe.textlength(trial, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        lines.append(current)
    line_h = int(size * 1.25)
    width = int(max(probe.textlength(line, font=font) for line in lines)) + 40 + stroke * 2
    sprite = Image.new("RGBA", (width, line_h * len(lines) + 30), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    for k, line in enumerate(lines):
        w = probe.textlength(line, font=font)
        draw.text(((width - w) / 2, 10 + k * line_h), line, font=font, fill=color + (255,),
                  stroke_width=stroke, stroke_fill=color + (255,))
    return sprite


def _number_text(value: str, p: float) -> str:
    """숫자 카운트업: 값 속 첫 숫자를 0에서 목표까지 올린다(자릿수·쉼표 형식 유지)."""
    match = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if not match or p >= 1:
        return value
    token = match.group(0)
    target = float(token.replace(",", ""))
    decimals = len(token.split(".")[1]) if "." in token else 0
    shown = target * _ease_out(p)
    text = f"{shown:,.{decimals}f}" if "," in token else f"{shown:.{decimals}f}"
    return value[:match.start()] + text + value[match.end():]


def _fit_size(text: str, size: int, max_width: int, floor: int = 64) -> int:
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    while size > floor and probe.textlength(text, font=_font(_FONT_BOLD, size)) > max_width:
        size -= 6
    return size


def _number_sprite(
    value: str, label: str, p: float, max_width: int = WIDE_SLOT_MAX_WIDTH, size_cap: int | None = None,
) -> Image.Image:
    # 카운트업 도중 폭이 변해 크기가 흔들리지 않게 최종 값 기준으로 글자 크기를 정한다(좌우 자리에서 잘리지 않게).
    size = _fit_size(value, min(190, size_cap or 190), max_width)
    main = _text_sprite(_number_text(value, p), size, WHITE, max_width=10_000)
    if not label:
        return main
    tag = _text_sprite(label, 56, ACCENT[:3], max_width=900)
    sprite = Image.new("RGBA", (max(main.width, tag.width), main.height + tag.height - 10), (0, 0, 0, 0))
    sprite.alpha_composite(tag, ((sprite.width - tag.width) // 2, 0))
    sprite.alpha_composite(main, ((sprite.width - main.width) // 2, tag.height - 10))
    return sprite


def _icon_sprite(name: str, size: int = 400) -> Image.Image:
    sprite = Image.new("RGBA", (size + 40, size + 40), (0, 0, 0, 0))
    draw_icon(ImageDraw.Draw(sprite), name, (size + 40) // 2, (size + 40) // 2, size)
    return sprite


def _arrow_sprite(direction: str, p: float, size: int = 300) -> Image.Image:
    """화살표가 그려지는 모습: 몸통이 길어진 뒤 머리가 붙는다."""
    pad = 40
    sprite = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    color = {"up": (46, 204, 113), "down": (229, 57, 53), "right": ACCENT[:3]}.get(direction, ACCENT[:3]) + (255,)
    shaft = int(size * 0.5 * min(p / 0.7, 1.0))
    head = max(0.0, (p - 0.7) / 0.3)
    cx, cy = (size + pad * 2) // 2, (size + pad * 2) // 2
    width = max(14, size // 14)
    vx, vy = {"up": (0, -1), "down": (0, 1), "right": (1, 0)}.get(direction, (1, 0))
    sx, sy = cx - vx * size * 0.3, cy - vy * size * 0.3
    ex, ey = sx + vx * shaft * 1.2, sy + vy * shaft * 1.2
    draw.line((sx, sy, ex, ey), fill=color, width=width)
    if head > 0:
        hs = size * 0.22 * head
        px, py = -vy, vx
        tip = (ex + vx * hs * 1.4, ey + vy * hs * 1.4)
        draw.polygon([(ex + px * hs, ey + py * hs), (ex - px * hs, ey - py * hs), tip], fill=color)
    return sprite


def _bars_sprite(title: str, items: list[dict], p: float) -> Image.Image:
    count = max(len(items), 1)
    slot, base_h = 260, 380
    width = slot * count + 120
    sprite = Image.new("RGBA", (width, base_h + 260), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    if title:
        tw = draw.textlength(title, font=_font(_FONT_BOLD, 58))
        draw.text(((width - tw) / 2, 0), title, font=_font(_FONT_BOLD, 58), fill=WHITE + (255,))
    top = max(float(item.get("value") or 0) for item in items) or 1.0
    for k, item in enumerate(items):
        h = max(int(base_h * float(item.get("value") or 0) / top * _ease_out(p)), 6)
        x = 60 + k * slot + 40
        draw.rectangle((x, 130 + base_h - h, x + slot - 80, 130 + base_h), fill=ACCENT[:3] + (255,))
        if p > 0.65:
            label = str(item.get("value_text") or "")
            lw = draw.textlength(label, font=_font(_FONT_BOLD, 46))
            draw.text((x + (slot - 80 - lw) / 2, 130 + base_h - h - 60), label, font=_font(_FONT_BOLD, 46),
                      fill=WHITE + (255,))
        name = str(item.get("label") or "")
        nw = draw.textlength(name, font=_font(_FONT_BOLD, 40))
        draw.text((x + (slot - 80 - nw) / 2, 130 + base_h + 20), name, font=_font(_FONT_BOLD, 40), fill=(200, 200, 205, 255))
    return sprite


def _row_sprite(label: str, value: str, color: tuple) -> Image.Image:
    tag = _text_sprite(label, 54, color, max_width=700)
    num = _text_sprite(value, _fit_size(value, 120, 520), color, max_width=10_000)
    sprite = Image.new("RGBA", (max(tag.width, num.width), tag.height + num.height - 20), (0, 0, 0, 0))
    sprite.alpha_composite(tag, ((sprite.width - tag.width) // 2, 0))
    sprite.alpha_composite(num, ((sprite.width - num.width) // 2, tag.height - 20))
    return sprite


def _node_sprite(label: str, icon: str | None) -> Image.Image:
    """흐름도의 한 단계. 아이콘이 있으면 아이콘+글, 없으면 윤곽선 카드 안의 글(같은 흐름에서는 구조가 같다)."""
    if icon:
        text = _text_sprite(label, 72, WHITE, max_width=520)
        size = 280
        sprite = Image.new("RGBA", (max(size + 40, text.width), size + 40 + text.height), (0, 0, 0, 0))
        sprite.alpha_composite(_icon_sprite(icon, size), ((sprite.width - size - 40) // 2, 0))
        sprite.alpha_composite(text, ((sprite.width - text.width) // 2, size + 30))
        return sprite
    text = _text_sprite(label, 80, WHITE, max_width=520)
    pad_x, pad_y = 36, 24
    sprite = Image.new("RGBA", (text.width + pad_x * 2, text.height + pad_y * 2), (0, 0, 0, 0))
    ImageDraw.Draw(sprite).rounded_rectangle(
        (2, 2, sprite.width - 3, sprite.height - 3), radius=26, fill=(255, 255, 255, 22), outline=(255, 255, 255, 210), width=4,
    )
    sprite.alpha_composite(text, (pad_x, pad_y))
    return sprite


def expand_primitives(sequence: dict) -> list[dict]:
    """합성 요소(list·rows·flow)를 화면에 그릴 수 있는 기본 요소로 풀어 위치·등장 시각을 정한다(결정론적)."""
    prims: list[dict] = []
    for element in sequence["elements"]:
        kind, content, at, until = element["kind"], element["content"], element["at"], element["until"]
        # 템플릿 요소는 위치(pos)·글자 크기(size)·최대 폭을 직접 정한다. 자유 배치 요소만 슬롯을 쓴다.
        cx, cy = element.get("pos") or SLOTS.get(element["slot"], SLOTS["center"])
        base = {"element": element["id"], "until": until, "anim": element["anim"],
                "max_width": element.get("max_width")
                or (SIDE_SLOT_MAX_WIDTH if element["slot"] in _SIDE_SLOTS else WIDE_SLOT_MAX_WIDTH)}
        if kind in ("text", "symbol"):
            size = element.get("size")
            if not size:
                size = 112 if element["slot"] in ("center", "top", "bottom") else 92
                if content.get("color") not in ("white", "gray"):
                    size = int(size * 1.2)  # 빨간 강조 글자는 디스플레이 서체라 같은 크기에서 더 작아 보인다
            color = {"white": WHITE, "gray": GRAY_TEXT}.get(content.get("color"), RED)
            if kind == "symbol":
                color, size = ACCENT[:3], element.get("size") or 150
            prims.append({**base, "kind": "text", "at": at, "pos": (cx, cy), "text": content["text"], "size": size,
                          "color": color})
        elif kind == "number":
            prims.append({**base, "kind": "number", "at": at, "pos": (cx, cy), "value": content["value"],
                          "label": content.get("label", ""), "size_cap": element.get("size_cap")})
        elif kind == "icon":
            prims.append({**base, "kind": "icon", "at": at, "pos": (cx, cy), "name": content["name"],
                          "icon_size": element.get("icon_size", 400)})
        elif kind == "arrow":
            prims.append({**base, "kind": "arrow", "at": at, "pos": (cx, cy), "direction": content["direction"]})
        elif kind == "bars":
            prims.append({**base, "kind": "bars", "at": at, "pos": (cx, cy), "title": content.get("title", ""),
                          "items": content["items"]})
        elif kind == "list":
            prims.append({**base, "kind": "text", "at": at, "pos": (cx, cy - 250), "text": content["title"],
                          "size": 72, "color": ACCENT[:3]})
            for k, item in enumerate(content["items"]):
                prims.append({**base, "kind": "text", "at": round(at + 0.55 + k * 0.75, 2),
                              "pos": (cx, cy - 90 + k * 130), "text": f"{k + 1}  {item}", "size": 84, "color": WHITE})
        elif kind == "rows":
            prims.append({**base, "kind": "text", "at": at, "pos": (cx, cy - 260), "text": content["headline"],
                          "size": 88, "color": WHITE})
            rows = content["rows"]
            palette = [(110, 214, 255), ACCENT[:3], WHITE]
            for k, row in enumerate(rows):
                x = cx + int((k - (len(rows) - 1) / 2) * 560)
                prims.append({**base, "kind": "row", "at": round(at + 0.7 + k * 0.9, 2), "pos": (x, cy + 40),
                              "label": row["label"], "value": row["value"], "color": palette[k % 3]})
        elif kind == "flow":
            nodes = content["nodes"]
            span = 700 if len(nodes) == 2 else 1250  # 단계가 적으면 가깝게 붙여 한눈에 읽히게 한다
            xs = [int(cx - span / 2 + span * k / max(len(nodes) - 1, 1)) for k in range(len(nodes))] if len(nodes) > 1 else [cx]
            stagger = min(0.9, max(0.35, (until - at - 0.8) / max(len(nodes), 1)))  # 짧은 주제는 간격을 줄인다
            for k, node in enumerate(nodes):
                t = round(at + k * stagger, 2)
                prims.append({**base, "kind": "node", "at": t, "pos": (xs[k], cy), "label": node["label"],
                              "icon": node.get("icon")})
                if k < len(nodes) - 1:
                    prims.append({**base, "kind": "arrow", "at": round(t + 0.5, 2),
                                  "pos": ((xs[k] + xs[k + 1]) // 2, cy - 30), "direction": "right", "size": 200})
    return prims


def _sprite_for(prim: dict, p: float) -> Image.Image:
    kind = prim["kind"]
    if kind == "text":
        return _text_sprite(prim["text"], prim["size"], prim["color"], stroke=0,
                            font_path=_FONT_DISPLAY if prim["color"] == RED else _FONT_BOLD,
                            max_width=prim.get("max_width", WIDE_SLOT_MAX_WIDTH))
    if kind == "number":
        return _number_sprite(prim["value"], prim["label"], p if prim["anim"] == "count" else 1.0,
                              prim.get("max_width", WIDE_SLOT_MAX_WIDTH), prim.get("size_cap"))
    if kind == "icon":
        return _icon_sprite(prim["name"], prim.get("icon_size", 400))
    if kind == "arrow":
        return _arrow_sprite(prim["direction"], p if prim["anim"] != "pop" else 1.0, prim.get("size", 300))
    if kind == "bars":
        return _bars_sprite(prim["title"], prim["items"], p if prim["anim"] == "grow" else 1.0)
    if kind == "row":
        return _row_sprite(prim["label"], prim["value"], prim["color"])
    return _node_sprite(prim["label"], prim.get("icon"))


def _animated(prim: dict) -> bool:
    return prim["kind"] in ("arrow", "bars") or (prim["kind"] == "number" and prim["anim"] == "count")


def render_frame(base: Image.Image, prims: list[dict], t: float, cache: dict) -> Image.Image:
    frame = base.copy()
    for number, prim in enumerate(prims):
        if t < prim["at"] or t >= prim["until"]:
            continue
        enter = ENTER_SECONDS.get(prim["kind"], 0.45)
        p = min((t - prim["at"]) / enter, 1.0)
        alpha = min(_ease_out(p) * 1.4, 1.0)
        fade_out = min((prim["until"] - t) / EXIT_FADE_SECONDS, 1.0)
        alpha *= max(fade_out, 0.0) if prim["until"] - t < EXIT_FADE_SECONDS else 1.0
        if alpha <= 0:
            continue
        if _animated(prim):
            sprite = _sprite_for(prim, p)
        else:
            sprite = cache.get(number)
            if sprite is None:
                sprite = cache[number] = _sprite_for(prim, 1.0)
        scale = 1.0
        if prim["anim"] == "pop" and p < 1.0:
            scale = 0.8 + 0.2 * _ease_out(p) + (0.06 * (1 - abs(2 * p - 1)) if p > 0 else 0)
        if scale != 1.0:
            sprite = sprite.resize((max(1, int(sprite.width * scale)), max(1, int(sprite.height * scale))))
        rise = 0 if prim["anim"] == "pop" else int(40 * (1 - _ease_out(p)))
        x = int(prim["pos"][0] - sprite.width / 2)
        y = int(prim["pos"][1] - sprite.height / 2)
        y = min(y, SUBTITLE_SAFE_BOTTOM - sprite.height)  # 하단 자막과 겹치지 않게 위로 올린다
        y = max(y, 20) + rise
        if alpha < 1.0:
            sprite = sprite.copy()
            sprite.putalpha(sprite.getchannel("A").point(lambda v, a=alpha: int(v * a)))
        frame.paste(sprite, (x, y), sprite)
    return frame


def render_sequence_clip(sequence: dict, t0: float, t1: float, board_path: str, output_path: str) -> bool:
    """시퀀스 시간축의 [t0, t1) 구간을 mp4로 만든다. 장면마다 호출해도 같은 시간 함수라 화면이 이어진다."""
    width, height = CANVAS
    try:
        base = Image.open(board_path).convert("RGB").resize(CANVAS)
        prims = expand_primitives(sequence)
        cache: dict = {}
        frames = max(int(round((t1 - t0) * FPS)), 1)
        cmd = [
            "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(FPS), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", output_path, "-loglevel", "error",
        ]
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for k in range(frames):
                process.stdin.write(render_frame(base, prims, t0 + k / FPS, cache).tobytes())
        finally:
            process.stdin.close()
        process.wait(timeout=300)
        return process.returncode == 0 and Path(output_path).is_file() and Path(output_path).stat().st_size > 1000
    except Exception:
        logger.exception("시퀀스 클립 렌더링 실패")
        return False
