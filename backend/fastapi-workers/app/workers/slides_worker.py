"""슬라이드형(캐릭터 없는) 시각 모드 — images_worker.py의 자매 워커.

2026-10-15 사용자 결정: V5 캐릭터/아키타입 시스템이 비금융(특히 범죄)
콘텐츠에서 반복적으로 의미 불일치를 일으켜, 캐릭터 없이 "실사 배경 +
기사 증거 카드 + 통계 그래프"로 구성하는 대안 시각 모드를 실제 운영
파이프라인에 연결한다. images_worker.py 안에 분기를 넣지 않고 별도
파일로 둔 이유: images_worker.py는 2500줄 이상의 캐릭터·LoRA·포즈 전용
로직에 깊이 얽혀 있어, 그 안에 분기를 넣으면 캐릭터 경로 회귀 위험이
크다. 대신 이 워커는 images_worker.py와 **동일한 입출력 계약**
(scenes_meta 입력 → {job_id, scenes, scene_count, ...} 출력, 각 장면은
SceneImageDto 호환 dict)을 쓰는 형제로 둔다 — Spring/DB 자산 저장·
longform 소비 경로는 전혀 건드리지 않는다.

배경 층/오버레이 층: 모든 장면에 스톡 → (Kling) → Gemini 배경을 먼저 깔고, 기사
카드·통계 그래프는 그 위의 오버레이로 얹는다(slide_layers에 두 층을 분리해 보존).

장면 준비(대본·TTS·자막 계보 검증, 기사 증거 자동 부착)는
app.services.scene_preparation.prepare_job_scenes()를 그대로 재사용한다
— images_worker.py와 완전히 같은 검증을 거친다.

품질 게이트: 캐릭터 전용 게이트(해부학·얼굴·의상)는 이 모드에 캐릭터가
없으므로 적용하지 않는다. 장면 의미 정렬(assess_visual_alignment)은
캐릭터 유무와 무관한 범용 게이트라 그대로 적용한다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import runtime_config
from app.services.scene_preparation import prepare_job_scenes
from app.services.slide_background import generate_korean_slide_background
from app.services.slide_kling import generate_slide_kling_clip
from app.services.slide_edit_plan import fallback_key_phrase, load_plan, plan_matches, save_plan
from app.services.slide_edit_replan import build_edit_plan_with_repair as build_edit_plan
from app.services.slide_sequence import render_sequence_clip
from app.services.slide_progress import SlideProgress
from app.services.slide_overlay import (
    DISCLAIMER_CARD_SECONDS,
    compose_animated_overlay,
    ensure_board_background,
    plan_slide_overlays,
    render_big_number_overlay,
    render_bullet_overlay,
    render_compare_numbers_layers,
    render_equation_overlay,
    render_flow_layers,
    render_disclaimer_card,
    render_key_text_overlay,
    render_news_clip_overlay,
    render_stat_overlay,
)
from app.services.slide_scene_direction import generate_slide_direction, select_best_candidate
from app.providers.real.pixabay_video import PixabayVideoUnavailableError, download_clip, search_candidates
from app.utils.intro_motion import scene_duration_seconds
from app.utils.visual_qa import assess_visual_alignment
from app.utils.operational_contract_audit import build_operational_contract_audit

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

# 스톡이 없어 Kling으로 만든 장면이 이 개수만큼 이어지면 다음 폴백은 Gemini 정지
# 이미지로 대체한다(비용·시각 리듬 상한, 운영자 결정 2026-10-15).
MAX_CONSECUTIVE_KLING = 2

# 배경 층 병렬 생성 동시 작업 수. Kling을 켜면 연속 규칙 때문에 순차 처리한다.
SLIDE_MAX_WORKERS = max(1, int(os.getenv("SLIDE_MAX_WORKERS", "6")))
_PIXABAY_LOCK = threading.Lock()
# Pixabay 영상 검색은 분당 100회 제한이라 병렬 장면 처리 중에도 검색 간격을 둔다.
_PIXABAY_MIN_INTERVAL = 0.65
_PIXABAY_THROTTLE_LOCK = threading.Lock()
_pixabay_last_search = [0.0]
# 장면당 시도할 최대 검색어 수(주 검색어 2형태 + 대체 검색어 2개).
MAX_STOCK_QUERIES = 4
_GENERIC_STOCK_QUERIES = ("business office", "city skyline")

# 2026-10-16 운영자 요청(Gemini 비용 절감): 19번 Job은 57장면에 Gemini 112건을 썼다(46장면이 2~3회).
# 같은 내레이션의 배경 층은 다시 만들지 않고 재사용한다. 단일 장면 다시 생성은 새로 만든다.
BASE_CACHE_VERSION = "slide-base-v1"


def _base_cache_key(narration: str) -> str:
    return hashlib.sha256(f"{BASE_CACHE_VERSION}|{narration}".encode("utf-8")).hexdigest()


def _base_cache_path(job_dir: Path, index: int, kind: str) -> Path:
    return job_dir / f"scene_{index:03d}_{kind}_cache.json"


def _read_base_cache(job_dir: Path, index: int, kind: str, narration: str) -> dict | None:
    """같은 내레이션으로 만든 배경 층이 디스크에 그대로 있으면 그 기록을 돌려준다."""
    try:
        meta = json.loads(_base_cache_path(job_dir, index, kind).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("key") != _base_cache_key(narration):
        return None
    for field in ("image_path", "video_path"):
        path = meta.get(field)
        if path and not (Path(path).is_file() and Path(path).stat().st_size > 0):
            return None
    return meta if meta.get("image_path") or meta.get("video_path") else None


def _prime_used_stock_ids(job_dir: Path, narrations: list[str], keep_indexes: set[int], used_ids: set) -> set[int]:
    """실행 전에, 그대로 두는 장면(캐시 재사용·이번에 다시 만들지 않는 장면)의 스톡 클립 ID를 먼저 등록한다.

    앞 장면과 같은 클립을 쓰는 캐시는 충돌로 돌려줘 그 장면이 새로 고르게 한다."""
    conflicts: set[int] = set()
    for index in sorted(keep_indexes):
        if index >= len(narrations):
            continue
        cached = _read_base_cache(job_dir, index, "stock", narrations[index])
        pixabay_id = (cached or {}).get("pixabay_id")
        if not pixabay_id:
            continue
        if pixabay_id in used_ids:
            conflicts.add(index)
        else:
            used_ids.add(pixabay_id)
    return conflicts


def _write_base_cache(job_dir: Path, index: int, kind: str, narration: str, **fields) -> None:
    try:
        _base_cache_path(job_dir, index, kind).write_text(
            json.dumps({"key": _base_cache_key(narration), "version": BASE_CACHE_VERSION, **fields},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("슬라이드 장면 %s 배경 캐시 기록 실패(재사용만 못 함)", index)


def _throttled_search(query: str, **kwargs) -> list[dict]:
    with _PIXABAY_THROTTLE_LOCK:
        wait = _pixabay_last_search[0] + _PIXABAY_MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _pixabay_last_search[0] = time.monotonic()
    return search_candidates(query, **kwargs)

def _extract_first_frame(video_path: str, output_path: str) -> bool:
    """영상 클립의 대표 프레임을 PNG로 뽑는다 — 의미 정렬 게이트와, 영상
    처리가 실패했을 때의 정지 이미지 폴백 양쪽에 쓰인다."""
    cmd = ["ffmpeg", "-y", "-i", video_path, "-frames:v", "1", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and Path(output_path).is_file()


def _extract_first_frame_at(video_path: str, output_path: str, seconds: float) -> bool:
    """오버레이가 이미 나타난 시점의 프레임 — 의미 정렬 게이트와 미리보기가 합성 결과를 본다."""
    cmd = ["ffmpeg", "-y", "-ss", f"{seconds:.2f}", "-i", video_path, "-frames:v", "1", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and Path(output_path).is_file()


class _NullProgress:
    """계획서만 만드는 실행(plan_only)은 장면 진행 파일을 건드리지 않는다."""

    def preparing(self) -> None: ...
    def start(self, scenes: list[dict]) -> None: ...
    def scene_done(self, index: int, out: dict) -> None: ...
    def finish(self, status: str = "completed") -> None: ...


class SlidesWorker:
    """images_worker.ImagesWorker와 같은 호출 계약의 슬라이드형 워커."""

    def generate(
        self,
        scenes_meta: list | None = None,
        job_id: int = 0,
        tts_meta_json: str | None = None,
        script_meta_json: str | None = None,
        budget_limit_krw: int | None = None,
        scene_indices: list[int] | None = None,
        plan_only: bool = False,
        **_ignored_character_kwargs,
    ) -> dict:
        """**_ignored_character_kwargs: character_image_path/style/poses_dir,
        lora_*, autonomy_mode 등 캐릭터 전용 인자는 이 모드와 무관하므로
        받기만 하고 쓰지 않는다 — Spring이 images_worker와 같은 호출부로
        두 워커를 모두 부를 수 있게 한다."""
        progress = _NullProgress() if plan_only else SlideProgress(
            job_id, DATA_DIR, keep_existing=scene_indices is not None,
        )
        progress.preparing()
        try:
            prepared = prepare_job_scenes(
                job_id=job_id,
                tts_meta_json=tts_meta_json,
                script_meta_json=script_meta_json,
                scenes_meta=scenes_meta,
                budget_limit_krw=budget_limit_krw,
            )
        except BaseException:
            progress.finish("failed")
            raise
        scenes_meta = prepared.scenes_meta
        job_dir = DATA_DIR / "jobs" / str(job_id) / "slides"
        job_dir.mkdir(parents=True, exist_ok=True)

        generated: list[dict] = []
        used_pixabay_ids: set[int] = set()
        # 2026-10-15 운영자 결정: 스톡에 없는 장면의 Kling 사용 여부는 평가 중이라
        # 런타임 설정(slide_kling_enabled, 기본 꺼짐)으로 전환한다. 켜면 Kling을
        # 연속 MAX_CONSECUTIVE_KLING개 만든 뒤 다음 폴백 장면은 Gemini 정지
        # 이미지로 대체하고 카운터를 리셋한다(장면 순서대로 처리).
        kling_state = (
            {"streak": 0, "provider": None, "provider_loaded": False}
            if bool(runtime_config.value("slide_kling_enabled")) else None
        )
        targets = [
            (index, scene) for index, scene in enumerate(scenes_meta)
            if scene_indices is None or index in scene_indices
        ]
        # 사용자 요청(2026-10-15): 장면이 몇 개로 나뉘었는지 먼저 알리고, 끝난 장면을
        # 하나씩 UI에 쌓아 보여준다(progress.json + 썸네일, UI가 폴링).
        def narration_of(scene: dict) -> str:
            return str(scene.get("subtitle_text") or scene.get("content") or scene.get("text") or "").strip()

        progress.start([{"index": index, "text": narration_of(scene)} for index, scene in targets])

        # 같은 스톡 클립이 여러 장면에 나오지 않게, 그대로 두는 장면의 클립 ID를 먼저 등록한다(2026-10-16 20번 Job 재현).
        target_indexes = {index for index, _ in targets}
        keep_indexes = set(range(len(scenes_meta))) if scene_indices is None else set(range(len(scenes_meta))) - target_indexes
        cache_conflicts = _prime_used_stock_ids(job_dir, [narration_of(s) for s in scenes_meta], keep_indexes, used_pixabay_ids)
        ai_miss_state = {"used": 0, "max": int(runtime_config.value("slide_stock_miss_ai_max")), "lock": threading.Lock()}

        # 오버레이 계획(Claude): 고정 개수·간격 없이 장면 맥락을 보고 정한다(운영자 지시).
        # 화면에 올라가는 글자·숫자는 계획 검증에서 내레이션 원문과 대조한다.
        from app.utils.statistic_chart_card import extract_numeric_series

        briefs: dict[int, dict] = {}
        all_narrations = [narration_of(scene) for scene in scenes_meta]
        # 계획서는 일부 장면만 다시 만드는 실행에서도 전체 흐름을 기준으로 하므로 모든 장면의 브리프를 만든다.
        for index, scene in enumerate(scenes_meta):
            # 수치 오버레이 검증용: 같은 흐름에서 직전 두 장면이 이미 말한 내용(원문 그대로).
            context = " ".join(all_narrations[max(0, index - 2):index]) if index < len(all_narrations) else ""
            brief = {
                "narration": narration_of(scene), "context": context,
                "duration": scene_duration_seconds(scene, float(runtime_config.value("scene_duration_sec"))),
            }
            capture = scene.get("article_capture")
            if capture:
                series = capture.get("numeric_series") or extract_numeric_series(str(capture.get("quote") or ""))
                brief.update(
                    has_article=True, has_numeric_series=bool(series),
                    article_brief=f"{capture.get('publisher') or ''} · {str(capture.get('source_title') or '')[:40]}",
                )
            briefs[index] = brief
        highlight = self._highlight_boundary(script_meta_json)
        banner_index = self._disclaimer_scene_index(scenes_meta, {i for i, _ in targets}, highlight)
        highlight_end = self._highlight_end_scene(scenes_meta, highlight)

        # 편집 계획서(2026-10-16 운영자 결정): 배경과 후편집을 생성 전에 한 번에 계획·저장하고, 이후
        # 장면 생성은 계획서를 그대로 실행한다. 같은 내레이션·같은 계획 버전이면 저장된 계획서를 재사용한다.
        edit_plan = load_plan(job_id, DATA_DIR)
        # 운영자가 확인한 계획서가 있는데 입력(내레이션·길이)이 달라 새로 만드는 경우는 검수 사유로 남긴다.
        plan_replaced_after_review = bool(edit_plan) and not plan_matches(edit_plan, briefs) and not plan_only
        # 계획서만 만드는 요청은 명시적인 (재)생성이므로 항상 새로 만든다(재사용은 실행에만 적용한다).
        if plan_only or not plan_matches(edit_plan, briefs):
            edit_plan = build_edit_plan(
                briefs, job_id=job_id,
                highlight_end_index=(highlight_end - 1) if highlight_end else None,
            )
            if edit_plan:
                save_plan(edit_plan, DATA_DIR)
        if plan_only:
            # 2026-10-16 운영자 요청: 계획서만 만들고 멈춘다 — 이미지·영상은 한 장도 만들지 않는다(비용 0).
            return {
                "job_id": job_id,
                "status": "PLAN_READY" if edit_plan else "PLAN_FAILED",
                "scenes": [],
                "scene_count": 0,
                "gifs": [],
                "gif_count": 0,
                "visual_mode": "SLIDE",
                "requires_manual_review": not edit_plan,
                "review_reasons": [] if edit_plan else ["편집 계획서 생성 실패(Claude) — 다시 시도해 주세요."],
                "edit_plan": (
                    {
                        "version": edit_plan["version"], "input_hash": edit_plan["input_hash"],
                        "scene_count": edit_plan["scene_count"], "summary": edit_plan["summary"],
                        "segments": edit_plan["segments"],
                    }
                    if edit_plan else None
                ),
            }
        if edit_plan:
            plan_failed = False
            plan_scenes = {s["index"]: s for s in edit_plan["scenes"]}
            overlay_plan = {i: s["overlay"] for i, s in plan_scenes.items() if s.get("overlay")}
            base_plans = plan_scenes
            sequences_by_scene = {
                index: seq for seq in edit_plan.get("sequences", []) for index in seq["scenes"]
            }
        else:
            # 계획서 생성 실패: 조용히 넘기지 않고 검수 사유로 남기고, 기존 방식(즉석 오버레이 계획)으로 진행한다.
            planned = plan_slide_overlays({i: briefs[i] for i, _ in targets})
            plan_failed = planned is None
            overlay_plan = planned or {}
            base_plans = {}
            sequences_by_scene = {}
        banner_plan = self._banner_schedule(scenes_meta, banner_index, {i for i, _ in targets})

        def render(item: tuple[int, dict]) -> dict:
            index, scene = item
            out = self._render_scene(
                index=index, scene=scene, job_id=job_id, job_dir=job_dir,
                used_pixabay_ids=used_pixabay_ids, kling_state=kling_state,
                overlay_plan=overlay_plan.get(index),
                banner=banner_plan.get(index),
                base_plan=base_plans.get(index),
                sequence=sequences_by_scene.get(index), ai_miss_state=ai_miss_state,
                # 전체 실행에서만 배경을 재사용한다. 단일 장면 다시 생성은 새 결과를 원하는 요청이다.
                reuse_base=scene_indices is None and index not in cache_conflicts,
            )
            progress.scene_done(index, out)
            return out

        try:
            if kling_state is None and len(targets) > 1:
                # 배경 층은 장면끼리 독립이라 병렬로 만든다(map이 순서를 보존한다).
                with ThreadPoolExecutor(max_workers=SLIDE_MAX_WORKERS) as pool:
                    generated.extend(pool.map(render, targets))
            else:
                generated.extend(render(item) for item in targets)
        except BaseException:
            progress.finish("failed")
            raise

        semantic_quality = assess_visual_alignment(
            generated,
            enabled=bool(runtime_config.value("visual_qa_enabled")),
            max_scenes=int(runtime_config.value("visual_qa_max_scenes")),
        )
        semantic_by_index = {item["index"]: item for item in semantic_quality.get("reviewed", [])}
        for scene in generated:
            scene.setdefault("quality_flags", [])
            semantic = semantic_by_index.get(scene.get("index"))
            if semantic:
                scene["semantic_score"] = semantic["score"]
                scene["semantic_reason"] = semantic["reason"]
                if semantic["retry_recommended"]:
                    scene["quality_flags"] = list(dict.fromkeys([
                        *scene["quality_flags"],
                        "semantic_review_recommended",
                        *semantic.get("failure_categories", []),
                    ]))

        review_reasons = (
            ["확인한 편집 계획서와 입력(내레이션·장면 길이)이 달라 계획서를 새로 만들어 실행했습니다 — 계획서를 다시 확인해 주세요."]
            if plan_replaced_after_review else []
        )
        review_reasons += [
            f"장면 {scene['index']}: {scene.get('slide_generation_error')}"
            for scene in generated
            if scene.get("slide_generation_error")
        ]
        review_reasons += [
            f"장면 {scene['index']}: Kling 생성 실패로 정지 이미지 사용 ({scene.get('kling_failure_reason')})"
            for scene in generated
            if scene.get("kling_failure_reason")
        ]
        review_reasons += [
            f"장면 {scene['index']}: 오버레이 합성 실패, 배경만 사용 ({scene.get('overlay_failure_reason')})"
            for scene in generated
            if scene.get("overlay_failure_reason")
        ]

        if plan_failed:
            review_reasons.append(
                "편집 계획서·오버레이 계획 생성 실패(Claude) — 모든 장면이 배경만 사용됐습니다. 다시 생성이 필요합니다."
            )

        progress.finish("completed")
        return {
            "job_id": job_id,
            "scenes": generated,
            "scene_count": len(generated),
            "gifs": [],
            "gif_count": 0,
            "tts_subtitle_sync": prepared.tts_subtitle_sync,
            "evidence_audit": prepared.evidence_audit,
            "visual_mode": "SLIDE",
            "operational_contract_audit": build_operational_contract_audit("images", checks={
                "tts_subtitle_sync_passed": prepared.tts_subtitle_sync.get("passed"),
                "scene_count": len(generated),
                "visual_mode": "SLIDE",
                "manual_review_required": bool(review_reasons),
            }),
            "requires_manual_review": bool(review_reasons),
            "review_reasons": review_reasons,
            "edit_plan": (
                {
                    "version": edit_plan["version"], "input_hash": edit_plan["input_hash"],
                    "scene_count": edit_plan["scene_count"], "summary": edit_plan["summary"],
                    "segments": edit_plan["segments"],
                }
                if edit_plan else None
            ),
        }

    def _render_scene(
        self, *, index: int, scene: dict, job_id: int, job_dir: Path,
        used_pixabay_ids: set[int], kling_state: dict | None = None,
        overlay_plan: dict | None = None, banner: dict | None = None, reuse_base: bool = False,
        base_plan: dict | None = None, sequence: dict | None = None, ai_miss_state: dict | None = None,
    ) -> dict:
        narration = str(
            scene.get("subtitle_text") or scene.get("content") or scene.get("text") or ""
        ).strip()
        duration = scene_duration_seconds(scene, float(runtime_config.value("scene_duration_sec")))
        start = float(scene.get("start_seconds") or 0.0)

        out = dict(scene)
        out["index"] = index
        out["start"] = start
        out["duration"] = duration
        out["text"] = narration
        out["use_kling"] = False
        out["generation_method"] = "slide"
        # 기사/인포그래픽 장면은 이 워커가 배경+오버레이를 이미 합성했다. longform의
        # 결정론적 장면 렌더(visual_kind=article_scene 등)가 합성본을 덮어쓰지 않게 막는다.
        out["_deterministic_scene_prepared"] = True

        # 1층(배경): 기사 카드 장면도 포함해 모든 장면에 스톡 → (Kling) → Gemini를 먼저 깐다.
        # 단, Claude가 "설명 장면"(explain)으로 계획한 장면은 배경 영상을 찾지 않고 일관된
        # 칠판 배경 위에 인포그래픽을 크게 보여준다(레퍼런스 영상의 방식, 비용·시간도 줄어든다).
        try:
            board_path = job_dir / "board_background_v4.png"
            planned_background = (base_plan or {}).get("background")
            if planned_background == "board" or (
                not base_plan and overlay_plan and overlay_plan.get("explain")
            ):
                if not ensure_board_background(str(board_path)):
                    raise RuntimeError("칠판 배경 생성 실패")
                out["image_path"] = str(board_path)
                out["visual_type"] = "slide_board"
                rendered = True
            elif planned_background == "photo":
                # 계획서가 정한 사진 장면: 스톡 검색 없이 계획서의 촬영 설명(글자·화면 금지)으로 생성한다.
                self._render_ai_background_scene(
                    index=index, narration=narration, job_id=job_id, job_dir=job_dir, out=out,
                    reuse_base=reuse_base, photo_brief=base_plan.get("photo_brief") or "",
                )
                rendered = True
            else:
                rendered = self._render_pixabay_scene(
                    index=index, narration=narration, duration=duration,
                    job_dir=job_dir, used_pixabay_ids=used_pixabay_ids, out=out,
                    reuse_base=reuse_base,
                    planned_queries=(base_plan or {}).get("stock_queries") if planned_background == "stock" else None,
                )
                if not rendered and planned_background == "stock" and self._claim_ai_replacement(ai_miss_state):
                    # 2026-10-16 운영자 결정: 스톡이 내용과 안 맞으면 회색 배경 대신 Gemini 이미지로 대체한다(영상당 상한).
                    queries = (base_plan or {}).get("stock_queries") or []
                    try:
                        self._render_ai_background_scene(
                            index=index, narration=narration, job_id=job_id, job_dir=job_dir, out=out,
                            reuse_base=reuse_base, photo_brief=str(queries[0]) if queries else "",
                        )
                        out["base_fallback"] = "stock_miss_to_ai"
                        out["quality_flags"] = list(dict.fromkeys([
                            *out.get("quality_flags", []), f"stock_miss_ai:{out.get('stock_miss_reason', 'unknown')}",
                        ]))
                        rendered = True
                    except Exception:
                        logger.exception("슬라이드 장면 %s 스톡 대체 AI 이미지 실패 — 회색 배경으로 대체", index)
                if not rendered and planned_background == "stock":
                    # 스톡이 없으면 유료 사진으로 넘어가지 않고 칠판 + 계획서의 대체 구절로 만든다.
                    if not ensure_board_background(str(board_path)):
                        raise RuntimeError("칠판 배경 생성 실패")
                    out["image_path"] = str(board_path)
                    out["visual_type"] = "slide_board"
                    out["base_fallback"] = "stock_miss_to_board"
                    out["quality_flags"] = list(dict.fromkeys([
                        *out.get("quality_flags", []), f"stock_miss:{out.get('stock_miss_reason', 'unknown')}",
                    ]))
                    if not overlay_plan:
                        overlay_plan = {
                            "type": "key_text", "text": base_plan.get("fallback_key_text")
                            or fallback_key_phrase(narration),
                            "icon": None, "explain": True, "source": "fallback_phrase",
                        }
                    rendered = True
            if not rendered:
                if kling_state is not None and kling_state["streak"] < MAX_CONSECUTIVE_KLING:
                    self._render_kling_scene(
                        index=index, narration=narration, duration=duration, job_id=job_id,
                        job_dir=job_dir, out=out, kling_state=kling_state, reuse_base=reuse_base,
                    )
                else:
                    self._render_ai_background_scene(
                        index=index, narration=narration, job_id=job_id, job_dir=job_dir, out=out,
                        reuse_base=reuse_base,
                    )
                    if kling_state is not None:
                        out["kling_skipped_reason"] = "consecutive_kling_cap"
                        kling_state["streak"] = 0
        except Exception as exc:
            logger.exception("슬라이드 장면 %s 배경 생성 실패", index)
            out["slide_generation_error"] = str(exc)
            out["quality_flags"] = ["slide_generation_failed"]
            return out

        # 2층(오버레이): 기사 카드/통계 그래프. 실패해도 배경 층은 그대로 둔다.
        base_visual_type = out.get("visual_type")
        out["slide_layers"] = {
            "base": {
                "type": base_visual_type,
                "image_path": out.get("image_path"),
                "video_path": out.get("stock_video_path"),
            },
            "overlays": [],
        }
        try:
            if sequence and base_visual_type == "slide_board" and not out.get("base_fallback"):
                # 회색 배경 구간은 장면 경계를 넘어 이어지는 모션 시퀀스가 화면을 담당한다(장면 몫만 잘라 낸다).
                self._render_sequence_scene(
                    index=index, duration=duration, job_dir=job_dir, out=out, sequence=sequence,
                )
            else:
                self._apply_overlay(
                    index=index, scene=scene, duration=duration, job_dir=job_dir,
                    out=out, overlay_plan=overlay_plan,
                )
        except Exception as exc:
            logger.exception("슬라이드 장면 %s 오버레이 실패", index)
            out["overlay_failure_reason"] = str(exc)
            out["quality_flags"] = list(dict.fromkeys([*out.get("quality_flags", []), "overlay_failed_base_only"]))
        if banner:
            try:
                self._apply_disclaimer_banner(
                    index=index, duration=duration, job_dir=job_dir, out=out, schedule=banner,
                )
            except Exception as exc:
                logger.exception("슬라이드 장면 %s 고지 배너 실패", index)
                out["overlay_failure_reason"] = str(exc)
                out["quality_flags"] = list(dict.fromkeys([*out.get("quality_flags", []), "banner_failed"]))
        return out

    def _render_sequence_scene(
        self, *, index: int, duration: float, job_dir: Path, out: dict, sequence: dict,
    ) -> None:
        """시퀀스 시간축에서 이 장면 몫만 mp4로 만든다. 같은 시간 함수라 인접 장면과 화면이 이어진다."""
        offsets = sequence.get("offsets") or {}
        t0 = float(offsets.get(str(index), 0.0))
        clip_path = job_dir / f"scene_{index:03d}_seq.mp4"
        if not render_sequence_clip(sequence, t0, t0 + duration, str(out["image_path"]), str(clip_path)):
            raise RuntimeError("모션 시퀀스 렌더링 실패")
        out["stock_video_path"] = str(clip_path)
        frame_path = job_dir / f"scene_{index:03d}_seq_frame.png"
        if _extract_first_frame_at(str(clip_path), str(frame_path), min(1.6, max(duration * 0.7, 0.5))):
            out["image_path"] = str(frame_path)
        out["visual_type"] = "slide_sequence"
        out["overlay_type"] = "sequence"
        out["slide_layers"]["overlays"].append({
            "type": "sequence", "sequence_id": sequence.get("id"), "signature": sequence.get("signature"),
            "elements": [{"id": e["id"], "kind": e["kind"], "at": e["at"], "until": e["until"]}
                         for e in sequence.get("elements", [])],
        })

    @staticmethod
    def _highlight_boundary(script_meta_json: str | None) -> dict | None:
        """대본 단계가 남긴 하이라이트 구간 계약(`highlight`)을 읽는다. 없거나 깨져 있으면 None."""
        if not script_meta_json:
            return None
        try:
            meta = json.loads(script_meta_json) if isinstance(script_meta_json, str) else script_meta_json
        except (TypeError, ValueError):
            return None
        highlight = meta.get("highlight") if isinstance(meta, dict) else None
        if not isinstance(highlight, dict) or not highlight.get("applicable") or not highlight.get("present"):
            return None
        return highlight

    @staticmethod
    def _disclaimer_scene_index(
        scenes_meta: list, target_indexes: set[int], highlight: dict | None = None,
    ) -> int | None:
        """고지 배너는 하이라이트 구간이 끝난 직후 첫 장면에 단다.

        우선순위: ① 대본이 남긴 하이라이트 마지막 문장(end_text)이 들어 있는 장면의 다음 장면
        ② 하이라이트 누적 글자 수(end_char) 이후 첫 장면 ③ 구간 정보가 없으면 intro 이후 첫 장면
        (그것도 없으면 5번째). 일부 장면만 다시 만드는 실행에서는 대상에 포함될 때만 쓴다."""
        if not scenes_meta:
            return None
        index = SlidesWorker._highlight_end_scene(scenes_meta, highlight)
        if index is None:
            index = next(
                (i for i, scene in enumerate(scenes_meta) if str(scene.get("section") or "") not in {"", "intro"}),
                None,
            )
        if index is None:
            index = min(5, len(scenes_meta) - 1)
        return index if index in target_indexes else None

    @staticmethod
    def _highlight_end_scene(scenes_meta: list, highlight: dict | None) -> int | None:
        if not highlight:
            return None
        squash = lambda text: re.sub(r"\s+", "", str(text or ""))  # noqa: E731
        texts = [
            squash(scene.get("subtitle_text") or scene.get("content") or scene.get("text"))
            for scene in scenes_meta
        ]
        tail = squash(highlight.get("end_text"))[-14:]
        if len(tail) >= 6:
            for i, text in enumerate(texts):
                if tail in text:
                    return i + 1 if i + 1 < len(scenes_meta) else None
        end_char = highlight.get("end_char")
        if isinstance(end_char, int) and end_char > 0:
            consumed = 0
            for i, text in enumerate(texts):
                if consumed >= end_char:
                    return i
                consumed += len(text)
        return None

    def _apply_overlay(
        self, *, index: int, scene: dict, duration: float, job_dir: Path, out: dict,
        overlay_plan: dict | None,
    ) -> None:
        if not overlay_plan:
            return
        capture = scene.get("article_capture") or {}
        if overlay_plan["type"] == "article_card" and not capture:
            # 승인한 계획서에는 기사 카드가 있지만 실행 시점에 기사 캡처가 없을 수 있다(수집은 실행마다 달라진다).
            # 계획서를 바꾸지 않고 원문 구절 강조로 대체하고, 검수 표시를 남겨 조용히 사라지지 않게 한다.
            narration = str(scene.get("subtitle_text") or scene.get("content") or scene.get("text") or "")
            overlay_plan = {
                "type": "key_text", "text": fallback_key_phrase(narration), "icon": None, "explain": False,
                "source": "article_card_without_capture",
            }
            out["quality_flags"] = list(dict.fromkeys([*out.get("quality_flags", []), "article_card_without_capture"]))
        kind = overlay_plan["type"]
        overlay_png = job_dir / f"scene_{index:03d}_overlay.png"
        spec: dict = {"type": kind, "source": "claude_overlay_plan"}
        # 영상 위에 얹는 데이터 카드·목록은 배경을 어둡게 눌러 읽히게 한다(레퍼런스 분석, 스톡 장면의 41%).
        dim_base = kind != "article_card" and out.get("visual_type") in {
            "slide_stock_video", "slide_ai_background", "slide_kling_video",
        }

        if kind == "key_text":
            if not render_key_text_overlay(
                text=overlay_plan["text"], icon=overlay_plan.get("icon"), output_path=str(overlay_png),
                style="board" if overlay_plan.get("explain") else "footage",
            ):
                raise RuntimeError("강조 텍스트 렌더링 실패")
            spec.update(text=overlay_plan["text"], icon=overlay_plan.get("icon"))
            out["visual_type"] = "slide_key_text"
        elif kind in {"big_number", "compare_numbers", "flow"}:
            if kind == "big_number":
                layers = render_big_number_overlay(
                    value=overlay_plan["value"], label=overlay_plan.get("label", ""),
                    icon=overlay_plan.get("icon"), output_path=str(overlay_png),
                )
                spec.update(value=overlay_plan["value"], label=overlay_plan.get("label", ""))
                out["visual_type"] = "slide_big_number"
            elif kind == "compare_numbers":
                layers = render_compare_numbers_layers(
                    headline=overlay_plan["headline"], rows=overlay_plan["rows"],
                    icon=overlay_plan.get("icon"), output_path=str(overlay_png),
                )
                spec.update(headline=overlay_plan["headline"], rows=overlay_plan["rows"])
                out["visual_type"] = "slide_compare_numbers"
            else:
                layers = render_flow_layers(nodes=overlay_plan["nodes"], output_path=str(overlay_png))
                spec.update(nodes=overlay_plan["nodes"])
                out["visual_type"] = "slide_flow"
            if not layers:
                raise RuntimeError(f"{kind} 렌더링 실패")
            # 요소가 내레이션 흐름대로 하나씩 나타난다: 레이어마다 시작 시점을 늦춘다.
            step = 0.9 if len(layers) > 1 else 0.0
            last_start = max(duration - 1.0, 0.35)
            for k, layer in enumerate(layers):
                self._compose_into_clip(
                    index=index, duration=duration, job_dir=job_dir, out=out,
                    overlay_png=Path(layer), suffix=f"composed{k}",
                    start_offset=min(0.35 + k * step, last_start),
                    dim_base=dim_base and k == 0,  # 이후 층은 이미 눌린 클립 위에 쌓인다
                )
            spec["overlay_png"] = [str(layer) for layer in layers]
            out["slide_layers"]["overlays"].append(spec)
            out["overlay_type"] = kind
            return
        elif kind == "equation":
            if not render_equation_overlay(
                label=overlay_plan.get("label", ""), left=overlay_plan["left"], ops=overlay_plan["ops"],
                result=overlay_plan["result"], icon=overlay_plan.get("icon"), output_path=str(overlay_png),
            ):
                raise RuntimeError("계산식 렌더링 실패")
            spec.update(label=overlay_plan.get("label", ""), left=overlay_plan["left"],
                        ops=overlay_plan["ops"], result=overlay_plan["result"], icon=overlay_plan.get("icon"))
            out["visual_type"] = "slide_equation"
        elif kind == "bullet_list":
            if not render_bullet_overlay(
                title=overlay_plan.get("title", ""), items=overlay_plan["items"], output_path=str(overlay_png),
            ):
                raise RuntimeError("목록 인포그래픽 렌더링 실패")
            spec.update(title=overlay_plan.get("title", ""), items=overlay_plan["items"])
            out["visual_type"] = "slide_bullet_list"
        elif kind == "stat_chart":
            title, items = overlay_plan.get("title", ""), overlay_plan.get("items") or []
            if overlay_plan.get("use_article_series"):
                from app.utils.statistic_chart_card import extract_numeric_series

                series = capture.get("numeric_series") or extract_numeric_series(str(capture.get("quote") or ""))
                if not series:
                    raise RuntimeError("기사 수치 계열이 없습니다")
                items = [
                    {"label": str(it["label"]), "value": float(it["value"]),
                     "value_text": f"{float(it['value']):g}{it.get('unit') or ''}"}
                    for it in series["items"]
                ]
                title = title or str(series.get("lead_text") or capture.get("source_title") or "")[:28] or "통계"
                spec["source"] = "article_numeric_series"
            if not render_stat_overlay(title=title, items=items, output_path=str(overlay_png)):
                raise RuntimeError("통계 오버레이 렌더링 실패")
            spec.update(title=title, items=items)
            out["visual_type"] = "slide_stat_chart"
        elif kind == "article_card":
            if not capture:
                raise RuntimeError("기사 증거가 없는 장면에 기사 카드가 계획됐습니다")
            if not render_news_clip_overlay(
                publisher=str(capture.get("publisher") or ""),
                headline=str(capture.get("source_title") or ""),
                quote=str(capture.get("quote") or ""), output_path=str(overlay_png),
            ):
                raise RuntimeError("기사 카드 렌더링 실패")
            spec.update(source="article_capture", publisher=str(capture.get("publisher") or ""),
                        headline=str(capture.get("source_title") or ""), quote=str(capture.get("quote") or ""))
            out["visual_type"] = "slide_article_card"
        else:
            raise RuntimeError(f"알 수 없는 오버레이 종류: {kind}")

        self._compose_into_clip(index=index, duration=duration, job_dir=job_dir, out=out,
                                overlay_png=overlay_png, suffix="composed", dim_base=dim_base)
        spec["overlay_png"] = str(overlay_png)
        out["slide_layers"]["overlays"].append(spec)
        out["overlay_type"] = kind

    @staticmethod
    def _banner_schedule(scenes_meta: list, first_index: int | None, target_indexes: set[int]) -> dict[int, dict]:
        """고지 카드를 보여 줄 장면과 구간. 하이라이트 뒤 첫 장면 시작에서 DISCLAIMER_CARD_SECONDS 동안만 화면 전체를 덮는다.

        2026-10-16 운영자 요청: 상단 띠(8초)가 아니라 화면 가운데에 1초쯤 보여 주며 효과음을 넣고 넘어간다(편집점 느낌).
        나레이션은 그대로 흐르므로 영상·음성 길이는 달라지지 않는다."""
        if first_index is None or not scenes_meta or first_index not in target_indexes:
            return {}
        return {first_index: {
            "start_offset": 0.0, "hold_seconds": DISCLAIMER_CARD_SECONDS,
            "fade_in_seconds": 0.06, "fade_out_seconds": 0.12, "hold_to_end": False,
        }}

    def _apply_disclaimer_banner(
        self, *, index: int, duration: float, job_dir: Path, out: dict, schedule: dict | None = None,
    ) -> None:
        card_png = job_dir / f"scene_{index:03d}_banner.png"
        if not render_disclaimer_card(output_path=str(card_png)):
            raise RuntimeError("고지 카드 렌더링 실패")
        schedule = schedule or {
            "start_offset": 0.0, "hold_seconds": DISCLAIMER_CARD_SECONDS,
            "fade_in_seconds": 0.06, "fade_out_seconds": 0.12, "hold_to_end": False,
        }
        self._compose_into_clip(
            index=index, duration=duration, job_dir=job_dir, out=out, overlay_png=card_png,
            suffix="banner", start_offset=schedule["start_offset"], hold_seconds=schedule["hold_seconds"],
            fade_in_seconds=schedule["fade_in_seconds"], fade_out_seconds=schedule["fade_out_seconds"],
            hold_to_end=schedule["hold_to_end"], motion="none",
        )
        out["slide_layers"]["overlays"].append({"type": "disclaimer_card", "overlay_png": str(card_png)})
        out["disclaimer_banner"] = True
        # 장면이 시작하는 순간 편집점 효과음을 넣는다(롱폼 조립이 나레이션 오디오에 합성한다).
        out["sfx_events"] = [{"name": "title_hit", "at": round(float(out.get("start") or 0.0), 3)}]

    def _compose_into_clip(
        self, *, index: int, duration: float, job_dir: Path, out: dict, overlay_png: Path,
        suffix: str, start_offset: float = 0.35, hold_seconds: float | None = None,
        fade_in_seconds: float = 0.35, fade_out_seconds: float = 0.3, hold_to_end: bool = False,
        motion: str = "rise", dim_base: bool = False,
    ) -> None:
        """현재 배경(영상 또는 이미지) 위에 오버레이를 시간에 맞춰 얹은 클립으로 장면을 갱신한다."""
        base_video = out.get("stock_video_path")
        base_image = out.get("image_path")
        base = base_video or base_image
        if not base:
            raise RuntimeError("합성할 배경이 없습니다")
        clip_path = job_dir / f"scene_{index:03d}_{suffix}.mp4"
        if not compose_animated_overlay(
            base_path=str(base), overlay_path=str(overlay_png), output_path=str(clip_path),
            duration=duration, base_is_video=bool(base_video), start_offset=start_offset,
            hold_seconds=hold_seconds, fade_in_seconds=fade_in_seconds,
            fade_out_seconds=fade_out_seconds, hold_to_end=hold_to_end, motion=motion,
            dim_base=dim_base,
        ):
            raise RuntimeError("오버레이 합성 실패")
        out["stock_video_path"] = str(clip_path)
        frame_path = job_dir / f"scene_{index:03d}_{suffix}_frame.png"
        if _extract_first_frame_at(str(clip_path), str(frame_path), min(1.2, max(duration / 2, 0.5))):
            out["image_path"] = str(frame_path)

    def _render_pixabay_scene(
        self, *, index: int, narration: str, duration: float, job_dir: Path,
        used_pixabay_ids: set[int], out: dict, reuse_base: bool = False,
        planned_queries: list[str] | None = None,
    ) -> bool:
        if reuse_base:
            cached = _read_base_cache(job_dir, index, "stock", narration)
            if cached and cached.get("video_path"):
                with _PIXABAY_LOCK:
                    used_pixabay_ids.add(cached.get("pixabay_id"))
                out["stock_video_path"] = cached["video_path"]
                if cached.get("image_path"):
                    out["image_path"] = cached["image_path"]
                out["visual_type"] = "slide_stock_video"
                out["pixabay_id"] = cached.get("pixabay_id")
                out["base_reused"] = True
                return True
        if planned_queries:
            # 계획서의 검색어(장면 맥락을 이미 반영)를 그대로 쓴다. 한국 현장 우선 → 일반 검색 순서.
            direction = {"pexels_query": planned_queries[0], "alt_queries": list(planned_queries[1:])}
        else:
            direction = generate_slide_direction(narration)
        with _PIXABAY_LOCK:
            claimed = set(used_pixabay_ids)
        # 첫 검색어에 맞는 스톡이 없으면 유료 AI 배경으로 넘어가므로, 같은 장면의 다른 각도 검색어를
        # 차례로 시도한다. 적합성 판정은 매번 내레이션 맥락 기준 Claude 선택이 한다.
        primary = str(direction.get("pexels_query") or "").strip()
        queries: list[str] = []
        for query in (f"korea {primary}", primary, *list(direction.get("alt_queries") or [])):
            query = query.strip()
            if query and query.lower() not in {q.lower() for q in queries}:
                queries.append(query)
        queries = queries[:MAX_STOCK_QUERIES]
        if planned_queries:
            # 구체적인 검색어가 모두 빗나가면 일반 검색어로 한 번 더 찾는다(레퍼런스의 스톡은 주제·분위기에 어울리는 일반 영상).
            for generic in _GENERIC_STOCK_QUERIES:
                if generic.lower() not in {q.lower() for q in queries}:
                    queries.append(generic)

        seen_ids: set[int] = set()
        clip = None
        search_log: list[dict] = []
        out["stock_search_log"] = search_log
        for position, query in enumerate(queries):
            try:
                found = _throttled_search(query, min_duration_seconds=int(min(duration, 3)))
            except PixabayVideoUnavailableError:
                if position == 0:
                    raise  # 첫 검색부터 실패하면 기존처럼 장면 오류로 올린다(조용히 유료 폴백하지 않는다).
                logger.warning("슬라이드 장면 %s 대체 검색어 조회 실패, 남은 검색어를 건너뜀", index)
                break
            fresh = [
                c for c in found
                if c["pixabay_id"] not in claimed and c["pixabay_id"] not in seen_ids
            ]
            seen_ids.update(c["pixabay_id"] for c in fresh)
            entry = {"query": query, "found": len(found), "fresh": len(fresh), "selected": False}
            search_log.append(entry)
            if not fresh:
                continue
            # 계획서가 정한 스톡 장면은 레퍼런스처럼 주제·분위기에 어울리는 일반 영상이면 충분하다.
            best_index = (
                select_best_candidate(narration, fresh, relaxed=True, vision=True) if planned_queries
                else select_best_candidate(narration, fresh)
            )
            if best_index is None:
                continue
            candidate = fresh[best_index]
            with _PIXABAY_LOCK:
                # 병렬 처리 중 다른 장면이 같은 클립을 먼저 가져갔으면 이 후보는 쓰지 않는다.
                if candidate["pixabay_id"] in used_pixabay_ids:
                    continue
                used_pixabay_ids.add(candidate["pixabay_id"])
            clip = candidate
            entry["selected"] = True
            out["stock_query"] = query
            break
        if clip is None:
            if any(item["fresh"] for item in search_log):
                out["stock_miss_reason"] = "candidates_rejected_by_selection"
            elif any(item["found"] for item in search_log):
                out["stock_miss_reason"] = "all_candidates_already_used"
            else:
                out["stock_miss_reason"] = "no_search_results"
            return False
        video_path = job_dir / f"scene_{index:03d}_stock.mp4"
        download_clip(clip["download_url"], str(video_path))
        frame_path = job_dir / f"scene_{index:03d}_stock_frame.png"
        _extract_first_frame(str(video_path), str(frame_path))
        out["stock_video_path"] = str(video_path)
        # image_path는 의미 정렬 QA와, longform 영상 클립 처리가 실패할 때의
        # 정지 이미지 안전망 둘 다로 쓰인다.
        if Path(frame_path).is_file():
            out["image_path"] = str(frame_path)
        out["visual_type"] = "slide_stock_video"
        out["pixabay_id"] = clip["pixabay_id"]
        _write_base_cache(
            job_dir, index, "stock", narration, video_path=str(video_path),
            image_path=out.get("image_path"), pixabay_id=clip["pixabay_id"],
        )
        return True

    def _render_kling_scene(
        self, *, index: int, narration: str, duration: float, job_id: int,
        job_dir: Path, out: dict, kling_state: dict, reuse_base: bool = False,
    ) -> None:
        """스톡에 없는 장면: Gemini 배경 정지 이미지를 만들고 Kling으로 움직인다.
        Kling이 실패해도 조용히 넘기지 않고 장면에 사유를 남겨 검수 대상으로 올린다
        (정지 이미지는 longform이 그대로 쓸 수 있는 안전망)."""
        direction = generate_slide_direction(narration)
        self._render_ai_background_scene(
            index=index, narration=narration, job_id=job_id, job_dir=job_dir, out=out,
            reuse_base=reuse_base,
        )
        if not kling_state["provider_loaded"]:
            kling_state["provider_loaded"] = True
            try:
                from app.providers.factory import get_video_provider

                kling_state["provider"] = get_video_provider()
            except Exception:
                logger.exception("Kling 비디오 프로바이더 로드 실패")
        clip_path = job_dir / f"scene_{index:03d}_kling.mp4"
        result = generate_slide_kling_clip(
            video_provider=kling_state["provider"],
            image_path=out["image_path"],
            output_path=str(clip_path),
            narration=narration,
            direction_query=direction.get("pexels_query", ""),
            duration=duration,
            job_id=job_id,
            scene_key=f"slide_kling:{index}",
        )
        if result["ok"]:
            # longform의 사전 렌더 클립 경로(stock_video_path)를 그대로 재사용한다.
            out["stock_video_path"] = str(clip_path)
            out["visual_type"] = "slide_kling_video"
            out["kling_request_id"] = result.get("request_id")
            kling_state["streak"] += 1
        else:
            out["kling_failure_reason"] = result["reason"]
            out["quality_flags"] = list(dict.fromkeys([
                *out.get("quality_flags", []), "kling_failed_static_fallback",
            ]))

    @staticmethod
    def _claim_ai_replacement(state: dict | None) -> bool:
        """영상당 상한 안에서만 스톡 대체 AI 이미지를 허용한다(병렬 장면 처리를 고려한 잠금)."""
        if not state:
            return False
        with state["lock"]:
            if state["used"] >= state["max"]:
                return False
            state["used"] += 1
            return True

    def _render_ai_background_scene(
        self, *, index: int, narration: str, job_id: int, job_dir: Path, out: dict,
        reuse_base: bool = False, photo_brief: str = "",
    ) -> None:
        # 계획서의 촬영 설명이 바뀌면 같은 문장이어도 다른 사진이므로 캐시 키에 함께 넣는다.
        cache_text = f"{narration}|{photo_brief}" if photo_brief else narration
        if reuse_base:
            cached = _read_base_cache(job_dir, index, "bg", cache_text)
            if cached and cached.get("image_path"):
                out["image_path"] = cached["image_path"]
                out["visual_type"] = "slide_ai_background"
                out["base_reused"] = True
                return
        if photo_brief:
            direction = {"pexels_query": photo_brief}
            scene_prompt = photo_brief
        else:
            direction = generate_slide_direction(narration)
            scene_prompt = f"{direction['pexels_query']} — scene context: {narration}"
        image_path = job_dir / f"scene_{index:03d}_bg.png"
        # 2026-10-14 사용자 재현: 압축된 pexels_query만 넘기면 "마트 안" 같은
        # 장소 맥락이 사라져 AI 배경이 엉뚱한 장소로 나왔다. 내레이션 원문을
        # 함께 넘겨 장소/맥락을 보존한다.
        generate_korean_slide_background(
            scene_prompt, str(image_path),
            job_id=job_id, scene_key=f"slide_bg:{index}",
        )
        out["image_path"] = str(image_path)
        out["visual_type"] = "slide_ai_background"
        _write_base_cache(job_dir, index, "bg", cache_text, image_path=str(image_path))
