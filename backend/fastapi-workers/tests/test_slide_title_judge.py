"""그래프 제목 검수(2026-10-18 운영자 피드백: 삼성전자 실적인데 제목이 "엔비디아 분기 실적")."""
import json

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_title_judge import fix_titles_in_plan, judge_titles


def _llm(results):
    return lambda system, messages, max_tokens: json.dumps({"results": results}, ensure_ascii=False)


def test_judge_replaces_a_wrong_title_with_a_phrase_from_the_current_narration_only():
    items = [{"id": 0, "title": "엔비디아 분기 실적", "values": ["매출=195조 원"], "narration": "삼성전자는 3분기 매출 195조 원을 기록했습니다.",
              "context": "엔비디아는 급락했습니다."}]

    ok = judge_titles(items, llm_call=_llm([{"id": 0, "ok": False, "title": "삼성전자는 3분기"}]))
    bad = judge_titles(items, llm_call=_llm([{"id": 0, "ok": False, "title": "엔비디아 실적"}]))     # 현재 내레이션에 없는 구절
    same = judge_titles(items, llm_call=_llm([{"id": 0, "ok": True}]))

    assert ok == {0: "삼성전자는 3분기"}
    assert bad == {0: ""}                  # 근거 없는 교체 제목은 쓰지 않고 제목을 뺀다
    assert same == {}


def test_failed_judgement_changes_nothing():
    def broken(system, messages, max_tokens):
        raise RuntimeError("down")

    assert judge_titles([{"id": 0, "title": "x", "values": [], "narration": "n"}], llm_call=broken) == {}


def test_plan_stat_chart_title_is_fixed_and_values_stay():
    briefs = {0: {"narration": "엔비디아는 급락했습니다.", "duration": 4.0},
              1: {"narration": "삼성전자는 3분기 매출 195조 원, 영업이익 107조 4000억 원을 기록했습니다.", "duration": 4.0}}
    entries = [{"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "엔비디아는 급락"}},
               {"index": 1, "background": "board", "overlay": {"type": "stat_chart", "title": "엔비디아 분기 실적", "items": [
                   {"label": "매출", "value_text": "195조 원"}, {"label": "영업이익", "value_text": "107조 4000억 원"}]}}]
    plan = assemble_plan(briefs, entries, job_id=1)
    assert plan["scenes"][1]["overlay"]["type"] == "stat_chart"        # 기존 검증은 직전 장면 글자를 근거로 통과시켰다
    title_before = plan["scenes"][1]["overlay"]["title"]

    fixed = fix_titles_in_plan(plan, llm_call=_llm([{"id": 0, "ok": False, "title": "삼성전자는 3분기"}]))

    overlay = fixed["scenes"][1]["overlay"]
    assert overlay["title"] == "삼성전자는 3분기" != title_before
    assert [i["value_text"] for i in overlay["items"]] == ["195조 원", "107조 4000억 원"]
    assert fixed["title_judgement"] == {"checked": 1, "changed": 1}
