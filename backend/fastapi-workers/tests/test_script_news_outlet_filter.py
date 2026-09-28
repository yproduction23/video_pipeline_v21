import logging

from app.utils.candidate_scoring import score_candidates
from app.workers import script_worker


class _RecordingNewsExtractor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def search_recent_news(self, query, **kwargs):
        self.calls.append({"query": query, **kwargs})
        if kwargs.get("outlet_filter"):
            return [row for row in self.rows if row.get("outlet")]
        return list(self.rows)


def test_script_news_collection_uses_outlet_filter(monkeypatch):
    extractor = _RecordingNewsExtractor()
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: extractor)

    script_worker._collect_keyword_news(["반도체"])

    assert extractor.calls == [{
        "query": "반도체",
        "max_age_hours": 24 * 7,
        "limit": 6,
        "outlet_filter": True,
    }]


def test_candidate_scoring_news_uses_same_outlet_filter_as_script():
    extractor = _RecordingNewsExtractor()

    score_candidates(
        [{"keyword": "반도체", "reason": ""}],
        [],
        {},
        "KOSPI",
        "반도체",
        extractor=extractor,
    )

    assert extractor.calls
    assert extractor.calls[0]["outlet_filter"] is True


def test_zero_news_articles_logs_warning_not_raises(monkeypatch, caplog):
    extractor = _RecordingNewsExtractor()
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: extractor)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        script_worker,
        "plan_narrative",
        lambda *_args, **_kwargs: {"plan_id": "test_plan", "story_beats": []},
    )
    monkeypatch.setattr(
        script_worker,
        "review_flow",
        lambda *_args, **_kwargs: {"passed": True, "method": "test", "transition_issues": []},
    )
    worker = script_worker.ScriptWorker()
    monkeypatch.setattr(worker, "_multi_round_fact_check", lambda *_args, **_kwargs: ([], []))

    def _fake_generate(*_args, **_kwargs):
        full_script, sections = worker._mock_script("반도체 HBM", "개별 종목", 1)
        return full_script, sections, "테스트 제목", "테스트 썸네일", "테스트 설명", "테스트 쇼츠"

    monkeypatch.setattr(worker, "_generate_with_verified_facts", _fake_generate)

    with caplog.at_level(logging.WARNING):
        result = worker.generate(
            keyword="반도체 HBM",
            category="INDIVIDUAL_STOCK",
            target_minutes=1,
            market_data={"source": "test"},
            job_id=999,
        )

    assert result["news_cross_check_status"] == "no_finance_outlet_articles"
    assert "23개 금융 언론사 기사 0건" in caplog.text
    assert "뉴스 검증 없이 진행" in caplog.text


def test_non_finance_articles_excluded_from_script_cross_check(monkeypatch):
    extractor = _RecordingNewsExtractor([
        {
            "title": "반도체 투자 확대",
            "url": "https://www.hankyung.com/article/1",
            "outlet": "한국경제",
        },
        {
            "title": "반도체 선수 인터뷰",
            "url": "https://sports.example.test/article/2",
            "outlet": "",
        },
    ])
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: extractor)

    articles = script_worker._collect_keyword_news(["반도체"])

    assert len(articles) == 1
    assert articles[0]["outlet"] == "한국경제"
    assert articles[0]["matched_keyword"] == "반도체"


def test_non_factual_news_collection_broadens_beyond_finance_outlets(monkeypatch):
    # 2026-09-28: 해설형·창작형은 정치·시사·화제 소재를 다루는데, 뉴스 검색이
    # 항상 금융 언론사 23곳으로만 좁혀져 있어서 관련 기사가 거의 항상 0건이었다.
    # 그 결과 팩트체크가 벤치마크 영상 문맥 몇 줄만 가지고 진행돼, 목표 분량을
    # 채울 근거 자체가 부족했다(job 10: DMZ 지뢰·지지율 소재가 매 시도 분량 미달).
    extractor = _RecordingNewsExtractor()
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: extractor)

    for nature in ("EXPLAINER", "STORY"):
        extractor.calls.clear()
        script_worker._collect_keyword_news(["DMZ 지뢰"], content_nature=nature)
        assert extractor.calls[0]["outlet_filter"] is False


