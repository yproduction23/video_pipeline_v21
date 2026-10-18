"""모션 시퀀스 구성 템플릿 (2026-10-16 운영자 피드백: "그냥 막 넣은 것 같다").

Claude는 주제마다 템플릿과 내용만 정하고, 자리·크기·정렬은 템플릿이 결정한다. 같은 템플릿 안의 항목은 같은 구조여야 한다."""
from __future__ import annotations

import json
import re

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_sequence import board_runs, design_sequence, element_rect

NARRATIONS = [
    "외국인 이탈과 자사주 매입 종료가 겹쳤습니다. 미국 금리가 올라가면 안전자산 수익률이 높아집니다.",
    "그래서 코스피는 7.57퍼센트 폭락했고 외국인은 8거래일 연속 팔았습니다.",
    "완충재 감소와 외부 충격이 이어지면 낙폭이 더 빨라집니다.",
]



def _approving(payload):
    """설계 응답은 payload로, 아이콘 적합성 판정 요청에는 모든 항목을 통과시키는 가짜 LLM."""
    def call(system, messages, max_tokens):
        if "아이콘 검수자" in system:
            ids = re.findall(r"^(\d+)\. 아이콘=", messages[0]["content"], re.M)
            return json.dumps({"ok": [int(i) for i in ids]})
        return json.dumps(payload, ensure_ascii=False)

    return call

def _plan(duration=6.0):
    briefs = {i: {"narration": t, "context": "", "duration": duration} for i, t in enumerate(NARRATIONS)}
    entries = [{"index": i, "background": "board", "overlay": {"type": "key_text", "text": t[:8]}}
               for i, t in enumerate(NARRATIONS)]
    return assemble_plan(briefs, entries, job_id=1)


def _design(topics, duration=6.0):
    plan = _plan(duration)
    run = board_runs(plan)[0]
    payload = {"topics": topics}
    return design_sequence(run, plan, llm_call=_approving(payload))


PAIR = {
    "template": "compare_pair", "scene": 0, "anchor": "외국인 이탈", "op": "+",
    "a": {"label": "외국인 이탈", "icon": "chip"},
    "b": {"label": "자사주 매입 종료", "icon": "building", "anchor": "자사주 매입 종료"},
}


def _by_kind(seq, kind):
    # 템플릿이 배치한 요소만(빈 구간 보충용 장면별 후편집 요소는 제외)
    return [e for e in seq["elements"] if e["kind"] == kind and e.get("fixed")]


def _overlap(a, b):
    ra, rb = element_rect(a), element_rect(b)
    return not (ra[2] <= rb[0] or rb[2] <= ra[0] or ra[3] <= rb[1] or rb[3] <= ra[1])


def test_compare_pair_puts_two_same_structure_columns_on_one_row_with_the_operator_between():
    seq = _design([PAIR])

    icons, texts, symbols = _by_kind(seq, "icon"), _by_kind(seq, "text"), _by_kind(seq, "symbol")
    assert len(icons) == 2 and len(texts) == 2 and len(symbols) == 1
    left_icon, right_icon = sorted(icons, key=lambda e: e["pos"][0])
    left_text, right_text = sorted(texts, key=lambda e: e["pos"][0])
    assert left_icon["pos"][1] == right_icon["pos"][1]  # 같은 줄
    assert left_text["pos"][1] == right_text["pos"][1]
    assert left_icon["pos"][0] == left_text["pos"][0] and right_icon["pos"][0] == right_text["pos"][0]  # 아이콘 바로 아래에 글
    assert left_icon["pos"][0] < symbols[0]["pos"][0] < right_icon["pos"][0]
    assert symbols[0]["content"]["text"] == "+"
    assert symbols[0]["at"] < _by_kind(seq, "icon")[1]["at"] + 1  # 두 번째 항목이 나올 때 기호도 함께
    for first in seq["elements"]:
        for second in seq["elements"]:
            if first is not second and first["topic"] == second["topic"] and first["topic"] > 0:  # 같은 시간에 함께 보이는 요소끼리만
                assert not _overlap(first, second)


def test_pair_items_with_mismatched_structure_are_both_reduced_to_text_only():
    pair = {**PAIR, "b": {"label": "자사주 매입 종료"}}  # 한쪽만 아이콘이 있다

    seq = _design([pair])

    assert _by_kind(seq, "icon") == []
    texts = sorted(_by_kind(seq, "text"), key=lambda e: e["pos"][0])
    assert [t["content"]["text"] for t in texts] == ["외국인 이탈", "자사주 매입 종료"]
    assert texts[0]["pos"][1] == texts[1]["pos"][1]


