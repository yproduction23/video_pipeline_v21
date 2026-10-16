"""슬라이드형 모션 시퀀스의 구성 템플릿.

2026-10-16 운영자 피드백(20번 Job 완성 영상): 요소를 자유 배치하니 좌·우·좌로 뒤섞이고, 중간에 그래프가 끼어들어 좁아지고,
"아이콘+글"이었으면 아래도 같은 구조여야 하는데 제각각이었다("그냥 막 넣은 것 같다").
그래서 Claude는 자리를 정하지 않고 "주제마다 템플릿 하나와 채울 내용"만 정한다. 자리·크기·정렬은 템플릿이 결정론적으로 정하며,
같은 템플릿 안의 항목은 항상 같은 구조(둘 다 아이콘+글, 또는 둘 다 숫자+글)로 맞춘다.

템플릿(레퍼런스 영상의 카드 + 운영자가 좋다고 한 "3단 연결"):
- title_number : 제목 문구 + 큰 숫자(+ 아이콘, 보조 한 줄)
- compare_pair : 같은 구조의 두 항목을 기호(+, vs, →, =)로 잇는 비교
- flow         : 2~3단계 연결(아이콘+글 → 아이콘+글 → …)
- headline_sub : 굵은 빨간 한 줄 + 작은 보조 문구
- bars / list / rows : 막대 비교, 번호 목록, 숫자 행(화면 전체)
"""
from __future__ import annotations

from typing import Callable, Protocol

CANVAS_W, CANVAS_H = 1920, 1080
TEMPLATES = ("title_number", "compare_pair", "flow", "headline_sub", "bars", "list", "rows")
OPERATORS = ("+", "vs", "→", "=")
GRAY = "gray"

# 레이아웃 상수(px). 하단 자막 안전 영역(y>890)은 피한다.
TITLE_Y = 235
NUMBER_Y = 560
CAPTION_Y = 790
PAIR_LEFT_X, PAIR_RIGHT_X, PAIR_MID_X = 560, 1360, 960
PAIR_ICON_Y, PAIR_VALUE_Y, PAIR_LABEL_Y = 450, 480, 700
PAIR_ICON_SIZE = 260
HEADLINE_Y, SUB_Y = 470, 690


class Context(Protocol):
    """slide_sequence가 넘겨 주는 검증·시각 계산 도구(원문 일치·계산 검산은 기존 검증기를 그대로 쓴다)."""

    def label(self, scene: int, text: object) -> str | None: ...
    def text(self, scene: int, text: object, color: str) -> dict | None: ...
    def content(self, kind: str, content: dict, scene: int) -> dict | None: ...
    def icon(self, name: object) -> str | None: ...
    def time(self, scene: int, anchor: object) -> float: ...


def _element(kind: str, scene: int, anchor: str, at: float, content: dict, pos: tuple[int, int], anim: str = "rise",
             **extra) -> dict:
    return {"kind": kind, "scene": scene, "anchor": str(anchor or "")[:60], "slot": "center", "pos": pos, "anim": anim,
            "at": round(at, 2), "content": content, "fixed": True, **extra}


def expand_topic(topic: dict, ctx: Context) -> list[dict]:
    """주제 하나를 위치가 정해진 요소 목록으로 풀어 낸다. 검증을 통과하지 못한 부분이 있으면 그 항목만 뺀다.
    주제로 성립하지 않으면(핵심 내용 없음) 빈 목록을 돌려준다."""
    template = str(topic.get("template") or "").strip()
    try:
        scene = int(topic.get("scene"))
    except (TypeError, ValueError):
        return []
    handler = _HANDLERS.get(template)
    if handler is None:
        return []
    anchor = str(topic.get("anchor") or "")
    start = ctx.time(scene, anchor)
    return handler(topic, ctx, scene, anchor, start)


# ---- 템플릿별 배치 ------------------------------------------------------------------------------------------

