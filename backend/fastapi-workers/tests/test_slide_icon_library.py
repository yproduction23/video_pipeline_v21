"""슬라이드형 아이콘 세트 (2026-10-16 운영자 요청: 영상 스타일에 맞는 아이콘, 노란 이모티콘 금지).

assets/icons의 SVG 세트는 한 스타일 계약으로 그린 평면 아이콘이며, Claude는 목록 안의 이름만 고를 수 있다."""
import json
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

from app.services import slide_icons
from app.services.slide_icons import ICON_HINTS, ICON_NAMES, SVG_ICON_DIR, draw_icon, icon_image

ALLOWED_TAGS = {"svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text"}
ALLOWED_COLORS = {"#f5f5f5", "#ffb81c", "#1b1d22", "#9aa0a6", "none"}


def _library_names():
    rows = json.loads((SVG_ICON_DIR / "library.json").read_text(encoding="utf-8"))
    return [row["name"] for row in rows]


def test_every_library_entry_has_an_svg_and_a_hint_and_is_selectable():
    names = _library_names()

    assert len(names) >= 40
    for name in names:
        assert (SVG_ICON_DIR / f"{name}.svg").is_file(), name
        assert name in ICON_NAMES and ICON_HINTS[name], name
    for legacy in ("money_stack", "coin", "chart_up", "chart_down", "person", "people"):
        assert legacy in ICON_NAMES  # 기존 계획서가 쓰던 이름은 계속 유효하다


def test_svgs_follow_the_style_contract():
    for name in _library_names():
        root = ET.parse(SVG_ICON_DIR / f"{name}.svg").getroot()
        tags = {el.tag.split("}")[-1] for el in root.iter()}
        assert tags <= ALLOWED_TAGS, (name, tags - ALLOWED_TAGS)
        assert root.attrib["viewBox"] == "0 0 128 128", name
        text = (SVG_ICON_DIR / f"{name}.svg").read_text(encoding="utf-8").lower()
        colors = set(re.findall(r"#[0-9a-f]{6}", text))
        assert colors <= ALLOWED_COLORS, (name, colors - ALLOWED_COLORS)  # 4색 팔레트만
        assert "gradient" not in text and "opacity" not in text, name


def test_icon_image_rasterizes_with_transparency_and_is_cached():
    image = icon_image("chip", 160)

    assert image is not None and image.size == (160, 160)
    alpha = image.getchannel("A")
    assert alpha.getextrema()[0] == 0 and alpha.getextrema()[1] == 255  # 투명 배경 + 불투명 도형
    assert (slide_icons._CACHE_DIR / "chip_160.png").is_file()
    assert icon_image("no_such_icon", 160) is None


def test_draw_icon_prefers_the_library_and_falls_back_to_the_legacy_shapes():
    canvas = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    assert draw_icon(draw, "chip", 200, 200, 200) is True
    assert canvas.getchannel("A").getbbox() is not None
    assert draw_icon(draw, "unicorn", 200, 200, 200) is False
