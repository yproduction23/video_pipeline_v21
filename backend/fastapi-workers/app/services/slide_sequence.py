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
from app.services.slide_overlay import _FONT_BOLD, _font, spell_like_narration as _spell_like_narration, validate_overlay_plan

logger = logging.getLogger(__name__)

CANVAS = (1920, 1080)
FPS = 30
SEQUENCE_VERSION = "slide-sequence-v2"
RED = (229, 57, 53)
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
MIN_TOPIC_SECONDS = 5.0              # 주제(한 가지 이야기)의 최소 길이: 이보다 짧으면 앞 주제에 합친다
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
    "이어지는 구간을 받아, 그 구간에 나타날 요소들의 타임라인을 설계한다.\n\n"
    "[레퍼런스 영상(부동산 10~33초)에서 확인한 연출 원칙]\n"
    "1. 구간은 '주제(한 가지 이야기)' 단위로 나뉜다. 한 주제는 보통 6~12초이고, 장면 경계와 무관하다.\n"
    "2. 한 주제 안에서는 요소가 하나씩 등장해 쌓이고 주제가 끝날 때까지 화면에 남는다. 새 주제가 시작될 때만 앞 주제의 "
    "요소가 한꺼번에 사라진다. 화면이 비는 순간이 없어야 한다.\n"
    "3. 주제마다 중심 요소 하나(큰 숫자, 핵심 문구, 아이콘+숫자 짝)로 시작하고, 그 둘레에 비교 대상·보조 숫자를 붙인다. "
    "예: 돈뭉치 아이콘 + \"10억 원\" → 옆에 \"현재 11.8%\" → 이어서 \"과거 8%\"(앞 값 자리에서 숫자만 바뀜).\n"
    "4. 같은 자리의 값이 바뀔 때는 같은 종류(숫자→숫자, 문구→문구)만 제자리에서 바꾼다(replaces에 앞 요소 id). 아이콘을 다른 "
    "아이콘으로 바꾸지 않는다.\n"
    "5. 글자는 작고 차분하다. 큰 것은 숫자 하나뿐이다.\n\n"
    "요소 종류(kind)와 content:\n"
    f'- "text": 핵심어 문구. {{"text": 그 장면 내레이션에 글자 그대로 있는 4~{MAX_TEXT_CHARS}자 핵심어, "color": "red|white"}}. '
    "자막 문장을 그대로 되풀이하지 말고 핵심어(2~3단어)만 뽑는다. red는 핵심 주장.\n"
    '- "number": 큰 숫자. {"value": 내레이션에 글자 그대로 있는 숫자 구절 16자 이내, "label": 8자 이내 소제목(없어도 됨, 숫자 금지)}.\n'
    '- "icon": 그림. {"name": 아래 이름 중 하나}. 그 순간 말하는 대상을 직접 그린 것만 쓴다(예: "외국인"→foreigner, "반도체"→chip). '
    "뜻이 정확히 맞는 아이콘이 없으면 아이콘을 쓰지 않는다. 같은 아이콘을 한 구간에서 두 번 쓰지 않는다.\n"
    '- "arrow": 그려지는 화살표. {"direction": "up|down|right"} (상승·하락·이어짐). 숫자나 문구 옆에서만 쓴다.\n'
    '- "bars": 막대 비교(화면 전체, center 전용). {"title": 18자 이내(숫자 금지), "items": [{"label": 대상 이름, "value_text": 내레이션에 적힌 숫자+단위 그대로}] 2~4개}.\n'
    '- "list": 순서대로 나타나는 목록(화면 전체, center 전용). {"title": 18자 이내(숫자 금지), "items": [내레이션에 글자 그대로 있는 24자 이내 구절 2~4개]}.\n'
    '- "rows": 이어서 추가되는 비교 행(화면 전체, center 전용). {"headline": 내레이션에 글자 그대로 있는 12자 이내 구절, "rows": [{"label": 6자 이내(숫자 금지), "value": 내레이션에 글자 그대로 있는 숫자 구절}] 2~3개}.\n'
    '- "flow": A→B→C가 차례로 나타나는 흐름도(화면 전체, center 전용). {"nodes": [{"label": 8자 이내(숫자 금지), "icon": 아이콘 이름}] 2~3개}.\n'
    "아이콘 이름: " + ", ".join(f"{name}({ICON_HINTS[name]})" for name in ICON_NAMES) + "\n\n"
    "각 요소 필드: id(e1, e2…), topic(주제 번호 1부터 시간 순서대로), kind, scene(구간에 포함된 장면 번호), "
    "anchor(그 장면 내레이션에 글자 그대로 있는 구절 — 이 말을 하는 순간에 요소가 나타난다), "
    "slot(center|top|bottom|left|right|upper_left|upper_right|lower_left|lower_right), "
    "anim(rise|draw|count|grow — number는 count, arrow는 draw, bars는 grow, 나머지는 rise), content, "
    "replaces(선택: 같은 자리에서 값만 바뀌는 앞 요소의 id).\n"
    "규칙:\n"
    "- 주제는 최소 5초 이상. 주제마다 text·number·bars·list·rows·flow 중 하나 이상이 있어야 한다(아이콘·화살표만으로 주제를 만들지 않는다).\n"
    "- 한 주제의 요소는 5개 이하. 서로 겹치지 않게 다른 자리를 쓴다(숫자는 left/right, 문구는 top, 아이콘은 숫자 옆). "
    "center(막대·목록·비교 행·흐름도)를 쓰는 주제에는 다른 요소를 넣지 않는다.\n"
    "- 내레이션에 숫자(퍼센트·원·배 등)가 나오면 그 숫자를 number로 크게 보여 주는 것이 가장 우선이다. 숫자는 아이콘·문구보다 "
    "먼저 고른다.\n"
    "- 한 주제는 12초를 넘기지 않고, 새 요소가 5초 이상 나오지 않는 구간이 없게 한다(긴 이야기는 주제를 나눈다).\n"
    "- 구간은 첫 장면 시작과 함께 첫 요소가 나와야 하고, 주제가 바뀔 때마다 다음 주제의 첫 요소가 바로 이어져 빈 화면이 없어야 한다.\n"
    "- text·value·items는 내레이션의 연속된 구절을 한 글자도 바꾸지 말고 복사한다(조사·어순·표기 그대로, 요약·바꿔쓰기·물음표 추가 금지, "
    "\"5.3퍼센트\"를 \"5.3%\"로 쓰지 않는다). 예: 내레이션이 \"미국 10년물 국채금리가 24년 만에 5.3퍼센트를 넘어섰다고\"이면 "
    "text는 \"24년 만에\", number의 value는 \"5.3퍼센트\".\n"
    "- 모든 text·value·items·label은 해당 장면 내레이션(또는 직전 두 장면)에 적힌 글자를 그대로 옮긴다. 숫자를 "
    "계산·반올림·추정·새로 만들지 않는다. 같은 문구를 두 번 쓰지 않는다.\n"
    '출력은 JSON만: {"elements":[{"id":"e1","topic":1,"kind":"...","scene":번호,"anchor":"...","slot":"...","anim":"...","content":{...}}]}'
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
        overlay = scene.get("overlay") or {}
        kind = _OVERLAY_TO_KIND.get(overlay.get("type"))
        if not kind:
            continue
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
        elements.append({
            "id": f"e{len(elements) + 1}", "kind": kind, "scene": scene["index"], "anchor": "", "slot": "center",
            "topic": len(elements) + 1, "replaces": None,
            "anim": {"number": "count", "bars": "grow"}.get(kind, "rise"),
            "at": round(starts[scene["index"]] - seq_start + 0.3, 2), "content": content,
        })
    return elements


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
        candidates = ["center"] if element["kind"] in _WIDE_KINDS else (
            [element["slot"], *[s for s in _SLOT_FALLBACKS if s != element["slot"]]]
            if element["slot"] != "center" else list(_SLOT_FALLBACKS)
        )
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
            if merged and end - group[0]["at"] < MIN_TOPIC_SECONDS:
                merged[-1].extend(group)
            else:
                merged.append(group)
        topics = merged
    topics = [g for g in topics if any(e["kind"] in _ANCHOR_KINDS for e in g)]
    survivors: list[list[dict]] = []
    for k, group in enumerate(topics):
        end = topics[k + 1][0]["at"] if k + 1 < len(topics) else duration
        group.sort(key=lambda e: e["at"])
        head, rest = group[0], group[1:]
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
    try:
        raw = _call_llm(_SEQUENCE_SYSTEM, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        designed = _normalize_elements(data.get("elements") or [], run, plan)
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


def _text_sprite(text: str, size: int, color: tuple, *, stroke: int = 0, max_width: int = 1500) -> Image.Image:
    font = _font(_FONT_BOLD, size)
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


def _number_sprite(value: str, label: str, p: float, max_width: int = WIDE_SLOT_MAX_WIDTH) -> Image.Image:
    # 카운트업 도중 폭이 변해 크기가 흔들리지 않게 최종 값 기준으로 글자 크기를 정한다(좌우 자리에서 잘리지 않게).
    size = _fit_size(value, 190, max_width)
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
    text = _text_sprite(label, 54, WHITE, max_width=420)
    size = 240
    sprite = Image.new("RGBA", (max(size + 40, text.width), size + 40 + text.height), (0, 0, 0, 0))
    if icon:
        sprite.alpha_composite(_icon_sprite(icon, size), ((sprite.width - size - 40) // 2, 0))
    sprite.alpha_composite(text, ((sprite.width - text.width) // 2, size + 30))
    return sprite


def expand_primitives(sequence: dict) -> list[dict]:
    """합성 요소(list·rows·flow)를 화면에 그릴 수 있는 기본 요소로 풀어 위치·등장 시각을 정한다(결정론적)."""
    prims: list[dict] = []
    for element in sequence["elements"]:
        kind, content, at, until = element["kind"], element["content"], element["at"], element["until"]
        cx, cy = SLOTS.get(element["slot"], SLOTS["center"])
        base = {"element": element["id"], "until": until, "anim": element["anim"],
                "max_width": SIDE_SLOT_MAX_WIDTH if element["slot"] in _SIDE_SLOTS else WIDE_SLOT_MAX_WIDTH}
        if kind == "text":
            size = 112 if element["slot"] in ("center", "top", "bottom") else 92
            color = WHITE if content.get("color") == "white" else RED
            prims.append({**base, "kind": "text", "at": at, "pos": (cx, cy), "text": content["text"], "size": size,
                          "color": color})
        elif kind == "number":
            prims.append({**base, "kind": "number", "at": at, "pos": (cx, cy), "value": content["value"],
                          "label": content.get("label", "")})
        elif kind == "icon":
            prims.append({**base, "kind": "icon", "at": at, "pos": (cx, cy), "name": content["name"]})
        elif kind == "arrow":
            prims.append({**base, "kind": "arrow", "at": at, "pos": (cx, cy), "direction": content["direction"]})
        elif kind == "bars":
            prims.append({**base, "kind": "bars", "at": at, "pos": (cx, cy), "title": content.get("title", ""),
                          "items": content["items"]})
        elif kind == "list":
            prims.append({**base, "kind": "text", "at": at, "pos": (cx, cy - 250), "text": content["title"],
                          "size": 64, "color": ACCENT[:3]})
            for k, item in enumerate(content["items"]):
                prims.append({**base, "kind": "text", "at": round(at + 0.55 + k * 0.75, 2),
                              "pos": (cx, cy - 90 + k * 130), "text": f"{k + 1}  {item}", "size": 70, "color": WHITE})
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
            span = 1400
            xs = [int(cx - span / 2 + span * k / max(len(nodes) - 1, 1)) for k in range(len(nodes))] if len(nodes) > 1 else [cx]
            for k, node in enumerate(nodes):
                t = round(at + k * 0.9, 2)
                prims.append({**base, "kind": "node", "at": t, "pos": (xs[k], cy), "label": node["label"],
                              "icon": node.get("icon")})
                if k < len(nodes) - 1:
                    prims.append({**base, "kind": "arrow", "at": round(t + 0.5, 2),
                                  "pos": ((xs[k] + xs[k + 1]) // 2, cy - 30), "direction": "right", "size": 200})
    return prims


def _sprite_for(prim: dict, p: float) -> Image.Image:
    kind = prim["kind"]
    if kind == "text":
        return _text_sprite(prim["text"], prim["size"], prim["color"], stroke=3 if prim["color"] == RED else 0,
                            max_width=prim.get("max_width", WIDE_SLOT_MAX_WIDTH))
    if kind == "number":
        return _number_sprite(prim["value"], prim["label"], p if prim["anim"] == "count" else 1.0,
                              prim.get("max_width", WIDE_SLOT_MAX_WIDTH))
    if kind == "icon":
        return _icon_sprite(prim["name"])
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
