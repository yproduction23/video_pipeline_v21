"""대본 장면에 등장하는 구체적 소품/사물의 실사 참조 이미지를 찾는다.

2026-10-06 사용자 요청: "전역모"처럼 고유한 실제 형태가 있는 소품은 텍스트
설명만으로는 Gemini가 정확한 모양을 그리지 못한다. NAVER API HUB 이미지
검색으로 실물 사진을 찾아, 캐릭터/채널 화풍 참조와는 별도인
`prop_reference_*` 슬롯으로 Gemini에 전달한다
(선택 로직은 app/v5/providers/gemini_provider.py 참고).

이 모듈의 모든 단계(용어 추출, 검색, 다운로드, 이미지 검증)는 실패해도
예외를 밖으로 내보내지 않고 빈 결과를 반환한다 — 기존 이미지 생성 흐름을
막지 않는 순수 부가 기능이다. 아직 실제 운영 Job으로 검증되지 않았으므로
기본값은 꺼져 있다 (PROP_REFERENCE_SEARCH_ENABLED, app/config.py).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from pathlib import Path
from typing import Optional

import httpx

from app.config import ANTHROPIC_API_KEY, CLAUDE_MODEL
from app.services.naver_api_hub import NaverApiHubClient, NaverApiHubUnavailable

logger = logging.getLogger(__name__)

PROP_REFERENCE_PREFIX = "prop_reference_"

_EXTRACTION_SYSTEM = (
    "당신은 영상 대본 한 장면에서, 글만으로는 AI 이미지 생성 모델이 정확한 "
    "형태를 그릴 수 없는 '구체적인 실존 사물·상징'을 찾는 보조자입니다. "
    "추상적 개념, 감정, 일반적 배경(책상, 사무실, 하늘, 건물 등)은 제외하고, "
    "고유하고 실제로 존재하는 형태가 있어 사진 참조가 꼭 필요한 명사만 고르세요. "
    "예: '전역모', '동계올림픽 금메달', '화랑무공훈장'. 해당하는 사물이 없으면 "
    "빈 배열을 반환하세요. 금융 수치나 등락률 같은 숫자는 절대 포함하지 마세요. "
    '반드시 JSON 객체 하나만 응답하세요: {"terms": ["검색에 쓸 한국어 명사", ...]}'
)

_ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_MIN_DIMENSION_PX = 200


def extract_prop_reference_terms(scene_text: str, *, max_terms: int = 1) -> list[str]:
    """장면 문장에서 실사 참조가 필요한 구체 사물 명사를 최대 max_terms개 추출한다.

    Claude 호출/응답 해석이 실패하면 빈 리스트를 반환한다. 이 기능은 선택적
    보조 기능이므로 실패가 이미지 생성을 막으면 안 된다.
    """
    text = " ".join((scene_text or "").split())
    if not text or not ANTHROPIC_API_KEY:
        return []
    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            system=_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
        raw = response.content[0].text.strip()
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            return []
        data = json.loads(match.group())
        terms = data.get("terms")
        if not isinstance(terms, list):
            return []
        cleaned = [str(term).strip() for term in terms if str(term).strip()]
        return cleaned[:max_terms]
    except Exception:
        logger.warning("소품 참조 용어 추출 실패 (무시하고 계속 진행)", exc_info=True)
        return []


def _slugify(term: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "_", term).strip("_")
    digest = hashlib.sha1(term.encode("utf-8")).hexdigest()[:8]
    return f"{slug or 'term'}_{digest}"


def _find_cached(cache_dir: Path, term: str) -> Optional[Path]:
    if not cache_dir.is_dir():
        return None
    prefix = f"{PROP_REFERENCE_PREFIX}{_slugify(term)}."
    for candidate in sorted(cache_dir.glob(f"{prefix}*")):
        if candidate.is_file():
            return candidate
    return None


def _download_and_validate(url: str, term: str, cache_dir: Path) -> Optional[Path]:
    try:
        response = httpx.get(url, timeout=10, follow_redirects=True)
        response.raise_for_status()
    except Exception:
        return None
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    ext = _ALLOWED_IMAGE_CONTENT_TYPES.get(content_type)
    if ext is None:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(response.content)) as image:
            image.verify()
    except Exception:
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{PROP_REFERENCE_PREFIX}{_slugify(term)}.{ext}"
    out_path.write_bytes(response.content)
    return out_path


def search_prop_reference_image(term: str, *, cache_dir: Path) -> Optional[Path]:
    """NAVER 이미지 검색에서 term의 실사 참조를 찾아 cache_dir에 내려받는다.

    검색/다운로드/검증 중 어느 단계든 실패하면 None을 반환한다 (NAVER API HUB
    미설정 포함 — 새 환경 변수를 추가로 요구하지 않는 선택적 기능이다).
    """
    term = (term or "").strip()
    if not term:
        return None
    try:
        client = NaverApiHubClient()
    except NaverApiHubUnavailable:
        logger.info("NAVER API HUB가 설정되지 않아 소품 참조 검색을 건너뜀")
        return None

    try:
        body = client.search_images(term, display=5, sort="sim", filter="large")
    except Exception:
        logger.warning("소품 참조 이미지 검색 실패: term=%s", term, exc_info=True)
        return None

    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if not link.startswith("http"):
            continue
        try:
            width = int(item.get("sizewidth") or 0)
            height = int(item.get("sizeheight") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if width and height and (width < _MIN_DIMENSION_PX or height < _MIN_DIMENSION_PX):
            continue
        path = _download_and_validate(link, term, cache_dir)
        if path is not None:
            return path
    return None


def resolve_prop_reference_paths(
    scene_text: str,
    *,
    cache_dir: Path,
    max_terms: int = 1,
) -> list[str]:
    """장면 문장에서 소품 참조 용어를 추출해 실사 참조 이미지 경로를 반환한다.

    같은 Job 안에서 이미 내려받은 용어는 캐시를 재사용해 중복 검색하지
    않는다. 어떤 단계든 실패하면 조용히 빈 리스트를 반환한다.
    """
    try:
        terms = extract_prop_reference_terms(scene_text, max_terms=max_terms)
        paths: list[str] = []
        for term in terms:
            cached = _find_cached(cache_dir, term)
            if cached is not None:
                paths.append(str(cached))
                continue
            found = search_prop_reference_image(term, cache_dir=cache_dir)
            if found is not None:
                paths.append(str(found))
        return paths
    except Exception:
        logger.warning("소품 참조 해석 실패 (무시하고 계속 진행)", exc_info=True)
        return []
