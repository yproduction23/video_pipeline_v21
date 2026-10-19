"""연상 소재 라이브러리 관리(어드민 화면용) — 목록·확인·삭제·직접 업로드·설명으로 생성.

2026-10-19 운영자 요청: 연상에 쓰는 소재를 UI에서 추가·관리할 수 있어야 한다. 움직임 부품(이동·도미노 등)은 코드라 UI로 못 늘리고,
소재(그림)만 UI로 늘린다. 즉석 생성된 소재는 review=pending으로 등록되므로 여기서 육안으로 확인(승인)하거나 삭제한다.

- 목록: 라이브러리 항목 + 이미지 파일 유무 + 출처(생성기·날짜·검수 상태)
- 업로드: 투명 배경 PNG(직접 그린 것)를 받아 크기·투명도를 확인하고 등록(source.manual)
- 생성: slide_motif_factory를 한 장 예산으로 호출한다(장당 UNIT_COST_KRW, 비전 검수 포함)
- 확인/삭제: review를 approved로 바꾸거나 항목과 파일을 지운다(되돌릴 수 없으므로 화면에서 확인을 받는다)"""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
from datetime import date
from pathlib import Path

from PIL import Image

from app.services import slide_motif_factory, slide_motifs

MAX_UPLOAD_BYTES = 6 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 64, 4096


class MotifAdminError(ValueError):
    """화면에 그대로 보여 줄 수 있는 한국어 사유."""


def _directory(directory: Path | str | None) -> Path:
    return Path(directory or slide_motifs.MOTIF_DIR)


def _entries(directory: Path) -> list[dict]:
    try:
        return json.loads((directory / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write(directory: Path, entries: list[dict]) -> None:
    (directory / "library.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    slide_motifs._library.cache_clear()


def list_motifs(directory: Path | str | None = None) -> list[dict]:
    directory = _directory(directory)
    rows = []
    for entry in _entries(directory):
        source = entry.get("source") or {}
        rows.append({
            "slug": entry.get("slug"), "label": entry.get("label"), "aliases": entry.get("aliases") or [],
            "has_image": (directory / str(entry.get("file") or "")).is_file(),
            "generator": source.get("generator") or ("manual" if source.get("manual") else ""),
            "created": source.get("created") or "", "on_demand": bool(source.get("on_demand")),
            "review": source.get("review") or "approved",
        })
    return rows


def image_path(slug: str, directory: Path | str | None = None) -> Path | None:
    directory = _directory(directory)
    for entry in _entries(directory):
        if entry.get("slug") == slug:
            path = directory / str(entry.get("file") or "")
            return path if path.is_file() else None
    return None


def _clean_aliases(aliases: object) -> list[str]:
    values = aliases if isinstance(aliases, list) else re.split(r"[,\n]", str(aliases or ""))
    cleaned = []
    for value in values:
        text = str(value).strip()
        if text and text not in cleaned:
            cleaned.append(text[:20])
    return cleaned[:8]


def _slug_for(label: str, entries: list[dict]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", slide_motif_factory._slug(label)) or "motif"
    slug, number = base, 2
    taken = {e.get("slug") for e in entries}
    while slug in taken:
        slug, number = f"{base}_{number}", number + 1
    return slug


def add_uploaded(label: str, aliases: object, image_base64: str, *, directory: Path | str | None = None) -> dict:
    """직접 그린 투명 배경 PNG를 등록한다. 이름·별칭이 기존 소재와 겹치면 거절한다(엉뚱한 소재가 대신 쓰이지 않게)."""
    directory = _directory(directory)
    label = str(label or "").strip()
    if not 2 <= len(label) <= 12:
        raise MotifAdminError("이름은 2~12자로 적어 주세요.")
    names = [label, *_clean_aliases(aliases)]
    for name in names:
        if slide_motifs.resolve_motif(name, directory):
            raise MotifAdminError(f"'{name}'은(는) 이미 다른 소재의 이름·별칭입니다.")
    try:
        raw = base64.b64decode(re.sub(r"^data:image/\w+;base64,", "", str(image_base64 or "")), validate=False)
    except (binascii.Error, ValueError):
        raise MotifAdminError("이미지 파일을 읽지 못했습니다.")
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise MotifAdminError("이미지는 6MB 이하여야 합니다.")
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception:
        raise MotifAdminError("PNG 이미지만 올릴 수 있습니다.")
    if min(image.size) < MIN_SIDE or max(image.size) > MAX_SIDE:
        raise MotifAdminError(f"이미지 한 변은 {MIN_SIDE}~{MAX_SIDE}px여야 합니다.")
    sprite = image.convert("RGBA")
    alpha = sprite.getchannel("A")
    if alpha.getextrema()[0] > 250:
        raise MotifAdminError("투명 배경이 필요합니다(배경이 있는 이미지는 영상 위에서 네모 상자로 보입니다).")
    sprite = slide_motifs._cropped(sprite)
    entries = _entries(directory)
    slug = _slug_for(label, entries)
    directory.mkdir(parents=True, exist_ok=True)
    sprite.save(directory / f"{slug}.png")
    entries.append({
        "slug": slug, "label": label, "aliases": _clean_aliases(aliases)[: 8], "file": f"{slug}.png",
        "source": {"generator": "manual", "manual": True, "style": "uploaded", "created": date.today().isoformat(), "review": "approved"},
    })
    _write(directory, entries)
    return {"slug": slug, "label": label}


def set_review(slug: str, action: str, directory: Path | str | None = None) -> dict:
    """approve: 검수 대기를 승인으로 바꾼다. delete: 항목과 이미지 파일을 지운다."""
    directory = _directory(directory)
    entries = _entries(directory)
    target = next((e for e in entries if e.get("slug") == slug), None)
    if not target:
        raise MotifAdminError("그런 소재가 없습니다.")
    if action == "approve":
        target.setdefault("source", {})["review"] = "approved"
    elif action == "delete":
        entries = [e for e in entries if e.get("slug") != slug]
        (directory / str(target.get("file") or "")).unlink(missing_ok=True)
    else:
        raise MotifAdminError("지원하지 않는 작업입니다.")
    _write(directory, entries)
    return {"slug": slug, "action": action}


def generate_from_description(description: str, *, job_id: int = 0, directory: Path | str | None = None, **kwargs) -> dict:
    """설명으로 소재 한 장을 만든다(Gemini 1장 ≈ UNIT_COST_KRW). 결과는 slide_motif_factory.make_missing_motifs의 반환값."""
    description = str(description or "").strip()
    if not 4 <= len(description) <= 120:
        raise MotifAdminError("만들 소재를 4~120자로 설명해 주세요(예: 쿠션처럼 충격을 흡수하는 완충재).")
    order = {"type": "motif", "name": f"adhoc_{abs(hash(description)) % 10**8}", "description": description}
    return slide_motif_factory.make_missing_motifs([order], job_id=job_id, budget=slide_motif_factory.UNIT_COST_KRW, directory=_directory(directory), **kwargs)
