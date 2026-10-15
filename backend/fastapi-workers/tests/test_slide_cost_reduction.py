"""슬라이드형 Gemini 비용 절감 계약 (2026-10-16 운영자 요청).

19번 Job은 57장면에 Gemini를 112번 호출했다(46장면이 2~3회). 원인은 전체 재실행 때마다 같은
장면의 배경을 처음부터 다시 만든 것이었다. 세 가지를 고정한다:
1. 같은 내레이션의 배경 층(스톡·AI 배경)은 전체 재실행에서 재사용한다(단일 장면 다시 생성은 새로 만든다).
2. 인포그래픽이 화면을 덮는 오버레이 종류는 배경을 만들지 않는 설명 장면(칠판)이 기본이다.
3. 첫 검색어에 스톡이 없으면 대체 검색어를 더 시도한 뒤에야 AI 배경으로 넘어간다."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.scene_preparation import PreparedScenes
from app.services.slide_overlay import validate_overlay_plan
from app.workers.slides_worker import SlidesWorker


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: {})
    monkeypatch.setattr("app.workers.slides_worker._PIXABAY_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(
        SlidesWorker, "_disclaimer_scene_index", staticmethod(lambda scenes_meta, targets, highlight=None: None),
    )
    monkeypatch.setattr("app.workers.slides_worker.assess_visual_alignment", lambda scenes, **kw: {"reviewed": []})
    monkeypatch.setattr("app.workers.slides_worker.DATA_DIR", tmp_path)


def _scenes(*texts):
    return [{"content": t, "start_seconds": i * 4, "end_seconds": i * 4 + 4} for i, t in enumerate(texts)]


def _prepare(monkeypatch, scenes):
    monkeypatch.setattr(
        "app.workers.slides_worker.prepare_job_scenes",
        lambda **kw: PreparedScenes(scenes_meta=[dict(s) for s in scenes], script_data={}),
    )


class _Counter:
    def __init__(self):
        self.gemini = 0
        self.searches: list[str] = []
        self.downloads = 0


def _providers(monkeypatch, *, stock_by_query: dict[str, list[dict]] | None = None, direction=None):
    counter = _Counter()
    direction = direction or {"headline": "h", "pexels_query": "main"}
    monkeypatch.setattr("app.workers.slides_worker.generate_slide_direction", lambda narration: dict(direction))

    def search(query, **kw):
        counter.searches.append(query)
        return list((stock_by_query or {}).get(query, []))

    monkeypatch.setattr("app.workers.slides_worker.search_candidates", search)
    monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda n, c: 0 if c else None)

    def download(url, output_path):
        counter.downloads += 1
        Path(output_path).write_bytes(b"mp4")

    monkeypatch.setattr("app.workers.slides_worker.download_clip", download)

    def frame(video_path, output_path):
        Path(output_path).write_bytes(b"png")
        return True

    monkeypatch.setattr("app.workers.slides_worker._extract_first_frame", frame)

    def bg(prompt_en, output_path, *, job_id, scene_key):
        counter.gemini += 1
        Path(output_path).write_bytes(b"bg")

    monkeypatch.setattr("app.workers.slides_worker.generate_korean_slide_background", bg)
    return counter


def test_full_rerun_reuses_ai_backgrounds_instead_of_paying_gemini_again(monkeypatch):
    worker = SlidesWorker()
    _prepare(monkeypatch, _scenes("첫 장면입니다.", "둘째 장면입니다."))
    counter = _providers(monkeypatch)

    first = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")
    second = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert counter.gemini == 2
    assert all(s.get("base_reused") for s in second["scenes"])
    assert [s["image_path"] for s in second["scenes"]] == [s["image_path"] for s in first["scenes"]]


def test_changed_narration_gets_a_new_background(monkeypatch):
    worker = SlidesWorker()
    _prepare(monkeypatch, _scenes("원래 문장입니다."))
    counter = _providers(monkeypatch)
    worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    _prepare(monkeypatch, _scenes("고친 문장입니다."))
    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert counter.gemini == 2
    assert not result["scenes"][0].get("base_reused")


def test_single_scene_regeneration_always_makes_a_fresh_background(monkeypatch):
    worker = SlidesWorker()
    _prepare(monkeypatch, _scenes("첫 장면입니다.", "둘째 장면입니다."))
    counter = _providers(monkeypatch)
    worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}", scene_indices=[1])

    assert counter.gemini == 3
    assert not result["scenes"][0].get("base_reused")


def test_full_rerun_reuses_the_stock_clip_without_searching_again(monkeypatch):
    worker = SlidesWorker()
    _prepare(monkeypatch, _scenes("거리를 걷는 시민들."))
    candidate = {"pixabay_id": 5, "download_url": "https://x/v.mp4", "tags": "street"}
    counter = _providers(monkeypatch, stock_by_query={"korea main": [candidate]})
    worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")
    searches_after_first = len(counter.searches)

    second = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert len(counter.searches) == searches_after_first
    assert counter.downloads == 1
    assert second["scenes"][0]["visual_type"] == "slide_stock_video"
    assert second["scenes"][0]["base_reused"] is True
    assert counter.gemini == 0


def test_alternative_queries_find_stock_before_falling_back_to_gemini(monkeypatch):
    worker = SlidesWorker()
    _prepare(monkeypatch, _scenes("아파트 단지가 보입니다."))
    candidate = {"pixabay_id": 7, "download_url": "https://x/a.mp4", "tags": "apartment complex"}
    counter = _providers(
        monkeypatch,
        stock_by_query={"apartment complex": [candidate]},
        direction={"headline": "h", "pexels_query": "main", "alt_queries": ["city residential", "apartment complex"]},
    )

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert counter.searches == ["korea main", "main", "city residential", "apartment complex"]
    assert result["scenes"][0]["visual_type"] == "slide_stock_video"
    assert result["scenes"][0]["stock_query"] == "apartment complex"
    assert counter.gemini == 0


def test_search_stops_at_the_first_query_that_yields_an_accepted_clip(monkeypatch):
    worker = SlidesWorker()
    _prepare(monkeypatch, _scenes("시장 장면입니다."))
    candidate = {"pixabay_id": 3, "download_url": "https://x/m.mp4", "tags": "market"}
    counter = _providers(
        monkeypatch, stock_by_query={"main": [candidate]},
        direction={"headline": "h", "pexels_query": "main", "alt_queries": ["other"]},
    )

    worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert counter.searches == ["korea main", "main"]


def test_diagram_overlays_default_to_the_board_but_key_text_stays_on_footage():
    briefs = {
        0: {"narration": "15억 원에서 5억 원을 빼면 순자산 10억 원입니다."},
        1: {"narration": "코스피는 7.6퍼센트 하락했습니다."},
    }
    raw = [
        {"index": 0, "type": "equation", "left": ["15억 원", "5억 원"], "ops": ["−"], "result": "순자산 10억 원"},
        {"index": 1, "type": "key_text", "text": "코스피는 7.6퍼센트 하락"},
    ]

    plan = validate_overlay_plan(raw, briefs)

    assert plan[0]["explain"] is True
    assert plan[1]["explain"] is False


def test_explicit_explain_false_is_respected_for_diagram_overlays():
    briefs = {0: {"narration": "15억 원에서 5억 원을 빼면 순자산 10억 원입니다."}}
    raw = [
        {"index": 0, "type": "equation", "left": ["15억 원", "5억 원"], "ops": ["−"],
         "result": "순자산 10억 원", "explain": False},
    ]

    assert validate_overlay_plan(raw, briefs)[0]["explain"] is False