def test_a_label_that_is_not_in_the_narration_drops_the_whole_topic():
    pair = {**PAIR, "a": {"label": "원문에 없는 표현", "icon": "chip"}}

    seq = _design([pair])

    assert seq["source"] == "fallback_overlays"  # 설계가 비면 장면별 후편집으로 대체한다


def test_title_number_with_icon_keeps_icon_and_number_on_the_same_line():
    topic = {"template": "title_number", "scene": 1, "anchor": "7.57퍼센트", "title": "그래서 코스피는",
             "number": {"value": "7.57퍼센트", "label": ""}, "icon": "chart_down"}

    seq = _design([topic])

    icon, number = _by_kind(seq, "icon")[0], _by_kind(seq, "number")[0]
    assert icon["pos"][1] == number["pos"][1] and icon["pos"][0] < number["pos"][0]
    assert number["content"]["value"] == "7.57%"  # 화면에는 % 기호
    assert _by_kind(seq, "text")[0]["pos"][1] < number["pos"][1]  # 제목은 숫자 위


def test_headline_sub_shows_a_red_headline_then_a_gray_subline_below():
    topic = {"template": "headline_sub", "scene": 2, "anchor": "완충재 감소", "headline": "완충재 감소",
             "sub": "외부 충격이 이어지면"}

    seq = _design([topic])

    headline, sub = sorted(_by_kind(seq, "text"), key=lambda e: e["pos"][1])
    assert headline["content"]["color"] == "red" and sub["content"]["color"] == "gray"
    assert headline["pos"][1] < sub["pos"][1] and sub["at"] > headline["at"]


def test_flow_with_icons_on_only_some_nodes_drops_all_icons_for_a_uniform_structure():
    topic = {"template": "flow", "scene": 2, "anchor": "완충재 감소",
             "nodes": [{"label": "완충재 감소", "icon": "shield"}, {"label": "외부 충격"}]}

    seq = _design([topic])

    flow = _by_kind(seq, "flow")[0]
    assert [n["icon"] for n in flow["content"]["nodes"]] == [None, None]


def test_topics_are_laid_out_by_the_template_and_not_moved_by_the_free_placement_rules():
    first = {**PAIR}
    second = {"template": "title_number", "scene": 1, "anchor": "7.57퍼센트", "title": "그래서 코스피는",
              "number": {"value": "7.57퍼센트", "label": ""}}

    seq = _design([first, second])

    assert {e["topic"] for e in seq["elements"] if e["topic"] > 0} == {1, 2}  # 0은 빈 구간 보충 요소
    pair_icons = [e for e in seq["elements"] if e["topic"] == 1 and e["kind"] == "icon"]
    assert sorted(e["pos"][0] for e in pair_icons) == [560, 1360]
    topic_one_until = {e["until"] for e in seq["elements"] if e["topic"] == 1}
    topic_two_start = min(e["at"] for e in seq["elements"] if e["topic"] == 2)
    assert len(topic_one_until) == 1 and max(topic_one_until) <= round(topic_two_start + 0.12, 2)  # 한꺼번에 사라지고, 다음 주제 뒤까지 남지 않는다


def _design_with_judge(topics, ok):
    """첫 호출(주제 설계)과 둘째 호출(요약 문구 검수)을 구분해 응답하는 모의 LLM."""
    plan = _plan()
    run = board_runs(plan)[0]
    calls = []

    def llm(system, messages, max_tokens):
        calls.append(messages[0]["content"])
        if "사실 확인 검수자" in system:
            return json.dumps({"ok": ok})
        return json.dumps({"topics": topics}, ensure_ascii=False)

    return design_sequence(run, plan, llm_call=llm), calls


def test_a_short_summary_label_is_allowed_only_after_the_semantic_check_approves_it():
    """2026-10-16: 화면 문구를 원문 그대로만 허용하면 핵심어를 못 써 자막 반복이 남는다. 요약 문구는 의미 검수를 거친다."""
    topic = {"template": "headline_sub", "scene": 2, "anchor": "완충재 감소", "headline": "충격에 취약"}

    approved, calls = _design_with_judge([topic], ok=[1])
    rejected, _ = _design_with_judge([topic], ok=[])

    assert approved["source"] == "claude" and _by_kind(approved, "text")[0]["content"]["text"] == "충격에 취약"
    assert any("충격에 취약" in c for c in calls[1:])  # 검수 호출에 문구와 내레이션이 함께 간다
    assert rejected["source"] == "fallback_overlays"


