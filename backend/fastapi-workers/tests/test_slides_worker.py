"""SlidesWorker의 층 구조(배경 층: 스톡 → (Kling) → Gemini / 오버레이 층: 기사 카드·통계 그래프)와
images_worker.py와 동일한 출력 계약(scenes/scene_count/...)을 확인한다.
실제 네트워크 호출(Pixabay/Claude/Gemini/Fal)은 전부 모킹한다."""
from pathlib import Path

import pytest

from app.services.scene_preparation import PreparedScenes
from app.workers.slides_worker import SlidesWorker


_REAL_DISCLAIMER_INDEX = SlidesWorker.__dict__["_disclaimer_scene_index"]


def _prepared(scenes):
    return PreparedScenes(scenes_meta=scenes, script_data={})


@pytest.fixture(autouse=True)
def _no_real_overlay_planning(monkeypatch):
    """오버레이 계획은 실제 Claude를 부르므로 기본적으로 빈 계획으로 막는다."""
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: {})
    # 기존 테스트는 계획서 없이 즉석 계획 경로를 검증한다(계획서 경로는 test_slide_edit_plan.py).
    monkeypatch.setattr("app.workers.slides_worker.build_edit_plan", lambda *a, **k: None)
    monkeypatch.setattr("app.workers.slides_worker._PIXABAY_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(
        SlidesWorker, "_disclaimer_scene_index", staticmethod(lambda scenes_meta, targets, highlight=None: None),
    )


def _stub_base_layer(monkeypatch, tmp_path, *, stock=False):
    """모든 장면에 배경 층이 먼저 깔린다 — 스톡 매칭 실패(기본) 또는 성공을 모킹한다."""
    monkeypatch.setattr(
        "app.workers.slides_worker.generate_slide_direction",
        lambda narration: {"headline": "h", "pexels_query": "q"},
    )
    if stock:
        monkeypatch.setattr(
            "app.workers.slides_worker.search_candidates",
            lambda query, **kw: [{"pixabay_id": 9, "download_url": "https://example/v.mp4", "tags": "t"}],
        )
        monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda n, c: 0)

        def fake_download(url, output_path):
            Path(output_path).write_bytes(b"fake-mp4")

        def fake_frame(video_path, output_path):
            Path(output_path).write_bytes(b"png")
            return True

        monkeypatch.setattr("app.workers.slides_worker.download_clip", fake_download)
        monkeypatch.setattr("app.workers.slides_worker._extract_first_frame", fake_frame)
    else:
        monkeypatch.setattr("app.workers.slides_worker.search_candidates", lambda query, **kw: [])
        monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda n, c: None)

    def fake_bg(prompt_en, output_path, *, job_id, scene_key):
        Path(output_path).write_bytes(b"fake-bg")

    monkeypatch.setattr("app.workers.slides_worker.generate_korean_slide_background", fake_bg)
    monkeypatch.setattr("app.workers.slides_worker.assess_visual_alignment", lambda scenes, **kw: {"reviewed": []})
    monkeypatch.setattr("app.workers.slides_worker.DATA_DIR", tmp_path)


def _fake_composer(monkeypatch, seen=None):
    """오버레이 PNG 렌더와 애니메이션 합성을 가짜로 바꾼다. 합성은 mp4 파일을 만든다."""
    seen = seen if seen is not None else {}

    def write_overlay(name):
        def fake(*, output_path, **kw):
            seen.setdefault("rendered", []).append((name, kw))
            Path(output_path).write_bytes(b"fake-overlay")
            return True
        return fake

    def fake_compose(*, base_path, overlay_path, output_path, base_is_video, **kw):
        seen.setdefault("composed", []).append({"base": base_path, "is_video": base_is_video, **kw})
        Path(output_path).write_bytes(b"fake-composed-mp4")
        return True

    def fake_frame(video_path, output_path, seconds):
        Path(output_path).write_bytes(b"png")
        return True

    def layered(name, count):
        def fake(*, output_path, **kw):
            seen.setdefault("rendered", []).append((name, kw))
            paths = []
            for k in range(count):
                path = output_path if k == 0 else output_path.replace(".png", f"_l{k}.png")
                Path(path).write_bytes(b"fake-overlay")
                paths.append(path)
            return paths
        return fake

    monkeypatch.setattr("app.workers.slides_worker.render_compare_numbers_layers", layered("compare", 3))
    monkeypatch.setattr("app.workers.slides_worker.render_flow_layers", layered("flow", 2))
    monkeypatch.setattr("app.workers.slides_worker.render_big_number_overlay", layered("big", 1))
    monkeypatch.setattr("app.workers.slides_worker.render_equation_overlay", write_overlay("render_equation_overlay"))
    for name in ("render_news_clip_overlay", "render_stat_overlay", "render_key_text_overlay",
                 "render_bullet_overlay"):
        monkeypatch.setattr(f"app.workers.slides_worker.{name}", write_overlay(name))
    monkeypatch.setattr("app.workers.slides_worker.compose_animated_overlay", fake_compose)
    monkeypatch.setattr("app.workers.slides_worker._extract_first_frame_at", fake_frame)
    return seen


