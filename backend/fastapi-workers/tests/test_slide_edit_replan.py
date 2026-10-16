"""편집 계획서의 문제 장면 정리 → 표시한 장면만 다시 계획 (2026-10-16 운영자 요청).

수동 필드 편집이 아니라 말로 쓴 수정 지시를 반영해 해당 장면만 다시 계획하며, 나머지 장면과 입력 해시는
그대로다. 다시 계획한 화면 글자도 같은 검증기(원문 일치)를 통과해야 한다."""
from __future__ import annotations

import json

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_edit_replan import replan_scenes

TEXTS = [
    "코스피는 7.57퍼센트 급락했습니다.",
    "외국인은 8거래일 연속으로 코스피를 팔았습니다.",
    "원인은 금리와 반도체 쏠림입니다.",
    "이 구조가 버틸 수 있는가가 핵심 질문입니다.",
]


def _plan():
    briefs = {i: {"narration": t, "context": "", "duration": 4.0} for i, t in enumerate(TEXTS)}
    entries = [
        {"index": 0, "background": "board", "overlay": {"type": "big_number", "value": "7.57퍼센트"}},
        {"index": 1, "background": "board", "overlay": {"type": "key_text", "text": "8거래일 연속으로 코스피를 팔았습니다"}},
        {"index": 2, "background": "board", "overlay": {"type": "key_text", "text": "금리와 반도체 쏠림"}},
        {"index": 3, "background": "board"},
    ]
    return assemble_plan(briefs, entries, job_id=20)


def _llm(payload):
    return lambda system, messages, max_tokens: json.dumps(payload, ensure_ascii=False)


def test_only_marked_scenes_change_and_notes_are_kept():
    plan = _plan()
    before = json.loads(json.dumps(plan))
    reply = {"scenes": [{
        "index": 2, "background": "board", "reason": "원인 두 가지를 목록으로",
        "overlay": {"type": "bullet_list", "title": "원인", "items": ["금리", "반도체 쏠림"]},
    }]}

    updated = replan_scenes(plan, [{"index": 2, "note": "원인을 목록으로 보여 줘"}], llm_call=_llm(reply))

    assert updated["scenes"][2]["overlay"]["type"] == "bullet_list"
    assert updated["scenes"][2]["revised"] is True
    assert updated["scenes"][2]["reason"].startswith("운영자 수정")
    strip = lambda scene: {k: v for k, v in scene.items() if k != "sequence_id"}  # noqa: E731
    for i in (0, 1, 3):  # 표시하지 않은 장면은 그대로(회색 배경 구간에 붙는 시퀀스 번호만 추가될 수 있다)
        assert strip(updated["scenes"][i]) == strip(before["scenes"][i])
    assert updated["review_notes"] == {"2": "원인을 목록으로 보여 줘"}
    assert updated["input_hash"] == before["input_hash"]  # 같은 내레이션이면 계획서는 계속 유효
    assert plan == before  # 원본 객체는 건드리지 않는다
    assert "bullet_list" in updated["summary"]["overlay_types"]


def test_replanned_text_must_still_be_verbatim_from_the_narration():
    plan = _plan()
    reply = {"scenes": [{
        "index": 1, "background": "board",
        "overlay": {"type": "key_text", "text": "원문에 없는 선동적인 문구입니다"},
    }]}

    updated = replan_scenes(plan, [{"index": 1, "note": "더 강하게"}], llm_call=_llm(reply))

    overlay = updated["scenes"][1]["overlay"]
    assert overlay["source"] == "fallback_phrase" and overlay["text"] in TEXTS[1]


def test_failure_or_unknown_scene_keeps_the_existing_plan():
    plan = _plan()

    assert replan_scenes(plan, [{"index": 2, "note": "x"}], llm_call=lambda s, m, t: "not json") is None
    assert replan_scenes(plan, [{"index": 99, "note": "x"}], llm_call=_llm({"scenes": []})) is None
    assert replan_scenes(plan, [], llm_call=_llm({"scenes": []})) is None


def test_marked_scene_can_become_stock_and_segments_and_summary_refresh():
    plan = _plan()
    reply = {"scenes": [{
        "index": 3, "background": "stock", "stock_queries": ["city street rain umbrellas", "office workers"],
        "overlay": {"type": "key_text", "text": "버틸 수 있는가"},
    }]}

    updated = replan_scenes(plan, [{"index": 3, "note": "현장 영상으로"}], llm_call=_llm(reply))

    assert updated["scenes"][3]["background"] == "stock"
    assert updated["scenes"][3]["stock_queries"][0] == "city street rain umbrellas"
    assert updated["scenes"][3]["overlay"]["explain"] is False
    assert updated["summary"]["stock"] == 1 and updated["summary"]["board"] == 3
    assert [seg["background"] for seg in updated["segments"]] == ["board", "stock"]


# ---- 레퍼런스 분석 기준 위반 검사·자동 보정 (2026-10-16) ------------------------------------

from app.services.slide_edit_plan import (  # noqa: E402
    MAX_BOARD_RUN_SECONDS,
    MAX_BARE_STOCK_SECONDS,
    MIN_FOOTAGE_RATIO,
    plan_violations,
)
from app.services.slide_edit_replan import build_edit_plan_with_repair, repair_targets  # noqa: E402


