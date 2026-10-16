from app.services.verbatim_guard import validate
from app.workers.script_worker import _parse_sections, clean_script_commas_and_pct


EVIDENCE = {
    "article_capture": {"quote": "한국 등 45개국에 12.5%의 추가 관세를 부과한다."},
    "verified_facts": [{"fact": "반도체 관세는 15%로 조정됐다.", "figure": "15%"}],
    "market_snapshot": {"kospi": {"close": 2783.50}},
}


def test_decimal_percent_passes_only_when_exactly_grounded():
    assert validate("12.5% 추가", EVIDENCE).passed
    rejected = validate("12.6% 추가", EVIDENCE)
    assert not rejected.passed
    assert "ungrounded_numeric_token:12.6%" in rejected.reasons
    assert validate("15%로 싹!", EVIDENCE).passed


def test_decimal_is_single_token_and_no_numeric_exclamation_is_allowed():
    result = validate("12.6%", EVIDENCE)
    assert result.numeric_tokens == ["12.6%"]
    assert validate("우리도?!", EVIDENCE).passed


def test_numeric_bubble_is_rejected_not_deleted_and_has_quality_reason():
    source = """## 씬 1: 관세
[대사] 관세를 확인합니다.
[비주얼 설명] 기사 근거
[비주얼 프롬프트] evidence card
[감정] surprised
[말풍선] 12.6%라고?
"""
    scene = _parse_sections(source, EVIDENCE)[0]
    assert scene["bubble_text"] == "12.6%라고?"
    assert scene["scene_rejected"] is True
    assert scene["bubble_validation"]["reasons"] == ["ungrounded_numeric_token:12.6%"]


def test_visual_percent_is_preserved_while_narration_normalizes():
    assert validate("15%로 싹!", EVIDENCE).passed
    assert "15%" in clean_script_commas_and_pct("15퍼센트로 조정됐습니다.")  # 2026-10-16: 대본은 % 기호


def test_screen_text_accepts_values_that_are_verbatim_in_current_scene_narration():
    source = """## 씬 1: 관세
[대사] 반도체 관세는 15%로 조정됐습니다.
[비주얼 설명] 관세 압박을 상징하는 무거운 문
[비주얼 프롬프트] a heavy locked customs gate
[감정] pointing
[화면 문구] ["반도체 관세", "15퍼센트"]
"""
    scene = _parse_sections(source, EVIDENCE)[0]

    assert scene["screen_texts"] == ["반도체 관세", "15퍼센트"]
    assert scene["screen_text_validation"]["passed"] is True
    assert scene["scene_rejected"] is False


def test_screen_text_does_not_import_another_scene_value_from_global_verified_facts():
    source = """## 씬 1: 관세
[대사] 반도체 관세를 확인합니다.
[비주얼 설명] 관세 압박을 상징하는 무거운 문
[비주얼 프롬프트] a heavy locked customs gate
[감정] pointing
[화면 문구] ["15%"]
"""

    scene = _parse_sections(source, EVIDENCE)[0]

    assert scene["screen_text_validation"]["passed"] is False
    assert scene["screen_text_validation"]["reasons"] == ["screen_text_not_verbatim:15%"]
    assert scene["scene_rejected"] is True


def test_screen_text_rejects_paraphrase_and_malformed_array():
    paraphrase = """## 씬 1: 관세
[대사] 반도체 관세를 확인합니다.
[비주얼 설명] 관세 압박을 상징하는 무거운 문
[비주얼 프롬프트] a heavy locked customs gate
[감정] pointing
[화면 문구] ["관세 폭탄"]
"""
    malformed = paraphrase.replace('["관세 폭탄"]', "관세 폭탄")

    rejected = _parse_sections(paraphrase, EVIDENCE)[0]
    invalid_json = _parse_sections(malformed, EVIDENCE)[0]

    assert rejected["scene_rejected"] is True
    assert rejected["screen_text_validation"]["reasons"] == ["screen_text_not_verbatim:관세 폭탄"]
    assert invalid_json["scene_rejected"] is True
    assert "screen_text_invalid_json_array" in invalid_json["screen_text_validation"]["reasons"]
