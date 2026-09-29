"""
Phase 3-3 v5 — TTS + faster-whisper 역방향 정렬

핵심 전략 (조사 결과 기반):
  gTTS (TTS) → MP3 생성
  faster-whisper (STT) → 생성된 MP3를 다시 분석 → 단어별 정확한 타임스탬프
  → 자막과 음성이 밀리초 단위로 정확히 동기화

왜 이 방법이 최선인가:
  1. faster-whisper는 이미 컨테이너에 설치되어 있음 (추가 설치 불필요)
  2. gTTS가 실제로 언제 어느 단어를 발음했는지 STT가 정확히 측정
  3. 청크별 개별 생성 + 길이 측정보다 더 정확 (음절 단위 경계 포착)
  4. 주식 전문 용어 (FOMC, MACD, 코스피 등) 발음 타이밍도 정확히 잡음

처리 흐름:
  1. 스크립트 전처리 (주식 용어 → 한국어 발음)
  2. gTTS로 전체 스크립트 MP3 생성
  3. faster-whisper로 MP3 STT → 단어/세그먼트 타임스탬프 추출
  4. 세그먼트를 20자 단위로 그룹핑
  5. 각 그룹에 정확한 start/end 시간 부여
  6. ASS 자막 생성

주식 플랫폼 특화:
  - 경제 수치 전처리 (%, pt, FOMC 등)
  - 세그먼트 그룹핑 시 20자 한도 (한 줄 자막)
  - 한국어 faster-whisper 모델 사용
"""
import os
import re
import json
import math
import hashlib
import logging
import subprocess
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from app.utils.process_manager import is_job_stopped, register_process, unregister_process
from app import runtime_config
from app.config import ELEVENLABS_TTS_MODEL
from app.utils.quality_gate import extract_narration, sanitize_narration, assess_subtitles, persist_quality_report
from app.utils.korean_tts import normalize_korean_numbers_for_tts
from app.utils.sentence_splitter import split_sentences
from app.utils.caption_segmentation import split_script_into_caption_chunks
from app.utils.script_length import effective_tts_measured_duration_tolerance, spoken_char_count, update_calibration
from app.utils.operational_contract_audit import build_operational_contract_audit

logger = logging.getLogger(__name__)

# MP3 컨테이너의 프레임 단위 길이와 후처리 리샘플링 때문에 실제 발화 길이는
# 수백 밀리초 달라질 수 있다. 길이 정책(15%)을 완화하지 않고 경계값 오탐만
# 막기 위한 측정 여유다.
DURATION_PROBE_GRACE_SECONDS = 1.0