def test_article_card_is_planned_by_claude_and_composed_over_the_base(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {
        "content": "전자발찌 착용 중 범행을 저질렀습니다.",
        "start_seconds": 0, "end_seconds": 4,
        "article_capture": {"publisher": "연합뉴스", "source_title": "제목", "quote": "인용문"},
    }
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path)
    seen = _fake_composer(monkeypatch)
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays",
                        lambda briefs, **kw: {0: {"type": "article_card"}})

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert out_scene["visual_type"] == "slide_article_card"
    assert out_scene["overlay_type"] == "article_card"
    assert out_scene["stock_video_path"].endswith("_composed.mp4")
    assert seen["composed"][0]["base"].endswith("_bg.png") and seen["composed"][0]["is_video"] is False
    layers = out_scene["slide_layers"]
    assert layers["base"]["type"] == "slide_ai_background"
    assert layers["overlays"][0]["type"] == "article_card"
    assert out_scene["_deterministic_scene_prepared"] is True
    assert out_scene["use_kling"] is False


def test_article_scene_without_a_plan_entry_keeps_only_the_base_layer(monkeypatch, tmp_path):
    """"상황에 맞게": 기사 증거가 있어도 Claude가 고르지 않으면 카드를 억지로 넣지 않는다."""
    worker = SlidesWorker()
    scene = {
        "content": "문장입니다.", "start_seconds": 0, "end_seconds": 4,
        "article_capture": {"publisher": "연합뉴스", "source_title": "제목", "quote": "인용문"},
    }
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path)
    seen = _fake_composer(monkeypatch)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert "overlay_type" not in out_scene
    assert out_scene["image_path"].endswith("_bg.png")
    assert out_scene["slide_layers"]["overlays"] == []
    assert "composed" not in seen


def test_key_text_and_bullet_list_overlays_are_applied_over_stock_video(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scenes = [
        {"content": "외국인이 4조 5천억 원을 던졌습니다.", "start_seconds": 0, "end_seconds": 4},
        {"content": "금리, 환율, 자사주가 원인입니다.", "start_seconds": 4, "end_seconds": 8},
    ]
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared(scenes))
    _stub_base_layer(monkeypatch, tmp_path, stock=False)
    seen = _fake_composer(monkeypatch)
    plan = {
        0: {"type": "key_text", "text": "4조 5천억 원을 던졌습니다"},
        1: {"type": "bullet_list", "title": "하락 원인", "items": ["금리", "환율", "자사주"]},
    }
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: plan)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert [s["overlay_type"] for s in result["scenes"]] == ["key_text", "bullet_list"]
    assert [s["visual_type"] for s in result["scenes"]] == ["slide_key_text", "slide_bullet_list"]
    assert sorted(n for n, _ in seen["rendered"]) == ["render_bullet_overlay", "render_key_text_overlay"]


def test_chart_plan_on_stock_video_composes_over_the_video(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {"content": "코스피는 7.6퍼센트, S&P500은 1.1퍼센트 하락했습니다.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path, stock=True)
    seen = _fake_composer(monkeypatch)
    plan = {0: {"type": "stat_chart", "title": "낙폭 비교", "items": [
        {"label": "코스피", "value_text": "7.6퍼센트", "value": 7.6, "unit": "퍼센트"},
        {"label": "S&P500", "value_text": "1.1퍼센트", "value": 1.1, "unit": "퍼센트"},
    ]}}
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: plan)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert out_scene["overlay_type"] == "stat_chart"
    assert seen["composed"][0]["is_video"] is True and seen["composed"][0]["base"].endswith("_stock.mp4")
    assert out_scene["slide_layers"]["base"]["video_path"].endswith("_stock.mp4")


def test_overlay_failure_keeps_base_and_is_flagged_for_review(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {
        "content": "문장입니다.", "start_seconds": 0, "end_seconds": 4,
        "article_capture": {"publisher": "연합뉴스", "source_title": "제목", "quote": "인용문"},
    }
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path)
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays",
                        lambda briefs, **kw: {0: {"type": "article_card"}})
    monkeypatch.setattr("app.workers.slides_worker.render_news_clip_overlay", lambda **kw: False)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert out_scene["image_path"].endswith("_bg.png")
    assert "slide_generation_error" not in out_scene
    assert "overlay_failed_base_only" in out_scene["quality_flags"]
    assert result["requires_manual_review"] is True


