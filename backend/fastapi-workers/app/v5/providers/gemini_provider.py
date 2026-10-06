"""V5 Gemini 3 Pro Image 어댑터.

기존 워커의 검증된 Gemini HTTP 경로를 재사용한다. Gemini 응답에는 청구 실비가
포함되지 않으므로, 생성 직후 이를 실비로 가장해 비용 원장에 적재하지 않는다.
"""
from __future__ import annotations

import hashlib
import logging
import os
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional

from app.utils.character_integrity_contract import COIN_SILHOUETTE_CONTRACT

from .bfl_flux_provider import ImageResult

logger = logging.getLogger(__name__)


class GeminiModel(str, Enum):
    PRO = "gemini-3-pro-image"
    FLASH = "gemini-3.1-flash-image"


class GeminiProviderError(RuntimeError):
    """Gemini 생성 또는 비용 계약 준비 실패."""


class ReferenceAssetMissingError(GeminiProviderError):
    """V5 화풍 계약에 필요한 승인 참조 자산이 없을 때 발생한다."""


# Job 52에서 화풍·색·정보 밀도·캐릭터 활용이 안정적이었던 서로 다른 장면을
# 채널의 '허용 범위'로 쓴다. 어느 한 장도 얼굴·의상·구도의 절대 모델시트가 아니다.
_SCENE_STYLE_REF_NAMES = [
    "channel_style_job52_data_lab.png",
    "channel_style_job52_briefing.png",
    "channel_style_job52_risk_map.png",
    "channel_style_job52_semiconductor.png",
    "channel_style_job52_market_flow.png",
    "channel_style_semiconductor_growth_scene_v1.png",
    "channel_style_semiconductor_production_scene_v1.png",
]

_FACE_RANGE_REF_NAME = "channel_character_face_range_v2.png"
_FACE_ROLE_REF_NAMES = {
    "goggles": "channel_character_face_scene05_v1.png",
}

GEMINI_REFERENCE_CONTRACT_VERSION = "job52-range-v2-operational-v3-eye-layers"
_REFERENCE_CONTRACT_MARKER = (
    f"FINAL GEMINI REFERENCE CONTRACT [{GEMINI_REFERENCE_CONTRACT_VERSION}]:"
)

_CONTEXTUAL_REFERENCE_GROUPS = {
    "data_lab": (
        "channel_style_job52_data_lab.png",
        "channel_style_job52_briefing.png",
    ),
    "semiconductor": (
        "channel_style_semiconductor_production_scene_v1.png",
        "channel_style_semiconductor_growth_scene_v1.png",
    ),
    "risk_weather": (
        "channel_style_job52_risk_map.png",
        "channel_style_job52_market_flow.png",
    ),
    "market_flow": (
        "channel_style_job52_market_flow.png",
        "channel_style_job52_risk_map.png",
    ),
    "briefing": (
        "channel_style_job52_briefing.png",
        "channel_style_job52_market_flow.png",
    ),
}


def _load_default_references() -> list[str]:
    """서로 다른 승인 후보 장면으로 구성된 채널 화풍 범위를 반환한다.

    단독 캐릭터 시트와 추상 붓자국 시트는 실제 Job 52 장면의 얼굴·의상·색감과
    달라 결과를 한 가지 잘못된 캐릭터로 끌어당겼다. 기본 경로에서는 이들을
    보내지 않고, 지정된 얼굴 여섯 장과 실제 장면들이 공유하는 그림 언어만 참조한다.
    """
    root = Path(__file__).resolve().parents[3] / "out" / "references"
    paths = [
        root / _FACE_RANGE_REF_NAME,
        *(root / name for name in _FACE_ROLE_REF_NAMES.values()),
        *(root / name for name in _SCENE_STYLE_REF_NAMES),
    ]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise ReferenceAssetMissingError(
            "V5 승인 참조 자산 없음:\n" + "\n".join(f"  {p}" for p in missing)
        )
    return [str(p) for p in paths]


_PROP_REFERENCE_PREFIX = "prop_reference_"
# 2026-10-06 사용자 요청: 대본 속 구체 사물(예: "전역모") 실사 참조는 캐릭터
# 정체성·채널 화풍과 전혀 다른 계약이다. 기존 3장 상한(캐릭터 1 + 화풍 2)을
# 줄이지 않고 별도 슬롯으로 덧붙이며, 과도한 평균화를 막기 위해 장면당 최대
# 1장만 허용한다.
_PROP_REFERENCE_MAX = 1


