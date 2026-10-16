"""슬라이드형 편집 계획서 계약 (2026-10-16 운영자 결정).

생성 전에 배경(칠판·스톡·사진)과 후편집을 한 번에 계획·저장하고, 실행은 계획서를 그대로 소비한다.
스톡·사진은 상한을 두고 나머지는 칠판 모션그래픽이며, 스톡 미적중은 유료 사진이 아니라 칠판으로 내려간다."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.scene_preparation import PreparedScenes
from app.services.slide_edit_plan import (
    GEMINI_IMAGE_EST_KRW,
    PLAN_VERSION,
    assemble_plan,
    build_edit_plan,
    fallback_key_phrase,
    load_plan,
    plan_input_hash,
    plan_matches,
    save_plan,
)
from app.workers.slides_worker import SlidesWorker


def _briefs(*texts, duration=4.0):
    return {i: {"narration": t, "context": "", "duration": duration} for i, t in enumerate(texts)}


def test_board_scene_without_a_valid_overlay_gets_a_verbatim_fallback_phrase():
    briefs = _briefs("외국인은 8거래일 연속으로 코스피를 팔았습니다. 규모가 컸습니다.")
    entries = [{"index": 0, "background": "board",
                "overlay": {"type": "key_text", "text": "원문에 없는 문구입니다 확실히"}}]

    plan = assemble_plan(briefs, entries, job_id=1)
    overlay = plan["scenes"][0]["overlay"]

    assert overlay["type"] == "key_text" and overlay["source"] == "fallback_phrase"
    assert overlay["text"] in briefs[0]["narration"] and 4 <= len(overlay["text"]) <= 28
    assert overlay["explain"] is True


def test_valid_overlay_is_kept_and_marked_explain_only_on_board():
    briefs = _briefs("코스피는 7.57퍼센트 급락했습니다.", "삼성전자가 영업이익 107조 원을 기록했습니다.")
    entries = [
        {"index": 0, "background": "board",
         "overlay": {"type": "big_number", "value": "7.57퍼센트", "label": ""}},
        {"index": 1, "background": "stock", "stock_queries": ["office building exterior"],
         "overlay": {"type": "key_text", "text": "영업이익 107조 원"}},
    ]

    plan = assemble_plan(briefs, entries, job_id=1)
    first, second = plan["scenes"]

    assert first["overlay"]["type"] == "big_number" and first["overlay"]["explain"] is True
    assert second["background"] == "stock" and second["overlay"]["explain"] is False
    assert second["stock_queries"] == ["office building exterior"]
    assert second["fallback_key_text"]


def test_footage_share_is_capped_and_extra_scenes_move_to_the_board():
    briefs = _briefs(*[f"장면 {i}번 문장입니다 충분히 길게." for i in range(10)])
    entries = [{"index": i, "background": "photo", "photo_brief": "city street"} for i in range(10)]

    plan = assemble_plan(briefs, entries, job_id=1, highlight_end_index=1)

    kept = [s["index"] for s in plan["scenes"] if s["background"] == "photo"]
    assert len(kept) == 3  # 10장면 × 30%
    assert {0, 1} <= set(kept)  # 하이라이트 구간은 지킨다
    assert plan["summary"]["demoted_to_board"] and all(
        plan["scenes"][i]["background"] == "board" and plan["scenes"][i]["overlay"] for i in plan["summary"]["demoted_to_board"]
    )
    assert plan["summary"]["gemini_images_expected"] == 3
    assert plan["summary"]["gemini_krw_expected"] == 3 * GEMINI_IMAGE_EST_KRW


def test_unknown_background_defaults_to_the_board_and_segments_group_runs():
    briefs = _briefs("가나다라마바사 문장입니다.", "아자차카타파하 문장입니다.", "하나둘셋넷 문장입니다.")
    entries = [
        {"index": 0, "background": "hologram"},
        {"index": 1, "background": "board"},
        {"index": 2, "background": "stock", "stock_queries": ["street"]},
    ]

    plan = assemble_plan(briefs, entries, job_id=1)

    assert [s["background"] for s in plan["scenes"]] == ["board", "board", "stock"]
    assert [(seg["background"], seg["range"]) for seg in plan["segments"]] == [("board", [0, 1]), ("stock", [2, 2])]
    assert plan["segments"][0]["seconds"] == 8.0


def test_plan_hash_changes_with_narration_and_matches_only_the_same_input(tmp_path):
    briefs = _briefs("첫 문장입니다.", "둘째 문장입니다.")
    plan = assemble_plan(briefs, [], job_id=7)
    save_plan(plan, tmp_path)

    loaded = load_plan(7, tmp_path)
    changed = _briefs("첫 문장입니다.", "고친 문장입니다.")

    assert loaded["version"] == PLAN_VERSION
    assert plan_matches(loaded, briefs) is True
    assert plan_matches(loaded, changed) is False
    assert plan_input_hash(briefs) != plan_input_hash(changed)
    assert load_plan(8, tmp_path) is None


def test_build_edit_plan_returns_none_when_claude_fails_or_is_unparsable():
    briefs = _briefs("문장입니다.")

    assert build_edit_plan(briefs, job_id=1, llm_call=lambda s, m, t: "not json") is None

    def boom(system, messages, max_tokens):
        raise RuntimeError("credit balance too low")

    assert build_edit_plan(briefs, job_id=1, llm_call=boom) is None


def test_fallback_key_phrase_prefers_a_clause_with_numbers():
    text = "시장이 흔들렸습니다. 외국인은 8거래일 연속 순매도를 이어 갔고, 규모는 컸습니다."

    assert "8거래일" in fallback_key_phrase(text)
    assert fallback_key_phrase("") == ""


# ---- 실행: SlidesWorker가 계획서를 그대로 소비한다 ---------------------------------

@pytest.fixture
def worker_env(monkeypatch, tmp_path):
    calls = {"stock": [], "gemini": [], "plans": 0, "direction": 0}
    monkeypatch.setattr("app.workers.slides_worker.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.workers.slides_worker._PIXABAY_MIN_INTERVAL", 0.0)
    monkeypatch.setattr("app.workers.slides_worker.assess_visual_alignment", lambda scenes, **kw: {"reviewed": []})
    monkeypatch.setattr(
        SlidesWorker, "_disclaimer_scene_index", staticmethod(lambda scenes_meta, targets, highlight=None: None),
    )

    def fake_direction(narration):
        calls["direction"] += 1
        return {"headline": "h", "pexels_query": "q"}

    monkeypatch.setattr("app.workers.slides_worker.generate_slide_direction", fake_direction)

    def fake_search(query, **kw):
        calls["stock"].append(query)
        return []

    monkeypatch.setattr("app.workers.slides_worker.search_candidates", fake_search)
    monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda n, c: None)

    def fake_bg(prompt_en, output_path, *, job_id, scene_key):
        calls["gemini"].append(prompt_en)
        Path(output_path).write_bytes(b"bg")

    monkeypatch.setattr("app.workers.slides_worker.generate_korean_slide_background", fake_bg)
    monkeypatch.setattr(
        "app.workers.slides_worker.ensure_board_background",
        lambda path, *a, **k: Path(path).write_bytes(b"board") or True,
    )

    def write_overlay(*, output_path, **kw):
        Path(output_path).write_bytes(b"overlay")
        return True

    for name in ("render_key_text_overlay", "render_bullet_overlay", "render_stat_overlay",
                 "render_news_clip_overlay", "render_equation_overlay"):
        monkeypatch.setattr(f"app.workers.slides_worker.{name}", write_overlay)
    monkeypatch.setattr(
        "app.workers.slides_worker.render_big_number_overlay",
        lambda *, output_path, **kw: Path(output_path).write_bytes(b"o") and None or [output_path],
    )

    def fake_compose(*, output_path, **kw):
        Path(output_path).write_bytes(b"mp4")
        return True

    monkeypatch.setattr("app.workers.slides_worker.compose_animated_overlay", fake_compose)
    monkeypatch.setattr(
        "app.workers.slides_worker._extract_first_frame_at",
        lambda video, out, seconds: Path(out).write_bytes(b"png") or True,
    )
    return calls


def _scenes(*texts):
    return [{"content": t, "start_seconds": i * 4, "end_seconds": i * 4 + 4} for i, t in enumerate(texts)]


def _prepare(monkeypatch, scenes):
    monkeypatch.setattr(
        "app.workers.slides_worker.prepare_job_scenes",
        lambda **kw: PreparedScenes(scenes_meta=[dict(s) for s in scenes], script_data={}),
    )


def _fake_planner(monkeypatch, entries_by_text, counter):
    def build(briefs, *, job_id, highlight_end_index=None, llm_call=None):
        counter["plans"] += 1
        entries = [{"index": i, **entries_by_text.get(b["narration"], {"background": "board"})}
                   for i, b in briefs.items()]
        return assemble_plan(briefs, entries, job_id=job_id, highlight_end_index=highlight_end_index)

    monkeypatch.setattr("app.workers.slides_worker.build_edit_plan", build)


def test_worker_executes_the_plan_board_photo_and_stock_miss_without_extra_gemini(monkeypatch, worker_env):
    texts = ["칠판에서 설명할 인과 문장입니다 금리가 올랐습니다.",
             "서울 출근길 지하철 장면을 보여 주는 문장입니다.",
             "거래소 건물 외관을 보여 주는 문장입니다 스톡이 없습니다."]
    fillers = [f"칠판 설명 문장 {i}번입니다 충분히 깁니다." for i in range(7)]  # 사진·스톡 상한(30%) 여유
    _prepare(monkeypatch, _scenes(*texts, *fillers))
    _fake_planner(monkeypatch, {
        texts[0]: {"background": "board", "overlay": {"type": "key_text", "text": "금리가 올랐습니다"}},
        texts[1]: {"background": "photo", "photo_brief": "commuters walking out of a subway station"},
        texts[2]: {"background": "stock", "stock_queries": ["stock exchange building exterior"]},
    }, worker_env)

    result = SlidesWorker().generate(job_id=5, tts_meta_json=None, script_meta_json="{}")

    types = [s["visual_type"] for s in result["scenes"]]
    assert types[0] == "slide_key_text"  # 칠판 + 강조 텍스트
    assert result["scenes"][0]["slide_layers"]["base"]["type"] == "slide_board"
    assert result["scenes"][1]["visual_type"] == "slide_ai_background"
    assert worker_env["gemini"] == ["commuters walking out of a subway station"]  # 계획서의 촬영 설명 그대로
    # 스톡 미적중은 사진 비용 없이 칠판 + 대체 구절
    assert result["scenes"][2]["base_fallback"] == "stock_miss_to_board"
    assert result["scenes"][2]["overlay_type"] == "key_text"
    assert "stock exchange building exterior" in " ".join(worker_env["stock"])
    assert worker_env["direction"] == 0  # 계획서가 있으면 장면별 연출 호출을 하지 않는다
    assert result["edit_plan"]["summary"]["photo"] == 1


def test_second_run_reuses_the_saved_plan_instead_of_asking_claude_again(monkeypatch, worker_env, tmp_path):
    texts = ["첫 문장입니다 칠판에서 설명합니다.", "둘째 문장입니다 칠판에서 설명합니다."]
    _prepare(monkeypatch, _scenes(*texts))
    _fake_planner(monkeypatch, {}, worker_env)

    worker = SlidesWorker()
    worker.generate(job_id=6, tts_meta_json=None, script_meta_json="{}")
    saved = json.loads((tmp_path / "jobs" / "6" / "slides" / "edit_plan.json").read_text(encoding="utf-8"))
    worker.generate(job_id=6, tts_meta_json=None, script_meta_json="{}")

    assert saved["version"] == PLAN_VERSION and saved["scene_count"] == 2
    assert worker_env["plans"] == 1


def test_changed_narration_builds_a_new_plan(monkeypatch, worker_env):
    _prepare(monkeypatch, _scenes("원래 문장입니다 설명합니다."))
    _fake_planner(monkeypatch, {}, worker_env)
    worker = SlidesWorker()
    worker.generate(job_id=9, tts_meta_json=None, script_meta_json="{}")

    _prepare(monkeypatch, _scenes("바뀐 문장입니다 설명합니다."))
    worker.generate(job_id=9, tts_meta_json=None, script_meta_json="{}")

    assert worker_env["plans"] == 2


def test_plan_failure_is_reported_and_falls_back_to_the_inline_overlay_plan(monkeypatch, worker_env):
    _prepare(monkeypatch, _scenes("계획서 실패 문장입니다."))
    monkeypatch.setattr("app.workers.slides_worker.build_edit_plan", lambda *a, **k: None)
    monkeypatch.setattr("app.workers.slides_worker.plan_slide_overlays", lambda briefs, **kw: None)

    result = SlidesWorker().generate(job_id=11, tts_meta_json=None, script_meta_json="{}")

    assert result["edit_plan"] is None
    assert result["requires_manual_review"] is True
    assert any("계획 생성 실패" in reason for reason in result["review_reasons"])
