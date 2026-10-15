"""2026-10-13 사용자 요청: 자동 게이트가 보류시킨 장면도 사용자가 실물 후보
이미지를 직접 보고 괜찮다고 판단하면, 재생성 없이 그대로 승인할 수 있어야
한다 — job 14 씬 0,1,3,4,5,6,7,8이 전부 "장면 누적 요청 상한 도달"로만
보류됐는데 이미지 자체는 이미 괜찮아 보이는 상황에서 나온 요구사항이다."""
import json
from pathlib import Path

import pytest
from PIL import Image

from app.utils.image_request_control import ImageRequestHeld, assert_request_review_cleared, write_request_review
from app.workers.images_worker import accept_held_scene_as_is


def _make_job_dir(tmp_path: Path, job_id: int) -> Path:
    job_dir = tmp_path / str(job_id) / "images"
    job_dir.mkdir(parents=True)
    return job_dir


def test_accept_held_scene_promotes_candidate_and_clears_the_review_gate(tmp_path):
    job_dir = _make_job_dir(tmp_path, 14)
    candidate = job_dir / "scene_002_rejected.png"
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    write_request_review(job_dir, 14, [
        {"index": 1, "status": "needs_review", "reason": "다른 장면", "image_path": None},
        {"index": 2, "status": "needs_review", "reason": "visual_quality_floor", "image_path": str(candidate)},
    ])

    result = accept_held_scene_as_is(14, 2, text="전역모 승인 내레이션 원문", accepted_by="jam", root=tmp_path)

    final_path = job_dir / "scene_002.png"
    assert final_path.is_file()
    assert final_path.read_bytes() == candidate.read_bytes()
    assert result["scenes"][0]["image_path"] == str(final_path)
    assert result["scenes"][0]["text"] == "전역모 승인 내레이션 원문"
    assert result["scenes"][0]["generation_method"] == "manual_accept_held"
    assert [scene["index"] for scene in result["held_scenes"]] == [1]
    assert result["requires_manual_review"] is True

    review = json.loads((job_dir / "image_request_review.json").read_text(encoding="utf-8"))
    assert [scene["index"] for scene in review["scenes"]] == [1]


def test_accept_held_scene_records_an_audit_trail(tmp_path):
    job_dir = _make_job_dir(tmp_path, 14)
    candidate = job_dir / "scene_002_rejected.png"
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    write_request_review(job_dir, 14, [
        {"index": 2, "status": "needs_review", "reason": "visual_quality_floor: 71점", "image_path": str(candidate)},
    ])

    accept_held_scene_as_is(14, 2, text="씬2 내레이션", accepted_by="jam", root=tmp_path)

    overrides = json.loads((job_dir / "manual_scene_overrides.json").read_text(encoding="utf-8"))
    assert len(overrides) == 1
    record = overrides[0]
    assert record["index"] == 2
    assert record["accepted_by"] == "jam"
    assert record["original_reason"] == "visual_quality_floor: 71점"
    assert record["image_sha256"]


def test_accept_held_scene_unblocks_assembly_once_it_was_the_last_held_scene(tmp_path):
    job_dir = _make_job_dir(tmp_path, 14)
    candidate = job_dir / "scene_002_rejected.png"
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    write_request_review(job_dir, 14, [
        {"index": 2, "status": "needs_review", "reason": "visual_quality_floor", "image_path": str(candidate)},
    ])
    with pytest.raises(ImageRequestHeld, match="Fal/조립"):
        assert_request_review_cleared(14, root=tmp_path)

    accept_held_scene_as_is(14, 2, text="씬2 내레이션", root=tmp_path)

    assert_request_review_cleared(14, root=tmp_path)  # no longer raises


def test_accept_held_scene_falls_back_to_last_candidate_when_entry_has_no_image_path(tmp_path):
    job_dir = _make_job_dir(tmp_path, 14)
    candidate = job_dir / "scene_006_last_candidate.png"
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    write_request_review(job_dir, 14, [
        {"index": 6, "status": "needs_review", "reason": "장면 누적 요청 상한 도달"},
    ])

    result = accept_held_scene_as_is(14, 6, text="씬6 내레이션", root=tmp_path)

    assert (job_dir / "scene_006.png").is_file()
    assert result["held_scenes"] == []


def test_accept_held_scene_rejects_an_index_that_is_not_currently_held(tmp_path):
    job_dir = _make_job_dir(tmp_path, 14)
    write_request_review(job_dir, 14, [{"index": 1, "status": "needs_review", "reason": "x"}])

    with pytest.raises(ImageRequestHeld, match="검토 대기 목록"):
        accept_held_scene_as_is(14, 2, text="씬2 내레이션", root=tmp_path)


def test_accept_held_scene_rejects_when_no_candidate_file_exists_anywhere(tmp_path):
    job_dir = _make_job_dir(tmp_path, 14)
    write_request_review(job_dir, 14, [
        {"index": 2, "status": "needs_review", "reason": "장면 누적 요청 상한 도달"},
    ])

    with pytest.raises(ImageRequestHeld, match="후보 이미지 파일을 찾을 수 없습니다"):
        accept_held_scene_as_is(14, 2, text="씬2 내레이션", root=tmp_path)


def test_accept_held_scene_rejects_when_no_review_gate_file_exists(tmp_path):
    _make_job_dir(tmp_path, 14)

    with pytest.raises(ImageRequestHeld, match="검토 대기 상태가 없습니다"):
        accept_held_scene_as_is(14, 2, text="씬2 내레이션", root=tmp_path)


def test_accept_held_scene_rejects_blank_narration_text(tmp_path):
    """2026-10-13 job 14 씬5 재현: 내레이션 원문 없이 승인하면 Spring이 저장하는
    SCENE_IMAGE 자산의 text가 비어, longform 조립 직전 "대본 원문과 장면
    내레이션 불일치"로 전체 조립이 막힌다. 승인 시점에 바로 막아야 한다."""
    job_dir = _make_job_dir(tmp_path, 14)
    candidate = job_dir / "scene_005_rejected.png"
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    write_request_review(job_dir, 14, [
        {"index": 5, "status": "needs_review", "reason": "x", "image_path": str(candidate)},
    ])

    with pytest.raises(ImageRequestHeld, match="내레이션 원문이 비어"):
        accept_held_scene_as_is(14, 5, text="   ", root=tmp_path)