def select_contextual_reference_paths(
    prompt: str,
    reference_image_paths: list[str] | None = None,
    *,
    max_references: int = 3,
) -> list[str]:
    """장면 의미와 가까운 Job52 참조만 골라 평균화 수렴을 막는다.

    명시적으로 선택된 캐릭터 참조는 보존한다. Job52 장면 참조는 전역 화풍
    라이브러리이지만 한 API 요청에는 가장 가까운 두 장만 보내며, 한 장면에
    서로 다른 의상·얼굴·구도 다섯 개를 동시에 평균내지 않는다.

    "prop_reference_" 접두사가 붙은 경로(장면 소품 실사 참조)는 캐릭터/화풍
    선택 로직에서 완전히 제외한 뒤, 최종 결과 뒤에 그대로 덧붙인다. 즉 이
    함수의 3장 상한(max_references)을 소비하지 않는다.
    """
    candidates = list(reference_image_paths or _load_default_references())
    if not candidates:
        return []
    prop_reference_paths = [
        path for path in candidates if Path(path).name.startswith(_PROP_REFERENCE_PREFIX)
    ][:_PROP_REFERENCE_MAX]
    candidates = [path for path in candidates if path not in prop_reference_paths]
    if not candidates:
        return prop_reference_paths
    by_name = {Path(path).name: path for path in candidates}
    system_identity_names = {_FACE_RANGE_REF_NAME, *_FACE_ROLE_REF_NAMES.values()}
    explicit = [
        path for path in candidates
        if not Path(path).name.startswith("channel_style_job52_")
        and not Path(path).name.startswith("channel_style_")
        and Path(path).name not in system_identity_names
    ]
    # 공통 얼굴 계약에는 "goggles or glasses must not erase..." 같은 일반
    # 안전 문구가 들어 있다. 이를 장면 지시로 해석하면 모든 장면이 고글 얼굴
    # 앵커를 받으므로, 참조 선택은 공통 하한선 앞의 장면 로컬 구간만 읽는다.
    source = str(prompt or "").split(
        "COMMON CROSS-SCENE ACCEPTANCE FLOOR:", 1
    )[0].casefold()
    if any(token in source for token in ("semiconductor", "wafer", "microchip", "chip sample", "production line")):
        group = "semiconductor"
    elif any(token in source for token in ("data laboratory", "data lab", "data-lab", "data_lab", "earnings laboratory", "lab coat")):
        group = "data_lab"
    elif any(token in source for token in ("market-flow", "market flow", "inflow", "outflow", "foreign investor", "매수", "매도")):
        group = "market_flow"
    elif any(token in source for token in (
        "weather-map", "weather map", "storm", "forecast", "outlook", "downgrade",
        "risk map", "risk control", "warning signal", "warning lamp",
    )):
        group = "risk_weather"
    else:
        group = "briefing"
    selected = [by_name[name] for name in _CONTEXTUAL_REFERENCE_GROUPS[group] if name in by_name]
    if explicit:
        identity = explicit[:1]
    else:
        identity = [by_name[_FACE_RANGE_REF_NAME]] if _FACE_RANGE_REF_NAME in by_name else []
        if any(token in source for token in ("goggles", "safety glasses", "scientist glasses")):
            anchor_name = _FACE_ROLE_REF_NAMES["goggles"]
            if anchor_name in by_name:
                identity.append(by_name[anchor_name])
    # 명시 캐릭터 1장 + 장면 화풍 2장이 기본 상한이다. 중복은 입력 순서를
    # 보존하며 제거한다.
    limit = max(1, min(int(max_references), 3))
    core = list(dict.fromkeys([*identity, *selected]))[:limit]
    return [*core, *prop_reference_paths]


