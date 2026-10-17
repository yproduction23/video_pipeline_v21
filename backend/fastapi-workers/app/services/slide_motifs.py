"""모션그래픽용 소재 이미지(motif) 라이브러리 — "자막 그대로 쓰기"를 줄이기 위한 상황별 일러스트.

2026-10-17 운영자 요청: 배경 전체가 아니라 모션그래픽에 쓸 소재(달러 지폐, 반도체 칩, 경보 등)를 같은 화풍으로 미리 그려
배경 없이 저장해 두고 두고두고 쓴다. 생성은 사전 작업(generate_motif)이고, 렌더 중에는 저장된 파일만 쓴다
(없는 소재는 그리지 않고 건너뛴다). 소재에는 글자·숫자를 넣지 않는다 — 수치·문구는 후편집(Pillow)이 만든다.

Gemini는 투명 PNG를 만들지 못하므로 균일한 초록(#00FF00) 배경으로 그리게 하고 key_out_green으로 지운다.
소재 팔레트(노랑·흰색·회색·붉은 경고)에는 초록이 없어 배경과 섞이지 않는다."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

MOTIF_DIR = Path(__file__).resolve().parents[2] / "assets" / "motifs"
PREFIX = "motif:"
STYLE_VERSION = "motif-style-v1"
GENERATION_MODEL = "gemini-3-pro-image"

MOTIF_STYLE_PROMPT = (
    "A single flat vector-style illustration of {subject}. Bold simple geometric shapes, clean medium-thick dark navy "
    "outline (#1B2230), flat colors only: warm yellow (#FFC933) as the main color, white, light gray and mid gray; "
    "use red (#E5383B) only if the subject is a warning or a decline. No gradients, no textures, no shadows, no "
    "reflections. Front view, centered, large, with a generous margin. The background must be one perfectly uniform "
    "pure chroma-key green (#00FF00) with nothing else on it: no ground line, no floor, no border, no vignette. "
    "The subject itself must not contain any green. The picture must contain NO text of any kind: no letters, no "
    "Korean or English words, no numbers, no currency amounts, no logos, no watermarks. Faceless: if a person is "
    "needed, draw a simple faceless figure."
)


@lru_cache(maxsize=4)
def _library(directory: str) -> dict:
    try:
        entries = json.loads((Path(directory) / "library.json").read_text(encoding="utf-8"))
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


def motif_labels(directory: Path | str | None = None) -> list[str]:
    return list(_library(str(directory or MOTIF_DIR))["labels"])


def resolve_motif(name: object, directory: Path | str | None = None) -> str | None:
    """이름·별칭이 정확히 일치하는 소재의 대표 이름(없으면 None). "motif:달러" 형태도 받는다."""
    text = str(name or "").strip()
    if text.startswith(PREFIX):
        text = text[len(PREFIX):]
    return _library(str(directory or MOTIF_DIR))["aliases"].get(text.lower())


def motif_prompt_hint(directory: Path | str | None = None) -> str:
    labels = motif_labels(directory)
    return ("그림 소재(icon 필드에 'motif:이름'으로 쓰면 일러스트가 그려진다): " + ", ".join(labels)) if labels else ""


def key_out_green(image: Image.Image) -> Image.Image:
    """초록 배경을 투명으로 만들고, 경계에 번진 초록기를 지운다."""
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, _ = pixels[x, y]
            dominance = g - max(r, b)
            if dominance >= 70 and g > 150:          # 배경 초록
                pixels[x, y] = (0, 0, 0, 0)
            elif dominance > 8:                      # 경계: 초록기를 깎고 반투명 처리
                softness = min(1.0, (dominance - 8) / 62)
                despilled = max(r, b)
                pixels[x, y] = (r, despilled, b, int(255 * (1 - softness)))
    return rgba


def _cropped(image: Image.Image) -> Image.Image:
    box = image.getchannel("A").getbbox()
    return image.crop(box) if box else image


def motif_sprite(name: str, directory: Path | str | None = None, height: int = 300) -> Image.Image | None:
    """투명 여백을 자른 소재 스프라이트(가로세로 비율 유지, 높이 height). 없으면 None."""
    label = resolve_motif(name, directory)
    if not label:
        return None
    entry = _library(str(directory or MOTIF_DIR))["labels"][label]
    try:
        with Image.open(entry["path"]) as source:
            image = _cropped(source.convert("RGBA"))
    except OSError:
        logger.warning("소재 파일을 열지 못함: %s", entry.get("path"))
        return None
    ratio = image.width / max(image.height, 1)
    return image.resize((max(int(height * ratio), 1), height), Image.LANCZOS)


def generate_motif(subject_en: str, raw_path: str, *, job_id: int, scene_key: str) -> None:
    """Gemini로 초록 배경 소재 이미지 1장을 그린다(유료, 약 ₩339). 배경 제거는 key_out_green으로 따로 한다."""
    from app.providers.real.image import NanaBananaProvider
    from app.utils.budget import ProviderRequestAudit

    NanaBananaProvider().generate_image(
        prompt=MOTIF_STYLE_PROMPT.format(subject=subject_en),
        output_path=raw_path,
        section="news",
        keyword=subject_en[:30],
        character_style_prompt="none",
        style_locked=True,
        suppress_legacy_style_lock=True,
        image_provider="gemini",
        gemini_model=GENERATION_MODEL,
        gemini_image_size="2K",
        gemini_max_attempts=1,
        gemini_request_audit=ProviderRequestAudit.for_job(job_id=job_id, scene_key=scene_key, model=GENERATION_MODEL),
    )
