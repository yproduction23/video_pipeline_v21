"""One length contract shared by script generation and ElevenLabs TTS.

The product measures Korean narration in *spoken* characters (spaces and
editorial markup excluded).  Visual prompts, section headings and subtitle
splits must never change the requested video duration.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


CALIBRATION_PATH = Path(os.getenv("TTS_CPS_CALIBRATION_PATH", "/app/data/tts_cps_calibration.json"))
TOLERANCE = 0.15
MAX_SCRIPT_DURATION_TOLERANCE = 0.05
# 실제 음성 길이 측정은 대본 글자수 계약과 다른 문제다. 글자수는 작가(LLM)가
# 재작성으로 정밀하게 맞출 수 있어 5%가 합리적이지만, 실제 발화는 억양·쉼·
# 리듬 때문에 같은 글자수라도 더 크게 흔들린다. TTS_DURATION_TOLERANCE의
# 기본값(0.25)이 바로 그 의도였는데, 두 검사가 같은 5% 캡을 공유하면서
# 실제 음성 검사가 부당하게 좁아졌다(job 12: 60초 목표에서 75~77초가
# 나와도 정상 범위일 수 있는데 5%(≈3초) 캡에 걸려 계속 실패).
MAX_TTS_MEASURED_DURATION_TOLERANCE = 0.30


def effective_duration_tolerance(configured: float) -> float:
    """대본 글자수 계약(target_chars) 허용치를 1~5% 사이로 제한한다."""
    return max(0.01, min(float(configured), MAX_SCRIPT_DURATION_TOLERANCE))


def effective_tts_measured_duration_tolerance(configured: float) -> float:
    """실측 음성 길이 검사 허용치. 설정값(기본 25%)을 그대로 쓰되 1~30% 사이로만 제한한다."""
    return max(0.01, min(float(configured), MAX_TTS_MEASURED_DURATION_TOLERANCE))


def get_tolerance() -> float:
    try:
        from app import runtime_config
        return effective_duration_tolerance(float(runtime_config.value("tts_duration_tolerance")))
    except Exception:
        return MAX_SCRIPT_DURATION_TOLERANCE


def spoken_char_count(text: str) -> int:
    """Count only characters that are actually narrated by the voice."""
    return len(re.sub(r"\s+", "", text or ""))


def _key(voice_id: str | None, model_id: str | None, speed: float) -> str:
    return f"{voice_id or 'default'}|{model_id or 'default'}|{float(speed):.2f}"


def _read_calibrations() -> dict[str, Any]:
    try:
        with CALIBRATION_PATH.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_calibrations(value: dict[str, Any]) -> None:
    try:
        CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=CALIBRATION_PATH.parent) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        temporary.replace(CALIBRATION_PATH)
    except OSError:
        # Calibration improves future estimates, but must never fail a job.
        return


def resolve_cpm(default_cpm: float, voice_id: str | None, model_id: str | None, speed: float) -> tuple[float, int]:
    """같은 음성·모델·속도의 실측 CPM이 한 건이라도 있으면 사용한다.

    ElevenLabs 정렬 응답으로 실제 길이를 확정한 샘플은 다음 영상의 분량을
    맞추는 가장 가까운 기준이다. 두 번째 샘플을 기다리는 동안 기본 CPM으로
    되돌아가면, Job 52처럼 이미 확인한 3분 25초 결과를 다시 5분으로 예측하는
    큰 오차가 반복된다. 관측치는 저장 단계에서 80~900 CPM 범위로 제한된다.
    """
    row = _read_calibrations().get(_key(voice_id, model_id, speed), {})
    samples = int(row.get("samples", 0) or 0)
    measured = float(row.get("cpm", 0) or 0)
    if samples >= 1 and measured > 0:
        return measured, samples
    return float(default_cpm), samples


def make_length_contract(
    target_minutes: int,
    base_cpm: float,
    speed: float,
    voice_id: str | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Calculate an explicit target before prose is generated.

    기본 CPM은 정상 속도 기준이므로 속도를 반영한다. 다만 보정값은 이미
    음성·모델·속도 조합별 실제 결과에서 측정한 값이므로 속도를 다시 곱하지
    않는다. 같은 속도를 두 번 적용하면 장문 대본이 짧아지는 문제가 생긴다.
    """
    safe_minutes = max(1, int(target_minutes or 1))
    safe_speed = max(0.5, min(float(speed or 1.0), 1.5))
    calibrated_cpm, samples = resolve_cpm(base_cpm, voice_id, model_id, safe_speed)
    effective_cpm = calibrated_cpm if samples >= 1 else calibrated_cpm * safe_speed
    # Avoid banker's rounding: a half-character budget should round up so the
    # displayed target is intuitive to operators.
    target_chars = int(safe_minutes * effective_cpm + 0.5)
    tolerance = get_tolerance()
    return {
        "target_seconds": safe_minutes * 60,
        "base_cpm": round(calibrated_cpm, 2),
        "effective_cpm": round(effective_cpm, 2),
        "tts_speed": safe_speed,
        "target_chars": target_chars,
        "min_chars": round(target_chars * (1 - tolerance)),
        "max_chars": round(target_chars * (1 + tolerance)),
        "tolerance_pct": round(tolerance * 100),
        "calibration_samples": samples,
        "voice_id": voice_id or "default",
        "model_id": model_id or "default",
    }


def update_calibration(
    narration: str,
    actual_duration_seconds: float,
    voice_id: str | None,
    model_id: str | None,
    speed: float,
) -> dict[str, Any] | None:
    """Store a bounded moving average from completed ElevenLabs audio."""
    spoken = spoken_char_count(narration)
    if spoken < 40 or actual_duration_seconds <= 0:
        return None
    observed_cpm = spoken / actual_duration_seconds * 60
    if not 80 <= observed_cpm <= 900:
        return None
    data = _read_calibrations()
    key = _key(voice_id, model_id, speed)
    existing = data.get(key, {})
    samples = int(existing.get("samples", 0) or 0)
    previous = float(existing.get("cpm", observed_cpm) or observed_cpm)
    # Preserve 80% history and add one bounded observation.  This avoids a
    # single unusually pause-heavy narration changing every future job.
    cpm = observed_cpm if samples == 0 else previous * 0.8 + observed_cpm * 0.2
    data[key] = {"cpm": round(cpm, 2), "samples": samples + 1}
    _write_calibrations(data)
    return {"observed_cpm": round(observed_cpm, 2), "calibrated_cpm": round(cpm, 2), "samples": samples + 1}
