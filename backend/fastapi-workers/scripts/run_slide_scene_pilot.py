"""2026-10-14 사용자 파일럿: 캐릭터 없는 "슬라이드형" 시각 모드 1개 씬을
실제 API(Claude + Pixabay)로 끝까지 만들어 본다.

사용법 (컨테이너 안에서):
    python3 scripts/run_slide_scene_pilot.py <job_id> <scene_index> <narration> [duration_seconds]

출력: /app/data/jobs/<job_id>/slides/scene_<index>.mp4
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.providers.real.pixabay_video import download_clip, search_landscape_clip
from app.services.slide_scene_direction import generate_slide_direction

FONT_PATH = "/app/app/assets/fonts/BlackHanSans-Regular.ttf"
CANVAS = (1920, 1080)


def render_headline_overlay(headline: str, output_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_size = 96
    font = ImageFont.truetype(FONT_PATH, font_size)

    # 자막 안전 영역을 피해 화면 중상단에 배치하고, 긴 헤드라인은 두 줄로 접는다.
    max_width = int(CANVAS[0] * 0.82)
    words = headline.split(" ") if " " in headline else list(headline)
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip() if " " in headline else current + word
        if draw.textlength(trial, font=font) > max_width and current:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    lines = lines[:2]

    line_height = int(font_size * 1.3)
    total_height = line_height * len(lines)
    start_y = int(CANVAS[1] * 0.18)

    # 가독성을 위해 헤드라인 뒤에 반투명 다크 밴드를 깐다(스톡 영상 위에 흰 글자만
    # 올리면 밝은 배경에서 안 읽힘).
    band_pad = 36
    draw.rectangle(
        [0, start_y - band_pad, CANVAS[0], start_y + total_height + band_pad],
        fill=(10, 14, 20, 200),
    )

    for i, line in enumerate(lines):
        y = start_y + i * line_height
        draw.text(
            (CANVAS[0] // 2, y), line, font=font, anchor="ma",
            fill=(255, 255, 255, 255), stroke_width=6, stroke_fill=(0, 0, 0, 255),
        )
    img.save(output_path)


def compose_scene_clip(background_path: str, overlay_path: str, duration: float, output_path: str) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", background_path,
        "-i", overlay_path,
        "-filter_complex",
        f"[0:v]scale={CANVAS[0]}:{CANVAS[1]}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS[0]}:{CANVAS[1]},setsar=1[bg];"
        f"[bg][1:v]overlay=0:0:format=auto[outv]",
        "-map", "[outv]", "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 합성 실패: {result.stderr[-2000:]}")


def main() -> None:
    job_id = sys.argv[1]
    index = int(sys.argv[2])
    narration = sys.argv[3]
    duration = float(sys.argv[4]) if len(sys.argv) > 4 else 6.0

    out_dir = Path(f"/app/data/jobs/{job_id}/slides")
    out_dir.mkdir(parents=True, exist_ok=True)

    direction = generate_slide_direction(narration)
    print(f"headline={direction['headline']!r} pexels_query={direction['pexels_query']!r}")

    clip = search_landscape_clip(direction["pexels_query"], min_duration_seconds=int(duration))
    if clip is None:
        # 1차 검색어가 너무 구체적이면 결과가 없을 수 있어, 더 일반적인 대체
        # 검색어로 한 번 더 시도한다.
        clip = search_landscape_clip("korean city street daily life", min_duration_seconds=int(duration))
    if clip is None:
        raise RuntimeError("Pixabay에서 적합한 배경 영상을 찾지 못했습니다.")
    print(f"pixabay_id={clip['pixabay_id']} resolution={clip['width']}x{clip['height']} tier={clip['resolution_tier']}")

    bg_path = out_dir / f"scene_{index:03d}_bg.mp4"
    download_clip(clip["download_url"], str(bg_path))

    overlay_path = out_dir / f"scene_{index:03d}_overlay.png"
    render_headline_overlay(direction["headline"], str(overlay_path))

    final_path = out_dir / f"scene_{index:03d}.mp4"
    compose_scene_clip(str(bg_path), str(overlay_path), duration, str(final_path))
    print(f"DONE: {final_path}")


if __name__ == "__main__":
    main()
