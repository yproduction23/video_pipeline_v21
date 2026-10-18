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
