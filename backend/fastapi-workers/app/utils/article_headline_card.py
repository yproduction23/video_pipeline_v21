"""기사 헤드라인 + 본문 발췌를 함께 보여주는 레퍼런스 채널 스타일 카드.

2026-10-14 사용자 결정: 캡쳐된 기사 문단 스크린샷 전체를 화면에 띄우면
정보가 너무 많다. 그렇다고 헤드라인만 보여주면 내용이 비어 보인다 —
레퍼런스 채널처럼 헤드라인과 기사 본문 발췌를 적절히 섞어 보여주고,
출처는 발행사 이름만(URL·발행일 등 상세 인용 표기 없이) 배지로 짧게
표시한다. 여기서 쓰는 publisher/headline/quote 문자열은 전부
ArticleCapture(실제 캡쳐 결과)에서 그대로 가져온 원문이며, LLM이 새로
생성·재작성하지 않는다 — 길이만 기계적으로(단어 경계에서) 자르고 Pillow로
결정론적으로 렌더링한다(AGENTS.md: 수치·사실 오버레이는 Pillow/FFmpeg로
렌더링).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_FONT_BOLD = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"

_BG_COLOR = (10, 14, 24)
_BADGE_COLOR = (247, 201, 72)
_BADGE_TEXT_COLOR = (20, 20, 20)
_HEADLINE_COLOR = (255, 255, 255)
_QUOTE_COLOR = (200, 212, 230)

# 2026-10-14 job 18 재현: 연합뉴스 기사의 article_capture.quote가
# "재판매 및 DB 금지] goodluck@yna.co.kr ◇ 8명 찌른 칼부림에도..." 같은
# 사진 저작권 고지·기자 이메일로 시작했다 — ArticleBodyResolver의 문장
# 분리가 사진 캡션 영역의 저작권 문구를 본문과 함께 추출한 경우다. 카드에
# 보여줄 때만 이 보일러플레이트를 제거한다(원본 quote 데이터 자체는
# 건드리지 않음 — 다른 경로의 전체 스크린샷 하이라이트는 영향 없음).
_COPYRIGHT_NOTICE_RE = re.compile(r"^.*?(?:재판매|무단\s*전재|재배포|Copyrights?)[^\]]*\]\s*")
_EMAIL_RE = re.compile(r"\S+@\S+\.[A-Za-z]{2,}")


def _clean_quote(text: str) -> str:
    text = _COPYRIGHT_NOTICE_RE.sub("", str(text or ""))
    text = _EMAIL_RE.sub("", text)
    text = text.lstrip("◇ \t")
    return re.sub(r"\s+", " ", text).strip()


def _trim(text: str, limit: int) -> str:
    """단어 경계에서 기계적으로 자른다 — LLM 재작성 없음."""
    text = " ".join(_clean_quote(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip() + "…"


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _wrap_by_glyph(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """한국어는 띄어쓰기 기준 줄바꿈이 어색할 때가 많아 글자 단위로 채운다."""
    lines: list[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def render_headline_card(
    *,
    publisher: str,
    headline: str,
    quote: str = "",
    output_path: str,
    canvas: tuple[int, int] = (1920, 1080),
    max_headline_lines: int = 3,
    max_quote_lines: int = 3,
    quote_char_limit: int = 90,
) -> bool:
    """헤드라인 + 기사 본문 발췌를 함께 보여주고, 출처는 발행사 이름
    배지로만 짧게 표시한다(URL·발행일 등 상세 인용 없음 — 레퍼런스 채널
    스타일)."""
    publisher = str(publisher or "").strip()
    headline = str(headline or "").strip()
    quote = _trim(quote, quote_char_limit)
    if not headline:
        return False
    try:
        width, height = canvas
        image = Image.new("RGB", (width, height), _BG_COLOR)
        draw = ImageDraw.Draw(image)

        badge_font = _font(_FONT_BOLD, 34)
        headline_font = _font(_FONT_BOLD, 58)
        quote_font = _font(_FONT_BOLD, 34)

        left = 120
        max_text_width = width - 2 * left

        headline_lines = _wrap_by_glyph(draw, headline, headline_font, max_text_width)[:max_headline_lines]
        quote_lines = _wrap_by_glyph(draw, quote, quote_font, max_text_width)[:max_quote_lines] if quote else []
        headline_block_h = len(headline_lines) * 78
        quote_block_h = (36 + len(quote_lines) * 50) if quote_lines else 0
        badge_h = 62 if publisher else 0
        total_h = badge_h + (26 if publisher else 0) + headline_block_h + quote_block_h
        cursor_y = max(80, (height - total_h) // 2)

        if publisher:
            pad_x, pad_y = 28, 14
            text_w = draw.textlength(publisher, font=badge_font)
            badge_w = text_w + pad_x * 2
            draw.rounded_rectangle(
                (left, cursor_y, left + badge_w, cursor_y + badge_h),
                radius=badge_h / 2, fill=_BADGE_COLOR,
            )
            draw.text((left + pad_x, cursor_y + pad_y - 4), publisher, font=badge_font, fill=_BADGE_TEXT_COLOR)
            cursor_y += badge_h + 26

        for line in headline_lines:
            draw.text((left, cursor_y), line, font=headline_font, fill=_HEADLINE_COLOR)
            cursor_y += 78

        if quote_lines:
            cursor_y += 36
            for line in quote_lines:
                draw.text((left, cursor_y), line, font=quote_font, fill=_QUOTE_COLOR)
                cursor_y += 50

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return Path(output_path).exists() and Path(output_path).stat().st_size > 2_000
    except Exception:
        logger.exception("헤드라인 카드 렌더링 실패")
        return False