def test_overlay_plan_failure_is_not_silent(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {"content": "문장입니다.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path)
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: None)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert result["requires_manual_review"] is True
    assert any("오버레이 계획 생성 실패" in r for r in result["review_reasons"])
    assert result["scenes"][0]["image_path"].endswith("_bg.png")


def test_disclaimer_is_a_one_second_full_screen_card_with_a_sound_effect_on_the_first_scene_after_the_highlight(
    monkeypatch, tmp_path,
):
    """2026-10-16 운영자 요청: 고지 문구를 상단 띠가 아니라 화면 가운데에 1초쯤 보여 주며 효과음을 넣고 넘어간다(편집점 느낌)."""
    worker = SlidesWorker()
    scenes = [
        {"content": f"장면 {i}", "start_seconds": i * 4, "end_seconds": i * 4 + 4,
         "section": "intro" if i < 3 else "background"}
        for i in range(5)
    ]
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared(scenes))
    _stub_base_layer(monkeypatch, tmp_path)
    seen = _fake_composer(monkeypatch)

    def fake_card(*, output_path, **kw):
        Path(output_path).write_bytes(b"card")
        return True

    monkeypatch.setattr("app.workers.slides_worker.render_disclaimer_card", fake_card)
    monkeypatch.setattr(SlidesWorker, "_disclaimer_scene_index", _REAL_DISCLAIMER_INDEX)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    carded = [s["index"] for s in result["scenes"] if s.get("disclaimer_banner")]
    assert carded == [3]  # 한 장면에서 끝난다(8초 띠가 아니다)
    scene = result["scenes"][3]
    assert scene["slide_layers"]["overlays"][-1]["type"] == "disclaimer_card"
    assert scene["sfx_events"] == [{"name": "title_hit", "at": 12.0}]  # 장면 시작 시각에 효과음
    assert len(seen["composed"]) == 1
    composed = seen["composed"][0]
    assert composed["start_offset"] == 0.0 and composed["hold_seconds"] == 1.3 and composed["motion"] == "none"


def _highlight_scenes():
    # 하이라이트 3문장(0~2번 장면) 뒤에 본론. section 값은 일부러 모두 intro로 둬서
    # 대본의 하이라이트 계약이 우선하는지 확인한다.
    texts = ["핵심 결과 하나입니다.", "핵심 결과 둘입니다.", "결론부터 말씀드리겠습니다.", "먼저 배경부터 보죠.", "본론 둘째입니다."]
    return [{"content": t, "section": "intro"} for t in texts]


def test_disclaimer_banner_follows_highlight_end_text_from_script_contract():
    highlight = {"applicable": True, "present": True, "end_text": "결론부터 말씀드리겠습니다.", "end_char": 999}

    index = _REAL_DISCLAIMER_INDEX.__func__(_highlight_scenes(), {0, 1, 2, 3, 4}, highlight)

    assert index == 3


def test_disclaimer_banner_falls_back_to_highlight_char_count_when_text_is_edited():
    scenes = _highlight_scenes()
    consumed = sum(len(s["content"].replace(" ", "")) for s in scenes[:3])
    highlight = {"applicable": True, "present": True, "end_text": "사용자가 고친 문장", "end_char": consumed}

    index = _REAL_DISCLAIMER_INDEX.__func__(scenes, {0, 1, 2, 3, 4}, highlight)

    assert index == 3


def test_disclaimer_banner_ignores_character_mode_highlight_contract():
    scenes = [
        {"content": f"장면 {i}", "section": "intro" if i < 2 else "background"} for i in range(6)
    ]
    highlight = {"version": "slide-highlight-v1", "applicable": False}

    assert SlidesWorker._highlight_boundary('{"highlight": {"applicable": false}}') is None
    assert _REAL_DISCLAIMER_INDEX.__func__(scenes, set(range(6)), highlight) == 2


def test_highlight_boundary_reads_script_meta_and_tolerates_garbage():
    meta = '{"highlight": {"applicable": true, "present": true, "end_text": "마지막"}}'

    assert SlidesWorker._highlight_boundary(meta)["end_text"] == "마지막"
    assert SlidesWorker._highlight_boundary("not json") is None
    assert SlidesWorker._highlight_boundary(None) is None


def test_pixabay_match_sets_stock_video_path_and_frame(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {"content": "거리를 걷는 시민들.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    monkeypatch.setattr(
        "app.workers.slides_worker.generate_slide_direction",
        lambda narration: {"headline": "h", "pexels_query": "street"},
    )
    candidate = {"pixabay_id": 42, "download_url": "https://example/video.mp4", "tags": "street"}
    monkeypatch.setattr("app.workers.slides_worker.search_candidates", lambda query, **kw: [candidate])
    monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda narration, candidates: 0)

    downloaded = {}

    def fake_download_clip(url, output_path):
        downloaded["url"] = url
        Path(output_path).write_bytes(b"fake-mp4")

    monkeypatch.setattr("app.workers.slides_worker.download_clip", fake_download_clip)
    monkeypatch.setattr("app.workers.slides_worker._extract_first_frame", lambda video_path, output_path: False)
    monkeypatch.setattr("app.workers.slides_worker.assess_visual_alignment", lambda scenes, **kw: {"reviewed": []})
    monkeypatch.setattr("app.workers.slides_worker.DATA_DIR", tmp_path)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert out_scene["visual_type"] == "slide_stock_video"
    assert out_scene["pixabay_id"] == 42
    assert downloaded["url"] == "https://example/video.mp4"
    assert out_scene["stock_video_path"].endswith("_stock.mp4")


