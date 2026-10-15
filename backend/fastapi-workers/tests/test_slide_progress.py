"""씬별 생성 진행 상황: 장면 분할 개수를 먼저 알리고, 끝난 장면을 하나씩 쌓아 보여준다."""
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.services.slide_progress import SlideProgress, read_progress

client = TestClient(main_module.app)


def test_start_publishes_total_and_pending_scenes_then_scene_done_accumulates(tmp_path):
    progress = SlideProgress(7, tmp_path)
    progress.preparing()
    assert read_progress(7, tmp_path)["status"] == "preparing"

    progress.start([{"index": 0, "text": "첫 장면"}, {"index": 1, "text": "둘째 장면"}])
    state = read_progress(7, tmp_path)
    assert state["total"] == 2 and state["done"] == 0
    assert [s["status"] for s in state["scenes"]] == ["pending", "pending"]

    image = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), (10, 20, 30)).save(image)
    progress.scene_done(1, {"image_path": str(image), "visual_type": "slide_stock_video", "overlay_type": "stat_chart"})

    state = read_progress(7, tmp_path)
    assert state["done"] == 1
    done_scene = next(s for s in state["scenes"] if s["index"] == 1)
    assert done_scene["status"] == "done"
    assert done_scene["has_thumb"] is True
    assert done_scene["overlay_type"] == "stat_chart"
    assert (tmp_path / "jobs" / "7" / "slides" / "thumb_001.jpg").is_file()


def test_failed_scene_is_reported_and_counted_as_finished(tmp_path):
    progress = SlideProgress(7, tmp_path)
    progress.start([{"index": 0, "text": "장면"}])
    progress.scene_done(0, {"slide_generation_error": "Gemini 보류"})

    state = read_progress(7, tmp_path)
    assert state["scenes"][0]["status"] == "failed"
    assert state["scenes"][0]["error"] == "Gemini 보류"
    assert state["done"] == 1


def test_preparing_clears_the_previous_run(tmp_path):
    progress = SlideProgress(7, tmp_path)
    progress.start([{"index": 0, "text": "이전 실행"}])
    progress.finish("completed")

    SlideProgress(7, tmp_path).preparing()

    state = read_progress(7, tmp_path)
    assert state["status"] == "preparing" and state["scenes"] == [] and state["total"] == 0


def test_progress_endpoint_returns_none_state_without_file(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "DATA_DIR", tmp_path)

    body = client.get("/workers/slides/progress/999").json()

    assert body["status"] == "none" and body["total"] == 0


def test_progress_and_thumb_endpoints_serve_written_state(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "DATA_DIR", tmp_path)
    progress = SlideProgress(5, tmp_path)
    progress.start([{"index": 0, "text": "장면"}])
    image = Path(tmp_path) / "x.png"
    Image.new("RGB", (640, 360), (1, 2, 3)).save(image)
    progress.scene_done(0, {"image_path": str(image), "visual_type": "slide_ai_background"})

    assert client.get("/workers/slides/progress/5").json()["done"] == 1
    thumb = client.get("/workers/slides/progress/5/thumb/0")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert client.get("/workers/slides/progress/5/thumb/9").status_code == 404


def test_partial_regeneration_keeps_the_full_scene_list(tmp_path):
    first = SlideProgress(7, tmp_path)
    first.start([{"index": 0, "text": "a"}, {"index": 1, "text": "b"}, {"index": 2, "text": "c"}])
    image = tmp_path / "x.png"
    Image.new("RGB", (640, 360), (1, 2, 3)).save(image)
    first.scene_done(0, {"image_path": str(image), "visual_type": "slide_ai_background"})
    first.scene_done(1, {"slide_generation_error": "실패"})
    first.finish("completed")

    again = SlideProgress(7, tmp_path, keep_existing=True)
    again.preparing()
    again.start([{"index": 1, "text": "b"}])

    state = read_progress(7, tmp_path)
    assert state["total"] == 3
    by_index = {s["index"]: s["status"] for s in state["scenes"]}
    assert by_index == {0: "done", 1: "pending", 2: "pending"}
    assert state["done"] == 1


def test_scene_media_endpoint_serves_the_final_clip_and_blocks_foreign_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "DATA_DIR", tmp_path)
    progress = SlideProgress(8, tmp_path)
    progress.start([{"index": 0, "text": "영상 장면"}, {"index": 1, "text": "이미지 장면"}, {"index": 2, "text": "외부"}])
    slides = tmp_path / "jobs" / "8" / "slides"
    slides.mkdir(parents=True, exist_ok=True)
    clip = slides / "scene_000_composed.mp4"
    clip.write_bytes(b"fake-mp4")
    still = slides / "scene_001_bg.png"
    Image.new("RGB", (64, 36), (1, 2, 3)).save(still)
    outside = tmp_path / "secret.png"
    Image.new("RGB", (64, 36), (9, 9, 9)).save(outside)
    progress.scene_done(0, {"image_path": str(still), "stock_video_path": str(clip), "visual_type": "slide_stock_video"})
    progress.scene_done(1, {"image_path": str(still), "visual_type": "slide_ai_background"})
    progress.scene_done(2, {"image_path": str(outside), "visual_type": "slide_ai_background"})

    video = client.get("/workers/slides/progress/8/scene/0")
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    image = client.get("/workers/slides/progress/8/scene/1")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.get("/workers/slides/progress/8/scene/2").status_code == 404
    assert client.get("/workers/slides/progress/8/scene/9").status_code == 404
