"""모션 시퀀스: 회색 배경 구간을 장면 경계를 넘어 이어지는 애니메이션으로 (2026-10-16 운영자 요청).

레퍼런스(부동산 영상 10~33초)처럼 요소가 문장 흐름에 맞춰 하나씩 등장하고, 같은 주제 안에서는 쌓이며 끝까지 남는다.
화면을 비우는 것은 주제가 바뀔 때뿐이고, 화면 글자는 기존 검증기를 요소마다 다시 통과해야 하며,
설계가 실패하면 장면별 후편집으로 대체한다.

2026-10-16 20번 Job 1:17~1:33 재현: 요소가 2초마다 같은 자리에서 서로 다른 아이콘으로 교체되고 빈 화면이 생겼다."""
from __future__ import annotations

import json

from PIL import Image

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_sequence import (
    MIN_TOPIC_SECONDS,
    OPENING_LATEST_SECONDS,
    attach_sequences,
    board_runs,
    design_sequence,
    element_rect,
    expand_primitives,
    render_frame,
    resolve_lifetimes,
)

TEXTS = [
    "순자산 10억 원이면 상위 11.8퍼센트에 들어갑니다.",
    "과거에는 상위 7.9퍼센트였습니다.",
    "자산이 현금으로 바뀌는 구조가 핵심입니다.",
    "그래서 시장은 크게 흔들렸습니다.",
]


def _plan(backgrounds=("board", "board", "board", "stock"), duration=4.0):
    briefs = {i: {"narration": t, "context": "", "duration": duration} for i, t in enumerate(TEXTS)}
    entries = []
    for i, background in enumerate(backgrounds):
        entry = {"index": i, "background": background}
        if background == "stock":
            entry["stock_queries"] = ["city skyline", "office"]
        else:
            entry["overlay"] = {"type": "key_text", "text": TEXTS[i][:10]}
        entries.append(entry)
    return assemble_plan(briefs, entries, job_id=1)


def _llm(payload):
    return lambda system, messages, max_tokens: json.dumps(payload, ensure_ascii=False)


DESIGN = {"elements": [
    {"id": "e1", "topic": 1, "kind": "icon", "scene": 0, "anchor": "순자산 10억 원", "slot": "left", "anim": "rise",
     "content": {"name": "money_stack"}},
    {"id": "e2", "topic": 1, "kind": "number", "scene": 0, "anchor": "상위 11.8퍼센트", "slot": "right", "anim": "count",
     "content": {"value": "11.8퍼센트", "label": "현재"}},
    {"id": "e3", "topic": 1, "kind": "number", "scene": 1, "anchor": "상위 7.9퍼센트", "slot": "right", "anim": "count",
     "replaces": "e2", "content": {"value": "7.9퍼센트", "label": "과거"}},
    {"id": "e4", "topic": 2, "kind": "flow", "scene": 2, "anchor": "자산이 현금으로", "slot": "center", "anim": "rise",
     "content": {"nodes": [{"label": "자산", "icon": "building"}, {"label": "현금", "icon": "money_stack"}]}},
    {"id": "e5", "topic": 1, "kind": "text", "scene": 0, "anchor": "상위", "slot": "top", "anim": "rise",
     "content": {"text": "원문에 없는 선동 문구입니다", "color": "red"}},   # 원문 불일치 → 버려진다
    {"id": "e6", "topic": 1, "kind": "icon", "scene": 0, "anchor": "", "slot": "left", "anim": "rise",
     "content": {"name": "unicorn"}},                                       # 없는 아이콘 → 버려진다
]}


