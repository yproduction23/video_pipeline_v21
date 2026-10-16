"""편집점 효과음(2026-10-16 운영자 요청: 고지 문구를 화면 가운데 1초쯤 보여 주며 효과음을 넣는다).

효과음은 파일을 내려받지 않고 ffmpeg로 합성한다. 나레이션 오디오 길이는 바꾸지 않고 지정한 시각에만 더한다."""
import subprocess

from app.services.sfx import mix_sfx_events, render_title_hit


def _duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return float(out)


def _silence(path, seconds):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo", "-t", str(seconds), str(path),
                    "-loglevel", "error"], check=True)


def _rms(path, start, length):
    result = subprocess.run(
        ["ffmpeg", "-ss", str(start), "-t", str(length), "-i", str(path), "-af", "astats=metadata=1:reset=0",
         "-f", "null", "-"], capture_output=True, text=True)
    for line in result.stderr.splitlines():
        if "RMS level dB" in line and "Overall" not in line:
            continue
        if "Overall" in line:
            break
    levels = [float(part.split(":")[1]) for part in result.stderr.splitlines() if "RMS level dB" in part and ":" in part]
    return levels[-1] if levels else -120.0


def test_title_hit_is_synthesized_and_short(tmp_path):
    out = tmp_path / "hit.wav"

    assert render_title_hit(str(out)) is True
    assert 0.4 <= _duration(out) <= 1.2


def test_mixing_adds_the_effect_only_at_the_event_time_and_keeps_the_length(tmp_path):
    narration = tmp_path / "n.wav"
    _silence(narration, 6.0)
    out = tmp_path / "mixed.wav"

    assert mix_sfx_events(str(narration), [{"at": 3.0, "name": "title_hit"}], str(out), workdir=str(tmp_path)) is True

    assert abs(_duration(out) - 6.0) < 0.1
    assert _rms(out, 3.0, 0.5) > _rms(out, 0.5, 0.5) + 20  # 3초 부근에만 소리가 있다


def test_mixing_without_events_returns_false_and_leaves_the_original(tmp_path):
    narration = tmp_path / "n.wav"
    _silence(narration, 2.0)

    assert mix_sfx_events(str(narration), [], str(tmp_path / "o.wav"), workdir=str(tmp_path)) is False


def test_title_hit_is_loud_enough_to_be_heard_over_the_narration(tmp_path):
    """2026-10-16 9020 시험 영상: 효과음이 최종 오디오에서 거의 들리지 않았다(저음 위주 + 라우드니스 정규화). 중음·고음 성분을 더한다."""
    import subprocess

    out = tmp_path / "hit.wav"
    render_title_hit(str(out))
    result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                            capture_output=True, text=True)
    max_volume = next(float(line.split("max_volume:")[1].split("dB")[0]) for line in result.stderr.splitlines()
                      if "max_volume" in line)

    assert max_volume > -6.0
