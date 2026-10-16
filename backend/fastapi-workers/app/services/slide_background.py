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


import re

# 스톡 검색용 검색어("stock market crash red screen numbers falling")를 그대로 넘기면 Gemini가
# 전광판·수치판·차트를 그려 넣는다(20번 Job 카나리). 화면·글자·수치를 부르는 단어는 제거한다.
_TEXT_PRONE_WORDS = re.compile(
    r"(?<![A-Za-z])(?:screens?|monitors?|boards?|billboards?|signboards?|signs?|signage|tickers?|"
    r"displays?|led|digital|numbers?|figures?|digits?|charts?|graphs?|candlesticks?|text|words?|"
    r"letters?|headlines?|banners?|newspapers?|documents?|reports?|percent(?:age)?|prices?|"
    r"stocks?|markets?|index|indices|trading|traders?|exchange|kospi|kosdaq|nasdaq|s&p|dow|"
    r"earnings|profits?|revenue|rates?|yields?|bonds?|crash(?:es|ed)?|plunge[sd]?)(?![A-Za-z])",
    re.IGNORECASE,
)
_FALLBACK_SCENE = "busy Korean city street with office workers walking under umbrellas, overcast rainy daylight"


def sanitize_photo_query(prompt_en: str) -> str:
    """Gemini 실사 배경에 넘길 장면 설명에서 글자·수치·화면을 부르는 단어를 뺀다."""
    cleaned = _TEXT_PRONE_WORDS.sub(" ", str(prompt_en or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-—")
    return cleaned if len(cleaned.split()) >= 3 else _FALLBACK_SCENE


def generate_korean_slide_background(
    prompt_en: str, output_path: str, *, job_id: int, scene_key: str,
) -> None:
    from app.providers.real.image import NanaBananaProvider
    from app.utils.budget import ProviderRequestAudit

    provider = NanaBananaProvider()
    model = "gemini-3-pro-image"
    provider.generate_image(
        prompt=(
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
            "streets, buildings, offices, trains and everyday objects instead of screens."
        ),
        output_path=output_path,
        section="news",
        keyword=prompt_en[:30],
        character_style_prompt="none",
        style_locked=True,
        suppress_legacy_style_lock=True,
        image_provider="gemini",
        gemini_model=model,
        gemini_image_size="2K",
        gemini_max_attempts=1,
        gemini_request_audit=ProviderRequestAudit.for_job(job_id=job_id, scene_key=scene_key, model=model),
    )
