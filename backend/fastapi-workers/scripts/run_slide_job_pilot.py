"""2026-10-14 사용자 파일럿: Job 전체를 "슬라이드형" 시각 모드 1차본(무음→TTS
입힌 풀컷)으로 만든다. 승인 대본의 start_seconds/end_seconds를 그대로 써서
각 씬 길이를 맞추고, 기존 TTS_AUDIO 자산을 그대로 입힌다 — 내레이션 원문과
타이밍은 전혀 건드리지 않는다.

사용법 (컨테이너 안에서):
    python3 scripts/run_slide_job_pilot.py <job_id>

출력: /app/data/jobs/<job_id>/slides/rough_cut.mp4
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from app.providers.real.pixabay_video import download_clip, search_candidates
from app.services.article.evidence_planner import ArticleEvidencePlanner
from app.services.slide_scene_direction import generate_slide_direction, select_best_candidate
from app.utils.article_headline_card import render_headline_card
from app.utils.statistic_chart_card import render_bar_chart_card

FONT_PATH = "/app/app/assets/fonts/BlackHanSans-Regular.ttf"
CANVAS = (1920, 1080)
FALLBACK_QUERY = "korean city street daily life"


def generate_ass_subtitles(chunks: list[dict], ass_path: str) -> None:
    """기존 longform_worker.py의 LongformWorker._generate_ass()와 동일한
    스타일(NanumGothic 72px, 하단 중앙, 검정 외곽선)을 그대로 재사용한다.
    자막 원문은 tts_manifest.json의 chunks를 그대로 쓴다 — 요약하거나
    새로 쓰지 않는다(AGENTS.md: 승인 대본 내레이션 원문이 자막의 단일 기준)."""
    font_name = "NanumGothic"
    font_size = 72
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
        text = str(chunk.get("text") or "").strip()
        if not text:
            continue
        start_sec = max(0.0, float(chunk.get("start") or 0.0))
        dur = float(chunk.get("duration") or 3.0)
        end_sec = start_sec + dur
        lines.append(f"Dialogue: 0,{to_ass_time(start_sec)},{to_ass_time(end_sec)},Main,,0,0,0,,{text}")

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))


def generate_korean_background_image(prompt_en: str, output_path: str, *, job_id: int, scene_key: str) -> None:
    """2026-10-14 사용자 결정: Pixabay에 한국적이면서 내용과도 맞는 영상이
    없으면, Gemini로 캐릭터 없는 배경 이미지를 직접 생성한다
    (character_style_prompt="background_only" — 기존 배경 레이어 생성
    경로와 동일한 계약 재사용). 캐릭터/archetype 시스템은 전혀 쓰지 않아
    오늘 하루 싸운 의미 불일치 문제와는 무관하다."""
    from app.providers.real.image import NanaBananaProvider
    from app.utils.budget import ProviderRequestAudit

    # 2026-10-14 사용자 결정: 이 슬라이드 모드는 실사 스타일이어야 한다.
    # NanaBananaProvider의 모든 기존 경로(character_style_prompt=
    # "background_only" 포함)가 BACKGROUND_ONLY_STYLE/STYLE_LOCK 상수를
    # 강제로 주입해 "no photorealism"을 못박아 두고 있어(기존 마스코트
    # 채널의 하우스 스타일 고정 장치), 그 세 가지 주입 경로를 모두 끈다:
    # character_style_prompt="none"(캐릭터 스타일 주입 차단) +
    # style_locked=True(화풍 미지정 기본 프롬프트로 안 덮어씀) +
    # suppress_legacy_style_lock=True(STYLE_LOCK 카툰 상수 미주입).
    provider = NanaBananaProvider()
    model = "gemini-3-pro-image"
    provider.generate_image(
        prompt=(
            f"Photorealistic documentary photograph: {prompt_en}, in South Korea, "
            "authentic Korean signage and architecture, natural lighting, real camera photo, "
            "news B-roll footage style. No illustration, no cartoon, no anime, no drawing, "
            "no mascot, no character, no face, no person close-up."
        ),
        output_path=output_path,
        section="news",
        keyword=prompt_en[:30],
        character_style_prompt="none",
        style_locked=True,
        suppress_legacy_style_lock=True,
        image_provider="gemini",
        gemini_model=model,
        gemini_image_size="2K",
        gemini_max_attempts=1,
        gemini_request_audit=ProviderRequestAudit.for_job(job_id=job_id, scene_key=scene_key, model=model),
    )


_FADE_SECONDS = 0.25


def _fade_filter(duration: float) -> str:
    """2026-10-14 사용자 결정: 씬 전환에 페이드인/아웃을 넣는다. 씬 길이가
    짧으면 페이드 두 개가 겹치지 않게 길이를 자동으로 줄인다."""
    fade = min(_FADE_SECONDS, max(duration / 4, 0.05))
    fade_out_start = max(duration - fade, 0.0)
    return f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={fade_out_start:.3f}:d={fade:.3f}"


def ken_burns_clip(image_path: str, duration: float, output_path: str) -> None:
    """정지 이미지를 고정 프레임으로 영상화한다.

    2026-10-14 사용자 결정: 기존 zoompan 효과가 "왼쪽에서 오른쪽으로 계속
    움직이는" 것처럼 보여 지루하다는 피드백 — x/y를 지정하지 않은 zoompan은
    좌상단(0,0) 기준으로 확대되어 화면이 우하단으로 밀리는 것처럼 보이는
    부작용이 있었다. 효과 자체를 없애고 고정 프레임 + 페이드만 쓴다."""
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-vf",
        f"scale={CANVAS[0]}:{CANVAS[1]}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS[0]}:{CANVAS[1]},setsar=1,fps=30,"
        f"{_fade_filter(duration)}",
        "-t", str(max(duration, 0.5)),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 정지 프레임 합성 실패: {result.stderr[-2000:]}")


def compose_scene_clip(background_path: str, duration: float, output_path: str) -> None:
    """2026-10-14 사용자 결정: 헤드라인 텍스트 카드는 제거한다 — 화면
    하단의 실제 자막과 겹쳐 혼동을 줬다. 배경 영상만 트리밍해서 쓴다.
    씬 전환에는 짧은 페이드인/아웃을 넣는다."""
    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", background_path,
        "-vf",
        f"scale={CANVAS[0]}:{CANVAS[1]}:force_original_aspect_ratio=increase,"
        f"crop={CANVAS[0]}:{CANVAS[1]},setsar=1,fps=30,"
        f"{_fade_filter(duration)}",
        "-t", str(max(duration, 0.5)),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-an",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 씬 합성 실패: {result.stderr[-2000:]}")


def main() -> None:
    job_id = int(sys.argv[1])
    out_dir = Path(f"/app/data/jobs/{job_id}/slides")
    out_dir.mkdir(parents=True, exist_ok=True)

    script_path = Path(f"/tmp/job{job_id}_script.json")
    with open(script_path, encoding="utf-8") as f:
        script = json.load(f)
    sections = script.get("sections") or script.get("scenes") or []

    # 2026-10-14 사용자 결정: 실제 기사로 검증된 장면은 Pixabay/AI 배경 대신
    # "헤드라인 + 본문 발췌" 카드를 보여준다(레퍼런스 채널 스타일). 기존
    # 프로덕션 경로(images_worker.py)와 동일한 ArticleEvidencePlanner를 그대로
    # 재사용한다 — 이 파일럿만을 위한 별도 증거 판정 로직을 새로 만들지
    # 않는다.
    verified_facts = script.get("verified_facts") or []
    if verified_facts:
        evidence_result = ArticleEvidencePlanner().attach(
            job_id=job_id, scenes=sections, verified_facts=verified_facts,
        )
        sections = evidence_result.scenes
        print(f"기사 증거: {evidence_result.audit.get('selected_count', 0)}개 장면에 연결됨")

    clip_paths: list[str] = []
    used_pixabay_ids: set[int] = set()
    for i, scene in enumerate(sections):
        narration = str(scene.get("content") or scene.get("text") or "")
        start = float(scene.get("start_seconds") or 0)
        end = float(scene.get("end_seconds") or (start + 4))
        duration = max(end - start, 1.0)
        scene_clip_path = out_dir / f"scene_{i:03d}.mp4"

        article_capture = scene.get("article_capture")
        if article_capture:
            card_path = out_dir / f"scene_{i:03d}_card.png"
            publisher = str(article_capture.get("publisher") or "")
            quote = str(article_capture.get("quote") or "")
            # 2026-10-14 사용자 결정: 같은 기사에 실제 수치 계열(예:
            # "△2023년 46건 △2024년 42건 △2025년 39건")이 있으면 텍스트
            # 인용보다 그래프가 더 분명하다 — 레퍼런스 채널 편집 관행상
            # 막연한 문장보다 숫자 그래프를 우선한다(ArticleEvidencePlanner가
            # 확인된 기사 안에서 같이 찾아 둔 것 — 검증 안 된 기사의 숫자는
            # 쓰지 않는다). 없으면 헤드라인+발췌 카드로 보여준다.
            series = article_capture.get("numeric_series")
            if series:
                print(f"[scene {i}] 통계 그래프 카드: {publisher!r} dur={duration:.2f}s")
                rendered = render_bar_chart_card(
                    title=series.get("lead_text") or str(article_capture.get("source_title") or ""),
                    items=series["items"],
                    publisher=publisher,
                    output_path=str(card_path),
                )
            else:
                print(f"[scene {i}] 기사 증거 카드: {publisher!r} dur={duration:.2f}s")
                rendered = render_headline_card(
                    publisher=publisher,
                    headline=str(article_capture.get("source_title") or ""),
                    quote=quote,
                    output_path=str(card_path),
                )
            if rendered:
                ken_burns_clip(str(card_path), duration, str(scene_clip_path))
                clip_paths.append(str(scene_clip_path))
                continue
            print("  -> 카드 렌더링 실패, 일반 배경 경로로 대체")

        direction = generate_slide_direction(narration)
        print(f"[scene {i}] headline={direction['headline']!r} query={direction['pexels_query']!r} dur={duration:.2f}s")

        # 2026-10-14 사용자 결정: 단어 겹침 개수로 관련성을 재는 방식은
        # "bonsai/japanese"가 "ankle monitor GPS"와 안 겹치는데도, 혹은
        # "korea" 한 단어만 겹쳐도 통과시키는 등 신뢰할 수 없었다. 이제
        # Pixabay에서 후보 목록만 받아오고, 실제로 내레이션 맥락에 맞는지는
        # Claude가 내레이션 원문 전체를 보고 판단한다(select_best_candidate).
        # 1순위 한국적이면서 내용도 맞는 Pixabay 결과, 없으면 2순위 AI 생성
        # 배경 이미지(Ken Burns 효과로 영상화).
        candidates = [
            c for c in search_candidates(f"korea {direction['pexels_query']}", min_duration_seconds=int(min(duration, 3)))
            if c["pixabay_id"] not in used_pixabay_ids
        ]
        best_index = select_best_candidate(narration, candidates) if candidates else None
        clip = candidates[best_index] if best_index is not None else None
        if clip is not None:
            used_pixabay_ids.add(clip["pixabay_id"])
            print(f"  -> pixabay(Claude 매칭) id={clip['pixabay_id']} {clip['width']}x{clip['height']} tags={clip['tags'][:60]!r}")
            bg_path = out_dir / f"scene_{i:03d}_bg.mp4"
            download_clip(clip["download_url"], str(bg_path))
            compose_scene_clip(str(bg_path), duration, str(scene_clip_path))
        else:
            print(f"  -> Pixabay 후보 {len(candidates)}개 중 내레이션에 맞는 게 없음, AI 배경 생성으로 대체")
            bg_image_path = out_dir / f"scene_{i:03d}_bg.png"
            # 2026-10-14 사용자 재현: 압축된 pexels_query만 넘기면 "마트 안"
            # 같은 장소 맥락이 사라져 AI 배경이 엉뚱한 장소(시장통 등)로
            # 나왔다. 내레이션 원문을 함께 넘겨 장소/맥락을 보존한다.
            generate_korean_background_image(
                f"{direction['pexels_query']} — scene context: {narration}", str(bg_image_path),
                job_id=job_id, scene_key=f"slide_bg:{i}",
            )
            ken_burns_clip(str(bg_image_path), duration, str(scene_clip_path))
        clip_paths.append(str(scene_clip_path))

    concat_list_path = out_dir / "concat_list.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")

    silent_cut_path = out_dir / "rough_cut_silent.mp4"
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent_cut_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat 실패: {result.stderr[-2000:]}")

    audio_path = Path(f"/app/data/jobs/{job_id}/tts/full.mp3")
    with_audio_path = out_dir / "rough_cut_with_audio.mp4"
    if audio_path.is_file():
        cmd = [
            "ffmpeg", "-y", "-i", str(silent_cut_path), "-i", str(audio_path),
            "-c:v", "copy", "-c:a", "aac", "-shortest", str(with_audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 오디오 합성 실패: {result.stderr[-2000:]}")
    else:
        with_audio_path = silent_cut_path
        print(f"경고: TTS 오디오 자산을 찾지 못함({audio_path}), 무음 출력")

    final_path = out_dir / "rough_cut.mp4"
    tts_manifest_path = Path(f"/app/data/jobs/{job_id}/tts/tts_manifest.json")
    if tts_manifest_path.is_file():
        with open(tts_manifest_path, encoding="utf-8") as f:
            tts_manifest = json.load(f)
        chunks = tts_manifest.get("chunks") or []
        ass_path = out_dir / "subtitles.ass"
        generate_ass_subtitles(chunks, str(ass_path))
        ass_path_escaped = str(ass_path).replace("\\", "/").replace(":", "\\:")
        cmd = [
            "ffmpeg", "-y", "-i", str(with_audio_path),
            "-vf", f"ass='{ass_path_escaped}'",
            "-c:a", "copy", "-movflags", "+faststart", str(final_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 자막 합성 실패: {result.stderr[-2000:]}")
        print(f"자막 {len(chunks)}개 항목 번인 완료")
    else:
        final_path = with_audio_path
        print(f"경고: TTS manifest를 찾지 못함({tts_manifest_path}), 자막 없이 출력")

    print(f"DONE: {final_path}")


if __name__ == "__main__":
    main()
