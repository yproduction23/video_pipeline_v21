"""슬라이드형 장면용 Kling image-to-video — 스톡 영상이 없는 장면의 폴백.

기존 kling_prompt_builder.py의 템플릿은 전부 "2D 카툰 마스코트" 전용(카메라
고정·실사 금지)이라 실사 슬라이드 배경에는 맞지 않는다. 캐릭터 경로의 템플릿을
건드리지 않기 위해 슬라이드형 전용 프롬프트를 별도 모듈로 둔다.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 2026-10-17 시험: fal 대기열에서 180초를 넘겨 포기한 요청 9/11건이 실패 처리됐다 — 10분까지 기다린다.
SLIDE_KLING_POLL_TIMEOUT_SEC = 600
SLIDE_KLING_NEGATIVE = (
    "text, letters, numbers, digits, watermark, logo, signage, subtitles, captions, "
    "face morphing, body distortion, extra people, cartoon, illustration, "
    "scene change, hard cut, flicker, warped hands"
)


def build_slide_kling_prompt(narration: str, direction_query: str) -> dict:
    """장면 의미(내레이션·검색 키워드)에 맞는 잔잔한 실사 b-roll 모션 프롬프트."""
    context = str(direction_query or "").strip() or "Korean news b-roll"
    prompt = (
        "Photorealistic Korean documentary b-roll. Animate this exact still image with "
        f"subtle, natural, realistic motion that fits the scene ({context}). "
        "A slow gentle camera push-in or slight parallax is allowed. Keep every person's "
        "face, identity, clothing and the overall composition stable — no morphing. "
        "Do NOT create, alter, or animate any text, numbers, signs, logos, charts, or "
        "screen content. No scene cuts, no new objects."
    )
    return {"prompt": prompt, "negative_prompt": SLIDE_KLING_NEGATIVE}


def _upload_image_for_fal(image_path: str) -> str | None:
    if not image_path or not os.path.exists(image_path):
        return None
    try:
        import fal_client

        return fal_client.upload_file(image_path)
    except Exception as exc:  # 네트워크·키 오류는 호출자가 사유로 기록한다
        logger.warning("Fal CDN 업로드 실패 (%s): %s", image_path, exc)
        return None


def generate_slide_kling_clip(
    *,
    video_provider,
    image_path: str,
    output_path: str,
    narration: str,
    direction_query: str,
    duration: float,
    job_id: int,
    scene_key: str,
) -> dict:
    """Kling 클립 1개를 만든다. 실패해도 예외 대신 사유를 담아 반환한다 —
    호출자가 조용히 대체하지 않고 장면에 명시적으로 기록하기 위해서다."""
    from app.services.fal_motion_safety import assess_fal_motion_safety
    from app.utils.budget import ProviderRequestAudit, ProviderRequestBudgetExceeded

    if video_provider is None:
        return {"ok": False, "reason": "video_provider_unavailable"}

    try:
        safety = assess_fal_motion_safety({"scene_id": scene_key}, image_path, scan_image=True)
    except Exception as exc:
        return {"ok": False, "reason": f"motion_safety_check_error: {exc}"}
    if not safety.get("eligible"):
        return {"ok": False, "reason": f"motion_safety_blocked: {safety.get('reasons')}"}

    start_image_url = _upload_image_for_fal(image_path)
    if not start_image_url:
        return {"ok": False, "reason": "fal_upload_failed"}

    prompt = build_slide_kling_prompt(narration, direction_query)
    motion_duration = min(max(int(duration), 1), 5)
    audit = ProviderRequestAudit.for_kling(job_id=job_id, scene_key=scene_key)
    try:
        attempt_id = audit.before_attempt(attempt=1)
    except ProviderRequestBudgetExceeded as exc:
        return {"ok": False, "reason": f"budget_exceeded: {exc}"}
    try:
        asset = video_provider.generate(
            prompt=prompt["prompt"],
            duration=motion_duration,
            output_path=output_path,
            image_path=image_path,
            image_url=start_image_url,
            negative_prompt=prompt["negative_prompt"],
            fal_only=True,
            poll_timeout_sec=SLIDE_KLING_POLL_TIMEOUT_SEC,
        )
    except Exception as exc:
        audit.after_attempt(attempt_id, outcome="failed")
        return {"ok": False, "reason": f"kling_request_failed: {exc}"}
    request_id = (asset.meta or {}).get("fal_request_id") if asset and getattr(asset, "meta", None) else None
    audit.after_attempt(attempt_id, outcome="succeeded", request_id=request_id)

    path = Path(output_path)
    if not path.is_file() or path.stat().st_size <= 1000:
        return {"ok": False, "reason": "kling_output_missing_or_empty"}
    return {"ok": True, "request_id": request_id, "motion_seconds": motion_duration}