def ensure_gemini_reference_contract(
    prompt: str,
    reference_image_paths: list[str] | None,
) -> str:
    """Canary와 운영 HTTP 경로에 동일한 참조 이미지 의미 계약을 붙인다.

    참조 파일만 전송하고 그 역할을 설명하지 않으면 모델이 얼굴 범위·화풍·
    이전 실패 프레임을 임의로 평균낸다. 모든 실제 GenerateContent POST 직전에
    이 함수를 거치며, marker가 이미 있으면 중복 주입하지 않는다.
    """
    source_prompt = str(prompt or "")
    if _REFERENCE_CONTRACT_MARKER in source_prompt:
        return source_prompt
    reference_names = [Path(path).name.lower() for path in reference_image_paths or []]
    if not reference_names:
        return source_prompt

    localized_edit = "the first attached image is the previously rejected full scene" in source_prompt.casefold()
    face_range_indices = [
        index + 1 for index, name in enumerate(reference_names)
        if "channel_character_face_range" in name
    ]
    face_anchor_indices = [
        index + 1 for index, name in enumerate(reference_names)
        if "channel_character_face_scene" in name
    ]
    style_indices = [
        index + 1 for index, name in enumerate(reference_names)
        if "style_reference" in name or "style_scene_ref" in name or "channel_style_" in name
    ]
    prop_indices = [
        index + 1 for index, name in enumerate(reference_names)
        if name.startswith(_PROP_REFERENCE_PREFIX)
    ]
    system_indices = set(face_range_indices + face_anchor_indices + style_indices + prop_indices)
    character_indices = [
        index + 1 for index, name in enumerate(reference_names)
        if "character_reference" in name
    ]
    if not character_indices and len(reference_names) > 1:
        character_indices = [
            index + 1 for index in range(len(reference_names))
            if index + 1 not in system_indices and not (localized_edit and index == 0)
        ]

    clauses = [_REFERENCE_CONTRACT_MARKER]
    if localized_edit:
        clauses.append(
            "Reference image 1 is only the previously rejected full-scene edit source. Preserve its successful composition and correct only the named failed contract; do not treat it as the channel character identity."
        )
    if character_indices:
        character_index = character_indices[0]
        clauses.append(
            f"Reference image {character_index} is an explicitly selected character reference. "
            f"Keep the mascot recognizably related to image {character_index}, while following the scene-specific expression, costume, action, and scale. "
            "Do not copy a neutral face, outfit, pose, or framing into every scene."
        )
    elif face_range_indices:
        face_index = face_range_indices[0]
        clauses.append(
            f"Reference image {face_index} contains exact, non-generatively altered face crops from approved Job52 scenes 03, 04, 05, 09, 13, and 14. "
            "Use their shared face construction as the identity contract. For every materially open eye, render four visually separate nested regions: the white sclera is a surrounding eye region; "
            "place a warm brown iris inside the sclera, a darker pupil inside that iris, and one or more white catchlights inside the iris or pupil. "
            "A catchlight dot inside an otherwise solid black oval does not count as sclera. Keep the warm brown iris diameter within 38% to 58% of the full visible eye width. "
            "Closed eyes may remain expressive lines, but goggles or glasses must not erase these layers from an open eye. A soft forehead reflection highlight is required. "
            "Use gently curved eyebrows that can rise or soften with the emotion; do not use sharply angled or deeply furrowed eyebrows. Keep subtle cheek blush "
            "when compatible with the scene lighting. The round gold-coin species, embossed rim, compact anatomy, and face construction takes priority over background and prop detail. "
            + COIN_SILHOUETTE_CONTRACT
            + "Costume and headwear remain scene-specific, as the six approved examples intentionally use different outfits. Do not force one outfit, hat, pose, expression, scale, or framing."
        )
        if face_anchor_indices:
            anchor_index = face_anchor_indices[0]
            clauses.append(
                f"Reference image {anchor_index} is a larger role-matched face crop taken without generative alteration from one of those same six approved scenes. "
                "Use it only to resolve the shared eye, iris, catchlight, forehead-highlight, and line construction at readable scale. "
                "Do not copy or freeze its expression, costume, goggles, pose, scale, or framing when the written scene requests something else."
            )
    elif len(reference_names) == 1 and not style_indices:
        clauses.append(
            "Reference image 1 is an explicitly selected character reference. Preserve the recognizable gold-coin mascot design language, "
            "but let the written scene choose expression, costume, action, headwear, and framing."
        )
    else:
        clauses.append(
            "No single authoritative mascot model sheet is supplied. When the written prompt requests the mascot, infer the shared gold-coin "
            "character design range from the channel scene references without copying one scene's face, outfit, pose, or scale."
        )

    if style_indices:
        clauses.append(
            f"Reference images {', '.join(str(index) for index in style_indices)} collectively define the channel's acceptable visual range. "
            "Preserve their 2D editorial-comic line language, scene-dependent palette, cel shading, information density, and varied use of the "
            "gold-coin mascot. They intentionally show different expressions, costumes, character sizes, compositions, text surfaces, and color moods. "
            "Do not collapse that range into one fixed expression, navy outfit, studio, board, or camera angle."
        )
        clauses.append(
            "Keep the earlier face-construction identity contract."
            if face_range_indices or character_indices
            else "Keep any explicitly requested mascot identity while varying the scene expression and staging."
        )
        clauses.append(
            "Do not copy any reference's literal text, numbers, speech bubbles, props, or composition into a different narration. When the current scene allows text or values, borrow the "
            "references' structural treatment—solid scene-mounted monitors, printed wall boards, machine gauges, engraved or painted prop faces—rather "
            "than inventing a detached translucent glass card. A floating holographic surface is allowed only when the scene-local surface plan explicitly requests one."
        )
    if prop_indices:
        prop_index_list = ", ".join(str(index) for index in prop_indices)
        clauses.append(
            f"Reference images {prop_index_list} are real-world photographs of a specific object or symbol named in this scene's narration. "
            "Use them only to correct that object's real shape, proportions, color, and distinguishing details — "
            "never its style, lighting, character, composition, background, people, watermark, or any text/number visible in the photo. "
            "Do not let it influence the mascot's identity, the channel's visual style, or the scene's framing."
        )

    clauses.append(
        "Follow the scene-specific composition in the written prompt. A continuous scene, split comparison, stage, classroom, control room, laboratory, or other framing is allowed only when that scene calls for it. "
        "Do not force every scene into one studio template. Do not reproduce marks from any reference image."
    )
    return " ".join(clauses) + "\n\n" + source_prompt


