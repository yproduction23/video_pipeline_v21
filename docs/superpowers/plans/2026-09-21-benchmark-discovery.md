# 핫키워드 · 벤치마크 채널 발견 → 벤치마크 제작 구현 계획 (덩어리 ①)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 카테고리별 핫키워드와 벤치마크 채널 신작을 메인화면에서 발견하고, 고른 영상으로 키워드 재검색 없이 롱폼 제작을 시작한다.

**Architecture:** FastAPI에 순수 집계 로직(`hot_keywords.py`)과 YouTube 조회 서비스(`services/discovery/`)를 추가하고, Spring이 이를 프록시하면서 `POST /api/jobs/from-benchmark`로 작업 생성 + `KEYWORD` 에셋 저장 + 기존 `KeywordService.confirm`(게이트 승인이 파이프라인을 시작)을 호출한다. 대본 워커는 기존 `candidate_evidence` 통로로 `benchmark_analysis`를 받아 프롬프트와 내러티브 플래너에 참고 자료로만 넘긴다. 프런트는 `TrendingSidebar`를 `BenchmarkDiscovery`로 교체하고 `JobNew`가 벤치마크 상태를 받으면 STEP 01을 건너뛴다.

**Tech Stack:** FastAPI + pytest(도커 컨테이너에서 실행), kiwipiepy, requests, Redis / Spring Boot 3.5(Java 17, Gradle) + Mockito·AssertJ / React 18 + Vite + TanStack Query + Tailwind

**Spec:** `docs/superpowers/specs/2026-09-21-benchmark-discovery-design.md`

## Global Constraints

- UI 텍스트와 코드 주석은 한국어로 쓰고, 주석은 "왜"가 자명하지 않을 때만 한 줄로 쓴다.
- API 키(`YOUTUBE_API_KEY`, `ANTHROPIC_API_KEY`)를 하드코딩하지 않는다. 키가 없으면 가짜 데이터를 만들지 않고 명시적 오류를 반환한다.
- YouTube 호출은 반드시 `app.providers.real.trending._consume_quota`를 거친다.
- LLM 모델은 `app.config.CLAUDE_MODEL`(`claude-sonnet-4-6`)만 사용한다.
- 영상 제목·조회수는 사실 근거가 아니라 주제·관심도 문맥이다. 다른 창작자의 문장을 그대로/거의 그대로 복제하지 않는다(훅의 유형·구조·리듬만 참고).
- 기존 `jobs/{id}/keyword/*` 흐름, 일반 `롱폼 만들기` 흐름, 기존 fastapi 테스트(1146개)를 깨지 않는다.
- 카테고리 목록은 실측으로 차트가 확인된 것만 사용한다: 전체(파라미터 생략), 1, 10, 15, 17, 20, 22, 23, 24, 25, 26, 28. 교육(27)은 제외한다.

## 명세 대비 조정 (실행자는 그대로 따른다)

1. 상위 댓글 수집은 이번에 하지 않는다(`commentThreads.list`가 별도 쿼터 버킷이라 추후). 분석 입력은 제목·태그·설명·통계다.
2. 선택 패널은 측정 가능한 지표(조회수, 시간당 조회수, 구독자 대비, 채널 평균 대비, 태그)를 보여주고 "뜨는 이유 분석은 제작 시작 시 자동으로 수행됩니다"라고 안내한다. 미리보기용 분석 엔드포인트는 만들지 않는다.
3. 레퍼런스 채널 "적용 채널"은 생성 후 편집에서 지정한다(생성 폼은 공용으로 생성).
4. 비경제 소재 전용 안내 문구("이 소재는 아직 제작 미지원")는 덩어리 ②에서 만든다. ①에서는 대본 단계의 기존 근거 부족 오류가 그대로 표시된다.

## 테스트 실행 방법

FastAPI (로컬에 파이썬 의존성이 없으므로 실행 중인 컨테이너에 파일을 복사해 실행한다. 저장소 루트 `C:/Users/jam/Documents/GitHub/video_pipeline_v21`에서):

```bash
docker cp backend/fastapi-workers/app/. pipeline_fastapi:/app/app/ && docker cp backend/fastapi-workers/tests/. pipeline_fastapi:/app/tests/ && docker exec pipeline_fastapi python -m pytest <테스트 경로> -q
```

