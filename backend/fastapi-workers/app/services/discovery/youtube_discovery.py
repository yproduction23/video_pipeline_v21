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