def test_same_stock_clip_is_not_reused_across_parallel_scenes(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scenes = [
        {"content": f"장면 {i} 입니다.", "start_seconds": i * 4, "end_seconds": i * 4 + 4}
        for i in range(4)
    ]
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared(scenes))
    _stub_base_layer(monkeypatch, tmp_path, stock=True)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    types = [s["visual_type"] for s in result["scenes"]]
    assert types.count("slide_stock_video") == 1
    assert types.count("slide_ai_background") == 3
    assert [s["index"] for s in result["scenes"]] == [0, 1, 2, 3]


def test_no_pixabay_match_falls_back_to_ai_background(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {"content": "흉기를 묶은 채 마트에 들어섰습니다.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    monkeypatch.setattr(
        "app.workers.slides_worker.generate_slide_direction",
        lambda narration: {"headline": "h", "pexels_query": "market entrance"},
    )
    monkeypatch.setattr("app.workers.slides_worker.search_candidates", lambda query, **kw: [])
    monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda narration, candidates: None)

    ai_calls = {}

    def fake_generate_korean_slide_background(prompt_en, output_path, *, job_id, scene_key):
        ai_calls.update(prompt_en=prompt_en, job_id=job_id, scene_key=scene_key)
        Path(output_path).write_bytes(b"fake-png")

    monkeypatch.setattr("app.workers.slides_worker.generate_korean_slide_background", fake_generate_korean_slide_background)
    monkeypatch.setattr("app.workers.slides_worker.assess_visual_alignment", lambda scenes, **kw: {"reviewed": []})
    monkeypatch.setattr("app.workers.slides_worker.DATA_DIR", tmp_path)
    # Kling이 실패하면 정지 이미지가 안전망이다(사유는 장면에 남는다).
    monkeypatch.setitem(__import__("app.runtime_config", fromlist=["_state"])._state, "slide_kling_enabled", True)
    monkeypatch.setattr("app.providers.factory.get_video_provider", lambda: object())
    monkeypatch.setattr(
        "app.workers.slides_worker.generate_slide_kling_clip",
        lambda **kw: {"ok": False, "reason": "test_kling_unavailable"},
    )

    result = worker.generate(job_id=7, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert out_scene["visual_type"] == "slide_ai_background"
    assert "마트에 들어섰습니다" in ai_calls["prompt_en"]
    assert ai_calls["job_id"] == 7
    assert out_scene["kling_failure_reason"] == "test_kling_unavailable"
    assert result["requires_manual_review"] is True


def _stock_miss_worker(monkeypatch, tmp_path, scene_count, kling_results):
    """스톡 매칭이 전부 실패하는 장면 N개. kling_results는 Kling 호출마다 반환값."""
    worker = SlidesWorker()
    scenes = [
        {"content": f"장면 {i} 내레이션입니다.", "start_seconds": i * 4, "end_seconds": i * 4 + 4}
        for i in range(scene_count)
    ]
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared(scenes))
    _stub_base_layer(monkeypatch, tmp_path)
    monkeypatch.setitem(__import__("app.runtime_config", fromlist=["_state"])._state, "slide_kling_enabled", True)
    monkeypatch.setattr("app.providers.factory.get_video_provider", lambda: object())

    calls = []
    results = iter(kling_results)

    def fake_kling(**kw):
        calls.append(kw["scene_key"])
        result = next(results)
        if result["ok"]:
            Path(kw["output_path"]).write_bytes(b"fake-mp4")
        return result

    monkeypatch.setattr("app.workers.slides_worker.generate_slide_kling_clip", fake_kling)
    return worker, calls


def test_stock_miss_scene_uses_kling_clip(monkeypatch, tmp_path):
    worker, calls = _stock_miss_worker(monkeypatch, tmp_path, 1, [{"ok": True, "request_id": "r1"}])

    result = worker.generate(job_id=3, tts_meta_json=None, script_meta_json="{}")

    scene = result["scenes"][0]
    assert scene["visual_type"] == "slide_kling_video"
    assert scene["stock_video_path"].endswith("_kling.mp4")
    assert scene["kling_request_id"] == "r1"
    assert scene["use_kling"] is False
    assert result["requires_manual_review"] is False
    assert calls == ["slide_kling:0"]


