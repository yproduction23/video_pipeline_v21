import os
import pytest
from app.utils.art_direction import (
    direct_scenes,
    select_archetype_for_scene,
    keyword_fallback,
    _batch_archetype_proposals,
)


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

def test_archetype_prompt_does_not_force_every_scene_into_a_stock_market_frame(monkeypatch):
    """2026-10-14 사용자 재현(job 15): archetype 선택 시스템 프롬프트가
    "채널 콘텐츠는 KOSPI, KOSDAQ, 미국 주식 시장을 다루며"라고 무조건
    못박아 놓아서, 흉기 난동 같은 비금융 범죄 뉴스 대본에서도 모델이 억지로
    금융 은유를 찾으려다 briefing_podium/data_lab 두 개만 기계적으로
    번갈아 골랐다(씬2 "신고 접수 시각은 오후 1시 49분" 같은 담담한 사실
    전달에 마이크·기자단·관계없는 흐름도가 나온 원인). 이 테스트는 시스템
    프롬프트가 더 이상 모든 장면을 주식 시장으로 단정하지 않고, 비금융
    내용에서는 억지로 끼워맞추지 말라는 지시를 담고 있는지 확인한다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_key")
    captured = {}

    def capture_system_prompt(system, messages, max_tokens):
        captured["system"] = system
        return '{"archetype": "briefing_podium", "reason": "테스트", "specific_props": "props"}'

    select_archetype_for_scene(
        "양손에 흉기를 묶고 대낮 마트에 나타났습니다.",
        llm_call=capture_system_prompt,
    )

    system_prompt = captured["system"]
    assert "채널 콘텐츠는 KOSPI, KOSDAQ, 미국 주식 시장을 다루며" not in system_prompt
    assert "비금융" in system_prompt
    assert "억지로" in system_prompt


def test_batch_archetype_prompt_does_not_force_a_pure_finance_identity_and_forbids_refusal(monkeypatch):
    """2026-10-14 사용자 재현(job 17, "홈플러스 인천 구월점 칼부림 사건"): 실제
    운영 경로인 direct_scenes()는 항상 allow_remote=False로 호출해 이 배치
    프롬프트(_batch_archetype_proposals)의 결과에만 의존하며,
    select_archetype_for_scene 내부 LLM 분기는 타지 않는다. 이 배치 프롬프트가
    "너는 한국 금융 영상의 수석 아트 디렉터다"로만 못박혀 있어서, 흉기 난동
    비금융 뉴스 대사를 Claude가 "금융 영상 제작 도구와 안 맞고 피해자에
    대한 모욕"이라며 배치 전체를 텍스트로 거부했다(컨테이너 내 실제
    Anthropic API 호출로 재현 확인). 그 거부 응답은 JSON이 아니라 파싱이
    실패하고, 모든 씬이 한 번에 keyword_fallback의 기계적 briefing_podium/
    data_lab 번갈아 배정으로 떨어졌다. 이 테스트는 시스템 프롬프트가 더 이상
    순수 금융 정체성만 말하지 않고, 비금융 뉴스도 같은 마스코트로 다룬다는
    점과 내용이 불편해도 거부하지 말라는 지시를 담고 있는지 확인한다."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_key")
    captured = {}

    def capture_system_prompt(system, messages, max_tokens):
        captured["system"] = system
        captured["prompt"] = messages[0]["content"]
        return '[{"index": 0, "archetype": "briefing_podium", "reason": "테스트", "specific_props": "props"}]'

    _batch_archetype_proposals(
        [{"content": "양손에 흉기를 묶고 대낮 마트에 나타났습니다."}],
        llm_call=capture_system_prompt,
    )

    system_prompt = captured["system"]
    assert "너는 한국 금융 영상의 수석 아트 디렉터다" not in system_prompt
    assert "비금융" in system_prompt
    assert "거부하지 말고" in system_prompt


def test_batch_archetype_proposals_falls_back_cleanly_when_llm_refuses_in_prose(monkeypatch):
    """배치 호출이 거부 설명문(비-JSON 한국어 텍스트)을 반환해도 예외 없이
    빈 결과를 돌려주고, direct_scenes()가 씬별 keyword_fallback으로 안전하게
    이어받는지 확인한다(완전한 크래시 방지 자체는 기존에도 보장돼 있었음)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy_key")

    def refusing_llm(system, messages, max_tokens):
        return "이 내용은 금융 영상과 맞지 않아 작업을 수행하지 않겠습니다."

    result = _batch_archetype_proposals(
        [{"content": "양손에 흉기를 묶고 대낮 마트에 나타났습니다."}],
        llm_call=refusing_llm,
    )
    assert result == {}


def test_keyword_fallback_maps_crime_narration_to_risk_control_room_not_blind_alternation():
    """2026-10-14 job 18 scene 11 재현: "불특정 다수를 겨냥한 흉기 난동,
    이번이 처음이 아닌 겁니다"는 금융 키워드가 전혀 없어 keyword_fallback의
    candidates가 비어 있었고, 마지막 줄의 기계적 briefing_podium/data_lab
    번갈아 배정으로 떨어졌다(실제 재현: Claude 배치 호출이 폭력 사건 연출을
    거부해 LLM 경로를 못 타는 상황에서 이 최후 폴백만 남았다). data_lab이
    선택되자 비전 검수가 "금융 차트 오인"으로 거듭 거부해 씬의 재시도 상한을
    소진시켰다. risk_control_room(위험 관제)이 금융 전용 무대보다 덜
    엉뚱하므로 이쪽으로 가야 한다."""
    archetype = keyword_fallback(
        "불특정 다수를 겨냥한 흉기 난동, 이번이 처음이 아닌 겁니다.",
        recent_prev="",
    )
    assert archetype == "risk_control_room"


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