def test_factual_news_collection_keeps_outlet_filter(monkeypatch):
    extractor = _RecordingNewsExtractor()
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: extractor)

    script_worker._collect_keyword_news(["반도체"], content_nature="FACTUAL")

    assert extractor.calls[0]["outlet_filter"] is True


def test_non_factual_zero_news_warning_does_not_claim_finance_outlet_filter(monkeypatch, caplog):
    extractor = _RecordingNewsExtractor()
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: extractor)

    with caplog.at_level(logging.WARNING):
        script_worker._collect_keyword_news(["DMZ 지뢰"], content_nature="EXPLAINER")

    assert "금융 언론사" not in caplog.text


def test_news_search_phrases_splits_compound_keyword_on_particle_boundaries():
    phrases = script_worker._news_search_phrases("DMZ 지뢰 폭발 북한 연루 의혹과 이재명 지지율 하락")

    assert "DMZ 지뢰 폭발 북한 연루 의혹" in phrases
    assert "이재명 지지율" in phrases or "이재명 지지율 하락" in phrases
    assert "DMZ 지뢰 폭발 북한 연루 의혹과 이재명 지지율 하락" not in phrases


def test_news_search_phrases_returns_empty_for_short_natural_terms():
    # 이미 자연스러운 단일 주제는 추가로 쪼갤 필요가 없다.
    assert script_worker._news_search_phrases("반도체") == [] or "반도체" not in script_worker._news_search_phrases("반도체")


def test_collect_keyword_news_falls_back_to_noun_phrases_when_compound_term_finds_nothing(monkeypatch):
    # job 10 재현: 복합 키워드 전체 검색은 0건이지만, 조사 경계로 쪼갠 명사구
    # 검색에서는 기사가 나온다.
    rows_by_query = {
        "이재명 지지율 하락": [
            {"title": "이재명 지지율 최저치", "url": "https://example.test/1", "outlet": ""},
        ],
    }

    class Extractor:
        def __init__(self):
            self.queries = []

        def search_recent_news(self, query, **kwargs):
            self.queries.append(query)
            return rows_by_query.get(query, [])

    extractor = Extractor()
    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: extractor)
    monkeypatch.setattr(
        script_worker, "_news_search_phrases",
        lambda term: ["북한 연루 의혹", "이재명 지지율 하락"] if term == "DMZ 지뢰 폭발 북한 연루 의혹과 이재명 지지율 하락" else [],
    )

    articles = script_worker._collect_keyword_news(
        ["DMZ 지뢰 폭발 북한 연루 의혹과 이재명 지지율 하락"], content_nature="EXPLAINER",
    )

    assert extractor.queries[0] == "DMZ 지뢰 폭발 북한 연루 의혹과 이재명 지지율 하락"
    assert "북한 연루 의혹" in extractor.queries and "이재명 지지율 하락" in extractor.queries
    assert len(articles) == 1 and articles[0]["title"] == "이재명 지지율 최저치"


def test_collect_keyword_news_skips_phrase_fallback_when_direct_search_succeeds(monkeypatch):
    calls = []

    class Extractor:
        def search_recent_news(self, query, **kwargs):
            calls.append(query)
            return [{"title": "반도체 기사", "url": "https://example.test/2", "outlet": "한국경제"}]

    monkeypatch.setattr(script_worker, "NewsKeywordExtractor", lambda: Extractor())
    monkeypatch.setattr(script_worker, "_news_search_phrases", lambda term: ["절대 호출되면 안 됨"])

    script_worker._collect_keyword_news(["반도체"], content_nature="FACTUAL")

    assert calls == ["반도체"]