Spring (저장소 루트에서):

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "C:/Users/jam/Documents/GitHub/video_pipeline_v21/backend:/work" -v gradle-cache:/home/gradle/.gradle -w /work/spring-app gradle:8.7-jdk17 gradle test --no-daemon --tests "<테스트 클래스>"
```

---

## 파일 구조

| 파일 | 역할 |
| --- | --- |
| `backend/fastapi-workers/app/utils/hot_keywords.py` (신규) | 급상승 영상 목록 → 핫키워드 집계(순수 로직, 네트워크 없음) |
| `backend/fastapi-workers/app/services/discovery/__init__.py` (신규) | 패키지 표시 |
| `backend/fastapi-workers/app/services/discovery/youtube_discovery.py` (신규) | 급상승 차트·채널 업로드·단일 영상 조회(쿼터·캐시 포함) |
| `backend/fastapi-workers/app/services/discovery/benchmark_analysis.py` (신규) | 영상 공개 정보 → Claude 1회 "뜨는 이유" 분석 |
| `backend/fastapi-workers/app/main.py` (수정) | `/workers/discovery/*`, `/workers/benchmark/analyze` 엔드포인트 |
| `backend/fastapi-workers/app/workers/script_worker.py` (수정) | `benchmark_analysis`를 프롬프트·플래너에 전달 |
| `backend/fastapi-workers/app/utils/narrative_planner.py` (수정) | 훅 "모사 금지" → "유형·구조 참고, 문장 복제 금지" |
| `backend/spring-app/.../domain/ReferenceChannel.java` (수정) | `ownerChannelId` 추가 |
| `backend/spring-app/.../repository/ReferenceChannelRepository.java` (수정) | 소유 채널 기준 조회 |
| `backend/spring-app/.../dto/ReferenceChannelCreateRequest.java`, `ReferenceChannelUpdateRequest.java` (수정) | `ownerChannelId` 필드 |
| `backend/spring-app/.../service/ReferenceChannelService.java` (수정) | `listForOwner`, 소유 채널 저장 |
| `backend/spring-app/.../service/FastApiClient.java` (수정) | discovery·분석 호출 메서드 |
| `backend/spring-app/.../dto/BenchmarkJobRequest.java` (신규) | `{videoId, job}` |
| `backend/spring-app/.../service/BenchmarkService.java` (신규) | 채널 신작 조회, 벤치마크 작업 생성 |
| `backend/spring-app/.../service/ScriptService.java` (수정) | `benchmark_analysis` 전달 |
| `backend/spring-app/.../controller/BenchmarkController.java` (신규) | 3개 공개 API |
| `frontend/src/api/discovery.js` (신규) | API 래퍼 |
| `frontend/src/components/dashboard/BenchmarkDiscovery.jsx` (신규) | 메인화면 발견 UI |
| `frontend/src/pages/Dashboard.jsx` (수정) | `TrendingSidebar` 교체 |
| `frontend/src/pages/JobNew.jsx` (수정) | 벤치마크 모드(STEP 01 생략, 배지, 제출 분기) |
| `frontend/src/components/admin/ReferenceChannelManager.jsx` (수정) | 적용 채널 표시·편집 |

(Spring 경로 `...`은 `backend/spring-app/src/main/java/com/pipeline/video`, 테스트는 `backend/spring-app/src/test/java/com/pipeline/video`)

---

### Task 1: 핫키워드 집계 로직 (FastAPI 순수 함수)

**Files:**
- Create: `backend/fastapi-workers/app/utils/hot_keywords.py`
- Test: `backend/fastapi-workers/tests/test_hot_keywords.py`

**Interfaces:**
- Produces: `extract_nouns(text: str) -> list[str]`; `aggregate_hot_keywords(videos: list[dict], window: str = "48h", noun_extractor: Callable[[str], list[str]] = extract_nouns, limit: int = 20) -> dict` — 입력 영상 dict는 `videoId, title, views, hoursSincePublish, tags`를 가진다. 반환 `{"window", "keywords": [{keyword, score, videoCount, persistence, sampleVideoIds}], "videos": [입력 필드 + viewsPerHour]}`. `persistence`는 `"sustained" | "spike" | "fading"`. `WINDOW_HOURS = {"48h": 48.0, "7d": 168.0}`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""핫키워드 집계: 기간 창, 시간당 조회수, 지속성 판정, 불용어 제외."""
import pytest

from app.utils.hot_keywords import aggregate_hot_keywords


def split(text):
    return text.split()


def video(video_id, title, hours, views, tags=None):
    return {"videoId": video_id, "title": title, "hoursSincePublish": hours, "views": views, "tags": tags or []}


def test_48h_window_excludes_older_videos():
    videos = [video("a", "부산상어 챌린지", 10, 10000), video("b", "환율 전망", 100, 50000)]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert [v["videoId"] for v in result["videos"]] == ["a"]
    assert {k["keyword"] for k in result["keywords"]} == {"부산상어", "챌린지"}


def test_views_per_hour_clamps_very_fresh_videos_to_one_hour():
    result = aggregate_hot_keywords([video("a", "부산상어", 0.2, 500)], "48h", split)

    assert result["videos"][0]["viewsPerHour"] == 500.0


def test_persistence_labels_in_7d_window():
    videos = [
        video("a", "부산상어 신곡", 10, 10000),
        video("b", "부산상어 리믹스", 100, 20000),
        video("c", "애프터마켓 오픈", 5, 8000),
        video("d", "환율 급등", 120, 9000),
    ]

    result = aggregate_hot_keywords(videos, "7d", split)
    labels = {k["keyword"]: k["persistence"] for k in result["keywords"]}

    assert labels["부산상어"] == "sustained"
    assert labels["애프터마켓"] == "spike"
    assert labels["환율"] == "fading"


def test_48h_window_marks_keyword_sustained_when_older_videos_share_it():
    videos = [video("a", "부산상어 신곡", 10, 10000), video("b", "부산상어 리믹스", 100, 20000)]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert {k["keyword"]: k["persistence"] for k in result["keywords"]}["부산상어"] == "sustained"


def test_stop_words_filler_and_short_tokens_are_dropped():
    videos = [video("a", "영상 시장 부산상어 a 2026", 5, 1000)]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert [k["keyword"] for k in result["keywords"]] == ["부산상어"]


def test_keywords_sorted_by_score_and_limited():
    videos = [video("a", "상어", 10, 1000), video("b", "환율", 10, 9000)]

    result = aggregate_hot_keywords(videos, "48h", split, limit=1)

    assert [k["keyword"] for k in result["keywords"]] == ["환율"]


def test_tags_contribute_keywords():
    videos = [video("a", "제목", 10, 1000, tags=["부산상어"])]

    result = aggregate_hot_keywords(videos, "48h", split)

    assert "부산상어" in {k["keyword"] for k in result["keywords"]}


def test_invalid_window_raises():
    with pytest.raises(ValueError):
        aggregate_hot_keywords([], "30d", split)
```

- [ ] **Step 2: 실패 확인**

Run: 위 "FastAPI 테스트 실행 방법"의 명령에 `tests/test_hot_keywords.py`를 넣어 실행
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.hot_keywords'`

- [ ] **Step 3: 구현**

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: Step 2와 같은 명령
Expected: `8 passed`

- [ ] **Step 5: 커밋**

```bash
git add backend/fastapi-workers/app/utils/hot_keywords.py backend/fastapi-workers/tests/test_hot_keywords.py
git commit -m "feat(discovery): 급상승 영상 핫키워드 집계 로직 추가"
```

---

### Task 2: YouTube 조회 서비스 (급상승 차트·채널 업로드·단일 영상)

**Files:**
- Create: `backend/fastapi-workers/app/services/discovery/__init__.py` (빈 파일)
- Create: `backend/fastapi-workers/app/services/discovery/youtube_discovery.py`
- Test: `backend/fastapi-workers/tests/test_youtube_discovery.py`

**Interfaces:**
- Consumes: `aggregate_hot_keywords` (Task 1), `app.providers.real.trending._consume_quota(redis, units, operation) -> bool`, `_get_redis_client()`, `_CACHE_TTL_SECONDS`.
- Produces: `DiscoveryError(code: str, message)` — `code`는 `NO_API_KEY | QUOTA | UNSUPPORTED_CATEGORY | NOT_FOUND | UPSTREAM`. `CATEGORIES: dict[str, {"id": str|None, "label": str}]`. `YouTubeDiscovery().hot_keywords(category_key="ALL", window="48h") -> dict`(Task 1 결과 + `category`, `categories`), `.recent_uploads(channel_ids: list[str], days: int = 7) -> {"videos": [...], "channels": [...]}`(영상 행에 `outperformanceIndex` 포함), `.fetch_video(video_id: str) -> dict`(`description` 포함). 영상 행 키: `videoId, title, channelId, channelTitle, publishedAt, hoursSincePublish, views, likes, comments, durationSeconds, tags, categoryId, isLive`, 차트 결과에는 `subscribers, subscriberCountAvailable` 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""YouTube 조회 서비스: 카테고리 파라미터, 오류 매핑, 채널 신작 계산."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.discovery import youtube_discovery as yd
from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def patched(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(yd, "_get_redis_client", lambda: None)
    monkeypatch.setattr(yd, "_consume_quota", lambda *args, **kwargs: True)


def install(monkeypatch, handler):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        return handler(url, params or {})

    monkeypatch.setattr(yd.requests, "get", fake_get)
    return calls


def video_item(video_id, title, hours_ago, views, live="none", channel_id="UC1"):
    return {
        "id": video_id,
        "snippet": {"title": title, "channelId": channel_id, "channelTitle": "채널", "publishedAt": iso(hours_ago),
                    "tags": ["태그"], "categoryId": "25", "liveBroadcastContent": live, "description": "설명" * 10},
        "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "2"},
        "contentDetails": {"duration": "PT1M30S"},
    }


def chart_handler(url, params):
    if url.endswith("/videos"):
        return FakeResponse(200, {"items": [video_item("v1", "부산상어 챌린지", 5, 5000), video_item("v2", "라이브", 3, 9000, live="live")]})
    if url.endswith("/channels"):
        return FakeResponse(200, {"items": [{"id": "UC1", "statistics": {"subscriberCount": "1200"}}]})
    raise AssertionError(url)


def test_all_category_omits_video_category_id(monkeypatch):
    calls = install(monkeypatch, chart_handler)

    YouTubeDiscovery().hot_keywords("ALL", "48h")

    chart_params = next(p for u, p in calls if u.endswith("/videos"))
    assert "videoCategoryId" not in chart_params
    assert chart_params["chart"] == "mostPopular" and chart_params["regionCode"] == "KR"


def test_specific_category_sends_id_and_excludes_live_videos(monkeypatch):
    calls = install(monkeypatch, chart_handler)

    result = YouTubeDiscovery().hot_keywords("NEWS", "48h")

    chart_params = next(p for u, p in calls if u.endswith("/videos"))
    assert chart_params["videoCategoryId"] == "25"
    assert [v["videoId"] for v in result["videos"]] == ["v1"]
    assert result["videos"][0]["subscribers"] == 1200
    assert {"key": "NEWS", "label": "뉴스·정치"} in result["categories"]


def test_unsupported_category_maps_to_error(monkeypatch):
    install(monkeypatch, lambda url, params: FakeResponse(404, {"error": {"errors": [{"reason": "notFound"}]}}))

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("TECH", "48h")

    assert error.value.code == "UNSUPPORTED_CATEGORY"


def test_unknown_category_key_is_rejected():
    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("EDU", "48h")

    assert error.value.code == "UNSUPPORTED_CATEGORY"


def test_missing_api_key_raises_without_calling_youtube(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("ALL", "48h")

    assert error.value.code == "NO_API_KEY"


def test_quota_exhaustion_raises(monkeypatch):
    monkeypatch.setattr(yd, "_consume_quota", lambda *args, **kwargs: False)

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().hot_keywords("ALL", "48h")

    assert error.value.code == "QUOTA"


def test_recent_uploads_computes_outperformance_and_filters_by_days(monkeypatch):
    def handler(url, params):
        if url.endswith("/channels"):
            return FakeResponse(200, {"items": [{"id": "UC1", "snippet": {"title": "채널1"}, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}, "statistics": {"subscriberCount": "1000"}}]})
        if url.endswith("/playlistItems"):
            return FakeResponse(200, {"items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}]})
        if url.endswith("/videos"):
            return FakeResponse(200, {"items": [video_item("v1", "새 영상", 10, 3000), video_item("v2", "옛 영상", 24 * 20, 1000)]})
        raise AssertionError(url)

    install(monkeypatch, handler)

    result = YouTubeDiscovery().recent_uploads(["UC1"], days=7)

    assert [v["videoId"] for v in result["videos"]] == ["v1"]
    assert result["videos"][0]["outperformanceIndex"] == 1.5
    assert result["channels"] == [{"channelId": "UC1", "title": "채널1", "subscribers": 1000}]


def test_fetch_video_returns_description_and_not_found_raises(monkeypatch):
    install(monkeypatch, lambda url, params: FakeResponse(200, {"items": [video_item("abcdefghijk", "제목", 5, 100)]}))
    row = YouTubeDiscovery().fetch_video("abcdefghijk")
    assert row["videoId"] == "abcdefghijk" and row["description"].startswith("설명")

    install(monkeypatch, lambda url, params: FakeResponse(200, {"items": []}))
    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().fetch_video("abcdefghijk")
    assert error.value.code == "NOT_FOUND"


def test_fetch_video_rejects_malformed_id_without_calling_youtube(monkeypatch):
    calls = install(monkeypatch, lambda url, params: FakeResponse(200, {"items": []}))

    with pytest.raises(DiscoveryError) as error:
        YouTubeDiscovery().fetch_video("../etc/passwd")

    assert error.value.code == "NOT_FOUND" and calls == []
```

- [ ] **Step 2: 실패 확인**

Run: FastAPI 테스트 실행 방법 + `tests/test_youtube_discovery.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.discovery'`

- [ ] **Step 3: 구현**

`backend/fastapi-workers/app/services/discovery/__init__.py`는 빈 파일로 만든다. `youtube_discovery.py`:

```python
"""YouTube 급상승 차트·채널 업로드·단일 영상 조회. 쿼터는 trending.py의 공유 카운터를 거친다."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

import requests

from app.providers.real.trending import _CACHE_TTL_SECONDS, _consume_quota, _get_redis_client
from app.utils.hot_keywords import aggregate_hot_keywords

logger = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
_UPLOADS_CACHE_TTL_SECONDS = 1800
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")

# 2026-09-21 한국(KR) 차트 실측으로 확인된 카테고리만 둔다. 교육(27)은 차트를 제공하지 않는다.
CATEGORIES = {
    "ALL": {"id": None, "label": "전체 인기"},
    "FILM": {"id": "1", "label": "영화·애니"},
    "MUSIC": {"id": "10", "label": "음악"},
    "PETS": {"id": "15", "label": "반려동물"},
    "SPORTS": {"id": "17", "label": "스포츠"},
    "GAMING": {"id": "20", "label": "게임"},
    "PEOPLE": {"id": "22", "label": "사람·블로그"},
    "COMEDY": {"id": "23", "label": "코미디"},
    "ENTERTAINMENT": {"id": "24", "label": "엔터테인먼트"},
    "NEWS": {"id": "25", "label": "뉴스·정치"},
    "HOWTO": {"id": "26", "label": "노하우·스타일"},
    "TECH": {"id": "28", "label": "과학기술"},
}


class DiscoveryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _parse_duration(value: str) -> float:
    match = _DURATION.fullmatch(value or "")
    if not match:
        return 0.0
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


def _hours_since(published_at: str) -> float:
    try:
        published = datetime.fromisoformat((published_at or "").replace("Z", "+00:00"))
    except ValueError:
        return 1e6  # 날짜를 못 읽은 영상은 어떤 기간 창에도 들어가지 않게 한다
    return max((datetime.now(timezone.utc) - published).total_seconds() / 3600, 0.0)


def _video_row(item: dict) -> dict:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    published_at = snippet.get("publishedAt", "")
    return {
        "videoId": item.get("id", ""),
        "title": snippet.get("title", ""),
        "channelId": snippet.get("channelId", ""),
        "channelTitle": snippet.get("channelTitle", ""),
        "publishedAt": published_at,
        "hoursSincePublish": round(_hours_since(published_at), 1),
        "views": int(stats.get("viewCount") or 0),
        "likes": int(stats.get("likeCount") or 0),
        "comments": int(stats.get("commentCount") or 0),
        "durationSeconds": _parse_duration(item.get("contentDetails", {}).get("duration", "")),
        "tags": snippet.get("tags") or [],
        "categoryId": snippet.get("categoryId", ""),
        "isLive": snippet.get("liveBroadcastContent", "none") != "none",
    }


class YouTubeDiscovery:
    def __init__(self):
        self._redis = _get_redis_client()

    def _get(self, resource: str, params: dict, operation: str, missing_code: str = "UPSTREAM") -> dict:
        key = os.getenv("YOUTUBE_API_KEY")
        if not key:
            raise DiscoveryError("NO_API_KEY", "YOUTUBE_API_KEY가 설정되지 않아 YouTube 데이터를 수집할 수 없습니다.")
        if not _consume_quota(self._redis, 1, operation):
            raise DiscoveryError("QUOTA", "오늘 YouTube 쿼터를 모두 사용했습니다.")
        response = requests.get(f"{API}/{resource}", params={**params, "key": key}, timeout=15)
        if response.status_code == 200:
            return response.json()
        reason = ""
        try:
            reason = response.json()["error"]["errors"][0]["reason"]
        except Exception:
            pass
        if reason in {"videoChartNotFound", "notFound"}:
            raise DiscoveryError(missing_code, "YouTube가 이 조회를 지원하지 않거나 대상을 찾을 수 없습니다.")
        raise DiscoveryError("UPSTREAM", f"YouTube API 오류({response.status_code})")

    def _cache_get(self, key: str):
        if not self._redis:
            return None
        try:
            cached = self._redis.get(key)
            return json.loads(cached) if cached else None
        except Exception as exc:
            logger.warning("discovery 캐시 조회 실패: %s", exc)
            return None

    def _cache_set(self, key: str, value, ttl: int) -> None:
        if not self._redis:
            return
        try:
            self._redis.setex(key, ttl, json.dumps(value, ensure_ascii=False))
        except Exception as exc:
            logger.warning("discovery 캐시 저장 실패: %s", exc)

    def _attach_subscribers(self, rows: list[dict]) -> None:
        channel_ids = list(dict.fromkeys(row["channelId"] for row in rows if row["channelId"]))[:50]
        subscribers: dict[str, int | None] = {}
        if channel_ids:
            try:
                data = self._get("channels", {"part": "statistics", "id": ",".join(channel_ids), "maxResults": 50}, "channels.list")
                for item in data.get("items", []):
                    stats = item.get("statistics", {})
                    subscribers[item["id"]] = None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount") or 0)
            except DiscoveryError as exc:
                if exc.code in {"NO_API_KEY", "QUOTA"}:
                    logger.warning("구독자 수 조회 생략: %s", exc)
        for row in rows:
            count = subscribers.get(row["channelId"])
            row["subscribers"] = count or 0
            row["subscriberCountAvailable"] = count is not None

    def _chart_rows(self, category_key: str, category_id: str | None) -> list[dict]:
        cache_key = f"discovery:hot:v1:{category_key}"
        rows = self._cache_get(cache_key)
        if rows is None:
            params = {
                "part": "snippet,statistics,contentDetails", "chart": "mostPopular",
                "regionCode": "KR", "maxResults": 50,
            }
            if category_id:
                params["videoCategoryId"] = category_id
            data = self._get("videos", params, "videos.list", missing_code="UNSUPPORTED_CATEGORY")
            rows = [row for row in (_video_row(item) for item in data.get("items", [])) if not row["isLive"]]
            self._attach_subscribers(rows)
            self._cache_set(cache_key, rows, _CACHE_TTL_SECONDS)
        for row in rows:
            row["hoursSincePublish"] = round(_hours_since(row["publishedAt"]), 1)
        return rows

    def hot_keywords(self, category_key: str = "ALL", window: str = "48h") -> dict:
        category = CATEGORIES.get(category_key)
        if category is None:
            raise DiscoveryError("UNSUPPORTED_CATEGORY", f"지원하지 않는 카테고리입니다: {category_key}")
        result = aggregate_hot_keywords(self._chart_rows(category_key, category["id"]), window)
        result["category"] = category_key
        result["categories"] = [{"key": key, "label": value["label"]} for key, value in CATEGORIES.items()]
        return result

    def recent_uploads(self, channel_ids: list[str], days: int = 7) -> dict:
        ids = list(dict.fromkeys(cid.strip() for cid in channel_ids if cid and cid.strip()))[:20]
        days = max(1, min(int(days), 7))
        if not ids:
            return {"videos": [], "channels": []}
        cache_key = "discovery:uploads:v1:" + hashlib.md5(f"{','.join(sorted(ids))}:{days}".encode()).hexdigest()
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        data = self._get("channels", {"part": "snippet,contentDetails,statistics", "id": ",".join(ids), "maxResults": 50}, "channels.list")
        channels, owner_of = [], {}
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            channels.append({"channelId": item["id"], "title": item.get("snippet", {}).get("title", ""),
                             "subscribers": int(stats.get("subscriberCount") or 0)})
            uploads = item.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            if not uploads:
                continue
            try:
                playlist = self._get("playlistItems", {"part": "contentDetails", "playlistId": uploads, "maxResults": 15}, "playlistItems.list")
            except DiscoveryError as exc:
                if exc.code in {"NO_API_KEY", "QUOTA"}:
                    raise
                logger.warning("업로드 목록 조회 실패(channel=%s): %s", item["id"], exc)
                continue
            for entry in playlist.get("items", []):
                owner_of[entry["contentDetails"]["videoId"]] = item["id"]

        video_ids = list(owner_of)
        rows = []
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start:start + 50]
            videos = self._get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch)}, "videos.list")
            rows.extend(row for row in (_video_row(item) for item in videos.get("items", [])) if not row["isLive"])

        views_by_channel: dict[str, list[int]] = {}
        for row in rows:
            views_by_channel.setdefault(row["channelId"], []).append(row["views"])
        result_rows = []
        for row in rows:
            if row["hoursSincePublish"] > days * 24:
                continue
            samples = views_by_channel.get(row["channelId"]) or []
            average = sum(samples) / len(samples) if samples else 0
            row["viewsPerHour"] = round(row["views"] / max(row["hoursSincePublish"], 1.0), 1)
            row["outperformanceIndex"] = round(row["views"] / average, 2) if average else None
            result_rows.append(row)
        result_rows.sort(key=lambda r: r["viewsPerHour"], reverse=True)

        result = {"videos": result_rows, "channels": channels}
        self._cache_set(cache_key, result, _UPLOADS_CACHE_TTL_SECONDS)
        return result

    def fetch_video(self, video_id: str) -> dict:
        if not _VIDEO_ID.match(video_id or ""):
            raise DiscoveryError("NOT_FOUND", "영상 ID 형식이 올바르지 않습니다.")
        data = self._get("videos", {"part": "snippet,statistics,contentDetails", "id": video_id}, "videos.list")
        items = data.get("items", [])
        if not items:
            raise DiscoveryError("NOT_FOUND", "영상을 찾을 수 없습니다. 삭제되었거나 비공개일 수 있습니다.")
        row = _video_row(items[0])
        row["description"] = (items[0].get("snippet", {}).get("description") or "")[:1500]
        return row
```

- [ ] **Step 4: 통과 확인**

Run: Step 2와 같은 명령
Expected: `9 passed`

- [ ] **Step 5: 커밋**

```bash
git add backend/fastapi-workers/app/services/discovery backend/fastapi-workers/tests/test_youtube_discovery.py
git commit -m "feat(discovery): YouTube 급상승 차트·채널 업로드·단일 영상 조회 서비스 추가"
```

---

### Task 3: 벤치마크 분석 모듈 + FastAPI 엔드포인트

**Files:**
- Create: `backend/fastapi-workers/app/services/discovery/benchmark_analysis.py`
- Modify: `backend/fastapi-workers/app/main.py` (`youtube_channel_benchmark` 함수 뒤에 추가)
- Test: `backend/fastapi-workers/tests/test_benchmark_analysis.py`, `backend/fastapi-workers/tests/test_discovery_endpoints.py`

**Interfaces:**
- Consumes: `YouTubeDiscovery`, `DiscoveryError` (Task 2).
- Produces: `analyze_benchmark(llm_call, video: dict) -> dict` — 반환 `{"topic_keyword": str|None, "reasons": list[str](≤3), "hook_type": str|None, "title_pattern": str}`, 사용할 수 없는 응답이면 `ValueError`. `claude_llm_call(system, user, max_tokens=800) -> str`. HTTP: `GET /workers/discovery/hot-keywords?category&window`, `GET /workers/discovery/recent-uploads?channel_ids&days`, `GET /workers/discovery/video/{video_id}`, `POST /workers/benchmark/analyze` body `{"video": {...}}`. 오류 매핑: `NO_API_KEY→503, QUOTA→429, UNSUPPORTED_CATEGORY/NOT_FOUND→404, 그 외→502`, 잘못된 기간은 400.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_benchmark_analysis.py`:

```python
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
```

`tests/test_discovery_endpoints.py`:

```python
from fastapi.testclient import TestClient

from app.main import app
from app.services.discovery import benchmark_analysis as ba
from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

client = TestClient(app)


def test_hot_keywords_success(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)
    monkeypatch.setattr(YouTubeDiscovery, "hot_keywords", lambda self, category, window: {"window": window, "category": category, "keywords": [], "videos": [], "categories": []})

    response = client.get("/workers/discovery/hot-keywords", params={"category": "GAMING", "window": "7d"})

    assert response.status_code == 200 and response.json()["category"] == "GAMING"


def test_hot_keywords_error_mapping(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)

    def raise_quota(self, category, window):
        raise DiscoveryError("QUOTA", "쿼터 소진")

    monkeypatch.setattr(YouTubeDiscovery, "hot_keywords", raise_quota)

    assert client.get("/workers/discovery/hot-keywords").status_code == 429


def test_hot_keywords_invalid_window_is_400(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)
    monkeypatch.setattr(YouTubeDiscovery, "_chart_rows", lambda self, key, category_id: [])

    assert client.get("/workers/discovery/hot-keywords", params={"window": "30d"}).status_code == 400


def test_recent_uploads_splits_channel_ids(monkeypatch):
    seen = {}
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)

    def fake(self, channel_ids, days):
        seen["ids"], seen["days"] = channel_ids, days
        return {"videos": [], "channels": []}

    monkeypatch.setattr(YouTubeDiscovery, "recent_uploads", fake)

    response = client.get("/workers/discovery/recent-uploads", params={"channel_ids": "UC1, UC2", "days": 3})

    assert response.status_code == 200 and seen == {"ids": ["UC1", "UC2"], "days": 3}


def test_video_not_found_is_404(monkeypatch):
    monkeypatch.setattr(YouTubeDiscovery, "__init__", lambda self: None)

    def missing(self, video_id):
        raise DiscoveryError("NOT_FOUND", "없음")

    monkeypatch.setattr(YouTubeDiscovery, "fetch_video", missing)

    assert client.get("/workers/discovery/video/abcdefghijk").status_code == 404


def test_analyze_endpoint_returns_analysis_and_502_on_bad_output(monkeypatch):
    monkeypatch.setattr(ba, "claude_llm_call", lambda system, user, max_tokens=800: '{"topic_keyword":"상어","reasons":["a"],"hook_type":null,"title_pattern":"x"}')
    ok = client.post("/workers/benchmark/analyze", json={"video": {"title": "제목"}})
    assert ok.status_code == 200 and ok.json()["topic_keyword"] == "상어"

    monkeypatch.setattr(ba, "claude_llm_call", lambda system, user, max_tokens=800: "형식 오류")
    bad = client.post("/workers/benchmark/analyze", json={"video": {"title": "제목"}})
    assert bad.status_code == 502
```

- [ ] **Step 2: 실패 확인**

Run: FastAPI 테스트 실행 방법 + `tests/test_benchmark_analysis.py tests/test_discovery_endpoints.py`
Expected: FAIL — 모듈/엔드포인트 없음

- [ ] **Step 3: 분석 모듈 구현**

```python
"""벤치마크 영상의 공개 정보로 "뜨는 이유"를 Claude 1회 호출로 분석한다."""
from __future__ import annotations

import json
import os
import re
from typing import Callable

HOOK_TYPES = {"number_context", "belief_reversal", "time_contrast", "hidden_context", "direct_question", "human_stake"}

SYSTEM = """당신은 한국 유튜브 콘텐츠 분석가입니다.
주어진 영상의 공개 정보(제목·태그·설명·통계)만으로 이 영상이 왜 반응을 얻는지 분석하세요.
제공되지 않은 사실과 수치는 만들지 마세요. 다른 창작자의 문장을 그대로 인용하지 말고 유형과 구조만 요약하세요.
JSON 객체만 반환하세요."""


def claude_llm_call(system: str, user: str, max_tokens: int = 800) -> str:
    from anthropic import Anthropic
    from app.config import CLAUDE_MODEL

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def analyze_benchmark(llm_call: Callable[..., str], video: dict) -> dict:
    facts = {key: video.get(key) for key in ("title", "channelTitle", "tags", "description", "views", "viewsPerHour", "outperformanceIndex", "durationSeconds")}
    prompt = f"""영상 공개 정보: {json.dumps(facts, ensure_ascii=False)}

아래 스키마로 분석하세요.
{{
  "topic_keyword": "이 영상의 핵심 주제를 명사구로, 40자 이내 (클릭베이트 어투·느낌표 제거)",
  "reasons": ["이 영상이 반응을 얻는 이유 2~3개, 각 60자 이내"],
  "hook_type": "number_context|belief_reversal|time_contrast|hidden_context|direct_question|human_stake 중 하나",
  "title_pattern": "제목의 구조를 한 줄로 (예: 결과를 먼저 던지고 과정을 궁금하게 만듦)"
}}"""
    raw = llm_call(SYSTEM, prompt, 800)
    match = re.search(r"\{[\s\S]*\}", raw or "")
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ValueError("분석 응답을 JSON으로 읽을 수 없습니다.")
    topic = str(data.get("topic_keyword") or "").strip()[:40]
    reasons = [str(item).strip()[:60] for item in (data.get("reasons") or []) if str(item).strip()][:3]
    hook_type = data.get("hook_type") if data.get("hook_type") in HOOK_TYPES else None
    return {
        "topic_keyword": topic or None,
        "reasons": reasons,
        "hook_type": hook_type,
        "title_pattern": str(data.get("title_pattern") or "").strip()[:120],
    }
```

- [ ] **Step 4: main.py에 엔드포인트 추가**

`youtube_channel_benchmark` 함수 바로 뒤에 붙인다.

```python
def _discovery_http_error(exc) -> HTTPException:
    status = {"NO_API_KEY": 503, "QUOTA": 429, "UNSUPPORTED_CATEGORY": 404, "NOT_FOUND": 404}.get(exc.code, 502)
    return HTTPException(status_code=status, detail=str(exc))


@app.get("/workers/discovery/hot-keywords")
def discovery_hot_keywords(category: str = "ALL", window: str = "48h"):
    """카테고리별 급상승 영상에서 핫키워드를 집계한다(48h/7d)."""
    from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

    try:
        return YouTubeDiscovery().hot_keywords(category, window)
    except DiscoveryError as exc:
        raise _discovery_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/workers/discovery/recent-uploads")
def discovery_recent_uploads(channel_ids: str, days: int = 7):
    """벤치마크 채널의 최근 업로드를 시간당 조회수·채널 평균 대비 배수와 함께 반환한다."""
    from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

    ids = [item.strip() for item in channel_ids.split(",") if item.strip()]
    try:
        return YouTubeDiscovery().recent_uploads(ids, days)
    except DiscoveryError as exc:
        raise _discovery_http_error(exc) from exc


@app.get("/workers/discovery/video/{video_id}")
def discovery_video(video_id: str):
    from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

    try:
        return YouTubeDiscovery().fetch_video(video_id)
    except DiscoveryError as exc:
        raise _discovery_http_error(exc) from exc


class BenchmarkAnalyzeRequest(BaseModel):
    video: dict


@app.post("/workers/benchmark/analyze")
def benchmark_analyze(request: BenchmarkAnalyzeRequest):
    from app.services.discovery import benchmark_analysis

    try:
        return benchmark_analysis.analyze_benchmark(benchmark_analysis.claude_llm_call, request.video)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"벤치마크 분석 실패: {exc}") from exc
```

- [ ] **Step 5: 통과 확인**

Run: Step 2와 같은 명령
Expected: `10 passed`

- [ ] **Step 6: 커밋**

```bash
git add backend/fastapi-workers/app/services/discovery/benchmark_analysis.py backend/fastapi-workers/app/main.py backend/fastapi-workers/tests/test_benchmark_analysis.py backend/fastapi-workers/tests/test_discovery_endpoints.py
git commit -m "feat(discovery): 벤치마크 분석 모듈과 discovery 엔드포인트 추가"
```

---

### Task 4: 대본 워커·내러티브 플래너에 벤치마크 참고 자료 전달

**Files:**
- Modify: `backend/fastapi-workers/app/workers/script_worker.py` (`_candidate_evidence_context`, `generate`의 플래너·본문 호출부, `_generate_with_verified_facts` 시그니처·프롬프트)
- Modify: `backend/fastapi-workers/app/utils/narrative_planner.py` (system 프롬프트)
- Test: `backend/fastapi-workers/tests/test_benchmark_script_context.py`

**Interfaces:**
- Consumes: `candidate_evidence["benchmark_analysis"]` (dict|None; Task 3의 반환 형태).
- Produces: `_candidate_evidence_context(...)`이 `"benchmark_analysis"` 키를 반환. `_generate_with_verified_facts(..., source_videos=None, benchmark_analysis=None)`. 플래너 `candidate_context`에 `"benchmark_points"` 추가.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""벤치마크 참고 자료가 대본 프롬프트·플래너에 '참고용'으로만 전달되는지 고정한다."""
from __future__ import annotations

import json

from app.utils.narrative_planner import plan_narrative
from app.workers.script_worker import ScriptWorker, _candidate_evidence_context

ANALYSIS = {"topic_keyword": "부산상어 재유행", "reasons": ["익숙한 소재의 재유행"], "hook_type": "direct_question", "title_pattern": "결과 먼저"}


def _script(scene_count):
    blocks = []
    for index in range(1, scene_count + 1):
        blocks.append(
            f"## 장면 {index:03d}\n[대사]\n부산상어 재유행 흐름과 배경을 아주 자세히 설명합니다 {index:02d}\n"
            "[비주얼 설명 (한국어)]\n캐릭터\n[비주얼 프롬프트 (영어)]\nscene\n[감정]\nneutral"
        )
    meta = "\n\n## 메타데이터\n[추천 제목]\n제목\n[추천 썸네일]\nbg\n[더보기 설명]\n설명\n[쇼츠 대본]\n요약"
    return "\n\n".join(blocks) + meta


def _capture_prompt(monkeypatch, benchmark_analysis):
    worker = ScriptWorker()
    worker._llm_provider_log = []
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured.setdefault("user", messages[-1]["content"])
        return _script(20)

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_call)
    worker._generate_with_verified_facts(
        keyword="부산상어 재유행", category_label="CUSTOM", target_minutes=1, target_chars=360,
        verified_facts=[{"fact": "사실", "figure": "1", "source_field": "테스트", "confidence": 0.9}],
        market_data={}, selected_terms=["부산상어 재유행"], keyword_news=[],
        length_contract={"target_seconds": 60}, narrative_plan={"plan_id": "t", "story_beats": []},
        benchmark_analysis=benchmark_analysis,
    )
    return captured["user"]


def test_prompt_includes_benchmark_points_and_no_copy_rule(monkeypatch):
    prompt = _capture_prompt(monkeypatch, ANALYSIS)

    assert "<benchmark_points>" in prompt and "익숙한 소재의 재유행" in prompt
    assert "그대로 또는 거의 그대로 쓰지 마세요" in prompt
    assert "사실 근거가 아닙니다" in prompt


def test_prompt_omits_benchmark_block_when_absent(monkeypatch):
    assert "<benchmark_points>" not in _capture_prompt(monkeypatch, None)


def test_candidate_context_passes_benchmark_analysis_through():
    assert _candidate_evidence_context([], {"benchmark_analysis": ANALYSIS})["benchmark_analysis"] == ANALYSIS
    assert _candidate_evidence_context([], None)["benchmark_analysis"] is None
    assert _candidate_evidence_context([], {"source_videos": []})["benchmark_analysis"] is None


def test_planner_allows_hook_pattern_reference_but_forbids_sentence_copy():
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured["system"] = system
        return json.dumps({"plan_id": "p", "hook_type": "human_stake", "story_beats": [
            {"role": "훅", "fact_ids": ["F1"], "transition_goal": "x"},
            {"role": "전개", "fact_ids": ["F1"], "transition_goal": "y"},
            {"role": "결말", "fact_ids": ["F1"], "transition_goal": "z"}]})

    plan_narrative(fake_call, selected_terms=["상어"], verified_facts=[{"fact": "사실", "figure": ""}],
                   candidate_context={"benchmark_points": ANALYSIS}, format_name="longform")

    assert "그대로 복제하지 마세요" in captured["system"]
    assert "유형·구조·리듬은 참고할 수 있습니다" in captured["system"]
    assert "benchmark_points" in captured["system"]
```

- [ ] **Step 2: 실패 확인**

Run: FastAPI 테스트 실행 방법 + `tests/test_benchmark_script_context.py`
Expected: FAIL — `_generate_with_verified_facts() got an unexpected keyword argument 'benchmark_analysis'`

- [ ] **Step 3: `_candidate_evidence_context` 수정** (`script_worker.py`)

두 개의 `return {...}` 각각에 키를 추가한다. 앞쪽(`not isinstance(candidate_evidence, dict)` 분기)의 반환 딕셔너리 끝에 `"benchmark_analysis": None,`을, 함수 마지막 반환 딕셔너리 끝에 아래를 추가한다.

```python
        "benchmark_analysis": (
            candidate_evidence.get("benchmark_analysis")
            if isinstance(candidate_evidence.get("benchmark_analysis"), dict) else None
        ),
```

- [ ] **Step 4: `generate`의 호출부 수정** (`script_worker.py`, 약 1445~1495행)

`source_videos = candidate_context["source_videos"]` 다음 줄에 추가:

```python
            benchmark_analysis = candidate_context["benchmark_analysis"]
```

`plan_narrative(...)`의 `candidate_context={...}` 딕셔너리에서 `"news_titles"` 항목 뒤에 추가:

```python
                        **({"benchmark_points": benchmark_analysis} if benchmark_analysis else {}),
```

`self._generate_with_verified_facts(...)` 호출의 마지막 인자 `source_videos,` 뒤에 추가:

```python
                benchmark_analysis=benchmark_analysis,
```

- [ ] **Step 5: `_generate_with_verified_facts` 시그니처와 프롬프트 수정**

시그니처 끝 `source_videos: Optional[list[dict]] = None):`를 다음으로 바꾼다.

```python
                                       source_videos: Optional[list[dict]] = None,
                                       benchmark_analysis: Optional[dict] = None):
```

`user_prompt = f"""...` 바로 위(`evidence_text = ...` 다음)에 추가:

```python
        benchmark_block = ""
        benchmark_rule = ""
        if benchmark_analysis:
            benchmark_block = f"<benchmark_points>{json.dumps(benchmark_analysis, ensure_ascii=False)}</benchmark_points>\n"
            benchmark_rule = (
                "- <benchmark_points>는 잘 되는 영상이 '왜 뜨는지'와 훅 유형을 참고하기 위한 자료입니다. "
                "훅의 유형·구조·리듬은 참고할 수 있지만 다른 영상의 문장을 그대로 또는 거의 그대로 쓰지 마세요. "
                "영상 제목·조회수는 사실 근거가 아닙니다.\n"
            )
```

프롬프트의 `<narrative_plan>...</narrative_plan>` 줄과 `작성 규칙:` 줄을 다음처럼 바꾼다.

```
<narrative_plan>{json.dumps(narrative_plan, ensure_ascii=False)}</narrative_plan>
{benchmark_block}작성 규칙:
{benchmark_rule}- [대사], [비주얼 설명 (한국어)], [비주얼 프롬프트 (영어)], [감정] 포함
```

(원래 `작성 규칙:` 다음 줄의 `- [대사], [비주얼 설명 ...` 항목은 `{benchmark_rule}` 뒤에 그대로 이어진다.)

- [ ] **Step 6: 플래너 프롬프트 수정** (`narrative_planner.py`)

`system = """...` 안의 아래 한 줄을

```
특정 창작자나 채널의 문장·훅을 모사하지 마세요. 제공 사실 밖의 수치, 인과, 예측을 만들지 마세요.
```

다음 두 줄로 바꾼다.

```
다른 창작자의 문장을 그대로 복제하지 마세요. 훅의 유형·구조·리듬은 참고할 수 있습니다. 제공 사실 밖의 수치, 인과, 예측을 만들지 마세요.
후보 맥락에 benchmark_points가 있으면 그 영상이 뜨는 이유와 훅 유형을 hook_type 선택의 참고로만 쓰고, 그 안의 표현을 대본에 옮기지 마세요.
```

- [ ] **Step 7: 통과 확인 + 기존 관련 테스트 회귀**

Run: FastAPI 테스트 실행 방법 + `tests/test_benchmark_script_context.py tests/test_narrative_planner_and_flow_qa.py tests/test_script_prompt_market_and_hook_rules.py tests/test_dialogue_length_safety_net.py`
Expected: 전부 passed (기존 테스트가 "모사하지 마세요" 문구를 검사하면 이 단계에서 실패하므로, 그 테스트를 새 문구에 맞게 수정한다)

- [ ] **Step 8: 커밋**

```bash
git add backend/fastapi-workers/app/workers/script_worker.py backend/fastapi-workers/app/utils/narrative_planner.py backend/fastapi-workers/tests/test_benchmark_script_context.py
git commit -m "feat(script): 벤치마크 분석을 참고 자료로 전달하고 훅 유형·구조 참고를 허용"
```

---

### Task 5: 레퍼런스 채널의 소유 채널(ownerChannelId) — Spring 데이터 모델·조회

**Files:**
- Modify: `backend/spring-app/src/main/java/com/pipeline/video/domain/ReferenceChannel.java`
- Modify: `backend/spring-app/src/main/java/com/pipeline/video/repository/ReferenceChannelRepository.java`
- Modify: `backend/spring-app/src/main/java/com/pipeline/video/dto/ReferenceChannelCreateRequest.java`, `ReferenceChannelUpdateRequest.java`
- Modify: `backend/spring-app/src/main/java/com/pipeline/video/service/ReferenceChannelService.java`
- Test: `backend/spring-app/src/test/java/com/pipeline/video/service/ReferenceChannelServiceTest.java`

**Interfaces:**
- Produces: `ReferenceChannel.getOwnerChannelId()/setOwnerChannelId(String)`(null = 공용); `ReferenceChannelRepository.findActiveVisibleToOwner(String ownerChannelId)`; `ReferenceChannelService.listForOwner(String ownerChannelId) -> List<ReferenceChannel>`(비어 있으면 공용만); `ReferenceChannelCreateRequest(displayName, channelRef, tier, displayOrder, ownerChannelId)`; `ReferenceChannelUpdateRequest(displayName, tier, displayOrder, active, ownerChannelId)` — 업데이트에서 `ownerChannelId == null`은 "변경 없음", `""`는 "공용으로 되돌림".

- [ ] **Step 1: 기존 테스트의 생성자 호출을 새 시그니처로 수정하고 새 테스트 추가**

`ReferenceChannelServiceTest.java`에서 다음 호출을 바꾼다.

- `new ReferenceChannelCreateRequest("검증 채널", "@verified", null, 7)` → `new ReferenceChannelCreateRequest("검증 채널", "@verified", null, 7, null)`
- `new ReferenceChannelCreateRequest("없는 채널", "UCmissing", null, null)` → `..., null, null, null)`
- `new ReferenceChannelCreateRequest("중복 채널", "@duplicate", null, null)` → `..., null, null, null)`
- `new ReferenceChannelUpdateRequest("새 이름", ReferenceChannelTier.SMALL, 30, false)` → `new ReferenceChannelUpdateRequest("새 이름", ReferenceChannelTier.SMALL, 30, false, null)`

클래스 끝(마지막 `}` 앞)에 테스트를 추가한다. (`candidate(...)` 헬퍼는 기존 테스트 파일에 이미 있다.)

```java
    @Test
    void createStoresOwnerChannelWhenProvided() {
        when(fastApiClient.resolveChannel("@owned")).thenReturn(Optional.of(candidate("UCowned", 100_000L)));
        when(repository.existsByChannelId("UCowned")).thenReturn(false);

        ReferenceChannel saved = service.create(
                new ReferenceChannelCreateRequest("소유 채널", "@owned", null, 1, "channel_a"), "admin");

        assertThat(saved.getOwnerChannelId()).isEqualTo("channel_a");
    }

    @Test
    void updateKeepsOwnerWhenRequestOwnerIsNullAndClearsWhenEmpty() {
        ReferenceChannel existing = ReferenceChannel.builder()
                .displayName("이름").channelId("UCx").ownerChannelId("channel_a").active(true).build();
        when(repository.findById(1L)).thenReturn(Optional.of(existing));

        service.update(1L, new ReferenceChannelUpdateRequest("이름", null, null, null, null));
        assertThat(existing.getOwnerChannelId()).isEqualTo("channel_a");

        service.update(1L, new ReferenceChannelUpdateRequest("이름", null, null, null, "channel_b"));
        assertThat(existing.getOwnerChannelId()).isEqualTo("channel_b");

        service.update(1L, new ReferenceChannelUpdateRequest("이름", null, null, null, ""));
        assertThat(existing.getOwnerChannelId()).isNull();
    }

    @Test
    void listForOwnerReturnsOwnedAndSharedChannels() {
        ReferenceChannel shared = ReferenceChannel.builder().displayName("공용").channelId("UCs").active(true).build();
        ReferenceChannel owned = ReferenceChannel.builder().displayName("A전용").channelId("UCa").ownerChannelId("channel_a").active(true).build();
        when(repository.findActiveVisibleToOwner("channel_a")).thenReturn(List.of(shared, owned));

        assertThat(service.listForOwner("channel_a")).containsExactly(shared, owned);
    }

    @Test
    void listForOwnerWithoutOwnerReturnsOnlySharedChannels() {
        ReferenceChannel shared = ReferenceChannel.builder().displayName("공용").channelId("UCs").active(true).build();
        ReferenceChannel owned = ReferenceChannel.builder().displayName("A전용").channelId("UCa").ownerChannelId("channel_a").active(true).build();
        when(repository.findByActiveTrueOrderByDisplayOrderAscIdAsc()).thenReturn(List.of(shared, owned));

        assertThat(service.listForOwner(" ")).containsExactly(shared);
    }
```

- [ ] **Step 2: 실패 확인**

Run: Spring 테스트 실행 방법 + `com.pipeline.video.service.ReferenceChannelServiceTest`
Expected: FAIL — 컴파일 오류(`ownerChannelId` 없음)

- [ ] **Step 3: 구현**

`ReferenceChannel.java` — 다른 `@Column` 필드들 사이(예: `displayOrder` 필드 뒤)에 추가:

```java
    /** null이면 모든 제작 채널이 함께 쓰는 공용 벤치마크 채널이다. */
    @Column(name = "owner_channel_id", length = 50)
    private String ownerChannelId;
