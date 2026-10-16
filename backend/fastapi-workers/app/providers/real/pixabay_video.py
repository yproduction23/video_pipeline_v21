"""Pixabay 영상 검색 클라이언트 — 캐릭터 없는 "슬라이드형" 시각 모드의 배경 영상 소스.

2026-10-14 사용자 결정: archetype/V5 캐릭터 시스템이 비금융 주제(범죄 뉴스 등)에서
반복적으로 의미 불일치를 일으켜, 캐릭터 없이 "헤드라인 텍스트 + 무료 스톡 영상"
스타일의 대안 시각 모드를 추가한다. 원래 Pexels를 검토했으나 2026-10-14 기준
신규 API 키 발급이 일시 중단되어(공식 페이지 직접 확인) Pixabay로 대체했다.

공식 문서(https://pixabay.com/api/docs/) 확인:
- 인증은 쿼리 파라미터 key=(헤더 아님)
- 응답에 large/medium/small/tiny 4단계 해상도가 바로 포함됨
- 분당 100회 요청 제한

2026-10-14 사용자 재현: 검색어-태그 단어 겹침 개수로 관련성을 판정하는 방식은
우연한 단어 하나(예: "close"가 "close up" 웨딩 영상과, "bonsai"가 전혀 안
겹치는데도 통과됨 등)만으로 완전히 무관한 영상을 통과시켰다. 이 모듈은 이제
순수하게 "후보 목록을 가져오는 것"까지만 담당하고, 실제로 내레이션 맥락에
맞는지 판단하는 건 app.services.slide_scene_direction의 Claude 기반 선택
함수가 한다(단어 개수가 아니라 의미로 판단).
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

PIXABAY_VIDEO_SEARCH_URL = "https://pixabay.com/api/videos/"

# 큰 해상도부터 순서대로 선호 — 1920폭에 가장 가까운 해상도를 고른다.
_RESOLUTION_TIERS = ("large", "medium", "small", "tiny")
_TARGET_WIDTH = 1920


class PixabayVideoUnavailableError(RuntimeError):
    """PIXABAY_API_KEY 미설정 또는 API 호출 실패."""


def _api_key() -> str:
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if not key:
        raise PixabayVideoUnavailableError("PIXABAY_API_KEY가 설정되지 않았습니다.")
    return key


def _pick_resolution_file(hit: dict) -> dict | None:
    """최종 영상이 1920x1080이므로 폭이 1920 이상인 것 중 가장 작은 해상도를 고른다(없으면 가장 큰 것).

    2026-10-16 20번 Job: "large"(4K)를 받아 파일이 4배 커지고, 병렬 합성이 메모리(컨테이너 4GB)를 넘겨 멈췄다."""
    videos = hit.get("videos") or {}
    landscape = []
    for tier in _RESOLUTION_TIERS:
        candidate_file = videos.get(tier)
        if candidate_file and int(candidate_file.get("width") or 0) >= int(candidate_file.get("height") or 1):
            landscape.append({**candidate_file, "resolution_tier": tier})
    if not landscape:
        return None
    enough = [f for f in landscape if int(f.get("width") or 0) >= _TARGET_WIDTH]
    if enough:
        return min(enough, key=lambda f: int(f.get("width") or 0))
    return max(landscape, key=lambda f: int(f.get("width") or 0))


def search_candidates(query: str, *, min_duration_seconds: int = 3, per_page: int = 15) -> list[dict]:
    """쿼리로 영상을 검색해, 화면에 쓸 수 있는(가로형·충분한 길이·실사·정상
    화질) 후보 목록을 Pixabay 자체 인기순 그대로 반환한다. 어떤 후보가
    "내용과 실제로 맞는지"는 이 함수가 판단하지 않는다 — 호출부가 내레이션
    맥락으로 골라야 한다(단어 겹침만으로는 신뢰할 수 없었다)."""
    params = {
        "key": _api_key(),
        "q": query,
        "video_type": "film",
        "safesearch": "true",
        "per_page": per_page,
    }
    try:
        response = requests.get(PIXABAY_VIDEO_SEARCH_URL, params=params, timeout=15)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise PixabayVideoUnavailableError(f"Pixabay 영상 검색 연결 실패: {exc}") from exc
    if response.status_code != 200:
        raise PixabayVideoUnavailableError(f"Pixabay 영상 검색 실패: HTTP {response.status_code} {response.text[:200]}")
    data = response.json()
    hits = data.get("hits") or []
    results = []
    for hit in hits:
        if float(hit.get("duration") or 0) < min_duration_seconds:
            continue
        if hit.get("isAiGenerated") or hit.get("isLowQuality"):
            continue
        chosen_file = _pick_resolution_file(hit)
        if not chosen_file:
            continue
        results.append({
            "pixabay_id": hit.get("id"),
            "pixabay_page_url": hit.get("pageURL"),
            "duration": hit.get("duration"),
            "resolution_tier": chosen_file["resolution_tier"],
            "download_url": chosen_file.get("url"),
            "width": chosen_file.get("width"),
            "height": chosen_file.get("height"),
            "thumbnail_url": chosen_file.get("thumbnail"),
            "tags": str(hit.get("tags") or ""),
        })
    return results


def download_clip(download_url: str, output_path: str) -> None:
    try:
        response = requests.get(download_url, timeout=60, stream=True)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise PixabayVideoUnavailableError(f"Pixabay 클립 다운로드 연결 실패: {exc}") from exc
    if response.status_code != 200:
        raise PixabayVideoUnavailableError(f"Pixabay 클립 다운로드 실패: HTTP {response.status_code}")
    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1 << 20):
            f.write(chunk)
