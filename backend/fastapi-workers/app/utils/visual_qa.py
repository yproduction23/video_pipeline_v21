"""Optional multimodal review for whether an image serves its finance-video scene."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from app.utils.image_text_contract import contains_financial_number, visible_text_contract_result
from app.utils.scene_visual_quality_contract import build_scene_visual_quality_contract

logger = logging.getLogger(__name__)
VISUAL_QA_MODEL = "gemini-3.7-flash"
VISUAL_QA_POLICY_VERSION = 19
_VISUAL_QA_GEMINI_OPEN_UNTIL = 0.0
_VISUAL_QA_GEMINI_COOLDOWN_SECONDS = 600.0


def _gemini_visual_qa_available() -> bool:
    """한 번 확인된 전송 장애를 장면마다 다시 기다리지 않는다."""
    return time.monotonic() >= _VISUAL_QA_GEMINI_OPEN_UNTIL


def _open_gemini_visual_qa_circuit() -> None:
    global _VISUAL_QA_GEMINI_OPEN_UNTIL
    _VISUAL_QA_GEMINI_OPEN_UNTIL = time.monotonic() + _VISUAL_QA_GEMINI_COOLDOWN_SECONDS


def _image_sha256(image_path: Path) -> str:
    """검수 결과가 동일한 최종 래스터에 대한 것인지 확인한다."""
    digest = hashlib.sha256()
    with image_path.open("rb") as image_file:
        for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_review_policy_compatible(review: dict[str, Any]) -> bool:
    """공통 품질 하한선까지 판정한 현재 정책 결과만 재사용한다."""
    version = int(review.get("policy_version") or 0)
    return version == VISUAL_QA_POLICY_VERSION


def _encode_review_image(image_path: Path) -> str:
    """원본은 건드리지 않고 의미 검사용 전송 사본만 가볍게 만든다."""
    with Image.open(image_path) as source:
        prepared = source.convert("RGB")
        prepared.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        prepared.save(buffer, "JPEG", quality=84, optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def _post_visual_review(payload: dict[str, Any], api_key: str):
    """연결 단절·429·서버 오류만 한 번 재시도하고 계약 오류는 즉시 반환한다."""
    response = None
    for attempt in range(2):
        try:
            response = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{VISUAL_QA_MODEL}:generateContent",
                params={"key": api_key}, json=payload, timeout=(10, 25),
            )
        except (requests.Timeout, requests.ConnectionError):
            if attempt == 0:
                time.sleep(1.0)
                continue
            raise
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 0:
                time.sleep(1.0)
                continue
        return response
    return response


def _claude_visual_review(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Gemini 연결 장애 때만 같은 이미지·지시를 Claude JSON 판독으로 보완한다."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import anthropic

        content: list[dict[str, Any]] = []
        parts = ((payload.get("contents") or [{}])[0].get("parts") or [])
        for part in parts:
            if part.get("text"):
                content.append({"type": "text", "text": str(part["text"])})
            inline = part.get("inlineData") or {}
            if inline.get("data"):
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": str(inline.get("mimeType") or "image/jpeg"),
                        "data": str(inline["data"]),
                    },
                })
        response = anthropic.Anthropic(api_key=api_key).messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1400,
            temperature=0,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(getattr(block, "text", "") for block in response.content)
        start, end = text.find("{"), text.rfind("}")
        return _parse_json(text[start:end + 1]) if start >= 0 and end > start else None
    except Exception as exc:
        logger.warning("Claude visual QA fallback failed: %s", exc)
        return None


def assess_local_edit_preservation(
    source_image_path: str,
    candidate_image_path: str,
    *,
    allowed_change_categories: list[str] | None = None,
) -> dict[str, Any]:
    """표적 재생성이 실패 범위 밖의 구도·얼굴·소품을 바꿨는지 비교한다."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        return {"passed": False, "status": "unavailable", "warning": "visual_qa_no_gemini_key"}
    source = Path(source_image_path)
    candidate = Path(candidate_image_path)
    if not source.is_file() or not candidate.is_file():
        return {"passed": False, "status": "unavailable", "warning": "local_edit_image_missing"}

    categories = list(allowed_change_categories or [])
    instruction = (
        "You are auditing a localized image correction. IMAGE 1 is the rejected source frame and IMAGE 2 is the corrected candidate. "
        "The candidate may change only the listed failed categories. It must preserve the same storyboard and visual identity, not merely a similar scene. "
        "Compare character face construction and expression, body and pose, camera, crop, foreground and background object inventory, object positions, "
        "stage architecture, palette, lighting, linework, and overall composition. Text-surface cleanup may remove malformed writing and may replace its "
        "paper texture, but the repaired patch must look like a clean native surface with no conspicuous blur blob, smeared ghost glyph, muddy halo, or melted edge. "
        "It must not introduce or remove unrelated objects. A newly added laptop, monitor, board, prop, person, or changed table layout is a failure. "
        "Small anti-aliasing and texture differences are acceptable. Return JSON only with keys: composition_preserved, camera_and_crop_preserved, "
        "character_face_preserved, character_pose_preserved, background_preserved, unrelated_props_preserved, style_preserved, only_requested_changes, "
        "repaired_surface_clean, no_blur_smear_or_ghosting "
        "(booleans); added_unrequested_objects, removed_unrequested_objects, changed_unrequested_regions (string arrays); preservation_score (0-100); "
        "decision ('accept' or 'reject'); reason (short Korean text).\n"
        f"Allowed failed categories: {json.dumps(categories, ensure_ascii=False)}"
    )
    payload = {
        "contents": [{"parts": [
            {"text": instruction},
            {"inlineData": {"mimeType": "image/jpeg", "data": _encode_review_image(source)}},
            {"inlineData": {"mimeType": "image/jpeg", "data": _encode_review_image(candidate)}},
        ]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    response = None
    if _gemini_visual_qa_available():
        try:
            response = _post_visual_review(payload, api_key)
        except (requests.Timeout, requests.ConnectionError) as exc:
            _open_gemini_visual_qa_circuit()
            logger.warning("Gemini local-edit QA network failure; 10분간 Claude 검수 사용: %s", exc)
        if response is not None and (response.status_code == 429 or response.status_code >= 500):
            _open_gemini_visual_qa_circuit()
    verdict = None
    if response is not None and response.status_code == 200:
        parts = (response.json().get("candidates") or [{}])[0].get("content", {}).get("parts") or []
        verdict = _parse_json(next((part.get("text") for part in parts if part.get("text")), ""))
    elif response is None or response.status_code == 429 or response.status_code >= 500:
        verdict = _claude_visual_review(payload)
    if not verdict:
        status = response.status_code if response is not None else "network"
        return {"passed": False, "status": "unavailable", "warning": f"local_edit_qa_unavailable:{status}"}
    required = (
        "composition_preserved", "camera_and_crop_preserved", "character_face_preserved",
        "character_pose_preserved", "background_preserved", "unrelated_props_preserved",
        "style_preserved", "only_requested_changes",
    )
    if any(str(category).startswith("text_") for category in categories):
        required = (*required, "repaired_surface_clean", "no_blur_smear_or_ghosting")
    passed = (
        all(bool(verdict.get(key)) for key in required)
        and float(verdict.get("preservation_score", 0) or 0) >= 75
        and str(verdict.get("decision") or "").lower() == "accept"
    )
    return {"passed": passed, "status": "completed", **verdict}


def _review_priority(scene: dict[str, Any]) -> int:
    profile = scene.get("image_profile") or {}
    direction = scene.get("art_direction") or {}
    if profile.get("tier") == "pro":
        return 100
    if direction.get("overlay_strategy") in {"market_chart", "headline_card"}:
        return 80
    return 10


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def assess_visual_alignment(scenes: list[dict[str, Any]], *, enabled: bool, max_scenes: int) -> dict[str, Any]:
    """장면 의미·정확 문자·캐릭터·해부학을 같은 최종 프레임에서 검수한다.

    재생성 비용은 이 함수가 쓰지 않는다. 실패 유형과 교정 대상을 장면 메타에
    남겨 기존 재생성 동작이 전체 화풍을 바꾸지 않고 해당 범주만 고치게 한다.
    """
    report: dict[str, Any] = {"enabled": enabled, "reviewed": [], "skipped": [], "warnings": []}
    if not enabled:
        report["warnings"].append("visual_qa_disabled")
        return report
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        report["warnings"].append("visual_qa_no_gemini_key")
        return report

    eligible = [scene for scene in scenes if Path(str(scene.get("final_image_path") or scene.get("image_path") or "")).exists()]
    eligible.sort(key=_review_priority, reverse=True)
    # 5분 영상의 모든 장면을 검수할 수 있도록 기존 24장 하드캡을 제거한다.
    # 운영자가 명시한 상한만 따르며 0 이하는 전체 장면을 뜻한다.
    configured_limit = int(max_scenes)
    review_limit = len(eligible) if configured_limit <= 0 else min(len(eligible), configured_limit)
    for scene in eligible[:review_limit]:
        index = int(scene.get("index", 0))
        try:
            image_path = Path(str(scene.get("final_image_path") or scene["image_path"]))
            image_sha256 = _image_sha256(image_path)
            cached_review = scene.get("visual_qa_review")
            if (
                isinstance(cached_review, dict)
                and _cached_review_policy_compatible(cached_review)
                and str(cached_review.get("image_sha256") or "") == image_sha256
            ):
                report["reviewed"].append(dict(cached_review))
                continue
            encoded = _encode_review_image(image_path)
            direction = scene.get("art_direction") or {}
            render_contract = scene.get("v5_render_contract") or {}
            text_contract = scene.get("image_text_contract") or {}
            visual_quality_contract = scene.get("scene_visual_quality_contract") or build_scene_visual_quality_contract(
                scene,
                text_contract=text_contract or None,
            )
            scene_variables = visual_quality_contract.get("scene_variables") or {}
            deterministic_surface_contract = visual_quality_contract.get("deterministic_surface") or {}
            approved_texts = list(text_contract.get("approved_texts") or scene.get("screen_texts") or [])
            bubble_policy = str(text_contract.get("bubble_policy") or scene.get("bubble_policy") or "unplanned")
            bubble_allowed = bubble_policy in {"allowed", "required"} or bool(text_contract.get("bubble_allowed") or scene.get("allow_speech_bubble"))
            character_required = bool(direction.get("character_required", True))
            directed_spec = scene.get("scene_spec") or {}
            expected_wardrobe = str(
                directed_spec.get("character_costume")
                or direction.get("wardrobe")
                or render_contract.get("scene_wardrobe")
                or ""
            ).strip()
            wardrobe_strict = bool(
                directed_spec.get("wardrobe_strict")
                or direction.get("wardrobe_strict")
                or render_contract.get("wardrobe_strict")
            )
            expected_composition = str(
                direction.get("composition")
                or directed_spec.get("composition")
                or ""
            ).strip()
            expected_camera = str(
                directed_spec.get("camera")
                or direction.get("camera")
                or ""
            ).strip()
            expected_position = str(
                direction.get("character_position")
                or directed_spec.get("character_position")
                or ""
            ).strip()
            expected_occupancy = direction.get("character_occupancy") or directed_spec.get("frame_occupancy")
            composition_strict = bool(
                directed_spec.get("composition_strict")
                or direction.get("composition_strict")
                or render_contract.get("composition_strict")
            )
            surface_plan = list(text_contract.get("surface_plan") or scene.get("screen_text_plan") or [])
            holographic_text_surface_allowed = any(
                str(item.get("surface_style") or item.get("material") or "").strip().lower()
                in {"holographic", "transparent_hologram", "translucent_hologram"}
                for item in surface_plan if isinstance(item, dict)
            )
            allowed_text_occurrences: dict[str, int] = {}
            required_approved_texts: list[str] = []
            for item in surface_plan:
                if not isinstance(item, dict):
                    continue
                value = str(item.get("text") or "").strip()
                if value:
                    allowed_text_occurrences[value] = allowed_text_occurrences.get(value, 0) + 1
                    if bool(item.get("required") or item.get("required_exact")):
                        required_approved_texts.append(value)
            for value in approved_texts:
                allowed_text_occurrences.setdefault(str(value), 1)
            required_props = list(direction.get("required_props") or scene.get("required_props") or [])
            avoid_number_panel_only = bool(direction.get("avoid_number_panel_only") or scene.get("avoid_number_panel_only"))
            forbid_white_halo = bool(direction.get("forbid_white_sticker_halo") or scene.get("forbid_white_sticker_halo"))
            instruction = (
                "You are the visual quality editor for a Korean finance explainer. "
                "Inspect the full-resolution image, including stylized Korean glyphs. Score it against the narration and visual brief. "
                "Reject generic gold/rocket/fire imagery when it is not specifically justified. "
                "Require original 2D editorial-comic treatment: variable black ink outlines, cel shading, colorful layered context; "
                "flag glossy 3D/toy, empty dark studio, or generic mascot treatment. "
                "Transcribe every visible Korean or English word and number exactly as seen, including malformed pseudo-text. "
                "Do not transcribe decorative parallel lines, blank cheque lines, signatures, chart strokes, texture marks, or axis ticks as words or numbers. "
                "A vertical bar, bracket, colon, bullet, or border drawn immediately around an otherwise exact approved string is decorative framing, not a spelling error. "
                "The existence of text is NOT a failure. Exact non-numeric words already present verbatim in the narration are also grounded and may appear. "
                "Approved strings are optional unless they are also listed as required. Korean and English are both allowed, and model-written readable text is not a failure by itself. "
                "A text failure means a required approved string is missing or misspelled, a financial number is not in the approved list, or visible wording is malformed pseudo-text, "
                "a misspelling, a contradictory/unsupported factual claim, or an unrelated intrusive label. Short scene-relevant role and context labels such as PRESS, MARKET, OUTLOOK, "
                "DIVIDEND, or their accurate Korean equivalents are allowed even when they are not verbatim narration substrings. Do not call a coherent relevant label unexpected merely because it is unlisted. "
                "Inspect whether the mascot remains within the broad character range visible across the Job52-style channel scenes: a round gold coin species, embossed rim, "
                "compact cartoon anatomy, expressive readable face, and compatible 2D ink language. This is a family-range check, not a fixed model-sheet check. "
                "Do not require one frozen expression, blush, nose, eye openness, costume, or presenter face. "
                "2026-09-29 channel operator decision: do not require a separated white sclera, a warm-brown iris, or any specific "
                "eye-layer structure. A solid dark eye, a simple dot eye, a catchlight-only eye, or any eye style consistent with the "
                "channel's 2D ink linework is acceptable and must NOT be flagged as a failure or lower the score. Flag only a genuinely "
                "different species/design or a nearly featureless accidental emoji face. "
                "Do not penalize scene-appropriate changes in expression, eye openness, brows, mouth, costume, headwear, pose, scale, or placement, "
                "except when this scene supplies an explicit composition, camera, position, or occupancy plan. "
                "Apply one shared acceptance floor without forcing identical scene direction. The face construction must be intentional and readable at the shown scale; "
                "closed or simplified eyes are allowed only when coherent whites, brows, mouth, and acting make the expression deliberate. Judge wardrobe and expression by whether "
                "they perform this scene's supplied role, not by pixel similarity to one reference. Require the same original 2D editorial-comic family, narration-specific causal story, "
                "and information-rich economic setting in every scene. A deterministic typography surface must remain a bounded subordinate in-world prop rather than dominating the frame. "
                "Detect a genuinely different character design and inspect MAIN GOLD-COIN MASCOT anatomy separately from supporting people. A scene costume may wrap modestly below the coin rim, "
                "so do not reject that accepted Job52-style costume extension by itself. Reject when the coin is no longer the dominant unified head-and-upper-body identity, when a narrow-necked independent human trunk appears, "
                "or when visible mascot legs are long human proportions instead of compact legs roughly half the coin diameter or shorter. Audience members may have their own hands, "
                "and a foreground person's arm naturally cropped by the frame is not an extra mascot limb. Flag only extra, detached, fused, or contradictory limbs belonging to the main mascot. "
                "Detect speech or thought bubbles. "
                "Also flag when the main idea is merely one giant number/word panel with little physical economic storytelling. "
                "Count every occurrence of each approved string; exact spelling does not permit extra duplicates beyond the allowed occurrence map. "
                "Return JSON only with these keys: scene_match, finance_specificity, composition, style_adherence, text_integrity, "
                "character_identity, repetition_risk (all 0-100); visible_texts (string array); unexpected_or_malformed_texts (string array); "
                "missing_approved_texts (string array); approved_text_occurrence_counts (object string-to-integer); "
                "composition_plan_match (boolean); speech_bubble_present (boolean); anatomy_pass (boolean, legacy whole-scene field); "
                "extra_limbs_or_hands (boolean, legacy whole-scene field); main_mascot_anatomy_pass (boolean); main_mascot_extra_limbs_or_hands (boolean); "
                "wardrobe_match (boolean); wardrobe_role_match (boolean); face_identity_match (boolean); face_construction_quality_pass (boolean); "
                "separate_sclera_region_visible (boolean; not required by this channel's floor — always return true unless the eye construction is otherwise broken); "
                "warm_brown_iris_region_visible (boolean; not required by this channel's floor — always return true unless the eye construction is otherwise broken); "
                "expression_role_match (boolean); style_family_match (boolean); scene_information_density_match (boolean); "
                "coin_disc_dominant_silhouette (boolean; true when the round coin remains the dominant unified head-and-upper-body identity even if a scene costume wraps modestly below it); "
                "compact_leg_proportion_match (boolean; true when each visible mascot leg is compact and roughly half the coin diameter or shorter, not a long human leg); "
                "costume_wrap_preserves_coin_dominance (boolean; true when an allowed coat, suit, robe, or tailcoat extension does not become a narrow-necked independent human trunk or displace the coin identity); "
                "deterministic_surface_scale_match (boolean); scene_causal_story_match (boolean); "
                "minimal_dot_eye_face (boolean; true only for a nearly featureless accidental emoji, not expressive shock pupils); detached_translucent_text_card_present "
                "(boolean; true only for a floating glass/UI card without physical mounting; an opaque wall sign, monitor, chalkboard, paper, gauge, or stage board is false); "
                "detached_unmounted_text_card_present (boolean; true only when the text rectangle has no visible frame attachment, wall attachment, stand, legs, poles, truss, desk/device body, or other physical support); "
                "text_surface_has_visible_physical_mount_or_frame (boolean; true for any opaque board or monitor with a visible enclosing frame plus wall mount, stand, legs, poles, truss, desk/device body, or set attachment. "
                "A large white or black board is still physically mounted when these supports are visible, and must not be called detached merely because it is front-facing or typography is large); "
                "text_surface_is_visibly_transparent_or_holographic (boolean); text_surface_integrated_with_set (boolean); visual_medium_violation (boolean; true only for actual photorealism, 3D render, photo collage, or mixed medium; "
                "smooth cel shading, glow, clean gradients, or a white comic cutout outline alone are false); semantic_contradiction (boolean; true only when the primary visual "
                "objects/action contradict or are unrelated to the narration, not for a non-strict camera or staging preference); "
                "obvious_localized_blur_or_smear_artifact_present (boolean; true only for a conspicuous muddy blur patch, smeared ghost lettering, melted panel edge, or inpainting blob; "
                "normal glow, motion streaks, depth-of-field, smooth cel gradients, and intentionally soft distant background are false); "
                "unexpected_or_ambiguous_props (string array; list any visible prop whose identity or causal role cannot be inferred from the narration and set, especially a handheld object that can be mistaken for a weapon, drill, radio, or unrelated tool); "
                "malformed_or_factual_text_error (boolean; true only when at least one listed unexpected string is visibly misspelled, pseudo-text, an unsupported financial value, "
                "a wrong entity, or a contradictory claim; false for coherent scene-relevant labels); "
                "unsupported_numeric_or_factual_values (string array containing exact visible strings only when the image asserts them as a real financial datum or real-world factual claim "
                "that is absent from the approved scene evidence. Do not include illustrative non-financial numbers such as a fictional test grade used as a metaphor, room or page numbers, "
                "clock faces, calculator buttons, decorative algebra, background equations, non-data chart strokes, or approved deterministic values. A number is not unsupported merely "
                "because its exact glyphs are absent from the narration; first decide whether it claims a real fact); unexpected_text_regions "
                "(array of {text:string,bbox:[x,y,width,height],confidence:0-1} using normalized 0..1 coordinates, tightly covering each actual malformed or unsupported string only); "
                "white_sticker_halo_present (boolean); missing_required_props (string array); "
                "number_panel_only (boolean); decision ('accept' or 'review'); reason (short Korean text).\n\n"
                f"Narration: {scene.get('text') or ''}\n"
                f"Approved exact visible strings: {json.dumps(approved_texts, ensure_ascii=False)}\n"
                f"Required exact visible strings: {json.dumps(required_approved_texts, ensure_ascii=False)}\n"
                f"Allowed occurrence counts: {json.dumps(allowed_text_occurrences, ensure_ascii=False)}\n"
                f"Detached translucent or holographic text card allowed: {holographic_text_surface_allowed}\n"
                f"Speech bubble policy: {bubble_policy}\n"
                f"Scene family: {direction.get('family') or ''}\n"
                f"Required setting: {direction.get('setting') or ''}\n"
                f"Required props (strict only): {', '.join(required_props)}\n"
                f"Scene-specific wardrobe role: {expected_wardrobe or scene_variables.get('wardrobe') or ''}\n"
                f"Scene-specific expression role: {scene_variables.get('emotion') or ''}\n"
                f"Scene-specific action role: {scene_variables.get('action') or ''}\n"
                f"Shared visual quality contract: {json.dumps(visual_quality_contract, ensure_ascii=False)}\n"
                f"Wardrobe strict: {wardrobe_strict}\n"
                f"Explicit composition, empty means unrestricted: {expected_composition}\n"
                f"Explicit camera, empty means unrestricted: {expected_camera}\n"
                f"Explicit character position, empty means unrestricted: {expected_position}\n"
                f"Explicit character frame occupancy, empty means unrestricted: {expected_occupancy or ''}\n"
                f"Composition strict: {composition_strict}\n"
                f"Character required: {character_required}"
            )
            payload = {
                "contents": [{"parts": [
                    {"text": instruction},
                    {"inlineData": {"mimeType": "image/jpeg", "data": encoded}},
                ]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingLevel": "low"},
                },
            }
            response = None
            if _gemini_visual_qa_available():
                try:
                    response = _post_visual_review(payload, api_key)
                except (requests.Timeout, requests.ConnectionError) as exc:
                    _open_gemini_visual_qa_circuit()
                    logger.warning(
                        "Gemini visual QA network failure for scene %s; 10분간 Claude 검수 사용: %s",
                        index, exc,
                    )
                if response is not None and (response.status_code == 429 or response.status_code >= 500):
                    _open_gemini_visual_qa_circuit()
            verdict = None
            if response is not None and response.status_code == 200:
                parts = (response.json().get("candidates") or [{}])[0].get("content", {}).get("parts") or []
                verdict = _parse_json(next((part.get("text") for part in parts if part.get("text")), ""))
            elif response is None or response.status_code == 429 or response.status_code >= 500:
                verdict = _claude_visual_review(payload)
            if not verdict:
                status = response.status_code if response is not None else "network"
                report["warnings"].append(f"visual_qa_unavailable:{status}:{index}")
                continue
            score_keys = ["scene_match", "finance_specificity", "composition", "style_adherence", "text_integrity"]
            if character_required:
                score_keys.append("character_identity")
            scores = [float(verdict.get(key, 0)) for key in score_keys]
            score = round(sum(scores) / len(scores))
            hard_failures: list[str] = []
            visible_texts = [
                str(value).strip() for value in verdict.get("visible_texts") or []
                if str(value).strip()
            ]
            visible_text_keys = {
                "".join(ch for ch in value.casefold() if ch.isalnum())
                for value in visible_texts
            }
            approved_text_keys = {
                "".join(ch for ch in str(value).casefold() if ch.isalnum())
                for value in approved_texts
            }
            model_flagged_texts = [
                str(value).strip() for value in verdict.get("unexpected_or_malformed_texts") or []
                if str(value).strip()
                and "".join(ch for ch in str(value).casefold() if ch.isalnum()) in visible_text_keys
                and "".join(ch for ch in str(value).casefold() if ch.isalnum()) not in approved_text_keys
            ]
            text_result = visible_text_contract_result(visible_texts, scene)
            unsupported_numeric_values = [
                str(value).strip() for value in verdict.get("unsupported_numeric_or_factual_values") or []
                if str(value).strip()
                and "".join(ch for ch in str(value).casefold() if ch.isalnum()) in visible_text_keys
                and "".join(ch for ch in str(value).casefold() if ch.isalnum()) not in approved_text_keys
            ]
            verdict["unexpected_or_malformed_texts"] = list(dict.fromkeys([
                *model_flagged_texts,
                *unsupported_numeric_values,
            ]))
            if (
                unsupported_numeric_values
                or (
                    model_flagged_texts
                    and (
                        bool(verdict.get("malformed_or_factual_text_error"))
                        or float(verdict.get("text_integrity", 0) or 0) < 70
                    )
                )
            ):
                hard_failures.append("text_unexpected_or_malformed")
            visible_compact = {
                "".join(ch for ch in str(value).casefold() if ch.isalnum())
                for value in verdict.get("visible_texts") or []
            }
            missing_required = [
                value for value in required_approved_texts
                if "".join(ch for ch in str(value).casefold() if ch.isalnum()) not in visible_compact
            ]
            verdict["missing_approved_texts"] = missing_required
            if missing_required:
                hard_failures.append("text_missing_approved")
            occurrence_counts = {
                value: sum(
                    1 for visible in visible_texts
                    if "".join(ch for ch in visible.casefold() if ch.isalnum())
                    == "".join(ch for ch in str(value).casefold() if ch.isalnum())
                )
                for value in allowed_text_occurrences
            }
            verdict["approved_text_occurrence_counts"] = occurrence_counts
            # 승인 대본의 금융 수치는 단순 참고 문구가 아니라 결정론 렌더러가
            # 최종 프레임에 반드시 남겨야 하는 사실 계약이다. 모델 검수가
            # missing_required_props로만 보고한 경우에도 누락을 하드 실패시킨다.
            missing_approved_numeric = [
                value for value in approved_texts
                if contains_financial_number(str(value))
                and occurrence_counts.get(value, 0) == 0
            ]
            verdict["missing_approved_numeric_texts"] = missing_approved_numeric
            if missing_approved_numeric:
                hard_failures.append("text_missing_approved_numeric")
            # 같은 승인 라벨이 서로 다른 실제 소품 두 곳에 반복되는 것은 Job 52의
            # 비교 장면 문법에 포함된다. 명시적 strict 계약이 없으면 2회까지 허용하고,
            # 그보다 많은 도배만 중복 오류로 막는다.
            occurrence_strict = bool(scene.get("screen_text_occurrence_strict")) or any(
                isinstance(item, dict) and item.get("max_occurrences") is not None
                for item in surface_plan
            )
            if any(
                occurrence_counts.get(value, 0) > (allowed if occurrence_strict else max(2, allowed))
                for value, allowed in allowed_text_occurrences.items()
            ):
                hard_failures.append("text_approved_duplicate")
            if bool(verdict.get("speech_bubble_present")) and not bubble_allowed:
                hard_failures.append("speech_bubble_unapproved")
            main_anatomy_pass = verdict.get("main_mascot_anatomy_pass")
            if main_anatomy_pass is None:
                main_anatomy_pass = verdict.get("anatomy_pass", False)
            main_extra_limbs = verdict.get("main_mascot_extra_limbs_or_hands")
            if main_extra_limbs is None:
                main_extra_limbs = verdict.get("extra_limbs_or_hands", False)
            if character_required and not bool(main_anatomy_pass):
                hard_failures.append("character_anatomy")
            if character_required and bool(main_extra_limbs):
                hard_failures.append("character_extra_limbs")
            if character_required and not bool(verdict.get("coin_disc_dominant_silhouette", False)):
                hard_failures.append("character_coin_silhouette")
            if character_required and not bool(verdict.get("compact_leg_proportion_match", False)):
                hard_failures.append("character_leg_proportion")
            if character_required and not bool(verdict.get("costume_wrap_preserves_coin_dominance", False)):
                hard_failures.append("character_costume_wrap")
            if character_required and wardrobe_strict and expected_wardrobe and not bool(verdict.get("wardrobe_match", False)):
                hard_failures.append("character_wardrobe")
            if (
                character_required
                and (expected_wardrobe or scene_variables.get("wardrobe"))
                and not bool(verdict.get("wardrobe_role_match", False))
            ):
                hard_failures.append("character_wardrobe")
            if character_required and not bool(verdict.get("face_construction_quality_pass", False)):
                hard_failures.append("character_face_quality")
            # 2026-09-29 사용자 결정: 눈 구조(공막·홍채 분리) 요구사항은 채널
            # 운영자가 필요 없다고 판단해 하드 실패 대상에서 제외한다. 다른
            # 캐릭터 해부학·의상·정체성 검사는 그대로 유지한다.
            if (
                character_required
                and (scene_variables.get("emotion") or scene_variables.get("action"))
                and not bool(verdict.get("expression_role_match", False))
            ):
                hard_failures.append("character_expression_role")
            if (
                character_required
                and not bool(verdict.get("face_identity_match", False))
                and float(verdict.get("character_identity", 0) or 0) < 60
            ):
                hard_failures.append("character_face_identity")
            if (
                character_required
                and bool(verdict.get("minimal_dot_eye_face"))
                and float(verdict.get("character_identity", 0) or 0) < 60
            ):
                hard_failures.append("character_face_simplified")
            if (
                approved_texts
                and not holographic_text_surface_allowed
                and (
                    bool(verdict.get("detached_unmounted_text_card_present"))
                    or (
                        bool(verdict.get("detached_translucent_text_card_present"))
                        and bool(verdict.get("text_surface_is_visibly_transparent_or_holographic"))
                        and not bool(verdict.get("text_surface_has_visible_physical_mount_or_frame"))
                    )
                )
                and not bool(verdict.get("text_surface_integrated_with_set"))
            ):
                hard_failures.append("text_surface_detached_translucent_card")
            if character_required and forbid_white_halo and bool(verdict.get("white_sticker_halo_present")):
                hard_failures.append("character_white_sticker_halo")
            if required_props and verdict.get("missing_required_props"):
                hard_failures.append("scene_required_props_missing")
            if avoid_number_panel_only and bool(verdict.get("number_panel_only")):
                hard_failures.append("number_panel_only")
            if (
                bool(verdict.get("visual_medium_violation"))
                and float(verdict.get("style_adherence", 0) or 0) < 45
            ):
                hard_failures.append("style_severe_mismatch")
            if not bool(verdict.get("style_family_match", False)):
                hard_failures.append("style_family_mismatch")
            if not bool(verdict.get("scene_information_density_match", False)):
                hard_failures.append("scene_information_density")
            if (
                bool(deterministic_surface_contract.get("required"))
                and not bool(verdict.get("deterministic_surface_scale_match", False))
            ):
                hard_failures.append("deterministic_surface_oversized")
            if not bool(verdict.get("scene_causal_story_match", False)):
                hard_failures.append("scene_semantic_underexplained")
            if (
                bool(verdict.get("semantic_contradiction"))
                and float(verdict.get("scene_match", 0) or 0) < 50
            ):
                hard_failures.append("scene_semantic_mismatch")
            if bool(verdict.get("obvious_localized_blur_or_smear_artifact_present")):
                hard_failures.append("local_edit_blur_smear_artifact")
            if any(str(value).strip() for value in verdict.get("unexpected_or_ambiguous_props") or []):
                hard_failures.append("unexpected_or_ambiguous_prop")
            if (
                composition_strict
                and (expected_composition or expected_camera or expected_position or expected_occupancy)
                and "composition_plan_match" in verdict
                and not bool(verdict.get("composition_plan_match"))
            ):
                hard_failures.append("scene_composition_plan_mismatch")
            retry = bool(hard_failures) or score < 78 or str(verdict.get("decision", "")).lower() == "review"
            if retry and not hard_failures:
                hard_failures.append("visual_quality_floor")
            report["reviewed"].append({
                "index": index,
                "score": score,
                "retry_recommended": retry,
                "failure_categories": hard_failures,
                "reason": str(verdict.get("reason") or ""),
                "raw": verdict,
                "policy_version": VISUAL_QA_POLICY_VERSION,
                "image_sha256": image_sha256,
            })
        except Exception as exc:
            logger.warning("visual QA failed for scene %s: %s", index, exc)
            report["warnings"].append(f"visual_qa_error:{index}")

    reviewed_indices = {item["index"] for item in report["reviewed"]}
    report["skipped"] = [int(scene.get("index", 0)) for scene in eligible if int(scene.get("index", 0)) not in reviewed_indices]
    report["score"] = round(sum(item["score"] for item in report["reviewed"]) / len(report["reviewed"])) if report["reviewed"] else None
    if report["reviewed"]:
        from app.utils.scene_accuracy_metrics import aggregate_scene_accuracy, visual_qa_item_results

        report["accuracy_metrics"] = aggregate_scene_accuracy([
            {
                "scene_key": f"scene:{int(item['index']):02d}",
                "items": visual_qa_item_results(item.get("failure_categories") or []),
            }
            for item in report["reviewed"]
        ])
    return report
