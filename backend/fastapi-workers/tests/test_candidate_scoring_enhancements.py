"""Unit tests for enhanced candidate scoring with 3 signals and Low Volume Floor Exception."""

import pytest
from app.utils.candidate_scoring import score_candidates


class DummyExtractor:
    def __init__(self, news_7d, fail=False):
        self._news_7d = news_7d
        self._fail = fail

    def search_recent_news(
        self,
        query: str,
        max_age_hours: int = 72,
        limit: int = 6,
        outlet_filter: bool = False,
    ):
        _ = outlet_filter
        if self._fail:
            raise RuntimeError("API failure simulated")
        return self._news_7d


def test_momentum_rising_and_low_volume_floor_exception():
    candidates = [{"keyword": "삼성전자", "reason": "반도체 실적 개선"}]

    # Case 1: Low volume niche topic (7d total 2 < 4) -> Low Volume Floor Exception -> protected_low_volume
    news_low_volume = [
        {"title": "삼성전자 반도체 이슈", "hoursSincePublish": 10, "publishedAt": "2026-08-06T00:00:00Z"},
        {"title": "삼성전자 반도체 뉴스", "hoursSincePublish": 40, "publishedAt": "2026-08-05T00:00:00Z"},
    ]
    extractor_niche = DummyExtractor(news_low_volume, fail=False)
    res_niche = score_candidates(candidates, [], {}, "KOSPI", "삼성전자", extractor=extractor_niche)
    assert res_niche[0]["evidence"]["momentum_direction"] == "protected_low_volume"

    # Case 2: High volume topic (7d total 35, avg 5.0), but only 1 article is within 24h (hoursSincePublish=10), 34 articles > 24h (hoursSincePublish=48)
    news_falling = [
        {"title": "삼성전자 반도체 24h", "hoursSincePublish": 10, "publishedAt": "2026-08-06T00:00:00Z"}
    ] + [
        {"title": "삼성전자 반도체 과거", "hoursSincePublish": 48, "publishedAt": "2026-08-04T00:00:00Z"}
    ] * 34
    extractor_falling = DummyExtractor(news_falling, fail=False)
    res_falling = score_candidates(candidates, [], {}, "KOSPI", "삼성전자", extractor=extractor_falling)
    assert res_falling[0]["evidence"]["momentum_direction"] == "falling"

    # Case 3: High volume topic (7d total 35, avg 5.0), 30 articles within 24h (hoursSincePublish=5) -> rising
    news_rising = [
        {"title": "삼성전자 반도체 최신", "hoursSincePublish": 5, "publishedAt": "2026-08-06T00:00:00Z"}
    ] * 30 + [
        {"title": "삼성전자 반도체 과거", "hoursSincePublish": 48, "publishedAt": "2026-08-04T00:00:00Z"}
    ] * 5
    extractor_rising = DummyExtractor(news_rising, fail=False)
    res_rising = score_candidates(candidates, [], {}, "KOSPI", "삼성전자", extractor=extractor_rising)
    assert res_rising[0]["evidence"]["momentum_direction"] == "rising"


def test_market_volatility_z_score():
    candidates = [{"keyword": "코스피", "reason": "증시 변동"}]
    # High volatility kospi change +2.5% -> z-score = 2.5 / 1.2 = 2.08 >= 1.5
    market_data_high = {"kr": {"index": {"kospi": {"close": 2700, "change_pct": 2.5}}}}
    res_high = score_candidates(candidates, [], market_data_high, "KOSPI", "코스피", extractor=DummyExtractor([], False))
    assert res_high[0]["evidence"]["market_move_magnitude"] >= 1.5

    # Low volatility kospi change +0.2% -> z-score = 0.2 / 1.2 = 0.17 < 1.5
    market_data_low = {"kr": {"index": {"kospi": {"close": 2700, "change_pct": 0.2}}}}
    res_low = score_candidates(candidates, [], market_data_low, "KOSPI", "코스피", extractor=DummyExtractor([], False))
    assert res_low[0]["evidence"]["market_move_magnitude"] < 1.5


def test_global_macro_relevance():
    candidates_global = [{"keyword": "FOMC 금리 결정", "reason": "연준 기준금리 및 미국 CPI 지표 발표"}]
    res = score_candidates(candidates_global, [], {}, "GLOBAL_MACRO", "FOMC", extractor=DummyExtractor([], False))
    assert res[0]["evidence"]["global_macro_relevance"] >= 0.8
    assert "FOMC 일정" in res[0]["evidence"]["related_global_events"]


def test_global_macro_bonus_does_not_leak_into_non_macro_categories():
    """2026-09-17 사용자 재현: CUSTOM/KOSPI 같은 비거시 카테고리에서, 사용자가
    고른 좁은 주제("애프터마켓")의 후보가 그 주제와 무관하게 "환율"·"금리"
    단어만 곁들인 경쟁 후보에게 거시 가산점 때문에 점수로 밀려서는 안 된다.
    실제로 이 버그 때문에 '애프터마켓' 대본 대신 환율·금리 위주 대본이 나왔다."""
    plain = {"keyword": "애프터마켓 오픈 시대", "reason": "애프터마켓 거래 시간 확대 효과를 설명한다"}
    diluted = {
        "keyword": "애프터마켓 오픈 시대 — 나스닥 ETF 웃돈 현상과 달러/원 환율 주목해야 하는 이유",
        "reason": "환율과 금리 변수도 함께 짚는다",
    }
    extractor = DummyExtractor([], fail=False)

    res = score_candidates([plain, diluted], [], {}, "CUSTOM", "애프터마켓", extractor=extractor)

    assert res[0]["evidence"]["global_macro_relevance"] == 0.0
    assert res[1]["evidence"]["global_macro_relevance"] == 0.0
    assert res[0]["category_score"] == res[1]["category_score"]
    # 거시 단어가 감지는 되지만(감사용), 비거시 카테고리에서는 점수에 반영되지 않는다.
    assert res[1]["evidence"]["related_global_events"]


def test_graceful_degradation_on_api_failure():
    candidates = [{"keyword": "테스트", "reason": "설명"}]
    extractor_fail = DummyExtractor([], fail=True)
    res = score_candidates(candidates, [], {}, "KOSPI", "테스트", extractor=extractor_fail)
    assert res[0]["score"] == 0
    assert res[0]["evidence"]["news_lookup_failed"] is True
