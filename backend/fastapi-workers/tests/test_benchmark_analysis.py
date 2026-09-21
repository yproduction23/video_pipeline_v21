import json

import pytest

from app.services.discovery.benchmark_analysis import analyze_benchmark

VIDEO = {"title": "부산상어 챌린지 이렇게 번졌다", "tags": ["부산상어"], "description": "설명", "views": 1000, "viewsPerHour": 40.0}


def fake_llm(payload):
    return lambda system, user, max_tokens=800: payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def test_normalizes_valid_response_and_caps_reasons():
    result = analyze_benchmark(fake_llm("분석 결과: " + json.dumps({
        "topic_keyword": " 부산상어 재유행 ",
        "reasons": ["a", "b", "c", "d"],
        "hook_type": "direct_question",
        "title_pattern": "결과 먼저 제시",
    }, ensure_ascii=False)), VIDEO)

    assert result == {"topic_keyword": "부산상어 재유행", "reasons": ["a", "b", "c"],
                      "hook_type": "direct_question", "title_pattern": "결과 먼저 제시"}


def test_unknown_hook_type_becomes_none():
    result = analyze_benchmark(fake_llm({"topic_keyword": "상어", "reasons": ["a"], "hook_type": "weird", "title_pattern": ""}), VIDEO)

    assert result["hook_type"] is None


def test_prompt_forbids_quoting_and_includes_video_facts():
    seen = {}

    def llm(system, user, max_tokens=800):
        seen["system"], seen["user"] = system, user
        return json.dumps({"topic_keyword": "상어", "reasons": [], "hook_type": None, "title_pattern": ""})

    analyze_benchmark(llm, VIDEO)

    assert "그대로 인용하지" in seen["system"]
    assert "부산상어 챌린지" in seen["user"]


def test_non_json_response_raises_value_error():
    with pytest.raises(ValueError):
        analyze_benchmark(fake_llm("분석할 수 없습니다"), VIDEO)


def test_transcript_adds_content_summary_and_is_sent_to_llm():
    seen = {}

    def llm(system, user, max_tokens=800):
        seen["user"] = user
        return json.dumps({"topic_keyword": "면접", "reasons": ["a"], "hook_type": None, "title_pattern": "",
                           "content_summary": " 늙어버린 25살이 면접에서 진실을 말한다 "}, ensure_ascii=False)

    result = analyze_benchmark(llm, VIDEO, transcript="저 25살이에요. 시계를 잘못 돌렸어요.")

    assert "저 25살이에요" in seen["user"] and "content_summary" in seen["user"]
    assert result["content_summary"] == "늙어버린 25살이 면접에서 진실을 말한다"


def test_no_transcript_means_no_content_summary_even_if_model_invents_one():
    payload = {"topic_keyword": "상어", "reasons": [], "hook_type": None, "title_pattern": "", "content_summary": "지어낸 줄거리"}

    result = analyze_benchmark(fake_llm(payload), VIDEO)

    assert "content_summary" not in result


def test_transcript_adds_story_premise_and_beats():
    payload = {"topic_keyword": "경고", "reasons": [], "hook_type": None, "title_pattern": "",
               "content_summary": "요약", "story_premise": " 낯선 사람이 경고를 전한다 ",
               "story_beats": ["경고", "의심", "", "위기"]}

    result = analyze_benchmark(fake_llm(payload), VIDEO, transcript="자막")

    assert result["story_premise"] == "낯선 사람이 경고를 전한다"
    assert result["story_beats"] == ["경고", "의심", "위기"]


def test_no_story_fields_without_transcript():
    payload = {"topic_keyword": "경고", "reasons": [], "hook_type": None, "title_pattern": "",
               "story_premise": "지어낸 전제", "story_beats": ["a"]}

    result = analyze_benchmark(fake_llm(payload), VIDEO)

    assert "story_premise" not in result and "story_beats" not in result


def test_transcript_analysis_gets_larger_token_budget():
    seen = {}

    def llm(system, user, max_tokens=800):
        seen["max_tokens"] = max_tokens
        return json.dumps({"topic_keyword": "a", "reasons": [], "hook_type": None, "title_pattern": ""})

    analyze_benchmark(llm, VIDEO, transcript="자막")
    assert seen["max_tokens"] >= 1500
    analyze_benchmark(llm, VIDEO)
    assert seen["max_tokens"] == 800
