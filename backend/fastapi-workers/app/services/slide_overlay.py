"""슬라이드형 오버레이 층 — 배경(스톡/Gemini) 위에 인포그래픽·강조 텍스트·기사 카드를 얹는다.

2026-10-15 운영자 결정: 배경은 모든 장면에 스톡 → Gemini로 먼저 깔고, 인포그래픽·
강조 텍스트·기사 카드는 그 위의 오버레이로 얹는다. 배경과 오버레이를 분리해 두면 한
층만 다시 만들 수 있고, 오버레이가 실패해도 장면이 비지 않는다.

어떤 장면에 어떤 오버레이를 넣을지는 고정 개수·간격이 아니라 Claude가 장면 맥락을
보고 정한다(운영자: "상황에 맞게"). 대신 화면에 올라가는 글자·숫자는 결정론적으로
검증한다(AGENTS.md): 모든 텍스트는 그 장면의 승인 내레이션(또는 기사 원문)에 글자 그대로
있어야 하고, 아니면 그 오버레이는 버린다. 렌더링은 Pillow/FFmpeg로 결정론적으로 한다.
"""
from __future__ import annotations

from app.services.slide_streak import STREAK_MAX_DAYS, STREAK_MIN_DAYS, streak_layers
from app.services.slide_timing import duplicates_subtitle

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.services.slide_icons import ACCENT, ICON_HINTS, ICON_NAMES, draw_icon

logger = logging.getLogger(__name__)

CANVAS = (1920, 1080)
_FONT_BOLD = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
_FONT_REGULAR = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
# 레퍼런스의 굵은 빨간 강조 글자와 같은 계열의 디스플레이 서체(Black Han Sans, SIL OFL). assets/fonts에 라이선스와 함께 둔다.
_FONT_DISPLAY = str(Path(__file__).resolve().parents[2] / "assets" / "fonts" / "BlackHanSans-Regular.ttf")
EMPHASIS_RED = (232, 30, 24)

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 고지 문구는 내레이션이 아니라 화면 배너로만 보여준다(운영자 결정 2026-10-15).
DISCLAIMER_BANNER_TEXT = "투자의 최종 판단은 본인에게 있습니다  ·  본 영상은 교육 목적의 콘텐츠입니다"
DISCLAIMER_SECONDS = 8.0  # (이전 상단 띠 방식, 더 이상 쓰지 않는다)
DISCLAIMER_CARD_SECONDS = 1.3
# 오버레이가 아래에서 올라오는 거리(px)와 시간(초).
OVERLAY_RISE_PX = 44
# 레퍼런스: 데이터 카드·목록은 어둡게 눌린 스톡 영상 위에 얹힌다(스톡 장면의 41%).
BASE_DIM_FILTER = ",eq=brightness=-0.14:saturation=0.9"
OVERLAY_RISE_SECONDS = 0.5

_PLAN_SYSTEM_PROMPT = (
    "너는 한국 경제 뉴스 유튜브의 편집 디렉터다. 번호가 붙은 승인 내레이션 목록을 읽고, 각 장면 위에 얹을 "
    "후편집(강조 텍스트·인포그래픽)을 계획한다. 이 채널의 레퍼런스 영상은 거의 모든 문장에 짧은 텍스트나 "
    "그림이 함께 뜨고, 설명 중심 장면은 배경 영상 없이 어두운 칠판 위에 텍스트·아이콘·도식이 가볍게 "
    "나타난다. 정해진 개수는 없지만 시청자가 지루하지 않게 리듬을 만든다:\n"
    "- 주장·원인·수치·핵심 용어가 있는 장면에는 대부분 key_text(핵심 구절) 이상을 붙인다. 아이콘 이름과 "
    "의미가 맞으면 icon도 붙인다(예: 금액→money_stack, 금리→bank, 하락→chart_down).\n"
    "- 연속 3장면 이상 아무 보조 없이 비워 두지 않는다. 같은 종류가 4번 이상 연달아 쌓이지 않게 섞는다.\n"
    "- 구체적으로 촬영할 수 있는 피사체가 없는 추상적 문장(구조·원인·전망·질문·정리)은 실사 배경이 오히려 "
    "어색하다. 그런 장면은 explain:true로 두고 key_text(+아이콘)나 도식으로 칠판 위에서 보여준다.\n"
    "- 실제 장소·사람·사물이 문장의 중심이면 배경 영상을 살리고 key_text를 가볍게 얹는다.\n\n"
    "오버레이 종류(type):\n"
    '- "key_text": 그 구간의 핵심 주장·핵심 수치를 큼직하게 강조. {"text": 내레이션에 글자 그대로 있는 '
    "4~28자 구절 — 자막 문장을 그대로 되풀이하지 말고 핵심어(2~3단어, 16자 안팎)만 뽑는다}\n"
    '- "bullet_list": 내레이션이 2~4개 항목(이유·원인·조건)을 나열할 때. {"title": 18자 이내 제목(숫자 금지), '
    '"items": [내레이션에 글자 그대로 있는 24자 이내 구절 2~4개]}\n'
    '- "stat_chart": 같은 장면에서 둘 이상의 수치를 비교할 때. {"title": 18자 이내(숫자 금지), '
    '"items": [{"label": 대상 이름, "value_text": 내레이션에 적힌 숫자+단위 그대로}] 2~4개}\n'
    '- "equation": 내레이션이 계산 과정(예: 자산 − 대출 = 순자산)을 말할 때. {"label": 8자 이내 소제목(숫자 금지), '
    '"left": [피연산자 구절 2~3개, 내레이션에 글자 그대로], "ops": ["+","−","×","÷" 중 left 개수-1개], '
    '"result": 결과 구절(내레이션에 글자 그대로)}. 계산이 실제로 맞아야 한다.\n'
    '- "big_number": 한 장면의 중심이 숫자 하나일 때 그 숫자를 화면 가득 크게. {"value": 내레이션에 글자 그대로 있는 숫자 구절 16자 이내, "label": 8자 이내 소제목(없어도 됨, 숫자 금지)}\n'
    '- "compare_numbers": 같은 대상의 두 시점·두 조건 수치를 순서대로 보여줄 때. {"headline": 내레이션에 글자 그대로 있는 12자 이내 대상 구절, "rows": [{"label": 6자 이내(숫자 금지), "value": 내레이션에 글자 그대로 있는 숫자 구절}] 2~3개}\n'
    '- "flow": 개념이 A에서 B로 이어지는 흐름(원인→결과, 자산→현금)을 말할 때. {"nodes": [{"label": 8자 이내(숫자 금지), "icon": 아이콘 이름}] 2~3개}\n'
    '- "streak": 내레이션이 "N일(거래일·주·개월) 연속"을 말할 때 — 달력에 날짜 칸 N개가 나오고 X가 차례로 쳐지는 그림. '
    '{"count": 내레이션에 글자 그대로 있는 구절(예: "8거래일"), 2~14}. 이런 표현이 있으면 key_text 대신 반드시 쓴다.\n'
    '- "article_card": 장면 정보에 article이 있고, 그 보도 인용이 신뢰를 더해주는 장면에서만. 추가 필드 없음.\n\n'
    "공통 선택 필드:\n"
    '- "explain": true/false — 이 장면은 배경 영상보다 설명(인포그래픽) 자체가 중심이다. true면 스톡/AI 배경을 '
    "쓰지 않고 어두운 칠판 배경 위에 인포그래픽을 크게 보여준다(배경 제작 비용도 들지 않는다). equation·"
    "compare_numbers·flow·big_number·stat_chart·bullet_list는 화면 대부분을 인포그래픽이 덮으므로 생략하면 "
    "true로 처리한다. 현장 영상이 의미를 더하는 드문 경우에만 false로 명시한다. key_text는 영상 위에 얹는 "
    "강조라서 기본 false다.\n"
    '- "icon": key_text나 equation에 곁들일 그림 이름. 키워드와 의미가 맞을 때만. 가능한 값: '
    + ", ".join(f"{name}({ICON_HINTS[name]})" for name in ICON_NAMES) + "\n\n"
    "절대 규칙: 모든 text·items·value_text·left·result는 그 장면 내레이션에 적힌 글자를 그대로 옮긴다. 숫자를 "
    "계산·반올림·추정·새로 만들지 않는다. 확신이 없으면 그 장면은 고르지 않는다.\n"
    '출력은 JSON만: {"overlays":[{"index":번호,"type":"...", ...}]}'
)


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 4000)
    import os

    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _in_narration(piece: str, narration: str) -> bool:
    piece = _compact(piece)
    return bool(piece) and piece in _compact(narration)