```

`ReferenceChannelRepository.java` — import 두 줄과 메서드 추가:

```java
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
```

```java
    @Query("select r from ReferenceChannel r where r.active = true "
            + "and (r.ownerChannelId is null or r.ownerChannelId = :ownerChannelId) "
            + "order by r.displayOrder asc, r.id asc")
    List<ReferenceChannel> findActiveVisibleToOwner(@Param("ownerChannelId") String ownerChannelId);
```

`ReferenceChannelCreateRequest.java`에서 마지막 컴포넌트 `Integer displayOrder`를 `Integer displayOrder,\n        String ownerChannelId`로 바꾼다. `ReferenceChannelUpdateRequest.java`도 마지막 컴포넌트 `Boolean active`를 `Boolean active,\n        String ownerChannelId`로 바꾼다.

`ReferenceChannelService.java`:
- `create`의 `saveVerified(...)` 호출 인자 끝에 `request.ownerChannelId()`를 추가한다.
- `confirm`의 `saveVerified(...)` 호출 인자 끝에 `null`을 추가한다.
- `saveVerified` 시그니처 끝에 `String ownerChannelId`를 추가하고 빌더에 `.ownerChannelId(normalizeOwner(ownerChannelId))`를 추가한다.
- `update`에서 `channel.setDisplayName(...)` 아래에 추가:

```java
        if (request.ownerChannelId() != null) {
            channel.setOwnerChannelId(normalizeOwner(request.ownerChannelId()));
        }