def test_third_consecutive_stock_miss_uses_gemini_still_then_kling_resumes(monkeypatch, tmp_path):
    ok = {"ok": True, "request_id": "r"}
    worker, calls = _stock_miss_worker(monkeypatch, tmp_path, 4, [ok, ok, ok])

    result = worker.generate(job_id=3, tts_meta_json=None, script_meta_json="{}")

    types = [s["visual_type"] for s in result["scenes"]]
    assert types == [
        "slide_kling_video", "slide_kling_video", "slide_ai_background", "slide_kling_video",
    ]
    assert result["scenes"][2]["kling_skipped_reason"] == "consecutive_kling_cap"
    assert calls == ["slide_kling:0", "slide_kling:1", "slide_kling:3"]


def test_failed_kling_does_not_count_toward_consecutive_cap(monkeypatch, tmp_path):
    ok = {"ok": True, "request_id": "r"}
    fail = {"ok": False, "reason": "budget_exceeded"}
    worker, calls = _stock_miss_worker(monkeypatch, tmp_path, 3, [fail, ok, ok])

    result = worker.generate(job_id=3, tts_meta_json=None, script_meta_json="{}")

    types = [s["visual_type"] for s in result["scenes"]]
    assert types == ["slide_ai_background", "slide_kling_video", "slide_kling_video"]
    assert result["requires_manual_review"] is True


def test_scene_failure_is_captured_not_raised(monkeypatch, tmp_path):
    """한 장면 생성 실패가 전체 배치를 막지 않는다 — 실패는 review_reasons로
    남고, 나머지 장면은 계속 처리된다."""
    worker = SlidesWorker()
    scenes = [
        {"content": "실패할 장면.", "start_seconds": 0, "end_seconds": 4},
        {"content": "성공할 장면.", "start_seconds": 4, "end_seconds": 8},
    ]
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared(scenes))

    def fake_generate_slide_direction(narration):
        if "실패" in narration:
            raise RuntimeError("의도된 실패")
        return {"headline": "h", "pexels_query": "q"}

    monkeypatch.setattr("app.workers.slides_worker.generate_slide_direction", fake_generate_slide_direction)
    monkeypatch.setattr("app.workers.slides_worker.search_candidates", lambda query, **kw: [])
    monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda narration, candidates: None)

    def fake_generate_korean_slide_background(prompt_en, output_path, *, job_id, scene_key):
        Path(output_path).write_bytes(b"fake-png")

    monkeypatch.setattr("app.workers.slides_worker.generate_korean_slide_background", fake_generate_korean_slide_background)
    monkeypatch.setattr("app.workers.slides_worker.assess_visual_alignment", lambda scenes, **kw: {"reviewed": []})
    monkeypatch.setattr("app.workers.slides_worker.DATA_DIR", tmp_path)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert result["scene_count"] == 2
    assert result["requires_manual_review"] is True
    assert any("장면 0" in r for r in result["review_reasons"])
    assert result["scenes"][0]["slide_generation_error"]
    assert result["scenes"][1]["visual_type"] == "slide_ai_background"


def test_output_contract_matches_images_worker_shape(monkeypatch, tmp_path):
    """Spring의 ImagesGenerateResponse가 읽는 최상위 키들이 그대로 있어야 한다."""
    worker = SlidesWorker()
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([]))
    monkeypatch.setattr("app.workers.slides_worker.assess_visual_alignment", lambda scenes, **kw: {"reviewed": []})
    monkeypatch.setattr("app.workers.slides_worker.DATA_DIR", tmp_path)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    for key in ("job_id", "scenes", "scene_count", "gifs", "gif_count", "requires_manual_review", "review_reasons"):
        assert key in result


def test_kling_is_off_by_default_stock_miss_goes_straight_to_gemini_still(monkeypatch, tmp_path):
    """slide_kling_enabled 기본값은 꺼짐 — 스톡 미스는 Kling 호출 없이 Gemini 정지 이미지."""
    worker, calls = _stock_miss_worker(monkeypatch, tmp_path, 2, [])
    import app.runtime_config as runtime_config

    monkeypatch.setitem(runtime_config._state, "slide_kling_enabled", False)

    result = worker.generate(job_id=3, tts_meta_json=None, script_meta_json="{}")

    assert [s["visual_type"] for s in result["scenes"]] == ["slide_ai_background", "slide_ai_background"]
    assert calls == []
    assert result["requires_manual_review"] is False


