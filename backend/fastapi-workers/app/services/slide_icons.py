"""슬라이드형 오버레이용 결정론적 아이콘 세트 (Pillow 도형으로 그린다).

2026-10-15 사용자 요청: 레퍼런스 영상은 "10억"이라고 말할 때 돈뭉치가 나오는 식으로
키워드에 맞는 그림이 함께 뜬다. 생성형 이미지는 AI 티가 나고 비용이 들어, 고정된
아이콘 목록에서 Claude가 이름만 고르고 렌더는 항상 같은 스타일로 그린다.
"""
from __future__ import annotations

from PIL import ImageDraw

ACCENT = (255, 184, 28, 255)
WHITE = (255, 255, 255, 255)
DARK = (16, 20, 32, 255)
DEEP_ACCENT = (214, 140, 10, 255)

ICON_NAMES = (
    "money_stack", "coin", "house", "building", "bank", "chart_up", "chart_down",
    "arrow_up", "arrow_down", "person", "people", "warning", "clock", "document",
)

ICON_HINTS = {
    "money_stack": "돈뭉치(금액·자산·현금)", "coin": "동전(소액·가격·단위)", "house": "집(주택·부동산)",
    "building": "건물(기업·빌딩)", "bank": "은행(금리·대출·금융기관)", "chart_up": "상승 차트",
    "chart_down": "하락 차트", "arrow_up": "위 화살표(상승)", "arrow_down": "아래 화살표(하락)",
    "person": "사람 한 명", "people": "여러 사람(투자자·가구)", "warning": "경고(위험·주의)",
    "clock": "시계(기간·시점)", "document": "문서(보고서·공시·정책)",
}


def draw_icon(draw: ImageDraw.ImageDraw, name: str, cx: int, cy: int, size: int) -> bool:
    """중심 (cx, cy), 한 변 size인 정사각 영역 안에 아이콘을 그린다."""
    painter = _PAINTERS.get(name)
    if painter is None:
        return False
    painter(draw, cx, cy, size)
    return True


