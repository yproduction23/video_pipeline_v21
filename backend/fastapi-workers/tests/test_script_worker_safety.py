import os

from app.workers.script_worker import (
    ScriptWorker,
    _cap_dialogue_to_target,
    _dialogue_char_count,
    _validate_unit_usage,
    clean_script_commas_and_pct,
)


def test_percentage_normalization_and_all_thousands_commas():
    # 2026-10-16 운영자 요청: 대본·자막은 "퍼센트" 대신 % 기호로 쓴다(음성은 TTS 전처리가 "퍼센트"로 읽는다).
    result = clean_script_commas_and_pct("하락률 6.37퍼센트, 거래대금 29,800,000원, 등락률 1.2%")
    assert "6.37%" in result
    assert "1.2%" in result
    assert "6.37포인트" not in result
    assert "29800000원" in result
    assert "퍼센트포인트" in clean_script_commas_and_pct("금리가 0.25퍼센트포인트 올랐습니다.")  # 단위가 다른 "퍼센트포인트"는 그대로


def test_unit_validation_allows_correct_point_and_percentage_sentence():
    result = _validate_unit_usage("코스피가 463포인트, 하락률은 6.37퍼센트 하락했습니다.")
    assert result["passed"], result


def test_unit_validation_allows_the_percent_symbol_in_the_script():
    assert _validate_unit_usage("코스피는 6.37% 하락했습니다.")["passed"]


def test_unit_validation_rejects_rate_label_with_point_value():
    result = _validate_unit_usage("하락률은 4.53포인트였습니다.")
    assert not result["passed"]
    assert "포인트" in result["errors"][0]


def test_sentence_safe_cap_never_cuts_decimal_or_large_number():
    script = """## 씬 1: 수치
[대사] 하락률은 6.37퍼센트이고 거래대금은 29,800,000원입니다. 다음 문장은 예산 때문에 제외되어야 합니다.
[비주얼 설명] 차트
"""
    capped = _cap_dialogue_to_target(script, target_chars=10)
    assert "6.37퍼센트" in capped
    assert "29,800,000원" in capped
    assert "다음 문장은" not in capped


def test_multi_scene_cap_fills_global_duration_budget_with_whole_sentences():
    blocks = []
    for index in range(10):
        blocks.append(
            f"## 씬 {index}: 설명\n"
            "[대사] 코스피와 빅테크 실적의 변화를 검증된 수치로 차분하게 설명합니다. "
            "반등의 조건과 위험 요인을 투자자가 이해하기 쉽게 하나씩 살펴봅니다.\n"
            "[비주얼 설명] 카툰 장면\n"
        )
    capped = _cap_dialogue_to_target("\n".join(blocks), target_chars=445)
    count = _dialogue_char_count(capped)
    assert 409 <= count <= 481
    assert capped.count("[대사]") == 10


def test_mock_response_matches_script_response_contract(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = ScriptWorker()._mock_generate("삼성전자", "코스피", 1, 999)
    required = {
        "job_id", "keyword", "script", "sections", "char_count", "length_contract",
        "keyword_validation", "unit_validation", "verified_facts", "fact_check_rounds",
        "fact_check_log", "market_snapshot_used", "market_snapshot", "used_real_llm",
        "llm_call_count", "narrative_plan", "flow_qa", "requires_manual_review", "quality_report", "youtube_metadata",
    }
    assert required.issubset(result)
    assert result["requires_manual_review"] is True
    assert result["script"] and result["sections"]
