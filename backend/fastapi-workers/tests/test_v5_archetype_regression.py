import os
import pytest
from app.utils.art_direction import direct_scenes, select_archetype_for_scene, keyword_fallback


def test_non_finance_topic_does_not_fall_back_to_a_financial_chart_studio():
    """2026-10-07 사용자 재현(job 14, 씬 0): 채널이 금융 외 사회·스포츠 이슈도
    정상적으로 다루는데도, TOPICS 키워드에 안 걸리는 장면은 전부 "금융 브리핑
    스튜디오+차트" 배경으로 떨어졌다. "저 군대 안 갑니다!" 같은 병역 논란
    장면에 증권 브리핑룸이 나오는 원인이었다. 매칭되는 주제가 없을 때는
    특정 산업 배경을 강요하지 않는 중립적인 폴백을 써야 한다."""
    scenes = direct_scenes([{
        "content": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "section": "intro",
    }])

    direction = scenes[0]["art_direction"]
    assert direction["topic"] != "finance"
    assert "finance" not in direction["setting"].lower()
    assert not any("financial" in prop.lower() for prop in direction["props"])

def test_regression_no_api_key(monkeypatch):
    """1. ANTHROPIC_API_KEY 미설정 환경에서 keyword_fallback 정상 동작 및 크래시 없음 증명."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = select_archetype_for_scene("코스피 지수가 3000포인트를 돌파하며 마감했습니다.", previous_archetypes=["data_lab"])
    assert res["archetype"] in ["briefing_podium", "data_lab", "risk_control_room", "weather_map"]
    assert res["reason"] == "내용 키워드 폴백 선택"

def test_regression_llm_exception(monkeypatch):
    """2. LLM API 호출 중 예외 발생 시 크래시 없이 폴백으로 정상 전환 증명."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_key")
    def mock_failing_llm(*args, **kwargs):
        raise RuntimeError("API Connection Timeout 504")

    res = select_archetype_for_scene("원달러 환율이 폭등하면서 외환 시장에 비상이 걸렸습니다.", previous_archetypes=["briefing_podium"], llm_call=mock_failing_llm)
    assert res["archetype"] in ["risk_control_room", "weather_map", "data_lab", "port_emergency"]
    assert res["reason"] == "내용 키워드 폴백 선택"

def test_regression_consecutive_repetition_guard(monkeypatch):
    """3. 3연속 동일 아키타입 발생 상황에서 하드 가드가 크래시 없이 대안 무대로 교체하는지 증명."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_key")
    # LLM이 3번 연속 risk_control_room을 반환하는 상황 모킹
    def mock_repeating_llm(*args, **kwargs):
        return '{"archetype": "risk_control_room", "reason": "지수 급락 모니터링", "specific_props": "gauges"}'

    res = select_archetype_for_scene(
        "지수가 다시 50포인트 폭락했습니다.",
        previous_archetypes=["risk_control_room", "risk_control_room"],
        llm_call=mock_repeating_llm
    )
    # 3연속 중복 방지로 인해 risk_control_room이 아닌 다른 무대로 교체되어야 함
    assert res["archetype"] != "risk_control_room"
    assert "3연속 risk_control_room 중복 방지" in res["reason"]

def test_pure_supply_chain_metaphor(monkeypatch):
    """4. 항구/부두 단어 없는 순수 공급망 위기 대사에서 port_emergency 시각적 은유 선택 검증."""
    res = select_archetype_for_scene("글로벌 원자재 및 핵심 부품 수급 차질로 대외 공급망 위기가 심화되고 있습니다.")
    assert res["archetype"] in ["port_emergency", "risk_control_room", "weather_map"]

def test_classroom_cap_limit(monkeypatch):
    """5. 10개 씬 대본 시뮬레이션 시 classroom 선택 비율이 15% 캡(최대 1~2개)을 초과하지 않는지 검증."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # 10개 씬 모두 개념/설명 키워드가 들어간 대본 시뮬레이션
    sample_scenes = [
        {"content": f"개념과 용어 정의 설명 {i}"} for i in range(10)
    ]
    directed = direct_scenes(sample_scenes)
    archetypes = [s["archetype"] for s in directed]
    classroom_count = archetypes.count("classroom")

    # 10개 씬 15% cap = round(1.5) = 2개 이하
    assert classroom_count <= 2, f"classroom 갯수({classroom_count})가 캡(2)을 초과함: {archetypes}"


def test_direct_scenes_batches_remote_art_direction_once(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_key")
    calls = []

    def fake_batch(_system, _messages, _max_tokens):
        calls.append(1)
        return """[
          {"index": 0, "archetype": "data_lab", "reason": "실적 분석", "specific_props": "두 반도체 웨이퍼"},
          {"index": 1, "archetype": "split_stage", "reason": "두 회사 비교", "specific_props": "두 생산 라인"},
          {"index": 2, "archetype": "briefing_podium", "reason": "결론 설명", "specific_props": "발표대"}
        ]"""

    directed = direct_scenes([
        {"content": "삼성전자 반도체 실적을 분석합니다."},
        {"content": "두 회사의 주주환원을 비교합니다."},
        {"content": "마지막으로 확인할 기준을 정리합니다."},
    ], llm_call=fake_batch)

    assert len(calls) == 1
    assert [scene["archetype"] for scene in directed] == [
        "data_lab", "split_stage", "briefing_podium",
    ]
