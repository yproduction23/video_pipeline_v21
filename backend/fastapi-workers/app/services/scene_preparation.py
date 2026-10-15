"""이미지 생성 전, 모든 시각 모드(캐릭터형·슬라이드형)가 공유하는 장면 준비 단계.

2026-10-15 사용자 결정: 승인 대본과 TTS 자막 간 계보 검증, 문장 경계
정규화, 자막 청크 바인딩, 화면 텍스트 계약 부착, 기사 증거 카드 자동
부착은 시각 모드와 무관하게 항상 똑같이 적용돼야 한다(AGENTS.md: 승인
대본 내레이션 원문이 TTS·자막의 단일 기준). 이 함수는
`images_worker.py`의 `_generate()`에서 캐릭터 전용 로직(아키타입 배정 등)이
시작되기 직전까지의 코드를 그대로 옮긴 것이다 — 새 시각 모드
(`slides_worker.py`)도 같은 함수를 호출해 같은 검증을 거친다.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app import runtime_config
from app.utils.narration_contract import (
    bind_caption_chunks_to_scenes,
    normalize_scene_sentence_boundaries,
    build_script_contract,
    verify_tts_against_script_contract,
)
from app.utils.scene_screen_text_planner import attach_scene_screen_texts

logger = logging.getLogger(__name__)


@dataclass
class PreparedScenes:
    scenes_meta: list[dict]
    script_data: dict
    market_snapshot: dict = field(default_factory=dict)
    evidence_audit: dict = field(default_factory=dict)
    tts_subtitle_sync: dict = field(
        default_factory=lambda: {"passed": None, "reason": "tts_metadata_not_supplied"}
    )
    budget_limit_krw: int | None = None
    still_image_pilot: bool = False


def prepare_job_scenes(
    *,
    job_id: int,
    tts_meta_json: str | None,
    script_meta_json: str | None,
    scenes_meta: list | None,
    budget_limit_krw: int | None,
) -> PreparedScenes:
    market_snapshot: dict[str, Any] = {}
    evidence_audit: dict[str, Any] = {}
    tts_subtitle_sync: dict[str, Any] = {"passed": None, "reason": "tts_metadata_not_supplied"}
    script_data: dict = {}
    # scenes_meta가 주어지지 않은 경우 script_meta_json에서 복원
    if script_meta_json:
        try:
            script_data = json.loads(script_meta_json)
            if isinstance(script_data, str):
                script_data = json.loads(script_data)
            market_snapshot = script_data.get("market_snapshot") or {}
            if not scenes_meta:
                scenes_meta = script_data.get("sections") or script_data.get("scenes") or []
            if not scenes_meta and script_data.get("script"):
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
            tts_subtitle_sync = {
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
            evidence_audit = planned.audit
        except NarrationHashMismatch:
            raise
        except Exception as exc:
            # Public news is an optional evidence enhancement.  Search,
            # publisher, or capture failures preserve the approved scene.
            logger.warning("automatic article evidence planning skipped: %s", exc)
            evidence_audit = {
                "job_id": job_id,
                "status": "unavailable",
                "reason": str(exc),
                "selected": [],
            }

    return PreparedScenes(
        scenes_meta=scenes_meta,
        script_data=script_data,
        market_snapshot=market_snapshot,
        evidence_audit=evidence_audit,
        tts_subtitle_sync=tts_subtitle_sync,
        budget_limit_krw=budget_limit_krw,
        still_image_pilot=still_image_pilot,
    )
