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
    briefs = _briefs("외국인은 코스피를 대량으로 팔았습니다. 규모가 컸습니다.")
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


def test_paid_photo_share_is_capped_but_stock_is_not():
    briefs = _briefs(*[f"장면 {i}번 문장입니다 충분히 길게." for i in range(50)])
    entries = [{"index": i, "background": "photo", "photo_brief": "city street"} for i in range(25)]
    entries += [{"index": i, "background": "stock", "stock_queries": ["city skyline", "office"]} for i in range(25, 50)]

    plan = assemble_plan(briefs, entries, job_id=1, highlight_end_index=1)

    kept = [s["index"] for s in plan["scenes"] if s["background"] == "photo"]
    assert len(kept) == 4  # 50장면 x 8%: 유료 Gemini 사진만 제한
    assert {0, 1} <= set(kept)  # 하이라이트 구간은 지킨다
    assert plan["summary"]["stock"] == 25  # 스톡은 비용이 없어 상한이 없다
    assert plan["summary"]["demoted_to_board"] and all(
        plan["scenes"][i]["background"] == "board" and plan["scenes"][i]["overlay"] for i in plan["summary"]["demoted_to_board"]
    )
    assert plan["summary"]["gemini_images_expected"] == 4
    assert plan["summary"]["gemini_krw_expected"] == 4 * GEMINI_IMAGE_EST_KRW


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
    monkeypatch.setattr("app.workers.slides_worker.select_best_candidate", lambda n, c, **kw: None)

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

    # 이 테스트는 "AI 대체 없이" 스톡 미적중이 회색 배경으로 내려가는 경로를 확인한다(대체 상한 0).
    monkeypatch.setitem(__import__("app.runtime_config", fromlist=["_state"])._state, "slide_stock_miss_ai_max", 0)
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


def test_plan_only_saves_the_plan_and_never_renders_or_spends(monkeypatch, worker_env, tmp_path):
    texts = ["칠판에서 설명할 문장입니다 금리가 올랐습니다.", "서울 출근길 지하철 장면 문장입니다."]
    fillers = [f"칠판 설명 문장 {i}번입니다 충분히 깁니다." for i in range(8)]
    _prepare(monkeypatch, _scenes(*texts, *fillers))
    _fake_planner(monkeypatch, {
        texts[1]: {"background": "photo", "photo_brief": "commuters walking out of a subway station"},
    }, worker_env)

    result = SlidesWorker().generate(job_id=12, tts_meta_json=None, script_meta_json="{}", plan_only=True)

    assert result["status"] == "PLAN_READY"
    assert result["scenes"] == [] and result["scene_count"] == 0
    assert result["edit_plan"]["summary"]["photo"] == 1
    assert result["edit_plan"]["summary"]["gemini_krw_expected"] == GEMINI_IMAGE_EST_KRW
    assert (tmp_path / "jobs" / "12" / "slides" / "edit_plan.json").is_file()
    # 계획서만 만든다: 이미지·스톡·진행 파일 모두 건드리지 않는다
    assert worker_env["gemini"] == [] and worker_env["stock"] == []
    assert not (tmp_path / "jobs" / "12" / "slides" / "progress.json").exists()


def test_plan_only_reports_failure_without_generating_anything(monkeypatch, worker_env):
    _prepare(monkeypatch, _scenes("실패 문장입니다."))
    monkeypatch.setattr("app.workers.slides_worker.build_edit_plan", lambda *a, **k: None)

    result = SlidesWorker().generate(job_id=13, tts_meta_json=None, script_meta_json="{}", plan_only=True)

    assert result["status"] == "PLAN_FAILED" and result["requires_manual_review"] is True
    assert result["edit_plan"] is None
    assert worker_env["gemini"] == []


def test_board_scenes_in_a_sequence_render_through_the_sequence_and_keep_other_paths(monkeypatch, worker_env, tmp_path):
    texts = [f"칠판 시퀀스 {i}번 문장입니다 충분히 깁니다 숫자 {i}." for i in range(3)]
    fillers = [f"스톡 장면 {i}번 문장입니다 충분히 깁니다." for i in range(7)]
    _prepare(monkeypatch, _scenes(*texts, *fillers))

    def build(briefs, *, job_id, highlight_end_index=None, llm_call=None):
        entries = [{"index": i, "background": "board",
                    "overlay": {"type": "key_text", "text": briefs[i]["narration"][:12]}} for i in range(3)]
        entries += [{"index": i, "background": "stock", "stock_queries": ["city skyline", "office"]}
                    for i in range(3, 10)]
        plan = assemble_plan(briefs, entries, job_id=job_id)
        from app.services.slide_sequence import attach_sequences
        return attach_sequences(plan, llm_call=lambda s, m, t: "not json", workers=1)  # 설계 실패 → 장면별 후편집 대체

    monkeypatch.setattr("app.workers.slides_worker.build_edit_plan", build)
    rendered = []

    def fake_sequence(sequence, t0, t1, board_path, output_path):
        rendered.append((round(t0, 2), round(t1, 2)))
        Path(output_path).write_bytes(b"seq-mp4")
        return True

    monkeypatch.setattr("app.workers.slides_worker.render_sequence_clip", fake_sequence)

    result = SlidesWorker().generate(job_id=14, tts_meta_json=None, script_meta_json="{}")

    first = result["scenes"][:3]
    assert [s["visual_type"] for s in first] == ["slide_sequence"] * 3
    assert sorted(rendered) == [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]  # 장면마다 시퀀스 시간축의 자기 몫
    assert first[0]["overlay_type"] == "sequence"
    assert first[0]["slide_layers"]["overlays"][0]["sequence_id"] == 1
    assert result["scenes"][5]["visual_type"] in {"slide_ai_background", "slide_board", "slide_key_text"}  # 스톡 미적중 → AI 이미지(상한 안) 또는 회색 배경


