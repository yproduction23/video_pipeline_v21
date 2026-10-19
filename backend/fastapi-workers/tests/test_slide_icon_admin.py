"""아이콘 라이브러리 관리(어드민) — 업로드 검사·삭제 제한·PNG 아이콘 렌더·목록 갱신."""
import base64
import io
import json

import pytest
from PIL import Image

from app.services import slide_icon_admin, slide_icons
from app.services.slide_icon_admin import IconAdminError

SVG = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'><circle cx='64' cy='64' r='40' fill='#F5F5F5' stroke='#1B1D22' stroke-width='5'/></svg>"


def _b64(raw: bytes) -> str:
    return "data:application/octet-stream;base64," + base64.b64encode(raw).decode()


def _png(size=(120, 90), transparent=True) -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0) if transparent else (255, 0, 0, 255))
    for x in range(30, 90):
        for y in range(20, 70):
            image.putpixel((x, y), (255, 184, 28, 255))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def library(tmp_path):
    (tmp_path / "library.json").write_text(json.dumps([{"name": "base", "hint": "기본 아이콘"}]), encoding="utf-8")
    (tmp_path / "base.svg").write_text(SVG, encoding="utf-8")
    return tmp_path


def test_svg_upload_is_registered_and_listed(library):
    result = slide_icon_admin.add_uploaded("Sentinel", "방패: 안전·방어·보호", _b64(SVG.encode()), directory=library)

    assert result == {"name": "sentinel", "hint": "방패: 안전·방어·보호", "format": "svg"}
    assert (library / "sentinel.svg").is_file()
    rows = {row["name"]: row for row in slide_icon_admin.list_icons(library)}
    assert rows["sentinel"]["uploaded"] is True and rows["sentinel"]["has_image"] is True
    assert rows["base"]["uploaded"] is False
    found = slide_icon_admin.image_file("sentinel", library)
    assert found and found[1] == "image/svg+xml"


def test_png_upload_requires_transparency_and_uses_a_file_field(library):
    with pytest.raises(IconAdminError, match="투명"):
        slide_icon_admin.add_uploaded("badge", "배지: 인증·보증 표시", _b64(_png(transparent=False)), directory=library)

    slide_icon_admin.add_uploaded("badge", "배지: 인증·보증 표시", _b64(_png()), directory=library)

    entry = next(e for e in json.loads((library / "library.json").read_text(encoding="utf-8")) if e["name"] == "badge")
    assert entry["file"] == "badge.png" and (library / "badge.png").is_file()
    assert slide_icon_admin.image_file("badge", library)[1] == "image/png"


@pytest.mark.parametrize("markup, reason", [
    ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><script>alert(1)</script></svg>", "쓸 수 없는 요소"),
    ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><image href='http://x/y.png'/></svg>", "쓸 수 없는 요소"),
    ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1 1'><rect onclick='x()'/></svg>", "쓸 수 없는 속성"),
    ("<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>", "viewBox"),
])
def test_unsafe_svg_is_refused(library, markup, reason):
    with pytest.raises(IconAdminError, match=reason):
        slide_icon_admin.add_uploaded("unsafe", "위험한 아이콘 시험", _b64(markup.encode()), directory=library)
    assert not (library / "unsafe.svg").exists()


def test_name_hint_and_duplicate_rules(library):
    with pytest.raises(IconAdminError, match="이름"):
        slide_icon_admin.add_uploaded("a", "충분히 긴 설명입니다", _b64(SVG.encode()), directory=library)
    with pytest.raises(IconAdminError, match="설명"):
        slide_icon_admin.add_uploaded("sentinel", "방패", _b64(SVG.encode()), directory=library)
    with pytest.raises(IconAdminError, match="같은 설명"):
        slide_icon_admin.add_uploaded("sentinel", "기본 아이콘", _b64(SVG.encode()), directory=library)
    # 한글 이름은 번호 이름이 붙고, 같은 이름은 번호가 늘어난다
    first = slide_icon_admin.add_uploaded("방패", "방패: 안전·방어·보호", _b64(SVG.encode()), directory=library)
    second = slide_icon_admin.add_uploaded("방패", "방패: 두 번째 시안", _b64(SVG.encode()), directory=library)
    assert first["name"] != second["name"] and first["name"].startswith("custom_")


def test_only_uploaded_icons_can_be_deleted(library):
    slide_icon_admin.add_uploaded("sentinel", "방패: 안전·방어·보호", _b64(SVG.encode()), directory=library)

    with pytest.raises(IconAdminError, match="기본 아이콘"):
        slide_icon_admin.delete_icon("base", library)
    slide_icon_admin.delete_icon("sentinel", library)

    assert not (library / "sentinel.svg").exists()
    assert [row["name"] for row in slide_icon_admin.list_icons(library)] == ["base"]
    with pytest.raises(IconAdminError, match="없습니다"):
        slide_icon_admin.delete_icon("sentinel", library)


def test_reload_makes_uploaded_png_icon_selectable_and_renderable(tmp_path, monkeypatch):
    (tmp_path / "library.json").write_text(json.dumps([
        {"name": "badge", "hint": "배지: 인증", "file": "badge.png", "source": {"manual": True}},
    ]), encoding="utf-8")
    (tmp_path / "badge.png").write_bytes(_png())
    monkeypatch.setattr(slide_icons, "SVG_ICON_DIR", tmp_path)
    monkeypatch.setattr(slide_icons, "_CACHE_DIR", tmp_path / "cache")
    try:
        names = slide_icons.ICON_NAMES
        slide_icons.reload_library()

        assert slide_icons.ICON_NAMES is names            # 다른 모듈이 import한 같은 객체가 갱신된다
        assert "badge" in names and slide_icons.ICON_HINTS["badge"] == "배지: 인증"
        image = slide_icons.icon_image("badge", 160)
        assert image is not None and image.size == (160, 160)
        assert image.getchannel("A").getextrema() == (0, 255)
    finally:
        monkeypatch.undo()
        slide_icons.reload_library()
    assert "badge" not in slide_icons.ICON_NAMES
