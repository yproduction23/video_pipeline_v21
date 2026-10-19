"""스톡 영상의 외국 랜드마크 검사 — 한국 시장 이야기에 특정 외국 도시·건물이 알아보게 나오면 쓰지 않는다.

2026-10-19 운영자 지적(2:38·2:50): "코스피 낙폭이 글로벌 증시 대비" 장면에 타이베이 101이, 도미노 장면에 이스탄불 모스크 야경이 나왔다.
후보를 고르는 단계의 프롬프트에 규칙을 넣어도 걸러지지 않아, 고른 뒤 같은 프레임을 "외국 랜드마크인가"만 묻는 예/아니오 질문으로 한 번 더 확인한다.
확인 호출이 실패하면 막지 않는다(스톡 선택은 이미 한 번 판정을 거쳤고, 검사는 보조 단계다)."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path

from app.utils.claude_ledger import claude_create
logger = logging.getLogger(__name__)
_CACHE_LOCK = __import__("threading").Lock()

_SYSTEM = (
    "너는 한국 경제 해설 영상의 배경 영상 검수자다. 이미지는 후보 영상의 한 프레임이고, 함께 그 장면의 내레이션이 주어진다. "
    "질문은 하나다: 이 프레임에서 한국이 아닌 특정 나라·도시를 알아볼 수 있는가? (예: 타이베이 101, 이스탄불의 이슬람 사원 돔과 첨탑, "
    "페트로나스 타워, 에펠탑, 자유의 여신상, 외국 간판·번체자·아랍 문자가 보이는 거리, 유럽식 광장). "
    "내레이션이 그 나라나 장소를 직접 말하면 foreign=false다. 한국인지 외국인지 알아볼 수 없는 일반 풍경·사물·사람은 foreign=false다. "
    "확신이 없으면 false다.\n"
    '출력은 JSON만: {"foreign": true|false, "reason": "한 줄"}'
)


def _default_vision_call(system: str, text: str, images: list[bytes]) -> str:
    from anthropic import Anthropic

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(images[0]).decode()}},
               {"type": "text", "text": text}]
    response = claude_create(purpose="slide_landmark_judge", 
        model="claude-sonnet-4-6", max_tokens=200, system=system, messages=[{"role": "user", "content": content}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def looks_foreign(frame_path: str, narration: str, *, vision_call=None) -> tuple[bool, str]:
    """프레임이 (내레이션이 말하지 않은) 외국 랜드마크·거리로 알아보이면 (True, 사유). 판정 호출이 실패하면 (False, 사유)."""
    import io

    from PIL import Image

    try:
        with Image.open(frame_path) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((768, 768))
            buffer = io.BytesIO()
            thumb.save(buffer, "PNG")
        raw = (vision_call or _default_vision_call)(_SYSTEM, f"내레이션: {narration}\nJSON만 반환.", [buffer.getvalue()])
        match = re.search(r"\{[\s\S]*\}", str(raw or ""))
        data = json.loads(match.group(0) if match else raw)
        return bool(data.get("foreign")), str(data.get("reason") or "")
    except Exception as exc:
        logger.warning("외국 랜드마크 검사 실패(막지 않는다): %s", exc)
        return False, f"검사 실패: {exc}"


def looks_foreign_cached(cache_path: Path | str, clip_id: object, frame_path: str, narration: str, *, vision_call=None) -> tuple[bool, str]:
    """같은 스톡 클립(clip_id)의 판정은 작업 폴더 캐시에 남겨 다시 묻지 않는다(재생성·재실행마다 같은 프레임을 비용 들여 또 검사하지 않게).

    판정은 내레이션에 따라 달라질 수 있으므로 캐시 키에 내레이션 지문도 넣는다. 검사 호출이 실패한 결과는 저장하지 않는다."""
    import hashlib
    import threading

    key = f"{clip_id}:{hashlib.sha1(narration.encode('utf-8')).hexdigest()[:10]}"
    path = Path(cache_path)
    with _CACHE_LOCK:
        try:
            cache = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        if key in cache:
            return bool(cache[key]["foreign"]), str(cache[key].get("reason") or "") + " (캐시)"
    foreign, reason = looks_foreign(frame_path, narration, vision_call=vision_call)
    if not reason.startswith("검사 실패"):
        with _CACHE_LOCK:
            try:
                cache = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cache = {}
            cache[key] = {"foreign": foreign, "reason": reason}
            path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return foreign, reason