```

- 메서드 두 개 추가:

```java
    @Transactional(readOnly = true)
    public List<ReferenceChannel> listForOwner(String ownerChannelId) {
        String owner = normalizeOwner(ownerChannelId);
        if (owner == null) {
            return repository.findByActiveTrueOrderByDisplayOrderAscIdAsc().stream()
                    .filter(channel -> channel.getOwnerChannelId() == null)
                    .toList();
        }
        return repository.findActiveVisibleToOwner(owner);
    }

    private static String normalizeOwner(String ownerChannelId) {
        return ownerChannelId == null || ownerChannelId.isBlank() ? null : ownerChannelId.trim();
    }
```

- [ ] **Step 4: 통과 확인**

Run: Step 2와 같은 명령
Expected: `BUILD SUCCESSFUL`, 모든 `ReferenceChannelServiceTest` 통과

- [ ] **Step 5: 커밋**

```bash
git add backend/spring-app/src
git commit -m "feat(reference-channel): 제작 채널별 벤치마크 채널(ownerChannelId) 지원"
```

---

### Task 6: Spring 벤치마크 서비스·컨트롤러·FastApiClient

**Files:**
- Modify: `backend/spring-app/src/main/java/com/pipeline/video/service/FastApiClient.java`
- Create: `backend/spring-app/src/main/java/com/pipeline/video/dto/BenchmarkJobRequest.java`
- Create: `backend/spring-app/src/main/java/com/pipeline/video/service/BenchmarkService.java`
- Create: `backend/spring-app/src/main/java/com/pipeline/video/controller/BenchmarkController.java`
- Modify: `backend/spring-app/src/main/java/com/pipeline/video/service/ScriptService.java` (`extractCandidateEvidence`)
- Test: `backend/spring-app/src/test/java/com/pipeline/video/service/BenchmarkServiceTest.java`

**Interfaces:**
- Consumes: `ReferenceChannelService.listForOwner` (Task 5), `JobService.createJob(CreateJobRequest, String) -> JobResponse`, `KeywordService.confirm(Long jobId, String keyword, String username)`, FastAPI 엔드포인트(Task 3).
- Produces: `FastApiClient.getHotKeywords(String category, String window)`, `getRecentUploads(List<String> channelIds, int days)`, `getYoutubeVideo(String videoId)`, `analyzeBenchmark(Map<String,Object> video)` (모두 `Map<String,Object>`; discovery 호출 실패 시 `ResponseStatusException`). `BenchmarkJobRequest(String videoId, CreateJobRequest job)`. `BenchmarkService.recentUploads(String ownerChannelId, int days)`, `createFromBenchmark(BenchmarkJobRequest, String username) -> JobResponse`, 정적 `cleanTitle(String)`. HTTP: `GET /api/trending/hot-keywords`, `GET /api/reference-channels/recent-uploads`, `POST /api/jobs/from-benchmark`.

- [ ] **Step 1: 실패하는 테스트 작성** (`BenchmarkServiceTest.java`)

```java
package com.pipeline.video.service;