def _parse_value(value_text: str) -> tuple[float, str] | None:
    match = _NUMBER_RE.search(str(value_text or ""))
    if not match:
        return None
    try:
        number = float(match.group(0).replace(",", ""))
    except ValueError:
        return None
    unit = str(value_text)[match.end():].strip()
    return number, unit


def _title_is_grounded(title: str, narration: str) -> bool:
    narration_digits = set(_NUMBER_RE.findall(narration))
    return bool(title) and all(d in narration_digits for d in _NUMBER_RE.findall(title))


_DIAGRAM_KINDS = {"equation", "compare_numbers", "flow", "big_number", "stat_chart", "bullet_list", "streak"}
_OPERATORS = {"+", "−", "×", "÷"}
_OP_ALIASES = {"-": "−", "–": "−", "x": "×", "X": "×", "*": "×", "/": "÷"}


def _validate_equation(entry: dict, narration: str) -> dict | None:
    """계산식은 피연산자·결과가 모두 내레이션 원문에 있어야 하고, 단위가 같은 숫자끼리면
    실제 계산 결과가 맞아야 한다(틀린 식을 화면에 올리지 않는다)."""
    left = [str(t).strip() for t in (entry.get("left") or []) if str(t).strip()][:3]
    result = str(entry.get("result") or "").strip()
    ops = [_OP_ALIASES.get(str(o).strip(), str(o).strip()) for o in (entry.get("ops") or [])]
    label = str(entry.get("label") or "").strip()[:8]
    if len(left) < 2 or not result or len(ops) != len(left) - 1 or any(o not in _OPERATORS for o in ops):
        return None
    if not all(_in_narration(t, narration) for t in [*left, result]):
        return None
    if label and not _title_is_grounded(label, narration):
        return None
    parsed = [_parse_value(t) for t in [*left, result]]
    if all(parsed):
        units = {u for _, u in parsed}
        if len(units) == 1:
            value = parsed[0][0]
            for op, (number, _) in zip(ops, parsed[1:-1]):
                value = {"+": value + number, "−": value - number, "×": value * number,
                         "÷": value / number if number else float("nan")}[op]
            if abs(value - parsed[-1][0]) > 1e-6 * max(1.0, abs(value)):
                return None
    return {"label": label, "left": left, "ops": ops, "result": result}


KEY_TEXT_ON_FOOTAGE_MAX = 14


def spell_like_narration(value, narration: str):
    """내레이션 표기("5.3퍼센트" 또는 "5.3%")에 Claude가 옮긴 퍼센트 표기만 맞춘다(값은 바꾸지 않는다)."""
    if "퍼센트" in narration and "%" not in narration:
        old, new = "%", "퍼센트"
    elif "%" in narration and "퍼센트" not in narration:
        old, new = "퍼센트", "%"
    else:
        return value
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [spell_like_narration(item, narration) for item in value]
    if isinstance(value, dict):
        return {key: spell_like_narration(item, narration) for key, item in value.items()}
    return value


_PERCENT_POINT_RE = re.compile(r"(\d)\s*퍼센트포인트")
_PERCENT_RE = re.compile(r"(\d)\s*퍼센트")


def to_percent_symbol(value):
    """화면 표시용: 숫자 뒤의 "퍼센트"를 "%"로 쓴다(2026-10-16 운영자 요청, 레퍼런스도 "-14.4%").
    검증이 끝난 뒤에만 적용하므로 원문 일치 검사에는 영향이 없다."""
    if isinstance(value, str):
        return _PERCENT_RE.sub(lambda m: f"{m.group(1)}%", _PERCENT_POINT_RE.sub(lambda m: f"{m.group(1)}%p", value))
    if isinstance(value, list):
        return [to_percent_symbol(item) for item in value]
    if isinstance(value, dict):
        return {key: to_percent_symbol(item) for key, item in value.items()}
    return value