def _el(element_id, kind, slot, at, topic, content=None, **extra):
    defaults = {
        "icon": {"name": "chip"}, "number": {"value": "11.8퍼센트", "label": ""},
        "text": {"text": "핵심 문구", "color": "red"}, "arrow": {"direction": "up"},
        "bars": {"title": "비교", "items": [{"label": "가", "value": 1.0, "value_text": "1퍼센트"},
                                           {"label": "나", "value": 2.0, "value_text": "2퍼센트"}]},
        "flow": {"nodes": [{"label": "자산", "icon": "building"}, {"label": "현금", "icon": "money_stack"}]},
    }
    return {"id": element_id, "kind": kind, "slot": slot, "at": at, "topic": topic, "scene": 0, "anchor": "",
            "anim": {"number": "count", "bars": "grow", "arrow": "draw"}.get(kind, "rise"),
            "content": content or defaults[kind], **extra}


def _alive(elements, t):
    return [e for e in elements if e["at"] <= t < e["until"]]


def _overlap(a, b):
    ra, rb = element_rect(a), element_rect(b)
    return not (ra[2] <= rb[0] or rb[2] <= ra[0] or ra[3] <= rb[1] or rb[3] <= ra[1])


def test_board_runs_group_consecutive_board_scenes():
    plan = _plan(("board", "board", "stock", "board"))

    assert [[s["index"] for s in run] for run in board_runs(plan)] == [[0, 1], [3]]


def test_design_keeps_only_verified_elements_and_times_them_by_the_spoken_phrase():
    plan = _plan(duration=6.0)
    run = board_runs(plan)[0]

    seq = design_sequence(run, plan, llm_call=_llm(DESIGN))

    ids = [e["id"] for e in seq["elements"] if not e["id"].startswith("b")]  # b*는 빈 구간 보충 요소
    assert ids == ["e1", "e2", "e3", "e4"]  # 원문 불일치(e5)·없는 아이콘(e6)은 제외
    assert seq["source"] == "claude" and seq["duration"] == 18.0
    by_id = {e["id"]: e for e in seq["elements"]}
    assert by_id["e1"]["at"] >= 0  # 첫 요소도 그 구절을 말하는 순간에 나온다(앞당기지 않는다)
    assert 0 <= by_id["e2"]["at"] < 6.0  # 장면 0 안
    assert 6.0 <= by_id["e3"]["at"] < 12.0  # 장면 1 안
    assert 12.0 <= by_id["e4"]["at"] < 18.0  # 장면 2 안
    assert seq["offsets"] == {"0": 0.0, "1": 6.0, "2": 12.0}


def test_same_topic_accumulates_and_only_a_topic_change_clears_the_screen():
    elements = [
        _el("a", "icon", "left", 0.3, 1), _el("b", "number", "right", 1.5, 1),
        _el("c", "text", "top", 3.0, 1),
        _el("d", "flow", "center", 9.0, 2),
    ]

    kept = resolve_lifetimes(elements, 16.0)

    until = {e["id"]: e["until"] for e in kept}
    assert until["a"] == until["b"] == until["c"] == 9.0 + 0.12  # 주제가 끝날 때까지 남는다(2초 만에 교체되지 않는다), 전환 때 잠깐 교차
    assert until["d"] == 16.0


def test_screen_is_never_empty_between_topics():
    elements = [
        _el("a", "text", "top", 0.3, 1), _el("b", "number", "right", 2.0, 1),
        _el("c", "text", "top", 8.0, 2), _el("d", "number", "right", 9.5, 2),
    ]

    kept = resolve_lifetimes(elements, 15.0)

    for step in range(3, 148):
        assert _alive(kept, step / 10), f"{step / 10}초에 화면이 비었다"


def test_a_short_topic_is_merged_into_the_previous_one():
    elements = [
        _el("a", "text", "top", 0.3, 1), _el("b", "number", "right", 2.0, 1),
        _el("c", "icon", "left", 9.0, 2), _el("d", "text", "bottom", 9.5, 2),  # 3초짜리 주제: 앞 주제에 합쳐진다
    ]

    kept = resolve_lifetimes(elements, 12.0)

    assert (12.0 - 9.0) < MIN_TOPIC_SECONDS
    assert {e["until"] for e in kept} == {12.0}  # 중간에 화면이 비거나 교체되지 않는다
    assert len({e["topic"] for e in kept}) == 1