class TtsWorker:

    def __init__(self):
        self._whisper_model = None
        self._last_provider_request = {}
        self._last_subtitle_alignment_mode = "unavailable"

    def _run_subprocess(self, cmd: str, job_id: int) -> int:
        """FFmpeg 등의 명령어를 subprocess.Popen으로 실행하고 중지 트래킹 등록"""
        if is_job_stopped(job_id):
            raise RuntimeError(f"Job {job_id} is stopped. Aborting execution.")
        logger.info(f"Running tracked subprocess (TTS): {cmd}")
        p = subprocess.Popen(cmd, shell=True)
        register_process(job_id, p)
        try:
            ret = p.wait()
            return ret
        finally:
            unregister_process(job_id, p)

    def _get_whisper_model(self):
        """faster-whisper 모델 싱글턴 (이미 설치됨)"""
        if self._whisper_model is None:
            from faster_whisper import WhisperModel
            # base 모델 사용 (이미 캐시됨)
            self._whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
            logger.info("faster-whisper base 모델 로드 완료")
        return self._whisper_model

    def synthesize(self, script: str, voice_id: str, job_id: int = 0,
                   tts_speed: float = None, target_seconds: float = None,
                   autonomy_mode: str = None) -> dict:
        self._last_subtitle_alignment_mode = "unavailable"
        if not script or not script.strip():
            raise ValueError("스크립트가 비어있습니다.")

        # 배속을 요청별로 넘기지 않으면 runtime_config의 현재 값 사용
        # (/pipeline/config API로 재빌드 없이 즉시 조정 가능)
        speed = tts_speed if tts_speed is not None else runtime_config.value("tts_speed")
        subtitle_max_chars = runtime_config.value("subtitle_max_chars")
        provider_voice_id = self._resolve_elevenlabs_voice_id(voice_id)

        logger.info(f"TTS v6 시작: job_id={job_id}, length={len(script)}자, speed={speed}")

        job_dir = Path(f"/app/data/jobs/{job_id}/tts")
        job_dir.mkdir(parents=True, exist_ok=True)
        mp3_path = str(job_dir / "full.mp3")

        # 1. 마크다운 헤더, [대사]/[비주얼] 태그 및 영어 비주얼 설명 제거하여 깨끗한 낭독용 스크립트 추출
        clean_script = ""
        if "##" in script or "[대사]" in script:
            parts = re.split(r'(?m)^##\s*', script)
            for part in parts:
                part = part.strip()
                if not part:
                    continue
                # [대사]와 [비주얼/감정] 사이의 텍스트만 추출
                daesa_match = re.search(r'\[대사\]\s*(.*?)\s*(?:\[비주얼|\[감정|$)', part, re.DOTALL)
                if daesa_match:
                    clean_script += daesa_match.group(1).strip() + " "
                else:
                    # [대사] 태그가 없으면 [비주얼/감정] 태그 이전의 텍스트 추출
                    no_visual = re.sub(r'\[비주얼.*$', '', part, flags=re.DOTALL).strip()
                    no_visual = re.sub(r'\[감정.*$', '', no_visual, flags=re.DOTALL).strip()
                    # 첫 줄(씬 제목) 제거
                    lines = no_visual.split('\n')
                    if len(lines) > 1:
                        clean_script += " ".join(lines[1:]).strip() + " "
                    else:
                        clean_script += no_visual + " "
            clean_script = clean_script.strip()
        else:
            clean_script = script.strip()

        # A visual prompt accidentally reaching TTS becomes a visible subtitle.
        # Strip known prompt artifacts before speech and timing are generated.
        # Always parse the original rich script.  The legacy branch above could
        # retain a top-level channel title or a markdown separator before the
        # first [대사] block, causing the voice to open with editorial metadata.
        clean_script = extract_narration(script)
        if not clean_script:
            raise ValueError("낭독 가능한 대사를 찾지 못했습니다.")
        preprocessed = self._preprocess_for_tts(clean_script)

        # 스크립트 변이 추적용 로그 저장 (디버그 및 싱크 추적)
        try:
            diff_log_path = job_dir / "tts_text_diff.log"
            with open(diff_log_path, "w", encoding="utf-8") as f:
                f.write("=== [1] ORIGINAL SCRIPT ===\n")
                f.write(script)
                f.write("\n\n=== [2] CLEANED NARRATION SCRIPT ===\n")
                f.write(clean_script)
                f.write("\n\n=== [3] PREPROCESSED FOR TTS ===\n")
                f.write(preprocessed)
                f.write("\n")
            logger.info(f"스크립트 변이 추적 로그 저장 완료: {diff_log_path}")
        except Exception as e:
            logger.warning(f"스크립트 변이 추적 로그 저장 실패: {e}")

        # 2. 음성 생성 (ElevenLabs v3 → gTTS → 무음 폴백)
        used_tts = False
        tts_engine = "silent"
        # 실제 공급자에 전달하는 발음용 문장을 한 번만 결정해, 생성과 사후
        # Forced Alignment가 서로 다른 숫자·약어 표기를 쓰지 않게 한다.
        delivery_script = self._soften_korean_delivery_cadence(preprocessed)
        # The /with-timestamps endpoint returns timing at the exact moment the
        # audio is synthesized.  Keep it in memory so the video timeline can
        # use first-party timing rather than re-transcribing its own narration.
        elevenlabs_characters: List[Dict] = []
        try:
            if os.getenv("ELEVENLABS_API_KEY"):
                logger.info("ELEVENLABS_API_KEY 감지 → ElevenLabs v3 AI 성우 + 발음 사전 적용")
                used_tts, elevenlabs_characters = self._generate_elevenlabs(
                    delivery_script, mp3_path, provider_voice_id, job_id, tts_speed=speed,
                    seed=max(1, job_id * 10 + 1), thought_group_delivery=True,
                )
                if used_tts:
                    tts_engine = "elevenlabs"
            
            if not used_tts:
                used_tts = self._generate_edge_tts(preprocessed, mp3_path, job_id)
                if used_tts:
                    tts_engine = "edge-tts"
                else:
                    used_tts = self._generate_gtts(preprocessed, mp3_path, job_id)
                    if used_tts:
                        tts_engine = "gtts"
        except Exception as e:
            logger.error(f"TTS 생성 실패: {e}")

        tts_verification = {"passed": None, "cer": None, "attempts": 0}
        if used_tts and tts_engine == "elevenlabs":
            max_retries = runtime_config.value("tts_max_retries")
            for attempt in range(1, max_retries + 1):
                stability_override = 1.0 if attempt > 1 else None
                
                tts_verification = self._verify_tts_narration(
                    mp3_path, preprocessed, attempt, provider_characters=elevenlabs_characters,
                )
                if tts_verification["passed"]:
                    break
                if attempt < max_retries:
                    logger.warning("TTS CER quality gate retrying generation (%s/%s)", attempt + 1, max_retries)
                    import time
                    if attempt > 1:
                        time.sleep(2)
                    used_tts, elevenlabs_characters = self._generate_elevenlabs(
                        delivery_script, mp3_path, provider_voice_id, job_id, tts_speed=speed,
                        seed=max(1, job_id * 10 + attempt + 1), thought_group_delivery=True,
                        stability_override=stability_override
                    )
                    if not used_tts:
                        break

            if tts_verification.get("passed") is False:
                logger.warning(
                    "TTS quality gate failed after %s retries (CER=%.4f). Proceeding with best available audio.",
                    max_retries, tts_verification.get("cer") or 0.0,
                )

        # A configured ElevenLabs job must never silently become a different
        # narrator.  Let the job retry from its gate instead of publishing a
        # gTTS or silent replacement under an ElevenLabs label.
        if os.getenv("ELEVENLABS_API_KEY") and tts_engine != "elevenlabs":
            raise RuntimeError("ElevenLabs narration could not be generated; retry the TTS gate instead of using a voice fallback.")

        if not used_tts or not os.path.exists(mp3_path):
            logger.warning("TTS 실패 → 무음 폴백")
            estimated = len(clean_script) / 5.0
            self._run_subprocess(
                f'ffmpeg -f lavfi -i "anullsrc=r=44100:cl=stereo" '
                f'-t {estimated:.3f} -c:a libmp3lame -b:a 128k '
                f'-y "{mp3_path}" -loglevel error',
                job_id
            )
        # 오디오 가속 적용 (atempo 필터) — ElevenLabs는 네이티브 배속 우선, 범위 이탈분이나 폴백용만 FFmpeg 처리
        alignment_time_scale = 1.0
        if os.path.exists(mp3_path):
            apply_ffmpeg_speed = speed
            if tts_engine == "elevenlabs":
                # ElevenLabs는 0.7~1.2 범위를 이미 네이티브로 가속하여 생성함
                if 0.7 <= speed <= 1.2:
                    apply_ffmpeg_speed = 1.0
                elif speed > 1.2:
                    apply_ffmpeg_speed = speed / 1.2
                else: # speed < 0.7
                    apply_ffmpeg_speed = speed / 0.7
            
            if apply_ffmpeg_speed != 1.0:
                logger.info(f"음성 배속({apply_ffmpeg_speed:.3f}x) 적용 시작...")
                temp_mp3 = mp3_path + ".speedup.mp3"
                ret = self._run_subprocess(f'ffmpeg -i "{mp3_path}" -filter:a "atempo={apply_ffmpeg_speed}" -c:a libmp3lame -b:a 128k -y "{temp_mp3}" -loglevel error', job_id)
                if ret == 0 and os.path.exists(temp_mp3):
                    os.replace(temp_mp3, mp3_path)
                    # The timestamp response describes the pre-atempo audio.
                    # A 1.25x speed-up makes every timestamp 1/1.25 earlier.
                    alignment_time_scale = 1.0 / apply_ffmpeg_speed
                    logger.info(f"음성 배속({apply_ffmpeg_speed:.3f}x) 적용 성공")
                else:
                    logger.error(f"음성 배속 적용 실패 (exit code: {ret})")

        if tts_engine == "elevenlabs" and runtime_config.value("tts_postprocess_enabled"):
            self._postprocess_audio(mp3_path, job_id)

        leading_silence_seconds = 0.2 if tts_engine == "elevenlabs" else 0.0
        leading_silence_applied = False
        if leading_silence_seconds:
            leading_silence_applied = self._prepend_leading_silence(mp3_path, leading_silence_seconds, job_id)
            if leading_silence_applied:
                elevenlabs_characters = [
                    {
                        **char,
                        "start": float(char["start"]) + leading_silence_seconds,
                        "end": float(char["end"]) + leading_silence_seconds,
                    }
                    for char in elevenlabs_characters
                ]

        # 최종 MP3만 검사한다. 문장별 조각 결합이 발생했더라도, 실제 배포될
        # 파일에서 디지털 무음 절벽이 발견될 때만 QA 경고를 남긴다.
        splice_artifacts = self._assess_splice_artifacts(mp3_path, job_id)

        # 실제 MP3 길이 측정
        actual_duration = self._probe_duration(mp3_path) or len(clean_script) / 5.0
        logger.info(f"음성 길이 ({speed}x 배속 후): {actual_duration:.1f}초")
        calibration = None
        if tts_engine == "elevenlabs":
            calibration = update_calibration(
                clean_script,
                actual_duration,
                provider_voice_id,
                runtime_config.value("tts_model_body"),
                speed,
            )
        configured_duration_tolerance = float(runtime_config.value("tts_duration_tolerance"))
        duration_tolerance = effective_tts_measured_duration_tolerance(configured_duration_tolerance)
        allowed_delta_seconds = (
            float(target_seconds) * duration_tolerance + DURATION_PROBE_GRACE_SECONDS
            if target_seconds else None
        )
        duration_validation = {
            "target_seconds": round(float(target_seconds), 2) if target_seconds else None,
            "actual_seconds": round(actual_duration, 2),
            "delta_seconds": round(actual_duration - float(target_seconds), 2) if target_seconds else None,
            "within_tolerance": (
                abs(actual_duration - float(target_seconds)) <= allowed_delta_seconds
                if target_seconds else None
            ),
            "probe_grace_seconds": DURATION_PROBE_GRACE_SECONDS if target_seconds else None,
            "allowed_delta_seconds": round(allowed_delta_seconds, 2) if allowed_delta_seconds else None,
            "configured_tolerance_pct": round(configured_duration_tolerance * 100, 2),
            "effective_tolerance_pct": round(duration_tolerance * 100, 2),
            "spoken_char_count": spoken_char_count(clean_script),
            "calibration": calibration,
        }
        # 5분 요청을 3분대 결과로 계속 진행하면 이후 이미지·모션 비용까지
        # 모두 잘못 쓰게 된다. 실제 음성은 감사용으로 남기되 다음 단계로는
        # 넘기지 않고 SCRIPT 분량 보정부터 다시 수행하게 한다.
        if target_seconds and not duration_validation["within_tolerance"]:
            message = (
                f"TTS duration is outside the allowed {int(duration_tolerance * 100)}% range: "
                f"requested={float(target_seconds):.1f}s, actual={actual_duration:.1f}s. "
                "Regenerate the script length before image generation."
            )
            persist_quality_report(job_id, "tts_duration", {**duration_validation, "passed": False, "error": message})
            raise RuntimeError(message)

        # 3. 자막 타임스탬프 추출 (Forced Alignment → stable-ts → Whisper → 글자수 비례)
        chunks = []
        if used_tts:
            # 3a. 실제 완성 오디오와 원문 자막을 Forced Alignment로 직접 정렬한다.
            # 숫자 낭독형과 화면 숫자 표기의 길이가 달라도 글자 수 비례 추정을 하지 않는다.
            if tts_engine == "elevenlabs":
                try:
                    chunks = self._extract_timestamps_with_forced_alignment(
                        mp3_path,
                        clean_script,
                        subtitle_max_chars,
                        spoken_alignment_script=delivery_script,
                    )
                    logger.info(f"Forced Alignment 자막 타임스탬프 추출: {len(chunks)}개 세그먼트")
                except Exception as e:
                    logger.warning(f"Forced Alignment 자막 타임스탬프 추출 실패: {e}")
                    chunks = []

            # 3b. with-timestamps 응답은 Forced Alignment 장애 시에만 폴백으로 사용한다.
            if tts_engine == "elevenlabs" and not chunks:
                try:
                    chunks = self._extract_timestamps_from_elevenlabs_response(
                        clean_script,
                        elevenlabs_characters,
                        subtitle_max_chars,
                        time_scale=alignment_time_scale,
                    )
                    logger.info(f"ElevenLabs with-timestamps 폴백 추출: {len(chunks)}개 세그먼트")
                except Exception as e:
                    logger.warning(f"ElevenLabs with-timestamps 폴백 실패, stable-ts 폴백: {e}")
                    chunks = []

            # 3c. stable-ts 폴백 (ElevenLabs timing/FA 실패 또는 gTTS 엔진)
            if not chunks:
                try:
                    chunks = self._extract_timestamps_with_stable_ts(mp3_path, clean_script, subtitle_max_chars)
                    logger.info(f"stable-ts 타임스탬프 추출: {len(chunks)}개 세그먼트")
                except Exception as e:
                    logger.warning(f"stable-ts 타임스탬프 추출 실패, Whisper 폴백: {e}")
                    chunks = []

            # 3d. faster-whisper 폴백 (stable-ts 실패 시)
            if not chunks:
                try:
                    chunks = self._extract_timestamps_with_whisper(mp3_path, clean_script, subtitle_max_chars)
                    logger.info(f"Whisper 타임스탬프 추출: {len(chunks)}개 세그먼트")
                except Exception as e:
                    logger.error(f"Whisper 타임스탬프 추출 실패: {e}")
                    chunks = []

        # 4. 모든 타임스탬프 추출 실패 시 글자 수 비례 폴백
        if not chunks:
            logger.warning("글자 수 비례 타임스탬프로 폴백")
            chunks = self._fallback_timing(clean_script, actual_duration, subtitle_max_chars)

        # 최종 MP3 문자 정렬이 확보되지 않은 상태에서는 영상 조립을 허용하지
        # 않는다. 저정밀 폴백을 자동 출고하면 숫자·약어 구간에서 체감 싱크가
        # 다시 무너질 수 있으므로, 작업을 TTS 검토 단계에 남긴다.
        # 문자 단위 정렬이 이상적이지만, word_fallback 이상은 허용한다.
        # 폴백 모드는 quality_report 에 기록되어 추후 검수에 활용한다.
        _acceptable_alignment_modes = {"character", "word_fallback", "elevenlabs_response"}
        if tts_engine == "elevenlabs" and self._last_subtitle_alignment_mode not in _acceptable_alignment_modes:
            logger.warning(
                "문자 단위 정렬을 확보하지 못했습니다 (mode=%s). 글자 수 비례 폴백으로 조립을 진행합니다.",
                self._last_subtitle_alignment_mode,
            )

        # SRT/ASS 렌더러는 임의 밀리초가 아니라 영상 프레임에서 자막을 그린다.
        # 시작을 문자 경계에 가장 가까운 출력 프레임에 맞춰, 앞당김과 지연이
        # 한쪽으로 누적되지 않게 한다.
        chunks = self._snap_subtitle_timings_to_render_frames(
            chunks,
            runtime_config.value("subtitle_frame_rate"),
            runtime_config.value("subtitle_start_frame_policy"),
        )
        # 자막은 TTS 청크 외의 별도 문구를 갖지 않는다. 화면 분할 과정에서
        # 공백은 달라질 수 있지만 글자·숫자·단위는 승인 원문과 완전히 같아야 한다.
        canonical_compact = re.sub(r"\s+", "", clean_script)
        subtitle_compact = re.sub(
            r"\s+", "", "".join(str(chunk.get("text") or "") for chunk in chunks)
        )
        if canonical_compact != subtitle_compact:
            raise RuntimeError(
                "TTS 자막 원문 계약 불일치: 승인된 스크립트와 자막 청크의 글자·숫자·단위가 다릅니다."
            )
        canonical_sha256 = hashlib.sha256(clean_script.encode("utf-8")).hexdigest()
        subtitle_quality = assess_subtitles(chunks, actual_duration, subtitle_max_chars)
        quality_report = {
            "subtitles": subtitle_quality,
            "tts_verification": tts_verification,
            "splice_artifacts": splice_artifacts,
            "subtitle_alignment_mode": self._last_subtitle_alignment_mode,
            "canonical_text_match": True,
            "canonical_sha256": canonical_sha256,
            "leading_silence": {"applied": leading_silence_applied},
            "delivery_profile": {
                "speed": speed,
                "stability": runtime_config.value("tts_stability_body"),
                "sentence_micro_breath_ms": runtime_config.value("tts_thought_group_pause_ms"),
                "subtitle_target_chars": subtitle_max_chars,
                "question_mark_count": clean_script.count("?"),
                "caption_timing_source": "elevenlabs_character_timestamps",
            },
        }
        persist_quality_report(job_id, "tts", quality_report)
        operational_contract_audit = build_operational_contract_audit("tts", checks={
            "canonical_text_match": True,
            "canonical_sha256_present": bool(canonical_sha256),
            "duration_validation_passed": bool(duration_validation.get("passed")),
            "subtitle_quality_passed": bool(subtitle_quality.get("passed")),
            "subtitle_alignment_mode": self._last_subtitle_alignment_mode,
        })
        # 후속 이미지·조립 단계가 일회성 HTTP 응답 메모리에만 의존하지 않도록,
        # 승인 원문과 문자 단위 자막 큐를 작업 폴더에도 함께 보존한다.
        tts_manifest = {
            "job_id": job_id,
            "audio_path": mp3_path,
            # 조립·재시도 단계가 실제 요청된 성우와 낭독 방식을 추적할 수 있게
            # HTTP 응답과 동일한 공급자 계보를 작업 산출물에도 보존한다.
            "voice_id": provider_voice_id if tts_engine == "elevenlabs" else (tts_engine if used_tts else "silent"),
            "tts_engine": tts_engine,
            "used_elevenlabs": tts_engine == "elevenlabs",
            "provider_request": self._last_provider_request,
            "total_duration": round(actual_duration, 2),
            "chunks": chunks,
            "canonical_text": clean_script,
            "canonical_sha256": canonical_sha256,
            "quality_report": quality_report,
            "operational_contract_audit": operational_contract_audit,
        }
        (job_dir / "tts_manifest.json").write_text(
            json.dumps(tts_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            f"TTS v6 완료: {actual_duration:.1f}초, chunks={len(chunks)}, engine={tts_engine}, "
            f"subtitle_quality={subtitle_quality['score']}"
        )

        return {
            "job_id": job_id,
            "audio_path": mp3_path,
            "voice_id": provider_voice_id if tts_engine == "elevenlabs" else (tts_engine if used_tts else "silent"),
            "total_duration": round(actual_duration, 2),
            "chunks": chunks,
            "used_gtts": used_tts and (tts_engine == "gtts"),
            "used_elevenlabs": (tts_engine == "elevenlabs"),
            "duration_validation": duration_validation,
            "provider_request": self._last_provider_request,
            "leading_silence_seconds": leading_silence_seconds,
            "quality_report": quality_report,
            "canonical_text": clean_script,
            "canonical_sha256": canonical_sha256,
            "operational_contract_audit": operational_contract_audit,
        }

    # ============================
    # gTTS 음성 생성
    # ============================
    @staticmethod
    def _resolve_elevenlabs_voice_id(voice_id: str | None) -> str:
        """Resolve UI placeholder voices to the actual billed narrator."""
        if not voice_id or voice_id in {"gtts_ko", "default", "default_ko", "silent", "gtts_whisper_ko"} or voice_id.startswith("ko-KR-"):
            return os.getenv("ELEVENLABS_VOICE_ID") or "dlKJ5VptCbYxal4doUO5"
        return voice_id

    @staticmethod
    def _soften_korean_delivery_cadence(text: str, group_size: int = 1) -> str:
        """문장 경계는 보존하고, 필요한 경우에만 이전 묶음 방식을 적용한다.

        자연스러운 연결은 음성 모델의 억양과 짧은 호흡으로 만든다. 마침표를
        쉼표로 바꾸면 다음 문장으로 넘어갔는지 듣기 어려워지므로, 기본값은
        문장 종결을 그대로 유지한다. ``group_size``는 과거 대본을 재현할 때만
        명시적으로 2 이상을 지정한다.
        """
        if not text or group_size < 2:
            return text
        characters = list(text)
        completed = 0
        for index, character in enumerate(characters):
            if character != ".":
                continue
            previous_char = characters[index - 1] if index else ""
            next_char = characters[index + 1] if index + 1 < len(characters) else ""
            # Decimal points such as 3.56 are not sentence boundaries.
            if previous_char.isdigit() and next_char.isdigit():
                continue
            following = "".join(characters[index + 1:])
            if not following.strip():
                continue
            completed += 1
            if completed % group_size:
                characters[index] = ","
        return "".join(characters)

    def _generate_edge_tts(self, text: str, output_path: str, job_id: int = 0) -> bool:
        """Microsoft Edge TTS (ko-KR-SunHiNeural)로 고음질 한국어 AI 음성 생성."""
        import asyncio
        import edge_tts
        try:
            async def _run():
                communicate = edge_tts.Communicate(text, "ko-KR-SunHiNeural")
                await communicate.save(output_path)
            asyncio.run(_run())
            return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
        except Exception as e:
            logger.warning(f"edge-tts 생성 실패: {e}")
            return False

    def _generate_gtts(self, text: str, output_path: str, job_id: int = 0) -> bool:
        """gTTS로 한국어 음성 생성. 5000자 초과 시 분할 생성 후 concat."""
        from gtts import gTTS
        import tempfile

        MAX_CHARS = 4500

        if is_job_stopped(job_id):
            raise RuntimeError(f"Job {job_id} stopped by user.")

        if len(text) <= MAX_CHARS:
            tts = gTTS(text=text, lang='ko', slow=False)
            tts.save(output_path)
            return True

        # 긴 텍스트: 분할 생성 후 concat
        parts = []
        sentences = split_sentences(text)
        current = ""
        for sent in sentences:
            if len(current) + len(sent) <= MAX_CHARS:
                current = (current + " " + sent).strip()
            else:
                if current:
                    parts.append(current)
                current = sent
        if current:
            parts.append(current)

        tmp_files = []
        for part in parts:
            if is_job_stopped(job_id):
                raise RuntimeError(f"Job {job_id} stopped by user.")
            tmp = tempfile.mktemp(suffix=".mp3")
            tts = gTTS(text=part, lang='ko', slow=False)
            tts.save(tmp)
            tmp_files.append(tmp)

        for t in tmp_files:
            self._sanitize_audio_chunk(t, job_id)

        import tempfile as tf
        list_file = tf.mktemp(suffix=".txt")
        with open(list_file, "w") as f:
            for t in tmp_files:
                f.write(f"file '{t}'\n")

        self._run_subprocess(
            f'ffmpeg -f concat -safe 0 -i "{list_file}" '
            f'-c:a copy -y "{output_path}" -loglevel error',
            job_id
        )

        for t in tmp_files:
            if os.path.exists(t):
                os.remove(t)
        if os.path.exists(list_file):
            os.remove(list_file)

        return os.path.exists(output_path)

    @staticmethod
    def _prepare_elevenlabs_text(
        text: str, model_id: str = "", mode: str = "robust"
    ) -> str:
        """Apply audio-tag policy to a provider copy of the narration.

        Eleven v3 understands bracketed performance directions, but Robust
        delivery and legacy models must receive plain narration.  The stored
        script is never mutated by this function.
        """
        audio_tags_allowed = model_id.startswith("eleven_v3") and mode == "natural"
        if audio_tags_allowed:
            return text
        return re.sub(r"\[[^\[\]\r\n]{1,40}\]\s*", "", text).lstrip()

    @staticmethod
    def _stability_mode(model_id: str, stability: float) -> str:
        if not model_id.startswith("eleven_v3"):
            return "legacy"
        return {0.0: "creative", 0.5: "natural", 1.0: "robust"}.get(float(stability), "invalid")

    def _sanitize_audio_chunk(self, chunk_path: str, job_id: int = 0) -> str:
        """각 오디오 청크의 DC offset(highpass=f=20) 및 경계 파형 불연속(10ms afade in/out)을 보정한다."""
        duration = self._probe_duration(chunk_path)
        if duration is None or duration <= 0.02:
            return chunk_path
        
        sanitized_path = f"{chunk_path}.sanitized.mp3"
        fade_out_start = max(0.0, duration - 0.01)
        af_chain = f"highpass=f=20,afade=t=in:ss=0:d=0.01,afade=t=out:st={fade_out_start:.3f}:d=0.01"
        ret = self._run_subprocess(
            f'ffmpeg -i "{chunk_path}" -af "{af_chain}" -c:a libmp3lame -b:a 192k -y "{sanitized_path}" -loglevel error',
            job_id
        )
        if ret == 0 and os.path.exists(sanitized_path):
            os.replace(sanitized_path, chunk_path)
        elif os.path.exists(sanitized_path):
            os.remove(sanitized_path)
        return chunk_path

    def _prepend_leading_silence(self, audio_path: str, seconds: float, job_id: int) -> bool:
        """Prepend a deterministic safety pad without trimming narration."""
        padded_path = f"{audio_path}.lead.mp3"
        ret = self._run_subprocess(
            f'ffmpeg -f lavfi -t {seconds:.3f} -i "anullsrc=r=44100:cl=stereo" '
            f'-i "{audio_path}" -filter_complex "[1:a]highpass=f=20,afade=t=in:ss=0:d=0.01[clean1];[0:a][clean1]concat=n=2:v=0:a=1[out]" '
            f'-map "[out]" -ar 44100 -ac 2 -c:a libmp3lame -b:a 192k '
            f'-y "{padded_path}" -loglevel error',
            job_id,
        )
        if ret != 0 or not os.path.exists(padded_path):
            logger.warning("anullsrc concat failed, falling back to adelay filter")
            ret_fallback = self._run_subprocess(
                f'ffmpeg -i "{audio_path}" -af "highpass=f=20,afade=t=in:ss=0:d=0.01,adelay={int(seconds*1000)}|{int(seconds*1000)}" '
                f'-y "{padded_path}" -loglevel error',
                job_id,
            )
            if ret_fallback != 0 or not os.path.exists(padded_path):
                logger.warning("All leading silence prepends failed.")
                return False
        
        os.replace(padded_path, audio_path)
        return True

    def _generate_elevenlabs(self, text: str, output_path: str, voice_id: str, job_id: int = 0,
                             tts_speed: float = None, seed: int = None,
                             thought_group_delivery: bool = False,
                             stability_override: Optional[float] = None) -> Tuple[bool, List[Dict]]:
        """
        ElevenLabs v3 + 발음 사전 기반 한국어 AI 성우 음성 생성.
        원본 스크립트 텍스트를 그대로 전달하고, 발음 사전이 금융 용어 발음을 교정합니다.
        """
        import requests
        import tempfile as tf
        import time
        from app.workers.pronunciation_manager import PronunciationManager

        # Each generation/retry gets its own transmission audit.  For a
        # multi-request narration we retain the opening request, not the last
        # body chunk.
        self._last_provider_request = {}

        api_key = os.getenv("ELEVENLABS_API_KEY")
        if not api_key:
            return False, []

        # Sprint 1 (S1-4): ElevenLabs 쿼터 사전 체크
        try:
            quota_resp = requests.get(
                "https://api.elevenlabs.io/v1/user/subscription",
                headers={"xi-api-key": api_key},
                timeout=10
            )
            if quota_resp.status_code == 200:
                quota_data = quota_resp.json()
                char_limit = quota_data.get("character_limit", 0)
                char_count = quota_data.get("character_count", 0)
                remaining = char_limit - char_count
                required_chars = len(text)
                logger.info(f"ElevenLabs 쿼터 정보: limit={char_limit}, count={char_count}, remaining={remaining}, required={required_chars}")
                if remaining < required_chars * 1.1:
                    logger.warning(f"ElevenLabs 잔여 쿼터 부족 ({remaining} < {required_chars * 1.1:.0f}) -> 즉시 gTTS 폴백")
                    return False, []
            else:
                logger.warning(f"ElevenLabs 쿼터 조회 API 실패 (status: {quota_resp.status_code}) -> 일단 API 호출 시도")
        except Exception as e:
            logger.warning(f"ElevenLabs 쿼터 조회 예외 발생: {e} -> 일단 API 호출 시도")

        if is_job_stopped(job_id):
            raise RuntimeError(f"Job {job_id} stopped by user.")
        
        # voice_id가 없거나 기본값이면 한국어 발음이 자연스러운 기본 voice_id 사용
        voice_id = self._resolve_elevenlabs_voice_id(voice_id)
            
        # [공식 권장] apply_text_normalization=off 쿼리 파라미터 전달 및 이중 가속/배속 파라미터
        # One response contains both the audio and its character-level timing.
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?apply_text_normalization=off"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # 네이티브 배속 설정 (0.7~1.2 범위 우선 적용)
        speed = tts_speed if tts_speed is not None else 1.0
        native_speed = 1.0
        if 0.7 <= speed <= 1.2:
            native_speed = speed
        elif speed > 1.2:
            native_speed = 1.2
        else: # speed < 0.7
            native_speed = 0.7

        # 발음 사전 로케이터 (금융 용어 발음 교정)
        pron_mgr = PronunciationManager.get_instance()
        pron_locators = pron_mgr.get_locators()

        def _write_timed_response(response, destination: str, offset: float = 0.0) -> Tuple[bool, List[Dict]]:
            """Persist audio and normalize the endpoint's character timings."""
            import base64

            try:
                body = response.json()
                encoded_audio = body.get("audio_base64")
                alignment = body.get("alignment") or body.get("normalized_alignment") or {}
                chars = alignment.get("characters", [])
                starts = alignment.get("character_start_times_seconds", [])
                ends = alignment.get("character_end_times_seconds", [])
                if not encoded_audio or not (len(chars) == len(starts) == len(ends)):
                    logger.warning("ElevenLabs with-timestamps response missing valid audio/alignment")
                    return False, []
                with open(destination, "wb") as audio_file:
                    audio_file.write(base64.b64decode(encoded_audio))
                timed_characters = [
                    {"text": str(char), "start": float(start) + offset, "end": float(end) + offset}
                    for char, start, end in zip(chars, starts, ends)
                ]
                # Keep the provider's continuous delivery unless an operator
                # explicitly asks for added pauses.  Injecting a fixed silence
                # after every sentence made the Korean narration noticeably
                # staccato and added tens of seconds to long-form jobs.
                if (
                    runtime_config.value("tts_sentence_pause_ms") > 0
                    or runtime_config.value("tts_paragraph_pause_ms") > 0
                ):
                    timed_characters = self._insert_sentence_pauses(
                        destination, timed_characters, job_id, offset,
                        pause_ms_override=(
                            int(runtime_config.value("tts_thought_group_pause_ms"))
                            if thought_group_delivery else None
                        ),
                    )
                return True, timed_characters
            except (ValueError, TypeError, KeyError, base64.binascii.Error) as exc:
                logger.warning("ElevenLabs with-timestamps response parse failed: %s", exc)
                return False, []
        
        def _build_payload(chunk_text: str, prev_text: str = "", next_text_val: str = "", is_intro: bool = False) -> dict:
            model_id = runtime_config.value("tts_model_intro" if is_intro else "tts_model_body")
            is_v3 = model_id.startswith("eleven_v3")
            stability = stability_override if stability_override is not None else runtime_config.value("tts_stability_intro" if is_intro else "tts_stability_body")
            # v3 stability는 0.0 / 0.5 / 1.0만 허용. 범위 이탈 시 1.0으로 보정 (에러 대신 경고)
            if is_v3 and stability not in {0.0, 0.5, 1.0}:
                logger.warning("Eleven v3 stability %s is invalid; clamping to 1.0", stability)
                stability = 1.0
            mode = self._stability_mode(model_id, stability)
            tts_text = self._prepare_elevenlabs_text(chunk_text, model_id, mode)
            if tts_text.count("?") != chunk_text.count("?"):
                logger.warning("provider copy lost %d Korean question mark(s); proceeding anyway",
                               chunk_text.count("?") - tts_text.count("?"))
            has_tag = bool(re.search(r"\[[^\[\]]{1,40}\]", tts_text))
            if mode != "natural" and has_tag:
                logger.warning("ElevenLabs audio tag survived sanitization in mode=%s; stripping", mode)
                tts_text = re.sub(r"\[[^\[\]]{1,40}\]\s*", "", tts_text).lstrip()
            first_sentence = re.split(r"(?<=[.!?。！？])\s+", tts_text, maxsplit=1)[0][:160]
            if is_intro or not self._last_provider_request:
                self._last_provider_request = {
                    "model_id": model_id,
                    "mode": mode,
                    "has_audio_tag": has_tag,
                    "first_sentence": first_sentence,
                    "first_30_chars": tts_text[:30],
                    "pause_boundary_policy": "next_spoken_character",
                    "cadence_policy": "native_question_intonation_with_micro_breaths",
                }
            logger.info(
                "ElevenLabs transmission model=%s mode=%s has_tag=%s first30=%r",
                model_id, mode, has_tag, tts_text[:30],
            )
            voice_settings = {
                "stability": stability,
                "similarity_boost": runtime_config.value("elevenlabs_similarity_boost"),
                "speed": native_speed,
            }
            # Speaker boost is unsupported by v3.  Omitting it prevents a
            # model switch from failing an otherwise valid narration request.
            if not is_v3:
                voice_settings["use_speaker_boost"] = True
            payload = {
                "text": tts_text,
                "model_id": model_id,
                "language_code": "ko",
                "voice_settings": voice_settings,
                # [공식 가이드] Root level에도 normalization 끄기 설정 명시
                "apply_text_normalization": "off"
            }
            if seed is not None:
                payload["seed"] = seed
            # eleven_v3 rejects previous_text/next_text with a 400 response.
            # A single rejected chunk used to downgrade the entire video to gTTS.
            supports_context = not is_v3
            if prev_text and supports_context:
                payload["previous_text"] = prev_text
            if next_text_val and supports_context:
                payload["next_text"] = next_text_val
            if pron_locators:
                payload["pronunciation_dictionary_locators"] = pron_locators
            return payload
        
        def _post_with_backoff(req_url: str, req_payload: dict, req_headers: dict) -> Optional[requests.Response]:
            max_retries = 5
            # 긴 스크립트(2000자 이상)는 ElevenLabs 렌더링에 최대 1~2분 소요될 수 있음
            deadline = time.time() + 240
            for attempt in range(max_retries):
                try:
                    time_left = deadline - time.time()
                    if time_left <= 0:
                        logger.warning("ElevenLabs request overall deadline exceeded")
                        break
                    # timeout 숫자 하나는 연결과 응답 읽기에 같은 60초를 적용한다.
                    # 긴 한국어 대본은 서버가 정상 렌더링 중이어도 60초를 넘길 수 있어
                    # 동일한 유료 합성을 반복 요청하게 된다. 연결 제한은 짧게 유지하고,
                    # 읽기는 전체 240초 마감 안에서 한 요청이 끝날 시간을 보장한다.
                    req_timeout = self._elevenlabs_http_timeout(time_left)
                    resp = requests.post(req_url, json=req_payload, headers=req_headers, timeout=req_timeout)
                    if resp.status_code == 200:
                        return resp
                    elif resp.status_code in {429, 500, 502, 503, 504}:
                        wait = min(2 ** attempt, max(1, deadline - time.time()))
                        if deadline - time.time() <= wait:
                            break
                        logger.warning(f"ElevenLabs retryable error {resp.status_code}, attempt {attempt+1}/{max_retries}. Waiting {wait}s.")
                        time.sleep(wait)
                        continue
                    else:
                        logger.warning(f"ElevenLabs non-retryable error {resp.status_code}: {resp.text}")
                        return resp
                except requests.RequestException as e:
                    wait = min(2 ** attempt, max(1, deadline - time.time()))
                    if deadline - time.time() <= wait:
                        break
                    logger.warning(f"ElevenLabs request exception {e}, attempt {attempt+1}/{max_retries}. Waiting {wait}s.")
                    time.sleep(wait)
            return None

        # Eleven v3 accepts substantially longer input than the legacy model.
        # A five-minute script should be one continuous performance so its
        # prosody matches the reference voice instead of restarting every 800
        # characters.  Keep the legacy ceiling for non-v3 fallbacks.
        MAX_CHARS = 8000 if runtime_config.value("tts_model_body").startswith("eleven_v3") else 800
        if len(text) <= MAX_CHARS:
            payload = _build_payload(text, is_intro=True)
            logger.info(f"ElevenLabs API 요청 (단일): URL={url}, model_id={payload.get('model_id')}, text_len={len(payload.get('text', ''))}")
            resp = _post_with_backoff(url, payload, headers)
            if resp and resp.status_code == 200:
                saved, timed_chars = _write_timed_response(resp, output_path)
                if saved:
                    logger.info(f"ElevenLabs v3 + 발음 사전 음성 생성 성공 (단일 요청)")
                    return True, timed_chars
            
            logger.warning("ElevenLabs 단일 요청 실패 -> caller fallback")
            return False, []
        else:
            # 800자 단위 분할 (문장 경계 기준)
            parts = []
            current = ""
            for sent in split_sentences(text):
                if len(current) + len(sent) <= MAX_CHARS:
                    current = (current + " " + sent).strip()
                else:
                    if current:
                        parts.append(current)
                    current = sent
            if current:
                parts.append(current)

            # v3 is less reliable with very short prompts. Keep a short tail
            # with the preceding paragraph whenever the 800-char ceiling allows.
            if len(parts) > 1 and len(parts[-1]) < 250 and len(parts[-2]) + len(parts[-1]) + 1 <= MAX_CHARS:
                parts[-2] = f"{parts[-2]} {parts[-1]}"
                parts.pop()
                
            tmp_files = []
            combined_timed_chars: List[Dict] = []
            elevenlabs_success_count = 0
            timeline_offset = 0.0
            for idx, part in enumerate(parts):
                if is_job_stopped(job_id):
                    raise RuntimeError(f"Job {job_id} stopped by user.")
                tmp = tf.mktemp(suffix=f"_el_{idx}.mp3")
                
                # 이전 청크와 다음 청크를 힌트로 넘겨 억양 단절 보정
                prev_p = parts[idx - 1] if idx > 0 else ""
                next_p = parts[idx + 1] if idx + 1 < len(parts) else ""
                
                payload = _build_payload(part, prev_text=prev_p, next_text_val=next_p, is_intro=(idx == 0))
                chunk_success = False
                
                logger.info(f"ElevenLabs API 요청: URL={url}, model_id={payload.get('model_id')}, text_len={len(payload.get('text', ''))}")
                resp = _post_with_backoff(url, payload, headers)
                if resp and resp.status_code == 200:
                    saved, timed_chars = _write_timed_response(resp, tmp, offset=timeline_offset)
                    if saved:
                        tmp_files.append(tmp)
                        combined_timed_chars.extend(timed_chars)
                        local_duration = self._probe_duration(tmp)
                        if local_duration is None:
                            local_duration = (timed_chars[-1]["end"] - timeline_offset) if timed_chars else 0.0
                        timeline_offset += local_duration
                        logger.info(f"ElevenLabs 분할 {idx+1}/{len(parts)} 성공")
                        chunk_success = True
                        elevenlabs_success_count += 1
                
                # 청크 레벨 폴백: ElevenLabs 실패 시 gTTS로 해당 청크 대체
                if not chunk_success:
                    # Never combine an ElevenLabs narration with a fallback
                    # voice: a single failed chunk should not change the
                    # narrator half-way through a finished video.
                    for temp_file in tmp_files:
                        if os.path.exists(temp_file):
                            os.remove(temp_file)
                    return False, []
                    logger.warning(f"ElevenLabs 분할 {idx+1} 최종 실패 -> gTTS 폴백 적용")
                    try:
                        from gtts import gTTS
                        tts = gTTS(text=part, lang='ko', slow=False)
                        tts.save(tmp)
                        tmp_files.append(tmp)
                        logger.info(f"gTTS 분할 {idx+1}/{len(parts)} 성공 (폴백)")
                    except Exception as ge:
                        logger.error(f"gTTS 분할 {idx+1} 폴백 실패: {ge} -> 무음 청크 생성")
                        estimated = len(part) / 5.0
                        self._run_subprocess(
                            f'ffmpeg -f lavfi -i "anullsrc=r=44100:cl=stereo" '
                            f'-t {estimated:.3f} -c:a libmp3lame -b:a 128k '
                            f'-y "{tmp}" -loglevel error',
                            job_id
                        )
                        tmp_files.append(tmp)
            
            if elevenlabs_success_count == 0:
                logger.warning("ElevenLabs 모든 분할 청크 요청 실패 -> gTTS 전체 폴백 시도")
                for t in tmp_files:
                    if os.path.exists(t): os.remove(t)
                return False, []
                    
            for t in tmp_files:
                self._sanitize_audio_chunk(t, job_id)
            
            list_file = tf.mktemp(suffix=".txt")
            with open(list_file, "w", encoding="utf-8") as f:
                for t in tmp_files:
                    f.write(f"file '{t}'\n")
            
            self._run_subprocess(
                f'ffmpeg -f concat -safe 0 -i "{list_file}" '
                f'-c:a copy -y "{output_path}" -loglevel error',
                job_id
            )
            for t in tmp_files:
                if os.path.exists(t): os.remove(t)
            if os.path.exists(list_file): os.remove(list_file)
            
            logger.info(f"음성 생성 및 병합 완료 ({len(parts)}개 조각, 하이브리드 모드)")
            return os.path.exists(output_path), combined_timed_chars

    # ============================
    # ElevenLabs Forced Alignment → 정밀 자막 타이밍
    # ============================
    def _strip_audio_tag_timings(self, characters: List[Dict]) -> List[Dict]:
        """Remove v3 control tags from timing without touching spoken text.

        ElevenLabs returns alignment relative to the exact input.  A tag such
        as ``[curious] `` is not subtitle text and would otherwise shift every
        following character.  This keeps canonical subtitle text and timing in
        the same coordinate system.
        """
        filtered: List[Dict] = []
        index = 0
        while index < len(characters):
            if characters[index].get("text") == "[":
                close = index + 1
                while close < len(characters) and close - index <= 40 and characters[close].get("text") != "]":
                    close += 1
                if close < len(characters) and characters[close].get("text") == "]":
                    index = close + 1
                    if index < len(characters) and str(characters[index].get("text", "")).isspace():
                        index += 1
                    continue
            filtered.append(characters[index])
            index += 1
        return filtered

    @staticmethod
    def _char_error_rate(reference: str, hypothesis: str) -> float:
        """Dependency-free CER, normalized for Korean narration comparison."""
        def normalize(value: str) -> str:
            # v3 audio tags (for example, [calm]) guide delivery but are not
            # spoken words.  They must not cause a false CER retry.
            value = re.sub(r"\[[A-Za-z][A-Za-z _-]{0,40}\]", "", value)
            return re.sub(r"[\s\W_]+", "", value).lower()
        a, b = normalize(reference), normalize(hypothesis)
        if not a:
            return 0.0 if not b else 1.0
        previous = list(range(len(b) + 1))
        for i, char_a in enumerate(a, 1):
            current = [i]
            for j, char_b in enumerate(b, 1):
                current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
            previous = current
        return previous[-1] / len(a)

    def _verify_tts_narration(
        self,
        mp3_path: str,
        reference_text: str,
        attempt: int,
        provider_characters: List[Dict] | None = None,
    ) -> Dict:
        """공급자 문자 정렬을 원문 하드 게이트로, Whisper를 청취 검수로 분리한다."""
        try:
            provider_text = "".join(
                str(item.get("text") or "")
                for item in self._strip_audio_tag_timings(provider_characters or [])
            )
            provider_cer = self._char_error_rate(
                self._preprocess_for_tts(reference_text),
                self._preprocess_for_tts(provider_text),
            ) if provider_text else None
            provider_unit_match = (
                self._financial_unit_sequence(reference_text) == self._financial_unit_sequence(provider_text)
                if provider_text else False
            )
            provider_text_exact = provider_cer == 0.0 and provider_unit_match

            model = self._get_whisper_model()
            segments, _ = model.transcribe(mp3_path, language="ko", beam_size=1, best_of=1, temperature=0)
            transcript = " ".join(segment.text.strip() for segment in segments).strip()
            # Whisper frequently writes a spoken Korean number back as digits
            # (e.g. "삼점이퍼센트" -> "3.2%"). Compare both sides after the
            # same financial reading normalization to avoid false retries.
            cer = self._char_error_rate(
                self._preprocess_for_tts(reference_text),
                self._preprocess_for_tts(transcript),
            )
            reference_units = self._financial_unit_sequence(reference_text)
            transcript_units = self._financial_unit_sequence(transcript)
            unit_sequence_match = reference_units == transcript_units
            whisper_passed = (
                bool(transcript)
                and cer <= runtime_config.value("tts_cer_threshold")
                and unit_sequence_match
            )
            # ElevenLabs의 문자별 타임스탬프는 실제 생성 요청과 한 쌍으로
            # 반환되는 정렬 근거다. 화면 자막의 원문 동일성은 이 근거로 판정하고,
            # 숫자·약어를 자주 달리 적는 로컬 STT는 청취 검수 경고로만 남긴다.
            passed = provider_text_exact or whisper_passed
            logger.info(
                "TTS validation attempt=%s provider_exact=%s cer=%.4f unit_match=%s passed=%s",
                attempt, provider_text_exact, cer, unit_sequence_match, passed,
            )
            return {
                "passed": passed,
                "cer": round(cer, 4),
                "attempts": attempt,
                "transcript": transcript[:500],
                "unit_sequence_match": unit_sequence_match,
                "reference_units": reference_units,
                "transcript_units": transcript_units,
                "provider_text_exact": provider_text_exact,
                "provider_cer": round(provider_cer, 4) if provider_cer is not None else None,
                "provider_unit_sequence_match": provider_unit_match,
                "whisper_passed": whisper_passed,
                "audio_review_required": not whisper_passed,
            }
        except Exception as exc:
            logger.warning("TTS CER validation unavailable on attempt %s: %s", attempt, exc)
            return {"passed": False, "cer": None, "attempts": attempt, "error": str(exc)}

    def _financial_unit_sequence(self, text: str) -> List[str]:
        """숫자 표기 차이는 흡수하되 금융 단위의 의미 변경은 허용하지 않는다."""
        normalized = self._preprocess_for_tts(text or "")
        return re.findall(r"퍼센트|포인트|달러|유로|엔|조|억|만원|원", normalized)

    @staticmethod
    def _elevenlabs_http_timeout(time_left: float) -> Tuple[float, float]:
        """긴 합성은 한 번만 기다리되 연결 장애는 빠르게 판별한다."""
        remaining = max(20.0, min(240.0, float(time_left)))
        connect_timeout = min(10.0, remaining / 2.0)
        read_timeout = max(10.0, remaining - connect_timeout)
        return connect_timeout, read_timeout

    @staticmethod
    def _sentence_pause_points(
        characters: List[Dict], pause_ms_override: int | None = None
    ) -> List[Tuple[float, float]]:
        """Find native-timing sentence boundaries and their required pauses.

        The input is the character-level alignment returned by ElevenLabs.  A
        pause follows only a terminal mark with more spoken text after it; a
        period inside a decimal or a run of ``...`` therefore cannot create a
        series of artificial gaps.
        """
        terminal_marks = {".", "!", "?", "…"}
        pauses: List[Tuple[float, float]] = []
        for index, item in enumerate(characters):
            if str(item.get("text", "")) not in terminal_marks:
                continue
            if index + 1 < len(characters) and str(characters[index + 1].get("text", "")) in terminal_marks:
                continue

            next_index = index + 1
            whitespace = ""
            while next_index < len(characters) and str(characters[next_index].get("text", "")).isspace():
                whitespace += str(characters[next_index].get("text", ""))
                next_index += 1
            if next_index >= len(characters):
                continue

            # Do not splice at the punctuation timestamp. ElevenLabs' visual
            # alignment can mark the period before the Korean final syllable's
            # acoustic release has finished. Inserting silence there detaches
            # endings such as "였어요/밀렸습니다/겁니다" from their tail and makes
            # them sound cut off. Preserve the provider's complete inter-
            # sentence region and insert the extra breath immediately before
            # the next spoken character instead.
            next_start = float(characters[next_index]["start"])
            terminal_end = float(item["end"])
            if not whitespace and next_start <= terminal_end + 0.02:
                continue

            pause_ms = (
                pause_ms_override
                if pause_ms_override is not None
                else (
                    runtime_config.value("tts_paragraph_pause_ms")
                    if "\n\n" in whitespace
                    else runtime_config.value("tts_sentence_pause_ms")
                )
            )
            pause_seconds = max(0.0, float(pause_ms) / 1000.0)
            if not pause_seconds:
                continue

            native_pause = max(0.0, next_start - terminal_end)
            # ElevenLabs 네이티브 음성에 이미 충분한 호흡(pause_seconds 이상)이 포함되어 있으면
            # 인공 무음을 추가 연결하지 않고 자연스러운 연속 음성을 유지한다.
            if native_pause >= pause_seconds and pause_ms_override is not None:
                continue

            pauses.append((max(terminal_end, next_start), pause_seconds))
        return pauses

    @staticmethod
    def _shift_character_timings_for_pauses(characters: List[Dict], pauses: List[Tuple[float, float]]) -> List[Dict]:
        """Shift native timing by exactly the silence inserted into the MP3."""
        shifted: List[Dict] = []
        ordered_pauses = sorted(pauses, key=lambda item: item[0])
        for item in characters:
            start = float(item["start"])
            offset = sum(duration for boundary, duration in ordered_pauses if boundary <= start + 1e-6)
            shifted.append({**item, "start": start + offset, "end": float(item["end"]) + offset})
        return shifted

    def _insert_sentence_pauses(
        self, audio_path: str, characters: List[Dict], job_id: int,
        timeline_offset: float = 0.0, pause_ms_override: int | None = None,
    ) -> List[Dict]:
        """Insert short silent breaths without losing ElevenLabs timestamp sync.

        ElevenLabs still generates the narration as one continuous performance.
        We splice audio at its own character timings, place the silence after
        each completed sentence, and apply the same time shift to the native
        alignment.  If FFmpeg cannot complete the splice, the untouched audio
        and original alignment are retained.
        """
        pauses = self._sentence_pause_points(characters, pause_ms_override)
        if not pauses or not os.path.exists(audio_path):
            return characters

        source_duration = self._probe_duration(audio_path)
        if not source_duration or source_duration <= 0:
            return characters

        local_pauses: List[Tuple[float, float, float]] = []
        previous_end = 0.0
        for absolute_boundary, pause_seconds in pauses:
            local_boundary = min(source_duration, max(0.0, absolute_boundary - timeline_offset))
            if local_boundary <= previous_end + 0.02 or local_boundary >= source_duration - 0.02:
                continue
            local_pauses.append((local_boundary, pause_seconds, absolute_boundary))
            previous_end = local_boundary
        if not local_pauses:
            return characters

        filters: List[str] = []
        inputs: List[str] = []
        cursor = 0.0
        label_index = 0
        for boundary, pause_seconds, _ in local_pauses:
            segment_label = f"s{label_index}"
            seg_duration = max(0.01, boundary - cursor)
            fade_out = f",afade=t=out:st={max(0.0, seg_duration - 0.03):.6f}:d=0.03" if seg_duration > 0.05 else ""
            fade_in = ",afade=t=in:st=0:d=0.01" if cursor > 0 and seg_duration > 0.02 else ""
            filters.append(
                f"[0:a]atrim=start={cursor:.6f}:end={boundary:.6f},asetpts=PTS-STARTPTS,highpass=f=40{fade_in}{fade_out},"
                f"aformat=sample_rates=44100:channel_layouts=stereo[{segment_label}]"
            )
            inputs.append(f"[{segment_label}]")
            pause_label = f"p{label_index}"
            filters.append(
                f"anullsrc=r=44100:cl=stereo,atrim=duration={pause_seconds:.6f},asetpts=PTS-STARTPTS[{pause_label}]"
            )
            inputs.append(f"[{pause_label}]")
            cursor = boundary
            label_index += 1

        final_label = f"s{label_index}"
        final_duration = max(0.01, source_duration - cursor)
        final_fade_in = ",afade=t=in:st=0:d=0.01" if cursor > 0 and final_duration > 0.02 else ""
        filters.append(
            f"[0:a]atrim=start={cursor:.6f}:end={source_duration:.6f},asetpts=PTS-STARTPTS,highpass=f=40{final_fade_in},"
            f"aformat=sample_rates=44100:channel_layouts=stereo[{final_label}]"
        )
        inputs.append(f"[{final_label}]")
        filters.append(f"{''.join(inputs)}concat=n={len(inputs)}:v=0:a=1[outa]")

        paused_path = f"{audio_path}.paused.mp3"
        command = (
            f'ffmpeg -i "{audio_path}" -filter_complex "{";".join(filters)}" '
            f'-map "[outa]" -c:a libmp3lame -b:a 128k -y "{paused_path}" -loglevel error'
        )
        ret = self._run_subprocess(command, job_id)
        if ret != 0 or not os.path.exists(paused_path):
            logger.warning("Sentence pause insertion failed; retaining native ElevenLabs timing")
            if os.path.exists(paused_path):
                os.remove(paused_path)
            return characters

        os.replace(paused_path, audio_path)
        logger.info("Inserted %s narration sentence pauses", len(local_pauses))
        return self._shift_character_timings_for_pauses(
            characters,
            [(absolute_boundary, pause_seconds) for _, pause_seconds, absolute_boundary in local_pauses],
        )

    def _postprocess_audio(self, mp3_path: str, job_id: int) -> None:
        """Apply duration-preserving narration mastering before video assembly."""
        if not os.path.exists(mp3_path):
            return
        temp_path = f"{mp3_path}.mastered.mp3"
        filters = "highpass=f=80,acompressor=threshold=-18dB:ratio=3:attack=10:release=200,loudnorm=I=-14:TP=-1.5:LRA=11"
        ret = self._run_subprocess(
            f'ffmpeg -i "{mp3_path}" -af "{filters}" -c:a libmp3lame -b:a 128k -y "{temp_path}" -loglevel error',
            job_id,
        )
        if ret == 0 and os.path.exists(temp_path):
            os.replace(temp_path, mp3_path)
            logger.info("TTS post-processing complete: high-pass, compression, loudness normalization")
        elif os.path.exists(temp_path):
            os.remove(temp_path)

    def _extract_timestamps_from_elevenlabs_response(self, original_script: str,
                                                      characters: List[Dict],
                                                      subtitle_max_chars: int = 22,
                                                      time_scale: float = 1.0) -> List[dict]:
        """Map native ElevenLabs character timings to readable subtitle rows.

        The alignment belongs to the generated audio, unlike an STT estimate.
        ``time_scale`` is only non-1 when FFmpeg intentionally changes the
        final audio speed after synthesis.
        """
        text_chunks = self._split_script_into_chunks(original_script, max_chars=subtitle_max_chars)
        if not text_chunks or not characters:
            return []
        scaled_characters = [
            {
                "text": item.get("text", ""),
                "start": float(item.get("start", 0.0)) * time_scale,
                "end": float(item.get("end", 0.0)) * time_scale,
            }
            for item in characters
        ]
        scaled_characters = self._strip_audio_tag_timings(scaled_characters)
        chunks = self._map_timestamps_by_character_alignment(text_chunks, scaled_characters)
        if chunks:
            logger.info("Using native ElevenLabs character timing for %s subtitle chunks", len(chunks))
        return chunks

    def _extract_timestamps_with_forced_alignment(
        self,
        mp3_path: str,
        original_script: str,
        subtitle_max_chars: int = 22,
        *,
        spoken_alignment_script: str | None = None,
    ) -> list[dict]:
        """
        ElevenLabs Forced Alignment API를 사용하여 단어 단위 정밀 타임스탬프를 추출합니다.

        정렬 API에는 실제 TTS 발음용 문장을 전달한다. 화면에는 원문 표기를
        유지하되, 각 화면 큐는 실제 발음 단어가 끝나는 시점에만 전환한다.
        """
        from app.tts.forced_alignment_srt import AlignmentError, align_audio_to_text_with_characters

        try:
            alignment_script = spoken_alignment_script or self._preprocess_for_tts(original_script)
            words, characters = align_audio_to_text_with_characters(mp3_path, alignment_script)
        except AlignmentError as exc:
            logger.warning("Forced Alignment API 요청 실패: %s", exc)
            self._last_subtitle_alignment_mode = "forced_alignment_error"
            return []

        display_chunks = self._split_script_into_chunks(original_script, max_chars=subtitle_max_chars)
        chunks = self._map_display_chunks_to_spoken_characters(display_chunks, characters)
        if not chunks:
            chunks = self._map_display_chunks_to_spoken_words(display_chunks, words)
            self._last_subtitle_alignment_mode = "word_fallback"
        else:
            self._last_subtitle_alignment_mode = "character"
        logger.info("Forced Alignment 원문 자막 큐 생성: %s개", len(chunks))
        return chunks

    def _map_display_chunks_to_spoken_characters(self, display_chunks: List[str], characters: List) -> List[dict]:
        """화면 원문을 실제 발음 문자 배열에 정확히 대응시킨다."""
        if not display_chunks or not characters:
            return []

        def comparison_key(value: str) -> str:
            return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)

        spoken_characters = [
            item for item in characters if comparison_key(str(item.text))
        ]
        spoken_text = "".join(comparison_key(str(item.text)) for item in spoken_characters)
        cursor = 0
        result: List[dict] = []
        for index, display_text in enumerate(display_chunks, start=1):
            expected = comparison_key(self._preprocess_for_tts(display_text))
            if not expected or not spoken_text.startswith(expected, cursor):
                logger.warning("문자 정렬 텍스트 불일치: 자막 %s", index)
                return []
            start_character = spoken_characters[cursor]
            end_character = spoken_characters[cursor + len(expected) - 1]
            start = float(start_character.start)
            end = float(end_character.end)
            if end <= start:
                return []
            result.append({
                "index": index,
                "text": display_text,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            })
            cursor += len(expected)

        if cursor != len(spoken_text):
            logger.warning("문자 정렬 잔여 발음 텍스트가 있어 단어 정렬로 폴백합니다.")
            return []
        return result

    def _map_display_chunks_to_spoken_words(self, display_chunks: List[str], words: List) -> List[dict]:
        """표시용 원문을 실제 발음 단어 경계에 맞춰 시간화한다.

        숫자 표기 ``3.75``와 실제 발음 ``삼쩜칠오``는 길이가 다르다. 비례식으로
        시간을 배분하면 화면 자막이 발음 중간에 넘어갈 수 있으므로, 발음용으로
        정규화한 각 표시 큐의 글자량을 실제 정렬 단어에서 순서대로 소비한다.
        마지막 단어 전체를 포함하므로 다음 자막이 앞선 발음보다 빨리 오지 않는다.
        """
        if not display_chunks or not words:
            return []

        def comparison_key(value: str) -> str:
            return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)

        result: List[dict] = []
        word_index = 0
        for index, display_text in enumerate(display_chunks, start=1):
            target_length = len(comparison_key(self._preprocess_for_tts(display_text)))
            if target_length <= 0:
                continue
            first_word_index = word_index
            consumed_length = 0
            while word_index < len(words) and consumed_length < target_length:
                consumed_length += len(comparison_key(str(words[word_index].text)))
                word_index += 1
            if word_index == first_word_index:
                return []

            start = float(words[first_word_index].start)
            end = float(words[word_index - 1].end)
            if end <= start:
                return []
            result.append({
                "index": index,
                "text": display_text,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            })

        # 정렬 응답이 문장부호를 별도 단어로 돌려준 경우에도 마지막 자막이
        # 실제 음성보다 먼저 사라지지 않도록 남은 단어의 끝을 반영한다.
        if result and word_index < len(words):
            last_end = float(words[-1].end)
            if last_end > result[-1]["end"]:
                result[-1]["end"] = round(last_end, 3)
                result[-1]["duration"] = round(result[-1]["end"] - result[-1]["start"], 3)
        return result

    @staticmethod
    def _snap_subtitle_timings_to_render_frames(
        chunks: List[dict],
        frame_rate: float = 30.0,
        start_frame_policy: str = "nearest",
    ) -> List[dict]:
        """자막이 음성보다 먼저 그려지지 않도록 출력 프레임에 맞춘다.

        강제 정렬 API의 시간은 연속적인 초 단위지만 영상은 이산 프레임으로
        출력된다. SRT/ASS의 시작 시간을 그대로 두면 렌더러가 이전 프레임에서
        이벤트를 활성화할 수 있다. 시작은 문자 경계에 가장 가까운 프레임으로
        맞추고, 끝은 다음 프레임으로 올림해 음성 꼬리가 먼저 사라지지 않게 한다.
        """
        if not chunks:
            return []
        try:
            rate = float(frame_rate)
        except (TypeError, ValueError) as exc:
            raise ValueError("subtitle_frame_rate must be a positive number") from exc
        if rate <= 0:
            raise ValueError("subtitle_frame_rate must be a positive number")
        policy = str(start_frame_policy or "nearest").lower()
        if policy not in {"nearest", "ceil"}:
            raise ValueError("subtitle_start_frame_policy must be 'nearest' or 'ceil'")

        frame = 1.0 / rate

        def ceil_to_frame(value: float) -> float:
            # 부동소수점 0.30000000000000004 때문에 이미 정확한 프레임을
            # 불필요하게 한 프레임 더 늦추지 않도록 작은 허용 오차를 둔다.
            return math.ceil((value / frame) - 1e-9) * frame

        def nearest_frame(value: float) -> float:
            return math.floor((value / frame) + 0.5) * frame

        snapped: List[dict] = []
        previous_end = 0.0
        for source in chunks:
            raw_start = max(0.0, float(source.get("start", 0.0) or 0.0))
            raw_end = max(raw_start, float(source.get("end", 0.0) or 0.0))
            rounded_start = nearest_frame(raw_start) if policy == "nearest" else ceil_to_frame(raw_start)
            start = max(rounded_start, previous_end)
            end = max(ceil_to_frame(raw_end), start + frame)
            item = dict(source)
            item["start"] = round(start, 6)
            item["end"] = round(end, 6)
            item["duration"] = round(end - start, 6)
            snapped.append(item)
            previous_end = end
        return snapped

    def _assess_splice_artifacts(self, audio_path: str, job_id: int) -> Dict:
        """최종 오디오의 디지털 무음 접합부를 경고용으로 검사한다."""
        from app.tts.splice_artifact_detector import analyze_audio

        try:
            report = analyze_audio(audio_path, work_dir=os.path.dirname(audio_path))
        except Exception as exc:
            logger.warning("TTS 접합부 QA를 실행하지 못했습니다: %s", exc)
            return {"passed": None, "warning": "splice_analysis_failed"}
        if report["suspect_count"]:
            logger.warning(
                "TTS 접합부 QA 경고: 의심 구간 %s개 (job_id=%s)",
                report["suspect_count"], job_id,
            )
        return report

    def _map_timestamps_by_character_alignment(self, text_chunks: List[str], characters: List[Dict]) -> List[dict]:
        """Map subtitle chunks to exact Forced Alignment character timings.

        Word-length ratios drift at Korean spacing, punctuation, and spoken
        numbers. The API returns timestamps for each character of the same
        normalized text used by TTS, so use those boundaries directly.
        """
        timed = [
            item for item in characters
            if str(item.get("text", "")).strip()
            and item.get("start") is not None and item.get("end") is not None
        ]
        expected_lengths = [
            len(self._preprocess_for_tts(chunk).replace(" ", ""))
            for chunk in text_chunks
        ]
        expected_total = sum(expected_lengths)
        if not timed or not expected_total:
            return []
        if abs(len(timed) - expected_total) > max(12, int(expected_total * 0.08)):
            logger.warning(
                "Forced Alignment character count mismatch: aligned=%s expected=%s",
                len(timed), expected_total,
            )
            return []

        chunks: List[dict] = []
        cursor = 0
        previous_end = 0.0
        for index, (text, char_count) in enumerate(zip(text_chunks, expected_lengths)):
            if char_count <= 0:
                continue
            end_cursor = len(timed) if index == len(text_chunks) - 1 else min(cursor + char_count, len(timed))
            if end_cursor <= cursor:
                return []
            start = max(previous_end, float(timed[cursor]["start"]))
            end = max(start + 0.05, float(timed[end_cursor - 1]["end"]))
            chunks.append({
                "index": index + 1,
                "text": text,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
            })
            cursor = end_cursor
            previous_end = end
        return chunks

    # ============================
    # 공용 타임스탬프 매핑 (핵심 버그 수정 지점 — FA/stable-ts/whisper 공통 사용)
    # ============================
    def _map_timestamps_by_preprocessed_length(self, text_chunks: List[str],
                                                engine_words: List[Dict]) -> List[dict]:
        """
        text_chunks: 자막에 표시할 원본(가독형) 텍스트 청크 목록
        engine_words: [{"word":str, "start":float, "end":float}, ...]

        기존 버그: 원본 텍스트의 글자 수 비율로 시간을 나누면, 숫자나 영문
        약어처럼 "표기는 짧지만 실제 발음은 긴" 구간에서 타이밍이 어긋납니다.
        (예: "FOMC"는 4글자지만 "에프오엠씨"로 5음절 발음됨)

        수정: 각 청크를 개별적으로 _preprocess_for_tts()에 통과시킨 후의 글자
        수를 비율 계산 기준으로 사용합니다. 전처리 후 글자 수는 실제 발음
        시간과 훨씬 비례합니다.
        """
        preprocessed_lengths = [
            max(len(self._preprocess_for_tts(c).replace(" ", "")), 1)
            for c in text_chunks
        ]
        total_len = max(sum(preprocessed_lengths), 1)
        total_engine_chars = max(sum(len(w["word"].replace(" ", "")) for w in engine_words), 1)
        num_words = len(engine_words)

        chunks = []
        cum_len = 0
        w_idx = 0
        cum_engine_chars = 0
        prev_end = 0.0

        for idx, chunk_text in enumerate(text_chunks):
            cum_len += preprocessed_lengths[idx]
            target_ratio = cum_len / total_len
            target_engine_chars = target_ratio * total_engine_chars

            start_w_idx = w_idx
            while w_idx < num_words and cum_engine_chars < target_engine_chars:
                cum_engine_chars += len(engine_words[w_idx]["word"].replace(" ", ""))
                w_idx += 1

            if w_idx > start_w_idx:
                chunk_start = engine_words[start_w_idx]["start"]
                chunk_end = engine_words[w_idx - 1]["end"]
            else:
                chunk_start = prev_end
                chunk_end = prev_end + 0.5

            if chunk_start < prev_end:
                chunk_start = prev_end
            if chunk_end <= chunk_start:
                chunk_end = chunk_start + 0.5

            if idx == len(text_chunks) - 1 and num_words > 0:
                chunk_end = max(chunk_end, engine_words[-1]["end"])

            duration = round(chunk_end - chunk_start, 3)
            chunks.append({
                "index": idx + 1,
                "text": chunk_text,
                "start": round(chunk_start, 3),
                "end": round(chunk_start + duration, 3),
                "duration": duration,
            })
            prev_end = chunk_start + duration

        return chunks

    # ============================
    # 자막 청크 분할 (단어 잘림 방지 + 마크다운 제거)
    # ============================
    @staticmethod
    def _split_script_into_chunks(script: str, max_chars: int = 22, min_chars: int = 8) -> list[str]:
        """공유 자막 분할기를 사용해 SCRIPT 예상 청크와 동일하게 만든다."""
        return split_script_into_caption_chunks(script, max_chars=max_chars, min_chars=min_chars)

    # ============================
    # stable-ts (cross-attention 기반) → 정밀 자막 싱크
    # ============================
    def _extract_timestamps_with_stable_ts(self, mp3_path: str, original_script: str,
                                             subtitle_max_chars: int = 22) -> list[dict]:
        """
        stable-ts 라이브러리 (stable_whisper)를 사용하여 cross-attention 기반
        단어 단위 정밀 타임스탬프를 추출합니다.

        faster-whisper 역방향 STT보다 더 정확한 이유:
        - DTW(Dynamic Time Warping) + cross-attention 가중치로 단어 경계 감지
        - vad=True로 무음 구간 스킵 → 타임스탬프 드리프트 방지
        - 한국어 구절 단위 자동 재결합 (regroup=True)
        """
        try:
            import stable_whisper
        except ImportError:
            raise ImportError("stable-ts가 설치되지 않았습니다. pip install 'stable-ts[fw]'")

        logger.info("stable-ts (cross-attention) 타임스탬프 추출 시작...")
        model = stable_whisper.load_faster_whisper(
            "base",
            device="cpu",
            compute_type="int8"
        )
        result = model.transcribe_stable(
            mp3_path,
            language="ko",
            vad=True,       # 무음 구간 자동 스킵 → 드리프트 방지
            regroup=True,   # 자연스러운 한국어 구절 단위 재결합
        )

        # 단어 단위 타임스탬프 수집
        stable_words = []
        for segment in result.segments:
            if hasattr(segment, "words") and segment.words:
                for word in segment.words:
                    stable_words.append({
                        "word": word.word.strip(),
                        "start": round(word.start, 3),
                        "end": round(word.end, 3),
                    })

        if not stable_words:
            logger.warning("stable-ts 단어 추출 결과 없음")
            return []

        logger.info(f"stable-ts 단어 {len(stable_words)}개 추출 완료")

        text_chunks = self._split_script_into_chunks(original_script, max_chars=subtitle_max_chars)
        if not text_chunks:
            return []

        # [버그 수정] 기존에는 원본 글자수 비례로 매핑해서 숫자/약어 구간에서
        # 드리프트가 발생했습니다. 공용 헬퍼(발음 전처리 길이 기준)로 교체.
        chunks = self._map_timestamps_by_preprocessed_length(text_chunks, stable_words)
        logger.info(f"stable-ts 정밀 매핑 완료 (발음전처리 길이 기준): {len(text_chunks)}개 청크")
        return chunks

    # ============================
    # faster-whisper 역방향 STT → 원본 텍스트 매핑
    # ============================

    def _extract_timestamps_with_whisper(self, mp3_path: str, original_script: str,
                                           subtitle_max_chars: int = 22) -> list[dict]:
        """
        핵심: gTTS/ElevenLabs로 생성된 MP3를 faster-whisper로 분석하여 시간 곡선을 구한 뒤,
        사용자가 작성한 원본 스크립트(100% 일치 텍스트)에 타임스탬프를 정밀 매핑.
        발음 기반 STT 오타/문법 왜곡을 원천 차단함.
        """
        model = self._get_whisper_model()
        segments, info = model.transcribe(
            mp3_path,
            language="ko",
            word_timestamps=True,
            beam_size=1,
            best_of=1,
            temperature=0,
        )

        whisper_words = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    whisper_words.append({
                        "word": w.word.strip(),
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                    })

        text_chunks = self._split_script_into_chunks(original_script, max_chars=subtitle_max_chars)
        if not text_chunks or not whisper_words:
            return []

        # [버그 수정] 원본 글자수 비례 매핑 → 공용 헬퍼(발음 전처리 길이 기준)로 교체
        chunks = self._map_timestamps_by_preprocessed_length(text_chunks, whisper_words)
        logger.info(f"Whisper 정밀 매핑 완료 (발음전처리 길이 기준): 원본 {len(text_chunks)}개 청크에 타임스탬프 부여")
        return chunks

    # ============================
    # 폴백 타이밍 (글자 수 비례)
    # ============================
    def _fallback_timing(self, script: str, total_duration: float,
                          subtitle_max_chars: int = 22) -> list[dict]:
        """Whisper 실패 시 글자 수 비례로 타이밍 계산 (단어 잘림 없는 원본 텍스트 분할)"""
        text_chunks = self._split_script_into_chunks(script, max_chars=subtitle_max_chars)
        if not text_chunks:
            return []

        # 여기서도 전처리 후 글자수를 기준으로 비율 계산 (숫자/약어 드리프트 방지)
        preprocessed_lengths = [
            max(len(self._preprocess_for_tts(c).replace(" ", "")), 1)
            for c in text_chunks
        ]
        total_chars = max(sum(preprocessed_lengths), 1)
        chunks = []
        cursor = 0.0

        for idx, chunk_text in enumerate(text_chunks):
            char_len = preprocessed_lengths[idx]
            ratio = char_len / total_chars
            duration = round(total_duration * ratio, 3)
            if idx == len(text_chunks) - 1:
                duration = round(max(total_duration - cursor, 0.1), 3)

            chunks.append({
                "index": idx + 1,
                "text": chunk_text,
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
                "duration": duration,
            })
            cursor = round(cursor + duration, 3)

        return chunks

    # ============================
    # 주식 텍스트 전처리
    # ============================
    @staticmethod
    def _preprocess_for_tts(text: str) -> str:
        """주식/경제 용어를 gTTS/ElevenLabs가 자연스럽게 읽도록 전처리"""
        text = re.sub(r'^##\s*.+$', '', text, flags=re.MULTILINE).strip()
        text = re.sub(r'([+-]?\d+\.?\d*)%', r'\1 퍼센트', text)
        text = re.sub(r'\+(\d)', r'플러스 \1', text)
        text = re.sub(r'(?<!\d)-(\d)', r'마이너스 \1', text)
        text = re.sub(r'(\d+)pt\b', r'\1 포인트', text)
        text = re.sub(r'(\d{1,3}),(\d{3})', r'\1\2', text)
        # TTS/STT alignment sees the same expanded text.  The original script is
        # kept separately, so this cannot alter the subtitle text shown to viewers.
        text = normalize_korean_numbers_for_tts(text)

        # 숫자 -> 한글 한글화 함수 (4자리 블록 만/억/조 단위 완벽 지원)
        def num_to_kor(num_str: str) -> str:
            if not num_str:
                return ""
            if num_str == "0":
                return "영"
            if re.match(r'^0+$', num_str):
                return "영" * len(num_str)
            
            # 0으로 시작하는 숫자(예: 010)는 단순 자릿수 단위 없이 한 글자씩 읽음
            if num_str.startswith("0"):
                digit_names = ["영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
                return "".join(digit_names[int(d)] for d in num_str)

            # 한국어 표준 자릿수 한글화 (4자리 단위: 만, 억, 조, 경)
            units_4 = ["", "만", "억", "조", "경"]
            units_1 = ["", "십", "백", "천"]
            digits = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
            
            # 4자리 단위로 크기 맞춤
            num_str = num_str.zfill(((len(num_str) + 3) // 4) * 4)
            chunks = [num_str[i:i+4] for i in range(0, len(num_str), 4)]
            chunks.reverse()
            
            result_parts = []
            for chunk_idx, chunk in enumerate(chunks):
                chunk_val = int(chunk)
                if chunk_val == 0:
                    continue
                
                chunk_str = ""
                for i, digit in enumerate(chunk):
                    d_val = int(digit)
                    if d_val == 0:
                        continue
                    
                    digit_name = digits[d_val]
                    position = 3 - i  # 3: 천, 2: 백, 1: 십, 0: 일
                    # 십, 백, 천 단위 바로 앞의 '일'은 자연스럽게 생략 (예: 일십 -> 십, 일백 -> 백)
                    if d_val == 1 and position > 0:
                        digit_name = ""
                    
                    chunk_str += digit_name + units_1[position]
                
                # '일만'은 한국어 구어체에서 보통 '만'으로 읽음
                if chunk_str == "일" and chunk_idx == 1:
                    chunk_str = ""
                
                result_parts.append(chunk_str + units_4[chunk_idx])
            
            result_parts.reverse()
            res = "".join(result_parts)
            return res if res else "영"

        # 소수점 변환 (소수부 각 자릿수 개별 읽기: e.g. 6.56 -> 육 점 오육, 1.125 -> 일 점 일이오)
        def repl_decimal(match):
            int_part = match.group(1)
            dec_part = match.group(2)
            int_kor = num_to_kor(int_part)
            digit_names = ["영", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]
            dec_kor = "".join(digit_names[int(d)] for d in dec_part)
            # 사용자의 요청에 따라 강한 발음인 '쩜' 대신 자연스러운 '점'을 사용하고, 앞뒤 공백을 주어 자연스러운 호흡 유도
            return f"{int_kor} 점 {dec_kor}"

        text = re.sub(r'(\d+)\.(\d+)', repl_decimal, text)

        def repl_num(match):
            return num_to_kor(match.group(0))

        # 독립된 숫자들을 모두 한글로 변환 (예: 7246 -> 칠천이백사십육)
        text = re.sub(r'\b\d+\b', repl_num, text)

        text = re.sub(r'\bFOMC\b', '에프오엠씨', text)
        text = re.sub(r'\bRSI\b', '알에스아이', text)
        text = re.sub(r'\bMACD\b', '맥디', text)
        text = re.sub(r'\bPER\b', '퍼', text)
        text = re.sub(r'\bPBR\b', '피비알', text)
        text = re.sub(r'\bS&P\b', '에스앤피', text)
        text = re.sub(r'\bETF\b', '이티에프', text)
        text = re.sub(r'\bCPI\b', '소비자물가지수', text)
        text = re.sub(r'\bGDP\b', '국내총생산', text)
        return text

    @staticmethod
    def _probe_duration(path: str) -> float | None:
        result = os.popen(
            f'ffprobe -v error -show_entries format=duration '
            f'-of default=noprint_wrappers=1:nokey=1 "{path}"'
        ).read().strip()
        try:
            return float(result)
        except ValueError:
            return None