import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.ReferenceChannel;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.BenchmarkJobRequest;
import com.pipeline.video.dto.CreateJobRequest;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpStatus;
import org.springframework.transaction.support.TransactionCallback;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BenchmarkServiceTest {

    @Mock FastApiClient fastApiClient;
    @Mock ReferenceChannelService referenceChannelService;
    @Mock JobService jobService;
    @Mock KeywordService keywordService;
    @Mock VideoJobRepository jobRepository;
    @Mock AssetRepository assetRepository;
    @Mock TransactionTemplate transactionTemplate;

    private BenchmarkService service;

    @BeforeEach
    @SuppressWarnings("unchecked")
    void setUp() {
        service = new BenchmarkService(fastApiClient, referenceChannelService, jobService,
                keywordService, jobRepository, assetRepository, transactionTemplate);
        org.mockito.Mockito.lenient().when(transactionTemplate.execute(any()))
                .thenAnswer(invocation -> ((TransactionCallback<Object>) invocation.getArgument(0)).doInTransaction(null));
    }

    private static Map<String, Object> video() {
        return Map.of("videoId", "abcdefghijk", "title", "오늘부터 애프터마켓 오픈!! ‘이렇게’ 바뀐다", "views", 1000);
    }

    private BenchmarkJobRequest request() {
        CreateJobRequest job = new CreateJobRequest();
        job.setChannelId("channel_a");
        return new BenchmarkJobRequest("abcdefghijk", job);
    }

    private JobResponse createdJob() {
        JobResponse response = new JobResponse();
        response.setId(5L);
        VideoJob entity = VideoJob.builder().id(5L).status(JobStatus.DRAFT).build();
        when(jobRepository.findById(5L)).thenReturn(Optional.of(entity));
        return response;
    }

    @Test
    void recentUploadsWithoutChannelsSkipsYoutubeCall() {
        when(referenceChannelService.listForOwner("channel_a")).thenReturn(List.of());

        Map<String, Object> result = service.recentUploads("channel_a", 7);

        assertThat((List<?>) result.get("videos")).isEmpty();
        assertThat(result.get("empty")).isEqualTo(true);
        verifyNoInteractions(fastApiClient);
    }

    @Test
    void recentUploadsPassesChannelIdsToFastApi() {
        ReferenceChannel channel = ReferenceChannel.builder().channelId("UC1").displayName("x").build();
        when(referenceChannelService.listForOwner("channel_a")).thenReturn(List.of(channel));
        when(fastApiClient.getRecentUploads(List.of("UC1"), 7)).thenReturn(Map.of("videos", List.of(), "channels", List.of()));

        service.recentUploads("channel_a", 7);

        verify(fastApiClient).getRecentUploads(List.of("UC1"), 7);
    }

    @Test
    void createFromBenchmarkStoresEvidenceAndConfirmsKeyword() {
        when(fastApiClient.getYoutubeVideo("abcdefghijk")).thenReturn(video());
        when(fastApiClient.analyzeBenchmark(any())).thenReturn(Map.of("topic_keyword", "애프터마켓 오픈", "reasons", List.of("이유")));
        JobResponse created = createdJob();
        when(jobService.createJob(any(CreateJobRequest.class), eq("user"))).thenReturn(created);

        JobResponse result = service.createFromBenchmark(request(), "user");

        assertThat(result.getId()).isEqualTo(5L);
        ArgumentCaptor<CreateJobRequest> jobCaptor = ArgumentCaptor.forClass(CreateJobRequest.class);
        verify(jobService).createJob(jobCaptor.capture(), eq("user"));
        assertThat(jobCaptor.getValue().getKeyword()).isEqualTo("애프터마켓 오픈");
        assertThat(jobCaptor.getValue().getTitle()).isEqualTo("애프터마켓 오픈");
        assertThat(jobCaptor.getValue().getChannelId()).isEqualTo("channel_a");

        ArgumentCaptor<Asset> assetCaptor = ArgumentCaptor.forClass(Asset.class);
        verify(assetRepository).save(assetCaptor.capture());
        assertThat(assetCaptor.getValue().getAssetType()).isEqualTo(AssetType.KEYWORD);
        assertThat(assetCaptor.getValue().getMetaJson())
                .contains("benchmark_analysis").contains("BENCHMARK").contains("source_videos").contains("abcdefghijk");
        verify(keywordService).confirm(5L, "애프터마켓 오픈", "user");
    }

    @Test
    void analysisFailureDoesNotBlockCreationAndFallsBackToCleanedTitle() {
        when(fastApiClient.getYoutubeVideo("abcdefghijk")).thenReturn(video());
        when(fastApiClient.analyzeBenchmark(any())).thenThrow(new IllegalStateException("크레딧 부족"));
        JobResponse created = createdJob();
        when(jobService.createJob(any(CreateJobRequest.class), eq("user"))).thenReturn(created);

        service.createFromBenchmark(request(), "user");

        ArgumentCaptor<CreateJobRequest> jobCaptor = ArgumentCaptor.forClass(CreateJobRequest.class);
        verify(jobService).createJob(jobCaptor.capture(), eq("user"));
        assertThat(jobCaptor.getValue().getKeyword()).doesNotContain("!").doesNotContain("‘");
        ArgumentCaptor<Asset> assetCaptor = ArgumentCaptor.forClass(Asset.class);
        verify(assetRepository).save(assetCaptor.capture());
        assertThat(assetCaptor.getValue().getMetaJson()).contains("\"benchmark_analysis\":null");
        verify(keywordService).confirm(eq(5L), any(), eq("user"));
    }

    @Test
    void missingVideoBecomesConflictAndCreatesNothing() {
        when(fastApiClient.getYoutubeVideo("abcdefghijk"))
                .thenThrow(new ResponseStatusException(HttpStatus.NOT_FOUND, "없음"));

        assertThatThrownBy(() -> service.createFromBenchmark(request(), "user"))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.CONFLICT));
        verifyNoInteractions(jobService);
        verify(keywordService, never()).confirm(any(), any(), any());
    }

    @Test
    void cleanTitleRemovesDecorationAndLimitsLength() {
        String cleaned = BenchmarkService.cleanTitle("오늘부터 8시까지 애프터마켓 오픈!! 주식패턴 ‘이렇게’ 완전히 바뀐다 " + "가".repeat(60));

        assertThat(cleaned).doesNotContain("!").doesNotContain("‘").doesNotContain("’");
        assertThat(cleaned.length()).isLessThanOrEqualTo(40);
    }
}
```

- [ ] **Step 2: 실패 확인**

Run: Spring 테스트 실행 방법 + `com.pipeline.video.service.BenchmarkServiceTest`
Expected: FAIL — 컴파일 오류(`BenchmarkService`, `BenchmarkJobRequest` 없음)

- [ ] **Step 3: DTO 작성** (`BenchmarkJobRequest.java`)

```java
package com.pipeline.video.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;