def test_explain_scene_uses_the_board_instead_of_looking_for_stock(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {"content": "15억 원에서 대출 5억 원을 빼면 순자산 10억 원입니다.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path, stock=True)  # 스톡이 있어도 설명 장면은 쓰지 않는다
    seen = _fake_composer(monkeypatch)
    searched = []
    monkeypatch.setattr(
        "app.workers.slides_worker.search_candidates", lambda q, **kw: searched.append(q) or [],
    )

    def fake_board(path, *a, **kw):
        Path(path).write_bytes(b"board")
        return True

    monkeypatch.setattr("app.workers.slides_worker.ensure_board_background", fake_board)
    plan = {0: {"type": "equation", "label": "대출", "left": ["15억 원", "5억 원"], "ops": ["−"],
                "result": "순자산 10억 원", "icon": "money_stack", "explain": True}}
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: plan)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert searched == []
    assert out_scene["overlay_type"] == "equation" and out_scene["visual_type"] == "slide_equation"
    assert out_scene["slide_layers"]["base"]["type"] == "slide_board"
    assert seen["composed"][0]["base"].endswith("board_background_v4.png")
    assert out_scene["slide_layers"]["overlays"][0]["icon"] == "money_stack"


def test_compare_numbers_layers_are_composed_one_after_another_with_staggered_starts(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {"content": "순자산 10억 원은 현재 상위 11.8퍼센트, 과거에는 상위 7.9퍼센트였습니다.",
             "start_seconds": 0, "end_seconds": 6}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path)
    seen = _fake_composer(monkeypatch)
    plan = {0: {"type": "compare_numbers", "headline": "순자산 10억 원", "icon": "money_stack",
                "rows": [{"label": "현재", "value": "11.8퍼센트"}, {"label": "과거", "value": "7.9퍼센트"}]}}
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: plan)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert out_scene["visual_type"] == "slide_compare_numbers"
    starts = [c["start_offset"] for c in seen["composed"]]
    assert len(starts) == 3 and starts == sorted(starts) and len(set(starts)) == 3
    assert seen["composed"][0]["is_video"] is False and seen["composed"][1]["is_video"] is True
    assert len(out_scene["slide_layers"]["overlays"][0]["overlay_png"]) == 3


def test_planned_article_card_without_a_capture_falls_back_to_a_key_phrase_overlay(monkeypatch, tmp_path):
    """계획서(승인본)에는 기사 카드가 있는데 실행 시점에 기사 캡처가 없을 수 있다 — 계획서를 바꾸지 않고
    원문 구절 강조로 대체하며 검수 표시를 남긴다(오버레이가 조용히 사라지지 않는다)."""
    worker = SlidesWorker()
    scene = {"content": "전자발찌 착용 중 범행을 저질렀습니다.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path)
    _fake_composer(monkeypatch)
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays",
                        lambda briefs, **kw: {0: {"type": "article_card"}})

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    out_scene = result["scenes"][0]
    assert out_scene["overlay_type"] == "key_text"
    assert "article_card_without_capture" in out_scene["quality_flags"]


def test_executing_with_a_stale_reviewed_plan_is_flagged_for_review(monkeypatch, tmp_path):
    worker = SlidesWorker()
    scene = {"content": "문장입니다.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    _stub_base_layer(monkeypatch, tmp_path)
    _fake_composer(monkeypatch)
    monkeypatch.setattr("app.workers.slides_worker.load_plan", lambda job_id, data_dir: {"input_hash": "old"})
    monkeypatch.setattr("app.workers.slides_worker.plan_matches", lambda plan, briefs: False)

    result = worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}")

    assert any("계획서를 새로 만들어" in reason for reason in result["review_reasons"])


def _stock_search_harness(monkeypatch, tmp_path, found_by_query, selector=lambda narration, fresh, **kw: 0):
    """_render_pixabay_scene을 단독으로 돌린다. found_by_query: 검색어 → 후보 목록."""
    searched = []

    def fake_search(query, **kw):
        searched.append(query)
        return found_by_query.get(query, [])

    monkeypatch.setattr("app.workers.slides_worker._throttled_search", fake_search)
    monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", selector)
    monkeypatch.setattr("app.workers.slides_worker.download_clip", lambda url, path: Path(path).write_bytes(b"v"))
    monkeypatch.setattr("app.workers.slides_worker._extract_first_frame", lambda v, p: Path(p).write_bytes(b"p") or True)
    return searched


def test_planned_stock_scene_falls_back_to_generic_queries_and_logs_every_search(monkeypatch, tmp_path):
    """2026-10-16 20번 Job: 구체적인 검색어("confused investor looking at documents")가 못 찾으면 장면이 회색 배경으로
    떨어졌는데 이유가 기록되지 않았다. 일반 검색어로 한 번 더 찾고, 시도한 검색어와 결과를 남긴다."""
    candidate = {"pixabay_id": 5, "download_url": "https://example/v.mp4", "tags": "office"}
    searched = _stock_search_harness(monkeypatch, tmp_path, {"business office": [candidate]})
    out: dict = {}

    ok = SlidesWorker()._render_pixabay_scene(
        index=0, narration="불안한 투자자가 서류를 봅니다.", duration=4.0, job_dir=tmp_path, used_pixabay_ids=set(),
        out=out, planned_queries=["confused investor looking at documents", "analyst reviewing financial report"],
    )

    assert ok is True and out["pixabay_id"] == 5
    assert "business office" in searched
    log = out["stock_search_log"]
    assert [entry["found"] for entry in log][:2] == [0, 0] and log[-1]["selected"] is True


