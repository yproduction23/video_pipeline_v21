"""연속 일수 달력(streak) — "8거래일 연속"을 들으면 달력에 날짜 칸 8개가 나오고 X가 차례로 쳐진다.

2026-10-17 운영자 요청: 자막을 그대로 쓰거나 엉뚱한 아이콘이 아니라, 말의 뜻이 연상되는 진짜 모션그래픽이어야 한다.
어떤 장면(스톡 위 후편집·회색 배경 시퀀스)에서도 같은 그림이 나오도록 그리기와 내레이션 감지를 이 파일에 모은다.
날짜 칸에는 숫자를 쓰지 않고, 일수 문구는 내레이션 원문 그대로 마지막에 나온다."""
from __future__ import annotations

import re
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from app.services.slide_icons import ACCENT

_FONT_BOLD = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
STREAK_MIN_DAYS, STREAK_MAX_DAYS = 2, 14
CANVAS = (1920, 1080)
SUBTITLE_SAFE_BOTTOM = 890
CENTER_Y = 560

# "N거래일 연속", "3주째"처럼 연속을 말하는 구절만(누적·하루 만에는 연속이 아니다).
_STREAK_PATTERN = re.compile(r"(\d{1,2})\s*(거래일|영업일|개월|분기|일|주)\s*(?:연속|째|연달아)")


def detect_streak(narration: object) -> Optional[dict]:
    """내레이션에 "N일 연속" 표현이 있으면 {"phrase", "days", "unit"}을 돌려준다(2~14일). 문구는 원문 그대로다."""
    match = _STREAK_PATTERN.search(str(narration or ""))
    if not match:
        return None
    days = int(match.group(1))
    if not STREAK_MIN_DAYS <= days <= STREAK_MAX_DAYS:
        return None
    return {"phrase": f"{match.group(1)}{match.group(2)}", "days": days, "unit": match.group(2)}


def _font(size: int):
    try:
        return ImageFont.truetype(_FONT_BOLD, size)
    except OSError:
        return ImageFont.load_default()


def streak_sprite(count: int, phrase: str, p: float) -> Image.Image:
    """달력 카드가 나오고 날짜 칸(동그라미)마다 X가 차례로 그려진 뒤 일수 문구가 나온다. p는 0~1 진행도."""
    rows = 1 if count <= 7 else 2          # 8일은 4+4처럼 줄을 고르게 나눈다(7+1로 한 칸만 남기지 않는다)
    cols = -(-count // rows)
    cell, pad, header, footer = 170, 60, 84, 170
    width, height = cols * cell + pad * 2, header + rows * cell + pad + footer
    sprite = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    card_bottom = header + rows * cell + pad
    card_alpha = int(255 * min(p / 0.12, 1.0))
    draw.rounded_rectangle((0, 0, width, card_bottom), radius=34, fill=(244, 245, 248, card_alpha))
    draw.rounded_rectangle((0, 0, width, header + 20), radius=34, fill=ACCENT[:3] + (card_alpha,))
    draw.rectangle((0, header - 10, width, header + 20), fill=(244, 245, 248, card_alpha))
    for ring in (width * 0.26, width * 0.74):
        draw.rounded_rectangle((ring - 9, -14, ring + 9, 34), radius=9, fill=(60, 64, 72, card_alpha))
    marked = max(0.0, (p - 0.15) / 0.75) * count      # 0..count: 정수 부분은 완성된 X, 소수 부분은 그리는 중인 X
    for k in range(count):
        cx = pad + (k % cols) * cell + cell // 2
        cy = header + pad // 2 + (k // cols) * cell + cell // 2
        r = 58
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ACCENT[:3] + (card_alpha,), width=8)
        done = min(max(marked - k, 0.0), 1.0)
        if done <= 0:
            continue
        first, second = min(done / 0.5, 1.0), max((done - 0.5) / 0.5, 0.0)
        reach = r * 0.78
        draw.line((cx - reach, cy - reach, cx - reach + 2 * reach * first, cy - reach + 2 * reach * first),
                  fill=(229, 57, 53, 255), width=14)
        if second > 0:
            draw.line((cx + reach, cy - reach, cx + reach - 2 * reach * second, cy - reach + 2 * reach * second),
                      fill=(229, 57, 53, 255), width=14)
    if p > 0.92:
        font = _font(120)
        text_w = int(draw.textlength(phrase, font=font))
        text = Image.new("RGBA", (max(text_w + 40, 1), 170), (0, 0, 0, 0))
        ImageDraw.Draw(text).text((20, 10), phrase, font=font, fill=ACCENT[:3] + (255,))
        alpha = min((p - 0.92) / 0.08, 1.0)
        text.putalpha(text.getchannel("A").point(lambda v, a=alpha: int(v * a)))
        sprite.alpha_composite(text, ((width - text.width) // 2, card_bottom + 20))
    return sprite


def streak_layers(count: int, phrase: str) -> list[Image.Image]:
    """스톡 영상 위에 얹을 층: 달력(동그라미) → X 하나씩 → 일수 문구. 층마다 화면 크기이며 앞 층 위에 쌓인다."""
    base = streak_sprite(count, phrase, 0.15)
    states = [base] + [streak_sprite(count, phrase, 0.15 + 0.75 * k / count) for k in range(1, count + 1)]
    states.append(streak_sprite(count, phrase, 1.0))
    width, height = base.size
    left = (CANVAS[0] - width) // 2
    top = max(min(CENTER_Y - height // 2, SUBTITLE_SAFE_BOTTOM - height), 20)
    layers = []
    for state in states:
        layer = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
        layer.alpha_composite(state, (left, top))
        layers.append(layer)
    return layers
