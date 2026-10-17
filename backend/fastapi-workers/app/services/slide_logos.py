"""상장사 로고 — 화면 템플릿(비교·흐름·숫자)에서 회사 이름 대신 아이콘 자리에 쓴다.

2026-10-17 운영자 요청: 삼성전자·SK하이닉스처럼 아는 회사가 나올 때는 회사 로고를 보여 주면 더 와닿는다.
로고 파일은 assets/logos에 두고, 파일별 출처·라이선스는 library.json(sources)에 기록한다. 로고는 흰 카드 위에 올려 어두운 배경에서도
읽히게 하고, 이름·별칭이 정확히 일치할 때만 쓴다(비슷한 이름으로 엉뚱한 회사 로고를 쓰지 않는다)."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

LOGO_DIR = Path(__file__).resolve().parents[2] / "assets" / "logos"
PREFIX = "logo:"
CARD_HEIGHT = 300
CARD_MIN_WIDTH, CARD_MAX_WIDTH = 320, 640


@lru_cache(maxsize=4)
def _library(directory: str) -> dict:
    path = Path(directory) / "library.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"labels": {}, "aliases": {}}
    labels: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    for entry in entries:
        file = Path(directory) / str(entry.get("file") or "")
        if not file.is_file():
            continue
        label = str(entry["label"])
        labels[label] = {**entry, "path": str(file)}
        for alias in [label, *entry.get("aliases", [])]:
            aliases[str(alias).strip().lower()] = label
    return {"labels": labels, "aliases": aliases}


def logo_labels(directory: Path | str | None = None) -> list[str]:
    return list(_library(str(directory or LOGO_DIR))["labels"])


def resolve_logo(name: object, directory: Path | str | None = None) -> str | None:
    """이름·별칭이 정확히 일치하는 회사의 대표 이름을 돌려준다(없으면 None). "logo:삼성전자" 형태도 받는다."""
    text = str(name or "").strip()
    if text.startswith(PREFIX):
        text = text[len(PREFIX):]
    return _library(str(directory or LOGO_DIR))["aliases"].get(text.lower())


def logo_prompt_hint(directory: Path | str | None = None) -> str:
    """시퀀스 설계 프롬프트에 붙일 로고 목록."""
    library = _library(str(directory or LOGO_DIR))["labels"]
    if not library:
        return ""
    return "회사 로고(icon 필드에 이 이름을 그대로 쓰면 로고가 그려진다): " + ", ".join(library)


def _cutout(logo: Image.Image) -> Image.Image:
    """흰 배경을 없애고(누끼), 어두운 무채색 글자·마크는 흰색으로 바꿔 어두운 화면에서도 읽히게 한다."""
    logo = logo.convert("RGBA")
    pixels = logo.load()
    width, height = logo.size
    corners = [pixels[0, 0], pixels[width - 1, 0], pixels[0, height - 1], pixels[width - 1, height - 1]]
    opaque_white_background = all(c[3] > 240 and min(c[:3]) > 235 for c in corners)
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            if opaque_white_background and min(r, g, b) > 235:
                pixels[x, y] = (255, 255, 255, 0)
                continue
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            if luminance < 95 and max(r, g, b) - min(r, g, b) < 45:  # 검정·짙은 회색 → 흰색
                pixels[x, y] = (255, 255, 255, a)
    return logo


def _with_rim(logo: Image.Image, radius: int = 5) -> Image.Image:
    """로고 둘레에 얇은 밝은 테두리를 둬 짙은 색 로고도 어두운 배경에서 분리되게 한다."""
    alpha = logo.getchannel("A")
    grown = alpha.filter(ImageFilter.MaxFilter(radius * 2 + 1))
    rim = Image.new("RGBA", logo.size, (245, 245, 248, 0))
    rim.putalpha(grown.point(lambda v: int(v * 0.8)))
    rim.alpha_composite(logo)
    return rim


def logo_sprite(name: str, directory: Path | str | None = None, height: int = CARD_HEIGHT) -> Image.Image | None:
    """배경 없이(누끼) 로고만 그린 스프라이트. 로고를 못 찾으면 None."""
    label = resolve_logo(name, directory)
    if not label:
        return None
    entry = _library(str(directory or LOGO_DIR))["labels"][label]
    try:
        with Image.open(entry["path"]) as source:
            logo = _cutout(source.copy())
    except OSError:
        logger.warning("로고 파일을 열지 못함: %s", entry.get("path"))
        return None
    box = logo.getchannel("A").getbbox()
    if box:
        logo = logo.crop(box)  # 투명 여백 제거
    scale = height / CARD_HEIGHT
    target_h = int(height * 0.7)
    max_w = int(CARD_MAX_WIDTH * scale)
    ratio = logo.width / max(logo.height, 1)
    target_w = int(target_h * ratio)
    if target_w > max_w:
        target_w, target_h = max_w, int(max_w / max(ratio, 0.01))
    logo = logo.resize((max(target_w, 1), max(target_h, 1)), Image.LANCZOS)
    pad = 8
    canvas = Image.new("RGBA", (logo.width + pad * 2, logo.height + pad * 2), (0, 0, 0, 0))
    canvas.alpha_composite(logo, (pad, pad))
    return _with_rim(canvas)
