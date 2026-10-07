from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

from app.services.final_frame_text_integrity import (
    FinalFrameTextIntegrityError,
    expected_final_frame_texts,
    inspect_final_frame_text_integrity,
    require_final_frame_text_integrity,
)
from app.workers.images_worker import DeterministicSurfaceMissingError, ImagesWorker


def _caption_scene() -> dict:
    return {
        "scene_id": "forecast-01",
        "v5_render_contract": {
            "visual_text_policy": "deterministic_surface_text",
            "primary_surface_region": [0.08, 0.12, 0.52, 0.40],
            "surface_caption": {
                "korean": "현재 전망\n수정 전망",
                "texts": ["현재 전망", "수정 전망"],
            },
        },
    }


def _save_blank_panel(path: Path) -> None:
    image = Image.new("RGB", (1280, 720), "#20354d")
    draw = ImageDraw.Draw(image)
    draw.rectangle((110, 85, 760, 390), fill="#d9edf7", outline="#091523", width=12)
    image.save(path)


def test_expected_text_prefers_verified_label_and_value_over_generic_caption():
    scene = _caption_scene()
    scene["v5_verified_overlays"] = [{"label": "영업이익", "value": "6조 8,130억원"}]

    assert expected_final_frame_texts(scene) == ["영업이익", "6조 8,130억원"]


def test_final_ocr_accepts_exact_approved_korean_across_rows(tmp_path: Path):
    report = inspect_final_frame_text_integrity(
        str(tmp_path / "unused.png"),
        _caption_scene(),
        ocr_rows=[{"text": "현재 전망", "conf": "94"}, {"text": "수정 전망", "conf": "92"}],
    )

    assert report["passed"] is True
    assert report["missing_or_altered"] == []


@pytest.mark.parametrize("recognized", ["현력 큰풍 수정 전망", "현재 전망 수정 계샥환"])
def test_final_ocr_rejects_korean_typo_or_gibberish(tmp_path: Path, recognized: str):
    with pytest.raises(FinalFrameTextIntegrityError):
        require_final_frame_text_integrity(
            str(tmp_path / "unused.png"),
            _caption_scene(),
            ocr_rows=[{"text": recognized, "conf": "96"}],
        )


def test_final_ocr_rejects_numeric_character_change(tmp_path: Path):
    scene = _caption_scene()
    scene["v5_verified_overlays"] = [{"label": "PER", "value": "4배"}]

    with pytest.raises(FinalFrameTextIntegrityError):
        require_final_frame_text_integrity(
            str(tmp_path / "unused.png"),
            scene,
            ocr_rows=[{"text": "PER 4X", "conf": "96"}],
        )


def test_worker_bakes_caption_on_existing_surface_without_ai_text(tmp_path: Path):
    image_path = tmp_path / "scene.png"
    _save_blank_panel(image_path)
    before = Image.open(image_path).copy()

    ImagesWorker()._apply_deterministic_surface_caption(_caption_scene(), str(image_path))

    after = Image.open(image_path).convert("RGB")
    assert ImageChops.difference(before.convert("RGB"), after).getbbox() is not None


def test_exact_deterministic_pixel_provenance_can_override_tesseract_misread(tmp_path: Path):
    image_path = tmp_path / "scene.png"
    _save_blank_panel(image_path)
    scene = _caption_scene()
    ImagesWorker()._apply_deterministic_surface_caption(scene, str(image_path))

    report = inspect_final_frame_text_integrity(
        str(image_path),
        scene,
        ocr_rows=[{"text": "현력 큰풍 수정 계샥환", "conf": "96"}],
    )

    assert report["passed"] is True
    assert report["ocr_passed"] is False
    assert report["deterministic_provenance"]["passed"] is True


def test_tampered_render_region_cannot_use_deterministic_provenance(tmp_path: Path):
    image_path = tmp_path / "scene.png"
    _save_blank_panel(image_path)
    scene = _caption_scene()
    ImagesWorker()._apply_deterministic_surface_caption(scene, str(image_path))
    left, top, _, _ = scene["deterministic_text_regions"][0]["bbox"]
    with Image.open(image_path) as source:
        changed = source.convert("RGB")
    changed.putpixel((left, top), (255, 0, 0))
    changed.save(image_path)

    with pytest.raises(FinalFrameTextIntegrityError):
        require_final_frame_text_integrity(
            str(image_path),
            scene,
            ocr_rows=[{"text": "현력 큰풍 수정 계샥환", "conf": "96"}],
        )