def _all_board_plan(count=20, duration=4.0):
    briefs = {i: {"narration": f"{i}번 장면의 주장 문장입니다 충분히 깁니다.", "context": "", "duration": duration}
              for i in range(count)}
    entries = [{"index": i, "background": "board",
                "overlay": {"type": "key_text", "text": f"{i}번 장면의 주장 문장입니다"}} for i in range(count)]
    return briefs, entries


def test_all_board_plan_violates_the_reference_profile():
    briefs, entries = _all_board_plan()
    plan = assemble_plan(briefs, entries, job_id=1)

    quality = plan_violations(plan)

    assert "board_run_too_long" in quality["violations"] and "footage_ratio_low" in quality["violations"]
    assert quality["long_board_runs"][0]["seconds"] == 80.0
    assert quality["footage_ratio"] == 0.0


def test_repair_targets_break_long_runs_and_reach_the_stock_floor():
    briefs, entries = _all_board_plan()
    plan = assemble_plan(briefs, entries, job_id=1)

    items = repair_targets(plan)
    converted = {i["index"] for i in items}
    after = [s["duration"] for s in plan["scenes"] if s["index"] in converted]

    assert sum(after) / plan["total_seconds"] >= MIN_FOOTAGE_RATIO
    # 남는 칠판 구간은 한도를 넘지 않는다
    run, longest = 0.0, 0.0
    for scene in plan["scenes"]:
        run = 0.0 if scene["index"] in converted else run + scene["duration"]
        longest = max(longest, run)
    assert longest <= MAX_BOARD_RUN_SECONDS
    assert all("스톡" in i["note"] for i in items)


def test_build_with_repair_converts_scenes_and_records_the_rounds():
    briefs, entries = _all_board_plan()
    first = {"scenes": entries}

    def llm(system, messages, max_tokens):
        prompt = messages[0]["content"]
        if "운영자가 '문제'로 표시한" in prompt:  # 부분 재계획 호출
            marked = sorted({int(m) for m in __import__("re").findall(r"장면 (\d+):", prompt)})
            return json.dumps({"scenes": [
                {"index": i, "background": "stock", "stock_queries": ["city skyline", "office workers"],
                 "overlay": {"type": "key_text", "text": f"{i}번 장면의 주장 문장입니다"}}
                for i in marked
            ]}, ensure_ascii=False)
        return json.dumps(first, ensure_ascii=False)

    plan = build_edit_plan_with_repair(briefs, job_id=1, llm_call=llm)

    # 칠판 연속·스톡 하한 위반은 보정됐다(모든 장면에 후편집이 붙은 장난감 입력의 밀도 경고만 남을 수 있다).
    assert "board_run_too_long" not in plan["quality"]["violations"]
    assert "footage_ratio_low" not in plan["quality"]["violations"]
    assert plan["summary"]["stock"] >= 11 and plan["auto_repair_rounds"] >= 1
    stock_scene = next(s for s in plan["scenes"] if s["background"] == "stock")
    assert stock_scene["auto_repaired"] is True and stock_scene["reason"].startswith("자동 보정")
    assert "revised" not in stock_scene and "review_notes" not in plan  # 운영자 수정 표식과 구분
    assert stock_scene["stock_queries"][0] == "city skyline"


def test_unrepairable_violation_stays_visible_in_the_plan_quality():
    briefs, entries = _all_board_plan()

    def llm(system, messages, max_tokens):
        if "운영자가 '문제'로 표시한" in messages[0]["content"]:
            return "not json"  # 보정 실패
        return json.dumps({"scenes": entries}, ensure_ascii=False)

    plan = build_edit_plan_with_repair(briefs, job_id=1, llm_call=llm)

    assert "board_run_too_long" in plan["quality"]["violations"]
    assert plan["auto_repair_rounds"] == 0


# ---- 스톡 영상 위 후편집 (2026-10-16 운영자 요청: 후편집 없이 스톡만 길게 이어지는 부분을 고친다) ----

def _stock_plan(overlay_pattern, duration=4.0):
    """overlay_pattern: 장면마다 후편집이 있으면 True. 모두 스톡 배경."""
    briefs = {i: {"narration": f"{i}번 장면의 주장 문장입니다 충분히 깁니다.", "context": "", "duration": duration}
              for i in range(len(overlay_pattern))}
    entries = []
    for i, has_overlay in enumerate(overlay_pattern):
        entry = {"index": i, "background": "stock", "stock_queries": ["office", "city"]}
        if has_overlay:
            entry["overlay"] = {"type": "key_text", "text": f"{i}번 장면의 주장 문장입니다"}
        entries.append(entry)
    return assemble_plan(briefs, entries, job_id=1)


def test_a_long_run_of_bare_stock_is_a_violation():
    plan = _stock_plan([False, False, False, False, True, True])  # 16초 동안 후편집 없는 스톡

    quality = plan_violations(plan)

    assert "bare_stock_run_too_long" in quality["violations"]
    assert quality["bare_stock_runs"][0]["scenes"] == [0, 1, 2, 3]


