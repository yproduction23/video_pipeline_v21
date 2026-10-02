"""
이미지 생성 워커 v3 — 배경 레이어 분리 + 캐릭터 합성 (Sprint 2)

핵심 변경:
  [S2-1] 배경 전용 생성 모드:
    - character_style_prompt="background_only"를 AI 프로바이더에 전달
    - 캐릭터 없는 순수 배경 이미지 생성
  [S2-3] 이중 레이어 합성 파이프라인:
    - channel_poses_dir 제공 시: 포즈별 투명 PNG + 배경 이미지를 FFmpeg overlay로 합성
    - 설정 단계에서 아직 포즈 라이브러리가 없으면 기존 캐릭터 일체형 모드로 폴백

비용: Fal.ai/Gemini API 콜 비용 + FFmpeg 로컬 코딩
"""
import os
import io
import json
import hashlib
import math
import logging
import random
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from PIL import Image, ImageFilter, ImageStat
from app.utils.process_manager import is_job_stopped
from app import runtime_config
from app.utils.quality_gate import enrich_scene_plan, assess_images, persist_quality_report
from app.utils.art_direction import direct_scenes, plan_image_quality_tiers, compile_editorial_prompt, assess_art_diversity, flag_archetype_content_mismatch
from app.utils.scene_entity_binder import bind_scene_entities, compute_grounding_score
from app.utils.scene_visual_quality_contract import (
    build_scene_visual_quality_contract,
    scene_visual_quality_prompt,
)
from app.utils.visual_qa import assess_local_edit_preservation, assess_visual_alignment
from app.utils import gemini_batch
from app.pipeline.scene_director import SceneDirector, SceneSpec, fallback_spec
from app.providers.real.prompt_builder import build_prompt
from app.v5.scene.runtime_contract import (
    attach_v5_scene_contracts,
    is_v5_final_lane_scene,
    prompt_for_scene,
    v5_provider_options,
)
from app.v5.scene.longform_visual_mix import apply_longform_visual_mix
from app.v5.providers.gemini_provider import _load_default_references, select_contextual_reference_paths
from app.utils.budget import ProviderRequestAudit, plan_preflight, record_cost, write_preflight
from app.utils.image_request_control import ImageRequestHeld, write_request_review
from app.utils.intro_motion import infer_total_duration_seconds, select_intro_motion_scene_indices, scene_duration_seconds
from app.utils.narration_contract import (
    bind_caption_chunks_to_scenes,
    normalize_scene_sentence_boundaries,
    build_script_contract,
    verify_tts_against_script_contract,
)
from app.utils.operational_contract_audit import build_operational_contract_audit
from app.utils.gemini_pressure import gemini_pressure
from app.utils.image_job_lock import acquire_image_job_lock, release_image_job_lock
from app.utils.retry_policy import classify_image_error, error_signature
from app.utils.image_text_contract import (
    build_scene_text_contract,
    contains_financial_number,
    detected_deterministic_texts,
    redact_financial_values_for_visual_direction,
    redact_financial_values_from_model_prompt,
    redact_unapproved_entity_literals_from_model_prompt,
    require_sanitized_generated_text_prompt,
    visible_text_contract_result,
)
from app.utils.character_integrity_contract import apply_character_integrity_contract
from app.utils.scene_screen_text_planner import attach_scene_screen_texts
from app.services.info_surface.info_scene_templates import select_template
from app.services.fal_motion_safety import (
    assess_fal_motion_safety,
    fal_motion_metadata_block_reasons,
    inspect_visible_text,
)
from app.services.final_frame_text_integrity import require_final_frame_text_integrity
from app.services.fal_motion_intent import build_fal_motion_preflight

logger = logging.getLogger(__name__)


ARCHETYPE_COMPOSITION = {
    # scene_type → 구도 힌트 (무엇을 그릴지가 아니라 어떤 구도인지만)
    "character_hero":      "wide dramatic stage, character spotlight center-right",
    "news_context":        "discovery moment composition, something revealed from behind",
    "data_visual":         "control room or dashboard background, multiple screens",
    "character_explainer": "classroom or lecture hall background, pointer indicated",
    "comparison":          "split stage left vs right, two distinct zones",
    "takeaway":            "podium announcement stage, single strong focal point",
    "general":             "situational metaphor background, no specific constraints",
    "metric":              "data room atmosphere, glowing displays",
    "graph":               "trend visualization environment, flowing data streams",
    "intro":               "opening title card environment, dramatic establishing shot",
    "conclusion":          "closing summary stage, warm resolution lighting",
}

from app.utils.entity_english_map import REAL_ENTITY_ENGLISH_MAP, get_entity_english_name

STYLE_SUFFIX = (
    "original 2D Korean finance comic, bold ink outlines, cel shading, "
    "at most one round gold coin mascot, no secondary mascot, contextual background people allowed when the scene needs them"
)

# LLM이 같은 승인 장면을 조금 다르게 표현해도 재개 실행이 이미 승인된 PNG를
# 전부 다시 과금하지 않도록, 생성 문장 자체가 아니라 안정적인 장면 계약에
# 지문을 묶는다. 프롬프트 정책을 의도적으로 바꾸면 이 버전을 올린다.
IMAGE_LINEAGE_FINGERPRINT_VERSION = 12
PROMPT_CACHE_POLICY_VERSION = 8
PROMPT_CACHE_COMPATIBLE_VERSIONS = (6,)


def _reference_asset_fingerprints(paths: list[str]) -> list[dict[str, str]]:
    """참조 파일 내용이 바뀌면 같은 경로여도 재개 지문이 달라지게 한다."""
    fingerprints: list[dict[str, str]] = []
    for value in paths:
        path = Path(value)
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
        fingerprints.append({"path": str(path), "sha256": digest})
    return fingerprints


def _stable_image_lineage_fingerprint(
    ctx: dict,
    *,
    character_style_prompt: str,
    character_reference_paths: list[str],
    use_composite: bool,
    character_poses_dir: str | None,
    lora_model_id: str | None,
    lora_trigger_word: str | None,
    lora_scale: float | None,
) -> str:
    """비결정적인 LLM 시각 문장이 아닌 승인 장면 계약으로 재개 지문을 만든다."""
    payload = {
        "fingerprint_version": IMAGE_LINEAGE_FINGERPRINT_VERSION,
        "approved_scene_text": ctx.get("text") or "",
        "approved_prompt_en": ctx.get("prompt_en") or "",
        "prompt_ko": ctx.get("prompt_ko") or "",
        "screen_texts": list(ctx.get("screen_texts") or []),
        "style_profile": ctx.get("style_profile"),
        "image_profile": ctx["image_profile"],
        "character_style_prompt": character_style_prompt,
        "character_references": _reference_asset_fingerprints(character_reference_paths),
        "use_composite": use_composite,
        "character_poses_dir": character_poses_dir,
        "lora_model_id": lora_model_id,
        "lora_trigger_word": lora_trigger_word,
        "lora_scale": lora_scale,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def _image_prompt_cache_key(
    *,
    index: int,
    narration: str,
    scene_type: str,
    visual_mode: str,
    character_required: bool,
    core_entities: list,
    core_figures: list,
    screen_texts: list[str] | None = None,
    policy_version: int = PROMPT_CACHE_POLICY_VERSION,
) -> str:
    """같은 승인 장면의 영문 시각 프롬프트를 재호출하지 않는 안정 키다."""
    payload = {
        "policy_version": int(policy_version),
        "index": index,
        "narration": narration,
        "scene_type": scene_type,
        "visual_mode": visual_mode,
        "character_required": character_required,
        "core_entities": core_entities,
        "core_figures": core_figures,
        # 장면의 콘텐츠·구도 프롬프트와 화면 문자 계약은 계보가 다르다.
        # 승인 screen_texts가 보완됐다는 이유만으로 콘텐츠 중심 프롬프트를
        # Claude에 53번 다시 쓰게 하지 않는다. 정확 문구는 매 렌더 직전
        # _bounded_text_generation_prompt가 최신 계약으로 결합한다.
        "screen_texts": [],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


class GeneratedImageTextDetectedError(RuntimeError):
    """현재 장면 허용 목록 밖의 글자가 검출되어 표면 교정이 필요함."""


class GeneratedImageVisualContractError(RuntimeError):
    """장면별 비전 검수에서 실제로 확인된 범주만 교정해야 함."""

    def __init__(self, message: str, review: dict):
        super().__init__(message)
        self.review = review


class LocalEditPreservationError(GeneratedImageVisualContractError):
    """표적 교정이 실패 범위 밖의 기존 장면 요소까지 바꾼 경우다."""


class DeterministicSurfaceMissingError(GeneratedImageVisualContractError):
    """승인 수치를 안전하게 넣을 빈 실제 소품 표면을 찾지 못한 경우다."""


class FinalImageValidationError(GeneratedImageVisualContractError):
    """공급자 호출 자체는 성공했지만 최종 파일이 손상·누락된 경우다.

    2026-10-01 발견: 이전에는 평범한 RuntimeError였고, classify_image_error가
    메시지를 인식하지 못해 "unclassified RuntimeError"로 비재시도 처리돼
    NonRetryableImageGenerationError로 배치 전체가 즉시 중단됐다. 그 결과 scene 6
    하나의 파일 손상 때문에 전혀 무관한 scene 7·8까지 시도조차 못 했다.
    GeneratedImageVisualContractError를 상속해 다른 장면별 콘텐츠 거부와 같은
    경로(장면별 재시도 → 소진 시 ImageRequestHeld)를 타게 한다.
    """


class VisualQaUnavailableError(RuntimeError):
    """검수 연결 오류 때문에 같은 이미지를 다시 과금하면 안 되는 상태."""


def _base_prompt_from_scene_spec(scene_spec_dict: object) -> str | None:
    """scene_spec(SceneDirector 지시)이 있으면 배치 경로와 같은 build_prompt()로
    구체적 의상·동작·소품·카메라 프롬프트를 만든다. 없거나 재구성에 실패하면
    None을 반환해 호출부가 범용 compile_editorial_prompt로 대체하게 한다.
    """
    if not isinstance(scene_spec_dict, dict) or not scene_spec_dict.get("scene_id"):
        return None
    try:
        from app.pipeline.scene_director import SceneSpec
        from app.providers.real.prompt_builder import build_prompt as build_director_prompt
        return build_director_prompt(SceneSpec(**scene_spec_dict))
    except Exception:
        logger.warning("scene_spec으로 프롬프트 생성 실패, 범용 프롬프트로 대체", exc_info=True)
        return None


def _persist_single_scene_visual_qa_review(job_dir: Path, index: int, review: dict) -> None:
    """개별 장면 재생성의 통과 판정을 배치 경로와 같은 visual_qa_cache.json에 남긴다.

    2026-10-01 job 12 scene 0/4/5 재현: generate_single_scene()은 성공해도 이
    캐시에 쓰지 않았다. 그 결과 다음 전체 배치 실행이 "기존 PNG 검증"에서
    캐시 히트를 찾지 못해 이미 고친 장면을 Spring의 원본(미보정) 데이터로
    처음부터 다시 생성해, 수동으로 고친 장면이 매번 같은 문제로 되돌아갔다.
    job_id·scene index에 무관한 공통 경로다.
    """
    if not isinstance(review, dict) or not review.get("image_sha256"):
        return
    cache_path = job_dir / "visual_qa_cache.json"
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}
    cache[str(index)] = review
    staged = cache_path.with_suffix(".tmp")
    staged.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, cache_path)


def _clear_scene_from_request_review(job_dir: Path, job_id: int, index: int) -> None:
    """개별 장면 재생성이 성공하면 배치 경로와 같은 조립 차단 게이트를 갱신한다.

    generate_single_scene()은 write_request_review()를 직접 호출하지 않아,
    보류(needs_review)된 장면을 개별 재생성 UI로 고쳐도 image_request_review.json이
    그대로 남아 assert_request_review_cleared()가 조립을 계속 막았다. 이 함수는
    job_id·scene index에 무관한 공통 경로이며, 게이트 파일이 없거나 해당 장면이
    보류 목록에 없으면 아무것도 하지 않는다.
    """
    path = job_dir / "image_request_review.json"
    if not path.exists():
        return
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return
    if not isinstance(review, dict):
        return
    remaining = [
        scene for scene in (review.get("scenes") or [])
        if int(scene.get("index", -1)) != index
    ]
    write_request_review(job_dir, job_id, remaining)


