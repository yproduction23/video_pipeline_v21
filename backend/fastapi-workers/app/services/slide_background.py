"""슬라이드형 시각 모드의 AI 배경 생성 — Pixabay에 맞는 영상이 없을 때의
최후 폴백.

2026-10-14/15 사용자 결정: 이 모드는 실사 스타일이어야 한다.
NanaBananaProvider의 기존 경로(character_style_prompt="background_only"
포함)가 BACKGROUND_ONLY_STYLE/STYLE_LOCK 상수를 강제로 주입해
"no photorealism"을 못박아 두고 있어(기존 마스코트 채널의 하우스 스타일
고정 장치), 그 세 가지 주입 경로를 모두 끈다:
character_style_prompt="none"(캐릭터 스타일 주입 차단) +
style_locked=True(화풍 미지정 기본 프롬프트로 안 덮어씀) +
suppress_legacy_style_lock=True(STYLE_LOCK 카툰 상수 미주입).
"""
from __future__ import annotations


import logging
import re

from app.utils.claude_ledger import claude_create
logger = logging.getLogger(__name__)

# 스톡 검색용 검색어("stock market crash red screen numbers falling")를 그대로 넘기면 Gemini가
# 전광판·수치판·차트를 그려 넣는다(20번 Job 카나리). 화면·글자·수치를 부르는 단어는 제거한다.
_TEXT_PRONE_WORDS = re.compile(
    r"(?<![A-Za-z])(?:screens?|monitors?|boards?|billboards?|signboards?|signs?|signage|tickers?|"
    r"displays?|led|digital|numbers?|figures?|digits?|charts?|graphs?|candlesticks?|text|words?|"
    r"letters?|headlines?|banners?|newspapers?|documents?|reports?|percent(?:age)?|prices?|"
    r"stocks?|markets?|index|indices|trading|traders?|exchange|kospi|kosdaq|nasdaq|s&p|dow|"
    r"earnings|profits?|revenue|rates?|yields?|bonds?|crash(?:es|ed)?|plunge[sd]?|"
    # 추상어는 Gemini가 만화·인포그래픽으로 그리게 만든다(2026-10-18 작업 20: 'concept' → 영어 글자가 든 만화 마스코트).
    r"concepts?|ideas?|metaphors?|illustrations?|cartoons?|infographics?|diagrams?|icons?|symbols?|abstract)(?![A-Za-z])",
    re.IGNORECASE,
)
_FALLBACK_SCENE = "busy Korean city street with office workers walking under umbrellas, overcast rainy daylight"


