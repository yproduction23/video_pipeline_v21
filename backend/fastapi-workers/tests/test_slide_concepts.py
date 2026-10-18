"""연출 단계(2026-10-17 운영자 요청): 대본 한 줄 한 줄의 행간을 읽고 "시청자가 떠올릴 그림"을 먼저 적은 뒤, 그 그림을 만들 도구를 고른다.
없는 소재·움직임 부품은 즉석에서 그리지 않고 주문 목록으로 모은다(같은 화풍으로 미리 만들어 계속 쓴다)."""
import json

from app.services.slide_concepts import TOOLS, design_concepts

BRIEFS = {
    0: {"narration": "외국인은 이 시점까지 8거래일 연속으로 코스피를 팔았습니다."},
    1: {"narration": "돈이 한꺼번에 빠져나가며 시장이 흔들렸습니다."},
    2: {"narration": "그래서 이게 끝일까요?"},
}


def _llm(scenes):
    return lambda system, messages, max_tokens: json.dumps({"scenes": scenes}, ensure_ascii=False)


def test_concepts_keep_the_picture_the_tool_and_existing_motifs():
    reply = [
        {"index": 0, "evokes": "달력에 하루씩 X가 쳐진다", "tool": "streak", "motifs": [], "missing": []},
        {"index": 1, "evokes": "상자에서 돈다발이 쏟아져 나간다", "tool": "motif_move", "motifs": ["달러 지폐 더미"],
         "missing": [{"type": "motif", "name": "open_box", "description": "열린 상자(자금이 담긴 곳)"}]},
        {"index": 2, "evokes": "", "tool": "none", "motifs": [], "missing": []},
    ]

    result = design_concepts(BRIEFS, llm_call=_llm(reply))

    by_scene = {c["index"]: c for c in result["concepts"]}
    assert by_scene[0]["tool"] == "streak" and by_scene[0]["evokes"] == "달력에 하루씩 X가 쳐진다"
    assert by_scene[1]["motifs"] == ["달러 지폐 더미"]                 # 라이브러리에 있는 소재
    assert by_scene[2]["tool"] == "none"
    assert result["missing"] == [{"type": "motif", "name": "open_box", "description": "열린 상자(자금이 담긴 곳)", "count": 1, "scenes": [1]}]


def test_unknown_tool_becomes_none_and_unknown_motif_is_ordered_instead_of_used():
    reply = [{"index": 1, "evokes": "돈이 빠져나간다", "tool": "magic_wand", "motifs": ["없는 소재"], "missing": []}]

    result = design_concepts(BRIEFS, llm_call=_llm(reply))

    scene = next(c for c in result["concepts"] if c["index"] == 1)
    assert scene["tool"] == "none"
    assert scene["motifs"] == []
    assert any(m["name"] == "없는 소재" for m in result["missing"])        # 사용하지 않고 주문 목록으로


def test_missing_parts_are_merged_across_scenes_and_scenes_without_a_reply_default_to_none():
    reply = [
        {"index": 0, "evokes": "기둥이 바닥을 받친다", "tool": "motif_title_number", "motifs": [],
         "missing": [{"type": "motif", "name": "support_pillar", "description": "바닥을 받치는 기둥"}]},
        {"index": 1, "evokes": "기둥이 받친다", "tool": "motif_title_number", "motifs": [],
         "missing": [{"type": "motif", "name": "support_pillar", "description": "바닥을 받치는 기둥"}]},
    ]

    result = design_concepts(BRIEFS, llm_call=_llm(reply))

    assert [m["count"] for m in result["missing"]] == [2] and result["missing"][0]["scenes"] == [0, 1]
    assert next(c for c in result["concepts"] if c["index"] == 2)["tool"] == "none"


def test_failed_call_returns_no_concepts_instead_of_inventing_any():
    def broken(system, messages, max_tokens):
        raise RuntimeError("api down")

    assert design_concepts(BRIEFS, llm_call=broken) is None
    assert "streak" in TOOLS and "none" in TOOLS