def _money_stack(d, cx, cy, s):
    w, h = int(s * 0.86), int(s * 0.34)
    for i, offset in enumerate((int(s * 0.28), int(s * 0.04), -int(s * 0.20))):
        top = cy + offset - h // 2
        d.rounded_rectangle((cx - w // 2, top, cx + w // 2, top + h), radius=int(s * 0.05),
                            fill=ACCENT if i == 2 else DEEP_ACCENT, outline=DARK, width=max(2, s // 40))
    # 맨 위 지폐: 가운데 원 + 좌우 띠
    top = cy - int(s * 0.20) - h // 2
    r = int(h * 0.30)
    d.ellipse((cx - r, top + h // 2 - r, cx + r, top + h // 2 + r), outline=DARK, width=max(2, s // 40))
    d.rectangle((cx - w // 2 + int(s * 0.07), top + h // 2 - 2, cx - w // 2 + int(s * 0.17), top + h // 2 + 2), fill=DARK)
    d.rectangle((cx + w // 2 - int(s * 0.17), top + h // 2 - 2, cx + w // 2 - int(s * 0.07), top + h // 2 + 2), fill=DARK)


def _coin(d, cx, cy, s):
    r = int(s * 0.42)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ACCENT, outline=DARK, width=max(3, s // 30))
    r2 = int(r * 0.68)
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), outline=DEEP_ACCENT, width=max(3, s // 36))
    d.rectangle((cx - int(s * 0.04), cy - int(r2 * 0.6), cx + int(s * 0.04), cy + int(r2 * 0.6)), fill=DEEP_ACCENT)


def _house(d, cx, cy, s):
    w = int(s * 0.62)
    top = cy - int(s * 0.40)
    d.polygon([(cx - int(s * 0.46), cy - int(s * 0.04)), (cx, top), (cx + int(s * 0.46), cy - int(s * 0.04))],
              fill=ACCENT, outline=DARK)
    d.rectangle((cx - w // 2, cy - int(s * 0.04), cx + w // 2, cy + int(s * 0.38)), fill=WHITE, outline=DARK, width=max(2, s // 40))
    d.rectangle((cx - int(s * 0.09), cy + int(s * 0.12), cx + int(s * 0.09), cy + int(s * 0.38)), fill=DEEP_ACCENT)


def _building(d, cx, cy, s):
    w, h = int(s * 0.46), int(s * 0.84)
    left, top = cx - w // 2, cy - h // 2
    d.rectangle((left, top, left + w, top + h), fill=WHITE, outline=DARK, width=max(2, s // 40))
    cols, rows = 3, 5
    for r in range(rows):
        for c in range(cols):
            x = left + int(w * (0.14 + c * 0.29))
            y = top + int(h * (0.08 + r * 0.17))
            d.rectangle((x, y, x + int(w * 0.16), y + int(h * 0.09)), fill=ACCENT)


def _bank(d, cx, cy, s):
    d.polygon([(cx - int(s * 0.46), cy - int(s * 0.12)), (cx, cy - int(s * 0.44)), (cx + int(s * 0.46), cy - int(s * 0.12))],
              fill=ACCENT, outline=DARK)
    for i in range(4):
        x = cx - int(s * 0.34) + i * int(s * 0.23)
        d.rectangle((x, cy - int(s * 0.06), x + int(s * 0.12), cy + int(s * 0.28)), fill=WHITE, outline=DARK)
    d.rectangle((cx - int(s * 0.46), cy + int(s * 0.30), cx + int(s * 0.46), cy + int(s * 0.42)), fill=DEEP_ACCENT, outline=DARK)


def _bars(d, cx, cy, s, heights):
    w = int(s * 0.18)
    base = cy + int(s * 0.38)
    for i, h in enumerate(heights):
        x = cx - int(s * 0.34) + i * int(s * 0.26)
        d.rectangle((x, base - int(s * h), x + w, base), fill=ACCENT, outline=DARK, width=max(2, s // 50))
    d.line((cx - int(s * 0.42), base, cx + int(s * 0.42), base), fill=WHITE, width=max(3, s // 40))


def _chart_up(d, cx, cy, s):
    _bars(d, cx, cy, s, (0.25, 0.42, 0.62))
    _arrow(d, cx + int(s * 0.18), cy - int(s * 0.30), s * 0.5, up=True)


def _chart_down(d, cx, cy, s):
    _bars(d, cx, cy, s, (0.62, 0.42, 0.25))
    _arrow(d, cx + int(s * 0.18), cy - int(s * 0.30), s * 0.5, up=False)


def _arrow(d, cx, cy, s, *, up: bool):
    s = int(s)
    head = int(s * 0.5)
    shaft_w = int(s * 0.22)
    sign = -1 if up else 1
    tip_y = cy + sign * int(s * 0.46)
    base_y = cy - sign * int(s * 0.46)
    mid_y = tip_y - sign * head
    d.rectangle((cx - shaft_w // 2, min(base_y, mid_y), cx + shaft_w // 2, max(base_y, mid_y)),
                fill=WHITE if not up else ACCENT)
    d.polygon([(cx - int(s * 0.34), mid_y), (cx + int(s * 0.34), mid_y), (cx, tip_y)],
              fill=ACCENT if up else WHITE, outline=DARK)


def _arrow_up(d, cx, cy, s):
    _arrow(d, cx, cy, s * 0.9, up=True)


def _arrow_down(d, cx, cy, s):
    _arrow(d, cx, cy, s * 0.9, up=False)


def _person(d, cx, cy, s, scale=1.0, fill=WHITE):
    r = int(s * 0.17 * scale)
    d.ellipse((cx - r, cy - int(s * 0.30 * scale) - r, cx + r, cy - int(s * 0.30 * scale) + r),
              fill=fill, outline=DARK, width=max(2, s // 50))
    bw = int(s * 0.34 * scale)
    d.rounded_rectangle((cx - bw, cy - int(s * 0.08 * scale), cx + bw, cy + int(s * 0.36 * scale)),
                        radius=int(bw * 0.7), fill=fill, outline=DARK, width=max(2, s // 50))


def _people(d, cx, cy, s):
    _person(d, cx - int(s * 0.26), cy + int(s * 0.04), s, 0.78, DEEP_ACCENT)
    _person(d, cx + int(s * 0.26), cy + int(s * 0.04), s, 0.78, DEEP_ACCENT)
    _person(d, cx, cy, s, 1.0, ACCENT)


def _warning(d, cx, cy, s):
    d.polygon([(cx, cy - int(s * 0.44)), (cx - int(s * 0.46), cy + int(s * 0.36)), (cx + int(s * 0.46), cy + int(s * 0.36))],
              fill=ACCENT, outline=DARK)
    d.rectangle((cx - int(s * 0.04), cy - int(s * 0.16), cx + int(s * 0.04), cy + int(s * 0.10)), fill=DARK)
    d.ellipse((cx - int(s * 0.05), cy + int(s * 0.16), cx + int(s * 0.05), cy + int(s * 0.26)), fill=DARK)


def _clock(d, cx, cy, s):
    r = int(s * 0.42)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=WHITE, outline=DARK, width=max(3, s // 30))
    d.line((cx, cy, cx, cy - int(r * 0.62)), fill=DARK, width=max(4, s // 24))
    d.line((cx, cy, cx + int(r * 0.46), cy + int(r * 0.12)), fill=ACCENT, width=max(4, s // 24))


def _document(d, cx, cy, s):
    w, h = int(s * 0.58), int(s * 0.80)
    left, top = cx - w // 2, cy - h // 2
    d.rectangle((left, top, left + w, top + h), fill=WHITE, outline=DARK, width=max(2, s // 40))
    for i in range(4):
        y = top + int(h * (0.20 + i * 0.18))
        d.rectangle((left + int(w * 0.14), y, left + int(w * (0.86 if i % 2 == 0 else 0.62)), y + int(h * 0.05)), fill=ACCENT)


_PAINTERS = {
    "money_stack": _money_stack, "coin": _coin, "house": _house, "building": _building, "bank": _bank,
    "chart_up": _chart_up, "chart_down": _chart_down, "arrow_up": _arrow_up, "arrow_down": _arrow_down,
    "person": _person, "people": _people, "warning": _warning, "clock": _clock, "document": _document,
}
