"""아이콘 라이브러리 관리(어드민 화면용) — 목록·이미지·직접 업로드·삭제.

2026-10-19 운영자 요청: 연상 소재뿐 아니라 아이콘(assets/icons)도 화면에서 추가할 수 있어야 하고, 직접 올리는 기능이 있어야 한다.
- 업로드: SVG(기본 세트와 같은 평면 스타일 권장) 또는 투명 배경 PNG. SVG는 허용 태그만 남겨 검사해 스크립트·외부 참조를 막는다.
- 기본 세트(library.json에 source가 없는 항목)는 기존 계획서가 이름으로 참조하므로 삭제할 수 없다. 올린 아이콘만 삭제한다.
- 등록·삭제 직후 slide_icons.reload_library()로 Claude가 고를 수 있는 이름·설명 목록을 갱신한다.
- 기본 세트의 4색 팔레트 계약(test_slide_icon_library)은 기본 세트 검사이며, 올린 아이콘은 모양 계약(태그·viewBox)만 확인한다."""
from __future__ import annotations

import base64
import binascii
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from PIL import Image

from app.services import slide_icons

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 48, 2048
ALLOWED_TAGS = {"svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text", "defs"}
FORBIDDEN_ATTR = re.compile(r"^(on\w+|href|style)$", re.IGNORECASE)


class IconAdminError(ValueError):
    """화면에 그대로 보여 줄 수 있는 한국어 사유."""


def _directory(directory: Path | str | None) -> Path:
    return Path(directory or slide_icons.SVG_ICON_DIR)


