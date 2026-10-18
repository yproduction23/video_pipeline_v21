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


# ── 연출 결과를 계획서에 연결: 부품 인자가 검증을 통과하면 장면 후편집이 움직임 부품이 된다 ──

def _plan_with(narrations, overlays, backgrounds=None):
    from app.services.slide_edit_plan import assemble_plan

    briefs = {i: {"narration": t, "duration": 4.0} for i, t in enumerate(narrations)}
    entries = [
        {"index": i, "background": (backgrounds or ["stock"] * len(narrations))[i], "stock_queries": ["q"],
         "overlay": overlays[i]}
        for i in range(len(narrations))
    ]
    return assemble_plan(briefs, entries, job_id=1)


def test_validated_part_params_replace_a_key_text_overlay_with_a_motion_part():
    from app.services.slide_concepts import apply_concepts

    narrations = ["자사주 매입이 하방을 받쳐 줍니다.", "외국인은 계속 팔았습니다."]
    plan = _plan_with(narrations, [{"type": "key_text", "text": "하방을 받쳐 줍니다"}, {"type": "key_text", "text": "계속 팔았습니다"}])
    concepts = {"concepts": [
        {"index": 0, "evokes": "기둥이 받친다", "tool": "motif_support", "tool_ready": True, "motifs": [],
         "params": {"top": "자사주 매입이", "pillar": "하방을", "state": "holding"}},
        {"index": 1, "evokes": "저울", "tool": "motif_balance", "tool_ready": True, "motifs": [],
         "params": {"left": "없는 말", "right": "외국인", "heavier": "right"}},     # 원문에 없는 라벨 → 거절
    ], "missing": []}

    updated = apply_concepts(plan, concepts)

    first, second = updated["scenes"]
    assert first["overlay"]["type"] == "motion_part" and first["overlay"]["part"] == "support"
    assert first["overlay"]["params"]["state"] == "holding" and first["overlay"]["source"] == "concept"
    assert second["overlay"]["type"] == "key_text"                      # 검증 실패 → 기존 후편집 유지(이어짐은 연출 단계가 요청할 때만)
    assert updated["concept_applied"] == {"motion_parts": 1, "scenes": 1, "rejected": 1}


def test_concept_does_not_override_data_overlays_or_operator_edits():
    from app.services.slide_concepts import apply_concepts

    narrations = ["코스피는 7.57퍼센트 급락했습니다.", "외국인은 계속 팔았습니다."]
    plan = _plan_with(narrations, [{"type": "big_number", "value": "7.57퍼센트", "label": ""}, {"type": "key_text", "text": "계속 팔았습니다"}])
    plan["scenes"][1]["revised"] = True
    params = {"left": "코스피는", "right": "외국인은", "heavier": "left"}
    concepts = {"concepts": [{"index": i, "evokes": "x", "tool": "motif_balance", "tool_ready": True, "motifs": [], "params": params}
                             for i in (0, 1)], "missing": []}

    updated = apply_concepts(plan, concepts)

    assert updated["scenes"][0]["overlay"]["type"] == "big_number"       # 숫자 후편집은 그대로
    assert updated["scenes"][1]["overlay"]["type"] == "key_text"         # 운영자가 고친 장면은 그대로


# ── 연상 그림은 적게·다양하게·천천히(2026-10-18 운영자 피드백: 너무 빠르고 조잡하다, 같은 장면이 자주 나온다) ──

def _scenes_plan(count, seconds=4.0, background="stock"):
    from app.services.slide_edit_plan import assemble_plan

    briefs = {i: {"narration": f"외국인이 팔았고 자금이 빠져나갔습니다 {i}.", "duration": seconds} for i in range(count)}
    entries = [{"index": i, "background": background, "stock_queries": ["q"], "overlay": None} for i in range(count)]
    return assemble_plan(briefs, entries, job_id=1)


