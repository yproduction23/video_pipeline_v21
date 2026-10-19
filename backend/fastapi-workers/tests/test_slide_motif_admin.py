"""연상 소재 라이브러리 관리(2026-10-19 운영자 요청): 목록·업로드·확인·삭제·설명으로 생성을 어드민 화면에서 한다."""
import base64
import io
import json

import pytest
from PIL import Image

from app.services import slide_motifs
from app.services.slide_motif_admin import MotifAdminError, add_uploaded, generate_from_description, image_path, list_motifs, set_review


def _library(tmp_path):
    directory = tmp_path / "motifs"
    directory.mkdir()
    (directory / "library.json").write_text("[]", encoding="utf-8")
    slide_motifs._library.cache_clear()
    return directory


def _png(transparent=True, size=(200, 200)):
    image = Image.new("RGBA", size, (0, 0, 0, 0) if transparent else (255, 255, 255, 255))
    from PIL import ImageDraw

    ImageDraw.Draw(image).rectangle((40, 40, 160, 160), fill=(255, 201, 51, 255))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def test_an_uploaded_transparent_png_is_registered_and_reusable_by_name_or_alias(tmp_path):
    directory = _library(tmp_path)

    result = add_uploaded("완충재", "쿠션, 충격 흡수", _png(), directory=directory)

    assert result["label"] == "완충재" and image_path(result["slug"], directory).is_file()
    assert slide_motifs.resolve_motif("쿠션", directory) == "완충재"
    row = list_motifs(directory)[0]
    assert row["review"] == "approved" and row["generator"] == "manual" and row["has_image"] is True


def test_uploads_with_a_background_a_duplicate_name_or_a_bad_size_are_refused(tmp_path):
    directory = _library(tmp_path)
    add_uploaded("완충재", "쿠션", _png(), directory=directory)

    with pytest.raises(MotifAdminError, match="투명 배경"):
        add_uploaded("방패막", "", _png(transparent=False), directory=directory)
    with pytest.raises(MotifAdminError, match="이미 다른 소재"):
        add_uploaded("쿠션", "", _png(), directory=directory)                       # 기존 별칭과 겹치면 엉뚱한 소재가 쓰인다
    with pytest.raises(MotifAdminError, match="2~12자"):
        add_uploaded("가", "", _png(), directory=directory)
    with pytest.raises(MotifAdminError, match="한 변"):
        add_uploaded("작은그림", "", _png(size=(20, 20)), directory=directory)


def test_pending_motifs_can_be_approved_or_deleted_with_their_file(tmp_path):
    directory = _library(tmp_path)
    slug = add_uploaded("충격파", "", _png(), directory=directory)["slug"]
    entries = json.loads((directory / "library.json").read_text(encoding="utf-8"))
    entries[0]["source"]["review"] = "pending"
    (directory / "library.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    assert list_motifs(directory)[0]["review"] == "pending"
    set_review(slug, "approve", directory)
    assert list_motifs(directory)[0]["review"] == "approved"
    set_review(slug, "delete", directory)
    assert list_motifs(directory) == [] and not (directory / f"{slug}.png").exists()
    with pytest.raises(MotifAdminError):
        set_review("없는소재", "delete", directory)


def test_generating_from_a_description_validates_the_text_and_uses_a_one_image_budget(tmp_path):
    directory = _library(tmp_path)
    with pytest.raises(MotifAdminError):
        generate_from_description("짧", directory=directory)

    seen = {}

    def fake_generate(subject, raw_path, *, job_id, scene_key):
        seen["subject"] = subject
        Image.new("RGB", (200, 200), (0, 255, 0)).save(raw_path)

    def naming(system, messages, max_tokens):
        import re

        slug = re.search(r"slug=(\S+)", messages[0]["content"]).group(1)          # 주문 슬러그를 그대로 받아 이름을 붙인다
        return json.dumps({"items": [{"slug": slug, "subject": "a soft cushion", "label": "완충재", "aliases": ["쿠션"], "skip": False}]})

    result = generate_from_description(
        "충격을 흡수하는 완충재", directory=directory, generate=fake_generate, llm_call=naming,
        vision_call=lambda s, t, i: json.dumps({"ok": True, "reason": "ok"}),
    )

    assert result["budget_krw"] == 339 and result["spent_krw"] == 339            # 장당 한 장만(예산이 한 장)
    assert seen["subject"] == "a soft cushion" and [c["label"] for c in result["created"]] == ["완충재"]
    assert slide_motifs.resolve_motif("쿠션", directory) == "완충재"                  # 만든 소재가 바로 쓰인다