class GeminiProvider:
    """기존 실사용 Gemini 전송 경로를 V5 ImageResult 계약으로 감싼다."""

    def __init__(self, api_key: Optional[str] = None, *, use_batch: bool = False) -> None:
        self._key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._use_batch = use_batch

    def estimate_cost_usd(self, model: GeminiModel, width: int, height: int, *, is_edit: bool = False) -> float:
        """승인자가 주입한 모델별 예상 비용만 사용한다.

        Gemini가 응답으로 실비를 주지 않으므로 기본값을 코드에 숨기지 않는다.
        P1-b 실행 전 현재 계약 단가를 환경변수로 명시해야 한다.
        """
        name = (
            "V5_GEMINI_PRO_IMAGE_2K_ESTIMATE_USD"
            if model == GeminiModel.PRO
            else "V5_GEMINI_FLASH_IMAGE_2K_ESTIMATE_USD"
        )
        raw = os.environ.get(name, "").strip()
        if not raw:
            raise GeminiProviderError(f"{name}가 필요합니다. 승인된 현재 계약 단가를 설정하세요.")
        try:
            value = float(raw)
        except ValueError as exc:
            raise GeminiProviderError(f"{name}는 양의 숫자여야 합니다.") from exc
        if value <= 0:
            raise GeminiProviderError(f"{name}는 양의 숫자여야 합니다.")
        return value

    def generate(
        self, prompt: str, *, model: GeminiModel = GeminiModel.PRO, width: int = 2048,
        height: int = 1152, seed: Optional[int] = None, reference_image_paths: Optional[list[str]] = None,
        request_audit=None, service_tier: str = "standard", thinking_level: str | None = None,
    ) -> ImageResult:
        if not self._key:
            raise GeminiProviderError("GEMINI_API_KEY 미설정")
        if self._use_batch:
            raise GeminiProviderError("V5 Gemini batch 생성은 P1-b 비교 범위 밖입니다.")
        reference_image_paths = select_contextual_reference_paths(prompt, reference_image_paths)
        service_tier = str(service_tier or "standard").strip().lower()
        if model == GeminiModel.PRO and service_tier not in {"standard", "priority", "flex"}:
            raise GeminiProviderError("Pro service tier는 standard/priority/flex만 허용합니다.")
        if model == GeminiModel.FLASH and service_tier != "standard":
            raise GeminiProviderError("Flash Image는 현재 Standard tier만 허용합니다.")
        if model == GeminiModel.FLASH:
            thinking_level = str(thinking_level or "minimal").strip().lower()
            if thinking_level not in {"minimal", "high"}:
                raise GeminiProviderError("Flash thinking level은 minimal/high만 허용합니다.")
        elif thinking_level is not None:
            raise GeminiProviderError("Pro Image에는 Flash thinking level을 전달하지 않습니다.")

        # 기존 이미지 프로바이더는 image_provider=gemini + Pro 조합에서 FAL/Pollinations
        # 하향 폴백을 거부한다. 따라서 비교 결과의 공급자가 섞이지 않는다.
        from app.providers.real.image import NanaBananaProvider

        prompt = ensure_gemini_reference_contract(prompt, reference_image_paths)
        # ──────────────────────────────────────────────────────────────────
        # [전송 전 강제 검증 로그] 매 Gemini API 호출마다 실제 전송 파일을 기록한다.
        # 이 로그 없이는 "코드는 맞다"는 보고가 검증 불가 — 반드시 출력돼야 한다.
        # ──────────────────────────────────────────────────────────────────
        logger.info("[GEMINI_REF_AUDIT] === Gemini API 호출 직전 참조 자산 검증 ===")
        logger.info("[GEMINI_REF_AUDIT] 전송 참조 이미지 수: %d", len(reference_image_paths))
        for idx, ref_path in enumerate(reference_image_paths, start=1):
            p = Path(ref_path)
            if p.is_file():
                sha = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                size_kb = p.stat().st_size // 1024
                logger.info(
                    "[GEMINI_REF_AUDIT]   [%d] %s  sha256=%s...  %dKB",
                    idx, p.name, sha, size_kb,
                )
            else:
                logger.error(
                    "[GEMINI_REF_AUDIT]   [%d] MISSING: %s", idx, ref_path
                )
        logger.info("[GEMINI_REF_AUDIT] 프롬프트 첫 200자: %s", prompt[:200])
        logger.info("[GEMINI_REF_AUDIT] ================================================")

        with TemporaryDirectory(prefix="v5_gemini_") as temporary_dir:
            output_path = Path(temporary_dir) / "generated.png"
            try:
                NanaBananaProvider().generate(
                    prompt=prompt,
                    output_path=str(output_path),
                    image_provider="gemini",
                    gemini_model=model.value,
                    gemini_image_size="2K",
                    gemini_service_tier=service_tier,
                    gemini_thinking_level=thinking_level,
                    # P1 비교는 승인된 장면 수와 실제 HTTP 요청 수가 같아야 한다.
                    # 재시도는 상위 승인 절차가 별도로 새 요청을 예약할 때만 허용한다.
                    gemini_max_attempts=1,
                    gemini_request_audit=request_audit,
                    gemini_reference_contract_declared=True,
                    character_image_paths=reference_image_paths or [],
                    # V5 SceneSpec과 참조 시트가 캐릭터·스타일 계약의 단일 출처다.
                    # 공용 V4의 청록 카드 마스코트와 빈 패널 금지문을 덧붙이지 않는다.
                    character_style_prompt="none",
                    suppress_legacy_style_lock=True,
                    style_locked=True,
                )
            except Exception as exc:
                from app.utils.image_request_control import ImageRequestHeld
                if isinstance(exc, ImageRequestHeld):
                    raise
                detail = str(exc).strip().replace("\n", " ")[:500]
                raise GeminiProviderError(
                    f"{model.value} 이미지 생성 실패: {type(exc).__name__}: {detail or '상세 메시지 없음'}"
                ) from exc
            if not output_path.exists() or not output_path.read_bytes():
                raise GeminiProviderError(f"{model.value}가 이미지 파일을 반환하지 않았습니다.")
            image_bytes = output_path.read_bytes()

        request_lineage = {}
        if request_audit is not None:
            for entry in reversed(request_audit.summary()["entries"]):
                if entry.get("image_sha256") == hashlib.sha256(image_bytes).hexdigest():
                    request_lineage = {key: entry.get(key) for key in ("attempt_id", "request_id", "image_sha256", "run_id")}
                    break
        return ImageResult(
            image_bytes=image_bytes,
            model=model.value,
            width=width,
            height=height,
            seed=seed,
            actual_cost_usd=None,
            request_id=request_lineage.get("request_id") or "gemini-request-id-not-returned",
            meta={
                "cost_status": "unverified_until_console_reconciliation",
                "reference_image_count": len(reference_image_paths or []),
                "request_lineage": request_lineage,
            },
        )
