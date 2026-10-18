"""고지 문구 1초 정지(2026-10-17 운영자 요청): 고지 카드가 나오는 동안 내레이션도 멈춘다.
음성 사이에 무음을 넣고, 이후 자막·장면 시각을 그만큼 뒤로 밀며, 카드 클립을 장면 앞에 끼운다. 자막 글자는 바뀌지 않는다."""
import subprocess
from pathlib import Path

from PIL import Image

from app.workers.longform_worker import (
    build_pause_clip, insert_audio_pauses, plan_scene_pauses, shift_chunks_for_pauses,
)


def _scenes():
    return [
        {"index": 0, "start_time": 0.0, "duration": 4.0},
        {"index": 1, "start_time": 4.0, "duration": 4.0, "pause_before": {"seconds": 1.0, "card_png": "/tmp/c.png"}},
        {"index": 2, "start_time": 8.0, "duration": 4.0},
    ]


def test_pause_plan_lists_scenes_that_ask_for_a_pause_with_their_clip_position():
    assert plan_scene_pauses(_scenes()) == [
        {"position": 1, "at": 4.0, "seconds": 1.0, "card_png": "/tmp/c.png"},
    ]
    assert plan_scene_pauses([{"index": 0, "start_time": 0.0}]) == []
    zero = [{"index": 0, "start_time": 1.0, "pause_before": {"seconds": 0, "card_png": "x"}}]
    assert plan_scene_pauses(zero) == []


def test_chunks_from_the_pause_point_on_are_pushed_back_and_text_is_unchanged():
    chunks = [
        {"index": 0, "text": "앞 줄", "start": 2.0, "end": 4.0, "duration": 2.0},
        {"index": 1, "text": "고지 뒤 첫 줄", "start": 4.0, "end": 6.0, "duration": 2.0},
        {"index": 2, "text": "다음 줄", "start": 6.0, "end": 8.0, "duration": 2.0},
    ]
    pauses = [{"position": 1, "at": 4.0, "seconds": 1.0, "card_png": "x"}]

    shifted = shift_chunks_for_pauses(chunks, pauses)

    assert [(c["start"], c["end"]) for c in shifted] == [(2.0, 4.0), (5.0, 7.0), (7.0, 9.0)]
    assert [c["text"] for c in shifted] == [c["text"] for c in chunks]
    assert chunks[1]["start"] == 4.0                      # 원본은 바뀌지 않는다


def test_audio_gets_silence_at_the_pause_point_and_gets_longer_by_the_pause(tmp_path):
    source = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-ar", "44100", "-ac", "2", str(source)], check=True)
    out = tmp_path / "paused.wav"

    assert insert_audio_pauses(str(source), [{"position": 1, "at": 2.0, "seconds": 1.0, "card_png": "x"}], str(out))

    def duration(path):
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True)
        return float(result.stdout)

    assert abs(duration(out) - 7.0) < 0.1
    # 2.0~3.0초는 무음이고 앞뒤는 소리가 있다
    def volume(start, length):
        result = subprocess.run(["ffmpeg", "-nostdin", "-ss", str(start), "-t", str(length), "-i", str(out), "-af", "volumedetect",
                                 "-f", "null", "-"], capture_output=True, text=True)
        line = next(l for l in result.stderr.splitlines() if "mean_volume" in l)
        return float(line.split("mean_volume:")[1].split("dB")[0])

    assert volume(2.1, 0.8) < -60
    assert volume(0.2, 1.5) > -30 and volume(3.2, 1.5) > -30


def test_pause_clip_is_a_still_card_of_the_requested_length(tmp_path):
    card = tmp_path / "card.png"
    Image.new("RGB", (1920, 1080), (24, 25, 28)).save(card)
    clip = tmp_path / "pause.mp4"

    assert build_pause_clip(str(card), 1.0, str(clip))

    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name:format=duration",
                            "-of", "csv=p=0", str(clip)], capture_output=True, text=True).stdout
    assert "1920,1080" in probe.replace("h264,", "") or "1920" in probe
    assert abs(float(probe.strip().splitlines()[-1]) - 1.0) < 0.15


def test_sfx_for_a_paused_scene_plays_when_the_card_appears():
    """효과음은 장면 시작이 아니라 정지 카드가 나오는 시각(정지 시작)에 맞춘다 — 장면 시작이 청크 경계로 보정돼 달라질 수 있다."""
    from app.workers.longform_worker import collect_sfx_events

    scenes = [
        {"index": 0},
        {"index": 1, "pause_before": {"seconds": 1.0, "card_png": "x"}, "sfx_events": [{"name": "title_hit", "at": 4.56}]},
        {"index": 2, "sfx_events": [{"name": "other", "at": 9.0}]},
    ]
    pauses = [{"position": 1, "at": 4.0, "seconds": 1.0, "card_png": "x"}]

    assert collect_sfx_events(scenes, pauses) == [{"name": "title_hit", "at": 4.0}, {"name": "other", "at": 9.0}]
    assert collect_sfx_events(scenes, []) == [{"name": "title_hit", "at": 4.56}, {"name": "other", "at": 9.0}]
