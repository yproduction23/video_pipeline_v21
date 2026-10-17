"""
Phase 3-5 v7 — 롱폼 조립 (병렬 처리 + 인코딩 패스 축소)

v6 대비 변경점 (조립 시간 단축 목적):
  1. 씬별 클립 생성(Fal/Kling AI 움짤 / 정지 이미지 렌더)을 ThreadPoolExecutor로
     병렬 처리. 기존에는 씬을 하나씩 순차로 처리해서 총 시간이
     "씬 개수 × 씬당 처리시간"으로 선형 증가했음. Kling API 호출은
     네트워크 I/O 대기(최대 180초 폴링)와 정지 이미지 인코딩을 병렬화해
     동시 실행 시 실제 이득이 큼.
  2. concat 단계를 재인코딩(-c:v libx264) 대신 스트림 복사(-c copy)로
     우선 시도. 씬별 클립이 이미 1단계에서 동일 코덱/해상도/프레임레이트로
     표준화되어 있으므로 재인코딩 없이 이어붙이기가 가능함. 실패 시에만
     기존 방식(재인코딩)으로 자동 폴백.
     → 기존 구조는 "씬별 인코딩 → concat 재인코딩 → 자막 합성 재인코딩"
       으로 사실상 영상을 최대 3번 인코딩했는데, 이번 변경으로 2번으로 줄어듦.
  3. 각 단계 소요시간을 로그로 남겨, 이후 병목 프로파일링이 쉽도록 함.

주의 (동시성 관련):
  - register_process/unregister_process(app.utils.process_manager)가
    스레드 세이프한지는 이 파일만으로 보장할 수 없음. 만약 여러 스레드가
    동시에 등록/해제할 때 경쟁 조건이 관측되면, 해당 모듈에 락(Lock)을
    추가하는 걸 권장합니다.
  - is_job_stopped() 체크는 각 씬 처리 시작 시점에 한 번 확인합니다.
    이미 실행 중인 FFmpeg/Kling 호출을 즉시 중단시키진 못하지만
    (기존 v6도 동일한 한계였음), 아직 시작 안 한 씬은 스킵됩니다.
  - 동시 실행 스레드 수는 runtime_config의 "longform_scene_max_workers"
    값을 따르며(없으면 기본 4), CPU/네트워크 과부하를 막기 위해
    최대 8로 상한을 둡니다.
"""
import json
import os
import re
import time
import hashlib
import math
import logging
import subprocess
import concurrent.futures
import shutil
from pathlib import Path
from app.utils.process_manager import register_process, unregister_process, is_job_stopped, stop_job_processes
from app import runtime_config
from app.utils.quality_gate import assess_images, assess_subtitles, persist_quality_report
from app.utils.art_direction import assess_art_diversity
from app.utils.stock_overlay import Anchor, IndexData, Market, overlay_filter, render_index_card
from app.utils.market_charts import render_market_chart
from app.utils.data_surface_locator import locate_data_surface
from app.services.overlay.surface_detector import detect_surface
from app.services.overlay.watermark import render_channel_watermark_layer
from app.services.sfx import mix_sfx_events
from app.utils.budget import ProviderRequestAudit, load_preflight, record_cost
from app.utils.intro_motion import scene_duration_seconds, select_intro_motion_scene_indices
from app.utils.narration_contract import (
    bind_caption_chunks_to_scenes,
    build_script_contract,
    narration_from_sections,
    verify_tts_against_script_contract,
)
from app.utils.operational_contract_audit import (
    PIPELINE_OPERATIONAL_CONTRACT_VERSION,
    build_operational_contract_audit,
)
from app.utils.output_qc import build_output_qc_report
from app.services.kling_prompt_builder import build_kling_motion_prompt
from app.services.fal_motion_safety import (
    FAL_MOTION_SAFETY_POLICY_VERSION,
    assess_fal_motion_safety,
    fal_motion_metadata_block_reasons,
    fal_motion_safety_is_current,
)
from app.services.motion_locality import assess_video_motion_locality, compact_motion_failure
from app.services.annotate import render_annotations
from app.services.verbatim_guard import validate as validate_verbatim
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Final-frame editorial policy.  Text and exact numerical values are rendered
# only as post-production graphics, never as image/video-generation prompts.
RENDER_VERIFIED_INDEX_CARDS = False
OVERLAY_POLICY_VERSION = 4

def _run_subprocess(cmd: str, job_id: int) -> int:
    """FFmpeg 등의 명령어를 subprocess.Popen으로 실행하고 중지 트래킹 등록"""
    if is_job_stopped(job_id):
        raise RuntimeError(f"Job {job_id} is stopped. Aborting execution.")
    logger.info(f"Running tracked subprocess: {cmd}")
    p = subprocess.Popen(cmd, shell=True)
    register_process(job_id, p)
    try:
        ret = p.wait()
        return ret
    finally:
        unregister_process(job_id, p)

NANUM_BOLD = r"C:\Windows\Fonts\malgunbd.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
SLIDE_SUBTITLE_FONT_SIZE = 56  # 슬라이드형 흰 박스 자막 글자 크기(px, 1080p)
NANUM_REGULAR = r"C:\Windows\Fonts\malgun.ttf" if os.name == "nt" else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"


def _probe_duration(media_path: str) -> float:
    """조립 단계가 추정값이 아닌 실제 미디어 길이를 사용하도록 한다."""
    if not media_path or not os.path.exists(media_path):
        return 0.0
    raw = os.popen(
        f'ffprobe -v error -show_entries format=duration '
        f'-of default=noprint_wrappers=1:nokey=1 "{media_path}"'
    ).read().strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