def test_a_topic_with_only_icons_or_arrows_is_dropped_and_the_previous_one_continues():
    elements = [
        _el("a", "text", "top", 0.3, 1), _el("b", "number", "right", 2.0, 1),
        _el("c", "icon", "left", 8.0, 2), _el("d", "arrow", "right", 9.0, 2),
        _el("e", "text", "top", 14.0, 3),
    ]

    kept = resolve_lifetimes(elements, 20.0, merge_short=False)

    assert [e["id"] for e in kept] == ["a", "b", "e"]
    assert {e["id"]: e["until"] for e in kept}["a"] == 14.0 + 0.12


def test_elements_that_would_overlap_are_relocated_or_dropped():
    """2026-10-16 재현: 큰 숫자가 막대 그래프 글자와 겹쳐 그려졌다."""
    elements = [
        _el("a", "bars", "center", 0.3, 1),
        _el("b", "number", "right", 2.0, 1),
        _el("c", "icon", "left", 3.0, 1),
    ]

    kept = resolve_lifetimes(elements, 12.0)

    assert "a" in {e["id"] for e in kept}
    assert "b" not in {e["id"] for e in kept}  # 막대와 겹치는 큰 숫자는 놓을 자리가 없어 버려진다
    for first in kept:
        for second in kept:
            if first is not second:
                assert not _overlap(first, second)


def test_non_overlapping_pairs_keep_their_slots_and_conflicting_slots_move_to_a_free_one():
    elements = [
        _el("a", "icon", "left", 0.3, 1), _el("b", "number", "right", 1.5, 1),
        _el("c", "icon", "left", 3.0, 1, content={"name": "bank"}),  # 같은 자리(left): 교체가 아니라 빈 자리로 옮겨 쌓인다
    ]

    kept = resolve_lifetimes(elements, 12.0)

    slots = {e["id"]: e["slot"] for e in kept}
    assert slots["a"] == "left" and slots["b"] == "right"
    assert "c" not in slots or slots["c"] not in ("left", "right")
    for first in kept:
        for second in kept:
            if first is not second:
                assert not _overlap(first, second)


def test_a_number_can_update_in_place_but_an_icon_never_replaces_another_icon():
    elements = [
        _el("n1", "number", "right", 0.3, 1, content={"value": "11.2퍼센트", "label": ""}),
        _el("n2", "number", "right", 3.0, 1, content={"value": "11.8퍼센트", "label": ""}, replaces="n1"),
        _el("i1", "icon", "left", 0.4, 1),
        _el("i2", "icon", "left", 3.5, 1, content={"name": "warning"}, replaces="i1"),
    ]

    kept = resolve_lifetimes(elements, 12.0)

    by_id = {e["id"]: e for e in kept}
    assert by_id["n1"]["until"] == by_id["n2"]["at"] == 3.0  # 같은 종류 숫자는 제자리에서 값만 바뀐다
    assert by_id["n2"]["slot"] == "right"
    assert "i2" not in by_id or by_id["i1"]["until"] == 12.0  # 아이콘은 다른 아이콘으로 교체되지 않는다


def test_an_element_that_would_appear_right_before_the_topic_ends_is_dropped():
    elements = [
        _el("a", "text", "top", 0.3, 1), _el("b", "number", "right", 1.5, 1),
        _el("late", "icon", "left", 7.2, 1),
        _el("c", "text", "top", 8.0, 2),
    ]

    kept = resolve_lifetimes(elements, 14.0)

    assert "late" not in {e["id"] for e in kept}  # 8초에 주제가 바뀌는데 0.8초만 보이는 요소는 만들지 않는다


