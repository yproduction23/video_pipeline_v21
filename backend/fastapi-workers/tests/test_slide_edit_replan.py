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
    for i in (0, 1, 3):  # 표시하지 않은 장면은 그대로
        assert updated["scenes"][i] == before["scenes"][i]
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
