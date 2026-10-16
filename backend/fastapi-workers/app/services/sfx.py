"""편집점 효과음 — 파일을 내려받지 않고 ffmpeg로 합성한다(저작권·용량 문제 없음).

2026-10-16 운영자 요청: 투자 고지 문구를 화면 가운데에 1초쯤 보여 주며 효과음을 넣고 넘어가는 "편집점" 느낌.
나레이션 오디오 길이는 바꾸지 않고 지정한 시각에 효과음만 더한다."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TITLE_HIT = (
    "[0:a]afade=t=out:st=0.04:d=0.66,volume=1.8[low];"
    "[1:a]highpass=f=1500,afade=t=out:st=0:d=0.3,volume=0.45[hiss];"
    "[low][hiss]amix=inputs=2:normalize=0,alimiter=limit=0.85,aformat=sample_rates=44100:channel_layouts=stereo"
)


def render_title_hit(output_path: str) -> bool:
    """낮은 타격음 + 짧은 숨소리 소음으로 만든 0.7초 "쿵" 효과음."""
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-f", "lavfi", "-i", "sine=frequency=90:duration=0.7",
        "-f", "lavfi", "-i", "anoisesrc=d=0.7:c=white:a=0.6", "-filter_complex", _TITLE_HIT,
        output_path, "-loglevel", "error",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        logger.exception("효과음 합성 실패")
        return False
    return result.returncode == 0 and Path(output_path).is_file() and Path(output_path).stat().st_size > 1000


_RENDERERS = {"title_hit": render_title_hit}


def mix_sfx_events(audio_path: str, events: list[dict], output_path: str, *, workdir: str) -> bool:
    """나레이션 오디오에 효과음을 지정한 시각(at, 초)마다 더한 파일을 만든다. 이벤트가 없거나 실패하면 False."""
    clean = [e for e in events or [] if e.get("name") in _RENDERERS and float(e.get("at", -1)) >= 0]
    if not clean:
        return False
    inputs: list[str] = ["-i", audio_path]
    delays: list[str] = []
    for number, event in enumerate(clean, start=1):
        sfx_path = str(Path(workdir) / f"sfx_{event['name']}.wav")
        if not Path(sfx_path).is_file() and not _RENDERERS[event["name"]](sfx_path):
            return False
        inputs += ["-i", sfx_path]
        millis = int(round(float(event["at"]) * 1000))
        delays.append(f"[{number}:a]adelay={millis}|{millis}[s{number}]")
    labels = "[0:a]" + "".join(f"[s{n}]" for n in range(1, len(clean) + 1))
    graph = ";".join(delays) + f";{labels}amix=inputs={len(clean) + 1}:duration=first:normalize=0[out]"
    cmd = ["ffmpeg", "-y", "-nostdin", *inputs, "-filter_complex", graph, "-map", "[out]", output_path, "-loglevel", "error"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except Exception:
        logger.exception("효과음 믹싱 실패")
        return False
    return result.returncode == 0 and Path(output_path).is_file() and Path(output_path).stat().st_size > 1000
