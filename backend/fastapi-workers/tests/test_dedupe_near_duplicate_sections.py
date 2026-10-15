"""2026-10-15 실제 Job(id=19)에서 발견된 버그를 고정한다: 분량 재작성 루프 막바지에
Claude가 같은 마무리 문장을 어미만 바꿔("~지켜보시기 바랍니다" / "~지켜보시기 바라죠")
두 장면으로 중복 생성했다. 기존 _dedupe_adjacent_paragraphs는 완전 일치만 걸러내고,
그나마도 sections 자체가 아니라 별도로 파생한 narration 문자열에만 적용돼 TTS·자막이
실제로 쓰는 sections에는 중복이 그대로 남았다."""
from app.workers.script_worker import (
    _dedupe_adjacent_paragraphs,
    _dedupe_adjacent_sections,
    _is_near_duplicate_key,
    _normalized_text_key,
)


def test_near_duplicate_closing_sentence_from_real_job_is_caught():
    a = _normalized_text_key("미국 금리 흐름과 AI 수익성에 대한 시장의 해석, 이 두 가지를 계속 지켜보시기 바랍니다.")
    b = _normalized_text_key("미국 금리 흐름과 AI 수익성에 대한 시장의 해석, 이 두 가지를 계속 지켜보시기 바라죠.")
    assert _is_near_duplicate_key(b, a) is True


def test_different_subject_and_number_is_not_flagged():
    a = _normalized_text_key("코스피는 오늘 3퍼센트 넘게 하락했습니다.")
    b = _normalized_text_key("코스닥은 오늘 2퍼센트 넘게 하락했습니다.")
    assert _is_near_duplicate_key(b, a) is False


def test_very_short_sentences_are_never_flagged_even_if_similar():
    assert _is_near_duplicate_key(_normalized_text_key("그렇죠?"), _normalized_text_key("그렇군요.")) is False


def test_dedupe_adjacent_sections_removes_the_restated_closing_scene():
    sections = [
        {"title": "장면 001", "text": "국내 기업 사상 처음으로 분기 영업이익 100조 원을 넘겼다는 소식이 전해졌습니다."},
        {
            "title": "장면 063",
            "text": "미국 금리 흐름과 AI 수익성에 대한 시장의 해석, 이 두 가지를 계속 지켜보시기 바랍니다.",
        },
        {
            "title": "장면 064",
            "text": "미국 금리 흐름과 AI 수익성에 대한 시장의 해석, 이 두 가지를 계속 지켜보시기 바라죠.",
        },
    ]

    result = _dedupe_adjacent_sections(sections)

    assert [s["title"] for s in result] == ["장면 001", "장면 063"]


def test_dedupe_adjacent_sections_keeps_legitimately_different_scenes():
    sections = [
        {"title": "장면 001", "text": "코스피는 오늘 3퍼센트 넘게 하락했습니다."},
        {"title": "장면 002", "text": "코스닥은 오늘 2퍼센트 넘게 하락했습니다."},
    ]

    result = _dedupe_adjacent_sections(sections)

    assert len(result) == 2


def test_dedupe_adjacent_paragraphs_still_catches_exact_duplicates():
    text = "문장 하나입니다.\n\n문장 하나입니다.\n\n다른 문장입니다."
    assert _dedupe_adjacent_paragraphs(text) == "문장 하나입니다.\n\n다른 문장입니다."


def test_sentences_differing_only_by_a_number_are_not_duplicates():
    a = _normalized_text_key("삼성전자 주가가 오늘 하루 동안 5퍼센트 넘게 하락했습니다.")
    b = _normalized_text_key("삼성전자 주가가 오늘 하루 동안 7퍼센트 넘게 하락했습니다.")
    assert _is_near_duplicate_key(b, a) is False