def _entries(directory: Path) -> list[dict]:
    try:
        return json.loads((directory / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write(directory: Path, entries: list[dict], *, refresh: bool) -> None:
    (directory / "library.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if refresh:
        slide_icons.reload_library()


def _is_default_dir(directory: Path) -> bool:
    return directory.resolve() == Path(slide_icons.SVG_ICON_DIR).resolve()


def list_icons(directory: Path | str | None = None) -> list[dict]:
    directory = _directory(directory)
    rows = []
    for entry in _entries(directory):
        name = str(entry.get("name") or "")
        if not name:
            continue
        source = entry.get("source") or {}
        rows.append({
            "name": name, "hint": entry.get("hint") or name,
            "has_image": (directory / slide_icons.icon_file_name(entry)).is_file(),
            "uploaded": bool(source.get("manual")), "created": source.get("created") or "",
            "format": Path(slide_icons.icon_file_name(entry)).suffix.lstrip(".").lower(),
        })
    return rows


def image_file(name: str, directory: Path | str | None = None) -> tuple[Path, str] | None:
    """(파일 경로, MIME). 없으면 None."""
    directory = _directory(directory)
    for entry in _entries(directory):
        if entry.get("name") == name:
            path = directory / slide_icons.icon_file_name(entry)
            if path.is_file():
                return path, "image/png" if path.suffix.lower() == ".png" else "image/svg+xml"
    return None


def _name_for(label: str, entries: list[dict]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")
    if not base or not base[0].isalpha():
        base = f"custom_{len(entries) + 1}"
    taken = {str(e.get("name")) for e in entries} | set(slide_icons.ICON_NAMES)
    name, number = base, 2
    while name in taken:
        name, number = f"{base}_{number}", number + 1
    return name


def _check_svg(raw: bytes) -> bytes:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise IconAdminError("SVG 파일을 읽지 못했습니다.")
    if root.tag.split("}")[-1] != "svg":
        raise IconAdminError("SVG 파일이 아닙니다.")
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag not in ALLOWED_TAGS:
            raise IconAdminError(f"SVG에 쓸 수 없는 요소가 있습니다: {tag} (그림 도형과 글자만 허용해요).")
        for key in element.attrib:
            if FORBIDDEN_ATTR.match(key.split("}")[-1]):
                raise IconAdminError(f"SVG에 쓸 수 없는 속성이 있습니다: {key.split('}')[-1]} (스크립트·외부 참조 금지).")
    if not root.attrib.get("viewBox"):
        raise IconAdminError("SVG에 viewBox가 필요합니다(예: 0 0 128 128).")
    return raw


def _check_png(raw: bytes) -> bytes:
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception:
        raise IconAdminError("SVG 또는 PNG 파일만 올릴 수 있습니다.")
    if image.format != "PNG":
        raise IconAdminError("SVG 또는 PNG 파일만 올릴 수 있습니다.")
    if min(image.size) < MIN_SIDE or max(image.size) > MAX_SIDE:
        raise IconAdminError(f"이미지 한 변은 {MIN_SIDE}~{MAX_SIDE}px여야 합니다.")
    if image.convert("RGBA").getchannel("A").getextrema()[0] > 250:
        raise IconAdminError("투명 배경이 필요합니다(배경이 있으면 영상 위에서 네모 상자로 보입니다).")
    return raw


def add_uploaded(name_label: str, hint: str, file_base64: str, *, directory: Path | str | None = None) -> dict:
    """직접 만든 아이콘을 등록한다. 설명(hint)은 Claude가 아이콘을 고르는 근거이므로 어떤 뜻에 쓰는지 구체적으로 받는다."""
    directory = _directory(directory)
    name_label = str(name_label or "").strip()
    hint = str(hint or "").strip()
    if not 2 <= len(name_label) <= 20:
        raise IconAdminError("이름은 2~20자로 적어 주세요(영문 소문자·숫자·밑줄이면 그대로 쓰이고, 한글이면 번호 이름이 붙어요).")
    if not 4 <= len(hint) <= 80:
        raise IconAdminError("설명을 4~80자로 적어 주세요(예: 방패: 안전·방어·보호). 영상 제작 때 이 설명을 보고 아이콘을 골라요.")
    try:
        raw = base64.b64decode(re.sub(r"^data:[\w/+.-]+;base64,", "", str(file_base64 or "")), validate=False)
    except (binascii.Error, ValueError):
        raise IconAdminError("파일을 읽지 못했습니다.")
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise IconAdminError("파일은 2MB 이하여야 합니다.")
    is_svg = raw.lstrip()[:200].lower().find(b"<svg") >= 0 or raw.lstrip().startswith(b"<?xml")
    extension = "svg" if is_svg else "png"
    raw = _check_svg(raw) if is_svg else _check_png(raw)
    entries = _entries(directory)
    for entry in entries:
        if str(entry.get("hint") or "").strip() == hint:
            raise IconAdminError("같은 설명의 아이콘이 이미 있습니다. 설명을 다르게 적어 주세요.")
    name = _name_for(name_label, entries)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.{extension}").write_bytes(raw)
    entry = {"name": name, "hint": hint, "source": {"manual": True, "label": name_label, "created": date.today().isoformat()}}
    if extension == "png":
        entry["file"] = f"{name}.png"
    entries.append(entry)
    _write(directory, entries, refresh=_is_default_dir(directory))
    return {"name": name, "hint": hint, "format": extension}


def delete_icon(name: str, directory: Path | str | None = None) -> dict:
    """올린 아이콘만 삭제한다. 기본 세트는 기존 계획서가 이름으로 참조하므로 삭제하지 않는다."""
    directory = _directory(directory)
    entries = _entries(directory)
    target = next((e for e in entries if e.get("name") == name), None)
    if not target:
        raise IconAdminError("그런 아이콘이 없습니다.")
    if not (target.get("source") or {}).get("manual"):
        raise IconAdminError("기본 아이콘은 삭제할 수 없어요(기존 계획서가 이름으로 쓰고 있어요). 직접 올린 아이콘만 삭제됩니다.")
    entries = [e for e in entries if e.get("name") != name]
    (directory / slide_icons.icon_file_name(target)).unlink(missing_ok=True)
    _write(directory, entries, refresh=_is_default_dir(directory))
    return {"name": name}
