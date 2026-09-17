"""Evidence-grounded keyword candidate scoring.

No score is inferred from an LLM.  Every numeric contribution is derived from
news lookups, the collected market snapshot, or public YouTube metrics.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from app.utils.topic_evidence import is_market_level_forecast, specific_terms
from app.workers.news_keyword_extractor import NewsKeywordExtractor


def _parse_published_at(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _article_matches(article: dict, terms: list[str]) -> bool:
    if not terms:
        return False
    text = " ".join(str(article.get(key, "")) for key in ("title", "summary", "description")).casefold()
    required = 1 if len(terms) == 1 else min(2, len(terms))
    return sum(term.casefold() in text for term in terms) >= required


def _grounded_numbers(news_keywords: list[dict], articles: list[dict], market_data: dict) -> set[float]:
    values: set[float] = set()

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            values.add(round(float(value), 1))
        elif isinstance(value, str):
            for number in re.findall(r"\d+(?:\.\d+)?", value):
                values.add(round(float(number), 1))

    for item in news_keywords:
        collect(item.get("keyword"))
        collect(item.get("sample_headline"))
    for article in articles:
        collect(article.get("title"))
        collect(article.get("summary"))
    collect(market_data or {})
    return values


def _candidate_has_numeric_claim(candidate: dict) -> tuple[bool, bool]:
    text = f"{candidate.get('keyword', '')} {candidate.get('reason', '')}"
    values = [round(float(value), 1) for value in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
    return bool(values), False if values else True


def _market_metrics_for_category(market_data: dict, category: str, candidate: dict) -> list[str]:
    if not isinstance(market_data, dict):
        return []
    category = (category or "").upper()
    text = f"{candidate.get('keyword', '')} {candidate.get('reason', '')}".casefold()
    metrics: list[str] = []
    kr = market_data.get("kr") or {}
    us = market_data.get("us") or {}
    associated = market_data.get("associated_data") or {}

    if category in {"KOSPI", "KOSDAQ", "INDIVIDUAL_STOCK"}:
        index_name = "kosdaq" if category == "KOSDAQ" else "kospi"
        if ((kr.get("index") or {}).get(index_name)):
            metrics.append(f"kr.index.{index_name}")
        if (kr.get("market_indicators") or {}).get("usd_krw") is not None:
            metrics.append("kr.market_indicators.usd_krw")
    elif category in {"US_STOCKS", "GLOBAL_MACRO"}:
        for index_name in ("sp500", "nasdaq"):
            if ((us.get("index") or {}).get(index_name)):
                metrics.append(f"us.index.{index_name}")
        for name in ("fed_rate", "cpi", "unemployment", "us_10yr_yield"):
            if (us.get("macro") or {}).get(name) is not None:
                metrics.append(f"us.macro.{name}")
    if associated.get("associated_stocks"):
        metrics.append("associated_data.associated_stocks")

    # A clearly mismatched market must not receive a full category score.
    if category == "US_STOCKS" and any(token in text for token in ("코스피", "코스닥")):
        return []
    if category in {"KOSPI", "KOSDAQ"} and any(token in text for token in ("s&p", "nasdaq", "dow jones")):
        return []
    return metrics


def _category_score(market_data: dict, category: str, candidate: dict) -> int:
    metrics = _market_metrics_for_category(market_data, category, candidate)
    if metrics:
        return 20
    if market_data:
        return 10
    return 5


def _youtube_score(candidate: dict) -> int | None:
    videos = candidate.get("source_videos") or []
    # 실제 영상 근거가 없는 후보에는 0점도 부여하지 않고 미수집 상태를 유지한다.
    if not videos:
        return None
    engagement = float(candidate.get("engagement_ratio") or 0)
    outperformance = float(candidate.get("outperformance_index") or 0)
    return round(min(engagement, 5) / 5 * 7 + min(outperformance, 4) / 4 * 8)


def _calculate_momentum(direct_news_24h: list[dict], direct_news_7d: list[dict]) -> tuple[int, str, float]:
    """Calculate momentum direction and ratio with Low Volume Floor Exception.
    
    Labels:
      - 'protected_low_volume': 7-day news < 4 (Total 1~3 articles, true low-volume niche protection, 0 penalty)
      - 'rising': 7-day news >= 4 and ratio >= 1.5 (+5 bonus)
      - 'falling': 7-day news >= 4 and ratio <= 0.5 (-5 penalty)
      - 'flat': 7-day news >= 4 and 0.5 < ratio < 1.5 (0 neutral)
    """
    count_24h = len(direct_news_24h)
    count_7d = len(direct_news_7d)
    avg_7d = count_7d / 7.0

    # Low Volume Floor Exception: Total 7-day articles < 4 (1~3 articles) is a true low-volume niche topic.
    # Do NOT apply negative momentum penalty to low-volume topics.
    if count_7d < 4:
        ratio = round(count_24h / max(avg_7d, 0.1), 2) if avg_7d > 0 else 0.0
        return 0, "protected_low_volume", ratio

    ratio = round(count_24h / avg_7d, 2)
    if ratio >= 1.5:
        return 5, "rising", ratio
    elif ratio <= 0.5:
        return -5, "falling", ratio
    else:
        return 0, "flat", ratio


def _calculate_market_volatility(market_data: dict, category: str, candidate: dict) -> tuple[int, float]:
    """Calculate market move magnitude z-score with 4-tier granular volatility bonuses.
    
    Tiers:
      - z >= 4.0: Extreme volatility (+7 bonus)
      - 2.0 <= z < 4.0: High volatility (+5 bonus)
      - 1.0 <= z < 2.0: Moderate volatility (+3 bonus)
      - z < 1.0: Normal volatility (0 bonus)
    """
    if not isinstance(market_data, dict):
        return 0, 0.0
    text = f"{candidate.get('keyword', '')} {candidate.get('reason', '')}".casefold()
    kr = market_data.get("kr") or {}
    us = market_data.get("us") or {}

    max_change_pct = 0.0
    indices = []
    if category in {"KOSPI", "KOSDAQ", "INDIVIDUAL_STOCK"}:
        idx = kr.get("index") or {}
        for k in ("kospi", "kosdaq"):
            if idx.get(k):
                indices.append(abs(float(idx[k].get("change_pct") or 0)))
    elif category in {"US_STOCKS", "GLOBAL_MACRO"}:
        idx = us.get("index") or {}
        for k in ("sp500", "nasdaq"):
            if idx.get(k):
                indices.append(abs(float(idx[k].get("change_pct") or 0)))

    if indices:
        max_change_pct = max(indices)
    
    # Baseline daily volatility std dev assumed to be ~1.2%
    z_score = round(max_change_pct / 1.2, 2)
    
    if z_score >= 4.0:
        volatility_bonus = 7
    elif z_score >= 2.0:
        volatility_bonus = 5
    elif z_score >= 1.0:
        volatility_bonus = 3
    else:
        volatility_bonus = 0

    return volatility_bonus, z_score


def _calculate_global_macro(category: str, candidate: dict) -> tuple[int, float, list[str]]:
    """Calculate global macro relevance score and events."""
    text = f"{candidate.get('keyword', '')} {candidate.get('reason', '')}".casefold()
    global_keywords = {
        "fomc": "FOMC 일정",
        "금리": "연준 금리 정책",
        "cpi": "미국 CPI 발표",
        "인플레이션": "글로벌 인플레이션",
        "환율": "달러/원 환율",
        "유가": "국제 유가",
        "빅테크": "미국 빅테크 실적",
        "엔화": "엔화 환율",
    }
    found_events = [event for kw, event in global_keywords.items() if kw in text]

    category_upper = (category or "").upper()
    if category_upper not in {"US_STOCKS", "GLOBAL_MACRO"}:
        # KOSPI/CUSTOM 등 비거시 카테고리 후보는 본문에 "환율", "금리" 같은
        # 단어가 우연히 섞였다는 이유만으로 가산점을 받으면 안 된다. 이
        # 가산점이 있으면 "애프터마켓"처럼 특정 주제를 다루는 후보가, 같은
        # 주제에 무관한 거시 키워드를 곁들인 경쟁 후보에게 점수로 밀려서
        # 사용자가 고른 좁은 주제 대신 시장 전반을 다루는 후보가 자동으로
        # 상위 랭크되는 문제가 있었다(2026-09-17, 애프터마켓 대본 사고).
        return 0, 0.0, found_events
    relevance = 1.0 if found_events else 0.8
    bonus = 5 if relevance >= 0.8 else 0
    return bonus, relevance, found_events


def score_candidates(candidates: list[dict], news_keywords: list[dict], market_data: dict,
                     category: str, seed: str, extractor: NewsKeywordExtractor | None = None) -> list[dict]:
    """Attach an auditable 0–100 score and raw evidence to each candidate."""
    extractor = extractor or NewsKeywordExtractor()
    scored: list[dict] = []
    for source_candidate in candidates:
        candidate = dict(source_candidate)
        keyword = str(candidate.get("keyword") or "").strip()
        terms = specific_terms(seed) or specific_terms(keyword)
        lookup_failed = False
        recent_news_24h: list[dict] = []
        recent_news_7d: list[dict] = []

        try:
            recent_news_7d = extractor.search_recent_news(
                keyword or seed,
                max_age_hours=24 * 7,
                limit=12,
                outlet_filter=True,
            )
            recent_news_24h = [art for art in recent_news_7d if float(art.get("hoursSincePublish") or 999) <= 24]
        except Exception:
            recent_news_7d = []
            recent_news_24h = []
            lookup_failed = True

        direct_news_7d = [article for article in recent_news_7d if _article_matches(article, terms)]
        direct_news_24h = [article for article in recent_news_24h if _article_matches(article, terms)]
        latest = max((_parse_published_at(article.get("publishedAt")) for article in direct_news_7d), default=None)

        # Base news count & freshness
        count_score = min(len(direct_news_7d), 4) / 4 * 24
        freshness_score = 0
        if latest:
            age_hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600
            freshness_score = 16 if age_hours <= 24 else 10 if age_hours <= 72 else 5 if age_hours <= 24 * 7 else 0
        
        # Momentum adjustment (with Low Volume Floor Exception)
        momentum_bonus, momentum_dir, momentum_ratio = _calculate_momentum(direct_news_24h, direct_news_7d)
        
        if lookup_failed:
            news_score = 0
        else:
            news_score = max(0, min(40, round(count_score + freshness_score + momentum_bonus)))

        # Market data & Volatility z-score
        market_outlook = is_market_level_forecast([keyword or seed])
        claims_numbers = [round(float(value), 1) for value in re.findall(
            r"(\d+(?:\.\d+)?)\s*%", f"{candidate.get('keyword', '')} {candidate.get('reason', '')}"
        )]
        grounded = _grounded_numbers(news_keywords, direct_news_7d, market_data)
        if claims_numbers:
            numeric_claims_verified: bool | None = all(
                any(abs(number - evidence) <= 0.1 for evidence in grounded) for number in claims_numbers
            )
            numeric_points = 15 if numeric_claims_verified else 0
        else:
            numeric_claims_verified = None
            numeric_points = 7
        market_metrics = _market_metrics_for_category(market_data, category, candidate)
        
        volatility_bonus, z_score = _calculate_market_volatility(market_data, category, candidate)
        base_market_score = numeric_points + (20 if market_outlook and market_metrics else 10 if market_metrics else 0)
        market_data_score = max(0, min(35, base_market_score + volatility_bonus))

        # Category score & Global macro relevance
        global_bonus, global_relevance, global_events = _calculate_global_macro(category, candidate)
        base_cat_score = _category_score(market_data, category, candidate)
        category_score = max(0, min(20, base_cat_score + global_bonus))

        youtube_score = _youtube_score(candidate)
        raw_score = news_score + market_data_score + category_score
        total_score = raw_score + youtube_score if youtube_score is not None else raw_score * 100 / 85

        if lookup_failed:
            total_score = 0

        candidate.update({
            "score": round(total_score),
            "news_score": news_score,
            "market_data_score": market_data_score,
            "category_score": category_score,
            "youtube_score": youtube_score,
            "metrics_available": youtube_score is not None,
            "news_articles": [
                {
                    "title": str(article.get("title") or ""),
                    "link": str(article.get("url") or article.get("link") or ""),
                    "outlet": str(article.get("outlet") or ""),
                    "pubDate": str(article.get("publishedAt") or article.get("pubDate") or ""),
                }
                for article in direct_news_7d
                if str(article.get("url") or article.get("link") or "").strip()
            ],
            "evidence": {
                "news_count": len(direct_news_7d),
                "latest_news_at": latest.isoformat() if latest else None,
                "news_sources": sorted({str(article.get("source") or "Google News") for article in direct_news_7d}),
                "numeric_claims_verified": numeric_claims_verified,
                "market_metrics": market_metrics,
                "youtube_data_available": youtube_score is not None,
                "evidence_video_ids": candidate.get("evidence_video_ids") or [
                    video.get("video_id") for video in (candidate.get("source_videos") or []) if video.get("video_id")
                ],
                "news_lookup_failed": lookup_failed,
                "momentum_direction": momentum_dir,
                "momentum_ratio": momentum_ratio,
                "market_move_magnitude": z_score,
                "volume_spike": None,
                "global_macro_relevance": global_relevance,
                "related_global_events": global_events,
            },
        })
        candidate["auto_confirm_eligible"] = bool(
            candidate["score"] >= 55 and (len(direct_news_7d) >= 1 or (market_outlook and bool(market_data)))
        )
        scored.append(candidate)
    return scored
