"""벤치마크 자막 전체의 구성(outline) 추출과 그 활용 (슬라이드형 한정, 2026-10-16).

운영자 요구: 벤치마크와 내용 흐름까지 결이 같아야 한다. 자막 문장을 옮기지 않고 구성(순서·역할·비중·
검증 검색어)만 구조화해 대본 플랜과 근거 뉴스 수집에 쓴다. 영상 속 주장은 사실이 아니다."""
from __future__ import annotations

import json

from app.services.discovery import transcript as transcript_module
from app.services.discovery.benchmark_outline import (
    MAX_SECTIONS,
    extract_outline,
    outline_search_queries,
)
from app.utils.narrative_planner import plan_narrative
from app.workers import script_worker
from app.workers.script_worker import _collect_outline_news, _merge_news_rows

TRANSCRIPT = "코스피가 급락했습니다. " * 80  # 400자 이상


def _llm(payload):
    return lambda system, user, max_tokens: json.dumps(payload, ensure_ascii=False)


GOOD = {
    "highlight": {"gist": "실적이 사상 최대인데도 주가가 급락한 이유가 수급이라는 결론"},
    "sections": [
        {"role": "기", "topic": "급락 당일 시장 상황", "claim_type": "수치 제시", "share_pct": 25,
         "search_queries": ["코스피 급락 사이드카", "코스피 급락 사이드카", "삼성전자 주가 하락"]},
        {"role": "승", "topic": "외국인 매도의 실체", "claim_type": "원인 분석", "share_pct": 35,
         "search_queries": ["외국인 순매도 반도체"]},
        {"role": "결", "topic": "수급이 주범이라는 진단", "claim_type": "정리", "share_pct": 40,
         "search_queries": []},
    ],
    "ending": "앞으로 볼 지표를 정리하며 마무리",
    "tone": "차분하지만 단정적인 해설",
}


def test_outline_is_validated_and_trimmed_to_the_contract():
    outline = extract_outline(_llm(GOOD), {"title": "t"}, TRANSCRIPT)

    assert [s["role"] for s in outline["sections"]] == ["기", "승", "결"]
    assert outline["sections"][0]["search_queries"] == ["코스피 급락 사이드카", "삼성전자 주가 하락"]  # 중복 제거
    assert outline["highlight"]["gist"].startswith("실적이 사상 최대")
    assert outline["ending"] and outline["tone"]


def test_outline_needs_a_transcript_and_enough_sections():
    assert extract_outline(_llm(GOOD), {}, None) is None
    assert extract_outline(_llm(GOOD), {}, "짧은 자막") is None
    too_few = {"sections": GOOD["sections"][:2]}
    assert extract_outline(_llm(too_few), {}, TRANSCRIPT) is None
    assert extract_outline(lambda s, u, m: "JSON 아님", {}, TRANSCRIPT) is None


def test_outline_caps_the_section_count_and_drops_partial_shares():
    many = {"sections": [
        {"role": "본론", "topic": f"주제 {i}", "claim_type": "원인 분석", "share_pct": None if i == 1 else 10,
         "search_queries": [f"검색 {i}"]}
        for i in range(12)
    ]}

    outline = extract_outline(_llm(many), {}, TRANSCRIPT)

    assert len(outline["sections"]) == MAX_SECTIONS
    assert all("share_pct" not in s for s in outline["sections"])  # 일부만 있는 비중은 강요하지 않는다


def test_outline_search_queries_are_ordered_deduped_and_capped():
    analysis = {"outline": {"sections": [
        {"search_queries": ["a1", "a2"]}, {"search_queries": ["a2", "b1"]}, {"search_queries": ["c1", "c2", "c3"]},
    ]}}

    # 구간마다 첫 검색어를 먼저 모은 뒤 둘째 검색어를 모은다(뒤쪽 구간이 잘리지 않게).
    assert outline_search_queries(analysis, limit=5) == ["a1", "a2", "c1", "b1", "c2"]
    assert outline_search_queries({}, limit=5) == []
    assert outline_search_queries(None) == []


def test_transcript_keeps_the_full_text_for_outline_but_truncates_for_summary(monkeypatch):
    long_text = "가" * 20_000
    transcript_module._fetch.cache_clear()
    monkeypatch.setattr(transcript_module, "_fetch", lambda video_id: long_text)

    assert len(transcript_module.fetch_transcript("vid")) == transcript_module.MAX_CHARS
    assert len(transcript_module.fetch_transcript("vid", max_chars=transcript_module.FULL_MAX_CHARS)) == 20_000