public record BenchmarkJobRequest(
        @NotBlank(message = "벤치마크 영상 ID는 필수입니다.") String videoId,
        @NotNull @Valid CreateJobRequest job
) {
}
```

- [ ] **Step 4: FastApiClient 메서드 추가** (`FastApiClient.java`, `getChannelBenchmarks` 메서드 바로 뒤)

파일 상단 import에 없으면 추가: `org.springframework.http.HttpStatus`, `org.springframework.web.client.HttpStatusCodeException`, `org.springframework.web.server.ResponseStatusException`.

```java
    public Map<String, Object> getHotKeywords(String category, String window) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/discovery/hot-keywords")
                .queryParam("category", category)
                .queryParam("window", window)
                .encode().toUriString();
        return getDiscovery(url);
    }

    public Map<String, Object> getRecentUploads(List<String> channelIds, int days) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/discovery/recent-uploads")
                .queryParam("channel_ids", String.join(",", channelIds))
                .queryParam("days", days)
                .encode().toUriString();
        return getDiscovery(url);
    }

    public Map<String, Object> getYoutubeVideo(String videoId) {
        String url = UriComponentsBuilder
                .fromHttpUrl(fastApiUrl + "/workers/discovery/video/{videoId}")
                .buildAndExpand(videoId)
                .encode().toUriString();
        return getDiscovery(url);
    }

    public Map<String, Object> analyzeBenchmark(Map<String, Object> video) {
        try {
            return readMap(postJson(fastApiUrl + "/workers/benchmark/analyze", Map.of("video", video)));
        } catch (Exception e) {
            throw new IllegalStateException("벤치마크 분석 실패: " + e.getMessage(), e);
        }
    }

    private Map<String, Object> getDiscovery(String url) {
        try {
            return readMap(restTemplate.getForObject(url, String.class));
        } catch (HttpStatusCodeException e) {
            String message = "YouTube 발견 서비스 오류";
            try {
                Object detail = readMap(e.getResponseBodyAsString()).get("detail");
                if (detail != null) {
                    message = String.valueOf(detail);
                }
            } catch (Exception ignored) {
                // FastAPI 오류 본문이 JSON이 아니면 기본 메시지를 쓴다.
            }
            throw new ResponseStatusException(e.getStatusCode(), message);
        } catch (Exception e) {
            log.error("발견 서비스 호출 오류: {}", e.getMessage());
            throw new ResponseStatusException(HttpStatus.BAD_GATEWAY, "YouTube 발견 서비스 연결 실패");
        }
    }
```

- [ ] **Step 5: BenchmarkService 작성**

```java
package com.pipeline.video.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.pipeline.video.domain.Asset;
import com.pipeline.video.domain.AssetType;
import com.pipeline.video.domain.JobStatus;
import com.pipeline.video.domain.ReferenceChannel;
import com.pipeline.video.domain.VideoJob;
import com.pipeline.video.dto.BenchmarkJobRequest;
import com.pipeline.video.dto.CreateJobRequest;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.repository.AssetRepository;
import com.pipeline.video.repository.VideoJobRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
@Slf4j
@RequiredArgsConstructor
public class BenchmarkService {

    private static final int MAX_KEYWORD_LENGTH = 40;

    private final FastApiClient fastApiClient;
    private final ReferenceChannelService referenceChannelService;
    private final JobService jobService;
    private final KeywordService keywordService;
    private final VideoJobRepository jobRepository;
    private final AssetRepository assetRepository;
    private final TransactionTemplate transactionTemplate;
    private final ObjectMapper objectMapper = new ObjectMapper();

    public Map<String, Object> recentUploads(String ownerChannelId, int days) {
        List<String> channelIds = referenceChannelService.listForOwner(ownerChannelId).stream()
                .map(ReferenceChannel::getChannelId)
                .toList();
        if (channelIds.isEmpty()) {
            Map<String, Object> empty = new LinkedHashMap<>();
            empty.put("videos", List.of());
            empty.put("channels", List.of());
            empty.put("empty", true);
            return empty;
        }
        return fastApiClient.getRecentUploads(channelIds, days);
    }

    public JobResponse createFromBenchmark(BenchmarkJobRequest request, String username) {
        Map<String, Object> video;
        try {
            video = fastApiClient.getYoutubeVideo(request.videoId());
        } catch (ResponseStatusException e) {
            if (e.getStatusCode().value() == HttpStatus.NOT_FOUND.value()) {
                throw new ResponseStatusException(HttpStatus.CONFLICT,
                        "선택한 영상을 찾을 수 없습니다. 삭제되었거나 비공개일 수 있습니다.");
            }
            throw e;
        }

        Map<String, Object> analysis = null;
        try {
            analysis = fastApiClient.analyzeBenchmark(video);
        } catch (Exception e) {
            log.warn("벤치마크 분석 실패, 분석 없이 진행: {}", e.getMessage());
        }

        String keyword = pickKeyword(analysis, String.valueOf(video.getOrDefault("title", "")));
        CreateJobRequest job = request.job();
        job.setTitle(keyword);
        job.setKeyword(keyword);
        Map<String, Object> finalAnalysis = analysis;
        return transactionTemplate.execute(status -> persist(job, video, finalAnalysis, keyword, username));
    }

    private JobResponse persist(CreateJobRequest jobRequest, Map<String, Object> video,
                                Map<String, Object> analysis, String keyword, String username) {
        JobResponse created = jobService.createJob(jobRequest, username);
        VideoJob job = jobRepository.findById(created.getId())
                .orElseThrow(() -> new IllegalStateException("작업 생성 직후 조회 실패: " + created.getId()));
        job.setStatus(JobStatus.KEYWORD_PENDING);
        jobRepository.save(job);

        Map<String, Object> candidate = new LinkedHashMap<>();
        candidate.put("keyword", keyword);
        candidate.put("reason", "벤치마크 영상 기반 제작");
        candidate.put("content_angle", analysis == null ? null : analysis.get("title_pattern"));
        candidate.put("source", "youtube");
        candidate.put("source_videos", List.of(video));
        candidate.put("evidence_video_ids", List.of(String.valueOf(video.get("videoId"))));
        candidate.put("benchmark_analysis", analysis);

        Map<String, Object> meta = new LinkedHashMap<>();
        meta.put("job_id", created.getId());
        meta.put("seed", String.valueOf(video.getOrDefault("title", "")));
        meta.put("selection_path", "BENCHMARK");
        meta.put("candidates", List.of(candidate));
        assetRepository.save(Asset.builder()
                .jobId(created.getId())
                .assetType(AssetType.KEYWORD)
                .metaJson(toJson(meta))
                .build());

        keywordService.confirm(created.getId(), keyword, username);
        return created;
    }

    private static String pickKeyword(Map<String, Object> analysis, String title) {
        if (analysis != null && analysis.get("topic_keyword") instanceof String topic && !topic.isBlank()) {
            String trimmed = topic.trim();
            return trimmed.length() > MAX_KEYWORD_LENGTH ? trimmed.substring(0, MAX_KEYWORD_LENGTH).trim() : trimmed;
        }
        String cleaned = cleanTitle(title);
        return cleaned.isBlank() ? "벤치마크 영상" : cleaned;
    }

    static String cleanTitle(String title) {
        String cleaned = title == null ? "" : title
                .replaceAll("[\\[\\]()\"'‘’“”!?…~#]", " ")
                .replaceAll("\\s+", " ")
                .trim();
        return cleaned.length() > MAX_KEYWORD_LENGTH ? cleaned.substring(0, MAX_KEYWORD_LENGTH).trim() : cleaned;
    }

    private String toJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            return "{}";
        }
    }
}
```

- [ ] **Step 6: 컨트롤러 작성** (`BenchmarkController.java`)

```java
package com.pipeline.video.controller;

import com.pipeline.video.dto.BenchmarkJobRequest;
import com.pipeline.video.dto.JobResponse;
import com.pipeline.video.service.BenchmarkService;
import com.pipeline.video.service.FastApiClient;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequiredArgsConstructor
public class BenchmarkController {

    private final FastApiClient fastApiClient;
    private final BenchmarkService benchmarkService;

    @GetMapping("/api/trending/hot-keywords")
    public Map<String, Object> hotKeywords(
            @RequestParam(defaultValue = "ALL") String category,
            @RequestParam(defaultValue = "48h") String window) {
        return fastApiClient.getHotKeywords(category, window);
    }

    @GetMapping("/api/reference-channels/recent-uploads")
    public Map<String, Object> recentUploads(
            @RequestParam(required = false) String ownerChannelId,
            @RequestParam(defaultValue = "7") int days) {
        return benchmarkService.recentUploads(ownerChannelId, Math.max(1, Math.min(days, 7)));
    }

    @PostMapping("/api/jobs/from-benchmark")
    public JobResponse fromBenchmark(
            @Valid @RequestBody BenchmarkJobRequest request,
            @AuthenticationPrincipal String username) {
        return benchmarkService.createFromBenchmark(request, username);
    }

    // Spring Boot는 기본으로 오류 메시지를 응답에 싣지 않으므로, 화면이 사유를 보이도록 message를 직접 내려준다.
    @ExceptionHandler(ResponseStatusException.class)
    public ResponseEntity<Map<String, String>> handle(ResponseStatusException exception) {
        String reason = exception.getReason() == null ? "요청을 처리하지 못했습니다." : exception.getReason();
        return ResponseEntity.status(exception.getStatusCode()).body(Map.of("message", reason));
    }
}
```

컨트롤러 상단 import에 `org.springframework.http.ResponseEntity`, `org.springframework.web.bind.annotation.ExceptionHandler`, `org.springframework.web.server.ResponseStatusException`을 추가한다.

- [ ] **Step 7: ScriptService가 분석을 대본 워커로 전달하게 수정**

`extractCandidateEvidence`의 `evidence.put("evidence", candidate.get("evidence"));` 다음 줄에 추가:

```java
            evidence.put("benchmark_analysis", candidate.get("benchmark_analysis"));
