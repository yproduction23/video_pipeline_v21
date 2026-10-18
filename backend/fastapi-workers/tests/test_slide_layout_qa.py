"""레이아웃 검수(2026-10-18 운영자 피드백): 흐름도 화살표와 상자가 겹친다, 목록이 왼쪽으로 치우쳤다, 회색 칠판이 비어 있다,
첫 요소가 말하기 전에 먼저 나온다. 모든 템플릿을 긴 글자로 겹침 검사한다."""
import json

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_sequence import (
    _tight_rect, board_runs, design_sequence, expand_primitives, resolve_lifetimes,
)

NARRATIONS = [
    "외국인 이탈과 자사주 매입 종료가 겹쳤습니다. 미국 금리가 올라가면 안전자산 수익률이 높아집니다.",
    "완충재 감소, 외부 충격, 낙폭 확대가 차례로 이어졌습니다.",
    "삼성전자와 SK하이닉스, AI 반도체가 핵심입니다.",
]


def _plan(duration=6.0, overlays=None):
    briefs = {i: {"narration": t, "context": "", "duration": duration} for i, t in enumerate(NARRATIONS)}
    entries = [{"index": i, "background": "board", "overlay": (overlays or {}).get(i, {"type": "key_text", "text": t[:8]})}
               for i, t in enumerate(NARRATIONS)]
    return assemble_plan(briefs, entries, job_id=1)


def _approving(payload):
    import re

    def call(system, messages, max_tokens):
        if "아이콘 검수자" in system:
            return json.dumps({"ok": [int(i) for i in re.findall(r"^(\d+)\. 아이콘=", messages[0]["content"], re.M)]})
        return json.dumps(payload, ensure_ascii=False)

    return call


def _seq(topics, plan=None):
    plan = plan or _plan()
    return design_sequence(board_runs(plan)[0], plan, llm_call=_approving({"topics": topics}))


def _overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def test_flow_nodes_and_arrows_never_overlap_even_with_long_labels():
    topic = {"template": "flow", "scene": 1, "anchor": "완충재 감소",
             "nodes": [{"label": "완충재 감소"}, {"label": "외부 충격"}, {"label": "낙폭 확대"}]}
    seq = _seq([topic])

    prims = expand_primitives(seq)
    nodes = sorted([p for p in prims if p["kind"] == "node"], key=lambda p: p["pos"][0])
    arrows = [p for p in prims if p["kind"] == "arrow"]
    assert len(nodes) == 3 and len(arrows) == 2
    rects = [_tight_rect(p) for p in nodes] + [_tight_rect(p) for p in arrows]
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not _overlap(a, b)
    assert min(r[0] for r in rects) >= 40 and max(r[2] for r in rects) <= 1880      # 화면 안에 들어온다


def test_list_card_is_centered_on_the_screen_not_pushed_to_the_left():
    topic = {"template": "list", "scene": 2, "anchor": "삼성전자와", "title": "AI 반도체 관련주",
             "items": ["삼성전자", "SK하이닉스"]}
    seq = _seq([topic])

    shown = {"■ 정리", "AI 반도체 관련주", "1", "2", "삼성전자", "SK하이닉스"}
    prims = [p for p in expand_primitives(seq) if p["kind"] == "rule" or p.get("text") in shown]
    rects = [_tight_rect(p) for p in prims]
    left, right = min(r[0] for r in rects), max(r[2] for r in rects)
    assert abs((left + right) / 2 - 960) < 120                                         # 블록이 화면 가운데에 있다


def test_a_gray_board_run_never_goes_blank_in_the_middle():
    """설계가 실패해 장면별 후편집으로 대체될 때, 후편집이 없는 장면 구간에서 앞 요소를 이어 둔다(2분 42초 빈 칠판)."""
    plan = _plan(overlays={1: None})
    run = board_runs(plan)[0]
    plan["scenes"][1]["overlay"] = None
    seq = design_sequence(run, plan, llm_call=lambda s, m, t: "not json")

    prims = expand_primitives(seq)
    covered = sorted((p["at"], p["until"]) for p in prims)
    cursor = 0.0
    for start, end in covered:
        assert start - cursor <= 0.6, f"빈 구간 {cursor:.1f}~{start:.1f}초"
        cursor = max(cursor, end)
    assert seq["duration"] - cursor <= 0.6


def test_first_element_waits_for_the_spoken_phrase_up_to_the_opening_limit():
    topic = {"template": "headline_sub", "scene": 0, "anchor": "미국 금리가 올라가면", "headline": "미국 금리가 올라가면"}
    seq = _seq([topic])

    first = min(e["at"] for e in seq["elements"] if e["topic"] == 1)
    assert 2.0 <= first <= 3.0                                                           # 말하기 전에 먼저 뜨지 않는다(첫 요소도 싱크)
    filler = [e for e in seq["elements"] if e["topic"] == 0 and e["at"] < first]
    assert all(e["until"] <= first + 0.2 for e in filler)                                # 그 사이 빈 칠판은 말하는 구절로 채운다


def test_a_gray_board_run_that_starts_without_any_overlay_still_shows_something_early():
    plan = _plan(overlays={0: None})
    run = board_runs(plan)[0]
    plan["scenes"][0]["overlay"] = None
    seq = design_sequence(run, plan, llm_call=lambda s, m, t: "not json")

    first = min(p["at"] for p in expand_primitives(seq))
    assert first <= 3.0                                           # 첫 장면에 후편집이 없어도 그 구절을 말할 때 원문 핵심 구절이 나온다


def test_a_topic_leaves_the_screen_soon_after_its_sentence_ends():
    """2026-10-18 운영자 피드백: 이야기가 다음으로 넘어간 뒤에도 앞 그래픽이 남아 있다 — 문장이 끝나면(여유 1초) 물러난다."""
    from app.services.slide_timing import sentence_end_offset

    topic = {"template": "headline_sub", "scene": 0, "anchor": "외국인 이탈과", "headline": "외국인 이탈과"}
    plan = _plan(duration=12.0)
    seq = _seq([topic], plan)

    end = sentence_end_offset(NARRATIONS[0], [], 12.0, "외국인 이탈과")
    shown = [e for e in seq["elements"] if e["topic"] == 1]
    assert shown and max(e["until"] for e in shown) <= end + 1.0 + 2.5            # 문장 끝 + 여유(최대) 이내
    assert max(e["until"] for e in shown) < 12.0                                  # 장면 끝까지 붙들고 있지 않는다


def test_sync_audit_flags_elements_that_appear_before_their_phrase_is_spoken():
    from app.services.slide_sequence import audit_sequence_sync

    briefs = {0: {"narration": "미국 채권 시장이 흔들렸습니다. 서울도 급락했습니다.", "context": "", "duration": 6.0,
                  "chunks": [{"text": "미국 채권 시장이", "start": 0.0, "end": 2.0}, {"text": "흔들렸습니다. 서울도 급락했습니다.", "start": 2.0, "end": 6.0}]}}
    plan = assemble_plan(briefs, [{"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "서울도 급락했습니다"}}], job_id=1)
    element = {"id": "e1", "topic": 1, "kind": "text", "scene": 0, "anchor": "서울도 급락", "at": 0.2, "until": 5.0,
               "content": {"text": "서울도 급락", "color": "red"}}
    plan["sequences"] = [{"id": 1, "scenes": [0], "elements": [element]}]

    audit = audit_sequence_sync(plan)

    assert audit["checked"] == 1 and audit["out_of_sync"][0]["verdict"] == "early"      # 말하기 3초 전에 떴다
    element["at"] = 3.6
    assert audit_sequence_sync(plan)["out_of_sync"] == []
