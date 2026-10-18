"""아이콘 적합성 판정(2026-10-17 운영자 피드백: "엉뚱한 아이콘을 쓰고 있다").
Claude가 고른 아이콘이 그 순간 말하는 대상을 직접 그린 것인지 한 번 더 판정하고, 아니면 아이콘을 뺀다(글은 그대로)."""
import json

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_icon_judge import judge_icons, strip_unjudged_icons_from_plan, strip_unjudged_icons_from_topics


def _llm(ok_ids):
    calls = []

    def call(system, messages, max_tokens):
        calls.append(messages[0]["content"])
        return json.dumps({"ok": ok_ids})

    call.calls = calls
    return call


def test_judge_returns_only_the_ids_claude_approves_and_skips_the_call_when_there_is_nothing():
    items = [
        {"id": 0, "icon": "chip", "text": "반도체", "narration": "반도체 업종이 흔들렸습니다."},
        {"id": 1, "icon": "scissors", "text": "구조적 취약점", "narration": "코스피의 구조적 취약점이 드러났습니다."},
    ]
    llm = _llm([0])

    assert judge_icons(items, llm_call=llm) == {0}
    assert "chip" in llm.calls[0] and "scissors" in llm.calls[0]
    assert judge_icons([], llm_call=llm) == set() and len(llm.calls) == 1


def test_judge_failure_approves_nothing_so_no_unchecked_icon_reaches_the_screen():
    def broken(system, messages, max_tokens):
        raise RuntimeError("api down")

    assert judge_icons([{"id": 0, "icon": "chip", "text": "반도체", "narration": "반도체"}], llm_call=broken) == set()


def test_plan_overlay_icons_that_fail_the_judgement_are_removed_but_the_text_stays():
    briefs = {0: {"narration": "코스피의 구조적 취약점이 드러났습니다.", "duration": 4.0},
              1: {"narration": "반도체 업종이 흔들렸습니다.", "duration": 4.0}}
    entries = [
        {"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "구조적 취약점", "icon": "scissors"}},
        {"index": 1, "background": "board", "overlay": {"type": "key_text", "text": "반도체 업종", "icon": "chip"}},
    ]
    plan = assemble_plan(briefs, entries, job_id=1)

    stripped = strip_unjudged_icons_from_plan(plan, llm_call=_llm([1]))

    first, second = stripped["scenes"]
    assert first["overlay"]["icon"] is None and first["overlay"]["text"] == "구조적 취약점"
    assert second["overlay"]["icon"] == "chip"
    assert stripped["icon_judgement"] == {"checked": 2, "removed": 1}


def test_topic_icons_are_removed_per_use_and_flow_loses_all_icons_if_any_fails():
    run = [{"index": 0, "narration": "미국 금리가 오르면 안전자산 수익률이 높아지고 한국 증시가 직격을 받습니다."}]
    topics = [
        {"template": "title_number", "scene": 0, "title": "금리", "number": {"value": "5.3퍼센트"}, "icon": "scissors"},
        {"template": "flow", "scene": 0, "nodes": [{"label": "금리 상승", "icon": "chart_up"}, {"label": "한국 증시", "icon": "gear"}]},
        {"template": "compare_pair", "scene": 0, "a": {"label": "금리", "icon": "bank"}, "b": {"label": "증시", "icon": "chart_down"}},
    ]
    # id 순서: title_number.icon(0), flow node 0(1), flow node 1(2), compare a(3), compare b(4)
    result = strip_unjudged_icons_from_topics(topics, run, llm_call=_llm([1, 3, 4]))

    assert result[0].get("icon") is None
    assert all(n.get("icon") is None for n in result[1]["nodes"])          # 일부만 통과 → 흐름은 전부 뺀다
    assert result[2]["a"]["icon"] == "bank" and result[2]["b"]["icon"] == "chart_down"


def test_missing_flow_icons_are_filled_from_the_library_only_when_every_node_gets_one():
    """2026-10-18 운영자 피드백: 흐름도 노드가 글자 상자뿐이다 — 라이브러리에 있는 그림만 붙이고, 일부만 붙으면 구조가 깨지므로 적용하지 않는다."""
    import json

    from app.services.slide_icon_judge import fill_missing_icons_in_topics, suggest_icons

    run = [{"index": 0, "narration": "자사주 매입이 은행의 하방을 받쳐 줍니다."}]
    topic = {"template": "flow", "scene": 0, "nodes": [{"label": "자사주 매입"}, {"label": "하방 지지"}]}

    def reply(results):
        return lambda system, messages, max_tokens: json.dumps({"results": results}, ensure_ascii=False)

    both = fill_missing_icons_in_topics([dict(topic, nodes=[dict(n) for n in topic["nodes"]])], run,
                                        llm_call=reply([{"id": 0, "icon": "buy"}, {"id": 1, "icon": "shield"}]))
    assert [n["icon"] for n in both[0]["nodes"]] == ["buy", "shield"]

    partial = fill_missing_icons_in_topics([dict(topic, nodes=[dict(n) for n in topic["nodes"]])], run,
                                           llm_call=reply([{"id": 0, "icon": "buy"}, {"id": 1, "icon": ""}]))
    assert all("icon" not in n for n in partial[0]["nodes"])                 # 일부만이면 모두 글자로 둔다

    assert suggest_icons([{"id": 0, "label": "x", "narration": "y"}], llm_call=reply([{"id": 0, "icon": "없는그림"}])) == {}   # 라이브러리에 없는 이름은 버린다
