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


def test_plan_key_text_is_replaced_or_dropped_never_swapped_for_an_unjudged_sentence():
    briefs = {0: {"narration": NARRATION, "context": "", "duration": 6.0}, 1: {"narration": NARRATION, "context": "", "duration": 6.0}}
    entries = [{"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "단순 조정이라고"}},
               {"index": 1, "background": "stock", "overlay": {"type": "key_text", "text": "단순 조정이라고"}}]
    plan = assemble_plan(briefs, entries, job_id=1)

    fix_key_texts_in_plan(plan, llm_call=_reply([{"id": 0, "ok": False, "text": "구조적인 변화가"}, {"id": 1, "ok": False, "text": ""}]))

    assert plan["scenes"][0]["overlay"]["text"] == "구조적인 변화가"
    assert plan["scenes"][1]["overlay"] is None                    # 알맞은 구절이 없으면 뺀다
    assert plan["text_judgement"] == {"checked": 2, "changed": 2}


def test_topic_sub_text_can_be_dropped_but_the_headline_is_kept():
    run = [{"index": 0, "narration": NARRATION}]
    topics = [{"template": "headline_sub", "scene": 0, "headline": "단순 조정이라고", "sub": "구조적인 변화가"}]

    fix_key_texts_in_topics(topics, run, llm_call=_reply([{"id": 0, "ok": False, "text": ""}, {"id": 1, "ok": False, "text": ""}]))

    assert topics[0]["headline"] == "단순 조정이라고" and "sub" not in topics[0]


def test_fake_list_that_splits_one_sentence_is_dropped_but_a_real_enumeration_stays():
    """2026-10-18 운영자 피드백(4:42): 한 문장을 줄바꿈해 1·2로 쓰면 '정리'라 볼 수 없다."""
    from app.services.slide_text_judge import drop_fake_lists_in_plan, drop_fake_lists_in_topics

    one = "금리와 수급과 신뢰가 함께 맞아야 하는 시장인 겁니다."
    briefs = {0: {"narration": one, "context": "", "duration": 4.0}, 1: {"narration": "이유는 금리, 수급, 신뢰입니다.", "context": "", "duration": 4.0}}
    entries = [
        {"index": 0, "background": "board", "overlay": {"type": "bullet_list", "title": "주가 상승 조건", "items": ["금리와 수급과 신뢰가", "함께 맞아야 하는 시장인 겁니다"]}},
        {"index": 1, "background": "board", "overlay": {"type": "bullet_list", "title": "이유", "items": ["금리", "수급", "신뢰"]}},
    ]
    plan = assemble_plan(briefs, entries, job_id=1)

    drop_fake_lists_in_plan(plan, llm_call=_reply_ids([{"id": 0, "ok": False}, {"id": 1, "ok": True}]))

    assert plan["scenes"][0]["overlay"] is None and plan["scenes"][1]["overlay"]["type"] == "bullet_list"
    assert plan["list_judgement"] == {"checked": 2, "dropped": 1}

    topics = [{"template": "list", "scene": 0, "items": ["a", "b"]}, {"template": "headline_sub", "scene": 0, "headline": "x"}]
    kept = drop_fake_lists_in_topics(topics, [{"index": 0, "narration": one}], llm_call=_reply_ids([{"id": 0, "ok": False}]))
    assert [t["template"] for t in kept] == ["headline_sub"]


def _reply_ids(results):
    return lambda system, messages, max_tokens: json.dumps({"results": results}, ensure_ascii=False)