def validate_overlay_plan(raw_overlays: list, briefs: dict[int, dict]) -> dict[int, dict]:
    """Claude 계획을 결정론적으로 검증한다. 통과 못 한 오버레이는 버리고 그 장면은 배경만 쓴다.
    briefs[index] = {"narration": str, "has_article": bool, "has_numeric_series": bool}"""
    plan: dict[int, dict] = {}
    for entry in raw_overlays or []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        brief = briefs.get(index)
        if not brief or index in plan:
            continue
        narration = str(brief.get("narration") or "")
        # 수치·계산 오버레이는 직전 두 장면에서 이미 말한 수치도 근거로 허용한다(원문 글자 일치는 그대로 요구).
        numeric_ground = f"{brief.get('context') or ''} {narration}".strip()
        entry = spell_like_narration(entry, numeric_ground)
        kind = str(entry.get("type") or "").strip()
        if kind in _DIAGRAM_KINDS and entry.get("explain") is None:
            # 화면을 인포그래픽이 덮는 종류는 배경을 새로 만들 이유가 없다(명시적 false만 예외).
            explain = True
        else:
            explain = entry.get("explain") is True and kind != "article_card"
        icon = str(entry.get("icon") or "").strip()
        icon = icon if icon in ICON_NAMES else None

        if kind == "key_text":
            text = str(entry.get("text") or "").strip()
            # 영상 위 문구는 자막을 되풀이하지 않도록 14자 이내 핵심어만(2026-10-16), 칠판 위 문구는 28자까지.
            if 4 <= len(text) <= (KEY_TEXT_ON_FOOTAGE_MAX if entry.get("on_footage") else 28)                     and _in_narration(text, narration) and not duplicates_subtitle(text, brief.get("chunks") or []):
                plan[index] = {"type": "key_text", "text": text, "icon": icon, "explain": explain}
        elif kind == "bullet_list":
            title = str(entry.get("title") or "").strip()[:18]
            items = [str(i).strip() for i in (entry.get("items") or []) if str(i).strip()]
            items = [i for i in items if len(i) <= 24 and _in_narration(i, narration)][:4]
            if len(items) >= 2 and _title_is_grounded(title, narration):
                plan[index] = {"type": "bullet_list", "title": title, "items": items, "explain": explain}
        elif kind == "stat_chart":
            title = str(entry.get("title") or "").strip()[:24]
            items = []
            for item in (entry.get("items") or [])[:4]:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "").strip()
                value_text = str(item.get("value_text") or "").strip()
                parsed = _parse_value(value_text)
                if label and parsed and _in_narration(value_text, numeric_ground):
                    items.append({"label": label, "value_text": value_text, "value": parsed[0], "unit": parsed[1]})
            if len(items) >= 2 and _title_is_grounded(title, numeric_ground):
                plan[index] = {"type": "stat_chart", "title": title, "items": items, "explain": explain}
            elif brief.get("has_numeric_series"):
                plan[index] = {"type": "stat_chart", "title": title, "items": [], "use_article_series": True,
                               "explain": explain}
        elif kind == "big_number":
            value = str(entry.get("value") or "").strip()
            label = str(entry.get("label") or "").strip()[:8]
            if 1 <= len(value) <= 16 and _in_narration(value, numeric_ground) and re.search(r"\d", value):
                if not label or _title_is_grounded(label, numeric_ground):
                    plan[index] = {"type": "big_number", "value": value, "label": label,
                                   "icon": icon, "explain": explain}
        elif kind == "compare_numbers":
            headline = str(entry.get("headline") or "").strip()
            rows = []
            for row in (entry.get("rows") or [])[:3]:
                if not isinstance(row, dict):
                    continue
                label, value = str(row.get("label") or "").strip()[:6], str(row.get("value") or "").strip()
                if label and value and _title_is_grounded(label, numeric_ground) \
                        and _in_narration(value, numeric_ground) and re.search(r"\d", value):
                    rows.append({"label": label, "value": value})
            if 1 <= len(headline) <= 12 and _in_narration(headline, numeric_ground) and len(rows) >= 2:
                plan[index] = {"type": "compare_numbers", "headline": headline, "rows": rows,
                               "icon": icon, "explain": explain}
        elif kind == "flow":
            nodes = []
            for node in (entry.get("nodes") or [])[:3]:
                if not isinstance(node, dict):
                    continue
                label = str(node.get("label") or "").strip()[:8]
                node_icon = str(node.get("icon") or "").strip()
                if label and _title_is_grounded(label, narration):
                    nodes.append({"label": label, "icon": node_icon if node_icon in ICON_NAMES else None})
            if len(nodes) >= 2:
                plan[index] = {"type": "flow", "nodes": nodes, "explain": explain}
        elif kind == "streak":
            # 연속 일수 달력: 일수 문구는 내레이션에 글자 그대로 있어야 하고 2~14일만 그린다.
            phrase = str(entry.get("count") or "").strip()
            days = re.search(r"\d+", phrase)
            if phrase and days and _in_narration(phrase, narration) and STREAK_MIN_DAYS <= int(days.group()) <= STREAK_MAX_DAYS:
                title = str(entry.get("title") or "").strip()[:12]
                plan[index] = {"type": "streak", "count": phrase, "days": int(days.group()),
                               "title": title if title and _title_is_grounded(title, narration) else "", "explain": explain}
        elif kind == "equation":
            equation = _validate_equation(entry, numeric_ground)
            if equation:
                plan[index] = {"type": "equation", **equation, "icon": icon, "explain": explain}
        elif kind == "article_card" and brief.get("has_article"):
            plan[index] = {"type": "article_card"}
    return {index: to_percent_symbol(value) for index, value in plan.items()}