```

- [ ] **Step 8: 통과 확인**

Run: Spring 테스트 실행 방법 + `com.pipeline.video.service.BenchmarkServiceTest`
Expected: `BUILD SUCCESSFUL`, 6개 테스트 통과. 이어서 `--tests "com.pipeline.video.service.ReferenceChannelServiceTest"`와 전체 `gradle test`(옵션 없이)도 통과하는지 확인한다.

- [ ] **Step 9: 보안 설정 확인**

Run: `grep -n "requestMatchers\|permitAll\|authenticated\|/api/" backend/spring-app/src/main/java/com/pipeline/video/config/SecurityConfig.java`
Expected: `/api/trending/**`, `/api/reference-channels/**`, `/api/jobs/**`가 인증된 사용자에게 열려 있다. 관리자 전용으로 막혀 있으면 이 세 경로만 인증 사용자 허용으로 조정한다.

- [ ] **Step 10: 커밋**

```bash
git add backend/spring-app/src
git commit -m "feat(benchmark): 벤치마크 영상으로 작업을 만드는 서비스와 발견 API 추가"
```

---

### Task 7: 프런트엔드 — 발견 화면(BenchmarkDiscovery)과 Dashboard 교체

**Files:**
- Create: `frontend/src/api/discovery.js`
- Create: `frontend/src/components/dashboard/BenchmarkDiscovery.jsx`
- Modify: `frontend/src/pages/Dashboard.jsx` (import와 사용처)
- Delete: `frontend/src/components/dashboard/TrendingSidebar.jsx` (Dashboard가 유일한 사용처)

**Interfaces:**
- Consumes: `GET /api/trending/hot-keywords`, `GET /api/reference-channels/recent-uploads`, `POST /api/jobs/from-benchmark`, 기존 `jobsApi.trendingYoutube(keyword, opts)`(snake_case 필드 반환), `GET /api/channels`.
- Produces: `discoveryApi.hotKeywords(category, window)`, `discoveryApi.recentUploads(ownerChannelId, days)`, `discoveryApi.createFromBenchmark(videoId, job)`. 컴포넌트는 "제작 시작" 시 `navigate('/longform/new', { state: { benchmark: { videoId, title, channelTitle }, channelId } })`.

- [ ] **Step 1: API 래퍼 작성** (`discovery.js`)

```js
import apiClient from './client'

export const discoveryApi = {
  hotKeywords: (category = 'ALL', window = '48h') =>
    apiClient.get('/trending/hot-keywords', { params: { category, window } }).then(r => r.data),
  recentUploads: (ownerChannelId, days = 7) =>
    apiClient.get('/reference-channels/recent-uploads', { params: { ownerChannelId: ownerChannelId || undefined, days } }).then(r => r.data),
  createFromBenchmark: (videoId, job) =>
    apiClient.post('/jobs/from-benchmark', { videoId, job }).then(r => r.data),
}
```

- [ ] **Step 2: 컴포넌트 작성** (`BenchmarkDiscovery.jsx`)

```jsx
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Clock, Eye, Flame, Search, TrendingUp, Users, Youtube, Zap } from 'lucide-react'
import apiClient from '../../api/client'
import { jobsApi } from '../../api/jobs'
import { discoveryApi } from '../../api/discovery'

const FALLBACK_CATEGORIES = [{ key: 'ALL', label: '전체 인기' }]
const WINDOWS = [{ id: '48h', label: '48시간 급상승' }, { id: '7d', label: '7일 지속' }]
const TABS = [{ id: 'hot', label: '핫키워드' }, { id: 'search', label: '직접 검색' }, { id: 'channels', label: '벤치마크 채널 신작' }]

const pick = (video, camel, snake) => video?.[camel] ?? video?.[snake]
const formatNumber = (num) => {
  if (!num) return '0'
  if (num >= 10000) return `${(num / 10000).toFixed(1)}만`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}천`
  return String(Math.round(num))
}

export function normalizeVideo(video) {
  const hours = Number(pick(video, 'hoursSincePublish', 'hours_since_publish'))
  const views = Number(video.views || 0)
  const subscribers = Number(video.subscribers || 0)
  return {
    videoId: pick(video, 'videoId', 'video_id') || '',
    title: video.title || '제목 없음',
    channelTitle: pick(video, 'channelTitle', 'channel_title') || '',
    views,
    subscribers,
    hours: Number.isFinite(hours) ? hours : null,
    viewsPerHour: video.viewsPerHour ?? (Number.isFinite(hours) ? Math.round(views / Math.max(hours, 1)) : null),
    outperformance: video.outperformanceIndex ?? null,
    tags: video.tags || [],
  }
}

function PersistenceBadge({ persistence }) {
  if (persistence === 'sustained') {
    return <span className="inline-flex items-center gap-0.5 rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-bold text-emerald-800"><Flame size={10} />지속 중</span>
  }
  if (persistence === 'spike') {
    return <span className="inline-flex items-center gap-0.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800"><Zap size={10} />순간 급등</span>
  }
  return null
}

export default function BenchmarkDiscovery() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('hot')
  const [category, setCategory] = useState('ALL')
  const [windowId, setWindowId] = useState('48h')
  const [keywordFilter, setKeywordFilter] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [searchKeyword, setSearchKeyword] = useState('')
  const [selected, setSelected] = useState(null)
  const [productionChannelId, setProductionChannelId] = useState('')

  const channelsQuery = useQuery({
    queryKey: ['production-channels'],
    queryFn: () => apiClient.get('/channels').then(r => r.data),
    staleTime: 1000 * 60 * 10,
  })
  const productionChannels = channelsQuery.data || []
  const effectiveChannelId = productionChannelId || productionChannels[0]?.channelId || ''

  const hotQuery = useQuery({
    queryKey: ['discovery-hot', category, windowId],
    queryFn: () => discoveryApi.hotKeywords(category, windowId),
    enabled: tab === 'hot',
    staleTime: 1000 * 60 * 30,
    retry: false,
  })
  const searchQuery = useQuery({
    queryKey: ['discovery-search', searchKeyword],
    queryFn: () => jobsApi.trendingYoutube(searchKeyword),
    enabled: tab === 'search' && !!searchKeyword,
    staleTime: 1000 * 60 * 30,
    retry: false,
  })
  const uploadsQuery = useQuery({
    queryKey: ['discovery-uploads', effectiveChannelId],
    queryFn: () => discoveryApi.recentUploads(effectiveChannelId),
    enabled: tab === 'channels' && !!effectiveChannelId,
    staleTime: 1000 * 60 * 15,
    retry: false,
  })

  const activeQuery = tab === 'hot' ? hotQuery : tab === 'search' ? searchQuery : uploadsQuery
  const rawVideos = tab === 'hot' ? hotQuery.data?.videos : tab === 'search'
    ? (Array.isArray(searchQuery.data) ? searchQuery.data : searchQuery.data?.videos)
    : uploadsQuery.data?.videos
  const videos = useMemo(() => {
    const rows = (rawVideos || []).map(normalizeVideo)
    if (tab !== 'hot' || !keywordFilter) return rows
    const needle = keywordFilter.toLowerCase()
    return rows.filter(v => v.title.toLowerCase().includes(needle) || v.tags.some(t => String(t).toLowerCase().includes(needle)))
  }, [rawVideos, tab, keywordFilter])

  const categories = hotQuery.data?.categories || FALLBACK_CATEGORIES
  const errorMessage = activeQuery.isError
    ? (activeQuery.error?.response?.data?.message || activeQuery.error?.response?.data?.detail || 'YouTube 데이터를 불러오지 못했습니다.')
    : null

  const submitSearch = () => {
    const keyword = searchInput.trim()
    if (!keyword) return
    setSearchKeyword(keyword)
    setSelected(null)
  }
  const backToHot = () => { setTab('hot'); setSearchInput(''); setSearchKeyword(''); setSelected(null) }
  const startBenchmark = () => {
    if (!selected || !effectiveChannelId) return
    navigate('/longform/new', {
      state: {
        benchmark: { videoId: selected.videoId, title: selected.title, channelTitle: selected.channelTitle },
        channelId: effectiveChannelId,
      },
    })
  }

  return (
    <section className="bg-navy-800 rounded-xl border border-slate-200 overflow-hidden">
      <div className="p-5 border-b border-slate-200 space-y-4">
        <div className="flex items-center gap-2">
          <Youtube className="text-red-500" size={20} />
          <div>
            <h2 className="font-bold text-sm text-white">지금 뜨는 핫키워드 · 벤치마크</h2>
            <p className="text-xs text-slate-500 mt-1">잘 되는 영상을 골라 그 영상을 기준으로 바로 제작을 시작하세요.</p>
          </div>
        </div>

        <div className="flex gap-2 overflow-x-auto">
          {TABS.map(item => (
            <button key={item.id} type="button" onClick={() => { setTab(item.id); setSelected(null) }}
              className={`shrink-0 rounded-full border px-3 py-1.5 text-xs font-semibold transition ${tab === item.id ? 'border-red-500 bg-red-500 text-white' : 'border-slate-300 text-slate-500 hover:border-red-500/60'}`}>
              {item.label}
            </button>
          ))}
        </div>

        {tab === 'hot' && (
          <>
            <div className="flex gap-2 flex-wrap">
              {categories.map(item => (
                <button key={item.key} type="button" onClick={() => { setCategory(item.key); setKeywordFilter(''); setSelected(null) }}
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${category === item.key ? 'border-cyan-600 bg-cyan-50 text-cyan-800' : 'border-slate-300 text-slate-600 hover:border-cyan-500'}`}>
                  {item.label}
                </button>
              ))}
            </div>
            <div className="inline-flex overflow-hidden rounded-lg border border-slate-300">
              {WINDOWS.map(item => (
                <button key={item.id} type="button" onClick={() => { setWindowId(item.id); setKeywordFilter('') }}
                  className={`px-3 py-1.5 text-xs font-semibold ${windowId === item.id ? 'bg-cyan-50 text-cyan-800' : 'text-slate-600'}`}>
                  {item.label}
                </button>
              ))}
            </div>
            {hotQuery.data?.keywords?.length > 0 && (
              <div className="flex gap-2 flex-wrap">
                {hotQuery.data.keywords.slice(0, 16).map(item => (
                  <button key={item.keyword} type="button" onClick={() => setKeywordFilter(keywordFilter === item.keyword ? '' : item.keyword)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold ${keywordFilter === item.keyword ? 'border-red-500 bg-red-50 text-red-700' : 'border-slate-300 text-slate-700 hover:border-red-400'}`}>
                    {item.keyword}<PersistenceBadge persistence={item.persistence} />
                  </button>
                ))}
              </div>
            )}
          </>
        )}

        {tab === 'search' && (
          <div className="flex gap-2 items-center">
            {searchKeyword && (
              <button type="button" onClick={backToHot} className="inline-flex items-center gap-1 rounded-xl border border-slate-300 px-3 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50">
                <ArrowLeft size={13} />핫키워드로 돌아가기
              </button>
            )}
            <input value={searchInput} onChange={e => setSearchInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && submitSearch()}
              placeholder="직접 키워드 검색" className="flex-1 rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-900" />
            <button type="button" onClick={submitSearch} className="inline-flex items-center gap-1 rounded-xl bg-cyan-700 px-4 py-2 text-xs font-bold text-white">
              <Search size={13} />검색
            </button>
          </div>
        )}

        {tab === 'channels' && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="font-bold">제작 채널 기준</span>
            <select value={effectiveChannelId} onChange={e => setProductionChannelId(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-2 py-1 font-bold text-slate-800">
              {productionChannels.map(ch => <option key={ch.channelId} value={ch.channelId}>{ch.channelName}</option>)}
            </select>
            <span>이 채널에 등록된 벤치마크 채널과 공용 채널의 최근 7일 업로드</span>
          </div>
        )}
      </div>

      {activeQuery.isFetching && <div className="flex h-32 items-center justify-center text-sm text-slate-500">수집 중...</div>}
      {errorMessage && <div className="px-5 py-8 text-center text-sm text-red-500">{errorMessage}</div>}
      {tab === 'channels' && !activeQuery.isFetching && uploadsQuery.data?.empty && (
        <div className="px-5 py-8 text-center text-sm text-slate-500">이 채널에 등록된 벤치마크 채널이 없습니다. 관리자 화면 "레퍼런스 채널"에서 적용 채널을 지정해 주세요.</div>
      )}
      {!activeQuery.isFetching && !errorMessage && videos.length === 0 && !(tab === 'channels' && uploadsQuery.data?.empty) && !(tab === 'search' && !searchKeyword) && (
        <div className="px-5 py-8 text-center text-sm text-slate-500">조건에 맞는 영상이 없습니다.</div>
      )}

      {!activeQuery.isFetching && videos.length > 0 && (
        <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2 xl:grid-cols-4">
          {videos.map((video, index) => (
            <button key={video.videoId || index} type="button" onClick={() => setSelected(video)}
              className={`text-left rounded-lg border p-2 transition ${selected?.videoId === video.videoId ? 'border-2 border-cyan-600' : 'border-slate-200 hover:border-red-400'}`}>
              <div className="relative aspect-video overflow-hidden rounded bg-navy-900">
                {video.videoId && <img src={`https://i.ytimg.com/vi/${video.videoId}/hqdefault.jpg`} alt="" className="h-full w-full object-cover" />}
                <span className="absolute left-2 top-2 rounded bg-black/70 px-1.5 py-0.5 text-[10px] font-bold text-white">#{index + 1}</span>
              </div>
              <p className="mt-2 line-clamp-2 min-h-8 text-xs font-bold leading-snug text-white">{video.title}</p>
              <p className="mt-1 truncate text-[11px] text-slate-500">{video.channelTitle || '채널 정보 없음'}</p>
              <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-1 text-[10px] text-slate-500">
                <span className="flex items-center gap-1"><Eye size={10} />{formatNumber(video.views)}회</span>
                <span className="flex items-center gap-1"><TrendingUp size={10} />{video.viewsPerHour == null ? '-' : `${formatNumber(video.viewsPerHour)}/시간`}</span>
                <span className="flex items-center gap-1"><Users size={10} />{formatNumber(video.subscribers)}명</span>
                <span className="flex items-center gap-1"><Clock size={10} />{video.hours == null ? '-' : video.hours < 24 ? `${Math.floor(video.hours)}시간 전` : `${Math.floor(video.hours / 24)}일 전`}</span>
              </div>
              {video.outperformance != null && <p className="mt-1 text-[10px] font-bold text-emerald-700">채널 평균 대비 {video.outperformance}배</p>}
            </button>
          ))}
        </div>
      )}

      {selected && (
        <div className="m-4 space-y-3 rounded-xl bg-slate-50 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="text-sm font-bold text-slate-900">선택한 벤치마크 영상</div>
            <div className="max-w-[60%] truncate text-xs text-slate-500">{selected.title}</div>
          </div>
          <ul className="list-disc space-y-1 pl-5 text-xs text-slate-700">
            <li>조회수 {formatNumber(selected.views)}회{selected.viewsPerHour != null && ` · 시간당 ${formatNumber(selected.viewsPerHour)}회`}</li>
            {selected.subscribers > 0 && <li>구독자 대비 조회율 {((selected.views / selected.subscribers) * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%</li>}
            {selected.outperformance != null && <li>이 채널의 최근 평균보다 {selected.outperformance}배</li>}
            {selected.tags.length > 0 && <li>태그: {selected.tags.slice(0, 6).join(', ')}</li>}
          </ul>
          <p className="text-[11px] text-slate-500">뜨는 이유 분석은 제작을 시작할 때 자동으로 수행되어 대본 설계에 반영됩니다.</p>
          <div className="flex flex-wrap items-center gap-2">
            <select value={effectiveChannelId} onChange={e => setProductionChannelId(e.target.value)} className="rounded-lg border border-slate-300 bg-white px-2 py-1.5 text-xs font-bold text-slate-800">
              {productionChannels.map(ch => <option key={ch.channelId} value={ch.channelId}>{ch.channelName}</option>)}
            </select>
            <button type="button" onClick={startBenchmark} disabled={!effectiveChannelId}
              className="ml-auto rounded-xl bg-cyan-700 px-4 py-2 text-xs font-bold text-white hover:bg-cyan-800 disabled:opacity-50">
              이 영상으로 롱폼 제작 시작
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
```

- [ ] **Step 3: Dashboard 교체**

`Dashboard.jsx`에서 두 곳을 바꾼다.

- `import TrendingSidebar from '../components/dashboard/TrendingSidebar'` → `import BenchmarkDiscovery from '../components/dashboard/BenchmarkDiscovery'`
- `<TrendingSidebar />` → `<BenchmarkDiscovery />`

그리고 사용처가 없어진 `frontend/src/components/dashboard/TrendingSidebar.jsx`를 `git rm`으로 삭제한다.

- [ ] **Step 4: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...` (오류 없음)

- [ ] **Step 5: 커밋**

```bash
git add frontend/src/api/discovery.js frontend/src/components/dashboard/BenchmarkDiscovery.jsx frontend/src/pages/Dashboard.jsx
git rm frontend/src/components/dashboard/TrendingSidebar.jsx
git commit -m "feat(dashboard): 핫키워드·벤치마크 채널 발견 화면으로 교체"
```

---

### Task 8: 프런트엔드 — JobNew 벤치마크 모드

**Files:**
- Modify: `frontend/src/pages/JobNew.jsx`

**Interfaces:**
- Consumes: 라우터 state `{ benchmark: { videoId, title, channelTitle }, channelId }` (Task 7), `discoveryApi.createFromBenchmark(videoId, job)`.

- [ ] **Step 1: import 추가**

`import { jobsApi } from '../api/jobs'` 다음 줄에 추가:

```jsx
import { discoveryApi } from '../api/discovery'
```

- [ ] **Step 2: 벤치마크 상태와 초기값**

`const [step, setStep] = useState(1)`을 다음으로 바꾼다.

```jsx
  const benchmark = location.state?.benchmark || null
  const [step, setStep] = useState(benchmark ? 2 : 1)
```

`useState({ title: '', channelId: '', category: 'KOSPI', ...` 폼 초기값에서 `title: '',`와 `channelId: '',`를 다음으로 바꾼다.

```jsx
    title: benchmark?.title || '',
    channelId: location.state?.channelId || '',
```

- [ ] **Step 3: 단계 이동 막기 (STEP 01 생략)**

스테퍼 버튼의 두 줄

```jsx
                  onClick={() => isPast && setStep(s.n)}
                  disabled={!isPast && !isCurrent}
```

을 다음으로 바꾼다.

```jsx
                  onClick={() => isPast && !(benchmark && s.n === 1) && setStep(s.n)}
                  disabled={(!isPast && !isCurrent) || (benchmark && s.n === 1)}
```

"이전 단계" 버튼의 `{step > 1 ? (`를 `{step > (benchmark ? 2 : 1) ? (`로 바꾼다.

- [ ] **Step 4: 벤치마크 배지 표시**

`{/* 카드 본문 컨테이너 (Clean High-Contrast White Card) */}` 줄 바로 위에 추가:

```jsx
        {benchmark && (
          <div className="flex items-center gap-2 rounded-xl border border-cyan-200 bg-cyan-50 px-4 py-3 text-xs font-bold text-cyan-900">
            <Sparkles size={14} className="text-cyan-600" />
            벤치마크: {benchmark.title}
            {benchmark.channelTitle && <span className="font-semibold text-cyan-700">· {benchmark.channelTitle}</span>}
          </div>
        )}

