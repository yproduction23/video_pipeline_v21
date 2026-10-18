"""연출 단계 재요청(2026-10-18 시험): 부품 인자의 글자가 대본 원문과 달라 거절된 장면이 22개 중 12개였다(중요도 3 포함).
거절된 장면만 이유를 알려 주고 한 번 더 받는다. 그래도 어긋나면 그 장면은 그림 없이 둔다(지어내지 않는다)."""
import json

from app.services.slide_concepts import design_concepts

BRIEFS = {
    0: {"narration": "뉴욕에서 불이 붙으면 서울이 타는 구조였죠."},
    1: {"narration": "자사주 매입이 하방을 받쳐 주는 역할을 합니다."},
}


def _sequence(replies):
    calls = []

    def call(system, messages, max_tokens):
        calls.append(messages[0]["content"])
        return json.dumps({"scenes": replies[min(len(calls) - 1, len(replies) - 1)]}, ensure_ascii=False)

    call.calls = calls
    return call


def test_rejected_part_params_are_asked_again_with_the_reason_and_fixed_ones_replace_them():
    first = [
        {"index": 0, "evokes": "도미노", "tool": "motif_spread", "importance": 3, "params": {"origin": "뉴욕 충격", "effect": "서울 하락"}},
        {"index": 1, "evokes": "기둥", "tool": "motif_support", "importance": 3,
         "params": {"top": "주가", "pillar": "자사주 매입이", "state": "holding"}},   # 라벨 '주가'가 이 장면 대본에 없다
    ]
    second = [
        {"index": 0, "tool": "motif_spread", "params": {"origin": "뉴욕에서", "effect": "서울이"}},
        {"index": 1, "tool": "motif_support", "params": {"top": "하방을", "pillar": "자사주 매입이", "state": "holding"}},
    ]
    llm = _sequence([first, second])

    result = design_concepts(BRIEFS, llm_call=llm)

    assert len(llm.calls) == 2 and "글자 그대로" in llm.calls[1]
    by_scene = {c["index"]: c for c in result["concepts"]}
    assert by_scene[0]["params"] == {"origin": "뉴욕에서", "effect": "서울이"}
    assert by_scene[1]["params"]["top"] == "하방을"


def test_params_that_are_still_wrong_after_the_retry_leave_the_scene_without_a_picture():
    bad = [{"index": 0, "evokes": "도미노", "tool": "motif_spread", "importance": 3, "params": {"origin": "없는 말", "effect": "서울이"}}]
    llm = _sequence([bad, bad])

    result = design_concepts({0: BRIEFS[0]}, llm_call=llm)

    scene = result["concepts"][0]
    assert scene["tool"] == "none" and scene["params"] == {}
    assert len(llm.calls) == 2                                                  # 재요청은 한 번만
