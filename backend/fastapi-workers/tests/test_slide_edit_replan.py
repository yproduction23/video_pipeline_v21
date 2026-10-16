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