def _move_concept(index, importance=1, part="motif_move"):
    params = {"item": "달러 지폐 더미", "source_label": "외국인이"} if part == "motif_move" else {"origin": "외국인이", "effect": "자금이"}
    return {"index": index, "evokes": "x", "tool": part, "tool_ready": True, "motifs": [], "importance": importance, "params": params}


def test_motion_parts_keep_a_minimum_gap_and_avoid_repeating_the_same_part():
    from app.services.slide_concepts import MIN_PART_GAP_SECONDS, apply_concepts

    plan = _scenes_plan(12, 4.0)       # 48초
    # 짝수 장면은 이동, 홀수 장면은 확산 후보(같은 부품이 연속되면 안 된다는 규칙을 보려고 번갈아 둔다)
    concepts = {"concepts": [
        _move_concept(i, importance=3 if i == 0 else 1, part="motif_move" if i % 2 == 0 else "motif_spread") for i in range(12)
    ], "missing": []}

    updated = apply_concepts(plan, concepts)

    heads = [s["index"] for s in updated["scenes"] if (s.get("overlay") or {}).get("type") == "motion_part"
             and s["overlay"]["span"]["head"] == s["index"]]
    assert heads[0] == 0                                                       # 가장 중요한 장면이 먼저 뽑힌다
    starts = [h * 4.0 for h in heads]
    assert all(b - a >= MIN_PART_GAP_SECONDS for a, b in zip(starts, starts[1:]))
    assert 2 <= len(heads) <= 3                                                # 48초에 20초당 1개 안팎
    parts = [updated["scenes"][h]["overlay"]["part"] for h in heads]
    assert all(a != b for a, b in zip(parts, parts[1:]))                       # 같은 부품 연속 금지


def test_a_part_continues_over_the_following_bare_scenes_so_it_moves_slowly():
    from app.services.slide_concepts import apply_concepts

    plan = _scenes_plan(5, 4.0)
    concept = {**_move_concept(1, importance=3), "span_to": 3}
    updated = apply_concepts(plan, {"concepts": [concept], "missing": []})

    head, follower, last = updated["scenes"][1], updated["scenes"][2], updated["scenes"][3]
    assert head["overlay"]["span"]["head"] == 1 and head["overlay"]["span"]["total_scenes"] == 3
    assert follower["overlay"]["type"] == "motion_part" and follower["overlay"]["span"]["head"] == 1
    assert follower["overlay"]["span"]["elapsed_before"] > 3.0               # 앞 장면에서 이미 진행한 시간
    assert last["overlay"]["span"]["last"] is True and follower["overlay"]["span"]["last"] is False
    assert updated["scenes"][4].get("overlay") is None                       # 최대 3장면까지만 이어진다
    assert head["overlay"]["span"]["anim"] >= 5.0                            # 한 장면 안에 몰아 그리지 않고 천천히


def test_without_span_to_the_part_ends_with_its_own_scene():
    """2026-10-18 피드백: 이야기가 바뀐 뒤에도 그림이 남았다 — 연출 단계가 같은 이야기라고 한 장면까지만 이어진다."""
    from app.services.slide_concepts import apply_concepts

    plan = _scenes_plan(5, 4.0)
    updated = apply_concepts(plan, {"concepts": [_move_concept(1, importance=3)], "missing": []})

    assert updated["scenes"][1]["overlay"]["span"]["total_scenes"] == 1
    assert updated["scenes"][2].get("overlay") is None


def test_same_part_with_the_same_item_is_not_repeated_within_a_minute():
    from app.services.slide_concepts import apply_concepts

    plan = _scenes_plan(20, 4.0)          # 80초
    concepts = {"concepts": [
        _move_concept(0, importance=3), _move_concept(6, importance=3),      # 24초 뒤 같은 이동·같은 지폐 → 건너뜀
        _move_concept(18, importance=3),                                      # 72초 뒤 → 허용
    ], "missing": []}

    updated = apply_concepts(plan, concepts)

    heads = [s["index"] for s in updated["scenes"] if (s.get("overlay") or {}).get("type") == "motion_part"
             and s["overlay"]["span"]["head"] == s["index"]]
    assert heads == [0, 18]