def test_failed_design_falls_back_to_the_per_scene_overlays():
    plan = _plan()
    run = board_runs(plan)[0]

    seq = design_sequence(run, plan, llm_call=lambda s, m, t: "not json")

    assert seq["source"] == "fallback_overlays"
    assert [e["scene"] for e in seq["elements"]] == [0, 1, 2]
    assert all(e["kind"] == "text" for e in seq["elements"])
    for step in range(3, 118):
        assert _alive(seq["elements"], step / 10)  # 대체 경로에서도 화면이 비지 않는다


def test_attach_sequences_reuses_unchanged_runs_by_signature():
    calls = []

    def llm(system, messages, max_tokens):
        calls.append(1)
        return json.dumps(DESIGN, ensure_ascii=False)

    plan = _plan(duration=6.0)
    attach_sequences(plan, llm_call=llm, workers=1)
    first = json.loads(json.dumps(plan["sequences"]))
    attach_sequences(plan, llm_call=llm, workers=1)

    assert len(calls) == 1  # 두 번째는 서명이 같아 재사용
    assert plan["sequences"] == first
    assert [s.get("sequence_id") for s in plan["scenes"]] == [1, 1, 1, None]


def test_frames_are_continuous_elements_appear_over_time_and_persist():
    plan = _plan(duration=6.0)
    run = board_runs(plan)[0]
    seq = design_sequence(run, plan, llm_call=_llm(DESIGN))
    prims = expand_primitives(seq)
    base = Image.new("RGB", (1920, 1080), (46, 47, 50))
    cache: dict = {}

    def painted(t):
        frame = render_frame(base, prims, t, cache)
        return sum(1 for px in frame.resize((96, 54)).getdata() if px != (46, 47, 50))

    empty, early, later = painted(0.05), painted(1.8), painted(4.0)

    assert early > empty  # 요소가 나타난다
    assert later >= early  # 앞 요소가 남은 채 새 요소가 쌓인다(누적)


def test_number_counts_up_and_flow_nodes_arrive_one_by_one():
    plan = _plan(duration=6.0)
    run = board_runs(plan)[0]
    seq = design_sequence(run, plan, llm_call=_llm(DESIGN))
    prims = expand_primitives(seq)
    flow = [p for p in prims if p["kind"] in ("node", "arrow") and p["element"] == "e4"]

    assert [p["kind"] for p in flow] == ["node", "arrow", "node"]
    assert flow[0]["at"] < flow[1]["at"] < flow[2]["at"]


def test_render_sequence_clip_writes_an_mp4_for_just_the_scene_slice(tmp_path):
    from app.services.slide_overlay import ensure_board_background
    from app.services.slide_sequence import render_sequence_clip

    plan = _plan(duration=6.0)
    seq = design_sequence(board_runs(plan)[0], plan, llm_call=_llm(DESIGN))
    board = tmp_path / "board.png"
    ensure_board_background(str(board))
    out = tmp_path / "scene1.mp4"

    ok = render_sequence_clip(seq, 6.0, 6.6, str(board), str(out))

    assert ok and out.is_file() and out.stat().st_size > 1000


def test_long_number_labels_are_shortened_at_a_word_boundary_not_mid_word():
    plan = _plan()
    run = board_runs(plan)[0]
    design = {"elements": [{
        "id": "e1", "topic": 1, "kind": "number", "scene": 0, "anchor": "상위 11.8퍼센트", "slot": "right", "anim": "count",
        "content": {"value": "11.8퍼센트", "label": "순자산 상위 비중입니다"},
    }]}

    seq = design_sequence(run, plan, llm_call=_llm(design))

    assert next(e for e in seq["elements"] if e["id"] == "e1")["content"]["label"] in ("", "순자산 상위")  # 중간에서 잘린 "순자산 상위 비"가 아니다


def test_text_sprite_handles_text_with_line_breaks():
    from app.services.slide_sequence import _text_sprite

    sprite = _text_sprite("첫째 줄\n둘째 줄", 60, (255, 255, 255))

    assert sprite.height > _text_sprite("첫째 줄", 60, (255, 255, 255)).height  # 두 줄로 그려진다