def test_stock_miss_records_why_nothing_was_chosen(monkeypatch, tmp_path):
    candidate = {"pixabay_id": 6, "download_url": "https://example/v.mp4", "tags": "x"}
    _stock_search_harness(monkeypatch, tmp_path, {"city skyline": [candidate]}, selector=lambda n, f, **kw: None)
    out: dict = {}

    ok = SlidesWorker()._render_pixabay_scene(
        index=0, narration="문장입니다.", duration=4.0, job_dir=tmp_path, used_pixabay_ids=set(), out=out,
        planned_queries=["office"],
    )

    assert ok is False
    assert out["stock_miss_reason"] == "candidates_rejected_by_selection"
    assert any(entry["found"] > 0 and entry["selected"] is False for entry in out["stock_search_log"])


def test_plan_only_always_builds_a_fresh_plan_even_when_the_saved_one_matches(monkeypatch, tmp_path):
    """2026-10-16 재현: "계획서 다시 만들기"가 입력이 같다는 이유로 저장된 계획서를 그대로 돌려줘 수정이 반영되지 않았다.
    계획서만 만드는 요청은 명시적인 (재)생성이므로 항상 새로 만든다. 재사용은 실행(씬별 생성)에만 적용한다."""
    worker = SlidesWorker()
    scene = {"content": "문장입니다.", "start_seconds": 0, "end_seconds": 4}
    monkeypatch.setattr("app.workers.slides_worker.prepare_job_scenes", lambda **kw: _prepared([scene]))
    built = []
    monkeypatch.setattr("app.workers.slides_worker.load_plan", lambda job_id, data_dir: {"input_hash": "same", "scenes": []})
    monkeypatch.setattr("app.workers.slides_worker.plan_matches", lambda plan, briefs: True)
    monkeypatch.setattr("app.workers.slides_worker.save_plan", lambda plan, data_dir: None)

    def fake_build(briefs, **kw):
        built.append(1)
        return {"version": "v", "input_hash": "new", "scene_count": 1, "summary": {}, "segments": [], "scenes": []}

    monkeypatch.setattr("app.workers.slides_worker.build_edit_plan", fake_build)

    worker.generate(job_id=1, tts_meta_json=None, script_meta_json="{}", plan_only=True)

    assert built == [1]


def _stock_miss_scene(monkeypatch, tmp_path, state, queries=("confused investor looking at documents", "office")):
    worker = SlidesWorker()
    calls = []

    def fake_ai(self, **kw):
        calls.append(kw.get("photo_brief"))
        kw["out"]["image_path"] = str(tmp_path / "bg.png")
        kw["out"]["visual_type"] = "slide_ai_background"

    monkeypatch.setattr(SlidesWorker, "_render_pixabay_scene", lambda self, **kw: False)
    monkeypatch.setattr(SlidesWorker, "_render_ai_background_scene", fake_ai)
    monkeypatch.setattr(SlidesWorker, "_apply_overlay", lambda self, **kw: None)  # 합성(ffmpeg)은 이 테스트의 관심이 아니다
    monkeypatch.setattr("app.workers.slides_worker.ensure_board_background", lambda path: Path(path).write_bytes(b"b") or True)
    out = worker._render_scene(
        index=0, scene={"content": "불안한 투자자가 서류를 봅니다.", "start_seconds": 0, "end_seconds": 4},
        job_id=1, job_dir=tmp_path, used_pixabay_ids=set(), overlay_plan=None,
        base_plan={"background": "stock", "stock_queries": list(queries)}, ai_miss_state=state,
    )
    return out, calls


def test_stock_miss_is_replaced_by_a_gemini_image_within_the_per_job_cap(monkeypatch, tmp_path):
    """2026-10-16 운영자 결정: 스톡이 내용과 안 맞아 대체해야 하면 회색 배경 대신 Gemini 이미지로 만든다(먼저 Gemini만)."""
    import threading

    state = {"used": 0, "max": 2, "lock": threading.Lock()}

    first, calls = _stock_miss_scene(monkeypatch, tmp_path, state)
    second, calls2 = _stock_miss_scene(monkeypatch, tmp_path, state)
    third, calls3 = _stock_miss_scene(monkeypatch, tmp_path, state)

    assert first["visual_type"] == "slide_ai_background" and first["base_fallback"] == "stock_miss_to_ai"
    assert calls == ["confused investor looking at documents"]  # 계획서의 첫 스톡 검색어(촬영 가능한 피사체)를 촬영 설명으로
    assert second["visual_type"] == "slide_ai_background"
    assert third["visual_type"] == "slide_board" and third["base_fallback"] == "stock_miss_to_board" and calls3 == []  # 상한 초과