def _sanitize_unplanned_prompt_structure(prompt: str, text_contract: dict) -> str:
    """과거 캐시의 빈 패널·임의 말풍선 지시를 장면 계약에 맞게 교정한다."""
    cleaned = str(prompt or "")
    if not text_contract.get("bubble_allowed"):
        # 문장 뒤에 덧붙은 임의 말풍선 절만 제거하고 앞의 차트·인물 행동은
        # 보존한다. 말풍선이 문장 전체인 경우에는 그 문장만 삭제한다.
        cleaned = re.sub(
            r"\s+(?:despite|with|while)\s+[^.]*?speech\s+bubbles?[^.]*?(?=\.|$)",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"(?:^|(?<=[.!?]))\s*[^.!?]*speech\s+bubbles?[^.!?]*[.!?]?",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )

    has_deterministic_text = bool(text_contract.get("deterministic_texts"))
    has_approved_text = bool(text_contract.get("approved_texts"))
    if has_approved_text:
        # 과거 장면 프롬프트의 거대 모니터 지시는 공통 표면 예산(16~24%)과
        # 정면으로 충돌한다. 문자 표면을 뜻하는 경우에만 크기 형용사를
        # 중립화하고, 터빈·항만·공장 같은 의미 전달 소품의 크기는 보존한다.
        cleaned = re.sub(
            r"\b(?:massive|giant|oversized|huge|enormous|large)\s+"
            r"((?:central\s+|wall(?:-mounted)?\s+)?(?:monitor|screen|display|board|panel)(?:\s+wall)?)\b",
            r"bounded \1",
            cleaned,
            flags=re.IGNORECASE,
        )
    if has_deterministic_text:
        # 문자 제거기가 따옴표 속 수치를 중립화할 때 쓰는 일반 장면 표현은
        # 비수치 씬에서는 유용하지만, 같은 위치에 결정론 문자를 합성해야 하는
        # 씬에서는 뒤의 "아무 표시 없는 고른 내부" 계약과 충돌한다. 실제
        # 물리 소품은 보존하되 그 한 표면 내부만 차분한 재질로 명확히 만든다.
        cleaned = cleaned.replace(
            "an unlettered scene-integrated visual surface with non-linguistic shapes",
            "an unlettered scene-integrated physical surface with a calm uniform interior",
        )
        # 과거 Claude 프롬프트가 수치마다 별도의 빈 플래카드·카드를
        # 추가하도록 만든 문장을 먼저 제거한다. 장면에 이미 존재하는 메인
        # 모니터·계기·제품 면을 쓰는 것이 원칙이며, 안전한 면이 실제로 없으면
        # 후단 표면 검출기가 해당 후보를 거절하고 장면 전체를 다시 구성한다.
        cleaned = re.sub(
            r"(?:^|(?<=[.!?]))\s*(?:one|a|an)\s+[^.!?]{0,80}?\b(?:blank|empty|unlettered)\b"
            r"[^.!?]{0,100}?\b(?:placard|panel|card|safe\s*zone|display)\b"
            r"[^.!?]{0,100}?\b(?:reserved|awaits?|waiting|waits?)\b[^.!?]*[.!?]?",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
    surface_adjective = (
        "unlettered opaque scene-integrated"
        if has_deterministic_text
        else "detailed scene-integrated"
    )
    cleaned = re.sub(r"\bblank\b", surface_adjective, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:reserved|awaits?|waiting|waits?)\s+(?:for\s+)?(?:later\s+)?(?:post-production\s+)?"
        r"(?:values?|figures?|text|labels?|labeling|value\s+insertion|critical\s+figures?)\b",
        (
            "carrying non-linguistic diagram structure and preserving one small in-perspective area "
            "for exact deterministic typography"
            if has_deterministic_text
            else "as a natural part of the environment"
        ),
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


def _bounded_text_generation_prompt(
    prompt: str,
    *,
    retry: bool = False,
    retry_feedback: dict | None = None,
    audit_target: dict | None = None,
) -> str:
    """승인 문자열은 보존하고 비승인 문자열만 이미지 프롬프트에서 제거한다."""
    cleaned = str(prompt or "")
    text_contract = build_scene_text_contract(audit_target)
    visual_quality_contract = build_scene_visual_quality_contract(
        audit_target,
        text_contract=text_contract,
    )
    generated_texts = list(text_contract["generated_texts"])
    cleaned, financial_redaction = redact_financial_values_from_model_prompt(
        cleaned,
        deterministic_values=text_contract["deterministic_texts"],
    )
    cleaned, entity_redaction = redact_unapproved_entity_literals_from_model_prompt(
        cleaned,
        core_entities=(audit_target or {}).get("core_entities") if isinstance(audit_target, dict) else [],
        allowed_texts=generated_texts,
    )
    cleaned, report = require_sanitized_generated_text_prompt(
        cleaned,
        allowed_texts=generated_texts,
    )
    cleaned = _sanitize_unplanned_prompt_structure(cleaned, text_contract)
    if isinstance(audit_target, dict):
        audit_target["image_text_contract"] = text_contract
        audit_target["scene_visual_quality_contract"] = visual_quality_contract
        # archetype 밀도 레이어는 텍스트 계약과 별도 계보로도 노출한다.
        # 그래야 운영 원장에서 문자 정확성 실패와 구성 밀도 실패를 섞지 않는다.
        audit_target["composition_density_profile"] = dict(
            visual_quality_contract.get("composition_density_profile") or {}
        )
        audit_target["image_prompt_text_contract"] = {
            **report,
            "financial_redaction": financial_redaction,
            "entity_redaction": entity_redaction,
            "retry_contract": bool(retry),
            "retry_failure_categories": list((retry_feedback or {}).get("failure_categories") or []),
        }
    deterministic = list(text_contract["deterministic_texts"])
    if generated_texts:
        surface_plan = list(text_contract.get("surface_plan") or [])
        plan_by_text = {
            str(item.get("text") or ""): item
            for item in surface_plan
            if isinstance(item, dict) and str(item.get("text") or "")
        }
        wording_rules = []
        for position, value in enumerate(generated_texts, start=1):
            item = plan_by_text.get(value) or {}
            surface = str(
                item.get("surface_description")
                or item.get("surface")
                or "an existing subordinate scene-native physical surface"
            )
            purpose = str(item.get("purpose") or "scene information")
            max_occurrences = max(1, int(item.get("max_occurrences") or 1))
            wording_rules.append(
                f"Approved wording {position} is {value}. Render those Korean characters exactly, no more than "
                f"{max_occurrences} time, as {purpose} on {surface}. Do not render the English instruction words, "
                "the item number, punctuation, brackets, braces, quotation marks, or field labels."
            )
        placement_note = " " + " ".join(wording_rules)
        if surface_plan:
            placement_note += (
                " Unless an approved wording rule explicitly declares a holographic material, render it on a solid opaque "
                "scene-mounted monitor, opaque wall-mounted information board, machine gauge, or painted or engraved prop face that already belongs to the set."
            )
        else:
            placement_note += (
                " Keep each wording visually subordinate on an existing solid opaque scene-native monitor, opaque wall-mounted information board, "
                "machine gauge, or painted or engraved prop face only when it helps the narration. Do not create a new board, panel, title card, "
                "oversized sign, safe zone, detached glass card, translucent floating UI, or hologram for it."
            )
        suffix = (
            " FINAL SCENE-LOCAL TEXT CONTRACT: only the approved Korean wording described below may be readable. "
            "Copy it exactly without translation, abbreviation, missing characters, extra characters, or pseudo-text."
            f"{placement_note} The script-specific physical objects, action, and causal relationship determine the visual hierarchy. "
            "Do not force all scenes into one giant board or one fixed typography layout. "
            "No unapproved fake ticker, invented company, logo, watermark, or secondary number. "
            "On documents, books, cheques, certificates, newspapers, and equipment labels, do not add microprint, serial numbers, dates, currency symbols, or filler lettering outside the approved wording. "
            "A detached translucent or holographic text card is forbidden unless the explicit scene-local surface plan requests that exact material."
        )
    elif deterministic:
        suffix = (
            " FINAL SCENE-LOCAL TEXT CONTRACT: this scene has no model-generated string. Do not add readable Korean or English, letters, digits, "
            "pseudo-text, logos, watermark, fake tickers, or invented labels. Preserve all narration-essential objects, action, diagrams, color, depth, and background detail. "
            "Exactly one storyboard-essential physical screen, gauge, board, product face, or document must contain a clearly framed, axis-aligned, calm uniform interior "
            "with no marks, symbols, line art, reflections, hands, character overlap, or decoration inside it. This surface must already belong to the scene and must not look like "
            "a detached card, title panel, floating UI, hologram, or generic empty safe zone. Other props remain detailed, but do not create a second calm framed surface."
        )
    else:
        suffix = (
            " FINAL SCENE-LOCAL TEXT CONTRACT: this scene has no approved generated string. Do not add readable "
            "Korean or English, letters, digits, pseudo-text, logos, watermark, fake tickers, or invented labels. "
            "Keep boards, monitors, packages, documents, and gauges visually rich with material, diagrams, color blocks, "
            "and non-linguistic shapes; do not remove the props or simplify the environment. Books, documents, cheques, "
            "certificates, newspapers, and labels must use clearly non-linguistic blank lines or graphic blocks, never microprint or serial numbers."
        )
    if deterministic:
        deterministic_plan = [
            item for item in (text_contract.get("surface_plan") or [])
            if isinstance(item, dict) and str(item.get("text") or "") in deterministic
        ]
        reserved_surfaces = list(dict.fromkeys(
            str(item.get("surface_description") or item.get("surface_id") or item.get("surface") or "").strip()
            for item in deterministic_plan
            if str(item.get("surface_description") or item.get("surface_id") or item.get("surface") or "").strip()
        ))
        reserved_surface_note = (
            " The later deterministic typography is bound specifically to "
            + "; ".join(reserved_surfaces)
            + ". Build that exact scene object and keep only its planned typography region calm, axis-aligned, fully visible, and clear of every hand or character."
            if reserved_surfaces else ""
        )
        suffix += (
            " Use a calm region inside an existing storyboard-essential physical screen, gauge, board, product face, or document for later deterministic typography. "
            "Do not draw, imitate, translate, or guess any of the withheld deterministic strings or values yourself. Do not add a second placard, detached card, empty safe zone, "
            "or generic panel merely to hold the later typography."
            + reserved_surface_note
        )
    suffix += scene_visual_quality_prompt(visual_quality_contract)
    if retry:
        categories = set((retry_feedback or {}).get("failure_categories") or ["text_unexpected_or_malformed"])
        reason = str((retry_feedback or {}).get("reason") or "").strip()
        suffix += (
            " PREVIOUS RENDER NEEDS A SCENE-LOCAL CORRECTION. Preserve every element that was not named by the failed review: "
            "the scene's economic story, successful composition, useful props, information density, palette, and original 2D comic rendering."
        )
        if any(category.startswith("text_") for category in categories):
            suffix += (
                " Correct only the offending text-bearing surfaces. Copy approved strings exactly and once; replace every other word, "
                "microprint, serial number, date, currency symbol, or pseudo-letter on papers, books, cheques, certificates, monitors, and labels "
                "with non-linguistic blank lines or graphic marks. Do not remove screens, boards, diagrams, arrows, background detail, or the physical props."
            )
        if categories & {"character_anatomy", "character_extra_limbs"}:
            suffix += " Correct only the main mascot anatomy: one connected body, exactly two arms and two hands, with no detached or duplicate limb."
        if categories & {"character_face_identity", "character_face_simplified", "character_face_quality"}:
            suffix += (
                " Correct only the mascot face into the broad round gold-coin 2D family shown across the channel scene references: embossed rim and an intentional readable expression at this scale. "
                "Preserve this scene's emotion; deliberate closed or simplified action eyes are valid when brows, mouth, whites, and acting remain coherent. "
                "Do not copy one reference expression, iris treatment, blush, costume, pose, or action."
            )
        if "character_expression_role" in categories:
            suffix += " Correct only the face and body acting so they clearly perform this scene's supplied emotion and action role; do not replace them with one default presenter expression."
        if "character_wardrobe" in categories:
            suffix += " Correct only the wardrobe so it serves this scene's supplied role and location; do not impose one recurring suit, hat, police uniform, or laboratory coat across unrelated scenes."
        if "speech_bubble_unapproved" in categories:
            suffix += " Remove only the unplanned speech or thought bubble and restore the background behind it."
        if "text_surface_detached_translucent_card" in categories:
            suffix += " Move only the approved wording onto an existing opaque in-world surface; remove the detached translucent card."
        if "number_panel_only" in categories:
            suffix += " Keep any approved value subordinate and rebuild the emphasis around the narration's physical objects, action, and causal relationship."
        if "scene_required_props_missing" in categories:
            suffix += " Add only the explicitly required missing scene props without changing the character design or overall art style."
        if "deterministic_surface_missing" in categories:
            suffix += (
                " Recompose one storyboard-essential existing object in this exact setting so its physical monitor, gauge, product face, board, or document includes "
                "a calm readable region occupying roughly 6 to 14 percent of the frame. Keep that object causally connected to the narration, fully visible, and clear of every character and hand. "
                "Do not add a second placard, floating card, title card, giant board, hologram, or empty studio composition."
            )
        if "deterministic_surface_oversized" in categories:
            suffix += (
                " Shrink only the text-bearing physical surface to the scene contract's frame budget and restore the released area with narration-specific props, mechanisms, depth, and causal action. "
                "Do not remove the physical surface or write withheld deterministic values yourself."
            )
        if "local_edit_blur_smear_artifact" in categories:
            suffix += (
                " Render a fresh clean 2D candidate with crisp intentional ink contours on foreground and background figures and props. "
                "Do not use muddy depth-of-field face blobs, smeared ghost lettering, melted panel edges, or inpainting-like blur patches."
            )
        if "text_generated_deterministic_numeric" in categories:
            suffix += (
                " Generate a fresh base illustration without writing any approved financial number yourself. "
                "Keep the storyboard-essential physical screen or board, but fill its future numeric area only with calm material tone and non-linguistic chart structure for deterministic post-production."
            )
        if "scene_composition_plan_mismatch" in categories:
            suffix += " Correct only the explicit scene composition, camera, or placement mismatch while preserving all successful content."
        if "scene_semantic_mismatch" in categories:
            suffix += " Replace only the off-topic visual metaphor with concrete objects and actions that directly explain this scene's narration."
        if "scene_semantic_underexplained" in categories:
            suffix += " Strengthen only the narration's missing causal relationship using concrete scene-specific objects and action; avoid generic finance decoration."
        if "scene_information_density" in categories:
            suffix += " Restore useful Job-52-like information density outside the bounded text surface with relevant layered props, mechanisms, context, and depth; do not fill space with random labels or numbers."
        if "style_family_mismatch" in categories:
            suffix += " Restore the shared original 2D editorial-comic family while preserving this scene's unique location, palette, costume, and emotion."
        if "visual_quality_floor" in categories:
            suffix += " Raise the weak reviewed dimensions above the common acceptance floor without changing scene variables that already passed."
        if "style_severe_mismatch" in categories:
            suffix += " Restore the original Job-52-like 2D editorial comic language: bold variable ink, cel shading, colorful layered finance context; no glossy 3D or empty studio."
        if reason:
            suffix += f" Review evidence to correct: {reason[:500]}"
    cleaned, integrity_report = apply_character_integrity_contract(cleaned + suffix, audit_target)
    if isinstance(audit_target, dict):
        audit_target["character_integrity_contract"] = integrity_report
    return cleaned


# 내부 테스트·오래된 호출의 이름 호환성을 유지하되 의미는 허용 목록 계약이다.
_strict_textless_generation_prompt = _bounded_text_generation_prompt


def _restore_raw_before_deterministic_overlay(scene: dict) -> bool:
    """재개 시 모든 결정론 수치 씬은 합성 전 원본으로 되돌린다."""
    if is_v5_final_lane_scene(scene):
        return True
    return bool(build_scene_text_contract(scene).get("deterministic_texts"))


def _requires_full_scene_regeneration(review: dict | None) -> bool:
    """국소 편집을 누적하면 악화되는 화질 결함인지 판정한다."""
    categories = set((review or {}).get("failure_categories") or [])
    return bool(categories & {
        "local_edit_blur_smear_artifact",
        "text_generated_deterministic_numeric",
        # 파일 자체가 손상·누락된 경우 손댈 유효한 소스가 없으므로 국소 편집이
        # 아니라 항상 전체 재생성이어야 한다.
        "final_image_invalid",
    })


def _image_provider_for_attempt(
    default_provider: str, *, attempt: int, max_retries: int, force_fal_last_resort: bool,
) -> str:
    """2026-09-29 사용자 결정: 메인 공급자(기본 OpenAI)가 같은 장면에서 표적
    교정 예산을 다 쓰거나 재시도 예산의 마지막 한 번에 도달하면, 포기하기
    전에 이 장면만 Fal.ai로 마지막 시도를 해본다."""
    if force_fal_last_resort or attempt == max_retries - 1:
        return "fal"
    return default_provider


def _audit_scene_quality(scene: dict, image_path: str, *, outcome: str, category: str) -> None:
    """유료 호출 없이 원본 이미지 해시와 QA 결과를 같은 요청 원장에 연결한다."""
    if scene.get("_request_job_id") is None:
        return
    ProviderRequestAudit.for_job(
        job_id=scene["_request_job_id"], scene_key=f"image:{scene['index']}",
        model=str((scene.get("image_profile") or {}).get("model") or "gemini-3-pro-image"),
    ).record_quality(image_path=image_path, raw_path=scene.get("_request_raw_path"), outcome=outcome, category=category)


def _inspect_generated_textless_image(scene: dict, image_path: str, *, ocr_rows=None) -> dict:
    try:
        result = _inspect_generated_textless_image_impl(scene, image_path, ocr_rows=ocr_rows)
    except (GeneratedImageTextDetectedError, GeneratedImageVisualContractError):
        _audit_scene_quality(scene, image_path, outcome="rejected", category="text_qa")
        raise
    _audit_scene_quality(scene, image_path, outcome="passed", category="text_qa")
    return result


def _inspect_generated_textless_image_impl(
    scene: dict,
    image_path: str,
    *,
    ocr_rows=None,
) -> dict:
    """OCR 결과가 현재 장면 허용 문자열 밖으로 새지 않았는지 확인한다."""
    if _article_evidence_path(scene):
        return {"passed": True, "status": "skipped_article_evidence", "visible_tokens": []}
    text_contract = build_scene_text_contract(scene)
    generated_ocr_policy = scene.get("generated_text_ocr_policy") or {}
    require_exact_generated = bool(
        isinstance(generated_ocr_policy, dict)
        and generated_ocr_policy.get("require_all_approved") is True
    )
    expected_generated = list(text_contract.get("generated_texts") or []) if require_exact_generated else []
    inspection = inspect_visible_text(
        image_path,
        ocr_rows=ocr_rows,
        expected_texts=expected_generated,
        targeted_exact_retry=bool(expected_generated and ocr_rows is None),
        # 생성 모델에 짧은 승인 문구를 맡긴 엄격 레인은 여러 모니터의
        # 접두·접미 이탈자까지 원본 표면별 OCR로 찾아야 한다.
        surface_text_recall=bool(expected_generated and ocr_rows is None),
    )
    status = str(inspection.get("status") or "failed")
    tokens = list(inspection.get("visible_tokens") or [])
    if status != "completed":
        raise NonRetryableImageGenerationError(f"생성 이미지 OCR을 실행할 수 없음: status={status}")
    missing_generated = list(inspection.get("missing_generated_texts") or [])
    if missing_generated:
        raise GeneratedImageTextDetectedError(
            "현재 장면의 승인 문구 누락: " + ", ".join(missing_generated)
        )
    generated_financial_texts = detected_deterministic_texts(tokens, scene)
    if generated_financial_texts:
        review = {
            "failure_categories": ["text_generated_deterministic_numeric"],
            "reason": "기본 이미지에 생성 모델이 금융 수치를 직접 그렸습니다: "
            + ", ".join(generated_financial_texts),
            "raw": {"generated_deterministic_texts": generated_financial_texts},
        }
        raise GeneratedImageVisualContractError(review["reason"], review)
    result = visible_text_contract_result(
        tokens,
        scene,
        occurrence_counts=inspection.get("surface_occurrence_counts"),
    )
    if not result["passed"]:
        duplicate_summary = (
            " 승인 문구 중복: " + ", ".join(result.get("duplicate_texts") or [])
            if result.get("duplicate_texts") else ""
        )
        raise GeneratedImageTextDetectedError(
            "현재 장면의 문자 계약 위반: 비승인/훼손 문자="
            + ", ".join(result["unexpected_texts"][:8])
            + duplicate_summary
        )
    return {**result, **inspection, "status": status, "visible_tokens": tokens}


def _inspect_generated_visual_image(scene: dict, image_path: str) -> dict:
    try:
        result = _inspect_generated_visual_image_impl(scene, image_path)
    except GeneratedImageVisualContractError:
        _audit_scene_quality(scene, image_path, outcome="rejected", category="visual_qa")
        raise
    except VisualQaUnavailableError:
        _audit_scene_quality(scene, image_path, outcome="unavailable", category="visual_qa")
        raise
    _audit_scene_quality(scene, image_path, outcome="skipped" if result.get("skipped") else "passed", category="visual_qa")
    return result


def _inspect_generated_visual_image_impl(scene: dict, image_path: str) -> dict:
    """최종 저장 전에 한 장씩 의미·문자·얼굴·해부학을 교차 검증한다."""
    if not bool(runtime_config.value("visual_qa_enabled")):
        return {"skipped": True, "reason": "visual_qa_disabled"}
    review_scene = {**scene, "final_image_path": image_path}
    report = assess_visual_alignment([review_scene], enabled=True, max_scenes=1)
    reviewed = list(report.get("reviewed") or [])
    warnings = list(report.get("warnings") or [])
    if warnings or not reviewed:
        detail = ", ".join(str(value) for value in warnings[:4]) or "no_review_result"
        raise VisualQaUnavailableError(f"장면 비전 검수를 완료하지 못함: {detail}")

    review = reviewed[0]
    failures = list(review.get("failure_categories") or [])
    if bool(review.get("retry_recommended")) and not failures:
        failures.append("visual_quality_floor")
    failures = list(dict.fromkeys(failures))
    review["failure_categories"] = failures
    scene["visual_qa_review"] = review
    if failures:
        raise GeneratedImageVisualContractError(
            "장면 비전 계약 위반: " + ", ".join(failures) + f"; {review.get('reason') or ''}",
            review,
        )
    return review


def _ocr_review_reasons(scenes: list[dict]) -> list[str]:
    """OCR 추정 사실은 AUTO 모드에서도 사용자 확인 없이는 확정할 수 없다."""
    reasons: list[str] = []
    for scene in scenes:
        facts = scene.get("verified_facts") or scene.get("facts") or []
        if not isinstance(facts, list):
            continue
        if any(
            isinstance(fact, dict)
            and str(fact.get("confidence") or fact.get("source_type") or fact.get("provenance") or "").lower() == "ocr_estimate"
            for fact in facts
        ):
            reasons.append(f"OCR_ESTIMATE:scene_{scene.get('index', 'unknown')}")
    return reasons


def _resolved_visual_mode(scene: dict) -> str:
    if _article_evidence_path(scene):
        return "article_evidence"
    contract = scene.get("v5_render_contract") or {}
    mode = str(scene.get("visual_mode") or contract.get("visual_mode") or "").strip()
    if mode:
        return mode
    return "archetype_explainer" if (scene.get("art_direction") or {}).get("character_required") else "semantic_illustration"


def _visual_mix_audit(scenes: list[dict]) -> dict:
    counts = Counter(_resolved_visual_mode(scene) for scene in scenes)
    total = len(scenes)
    failures: list[str] = []
    if total >= 9:
        minimum_article = max(1, math.ceil(total * 0.05))
        minimum_generated = max(1, math.ceil(total * 0.25))
        if counts["article_evidence"] < minimum_article:
            failures.append(f"article_evidence:{counts['article_evidence']}<{minimum_article}")
        for mode in ("semantic_illustration", "archetype_explainer"):
            if counts[mode] < minimum_generated:
                failures.append(f"{mode}:{counts[mode]}<{minimum_generated}")
    return {"passed": not failures, "counts": dict(counts), "failures": failures}


def _apply_fal_motion_safety_contract(scenes: list[dict]) -> dict[str, int]:
    """최종 PNG를 검사해 문자·수치가 있는 Fal 후보를 정지 이미지로 바꾼다."""
    selected = sum(1 for scene in scenes if scene.get("use_kling"))
    blocked = 0
    for scene in scenes:
        was_selected = bool(scene.get("use_kling"))
        safety = assess_fal_motion_safety(
            scene,
            str(scene.get("image_path") or ""),
            scan_image=was_selected,
        )
        scene["fal_motion_safety"] = safety
        if was_selected and not safety["eligible"]:
            scene["use_kling"] = False
            blocked += 1
            logger.info(
                "Fal 문자·수치 안전 게이트: scene=%s 정지 이미지로 전환 reasons=%s",
                scene.get("index"),
                safety["reasons"],
            )
    audit = {"selected": selected, "blocked": blocked, "eligible": selected - blocked}
    logger.info("Fal 문자·수치 안전 게이트 완료: %s", audit)
    return audit


def _manual_review_reasons(scenes: list[dict], image_quality: dict | None = None) -> list[str]:
    """의미 검수·구성 다양성·반복도를 통과하지 못하면 자동 확정을 차단한다."""
    reasons = _ocr_review_reasons(scenes)
    for scene in scenes:
        if scene.get("retry_recommended"):
            reasons.append(f"IMAGE_RETRY_RECOMMENDED:scene_{scene.get('index', 'unknown')}")

    quality = image_quality or {}
    semantic = quality.get("semantic_alignment") or {}
    if bool(runtime_config.value("visual_qa_enabled")) and not semantic.get("reviewed"):
        reasons.append("VISUAL_QA_UNAVAILABLE")
    configured_review_limit = int(runtime_config.value("visual_qa_max_scenes"))
    expected_semantic_reviews = (
        len(scenes) if configured_review_limit <= 0
        else min(len(scenes), configured_review_limit)
    )
    if bool(runtime_config.value("visual_qa_enabled")) and len(semantic.get("reviewed") or []) < expected_semantic_reviews:
        reasons.append(
            f"VISUAL_QA_INCOMPLETE:{len(semantic.get('reviewed') or [])}<{expected_semantic_reviews}"
        )
    for warning in semantic.get("warnings") or []:
        if str(warning).startswith(("visual_qa_http_", "visual_qa_error", "visual_qa_invalid_json")):
            reasons.append(f"VISUAL_QA_ERROR:{warning}")
    for warning in (quality.get("art_direction") or {}).get("warnings") or []:
        reasons.append(f"ART_DIRECTION:{warning}")
    mix = quality.get("visual_mix") or _visual_mix_audit(scenes)
    for failure in mix.get("failures") or []:
        reasons.append(f"VISUAL_MIX:{failure}")

    fingerprints = Counter(
        hashlib.sha256(str(scene.get("prompt_en") or scene.get("prompt") or "").encode("utf-8")).hexdigest()
        for scene in scenes if str(scene.get("prompt_en") or scene.get("prompt") or "").strip()
    )
    duplicate_limit = max(3, math.ceil(len(scenes) * 0.10))
    if fingerprints and max(fingerprints.values()) > duplicate_limit:
        reasons.append(f"PROMPT_LAYOUT_REPETITION:{max(fingerprints.values())}>{duplicate_limit}")
    return sorted(set(reasons))


def _review_reasons_including_held_scenes(review_reasons: list[str], held_scenes: list[dict]) -> list[str]:
    """자동 승인 응답이 보류된 장면을 무시하지 않도록 held_scenes도 합친다.

    2026-09-30 job 12 재현: _manual_review_reasons()는 성공적으로 생성된
    장면만 보고 계산되어, 계약을 통과하지 못해 ImageRequestHeld로 보류된
    장면은 이 함수가 전혀 몰랐다. 그 결과 requires_manual_review=false인
    응답이 Spring에 돌아가 AUTO 모드가 보류 장면을 무시한 채 IMAGES 게이트를
    통과시켜 job 상태가 ASSEMBLING으로 넘어갔고, 이후 조립은 별도 게이트에서
    막혔지만 job은 앞으로도 뒤로도 못 가는 상태가 됐다. job_id·scene index에
    무관한 공통 경로다.
    """
    held_reasons = {f"SCENE_HELD_FOR_REVIEW:scene_{held.get('index')}" for held in held_scenes}
    return sorted(set(review_reasons) | held_reasons)


def _apply_info_scene_template(scene: dict) -> tuple[dict, object | None]:
    """검증 payload 기반으로만 v4 장면 계약을 주입한다."""
    template = select_template(scene, scene.get("proposed_template_id"))
    if template is None:
        return scene, None
    planned = dict(scene)
    direction = dict(planned.get("art_direction") or {})
    direction.update({
        "character_placement": f"{template.character.side} third",
        "pose_asset": template.character.pose_asset,
        "fallback_pose": "present_right" if template.character.side == "left" else "present_left",
        "data_surface_kind": template.surface_kind,
        "board_side": template.board_side,
        "info_scene_template": template.template_id,
    })
    planned["art_direction"] = direction
    planned["info_scene_template"] = template.model_dump()
    return planned, template


def _diagram_spec_from_scene(scene: dict, plan):
    """검증된 구조 필드만 서사 다이어그램 입력으로 변환한다."""
    from app.services.info_surface.narrative_diagrams import DiagramItem, DiagramSpec
    chart = scene.get("market_chart") or {}
    kind = str(plan.diagram_kind or "")
    source = scene.get("stage_items") if kind == "stage_locks" else (
        scene.get("structure_items") if kind == "blueprint_callouts" else (
            chart.get("external_rates") if kind == "map_clouds" else scene.get("causal_nodes")
        )
    )
    items = []
    for raw in list(source or []):
        if not isinstance(raw, dict) or not raw.get("label") or not raw.get("source_refs"):
            continue
        items.append(DiagramItem(label=str(raw["label"]), value=str(raw["value"]) if raw.get("value") is not None else None, state=raw.get("state"), emphasis=bool(raw.get("emphasis"))))
    template = scene.get("info_scene_template") or {}
    if len(items) < int(template.get("min_items", 1)):
        raise ValueError("서사 다이어그램의 검증 항목 수가 템플릿 최소값보다 적습니다")
    title = str(scene.get("embedded_title") or chart.get("label") or scene.get("title") or "핵심 구조")
    # 서사형 다이어그램은 검증 payload의 노드·라벨만 제시한다. v3의 영문
    # 기간 의미줄을 재사용하면 보드 가장자리에서 잘려 정보 소품 문법을 해친다.
    meaning = ""
    return DiagramSpec(kind=kind, title=title, items=items, meaning_line=meaning, stamp=str(scene.get("verdict_stamp") or "") or None, seed=str(chart.get("scene_seed") or plan.scene_id))


def _scene_metadata_contract(source_scenes: list[dict], output_scenes: list[dict]) -> dict:
    """Audit that every source metadata key survives image generation."""
    source_keys = sorted({key for scene in source_scenes for key in scene})
    output_keys = sorted({key for scene in output_scenes for key in scene})
    missing = sorted(set(source_keys) - set(output_keys))
    return {
        "passed": not missing,
        "source_keys": source_keys,
        "output_keys": output_keys,
        "missing_keys": missing,
        "market_chart_count": sum(bool(scene.get("market_chart")) for scene in output_scenes),
        "index_data_count": sum(bool(scene.get("index_data")) for scene in output_scenes),
        "motion_type_count": sum(bool(scene.get("motion_type")) for scene in output_scenes),
    }


def _article_evidence_path(scene: dict) -> str:
    capture = scene.get("article_capture")
    kind = str(scene.get("visual_kind") or scene.get("visual_type") or "")
    if kind not in {"article_evidence", "article_scene"} or not isinstance(capture, dict):
        return ""
    return str(capture.get("local_path") or scene.get("image_path") or "")


def _character_regions(scene: dict) -> list[dict[str, float]]:
    """Return conservative normalized keep-out zones for post-production UI.

    Integrated AI illustrations cannot yield an alpha mask, so their region is
    derived from the same art-direction placement contract used to compose the
    scene.  Explicit regions from an editor/compositor always take precedence.
    """
    explicit = scene.get("character_regions")
    if isinstance(explicit, list):
        return explicit
    direction = scene.get("art_direction") or {}
    if not direction.get("character_required", False):
        return []
    placement = str(direction.get("character_placement") or "right third").lower()
    if "left" in placement:
        return [{"x": .015, "y": .10, "width": .41, "height": .79, "source": "art_direction_estimate"}]
    return [{"x": .555, "y": .10, "width": .43, "height": .79, "source": "art_direction_estimate"}]


def _saliency_grid_and_negative_spaces(clean_plate_path: str | None) -> tuple[list[list[float]], list[dict[str, float]]]:
    """Build a deterministic local 8×8 saliency approximation from edge energy.

    It deliberately avoids sending a frame to another model.  The planner uses
    it only to reject busy speech-bubble positions; it is not face detection.
    """
    default_grid = [[0.0 for _ in range(8)] for _ in range(8)]
    if not clean_plate_path or not Path(clean_plate_path).is_file():
        return default_grid, []
    try:
        with Image.open(clean_plate_path) as loaded:
            image = loaded.convert("L").resize((320, 180), Image.Resampling.LANCZOS)
        edges = image.filter(ImageFilter.FIND_EDGES)
        values: list[list[float]] = []
        for row in range(8):
            result_row: list[float] = []
            for col in range(8):
                crop = edges.crop((col * 40, row * 22, (col + 1) * 40, (row + 1) * 22))
                result_row.append(float(ImageStat.Stat(crop).mean[0]))
            values.append(result_row)
        maximum = max(max(row) for row in values) or 1.0
        grid = [[round(value / maximum, 4) for value in row] for row in values]
        # Candidate locations correspond to common bubble slots. Low edge
        # energy means the copy will not collide with a visual focal object.
        candidates = [
            (0, 0, 3, 3), (5, 0, 3, 3), (0, 3, 3, 2), (5, 3, 3, 2),
        ]
        spaces: list[dict[str, float]] = []
        for x, y, width, height in candidates:
            cells = [grid[row][col] for row in range(y, y + height) for col in range(x, x + width)]
            if sum(cells) / len(cells) <= .42:
                spaces.append({"x": x / 8, "y": y / 8, "width": width / 8, "height": height / 8})
        return grid, spaces
    except OSError:
        return default_grid, []


def _asset_layout_metadata(scene: dict, clean_plate_path: str | None) -> dict:
    """Persist enough layout facts to make thumbnail selection deterministic.

    This intentionally does not pretend to have face detection for an AI
    illustration.  Unknown values remain null and the planner then avoids
    person/mascot-led candidates rather than guessing an empty space.
    """
    regions = _character_regions(scene)
    direction = scene.get("art_direction") or {}
    placement = str(direction.get("character_placement") or "").lower()
    subject_side = "left" if "left" in placement else ("right" if placement else None)
    saliency_grid, negative_spaces = _saliency_grid_and_negative_spaces(clean_plate_path)
    # A pose's opposite side is preferred if it is also locally quiet. If the
    # plate is visually busy, no bubble is emitted rather than guessing.
    preferred = {"x": .54, "y": .08, "width": .39, "height": .38} if subject_side == "left" else (
        {"x": .07, "y": .08, "width": .39, "height": .38} if subject_side == "right" else None
    )
    negative_space = preferred if preferred and negative_spaces else (negative_spaces[0] if negative_spaces else None)
    return {
        "face_bbox": None,
        "gaze_direction": None,
        "hand_regions": [],
        "saliency_map_version": "edge_energy_8x8_v1",
        "saliency_grid": saliency_grid,
        "negative_space": negative_space,
        "negative_spaces": negative_spaces,
        "subject_side": subject_side,
        "clean_plate_path": clean_plate_path,
        "duplicate_character_count": 0 if clean_plate_path else None,
        "character_regions": regions,
    }

DEFAULT_CHARACTER_SHEET = Path("/app/assets/character/goldie_sheet_v1.png")


class NonRetryableImageGenerationError(RuntimeError):
    """A deterministic image-generation fault that must not fan out into scene retries."""


class ImageProviderCreditRequiredError(RuntimeError):
    """The configured image provider cannot render until billing/quota is restored."""


class ImageProviderTemporarilyUnavailableError(RuntimeError):
    """공급자 과부하로 현재 실행만 멈췄으며 기존 씬은 재개 가능한 상태다."""


def _is_non_retryable_image_error(exc: Exception) -> bool:
    return not classify_image_error(exc).retryable

import re

# 캐릭터 합성 위치 설정 (영상 충 대비 비율)
# 우하단 중앙에 위치
CHAR_OVERLAY_X_RATIO = 0.58   # 화면 왼쪽에서 58% 지점 (1920기준 약 1114px)
CHAR_OVERLAY_Y_RATIO = 0.08   # 상단 8% 지점
CHAR_HEIGHT_RATIO   = 0.72   # Keep the whole character inside a 16:9 frame.


def _extract_market_signal(text: str) -> dict:
    """
    씬 텍스트에서 실제 언급된 등락 방향과 지수/수치를 추출합니다.
    matplotlib 폴백 차트가 대본 내용과 모순되는 방향·숫자로 그려지는 것을
    막기 위한 용도입니다.
    """
    if not text:
        return {"direction": None, "value": None, "pct": None}

    direction = None
    if any(k in text for k in ["상승", "급등", "올랐", "돌파", "반등", "강세", "최고치", "호재"]):
        direction = "up"
    if any(k in text for k in ["하락", "급락", "내렸", "붕괴", "꺾", "약세", "부진", "악재"]):
        direction = "down"

    pct_match = re.search(r'([+-]?\d+(?:\.\d+)?)\s*(?:퍼센트|%)', text)
    pct = float(pct_match.group(1)) if pct_match else None
    if pct is not None and direction is None:
        if pct > 0:
            direction = "up"
        elif pct < 0:
            direction = "down"
    if pct is not None and direction == "down" and pct > 0:
        pct = -pct
    elif pct is not None and direction == "up" and pct < 0:
        pct = abs(pct)

    value_match = re.search(r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)', text)
    value = value_match.group(1) if value_match else None

    return {"direction": direction, "value": value, "pct": pct}

# 주식 플랫폼 컬러 팔레트 (네이비 테마 통일)
COLOR_BG = "#0d1b2a"
COLOR_BG2 = "#16213e"
COLOR_ACCENT_GOLD = "#e2b96f"
COLOR_ACCENT_CYAN = "#00d4ff"
COLOR_ACCENT_GREEN = "#00c896"
COLOR_ACCENT_RED = "#e94560"
COLOR_TEXT = "#ffffff"
COLOR_GRID = "#2a3f5f"

FONT_PATH = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"


class ImagesWorker:
    CANVAS_SIZE = (1920, 1080)

    def _call_llm(self, system: str, messages: list, max_tokens: int = 200) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        from anthropic import Anthropic
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return resp.content[0].text

    def _build_prompt_from_narration(
        self,
        narration: str,
        scene_type: str,
        title: str,
        *,
        core_entities: list[str] | None = None,
        core_figures: list[dict] | None = None,
        fictionalized_labels: dict[str, str] | None = None,
        visual_mode: str | None = None,
        character_required: bool = True,
        screen_texts: list[str] | None = None,
    ) -> str:
        """
        대사(narration)와 바인딩된 엔티티/수치를 읽고 내용에 맞는 시각 은유 배경/캐릭터 프롬프트를 LLM으로 생성한다.
        - core_entities / core_figures는 장면 의미와 소품 선택을 고정한다.
          비수치 이름·문구는 scene-local screen_texts에 승인된 경우 직접 쓸 수 있고,
          금융 수치는 결정론 표면 렌더러가 담당한다.
        - character_required=True일 경우 Goldie(금화 마스코트) 캐릭터가 전경의 1/3 크기로 명시 배치된다.
        - 매 씬마다 대사가 다르면 반드시 다른 프롬프트가 나와야 함
        """
        composition = ARCHETYPE_COMPOSITION.get(scene_type, ARCHETYPE_COMPOSITION["general"])
        visual_narration = redact_financial_values_for_visual_direction(narration)
        is_news_mode = (visual_mode == "article_evidence") or (scene_type in ("news_context", "reporter_worried", "news_anchor"))

        if is_news_mode:
            system = (
                "You are a visual director for a Korean 2D financial animation channel. "
                "Convert Korean financial narration into a vivid 2D scene featuring the channel's Goldie (2D gold coin mascot) news reporter. "
                "The scene MUST depict the 2D gold coin mascot character standing as a financial reporter or presenter. "
                "The background features an appropriate display surface (such as a standing presentation screen, wall display board, or weather map) "
                "using charts and physical props to represent the economic situation. "
                "Only the supplied scene-local approved strings may be written; never invent filler labels. "
                "Output only the prompt text, no explanation, no markdown."
            )
            char_rules = (
                "- Depict one Goldie gold-coin mascot when the storyboard calls for a presenter. Its size, side, crop, expression, costume, and headwear follow this scene's role and composition; do not default every scene to the same finance-anchor outfit or face.\n"
                "- Keep the mascot recognizably within the same round coin-character drawing family while allowing expressive variation.\n"
                "- Background people may appear when the narration needs an audience, traders, shoppers, press, or workers; never add a second coin mascot.\n"
                "- The background and information surfaces follow this scene's specific visual metaphor, not a default studio template."
            )
        elif character_required and visual_mode != "background_only":
            system = (
                "You are a visual director for a Korean 2D financial animation channel. "
                "Convert Korean financial narration into a vivid English scene description featuring the channel's Goldie (2D gold coin mascot) character. "
                "The 2D gold coin mascot character stands in the foreground observing or presenting the economic situation. "
                "Output only the prompt text, no explanation, no markdown."
            )
            char_rules = (
                "- Depict one Goldie gold-coin mascot with scene-specific size, placement, expression, costume, headwear, and action; do not copy one neutral face or navy presenter outfit into every scene.\n"
                "- Preserve the shared round coin-character drawing language, not a frozen face sheet.\n"
                "- Background people may appear only when they serve the scene's economic story; never add a second coin mascot.\n"
                "- The character reacts to or presents the economic situation in the way this particular scene requires."
            )
        else:
            system = (
                "You are a visual director for a Korean 2D financial animation channel. "
                "Convert Korean financial narration into a vivid English background scene description. "
                "Never describe characters. Only describe the background environment and symbolic objects. "
                "Output only the prompt text, no explanation, no markdown."
            )
            char_rules = "- No characters, no people, no coin character, background environment only."

        entity_instructions = ""
        if core_entities or core_figures:
            mapped_entities = []
            uncertain_entities = []
            for ent in (core_entities or []):
                mapped, confidence = get_entity_english_name(ent)
                if confidence == "uncertain":
                    uncertain_entities.append(ent)
                elif mapped not in mapped_entities:
                    mapped_entities.append(mapped)

            entities_str = ", ".join(mapped_entities)
            uncertain_clause = ""
            if uncertain_entities:
                uncertain_clause = (
                    f"- UNCERTAIN UNMAPPED ENTITIES ({', '.join(uncertain_entities)}): "
                    f"Use only generic industry props for semantic context. Never guess or render a company name, logo, or text label.\n"
                )

            figures_clause = ""
            if core_figures:
                fig_list = [f.get("raw") for f in core_figures if isinstance(f, dict) and f.get("raw")]
                if fig_list:
                    figures_clause = (
                        "- Verified financial quantities are relevant to the scene, but their literal values are withheld from image generation and will be rendered deterministically later.\n"
                        f"- Let one storyboard-essential existing physical prop contain a calm in-perspective region for deterministic post-production; never add a separate blank placard, floating card, or safe-zone panel.\n"
                        f"- Do NOT invent or guess secondary numbers, dates, labels, or fake tick marks.\n"
                    )

            entity_instructions = (
                f"\nCRITICAL ENTITY & FIGURE GROUNDING INSTRUCTIONS:\n"
                f"- Core Verified Entities for semantic grounding only; depict their relevant industry objects. Render a name only if it is in the scene-local approved screen-text list: {entities_str}\n"
                f"{figures_clause}"
                f"{uncertain_clause}"
                f"- Incorporate at least one relevant unlabeled physical prop alongside rich environmental storytelling props.\n"
                f"- Every readable sign, monitor, board, paper, gauge, and package must use only a scene-local approved exact string. Non-readable chart strokes and material details remain allowed.\n"
            )

        approved_generated_texts = [
            value for value in (screen_texts or []) if not contains_financial_number(value)
        ]
        deterministic_texts = [
            value for value in (screen_texts or []) if contains_financial_number(value)
        ]
        if approved_generated_texts:
            screen_text_clause = (
                "- SCENE-LOCAL APPROVED GENERATED TEXT: "
                + " | ".join(approved_generated_texts)
                + ". Use at most these exact strings, copied character-for-character on natural in-world surfaces.\n"
            )
        else:
            screen_text_clause = "- This scene has no approved image-model text. Do not invent labels or pseudo-text.\n"
        if deterministic_texts:
            screen_text_clause += (
                "- Verified financial values are withheld from this visual-direction request and will be rendered deterministically later. "
                "Use a calm region of an existing storyboard-essential, perspective-correct physical prop for them. "
                "Do not guess or draw any financial number in the base raster, and do not add a separate blank placard, floating card, or safe-zone panel.\n"
            )

        from app.utils.scene_content_classifier import classify_narration_content_type
        strategy = classify_narration_content_type(narration, core_entities, core_figures)
        content_type_instructions = (
            f"\nCONTENT TYPE STRATEGY ({strategy.content_type}):\n"
            f"- Text & Labeling Rule: {strategy.label_text_rule}\n"
            f"- Background Narrative Rule: {strategy.background_narrative_rule}\n"
        )

        user_content = f"""Korean narration semantic copy with literal financial values withheld: "{visual_narration}"
Scene type: {scene_type}
Composition hint: {composition}
Visual mode: {visual_mode or "general"}
{content_type_instructions}
{entity_instructions}
{screen_text_clause}
Convert the narration's core economic situation into a physical visual scene.
Rules:
- Abstract concepts must become concrete physical objects/environments; identify entities through context, props, and only approved exact strings
- Each scene must be visually distinct. Never repeat the same background.
- Do NOT translate narration sentences into large blackboard text. Use environmental props and scene-specific backgrounds.
- Do not generate unapproved Korean, English, numbers, logos, or pseudo-text. A bubble or split composition appears only when this scene's storyboard explicitly plans it.
- Choose the scene's own visual hierarchy. Some scenes may be simple and iconic; others may be dense, split, chart-led, object-led, or environment-led. Do not force one board, studio, prop count, character size, or camera layout across the video.
{char_rules}
- Never introduce a second coin mascot. Human background figures are allowed only when they have a clear scene role.
- Keep under 75 words.
- End with exactly: {STYLE_SUFFIX}"""

        try:
            raw = self._call_llm(system, [{"role": "user", "content": user_content}], max_tokens=600)
            cleaned = raw.strip().strip('"').strip("'")
            if (is_news_mode or character_required) and not cleaned.lower().startswith("the 2d gold coin") and not cleaned.lower().startswith("a 2d gold coin"):
                cleaned = f"The 2D gold coin mascot character (Goldie), {cleaned}"
            if STYLE_SUFFIX not in cleaned:
                cleaned = f"{cleaned}, {STYLE_SUFFIX}"
            logger.info("대사 기반 프롬프트 재생성 완료 (씬: %s): %s...", title, cleaned[:60])
            return cleaned
        except Exception as exc:
            logger.warning("대사 기반 프롬프트 재생성 실패 (씬: %s): %s", title, exc)
            mapped_entities = [get_entity_english_name(e)[0] for e in (core_entities or []) if get_entity_english_name(e)[1] != "uncertain"]
            fig_list = [f.get("raw") for f in (core_figures or []) if isinstance(f, dict) and f.get("raw")]
            ent_clause = f" with industry-specific unlabeled physical props associated with {', '.join(mapped_entities)}" if mapped_entities else ""
            if fig_list:
                ent_clause += " and a calm region on one storyboard-essential existing physical prop for exact verified values in post-production, without a separate blank placard or floating card"
            if strategy.content_type == "concept_explainer" or scene_type == "classroom":
                setting = "A cozy 2D classroom with an illustrated green teaching wall, open textbooks and an academic balance scale"
            elif strategy.content_type == "comparison" or scene_type == "split_stage":
                setting = "A split-stage 2D financial environment comparing two contrasting sides with distinct company logos"
            elif strategy.content_type == "macro_geopolitical" or scene_type == "risk_control_room":
                setting = "A 2D geopolitical risk control room with oil barrels, red alert lamps and scene-appropriate illustrated monitor surfaces"
            elif strategy.content_type == "entity_news" or scene_type == "briefing_podium":
                setting = "A sleek 2D press briefing stage with curved LED display screen"
            elif scene_type == "real_estate_office":
                setting = "A 2D real estate office with apartment blueprints, property maps and market charts"
            else:
                setting = f"A 2D financial environment matching {composition}"
            return f"{setting}{ent_clause}, featuring the 2D gold coin mascot character (Goldie) presenting the scene, {STYLE_SUFFIX}"

    def _normalize_canvas(self, img_path: str) -> None:
        """AI 이미지를 최종 캔버스(1920x1080)로 정규화. cover-crop 방식.

        반드시 모든 Pillow 오버레이보다 먼저 호출할 것.
        Flash 1K(≈1024px) 이미지도 여기서 Lanczos 업스케일되므로,
        이후에 그리는 차트/말풍선/숫자는 최종 해상도에서 렌더되어 선명함.
        """
        from PIL import Image
        sw, sh = self.CANVAS_SIZE
        with Image.open(img_path) as img:
            img = img.convert("RGB")
            if img.size == (sw, sh):
                return
            scale = max(sw / img.width, sh / img.height)   # cover (여백 없음)
            nw, nh = round(img.width * scale), round(img.height * scale)
            img = img.resize((nw, nh), Image.Resampling.LANCZOS)
            left, top = (nw - sw) // 2, (nh - sh) // 2
            
            # Save format matching the extension
            ext = os.path.splitext(img_path)[1].lower()
            save_format = "JPEG" if ext in {".jpg", ".jpeg"} else "PNG"
            img.crop((left, top, left + sw, top + sh)).save(img_path, save_format)

    def generate(self, scenes_meta: list = None, job_id: int = 0,
                 tts_meta_json: str = None, script_meta_json: str = None,
                 character_image_path: str = None, character_style_prompt: str = None,
                 character_poses_dir: str = None,
                 lora_model_id: str = None, lora_trigger_word: str = None,
                 lora_scale: float = 1.0, autonomy_mode: str | None = None,
                 budget_limit_krw: int | None = None, budget_policy_version: str | None = None) -> dict:
        """
        씬별 이미지를 생성합니다.

        [Sprint 3] LoRA 파라미터:
          - lora_model_id: safetensors CDN URL (Fal.ai flux-lora 사용)
          - lora_trigger_word: LoRA 활성화 트리거 단어
          - lora_scale: LoRA 적용 강도 (0.8~1.2, 기본 1.0)

        [S2-3] character_poses_dir 지정 시 이중 레이어 합성 모드:
          1) 배경 전용 이미지 생성 (character_style_prompt="background_only")
          2) 씬별 포즈 투명 PNG 로드
          3) FFmpeg overlay 필터로 합성
        """
        lock_token = acquire_image_job_lock(job_id)
        try:
            return self._generate(
                scenes_meta=scenes_meta, job_id=job_id, tts_meta_json=tts_meta_json,
                script_meta_json=script_meta_json, character_image_path=character_image_path,
                character_style_prompt=character_style_prompt, character_poses_dir=character_poses_dir,
                lora_model_id=lora_model_id, lora_trigger_word=lora_trigger_word, lora_scale=lora_scale,
                autonomy_mode=autonomy_mode,
                budget_limit_krw=budget_limit_krw, budget_policy_version=budget_policy_version,
            )
        finally:
            release_image_job_lock(job_id, lock_token)

    def generate_single_scene(
        self,
        *,
        scene: dict,
        job_id: int,
        output_dir: Path | None = None,
        character_image_path: str | None = None,
        character_style_prompt: str | None = None,
        character_poses_dir: str | None = None,
    ) -> dict:
        """UI의 장면 재생성도 전체 이미지와 같은 생성·검수 계약으로 처리한다.

        이 경로는 과거에 장면 메타데이터와 공통 QA를 버린 뒤 공급자 실패를
        Matplotlib 차트로 조용히 대체했다. 명시 재생성은 기존 승인 이미지를
        성공 전까지 보존하고, 문자·수치·캐릭터·표면·Fal 안전 계약을 모두
        통과한 새 PNG만 게시한다.
        """
        import shutil

        ctx = dict(scene or {})
        index = int(ctx.get("index", 0))
        section = str(ctx.get("section") or f"scene_{index}")
        narration = str(
            ctx.get("text_for_tts")
            or ctx.get("narration")
            or ctx.get("script")
            or ctx.get("content")
            or ctx.get("text")
            or ""
        ).strip()
        if not narration:
            raise ValueError("단일 장면 재생성에는 승인 장면 원문이 필요합니다.")

        base_prompt = str(ctx.get("prompt_en") or ctx.get("prompt") or "").strip()
        if not base_prompt:
            # 2026-09-30 발견: 배치 경로(_generate_parallel_scenes)는 scene_spec이
            # 있으면(SceneDirector가 장면별 의상·행동·소품·카메라를 직접 지정)
            # 항상 build_prompt(spec)을 쓴다. 이 단일 장면 경로는 scene_spec을
            # 전혀 읽지 않아 항상 더 일반적인 compile_editorial_prompt로
            # 대체됐다 — 원래 승인 장면이 SceneDirector로 연출됐어도 재생성 시
            # 그 구체적 의상·동작·소품·카메라 지시가 전부 사라졌다. job/scene에
            # 무관한 공통 계약 버그다.
            base_prompt = _base_prompt_from_scene_spec(ctx.get("scene_spec")) or ""
        if not base_prompt:
            # CUSTOM 카테고리 등 금융과 무관한 주제도 이 경로를 탄다(예: 정치·
            # 안보 사안). "financial narration"으로 고정하면 실제 art_direction과
            # 무관하게 금융 화풍(성장 차트 등)을 암시해 의미 불일치를 유발한다.
            base_prompt = compile_editorial_prompt(
                ctx,
                f'Visually explain this Korean narration: "{narration}"',
            )
        ctx.update({
            "index": index,
            "section": section,
            "text": narration,
            "prompt_ko": str(ctx.get("prompt_ko") or narration),
            "prompt_en": base_prompt,
            "prompt": base_prompt,
            "_request_job_id": job_id,
        })

        lock_token = acquire_image_job_lock(job_id)
        try:
            from app.providers.factory import get_image_provider

            provider = get_image_provider()
            if provider is None:
                raise RuntimeError("운영 이미지 공급자를 불러올 수 없습니다.")

            target_dir = output_dir or Path(f"/app/data/jobs/{job_id}/images")
            target_dir.mkdir(parents=True, exist_ok=True)
            final_path = target_dir / f"scene_{index:03d}.png"
            raw_path = target_dir / f"scene_{index:03d}_regenerate_raw.png"
            ctx["_request_raw_path"] = str(raw_path)
            generation_prompt = _bounded_text_generation_prompt(
                base_prompt,
                audit_target=ctx,
            )

            all_references: list[str] = []
            if character_image_path and Path(character_image_path).is_file():
                all_references.append(character_image_path)
            all_references.extend(_load_default_references())
            character_required = bool((ctx.get("art_direction") or {}).get("character_required", True))
            selected_references = (
                select_contextual_reference_paths(generation_prompt, all_references)
                if character_required else []
            )

            if character_poses_dir and Path(character_poses_dir).is_dir():
                background_path = target_dir / f"scene_{index:03d}_regenerate_bg.png"
                self._generate_background_layer(
                    provider,
                    generation_prompt,
                    str(background_path),
                    section,
                    str(ctx.get("pose") or "neutral"),
                    ctx.get("image_profile") or {},
                    job_id=job_id,
                    scene_key=f"background:{index}",
                )
                self._normalize_canvas(str(background_path))
                self._compose_layered_scene(
                    ctx,
                    str(background_path),
                    character_poses_dir,
                    str(ctx.get("pose") or "neutral"),
                    str(raw_path),
                    job_id,
                )
                generation_method = "single_scene_operational_composite"
            else:
                provider.generate_image(
                    prompt=generation_prompt,
                    output_path=str(raw_path),
                    section=section,
                    keyword=narration[:30],
                    character_image_path=selected_references[0] if selected_references else None,
                    character_image_paths=selected_references,
                    character_style_prompt=character_style_prompt,
                    image_provider=runtime_config.value("image_provider"),
                    openai_job_id=job_id,
                    openai_scene_key=f"image:{index}",
                    gemini_model=str((ctx.get("image_profile") or {}).get("model") or "gemini-3-pro-image"),
                    gemini_image_size=str((ctx.get("image_profile") or {}).get("image_size") or "2K"),
                    gemini_service_tier=runtime_config.value("gemini_service_tier"),
                    gemini_max_attempts=1,
                    gemini_retry_base_seconds=runtime_config.value("gemini_pro_retry_base_seconds"),
                    gemini_request_audit=ProviderRequestAudit.for_job(
                        job_id=job_id,
                        scene_key=f"image:{index}",
                        model=str((ctx.get("image_profile") or {}).get("model") or "gemini-3-pro-image"),
                    ),
                    style_locked=True,
                    gemini_reference_contract_declared=True,
                )
                generation_method = "single_scene_operational"

            if not raw_path.is_file() or raw_path.stat().st_size <= 15_000:
                raise RuntimeError("단일 장면 공급자가 검증 가능한 이미지를 반환하지 않았습니다.")
            self._normalize_canvas(str(raw_path))
            _inspect_generated_textless_image(ctx, str(raw_path))

            staged_final = target_dir / f"scene_{index:03d}_regenerate_final.png"
            shutil.copy2(raw_path, staged_final)
            self._apply_image_overlays(ctx, str(staged_final))
            _inspect_generated_visual_image(ctx, str(staged_final))
            os.replace(staged_final, final_path)

            ctx.update({
                "image_path": str(final_path),
                "generation_method": generation_method,
                "quality_score": 90,
                "prompt_en": generation_prompt,
                "prompt": generation_prompt,
            })
            _apply_fal_motion_safety_contract([ctx])
            ctx["character_regions"] = _character_regions(ctx)
            ctx["operational_contract_audit"] = build_operational_contract_audit(
                "single_scene",
                checks={
                    "scene_metadata_preserved": True,
                    "bounded_prompt_applied": True,
                    "base_image_gate_passed": True,
                    "final_image_gate_passed": True,
                    "fal_preflight_attached": isinstance(ctx.get("fal_motion_safety"), dict),
                },
            )
            _persist_single_scene_visual_qa_review(target_dir, index, ctx.get("visual_qa_review"))
            _clear_scene_from_request_review(target_dir, job_id, index)
            return ctx
        finally:
            release_image_job_lock(job_id, lock_token)

    def _generate(self, scenes_meta: list = None, job_id: int = 0,
                  tts_meta_json: str = None, script_meta_json: str = None,
                  character_image_path: str = None, character_style_prompt: str = None,
                  character_poses_dir: str = None,
                  lora_model_id: str = None, lora_trigger_word: str = None,
                  lora_scale: float = 1.0, autonomy_mode: str | None = None,
                  budget_limit_krw: int | None = None, budget_policy_version: str | None = None) -> dict:
        market_snapshot = {}
        self.market_snapshot = {}
        self.evidence_audit = {}
        self.visual_mix_plan: dict = {}
        self.tts_subtitle_sync: dict = {"passed": None, "reason": "tts_metadata_not_supplied"}
        script_data: dict = {}
        # scenes_meta가 주어지지 않은 경우 script_meta_json에서 복원
        if script_meta_json:
            try:
                import json
                script_data = json.loads(script_meta_json)
                if isinstance(script_data, str):
                    script_data = json.loads(script_data)
                market_snapshot = script_data.get("market_snapshot") or {}
                self.market_snapshot = market_snapshot
                if not scenes_meta:
                    scenes_meta = script_data.get("sections") or script_data.get("scenes") or []
                if not scenes_meta and script_data.get("script"):
                    import re
                    raw_script = script_data.get("script", "").strip()
                    parts = [p.strip() for p in re.split(r'(?m)^##\s*|\n{2,}', raw_script) if p.strip()]
                    total_parts = len(parts)
                    section_keys = ["intro", "background", "data", "scenario", "action", "conclusion"]
                    for idx, part in enumerate(parts):
                        ratio = idx / max(total_parts - 1, 1) if total_parts > 1 else 0
                        section_type = section_keys[min(int(ratio * len(section_keys)), len(section_keys) - 1)]
                        scenes_meta.append({
                            "title": f"Scene {idx + 1}",
                            "content": part,
                            "text": part,
                            "prompt": "A Korean finance editorial comic scene with one clear visual metaphor and specific business context.",
                            "section": section_type
                        })
                logger.info(f"script_meta_json에서 {len(scenes_meta)}개 씬 복원 성공")
            except Exception as e:
                logger.error(f"script_meta_json에서 씬 목록 추출 실패: {e}")
                scenes_meta = []

        # 기존 이미지 생성 경로는 유지한다. 다만 TTS 메타데이터가 함께 온
        # 정상 롱폼 경로에서는 이미지가 다른 대본에서 출발하지 않았는지 먼저 막는다.
        if tts_meta_json and script_data:
            try:
                tts_data = json.loads(tts_meta_json)
                if isinstance(tts_data, str):
                    tts_data = json.loads(tts_data)
                contract = script_data.get("narration_contract") or build_script_contract(
                    str(script_data.get("script") or ""),
                    list(script_data.get("sections") or script_data.get("scenes") or []),
                )
                narration_sync = verify_tts_against_script_contract(tts_data, contract)
                scenes_meta, sentence_boundary_sync = normalize_scene_sentence_boundaries(
                    list(scenes_meta),
                )
                caption_sync = bind_caption_chunks_to_scenes(
                    list(scenes_meta),
                    list(tts_data.get("chunks") or []),
                )
                tts_chunks = list(tts_data.get("chunks") or [])
                for scene, mapping in zip(scenes_meta, caption_sync["mappings"]):
                    narration = str(
                        scene.get("text_for_tts") or scene.get("content") or scene.get("text") or ""
                    ).strip()
                    planned = scene.get("caption_chunk_plan")
                    if isinstance(planned, dict) and isinstance(planned.get("chunks"), list):
                        planned_texts = [str(item.get("text") or "") for item in planned["chunks"]]
                        actual_texts = [
                            str(item.get("text") or "")
                            for item in tts_chunks[
                                mapping["chunk_start_index"]:mapping["chunk_end_index"] + 1
                            ]
                        ]
                        if planned_texts != actual_texts:
                            raise RuntimeError(
                                f"장면 {mapping['scene_index'] + 1}의 SCRIPT 예상 자막 청크와 "
                                "실제 TTS 자막 청크가 다릅니다. 설정 변경 후 TTS를 재사용할 수 없습니다."
                            )
                    scene["subtitle_text"] = narration
                    scene["caption_chunk_contract"] = mapping
                    # 이미지 프롬프트가 설명해야 하는 의미 원문도 동일한 장면
                    # 텍스트 해시로 고정한다. 실제 자막 문구를 이미지 안에
                    # 그리라는 뜻이 아니라, 이미지의 설명 대상 계보이다.
                    scene["caption_image_contract"] = {
                        "version": mapping["version"],
                        "scene_text_sha256": mapping["scene_text_sha256"],
                        "prompt_semantic_source_sha256": mapping["scene_text_sha256"],
                        "caption_compact_sha256": mapping["caption_compact_sha256"],
                        "chunk_start_index": mapping["chunk_start_index"],
                        "chunk_end_index": mapping["chunk_end_index"],
                        "transition_on_caption_boundary": True,
                    }
                self.tts_subtitle_sync = {
                    **narration_sync,
                    "scene_sentence_boundary": sentence_boundary_sync,
                    "caption_scene_contract_version": caption_sync["version"],
                    "scene_count": caption_sync["scene_count"],
                    "all_scene_boundaries_on_caption_boundaries": True,
                    "scene_mappings": caption_sync["mappings"],
                }
            except Exception as exc:
                raise RuntimeError(f"이미지 단계 대본·TTS·자막 계보 검증 실패: {exc}") from exc

        if not scenes_meta:
            scenes_meta = []
        else:
            # 이전 대본 자산도 재생성 없이 최신 장면 로컬 문자 계약을 적용한다.
            # 기존 명시 screen_texts는 보존하고, 빈 씬만 승인 대본에서 추출한다.
            scenes_meta = attach_scene_screen_texts(scenes_meta)
        still_image_pilot = bool(script_data.get("still_image_pilot"))
        if still_image_pilot:
            test_budget_limit = int(runtime_config.value("gemini_test_image_budget_krw"))
            if test_budget_limit <= 0:
                raise RuntimeError("정지 이미지 파일럿 예산은 0원보다 커야 합니다.")
            budget_limit_krw = min(
                test_budget_limit,
                int(budget_limit_krw) if budget_limit_krw is not None else test_budget_limit,
            )

        if scenes_meta and bool(runtime_config.value("article_evidence_auto_enabled")):
            try:
                from app.services.article.evidence_planner import (
                    ArticleEvidencePlanner,
                    NarrationHashMismatch,
                )

                planned = ArticleEvidencePlanner().attach(
                    job_id=job_id,
                    scenes=scenes_meta,
                    verified_facts=list(script_data.get("verified_facts") or []),
                )
                scenes_meta = planned.scenes
                self.evidence_audit = planned.audit
            except NarrationHashMismatch:
                raise
            except Exception as exc:
                # Public news is an optional evidence enhancement.  Search,
                # publisher, or capture failures preserve the approved scene.
                logger.warning("automatic article evidence planning skipped: %s", exc)
                self.evidence_audit = {
                    "job_id": job_id,
                    "status": "unavailable",
                    "reason": str(exc),
                    "selected": [],
                }

        # 기사 캡처가 확정된 뒤에만 전체 대본의 장면 계보를 세 유형으로
        # 배정한다. 원문·TTS 문장·장면 순서는 바꾸지 않는다.
        if scenes_meta and not still_image_pilot:
            scenes_meta, self.visual_mix_plan = apply_longform_visual_mix(scenes_meta)

        # V5 visual_mode를 먼저 확정해야 기사형·상황형 장면에 마스코트를
        # 강제로 넣는 기존 art-direction 계약을 피할 수 있다.
        if scenes_meta and all(scene.get("scene_type") for scene in scenes_meta):
            scenes_meta = attach_v5_scene_contracts(scenes_meta)
        if scenes_meta and not all(scene.get("art_direction") for scene in scenes_meta):
            scenes_meta = direct_scenes([
                enrich_scene_plan(scene, i, len(scenes_meta))
                for i, scene in enumerate(scenes_meta)
            ])
        # Stage 1 + Stage 3: Bind entities & figures for all scenes
        verified_facts = list(script_data.get("verified_facts") or [])
        keyword_news = list(script_data.get("keyword_news") or script_data.get("articles") or [])
        entity_bindings = bind_scene_entities(scenes_meta, verified_facts, keyword_news)
        binding_map = {str(b.scene_id): b for b in entity_bindings}
        binding_by_index = {b.scene_index: b for b in entity_bindings}
        # ScriptWorker가 이미 확정한 scene_type을 운영 이미지 경로까지
        # 보존한다. 이 단계는 순수 계획만 만들며 이미지 API를 호출하지 않는다.
        visual_mix_preflight = _visual_mix_audit(scenes_meta)
        if not still_image_pilot and scenes_meta and not visual_mix_preflight["passed"]:
            logger.warning(
                "IMAGE_MIX_PREFLIGHT_WARNING: 기사형·상황형·정보형 구성 비율 경고 (이미지 생성 계속 진행): "
                + ", ".join(visual_mix_preflight["failures"])
            )
        budget_preflight = None
        if scenes_meta:
            billable_scenes = [scene for scene in scenes_meta if not _article_evidence_path(scene)]
            # 재개 실행에서는 이미 검증되어 저장된 PNG를 다시 생성하거나 비용에
            # 포함하지 않는다. Kling은 아직 생성하지 않았으므로 별도로 계속 예약한다.
            pending_billable_scenes = [
                scene for index, scene in enumerate(scenes_meta)
                if not _article_evidence_path(scene)
                and not (
                    (cached_path := Path(f"/app/data/jobs/{job_id}/images/scene_{index:03d}.png")).is_file()
                    and cached_path.stat().st_size > 15000
                )
            ]
            # 모든 Pro 요청 비용을 첫 이미지 호출 전에 확인하며, 초과 시
            # 품질을 낮추지 않고 작업 전체를 거절한다.
            estimated_total_duration = infer_total_duration_seconds(
                scenes_meta,
                float(runtime_config.value("scene_duration_sec")),
            )
            # Each selected scene produces one Fal/Kling request; retain the
            # actual seconds separately for audit because the provider caps a
            # request at five seconds.
            intro_motion_indices, motion_target_seconds, planned_motion_seconds = select_intro_motion_scene_indices(
                billable_scenes,
                estimated_total_duration,
                short_seconds=float(runtime_config.value("intro_motion_seconds_short")),
                long_seconds=float(runtime_config.value("intro_motion_seconds_long")),
                short_threshold=float(runtime_config.value("intro_motion_short_threshold")),
                max_clips=(
                    0
                    if still_image_pilot or not bool(runtime_config.value("intro_motion_enabled"))
                    else min(
                        2 if bool(runtime_config.value("intro_motion_test_mode")) else int(runtime_config.value("intro_motion_clip_count")),
                        int(runtime_config.value("intro_motion_clip_count")),
                    )
                ),
                clip_seconds=float(runtime_config.value("intro_motion_clip_seconds")),
                # 이미지 생성 전에는 OCR 결과가 아직 없으므로 장면 계약만으로
                # 문자·수치·기사형을 건너뛴다. 최종 PNG OCR은 생성 직후 다시
                # 검사하며, 조립 단계가 안전한 다음 후보를 한 번 더 선택한다.
                scene_eligible=lambda candidate: not fal_motion_metadata_block_reasons(candidate),
            )
            template_scene_count = sum(
                1 for scene in pending_billable_scenes
                if select_template(scene, scene.get("proposed_template_id")) is not None
            )

            def make_budget_preflight(kling_count: int) -> dict:
                return plan_preflight(
                    len(pending_billable_scenes),
                    str(runtime_config.value("image_quality_tier")),
                    int(runtime_config.value("pro_image_max_scenes")),
                    kling_count,
                    template_scene_count=template_scene_count,
                    # 썸네일은 본편의 사람 검수·승인 뒤에 별도 단계로 만든다.
                    # 이미지 생성 단계에서 비용을 예약하거나 자동으로 시작하지 않는다.
                    include_thumbnail=False,
                    budget_limit_krw=budget_limit_krw,
                    policy_version=budget_policy_version,
                )

            requested_motion_count = len(intro_motion_indices)
            gemini_image_only_budget = bool(
                budget_policy_version and "gemini-image-only" in budget_policy_version
            )
            # Gemini 정지 이미지 예산에는 Fal 모션을 넣지 않는다. Fal은 이미지
            # 검수 후 조립 단계에서 선택한 장면만 별도 비용으로 실행한다.
            budget_preflight = make_budget_preflight(
                0 if gemini_image_only_budget else requested_motion_count
            )
            # 선행 단계에서 사용한 비용만큼 잔여 예산이 줄면, 본문 이미지 품질은
            # 유지하고 선택 사항인 인트로 Kling만 뒤 장면부터 축소한다.
            while (
                not gemini_image_only_budget
                and not budget_preflight["allowed"]
                and intro_motion_indices
            ):
                intro_motion_indices.remove(max(intro_motion_indices))
                budget_preflight = make_budget_preflight(len(intro_motion_indices))
            if len(intro_motion_indices) != requested_motion_count:
                budget_preflight["actions"].append(
                    "intro_motion_clips_reduced_for_remaining_budget:"
                    f"{requested_motion_count}->{len(intro_motion_indices)}"
                )
            if gemini_image_only_budget:
                budget_preflight["gemini_image_only_budget"] = True
                budget_preflight["fal_motion_clips_reserved_separately"] = requested_motion_count
            if not billable_scenes:
                # plan_preflight normally reserves one generated thumbnail;
                # an evidence-only job has no generated still at all.
                budget_preflight.update({
                    "pro_scene_count": 0,
                    "flash_scene_count": 0,
                    "kling_clip_count": 0,
                    "estimated_cost_krw": 0,
                    "actions": [],
                    "allowed": True,
                    "reason": None,
                })
            budget_preflight["intro_motion_target_seconds"] = motion_target_seconds
            budget_preflight["intro_motion_planned_seconds"] = planned_motion_seconds
            budget_preflight["intro_motion_estimated_total_seconds"] = estimated_total_duration
            budget_preflight["still_image_pilot"] = still_image_pilot
            budget_preflight["resumed_scene_count"] = len(billable_scenes) - len(pending_billable_scenes)
            write_preflight(job_id, budget_preflight)
            if not budget_preflight["allowed"]:
                raise RuntimeError(
                    f"Budget preflight blocked this job: expected ₩{budget_preflight['estimated_cost_krw']:,} "
                    f"> limit ₩{budget_preflight['budget_limit_krw']:,} ({budget_preflight['reason']})"
                )
            if budget_preflight["actions"]:
                logger.warning("Budget preflight degraded optional quality: %s", budget_preflight["actions"])
            tiered_billable_scenes = plan_image_quality_tiers(
                billable_scenes,
                runtime_config.value("image_quality_tier"),
                len(billable_scenes),
            )
            tiered_iter = iter(tiered_billable_scenes)
            scenes_meta = [scene if _article_evidence_path(scene) else next(tiered_iter) for scene in scenes_meta]
            # 품질 분배기가 적용된 뒤에도 V5 최종 lane의 Gemini Pro 계약은
            # 하향되지 않도록 다시 명시한다.
            if all(scene.get("scene_type") for scene in scenes_meta):
                scenes_meta = attach_v5_scene_contracts(scenes_meta)

            # ── use_kling 씬 마킹 ──────────────────────────────────────────────
            # intro_motion_indices(예산 확정 후의 최종 클립 목록)를 기반으로
            # 각 씬에 use_kling 필드를 기록한다.
            # longform_worker의 _has_manual_kling_selection()이 이 필드를 읽어
            # Kling 호출 여부를 결정한다. 필드가 하나라도 있으면 manual 선택으로
            # 인식하므로, 전체 씬에 True/False를 반드시 채워야 한다.
            # - 기사 캡처 씬: 항상 False (이미 캡처 이미지 사용)
            # - V5 검증 사실 오버레이 씬: 항상 False (수치 변형 방지)
            # - 그 외 intro_motion_indices 해당 씬: True
            _billable_idx_map = {
                orig_idx: bill_idx
                for bill_idx, orig_idx in enumerate(
                    idx for idx, s in enumerate(scenes_meta)
                    if not _article_evidence_path(s)
                )
            }
            for _scene_idx, _scene in enumerate(scenes_meta):
                if _article_evidence_path(_scene):
                    _scene["use_kling"] = False
                    continue
                _v5_overlays = _scene.get("v5_verified_overlays")
                _has_fact_overlay = isinstance(_v5_overlays, list) and bool(_v5_overlays)
                if _has_fact_overlay:
                    _scene["use_kling"] = False
                    continue
                _bill_idx = _billable_idx_map.get(_scene_idx)
                _scene["use_kling"] = (
                    _bill_idx is not None and _bill_idx in intro_motion_indices
                )
            logger.info(
                "use_kling 마킹 완료: 총 %d씬 중 %d씬 True (intro_motion_indices=%s)",
                len(scenes_meta),
                sum(1 for s in scenes_meta if s.get("use_kling")),
                sorted(intro_motion_indices),
            )
            # 아직 Fal/Kling을 호출하지 않는다. 승인될 최종 PNG를 기준으로
            # 움직일 사물과 고정할 영역을 미리 기록해, 후속 단계가 전 화면
            # 지글링을 기본 효과처럼 선택하지 못하게 한다.
            motion_plan_path = Path(f"/app/data/jobs/{job_id}/images/fal_motion_plan.json")
            motion_plan_path.parent.mkdir(parents=True, exist_ok=True)
            staged_motion_plan = motion_plan_path.with_suffix(".tmp")
            staged_motion_plan.write_text(
                json.dumps(build_fal_motion_preflight(scenes_meta), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(staged_motion_plan, motion_plan_path)

        # Visual direction is deliberately separate from script writing. One
        # coordinated request assigns a distinct role/costume/action to every
        # scene; a deterministic fallback keeps the pipeline runnable when
        # the director is temporarily unavailable.
        directed_specs: dict[int, SceneSpec] = {}
        article_evidence_indices = {index for index, scene in enumerate(scenes_meta) if _article_evidence_path(scene)}
        if scenes_meta:
            topic_context = " ".join(
                str(scene.get("title") or scene.get("section") or "") for scene in scenes_meta[:4]
            )
            lines = [
                (str(index), str(scene.get("content") or scene.get("text") or scene.get("prompt") or scene.get("title") or "시장 분석"))
                for index, scene in enumerate(scenes_meta)
                if index not in article_evidence_indices
            ]
            direction_cache_path = Path(f"/app/data/jobs/{job_id}/images/scene_direction_cache.json")
            direction_input_hash = hashlib.sha256(json.dumps(
                {"model": "claude-sonnet-4-6", "topic": topic_context, "lines": lines},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")).hexdigest()
            specs: list[SceneSpec] = []
            try:
                cached_direction = json.loads(direction_cache_path.read_text(encoding="utf-8"))
                if cached_direction.get("input_sha256") == direction_input_hash:
                    specs = [
                        SceneSpec(**item)
                        for item in cached_direction.get("specs") or []
                        if isinstance(item, dict)
                    ]
                    if len(specs) != len(lines):
                        specs = []
            except (OSError, ValueError, TypeError):
                specs = []
            # 이전 실행이 SceneDirector 후 장면별 최종 프롬프트까지
            # 저장한 뒤 중단됐다면 동일 대형 Anthropic 요청을 다시 보내지
            # 않는다. 이전 지시 캐시가 없던 작업은 최종 프롬프트를
            # 바꾸지 않는 결정론적 spec으로 계보를 승격한다.
            if not specs and lines:
                prompt_manifest_path = Path(f"/app/data/jobs/{job_id}/images/prompt_manifest.json")
                try:
                    prompt_manifest = json.loads(prompt_manifest_path.read_text(encoding="utf-8"))
                except (OSError, ValueError, TypeError):
                    prompt_manifest = {}
                if isinstance(prompt_manifest, dict) and all(
                    str(index) in prompt_manifest and str((prompt_manifest[str(index)] or {}).get("prompt_en") or "").strip()
                    for index in range(len(lines))
                ):
                    specs = [
                        fallback_spec(scene_id, narration, index)
                        for index, (scene_id, narration) in enumerate(lines)
                    ]
                    logger.info(
                        "완전한 프롬프트 캐시에서 SceneDirector 결정론적 지시를 복원: job=%s scenes=%s",
                        job_id, len(specs),
                    )
            if specs:
                if not direction_cache_path.is_file():
                    direction_cache_path.parent.mkdir(parents=True, exist_ok=True)
                    staged_direction = direction_cache_path.with_suffix(".tmp")
                    staged_direction.write_text(json.dumps({
                        "input_sha256": direction_input_hash,
                        "model": "claude-sonnet-4-6",
                        "specs": [spec.to_dict() for spec in specs],
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    os.replace(staged_direction, direction_cache_path)
                logger.info("SceneDirector 지시 캐시 재사용: job=%s scenes=%s", job_id, len(specs))
            elif lines:
                specs = SceneDirector().direct_batch(lines, topic_context=topic_context)
                direction_cache_path.parent.mkdir(parents=True, exist_ok=True)
                staged_direction = direction_cache_path.with_suffix(".tmp")
                staged_direction.write_text(json.dumps({
                    "input_sha256": direction_input_hash,
                    "model": "claude-sonnet-4-6",
                    "specs": [spec.to_dict() for spec in specs],
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(staged_direction, direction_cache_path)
            directed_specs = {int(spec.scene_id): spec for spec in specs if str(spec.scene_id).isdigit()}
            for index, scene in enumerate(scenes_meta):
                if spec := directed_specs.get(index):
                    scene["scene_spec"] = spec.to_dict()
                    scene["headline"] = spec.headline
            # V5 계약은 SceneDirector의 장면별 의상·행동·카메라 결정을 소비해야
            # 한다. 이전에는 V5 계약을 먼저 만든 뒤 director 결과를 붙여 실제
            # 프롬프트가 그 다양성을 무시했다.
            if all(scene.get("scene_type") for scene in scenes_meta):
                scenes_meta = attach_v5_scene_contracts(scenes_meta)

        # 사용자가 선택한 캐릭터 시트가 있어도 승인 화풍·장면 참조는 항상 병합한다.
        # 기존에는 명시 캐릭터 한 장만 전송되어 같은 대본에서도 화풍과 얼굴이
        # 흔들렸고, 참조 경로만 지문에 들어가 파일 교체도 감지하지 못했다.
        is_v5_final_batch = any(is_v5_final_lane_scene(scene) for scene in scenes_meta)
        selected_character_exists = bool(character_image_path and Path(character_image_path).exists())
        character_reference_paths = []
        default_references = _load_default_references()
        if selected_character_exists:
            # 사용자가 명시적으로 고른 캐릭터 참조만 별도 정체성 참고로 앞에
            # 두고, 실제 Job 52 장면에서 추출한 화풍 범위는 모두 유지한다.
            reference_candidates = [character_image_path, *default_references]
        else:
            reference_candidates = default_references
        for path in reference_candidates:
            if path and Path(path).exists() and path not in character_reference_paths:
                character_reference_paths.append(path)
        if character_reference_paths or selected_character_exists or character_poses_dir or lora_model_id:
            # When reference images/poses/LoRA are supplied, do not let
            # the provider inject its legacy generic mascot description.
            character_style_prompt = "none"

        # V3 canonical scene layers.  A pose library is the character's
        # identity lock: clean plate -> verified physical surface -> alpha
        # foreground character.  The legacy integrated path remains only for
        # old jobs without a library and is never marked identity-locked.
        use_composite = bool(character_poses_dir and Path(character_poses_dir).is_dir())
        if use_composite and any(is_v5_final_lane_scene(scene) for scene in scenes_meta):
            # V5는 Gemini가 완성한 동일 매체의 캐릭터·무대·primary 표면을
            # 한 장으로 받는다. 기존 포즈 PNG 합성은 이 계약을 깨므로 사용하지 않는다.
            use_composite = False
            logger.info("V5 final lane: legacy pose composite를 비활성화하고 Gemini 통합 씬을 사용합니다.")
        if use_composite:
            logger.info(f"[합성모드] 이중 레이어 합성 활성화: poses_dir={character_poses_dir}")
        else:
            logger.info("캐릭터 라이브러리 없음 → 일체형 모드")

        # LoRA 사용 여부 로깅
        if lora_model_id:
            logger.info(
                f"[LoRA 모드] 활성화: trigger_word={lora_trigger_word}, scale={lora_scale}"
            )

        # AI 이미지 프로바이더 로드
        ai_provider = None
        try:
            from app.providers.factory import get_image_provider
            ai_provider = get_image_provider()
            logger.info("일러스트 전용 모드 활성화: 모든 씬에 AI 캐릭터 일러스트 생성")
        except Exception as e:
            logger.warning(f"AI 이미지 프로바이더 로드 실패: {e}")

        job_dir = Path(f"/app/data/jobs/{job_id}/images")
        job_dir.mkdir(parents=True, exist_ok=True)

        def build_batch_scene(original: dict, index: int) -> dict:
            scene = enrich_scene_plan(original, index, len(scenes_meta))
            scene, template = _apply_info_scene_template(scene)
            narration = (
                scene.get("narration")
                or scene.get("script")
                or scene.get("narration_text")
                or scene.get("text_for_tts")
                or scene.get("content")
                or scene.get("text")
                or ""
            )
            spec = directed_specs.get(index)
            base_prompt = scene.get("prompt_en") or scene.get("prompt") or narration or scene.get("title") or ""
            prompt_en = (
                prompt_for_scene(scene)
                or (build_prompt(spec, scene.get("market_chart"), template) if spec else compile_editorial_prompt(scene, base_prompt))
            )
            return {
                **scene,
                "_job_id": job_id,
                "index": index,
                "section": scene.get("section", f"scene_{index}"),
                "prompt_en": prompt_en,
                "prompt_ko": scene.get("prompt_ko") or narration or scene.get("title") or "",
                "prompt": prompt_en,
                "pose": scene.get("pose", "neutral"),
                "visual_type": scene.get("visual_type"),
                "visual_plan": scene.get("visual_plan"),
                "art_direction": scene.get("art_direction") or {},
                "style_profile": scene.get("style_profile", "editorial_comic_2d"),
                "image_profile": scene.get("image_profile") or {},
                "market_snapshot": scene.get("market_snapshot") or market_snapshot,
                "market_chart": scene.get("market_chart"),
                "index_data": scene.get("index_data"),
                "bubble_text": scene.get("bubble_text", ""),
                "motion_type": scene.get("motion_type", ""),
                "text": narration,
                "headline": spec.headline if spec else scene.get("headline", ""),
                "headline_mood": spec.mood if spec else "neutral",
                "scene_spec": spec.to_dict() if spec else scene.get("scene_spec"),
            }

        # Batch is an explicitly selected economy mode. Interactive video
        # creation stays on synchronous Pro rendering and only falls back to
        # Batch after a retry-exhausted direct scene failure.
        use_pro_batch = (
            bool(runtime_config.value("gemini_pro_batch_enabled"))
            and runtime_config.value("image_provider") == "gemini"
            and bool(scenes_meta)
            and not article_evidence_indices
            # V5 운영 씬은 request 단위 가드·표준 tier·max_attempts=1을
            # 유지해야 하므로 기존 Batch 경로로 우회하지 않는다.
            and not any(is_v5_final_lane_scene(scene) for scene in scenes_meta)
            and all((scene.get("image_profile") or {}).get("tier") == "pro" for scene in scenes_meta)
            and not lora_model_id
        )
        if use_pro_batch:
            batch_scenes = [build_batch_scene(scene, i) for i, scene in enumerate(scenes_meta)]
            logger.info("Gemini Pro Batch submit: job=%s scenes=%s", job_id, len(batch_scenes))
            return gemini_batch.submit(job_id, batch_scenes, character_reference_paths)

        # Direct and character-composite renders use scene-specific files, so
        # both can safely share the same bounded worker pool.
        if (
            ai_provider
            and len(scenes_meta) > 1
            and not article_evidence_indices
            and bool(runtime_config.value("gemini_parallel_enabled"))
        ):
            return self._generate_parallel_scenes(
                scenes_meta=scenes_meta,
                directed_specs=directed_specs,
                market_snapshot=market_snapshot,
                character_reference_paths=character_reference_paths,
                character_style_prompt=character_style_prompt,
                lora_model_id=lora_model_id,
                lora_trigger_word=lora_trigger_word,
                lora_scale=lora_scale,
                ai_provider=ai_provider,
                job_dir=job_dir,
                job_id=job_id,
                use_composite=use_composite,
                character_poses_dir=character_poses_dir,
                budget_preflight=budget_preflight,
                entity_bindings=entity_bindings,
            )

        generated = []
        # Keep direct Pro 2K rendering interactive, but do not burst a long
        # sequence of paid requests into a transient rate/availability limit.
        last_pro_request_finished_at = None
        for i, scene in enumerate(scenes_meta):
            if is_job_stopped(job_id):
                raise RuntimeError(f"Job {job_id} stopped by user.")
            evidence_path = _article_evidence_path(scene)
            if evidence_path:
                if not Path(evidence_path).is_file() or Path(evidence_path).stat().st_size < 100:
                    raise RuntimeError(f"article evidence scene {i} has no valid captured image: {evidence_path}")
                # Do not send screenshots to Gemini/Fal or substitute them with
                # a generated still.  This keeps DOM coordinates exact and
                # makes the scene's generation cost objectively zero.
                generated.append({
                    **scene,
                    "index": i,
                    "image_path": evidence_path,
                    "generation_method": "article_evidence",
                    "generation_cost_krw": 0,
                    "use_kling": False,
                    "quality_score": 100,
                })
                logger.info("scene %s uses captured article evidence directly (Gemini/Kling skipped)", i)
                continue
            scene = enrich_scene_plan(scene, i, len(scenes_meta))
            scene, template = _apply_info_scene_template(scene)
            scene["_template_regeneration_enabled"] = (
                bool((budget_preflight or {}).get("template_regeneration_enabled"))
                and not is_v5_final_lane_scene(scene)
            )
            section = scene.get("section", f"scene_{i}")
            narration = (
                scene.get("narration")
                or scene.get("script")
                or scene.get("narration_text")
                or scene.get("text_for_tts")
                or scene.get("content")
                or scene.get("text")
                or ""
            )

            binding = binding_map.get(str(scene.get("scene_id"))) or binding_by_index.get(i)
            core_entities = binding.core_entities if binding else []
            core_figures = binding.core_figures if binding else []
            fictionalized_labels = binding.fictionalized_labels if binding else {}
            b_visual_mode = binding.suggested_visual_type if binding else (scene.get("visual_mode") or "general")

            scene["core_entities"] = core_entities
            scene["core_figures"] = core_figures
            scene["fictionalized_labels"] = fictionalized_labels

            art_direction = scene.get("art_direction") or {}
            character_required = bool(art_direction.get("character_required", True))
            spec = directed_specs.get(i)
            base_prompt = scene.get("prompt_en") or scene.get("prompt") or ""
            mismatch = flag_archetype_content_mismatch(scene)
            needs_rebuild = (
                bool(scene.get("prompt_needs_rebuild"))
                or not str(base_prompt).strip()
                or "Financial editorial scene representing" in str(base_prompt)
                or mismatch is not None
            )
            if needs_rebuild and narration:
                prompt_en = self._build_prompt_from_narration(
                    narration=narration,
                    scene_type=str(scene.get("archetype") or scene.get("scene_type") or scene.get("visual_type") or scene.get("section") or "general"),
                    title=str(scene.get("title") or f"scene_{i}"),
                    core_entities=core_entities,
                    core_figures=core_figures,
                    fictionalized_labels=fictionalized_labels,
                    visual_mode=b_visual_mode,
                    character_required=character_required,
                    screen_texts=list(scene.get("screen_texts") or []),
                )
                scene["prompt_en"] = prompt_en
                scene["prompt"] = prompt_en
                scene["grounding_score_pre"] = compute_grounding_score(prompt_en, core_entities)
                logger.info("씬 '%s': 대사 기반 프롬프트로 교체됨 (이유: %s, grounding_pre: %.2f)", scene.get("title"), "아키타입 미스매치 감지" if mismatch else "폴백/빈값 트리거", scene["grounding_score_pre"])
            else:
                prompt_en = (
                    prompt_for_scene(scene)
                    or (build_prompt(spec, scene.get("market_chart"), template) if spec else compile_editorial_prompt(scene, base_prompt))
                )
            base_generation_prompt = prompt_en
            prompt_ko = scene.get("prompt_ko") or narration or scene.get("title") or ""
            pose = scene.get("pose", "neutral")
            art_direction = scene.get("art_direction") or {}
            image_profile = scene.get("image_profile") or {}
            is_v5_scene = is_v5_final_lane_scene(scene)
            provider_options = v5_provider_options(scene)
            is_direct_pro_scene = is_v5_scene or (
                image_profile.get("tier") == "pro"
                and runtime_config.value("image_provider") == "gemini"
            )
            scene_market_snapshot = scene.get("market_snapshot") or market_snapshot
            character_required = bool(art_direction.get("character_required", True))
            effective_reference_paths = character_reference_paths if character_required else []
            pose_asset = art_direction.get("pose_asset") or pose
            # Keep a successful composite render on the normal quality path.
            # (The direct AI path assigns this inside its retry loop.)
            quality_score = 0

            img_path = str(job_dir / f"scene_{i:03d}.png")
            raw_img_path = str(job_dir / f"scene_{i:03d}_raw.png")
            scene["_request_job_id"] = job_id
            scene["_request_raw_path"] = raw_img_path
            scene.setdefault("index", i)
            background_path = None

            # If a long HTTP request was interrupted after this frame completed,
            # recover from disk instead of charging the image model a second time.
            if os.path.exists(img_path) and os.path.getsize(img_path) > 15000:
                self._apply_image_overlays(scene, img_path)
                resumed_clean_plate = str(job_dir / f"scene_{i:03d}_bg.png")
                if not Path(resumed_clean_plate).is_file():
                    resumed_clean_plate = None
                generated.append({
                    **scene,
                    "index": i, "section": section, "image_path": img_path,
                    "generation_method": "resumed_existing", "quality_score": 90,
                    "prompt_en": prompt_en, "prompt_ko": prompt_ko, "prompt": prompt_en,
                    "pose": pose, "visual_type": scene.get("visual_type"),
                    "visual_plan": scene.get("visual_plan"), "art_direction": art_direction,
                    "style_profile": scene.get("style_profile", "editorial_comic_2d"),
                    "image_profile": image_profile, "market_snapshot": scene_market_snapshot,
                    "market_chart": scene.get("market_chart"), "index_data": scene.get("index_data"),
                    "bubble_text": scene.get("bubble_text", ""),
                    "motion_type": scene.get("motion_type", ""),
                    "text": narration, "headline": spec.headline if spec else scene.get("headline", ""),
                    "headline_mood": spec.mood if spec else "neutral",
                    "scene_spec": spec.to_dict() if spec else scene.get("scene_spec"),
                    "clean_plate_path": resumed_clean_plate,
                    "asset_layout_metadata": _asset_layout_metadata(scene, resumed_clean_plate),
                })
                logger.info("Reusing completed image scene %s for job %s", i, job_id)
                continue

            if ai_provider:
                try:
                    effective_character_style = character_style_prompt if character_required else "none"
                    if use_composite and character_required:
                        # [S2-3] 이중 레이어 합성
                        generation_prompt = _bounded_text_generation_prompt(
                            base_generation_prompt, audit_target=scene,
                        )
                        bg_path = str(job_dir / f"scene_{i:03d}_bg.png")
                        background_path = bg_path
                        self._generate_background_layer(
                            ai_provider, generation_prompt, bg_path, section, pose, image_profile,
                            job_id=job_id, scene_key=f"image:{i}",
                        )
                        self._normalize_canvas(bg_path)
                        self._compose_layered_scene(
                            scene, bg_path, character_poses_dir, pose_asset, img_path, job_id,
                            fallback_pose=(scene.get("art_direction") or {}).get("fallback_pose") or pose,
                        )
                        _inspect_generated_textless_image(scene, img_path)
                            
                    else:
                        max_retries = max(3, min(int(runtime_config.value("gemini_retry_max")), 8))
                        text_rejections = 0
                        for attempt in range(max_retries):
                            if is_direct_pro_scene and last_pro_request_finished_at is not None:
                                delay_seconds = max(
                                    0.0,
                                    float(runtime_config.value("gemini_pro_request_delay_seconds")),
                                )
                                remaining_delay = delay_seconds - (
                                    time.monotonic() - last_pro_request_finished_at
                                )
                                if remaining_delay > 0:
                                    logger.info(
                                        "Pacing Gemini Pro request for job %s scene %s: waiting %.1fs",
                                        job_id, i, remaining_delay,
                                    )
                                    time.sleep(remaining_delay)
                            try:
                                generation_prompt = _bounded_text_generation_prompt(
                                    base_generation_prompt,
                                    retry=text_rejections > 0,
                                    audit_target=scene,
                                )
                                scene_reference_paths = (
                                    select_contextual_reference_paths(generation_prompt, effective_reference_paths)
                                    if is_direct_pro_scene and character_required else effective_reference_paths
                                )
                                ai_provider.generate_image(
                                    prompt=generation_prompt,
                                    output_path=raw_img_path,
                                    section=section,
                                    keyword=generation_prompt[:30],
                                    character_image_path=scene_reference_paths[0] if scene_reference_paths else None,
                                    character_image_paths=scene_reference_paths,
                                    character_style_prompt=effective_character_style,
                                    lora_model_id=lora_model_id,
                                    lora_trigger_word=lora_trigger_word,
                                    lora_scale=lora_scale,
                                image_provider=provider_options.get("image_provider", runtime_config.value("image_provider")),
                                openai_job_id=job_id,
                                openai_scene_key=f"image:{i}",
                                gemini_model=provider_options.get("gemini_model", image_profile.get("model")),
                                gemini_image_size=provider_options.get("gemini_image_size", image_profile.get("image_size")),
                                gemini_service_tier=provider_options.get("gemini_service_tier", runtime_config.value("gemini_service_tier")),
                                gemini_max_attempts=provider_options.get("gemini_max_attempts", runtime_config.value("gemini_pro_max_attempts")),
                                gemini_retry_base_seconds=runtime_config.value("gemini_pro_retry_base_seconds"),
                                gemini_request_audit=ProviderRequestAudit.for_job(
                                    job_id=job_id,
                                    scene_key=f"image:{i}",
                                    model=str(image_profile.get("model") or "gemini-3-pro-image"),
                                    service_tier=str(provider_options.get(
                                        "gemini_service_tier", runtime_config.value("gemini_service_tier")
                                    )),
                                ),
                                style_locked=bool(spec) or bool(provider_options.get("style_locked")),
                                suppress_legacy_style_lock=bool(provider_options.get("suppress_legacy_style_lock")),
                                gemini_reference_contract_declared=bool(provider_options.get("gemini_reference_contract_declared")),
                                )
                            finally:
                                if is_direct_pro_scene:
                                    last_pro_request_finished_at = time.monotonic()
                            # [S5] AI 품질 자동 검수
                            if os.path.exists(raw_img_path) and os.path.getsize(raw_img_path) > 15000:
                                try:
                                    _inspect_generated_textless_image(scene, raw_img_path)
                                except GeneratedImageTextDetectedError as exc:
                                    text_rejections += 1
                                    logger.warning(
                                        "씬 %s의 비승인/훼손 문자열을 감지해 해당 표면만 재생성함: %s",
                                        i, exc,
                                    )
                                    if text_rejections < 2:
                                        continue
                                    raise
                                # 이미지 위 요약 헤드라인은 소품 표면 계약을 깨고
                                # ASS 자막과 중복되므로 모든 운영 경로에서 합성하지 않는다.
                                import shutil
                                shutil.copy2(raw_img_path, img_path)
                                self._apply_image_overlays(scene, img_path)
                                # v4 보드 검출 실패는 이 경로에서만 한 번 더 생성한다.
                                # 일반 제공자 재시도와 분리해 비용/manifest 의미를 보존한다.
                                if scene.pop("_template_regeneration_request", None):
                                    regen_prompt = generation_prompt + " Regenerate this same template with the physical information surface much larger, unobstructed, and clearly separated from the character."
                                    ai_provider.generate_image(
                                        prompt=regen_prompt, output_path=raw_img_path, section=section, keyword=regen_prompt[:30],
                                        character_image_path=effective_reference_paths[0] if effective_reference_paths else None,
                                        character_image_paths=effective_reference_paths, character_style_prompt=effective_character_style,
                                        lora_model_id=lora_model_id, lora_trigger_word=lora_trigger_word, lora_scale=lora_scale,
                                        image_provider=runtime_config.value("image_provider"), gemini_model=image_profile.get("model"),
                                        openai_job_id=job_id, openai_scene_key=f"template_regen:{i}",
                                        gemini_image_size=image_profile.get("image_size"), gemini_service_tier=runtime_config.value("gemini_service_tier"),
                                        gemini_max_attempts=1, gemini_retry_base_seconds=runtime_config.value("gemini_pro_retry_base_seconds"),
                                        gemini_request_audit=ProviderRequestAudit.for_job(
                                            job_id=job_id,
                                            scene_key=f"template_regen:{i}",
                                            model=str(image_profile.get("model") or "gemini-3-pro-image"),
                                        ),
                                        style_locked=bool(spec),
                                    )
                                    _inspect_generated_textless_image(scene, raw_img_path)
                                    self._replace_with_regenerated_surface_source(scene, raw_img_path, img_path)
                                    # ProviderRequestAudit가 이 Gemini Pro 요청을 이미 원장에
                                    # 기록한다. 여기서 다시 record_cost를 호출하면 한 요청이
                                    # 두 번 합산된다.
                                    self._apply_image_overlays(scene, img_path)
                                quality_score = 95 if lora_model_id else 90
                                break
                            else:
                                logger.warning(f"씬 {i} 이미지 품질 미달/손상 (attempt {attempt+1}/{max_retries}), 자동 재생성 시도...")
                        if quality_score == 0:
                            raise RuntimeError("최대 재시도 후에도 고품질 이미지 생성 실패")

                    # Generate variations if hold time exceeds limit
                    duration = scene_duration_seconds(scene, float(runtime_config.value("scene_duration_sec")))
                    # V5 운영 씬은 한 요청이 한 검증 단위다. 노출 길이 때문에
                    # 자동 변주 이미지를 추가 호출하지 않고, 영상 조립 단계가 시간을 처리한다.
                    max_hold = float("inf") if is_v5_scene else float(runtime_config.value("max_image_hold_seconds"))
                    if duration > max_hold:
                        import math
                        num_vars = math.ceil(duration / max_hold)
                        for v in range(1, num_vars):
                            var_img_path = str(job_dir / f"scene_{i:03d}_var_{v}.jpg")
                            var_prompt = _bounded_text_generation_prompt(
                                base_generation_prompt + f", alternate visual angle version {v}",
                                audit_target=scene,
                            )
                            try:
                                if use_composite and character_required:
                                    var_bg_path = str(job_dir / f"scene_{i:03d}_bg_var_{v}.png")
                                    self._generate_background_layer(
                                        ai_provider, var_prompt, var_bg_path, section, pose, image_profile,
                                        job_id=job_id, scene_key=f"image:{i}:variation:{v}",
                                    )
                                    self._normalize_canvas(var_bg_path)
                                    
                                    self._compose_layered_scene(
                                        # A variation owns a distinct clean-plate source and
                                        # must not reuse the main still's detector provenance.
                                        dict(scene), var_bg_path, character_poses_dir, pose_asset, var_img_path, job_id,
                                        fallback_pose=(scene.get("art_direction") or {}).get("fallback_pose") or pose,
                                    )
                                else:
                                    var_raw_path = str(job_dir / f"scene_{i:03d}_raw_var_{v}.jpg")
                                    ai_provider.generate_image(
                                        prompt=var_prompt,
                                        output_path=var_raw_path,
                                        section=section,
                                        keyword=var_prompt[:30],
                                        character_image_path=effective_reference_paths[0] if effective_reference_paths else None,
                                        character_style_prompt=effective_character_style,
                                        lora_model_id=lora_model_id,
                                        image_provider=runtime_config.value("image_provider"),
                                        openai_job_id=job_id, openai_scene_key=f"image:{i}:variation:{v}",
                                        gemini_model=image_profile.get("model"),
                                        gemini_image_size=image_profile.get("image_size"),
                                        gemini_request_audit=ProviderRequestAudit.for_job(
                                            job_id=job_id,
                                            scene_key=f"image:{i}:variation:{v}",
                                            model=str(image_profile.get("model") or "gemini-3-pro-image"),
                                        ),
                                    )
                                    import shutil
                                    shutil.copy2(var_raw_path, var_img_path)
                                    self._apply_image_overlays(scene, var_img_path)
                                logger.info(f"Generated hold-time split variation {v} for scene {i} -> {var_img_path}")
                            except Exception as var_ex:
                                logger.warning(f"Failed to generate hold-time split variation {v} for scene {i}: {var_ex}. Copying original.")
                                import shutil
                                shutil.copy2(img_path, var_img_path)

                    # Gemini Pro 직접 호출은 ProviderRequestAudit가 단일 비용 원장이다.
                    generated.append({
                        **scene,
                        "index": i,
                        "section": section,
                        "image_path": img_path,
                        "background_path": background_path,
                        "clean_plate_path": background_path,
                        "asset_layout_metadata": _asset_layout_metadata(scene, background_path),
                        "generation_method": "composite" if use_composite else "pro_gemini",
                        "quality_score": quality_score or 85,
                        "prompt_en": prompt_en,
                        "prompt_ko": prompt_ko,
                        "prompt": prompt_en,
                        "pose": pose,
                        "visual_type": scene.get("visual_type"),
                        "visual_plan": scene.get("visual_plan"),
                        "art_direction": art_direction,
                        "style_profile": scene.get("style_profile", "editorial_comic_2d"),
                        "image_profile": image_profile,
                        "market_snapshot": scene_market_snapshot,
                        "market_chart": scene.get("market_chart"),
                        "index_data": scene.get("index_data"),
                        "bubble_text": scene.get("bubble_text", ""),
                        "motion_type": scene.get("motion_type", ""),
                        "text": narration,
                        "headline": spec.headline if spec else scene.get("headline", ""),
                        "headline_mood": spec.mood if spec else "neutral",
                        "scene_spec": spec.to_dict() if spec else scene.get("scene_spec"),
                    })
                    logger.info(f"씬 {i} AI 이미지 생성 및 품질 검수 완료 (점수={quality_score or 85})")
                    continue
                except Exception as e:
                    if isinstance(e, ImageRequestHeld):
                        write_request_review(job_dir, job_id, [{"index": i, "status": e.status,
                            "reason": e.reason, "next_allowed_at": e.next_allowed_at}])
                        raise
                    if image_profile.get("tier") == "pro":
                        if bool(runtime_config.value("gemini_pro_batch_fallback_enabled")) and not lora_model_id:
                            remaining = [
                                build_batch_scene(remaining_scene, remaining_index)
                                for remaining_index, remaining_scene in enumerate(scenes_meta[i:], start=i)
                            ]
                            logger.warning(
                                "Gemini Pro direct render exhausted retries at scene %s; "
                                "submitting %s remaining scenes to Batch fallback (completed=%s).",
                                i, len(remaining), len(generated),
                            )
                            return gemini_batch.submit(
                                job_id,
                                remaining,
                                character_reference_paths,
                                completed_scenes=generated,
                            )
                        # Do not publish the text-on-solid-background fallback
                        # when a reference-quality image request failed.
                        raise RuntimeError(f"Pro image scene {i} failed: {e}") from e
                    logger.warning(f"씬 {i} AI 이미지 실패, 폴백 Solid 배경 생성: {e}")


            # 로컬 폴백 (Matplotlib 고체 단색 배경 렌더링)
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            try:
                self._render_fallback(narration, img_path, plt)
                generated.append({
                    **scene,
                    "index": i,
                    "section": section,
                    "image_path": img_path,
                    "generation_method": "fallback_solid",
                    "prompt_en": prompt_en,
                    "prompt_ko": prompt_ko,
                    "prompt": prompt_en,
                    "pose": pose,
                    "visual_type": scene.get("visual_type"),
                    "visual_plan": scene.get("visual_plan"),
                    "art_direction": art_direction,
                    "style_profile": scene.get("style_profile", "editorial_comic_2d"),
                    "image_profile": image_profile,
                    "market_snapshot": scene_market_snapshot,
                    "market_chart": scene.get("market_chart"),
                    "index_data": scene.get("index_data"),
                    "bubble_text": scene.get("bubble_text", ""),
                    "motion_type": scene.get("motion_type", ""),
                    "text": narration,
                })
            except Exception as e:
                logger.error(f"씬 {i} 로컬 폴백 최종 실패: {e}")

        if len(generated) != len(scenes_meta):
            completed = {s["index"] for s in generated}
            write_request_review(job_dir, job_id, [{"index": i, "status": "needs_review"}
                for i in range(len(scenes_meta)) if i not in completed])
            raise ImageRequestHeld("미완료 장면 존재; 이미지 단계 완료 처리 차단")
        if (job_dir / "image_request_review.json").exists():
            write_request_review(job_dir, job_id, [])
        _apply_fal_motion_safety_contract(generated)
        for scene in generated:
            scene["character_regions"] = _character_regions(scene)
        image_quality = assess_images(generated)
        semantic_quality = assess_visual_alignment(
            generated,
            enabled=bool(runtime_config.value("visual_qa_enabled")),
            max_scenes=int(runtime_config.value("visual_qa_max_scenes")),
        )
        semantic_by_index = {item["index"]: item for item in semantic_quality.get("reviewed", [])}
        metrics_by_index = {
            metric["index"]: metric
            for metric in image_quality.get("scene_metrics", [])
        }
        for scene in generated:
            metric = metrics_by_index.get(scene.get("index"), {})
            scene["quality_score"] = metric.get("score", 0)
            scene["quality_flags"] = metric.get("warnings", [])
            scene["retry_recommended"] = metric.get("retry_recommended", False)
            semantic = semantic_by_index.get(scene.get("index"))
            if semantic:
                scene["semantic_score"] = semantic["score"]
                scene["semantic_reason"] = semantic["reason"]
                scene["quality_score"] = min(scene["quality_score"], semantic["score"])
                scene["retry_recommended"] = scene["retry_recommended"] or semantic["retry_recommended"]
                if semantic["retry_recommended"]:
                    scene["quality_flags"] = list(dict.fromkeys([
                        *scene["quality_flags"],
                        "semantic_review_recommended",
                        *semantic.get("failure_categories", []),
                    ]))
        image_quality["art_direction"] = assess_art_diversity(generated)
        image_quality["semantic_alignment"] = semantic_quality
        image_quality["scene_metadata_contract"] = _scene_metadata_contract(scenes_meta, generated)
        image_quality["visual_mix"] = _visual_mix_audit(generated)
        persist_quality_report(job_id, "images", image_quality)
        logger.info(f"이미지 생성 완료: {len(generated)}개, quality={image_quality['score']}")
        review_reasons = _manual_review_reasons(generated, image_quality)
        return {
            "job_id": job_id,
            "scenes": generated,
            "scene_count": len(generated),
            "gifs": [],
            "gif_count": 0,
            "quality_report": {"images": image_quality},
            "tts_subtitle_sync": self.tts_subtitle_sync,
            "evidence_audit": self.evidence_audit,
            "visual_mix_plan": self.visual_mix_plan,
            "operational_contract_audit": build_operational_contract_audit("images", checks={
                "tts_subtitle_sync_passed": self.tts_subtitle_sync.get("passed"),
                "scene_count": len(generated),
                "scene_metadata_contract_present": bool(image_quality.get("scene_metadata_contract")),
                "fal_preflight_attached_to_all": all(
                    isinstance(scene.get("fal_motion_safety"), dict) for scene in generated
                ),
                "manual_review_required": bool(review_reasons),
            }),
            "requires_manual_review": bool(review_reasons),
            "review_reasons": review_reasons,
        }

    def _generate_parallel_scenes(
        self, scenes_meta, directed_specs, market_snapshot,
        character_reference_paths, character_style_prompt,
        lora_model_id, lora_trigger_word, lora_scale,
        ai_provider, job_dir, job_id, use_composite=False, character_poses_dir=None,
        budget_preflight=None, entity_bindings=None,
    ) -> dict:
        """Render independent direct-AI scenes with bounded concurrency.

        Each task writes to a scene-specific raw file and only publishes the
        final path after validating a decodable image. A failed task never
        becomes a scene entry, and the method raises before quality/assembly
        when any scene failed. Re-running the job reuses validated outputs.
        """
        import shutil

        prompt_cache_path = Path(job_dir) / "prompt_manifest.json"
        request_run_id = uuid.uuid4().hex
        try:
            prompt_cache = json.loads(prompt_cache_path.read_text(encoding="utf-8"))
            if not isinstance(prompt_cache, dict):
                prompt_cache = {}
        except (OSError, ValueError, TypeError):
            prompt_cache = {}
        prompt_cache_dirty = False

        def valid_image(path: str) -> bool:
            try:
                if not os.path.exists(path) or os.path.getsize(path) <= 15000:
                    return False
                try:
                    from PIL import Image
                    with Image.open(path) as image:
                        image.load()
                except ImportError:
                    pass
                return True
            except (OSError, ValueError):
                return False

        def make_context(original: dict, index: int) -> dict:
            nonlocal prompt_cache_dirty
            binding_map = {str(b.scene_id): b for b in (entity_bindings or [])}
            binding_by_index = {b.scene_index: b for b in (entity_bindings or [])}
            binding = binding_map.get(str(original.get("scene_id"))) or binding_by_index.get(index)

            core_entities = binding.core_entities if binding else []
            core_figures = binding.core_figures if binding else []
            fictionalized_labels = binding.fictionalized_labels if binding else {}
            b_visual_mode = binding.suggested_visual_type if binding else (original.get("visual_mode") or "general")

            scene = enrich_scene_plan(original, index, len(scenes_meta))
            scene["core_entities"] = core_entities
            scene["core_figures"] = core_figures
            scene["fictionalized_labels"] = fictionalized_labels

            scene, template = _apply_info_scene_template(scene)
            scene["_template_regeneration_enabled"] = (
                bool((budget_preflight or {}).get("template_regeneration_enabled"))
                and not is_v5_final_lane_scene(scene)
            )
            narration = (
                scene.get("narration")
                or scene.get("script")
                or scene.get("narration_text")
                or scene.get("text_for_tts")
                or scene.get("content")
                or scene.get("text")
                or ""
            )
            art_direction = scene.get("art_direction") or {}
            character_required = bool(art_direction.get("character_required", True))
            spec = directed_specs.get(index)
            base_prompt = scene.get("prompt_en") or scene.get("prompt") or ""
            mismatch = flag_archetype_content_mismatch(scene)
            needs_rebuild = (
                bool(scene.get("prompt_needs_rebuild"))
                or not str(base_prompt).strip()
                or "Financial editorial scene representing" in str(base_prompt)
                or mismatch is not None
            )
            scene_type = str(scene.get("archetype") or scene.get("scene_type") or scene.get("visual_type") or scene.get("section") or "general")
            prompt_cache_key = _image_prompt_cache_key(
                index=index,
                narration=narration,
                scene_type=scene_type,
                visual_mode=str(b_visual_mode),
                character_required=character_required,
                core_entities=list(core_entities or []),
                core_figures=list(core_figures or []),
                screen_texts=list(scene.get("screen_texts") or []),
            )
            compatible_prompt_cache_keys = {
                prompt_cache_key,
                *(
                    _image_prompt_cache_key(
                        index=index,
                        narration=narration,
                        scene_type=scene_type,
                        visual_mode=str(b_visual_mode),
                        character_required=character_required,
                        core_entities=list(core_entities or []),
                        core_figures=list(core_figures or []),
                        screen_texts=list(scene.get("screen_texts") or []),
                        policy_version=version,
                    )
                    for version in PROMPT_CACHE_COMPATIBLE_VERSIONS
                ),
            }
            cached_prompt = prompt_cache.get(str(index))
            if (
                needs_rebuild
                and isinstance(cached_prompt, dict)
                and cached_prompt.get("key") in compatible_prompt_cache_keys
                and str(cached_prompt.get("prompt_en") or "").strip()
            ):
                prompt_en = str(cached_prompt["prompt_en"])
                scene["prompt_en"] = prompt_en
                scene["prompt"] = prompt_en
                if cached_prompt.get("key") != prompt_cache_key:
                    prompt_cache[str(index)] = {"key": prompt_cache_key, "prompt_en": prompt_en}
                    prompt_cache_dirty = True
                logger.info("씬 '%s': 승인 장면 프롬프트 캐시 재사용", scene.get("title"))
            elif needs_rebuild and narration:
                prompt_en = self._build_prompt_from_narration(
                    narration=narration,
                    scene_type=scene_type,
                    title=str(scene.get("title") or f"scene_{index}"),
                    core_entities=core_entities,
                    core_figures=core_figures,
                    fictionalized_labels=fictionalized_labels,
                    visual_mode=b_visual_mode,
                    character_required=character_required,
                    screen_texts=list(scene.get("screen_texts") or []),
                )
                scene["prompt_en"] = prompt_en
                scene["prompt"] = prompt_en
                scene["grounding_score_pre"] = compute_grounding_score(prompt_en, core_entities)
                prompt_cache[str(index)] = {"key": prompt_cache_key, "prompt_en": prompt_en}
                prompt_cache_dirty = True
                logger.info("씬 '%s': 대사 기반 프롬프트로 교체됨 (이유: %s, grounding_pre: %.2f)", scene.get("title"), "아키타입 미스매치 감지" if mismatch else "폴백/빈값 트리거", scene["grounding_score_pre"])
            else:
                prompt_en = (
                    prompt_for_scene(scene)
                    or (build_prompt(spec, scene.get("market_chart"), template) if spec else compile_editorial_prompt(scene, base_prompt))
                )
            image_profile = scene.get("image_profile") or {}
            return {
                **scene,
                "index": index,
                "section": scene.get("section", f"scene_{index}"),
                "prompt_en": prompt_en,
                "prompt_ko": scene.get("prompt_ko") or narration or scene.get("title") or "",
                "pose": scene.get("pose", "neutral"),
                "visual_type": scene.get("visual_type"),
                "visual_plan": scene.get("visual_plan"),
                "art_direction": scene.get("art_direction") or {},
                "style_profile": scene.get("style_profile", "editorial_comic_2d"),
                "image_profile": image_profile,
                "market_snapshot": scene.get("market_snapshot") or market_snapshot,
                "text": narration,
                "headline": spec.headline if spec else scene.get("headline", ""),
                "headline_mood": spec.mood if spec else "neutral",
                "scene_spec": spec.to_dict() if spec else scene.get("scene_spec"),
                "spec": spec,
                # §7: overlay keys — must be propagated or _apply_image_overlays silently skips
                "bubble_text": scene.get("bubble_text", ""),
                "motion_type": scene.get("motion_type", ""),
                "market_chart": scene.get("market_chart"),
                "index_data": scene.get("index_data"),
                "v5_provider_options": v5_provider_options(scene),
            }

        contexts = [make_context(scene, index) for index, scene in enumerate(scenes_meta)]
        if prompt_cache_dirty:
            staged_prompt_cache = prompt_cache_path.with_suffix(".tmp")
            staged_prompt_cache.write_text(
                json.dumps(prompt_cache, ensure_ascii=False, indent=2), encoding="utf-8",
            )
            os.replace(staged_prompt_cache, prompt_cache_path)
        manifest_path = job_dir / "images_manifest.json"
        try:
            image_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(image_manifest, dict):
                image_manifest = {}
        except (OSError, ValueError, TypeError):
            image_manifest = {}
        loaded_manifest_version = int(image_manifest.get("_fingerprint_version") or 1)
        visual_qa_cache_path = job_dir / "visual_qa_cache.json"
        try:
            visual_qa_cache = json.loads(visual_qa_cache_path.read_text(encoding="utf-8"))
            if not isinstance(visual_qa_cache, dict):
                visual_qa_cache = {}
        except (OSError, ValueError, TypeError):
            visual_qa_cache = {}
        for ctx in contexts:
            cached_review = visual_qa_cache.get(str(ctx["index"]))
            if isinstance(cached_review, dict):
                ctx["visual_qa_review"] = cached_review

        def fingerprint(ctx: dict) -> str:
            return _stable_image_lineage_fingerprint(
                ctx,
                character_style_prompt=character_style_prompt,
                character_reference_paths=character_reference_paths,
                use_composite=use_composite,
                character_poses_dir=character_poses_dir,
                lora_model_id=lora_model_id,
                lora_trigger_word=lora_trigger_word,
                lora_scale=lora_scale,
            )

        def render_one(ctx: dict) -> dict:
            index = ctx["index"]
            img_path = str(job_dir / f"scene_{index:03d}.png")
            raw_img_path = str(job_dir / f"scene_{index:03d}_raw.png")
            ctx["_request_job_id"] = job_id
            ctx["_request_raw_path"] = raw_img_path
            background_path = None
            image_profile = ctx["image_profile"]
            provider_options = ctx.get("v5_provider_options") or {}

            # 씬 유형 -> 아키타입 기본 구도 주입 및 프롬프트 검증
            SCENE_TYPE_TO_ARCHETYPE = {
                "character_hero": "briefing podium on stage, spotlight, financial presenter at center",
                "news_context": "retail shock market backdrop, news event setting, torn blank fabric ribbon",
                "data_visual": "risk control room with analog signal lamps, physical gauges, and colored light paths",
                "character_explainer": "classroom teaching wall with abstract shapes and arrows only",
                "comparison": "giant golden balance scale stage with two physical market weights",
                "takeaway": "briefing podium, illuminated presentation stage",
            }
            prompt_en = ctx.get("prompt_en", "")
            if not prompt_en or "Financial editorial scene representing 장면" in prompt_en or "Financial editorial scene representing" in prompt_en:
                logger.warning("scene %s has generic or empty prompt_en; applying archetype mapping & keyword visual metaphors", index)
                scene_type = ctx.get("visual_type") or ctx.get("section") or "character_hero"
                archetype_setting = SCENE_TYPE_TO_ARCHETYPE.get(scene_type, "briefing podium on stage, spotlight")
                narration_text = ctx.get("text") or ctx.get("prompt_ko") or ""
                
                metaphors = []
                if any(k in narration_text for k in ["패닉", "무너졌", "폭락", "급락"]):
                    metaphors.append("dark stormy market chaos with red falling arrows")
                elif any(k in narration_text for k in ["반등", "회복", "만회"]):
                    metaphors.append("golden recovery arc rising dramatically from cliff edge")
                elif any(k in narration_text for k in ["저울", "비교", "대비"]):
                    metaphors.append("giant golden balance scale with two glowing market orbs")
                elif any(k in narration_text for k in ["반도체", "amd", "실적"]):
                    metaphors.append("glowing semiconductor chip facility and circuit pathways")
                elif any(k in narration_text for k in ["금리"]):
                    metaphors.append("massive interest rate dial and financial gauge pointing up")
                elif any(k in narration_text for k in ["지수", "포인트", "마감"]):
                    metaphors.append("giant illuminated scoreboard displaying stock market results")
                elif any(k in narration_text for k in ["전망", "방향"]):
                    metaphors.append("foggy financial crossroads with diverging colored light paths")

                metaphor_str = ", ".join(metaphors) or "one clear physical economic metaphor with no written marks"
                prompt_en = f"{archetype_setting}, {metaphor_str}, dark navy tone, {STYLE_SUFFIX}"
                ctx["prompt_en"] = prompt_en

            character_required = bool(ctx["art_direction"].get("character_required", True))
            effective_reference_paths = character_reference_paths if character_required else []
            tier = image_profile.get("tier", "pro")
            scene_fingerprint = fingerprint(ctx)
            manifest_fingerprint = image_manifest.get(str(index))
            contract_rejections = 0
            provider_transient_failures = 0
            retry_feedback: dict | None = None
            retry_source_path = str(job_dir / f"scene_{index:03d}_rejected.png")
            full_regeneration_after_local_drift = False
            # 2026-09-29 사용자 결정: 메인 공급자(OpenAI)가 같은 장면에서 장면별
            # 표적 교정 예산을 다 써도 계약을 못 넘기면, 포기하기 전에 이 장면만
            # Fal.ai로 마지막 시도를 해본다. 다른 장면·다른 job에는 영향 없다.
            force_fal_last_resort = False

            def preserve_retry_source(source_path: str) -> None:
                """실패 프레임을 다음 요청의 국소 편집 기준으로 보존한다."""
                if valid_image(source_path):
                    shutil.copy2(source_path, retry_source_path)
            # 외부 이미지 응답 저장 직후 OCR/합성 단계에서 프로세스가 끊기면
            # 유효한 원본 PNG만 남을 수 있다. 같은 원본을 다시 과금하지 않고
            # 최신 문자 게이트를 통과한 경우에만 최종 PNG로 승격한다.
            if not valid_image(img_path) and valid_image(raw_img_path):
                try:
                    self._normalize_canvas(raw_img_path)
                    _inspect_generated_textless_image(ctx, raw_img_path)
                    shutil.copy2(raw_img_path, img_path)
                    self._apply_image_overlays(ctx, img_path)
                    _inspect_generated_visual_image(ctx, img_path)
                except GeneratedImageTextDetectedError as exc:
                    preserve_retry_source(raw_img_path)
                    contract_rejections += 1
                    retry_feedback = {
                        "failure_categories": ["text_unexpected_or_malformed"],
                        "reason": str(exc),
                    }
                    logger.warning("기존 scene %s 원본 PNG의 비승인/훼손 텍스트를 감지해 재생성함: %s", index, exc)
                except GeneratedImageVisualContractError as exc:
                    if not isinstance(exc, DeterministicSurfaceMissingError):
                        preserve_retry_source(img_path)
                    else:
                        full_regeneration_after_local_drift = True
                    contract_rejections += 1
                    retry_feedback = exc.review
                    logger.warning("기존 scene %s 원본 PNG의 비전 계약 위반을 감지해 재생성함: %s", index, exc)
                else:
                    return {
                        **ctx,
                        "image_path": img_path,
                        "clean_plate_path": None,
                        "asset_layout_metadata": _asset_layout_metadata(ctx, None),
                        "generation_method": "resumed_validated_raw",
                        "quality_score": 90,
                        "_fingerprint": scene_fingerprint,
                    }
            # v1은 비결정적인 LLM 영문 표현을 지문에 넣었기 때문에 동일 승인
            # 대본 재개도 전 장면을 재과금했다. 한 번만 기존 PNG를 엄격 OCR로
            # 재검증해 v2 안정 지문으로 승격한다.
            legacy_resume = loaded_manifest_version < IMAGE_LINEAGE_FINGERPRINT_VERSION and bool(manifest_fingerprint)
            if valid_image(img_path) and (
                manifest_fingerprint is None
                or manifest_fingerprint == scene_fingerprint
                or legacy_resume
            ) and contract_rejections == 0:
                # Validate/recreate the final composited still from its saved
                # source image on every resume entry point.
                # V5 검증 수치 오버레이는 원본 Gemini 출력 위에만 한 번 적용한다.
                # 이미 합성된 PNG에 재개 실행이 겹쳐 쓰면 값이 누적되므로, 보존한
                # raw 이미지를 먼저 복원한 뒤 최신 검증 사실을 다시 반영한다.
                source_for_ocr = raw_img_path if valid_image(raw_img_path) else img_path
                try:
                    self._normalize_canvas(source_for_ocr)
                    _inspect_generated_textless_image(ctx, source_for_ocr)
                    if _restore_raw_before_deterministic_overlay(ctx) and valid_image(raw_img_path):
                        shutil.copy2(raw_img_path, img_path)
                    self._apply_image_overlays(ctx, img_path)
                    _inspect_generated_visual_image(ctx, img_path)
                except GeneratedImageTextDetectedError as exc:
                    preserve_retry_source(source_for_ocr)
                    contract_rejections += 1
                    retry_feedback = {
                        "failure_categories": ["text_unexpected_or_malformed"],
                        "reason": str(exc),
                    }
                    logger.warning("기존 scene %s PNG의 비승인/훼손 텍스트를 감지해 재생성함: %s", index, exc)
                except GeneratedImageVisualContractError as exc:
                    if not isinstance(exc, DeterministicSurfaceMissingError):
                        preserve_retry_source(img_path)
                    else:
                        full_regeneration_after_local_drift = True
                    contract_rejections += 1
                    retry_feedback = exc.review
                    logger.warning("기존 scene %s PNG의 비전 계약 위반을 감지해 재생성함: %s", index, exc)
                else:
                    resumed_clean_plate = str(job_dir / f"scene_{index:03d}_bg.png")
                    if not Path(resumed_clean_plate).is_file():
                        resumed_clean_plate = None
                    return {**ctx, "image_path": img_path, "clean_plate_path": resumed_clean_plate,
                            "asset_layout_metadata": _asset_layout_metadata(ctx, resumed_clean_plate),
                            "generation_method": "resumed_existing", "quality_score": 90, "_fingerprint": scene_fingerprint}

            max_retries = max(3, min(int(runtime_config.value("gemini_retry_max")), 8))
            last_error = None
            for attempt in range(max_retries):
                if is_job_stopped(job_id):
                    raise RuntimeError(f"Job {job_id} stopped by user.")
                provider_request_started = False
                localized_edit = False
                try:
                    generation_prompt = _bounded_text_generation_prompt(
                        ctx["prompt_en"],
                        retry=contract_rejections > 0,
                        retry_feedback=retry_feedback,
                        audit_target=ctx,
                    )
                    scene_reference_paths = (
                        select_contextual_reference_paths(
                            generation_prompt,
                            effective_reference_paths,
                            # 동일 Pro 요청이 공급자 5xx를 한 차례 소진한 뒤에는
                            # 얼굴 범위 참조와 가장 가까운 장면 화풍 한 장만 남긴다.
                            # 모델·해상도·장면 계약은 유지하면서 요청 부담만 줄인다.
                            max_references=2 if provider_transient_failures else 3,
                        )
                        if is_v5_final_lane_scene(ctx) and character_required else effective_reference_paths
                    )
                    if provider_transient_failures and is_v5_final_lane_scene(ctx):
                        logger.info(
                            "Gemini Pro transient recovery: scene=%s references=%s prompt/model unchanged",
                            index,
                            len(scene_reference_paths),
                        )
                    localized_edit = (
                        contract_rejections > 0
                        and valid_image(retry_source_path)
                        and not full_regeneration_after_local_drift
                    )
                    if localized_edit:
                        scene_reference_paths = [
                            retry_source_path,
                            *(path for path in scene_reference_paths if path != retry_source_path),
                        ][:3]
                        generation_prompt = (
                            "The FIRST attached image is the previously rejected full scene. Edit that same frame locally. "
                            "Keep all successful composition, camera, palette, background, props, character identity, costume, pose, and 2D linework unchanged. "
                            "Apply only the scene-local corrections named below. Any later attached images are channel identity and style references.\n\n"
                            + generation_prompt
                        )
                        categories = set((retry_feedback or {}).get("failure_categories") or [])
                        review_raw = (retry_feedback or {}).get("raw") or {}
                        malformed_texts = list(review_raw.get("unexpected_or_malformed_texts") or [])
                        text_only_categories = {
                            "text_unexpected_or_malformed", "text_approved_duplicate",
                        }
                        if categories and categories.issubset(text_only_categories) and malformed_texts:
                            from app.services.local_text_repair import (
                                assess_deterministic_inpaint_surface,
                                inpaint_text_regions,
                                locate_malformed_text_regions,
                                text_regions_from_visual_review,
                            )

                            regions = text_regions_from_visual_review(review_raw, malformed_texts)
                            already_located = {region.text for region in regions}
                            missing_targets = [value for value in malformed_texts if value not in already_located]
                            if missing_targets:
                                regions.extend(locate_malformed_text_regions(
                                    retry_source_path,
                                    missing_targets,
                                    job_id=job_id,
                                    scene_key=f"local_text_repair:scene_{index:03d}",
                                ))
                            located_texts = {region.text for region in regions}
                            unlocated_texts = [value for value in malformed_texts if value not in located_texts]
                            surface_preflight = (
                                assess_deterministic_inpaint_surface(retry_source_path, regions)
                                if regions and not unlocated_texts else
                                {"passed": False, "reason": "incomplete_coordinates", "regions": []}
                            )
                            if not surface_preflight.get("passed"):
                                # 좌표가 정확해도 컬러 게이지·차트·직인처럼 복잡한
                                # 표면이면 인페인팅이 흐린 얼룩을 만든다. 해당 문자열
                                # 전체를 비전 국소 편집 경로로 넘긴다.
                                ctx["local_text_surface_preflight"] = surface_preflight
                                regions = []
                                unlocated_texts = list(malformed_texts)
                            if not regions or unlocated_texts:
                                # 좌표 판독이 불완전할 때 임의 상자를 지우지 않는다. 같은
                                # 승인 프롬프트와 원본 프레임을 사용하는 국소 비전 편집으로
                                # 넘기고, 아래 원본-후보 보존 게이트에서 전역 재구성을 막는다.
                                ctx["local_text_repair_fallback"] = {
                                    "reason": "safe_coordinates_unavailable",
                                    "unlocated_texts": unlocated_texts or malformed_texts,
                                }
                                approved_replacements = list(dict.fromkeys(
                                    str(value).strip()
                                    for value in (
                                        ((ctx.get("v5_render_contract") or {}).get("surface_caption") or {}).get("generated_texts")
                                        or []
                                    )
                                    if str(value).strip()
                                ))
                                generation_prompt += (
                                    "\n\nCoordinate-safe deterministic text repair was unavailable. "
                                    "Using the FIRST reference frame, correct only these malformed glyph clusters: "
                                    + json.dumps(unlocated_texts or malformed_texts, ensure_ascii=False)
                                    + ". The only exact replacement strings allowed on the same existing physical surfaces are: "
                                    + json.dumps(approved_replacements, ensure_ascii=False)
                                    + ". Keep every correct label and all non-text pixels, composition, character, props, palette, and linework unchanged. "
                                    "When the intended approved replacement is unambiguous, write it exactly in the original sign or monitor lettering style. "
                                    "Otherwise remove only the malformed glyphs and restore the underlying surface. Do not add a card or detached label."
                                )
                            else:
                                local_repair = inpaint_text_regions(
                                    retry_source_path,
                                    raw_img_path,
                                    regions,
                                )
                                _inspect_generated_textless_image(ctx, raw_img_path)
                                shutil.copy2(raw_img_path, img_path)
                                self._apply_image_overlays(ctx, img_path)
                                preservation = assess_local_edit_preservation(
                                    retry_source_path,
                                    img_path,
                                    allowed_change_categories=sorted(categories),
                                )
                                if preservation.get("status") != "completed":
                                    raise VisualQaUnavailableError(
                                        "결정론 문자 수리 원본-후보 비교를 완료하지 못함: "
                                        + str(preservation.get("warning") or "unknown")
                                    )
                                if not preservation.get("passed"):
                                    raise LocalEditPreservationError(
                                        "결정론 문자 수리가 비문제 영역을 변경함: "
                                        + str(preservation.get("reason") or ""),
                                        {
                                            "failure_categories": ["local_edit_scope_drift"],
                                            "reason": str(preservation.get("reason") or ""),
                                            "raw": preservation,
                                        },
                                    )
                                ctx["local_text_repair"] = local_repair
                                ctx["local_edit_preservation"] = preservation
                                _inspect_generated_visual_image(ctx, img_path)
                                Path(retry_source_path).unlink(missing_ok=True)
                                return {
                                    **ctx,
                                    "image_path": img_path,
                                    "background_path": background_path,
                                    "clean_plate_path": background_path,
                                    "asset_layout_metadata": _asset_layout_metadata(ctx, background_path),
                                    "generation_method": "deterministic_local_text_repair",
                                    "quality_score": 90,
                                    "_fingerprint": scene_fingerprint,
                                }
                    Path(raw_img_path).unlink(missing_ok=True)
                    if use_composite and character_required:
                        bg_path = str(job_dir / f"scene_{index:03d}_bg.png")
                        background_path = bg_path
                        self._generate_background_layer(
                            ai_provider, generation_prompt, bg_path, ctx["section"], ctx["pose"], image_profile,
                            job_id=job_id, scene_key=f"image:{index}",
                        )
                        if not valid_image(bg_path):
                            raise FinalImageValidationError(
                                "provider returned a missing, undersized, or invalid background",
                                {"failure_categories": ["final_image_invalid"], "reason": "invalid background"},
                            )
                        
                        self._normalize_canvas(bg_path)
                        self._compose_layered_scene(
                            ctx, bg_path, character_poses_dir,
                            ctx["art_direction"].get("pose_asset") or ctx["pose"], img_path, job_id,
                            fallback_pose=ctx["art_direction"].get("fallback_pose") or ctx["pose"],
                        )
                        _inspect_generated_textless_image(ctx, img_path)
                            
                    else:
                        gemini_pressure.acquire()
                        provider_request_started = True
                        ai_provider.generate_image(
                            prompt=generation_prompt,
                            output_path=raw_img_path,
                            section=ctx["section"],
                            keyword=generation_prompt[:30],
                            character_image_path=scene_reference_paths[0] if scene_reference_paths else None,
                            character_image_paths=scene_reference_paths,
                            character_style_prompt=character_style_prompt if character_required else "none",
                            lora_model_id=lora_model_id,
                            lora_trigger_word=lora_trigger_word,
                            lora_scale=lora_scale,
                            image_provider=_image_provider_for_attempt(
                                provider_options.get("image_provider", runtime_config.value("image_provider")),
                                attempt=attempt, max_retries=max_retries,
                                force_fal_last_resort=force_fal_last_resort,
                            ),
                            openai_job_id=job_id,
                            openai_scene_key=f"image:{index}",
                            gemini_model=provider_options.get("gemini_model", image_profile.get("model")),
                            gemini_image_size=provider_options.get("gemini_image_size", image_profile.get("image_size")),
                            gemini_service_tier=provider_options.get("gemini_service_tier", runtime_config.value("gemini_service_tier")),
                            gemini_max_attempts=provider_options.get("gemini_max_attempts", 1),
                            gemini_retry_base_seconds=runtime_config.value("gemini_pro_retry_base_seconds"),
                            gemini_request_audit=ProviderRequestAudit.for_job(
                                job_id=job_id,
                                scene_key=f"image:{index}",
                                model=str(image_profile.get("model") or "gemini-3-pro-image"),
                                contract_fingerprint=scene_fingerprint, run_id=request_run_id,
                                service_tier=str(provider_options.get(
                                    "gemini_service_tier", runtime_config.value("gemini_service_tier")
                                )),
                            ),
                            style_locked=bool(ctx["spec"]) or bool(provider_options.get("style_locked")),
                            suppress_legacy_style_lock=bool(provider_options.get("suppress_legacy_style_lock")),
                            gemini_reference_contract_declared=(
                                localized_edit
                                or bool(provider_options.get("gemini_reference_contract_declared"))
                            ),
                        )
                        if not valid_image(raw_img_path):
                            raise FinalImageValidationError(
                                "provider returned a missing, undersized, or invalid image",
                                {"failure_categories": ["final_image_invalid"], "reason": "invalid raw image"},
                            )
                        gemini_pressure.outcome()
                        provider_request_started = False

                        # 2026-09-29: Fal.ai(flux/schnell)는 1024x576 JPEG를
                        # .png 확장자로 반환해, 최종 1920x1080 캔버스와 형식·
                        # 크기가 어긋난 채로 검수·오버레이 단계로 넘어가던
                        # 문제가 있었다. 모든 공급자 출력을 오버레이 전에
                        # 표준 캔버스로 정규화한다(이미 표준 크기인 출력은
                        # _normalize_canvas가 그대로 통과시킨다).
                        self._normalize_canvas(raw_img_path)
                        _inspect_generated_textless_image(ctx, raw_img_path)
                        shutil.copy2(raw_img_path, img_path)
                        
                        self._apply_image_overlays(ctx, img_path)
                        if ctx.pop("_template_regeneration_request", None):
                            regen_prompt = generation_prompt + " Regenerate this same template with the blank physical board much larger, unobstructed, and clearly separated from the character."
                            ai_provider.generate_image(
                                prompt=regen_prompt, output_path=raw_img_path, section=ctx["section"], keyword=regen_prompt[:30],
                                character_image_path=effective_reference_paths[0] if effective_reference_paths else None,
                                character_image_paths=effective_reference_paths, character_style_prompt=character_style_prompt if character_required else "none",
                                lora_model_id=lora_model_id, lora_trigger_word=lora_trigger_word, lora_scale=lora_scale,
                                image_provider=runtime_config.value("image_provider"), gemini_model=image_profile.get("model"),
                                openai_job_id=job_id, openai_scene_key=f"template_regen:{index}",
                                gemini_image_size=image_profile.get("image_size"), gemini_service_tier=runtime_config.value("gemini_service_tier"),
                                gemini_max_attempts=1, gemini_retry_base_seconds=runtime_config.value("gemini_pro_retry_base_seconds"),
                                gemini_request_audit=ProviderRequestAudit.for_job(
                                    job_id=job_id,
                                    scene_key=f"template_regen:{index}",
                                    model=str(image_profile.get("model") or "gemini-3-pro-image"),
                                    contract_fingerprint=scene_fingerprint, run_id=request_run_id,
                                ),
                                style_locked=bool(ctx["spec"]),
                            )
                            self._normalize_canvas(raw_img_path)
                            _inspect_generated_textless_image(ctx, raw_img_path)
                            self._replace_with_regenerated_surface_source(ctx, raw_img_path, img_path)
                            # 재생성 요청도 ProviderRequestAudit가 이미 기록한다.
                            self._apply_image_overlays(ctx, img_path)
                        

                    if not valid_image(img_path):
                        raise FinalImageValidationError(
                            "final image validation failed",
                            {"failure_categories": ["final_image_invalid"], "reason": "final image validation failed"},
                        )
                    if localized_edit:
                        preservation = assess_local_edit_preservation(
                            retry_source_path,
                            img_path,
                            allowed_change_categories=list((retry_feedback or {}).get("failure_categories") or []),
                        )
                        if preservation.get("status") != "completed":
                            raise VisualQaUnavailableError(
                                "국소 수정 원본-후보 비교를 완료하지 못함: "
                                + str(preservation.get("warning") or "unknown")
                            )
                        ctx["local_edit_preservation"] = preservation
                        if not preservation.get("passed"):
                            raise LocalEditPreservationError(
                                "국소 수정이 비문제 영역을 변경함: "
                                + str(preservation.get("reason") or "원본 구도·얼굴·소품 보존 실패"),
                                {
                                    "failure_categories": ["local_edit_scope_drift"],
                                    "reason": str(preservation.get("reason") or ""),
                                    "raw": preservation,
                                },
                            )
                    _inspect_generated_visual_image(ctx, img_path)
                    Path(retry_source_path).unlink(missing_ok=True)
                    return {
                        **ctx,
                        "image_path": img_path,
                        "background_path": background_path,
                        "clean_plate_path": background_path,
                        "asset_layout_metadata": _asset_layout_metadata(ctx, background_path),
                        "generation_method": "flux_lora" if lora_model_id else f"{tier}_gemini",
                        "quality_score": 95 if lora_model_id else 90,
                        "_fingerprint": scene_fingerprint,
                    }
                except Exception as exc:
                    last_error = exc
                    if provider_request_started:
                        gemini_pressure.outcome(str(exc))
                    if isinstance(exc, ImageRequestHeld):
                        raise
                    if isinstance(exc, LocalEditPreservationError) and localized_edit:
                        # Gemini 국소 편집이 배경·팔레트·소품까지 재해석하면 그 후보는
                        # 폐기한다. 같은 실패 프레임을 다시 편집해 드리프트를 누적하지
                        # 않고, 원래 장면 프롬프트로 새 후보를 만든 뒤 장면 계약 전체를
                        # 다시 검수한다.
                        Path(retry_source_path).unlink(missing_ok=True)
                        full_regeneration_after_local_drift = True
                        contract_rejections = 0
                        logger.warning(
                            "Image scene %s local edit drifted; discarded candidate and switching to full scene regeneration: %s",
                            index,
                            exc,
                        )
                        continue
                    if isinstance(exc, VisualQaUnavailableError):
                        # 2026-10-01 job 13 scene 0 재현(job 12 scene 7과 동일 메시지로
                        # 기존에도 재현됨): 검수 연결 문제는 이 장면 하나의 문제이지
                        # 다른 장면과 무관하다. 예전에는 NonRetryableImageGenerationError로
                        # 바뀌어 대기 중인 다른 모든 scene의 futures까지 취소시켰다.
                        # 같은 이미지를 다시 과금해 만들지 않는다는 이 예외의 설계
                        # 의도(클래스 docstring)는 그대로 지키되, 장면 로컬 보류로 바꿔
                        # 나머지 장면은 계속 진행되게 한다.
                        raise ImageRequestHeld(
                            f"scene {index} 비전 검수 연결을 확인할 때까지 동일 이미지를 재생성하지 않음: {exc}"
                        ) from exc
                    if isinstance(exc, DeterministicSurfaceMissingError):
                        _audit_scene_quality(ctx, img_path, outcome="rejected", category="surface_composition")
                        Path(retry_source_path).unlink(missing_ok=True)
                        full_regeneration_after_local_drift = True
                        contract_rejections += 1
                        retry_feedback = exc.review
                        if contract_rejections >= 3:
                            raise ImageRequestHeld(
                                f"scene {index} 안전한 결정론 수치 표면을 3개 후보에서 찾지 못함: {exc}"
                            ) from exc
                        logger.warning(
                            "Image scene %s has no safe numeric surface; regenerating the full scene %s/3",
                            index,
                            contract_rejections,
                        )
                        continue
                    if isinstance(exc, (GeneratedImageTextDetectedError, GeneratedImageVisualContractError)):
                        review = exc.review if isinstance(exc, GeneratedImageVisualContractError) else {}
                        if _requires_full_scene_regeneration(review):
                            # 흐린 인물·녹은 패널·인페인팅 얼룩은 한 부분만 다시
                            # 그리면 주변 화풍과 구도가 더 흔들린다. 실패 프레임을
                            # 참조 편집하지 않고 동일 장면 계약으로 새 후보를 만든다.
                            Path(retry_source_path).unlink(missing_ok=True)
                            full_regeneration_after_local_drift = True
                        else:
                            preserve_retry_source(raw_img_path if valid_image(raw_img_path) else img_path)
                        contract_rejections += 1
                        retry_feedback = (
                            review
                            if isinstance(exc, GeneratedImageVisualContractError)
                            else {
                                "failure_categories": ["text_unexpected_or_malformed"],
                                "reason": str(exc),
                            }
                        )
                        if contract_rejections >= 3:
                            if not force_fal_last_resort and attempt < max_retries - 1:
                                force_fal_last_resort = True
                                contract_rejections = 0
                                full_regeneration_after_local_drift = True
                                logger.warning(
                                    "Image scene %s: 메인 공급자로 표적 교정 2회 후에도 계약을 통과하지 "
                                    "못해, 이 장면만 Fal.ai로 마지막 시도합니다: %s",
                                    index, exc,
                                )
                                continue
                            raise ImageRequestHeld(
                                f"scene {index} 장면별 표적 교정 2회 후에도 계약을 통과하지 못함"
                                f"{'(Fal.ai 대체 시도 포함)' if force_fal_last_resort else ''}: {exc}"
                            ) from exc
                        logger.warning(
                            "Image scene %s rejected by scene-local QA; targeted retry %s/2: %s",
                            index, contract_rejections, exc,
                        )
                        continue
                    if "image request exhausted" in str(exc).lower():
                        raise NonRetryableImageGenerationError(
                            f"scene {index} 공급자 내부 재시도를 이미 소진함; 바깥 루프에서 같은 장기 요청을 중첩 재시도하지 않음: {exc}"
                        ) from exc
                    decision = classify_image_error(exc)
                    if not decision.retryable:
                        raise NonRetryableImageGenerationError(
                            f"scene {index} stopped: non-retryable ({decision.reason}): {exc}"
                        ) from exc
                    # Gemini HTTP의 대기는 영속 요청 제어기만 소유한다.
                    # 감사 계약 밖의 예외도 로컬 루프로 재전송하지 않는다.
                    raise RuntimeError(f"감사되지 않은 공급자 오류: {type(exc).__name__}") from exc
            # 2026-10-02 job 13 scene 1 재현: 아래에서 원인 체인(from last_error)을
            # 지키지 않으면, 바깥 except 블록이 진짜 원인을 볼 수 없어 이 예외를
            # 무조건 "일시적 공급자 장애"로 취급한다. 실제로는 Gemini API 호출은
            # 매번 성공했고, 우리 쪽 콘텐츠 계약 검수만 반복해서 거부한 경우에도
            # "Gemini Pro 과부하"라는 잘못된 메시지로 둔갑했다.
            raise RuntimeError(
                f"scene {index} image generation failed after {max_retries} attempts"
                f"{' (Fal.ai 대체 시도 포함)' if force_fal_last_resort else ''}: {last_error}"
            ) from last_error

        configured_workers = max(1, min(int(runtime_config.value("gemini_max_concurrency")), 32))
        max_workers = gemini_pressure.recommended_concurrency(configured_workers)
        logger.info("Parallel image generation enabled: job=%s scenes=%s concurrency=%s retries=%s", job_id, len(contexts), max_workers, runtime_config.value("gemini_retry_max"))
        results = []
        held_scenes = []
        failures = []
        same_error_counts: Counter[str] = Counter()
        break_count = max(1, int(runtime_config.value("image_same_error_break_count")))
        def persist_manifest() -> None:
            image_manifest["_fingerprint_version"] = IMAGE_LINEAGE_FINGERPRINT_VERSION
            staged = manifest_path.with_suffix(".tmp")
            staged.write_text(json.dumps(image_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(staged, manifest_path)

        def persist_visual_qa_review(result: dict) -> None:
            review = result.get("visual_qa_review")
            if not isinstance(review, dict):
                return
            visual_qa_cache[str(result["index"])] = review
            staged = visual_qa_cache_path.with_suffix(".tmp")
            staged.write_text(json.dumps(visual_qa_cache, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(staged, visual_qa_cache_path)

        worker_count = min(max_workers, len(contexts))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="image") as pool:
            # 전체 씬을 미리 큐에 넣으면 첫 씬에서 회로 차단기가 열려도 worker가
            # 다음 씬을 먼저 꺼내 외부 요청을 시작할 수 있다. 실제 동시성만큼만
            # 제출하고 완료 결과를 확인한 뒤 한 장씩 보충한다.
            context_iter = iter(contexts)
            futures = {}

            def submit_next() -> bool:
                try:
                    next_ctx = next(context_iter)
                except StopIteration:
                    return False
                futures[pool.submit(render_one, next_ctx)] = next_ctx["index"]
                return True

            for _ in range(worker_count):
                if not submit_next():
                    break
                if worker_count > 1:
                    time.sleep(0.8)

            while futures:
                future = next(as_completed(tuple(futures)))
                index = futures.pop(future)
                try:
                    result = future.result()
                    persist_visual_qa_review(result)
                    image_manifest[str(index)] = result.pop("_fingerprint", "")
                    persist_manifest()
                    # 병렬 Gemini Pro 요청의 비용은 ProviderRequestAudit가 기록한다.
                    results.append(result)
                    logger.info("Parallel image scene complete: job=%s scene=%s", job_id, index)
                except Exception as exc:
                    failures.append((index, str(exc)))
                    logger.error("Parallel image scene failed: job=%s scene=%s error=%s", job_id, index, exc)
                    if isinstance(exc, ImageRequestHeld):
                        held_scenes.append({"index": index, "status": exc.status,
                                            "reason": exc.reason, "next_allowed_at": exc.next_allowed_at})
                        write_request_review(job_dir, job_id, held_scenes)
                        submit_next()
                        continue
                    if isinstance(exc, (NonRetryableImageGenerationError, VisualQaUnavailableError)):
                        for pending in futures:
                            pending.cancel()
                        root_cause = exc.__cause__ or exc
                        decision = classify_image_error(root_cause)
                        if decision.reason == "permanent provider billing/quota response":
                            raise ImageProviderCreditRequiredError(
                                "IMAGE_PROVIDER_CREDIT_REQUIRED: Gemini image credits or quota are unavailable. "
                                "Restore billing, then retry image generation; completed scenes will be reused."
                            ) from exc
                        raise RuntimeError(
                            f"Image generation stopped before recovery: scene {index} has a non-retryable error: {exc}"
                        ) from exc
                    root_cause = exc.__cause__ or exc
                    if not classify_image_error(root_cause).retryable:
                        # 2026-10-02 job 13 scene 1 재현: Gemini API 호출은 매번
                        # 성공했지만(공식 응답 성공 로그 다수), 우리 쪽 콘텐츠 계약
                        # 검수가 메인 공급자·국소 편집·Fal.ai 대체까지 전부 거부해
                        # 재시도 예산을 소진했다. 진짜 원인(root_cause)이 네트워크·
                        # 429·5xx 패턴이 아니면 실제 공급자 장애가 아니므로, 전체
                        # 배치를 "Gemini Pro 과부하"로 멈추지 않고 이 장면만
                        # 장면 로컬 보류로 넘겨 나머지 장면은 계속 진행되게 한다.
                        held_scenes.append({"index": index, "status": "needs_review",
                                            "reason": str(exc), "next_allowed_at": 0})
                        write_request_review(job_dir, job_id, held_scenes)
                        submit_next()
                        continue
                    signature = error_signature(root_cause)
                    same_error_counts[signature] += 1
                    if same_error_counts[signature] >= break_count:
                        for pending in futures:
                            pending.cancel()
                        raise ImageProviderTemporarilyUnavailableError(
                            "IMAGE_PROVIDER_TEMPORARILY_UNAVAILABLE: Gemini Pro 이미지 생성 서비스가 현재 과부하입니다. "
                            "완료된 씬과 비용 원장은 보존됐으며, 공급자 회복 후 이미지 생성만 다시 시도하면 이어서 진행됩니다. "
                            f"scene={index}, transient_failures={same_error_counts[signature]}"
                        ) from exc
                submit_next()

        # 별도 복구 라운드는 카운터를 초기화하므로 제거한다. 명시적 재개도 같은 원장을 사용한다.
        if failures:
            failed_indices = ", ".join(str(index) for index, _ in sorted(failures))
            raise RuntimeError(f"Image generation incomplete; failed scenes: {failed_indices}")

        if (job_dir / "image_request_review.json").exists():
            write_request_review(job_dir, job_id, [])

        generated = []
        for result in sorted(results, key=lambda item: item["index"]):
            result.pop("spec", None)
            generated.append(result)
        _apply_fal_motion_safety_contract(generated)
        for scene in generated:
            scene["character_regions"] = _character_regions(scene)
        image_quality = assess_images(generated)
        semantic_quality = assess_visual_alignment(
            generated,
            enabled=bool(runtime_config.value("visual_qa_enabled")),
            max_scenes=int(runtime_config.value("visual_qa_max_scenes")),
        )
        semantic_by_index = {item["index"]: item for item in semantic_quality.get("reviewed", [])}
        metrics_by_index = {metric["index"]: metric for metric in image_quality.get("scene_metrics", [])}
        for scene in generated:
            metric = metrics_by_index.get(scene.get("index"), {})
            scene["quality_score"] = metric.get("score", scene.get("quality_score", 0))
            scene["quality_flags"] = metric.get("warnings", [])
            scene["retry_recommended"] = metric.get("retry_recommended", False)
            semantic = semantic_by_index.get(scene.get("index"))
            if semantic:
                scene["semantic_score"] = semantic["score"]
                scene["semantic_reason"] = semantic["reason"]
                scene["quality_score"] = min(scene["quality_score"], semantic["score"])
                scene["retry_recommended"] = scene["retry_recommended"] or semantic["retry_recommended"]
                if semantic["retry_recommended"]:
                    scene["quality_flags"] = list(dict.fromkeys([
                        *scene["quality_flags"],
                        "semantic_review_recommended",
                        *semantic.get("failure_categories", []),
                    ]))
        image_quality["art_direction"] = assess_art_diversity(generated)
        image_quality["semantic_alignment"] = semantic_quality
        image_quality["scene_metadata_contract"] = _scene_metadata_contract(scenes_meta, generated)
        image_quality["visual_mix"] = _visual_mix_audit(generated)
        persist_quality_report(job_id, "images", image_quality)
        logger.info("Parallel image generation complete: job=%s scenes=%s quality=%s", job_id, len(generated), image_quality["score"])
        review_reasons = _review_reasons_including_held_scenes(
            _manual_review_reasons(generated, image_quality), held_scenes,
        )
        return {
            "job_id": job_id,
            "scenes": generated,
            "scene_count": len(generated),
            "gifs": [],
            "gif_count": 0,
            "quality_report": {"images": image_quality},
            "tts_subtitle_sync": self.tts_subtitle_sync,
            "budget_preflight": budget_preflight,
            "evidence_audit": self.evidence_audit,
            "visual_mix_plan": self.visual_mix_plan,
            "operational_contract_audit": build_operational_contract_audit("images", checks={
                "tts_subtitle_sync_passed": self.tts_subtitle_sync.get("passed"),
                "scene_count": len(generated),
                "scene_metadata_contract_present": bool(image_quality.get("scene_metadata_contract")),
                "fal_preflight_attached_to_all": all(
                    isinstance(scene.get("fal_motion_safety"), dict) for scene in generated
                ),
                "manual_review_required": bool(review_reasons),
            }),
            "requires_manual_review": bool(review_reasons),
            "review_reasons": review_reasons,
        }

    # ============================
    # [S2-3] 배경 레이어 생성
    # ============================
    def _generate_background_layer(self, ai_provider, prompt_en: str, bg_path: str,
                                    section: str, pose: str, image_profile: dict | None = None,
                                    *, job_id: int = 0, scene_key: str = "background:unknown"):
        """
        캐릭터 없는 순수 배경 이미지 생성.
        character_style_prompt="background_only" 를 전달해 캐릭터 주입을 차단.
        """
        gemini_pressure.acquire()
        try:
            ai_provider.generate_image(
            prompt=(
                f"{prompt_en} "
                "CLEAN PLATE REQUIREMENT: render the illustrated location and the physical blank information prop only. "
                "No mascot, no person, no face, no arms, no hands, no fingers, no pointer, and no character silhouette. "
                "A separately composited canonical foreground character will occupy the planned character side."
            ),
            output_path=bg_path,
            section=section,
            keyword=prompt_en[:30],
            character_style_prompt="background_only",
            image_provider=runtime_config.value("image_provider"),
            openai_job_id=job_id, openai_scene_key=scene_key,
            gemini_model=(image_profile or {}).get("model"),
            gemini_image_size=(image_profile or {}).get("image_size"),
            # Retries are owned by the bounded scene executor.  Keeping a
            # single provider attempt here prevents retry multiplication when
            # a character-composite scene is temporarily rate limited.
            gemini_service_tier=runtime_config.value("gemini_service_tier"),
            gemini_max_attempts=1,
            gemini_retry_base_seconds=runtime_config.value("gemini_pro_retry_base_seconds"),
            gemini_request_audit=ProviderRequestAudit.for_job(
                job_id=job_id,
                scene_key=scene_key,
                model=str((image_profile or {}).get("model") or "gemini-3-pro-image"),
            ),
            )
        except Exception as exc:
            gemini_pressure.outcome(str(exc))
            raise
        else:
            gemini_pressure.outcome()
        logger.info(f"[배경레이어] 생성 완료: {bg_path}")

    # ============================
    # [S2-3] 캐릭터 overlay 합성
    # ============================
    def _run_subprocess(self, cmd: list, job_id: int) -> int:
        import subprocess
        from app.utils.process_manager import register_process, unregister_process
        logger.info(f"Running tracked subprocess (composite): {' '.join(cmd)}")
        p = subprocess.Popen(cmd)
        register_process(job_id, p)
        try:
            ret = p.wait()
            return ret
        finally:
            unregister_process(job_id, p)

    def _composite_character(self, bg_path: str, poses_dir: str, pose: str,
                              output_path: str, job_id: int = 0,
                              fallback_pose: str = None,
                              x_ratio: float = CHAR_OVERLAY_X_RATIO,
                              y_ratio: float = CHAR_OVERLAY_Y_RATIO):
        """
        FFmpeg overlay 필터를 사용해 배경 이미지 위에 캐릭터 투명 PNG를 합성합니다.

        [S2-3] 합성 로직:
          1. 선택된 포즈 PNG (투명 RGBA)를 1080px 높이로 스케일
          2. 배경(1920x1080) 위에 우하단 중앙 위치로 overlay
          3. 합성 실패 시 배경만 출력 (Graceful fallback)
        """
        from app.services.scene_layers import compose_character_foreground

        placement = "left third" if x_ratio <= 0.10 else "right third"
        try:
            metadata = compose_character_foreground(
                bg_path, poses_dir, pose, output_path,
                fallback_pose=fallback_pose,
                placement=placement,
            )
            logger.info("[합성] alpha foreground 완료: %s", output_path)
            return metadata
        except Exception as exc:
            # Do not silently produce a different, model-generated character.
            # The clean plate remains usable, but the caller can record that
            # the canonical foreground layer was unavailable.
            logger.error("[합성] canonical foreground failed, using clean plate: %s", exc)
            import shutil
            shutil.copy2(bg_path, output_path)
            return None

    # ============================
    # 섹션별 시각화 라우팅
    # ============================
    def _render_section(self, section, text, img_path, plt):
        # AI 이미지가 생성되었을 경우, 기존의 Matplotlib 차트로 덮어씌우지 않습니다.
        # 이 메서드는 이제 사용되지 않지만 하위 호환성을 위해 남겨둡니다.
        pass

    # ── 인트로: 타이틀 카드 ──
    def _render_title_card(self, text, img_path, plt):
        fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(COLOR_BG)
        ax.set_facecolor(COLOR_BG)
        ax.axis("off")

        for r, alpha in [(4.5, 0.08), (3.5, 0.12), (2.5, 0.18)]:
            circle = plt.Circle((0.5, 0.55), r/10, color=COLOR_ACCENT_CYAN, alpha=alpha, transform=ax.transAxes)
            ax.add_patch(circle)

        title = self._extract_title(text, max_chars=20)
        ax.text(0.5, 0.55, title, fontsize=52, color=COLOR_TEXT, ha="center", va="center",
                weight="bold", transform=ax.transAxes)
        ax.text(0.5, 0.35, "📈 주식 시장 분석", fontsize=24, color=COLOR_ACCENT_GOLD,
                ha="center", va="center", transform=ax.transAxes)

        plt.savefig(img_path, facecolor=COLOR_BG, bbox_inches="tight")
        plt.close(fig)

    # ── 시장 배경: 지수 추이 라인 차트 ──
    def _render_line_chart(self, text, img_path, plt):
        import numpy as np
        signal = _extract_market_signal(text)

        fig, ax2 = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(COLOR_BG)

        ax2.set_facecolor(COLOR_BG2)
        days = np.arange(30)
        base = 2600
        if signal["direction"] == "up":
            drift = abs(np.random.randn()) * 3 + 1.5
            trend = np.cumsum(np.random.randn(30) * 6 + drift) + base
        elif signal["direction"] == "down":
            drift = -(abs(np.random.randn()) * 3 + 1.5)
            trend = np.cumsum(np.random.randn(30) * 6 + drift) + base
        else:
            trend = np.cumsum(np.random.randn(30) * 8) + base
        color = COLOR_ACCENT_GREEN if trend[-1] > trend[0] else COLOR_ACCENT_RED
        ax2.plot(days, trend, color=color, linewidth=3)
        ax2.fill_between(days, trend, trend.min() - 20, color=color, alpha=0.15)

        if signal["value"]:
            label = signal["value"]
            if signal["pct"] is not None:
                label += f" ({signal['pct']:+.2f}%)"
            ax2.text(0.98, 0.92, label, transform=ax2.transAxes, ha="right", va="top",
                      fontsize=24, color=COLOR_ACCENT_GOLD, weight="bold")

        short_title = self._extract_title(text, max_chars=20)
        ax2.set_title(short_title, color=COLOR_TEXT, fontsize=28, pad=20, weight="bold")
        ax2.tick_params(colors=COLOR_TEXT, labelsize=14)
        ax2.grid(color=COLOR_GRID, alpha=0.3)
        for spine in ax2.spines.values():
            spine.set_color(COLOR_GRID)

        plt.tight_layout()
        plt.savefig(img_path, facecolor=COLOR_BG, bbox_inches="tight")
        plt.close(fig)

    # ── 핵심 데이터: 캔들스틱 스타일 차트 ──
    def _render_candlestick(self, text, img_path, plt):
        import numpy as np
        signal = _extract_market_signal(text)

        fig, ax2 = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(COLOR_BG)

        ax2.set_facecolor(COLOR_BG2)
        n = 20
        if signal["direction"] == "up":
            drift = abs(np.random.randn()) * 2 + 1.0
        elif signal["direction"] == "down":
            drift = -(abs(np.random.randn()) * 2 + 1.0)
        else:
            drift = 0
        opens = np.cumsum(np.random.randn(n) * 5 + drift) + 2600
        closes = opens + np.random.randn(n) * 15
        if signal["direction"] == "up":
            closes[-1] = opens[-1] + abs(np.random.randn() * 15) + 5
        elif signal["direction"] == "down":
            closes[-1] = opens[-1] - abs(np.random.randn() * 15) - 5
        highs = np.maximum(opens, closes) + np.abs(np.random.randn(n) * 5)
        lows = np.minimum(opens, closes) - np.abs(np.random.randn(n) * 5)

        for i in range(n):
            color = COLOR_ACCENT_GREEN if closes[i] >= opens[i] else COLOR_ACCENT_RED
            ax2.plot([i, i], [lows[i], highs[i]], color=color, linewidth=1.5)
            ax2.add_patch(plt.Rectangle(
                (i - 0.35, min(opens[i], closes[i])), 0.7, abs(closes[i] - opens[i]),
                color=color
            ))

        if signal["value"]:
            label = signal["value"]
            if signal["pct"] is not None:
                label += f" ({signal['pct']:+.2f}%)"
            ax2.text(0.98, 0.92, label, transform=ax2.transAxes, ha="right", va="top",
                      fontsize=24, color=COLOR_ACCENT_GOLD, weight="bold")

        short_title = self._extract_title(text, max_chars=20)
        ax2.set_title(short_title, color=COLOR_TEXT, fontsize=28, pad=20, weight="bold")
        ax2.tick_params(colors=COLOR_TEXT, labelsize=14)
        ax2.grid(color=COLOR_GRID, alpha=0.3)
        for spine in ax2.spines.values():
            spine.set_color(COLOR_GRID)

        plt.tight_layout()
        plt.savefig(img_path, facecolor=COLOR_BG, bbox_inches="tight")
        plt.close(fig)

    # ── 시나리오: 상승/하락 분기 ──
    def _render_scenario_split(self, text, img_path, plt):
        fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(COLOR_BG)
        ax.set_facecolor(COLOR_BG)
        ax.axis("off")
        ax.set_xlim(0, 10); ax.set_ylim(0, 10)

        short_title = self._extract_title(text, max_chars=22)
        ax.text(5, 9.2, short_title, fontsize=26, color=COLOR_TEXT, ha="center", weight="bold")

        ax.add_patch(plt.Rectangle((0.5, 1), 4, 6.5, facecolor=COLOR_ACCENT_GREEN, alpha=0.15,
                                     edgecolor=COLOR_ACCENT_GREEN, linewidth=2))
        ax.text(2.5, 6.5, "▲ 상승 시나리오", fontsize=24, color=COLOR_ACCENT_GREEN, ha="center", weight="bold")
        ax.text(2.5, 4, "외국인 순매수 지속\n거래량 증가\n저항선 돌파", fontsize=18, color=COLOR_TEXT, ha="center")

        ax.add_patch(plt.Rectangle((5.5, 1), 4, 6.5, facecolor=COLOR_ACCENT_RED, alpha=0.15,
                                     edgecolor=COLOR_ACCENT_RED, linewidth=2))
        ax.text(7.5, 6.5, "▼ 하락 시나리오", fontsize=24, color=COLOR_ACCENT_RED, ha="center", weight="bold")
        ax.text(7.5, 4, "기관 매도 전환\n거래량 감소\n지지선 이탈", fontsize=18, color=COLOR_TEXT, ha="center")

        plt.savefig(img_path, facecolor=COLOR_BG, bbox_inches="tight")
        plt.close(fig)

    # ── 실행 가이드: 체크리스트 ──
    def _render_checklist(self, text, img_path, plt):
        fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(COLOR_BG)
        ax.set_facecolor(COLOR_BG)
        ax.axis("off")
        ax.set_xlim(0, 10); ax.set_ylim(0, 10)

        short_title = self._extract_title(text, max_chars=22)
        ax.text(5, 9.2, short_title, fontsize=28, color=COLOR_ACCENT_GOLD, ha="center", weight="bold")

        items = ["거래량 변화 확인", "외국인·기관 매매 동향", "주요 지지·저항선", "글로벌 지수 연동성"]
        for i, item in enumerate(items):
            y = 6.5 - i * 1.5
            ax.add_patch(plt.Circle((1.5, y), 0.3, facecolor=COLOR_ACCENT_CYAN))
            ax.text(1.5, y, "✓", fontsize=20, color=COLOR_BG, ha="center", va="center", weight="bold")
            ax.text(2.5, y, item, fontsize=22, color=COLOR_TEXT, va="center")

        plt.savefig(img_path, facecolor=COLOR_BG, bbox_inches="tight")
        plt.close(fig)

    # ── 결론: 요약 카드 ──
    def _render_summary_card(self, text, img_path, plt):
        fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(COLOR_BG)
        ax.set_facecolor(COLOR_BG)
        ax.axis("off")
        ax.set_xlim(0, 10); ax.set_ylim(0, 10)

        ax.text(5, 9.2, "오늘의 핵심 정리", fontsize=36, color=COLOR_ACCENT_GOLD, ha="center", weight="bold")

        points = ["✅ 시장 핵심 데이터 확인", "✅ 상승·하락 시나리오 분석", "✅ 다음 영상도 구독하세요!"]
        for i, pt in enumerate(points):
            y = 6.0 - i * 1.8
            ax.text(5, y, pt, fontsize=26, color=COLOR_TEXT, ha="center", va="center", weight="bold")

        plt.savefig(img_path, facecolor=COLOR_BG, bbox_inches="tight")
        plt.close(fig)

    # ── 폴백: 단색 배경 ──
    def _render_fallback(self, text, img_path, plt):
        fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
        fig.patch.set_facecolor(COLOR_BG)
        ax.set_facecolor(COLOR_BG)
        ax.axis("off")
        wrapped = self._wrap_text(text, 20)
        ax.text(0.5, 0.5, wrapped, fontsize=28, color=COLOR_TEXT, ha="center", va="center",
                transform=ax.transAxes)
        plt.savefig(img_path, facecolor=COLOR_BG, bbox_inches="tight")
        plt.close(fig)

    @staticmethod
    def _extract_title(text: str, max_chars: int = 20) -> str:
        """텍스트에서 첫 문장(또는 첫 max_chars자)만 추출하여 제목으로 사용"""
        import re
        if not text:
            return ""
        first_sent = re.split(r'(?<=[다요죠네.!?])\s', text.strip())[0]
        first_sent = first_sent.strip()
        if len(first_sent) <= max_chars:
            return first_sent
        return first_sent[:max_chars] + "…"

    @staticmethod
    def _wrap_text(text: str, width: int) -> str:
        import textwrap
        return "\n".join(textwrap.wrap(text, width=width))

    def _apply_chart_only(self, scene: dict, img_path: str):
        # v4 템플릿 장면은 검출한 보드에 직접 워프한다. 레거시 크림 패널
        # 정리 함수가 호출되면 안 된다.
        if scene.get("info_scene_template"):
            return
        if scene.get("section") == "data":
            from app.utils.market_charts import extract_market_chart
            from PIL import Image
            chart_data = extract_market_chart(scene)
            if chart_data:
                # 데이터가 확보된 씬에서만 패널을 정리한다.
                self._check_panel_blank_qc(img_path)
                
                pie = chart_data.get("market_cap_pie", [])
                points = chart_data.get("points", [])
                
                payload = {
                    "title": chart_data.get("label", "주요 지표"),
                    "as_of": chart_data.get("source_date", "2026-07-20"),
                    "unit": "%" if pie else "pt",
                }
                if pie:
                    payload["type"] = "donut"
                    payload["items"] = [{"name": item["label"], "value": item["value"]} for item in pie]
                elif len(points) >= 5:
                    payload["type"] = "line_trend"
                    payload["items"] = [{"name": item["date"], "value": item["close"]} for item in points]
                else:
                    payload["type"] = "big_number"
                    payload["value_str"] = f"{chart_data.get('latest', 0):,}"
                    payload["change"] = "up" if chart_data.get("change_pct", 0) >= 0 else "down"
                    payload["change_value"] = f"{abs(chart_data.get('change_pct', 0))}%"
                
                try:
                    overlay_img = render_chart_to_overlay(payload)
                    base_img = Image.open(img_path).convert("RGBA")
                    
                    if base_img.size != overlay_img.size:
                        logger.warning("overlay size mismatch %s vs %s — resizing base",
                                       base_img.size, overlay_img.size)
                        base_img = base_img.resize(overlay_img.size, Image.Resampling.LANCZOS)
                        
                    composited = Image.alpha_composite(base_img, overlay_img)
                    composited.convert("RGB").save(img_path, "JPEG")
                    logger.info(f"Data chart baked into image: {img_path}")
                except Exception as ex:
                    logger.error(f"Failed to render/composite data chart: {ex}")

    def _apply_image_overlays(self, scene: dict, img_path: str):
        """Finalize a still before any Ken Burns/video composition.

        A verified data visual is written into its detected physical prop here
        so the entire image moves together. Longform must never add a fixed
        coordinate chart over a moving background.
        """
        from app.services.scene_layers import load_layer_manifest

        if scene.get("text_render_policy") == "semantic_roles_v1":
            from app.services.semantic_surface_text import render_semantic_surface_text
            if scene.get("layered_scene") or load_layer_manifest(img_path):
                raise ValueError("의미형 문자 계약의 레이어 원근 연결은 별도 검증이 필요합니다.")
            render_semantic_surface_text(scene, img_path)
            return

        # Resume must rebuild the final image from the prepared surface, not
        # write chart ink over an already-composited hand or finger.
        layered = scene.get("layered_scene") or load_layer_manifest(img_path)
        if isinstance(layered, dict) and layered.get("surface_image_path"):
            self._resume_layered_scene(scene, img_path, layered)
            self._apply_verified_fact_overlay(scene, img_path)
            self._apply_deterministic_surface_caption(scene, img_path)
            scene["final_frame_text_integrity"] = require_final_frame_text_integrity(img_path, scene)
            return
        self._normalize_canvas(img_path)
        # AI 원본 글자는 사용하지 않는다. 정보형 장면의 정확한 한글 문구와
        # 검증 사실은 후처리로 합성하고 최종 OCR까지 통과해야 한다.
        if isinstance(scene.get("v5_render_contract"), dict):
            self._apply_verified_fact_overlay(scene, img_path)
            self._apply_deterministic_surface_caption(scene, img_path)
            scene["final_frame_text_integrity"] = require_final_frame_text_integrity(img_path, scene)
            return
        # 기존 chart warp/data-cutaway 경로는 운영 이미지 생성에서 사용하지 않는다.
        return

    def _apply_deterministic_surface_caption(self, scene: dict, img_path: str) -> None:
        """검증 수치가 없는 정보형 장면에 승인 대본 기반 한글 문구를 합성한다."""
        overlays = scene.get("v5_verified_overlays")
        if isinstance(overlays, list) and overlays:
            # 같은 표면에는 검증 사실 렌더러가 label/value를 담당한다.
            return
        contract = scene.get("v5_render_contract")
        if not isinstance(contract, dict) or contract.get("visual_text_policy") != "deterministic_surface_text":
            return
        caption = contract.get("surface_caption")
        region = contract.get("primary_surface_region")
        text = str(caption.get("korean") or "").strip() if isinstance(caption, dict) else ""
        if not text:
            raise ValueError("결정론 표면 문구 계약에 한글 문구가 없습니다.")
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ValueError("결정론 표면 문구 계약에 물리 표면 좌표가 없습니다.")
        surface_plan = [
            item for item in (caption.get("surface_plan") or [])
            if isinstance(item, dict) and any(char.isdigit() for char in str(item.get("text") or ""))
        ] if isinstance(caption, dict) else []
        planned_surface_kinds = {
            str(item.get("surface_kind") or "").strip() for item in surface_plan
            if str(item.get("surface_kind") or "").strip()
        }
        if len(planned_surface_kinds) > 1:
            raise ValueError("한 번의 결정론 문구 합성에 서로 다른 표면 종류가 섞여 있습니다.")
        planned_surface_kind = next(iter(planned_surface_kinds), "board")
        planned_surface_ids = list(dict.fromkeys(
            str(item.get("surface_id") or item.get("surface") or "").strip()
            for item in surface_plan
            if str(item.get("surface_id") or item.get("surface") or "").strip()
        ))
        if len(surface_plan) > 1:
            raise ValueError("한 번의 결정론 문구 합성에 여러 의미 표면 계획이 섞여 있습니다.")
        planned_item = surface_plan[0] if surface_plan else None
        from app.postprocess.text_overlay import add_surface_caption
        from app.services.overlay.surface_detector import detect_surface_for_plan

        # 아키타입 좌표는 생성 프롬프트의 구도 가이드일 뿐 실제 생성 결과의
        # 모니터 위치가 아니다. 최종 PNG 안에서 빈 물리 표면을 다시 찾아야
        # 지구본·캐릭터 위에 거대한 글자가 얹히는 일을 막을 수 있다.
        detection = detect_surface_for_plan(
            img_path,
            planned_surface_kind,
            plan_item=planned_item,
            job_id=int(scene.get("_job_id") or 0) or None,
            scene_key=f"deterministic_surface:{scene.get('index', scene.get('scene_id', 'unknown'))}",
        )
        if detection is not None:
            xs = [point[0] for point in detection.quad]
            ys = [point[1] for point in detection.quad]
            detected_region = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            inset_x = detected_region[2] * .06
            inset_y = detected_region[3] * .08
            region = (
                detected_region[0] + inset_x,
                detected_region[1] + inset_y,
                detected_region[2] - inset_x * 2,
                detected_region[3] - inset_y * 2,
            )
            scene["deterministic_surface_detection"] = {
                "strategy": detection.strategy,
                "confidence": detection.confidence,
                "quad": [list(point) for point in detection.quad],
                "resolved_region": list(region),
                "planned_surface_kind": planned_surface_kind,
                "planned_surface_ids": planned_surface_ids,
                "planned_surface_description": (
                    str((planned_item or {}).get("surface_description") or "").strip()
                ),
            }
        else:
            scene["deterministic_surface_detection"] = {
                "strategy": "rejected_no_safe_surface",
                "confidence": 0.0,
                "planned_surface_kind": planned_surface_kind,
                "planned_surface_ids": planned_surface_ids,
                "planned_surface_description": (
                    str((planned_item or {}).get("surface_description") or "").strip()
                ),
            }
            raise DeterministicSurfaceMissingError(
                "캐릭터·기존 글자와 겹치지 않는 테두리 있는 빈 물리 표면을 찾지 못했습니다.",
                {
                    "failure_categories": ["deterministic_surface_missing"],
                    "reason": "안전한 빈 모니터/보드가 없어 정적 좌표 폴백을 금지하고 전체 장면을 다시 생성합니다.",
                    "raw": {"deterministic_surface_detection": scene["deterministic_surface_detection"]},
                },
            )

        render_metadata = add_surface_caption(
            img_path, text, tuple(float(value) for value in region),
        )
        scene["deterministic_text_regions"] = [render_metadata]
        logger.info(
            "scene %s deterministic surface caption baked: %s",
            scene.get("scene_id") or scene.get("id") or "?",
            text,
        )

    def _apply_verified_fact_overlay(self, scene: dict, img_path: str) -> None:
        """검증 수치 오버레이 계획이 있는 V5 장면에만 소품 표면 텍스트를 합성한다.

        ``v5_verified_overlays``는 ``runtime_contract.attach_v5_scene_contracts``가
        이 장면이 실제로 그려진 archetype의 primary 표면 안에서만 만든 계획이다.
        여기서는 좌표·값을 가공하지 않고, 렌더 직전 마지막 검증
        (``facts_from_verified_scene``)을 통과해야만 실제로 합성한다. 검증
        실패는 조용히 삼키지 않는다: 00 문서의 "조용한 폴백 금지" 원칙에 따라
        예외를 그대로 올려 이 씬의 이미지 생성이 실패로 이어지게 한다.
        """
        overlays = scene.get("v5_verified_overlays")
        if not isinstance(overlays, list) or not overlays:
            return
        from app.v5.overlay.diegetic_fact_overlay import apply_verified_scene_facts

        with open(img_path, "rb") as handle:
            source_bytes = handle.read()
        with Image.open(io.BytesIO(source_bytes)) as probe:
            original_format = probe.format or "PNG"
        rendered_png = apply_verified_scene_facts(source_bytes, scene)
        with Image.open(io.BytesIO(rendered_png)) as rendered:
            if original_format in {"JPEG", "JPG"}:
                rendered.convert("RGB").save(img_path, "JPEG", quality=95)
            else:
                rendered.save(img_path, original_format)
        logger.info(
            "scene %s verified fact overlay baked: %d surface value(s) -> %s",
            scene.get("scene_id") or scene.get("id") or "?",
            len(overlays),
            img_path,
        )

    def _replace_with_regenerated_surface_source(self, scene: dict, raw_img_path: str, img_path: str) -> None:
        """재생성된 보드를 다음 검출·워프의 새 원본으로 확정한다.

        v4 보드 검출 실패 뒤 재생성하는 경우 이전 ``*_source`` 파일을 계속
        사용하면 새 보드를 검사하지 못하고 빈 패널이 최종 PNG에 남는다.
        재생성본을 최종 캔버스로 정규화한 뒤 source와 fingerprint를 함께
        교체해, 한 번만 허용된 재생성도 실제 합성 결과로 이어지게 한다.
        """
        import shutil

        shutil.copy2(raw_img_path, img_path)
        self._normalize_canvas(img_path)
        final_path = Path(img_path)
        source_path = final_path.with_name(f"{final_path.stem}_source{final_path.suffix}")
        shutil.copy2(final_path, source_path)
        scene["source_image_path"] = str(source_path)
        scene.pop("info_surface_final_fingerprint", None)
        scene.pop("info_surface_final_sha256", None)
        scene.pop("final_image_path", None)

    def _compose_layered_scene(
        self,
        scene: dict,
        background_path: str,
        poses_dir: str,
        pose_asset: str,
        output_path: str,
        job_id: int,
        *,
        fallback_pose: str | None = None,
    ) -> str:
        """Assemble `plate -> verified surface -> canonical foreground`.

        This is intentionally the only path for a pose-library scene.  The
        foreground alpha layer is placed *after* the chart warp, so a palm,
        glove, finger, or pointer cannot be painted over by a later graphic.
        """
        import shutil
        from app.services.scene_layers import write_layer_manifest

        self._normalize_canvas(background_path)
        direction = scene.get("art_direction") or {}
        if direction.get("character_required", True):
            placement_x = 0.02 if "left" in str(direction.get("character_placement") or "").lower() else CHAR_OVERLAY_X_RATIO
            metadata = self._composite_character(
                background_path, poses_dir, pose_asset, output_path, job_id,
                fallback_pose=fallback_pose,
                x_ratio=placement_x,
            )
        else:
            shutil.copy2(background_path, output_path)
            metadata = {
                "version": "3.0",
                "surface_image_path": background_path,
                "foreground_mask_path": None,
                "character_regions": [],
                "z_order": ["clean_background", "verified_info_surface", "editorial_text"],
            }
        metadata = dict(metadata or {})
        metadata.update({
            "surface_image_path": str(background_path),
            "output_path": str(output_path),
            "pose_asset": pose_asset,
            "fallback_pose": fallback_pose,
            "character_placement": direction.get("character_placement", "right third"),
        })
        metadata["manifest_path"] = write_layer_manifest(output_path, metadata)
        scene["layered_scene"] = metadata
        scene["character_regions"] = metadata.get("character_regions", [])
        plan = scene.get("info_surface_plan")
        if isinstance(plan, dict):
            plan["surface_image_path"] = str(background_path)
            plan["final_image_path"] = str(output_path)
        scene["final_image_path"] = str(output_path)
        return str(output_path)

    def _resume_layered_scene(self, scene: dict, output_path: str, layered: dict) -> str:
        """Recompose a layered still from its clean/surface plate on resume."""
        surface_path = str(layered.get("surface_image_path") or "")
        foreground_asset = str(layered.get("foreground_asset_path") or "")
        if not Path(surface_path).is_file():
            logger.warning("Layer manifest has no usable surface plate for %s", output_path)
            return output_path
        # 검증 사실은 생성 직후 V5 슬롯 renderer가 적용한다. 재개 시에는
        # 기존 합성 결과를 다시 warp하지 않아 캐릭터 영역을 보존한다.
        if foreground_asset and Path(foreground_asset).is_file():
            from app.services.scene_layers import compose_character_foreground, write_layer_manifest
            placement = str(layered.get("character_placement") or "right third")
            pose_name = Path(foreground_asset).stem
            metadata = compose_character_foreground(
                surface_path, str(Path(foreground_asset).parent), pose_name, output_path,
                fallback_pose=layered.get("fallback_pose"), placement=placement,
            )
            metadata.update({
                "surface_image_path": surface_path,
                "output_path": str(output_path),
                "pose_asset": layered.get("pose_asset") or pose_name,
                "fallback_pose": layered.get("fallback_pose"),
                "character_placement": placement,
            })
            metadata["manifest_path"] = write_layer_manifest(output_path, metadata)
            scene["layered_scene"] = metadata
            scene["character_regions"] = metadata.get("character_regions", [])
        else:
            import shutil
            shutil.copy2(surface_path, output_path)
        plan = scene.get("info_surface_plan")
        if isinstance(plan, dict):
            plan["surface_image_path"] = surface_path
            plan["final_image_path"] = str(output_path)
        scene["final_image_path"] = str(output_path)
        return str(output_path)

    def _check_panel_blank_qc(self, img_path: str):
        """
        패널 영역(우측 960, 120, 1800, 960)의 픽셀 표준편차를 분석합니다.
        AI 모델이 빈 패널에 글자나 선을 마음대로 그렸을 경우,
        안전하게 Pillow 단색 cream 패널로 강제 덮어쓰기 폴백을 처리합니다.
        """
        try:
            from PIL import Image, ImageStat
            img = Image.open(img_path)
            # Crop the panel area
            panel_crop = img.crop((960, 120, 1800, 960)).convert("L")
            stat = ImageStat.Stat(panel_crop)
            stddev = stat.stddev[0]
            logger.info(f"Panel QC stddev for {img_path}: {stddev:.2f}")
            
            # 픽셀 편차가 크다는 것은(임계값 18.0 초과) 디테일(선, 글자 등)이 채워져 있음을 의미
            if stddev > 18.0:
                logger.warning(f"Panel QC failed (stddev {stddev:.2f} > 18.0). Enforcing cream background overlay.")
                from PIL import ImageDraw
                rgba_img = img.convert("RGBA")
                draw = ImageDraw.Draw(rgba_img)
                from app.services.data_chart_renderer import draw_cream_panel
                draw_cream_panel(draw)
                rgba_img.convert("RGB").save(img_path, "JPEG")
                logger.info(f"Cream panel successfully overwritten for {img_path}")
        except Exception as e:
            logger.warning(f"Failed to run Panel QC: {e}")
