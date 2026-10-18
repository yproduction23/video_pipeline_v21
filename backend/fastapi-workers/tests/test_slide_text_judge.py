"""화면 문구 의미 검수(2026-10-18, 52초): "단순 조정이라고 보기 어렵습니다"인데 "단순 조정이라고"만 떠서 정반대로 읽혔다."""
import json

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_text_judge import fix_key_texts_in_plan, fix_key_texts_in_topics, judge_fragments

NARRATION = "이번 하락을 단순 조정이라고 보기 어렵습니다. 구조적인 변화가 시작됐습니다."


def _reply(results):
    return lambda system, messages, max_tokens: json.dumps({"results": results}, ensure_ascii=False)


def test_a_replacement_must_be_verbatim_and_short_otherwise_the_text_is_dropped():
    items = [{"id": 0, "text": "단순 조정이라고", "narration": NARRATION}]

    ok = judge_fragments(items, llm_call=_reply([{"id": 0, "ok": False, "text": "단순 조정이라고 보기 어렵"}]))
    invented = judge_fragments(items, llm_call=_reply([{"id": 0, "ok": False, "text": "일시적 조정입니다"}]))
    too_long = judge_fragments(items, llm_call=_reply([{"id": 0, "ok": False, "text": "이번 하락을 단순 조정이라고 보기 어렵습니다"}]))

    assert ok == {0: "단순 조정이라고 보기 어렵"}
    assert invented == {0: ""} and too_long == {0: ""}            # 원문에 없거나 너무 길면 쓰지 않는다


def test_a_failed_judgement_changes_nothing():
    def broken(system, messages, max_tokens):
        raise RuntimeError("down")

    assert judge_fragments([{"id": 0, "text": "가", "narration": "가나"}], llm_call=broken) == {}


def test_plan_key_text_is_replaced_and_a_dropped_board_text_gets_a_narration_phrase():
    briefs = {0: {"narration": NARRATION, "context": "", "duration": 6.0}, 1: {"narration": NARRATION, "context": "", "duration": 6.0}}
    entries = [{"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "단순 조정이라고"}},
               {"index": 1, "background": "stock", "overlay": {"type": "key_text", "text": "단순 조정이라고"}}]
    plan = assemble_plan(briefs, entries, job_id=1)

    fix_key_texts_in_plan(plan, llm_call=_reply([{"id": 0, "ok": False, "text": "구조적인 변화가"}, {"id": 1, "ok": False, "text": ""}]))

    assert plan["scenes"][0]["overlay"]["text"] == "구조적인 변화가"
    assert plan["scenes"][1]["overlay"] is None                    # 스톡 장면은 문구를 뺀다
    assert plan["text_judgement"] == {"checked": 2, "changed": 2}


def test_topic_sub_text_can_be_dropped_but_the_headline_is_kept():
    run = [{"index": 0, "narration": NARRATION}]
    topics = [{"template": "headline_sub", "scene": 0, "headline": "단순 조정이라고", "sub": "구조적인 변화가"}]

    fix_key_texts_in_topics(topics, run, llm_call=_reply([{"id": 0, "ok": False, "text": ""}, {"id": 1, "ok": False, "text": ""}]))

    assert topics[0]["headline"] == "단순 조정이라고" and "sub" not in topics[0]