def test_gemini_failure_for_a_stock_miss_falls_back_to_the_board_background(monkeypatch, tmp_path):
    import threading

    state = {"used": 0, "max": 5, "lock": threading.Lock()}
    worker = SlidesWorker()

    def failing_ai(self, **kw):
        raise RuntimeError("budget exceeded")

    monkeypatch.setattr(SlidesWorker, "_render_pixabay_scene", lambda self, **kw: False)
    monkeypatch.setattr(SlidesWorker, "_render_ai_background_scene", failing_ai)
    monkeypatch.setattr(SlidesWorker, "_apply_overlay", lambda self, **kw: None)
    monkeypatch.setattr("app.workers.slides_worker.ensure_board_background", lambda path: Path(path).write_bytes(b"b") or True)

    out = worker._render_scene(
        index=0, scene={"content": "문장입니다.", "start_seconds": 0, "end_seconds": 4}, job_id=1, job_dir=tmp_path,
        used_pixabay_ids=set(), overlay_plan=None, base_plan={"background": "stock", "stock_queries": ["office"]},
        ai_miss_state=state,
    )

    assert out["visual_type"] == "slide_board" and out["base_fallback"] == "stock_miss_to_board"


def test_cached_stock_clips_are_claimed_up_front_so_no_scene_picks_a_clip_another_scene_already_uses(tmp_path):
    """2026-10-16 20번 Job: 같은 스톡 클립이 장면 3곳에 나왔다 — 캐시를 재사용하는 장면과 새로 고르는 장면이 병렬로 돌며
    아직 등록되지 않은 클립을 서로 가져갔다. 실행 전에 캐시된 클립 ID를 먼저 등록하고, 앞 장면과 겹치는 캐시는 다시 고른다."""
    from app.workers.slides_worker import _prime_used_stock_ids, _write_base_cache

    narrations = ["첫째 장면입니다.", "둘째 장면입니다.", "셋째 장면입니다."]
    for index, pixabay_id in enumerate((11, 22, 11)):  # 0번과 2번이 같은 클립
        video = tmp_path / f"v{index}.mp4"
        video.write_bytes(b"v")
        _write_base_cache(tmp_path, index, "stock", narrations[index], video_path=str(video), pixabay_id=pixabay_id)
    used: set[int] = set()

    conflicts = _prime_used_stock_ids(tmp_path, narrations, {0, 1, 2}, used)

    assert used == {11, 22}
    assert conflicts == {2}  # 뒤 장면의 중복 캐시는 버리고 다시 고른다


def test_primed_ids_skip_scenes_that_will_be_regenerated(tmp_path):
    from app.workers.slides_worker import _prime_used_stock_ids, _write_base_cache

    narrations = ["첫째 장면입니다.", "둘째 장면입니다."]
    for index, pixabay_id in enumerate((11, 22)):
        video = tmp_path / f"v{index}.mp4"
        video.write_bytes(b"v")
        _write_base_cache(tmp_path, index, "stock", narrations[index], video_path=str(video), pixabay_id=pixabay_id)
    used: set[int] = set()

    _prime_used_stock_ids(tmp_path, narrations, {1}, used)  # 0번 장면만 다시 만든다 → 1번 장면의 클립만 피한다

    assert used == {22}


def test_a_failed_stock_download_is_a_stock_miss_not_a_failed_scene(monkeypatch, tmp_path):
    """다운로드가 끝내 실패해도 장면 전체가 실패하지 않고(영상 조립이 막히지 않게) 다른 배경으로 내려간다."""
    from app.providers.real.pixabay_video import PixabayVideoUnavailableError

    candidate = {"pixabay_id": 5, "download_url": "https://example/v.mp4", "tags": "office"}
    _stock_search_harness(monkeypatch, tmp_path, {"business office": [candidate], "office": [candidate]})

    def broken_download(url, path):
        raise PixabayVideoUnavailableError("Pixabay 클립 다운로드 실패: HTTP 429")

    monkeypatch.setattr("app.workers.slides_worker.download_clip", broken_download)
    used: set[int] = set()
    out: dict = {}

    ok = SlidesWorker()._render_pixabay_scene(
        index=0, narration="문장입니다.", duration=4.0, job_dir=tmp_path, used_pixabay_ids=used, out=out,
        planned_queries=["office"],
    )

    assert ok is False and out["stock_miss_reason"] == "download_failed"
    assert 5 not in used  # 못 받은 클립은 다른 장면이 다시 시도할 수 있게 풀어 준다
