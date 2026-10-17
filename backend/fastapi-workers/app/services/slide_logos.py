"""상장사 로고 — 화면 템플릿(비교·흐름·숫자)에서 회사 이름 대신 아이콘 자리에 쓴다.

2026-10-17 운영자 요청: 삼성전자·SK하이닉스처럼 아는 회사가 나올 때는 회사 로고를 보여 주면 더 와닿는다.
로고 파일은 assets/logos에 두고, 파일별 출처·라이선스는 library.json(sources)에 기록한다. 로고는 흰 카드 위에 올려 어두운 배경에서도
읽히게 하고, 이름·별칭이 정확히 일치할 때만 쓴다(비슷한 이름으로 엉뚱한 회사 로고를 쓰지 않는다)."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

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


def logo_sprite(name: str, directory: Path | str | None = None, height: int = CARD_HEIGHT) -> Image.Image | None:
    """흰 둥근 카드 위에 로고를 올린 스프라이트. 로고를 못 찾으면 None."""
    label = resolve_logo(name, directory)
    if not label:
        return None
    entry = _library(str(directory or LOGO_DIR))["labels"][label]
    try:
        with Image.open(entry["path"]) as source:
            logo = source.convert("RGBA")
    except OSError:
        logger.warning("로고 파일을 열지 못함: %s", entry.get("path"))
        return None
    scale = height / CARD_HEIGHT
    inner_h = int(height * 0.62)
    ratio = logo.width / max(logo.height, 1)
    inner_w = int(inner_h * ratio)
    card_w = int(min(max(inner_w + 90 * scale, CARD_MIN_WIDTH * scale), CARD_MAX_WIDTH * scale))
    if inner_w > card_w - 70 * scale:  # 아주 가로로 긴 로고는 폭에 맞춘다
        inner_w = int(card_w - 70 * scale)
        inner_h = int(inner_w / max(ratio, 0.01))
    logo = logo.resize((max(inner_w, 1), max(inner_h, 1)), Image.LANCZOS)
    card = Image.new("RGBA", (card_w, height), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, card_w - 1, height - 1), radius=int(34 * scale), fill=(250, 250, 250, 255))
    card.alpha_composite(logo, ((card_w - logo.width) // 2, (height - logo.height) // 2))
    return card
