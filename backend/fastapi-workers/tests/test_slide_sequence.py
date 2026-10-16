"""모션 시퀀스: 회색 배경 구간을 장면 경계를 넘어 이어지는 애니메이션으로 (2026-10-16 운영자 요청).

레퍼런스(부동산 영상 10~33초)처럼 요소가 문장 흐름에 맞춰 하나씩 등장·유지·교체된다. 화면 글자는 기존
검증기를 요소마다 다시 통과해야 하고, 설계가 실패하면 장면별 후편집으로 대체한다."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_sequence import (
    OPENING_LATEST_SECONDS,
    attach_sequences,
    board_runs,
    design_sequence,
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


def _plan(backgrounds=("board", "board", "board", "stock")):
    briefs = {i: {"narration": t, "context": "", "duration": 4.0} for i, t in enumerate(TEXTS)}
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
    {"id": "e1", "kind": "icon", "scene": 0, "anchor": "순자산 10억 원", "slot": "left", "anim": "pop",
     "content": {"name": "money_stack"}},
    {"id": "e2", "kind": "number", "scene": 0, "anchor": "상위 11.8퍼센트", "slot": "right", "anim": "count",
     "content": {"value": "11.8퍼센트", "label": "현재"}},
    {"id": "e3", "kind": "number", "scene": 1, "anchor": "상위 7.9퍼센트", "slot": "lower_right", "anim": "count",
     "content": {"value": "7.9퍼센트", "label": "과거"}},
    {"id": "e4", "kind": "flow", "scene": 2, "anchor": "자산이 현금으로", "slot": "center", "anim": "rise",
     "content": {"nodes": [{"label": "자산", "icon": "building"}, {"label": "현금", "icon": "money_stack"}]}},
    {"id": "e5", "kind": "text", "scene": 0, "anchor": "상위", "slot": "top", "anim": "rise",
     "content": {"text": "원문에 없는 선동 문구입니다", "color": "red"}},   # 원문 불일치 → 버려진다
    {"id": "e6", "kind": "icon", "scene": 0, "anchor": "", "slot": "left", "anim": "pop",
     "content": {"name": "unicorn"}},                                       # 없는 아이콘 → 버려진다
]}


def test_board_runs_group_consecutive_board_scenes():
    plan = _plan(("board", "board", "stock", "board"))

    assert [[s["index"] for s in run] for run in board_runs(plan)] == [[0, 1], [3]]


def test_design_keeps_only_verified_elements_and_times_them_by_the_spoken_phrase():
    plan = _plan()
    run = board_runs(plan)[0]

    seq = design_sequence(run, plan, llm_call=_llm(DESIGN))

    ids = [e["id"] for e in seq["elements"]]
    assert ids == ["e1", "e2", "e3", "e4"]  # 원문 불일치(e5)·없는 아이콘(e6)은 제외
    assert seq["source"] == "claude" and seq["duration"] == 12.0
    by_id = {e["id"]: e for e in seq["elements"]}
    assert by_id["e1"]["at"] <= OPENING_LATEST_SECONDS  # 첫 요소는 구간 시작과 함께
    assert 0 <= by_id["e2"]["at"] < 4.0  # 장면 0 안
    assert 4.0 <= by_id["e3"]["at"] < 8.0  # 장면 1 안
    assert 8.0 <= by_id["e4"]["at"] < 12.0  # 장면 2 안
    assert seq["offsets"] == {"0": 0.0, "1": 4.0, "2": 8.0}


def test_same_slot_replaces_and_other_slots_accumulate_and_center_clears_everything():
    elements = [
        {"id": "a", "slot": "left", "at": 0.3}, {"id": "b", "slot": "right", "at": 1.0},
        {"id": "c", "slot": "right", "at": 5.0}, {"id": "d", "slot": "center", "at": 8.0},
    ]

    resolve_lifetimes(elements, 12.0)
    until = {e["id"]: e["until"] for e in elements}

    assert until["b"] == 5.0  # 같은 자리 → 교체
    assert until["a"] == 8.0  # 다른 자리 요소는 center가 나타날 때까지 누적
    assert until["c"] == 8.0
    assert until["d"] == 12.0


def test_failed_design_falls_back_to_the_per_scene_overlays():
    plan = _plan()
    run = board_runs(plan)[0]

    seq = design_sequence(run, plan, llm_call=lambda s, m, t: "not json")

    assert seq["source"] == "fallback_overlays"
    assert [e["scene"] for e in seq["elements"]] == [0, 1, 2]
    assert all(e["kind"] == "text" for e in seq["elements"])


def test_attach_sequences_reuses_unchanged_runs_by_signature():
    calls = []

    def llm(system, messages, max_tokens):
        calls.append(1)
        return json.dumps(DESIGN, ensure_ascii=False)

    plan = _plan()
    attach_sequences(plan, llm_call=llm, workers=1)
    first = json.loads(json.dumps(plan["sequences"]))
    attach_sequences(plan, llm_call=llm, workers=1)

    assert len(calls) == 1  # 두 번째는 서명이 같아 재사용
    assert plan["sequences"] == first
    assert [s.get("sequence_id") for s in plan["scenes"]] == [1, 1, 1, None]


def test_frames_are_continuous_elements_appear_over_time_and_persist():
    plan = _plan()
    run = board_runs(plan)[0]
    seq = design_sequence(run, plan, llm_call=_llm(DESIGN))
    prims = expand_primitives(seq)
    base = Image.new("RGB", (1920, 1080), (46, 47, 50))
    cache: dict = {}

    def painted(t):
        frame = render_frame(base, prims, t, cache)
        return sum(1 for px in frame.resize((96, 54)).getdata() if px != (46, 47, 50))

    empty, early, later = painted(0.05), painted(1.8), painted(5.0)

    assert early > empty  # 요소가 나타난다
    assert later > early  # 앞 요소가 남은 채 새 요소가 쌓인다(누적)


def test_number_counts_up_and_flow_nodes_arrive_one_by_one():
    plan = _plan()
    run = board_runs(plan)[0]
    seq = design_sequence(run, plan, llm_call=_llm(DESIGN))
    prims = expand_primitives(seq)
    flow = [p for p in prims if p["kind"] in ("node", "arrow") and p["element"] == "e4"]

    assert [p["kind"] for p in flow] == ["node", "arrow", "node"]
    assert flow[0]["at"] < flow[1]["at"] < flow[2]["at"]


def test_render_sequence_clip_writes_an_mp4_for_just_the_scene_slice(tmp_path):
    from app.services.slide_overlay import ensure_board_background
    from app.services.slide_sequence import render_sequence_clip

    plan = _plan()
    seq = design_sequence(board_runs(plan)[0], plan, llm_call=_llm(DESIGN))
    board = tmp_path / "board.png"
    ensure_board_background(str(board))
    out = tmp_path / "scene1.mp4"

    ok = render_sequence_clip(seq, 4.0, 4.6, str(board), str(out))

    assert ok and out.is_file() and out.stat().st_size > 1000


def test_long_number_labels_are_shortened_at_a_word_boundary_not_mid_word():
    plan = _plan()
    run = board_runs(plan)[0]
    design = {"elements": [{
        "id": "e1", "kind": "number", "scene": 0, "anchor": "상위 11.8퍼센트", "slot": "right", "anim": "count",
        "content": {"value": "11.8퍼센트", "label": "순자산 상위 비중입니다"},
    }]}

    seq = design_sequence(run, plan, llm_call=_llm(design))

    assert seq["elements"][0]["content"]["label"] in ("", "순자산 상위")  # 중간에서 잘린 "순자산 상위 비"가 아니다


def test_design_stays_simple_at_most_three_elements_visible_and_two_seconds_on_screen():
    plan = _plan()
    run = board_runs(plan)[0]
    design = {"elements": [
        {"id": f"e{k}", "kind": "icon", "scene": 0, "anchor": "", "slot": slot, "anim": "rise",
         "content": {"name": "person"}}
        for k, slot in enumerate(["left", "right", "top", "bottom", "upper_left"])
    ]}

    seq = design_sequence(run, plan, llm_call=_llm(design))

    for element in seq["elements"]:
        alive = [e for e in seq["elements"] if e["at"] <= element["at"] + 1e-6 < e["until"]]
        assert len(alive) <= 3
    for earlier, later in zip(seq["elements"], seq["elements"][1:]):
        if earlier["until"] <= later["at"] + 1e-6 and earlier["slot"] == later["slot"]:
            assert later["at"] - earlier["at"] >= 2.0 - 1e-6  # 교체는 최소 2초 뒤