class LongformWorker:

    def _resolve_clip_count(self, budget_remaining_krw: int, requested_max: int) -> int:
        usd_krw = float(runtime_config.value("usd_krw") or 1400.0)
        kling_cost_per_clip = float(runtime_config.value("kling_cost_per_clip_usd")) * usd_krw
        candidates = sorted({max(0, requested_max), min(8, requested_max), min(5, requested_max), 1}, reverse=True)
        for count in candidates:
            if kling_cost_per_clip * count <= budget_remaining_krw * 0.25:
                # Kling이 남은 예산의 25% 이하 차지하도록 제약
                return count
        return 0

    def assemble(self, tts_meta_json: str, scenes_meta_json: str,
                 gifs_meta_json: str, job_id: int = 0) -> dict:

        from app.utils.image_request_control import assert_request_review_cleared
        assert_request_review_cleared(job_id)

        stage_t0 = time.time()

        tts_meta = json.loads(tts_meta_json)
        audio_path = tts_meta.get("audio_path", "")
        total_duration = tts_meta.get("total_duration", 0)
        chunks = tts_meta.get("chunks", [])

        raw_scenes = json.loads(scenes_meta_json)
        scenes = [json.loads(s) if isinstance(s, str) else s for s in raw_scenes]

        # 조립 단계는 승인 원문과 100% 일치하는 TTS/자막만 받는다.
        canonical_text = str(tts_meta.get("canonical_text") or "").strip()
        canonical_sha256 = str(tts_meta.get("canonical_sha256") or "").strip()
        if not canonical_text or not canonical_sha256:
            raise RuntimeError("TTS 원문 계약이 없습니다. TTS를 다시 생성한 뒤 조립하세요.")
        if hashlib.sha256(canonical_text.encode("utf-8")).hexdigest() != canonical_sha256:
            raise RuntimeError("TTS 원문 해시가 일치하지 않습니다. 변조되거나 오래된 TTS 자산입니다.")
        subtitle_compact = re.sub(
            r"\s+", "", "".join(str(chunk.get("text") or "") for chunk in chunks)
        )
        if subtitle_compact != re.sub(r"\s+", "", canonical_text):
            raise RuntimeError("TTS 음성과 자막 청크가 승인된 스크립트 원문과 일치하지 않습니다.")
        subtitle_quality = (
            (tts_meta.get("quality_report") or {}).get("subtitles")
            if isinstance(tts_meta.get("quality_report"), dict) else None
        )
        if not isinstance(subtitle_quality, dict) or not subtitle_quality.get("passed", False):
            logger.warning(
                "자막 품질 검수 경고 발생 (score=%s, warnings=%s) — 영상 조립을 계속 진행합니다.",
                subtitle_quality.get("score") if isinstance(subtitle_quality, dict) else "None",
                subtitle_quality.get("warnings") if isinstance(subtitle_quality, dict) else [],
            )

        # 조립을 직접 호출해 이미지 단계를 우회하더라도, 실제 사용할 장면의
        # 원문과 TTS·자막 원문이 모두 같은지 다시 확인한다.
        try:
            scene_contract = build_script_contract(narration_from_sections(scenes), scenes)
            tts_subtitle_sync = verify_tts_against_script_contract(tts_meta, scene_contract)
            caption_scene_sync = bind_caption_chunks_to_scenes(scenes, chunks)
            for scene, mapping in zip(scenes, caption_scene_sync["mappings"]):
                stored = scene.get("caption_chunk_contract")
                if isinstance(stored, dict):
                    compared_fields = (
                        "scene_text_sha256",
                        "caption_compact_sha256",
                        "chunk_start_index",
                        "chunk_end_index",
                    )
                    if any(stored.get(field) != mapping.get(field) for field in compared_fields):
                        raise RuntimeError(
                            f"이미지 장면 {mapping['scene_index'] + 1}의 자막 청크 계약이 현재 TTS와 다릅니다."
                        )
                scene["caption_chunk_contract"] = mapping
            tts_subtitle_sync.update({
                "caption_scene_contract_version": caption_scene_sync["version"],
                "scene_count": caption_scene_sync["scene_count"],
                "all_scene_boundaries_on_caption_boundaries": True,
            })
        except Exception as exc:
            raise RuntimeError(f"조립 전 대본·TTS·자막·장면 계보 검증 실패: {exc}") from exc

        raw_gifs = json.loads(gifs_meta_json)
        gifs = [json.loads(g) if isinstance(g, str) else g for g in raw_gifs]

        logger.info(f"롱폼 조립 시작: job_id={job_id}, scenes={len(scenes)}, "
                    f"chunks={len(chunks)}, total={total_duration:.0f}s")

        job_dir = Path(f"/app/data/jobs/{job_id}/longform")
        job_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = job_dir / "temp"
        temp_dir.mkdir(exist_ok=True)

        output_path = str(job_dir / "final.mp4")
        result_cache_path = job_dir / "result.json"

        # A Temporal retry can arrive after FFmpeg finished but before Spring
        # received the HTTP response (for example during a container restart).
        # Reuse that verified final artifact instead of starting Fal/Kling or
        # re-encoding the same job a second time.  Include source file state so
        # an explicit scene/TTS regeneration correctly invalidates the cache.
        def source_state(path: str) -> dict:
            try:
                stat = os.stat(path)
                return {"path": path, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
            except OSError:
                return {"path": path, "missing": True}

        assembly_fingerprint = hashlib.sha256(json.dumps({
            "assembly_contract": "integrated-data-motion-v6",
            "operational_contract": PIPELINE_OPERATIONAL_CONTRACT_VERSION,
            "tts": tts_meta_json,
            "scenes": scenes_meta_json,
            "gifs": gifs_meta_json,
            "audio": source_state(audio_path),
            "images": [source_state(_article_image_path(scene)) for scene in scenes],
            "motion_policy": {
                "enabled": bool(runtime_config.value("intro_motion_enabled")),
                "clip_count": int(runtime_config.value("intro_motion_clip_count")),
                "clip_seconds": int(runtime_config.value("intro_motion_clip_seconds")),
                "manual_values": [scene.get("use_kling") for scene in scenes],
                "text_number_safety_policy_version": FAL_MOTION_SAFETY_POLICY_VERSION,
            },
            "overlay_policy": {
                "version": OVERLAY_POLICY_VERSION,
                "verified_index_cards": RENDER_VERIFIED_INDEX_CARDS,
                "speech_bubbles": bool(runtime_config.value("render_speech_bubbles")),
                "article_evidence": bool(runtime_config.value("render_article_evidence")),
                "market_charts": True,
            },
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        try:
            cached = json.loads(result_cache_path.read_text(encoding="utf-8"))
            if cached.get("input_fingerprint") == assembly_fingerprint and _verify_video(output_path):
                logger.info("Reusing verified completed longform result for job %s", job_id)
                return cached["result"]
        except (OSError, ValueError, KeyError, TypeError):
            pass

        # Do not assemble a partial video. Validate the full scene set before
        # starting FFmpeg; this is critical for 200+ scene jobs.
        if not scenes:
            raise RuntimeError("조립할 씬이 없습니다.")
        expected_indices = set(range(len(scenes)))
        actual_indices = [int(scene.get("index", i)) for i, scene in enumerate(scenes)]
        if set(actual_indices) != expected_indices or len(actual_indices) != len(set(actual_indices)):
            raise RuntimeError(
                f"씬 인덱스가 연속적이지 않습니다: expected=0..{len(scenes) - 1}, actual={actual_indices[:12]}"
            )
        missing_images = [
            int(scene.get("index", i))
            for i, scene in enumerate(scenes)
            if not _verify_image(_article_image_path(scene))
        ]
        if missing_images:
            raise RuntimeError(f"이미지 생성이 완료되지 않은 씬이 있어 조립을 중단합니다: {missing_images[:20]}")

        # [S4] 1. 씬별 재생 시간(duration) 정밀 타임라인 동적 매핑 (TTS 청크 기반)
        _assign_scene_durations_from_chunks(scenes, chunks, total_duration)
        _assign_editorial_overlay_timing(scenes)

        # Editorial overlay policy: generic AI/text overlays remain disabled.
        # Only scenes carrying an explicitly verified index_data payload may
        # receive a deterministic card in _process_scene.
        data_card_count = sum(
            1 for scene in scenes
            if RENDER_VERIFIED_INDEX_CARDS
            and isinstance(scene.get("index_data"), dict)
            and scene["index_data"].get("verified") is True
        )
        market_chart_count = sum(
            1 for scene in scenes
            if isinstance(scene.get("market_chart"), dict)
            and scene["market_chart"].get("verified") is True
        )
        logger.info(
            f"editorial overlays: verified index cards={data_card_count}, market charts={market_chart_count} (scenes={len(scenes)})"
        )

        # Kling 비디오 프로바이더 로드 (하이브리드 모드)
        video_provider = None
        try:
            from app.providers.factory import get_video_provider
            video_provider = get_video_provider()
            logger.info("하이브리드 Kling 비디오 프로바이더 로드 성공")
        except Exception as e:
            logger.warning(f"Kling 비디오 프로바이더 로드 실패 (FFmpeg 폴백 사용): {e}")

        # Contiguous opening-only Fal motion. Later scenes are deliberately
        # static to preserve chart, caption, and numerical alignment. Credit
        # availability is judged by the cost ledger and the real provider
        # response, never by an undocumented Fal billing endpoint.
        if not bool(runtime_config.value("intro_motion_enabled")):
            video_provider = None
            logger.info("Fal motion disabled by configuration")
        max_clips_cap = (
            max(0, int(runtime_config.value("intro_motion_clip_count")))
            if bool(runtime_config.value("intro_motion_enabled")) else 0
        )
        if bool(runtime_config.value("intro_motion_test_mode")):
            max_clips_cap = min(max_clips_cap, 2)
            logger.info("intro_motion_test_mode is active. Kling clip count capped to 2.")

        budget_preflight = load_preflight(job_id)
        if budget_preflight:
            if budget_preflight.get("gemini_image_only_budget"):
                preflight_kling_cap = int(budget_preflight.get("fal_motion_clips_reserved_separately", max_clips_cap))
            else:
                preflight_kling_cap = int(budget_preflight.get("kling_clip_count", max_clips_cap))
            max_clips_cap = min(max_clips_cap, max(0, preflight_kling_cap))
            logger.info("Budget preflight applies Kling cap=%s, estimate=₩%s", max_clips_cap, budget_preflight.get("estimated_cost_krw"))

        max_clips_cap = _cap_intro_motion_for_short_video(total_duration, max_clips_cap)

        intro_kling_indices, intro_motion_target, intro_motion_actual = _select_intro_kling_scenes(
            scenes, total_duration, max_clips_cap
        )
        candidate_intro_kling_indices = set(intro_kling_indices)
        previously_rejected_motion = _rejected_global_motion_indices(job_id)
        rejected_selected: set[int] = set()
        if previously_rejected_motion:
            rejected_selected = intro_kling_indices & previously_rejected_motion
            intro_kling_indices -= rejected_selected
            candidate_intro_kling_indices |= rejected_selected
            for index in sorted(rejected_selected):
                motion_reason = "previous_global_motion_rejection"
                logger.info("씬 %s 이전 전역 흔들림 감사 결과로 Fal 재요청 차단", index)
                # 아래 안전 게이트 감사 구조에 동일한 원인으로 합류시킨다.
                motion_contract = scenes[index].get("motion_contract")
                if not isinstance(motion_contract, dict):
                    motion_contract = {}
                    scenes[index]["motion_contract"] = motion_contract
                motion_contract["previous_rejection"] = motion_reason
            # 이미지 단계가 미리 표시한 다음 안전 후보로 빈 자리를 채운다.
            elapsed = 0.0
            for index, scene in enumerate(scenes):
                starts_in_opening = elapsed < intro_motion_target
                elapsed += scene_duration_seconds(scene, float(runtime_config.value("scene_duration_sec")))
                if len(intro_kling_indices) >= max_clips_cap:
                    break
                if not starts_in_opening or index in intro_kling_indices or index in previously_rejected_motion:
                    continue
                if scene.get("use_kling") is not True:
                    continue
                safety = _ensure_fal_motion_safety(scene, _article_image_path(scene))
                if safety["eligible"]:
                    intro_kling_indices.add(index)
                    candidate_intro_kling_indices.add(index)
        # 프롬프트의 금지문만으로는 이미지 속 숫자·글자를 보존할 수 없다.
        # 최종 PNG와 메타데이터를 모두 통과한 씬만 Fal 후보로 남긴다.
        protected_fact_indices = {
            index for index, scene in enumerate(scenes)
            if _has_v5_verified_fact_overlay(scene)
        }
        motion_block_reasons: dict[int, list[str]] = {}
        protected_motion_indices: set[int] = set(rejected_selected)
        for index in rejected_selected:
            motion_block_reasons[index] = ["previous_global_motion_rejection"]
        for index in sorted(intro_kling_indices):
            safety = _ensure_fal_motion_safety(
                scenes[index],
                _article_image_path(scenes[index]),
            )
            if not safety["eligible"]:
                protected_motion_indices.add(index)
                motion_block_reasons[index] = list(safety["reasons"])
        excluded_motion_indices = intro_kling_indices & protected_motion_indices
        if excluded_motion_indices:
            intro_kling_indices -= excluded_motion_indices
            intro_motion_actual = sum(
                min(
                    float(runtime_config.value("intro_motion_clip_seconds")),
                    5.0,
                    scene_duration_seconds(scenes[index]),
                )
                for index in intro_kling_indices
            )
            logger.info(
                "Fal 문자·수치 안전 게이트: 정지 이미지 전환 indices=%s reasons=%s",
                sorted(excluded_motion_indices),
                motion_block_reasons,
            )
        logger.info(
            "Opening Fal motion plan: target=%.1fs, actual=%.1fs, scenes=%s/%s, indices=%s",
            intro_motion_target, intro_motion_actual, len(intro_kling_indices), len(scenes),
            sorted(intro_kling_indices),
        )
        # If the editor has selected one or more scenes, use exactly those;
        # otherwise retain the safe automatic intro selection. Both paths are
        # capped to the configured opening-motion budget.
        # Java DTOs serialize optional Boolean fields as explicit null.  A
        # null is not an editor choice and must not disable the automatic
        # opening-motion plan for every scene.
        has_manual_kling_selection = _has_manual_kling_selection(scenes)

        # ── 2. 씬별 클립 생성 (병렬 처리) ──────────────────────────
        scene_stage_t0 = time.time()
        max_workers = _get_max_workers(len(scenes))
        logger.info(f"씬 클립 생성 병렬 처리 시작: max_workers={max_workers}")

        clip_paths_map: dict[int, str] = {}
        clip_modes: dict[int, str] = {}
        clip_failures: dict[int, str] = {}
        stopped_error = None

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    self._process_scene, i, scene, video_provider,
                    _should_request_kling_for_scene(
                        scene,
                        selected_for_intro_motion=i in intro_kling_indices,
                        has_manual_kling_selection=has_manual_kling_selection,
                    ),
                    temp_dir,
                    job_id,
                ): i
                for i, scene in enumerate(scenes)
            }
            for future in concurrent.futures.as_completed(futures):
                i = futures[future]
                try:
                    idx, clip_path, clip_mode, failure_reason = future.result()
                    clip_paths_map[idx] = clip_path
                    clip_modes[idx] = clip_mode
                    if failure_reason:
                        clip_failures[idx] = failure_reason
                except Exception as e:
                    stopped_error = e
                    if "stopped by user" in str(e):
                        logger.info(f"Job {job_id} 중지 감지, 나머지 씬 처리 건너뜀")
                    else:
                        # Do not deliver a final video containing silently
                        # substituted scenes. Stop active FFmpeg work and let the
                        # caller mark this job FAILED without automatic retry.
                        logger.error(f"씬 {i} 처리 오류. Job {job_id} 전체 중지: {e}")
                        stop_job_processes(job_id)
                    for pending in futures:
                        pending.cancel()
                    break

        if stopped_error is not None:
            raise stopped_error

        if set(clip_paths_map) != expected_indices:
            missing_clips = sorted(expected_indices - set(clip_paths_map))
            raise RuntimeError(f"씬 클립이 누락되어 조립을 중단합니다: {missing_clips[:20]}")
        clip_paths = [clip_paths_map[i] for i in sorted(clip_paths_map)]
        requested_motion_indices = {
            i for i in intro_kling_indices
            if _should_request_kling_for_scene(
                scenes[i],
                selected_for_intro_motion=True,
                has_manual_kling_selection=has_manual_kling_selection,
            )
        }
        actual_kling_count = sum(
            1 for i in requested_motion_indices if clip_modes.get(i) == "kling"
        )
        minimum_kling_count = _minimum_motion_delivery(len(requested_motion_indices))
        if actual_kling_count < minimum_kling_count and video_provider:
            # 생성 후 국소 동작 검사를 실패한 경우, 같은 오프닝 구간의 다음
            # 안전 후보 한 장만 보충한다. 실패 장면을 그대로 쓰거나 전체 화면
            # 흔들림을 조용히 허용하지 않는다.
            elapsed = 0.0
            backfill_candidates: list[int] = []
            for index, scene in enumerate(scenes):
                starts_in_opening = elapsed < intro_motion_target
                elapsed += scene_duration_seconds(scene, float(runtime_config.value("scene_duration_sec")))
                if (
                    not starts_in_opening
                    or index in requested_motion_indices
                    or index in previously_rejected_motion
                ):
                    continue
                if scene.get("use_kling") is not True:
                    continue
                safety = _ensure_fal_motion_safety(scene, _article_image_path(scene))
                if safety["eligible"]:
                    backfill_candidates.append(index)
            for index in backfill_candidates[:1]:
                logger.info("Fal 국소 동작 실패 보충 생성: scene=%s", index)
                idx, clip_path, clip_mode, failure_reason = self._process_scene(
                    index, scenes[index], video_provider, True, temp_dir, job_id,
                )
                clip_paths_map[idx] = clip_path
                clip_modes[idx] = clip_mode
                requested_motion_indices.add(idx)
                candidate_intro_kling_indices.add(idx)
                if failure_reason:
                    clip_failures[idx] = failure_reason
                if clip_mode == "kling":
                    actual_kling_count += 1
                    break
            if actual_kling_count >= minimum_kling_count:
                intro_kling_indices = {
                    index for index in requested_motion_indices if clip_modes.get(index) == "kling"
                }
                intro_motion_actual = sum(
                    min(
                        float(runtime_config.value("intro_motion_clip_seconds")),
                        5.0,
                        scene_duration_seconds(scenes[index]),
                    )
                    for index in intro_kling_indices
                )
        if actual_kling_count < minimum_kling_count:
            raise RuntimeError(
                "Fal/Kling opening-motion delivery gate failed: "
                f"requested={len(requested_motion_indices)}, actual={actual_kling_count}, "
                f"required={minimum_kling_count}, failures={json.dumps(clip_failures, ensure_ascii=False)}. "
                "Refusing to deliver a silently static video."
            )
        kling_motion_plan = _build_kling_motion_plan(
            scenes=scenes,
            total_duration=total_duration,
            target_seconds=intro_motion_target,
            selected_seconds=intro_motion_actual,
            selected_indices=intro_kling_indices,
            candidate_indices=candidate_intro_kling_indices,
            requested_indices=requested_motion_indices,
            protected_fact_indices=protected_fact_indices,
            clip_modes=clip_modes,
            failures=clip_failures,
            has_manual_selection=has_manual_kling_selection,
            protected_motion_indices=protected_motion_indices,
            motion_block_reasons=motion_block_reasons,
        )
        logger.info(
            f"씬 클립 생성 완료: {len(clip_paths)}/{len(scenes)}개 성공, "
            f"소요={time.time() - scene_stage_t0:.1f}s"
        )

        if not clip_paths:
            raise RuntimeError("씬 클립이 하나도 생성되지 않았습니다.")

        # ── 3. concat ─────────────────────────────────────────────────
        # 안전 우선: Fal 클립과 정지 이미지 클립은 타임베이스/비트레이트가
        # 미세하게 달라 -c copy가 exit 0을 반환해도 깨진 파일을 만들 수 있음.
        # → 항상 재인코딩(-c:v libx264)으로 처리해 파일 무결성을 보장.
        # (병렬 처리로 클립 생성 시간 자체가 줄었기 때문에, concat 재인코딩
        #  비용은 전체 대비 상대적으로 작아짐. 재생 불가 파일이 더 큰 손실.)
        concat_stage_t0 = time.time()
        clip_list_path = str(temp_dir / "clips.txt")
        with open(clip_list_path, "w") as f:
            for cp in clip_paths:
                # FFmpeg's concat demuxer parses Windows drive-colons as a
                # protocol unless the path uses POSIX separators and escapes
                # the drive separator.
                clip_ref = Path(cp).as_posix()
                if os.name == "nt" and len(clip_ref) > 1 and clip_ref[1] == ":":
                    clip_ref = clip_ref[0] + r"\:" + clip_ref[2:]
                f.write(f"file '{clip_ref}'\n")

        silent_video = str(temp_dir / "silent.mp4")
        clip_manifest_path = temp_dir / "clip_manifest.json"
        clip_manifest = [
            {"path": cp, "size": os.path.getsize(cp), "mtime_ns": os.stat(cp).st_mtime_ns}
            for cp in clip_paths
        ]
        can_reuse_silent = False
        try:
            previous_manifest = json.loads(clip_manifest_path.read_text(encoding="utf-8"))
            can_reuse_silent = previous_manifest == clip_manifest and _verify_video(silent_video)
        except (OSError, ValueError, TypeError):
            can_reuse_silent = False

        if can_reuse_silent:
            ret = 0
            logger.info("재생 가능한 silent.mp4와 씬 manifest를 재사용합니다.")
        else:
            ret = _run_subprocess(
                f'ffmpeg -f concat -safe 0 -i "{clip_list_path}" '
                f'-c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p '
                f'-y "{silent_video}" -loglevel error',
                job_id
            )

        # ffprobe로 실제 재생 가능 여부 검증
        concat_ok = _verify_video(silent_video)
        if not concat_ok:
            logger.error(
                f"concat 결과물 ffprobe 검증 실패 (ret={ret}). "
                f"파일이 손상됐거나 모든 씬 클립이 비정상일 가능성 있음."
            )
            raise RuntimeError("concat 단계에서 재생 가능한 영상을 만들지 못했습니다.")
        if not can_reuse_silent:
            clip_manifest_path.write_text(json.dumps(clip_manifest, ensure_ascii=False), encoding="utf-8")

        # Kling은 5초 클립만 반환한다. 어떤 씬이 짧게 끝나도 최종 -shortest가
        # 나레이션을 자르지 않도록 마지막 프레임을 복제해 오디오 길이를 보장한다.
        audio_duration = _probe_duration(audio_path)
        target_duration = audio_duration or float(total_duration or 0.0)
        silent_duration = _probe_duration(silent_video)
        if target_duration > 0 and silent_duration + 0.05 < target_duration:
            pad_seconds = target_duration - silent_duration
            padded_video = str(temp_dir / "silent_padded.mp4")
            logger.warning(
                f"시각 트랙이 나레이션보다 짧음: video={silent_duration:.2f}s, "
                f"audio={target_duration:.2f}s. 마지막 프레임 {pad_seconds:.2f}s 패딩"
            )
            pad_ret = _run_subprocess(
                f'ffmpeg -i "{silent_video}" '
                f'-vf "tpad=stop_mode=clone:stop_duration={pad_seconds:.3f}" '
                f'-t {target_duration:.3f} -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p '
                f'-an -y "{padded_video}" -loglevel error',
                job_id,
            )
            if pad_ret != 0 or not _verify_video(padded_video):
                raise RuntimeError("나레이션 길이 보정 영상 생성 실패")
            os.replace(padded_video, silent_video)

        logger.info(
            f"concat 완료 (재인코딩 모드, ffprobe 검증 통과), "
            f"소요={time.time() - concat_stage_t0:.1f}s"
        )

        # 4. ASS 자막 생성 (경제사냥꾼 스타일)
        ass_path = str(temp_dir / "subtitles.ass")
        self._generate_ass(chunks, ass_path, scenes)

        # 5. 음성 + BGM + 자막 합성
        merge_stage_t0 = time.time()
        assembly_stage_path = str(temp_dir / "final.partial.mp4")
        if os.path.exists(assembly_stage_path):
            os.remove(assembly_stage_path)
        font_available = os.path.exists(NANUM_BOLD) or os.path.exists(NANUM_REGULAR)
        ass_exists = os.path.exists(ass_path) and os.path.getsize(ass_path) > 200
        audio_exists = os.path.exists(audio_path) and os.path.getsize(audio_path) > 0
        # 편집점 효과음(예: 고지 카드)을 나레이션 오디오에 미리 합성한다. 길이는 그대로이고 실패하면 원본을 쓴다.
        sfx_events = [event for scene in scenes for event in (scene.get("sfx_events") or [])]
        if audio_exists and sfx_events:
            sfx_audio = str(temp_dir / "narration_with_sfx.wav")
            if mix_sfx_events(audio_path, sfx_events, sfx_audio, workdir=str(temp_dir)):
                audio_path = sfx_audio
                logger.info("편집점 효과음 합성: %s개", len(sfx_events))
            else:
                logger.warning("편집점 효과음 합성 실패 — 효과음 없이 진행")

        # BGM 파일 탐색 (bgm_worker가 생성한 파일)
        bgm_path = f"/app/data/jobs/{job_id}/bgm.mp3"
        bgm_exists = os.path.exists(bgm_path) and os.path.getsize(bgm_path) > 0
        bgm_volume = runtime_config.value("bgm_volume")

        if audio_exists:
            ass_path_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
            vf_filter = f'-vf "ass=\'{ass_path_escaped}\'" ' if (font_available and ass_exists) else ''
            vcodec = '-c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p' if vf_filter else '-c:v copy'

            if bgm_exists:
                # 3-트랙 믹싱: 나레이션(100%) + BGM(runtime_config 값, 기본 12%)
                merge_cmd = (
                    f'ffmpeg -i "{silent_video}" -i "{audio_path}" -i "{bgm_path}" '
                    f'-filter_complex "[1:a]volume=1.0[narr];[2:a]volume={bgm_volume},aloop=loop=-1:size=2e+09[bgm];'
                    f'[narr][bgm]amix=inputs=2:duration=first:dropout_transition=3[mixed];'
                    f'[mixed]loudnorm=I=-16:LRA=11:TP=-1.5:linear=true[master]" '
                    f'{vf_filter}'
                    f'{vcodec} '
                    f'-map 0:v -map "[master]" '
                    f'-c:a aac -b:a 192k -ar 44100 -ac 2 -movflags +faststart -shortest '
                    f'-y "{assembly_stage_path}" -loglevel error'
                )
                logger.info(f"BGM 믹싱 적용: 나레이션 + BGM(volume={bgm_volume})")
            else:
                merge_cmd = (
                    f'ffmpeg -i "{silent_video}" -i "{audio_path}" '
                    f'-filter_complex "[1:a]loudnorm=I=-16:LRA=11:TP=-1.5:linear=true[master]" '
                    f'{vf_filter}'
                    f'{vcodec} '
                    f'-map 0:v -map "[master]" '
                    f'-c:a aac -b:a 192k -ar 44100 -ac 2 -movflags +faststart -shortest '
                    f'-y "{assembly_stage_path}" -loglevel error'
                )
        else:
            if font_available and ass_exists:
                merge_cmd = (
                    f'ffmpeg -i "{silent_video}" '
                    f'-vf "ass=\'{ass_path}\'" '
                    f'-c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p '
                    f'-y "{assembly_stage_path}" -loglevel error'
                )
            else:
                merge_cmd = None

        if merge_cmd is None:
            shutil.copy2(silent_video, assembly_stage_path)
            ret = 0
        else:
            ret = _run_subprocess(merge_cmd, job_id)
        if ret != 0 or not _verify_video(assembly_stage_path):
            if os.path.exists(assembly_stage_path):
                os.remove(assembly_stage_path)
            logger.error("자막/BGM 합성 실패, 폴백")
            if audio_exists:
                fallback_ret = _run_subprocess(
                    f'ffmpeg -i "{silent_video}" -i "{audio_path}" '
                    f'-map 0:v -map 1:a '
                    f'-c:v copy -c:a aac -ar 44100 -ac 2 -shortest '
                    f'-y "{assembly_stage_path}" -loglevel error',
                    job_id
                )
                if fallback_ret != 0 or not _verify_video(assembly_stage_path):
                    raise RuntimeError("자막/BGM 조립 및 안전한 오디오 폴백이 모두 실패했습니다.")
            else:
                shutil.copy2(silent_video, assembly_stage_path)
                if not _verify_video(assembly_stage_path):
                    raise RuntimeError("최종 영상 임시 파일 검증에 실패했습니다.")

        if not _verify_video(assembly_stage_path):
            raise RuntimeError("최종 영상 임시 파일이 재생 가능한 상태가 아닙니다.")
        os.replace(assembly_stage_path, output_path)

        logger.info(f"자막/BGM 합성 완료, 소요={time.time() - merge_stage_t0:.1f}s")

        if not _verify_video(output_path):
            raise RuntimeError("롱폼 영상 생성 실패")

        # 우상단 채널 워터마크는 자막/BGM 합성과 독립된 마지막 패스로 적용한다.
        # 슬라이드형(캐릭터 없는 모드)에는 캐릭터 채널 배지를 달지 않는다(2026-10-16 운영자 결정).
        watermark_applied = False if _is_slide_job(scenes) else _apply_channel_watermark(output_path, temp_dir, job_id)

        probe = os.popen(
            f'ffprobe -v error -show_entries format=duration '
            f'-of default=noprint_wrappers=1:nokey=1 "{output_path}"'
        ).read().strip()
        actual_duration = float(probe) if probe else total_duration

        file_size = os.path.getsize(output_path)
        has_subtitles = font_available and ass_exists
        subtitle_quality = assess_subtitles(
            chunks, float(total_duration or actual_duration),
            int(runtime_config.value("subtitle_max_chars")),
        )
        visual_quality = assess_images(scenes)
        duration_delta = round(abs(actual_duration - float(total_duration or actual_duration)), 3)
        quality_report = {
            "score": min(subtitle_quality["score"], visual_quality["score"]),
            "duration_delta_seconds": duration_delta,
            "duration_ok": duration_delta <= 0.2,
            "subtitles": subtitle_quality,
            "tts_subtitle_sync": tts_subtitle_sync,
            "images": visual_quality,
            "art_direction": assess_art_diversity(scenes),
            "has_subtitles": has_subtitles,
            "watermark_applied": watermark_applied,
            "data_card_count": data_card_count,
            "market_chart_count": market_chart_count,
            "verified_data_cards_expected": data_card_count,
            "verified_data_cards_rendered": data_card_count,
            "verified_market_charts_expected": market_chart_count,
            "verified_market_charts_rendered": market_chart_count,
            "planned_kling_clip_count": len(requested_motion_indices),
            "kling_clip_count": actual_kling_count,
            "motion_delivery_ok": actual_kling_count >= minimum_kling_count,
            "kling_motion_plan": kling_motion_plan,
        }
        output_qc = build_output_qc_report(
            job_id=job_id,
            output_path=output_path,
            audio_path=audio_path,
            tts_meta=tts_meta,
            scenes=scenes,
            expected_data_cards=data_card_count,
            rendered_data_cards=data_card_count,
            expected_market_charts=market_chart_count,
            rendered_market_charts=market_chart_count,
            planned_kling=len(requested_motion_indices),
            actual_kling=actual_kling_count,
            fal_failures=clip_failures,
            kling_motion_plan=kling_motion_plan,
        )
        quality_report["output_qc"] = output_qc
        operational_contract_audit = build_operational_contract_audit("longform", checks={
            "tts_subtitle_sync_passed": bool(tts_subtitle_sync.get("passed")),
            "duration_ok": bool(quality_report.get("duration_ok")),
            "output_qc_passed": bool(output_qc.get("passed")),
            "requested_motion_count": len(requested_motion_indices),
            "delivered_motion_count": actual_kling_count,
            "motion_delivery_ok": bool(quality_report.get("motion_delivery_ok")),
        })
        quality_report["operational_contract_audit"] = operational_contract_audit
        logger.info(
            "Verified overlay delivery cards=%s/%s charts=%s/%s; output_qc=%s",
            data_card_count, data_card_count, market_chart_count, market_chart_count,
            output_qc["passed"],
        )
        persist_quality_report(job_id, "longform", quality_report)
        total_elapsed = time.time() - stage_t0
        logger.info(
            f"롱폼 조립 완료: size={file_size/1024/1024:.1f}MB, "
            f"actual={actual_duration:.0f}s, subtitles={has_subtitles}, "
            f"총 조립 소요시간={total_elapsed:.1f}s "
            f"(씬생성={time.time() - scene_stage_t0:.1f}s 포함 아님, 위 로그 참고)"
        )

        # Kling clips are billed artifacts and must survive a successful
        # assembly.  Temporal retries and QC-only reassemblies can then reuse
        # the exact motion output instead of charging Fal again.  Static clips
        # are cheap and remain disposable.
        _cleanup_scene_clips(clip_paths, clip_modes)
        if os.path.exists(silent_video):
            os.remove(silent_video)

        result = {
            "job_id": job_id,
            "video_path": output_path,
            "duration_seconds": round(actual_duration, 1),
            "scene_count": len(scenes),
            "gif_count": len(gifs),
            "data_card_count": data_card_count,
            "market_chart_count": market_chart_count,
            "planned_kling_clip_count": len(requested_motion_indices),
            "kling_clip_count": actual_kling_count,
            "kling_motion_plan": kling_motion_plan,
            "has_subtitles": has_subtitles,
            "resolution": "1920x1080",
            "quality_report": quality_report,
            "operational_contract_audit": operational_contract_audit,
        }
        # Persist a small, stable provenance manifest for deterministic
        # thumbnail selection.  A thumbnail may only use an image recorded
        # here, never a separately generated poster that viewers will not see
        # in the finished video.
        timeline_seconds = 0.0
        manifest_scenes = []
        for index, scene in enumerate(scenes):
            duration = scene_duration_seconds(scene, float(runtime_config.value("scene_duration_sec")))
            image_path = _article_image_path(scene)
            manifest_scenes.append({
                "scene_id": str(scene.get("scene_id") or scene.get("id") or index),
                "index": index,
                "image_path": image_path,
                "used_in_final_video": True,
                "start_seconds": round(timeline_seconds, 3),
                "end_seconds": round(timeline_seconds + duration, 3),
                "use_kling": bool(scene.get("use_kling")),
                "kling_requested": index in requested_motion_indices,
                "kling_delivery_mode": clip_modes.get(index, "static"),
                "generation_method": scene.get("generation_method"),
                "visual_type": scene.get("visual_type"),
                "character_regions": scene.get("character_regions") or [],
                "clean_plate_path": scene.get("clean_plate_path"),
                "asset_layout_metadata": scene.get("asset_layout_metadata") or {},
                "annotations": scene.get("annotations") or [],
                "market_chart": scene.get("market_chart"),
            })
            timeline_seconds += duration
        manifest_path = job_dir / "assembly_manifest.json"
        manifest_path.write_text(json.dumps({
            "job_id": job_id,
            "video_path": output_path,
            "resolution": "1920x1080",
            "kling_motion_plan": kling_motion_plan,
            "operational_contract_audit": operational_contract_audit,
            "scenes": manifest_scenes,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        result["assembly_manifest_path"] = str(manifest_path)
        cache_tmp = result_cache_path.with_suffix(".tmp")
        cache_tmp.write_text(
            json.dumps({"input_fingerprint": assembly_fingerprint, "result": result}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(cache_tmp, result_cache_path)
        return result

    # ============================
    # 씬 1개를 클립(mp4)으로 변환 — 병렬 실행 단위
    # ============================
    def _process_scene(self, i: int, scene: dict, video_provider,
                        use_kling: bool, temp_dir: Path, job_id: int):
        """
        단일 씬을 처리하여 (씬 인덱스, 생성된 클립 경로) 튜플을 반환합니다.
        ThreadPoolExecutor에서 씬 여러 개를 동시에 실행하기 위해 분리했습니다.
        Kling 인트로 실패는 정적 이미지로 안전하게 대체할 수 있지만, 이미지/FFmpeg
        조립 자체의 오류는 호출자에게 전파합니다. 빈 배경 씬을 몰래 넣지 않고
        작업 전체를 중지하는 정책입니다.
        """
        if is_job_stopped(job_id):
            raise RuntimeError(f"Job {job_id} stopped by user.")

        _prepare_deterministic_scene_asset(scene, temp_dir, i)

        # 2026-10-15 사용자 결정: 슬라이드형 모드의 Pixabay 매칭 장면은 이미
        # "이미지 1장 + 모션"이 아니라 그 자체로 완성된 영상 소재다 — 기존
        # 경로는 모든 씬이 정지 이미지라고 가정해(아래 img_path 필수 체크)
        # 이런 씬을 처리할 방법이 없었다. 캐릭터 경로는 전혀 건드리지 않고,
        # stock_video_path가 있을 때만 이 새 분기를 탄다.
        stock_video_path = scene.get("stock_video_path")
        if stock_video_path:
            raw_dur = scene.get("duration")
            duration = float(raw_dur) if raw_dur is not None else 15.0
            clip_path = str(temp_dir / f"clip_{i:03d}.mp4")
            clip_fingerprint = _scene_clip_fingerprint(scene, stock_video_path, duration, "slide_stock_video")
            if _can_reuse_scene_clip(
                clip_path, duration, motion_requested=False,
                expected_source_type="slide_stock_video", expected_fingerprint=clip_fingerprint,
            ):
                logger.info("Reusing completed slide_stock_video scene clip %s", i)
                return i, clip_path, "slide_stock_video", None
            if not os.path.exists(stock_video_path):
                raise RuntimeError(f"scene {i} stock video missing: {stock_video_path}")
            _ffmpeg_trim_scale_stock_video(stock_video_path, clip_path, duration, job_id)
            if not _verify_video(clip_path):
                raise RuntimeError(f"scene {i} stock video clip failed validation")
            if not _apply_editorial_overlays(scene, clip_path, temp_dir, i, duration, job_id):
                raise RuntimeError(f"scene {i} editorial overlay failed")
            if not _verify_video(clip_path):
                raise RuntimeError(f"scene {i} clip failed validation after overlay")
            _write_scene_clip_meta(clip_path, "slide_stock_video", clip_fingerprint)
            return i, clip_path, "slide_stock_video", None

        article_capture = _article_capture_for_scene(scene)
        img_path = _article_image_path(scene)
        if article_capture:
            if not bool(runtime_config.value("render_article_evidence")):
                raise RuntimeError(f"scene {i} is article evidence but article overlays are disabled")
            if not _article_source_credit(scene):
                raise RuntimeError(f"scene {i} article evidence is missing source_credit/publisher/date")
            # Evidence screenshots must never be sent to Kling.  They retain
            # DOM/PDF pixel geometry and therefore cost no Gemini/Kling call.
            use_kling = False
        if _has_v5_verified_fact_overlay(scene):
            # 호출자 분기와 독립적으로 한 번 더 막아, 향후 호출 경로가 바뀌어도
            # 검증 수치가 포함된 PNG가 FAL 입력으로 전달되지 않게 한다.
            use_kling = False
        if use_kling and not _motion_contract_allows_scene(scene):
            # 호출 경로가 바뀌어도 이미지 계약을 우회할 수 없게 한 번 더 차단한다.
            use_kling = False
        raw_dur = scene.get("duration")
        duration = float(raw_dur) if raw_dur is not None else 15.0
        clip_path = str(temp_dir / f"clip_{i:03d}.mp4")
        if not _verify_image(img_path):
            raise RuntimeError(f"scene {i} image is missing or corrupt: {img_path}")
        if use_kling:
            safety = _ensure_fal_motion_safety(scene, img_path)
            if not safety["eligible"]:
                logger.info(
                    "씬 %s Fal 호출 차단: 최종 PNG 문자·수치 안전 게이트 reasons=%s",
                    i,
                    safety["reasons"],
                )
                use_kling = False
        # An interrupted assembly can leave a valid *static* clip behind.  It
        # must never satisfy a later request for an opening Kling scene.
        motion_requested = bool(video_provider and use_kling)
        expected_source_type = "kling" if motion_requested else ("article_evidence" if article_capture else "static")
        clip_fingerprint = _scene_clip_fingerprint(scene, img_path, duration, expected_source_type)
        if _can_reuse_scene_clip(
            clip_path,
            duration,
            motion_requested=motion_requested,
            expected_source_type=expected_source_type,
            expected_fingerprint=clip_fingerprint,
        ):
            logger.info("Reusing completed %s scene clip %s", expected_source_type, i)
            return i, clip_path, expected_source_type, None
        section = scene.get("section", "default")
        bg_color = {
            "intro": "0x1a1a2e", "background": "0x16213e",
            "data": "0x0f3460", "scenario": "0x1b1464",
            "action": "0x0d3b2e", "conclusion": "0x1a1a2e",
        }.get(section, "0x0d1b2a")

        try:
            # Only planned opening scenes use Fal image-to-video.  Every other
            # scene (including Fal failures) is rendered as a static image.
            if video_provider and use_kling:
                try:
                    logger.info(f"씬 {i} Kling AI 움짤 생성")

                    # image-to-video는 이미지에 이미 있는 내용(캐릭터 생김새, 배경,
                    # 구도)을 다시 설명하면 안 되고 "무엇이 어떻게 움직이는가"만
                    # 묘사해야 결과가 안정적입니다.
                    prompt_dict = build_kling_motion_prompt(scene)
                    prompt = prompt_dict["prompt"]
                    negative_prompt = prompt_dict["negative_prompt"]

                    motion_duration = min(
                        max(int(duration), 1),
                        max(1, min(int(runtime_config.value("intro_motion_clip_seconds")), 5)),
                    )

                    # 로컬 이미지를 Fal CDN에 업로드하고 공개 URL 확보
                    # Fal.ai v2.6 image-to-video의 start_image_url은 인터넷에서 접근 가능한
                    # https:// URL만 허용합니다. MinIO presigned URL(minio:9000)은 Docker
                    # 내부 네트워크 주소라 Fal 서버에서 접근 불가합니다.
                    start_image_url = _upload_image_for_fal(img_path)
                    if not start_image_url:
                        raise RuntimeError(f"씬 {i}: Fal CDN 이미지 업로드 실패. 정지 이미지 폴백.")

                    request_audit = ProviderRequestAudit.for_kling(
                        job_id=job_id, scene_key=f"kling:{i}")
                    attempt_id = request_audit.before_attempt(attempt=1)
                    try:
                        asset = video_provider.generate(
                            prompt=prompt,
                            duration=motion_duration,
                            output_path=clip_path,
                            image_path=img_path,
                            image_url=start_image_url,
                            negative_prompt=negative_prompt,
                            fal_only=True,
                        )
                    except Exception:
                        request_audit.after_attempt(attempt_id, outcome="failed")
                        raise
                    request_audit.after_attempt(
                        attempt_id,
                        outcome="succeeded",
                        request_id=(asset.meta or {}).get("fal_request_id") if asset and asset.meta else None,
                    )
                    if os.path.exists(clip_path) and os.path.getsize(clip_path) > 1000:
                        temp_kling = clip_path + ".temp.mp4"
                        try:
                            os.rename(clip_path, temp_kling)
                            # Kling 동작은 최대 5초만 사용하고, 나머지는 마지막
                            # 프레임을 유지한다. 따라서 긴 씬도 정확한 길이를 가진다.
                            freeze_duration = max(0.0, duration - motion_duration)
                            std_cmd = (
                                f'ffmpeg -i "{temp_kling}" -vf "scale=1920:1080:force_original_aspect_ratio=decrease,'
                                f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,'
                                f'tpad=stop_mode=clone:stop_duration={freeze_duration:.3f}" '
                                f'-t {duration:.3f} -c:v libx264 -preset fast -pix_fmt yuv420p -an -y "{clip_path}" -loglevel error'
                            )
                            _run_subprocess(std_cmd, job_id)
                        except Exception as ex:
                            logger.warning(f"씬 {i} Kling 표준화 중 오류 발생, 원본 사용 시도: {ex}")
                            if os.path.exists(temp_kling) and not os.path.exists(clip_path):
                                os.rename(temp_kling, clip_path)
                        finally:
                            if os.path.exists(temp_kling):
                                try:
                                    os.remove(temp_kling)
                                except Exception:
                                    pass

                        if _verify_video(clip_path) and abs(_probe_duration(clip_path) - duration) <= 0.15:
                            motion_locality = assess_video_motion_locality(clip_path)
                            if not motion_locality.get("passed"):
                                _write_kling_audit(job_id, i, {
                                    "status": "REJECTED_GLOBAL_MOTION",
                                    "motion_locality": motion_locality,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                })
                                raise RuntimeError(
                                    "Fal 결과가 피사체 국소 동작이 아니라 전역 흔들림으로 판정됨: "
                                    + compact_motion_failure(motion_locality)
                                )
                            if not _apply_editorial_overlays(scene, clip_path, temp_dir, i, duration, job_id):
                                raise RuntimeError(f"scene {i} editorial overlay failed")
                            record_cost(job_id, "kling", scene_key=f"kling:{i}")
                            
                            # Write Kling audit params for reproducibility
                            _write_kling_audit(job_id, i, {
                                "fal_request_id": asset.meta.get("fal_request_id") if asset and asset.meta else None,
                                "prompt": prompt,
                                "negative_prompt": negative_prompt,
                                "duration": motion_duration,
                                "start_image_url_prefix": start_image_url[:80] if start_image_url else None,
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                            
                            logger.info(f"씬 {i} Kling AI 움짤 완성 (표준 규격 변환 완료)")
                            _write_scene_clip_meta(clip_path, "kling", clip_fingerprint)
                            return i, clip_path, "kling", None
                        raise RuntimeError("Kling 표준화 클립 검증 실패")
                except Exception as e:
                    logger.warning(f"씬 {i} Fal 모션 생성 실패, 정지 이미지 + 크로스페이드 페이드 필터 폴백: {e}")
                    if os.path.exists(img_path):
                        try:
                            _ffmpeg_static_image_with_fade(img_path, clip_path, duration, bg_color, job_id)
                            if not _apply_editorial_overlays(scene, clip_path, temp_dir, i, duration, job_id):
                                raise RuntimeError(f"scene {i} editorial overlay failed")
                            if _verify_video(clip_path):
                                logger.info(f"씬 {i} Fal 모션 실패 페이드 폴백 완료: job_id={job_id}, scene={i}, reason={e}")
                                _write_scene_clip_meta(clip_path, "fal_fallback_static", clip_fingerprint)
                                _write_kling_audit(job_id, i, {
                                    "status": "FAILED_FALLBACK_STATIC",
                                    "error": str(e),
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                })
                                return i, clip_path, "fal_fallback_static", str(e)
                        except Exception as fallback_err:
                            logger.error(f"씬 {i} 페이드 폴백 실패, 일반 정적 복구 시도: {fallback_err}")

            # 전 화면 zoompan은 피사체가 움직이지 않고 화면만 지글거리는 인상을
            # 만들므로 재사용 장면도 흔들림 없는 정지+페이드로 처리한다.
            is_ken_burns_reuse = bool(scene.get("reuse_previous_image")) or bool(scene.get("ken_burns_effect"))
            if is_ken_burns_reuse and os.path.exists(img_path):
                logger.info(f"[STATIC_REUSE] Scene {i} reused image without whole-frame zoompan: job_id={job_id}")
                _ffmpeg_static_image_with_fade(img_path, clip_path, duration, bg_color, job_id)
            elif os.path.exists(img_path):
                reveal = scene.get("reveal_assets") if isinstance(scene.get("reveal_assets"), dict) else {}
                if bool(runtime_config.value("scene_reveal_enabled")) and reveal.get("plain_path") and reveal.get("emphasized_path"):
                    _ffmpeg_reveal_images(str(reveal["plain_path"]), str(reveal["emphasized_path"]), clip_path, duration, job_id)
                else:
                    _ffmpeg_static_image(img_path, clip_path, duration, bg_color, job_id)
            else:
                raise RuntimeError(f"scene {i} image disappeared during assembly")
            if not _verify_video(clip_path):
                raise RuntimeError(f"scene {i} clip failed validation after rendering")
            if not _apply_editorial_overlays(scene, clip_path, temp_dir, i, duration, job_id):
                raise RuntimeError(f"scene {i} editorial overlay failed")
            if not _verify_video(clip_path):
                raise RuntimeError(f"scene {i} clip failed validation after overlay")
            source_type = "article_evidence" if article_capture else "static"
            _write_scene_clip_meta(clip_path, source_type, clip_fingerprint)
            return i, clip_path, source_type, None

        except Exception as e:
            logger.error(f"scene {i} processing failed; aborting job: {e}")
            raise

    def _generate_ass(self, chunks: list, ass_path: str, scenes: list | None = None):
        """
        ASS 자막 — 경제사냥꾼 스타일
        - NanumGothicBold 72px (이전 52px → 더 큼)
        - 검정 불투명 박스 배경 (BorderStyle=3)
        - 흰색 굵은 텍스트
        - 하단 중앙 배치 (Alignment=2)
        - 최대 20자 1줄
        """
        # Docker 이미지에 설치된 글꼴을 명시해 환경별 폴백을 없앤다.
        # Fontconfig family name is NanumGothic; bold weight is controlled by
        # the ASS Bold field below. "NanumGothicBold" would fall back to
        # DejaVu Sans in the Docker image despite the font file being present.
        font_name = "NanumGothic"
        font_size = runtime_config.value("subtitle_font_size")
        theme = runtime_config.value("subtitle_theme")

        if _is_slide_job(scenes or []):
            # 슬라이드형(2026-10-17 레퍼런스 대조): 흰 불투명 박스 + 어두운 글자. BorderStyle=3에서 OutlineColour가 박스 색이고
            # Outline 값이 박스 여백이다. 글자 크기는 레퍼런스(1080p 기준 약 56px)에 맞춘다.
            style = (
                f"Style: Main,{font_name},{SLIDE_SUBTITLE_FONT_SIZE},&H00181818,&H0000FFFF,"
                "&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,12,0,2,70,70,64,1"
            )
        elif theme == "knowledge":
            # 반투명 다크 바 + 흰 글자 + 금색 강조 포인트.
            style = (
                f"Style: Main,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,"
                "&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,58,1"
            )
        else:
            # 흰 굵은 글자와 검정 외곽선. 불투명 박스 없이 장면을 살린다.
            style = (
                f"Style: Main,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,"
                "&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,70,70,58,1"
            )

        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
{style}
[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
        def to_ass_time(s: float) -> str:
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = s % 60
            return f"{h}:{m:02d}:{sec:05.2f}"

        lines = [header]
        for chunk in chunks:
            text = chunk.get("text", "").strip()
            if not text:
                continue

            start_sec = max(0.0, chunk.get("start", 0.0))
            dur = chunk.get("duration", 3.0)
            end_sec = start_sec + dur  # End time relative to original start

            display = self._trim_to_limit(text)

            start_str = to_ass_time(start_sec)
            end_str = to_ass_time(end_sec)
            lines.append(f"Dialogue: 0,{start_str},{end_str},Main,,0,0,0,,{display}")

        # UTF-8-SIG 저장
        with open(ass_path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(lines))

        logger.info(f"ASS 자막 생성: {len(chunks)}개 항목")

    @staticmethod
    def _trim_to_limit(text: str) -> str:
        return text

    @staticmethod
    def _highlight_stock_numbers(text: str) -> str:
        """주식 수치 노란색 강조 자동화"""
        import re
        text = re.sub(
            r'(\d[\d,.]*\s*(?:포인트|원|%|퍼센트|달러|조|억|만))',
            r'{\\c&H0000FFFF&}\1{\\r}',
            text
        )
        return text


# ──────────────────────────────────────────────────────────
# 모듈 수준 헬퍼 함수
# ──────────────────────────────────────────────────────────


CHARACTER_ACTION_MOTION = {
    "pointer_up": "the character raises the pointer prop slightly toward the existing top-right chart area, then holds steady",
    "arms_crossed": "the character keeps arms crossed with one slow concerned head tilt and minimal shoulder movement",
    "hands_open": "the character opens hands outward in one small explanatory gesture, then returns to the reference pose",
    "neutral": "the character gives one calm blink and one subtle nod",
}

EMOTION_FACIAL = {
    "neutral": "calm neutral expression",
    "highlight": "focused confident expression with a slight brow raise",
    "surprised": "eyes slightly wider and a brief open-mouth reaction",
    "worried": "worried expression with a gently furrowed brow",
    "happy": "gentle smile with a cheerful expression",
}

BACKGROUND_AMBIENT = {
    "data_overlay": "existing background chart lines pulse very softly while every number and label remains perfectly static and legible",
    "emphasis_zoom": "a very subtle background light glow appears briefly around the character without any camera movement",
    "scene_change": "a very subtle ambient light shift occurs in the background",
}


def _build_kling_motion_prompt(scene: dict) -> str:
    """Build a locked-camera, metadata-driven Fal image-to-video prompt.

    The generated image is a reference frame, not a suggestion.  Position,
    proportions, camera framing, charts, and all text/numbers are protected to
    reduce character drift and jitter before the frozen tail is appended.
    """
    action = str(scene.get("character_action") or "neutral")
    emotion = str(scene.get("emotion_tag") or "neutral")
    marker = str(scene.get("edit_marker") or "scene_change")
    body_motion = CHARACTER_ACTION_MOTION.get(action, CHARACTER_ACTION_MOTION["neutral"])
    face = EMOTION_FACIAL.get(emotion, EMOTION_FACIAL["neutral"])
    ambient = BACKGROUND_AMBIENT.get(marker, BACKGROUND_AMBIENT["scene_change"])

    return (
        "Minimal, subtle animation of the fixed gold coin character in the reference image. "
        f"{face}. {body_motion}. {ambient}. "
        "The character's body position, size, proportions, outline, and pose remain identical to the reference image. "
        "Absolutely no camera motion, no zoom, no pan, no dolly, no shake, and no transition. "
        "Static locked-off camera. No morphing of facial features. No changes to clothing, colors, "
        "background composition, charts, text, or numerical values. Duration 5 seconds, seamless-loop friendly, "
        "and end in the neutral reference pose."
    )


def _upload_image_for_fal(img_path: str) -> str | None:
    """
    로컬 이미지를 Fal.ai 자체 CDN 스토리지에 업로드하고 공개 URL을 반환합니다.

    [왜 fal_client.upload_file인가]
    - Fal.ai image-to-video는 start_image_url로 인터넷에서 접근 가능한 URL 필요
    - MinIO presigned URL은 http://minio:9000/... 형태 (Docker 내부 네트워크 호스트명)
      → Fal.ai 서버(외부 인터넷)에서 접근 불가능 → 이미지 로드 실패 → 생성 실패
    - fal_client.upload_file()은 파일을 Fal CDN에 올리고 공개 https:// URL을 반환
      → Fal 서버에서 직접 접근 가능한 유일하게 신뢰할 수 있는 경로

    [FAL_KEY 설정]
    fal_client는 FAL_KEY 환경변수를 자동으로 읽습니다 (Authorization: Key <값> 헤더).
    requirements.txt에 fal-client>=0.5.0 추가됨.
    """
    if not img_path or not os.path.exists(img_path):
        logger.warning(f"이미지 파일 없음, Fal 업로드 스킵: {img_path}")
        return None
    try:
        import fal_client  # fal-client>=0.5.0 (requirements.txt)
        url = fal_client.upload_file(img_path)
        logger.info(f"Fal CDN 업로드 완료: {url[:80]}...")
        return url
    except Exception as e:
        logger.warning(f"Fal CDN 이미지 업로드 실패 ({img_path}): {e}")
        return None



def _select_intro_kling_scenes(
    scenes: list[dict], total_duration: float, max_clips_cap: int
) -> tuple[set[int], float, float]:
    """실제 초반 시간창 안에서 최종 PNG까지 안전한 Fal 장면을 채운다."""

    def eligible(scene: dict) -> bool:
        if fal_motion_metadata_block_reasons(scene) or not _motion_contract_allows_scene(scene):
            return False
        image_path = _article_image_path(scene)
        if not _verify_image(image_path):
            return False
        return bool(_ensure_fal_motion_safety(scene, image_path).get("eligible"))

    return select_intro_motion_scene_indices(
        scenes,
        total_duration,
        short_seconds=float(runtime_config.value("intro_motion_seconds_short")),
        long_seconds=float(runtime_config.value("intro_motion_seconds_long")),
        short_threshold=float(runtime_config.value("intro_motion_short_threshold")),
        max_clips=max(0, int(max_clips_cap)),
        clip_seconds=float(runtime_config.value("intro_motion_clip_seconds")),
        scene_eligible=eligible,
    )


def _cap_intro_motion_for_short_video(total_duration: float, max_clips_cap: int) -> int:
    """Cap Fal/Kling intro motion duration according to requested video runtime:
    - 1분 영상 (<=90s): 최대 3클립 (~15초 제한)
    - 10분 미만 (90s..600s): 최대 9클립 (~45초 제한)
    - 20분 내외 (>600s): 최대 12클립 (~60초 / 1분 제한)
    """
    cap = max(0, int(max_clips_cap))
    duration = float(total_duration or 0)
    if duration <= 90:
        capped = min(cap, 3)
    elif duration <= 600:
        capped = min(cap, 9)
    else:
        capped = min(cap, 12)

    if capped != cap:
        logger.info(
            "Fal motion duration cap applied: duration=%.1fs, clip cap reduced %s -> %s",
            duration, cap, capped,
        )
    return capped


def _get_max_workers(scene_count: int) -> int:
    """
    씬 병렬 처리에 사용할 스레드 수를 결정합니다.
    runtime_config에 "longform_scene_max_workers" 키를 추가해두면
    코드 재배포 없이 /pipeline/config API로 즉시 조정할 수 있습니다.
    (키가 아직 없다면 기본값 4를 사용하며, 이는 서버 CPU 코어 수와
    Kling/Fal.ai API 동시 요청 한도를 함께 고려해 정한 보수적인 값입니다.
    CPU 코어가 넉넉하고 API 동시 처리 한도가 높다면 6~8까지 올려도 됩니다.)
    """
    default = 6
    try:
        configured = runtime_config.value("longform_scene_max_workers")
    except Exception:
        configured = None

    try:
        workers = int(configured) if configured else default
    except (TypeError, ValueError):
        workers = default

    return max(1, min(workers, max(scene_count, 1), 8))


def _verify_video(video_path: str) -> bool:
    """
    ffprobe로 영상 파일이 실제로 재생 가능한지 검증합니다.
    파일이 존재하고, 크기가 있고, duration을 정상적으로 읽을 수 있어야 True.
    -c copy concat이 exit 0을 반환해도 깨진 파일을 만드는 경우를 잡기 위함.
    """
    if not video_path or not os.path.exists(video_path):
        return False
    # Very short valid clips can be smaller than 10KB; ffprobe duration is
    # the authoritative decodability check, with 1KB as the truncation floor.
    if os.path.getsize(video_path) < 1000:
        return False
    probe = os.popen(
        f'ffprobe -v error -show_entries format=duration '
        f'-of default=noprint_wrappers=1:nokey=1 "{video_path}"'
    ).read().strip()
    try:
        duration = float(probe)
        return duration > 0.0
    except (ValueError, TypeError):
        return False


def _can_reuse_scene_clip(
    clip_path: str,
    duration: float,
    *,
    motion_requested: bool,
    expected_source_type: str | None = None,
    expected_fingerprint: str | None = None,
) -> bool:
    """Reuse only a clip whose sidecar proves its origin and exact inputs."""
    if expected_source_type is None or expected_fingerprint is None:
        return False
    meta_path = Path(f"{clip_path}.meta.json")
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if metadata.get("source_type") != expected_source_type:
        return False
    if metadata.get("input_fingerprint") != expected_fingerprint:
        return False
    return _verify_video(clip_path) and abs(_probe_duration(clip_path) - duration) <= 0.15


def _article_capture_for_scene(scene: dict) -> dict | None:
    capture = scene.get("article_capture")
    if str(scene.get("visual_type") or scene.get("visual_kind") or "") not in {"article_evidence", "article_scene"} or not isinstance(capture, dict):
        return None
    return capture


def _article_source_credit(scene: dict) -> str:
    explicit = str(scene.get("source_credit") or "").strip()
    if explicit:
        return explicit
    capture = _article_capture_for_scene(scene) or {}
    publisher = str(capture.get("publisher") or "").strip()
    published = str(capture.get("published_at") or "").strip()
    return f"출처: {publisher} · {published}" if publisher and published else ""


def _article_image_path(scene: dict) -> str:
    capture = _article_capture_for_scene(scene) or {}
    if str(scene.get("visual_kind") or "") == "article_scene" and scene.get("image_path"):
        return str(scene["image_path"])
    if scene.get("final_image_path"):
        return str(scene["final_image_path"])
    return str(capture.get("local_path") or scene.get("image_path") or "")


def _prepare_deterministic_scene_asset(scene: dict, temp_dir: Path, index: int) -> None:
    """Materialise declared evidence/infographic scenes before FFmpeg reads them.

    These routes never call an image model.  A malformed scene stays a hard
    failure instead of silently falling back to a mascot or generic artwork.
    """
    kind = str(scene.get("visual_kind") or "")
    if kind not in {"article_scene", "infographic_timeline", "infographic_bullet_list"}:
        return
    if scene.get("_deterministic_scene_prepared"):
        return
    output = temp_dir / f"deterministic_scene_{index:03d}.png"
    plain = temp_dir / f"deterministic_scene_{index:03d}_plain.png"
    if kind == "article_scene":
        from app.models.article_evidence import ArticleCapture
        from app.services.article.frame_editor import ArticleFrameEditor
        from app.services.scene_frames.article_scene import ArticleSceneRenderer, ArticleSceneSpec
        from app.services.scene_frames.emphasis_policy import plan_from_scene
        capture = ArticleCapture.model_validate(scene.get("article_capture") or {})
        frame = ArticleFrameEditor().from_capture(capture)
        emphasis = plan_from_scene(scene.get("emphasis_plan") or scene.get("article_emphasis"))
        rendered = ArticleSceneRenderer().render(
            frame,
            ArticleSceneSpec(
                evidence_quote=capture.quote,
                emphasis=emphasis,
                key_phrase=str(scene.get("key_phrase") or ""),
                channel_watermark_path=str(scene.get("channel_watermark_path") or "") or None,
            ),
        )
        scene["emphasis_plan"] = emphasis.model_dump(mode="json")
        scene["emphasis_audit"] = rendered.audit or {}
    else:
        from app.services.scene_frames.infographic.schemas import BulletListCardSpec, TimelineCardSpec
        from app.services.scene_frames.infographic.timeline import InfographicRenderer
        renderer = InfographicRenderer()
        verified = list(scene.get("verified_facts") or [])
        if kind == "infographic_timeline":
            rendered = renderer.timeline(TimelineCardSpec.model_validate(scene.get("card") or {}), verified)
        else:
            rendered = renderer.bullet_list(BulletListCardSpec.model_validate(scene.get("card") or {}), verified)
    rendered.plain.save(plain, "PNG")
    rendered.emphasized.save(output, "PNG")
    scene["image_path"] = str(output)
    scene["scene_kind"] = "article_scene" if kind == "article_scene" else "infographic"
    scene["reveal_assets"] = {"plain_path": str(plain), "emphasized_path": str(output)}
    scene["_deterministic_scene_prepared"] = True


def _default_capture_annotations(capture: dict, raw_plan: object = None) -> list[dict]:
    """Deprecated compatibility path routed through the V3 emphasis policy."""
    from app.services.scene_frames.emphasis_policy import BodyEmphasis, plan_from_scene

    plan = plan_from_scene(raw_plan)
    annotations: list[dict] = []
    quote_bboxes = capture.get("quote_bboxes") or []
    key_bboxes = capture.get("key_phrase_bboxes") or []
    effective = plan.body
    if effective == BodyEmphasis.RECT and not key_bboxes:
        effective = BodyEmphasis.HIGHLIGHT
    if effective in {BodyEmphasis.HIGHLIGHT, BodyEmphasis.HIGHLIGHT_UNDERLINE}:
        annotations.extend({
            "type": "highlighter", "bbox": box,
            "color": "#39E65A", "origin": "dom_quote_default_v3",
        } for box in quote_bboxes)
    if effective in {BodyEmphasis.UNDERLINE, BodyEmphasis.HIGHLIGHT_UNDERLINE} and quote_bboxes:
        annotations.append({
            "type": "underline", "bboxes": quote_bboxes,
            "color": "#E60023", "stroke_width": 10,
            "origin": "dom_quote_default_v3",
        })
    elif effective == BodyEmphasis.RECT:
        annotations.extend({
            "type": "rect", "bbox": box,
            "color": "#E60023", "stroke_width": 8,
            "origin": "dom_quote_default_v3",
        } for box in key_bboxes)
    return annotations


def _scene_clip_fingerprint(
    scene: dict, image_path: str, duration: float, source_type: str
) -> str:
    """Hash every input that can change a rendered scene clip."""
    try:
        stat = os.stat(image_path)
        digest = hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
        image_identity = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}
    except OSError:
        image_identity = {"size": None, "mtime_ns": None}
    contract = {
        "schema": 4,
        "source_type": source_type,
        "duration": round(float(duration), 3),
        "image": image_identity,
        "motion_type": scene.get("motion_type"),
        "market_chart": scene.get("market_chart"),
        "info_surface_plan": scene.get("info_surface_plan"),
        "index_data": scene.get("index_data"),
        "article_capture": scene.get("article_capture"),
        "annotations": scene.get("annotations"),
        "callout": scene.get("callout"),
        "speech_bubble": scene.get("speech_bubble"),
        "bubble_text": scene.get("bubble_text"),
        "subtitle_text": scene.get("subtitle_text"),
        "fal_motion_safety_policy_version": FAL_MOTION_SAFETY_POLICY_VERSION,
        "fal_motion_safety": scene.get("fal_motion_safety"),
        "overlay_policy": {
            "version": OVERLAY_POLICY_VERSION,
            "verified_index_cards": RENDER_VERIFIED_INDEX_CARDS,
            "speech_bubbles": bool(runtime_config.value("render_speech_bubbles")),
            "article_evidence": bool(runtime_config.value("render_article_evidence")),
            "market_charts": True,
        },
    }
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cleanup_scene_clips(clip_paths: list[str], clip_modes: dict[int, str]) -> None:
    """Delete reproducible static clips, retaining every billed Kling clip."""
    for index, clip_path in enumerate(clip_paths):
        if clip_modes.get(index) == "kling":
            continue
        if os.path.exists(clip_path):
            os.remove(clip_path)


def _write_scene_clip_meta(clip_path: str, source_type: str, fingerprint: str) -> None:
    meta_path = Path(f"{clip_path}.meta.json")
    temp_path = meta_path.with_suffix(meta_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(
            {
                "source_type": source_type,
                "input_fingerprint": fingerprint,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temp_path, meta_path)


def _has_manual_kling_selection(scenes: list[dict]) -> bool:
    """Treat only an explicit boolean as an editor motion selection."""
    return any(scene.get("use_kling") is not None for scene in scenes)


def _has_v5_verified_fact_overlay(scene: dict) -> bool:
    """검증 사실 오버레이가 있는 V5 씬은 FAL 입력에서 제외한다."""
    overlays = scene.get("v5_verified_overlays")
    return isinstance(overlays, list) and bool(overlays)


def _ensure_fal_motion_safety(scene: dict, image_path: str) -> dict:
    """동일한 최종 PNG 검사는 재사용하고, 누락·구버전 결과만 다시 검사한다."""
    if fal_motion_safety_is_current(scene, image_path):
        return scene["fal_motion_safety"]
    safety = assess_fal_motion_safety(scene, image_path, scan_image=True)
    scene["fal_motion_safety"] = safety
    return safety


def _motion_contract_allows_scene(scene: dict) -> bool:
    """V5 장면은 텍스트 없는 일반형만 Fal 후보가 될 수 있다."""
    contract = scene.get("motion_contract")
    if not isinstance(contract, dict):
        # 기존 작업물은 명시 계약이 없으므로 이전 호환 규칙을 유지한다.
        return True
    return bool(contract.get("eligible"))


def _should_request_kling_for_scene(
    scene: dict,
    *,
    selected_for_intro_motion: bool,
    has_manual_kling_selection: bool,
) -> bool:
    """일반 장면만 FAL 후보로 남기고 검증 사실 화면은 정지로 보호한다."""
    if not selected_for_intro_motion:
        return False
    if _article_capture_for_scene(scene) or _has_v5_verified_fact_overlay(scene):
        return False
    if fal_motion_metadata_block_reasons(scene):
        return False
    safety = scene.get("fal_motion_safety")
    if isinstance(safety, dict) and safety.get("eligible") is False:
        return False
    if not _motion_contract_allows_scene(scene):
        return False
    contract = scene.get("motion_contract")
    if isinstance(contract, dict) and contract.get("requires_explicit_selection"):
        return bool(scene.get("use_kling"))
    return bool(scene.get("use_kling")) if has_manual_kling_selection else True


def _minimum_motion_delivery(requested_count: int) -> int:
    """Require at least 75% of the planned opening motion before delivery."""
    if requested_count <= 0:
        return 0
    return max(1, math.ceil(requested_count * 0.75))


def _verify_image(image_path: str) -> bool:
    """Return true only for a non-trivial, decodable image file."""
    if not image_path or not os.path.exists(image_path):
        return False
    try:
        # Tiny files are usually empty/truncated; valid flat-color PNGs from
        # the mock provider can legitimately be below the image worker's
        # 15KB cache threshold, so assembly uses a lower safety floor.
        if os.path.getsize(image_path) <= 1000:
            return False
        from PIL import Image
        with Image.open(image_path) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def _write_kling_audit(job_id: int, scene_idx: int, params: dict):
    audit_file = Path(f"/app/data/jobs/{job_id}/kling_audit.jsonl")
    try:
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"scene_idx": scene_idx, **params}, ensure_ascii=False)
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as ex:
        logger.warning(f"Failed to write Kling audit log: {ex}")


def _rejected_global_motion_indices(job_id: int) -> set[int]:
    """이미 전역 흔들림으로 거절된 장면을 같은 Job 재개에서 재과금하지 않는다."""
    audit_file = Path(f"/app/data/jobs/{job_id}/kling_audit.jsonl")
    rejected: set[int] = set()
    try:
        for line in audit_file.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("status") == "REJECTED_GLOBAL_MOTION":
                rejected.add(int(row["scene_idx"]))
    except FileNotFoundError:
        return set()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Kling 전역 흔들림 감사 로그를 읽지 못함: %s", exc)
    return rejected


def _ffmpeg_static_image_with_fade(img_path: str, clip_path: str, duration: float, bg_color: str = "0x0d1b2a", job_id: int = 0):
    """
    정적 이미지를 페이드 인/아웃(0.5초) 효과를 추가하여 FFmpeg로 고화질 비디오 클립으로 인코딩합니다.
    """
    total_frames = int(duration * 30)
    fade_out_start = max(0, total_frames - 15)
    cmd = (
        f'ffmpeg -loop 1 -i "{img_path}" '
        f'-vf "scale=1920:1080:force_original_aspect_ratio=decrease,'
        f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:{bg_color},setsar=1,fps=30,'
        f'fade=in:0:15,fade=out:{fade_out_start}:15" '
        f'-t {duration:.3f} -c:v libx264 -threads 2 -preset fast -crf 18 -pix_fmt yuv420p '
        f'-y "{clip_path}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(clip_path):
        raise RuntimeError(f"static image clip with fade render failed: {img_path}")


def _ffmpeg_ken_burns_image(img_path: str, clip_path: str, duration: float, zoom_type: str = "in", bg_color: str = "0x0d1b2a", job_id: int = 0):
    """
    Plan B: 동일 맥락 씬에서 이전 정적 이미지를 재활용할 때 켄번스(Slow Zoom/Pan) 연출 효과를 추가하여
    FFmpeg로 동적 비디오 클립으로 인코딩합니다. (Gemini API 추가 호출 0원)
    """
    total_frames = int(duration * 30)
    # Slow zoom-in from 1.0 to 1.15 over the clip duration
    zoom_expr = f"min(zoom+0.0012,1.15)" if zoom_type == "in" else f"max(1.15-0.0012*on,1.0)"
    cmd = (
        f'ffmpeg -loop 1 -i "{img_path}" '
        f'-vf "scale=1920:1080:force_original_aspect_ratio=decrease,'
        f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:{bg_color},setsar=1,'
        f'zoompan=z=\'{zoom_expr}\':x=\'iw/2-(iw/zoom/2)\':y=\'ih/2-(ih/zoom/2)\':d={total_frames}:s=1920x1080,fps=30" '
        f'-t {duration:.3f} -c:v libx264 -threads 2 -preset fast -crf 18 -pix_fmt yuv420p '
        f'-y "{clip_path}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(clip_path):
        logger.warning(f"Ken Burns zoompan fallback to static image for {img_path}")
        _ffmpeg_static_image_with_fade(img_path, clip_path, duration, bg_color, job_id)



def _ffmpeg_static_image(img_path: str, clip_path: str, duration: float, bg_color: str = "0x0d1b2a", job_id: int = 0):
    """
    정적 이미지를 FFmpeg로 흔들림 없이(static) 고화질 비디오 클립으로 인코딩합니다.
    """
    cmd = (
        f'ffmpeg -loop 1 -i "{img_path}" '
        f'-vf "scale=1920:1080:force_original_aspect_ratio=decrease,'
        f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2:{bg_color},setsar=1,fps=30" '
        f'-t {duration:.3f} -c:v libx264 -threads 2 -preset fast -crf 18 -pix_fmt yuv420p '
        f'-y "{clip_path}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(clip_path):
        raise RuntimeError(f"static image clip render failed: {img_path}")


def _ffmpeg_trim_scale_stock_video(src_path: str, clip_path: str, duration: float, job_id: int = 0) -> None:
    """Pixabay 등 기존 영상 소재를 장면 길이에 맞게 트리밍·스케일한다.

    2026-10-15 사용자 결정: 확대/축소(zoompan) 효과를 넣지 않는다 — "계속
    움직이는 효과가 지루하다"는 피드백으로 슬라이드형 전체에서 효과 없는
    정지/고정 프레임을 기본으로 삼았다. 영상이 duration보다 짧으면
    -stream_loop로 반복해 길이를 채운다.
    """
    cmd = (
        f'ffmpeg -stream_loop -1 -i "{src_path}" '
        f'-vf "scale=1920:1080:force_original_aspect_ratio=increase,'
        f'crop=1920:1080,setsar=1,fps=30" '
        f'-t {duration:.3f} -an -c:v libx264 -threads 2 -preset fast -crf 18 -pix_fmt yuv420p '
        f'-y "{clip_path}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(clip_path):
        raise RuntimeError(f"stock video clip render failed: {src_path}")


def _ffmpeg_reveal_images(plain_path: str, emphasized_path: str, clip_path: str, duration: float, job_id: int) -> None:
    """One-pass wipe reveal for deterministic evidence/card scenes."""
    hold = min(max(.2, duration * .28), max(.2, duration - .65))
    cmd = (
        f'ffmpeg -loop 1 -t {hold:.3f} -i "{plain_path}" -loop 1 -t {max(.7, duration - hold):.3f} -i "{emphasized_path}" '
        f'-filter_complex "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih):white,setsar=1,fps=30[a];'
        f'[1:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih):white,setsar=1,fps=30[b];'
        f'[a][b]xfade=transition=wipeleft:duration=0.6:offset={max(0, hold-.2):.3f}[v]" '
        f'-map "[v]" -t {duration:.3f} -an -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p -y "{clip_path}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(clip_path):
        raise RuntimeError("deterministic scene reveal render failed")


def _requires_verified_index_card(scene: dict) -> bool:
    if not RENDER_VERIFIED_INDEX_CARDS:
        return False
    payload = scene.get("index_data") or scene.get("overlay_data")
    return isinstance(payload, dict) and payload.get("verified") is True


def _apply_verified_index_card(scene: dict, clip_path: str, temp_dir: Path, index: int, duration: float, job_id: int) -> bool:
    """Overlay a card only when a scene carries explicitly verified values."""
    payload = scene.get("index_data") or scene.get("overlay_data")
    if not isinstance(payload, dict) or payload.get("verified") is not True:
        return False
    try:
        source = str(payload.get("source") or payload.get("data_source") or "").strip()
        if not source:
            return False
        data = IndexData(
            name=str(payload["name"]),
            value=float(payload["value"]),
            change=float(payload["change"]),
            change_pct=float(payload["change_pct"]),
            market=Market(str(payload.get("market", "kr")).lower()),
        )
        card_path = str(temp_dir / f"index_card_{index:03d}.png")
        render_index_card(data, card_path, scale=2)
        placement = scene.get("overlay_placement") or {}
        mode = str(placement.get("mode") or "anchor").lower()
        margin = max(0, int(placement.get("margin", 40)))
        xy = None
        anchor = Anchor.TOP_RIGHT
        if mode == "pixel":
            xy = (int(placement.get("x", 0)), int(placement.get("y", 0)))
        else:
            anchor = Anchor(str(placement.get("anchor", "top_right")).lower())
        filt = overlay_filter(1920, 1080, 0, 0, anchor=anchor, margin=margin, xy=xy)
        staged = clip_path + ".index-card.mp4"
        cmd = (
            f'ffmpeg -i "{clip_path}" -loop 1 -i "{card_path}" '
            f'-filter_complex "[0:v][1:v]{filt}:format=auto[v]" '
            f'-map "[v]" -t {duration:.3f} -an -c:v libx264 -preset fast -crf 18 '
            f'-pix_fmt yuv420p -y "{staged}" -loglevel error'
        )
        ret = _run_subprocess(cmd, job_id)
        if ret != 0 or not _verify_video(staged):
            if os.path.exists(staged):
                os.remove(staged)
            return False
        os.replace(staged, clip_path)
        logger.info("scene %s verified index card overlay applied (source=%s, mode=%s)", index, source, mode)
        return True
    except (KeyError, TypeError, ValueError, OSError) as exc:
        logger.warning("scene %s verified index card overlay skipped: %s", index, exc)
        return False


def _requires_verified_market_chart(scene: dict) -> bool:
    plan = scene.get("info_surface_plan")
    # New images already contain the verified graphic before motion.  Keeping
    # the old FFmpeg card path here would create the exact floating-card bug
    # this pipeline replaces.
    if isinstance(plan, dict) and plan.get("final_image_path"):
        return False
    payload = scene.get("market_chart")
    if not (isinstance(payload, dict) and payload.get("verified") is True and bool(payload.get("source"))):
        return False
    # A policy/cost comparison has no time axis.  It is valid with two or
    # more collector-backed values, while a trend still needs its full series.
    if str(payload.get("visual_kind") or "") == "indexed_comparison":
        return (
            isinstance(payload.get("comparison_values"), list)
            and len(payload["comparison_values"]) >= 2
            and bool(str(payload.get("comparison_basis") or "").strip())
        )
    if str(payload.get("visual_kind") or "") in {"supply_flow", "stock_movers"}:
        return True
    return isinstance(payload.get("points"), list) and len(payload["points"]) >= 5


def _chart_focus_annotation(payload: dict, surface: dict[str, int]) -> dict | None:
    """Derive a dotted emphasis ring from verified chart geometry, not an LLM.

    The renderer owns the chart's panel rectangle.  This converts its current
    data series into the same 1920×1080 coordinate space used by annotations,
    so a later chart-surface adjustment cannot leave a ring floating elsewhere.
    """
    focus = payload.get("focus") or {}
    if focus.get("enabled") is not True:
        return None
    kind = str(payload.get("visual_kind") or "trend_dashboard")
    x, y, width, height = (float(surface[key]) for key in ("x", "y", "width", "height"))
    if kind == "trend_dashboard":
        values = [float(item.get("close")) for item in payload.get("points") or [] if item.get("close") is not None]
        if len(values) < 2:
            return None
        minimum, maximum, latest = min(values), max(values), values[-1]
        span = max(maximum - minimum, max(abs(maximum), 1.0) * .01)
        padded_min, padded_max = minimum - span * .05, maximum + span * .05
        normal = (latest - padded_min) / max(padded_max - padded_min, 1e-9)
        # `_render_trend_dashboard` reserves the top area for title and the
        # lower area for bars; the final point lies at 92% of the plot width.
        center_x = x + width * .92
        center_y = y + height * (.32 + (1 - normal) * .53)
        ring_w, ring_h = width * .17, height * .20
    elif kind == "change_arrow":
        center_x, center_y, ring_w, ring_h = x + width * .64, y + height * .50, width * .28, height * .34
    else:
        center_x, center_y, ring_w, ring_h = x + width * .50, y + height * .51, width * .34, height * .42
    return {
        "type": "dashed_ellipse",
        "bbox": {
            "x": max(0, center_x - ring_w / 2) / 1920,
            "y": max(0, center_y - ring_h / 2) / 1080,
            "width": min(ring_w, 1920) / 1920,
            "height": min(ring_h, 1080) / 1080,
        },
        "color": "#E60023",
        "stroke_width": 10,
        "origin": "verified_market_chart_focus",
    }


def _apply_verified_market_chart(scene: dict, clip_path: str, temp_dir: Path, index: int, duration: float, job_id: int) -> bool:
    """Render and overlay a collected price series; never ask the image model for a factual chart."""
    payload = scene.get("market_chart")
    if not _requires_verified_market_chart(scene):
        return False
    chart_path = str(temp_dir / f"market_chart_{index:03d}.png")
    try:
        resolved_surface = _resolve_market_chart_surface(
            (scene.get("art_direction") or {}).get("data_surface"),
            str(scene.get("image_path") or scene.get("path") or ""),
            job_id=job_id,
            scene_key=str(scene.get("scene_id") or index),
        )
        render_payload = dict(payload)
        render_payload["render_surface"] = {
            "width": resolved_surface["width"],
            "height": resolved_surface["height"],
        }
        if not render_market_chart(render_payload, chart_path):
            logger.warning("scene %s market chart renderer returned no valid file", index)
            return False
        if not os.path.exists(chart_path) or os.path.getsize(chart_path) < 4_000:
            return False
        ok = _apply_market_chart_overlay(
            clip_path,
            chart_path,
            duration,
            job_id,
            resolved_surface,
        )
        if ok:
            focus_annotation = _chart_focus_annotation(payload, resolved_surface)
            if focus_annotation:
                existing = list(scene.get("annotations") or [])
                if not any(item.get("origin") == "verified_market_chart_focus" for item in existing if isinstance(item, dict)):
                    scene["annotations"] = [*existing, focus_annotation]
            logger.info(
                "scene %s verified market chart overlay applied (series=%s source=%s)",
                index, payload.get("series_key"), payload.get("source"),
            )
        return ok
    except (TypeError, ValueError, OSError) as exc:
        logger.warning("scene %s verified market chart overlay failed: %s", index, exc)
        return False


def _apply_data_card_overlay(clip_path: str, card_path: str, duration: float, job_id: int) -> bool:
    """Place deterministic Korean data-card text above the subtitle-safe area."""
    if not card_path or not os.path.exists(card_path) or not _verify_video(clip_path):
        return False
    staged = clip_path + ".card.mp4"
    cmd = (
        f'ffmpeg -i "{clip_path}" -loop 1 -i "{card_path}" '
        f'-filter_complex "[0:v][1:v]overlay=70:55:format=auto[v]" '
        f'-map "[v]" -t {duration:.3f} -an '
        f'-c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p '
        f'-y "{staged}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(staged):
        if os.path.exists(staged):
            os.remove(staged)
        return False
    os.replace(staged, clip_path)
    return True


def _resolve_market_chart_surface(
    surface: dict | None,
    source_image_path: str = "",
    *,
    job_id: int | None = None,
    scene_key: str = "",
) -> dict[str, int]:
    """Return the actual 1920×1080 safe rectangle for both renderer and FFmpeg."""
    surface = surface if isinstance(surface, dict) else {}
    resolved = {
        "x": int(surface.get("x", 1120)), "y": int(surface.get("y", 65)),
        "width": int(surface.get("width", 720)), "height": int(surface.get("height", 390)),
    }
    detection = detect_surface(
        source_image_path,
        "board",
        job_id=job_id,
        scene_key=scene_key,
    )


def _build_kling_motion_plan(
    *,
    scenes: list[dict],
    total_duration: float,
    target_seconds: float,
    selected_seconds: float,
    selected_indices: set[int],
    requested_indices: set[int],
    protected_fact_indices: set[int],
    clip_modes: dict[int, str],
    failures: dict[int, str],
    has_manual_selection: bool,
    candidate_indices: set[int] | None = None,
    protected_motion_indices: set[int] | None = None,
    motion_block_reasons: dict[int, list[str]] | None = None,
) -> dict:
    """최종 영상과 함께 저장할 초반 Kling 구간의 재현 가능한 계획이다."""
    candidates = candidate_indices if candidate_indices is not None else selected_indices
    timeline = 0.0
    scene_windows = []
    for index, scene in enumerate(scenes):
        duration = scene_duration_seconds(scene, float(runtime_config.value("scene_duration_sec")))
        scene_windows.append({
            "index": index,
            "scene_id": str(scene.get("scene_id") or scene.get("id") or index),
            "start_seconds": round(timeline, 3),
            "end_seconds": round(timeline + duration, 3),
        })
        timeline += duration
    delivered_indices = sorted(index for index in requested_indices if clip_modes.get(index) == "kling")
    return {
        "policy": "opening_contiguous_window",
        "video_duration_seconds": round(float(total_duration or timeline), 3),
        "window_start_seconds": 0.0,
        "window_end_seconds": round(float(target_seconds), 3),
        "selected_scene_seconds": round(float(selected_seconds), 3),
        "clip_seconds": min(float(runtime_config.value("intro_motion_clip_seconds")), 5.0),
        "clip_count_cap": int(runtime_config.value("intro_motion_clip_count")),
        "manual_scene_filter_applied": bool(has_manual_selection),
        "candidate_scene_indices": sorted(candidates),
        "selected_scene_indices": sorted(selected_indices),
        "requested_scene_indices": sorted(requested_indices),
        "delivered_scene_indices": delivered_indices,
        "protected_verified_fact_scene_indices": sorted(protected_fact_indices & candidates),
        "protected_text_number_scene_indices": sorted((protected_motion_indices or set()) & candidates),
        "motion_block_reasons": {
            str(index): reasons
            for index, reasons in sorted((motion_block_reasons or {}).items())
            if index in candidates
        },
        "failed_scene_indices": sorted(failures),
        "scene_windows": scene_windows,
    }
    detected = locate_data_surface(source_image_path, surface)
    if not detected and not detection:
        return resolved
    try:
        from PIL import Image
        with Image.open(source_image_path) as source:
            source_w, source_h = source.size
        if detection:
            xs = [point[0] for point in detection.quad]
            ys = [point[1] for point in detection.quad]
            detected = {
                "x": round(min(xs) * source_w), "y": round(min(ys) * source_h),
                "width": round((max(xs) - min(xs)) * source_w),
                "height": round((max(ys) - min(ys)) * source_h),
            }
        resolved = {
            "x": round(detected["x"] * 1920 / source_w),
            "y": round(detected["y"] * 1080 / source_h),
            "width": round(detected["width"] * 1920 / source_w),
            "height": round(detected["height"] * 1080 / source_h),
        }
        logger.info("detected data surface: %s", resolved)
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return resolved


def _apply_market_chart_overlay(
    clip_path: str,
    chart_path: str,
    duration: float,
    job_id: int,
    surface: dict | None = None,
) -> bool:
    """Center a real chart within the detected in-world panel, not fixed pixels."""
    if not chart_path or not os.path.exists(chart_path) or not _verify_video(clip_path):
        return False
    staged = clip_path + ".chart.mp4"
    surface = surface if isinstance(surface, dict) else {}
    x = int(surface.get("x", 1120)); y = int(surface.get("y", 65))
    width = int(surface.get("width", 720)); height = int(surface.get("height", 390))
    cmd = (
        f'ffmpeg -i "{clip_path}" -loop 1 -i "{chart_path}" '
        f'-filter_complex "[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease[chart];'
        f'[0:v][chart]overlay={x}+({width}-w)/2:{y}+({height}-h)/2:format=auto[v]" '
        f'-map "[v]" -t {duration:.3f} -an '
        f'-c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p '
        f'-y "{staged}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(staged):
        if os.path.exists(staged):
            os.remove(staged)
        return False
    os.replace(staged, clip_path)
    return True


def _apply_speech_bubble_overlay(
    scene: dict,
    clip_path: str,
    temp_dir: Path,
    index: int,
    duration: float,
    job_id: int,
) -> bool:
    """기존 말풍선 요청을 계보에 남기되 영상에는 합성하지 않는다."""
    spec = scene.get("speech_bubble") if isinstance(scene.get("speech_bubble"), dict) else {}
    text = str(spec.get("text") or scene.get("bubble_text") or "").strip()
    if text:
        scene.setdefault("overlay_provenance", []).append({
            "kind": "speech_bubble",
            "skipped_reason": "script_caption_only_policy",
        })
    # TTS 원문과 100% 동기화되는 ASS 자막만 영상 위 텍스트로 허용한다.
    # 말풍선은 자막과 경쟁하고 프레임 밖 UI처럼 보이므로 재활성화하지 않는다.
    return True


def _apply_scene_editorial_plan(
    scene: dict,
    clip_path: str,
    temp_dir: Path,
    index: int,
    duration: float,
    job_id: int,
) -> bool:
    """텍스트 오버레이 계획을 차단하고 원본 계획만 계보에 남긴다."""
    raw = scene.get("editorial_overlay_plan")
    data_raw = scene.get("data_overlay_plan")
    if not isinstance(raw, dict) and not isinstance(data_raw, dict):
        return True
    scene.setdefault("overlay_provenance", []).append({
        "kind": "editorial_text_overlay",
        "skipped_reason": "script_caption_only_policy",
        "plan_source": "editorial_overlay_plan" if isinstance(raw, dict) else "data_overlay_plan",
    })
    return True


def _apply_scene_annotation_overlay(
    scene: dict,
    clip_path: str,
    temp_dir: Path,
    index: int,
    duration: float,
    job_id: int,
) -> bool:
    """Apply annotations to either article captures or existing cartoon charts.

    This is deliberately a post-process layer: existing Kling/static clips and
    verified market-chart rendering stay unchanged underneath it.
    """
    # article_scene already contains its renderer-owned highlight + boxes;
    # applying source-capture coordinates a second time would misalign them.
    if str(scene.get("visual_kind") or "") == "article_scene":
        return True
    capture = _article_capture_for_scene(scene)
    has_graphics = bool(scene.get("annotations")) or bool(scene.get("callout"))
    if not capture and not has_graphics:
        return True
    credit = _article_source_credit(scene) if capture else ""
    if capture and not credit:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont

        raw_annotations = list(scene.get("annotations") or [])
        # DOM captures provide exact quote geometry. If an editor has not
        # selected custom annotations, render the captured lines with the
        # same red underline/box treatment as the supplied references.
        if capture and not raw_annotations:
            raw_annotations.extend(_default_capture_annotations(
                capture,
                scene.get("emphasis_plan") or scene.get("article_emphasis"),
            ))
        annotations = []
        for raw in raw_annotations:
            item = dict(raw)
            if item.get("type") == "circle":  # compatibility with the scene example contract
                item["type"] = "ellipse"
            annotations.append(item)
        overlay = render_annotations((1920, 1080), annotations)
        callout = scene.get("callout") if isinstance(scene.get("callout"), dict) else None
        if callout and callout.get("text"):
            scene.setdefault("overlay_provenance", []).append({
                "kind": "annotation_callout",
                "skipped_reason": "script_caption_only_policy",
            })
        draw = ImageDraw.Draw(overlay)
        font_path = NANUM_BOLD if os.path.exists(NANUM_BOLD) else NANUM_REGULAR
        try:
            font = ImageFont.truetype(font_path, 27)
        except OSError:
            font = ImageFont.load_default()
        if credit:
            draw.text((38, 1016), credit, font=font, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 220))
        overlay_path = temp_dir / f"scene_annotations_{index:03d}.png"
        overlay.save(overlay_path, "PNG")
    except Exception as exc:
        logger.exception("scene %s annotation overlay render failed", index)
        return False
    staged = clip_path + ".evidence.mp4"
    cmd = (
        f'ffmpeg -i "{clip_path}" -loop 1 -i "{overlay_path}" '
        f'-filter_complex "[0:v][1:v]overlay=0:0:format=auto[v]" '
        f'-map "[v]" -t {duration:.3f} -an -c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p '
        f'-y "{staged}" -loglevel error'
    )
    ret = _run_subprocess(cmd, job_id)
    if ret != 0 or not _verify_video(staged):
        if os.path.exists(staged):
            os.remove(staged)
        return False
    os.replace(staged, clip_path)
    return True


def _apply_editorial_overlays(scene: dict, clip_path: str, temp_dir: Path, index: int, duration: float, job_id: int) -> bool:
    chart = scene.get("market_chart")
    verified_chart = isinstance(chart, dict) and chart.get("verified") is True
    if verified_chart or _requires_verified_index_card(scene) or scene.get("data_overlay_plan"):
        scene.setdefault("overlay_provenance", []).append({
            "kind": "numeric_visual",
            "skipped_reason": "non_numeric_visual_policy",
        })
    if not _apply_scene_editorial_plan(scene, clip_path, temp_dir, index, duration, job_id):
        return False
    if not _apply_scene_annotation_overlay(scene, clip_path, temp_dir, index, duration, job_id):
        return False
    # A typed plan supersedes the legacy string bubble. The legacy path stays
    # for one release of previously persisted jobs only.
    if isinstance(scene.get("editorial_overlay_plan"), dict):
        return True
    return _apply_speech_bubble_overlay(scene, clip_path, temp_dir, index, duration, job_id)


def _is_slide_job(scenes: list[dict]) -> bool:
    """장면이 슬라이드형(배경 영상 + 후편집)으로 만들어졌는지."""
    return any(
        str(scene.get("generation_method") or "") == "slide" or str(scene.get("visual_type") or "").startswith("slide_")
        for scene in scenes or []
    )


def _apply_channel_watermark(video_path: str, temp_dir: Path, job_id: int) -> bool:
    """완성된 최종 영상 전체에 우상단 채널 배지를 한 번만 합성한다.

    자막·BGM 합성(위 5단계)과 별도 패스로 분리한 이유: 그 단계는 오디오
    믹싱 유무에 따라 4갈래로 분기하는 이미 섬세한 문자열 조립이라, 거기에
    필터를 더 얹으면 회귀 위험이 크다. 여기서는 이미 완성된 최종 영상 위에
    한 번 더 겹쳐 그리기만 하므로 그 분기와 완전히 독립적이다.

    워터마크는 채널 정체성을 위한 장식 계층이며 대본·수치의 사실성과
    무관하다. 따라서 이 합성이 실패해도(폰트/ffmpeg 환경 문제 등) 이미
    검증을 마친 영상 자체를 폐기하지 않고 경고만 남긴다.
    """
    try:
        watermark_path = str(temp_dir / "channel_watermark.png")
        render_channel_watermark_layer((1920, 1080)).save(watermark_path, "PNG")
        staged = video_path + ".watermarked.mp4"
        cmd = (
            f'ffmpeg -i "{video_path}" -i "{watermark_path}" '
            f'-filter_complex "[0:v][1:v]overlay=0:0:format=auto[v]" '
            f'-map "[v]" -map 0:a? '
            f'-c:v libx264 -preset fast -crf 18 -pix_fmt yuv420p '
            f'-c:a copy -movflags +faststart '
            f'-y "{staged}" -loglevel error'
        )
        ret = _run_subprocess(cmd, job_id)
        if ret != 0 or not _verify_video(staged):
            if os.path.exists(staged):
                os.remove(staged)
            logger.warning("scene watermark overlay failed, keeping unwatermarked final video")
            return False
        os.replace(staged, video_path)
        return True
    except OSError as exc:
        logger.warning("scene watermark overlay skipped: %s", exc)
        return False


def _get_scene_text(scene: dict) -> str:
    """씬에서 내레이션 텍스트를 읽는 키 우선순위: narration > script > text_for_tts > content > text."""
    return str(
        scene.get("narration")
        or scene.get("script")
        or scene.get("narration_text")
        or scene.get("text_for_tts")
        or scene.get("content")
        or scene.get("text")
        or ""
    )


def _assign_scene_durations_from_chunks(scenes: list, chunks: list, total_duration: float):
    """
    [S4] TTS-씬 정밀 타임라인 싱크 (Chunk Boundary Snap 버전):

    변경 이력 (P0 recovery):
    - scene 텍스트 키를 narration/script/text_for_tts/content/text 순으로 확장
    - get_time_at_ratio() 내부 선형 보간 제거 →
      씬 경계를 자막 청크의 end 타임스탬프에 정확히 스냅
    - 이미지 전환이 자막 행 전환 순간과 100% 일치하도록 보장
    """
    if not scenes or total_duration <= 0:
        return

    # ── 폴백: 청크 없음 ──────────────────────────────────────────
    if not chunks or not isinstance(chunks, list):
        total_chars = sum(len(_get_scene_text(s)) for s in scenes)
        for scene in scenes:
            char_len = len(_get_scene_text(scene))
            scene["duration"] = round(
                (char_len / total_chars) * total_duration if total_chars > 0
                else total_duration / len(scenes),
                3,
            )
        return

    # 신규 자산은 이미지 생성 전에 각 장면이 소유하는 완전한 자막 청크
    # 범위를 고정한다. 이 계약이 있으면 글자 수 비율을 전혀 사용하지 않고
    # 실제 청크 끝 시각만으로 이미지 전환을 결정한다.
    caption_contracts = [scene.get("caption_chunk_contract") for scene in scenes]
    if caption_contracts and all(isinstance(contract, dict) for contract in caption_contracts):
        expected_chunk_start = 0
        previous_scene_end = 0.0
        for scene_index, (scene, contract) in enumerate(zip(scenes, caption_contracts)):
            chunk_start = int(contract.get("chunk_start_index", -1))
            chunk_end = int(contract.get("chunk_end_index", -1))
            if chunk_start != expected_chunk_start or chunk_end < chunk_start or chunk_end >= len(chunks):
                raise RuntimeError(
                    f"장면 {scene_index + 1}의 자막 청크 범위가 연속적이지 않습니다: "
                    f"start={chunk_start}, end={chunk_end}, expected={expected_chunk_start}"
                )
            final_chunk = chunks[chunk_end]
            chunk_end_time = float(
                final_chunk.get("end")
                if final_chunk.get("end") is not None
                else float(final_chunk.get("start") or 0.0) + float(final_chunk.get("duration") or 0.0)
            )
            if chunk_end_time <= previous_scene_end:
                raise RuntimeError(
                    f"장면 {scene_index + 1}의 자막 종료 시각이 이전 이미지 전환보다 빠릅니다."
                )
            scene["start_time"] = round(previous_scene_end, 3)
            scene["end_time"] = round(chunk_end_time, 3)
            scene["duration"] = round(chunk_end_time - previous_scene_end, 3)
            scene["image_transition_after_caption_chunk"] = chunk_end
            previous_scene_end = chunk_end_time
            expected_chunk_start = chunk_end + 1

        if expected_chunk_start != len(chunks):
            raise RuntimeError("마지막 이미지 뒤에 연결되지 않은 자막 청크가 남았습니다.")
        # 오디오 꼬리의 무음은 마지막 이미지가 유지한다. 앞선 장면 경계는
        # 이미 실제 자막 청크 끝에 고정됐으므로 절대 이동하지 않는다.
        if total_duration > previous_scene_end:
            scenes[-1]["end_time"] = round(total_duration, 3)
            scenes[-1]["duration"] = round(total_duration - float(scenes[-1]["start_time"]), 3)
        return

    # ── 청크 경계 사전 빌드 ──────────────────────────────────────
    # chunk_boundaries[i] = (cum_start_char, cum_end_char, t_start, t_end)
    chunk_boundaries: list[tuple[int, int, float, float]] = []
    acc_chunk = 0
    for c in chunks:
        c_text = str(c.get("text") or "")
        c_len = len(c_text)
        t_start = float(c.get("start") or 0.0)
        t_end = float(c.get("end") or t_start + float(c.get("duration") or 0.0))
        chunk_boundaries.append((acc_chunk, acc_chunk + c_len, t_start, t_end))
        acc_chunk += c_len
    total_chunk_chars = acc_chunk

    # ── 씬 텍스트 합산 ───────────────────────────────────────────
    scene_texts = [_get_scene_text(s) for s in scenes]
    total_chars = sum(len(t) for t in scene_texts)
    if total_chars <= 0:
        for scene in scenes:
            scene["duration"] = round(total_duration / len(scenes), 3)
        return

    # ── 청크 경계 스냅 함수 ──────────────────────────────────────
    def snap_to_chunk_end(scene_char_end: int) -> float:
        """scene_char_end 이전까지를 커버하는 마지막 청크의 end 타임스탬프 반환.

        - 비율 보간을 완전히 제거하고 실제 청크 end 값에만 스냅.
        - 씬 경계가 자막 행 전환 순간과 일치하게 된다.
        """
        if total_chunk_chars <= 0:
            return total_duration
        # scene 문자 위치 → chunk 문자 위치로 비율 변환
        target_chunk_char = round((scene_char_end / total_chars) * total_chunk_chars)
        # target_chunk_char 이전까지 포함하는 청크 중 마지막을 찾음
        snapped_t_end = total_duration
        for (bc_start, bc_end, bt_start, bt_end) in chunk_boundaries:
            if bc_start < target_chunk_char:
                snapped_t_end = bt_end          # 계속 갱신 → 마지막 적합 청크
            else:
                break                           # 청크는 순서대로 정렬되어 있음
        return round(snapped_t_end, 3)

    # ── 씬별 start_time / end_time 할당 ─────────────────────────
    # 첫 씬 start = 0, 이후 씬 start = 이전 씬 end (체인 방식)
    acc_scene = 0
    prev_end = 0.0
    for scene, scene_text in zip(scenes, scene_texts):
        acc_scene += len(scene_text)
        e_time = snap_to_chunk_end(acc_scene)
        s_time = prev_end
        dur = round(max(0.05, e_time - s_time), 3)
        scene["start_time"] = round(s_time, 3)
        scene["end_time"] = round(e_time, 3)
        scene["duration"] = dur
        prev_end = e_time

    # ── 마지막 씬 보정 (total_duration 오차 정렬) ─────────────────
    if scenes:
        sum_dur = sum(s["duration"] for s in scenes)
        diff = round(total_duration - sum_dur, 3)
        if abs(diff) > 0.001:
            scenes[-1]["duration"] = round(max(0.05, scenes[-1]["duration"] + diff), 3)
            scenes[-1]["end_time"] = round(scenes[-1]["start_time"] + scenes[-1]["duration"], 3)


def _assign_editorial_overlay_timing(scenes: list[dict]) -> None:
    """Give overlays a short interval derived from the TTS-mapped scene time."""
    for scene in scenes:
        if not isinstance(scene.get("editorial_overlay_plan"), dict) and not isinstance(scene.get("data_overlay_plan"), dict):
            continue
        duration = max(.05, float(scene.get("duration") or 0))
        start = min(max(.18, duration * .12), max(.05, duration - .5))
        end = min(duration - .08, max(start + .7, duration * .78))
        if end <= start:
            start, end = .0, duration
        scene["overlay_timing"] = {
            "start": round(start, 3),
            "end": round(end, 3),
            "source": "tts_scene_duration",
        }


def _overlay_timing(scene: dict, duration: float) -> tuple[float, float]:
    raw = scene.get("overlay_timing") if isinstance(scene.get("overlay_timing"), dict) else {}
    start = max(0.0, min(float(raw.get("start", 0.0)), max(0.0, duration - .05)))
    end = max(start + .01, min(float(raw.get("end", duration)), duration))
    return start, end