def test_a_centered_text_element_stays_centered_instead_of_being_moved_to_a_corner():
    """2026-10-16 9020 시험 영상: 장면별 후편집으로 대체된 빨간 문구가 왼쪽 구석에 작게 나왔다(중앙 문구를 빈 자리로 옮기던 규칙)."""
    elements = [_el("a", "text", "center", 0.3, 1, content={"text": "핵심 문구", "color": "red"})]

    kept = resolve_lifetimes(elements, 8.0)

    assert kept[0]["slot"] == "center"


def test_gap_fill_does_not_repeat_content_already_on_screen_or_clause_length_text():
    from app.services.slide_sequence import _fill_gaps

    plan = _plan(duration=6.0)
    run = board_runs(plan)[0]
    plan["scenes"][1]["overlay"] = {"type": "key_text", "text": "과거에는 상위 7.9퍼센트였습니다"}  # 16자 초과 절
    plan["scenes"][2]["overlay"] = {"type": "key_text", "text": "자산이 현금으로 바뀌는"}
    existing = [{"id": "e1", "kind": "text", "scene": 0, "topic": 1, "at": 0.3, "until": 18.0, "slot": "center", "anim": "rise",
                 "anchor": "", "replaces": None, "content": {"text": "자산이 현금으로 바뀌는", "color": "red"}}]

    filled = _fill_gaps(existing, run, plan)

    assert filled == existing  # 이미 화면에 있는 문구, 절 길이 문구는 보충하지 않는다


def test_a_short_topic_slows_the_flow_so_each_step_still_has_time_to_land():
    plan = _plan(duration=6.0)
    run = board_runs(plan)[0]
    design = {"topics": [{"template": "flow", "scene": 2, "anchor": "자산이 현금으로",
                          "nodes": [{"label": "자산이"}, {"label": "현금으로"}, {"label": "구조가"}]}]}

    seq = design_sequence(run, plan, llm_call=_llm(design))
    flow = [e for e in seq["elements"] if e["kind"] == "flow"][0]
    flow["until"] = flow["at"] + 3.0  # 3초짜리 주제
    prims = [p for p in expand_primitives({"elements": [flow]}) if p["kind"] == "node"]

    assert prims[-1]["at"] - flow["at"] <= 1.6  # 짧은 주제에서는 간격을 줄여 마지막 단계도 충분히 머문다


def test_a_topic_change_overlaps_briefly_so_the_screen_never_flashes_empty():
    """2026-10-16 9022 시험 영상: 주제가 바뀔 때 앞 요소는 사라지고 새 요소는 투명에서 시작해 화면이 0.25~1초씩 24번 비었다.
    앞 주제의 요소를 새 주제가 나온 직후까지 잠깐 남겨 교차한다."""
    elements = [_el("a", "text", "top", 0.3, 1), _el("b", "number", "right", 2.0, 1),
                _el("c", "text", "top", 8.0, 2), _el("d", "number", "right", 9.0, 2)]

    kept = resolve_lifetimes(elements, 15.0)

    until_first = {e["until"] for e in kept if e["topic"] == 1}
    assert until_first == {8.0 + 0.12}
    assert [e["until"] for e in kept if e["topic"] == 2] == [15.0, 15.0]


def test_the_opening_element_appears_no_later_than_the_opening_limit():
    plan = _plan(duration=6.0)
    run = board_runs(plan)[0]

    seq = design_sequence(run, plan, llm_call=_llm(DESIGN))

    # 말하는 구절에 맞추되 칠판이 오래 비지 않게 늦어도 OPENING_LATEST_SECONDS(1.8초)에는 나온다.
    assert min(e["at"] for e in seq["elements"]) <= 1.8 + 1e-6