def sanitize_photo_query(prompt_en: str) -> str:
    """Gemini 실사 배경에 넘길 장면 설명에서 글자·수치·화면을 부르는 단어를 뺀다."""
    cleaned = _TEXT_PRONE_WORDS.sub(" ", str(prompt_en or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-—")
    return cleaned if len(cleaned.split()) >= 3 else _FALLBACK_SCENE


STRICT_PHOTO_SUFFIX = (
    " Real documentary photograph taken with a camera ONLY. Absolutely no cartoon, no illustration, no drawing, no 3D render, "
    "no mascot, no character, no infographic, no poster and no readable text, letters or numbers anywhere in the image."
)

_GATE_SYSTEM = (
    "너는 영상 배경 이미지 검수자다. 이미지가 (1) 실제 카메라로 찍은 듯한 사진인지(만화·일러스트·3D 캐릭터·마스코트·인포그래픽·포스터가 아님), "
    "(2) 쓰면 안 되는 글자가 없는지 확인한다. has_text=true는 다음뿐이다: 읽을 수 있는 숫자·가격·퍼센트·시세·그래프 눈금(전광판·모니터·"
    "인쇄물 포함), 화면 중앙이나 큰 크기의 제목·문구, 실제 회사·언론사·브랜드 이름. 배경에 작게 보이는 거리 간판·상점 이름·표지판 같은 "
    "부수적인 한글·영어 글자는 글자로 치지 않는다(2026-10-19 운영자 결정: 간판 글자는 중요하지 않다). 흐릿해서 읽히지 않는 글자도 아니다. "
    '출력은 JSON만: {"photo": true|false, "has_text": true|false, "reason": "한 줄"}'
)


class BackgroundRejected(RuntimeError):
    """AI 배경이 실사 사진이 아니거나 읽히는 글자가 있어 쓰지 않는다."""


def _default_vision_call(system: str, text: str, images: list[bytes]) -> str:
    import base64
    import os

    from anthropic import Anthropic

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(images[0]).decode()}},
               {"type": "text", "text": text}]
    response = claude_create(purpose="slide_background_gate", 
        model="claude-sonnet-4-6", max_tokens=200, system=system, messages=[{"role": "user", "content": content}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def assess_background(image_path: str, *, vision_call=None) -> tuple[bool, str]:
    """만든 AI 배경이 글자 없는 실사 사진인지 확인한다. 확인 자체가 실패하면 통과시키지 않는다(사유에 '확인 실패')."""
    import json

    try:
        from PIL import Image
        import io

        with Image.open(image_path) as image:
            thumb = image.convert("RGB")
            thumb.thumbnail((1024, 1024))
            buffer = io.BytesIO()
            thumb.save(buffer, "PNG")
        raw = (vision_call or _default_vision_call)(_GATE_SYSTEM, "이 배경 이미지를 검수해라. JSON만 반환.", [buffer.getvalue()])
        match = re.search(r"\{[\s\S]*\}", str(raw or ""))
        data = json.loads(match.group(0) if match else raw)
        ok = bool(data.get("photo")) and not bool(data.get("has_text"))
        return ok, str(data.get("reason") or "")
    except Exception as exc:
        return False, f"배경 검수 확인 실패: {exc}"


def ensure_photo_background(make, output_path: str, *, prompt: str, vision_call=None) -> None:
    """make(prompt, path, strict)로 배경을 만들고 검수한다. 통과하지 못하면 엄격한 프롬프트로 한 번 더 만들고, 그래도 아니면 BackgroundRejected."""
    reason = ""
    for strict in (False, True):
        make(prompt, output_path, strict)
        ok, reason = assess_background(output_path, vision_call=vision_call)
        if ok:
            return
        logger.warning("AI 배경 검수 불합격(%s회차, 엄격=%s): %s", 2 if strict else 1, strict, reason)
    raise BackgroundRejected(reason)


def generate_korean_slide_background(
    prompt_en: str, output_path: str, *, job_id: int, scene_key: str, strict: bool = False,
) -> None:
    from app.providers.real.image import NanaBananaProvider
    from app.utils.budget import ProviderRequestAudit

    provider = NanaBananaProvider()
    model = "gemini-3-pro-image"
    provider.generate_image(
        prompt=(
            (STRICT_PHOTO_SUFFIX if strict else "") +
            f"Photorealistic documentary photograph: {sanitize_photo_query(prompt_en)}, in South Korea, "
            "authentic Korean architecture and street scenery, natural lighting, real camera photo, "
            "news B-roll footage style. No illustration, no cartoon, no anime, no drawing, "
            "no mascot, no character, no face, no person close-up. "
            # 2026-10-16 20번 Job 재현: 장면 문맥(내레이션)을 그대로 넘기자 Gemini가 문장을 전광판에
            # 그려 넣거나(1번) 가짜 KOSPI 수치판(6번)·"코스피 급락 2300.50"(19번)을 만들어 자막과 이중이
            # 됐다. 화면의 글자·숫자는 후편집(Pillow/FFmpeg)만 만든다 — 이미지에는 어떤 판독 가능한
            # 글자·숫자도 넣지 않는다(수치 데이터를 생성형 이미지로 만들지 않는 프로젝트 규칙).
            "The picture must contain NO legible text of any kind: no letters, no Korean or English "
            "words, no numbers, no captions, no subtitles, no watermarks, no timestamps or REC overlays, "
            "no stock tickers, no price boards, no electronic signboards or LED screens, no charts or "
            "graphs on screens, no newspapers or documents with readable writing. Shop signs and "
            "street signs, if present, must be blurred or unreadable. The scene context is only for "
            "choosing what to photograph; never write it into the image. Show the mood through people, "
            "streets, buildings, offices, trains and everyday objects instead of screens. "
            # 2026-10-19 9034 재현: 거리·상가 사진은 간판 글자가 읽혀 검수에서 대부분 불합격했다 — 처음부터 간판이 안 나오는 구도(넓은 전경·실내·사물 클로즈업)를 고른다.
            "Prefer compositions without storefronts or street-level signage: wide skylines seen from a distance, interiors, "
            "desks, hands, objects, trains and bridges."
        ),
        output_path=output_path,
        section="news",
        keyword=prompt_en[:30],
        character_style_prompt="none",
        style_locked=True,
        suppress_legacy_style_lock=True,
        allow_account_fallback=False,        # Gemini 계정 오류일 때 Fal.ai로 대체하지 않는다(검수에서 버려지고 비용만 나간다)
        image_provider="gemini",
        gemini_model=model,
        gemini_image_size="2K",
        gemini_max_attempts=1,
        gemini_request_audit=ProviderRequestAudit.for_job(job_id=job_id, scene_key=scene_key, model=model),
    )