def test_worker_refuses_static_coordinate_fallback_when_no_physical_surface_exists(tmp_path: Path):
    image_path = tmp_path / "open-background.png"
    Image.new("RGB", (1280, 720), "#d9edf7").save(image_path)

    with pytest.raises(DeterministicSurfaceMissingError):
        ImagesWorker()._apply_deterministic_surface_caption(_caption_scene(), str(image_path))


def test_deterministic_caption_uses_the_planned_surface_kind(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "scene.png"
    Image.new("RGB", (1280, 720), "#20354d").save(image_path)
    scene = _caption_scene()
    scene["v5_render_contract"]["surface_caption"]["surface_plan"] = [{
        "text": "PER 4배",
        "surface": "balance_scale_plinth",
        "surface_id": "balance_scale_plinth",
        "surface_kind": "prop_panel",
    }]
    scene["v5_render_contract"]["surface_caption"]["korean"] = "PER 4배"
    seen = {}

    def fake_detect(path, kind, **kwargs):
        seen["kind"] = kind
        return None

    monkeypatch.setattr("app.services.overlay.surface_detector.detect_surface_for_plan", fake_detect)
    with pytest.raises(DeterministicSurfaceMissingError):
        ImagesWorker()._apply_deterministic_surface_caption(scene, str(image_path))
    assert seen["kind"] == "prop_panel"


def test_deterministic_caption_uses_the_planned_surface_kind_for_non_numeric_quotes(monkeypatch, tmp_path: Path):
    """2026-10-07 사용자 재현(job 14, 씬 0): "저 군대 안 갑니다!"처럼 숫자가
    전혀 없는 승인 인용문의 surface_plan 항목이, 숫자 포함 여부만 보던 필터
    때문에 통째로 버려지고 범용 "board"로 폴백했다. 숫자 유무와 무관하게
    승인 문구가 있는 계획 항목은 그대로 전달돼야 한다."""
    image_path = tmp_path / "scene.png"
    Image.new("RGB", (1280, 720), "#20354d").save(image_path)
    scene = _caption_scene()
    scene["v5_render_contract"]["surface_caption"]["surface_plan"] = [{
        "text": "저 군대 안 갑니다!",
        "surface": "context_sign_1",
        "surface_id": "context_sign_1",
        "surface_kind": "signboard",
    }]
    scene["v5_render_contract"]["surface_caption"]["korean"] = "저 군대 안 갑니다!"
    seen = {}

    def fake_detect(path, kind, **kwargs):
        seen["kind"] = kind
        return None

    monkeypatch.setattr("app.services.overlay.surface_detector.detect_surface_for_plan", fake_detect)
    with pytest.raises(DeterministicSurfaceMissingError):
        ImagesWorker()._apply_deterministic_surface_caption(scene, str(image_path))
    assert seen["kind"] == "signboard"


def test_deterministic_caption_passes_semantic_plan_to_surface_detector(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "scene.png"
    _save_blank_panel(image_path)
    scene = _caption_scene()
    plan_item = {
        "text": "PER 4배",
        "surface": "balance_scale_plinth",
        "surface_id": "balance_scale_plinth",
        "surface_kind": "prop_panel",
        "locator_region": [0.18, 0.62, 0.42, 0.36],
        "surface_description": "front-facing inset label plate on the scale base",
    }
    scene["v5_render_contract"]["surface_caption"]["surface_plan"] = [plan_item]
    scene["v5_render_contract"]["surface_caption"]["korean"] = "PER 4배"
    seen = {}

    def fake_detect(path, kind, *, plan_item=None, **kwargs):
        seen["plan_item"] = plan_item
        return None

    monkeypatch.setattr("app.services.overlay.surface_detector.detect_surface_for_plan", fake_detect)
    with pytest.raises(DeterministicSurfaceMissingError):
        ImagesWorker()._apply_deterministic_surface_caption(scene, str(image_path))

    assert seen["plan_item"] == plan_item
