"""급상승 영상 목록에서 핫키워드를 집계한다. 네트워크 호출이 없는 순수 로직."""
from __future__ import annotations

import re
from typing import Callable

from app.utils.keyword_aliases import STOP_WORDS

WINDOW_HOURS = {"48h": 48.0, "7d": 168.0}
RECENT_HOURS = 48.0
_FILLER = {
    "영상", "채널", "구독", "좋아요", "라이브", "공식", "official", "shorts", "쇼츠",
    "예고편", "하이라이트", "티저", "풀버전", "모음", "레전드", "진짜", "지금", "이슈",
}
_kiwi = None
_kiwi_unavailable = False


def _get_kiwi():
    global _kiwi, _kiwi_unavailable
    if _kiwi is None and not _kiwi_unavailable:
        try:
            from kiwipiepy import Kiwi
            _kiwi = Kiwi()
        except Exception:
            _kiwi_unavailable = True
    return _kiwi


def extract_nouns(text: str) -> list[str]:
    kiwi = _get_kiwi()
    if kiwi is None:
        return re.findall(r"[0-9A-Za-z가-힣]{2,}", text or "")
    analyzed = kiwi.analyze(text or "")
    if not analyzed:
        return []
    return [m.form for m in analyzed[0][0] if m.tag in ("NNG", "NNP", "SL") and len(m.form) >= 2]


def _keywords_of(video: dict, noun_extractor: Callable[[str], list[str]]) -> set[str]:
    text = " ".join([str(video.get("title") or "")] + [str(tag) for tag in (video.get("tags") or [])])
    found: set[str] = set()
    for noun in noun_extractor(text):
        word = noun.strip().lower()
        if len(word) < 2 or word.isdigit() or word in STOP_WORDS or word in _FILLER:
            continue
        found.add(word)
    return found


def aggregate_hot_keywords(
    videos: list[dict],
    window: str = "48h",
    noun_extractor: Callable[[str], list[str]] = extract_nouns,
    limit: int = 20,
) -> dict:
    if window not in WINDOW_HOURS:
        raise ValueError(f"지원하지 않는 기간입니다: {window}")
    rows = []
    for video in videos:
        hours = max(float(video.get("hoursSincePublish") or 0.0), 1.0)
        views = int(video.get("views") or 0)
        rows.append({
            **video,
            "viewsPerHour": round(views / hours, 1),
            "_hours": hours,
            "_kw": _keywords_of(video, noun_extractor),
        })

    horizon = WINDOW_HOURS["7d"]
    recent_kw = set().union(*[r["_kw"] for r in rows if r["_hours"] <= RECENT_HOURS])
    older_kw = set().union(*[r["_kw"] for r in rows if RECENT_HOURS < r["_hours"] <= horizon])

    in_window = [r for r in rows if r["_hours"] <= WINDOW_HOURS[window]]
    buckets: dict[str, dict] = {}
    for row in in_window:
        for keyword in row["_kw"]:
            bucket = buckets.setdefault(keyword, {"score": 0.0, "videos": []})
            bucket["score"] += row["viewsPerHour"]
            bucket["videos"].append(row)

    keywords = []
    for keyword, bucket in buckets.items():
        has_recent, has_older = keyword in recent_kw, keyword in older_kw
        persistence = "sustained" if has_recent and has_older else "spike" if has_recent else "fading"
        top = sorted(bucket["videos"], key=lambda v: v["viewsPerHour"], reverse=True)
        keywords.append({
            "keyword": keyword,
            "score": round(bucket["score"], 1),
            "videoCount": len(bucket["videos"]),
            "persistence": persistence,
            "sampleVideoIds": [v["videoId"] for v in top[:3]],
        })
    keywords.sort(key=lambda k: k["score"], reverse=True)

    videos_out = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in sorted(in_window, key=lambda r: r["viewsPerHour"], reverse=True)
    ]
    return {"window": window, "keywords": keywords[:limit], "videos": videos_out}
