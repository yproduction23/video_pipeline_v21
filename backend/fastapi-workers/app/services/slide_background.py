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


def generate_korean_slide_background(
    prompt_en: str, output_path: str, *, job_id: int, scene_key: str,
) -> None:
    from app.providers.real.image import NanaBananaProvider
    from app.utils.budget import ProviderRequestAudit

    provider = NanaBananaProvider()
    model = "gemini-3-pro-image"
    provider.generate_image(
        prompt=(
            f"Photorealistic documentary photograph: {prompt_en}, in South Korea, "
            "authentic Korean signage and architecture, natural lighting, real camera photo, "
            "news B-roll footage style. No illustration, no cartoon, no anime, no drawing, "
            "no mascot, no character, no face, no person close-up."
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
