"""벤치마크 영상의 자막(대사)을 가져와 줄거리 분석의 근거로 쓴다.

비공식 자막 경로(youtube-transcript-api)를 쓰므로 언제든 실패할 수 있다.
모든 실패는 None으로 돌려 기존 동작(제목·태그 기반 분석)으로 자연스럽게 되돌아간다.
자막 원문은 분석 입력으로만 쓰고 저장·인용하지 않는다.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

MAX_CHARS = 6000
_NOISE = re.compile(r"\[[^\]]{1,10}\]|>>+")


def _clean(snippets) -> str:
    text = " ".join(str(getattr(item, "text", item.get("text", "") if isinstance(item, dict) else "")) for item in snippets)
    text = _NOISE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=128)
def _fetch(video_id: str) -> Optional[str]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["ko", "en"])
        text = _clean(list(fetched))
    except Exception as exc:  # 자막 없음·비공개·차단·라이브러리 미설치 모두 동일하게 처리
        logger.info("자막 수집 실패(무시): video_id=%s, %s", video_id, type(exc).__name__)
        return None
    return text[:MAX_CHARS] if text else None


def fetch_transcript(video_id: Optional[str]) -> Optional[str]:
    """자막 텍스트(최대 6000자)를 반환한다. 없거나 실패하면 None."""
    video_id = str(video_id or "").strip()
    return _fetch(video_id) if video_id else None
