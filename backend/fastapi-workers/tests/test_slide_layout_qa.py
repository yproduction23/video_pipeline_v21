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


import pytest


@pytest.fixture(autouse=True)
def allow_one_red_topic(monkeypatch):
    """이 모듈은 템플릿·수명·대체 경로의 메커니즘을 확인하므로 빨간 글 주제 1개를 허용한다(운영 기본값 0은 test_slide_summary_policy에서 확인)."""
    from app.services import slide_sequence

    monkeypatch.setattr(slide_sequence, "RED_TOPICS_PER_SEQUENCE", 1)


def _plan(duration=6.0, overlays=None):
    briefs = {i: {"narration": t, "context": "", "duration": duration} for i, t in enumerate(NARRATIONS)}
    entries = [{"index": i, "background": "board", "overlay": (overlays or {}).get(i, {"type": "key_text", "text": t[:8]})}
               for i, t in enumerate(NARRATIONS)]
    return assemble_plan(briefs, entries, job_id=1)


def _approving(payload):
    import re

    def call(system, messages, max_tokens):
        if "아이콘 검수자" in system:
            return json.dumps({"ok": [int(i) for i in re.findall(r"^(\d+)\. (?:\[기준: \w+\] )?아이콘=", messages[0]["content"], re.M)]})
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


def test_first_element_of_the_video_opens_early_and_lands_on_the_spoken_phrase():
    """첫 장면(후킹)의 첫 그래픽은 0.2초부터 천천히 움직이고 말하는 순간(약 2.3~2.9초)에 완성된다 — 말하기 전에 화면이 비지 않는다."""
    topic = {"template": "headline_sub", "scene": 0, "anchor": "미국 금리가 올라가면", "headline": "미국 금리가 올라가면"}
    seq = _seq([topic])

    lead = min((e for e in seq["elements"] if e["topic"] == 1), key=lambda e: e["at"])
    assert lead["at"] == 0.2 and lead.get("opening") is True
    assert 0.2 + lead["enter"] >= 2.3                                                    # 말하는 순간(2.3초 이후)에 완성된다


def test_a_gray_board_run_without_any_overlay_never_invents_narration_fragments():
    """2026-10-18: 비는 칠판을 원문 조각("외국인은 그")으로 채우면 뜻 없는 빨간 글씨가 나온다 — 채울 검증된 후편집이 없으면 비워 둔다."""
    plan = _plan(overlays={0: None})
    run = board_runs(plan)[0]
    plan["scenes"][0]["overlay"] = None
    seq = design_sequence(run, plan, llm_call=lambda s, m, t: "not json")

    shown = [p.get("text") for p in expand_primitives(seq) if p["kind"] == "text"]
    assert all(text and text in NARRATIONS[1] + NARRATIONS[2] + "외국인 이탈과 자사주" for text in shown)  # 장면 1·2의 검증된 후편집뿐
    assert not any(text in ("외국인은 그",) for text in shown)


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