def test_a_summary_label_containing_a_number_is_never_sent_for_approval():
    topic = {"template": "headline_sub", "scene": 2, "anchor": "완충재 감소", "headline": "낙폭 30배 확대"}

    seq, calls = _design_with_judge([topic], ok=[1])

    assert len(calls) == 1  # 숫자가 든 요약 문구는 검수 없이 거절(숫자는 원문 일치만 허용)
    assert seq["source"] == "fallback_overlays"


# ── 연속 일수 달력(streak): 달력이 나오고 날짜 칸마다 순서대로 X 표시(2026-10-17 운영자 요청) ──

def _streak_topic(count="8거래일"):
    return {"template": "streak", "scene": 1, "anchor": "8거래일 연속", "count": count, "title": "외국인 순매도"}


def test_streak_draws_a_calendar_for_the_number_of_days_said_in_the_narration():
    seq = _design([_streak_topic()])

    streaks = _by_kind(seq, "streak")
    assert len(streaks) == 1
    assert streaks[0]["content"] == {"count": 8, "phrase": "8거래일"}
    assert streaks[0]["anim"] == "draw"


def test_streak_with_a_day_count_not_in_the_narration_or_out_of_range_is_dropped():
    assert _by_kind(_design([_streak_topic("9거래일")]), "streak") == []    # 내레이션에 없는 숫자
    assert _by_kind(_design([_streak_topic("1거래일")]), "streak") == []    # 연속이 아님


def test_streak_sprite_marks_one_more_day_as_the_animation_progresses():
    from app.services.slide_sequence import _streak_sprite

    def red_pixels(p):
        image = _streak_sprite(8, "8거래일", p)
        return sum(1 for r, g, b, a in image.getdata() if a > 200 and r > 200 and g < 90 and b < 90)

    assert red_pixels(0.0) == 0
    assert 0 < red_pixels(0.4) < red_pixels(0.7) < red_pixels(1.0)
    assert _streak_sprite(8, "8거래일", 1.0).height <= 700     # 하단 자막 영역을 침범하지 않는다


def test_streak_splits_eight_days_into_two_even_rows_and_wide_motifs_stay_narrow():
    from app.services.slide_sequence import MOTIF_MAX_WIDTH, _icon_sprite, _streak_sprite

    sprite = _streak_sprite(8, "8거래일", 1.0)
    # 4+4 배치: 7+1이면 카드 폭이 7칸 기준이지만 4+4면 4칸 기준이라 훨씬 좁다
    assert sprite.width < 4 * 170 + 2 * 60 + 5
    # 가로로 긴 소재(달러 지폐 더미)가 숫자 옆 자리를 침범하지 않는다
    assert _icon_sprite("motif:달러 지폐 더미", 340).width <= MOTIF_MAX_WIDTH + 40


def test_motif_names_and_aliases_are_accepted_as_icons_in_a_designed_topic():
    topic = {"template": "title_number", "scene": 1, "anchor": "7.57퍼센트", "title": "코스피",
             "number": {"value": "7.57퍼센트", "label": "폭락"}, "icon": "폭락"}
    seq = _design([topic])

    icons = _by_kind(seq, "icon")
    assert [e["content"]["name"] for e in icons] == ["motif:하락 막대차트"]


def test_journey_pair_puts_the_start_first_and_the_number_later_on_the_same_screen():
    """"출발점은 미국 채권 시장 → 24년 만에 5.3%": 출발이 먼저 놓이고 도착은 말하는 순간 같은 화면에 이어서 쌓인다(2026-10-18 피드백)."""
    topic = {
        "template": "compare_pair", "scene": 1, "anchor": "그래서 코스피는", "op": "→",
        "a": {"label": "코스피는", "icon": "chip"},
        "b": {"label": "폭락했고", "value": "7.57퍼센트", "anchor": "7.57퍼센트"},
    }
    seq = _design([topic])

    icons, numbers = _by_kind(seq, "icon"), _by_kind(seq, "number")
    assert len(icons) == 1 and len(numbers) == 1
    assert icons[0]["pos"][0] < numbers[0]["pos"][0]                      # 출발은 왼쪽, 도착은 오른쪽
    assert numbers[0]["at"] > icons[0]["at"] + 0.8                        # 도착은 나중에 이어서 나온다
    assert icons[0]["until"] > numbers[0]["at"]                           # 출발은 도착이 나올 때까지 남아 있다