def test_list_is_laid_out_like_the_reference_with_left_aligned_numbered_rows_under_a_rule():
    """2026-10-17 운영자 요청: 막대·목록 템플릿을 레퍼런스처럼 — 왼쪽 정렬 제목 + 구분선 + 노란 번호가 붙은 항목."""
    element = {"id": "e1", "kind": "list", "slot": "center", "at": 0.2, "until": 9.0, "anim": "rise", "topic": 1,
               "content": {"title": "낙폭의 원인", "items": ["실적 훼손", "수급 불안", "금리 상승"]}}

    prims = expand_primitives({"elements": [element]})

    texts = [p for p in prims if p["kind"] == "text"]
    numbers = [p for p in texts if p["text"] in ("1", "2", "3")]
    items = [p for p in texts if p["text"] in ("실적 훼손", "수급 불안", "금리 상승")]
    assert [p["text"] for p in numbers] == ["1", "2", "3"] and len({p["pos"][0] for p in numbers}) == 1  # 번호가 한 줄로 정렬
    assert all(p.get("align") == "left" for p in [*numbers, *items])
    assert len({p["pos"][0] for p in items}) == 1 and items[0]["pos"][0] > numbers[0]["pos"][0]
    assert any(p["kind"] == "rule" for p in prims)  # 제목 아래 구분선
    assert [p["pos"][1] for p in items] == sorted(p["pos"][1] for p in items)
    assert all(a["at"] < b["at"] for a, b in zip(items, items[1:]))  # 말하는 순서대로 하나씩


def test_bars_have_yellow_value_labels_and_a_baseline_like_the_reference():
    from app.services.slide_sequence import _bars_sprite

    sprite = _bars_sprite("외국인 순매도", [
        {"label": "7월", "value": 13.1, "value_text": "13.1조"}, {"label": "10월", "value": 9.9, "value_text": "9.9조"}], 1.0)

    pixels = list(sprite.convert("RGBA").getdata())
    yellow = [p for p in pixels if p[3] > 200 and p[0] > 230 and 150 < p[1] < 215 and p[2] < 80]
    assert len(yellow) > 20000  # 막대와 값 글자가 노랗다
    baseline = sprite.getpixel((30, 190 + 380 + 2))  # 첫 막대 왼쪽 바깥, 막대 바닥 바로 아래
    assert baseline[3] > 150 and baseline[0] > 180  # 막대 아래 기준선


def test_scenes_with_a_motion_part_stay_out_of_the_gray_board_sequence():
    """움직임 부품은 장면 단위로 따로 그리므로 회색 배경 시퀀스 묶음을 끊는다."""
    scenes = [
        {"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "하나"}},
        {"index": 1, "background": "board", "overlay": {"type": "motion_part", "part": "move", "params": {}}},
        {"index": 2, "background": "board", "overlay": {"type": "key_text", "text": "셋"}},
    ]

    runs = board_runs({"scenes": scenes})

    assert [[s["index"] for s in run] for run in runs] == [[0], [2]]


def test_bars_with_long_value_labels_are_not_clipped_and_do_not_touch_the_title():
    """2026-10-18 분석: "107조 4000억 원" 같은 긴 값 라벨이 막대 폭보다 넓어 잘리고 제목과 닿았다."""
    from app.services.slide_sequence import _bars_sprite

    items = [{"label": "매출", "value_text": "195조 원", "value": 195.0}, {"label": "영업이익", "value_text": "107조 4000억 원", "value": 107.4}]
    sprite = _bars_sprite("분기 실적", items, 1.0)
    alpha = sprite.getchannel("A")

    width, height = sprite.size
    assert alpha.crop((0, 0, 6, height)).getbbox() is None and alpha.crop((width - 6, 0, width, height)).getbbox() is None  # 가장자리에서 잘리지 않는다
    rows = [alpha.crop((0, y, width, y + 1)).getbbox() is not None for y in range(height)]
    title_end = max(y for y in range(0, 110) if rows[y])
    first_label = next(y for y in range(title_end + 1, height) if rows[y])
    assert first_label - title_end >= 14                                            # 제목과 값 라벨 사이에 틈이 있다
