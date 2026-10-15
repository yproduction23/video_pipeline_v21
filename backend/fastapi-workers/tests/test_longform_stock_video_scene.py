"""2026-10-15 사용자 결정: 슬라이드형 모드의 Pixabay 매칭 장면은 "이미지 1장
+ 모션"이 아니라 그 자체로 완성된 영상 소재다. _process_scene()의 기존
경로는 모든 씬에 정지 이미지가 있다고 가정해 이런 씬을 처리하지 못했다 —
이 테스트는 stock_video_path가 있는 씬을 위한 새 분기만 확인한다. 기존
캐릭터/기사증거 경로는 이 분기를 타지 않는다(건드리지 않음)."""
from pathlib import Path

from app.workers.longform_worker import LongformWorker


def test_scene_with_stock_video_path_skips_image_requirement(monkeypatch, tmp_path):
    """stock_video_path가 있으면 image_path가 아예 없어도(또는 깨져 있어도)
    처리돼야 한다 — 기존 경로라면 `_verify_image` 실패로 전체가 막혔을
    상황이다."""
    worker = LongformWorker()
    scene = {
        "stock_video_path": "/app/data/jobs/1/slides/scene_000_stock.mp4",
        "duration": 4.0,
        # image_path를 일부러 넣지 않는다 — stock_video_path 경로는 이걸 요구하지 않는다.
    }
    temp_dir = tmp_path

    monkeypatch.setattr("app.workers.longform_worker.os.path.exists", lambda path: True)
    monkeypatch.setattr(
        "app.workers.longform_worker._ffmpeg_trim_scale_stock_video",
        lambda src, clip, duration, job_id=0: Path(clip).write_bytes(b"fake-mp4"),
    )
    monkeypatch.setattr("app.workers.longform_worker._verify_video", lambda path: True)
    monkeypatch.setattr("app.workers.longform_worker._apply_editorial_overlays", lambda *a, **kw: True)
    monkeypatch.setattr("app.workers.longform_worker.is_job_stopped", lambda job_id: False)
    monkeypatch.setattr("app.workers.longform_worker._can_reuse_scene_clip", lambda *a, **kw: False)

    index, clip_path, source_type, error = worker._process_scene(0, scene, None, False, temp_dir, job_id=1)

    assert source_type == "slide_stock_video"
    assert error is None
    assert clip_path == str(temp_dir / "clip_000.mp4")


def test_missing_stock_video_file_raises_instead_of_silently_skipping(monkeypatch, tmp_path):
    worker = LongformWorker()
    scene = {"stock_video_path": "/does/not/exist.mp4", "duration": 4.0}

    monkeypatch.setattr("app.workers.longform_worker.is_job_stopped", lambda job_id: False)
    monkeypatch.setattr("app.workers.longform_worker._can_reuse_scene_clip", lambda *a, **kw: False)
    monkeypatch.setattr("app.workers.longform_worker.os.path.exists", lambda path: False)

    try:
        worker._process_scene(0, scene, None, False, tmp_path, job_id=1)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "stock video missing" in str(exc)


def test_reuses_existing_clip_when_fingerprint_matches(monkeypatch, tmp_path):
    worker = LongformWorker()
    scene = {"stock_video_path": "/app/data/jobs/1/slides/scene_000_stock.mp4", "duration": 4.0}

    monkeypatch.setattr("app.workers.longform_worker.is_job_stopped", lambda job_id: False)
    monkeypatch.setattr("app.workers.longform_worker._can_reuse_scene_clip", lambda *a, **kw: True)

    called = {"trimmed": False}
    monkeypatch.setattr(
        "app.workers.longform_worker._ffmpeg_trim_scale_stock_video",
        lambda *a, **kw: called.__setitem__("trimmed", True),
    )

    index, clip_path, source_type, error = worker._process_scene(0, scene, None, False, tmp_path, job_id=1)

    assert source_type == "slide_stock_video"
    assert called["trimmed"] is False