def test_plan_is_reused_even_when_article_evidence_availability_changes(tmp_path):
    """2026-10-16 20번 Job 재현: 기사 증거 수집이 실행마다 성공·실패가 달라(has_article) 승인한 계획서가
    해시 불일치로 조용히 새 계획서로 바뀌었다. 기사 유무는 계획서 재사용 기준에 넣지 않는다."""
    with_article = _briefs("첫 문장입니다.", "둘째 문장입니다.")
    with_article[1]["has_article"] = True
    without_article = _briefs("첫 문장입니다.", "둘째 문장입니다.")
    plan = assemble_plan(with_article, [], job_id=7)

    assert plan_input_hash(with_article) == plan_input_hash(without_article)
    assert plan_matches(plan, without_article) is True


def test_plan_marks_overlays_on_stock_as_on_footage_so_clause_length_text_is_dropped():
    briefs = _briefs("기업 코어위브의 주가가 급락했습니다.", "기업 코어위브의 주가가 급락했습니다.")
    entries = [
        {"index": 0, "background": "stock", "stock_queries": ["office", "city"],
         "overlay": {"type": "key_text", "text": "코어위브의 주가가 급락했습니다"}},
        {"index": 1, "background": "board", "overlay": {"type": "key_text", "text": "코어위브의 주가가 급락했습니다"}},
    ]

    plan = assemble_plan(briefs, entries, job_id=1)

    assert plan["scenes"][0]["overlay"] is None
    assert plan["scenes"][1]["overlay"]["text"] == "코어위브의 주가가 급락했습니다"


def test_plan_scenes_keep_the_caption_chunks_and_sequence_anchors_use_them():
    """계획서 장면에 자막 줄 시각이 보존되고, 시퀀스 등장 시각은 구절이 속한 줄에서 정해진다."""
    from app.services.slide_sequence import _anchor_time

    scene = {"narration": "외국인은 8거래일 연속 코스피를 팔았습니다.", "duration": 4.0,
             "chunks": [{"text": "외국인은 8거래일 연속", "start": 0.0, "end": 1.5},
                        {"text": "코스피를 팔았습니다", "start": 1.5, "end": 4.0}]}
    ratio_only = {k: v for k, v in scene.items() if k != "chunks"}

    assert 11.3 <= _anchor_time(scene, 10.0, "코스피를") <= 11.6               # 둘째 줄 시작(1.5초) 직전
    assert _anchor_time(scene, 10.0, "코스피를") != _anchor_time(ratio_only, 10.0, "코스피를")


def test_fallback_key_phrase_avoids_repeating_the_subtitle_line():
    """대체 구절이 자막 줄 그대로면 같은 말이 두 번 보인다 — 줄의 앞부분 핵심어로 줄인다(원문 글자 그대로)."""
    from app.services.slide_edit_plan import fallback_key_phrase

    narration = "긴급 진단에 나섰습니다."
    chunks = [{"text": "긴급 진단에 나섰습니다.", "start": 0.0, "end": 2.0}]

    assert fallback_key_phrase(narration) == "긴급 진단에 나섰습니다"                 # 자막 정보가 없으면 기존 동작
    short = fallback_key_phrase(narration, chunks)
    assert short != "긴급 진단에 나섰습니다" and 4 <= len(short) < len("긴급 진단에 나섰습니다")
    assert short in narration                                                          # 원문 일부 그대로


# ── 그래픽 지속(2026-10-17): 데이터 후편집은 다음 스톡 장면까지 이어서 보여 준다 ──

def _carry_plan(overlays, backgrounds):
    briefs = {i: {"narration": f"장면 {i} 내레이션입니다 숫자 5.3퍼센트.", "duration": 4.0} for i in range(len(overlays))}
    entries = [
        {"index": i, "background": backgrounds[i], "stock_queries": ["q"] if backgrounds[i] == "stock" else [],
         "overlay": overlays[i]}
        for i in range(len(overlays))
    ]
    return assemble_plan(briefs, entries, job_id=1)


def test_data_overlay_is_carried_into_the_next_bare_stock_scene_only():
    big = {"type": "big_number", "value": "5.3퍼센트", "label": ""}
    plan = _carry_plan([big, None, None], ["stock", "stock", "stock"])

    by_index = {s["index"]: s for s in plan["scenes"]}
    assert by_index[1].get("carry_from") == 0          # 바로 다음 맨 스톡 장면이 이어 받는다
    assert "carry_from" not in by_index[2]             # 최대 한 장면까지만(너무 오래 정지해 있지 않게)
    assert "carry_from" not in by_index[0]


def test_key_text_and_scenes_with_their_own_overlay_are_not_carried():
    key = {"type": "key_text", "text": "내레이션입니다 숫자"}
    big = {"type": "big_number", "value": "5.3퍼센트", "label": ""}

    plan = _carry_plan([key, None], ["stock", "stock"])
    assert "carry_from" not in plan["scenes"][1]
    plan = _carry_plan([big, key], ["stock", "stock"])
    assert "carry_from" not in plan["scenes"][1]
    plan = _carry_plan([big, None], ["stock", "board"])   # 회색 배경은 시퀀스가 화면을 맡는다
    assert "carry_from" not in plan["scenes"][1]
