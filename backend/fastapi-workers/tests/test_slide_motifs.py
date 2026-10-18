"""모션그래픽용 소재 이미지(motif): 초록 배경 생성본에서 배경을 지워 투명 PNG로 만들고 라이브러리로 쓴다(2026-10-17)."""
import json

from PIL import Image, ImageDraw

from app.services.slide_motifs import key_out_green, motif_labels, motif_sprite, resolve_motif


def _green_sample():
    image = Image.new("RGB", (400, 400), (0, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 300, 300), fill=(255, 201, 51))      # 노란 소재
    draw.rectangle((90, 90, 100, 310), fill=(120, 200, 40))        # 가장자리에 초록이 번진 경계
    return image


def test_green_background_becomes_transparent_and_subject_stays_opaque():
    result = key_out_green(_green_sample())

    assert result.getpixel((10, 10))[3] == 0            # 배경
    assert result.getpixel((200, 200))[3] == 255        # 소재
    assert result.getpixel((200, 200))[:3] == (255, 201, 51)
    assert result.getpixel((95, 200))[3] < 255          # 번진 경계는 투명해지거나 반투명


def test_edge_pixels_lose_their_green_tint():
    result = key_out_green(_green_sample())
    r, g, b, a = result.getpixel((101, 200))
    if a:
        assert g <= max(r, b) + 5                       # 가장자리 초록기 제거


def test_motif_library_resolves_label_and_alias_and_renders_a_cropped_sprite(tmp_path):
    sprite = key_out_green(_green_sample())
    sprite.save(tmp_path / "dollar_stack.png")
    (tmp_path / "library.json").write_text(json.dumps([
        {"slug": "dollar_stack", "label": "달러 지폐 더미", "aliases": ["달러", "지폐"], "file": "dollar_stack.png"},
    ], ensure_ascii=False), encoding="utf-8")

    assert motif_labels(tmp_path) == ["달러 지폐 더미"]
    assert resolve_motif("지폐", tmp_path) == "달러 지폐 더미"
    assert resolve_motif("motif:달러", tmp_path) == "달러 지폐 더미"
    assert resolve_motif("없는 소재", tmp_path) is None

    image = motif_sprite("달러", tmp_path, height=300)
    assert image is not None and image.mode == "RGBA"
    assert image.getchannel("A").getbbox() is not None
    assert image.height <= 340 and image.width < 400    # 투명 여백을 잘라 냈다
    assert motif_sprite("없는 소재", tmp_path) is None


def test_general_purpose_motifs_added_on_2026_10_18_resolve_and_draw():
    """레퍼런스처럼 대상을 그림으로 보여 주려고 늘린 범용 소재(2026-10-18): 이름·별칭이 정확히 일치할 때만 쓰이고, 그려진다."""
    from app.services.slide_motifs import motif_labels, motif_sprite, resolve_motif

    assert len(motif_labels()) >= 49
    for alias, label in (("배당", "배당 동전"), ("아파트", "아파트 단지"), ("서류", "서류 더미"),
                         ("정부", "정부 청사"), ("금고", "금고"), ("분석 돋보기", "분석 돋보기")):
        assert resolve_motif(alias) == label
    sprite = motif_sprite("배당 동전", height=300)
    assert sprite is not None and sprite.height <= 300 and sprite.getchannel("A").getbbox()
    assert resolve_motif("없는소재") is None