def plan_slide_overlays(briefs: dict[int, dict], *, llm_call=None) -> dict[int, dict] | None:
    """장면 번호 → 오버레이 계획. Claude 호출/파싱이 실패하면 None을 돌려줘 호출자가
    "계획 실패"를 조용히 넘기지 않고 검수 사유로 남길 수 있게 한다({} 는 "넣을 곳 없음")."""
    if not briefs:
        return {}
    lines = []
    for index in sorted(briefs):
        brief = briefs[index]
        article = ""
        if brief.get("has_article"):
            article = f" [article: {brief.get('article_brief', '')}{' / 수치계열 있음' if brief.get('has_numeric_series') else ''}]"
        lines.append(f"{index}: {brief.get('narration', '')}{article}")
    prompt = "승인 내레이션 목록(번호: 문장):\n" + "\n".join(lines) + "\n\nJSON만 반환:"
    try:
        raw = _call_llm(_PLAN_SYSTEM_PROMPT, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        return validate_overlay_plan(data.get("overlays") or [], briefs)
    except Exception as exc:
        logger.warning("오버레이 계획 생성 실패: %s", exc)
        return None


def _font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _save(image: Image.Image, output_path: str) -> bool:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return Path(output_path).stat().st_size > 2_000


def add_source_credit(png_path: str, publisher: str) -> bool:
    """오버레이 PNG 왼쪽 아래에 "자료: 매체" 표기를 그린다(레퍼런스 방식). 출처가 비어 있으면 그리지 않는다."""
    publisher = str(publisher or "").strip()
    if not publisher:
        return False
    try:
        with Image.open(png_path) as source:
            image = source.convert("RGBA")
        draw = ImageDraw.Draw(image)
        font = _font(_FONT_REGULAR, 30)
        text = f"자료: {publisher}"[:40]
        x, y = 56, image.height - 150
        draw.rectangle((x, y, x + 4, y + 36), fill=(255, 255, 255, 235))
        draw.text((x + 18 + 2, y + 2), text, font=font, fill=(0, 0, 0, 160))
        draw.text((x + 18, y), text, font=font, fill=(255, 255, 255, 240))
        image.save(png_path)
        return True
    except Exception:
        logger.warning("자료 출처 표기 실패: %s", png_path, exc_info=True)
        return False


def render_stat_overlay(*, title: str, items: list[dict], output_path: str, canvas: tuple[int, int] = CANVAS) -> bool:
    """어두운 스크림 위에 제목과 노란 막대 비교 그래프를 그린 전체 화면 RGBA PNG."""
    if not title or len(items) < 2:
        return False
    try:
        width, height = canvas
        image = Image.new("RGBA", canvas, (0, 0, 0, 150))
        draw = ImageDraw.Draw(image)
        title_font = _font(_FONT_BOLD, 64)
        value_font = _font(_FONT_BOLD, 56)
        label_font = _font(_FONT_REGULAR, 40)

        tw = draw.textlength(title, font=title_font)
        draw.text(((width - tw) / 2, int(height * 0.10)), title, font=title_font, fill=(255, 255, 255, 255))

        count = len(items)
        top = max(float(it["value"]) for it in items) or 1.0
        chart_top, baseline = int(height * 0.32), int(height * 0.80)
        slot = int(width * 0.62 / count)
        bar_w = min(int(slot * 0.55), 220)
        left = int((width - slot * count) / 2)
        for i, item in enumerate(items):
            bar_h = max(int((baseline - chart_top) * float(item["value"]) / top), 12)
            x0 = left + i * slot + (slot - bar_w) // 2
            draw.rounded_rectangle((x0, baseline - bar_h, x0 + bar_w, baseline), radius=6, fill=ACCENT)
            value_text = str(item["value_text"])
            vw = draw.textlength(value_text, font=value_font)
            draw.text((x0 + (bar_w - vw) / 2, baseline - bar_h - 74), value_text, font=value_font, fill=ACCENT)
            label = str(item["label"])
            lw = draw.textlength(label, font=label_font)
            draw.text((x0 + (bar_w - lw) / 2, baseline + 18), label, font=label_font, fill=(235, 240, 248, 255))
        draw.line((left, baseline, left + slot * count, baseline), fill=(235, 240, 248, 200), width=3)
        return _save(image, output_path)
    except Exception:
        logger.exception("통계 오버레이 렌더링 실패")
        return False


def _greedy_wrap(draw: ImageDraw.ImageDraw, paragraph: str, font, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in paragraph.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
        # 한 단어가 폭보다 길 때만 글자 단위로 쪼갠다.
        while draw.textlength(current, font=font) > max_width and len(current) > 1:
            cut = len(current) - 1
            while cut > 1 and draw.textlength(current[:cut], font=font) > max_width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    return lines


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    """단어(띄어쓰기) 단위로 줄바꿈하고 줄 길이를 고르게 맞춘다. max_lines를 넘으면 마지막 줄을 말줄임표로 끝낸다.

    2026-10-16: 글자 단위 줄바꿈이 "겁니/다"처럼 단어 중간을 잘라 한 글자 줄을 만들었다."""
    lines: list[str] = []
    for paragraph in str(text or "").strip().splitlines() or [""]:
        wrapped = _greedy_wrap(draw, paragraph, font, max_width)
        if len(wrapped) > 1:
            # 같은 줄 수를 유지하는 가장 좁은 폭으로 다시 감아 마지막 줄이 짧게 남는 것을 막는다.
            low, high = 1.0, float(max_width)
            for _ in range(14):
                mid = (low + high) / 2
                if len(_greedy_wrap(draw, paragraph, font, mid)) <= len(wrapped):
                    high = mid
                else:
                    low = mid
            wrapped = _greedy_wrap(draw, paragraph, font, high)
        lines.extend(wrapped)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.…")[: max(1, len(lines[-1]) - 1)] + "…"
    return lines


def render_news_clip_overlay(
    *, publisher: str, headline: str, quote: str, output_path: str, canvas: tuple[int, int] = CANVAS,
) -> bool:
    """기사 인용을 화면 정중앙에 얹는 컴팩트한 "신문 스크랩" 패널(전체 화면 RGBA PNG).

    2026-10-15 사용자 평가: 화면 대부분을 덮는 어두운 빈 박스는 "AI가 만든 티가 난다" →
    내용 크기에 딱 맞는 밝은 종이 패널로 줄이고, 정중앙에 둔다(운영자 지시). 본문은
    기사 원문 인용을 그대로(말줄임만) 쓴다.
    """
    headline = str(headline or "").strip()
    quote = str(quote or "").strip()
    if not headline and not quote:
        return False
    try:
        width, height = canvas
        overlay = Image.new("RGBA", canvas, (0, 0, 0, 90))
        draw = ImageDraw.Draw(overlay)
        panel_w = int(width * 0.54)
        pad = 40
        text_w = panel_w - pad * 2
        mast_font = _font(_FONT_BOLD, 30)
        head_font = _font(_FONT_BOLD, 48)
        quote_font = _font(_FONT_REGULAR, 34)

        # 2026-10-16 운영자 피드백("기사 내용 반복된다"): 인용문은 내레이션과 같은 말이라 카드에서 빼고, 제목이 있으면 제목만 보여 준다.
        if not headline:
            headline, quote = quote, ""
        head_lines = _wrap_text(draw, headline, head_font, text_w, 3)
        quote_lines = []
        mast_h, head_lh, quote_lh = 56, 62, 50
        panel_h = pad + mast_h + 20 + len(head_lines) * head_lh + (16 if quote_lines else 0) + len(quote_lines) * quote_lh + pad
        x0, y0 = (width - panel_w) // 2, (height - panel_h) // 2

        shadow = Image.new("RGBA", canvas, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            (x0 + 10, y0 + 14, x0 + panel_w + 10, y0 + panel_h + 14), radius=14, fill=(0, 0, 0, 120),
        )
        overlay.alpha_composite(shadow)
        draw = ImageDraw.Draw(overlay)
        draw.rounded_rectangle((x0, y0, x0 + panel_w, y0 + panel_h), radius=14, fill=(247, 246, 242, 255))
        draw.rectangle((x0, y0, x0 + panel_w, y0 + 10), fill=ACCENT)
        y = y0 + pad
        draw.text((x0 + pad, y), str(publisher or "기사"), font=mast_font, fill=(30, 36, 52, 255))
        y += mast_h
        draw.line((x0 + pad, y, x0 + panel_w - pad, y), fill=(190, 188, 180, 255), width=2)
        y += 20
        for line in head_lines:
            draw.text((x0 + pad, y), line, font=head_font, fill=(18, 22, 38, 255))
            y += head_lh
        y += 16
        if quote_lines:
            draw.rectangle((x0 + pad, y + 4, x0 + pad + 6, y + len(quote_lines) * quote_lh - 6), fill=ACCENT)
        for line in quote_lines:
            draw.text((x0 + pad + 28, y), line, font=quote_font, fill=(70, 74, 86, 255))
            y += quote_lh
        return _save(overlay, output_path)
    except Exception:
        logger.exception("뉴스 클립 오버레이 렌더링 실패")
        return False


def render_key_text_overlay(
    *, text: str, output_path: str, icon: str | None = None, style: str = "footage",
    canvas: tuple[int, int] = CANVAS,
) -> bool:
    """핵심 구절을 화면 중앙에 크게 강조하는 오버레이(강조 텍스트)."""
    text = str(text or "").strip()
    if not text:
        return False
    try:
        width, height = canvas
        overlay = Image.new("RGBA", canvas, (0, 0, 0, 120))
        draw = ImageDraw.Draw(overlay)
        board_style = style == "board"
        font = _font(_FONT_DISPLAY, 150) if board_style else _font(_FONT_BOLD, 96)
        lines = _wrap_text(draw, text, font, int(width * (0.82 if board_style else 0.74)), 2)
        line_h = 176 if board_style else 124
        block_h = len(lines) * line_h
        icon_size = 260 if icon else 0
        y = (height - block_h - icon_size) // 2 + icon_size
        if icon:
            draw_icon(draw, icon, width // 2, y - icon_size // 2 - 20, icon_size)
        for line in lines:
            tw = draw.textlength(line, font=font)
            x = (width - tw) / 2
            # 2026-10-16 운영자 피드백("칠판 위에 텍스트만 가볍게"): 어두운 글자가 노란 띠 밖으로 삐져나와
            # 읽히지 않던 형광펜 띠를 없애고, 흰 분필 글자 + 얇은 강조색 밑줄로 바꾼다.
            left, top, right, bottom = draw.textbbox((x, y), line, font=font)
            if style == "board":
                # 레퍼런스의 어두운 배경 문구 장면: 굵은 붉은 글자(밑줄 없음).
                draw.text((x, y), line, font=font, fill=EMPHASIS_RED + (255,))
            else:
                draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 150))
                draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
                draw.rectangle((left, bottom + 12, right, bottom + 20), fill=ACCENT)
            y += line_h
        return _save(overlay, output_path)
    except Exception:
        logger.exception("강조 텍스트 오버레이 렌더링 실패")
        return False


_OPERATOR_SHAPES = {"+", "−", "×", "÷", "="}


def _draw_operator(draw: ImageDraw.ImageDraw, op: str, cx: float, cy: float, size: float, fill) -> None:
    """연산 기호(+ − × ÷ =)를 선분·점으로 그린다. 중심 (cx, cy), 폭 size."""
    half = size / 2
    stroke = max(6, int(size * 0.17))
    if op in {"+", "−", "÷", "="}:
        gap = size * 0.28
        if op == "=":
            draw.line((cx - half, cy - gap, cx + half, cy - gap), fill=fill, width=stroke)
            draw.line((cx - half, cy + gap, cx + half, cy + gap), fill=fill, width=stroke)
        else:
            draw.line((cx - half, cy, cx + half, cy), fill=fill, width=stroke)
    if op == "+":
        draw.line((cx, cy - half, cx, cy + half), fill=fill, width=stroke)
    elif op == "×":
        d = half * 0.8
        draw.line((cx - d, cy - d, cx + d, cy + d), fill=fill, width=stroke)
        draw.line((cx - d, cy + d, cx + d, cy - d), fill=fill, width=stroke)
    elif op == "÷":
        r = stroke * 0.8
        for dy in (-size * 0.34, size * 0.34):
            draw.ellipse((cx - r, cy + dy - r, cx + r, cy + dy + r), fill=fill)


def render_equation_overlay(
    *, label: str, left: list[str], ops: list[str], result: str, output_path: str,
    icon: str | None = None, canvas: tuple[int, int] = CANVAS,
) -> bool:
    """"A − B = C" 계산식. 피연산자는 흰색, 결과만 강조색 — 레퍼런스 영상의 설명 방식."""
    if len(left) < 2 or len(ops) != len(left) - 1 or not result:
        return False
    try:
        width, height = canvas
        overlay = Image.new("RGBA", canvas, (0, 0, 0, 110))
        draw = ImageDraw.Draw(overlay)
        tokens: list[tuple[str, tuple]] = []
        for i, term in enumerate(left):
            tokens.append((term, (255, 255, 255, 255)))
            if i < len(ops):
                tokens.append((ops[i], ACCENT))
        tokens.append(("=", ACCENT))
        tokens.append((result, ACCENT))
        size = 92
        gap = 34

        def token_width(text: str, font, font_size: int) -> float:
            # 연산 기호는 글꼴 글리프가 없을 수 있어 도형으로 직접 그린다(폭 고정).
            return font_size * 0.62 if text in _OPERATOR_SHAPES else draw.textlength(text, font=font)

        while size > 44:
            font = _font(_FONT_BOLD, size)
            total = sum(token_width(t, font, size) for t, _ in tokens) + gap * (len(tokens) - 1)
            if total <= width * 0.88:
                break
            size -= 6
        font = _font(_FONT_BOLD, size)
        total = sum(token_width(t, font, size) for t, _ in tokens) + gap * (len(tokens) - 1)
        y = int(height * 0.48)
        if icon:
            draw_icon(draw, icon, width // 2, y - 190, 210)
        if label:
            lw = draw.textlength(label, font=_font(_FONT_BOLD, 44))
            draw.text(((width - lw) / 2, y - 92), label, font=_font(_FONT_BOLD, 44), fill=ACCENT)
        x = (width - total) / 2
        for text, color in tokens:
            tw = token_width(text, font, size)
            if text in _OPERATOR_SHAPES:
                # 숫자 글자 높이의 중앙선에 맞춘다.
                _draw_operator(draw, text, x + tw / 2, y + size * 0.62, size * 0.62, color)
            else:
                draw.text((x, y), text, font=font, fill=color)
            x += tw + gap
        return _save(overlay, output_path)
    except Exception:
        logger.exception("계산식 오버레이 렌더링 실패")
        return False


def ensure_board_background(output_path: str, canvas: tuple[int, int] = CANVAS) -> bool:
    """설명 장면용 칠판 배경(결정론적 Pillow 렌더, 외부 호출 없음). 이미 있으면 재사용.

    레퍼런스 분석(2026-10-16): 문구·숫자 장면의 배경은 어두운 회색(charcoal) 배경이다(초록 칠판은 챕터 카드 2%).
    짙은 회색에 흐린 분필 가루·가장자리 그림자만 얹는다(글자는 오버레이가 따로 그린다)."""
    path = Path(output_path)
    if path.is_file() and path.stat().st_size > 2_000:
        return True
    try:
        import random

        width, height = canvas
        rng = random.Random(20261016)
        # 2026-10-16 20번 Job 재현: 픽셀 단위 잡음(effect_noise)을 섞은 칠판은 정지 이미지인데도 x264가
        # 장면 하나당 수 분씩 걸려(3초 클립이 300초 초과) 71장면 생성이 1시간 넘게 걸렸다. 질감은
        # 흐린 분필 얼룩만으로 내고 고주파 잡음은 넣지 않는다(인코딩 비용이 단색 이미지와 같다).
        board = Image.new("RGB", canvas, (46, 47, 50))

        # 분필 가루·지운 자국: 크고 흐린 흰 얼룩을 여러 번 겹친다.
        dust = Image.new("L", canvas, 0)
        draw = ImageDraw.Draw(dust)
        for _ in range(26):
            cx, cy = rng.randint(-100, width + 100), rng.randint(-60, height + 60)
            rx, ry = rng.randint(180, 520), rng.randint(40, 170)
            draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=rng.randint(10, 26))
        for _ in range(7):  # 지우개로 쓸어 낸 넓은 호
            x0, y0 = rng.randint(0, width), rng.randint(0, height)
            draw.arc((x0 - 420, y0 - 180, x0 + 420, y0 + 180), rng.randint(0, 180), rng.randint(200, 360),
                     fill=rng.randint(18, 34), width=rng.randint(34, 70))
        dust = dust.filter(ImageFilter.GaussianBlur(46))
        chalk = Image.new("RGB", canvas, (200, 200, 205))
        board = Image.composite(chalk, board, dust.point(lambda v: min(255, int(v * 0.9))))

        # 가장자리 어둡게(조명 비네팅) + 아래쪽 분필 받침 그림자
        vignette = Image.new("L", canvas, 0)
        ImageDraw.Draw(vignette).ellipse((-width * 0.20, -height * 0.30, width * 1.20, height * 1.30), fill=255)
        vignette = vignette.filter(ImageFilter.GaussianBlur(170))
        board = Image.composite(board, Image.new("RGB", canvas, (12, 12, 14)), vignette)
        
        path.parent.mkdir(parents=True, exist_ok=True)
        board.save(path)
        return path.stat().st_size > 2_000
    except Exception:
        logger.exception("칠판 배경 생성 실패")
        return False


def _layer_path(output_path: str, k: int) -> str:
    base = Path(output_path)
    return str(base.with_name(f"{base.stem}_l{k}{base.suffix}"))


def render_big_number_overlay(
    *, value: str, label: str, output_path: str, icon: str | None = None, canvas: tuple[int, int] = CANVAS,
) -> list[str] | None:
    """숫자 하나를 화면 가득 크게(레퍼런스의 "8,343"). 레이어 목록을 돌려준다."""
    value = str(value or "").strip()
    if not value:
        return None
    try:
        width, height = canvas
        overlay = Image.new("RGBA", canvas, (0, 0, 0, 120))
        draw = ImageDraw.Draw(overlay)
        size = 230
        while size > 90:
            font = _font(_FONT_BOLD, size)
            if draw.textlength(value, font=font) <= width * 0.86:
                break
            size -= 12
        font = _font(_FONT_BOLD, size)
        tw = draw.textlength(value, font=font)
        y = int(height * 0.40)
        if icon:
            draw_icon(draw, icon, width // 2, y - 170, 190)
        if label:
            label_font = _font(_FONT_BOLD, 54)
            lw = draw.textlength(label, font=label_font)
            draw.text(((width - lw) / 2, y - 80 if not icon else y - 60), label, font=label_font, fill=ACCENT)
        draw.text(((width - tw) / 2, y), value, font=font, fill=(255, 255, 255, 255))
        return [output_path] if _save(overlay, output_path) else None
    except Exception:
        logger.exception("큰 숫자 오버레이 렌더링 실패")
        return None


def render_compare_numbers_layers(
    *, headline: str, rows: list[dict], output_path: str, icon: str | None = None,
    canvas: tuple[int, int] = CANVAS,
) -> list[str] | None:
    """대상 하나(헤드라인+아이콘)를 먼저 보이고, 비교 행이 내레이션 순서대로 하나씩 나타난다."""
    if not headline or len(rows) < 2:
        return None
    try:
        width, height = canvas
        head_font = _font(_FONT_BOLD, 96)
        label_font = _font(_FONT_BOLD, 46)
        value_font = _font(_FONT_BOLD, 120)
        colors = [(110, 214, 255, 255), ACCENT, (255, 255, 255, 255)]

        layer0 = Image.new("RGBA", canvas, (0, 0, 0, 120))
        d0 = ImageDraw.Draw(layer0)
        row_x = int(width * 0.60)
        left = int(width * 0.08)
        column_w = row_x - left - 80  # 헤드라인이 오른쪽 비교 행과 겹치지 않는 폭
        head_size = 96
        while head_size > 48 and d0.textlength(headline, font=_font(_FONT_BOLD, head_size)) > column_w:
            head_size -= 6
        head_font = _font(_FONT_BOLD, head_size)
        head_w = d0.textlength(headline, font=head_font)
        center_x = left + column_w / 2
        if icon:
            draw_icon(d0, icon, int(center_x), int(height * 0.40), 280)
            head_y = int(height * 0.40) + 190
        else:
            head_y = int(height * 0.46)
        d0.text((center_x - head_w / 2, head_y), headline, font=head_font, fill=(255, 255, 255, 255))
        paths = [output_path]
        if not _save(layer0, output_path):
            return None
        for k, row in enumerate(rows):
            layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            y = int(height * 0.24) + k * 190
            color = colors[k % len(colors)]
            d.text((row_x, y), str(row["label"]), font=label_font, fill=color)
            d.text((row_x, y + 52), str(row["value"]), font=value_font, fill=color)
            path = _layer_path(output_path, k + 1)
            if not _save(layer, path):
                return None
            paths.append(path)
        return paths
    except Exception:
        logger.exception("비교 수치 오버레이 렌더링 실패")
        return None


def render_streak_layers(*, count: int, phrase: str, output_path: str) -> list[str] | None:
    """연속 일수 달력 층(달력 → X 하나씩 → 일수 문구). 첫 층은 output_path, 나머지는 _layer_path 이름으로 저장한다."""
    try:
        paths: list[str] = []
        for k, layer in enumerate(streak_layers(count, phrase)):
            path = output_path if k == 0 else _layer_path(output_path, k)
            if not _save(layer, path):
                return None
            paths.append(path)
        return paths
    except Exception:
        logger.exception("연속 일수 달력 렌더링 실패")
        return None


def render_flow_layers(
    *, nodes: list[dict], output_path: str, canvas: tuple[int, int] = CANVAS,
) -> list[str] | None:
    """A → B(→ C) 흐름. 노드가 왼쪽부터 하나씩 나타나고 사이에 화살표가 붙는다."""
    if len(nodes) < 2:
        return None
    try:
        width, height = canvas
        label_font = _font(_FONT_BOLD, 56)
        count = len(nodes)
        slot = int(width * 0.74 / count)
        left = int((width - slot * count) / 2) + slot // 2
        cy = int(height * 0.46)
        paths: list[str] = []
        for k, node in enumerate(nodes):
            layer = Image.new("RGBA", canvas, (0, 0, 0, 120 if k == 0 else 0))
            d = ImageDraw.Draw(layer)
            cx = left + k * slot
            if k > 0:
                ax = cx - slot // 2
                d.line((ax - 70, cy, ax + 50, cy), fill=ACCENT, width=14)
                d.polygon([(ax + 30, cy - 34), (ax + 86, cy), (ax + 30, cy + 34)], fill=ACCENT)
            if node.get("icon"):
                draw_icon(d, node["icon"], cx, cy, 300)
            lw = d.textlength(str(node["label"]), font=label_font)
            d.text((cx - lw / 2, cy + 190), str(node["label"]), font=label_font, fill=(255, 255, 255, 255))
            path = output_path if k == 0 else _layer_path(output_path, k)
            if not _save(layer, path):
                return None
            paths.append(path)
        return paths
    except Exception:
        logger.exception("흐름 오버레이 렌더링 실패")
        return None


def render_bullet_overlay(*, title: str, items: list[str], output_path: str, canvas: tuple[int, int] = CANVAS) -> bool:
    """2~4개 항목을 레퍼런스 목록 카드처럼 보여 준다: 왼쪽 정렬, 노란 태그, 굵은 흰 제목, 구분선, 노란 번호 항목."""
    items = [str(i).strip() for i in items if str(i).strip()][:4]
    if len(items) < 2:
        return False
    try:
        width, height = canvas
        overlay = Image.new("RGBA", canvas, (0, 0, 0, 150))
        draw = ImageDraw.Draw(overlay)
        tag_font, title_font = _font(_FONT_BOLD, 44), _font(_FONT_BOLD, 88)
        number_font, item_font = _font(_FONT_BOLD, 84), _font(_FONT_BOLD, 80)
        row_h = 125
        block_h = 240 + len(items) * row_h
        left = 330
        y = (height - block_h) // 2
        draw.text((left, y), "■ 정리", font=tag_font, fill=ACCENT)
        if title:
            draw.text((left, y + 56), str(title), font=title_font, fill=(255, 255, 255, 255))
        draw.rectangle((left, y + 168, left + 1260, y + 172), fill=(235, 236, 240, 230))
        y += 220
        for number, item in enumerate(items, start=1):
            draw.text((left, y), str(number), font=number_font, fill=ACCENT)
            draw.text((left + 120, y + 2), item, font=item_font, fill=(255, 255, 255, 255))
            if number < len(items):
                draw.rectangle((left, y + row_h - 14, left + 1260, y + row_h - 12), fill=(255, 255, 255, 50))
            y += row_h
        return _save(overlay, output_path)
    except Exception:
        logger.exception("목록 오버레이 렌더링 실패")
        return False


DISCLAIMER_CARD_LINES = ("투자의 최종 판단은 본인에게 있습니다", "본 영상은 교육 목적의 콘텐츠입니다")


def render_disclaimer_card(*, output_path: str, canvas: tuple[int, int] = CANVAS) -> bool:
    """투자 판단·교육 목적 고지를 화면 가운데에 보여 주는 전체 화면 카드(불투명). 내레이션이 아니라 화면 문구다."""
    try:
        width, height = canvas
        card = Image.new("RGBA", canvas, (24, 25, 28, 255))
        draw = ImageDraw.Draw(card)
        main_font, sub_font = _font(_FONT_DISPLAY, 92), _font(_FONT_BOLD, 54)
        main, sub = DISCLAIMER_CARD_LINES
        main_w, sub_w = draw.textlength(main, font=main_font), draw.textlength(sub, font=sub_font)
        top = height // 2 - 110
        draw.text(((width - main_w) / 2, top), main, font=main_font, fill=(255, 255, 255, 255))
        bar_w = 180
        draw.rectangle(((width - bar_w) / 2, top + 150, (width + bar_w) / 2, top + 158), fill=ACCENT)
        draw.text(((width - sub_w) / 2, top + 190), sub, font=sub_font, fill=(190, 192, 198, 255))
        return _save(card, output_path)
    except Exception:
        logger.exception("고지 카드 렌더링 실패")
        return False


def render_disclaimer_banner(*, output_path: str, text: str = DISCLAIMER_BANNER_TEXT, canvas: tuple[int, int] = CANVAS) -> bool:
    """투자 판단·교육 목적 고지 배너(상단 띠). 내레이션이 아니라 화면 문구다."""
    try:
        width, height = canvas
        overlay = Image.new("RGBA", canvas, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = _font(_FONT_BOLD, 34)
        text_w = draw.textlength(text, font=font)
        bar_h = 72
        top = 56
        draw.rectangle((0, top, width, top + bar_h), fill=(10, 14, 24, 220))
        draw.rectangle((0, top, 14, top + bar_h), fill=ACCENT)
        draw.text(((width - text_w) / 2, top + 16), text, font=font, fill=(255, 255, 255, 255))
        return _save(overlay, output_path)
    except Exception:
        logger.exception("고지 배너 렌더링 실패")
        return False


_FFMPEG_THREADS = 4
_COMPOSE_SLOTS = threading.BoundedSemaphore(int(os.getenv("SLIDE_COMPOSE_CONCURRENCY", "3")))


def compose_animated_overlay(
    *, base_path: str, overlay_path: str, output_path: str, duration: float,
    base_is_video: bool, start_offset: float = 0.35, hold_seconds: float | None = None,
    fade_in_seconds: float = 0.35, fade_out_seconds: float = 0.3, hold_to_end: bool = False,
    motion: str = "rise", dim_base: bool = False, canvas: tuple[int, int] = CANVAS,
) -> bool:
    """배경(영상 또는 정지 이미지) 위에 오버레이를 "시간에 맞춰" 얹은 mp4를 만든다.

    2026-10-15 운영자 지시: 수치 그래프·기사 카드는 시청자를 지루하지 않게 하는 후편집이다.
    장면 내내 박혀 있는 정지 그림이 아니라 start_offset 이후 페이드로 나타났다 사라진다.
    """
    width, height = canvas
    duration = max(float(duration), 0.8)
    start = min(max(start_offset, 0.0), max(duration - 0.6, 0.0))
    if hold_to_end:
        end = duration
    else:
        end = duration - 0.3 if hold_seconds is None else min(duration - 0.3, start + hold_seconds)
    end = max(end, start + 0.5)
    fade_in, fade_out = max(fade_in_seconds, 0.0), max(fade_out_seconds, 0.0)
    fades = ""
    if fade_in > 0:
        fades += f",fade=t=in:st={start:.3f}:d={fade_in}:alpha=1"
    if fade_out > 0:
        fades += f",fade=t=out:st={max(end - fade_out, start):.3f}:d={fade_out}:alpha=1"
    # 아래에서 제자리로 올라오며 나타나는 모션(상승 화살표·숫자 강조에 어울림). 배너는 제자리 유지.
    if motion == "rise":
        place = (
            f"0:'{OVERLAY_RISE_PX}*pow(1-min(max((t-{start:.3f})/{OVERLAY_RISE_SECONDS},0),1),2)'"
            ":eval=frame:format=auto"
        )
    else:
        place = "0:0:format=auto"
    if base_is_video:
        base_input = ["-stream_loop", "-1", "-i", base_path]
    else:
        base_input = ["-loop", "1", "-framerate", "30", "-i", base_path]
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-threads", str(_FFMPEG_THREADS), *base_input,
        "-loop", "1", "-framerate", "30", "-i", overlay_path,
        "-filter_complex",
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
        f"setsar=1,fps=30{BASE_DIM_FILTER if dim_base else ''}[b];"
        f"[1:v]scale={width}:{height},format=rgba{fades}[o];"
        f"[b][o]overlay={place}",
        "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p", output_path, "-loglevel", "error",
    ]
    # 2026-10-16 20번 Job: 4K 스톡을 병렬로 합성하다 일부 ffmpeg가 240초 시간 초과로 멈춰 후편집 8장면이 배경만 남았다.
    # 동시에 도는 합성 수를 제한하고(컨테이너 메모리 4GB), 시간 초과는 한 번 재시도하며 실패 원인을 남긴다.
    for attempt in (1, 2):
        try:
            with _COMPOSE_SLOTS:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            logger.warning("애니메이션 오버레이 합성 시간 초과(%s회차): %s", attempt, output_path)
            continue
        except Exception:
            logger.exception("애니메이션 오버레이 합성 실패")
            return False
        ok = result.returncode == 0 and Path(output_path).is_file() and Path(output_path).stat().st_size > 1000
        if not ok:
            logger.warning("애니메이션 오버레이 합성 실패(rc=%s): %s", result.returncode, str(result.stderr)[-300:])
        return ok
    return False
