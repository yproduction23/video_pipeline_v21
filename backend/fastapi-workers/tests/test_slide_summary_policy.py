"""빨간 글 정책(2026-10-19 운영자 결정): 자막을 빨간 글씨로 따라 쓰지 않는다 — 주장은 '정리' 카드(요약)로, 나머지는 그림으로.
운영 기본값(RED_TOPICS_PER_SEQUENCE=0)에서 확인한다."""
import json
import re

from app.services import slide_sequence
from app.services.slide_edit_plan import assemble_plan
from app.services.slide_sequence import board_runs, design_sequence

NARRATION = "수급과 금리가 시장을 뒤흔들었고, 코스피 구조 자체가 낙폭을 키웠습니다."


def _plan():
    briefs = {0: {"narration": NARRATION, "context": "", "duration": 6.0}}
    return assemble_plan(briefs, [{"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "수급과 금리가"}}], job_id=1)


def _llm(topics):
    def call(system, messages, max_tokens):
        text = messages[0]["content"]
        if "아이콘 검수자" in system:
            return json.dumps({"ok": [int(i) for i in re.findall(r"^(\d+)\. 아이콘=", text, re.M)]})
        if "목록 카드" in system:
            return json.dumps({"results": [{"id": 0, "ok": True}]})
        return json.dumps({"topics": topics}, ensure_ascii=False)
    return call


def test_default_policy_allows_no_red_headline():
    assert slide_sequence.RED_TOPICS_PER_SEQUENCE == 0
    plan = _plan()

    seq = design_sequence(board_runs(plan)[0], plan, llm_call=_llm(
        [{"template": "headline_sub", "scene": 0, "anchor": "수급과 금리가", "headline": "수급과 금리가"}]))

    reds = [e for e in (seq or {"elements": []})["elements"] if e["kind"] == "text" and e["content"].get("color") == "red"]
    assert reds == []


def test_a_claim_is_summarised_on_a_white_summary_card_instead():
    plan = _plan()
    topic = {"template": "list", "scene": 0, "anchor": "수급과 금리가", "title": "낙폭 확대 원인",
             "items": ["수급과 금리가 시장을 뒤흔들었고", "코스피 구조 자체가 낙폭을 키웠습니다"]}

    seq = design_sequence(board_runs(plan)[0], plan, llm_call=_llm([topic]))

    cards = [e for e in seq["elements"] if e["kind"] == "list"]
    assert cards and cards[0]["content"]["title"] == "낙폭 확대 원인" and len(cards[0]["content"]["items"]) == 2
    assert not [e for e in seq["elements"] if e["kind"] == "text" and e["content"].get("color") == "red"]


def test_locked_scenes_keep_their_previous_decisions_when_the_plan_is_rebuilt():
    """2026-10-19 운영자 요청: 수정할 때 고칠 부분만 바꾼다 — 좋았던 부분이 다시 만들면서 이상하게 바뀌는 일이 많았다."""
    from app.services.slide_edit_replan import carry_locked_scenes

    previous = {"scenes": [
        {"index": 0, "narration": "가", "background": "board", "overlay": {"type": "big_number", "value": "5%"}, "locked": True, "reason": "좋음"},
        {"index": 1, "narration": "나", "background": "stock", "stock_queries": ["a"], "overlay": None, "locked": True},
        {"index": 2, "narration": "다", "background": "stock", "overlay": None},
    ]}
    fresh = {"scenes": [
        {"index": 0, "narration": "가", "background": "stock", "overlay": {"type": "key_text", "text": "x"}, "duration": 4.0},
        {"index": 1, "narration": "바뀐 문장", "background": "board", "overlay": None, "duration": 3.0},   # 내레이션이 달라졌다
        {"index": 2, "narration": "다", "background": "board", "overlay": None, "duration": 3.0},          # 잠그지 않았다
    ]}

    carried = carry_locked_scenes(fresh, previous)

    assert carried == [0] and fresh["locked_scenes"] == [0]
    assert fresh["scenes"][0]["background"] == "board" and fresh["scenes"][0]["overlay"]["value"] == "5%"   # 이전 결정 그대로
    assert fresh["scenes"][0]["duration"] == 4.0                                                          # 길이는 새 값
    assert fresh["scenes"][1]["background"] == "board" and fresh["scenes"][2]["background"] == "board"    # 내레이션이 다르거나 잠그지 않은 장면은 새 결과