def test_fallback_never_repeats_a_subtitle_line_as_red_text():
    """2026-10-18 운영자 피드백(4:17): 자막 줄을 그대로 빨간 글씨로 또 쓴다 — 설계 실패 시 장면별 후편집도 자막과 겹치면 쓰지 않는다."""
    briefs = {0: {"narration": "핵심 변수는 미국 금리입니다.", "context": "", "duration": 4.0,
                  "chunks": [{"text": "핵심 변수는 미국 금리입니다.", "start": 0.0, "end": 4.0}]}}
    plan = assemble_plan(briefs, [{"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "핵심 변수는 미국 금리입니다"}}], job_id=1)
    run = board_runs(plan)[0]

    seq = design_sequence(run, plan, llm_call=lambda s, m, t: "not json")

    assert seq is None or not [e for e in seq["elements"] if e["kind"] == "text"]


def test_pictogram_shows_a_big_icon_with_a_short_verbatim_label_in_the_center():
    topic = {"template": "pictogram", "scene": 2, "anchor": "삼성전자와", "icon": "chip", "label": "AI 반도체"}
    seq = _seq([topic])

    prims = expand_primitives(seq)
    icon = [p for p in prims if p["kind"] == "icon"]
    label = [p for p in prims if p["kind"] == "text" and p["text"] == "AI 반도체"]
    assert icon and label
    assert icon[0]["pos"][0] == 960 and label[0]["pos"][0] == 960 and icon[0]["pos"][1] < label[0]["pos"][1]
    assert [e["content"]["text"] for e in seq["elements"] if e["kind"] == "text" and e["topic"] == 1] == ["AI 반도체"]  # 라벨은 원문 구절 그대로


def test_a_number_the_previous_scene_already_showed_is_not_repeated_by_the_sequence():
    """2026-10-18 운영자 피드백(1:29): 앞 장면에서 나온 같은 5.3%가 뒤에서 또 나온다."""
    plan = _plan()
    plan["scenes"][0]["background"] = "stock"
    plan["scenes"][0]["overlay"] = {"type": "big_number", "value": "5.3%", "label": ""}
    plan["scenes"][1]["narration"] = "금리가 5.3%를 넘었습니다."
    topic = {"template": "title_number", "scene": 1, "anchor": "5.3%", "number": {"value": "5.3%", "label": ""}}
    run = board_runs(plan)[0]

    seq = design_sequence(run, plan, llm_call=_approving({"topics": [topic]}))

    assert not [e for e in (seq or {"elements": []})["elements"] if e["kind"] == "number"]


def test_concept_candidates_on_scenes_with_red_key_text_are_picked_first():
    from app.services.slide_concepts import apply_concepts

    plan = _scenes_plan_for_priority()
    concepts = {"concepts": [
        {"index": 0, "evokes": "x", "tool": "motif_chart", "tool_ready": True, "motifs": [], "importance": 2,
         "params": {"direction": "fall", "label": "외국인이"}},
        {"index": 1, "evokes": "x", "tool": "motif_chart", "tool_ready": True, "motifs": [], "importance": 2,
         "params": {"direction": "fall", "label": "외국인이"}},
    ], "missing": []}

    updated = apply_concepts(plan, concepts)

    assert (updated["scenes"][1]["overlay"] or {}).get("type") == "motion_part"      # 같은 중요도면 빨간 글 장면이 먼저
    assert (updated["scenes"][0]["overlay"] or {}).get("type") != "motion_part"      # 같은 설명이라 60초 안 반복은 막힌다


def _scenes_plan_for_priority():
    from app.services.slide_edit_plan import assemble_plan

    briefs = {i: {"narration": f"외국인이 팔았고 자금이 빠져나갔습니다 {i}.", "duration": 4.0} for i in range(2)}
    entries = [{"index": 0, "background": "stock", "stock_queries": ["q"], "overlay": None},
               {"index": 1, "background": "stock", "stock_queries": ["q"], "overlay": {"type": "key_text", "text": "외국인이 팔았고"}}]
    return assemble_plan(briefs, entries, job_id=1)


def test_red_headline_is_limited_per_sequence_and_the_role_hint_reaches_the_design_prompt():
    """2026-10-18 운영자 합의: 빨간 글은 구간당 1개까지, 장면의 역할([역할: ...])이 설계 입력에 실린다."""
    plan = _plan()
    plan["visual_concepts"] = {"concepts": [{"index": 0, "role": "claim", "tool": "none", "evokes": ""},
                                            {"index": 1, "role": "action", "tool": "motif_move", "evokes": "자금이 빠져나가는 상자"}]}
    prompts = []

    def llm(system, messages, max_tokens):
        if "아이콘 검수자" in system:
            return json.dumps({"ok": []})
        prompts.append(messages[0]["content"])
        topics = [{"template": "headline_sub", "scene": 0, "anchor": "외국인 이탈과", "headline": "외국인 이탈과"},
                  {"template": "headline_sub", "scene": 1, "anchor": "완충재 감소", "headline": "완충재 감소"}]
        return json.dumps({"topics": topics}, ensure_ascii=False)

    seq = design_sequence(board_runs(plan)[0], plan, llm_call=llm)

    assert "[역할: claim]" in prompts[0] and "[역할: action / 떠올릴 그림: 자금이 빠져나가는 상자]" in prompts[0]
    red_topics = {e["topic"] for e in seq["elements"] if e["topic"] > 0 and e["kind"] == "text" and e["content"].get("color") == "red"}
    assert len(red_topics) <= 1


def test_a_sequence_too_close_to_the_previous_red_text_is_redesigned_without_red_text():
    from app.services.slide_sequence import RED_TEXT_MIN_GAP_SECONDS, attach_sequences

    plan = _plan()
    plan["scenes"][1]["background"] = "stock"                      # 구간 둘: 장면 0, 장면 2
    calls = []

    def llm(system, messages, max_tokens):
        if "아이콘 검수자" in system:
            import re
            return json.dumps({"ok": [int(i) for i in re.findall(r"^(\d+)\. (?:\[기준: \w+\] )?아이콘=", messages[0]["content"], re.M)]})
        text = messages[0]["content"]
        calls.append("빨간 글 강조(headline_sub)를 쓰지 않는다" in text)
        scene = 0 if "외국인 이탈과" in text and "삼성전자와" not in text else 2
        anchor = "외국인 이탈과" if scene == 0 else "삼성전자와"
        if "빨간 글 강조(headline_sub)를 쓰지 않는다" in text:
            topic = {"template": "pictogram", "scene": scene, "anchor": anchor, "icon": "chip", "label": anchor}
        else:
            topic = {"template": "headline_sub", "scene": scene, "anchor": anchor, "headline": anchor}
        return json.dumps({"topics": [topic]}, ensure_ascii=False)

    attach_sequences(plan, llm_call=llm)

    assert RED_TEXT_MIN_GAP_SECONDS == 20.0
    assert any(calls)                                               # 두 번째 구간은 빨간 글 없이 다시 설계됐다
    second = [q for q in plan["sequences"] if 2 in q["scenes"]][0]
    assert not [e for e in second["elements"] if e["topic"] > 0 and e["kind"] == "text" and e["content"].get("color") == "red"]


def test_red_text_spacing_counts_fillers_too():
    """빈 구간 보충 요소(topic 0)의 빨간 글도 앞 빨간 글과의 20초 간격에 센다."""
    from app.services.slide_sequence import _red_elements

    seq = {"elements": [{"kind": "text", "topic": 0, "content": {"text": "x", "color": "red"}},
                        {"kind": "text", "topic": 1, "content": {"text": "y", "color": "gray"}}]}

    assert len(_red_elements(seq)) == 1


def test_the_opening_graphic_starts_moving_before_the_first_phrase_and_lands_when_it_is_spoken():
    """2026-10-19 운영자 결정(후킹): 첫 장면은 말하기 전부터 천천히 움직이기 시작해 말하는 순간에 완성된다. 다른 구간은 말하는 순간 맞춤을 그대로 지킨다."""
    from app.services.slide_sequence import OPENING_START_SECONDS, _open_with_motion

    elements = [{"id": "e1", "kind": "number", "topic": 1, "at": 1.7, "until": 4.0, "content": {"value": "100조 원", "label": ""}},
                {"id": "e2", "kind": "text", "topic": 1, "at": 2.4, "until": 4.0, "content": {"text": "역사상 처음", "color": "gray"}}]

    opened = _open_with_motion([dict(e) for e in elements], [{"index": 0}])

    first = opened[0]
    assert first["at"] == OPENING_START_SECONDS and first["opening"] is True
    assert 1.8 <= first["enter"] <= 2.4                      # 1.7 + 0.6 = 2.3초에 완성 → 0.2초부터 약 2.1초 동안 천천히
    assert opened[1]["at"] == 2.4                            # 뒤따르는 요소는 그대로
    untouched = _open_with_motion([dict(e) for e in elements], [{"index": 5}])
    assert untouched[0]["at"] == 1.7 and "enter" not in untouched[0]