def test_a_low_share_of_stock_scenes_with_overlays_is_a_violation():
    plan = _stock_plan([True, False, False, False, False, False, False, False, False, True])

    assert "stock_overlay_ratio_low" in plan_violations(plan)["violations"]


def test_stock_with_regular_overlays_passes_both_stock_overlay_checks():
    plan = _stock_plan([True, False, True, True, False, True, True, False, True, True])

    violations = plan_violations(plan)["violations"]

    assert "bare_stock_run_too_long" not in violations and "stock_overlay_ratio_low" not in violations


def test_repair_marks_bare_stock_scenes_to_get_overlays_without_changing_their_background():
    plan = _stock_plan([False] * 8)

    items = repair_targets(plan)

    marked = {i["index"] for i in items}
    run, longest = 0.0, 0.0
    for scene in plan["scenes"]:
        run = 0.0 if scene["index"] in marked else run + scene["duration"]
        longest = max(longest, run)
    assert longest <= MAX_BARE_STOCK_SECONDS
    assert all("후편집" in i["note"] and "stock" in i["note"] for i in items)


def test_replan_asks_once_more_when_an_overlay_is_rejected_for_not_copying_the_narration():
    """2026-10-16 재현: 스톡 위 후편집 13장면이 모두 내레이션을 바꿔 쓴 문구라 거절돼 보정이 아무 효과가 없었다.
    거절된 장면만 거절 이유와 함께 한 번 더 요청한다."""
    briefs = {0: {"narration": "위험회피 심리가 시장 전반으로 빠르게 퍼졌습니다.", "context": "", "duration": 4.0}}
    plan = assemble_plan(briefs, [{"index": 0, "background": "stock", "stock_queries": ["office", "city"]}], job_id=1)
    calls = []

    def llm(system, messages, max_tokens):
        calls.append(messages[0]["content"])
        text = "위험회피 심리 확산" if len(calls) == 1 else "위험회피 심리가"
        return json.dumps({"scenes": [{"index": 0, "background": "stock", "stock_queries": ["office", "city"],
                                       "overlay": {"type": "key_text", "text": text}}]}, ensure_ascii=False)

    updated = replan_scenes(plan, [{"index": 0, "note": "후편집을 얹는다"}], llm_call=llm, source="auto")

    assert len(calls) == 2 and "글자 그대로" in calls[1] and "위험회피 심리 확산" in calls[1]
    assert updated["scenes"][0]["background"] == "stock"
    assert updated["scenes"][0]["overlay"]["text"] == "위험회피 심리가"


def test_excess_overlays_on_stock_are_trimmed_without_creating_a_long_bare_run():
    """2026-10-16 20번 Job: 스톡 위 후편집 하한(60%)을 강조하자 Claude가 거의 모든 스톡 장면(93%)에 문구를 얹어 단조로워졌다.
    상한(80%)을 넘으면 정보량이 가장 적은 강조 문구부터 하나 건너 하나씩 걷어 낸다."""
    from app.services.slide_edit_plan import MAX_STOCK_OVERLAY_RATIO, MIN_STOCK_OVERLAY_RATIO
    from app.services.slide_edit_replan import trim_excess_stock_overlays

    plan = _stock_plan([True] * 10)

    trimmed = trim_excess_stock_overlays(plan)

    stock = [s for s in trimmed["scenes"] if s["background"] == "stock"]
    ratio = sum(1 for s in stock if s.get("overlay")) / len(stock)
    assert MIN_STOCK_OVERLAY_RATIO <= ratio <= MAX_STOCK_OVERLAY_RATIO
    assert "stock_overlay_ratio_high" not in plan_violations(trimmed)["violations"]
    assert "bare_stock_run_too_long" not in plan_violations(trimmed)["violations"]
    assert all(not (a.get("overlay") is None and b.get("overlay") is None) for a, b in zip(stock, stock[1:]))


def test_trim_keeps_informative_overlays_and_only_removes_key_text():
    from app.services.slide_edit_replan import trim_excess_stock_overlays

    briefs = {i: {"narration": f"{i}번 장면의 주장 문장입니다 충분히 깁니다.", "context": "", "duration": 4.0} for i in range(10)}
    entries = []
    for i in range(10):
        entry = {"index": i, "background": "stock", "stock_queries": ["office", "city"]}
        if i % 3 == 0:
            entry["overlay"] = {"type": "bullet_list", "title": "근거 목록", "items": [f"{i}번 장면의 주장", "충분히 깁니다"]}
        else:
            entry["overlay"] = {"type": "key_text", "text": f"{i}번 장면의 주장 문장입니다"}
        entries.append(entry)
    plan = assemble_plan(briefs, entries, job_id=1)
    kept_types = {s["index"]: (s.get("overlay") or {}).get("type") for s in plan["scenes"]}

    trimmed = trim_excess_stock_overlays(plan)

    for scene in trimmed["scenes"]:
        if kept_types[scene["index"]] == "bullet_list":
            assert scene["overlay"] and scene["overlay"]["type"] == "bullet_list"