def _title_number(topic: dict, ctx: Context, scene: int, anchor: str, start: float) -> list[dict]:
    number_raw = topic.get("number") if isinstance(topic.get("number"), dict) else None
    number = ctx.content("number", number_raw, scene) if number_raw else None
    if not number:
        return []
    icon = ctx.icon(topic.get("icon"))
    title = ctx.text(scene, topic.get("title"), "white") if topic.get("title") else None
    caption = ctx.label(scene, topic.get("caption")) if topic.get("caption") else None
    elements: list[dict] = []
    if title:
        elements.append(_element("text", scene, anchor, start, title, (CANVAS_W // 2, TITLE_Y), size=88, max_width=1500))
    number_x, number_width = (1260, 880) if icon else (CANVAS_W // 2, 1500)
    if icon:
        elements.append(_element("icon", scene, anchor, start + 0.1, {"name": icon}, (600, NUMBER_Y), icon_size=300))
    elements.append(_element("number", scene, anchor, start + (0.25 if title else 0.0), number, (number_x, NUMBER_Y),
                             anim="count", max_width=number_width))
    if caption:
        elements.append(_element(
            "text", scene, anchor, start + 0.9, {"text": caption, "color": GRAY}, (CANVAS_W // 2, CAPTION_Y),
            size=56, max_width=1500))
    return elements


def _side(item: dict, ctx: Context, scene: int, structure: str) -> dict | None:
    """compare_pair의 한쪽 항목을 검증한다. structure(icon|value|label)가 양쪽에서 같아야 한다."""
    label = ctx.label(scene, item.get("label"))
    if not label:
        return None
    out: dict = {"label": label}
    if structure == "icon":
        icon = ctx.icon(item.get("icon"))
        if not icon:
            return None
        out["icon"] = icon
    elif structure == "value":
        value = ctx.content("number", {"value": item.get("value"), "label": ""}, scene) if item.get("value") else None
        if not value:
            return None
        out["value"] = value
    return out


def _compare_pair(topic: dict, ctx: Context, scene: int, anchor: str, start: float) -> list[dict]:
    raw_a, raw_b = topic.get("a"), topic.get("b")
    if not isinstance(raw_a, dict) or not isinstance(raw_b, dict):
        return []
    op = str(topic.get("op") or "+").strip()
    op = op if op in OPERATORS else "+"
    # 양쪽 구조를 맞춘다: 둘 다 아이콘이 있으면 아이콘+글, 둘 다 숫자가 있으면 숫자+글, 아니면 글만.
    structure = "label"
    if ctx.icon(raw_a.get("icon")) and ctx.icon(raw_b.get("icon")):
        structure = "icon"
    elif raw_a.get("value") and raw_b.get("value"):
        structure = "value"
    side_a, side_b = _side(raw_a, ctx, scene, structure), _side(raw_b, ctx, scene, structure)
    if not side_a or not side_b:
        if structure != "label":  # 구조가 안 맞으면 글만으로 내려가 두 항목을 지킨다
            side_a, side_b = _side(raw_a, ctx, scene, "label"), _side(raw_b, ctx, scene, "label")
            structure = "label"
        if not side_a or not side_b:
            return []
    b_anchor = str(raw_b.get("anchor") or "")
    b_start = ctx.time(scene, b_anchor) if b_anchor else start + 1.3
    b_start = max(b_start, start + 0.8)
    title = ctx.text(scene, topic.get("title"), "white") if topic.get("title") else None
    elements: list[dict] = []
    if title:
        elements.append(_element("text", scene, anchor, start, title, (CANVAS_W // 2, TITLE_Y - 20), size=76,
                                 max_width=1500))

    def column(side: dict, x: int, at: float) -> list[dict]:
        parts: list[dict] = []
        if structure == "icon":
            parts.append(_element("icon", scene, anchor, at, {"name": side["icon"]}, (x, PAIR_ICON_Y),
                                  icon_size=PAIR_ICON_SIZE))
        elif structure == "value":
            parts.append(_element("number", scene, anchor, at, side["value"], (x, PAIR_VALUE_Y), anim="count",
                                  max_width=640, size_cap=150))
        label_y = PAIR_LABEL_Y if structure != "label" else 540
        parts.append(_element("text", scene, anchor, at + 0.15, {"text": side["label"], "color": "white"}, (x, label_y),
                              size=70, max_width=640))
        return parts

    elements += column(side_a, PAIR_LEFT_X, start + (0.2 if title else 0.0))
    elements.append(_element("symbol", scene, anchor, b_start - 0.1, {"text": op}, (PAIR_MID_X, 520 if structure != "label" else 540)))
    elements += column(side_b, PAIR_RIGHT_X, b_start)
    return elements


def _flow(topic: dict, ctx: Context, scene: int, anchor: str, start: float) -> list[dict]:
    nodes_raw = topic.get("nodes") if isinstance(topic.get("nodes"), list) else []
    nodes: list[dict] = []
    for raw in nodes_raw[:3]:
        if not isinstance(raw, dict):
            continue
        label = ctx.label(scene, raw.get("label"))
        icon = ctx.icon(raw.get("icon"))
        if label:
            nodes.append({"label": label, "icon": icon, "anchor": str(raw.get("anchor") or "")})
    if len(nodes) < 2:
        return []
    with_icon = [bool(n["icon"]) for n in nodes]
    if any(with_icon) and not all(with_icon):  # 같은 구조: 아이콘이 일부만 있으면 모두 뺀다
        for n in nodes:
            n["icon"] = None
    content = {"nodes": [{"label": n["label"], "icon": n["icon"]} for n in nodes]}
    return [_element("flow", scene, anchor, start, content, (CANVAS_W // 2, 520))]


def _headline_sub(topic: dict, ctx: Context, scene: int, anchor: str, start: float) -> list[dict]:
    headline = ctx.text(scene, topic.get("headline"), "red")
    if not headline:
        return []
    sub = ctx.label(scene, topic.get("sub")) if topic.get("sub") else None
    y = HEADLINE_Y if sub else 520
    elements = [_element("text", scene, anchor, start, headline, (CANVAS_W // 2, y), size=210, max_width=1700)]
    if sub:
        elements.append(_element("text", scene, anchor, start + 0.7, {"text": sub, "color": GRAY}, (CANVAS_W // 2, SUB_Y),
                                 size=58, max_width=1500))
    return elements


def _full_screen(kind: str, key: str) -> Callable:
    def handler(topic: dict, ctx: Context, scene: int, anchor: str, start: float) -> list[dict]:
        content = {k: topic.get(k) for k in ("title", "items", "headline", "rows") if k in topic}
        validated = ctx.content(kind, content, scene)
        if not validated:
            return []
        return [_element(kind, scene, anchor, start, validated, (CANVAS_W // 2, 540),
                         anim="grow" if kind == "bars" else "rise")]
    return handler


_HANDLERS: dict[str, Callable] = {
    "title_number": _title_number,
    "compare_pair": _compare_pair,
    "flow": _flow,
    "headline_sub": _headline_sub,
    "bars": _full_screen("bars", "items"),
    "list": _full_screen("list", "items"),
    "rows": _full_screen("rows", "rows"),
}