```

- [ ] **Step 5: 제출 분기**

`handleSubmit`의 아래 블록

```jsx
      const job = await jobsApi.create(form)
      jobsApi.searchKeyword(job.id, form.keyword || form.title, 5).catch(err => {
        console.warn('키워드 자동 탐색 백그라운드 호출:', err)
      })
      navigate(`/longform/${job.id}`)
```

을 다음으로 바꾼다.

```jsx
      const job = benchmark
        ? await discoveryApi.createFromBenchmark(benchmark.videoId, form)
        : await jobsApi.create(form)
      if (!benchmark) {
        jobsApi.searchKeyword(job.id, form.keyword || form.title, 5).catch(err => {
          console.warn('키워드 자동 탐색 백그라운드 호출:', err)
        })
      }
      navigate(`/longform/${job.id}`)
```

- [ ] **Step 6: 최종 요약 행 조정 (STEP 3)**

`<Row label="영상 대표 주제" value={form.title} highlight />`를 다음으로 바꾼다.

```jsx
                <Row label="영상 대표 주제" value={benchmark ? '벤치마크 분석 후 자동 결정' : form.title} highlight />
                {benchmark && <Row label="벤치마크 영상" value={benchmark.title} />}
```

또한 제출 실패 시 서버 메시지를 보이도록 `setError(err?.response?.data?.message || err.message || '작업 생성 실패')`는 그대로 둔다(409 메시지가 이 경로로 표시된다).

- [ ] **Step 7: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...`

- [ ] **Step 8: 커밋**

```bash
git add frontend/src/pages/JobNew.jsx
git commit -m "feat(job-new): 벤치마크 영상으로 시작하는 모드(STEP 01 생략) 추가"
```

---

### Task 9: 프런트엔드 — 레퍼런스 채널 적용 채널 표시·편집

**Files:**
- Modify: `frontend/src/components/admin/ReferenceChannelManager.jsx`

- [ ] **Step 1: 제작 채널 목록 조회 추가**

`const selectedItems = bulkRows.flatMap(row => {` 줄 바로 위에 추가:

```jsx
  const productionChannelsQuery = useQuery({
    queryKey: ['production-channels'],
    queryFn: () => apiClient.get('/channels').then(response => response.data),
    staleTime: 1000 * 60 * 10,
  })
  const productionChannels = productionChannelsQuery.data || []
  const ownerLabel = ownerId => (ownerId ? (productionChannels.find(ch => ch.channelId === ownerId)?.channelName || ownerId) : '공용')

```

(`useQuery`는 이 파일이 이미 `channelsQuery`에 사용 중이라 import되어 있다. 없으면 `@tanstack/react-query`에서 import한다.)

- [ ] **Step 2: 편집 시작 시 값 포함**

`beginEdit`의 `active: channel.active,` 다음 줄에 추가:

```jsx
      ownerChannelId: channel.ownerChannelId || '',
```

- [ ] **Step 3: 편집 입력에 선택 상자 추가**

편집 모드의 표시명 입력 한 줄

```jsx
                          <input value={editForm.displayName} onChange={event => setEditForm({ ...editForm, displayName: event.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5 font-bold" />
```

을 다음으로 바꾼다.

```jsx
                          <div className="space-y-1.5">
                            <input value={editForm.displayName} onChange={event => setEditForm({ ...editForm, displayName: event.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5 font-bold" />
                            <select value={editForm.ownerChannelId} onChange={event => setEditForm({ ...editForm, ownerChannelId: event.target.value })} className="w-full rounded-lg border border-slate-300 px-2 py-1.5 font-bold">
                              <option value="">공용 (모든 제작 채널)</option>
                              {productionChannels.map(ch => <option key={ch.channelId} value={ch.channelId}>{ch.channelName}</option>)}
                            </select>
                          </div>
```

- [ ] **Step 4: 목록에 적용 채널 표시**

`<div className="mt-0.5 break-all text-[10px] text-slate-400">{channel.channelId}</div>` 다음 줄에 추가:

```jsx
                            <div className="mt-0.5 text-[10px] font-bold text-cyan-700">적용 채널: {ownerLabel(channel.ownerChannelId)}</div>
```

(재활성화 버튼의 payload에는 `ownerChannelId`가 없으므로 서버가 기존 값을 유지한다. 새 채널 생성 폼은 공용으로 생성되며 생성 후 이 편집 화면에서 지정한다.)

- [ ] **Step 5: 빌드 확인**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...`

- [ ] **Step 6: 커밋**

```bash
git add frontend/src/components/admin/ReferenceChannelManager.jsx
git commit -m "feat(admin): 레퍼런스 채널에 적용 제작 채널 표시·편집 추가"
```

---

### Task 10: 통합 — 이미지 재빌드, 실제 흐름 검증, 전체 회귀

**Files:** 없음(검증 전용). 문제를 발견하면 해당 Task의 파일을 수정하고 별도 커밋한다.

- [ ] **Step 1: 전체 회귀**

FastAPI: `docker cp backend/fastapi-workers/app/. pipeline_fastapi:/app/app/ && docker cp backend/fastapi-workers/tests/. pipeline_fastapi:/app/tests/ && docker exec pipeline_fastapi python -m pytest tests/ -q --continue-on-collection-errors`
Expected: 새 테스트 포함 전부 passed, 실패 0 (기존의 컨테이너 경로 collection error 14개는 그대로여도 무관)

Spring: Spring 테스트 실행 방법에서 `--tests` 옵션 없이 `gradle test` 전체 실행
Expected: `BUILD SUCCESSFUL`

- [ ] **Step 2: 이미지 재빌드와 재기동**

```bash
docker compose build fastapi-workers spring-app frontend && docker compose up -d
```

`docker logs pipeline_spring --tail 20`에서 `Started VideoPipelineApplication`, `docker logs pipeline_fastapi --tail 10`에서 `Application startup complete`를 확인한다. Hibernate가 `reference_channel.owner_channel_id` 컬럼을 추가했는지 확인한다:

```bash
docker exec pipeline_postgres psql -U pipeline_user -d ai_video_pipeline -c "\d reference_channel" | grep owner_channel_id
```

- [ ] **Step 3: FastAPI 엔드포인트 실제 호출 확인**

```bash
curl -s "http://localhost:8201/workers/discovery/hot-keywords?category=ALL&window=48h" | head -c 600
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8201/workers/discovery/hot-keywords?category=ALL&window=30d"
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8201/workers/discovery/video/abcdefghijk"
```

Expected: 첫 호출은 `keywords`·`videos`·`categories`가 있는 JSON, 두 번째는 `400`, 세 번째는 `404`(존재하지 않는 영상). 첫 호출의 키워드 품질(흔한 단어가 섞이는지)을 눈으로 확인하고, 거슬리는 단어가 있으면 `hot_keywords.py`의 `_FILLER`에 추가하는 별도 커밋을 만든다.

- [ ] **Step 4: Spring 경로와 인증 확인**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8080/api/trending/hot-keywords"
curl -s -o /dev/null -w "%{http_code}\n" -X POST "http://localhost:8080/api/jobs/from-benchmark" -H "Content-Type: application/json" -d "{}"
```

Expected: 둘 다 `401` 또는 `403`(경로는 존재하고 인증만 필요). `404`면 경로가 등록되지 않은 것이다.

- [ ] **Step 5: 브라우저 흐름 검증** (Claude in Chrome, 사용자가 이미 로그인한 크롬)

1. `http://localhost:3000` 대시보드에서 "지금 뜨는 핫키워드 · 벤치마크" 섹션이 뜨고 카테고리 칩·기간 토글·핫키워드 칩·영상 카드가 보이는지 확인한다.
2. 카테고리를 바꾸고 `7일 지속`으로 전환해 결과가 바뀌는지, 핫키워드 칩 클릭 시 영상이 걸러지는지 확인한다.
3. `직접 검색` 탭에서 검색 후 `핫키워드로 돌아가기`가 동작하는지 확인한다.
4. 영상 카드를 선택하면 패널이 열리고, 제작 채널을 고른 뒤 `이 영상으로 롱폼 제작 시작`을 누르면 `/longform/new`가 STEP 02로 열리고 상단에 "벤치마크:" 배지가 뜨는지 확인한다.
5. STEP 03에서 제출해 작업이 생성되고 `/longform/{id}`로 이동하는지, DB에서 작업의 `keyword`가 채워지고 KEYWORD 에셋에 `benchmark_analysis`가 들어갔는지 확인한다:

```bash
docker exec pipeline_postgres psql -U pipeline_user -d ai_video_pipeline -c "SELECT id, title, keyword, status FROM video_job ORDER BY id DESC LIMIT 1;"
docker exec pipeline_postgres psql -U pipeline_user -d ai_video_pipeline -c "SELECT LEFT(meta_json, 300) FROM asset WHERE asset_type='KEYWORD' ORDER BY id DESC LIMIT 2;"
```

6. 관리자 "레퍼런스 채널"에서 채널의 적용 채널을 지정하고 대시보드 `벤치마크 채널 신작` 탭에 반영되는지 확인한다(등록된 채널이 없으면 이 항목은 등록 후 확인).
7. 일반 `롱폼 만들기`(직접 키워드 입력) 흐름이 그대로 동작하는지 확인한다.

- [ ] **Step 6: 푸시 전 정리**

`git status`로 의도하지 않은 변경이 없는지 확인한다. 푸시는 사용자 승인을 받은 뒤에 한다.