def test_slide_planner_is_told_to_follow_the_benchmark_outline():
    captured = {}

    def fake(system, messages, max_tokens):
        captured["system"] = system
        return "{}"

    plan_narrative(
        fake, selected_terms=["코스피"], verified_facts=[{"fact": "사실", "figure": ""}],
        candidate_context={"benchmark_points": {"outline": GOOD}}, format_name="longform", visual_mode="SLIDE",
    )

    assert "outline.sections" in captured["system"] and "share_pct" in captured["system"]
    assert "사실이 아니므로 fact_ids는 제공된 검증 사실에서만" in captured["system"]


def test_character_planner_never_mentions_outline():
    captured = {}

    def fake(system, messages, max_tokens):
        captured["system"] = system
        return "{}"

    plan_narrative(
        fake, selected_terms=["코스피"], verified_facts=[{"fact": "사실", "figure": ""}],
        candidate_context={}, format_name="longform", visual_mode="CHARACTER",
    )

    assert "outline" not in captured["system"]


class _FakeExtractor:
    calls: list[str] = []

    def search_recent_news(self, query, *, max_age_hours, limit, outlet_filter):
        _FakeExtractor.calls.append(query)
        if query == "실패 검색어":
            raise RuntimeError("boom")
        return [{"title": f"{query} 기사 {i}", "url": f"https://n/{query}/{i}"} for i in range(limit)]


def test_outline_news_collection_skips_failed_queries_and_respects_the_cap(monkeypatch):
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", _FakeExtractor)
    monkeypatch.setattr(script_worker, "OUTLINE_NEWS_CAP", 6)
    _FakeExtractor.calls = []

    rows = _collect_outline_news(["검색 A", "실패 검색어", "검색 B", "검색 C"])

    assert len(rows) == 6
    assert rows[0]["matched_keyword"] == "검색 A"
    assert "실패 검색어" in _FakeExtractor.calls  # 시도는 했고 건너뛰었다


def test_merge_news_rows_dedupes_by_title_and_url():
    base = [{"title": "가", "url": "u1"}]
    extra = [{"title": "가", "url": "u1"}, {"title": "나", "url": "u2"}]

    assert _merge_news_rows(base, extra) == [{"title": "가", "url": "u1"}, {"title": "나", "url": "u2"}]
    assert _collect_outline_news([]) == []


def test_old_benchmark_analysis_gets_an_outline_from_the_source_video(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery.transcript.fetch_transcript", lambda video_id, max_chars=6000: TRANSCRIPT,
    )
    analysis = {"topic_keyword": "코스피 급락", "hook_type": "number_context"}

    enriched = script_worker._ensure_benchmark_outline(
        analysis, [{"videoId": "abc"}], lambda s, u, m: json.dumps(GOOD, ensure_ascii=False),
    )

    assert enriched["outline"]["sections"][0]["role"] == "기"
    assert enriched["topic_keyword"] == "코스피 급락"
    assert "outline" not in analysis  # 원본 dict는 바꾸지 않는다


def test_outline_enrichment_is_a_noop_without_video_or_when_already_present():
    llm = lambda s, u, m: (_ for _ in ()).throw(AssertionError("호출되면 안 됨"))  # noqa: E731

    assert script_worker._ensure_benchmark_outline(None, [{"videoId": "a"}], llm) is None
    assert script_worker._ensure_benchmark_outline({"x": 1}, [], llm) == {"x": 1}
    already = {"outline": {"sections": []}}
    # outline이 비어 있어도(falsy) 다시 만들지만, 이미 채워져 있으면 그대로다.
    filled = {"outline": GOOD}
    assert script_worker._ensure_benchmark_outline(filled, [{"videoId": "a"}], llm) is filled
    assert already is not None


def test_enrichment_failure_keeps_the_original_analysis(monkeypatch):
    def boom(video_id, max_chars=6000):
        raise RuntimeError("자막 차단")

    monkeypatch.setattr("app.services.discovery.transcript.fetch_transcript", boom)
    analysis = {"topic_keyword": "t"}

    assert script_worker._ensure_benchmark_outline(analysis, [{"videoId": "a"}], lambda s, u, m: "{}") is analysis
