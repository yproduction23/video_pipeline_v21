from app.workers.script_worker import (
    ScriptResearchRequiredError,
    _keyword_coverage_terms,
    _selected_keyword_terms,
    _validate_keyword_coverage,
)


def test_korean_postpositions_and_connective_words_do_not_create_false_missing_terms():
    topic = "코스피 급락 이후 반등과 빅테크 실적 영향"
    script = "코스피는 고점에서 급락했습니다. 빅테크 실적 발표 뒤 반등 가능성을 살펴봅니다."
    validation = _validate_keyword_coverage(script, _selected_keyword_terms(topic))
    assert validation["passed"]
    assert validation["missing_terms"] == []


def test_research_required_error_keeps_explicit_missing_terms():
    error = ScriptResearchRequiredError("근거 확인 필요", ["삼성전자", "HBM"])
    assert error.missing_terms == ["삼성전자", "HBM"]


def test_market_semantic_equivalents_cover_text_flow_conclusion():
    topic = "코스피 급락 이후 반등과 빅테크 실적 영향"
    script = (
        "코스피는 외국인 매도로 낙폭이 크게 확대됐습니다. "
        "이후 지수는 손실을 일부 회복했고, 빅테크 실적이 방향을 좌우했습니다."
    )
    validation = _validate_keyword_coverage(script, _selected_keyword_terms(topic))
    assert validation["passed"]
    assert validation["missing_terms"] == []


def test_market_crash_can_be_narrated_as_index_collapse():
    topic = "코스피 급락 이후 반등과 빅테크 실적 영향"
    script = "코스피가 한 달 사이 무너졌습니다. 이후 회복 흐름에는 빅테크 실적이 영향을 줬습니다."
    validation = _validate_keyword_coverage(script, _selected_keyword_terms(topic))
    assert validation["passed"]


def test_clickbait_interrogative_and_verb_forms_are_not_mandatory_concepts():
    """2026-09-17 사용자 재현: 클릭베이트형 키워드의 의문사·용언 활용형까지
    "반드시 다뤄야 할 개념"으로 취급되면, 실제 주제(애프터마켓)보다 훨씬 많은
    분량이 무관한 거시 데이터로 채워진다. "어떻게"/"달라지나"는 개념이 아니라
    문법 요소이므로 커버리지 대상에서 빠져야 한다."""
    topic = "애프터마켓 오픈 후 달러·환율 변수, 주식패턴 어떻게 달라지나"
    terms = _keyword_coverage_terms(_selected_keyword_terms(topic))

    assert "어떻게" not in terms
    assert "달라지나" not in terms
    # 진짜 주제 개체는 여전히 커버리지 대상으로 남아야 한다.
    assert "애프터마켓" in terms
    assert "환율" in terms
