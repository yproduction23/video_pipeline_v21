import os
import json
import logging
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from dotenv import load_dotenv

_ENV_PATH = next(
    (parent / ".env" for parent in Path(__file__).resolve().parents if (parent / ".env").is_file()),
    None,
)
if _ENV_PATH is not None:
    load_dotenv(_ENV_PATH, override=False)

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse, Response, JSONResponse
from pydantic import BaseModel, Field

from app.workers.shorts_worker import ShortsWorker
from app.workers.keyword_worker import KeywordWorker
from app.workers.script_worker import ScriptWorker, ScriptResearchRequiredError
from app.workers.tts_worker import TtsWorker
from app.workers.images_worker import (
    ImagesWorker,
    ImageProviderCreditRequiredError,
    ImageProviderTemporarilyUnavailableError,
)
from app.workers.longform_worker import LongformWorker
from app.workers.sfx_worker import SfxWorker
from app.workers.bgm_worker import BgmWorker
from app.workers.pronunciation_manager import PronunciationManager
from app.config import APP_MODE, BFL_API_KEY, CLAUDE_MODEL, V5_BFL_ENABLED
from app import runtime_config
from app.utils.budget import load_cost_ledger_summary
from app.utils.image_request_control import ImageRequestHeld
from app.utils.art_direction import compile_editorial_prompt
from app.models.article_evidence import EvidenceCaptureRequest, QuoteCardRequest, UserImageEvidenceRequest
from app.services.article_discovery import ArticleDiscoveryService, ArticleDiscoveryUnavailable
from app.services.evidence_capture import EvidenceCaptureError, EvidenceCaptureService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """서버 시작 자원을 초기화하고 종료 시 브라우저 프로세스를 정리한다."""
    if not BFL_API_KEY:
        logger.warning("BFL_API_KEY가 설정되지 않았습니다. V5 실제 이미지 생성은 비활성입니다.")
    elif V5_BFL_ENABLED:
        logger.info("V5 BFL 이미지 생성 경로가 활성화되었습니다.")
    try:
        result = PronunciationManager.get_instance().initialize()
        logger.info("발음 사전 초기화: %s", result)
    except Exception as exc:
        logger.warning("발음 사전 초기화 실패 (TTS는 정상 작동): %s", exc)

    try:
        yield
    finally:
        EvidenceCaptureService.shutdown()


app = FastAPI(title="AI Video Pipeline Workers", version="0.5.0", lifespan=lifespan)

DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

shorts_worker = None
keyword_worker = None
script_worker = None
tts_worker = None
images_worker = None
longform_worker = None
sfx_worker = None
bgm_worker = None
evidence_capture_service = None


def get_shorts_worker():
    global shorts_worker
    if shorts_worker is None:
        shorts_worker = ShortsWorker()
    return shorts_worker

def get_keyword_worker():
    global keyword_worker
    if keyword_worker is None:
        keyword_worker = KeywordWorker()
    return keyword_worker

def get_script_worker():
    global script_worker
    if script_worker is None:
        script_worker = ScriptWorker()
    return script_worker

def get_tts_worker():
    global tts_worker
    if tts_worker is None:
        tts_worker = TtsWorker()
    return tts_worker

def get_images_worker():
    global images_worker
    if images_worker is None:
        images_worker = ImagesWorker()
    return images_worker

def get_longform_worker():
    global longform_worker
    if longform_worker is None:
        longform_worker = LongformWorker()
    return longform_worker

def get_sfx_worker():
    global sfx_worker
    if sfx_worker is None:
        sfx_worker = SfxWorker()
    return sfx_worker

def get_bgm_worker():
    global bgm_worker
    if bgm_worker is None:
        bgm_worker = BgmWorker()
    return bgm_worker


def get_evidence_capture_service() -> EvidenceCaptureService:
    global evidence_capture_service
    if evidence_capture_service is None:
        evidence_capture_service = EvidenceCaptureService()
    return evidence_capture_service


@app.get("/health")
def health():
    return {"status": "ok", "mode": APP_MODE, "claude_model": CLAUDE_MODEL}


@app.get("/providers/status")
def provider_status():
    return {
        "youtube": {"configured": bool(os.environ.get("YOUTUBE_API_KEY", "").strip()), "provider": "YouTube Data API v3"},
        "anthropic": {"configured": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())},
        "elevenlabs": {"configured": bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())},
        "gemini": {
            "image_model": "gemini-3-pro-image",
            "quality_tier": runtime_config.value("image_quality_tier"),
        },
        "fal": {"configured": bool(os.environ.get("FAL_KEY", "").strip())},
    }


@app.get("/workers/jobs/{job_id}/cost-ledger")
def worker_cost_ledger(job_id: int):
    """실제 FastAPI provider 요청 원장을 Spring/UI에 노출한다."""
    if job_id <= 0:
        raise HTTPException(status_code=422, detail="job_id는 양수여야 합니다.")
    return load_cost_ledger_summary(job_id)


# ============================
# 신규 — 파이프라인 파라미터 실시간 조정 API
#
# TTS 속도, ElevenLabs 목소리 설정, BGM 볼륨, 자막 크기, Kling 인트로
# 길이 등을 여기로 GET/POST하면 코드 수정이나 Docker 재빌드 없이
# 다음 Job부터 바로 반영됩니다.
# ============================
class PipelineConfigUpdate(BaseModel):
    tts_speed: Optional[float] = None
    chars_per_minute: Optional[int] = None
    scene_duration_sec: Optional[float] = None
    subtitle_max_chars: Optional[int] = None
    subtitle_frame_rate: Optional[float] = None
    subtitle_start_frame_policy: Optional[str] = None
    subtitle_font_size: Optional[int] = None
    subtitle_theme: Optional[str] = None
    image_headline_overlay: Optional[bool] = None
    image_provider: Optional[str] = None
    image_quality_tier: Optional[str] = None
    pro_image_max_scenes: Optional[int] = None
    gemini_pro_batch_enabled: Optional[bool] = None
    gemini_pro_batch_fallback_enabled: Optional[bool] = None
    gemini_service_tier: Optional[str] = None
    gemini_pro_max_attempts: Optional[int] = None
    gemini_pro_retry_base_seconds: Optional[float] = None
    gemini_pro_request_delay_seconds: Optional[float] = None
    gemini_parallel_enabled: Optional[bool] = None
    gemini_max_concurrency: Optional[int] = None
    gemini_retry_max: Optional[int] = None
    gemini_scene_request_limit: Optional[int] = None
    gemini_rpm_soft_cap: Optional[int] = None
    gemini_adaptive_backoff_enabled: Optional[bool] = None
    longform_scene_max_workers: Optional[int] = None
    visual_qa_enabled: Optional[bool] = None
    visual_qa_max_scenes: Optional[int] = None
    elevenlabs_voice_id: Optional[str] = None
    elevenlabs_stability: Optional[float] = None
    elevenlabs_similarity_boost: Optional[float] = None
    elevenlabs_style: Optional[float] = None
    tts_model_intro: Optional[str] = None
    tts_model_body: Optional[str] = None
    tts_stability_intro: Optional[float] = None
    tts_stability_body: Optional[float] = None
    tts_cer_threshold: Optional[float] = None
    tts_max_retries: Optional[int] = None
    tts_postprocess_enabled: Optional[bool] = None
    tts_sentence_pause_ms: Optional[int] = None
    tts_paragraph_pause_ms: Optional[int] = None
    bgm_volume: Optional[float] = None
    intro_motion_seconds_short: Optional[float] = None
    intro_motion_seconds_long: Optional[float] = None
    intro_motion_short_threshold: Optional[float] = None
    intro_kling_max_clips: Optional[int] = None
    img_cost_flash_1k_usd: Optional[float] = None
    img_cost_flash_2k_usd: Optional[float] = None
    img_cost_pro_2k_usd: Optional[float] = None
    kling_cost_per_clip_usd: Optional[float] = None
    usd_krw: Optional[float] = None
    max_budget_per_video_krw: Optional[int] = None
    budget_retry_buffer_pct: Optional[float] = None
    keyword_score_weight_multiple: Optional[float] = None
    keyword_score_weight_velocity: Optional[float] = None
    keyword_score_weight_like: Optional[float] = None
    keyword_score_weight_comment: Optional[float] = None
    keyword_like_rate_benchmark: Optional[float] = None
    keyword_comment_rate_benchmark: Optional[float] = None
    render_speech_bubbles: Optional[bool] = None
    render_article_evidence: Optional[bool] = None
    article_evidence_auto_enabled: Optional[bool] = None
    evidence_max_scenes: Optional[int] = None
    evidence_max_searches_per_scene: Optional[int] = None
    evidence_min_sentence_similarity: Optional[float] = None
    bubble_font_max_px: Optional[int] = None
    bubble_font_min_px: Optional[int] = None
    subtitle_safe_area_pct: Optional[float] = None
    info_surface_enabled: Optional[bool] = None
    info_surface_mode_default: Optional[str] = None
    info_surface_quad_min_confidence: Optional[float] = None
    info_surface_texture_strength: Optional[float] = None
    info_surface_baked_enabled: Optional[bool] = None
    script_house_style_enabled: Optional[bool] = None
    script_pattern_numbers_enabled: Optional[bool] = None
    script_pattern_analogy_enabled: Optional[bool] = None
    script_pattern_fake_question_enabled: Optional[bool] = None
    script_pattern_llm_labeling_enabled: Optional[bool] = None


@app.get("/pipeline/config")
def get_pipeline_config():
    """현재 적용 중인 파이프라인 파라미터 전체를 반환합니다."""
    return runtime_config.get()


@app.get("/pipeline/motion-policy")
def get_intro_motion_policy():
    """편집 화면에 노출할 비밀값 없는 초반 Kling 정책만 반환합니다."""
    return {
        "enabled": bool(runtime_config.value("intro_motion_enabled")),
        "short_window_seconds": float(runtime_config.value("intro_motion_seconds_short")),
        "long_window_seconds": float(runtime_config.value("intro_motion_seconds_long")),
        "long_threshold_seconds": float(runtime_config.value("intro_motion_short_threshold")),
        "clip_count_cap": int(runtime_config.value("intro_motion_clip_count")),
        "clip_seconds": min(float(runtime_config.value("intro_motion_clip_seconds")), 5.0),
    }


@app.post("/pipeline/config")
def update_pipeline_config(update: PipelineConfigUpdate):
    """전달된 파라미터만 즉시 갱신합니다. (다음 Job부터 바로 반영, 재빌드 불필요)"""
    try:
        updated = runtime_config.update(**update.dict(exclude_none=True))
        return {"status": "ok", "config": updated}
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/pipeline/config/reset")
def reset_pipeline_config():
    """환경변수 기본값으로 되돌립니다."""
    return {"status": "ok", "config": runtime_config.reset_to_env_defaults()}


class ArticleDiscoveryRequest(BaseModel):
    query: str
    terms: List[str] = []
    limit: int = 10


@app.post("/workers/evidence/discover")
def discover_article_candidates(request: ArticleDiscoveryRequest):
    """Return attributable public-news candidates; this does not capture them."""
    try:
        candidates = ArticleDiscoveryService().discover(request.query, request.terms, request.limit)
        return {"query": request.query, "candidates": [item.model_dump() for item in candidates]}
    except ArticleDiscoveryUnavailable as exc:
        raise HTTPException(503, str(exc))
    except Exception as exc:
        logger.exception("article discovery failed")
        raise HTTPException(502, f"article discovery failed: {exc}")


@app.post("/workers/evidence/capture")
def capture_article_evidence(request: EvidenceCaptureRequest):
    """Capture one public HTML article quote using DOM text coordinates."""
    try:
        return get_evidence_capture_service().capture_dom(request).model_dump(mode="json")
    except EvidenceCaptureError as exc:
        raise HTTPException(exc.status_code, str(exc))


@app.post("/workers/evidence/capture-upload")
async def capture_uploaded_article_evidence(
    image: UploadFile = File(..., description="사람이 확보한 기사 또는 차트 캡처 이미지"),
    job_id: int = Form(...),
    source_url: str = Form(...),
    quote: str = Form(...),
    target_bbox_json: str = Form(...),
    quote_bboxes_json: str = Form(...),
    source_title: str = Form(default=""),
    publisher: str = Form(default=""),
    published_at: str | None = Form(default=None),
    key_phrase: str | None = Form(default=None),
    key_phrase_bboxes_json: str = Form(default="[]"),
):
    """OCR 추측 없이 사용자가 검증한 좌표로 업로드 캡처를 등록한다."""
    try:
        payload = UserImageEvidenceRequest(
            job_id=job_id,
            source_url=source_url,
            quote=quote,
            source_title=source_title,
            publisher=publisher,
            published_at=published_at,
            target_bbox=json.loads(target_bbox_json),
            quote_bboxes=json.loads(quote_bboxes_json),
            key_phrase=key_phrase,
            key_phrase_bboxes=json.loads(key_phrase_bboxes_json),
        )
        return get_evidence_capture_service().capture_user_image(payload, await image.read()).model_dump(mode="json")
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"invalid verified evidence input: {exc}") from exc
    except EvidenceCaptureError as exc:
        raise HTTPException(exc.status_code, str(exc))


@app.post("/workers/evidence/render-quote-card")
def render_quote_card(request: QuoteCardRequest):
    """Render a clearly labelled editorial quote card, never a fake article."""
    try:
        return get_evidence_capture_service().render_quote_card(request).model_dump(mode="json")
    except EvidenceCaptureError as exc:
        raise HTTPException(exc.status_code, str(exc))


@app.get("/workers/quality/{job_id}")
def get_quality_report(job_id: int, stage: Optional[str] = None):
    """Return persisted deterministic quality-gate results for a job."""
    quality_dir = DATA_DIR / "jobs" / str(job_id) / "quality"
    if not quality_dir.exists():
        raise HTTPException(404, "quality report not found")
    allowed = {"tts", "images", "longform"}
    stages = [stage] if stage else sorted(allowed)
    if stage and stage not in allowed:
        raise HTTPException(400, "invalid quality report stage")
    reports = {}
    for name in stages:
        path = quality_dir / f"{name}.json"
        if path.exists():
            try:
                reports[name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                reports[name] = {"error": "unreadable quality report"}
    if not reports:
        raise HTTPException(404, "quality report not found")
    return {"job_id": job_id, "reports": reports}


# ============================
# Phase 2 — 쇼츠
# ============================
class ShortsSegment(BaseModel):
    index: int
    text: Optional[str] = None
    start: float
    end: float
    reason: Optional[str] = None

class ShortsCutRequest(BaseModel):
    source_video_path: str
    segments: List[ShortsSegment]
    job_id: Optional[int] = 0

@app.post("/workers/shorts/analyze")
async def analyze_shorts(file: UploadFile = File(...), shorts_count: int = Query(default=3), job_id: int = Query(default=0)):
    if not file.filename or not file.filename.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
        raise HTTPException(400, "지원하지 않는 형식.")
    job_dir = DATA_DIR / "jobs" / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower() or ".mp4"
    source_path = job_dir / f"source{ext}"
    content = await file.read()
    with open(source_path, "wb") as f: f.write(content)
    try:
        analysis = get_shorts_worker().analyze(str(source_path), shorts_count=shorts_count)
        # Whisper provides timestamps; the LLM turns each timestamped chunk
        # into a concise, readable scene script without changing its range.
        analysis["transcript_segments"] = get_shorts_worker().enhance_scene_script(
            analysis["transcript_segments"]
        )
        analysis["transcript"] = " ".join(
            scene.get("text", "") for scene in analysis["transcript_segments"]
        )
    except Exception as e:
        raise HTTPException(500, f"분석 실패: {str(e)}")
    return {
        "job_id": job_id,
        "source_video_path": str(source_path),
        "transcript": analysis["transcript"],
        "transcript_segments": analysis["transcript_segments"],
        "words": analysis["words"],
        "suggested_segments": analysis["suggested_segments"],
        "total_duration": analysis["total_duration"],
    }

class ShortsScene(BaseModel):
    index: int
    text: str
    start: float
    duration: float

class ShortsExtractScenariosRequest(BaseModel):
    job_id: int
    scenes: List[ShortsScene]

class ShortsNormalizeScenesRequest(BaseModel):
    source_video_path: str
    scenes: List[ShortsScene]

@app.post("/workers/shorts/normalize-scenes")
async def normalize_shorts_scenes(request: ShortsNormalizeScenesRequest):
    source = Path(request.source_video_path)
    if not source.exists():
        raise HTTPException(404, f"Source video not found: {source}")
    try:
        normalized = get_shorts_worker().normalize_scenes(
            [scene.dict() for scene in request.scenes], str(source)
        )
        return {"source_video_path": str(source), "scenes": normalized}
    except Exception as e:
        raise HTTPException(500, f"Scene timeline normalization failed: {str(e)}")

class ShortsCutMergeRequest(BaseModel):
    source_video_path: str
    segments: List[ShortsSegment]
    job_id: Optional[int] = 0
    output_path: str

@app.post("/workers/shorts/extract-scenarios")
async def extract_scenarios(request: ShortsExtractScenariosRequest):
    try:
        scenes_list = [s.dict() for s in request.scenes]
        analysis = get_shorts_worker().extract_scenarios(scenes_list, job_id=request.job_id)
        return analysis
    except Exception as e:
        raise HTTPException(500, f"시나리오 추출 실패: {str(e)}")

@app.post("/workers/shorts/cut-merge")
async def cut_merge_shorts(request: ShortsCutMergeRequest):
    source = Path(request.source_video_path)
    if not source.exists():
        raise HTTPException(404, f"원본 영상 없음: {source}")
    try:
        segments_list = [s.dict() for s in request.segments]
        clip = get_shorts_worker().cut_and_merge(str(source), segments_list, request.output_path)
        return {"job_id": request.job_id, "clip": clip}
    except Exception as e:
        raise HTTPException(500, f"병합 자르기 실패: {str(e)}")

@app.post("/workers/shorts/cut")
async def cut_shorts(request: ShortsCutRequest):
    source = Path(request.source_video_path)
    if not source.exists(): raise HTTPException(404, f"원본 영상 없음: {source}")
    job_id = request.job_id or 0
    output_dir = DATA_DIR / "jobs" / str(job_id) / "shorts"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        clips = get_shorts_worker().cut(str(source), [s.dict() for s in request.segments], str(output_dir))
    except Exception as e:
        raise HTTPException(500, f"자르기 실패: {str(e)}")
    return {"job_id": job_id, "clips": clips}

@app.get("/workers/shorts/download")
def download_clip(path: str):
    if not os.path.exists(path): raise HTTPException(404, "파일 없음")
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))


# ============================
# Phase 3-1 — 키워드
# ============================
class KeywordSearchRequest(BaseModel):
    seed: str = ""
    limit: int = 5
    category: str = "CUSTOM"
    outperformer_count: int = 1
    job_id: Optional[int] = 0
    autonomy_mode: Optional[str] = None

@app.post("/workers/keyword/search")
def keyword_search(request: KeywordSearchRequest):
    try:
        return get_keyword_worker().search(
            category=request.category,
            seed=request.seed,
            limit=request.limit,
            outperformer_count=request.outperformer_count,
            job_id=request.job_id or 0,
            autonomy_mode=request.autonomy_mode
        )
    except Exception as e:
        raise HTTPException(500, f"키워드 탐색 실패: {str(e)}")


class TrendingRequest(BaseModel):
    keyword: str = ""
    limit: int = 10
    recent_hours: Optional[int] = None
    ranking: str = "evidence"
    min_subscribers: Optional[int] = None

@app.post("/workers/trending/youtube")
def trending_youtube(request: TrendingRequest):
    try:
        from app.providers.factory import get_trending_video_analyzer
        analyzer = get_trending_video_analyzer()
        if request.ranking not in {"evidence", "outperformer", "large_channel"}:
            raise HTTPException(400, "ranking must be evidence, outperformer, or large_channel")
        if request.min_subscribers is not None and request.min_subscribers < 0:
            raise HTTPException(400, "min_subscribers must be zero or greater")
        videos = analyzer.collect(
            category="",
            seed=request.keyword,
            limit=max(1, min(request.limit, 20)),
            recent_hours=request.recent_hours,
            ranking=request.ranking,
            min_subscribers=request.min_subscribers,
        )
        return {"videos": [v.__dict__ for v in videos]}
    except Exception as e:
        raise HTTPException(500, f"트렌딩 비디오 검색 실패: {str(e)}")


@app.get("/workers/youtube/channels/benchmark")
async def youtube_channel_benchmark(
    channel_ids: str | None = None,
):
    """
    경쟁 채널 공개 지표 벤치마크.
    - channel_ids: 쉼표로 구분된 YouTube channel_id 목록 (선택)
    - 응답: 채널별 구독자수(근사), 평균조회수, 업로드 간격
    - 쿼터 비용: 채널당 ~3 유닛 (Redis 캐시 6hr)
    """
    if channel_ids is None:
        raise HTTPException(status_code=400, detail="channel_ids가 필요합니다.")

    try:
        from app.providers.real.trending import YouTubeTrendingAnalyzer
        analyzer = YouTubeTrendingAnalyzer()

        ids = [c.strip() for c in channel_ids.split(",") if c.strip()]
        data = analyzer.get_channel_benchmarks(channel_ids=ids)
        return {"status": "ok", "channels": data}
    except Exception as e:
        raise HTTPException(500, f"채널 벤치마크 조회 실패: {str(e)}")


def _discovery_http_error(exc) -> HTTPException:
    status = {"NO_API_KEY": 503, "QUOTA": 429, "UNSUPPORTED_CATEGORY": 404, "NOT_FOUND": 404}.get(exc.code, 502)
    return HTTPException(status_code=status, detail=str(exc))


@app.get("/workers/discovery/hot-keywords")
def discovery_hot_keywords(category: str = "ALL", window: str = "48h"):
    """카테고리별 급상승 영상에서 핫키워드를 집계한다(48h/7d)."""
    from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

    try:
        return YouTubeDiscovery().hot_keywords(category, window)
    except DiscoveryError as exc:
        raise _discovery_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/workers/discovery/recent-uploads")
def discovery_recent_uploads(channel_ids: str, days: int = 7):
    """벤치마크 채널의 최근 업로드를 시간당 조회수·채널 평균 대비 배수와 함께 반환한다."""
    from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

    ids = [item.strip() for item in channel_ids.split(",") if item.strip()]
    try:
        return YouTubeDiscovery().recent_uploads(ids, days)
    except DiscoveryError as exc:
        raise _discovery_http_error(exc) from exc


@app.get("/workers/discovery/video/{video_id}")
def discovery_video(video_id: str):
    from app.services.discovery.youtube_discovery import DiscoveryError, YouTubeDiscovery

    try:
        return YouTubeDiscovery().fetch_video(video_id)
    except DiscoveryError as exc:
        raise _discovery_http_error(exc) from exc


class BenchmarkAnalyzeRequest(BaseModel):
    video: dict


@app.post("/workers/benchmark/analyze")
def benchmark_analyze(request: BenchmarkAnalyzeRequest):
    from app.services.discovery import benchmark_analysis

    try:
        return benchmark_analysis.analyze_benchmark(benchmark_analysis.claude_llm_call, request.video)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"벤치마크 분석 실패: {exc}") from exc


@app.get("/workers/youtube/channels/resolve")
def resolve_youtube_channel(channel_ref: str):
    """채널 ID 또는 @handle을 Spring 저장 전에 실제 채널로 검증한다."""
    from app.providers.real.trending import YouTubeTrendingAnalyzer

    try:
        candidate = YouTubeTrendingAnalyzer().resolve_channel(channel_ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if candidate is None:
        raise HTTPException(status_code=404, detail="존재하는 YouTube 채널을 찾지 못했습니다.")
    return {"status": "ok", "channel": candidate}


class ChannelCandidateSearchRequest(BaseModel):
    query: str
    limit: int = 3


@app.post("/workers/youtube/channels/search-candidates")
def search_youtube_channel_candidates(request: ChannelCandidateSearchRequest):
    """일반 채널명으로 사람이 확정할 후보를 최대 3개 조회한다."""
    from app.providers.real.trending import YouTubeTrendingAnalyzer

    try:
        candidates = YouTubeTrendingAnalyzer().search_channel_candidates(
            request.query,
            limit=min(max(request.limit, 1), 3),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {
        "status": "ok",
        "query": request.query,
        "candidates": candidates,
    }


class ManualKeywordContextRequest(BaseModel):
    keyword: str
    recent_hours: int = 3
    category: str = "KOSPI"


ALLOWED_NEWS_HOURS = {1, 3, 24}


def _build_keyword_news_preview(keyword: str, hours: int, category: str) -> Dict[str, Any]:
    normalized_keyword = keyword.strip()
    if not normalized_keyword:
        raise HTTPException(400, "keyword is required")
    if hours not in ALLOWED_NEWS_HOURS:
        raise HTTPException(
            status_code=400,
            detail=f"hours는 {sorted(ALLOWED_NEWS_HOURS)} 중 하나여야 합니다.",
        )

    normalized_category = (category or "KOSPI").strip().upper()
    analyzer_category = normalized_category if normalized_category in {
        "KOSPI", "KOSDAQ", "US_STOCKS", "INDIVIDUAL_STOCK", "ASSOCIATED_STOCKS",
    } else ""
    try:
        from app.providers.factory import get_trending_video_analyzer
        from app.workers.news_keyword_extractor import NewsKeywordExtractor

        videos = get_trending_video_analyzer().collect(
            category=analyzer_category,
            seed=normalized_keyword,
            limit=12,
            recent_hours=hours,
        )
        recent_videos = [
            video.__dict__ for video in videos if (video.hours_since_publish or 999) <= hours
        ]
        extractor = NewsKeywordExtractor()
        recent_news = extractor.search_recent_news(
            normalized_keyword,
            max_age_hours=hours,
            outlet_filter=True,
        )
        if normalized_category == "US_STOCKS":
            recent_news = extractor.search_recent_news_us_market(
                normalized_keyword,
                max_age_hours=hours,
            ) + recent_news

        deduplicated_news = []
        seen_news = set()
        for article in recent_news:
            dedupe_key = str(article.get("url") or article.get("title") or "").strip()
            if not dedupe_key or dedupe_key in seen_news:
                continue
            seen_news.add(dedupe_key)
            deduplicated_news.append(article)

        return {
            "keyword": normalized_keyword,
            "category": normalized_category,
            "windowHours": hours,
            "recentNews": deduplicated_news,
            "recentVideos": recent_videos,
            "evidenceStatus": "confirmed" if deduplicated_news or recent_videos else "not_found",
            "outletFilter": True,
            "disclaimer": "뉴스와 공개 영상은 최신성 확인용 근거이며, 특정 뉴스가 시장 움직임을 유발했다는 인과관계는 표시하지 않습니다.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("수동 키워드 최신 근거 조회 실패: %s", type(exc).__name__)
        raise HTTPException(500, "수동 키워드 최신 근거 조회에 실패했습니다.") from exc


@app.post("/workers/keyword/manual-context")
def manual_keyword_context(request: ManualKeywordContextRequest):
    """Fresh public evidence that lets an operator validate a manual topic."""
    return _build_keyword_news_preview(
        request.keyword,
        request.recent_hours,
        request.category,
    )


@app.get("/workers/keyword-news-preview")
def keyword_news_preview(keyword: str, hours: int = 3, category: str = "KOSPI"):
    """운영 점검과 관리자 호출을 위한 긴급뉴스 미리보기 경로."""
    return _build_keyword_news_preview(keyword, hours, category)


class KeywordMindMapRequest(BaseModel):
    keyword: str
    videos: List[Dict[str, Any]] = []


class KeywordPlanRequest(BaseModel):
    mode: str = "MANUAL"
    keywords: List[str]
    metrics: List[Dict[str, Any]] = []
    market: str = "KR"


@app.post("/ai/keyword-mindmap")
def keyword_mindmap(request: KeywordMindMapRequest):
    """태그·제목 기반 1차 링과 캐시된 Claude 확장 2차 링을 돌려준다."""
    try:
        from app.workers.keyword_planning import build_mindmap
        return build_mindmap(request.keyword, request.videos)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"키워드 마인드맵 생성 실패: {str(exc)}")


@app.post("/ai/keyword-plan")
def keyword_plan(request: KeywordPlanRequest):
    """선택 키워드와 원본 지표를 토대로 정확히 3개의 기획안을 반환한다."""
    try:
        from app.workers.keyword_planning import build_keyword_plans
        return build_keyword_plans(request.mode, request.keywords, request.metrics, request.market)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"키워드 기획 생성 실패: {str(exc)}")


@app.post("/workers/overlay/preview")
async def overlay_preview(
    image: UploadFile = File(...),
    name: str = Form("코스피"),
    value: float = Form(...),
    change: float = Form(...),
    change_pct: float = Form(...),
    market: str = Form("kr"),
    placement_mode: str = Form("anchor"),
    anchor: str = Form("top_right"),
    margin: int = Form(40),
    x: int = Form(0),
    y: int = Form(0),
):
    """Render a verified data card over a supplied image for local QA."""
    from app.utils.stock_overlay import Anchor, IndexData, Market, compose_on_image, render_index_card

    raw = await image.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(413, "image is larger than 20MB")
    preview_dir = DATA_DIR / "overlay_previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(raw).hexdigest()[:16]
    base_path = preview_dir / f"{token}_base.png"
    card_path = preview_dir / f"{token}_card.png"
    output_path = preview_dir / f"{token}_composited.png"
    base_path.write_bytes(raw)
    try:
        data = IndexData(name=name, value=value, change=change, change_pct=change_pct, market=Market(market.lower()))
        render_index_card(data, str(card_path), scale=2)
        if placement_mode.lower() == "pixel":
            compose_on_image(str(base_path), str(card_path), str(output_path), xy=(x, y))
        else:
            compose_on_image(
                str(base_path), str(card_path), str(output_path),
                anchor=Anchor(anchor.lower()), margin=max(0, margin),
            )
        return FileResponse(str(output_path), media_type="image/png", filename="overlay_preview.png")
    except (ValueError, OSError) as exc:
        raise HTTPException(400, f"overlay preview failed: {exc}") from exc


# ============================
# Phase 3-2 — 스크립트
# ============================
class ScriptGenerateRequest(BaseModel):
    keyword: str
    target_minutes: int = 20
    category: str = "CUSTOM"
    job_id: Optional[int] = 0
    voice_id: Optional[str] = None
    market_data: Optional[dict] = None  # KeywordWorker에서 전달된 market_snapshot
    candidate_evidence: Optional[dict] = None  # 선택 후보의 뉴스·YouTube 근거
    autonomy_mode: Optional[str] = None
    content_nature: Optional[str] = None  # FACTUAL | EXPLAINER | STORY (없으면 FACTUAL)

    # 숫자 카드·차트는 명시적으로 켠 레거시 작업에서만 사용한다.
    data_visuals_enabled: bool = False
    # Uses the product's original house style.  Named-channel imitation is not
    # accepted as a profile; future approved profiles remain opt-in here.
    storytelling_profile: str = "original_finance_storyteller_v1"


class ScriptQualityGateRequest(BaseModel):
    script: str
    format: Literal["shorts", "longform"] = "longform"
    verified_facts: list[dict] = Field(default_factory=list)
    reference_texts: list[str] = Field(default_factory=list)


class ScriptFlowRevalidateRequest(BaseModel):
    script: str
    narrative_plan: dict = Field(default_factory=dict)
    previous_flow: dict = Field(default_factory=dict)

@app.post("/workers/script/generate")
def script_generate(request: ScriptGenerateRequest):
    try:
        return get_script_worker().generate(
            keyword=request.keyword,
            target_minutes=request.target_minutes,
            category=request.category,
            market_data=request.market_data,
            job_id=request.job_id or 0,
            data_visuals_enabled=request.data_visuals_enabled,
            storytelling_profile=request.storytelling_profile,
            voice_id=request.voice_id,
            autonomy_mode=request.autonomy_mode,
            candidate_evidence=request.candidate_evidence,
            content_nature=request.content_nature,
        )
    except ScriptResearchRequiredError as exc:
        return JSONResponse(status_code=422, content={
            "error_code": "SCRIPT_RESEARCH_REQUIRED",
            "message": str(exc),
            "missing_terms": exc.missing_terms,
            "recoverable": True,
        })
    except Exception as e:
        raise HTTPException(500, f"스크립트 생성 실패: {str(e)}")


@app.post("/workers/script/quality-gate")
def script_quality_gate(request: ScriptQualityGateRequest):
    """수동 편집 대본도 확정 전 동일한 결정론 하드 게이트를 통과시킨다."""
    from app.utils.quality_gate import assess_script_house_style

    return assess_script_house_style(
        request.script,
        format_name=request.format,
        verified_facts=request.verified_facts,
        reference_texts=request.reference_texts,
        enabled=bool(runtime_config.value("script_house_style_enabled")),
        llm_labeling_enabled=bool(runtime_config.value("script_pattern_llm_labeling_enabled")),
        number_traceability_required=bool(runtime_config.value("script_pattern_numbers_enabled")),
    )


@app.post("/workers/script/flow-revalidate")
def script_flow_revalidate(request: ScriptFlowRevalidateRequest):
    """기존 Claude 의미 검토를 보존하고 현재 결정론 계약만 다시 검사한다."""
    import json
    from app.utils.flow_qa import review_flow

    previous = request.previous_flow or {}
    if not str(previous.get("method") or "").startswith("claude"):
        raise HTTPException(409, "이전 Claude 흐름 검토가 없어 결정론 재검증만으로 승인할 수 없습니다.")
    semantic = {
        "passed": not any(bool(previous.get(field)) for field in (
            "question_answer_issues", "repetition_issues", "ending_issue",
            "transition_issues", "rhythm_issues",
        )),
        "question_answer_issues": previous.get("question_answer_issues") or [],
        "repetition_issues": previous.get("repetition_issues") or [],
        "ending_issue": previous.get("ending_issue"),
        "transition_issues": previous.get("transition_issues") or [],
        "rhythm_issues": previous.get("rhythm_issues") or [],
        "revision_instruction": previous.get("revision_instruction") or "",
    }
    result = review_flow(
        lambda *_args: json.dumps(semantic, ensure_ascii=False),
        script=request.script,
        narrative_plan=request.narrative_plan or {},
    )
    result["revalidated_from"] = previous.get("method")
    result["revalidation_method"] = "previous_claude_semantics_plus_current_deterministic"
    return result


# ============================
# Phase 3-3 — TTS
# ============================
class TtsGenerateRequest(BaseModel):
    script: str
    voice_id: str = "default_ko"
    job_id: Optional[int] = 0
    tts_speed: Optional[float] = None  # 생략 시 runtime_config의 현재 기본값 사용
    target_seconds: Optional[float] = None
    autonomy_mode: Optional[str] = None


class TtsPreviewRequest(BaseModel):
    voice_id: str
    text: str

@app.post("/workers/tts/generate")
def tts_generate(request: TtsGenerateRequest):
    try:
        return get_tts_worker().synthesize(
            request.script, request.voice_id, request.job_id or 0,
            tts_speed=request.tts_speed,
            target_seconds=request.target_seconds,
            autonomy_mode=request.autonomy_mode,
        )
    except Exception as e:
        raise HTTPException(500, f"TTS 생성 실패: {str(e)}")

@app.get("/workers/tts/download")
def download_tts(path: str):
    if not os.path.exists(path): raise HTTPException(404, "파일 없음")
    return FileResponse(path, media_type="audio/mpeg", filename=os.path.basename(path))


@app.get("/workers/tts/voices")
def get_elevenlabs_voices():
    """ElevenLabs 계정에서 사용 가능한 모든 성우 목소리 목록 조회"""
    # Keep a stable 21-voice catalog visible in the UI even before an API key is configured.
    fallback = [
        {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel (여성 · 차분한 설명)", "category": "premade"},
        {"voice_id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi (여성 · 자신감 있는 진행)", "category": "premade"},
        {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella (여성 · 따뜻한 내레이션)", "category": "premade"},
        {"voice_id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli (여성 · 친근한 교육)", "category": "premade"},
        {"voice_id": "ThT5KcBeYPX3keUQqHPh", "name": "Dorothy (여성 · 밝은 뉴스)", "category": "premade"},
        {"voice_id": "XrExE9yKIg1WjnnlVkGX", "name": "Matilda (여성 · 안정적인 해설)", "category": "premade"},
        {"voice_id": "jBpfuIE2acCO8z3wKNLl", "name": "Gigi (여성 · 활기찬 진행)", "category": "premade"},
        {"voice_id": "jsCqWAovK2LkecY7zXl4", "name": "Freya (여성 · 부드러운 시사)", "category": "premade"},
        {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni (남성 · 신뢰감 있는 해설)", "category": "premade"},
        {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh (남성 · 깊은 저음)", "category": "premade"},
        {"voice_id": "VR6AewLTigWG4xSOukaG", "name": "Arnold (남성 · 다큐멘터리)", "category": "premade"},
        {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam (남성 · 금융 뉴스)", "category": "premade"},
        {"voice_id": "yoZ06aMxZJJ28mfd3POQ", "name": "Sam (남성 · 차분한 분석)", "category": "premade"},
        {"voice_id": "flq6f7yk4E4fJM5XTYuZ", "name": "Michael (남성 · 전문 해설)", "category": "premade"},
        {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel (남성 · 영국식 뉴스)", "category": "premade"},
        {"voice_id": "N2lVS1w4EtoT3dr4eOWO", "name": "Callum (남성 · 시사 토론)", "category": "premade"},
        {"voice_id": "IKne3meq5aSn9XLyUdCD", "name": "Charlie (남성 · 선명한 전달)", "category": "premade"},
        {"voice_id": "SAz9YHcvj6GT2YYXdXww", "name": "River (중성 · 차분한 정보)", "category": "premade"},
        {"voice_id": "JBFqnCBsd6RMkjVDRZzb", "name": "George (남성 · 따뜻한 스토리텔러)", "category": "premade"},
        {"voice_id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Alice (여성 · 몰입감 있는 교육)", "category": "premade"},
        {"voice_id": "pFZP5JQG7iQjIQuC4Bku", "name": "Liam (남성 · 또렷한 금융 해설)", "category": "premade"},
    ]
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        return fallback
    try:
        import requests
        resp = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            voices = data.get("voices", [])
            return [
                {
                    "voice_id": v.get("voice_id"),
                    "name": v.get("name"),
                    "category": v.get("category"),
                    "description": v.get("description") or f"{v.get('labels', {}).get('accent', '')} {v.get('labels', {}).get('gender', '')}",
                    "preview_url": v.get("preview_url")
                }
                for v in voices
            ]
        else:
            logger.warning(f"ElevenLabs Voices API 실패: {resp.status_code} {resp.text}")
            return fallback
    except Exception as e:
        logger.error(f"ElevenLabs 목소리 조회 중 오류: {e}")
        return fallback


@app.post("/workers/tts/preview")
def preview_elevenlabs_voice(request: TtsPreviewRequest):
    """Render one short audition sentence, cached by voice and exact text."""
    text = (request.text or "").strip()
    voice_id = (request.voice_id or "").strip()
    if not voice_id or voice_id in {"default_ko", "gtts_ko", "default"}:
        raise HTTPException(400, "ElevenLabs voice_id가 필요합니다")
    if not text:
        raise HTTPException(400, "미리듣기 문장을 입력하세요")
    if len(text) > 100:
        raise HTTPException(422, "미리듣기는 100자 이내만 가능합니다")

    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        raise HTTPException(503, "ELEVENLABS_API_KEY가 설정되지 않았습니다")

    digest = hashlib.sha256(f"{voice_id}\0{text}".encode("utf-8")).hexdigest()
    cache_key = f"tts:preview:v1:{digest}"
    redis_client = None
    try:
        import redis
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
        )
        cached = redis_client.get(cache_key)
        if cached:
            logger.info("TTS preview cache hit: voice_id=%s hash=%s", voice_id, digest[:12])
            return Response(content=cached, media_type="audio/mpeg", headers={"X-Preview-Cache": "HIT"})
    except Exception as exc:
        logger.warning("TTS preview Redis unavailable; rendering uncached: %s", exc)

    import requests
    from app.config import ELEVENLABS_TTS_MODEL
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        json={
            "text": text,
            "model_id": ELEVENLABS_TTS_MODEL,
            "language_code": "ko",
            "voice_settings": {
                "stability": runtime_config.value("elevenlabs_stability"),
                "similarity_boost": runtime_config.value("elevenlabs_similarity_boost"),
                "style": 0.0,
                "use_speaker_boost": True,
            },
            "apply_text_normalization": "off",
        },
        timeout=40,
    )
    if response.status_code != 200:
        logger.warning("TTS preview failed: voice_id=%s status=%s", voice_id, response.status_code)
        raise HTTPException(response.status_code, "ElevenLabs 미리듣기 생성에 실패했습니다")
    if redis_client is not None:
        try:
            redis_client.setex(cache_key, 7 * 24 * 60 * 60, response.content)
            logger.info("TTS preview cache write: voice_id=%s hash=%s", voice_id, digest[:12])
        except Exception as exc:
            logger.warning("TTS preview cache write failed: %s", exc)
    return Response(content=response.content, media_type="audio/mpeg", headers={"X-Preview-Cache": "MISS"})


# ============================
# Phase 3-4 — 이미지 + GIF
# ============================
class ImagesGenerateRequest(BaseModel):
    tts_meta: str      # TTS 결과 JSON 문자열
    script_meta: str   # 스크립트 결과 JSON 문자열
    job_id: Optional[int] = 0
    character_image_path: Optional[str] = None
    character_style_prompt: Optional[str] = None
    character_poses_dir: Optional[str] = None  # [S2-4] 이중 레이어 합성용 포즈 디렉토리
    # [Sprint 3] LoRA 캐릭터 파인튜닝 파라미터
    lora_model_id: Optional[str] = None        # safetensors CDN URL (Fal.ai flux-lora)
    lora_trigger_word: Optional[str] = None    # LoRA 활성화 트리거 단어
    lora_scale: Optional[float] = 1.0          # LoRA 적용 강도 (0.8~1.2)
    autonomy_mode: Optional[Literal["GUIDED", "AUTO", "MANUAL"]] = None
    budget_limit_krw: Optional[int] = Field(default=None, ge=1, le=70_000)
    budget_policy_version: Optional[str] = Field(default=None, max_length=64)


@app.post("/workers/images/generate")
def images_generate(request: ImagesGenerateRequest):
    try:
        return get_images_worker().generate(
            tts_meta_json=request.tts_meta,
            script_meta_json=request.script_meta,
            job_id=request.job_id or 0,
            character_image_path=request.character_image_path,
            character_style_prompt=request.character_style_prompt,
            character_poses_dir=request.character_poses_dir,
            # [Sprint 3] LoRA 파라미터 전달
            lora_model_id=request.lora_model_id,
            lora_trigger_word=request.lora_trigger_word,
            lora_scale=request.lora_scale,
            autonomy_mode=request.autonomy_mode,
            budget_limit_krw=request.budget_limit_krw,
            budget_policy_version=request.budget_policy_version,
        )
    except ImageProviderCreditRequiredError as e:
        logger.warning("이미지 생성 중단: 공급자 크레딧/쿼터 필요: %s", e)
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "IMAGE_PROVIDER_CREDIT_REQUIRED",
                "message": str(e),
                "retryable": False,
            },
        )
    except ImageProviderTemporarilyUnavailableError as e:
        logger.warning("이미지 생성 일시 중단: 공급자 과부하: %s", e)
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "IMAGE_PROVIDER_TEMPORARILY_UNAVAILABLE",
                "message": str(e),
                "retryable": True,
            },
        )
    except Exception as e:
        logger.exception("이미지 생성 실패")
        raise HTTPException(500, f"이미지 생성 실패: {str(e)}")
class ImagesBatchStatusRequest(BaseModel):
    job_id: int


@app.post("/workers/images/batch-status")
def images_batch_status(request: ImagesBatchStatusRequest):
    try:
        from app.utils.gemini_batch import poll
        return poll(request.job_id)
    except Exception as e:
        logger.exception("Gemini Pro Batch status failed")
        raise HTTPException(500, f"Gemini Pro Batch status failed: {str(e)}")


@app.get("/workers/images/download")
def download_image(path: str):
    if not os.path.exists(path): raise HTTPException(404, "파일 없음")
    media = "image/png" if path.endswith(".png") else "image/gif"
    return FileResponse(path, media_type=media, filename=os.path.basename(path))




# ============================
# Phase 3-5A — 롱폼 조립
# ============================
class LongformGenerateRequest(BaseModel):
    tts_meta: str
    scenes_meta: str
    gifs_meta: str
    job_id: Optional[int] = 0

@app.post("/workers/longform/generate")
def longform_generate(request: LongformGenerateRequest):
    try:
        # A new generate/rebuild request is an explicit retry, so clear a prior
        # user/error stop marker before starting fresh worker processes.
        from app.utils.process_manager import clear_job_stop
        clear_job_stop(request.job_id or 0)
        return get_longform_worker().assemble(
            tts_meta_json=request.tts_meta,
            scenes_meta_json=request.scenes_meta,
            gifs_meta_json=request.gifs_meta,
            job_id=request.job_id or 0,
        )
    except Exception as e:
        logger.exception("롱폼 조립 실패")
        raise HTTPException(500, f"롱폼 조립 실패: {str(e)}")

@app.get("/workers/longform/download")
def download_longform(path: str):
    if not os.path.exists(path): raise HTTPException(404, "파일 없음")
    return FileResponse(path, media_type="video/mp4", filename=os.path.basename(path))

class SingleImageGenerateRequest(BaseModel):
    index: int
    # text remains for older Spring containers. New requests keep the Korean
    # source sentence and the reviewed English image prompt separate.
    text: Optional[str] = None
    source_text: Optional[str] = None
    prompt_en: Optional[str] = None
    section: str
    job_id: int
    character_image_path: Optional[str] = None
    character_style_prompt: Optional[str] = None
    character_poses_dir: Optional[str] = None  # [S2-4]
    # 기존 장면의 문자·수치·표면·캐릭터·모션 계약 전체를 보존한다.
    # source_text/prompt_en만 전송하면 단일 재생성이 전체 Job의 품질
    # 게이트를 우회하므로 Spring이 저장한 scene metadata를 함께 보낸다.
    scene_meta: Optional[Dict[str, Any]] = None

@app.post("/workers/images/generate-single")
async def generate_single_image(request: SingleImageGenerateRequest):
    try:
        job_dir = DATA_DIR / "jobs" / str(request.job_id) / "images"
        job_dir.mkdir(parents=True, exist_ok=True)
        source_text = (request.source_text or request.text or "").strip()
        if not source_text:
            raise HTTPException(422, "source_text is required")
        prompt_en = (request.prompt_en or "").strip()
        if not prompt_en:
            # The source sentence is preserved inside an otherwise-English
            # editorial prompt. This keeps the model grounded in the Korean
            # narration while giving the UI a stable prompt to review/reuse.
            prompt_en = compile_editorial_prompt(
                {
                    "content": source_text,
                    "section": request.section,
                    "art_direction": {
                        "family": "character_role",
                        "setting": "Korean finance editorial scene",
                        "camera": "wide 16:9 editorial composition",
                        "palette": {"colors": "clear teal, warm gold, and confident coral accents"},
                        "lighting": "clean broadcast-studio lighting",
                        "character_required": True,
                    },
                },
                f'Visually explain this Korean financial narration: "{source_text}"',
            )
        scene = dict(request.scene_meta or {})
        scene.update({
            "index": request.index,
            "text": source_text,
            "prompt_ko": source_text,
            "prompt_en": prompt_en,
            "prompt": prompt_en,
            "section": request.section,
        })
        return get_images_worker().generate_single_scene(
            scene=scene,
            job_id=request.job_id,
            output_dir=job_dir,
            character_image_path=request.character_image_path,
            character_style_prompt=request.character_style_prompt,
            character_poses_dir=request.character_poses_dir,
        )
    except ImageRequestHeld as e:
        raise HTTPException(
            409,
            {"error_code": "IMAGE_REQUEST_HELD", "message": str(e), "retryable": False},
        )
    except ImageProviderCreditRequiredError as e:
        raise HTTPException(422, {"error_code": "IMAGE_PROVIDER_CREDIT_REQUIRED", "message": str(e)})
    except ImageProviderTemporarilyUnavailableError as e:
        raise HTTPException(503, {"error_code": "IMAGE_PROVIDER_TEMPORARILY_UNAVAILABLE", "message": str(e)})
    except Exception as e:
        logger.exception("단일 이미지 생성 실패")
        raise HTTPException(500, f"단일 이미지 생성 실패: {str(e)}")

# ============================
# [S2-2] 캐릭터 포즈 라이브러리 생성
# ============================
class CharacterLibraryRequest(BaseModel):
    channel_id: str
    character_description: str
    regenerate: bool = False
    include_role_costumes: bool = False
    include_legacy_poses: bool = False
    pose_names: list[str] | None = None

@app.post("/workers/character-library/generate")
async def generate_character_library(request: CharacterLibraryRequest):
    """
    [S2-2] 주어진 캐릭터 설명으로 7개 포즈(neutral/happy/surprised/worried/thinking/explaining/pointing)를
    배치 생성하고 배경 제거 후 /app/data/characters/<channel_id>/poses/ 에 저장합니다.
    """
    try:
        from app.workers.character_library_worker import CharacterLibraryWorker
        worker = CharacterLibraryWorker()
        result = worker.generate_library(
            channel_id=request.channel_id,
            character_description=request.character_description,
            regenerate=request.regenerate,
            include_role_costumes=request.include_role_costumes,
            include_legacy_poses=request.include_legacy_poses,
            pose_names=request.pose_names,
        )
        return result
    except Exception as e:
        logger.exception("캐릭터 라이브러리 생성 실패")
        raise HTTPException(500, f"생성 실패: {str(e)}")

@app.get("/workers/character-library/list")
async def list_character_libraries():
    """[S2-2] 구성된 모든 칔널 라이브러리 목록 조회"""
    try:
        from app.workers.character_library_worker import CharacterLibraryWorker
        return {"channels": CharacterLibraryWorker().list_channels()}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/workers/character-library/{channel_id}")
async def get_character_library_status(channel_id: str):
    """Return the usable pose names and metadata for one channel library."""
    try:
        from app.workers.character_library_worker import CharacterLibraryWorker
        return CharacterLibraryWorker().get_library_status(channel_id)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/workers/character-library/{channel_id}/pose/{pose}")
async def get_pose_image(channel_id: str, pose: str):
    """[S2-2] 특정 칔널의 포즈 이미지 다운로드"""
    try:
        from app.workers.character_library_worker import CharacterLibraryWorker
        path = CharacterLibraryWorker().get_pose_path(channel_id, pose)
        if not path:
            raise HTTPException(404, f"포즈 이미지 없음: channel={channel_id}, pose={pose}")
        return FileResponse(path, media_type="image/png", filename=f"{channel_id}_{pose}.png")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ============================
# [Sprint 3] LoRA 캐릭터 파인튜닝
# ============================

@app.post("/workers/lora/train")
async def lora_train(
    channel_id: str = Query(..., description="채널 고유 ID"),
    trigger_word: str = Query(default="mycoin", description="LoRA 활성화 트리거 단어 (영문+숫자만)"),
    steps: int = Query(default=1000, description="학습 스텝 수 (권장: 1000~2000)"),
    is_style: bool = Query(default=False, description="스타일 LoRA 여부 (False=캐릭터/주제 LoRA)"),
    zip_file: UploadFile = File(..., description="캐릭터 레퍼런스 이미지 ZIP 파일"),
):
    """
    [Sprint 3] 채널 마스코트 캐릭터 LoRA 파인튜닝 학습 시작.

    캐릭터 이미지(최소 10~20장)를 ZIP으로 묶어 업로드하면
    Fal.ai flux-lora-fast-training 으로 개인화 LoRA 모델을 학습합니다.

    - 학습 비용: ~$3~5 / 1회
    - 소요 시간: 약 5~15분
    - 완료 후 GET /workers/lora/status/{request_id} 로 상태 조회
    - COMPLETED 시 반환된 lora_model_url 을 채널 프로필 loraModelId에 저장
    """
    try:
        from app.workers.lora_trainer_worker import LoraTrainerWorker

        # ZIP 파일 임시 저장
        zip_data = await zip_file.read()
        lora_dir = DATA_DIR / "lora" / channel_id
        lora_dir.mkdir(parents=True, exist_ok=True)
        zip_path = str(lora_dir / "reference_images.zip")
        with open(zip_path, "wb") as f:
            f.write(zip_data)
        logger.info(f"LoRA 학습 ZIP 저장: {zip_path} ({len(zip_data)//1024}KB)")

        worker = LoraTrainerWorker()
        result = worker.train(
            channel_id=channel_id,
            zip_path=zip_path,
            trigger_word=trigger_word,
            steps=steps,
            is_style=is_style,
        )
        return result
    except Exception as e:
        logger.exception("LoRA 학습 시작 실패")
        raise HTTPException(500, f"LoRA 학습 시작 실패: {str(e)}")


@app.get("/workers/lora/status/{request_id}")
async def lora_status(request_id: str):
    """
    [Sprint 3] LoRA 학습 진행 상태 조회.

    응답 status:
      - IN_QUEUE: 큐 대기 중
      - IN_PROGRESS: 학습 진행 중
      - COMPLETED: 완료 (lora_model_url 포함)
      - FAILED / ERROR: 실패
    """
    try:
        from app.workers.lora_trainer_worker import LoraTrainerWorker
        worker = LoraTrainerWorker()
        return worker.get_status(request_id)
    except Exception as e:
        logger.exception("LoRA 상태 조회 실패")
        raise HTTPException(500, f"LoRA 상태 조회 실패: {str(e)}")


@app.get("/workers/lora/channel/{channel_id}")
async def lora_channel_meta(channel_id: str):
    """[Sprint 3] 채널의 LoRA 학습 메타데이터 조회"""
    try:
        from app.workers.lora_trainer_worker import LoraTrainerWorker
        meta = LoraTrainerWorker().get_channel_training_meta(channel_id)
        if not meta:
            raise HTTPException(404, f"채널 '{channel_id}'의 LoRA 학습 이력 없음")
        return meta
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))

# ============================
# 디버깅
# ============================
@app.post("/workers/transcribe")
async def transcribe(file: UploadFile = File(...)):
    from app.providers.factory import get_transcript_provider
    import tempfile
    tmp = tempfile.mktemp(suffix=".mp4")
    try:
        content = await file.read()
        with open(tmp, "wb") as f: f.write(content)
        segments = get_transcript_provider().transcribe(tmp)
        return {"segments": [{"text": s.text, "start": s.start, "end": s.end, "words": s.words} for s in segments]}
    finally:
        if os.path.exists(tmp): os.remove(tmp)


# ============================
# Phase 2+ — 효과음 / BGM / 발음 사전
# ============================
class SfxRequest(BaseModel):
    job_id: int
    sections: list = []

class BgmRequest(BaseModel):
    job_id: int
    category: str = "CUSTOM"
    duration_seconds: int = 60


@app.post("/workers/sfx/generate")
def sfx_generate(req: SfxRequest):
    """효과음 자동 생성 (ElevenLabs Sound Effects API)"""
    try:
        worker = get_sfx_worker()
        result = worker.generate(job_id=req.job_id, sections=req.sections)
        return result
    except Exception as e:
        logger.error(f"SFX 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=f"효과음 생성 실패: {e}")


@app.post("/workers/bgm/generate")
def bgm_generate(req: BgmRequest):
    """BGM 자동 생성 (ElevenLabs Music Generation API)"""
    try:
        worker = get_bgm_worker()
        result = worker.generate(
            job_id=req.job_id,
            category=req.category,
            duration_seconds=req.duration_seconds
        )
        return result
    except Exception as e:
        logger.error(f"BGM 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=f"BGM 생성 실패: {e}")


class YoutubeMetadataRequest(BaseModel):
    script_text: str
    is_shorts: bool = False


@app.post("/workers/youtube/metadata")
async def generate_youtube_metadata(request: YoutubeMetadataRequest):
    """유튜브 업로드용 메타데이터(제목 3안, 설명글, 태그) 자동 생성"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY 미설정 — Mock 유튜브 메타데이터 폴백")
        return {
            "titles": [
                f"[Mock] {'쇼츠 - ' if request.is_shorts else ''}주식 트렌드 긴급 분석",
                f"[Mock] {'쇼츠 - ' if request.is_shorts else ''}시장 변동성과 향후 전망",
                f"[Mock] {'쇼츠 - ' if request.is_shorts else ''}반도체 및 주요 테마 요약"
            ],
            "description": f"[Mock 설명글]\n오늘의 주요 시장 이슈 브리핑입니다.\n\n#주식 #재테크 #금융 {'#Shorts' if request.is_shorts else ''}",
            "tags": ["주식", "투자", "경제", "재테크", "뉴스"]
        }

    try:
        from anthropic import Anthropic
        from app.utils.anthropic_cache import cached_system, log_cache_usage
        client = Anthropic(api_key=api_key)

        system_prompt = """You are a YouTube SEO and financial content editor.
Create accurate Korean metadata from the supplied script only. Do not invent
market facts, prices, percentages, dates, companies, or guarantees. Produce
three distinct but faithful title candidates, one useful description with a
brief summary and hashtags, and 5-8 search tags. Avoid misleading investment
advice, guaranteed returns, or claims not present in the script. Return only
valid JSON with exactly these keys: titles (array of 3 strings), description
(string), tags (array of strings)."""
        prompt = f"<script>\n{request.script_text}\n</script>"

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            system=cached_system(system_prompt),
            messages=[{"role": "user", "content": prompt}]
        )
        log_cache_usage(response, "youtube_metadata")
        content_text = response.content[0].text.strip()
        
        # Clean potential markdown wrapping
        if content_text.startswith("```json"):
            content_text = content_text[7:]
        if content_text.endswith("```"):
            content_text = content_text[:-3]
        content_text = content_text.strip()
        
        import json
        metadata = json.loads(content_text)
        return metadata
    except Exception as e:
        logger.error(f"유튜브 메타데이터 생성 오류: {e}")
        raise HTTPException(status_code=500, detail=f"유튜브 메타데이터 생성 오류: {e}")


class ThumbnailRequest(BaseModel):
    job_id: int
    title: str
    format: str # "longform" | "shorts"
    output_path: str
    character_image_path: Optional[str] = None
    character_style_prompt: Optional[str] = None
    lora_model_id: Optional[str] = None
    lora_trigger_word: Optional[str] = None
    lora_scale: Optional[float] = 1.0
    # v2 keeps thumbnail provenance tied to a scene that is actually present
    # in the final longform.  The legacy title-only route remains available as
    # an explicit provider fallback while Spring is rolled out incrementally.
    scene_candidates: List[Dict[str, Any]] = Field(default_factory=list)
    thumbnail_brief: Dict[str, Any] = Field(default_factory=dict)
    character_identity: Dict[str, Any] = Field(default_factory=dict)
    person_photos: List[Dict[str, Any]] = Field(default_factory=list)
    watermark_path: Optional[str] = None
    variants: int = 3
    reference_style_profile: str = "black_han_sans_v1"
    preset: Optional[Literal["person_led", "mascot_led", "chart_led"]] = None


def _render_thumbnail_request(req: ThumbnailRequest):
    """유튜브 업로드용 썸네일 생성.

    Scene candidates are rendered locally to ensure the image really appears
    in the video.  The old generated-poster path is only a compatibility
    fallback for jobs produced before assembly manifests existed.
    """
    try:
        if req.scene_candidates:
            from app.services.thumbnail import ThumbnailGenerator
            return ThumbnailGenerator().render(
                job_id=req.job_id,
                format_name=req.format,
                output_path=req.output_path,
                candidates=req.scene_candidates,
                brief=req.thumbnail_brief,
                character_asset_path=req.character_image_path,
                character_identity=req.character_identity,
                person_photos=req.person_photos,
                watermark_path=req.watermark_path,
                variants=req.variants,
                reference_style_profile=req.reference_style_profile,
                forced_preset=req.preset,
            )
        from app.providers.factory import get_image_provider
        provider = get_image_provider()
        
        theme_style = (
            "bold finance poster style, vibrant stock market charts, "
            "neon blue and gold accents, high contrast, professional digital art, 8k, cinematic lighting"
        )
        prompt = f"YouTube Video Thumbnail: {req.title}. {theme_style}"
        
        # A custom YouTube longform thumbnail must be 16:9; the previous
        # conditional produced a square for both formats.
        width, height = (1080, 1920) if req.format == "shorts" else (1280, 720)
        
        provider.width = width
        provider.height = height
        provider.generate_image(
            prompt=prompt,
            output_path=req.output_path,
            section="intro",
            keyword=req.title[:30],
            character_image_path=req.character_image_path,
            character_style_prompt=req.character_style_prompt,
            lora_model_id=req.lora_model_id,
            lora_trigger_word=req.lora_trigger_word,
            lora_scale=req.lora_scale,
            gemini_model="gemini-3-pro-image",
            gemini_image_size="2K",
            gemini_service_tier="standard"
        )
        return {"status": "ok", "mode": "ai_fallback", "output_path": req.output_path, "variants": []}
    except Exception as e:
        from app.services.thumbnail import PhotoLicenseError
        from app.services.thumbnail.generator import ThumbnailRenderError
        if isinstance(e, (PhotoLicenseError, ThumbnailRenderError)):
            # These are actionable inputs/provenance errors, not an opaque
            # provider failure.  Spring keeps the job usable and surfaces the
            # concrete reason rather than claiming a successful thumbnail.
            logger.warning("썸네일 생성 입력 검증 실패: %s", e)
            raise HTTPException(status_code=422, detail={"code": str(e).split(":", 1)[0], "message": str(e)})
        if isinstance(e, ValueError) and str(e).split(":", 1)[0] in {
            "BRIEF_VALIDATION_FAILED", "HEADLINE_OVERFLOW", "THUMBNAIL_SOURCE_NOT_IN_VIDEO", "CLEAN_PLATE_REQUIRED",
        }:
            logger.warning("썸네일 v2 게이트 거부: %s", e)
            raise HTTPException(status_code=422, detail={"code": str(e).split(":", 1)[0], "message": str(e)})
        logger.error(f"썸네일 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=f"썸네일 생성 실패: {e}")


@app.post("/workers/youtube/thumbnail")
def generate_thumbnail(req: ThumbnailRequest):
    return _render_thumbnail_request(req)


@app.post("/workers/thumbnail/regenerate")
def regenerate_thumbnail(req: ThumbnailRequest):
    """Re-render existing, video-proven candidates without rewriting a script."""
    if not req.scene_candidates:
        raise HTTPException(status_code=422, detail={"code": "THUMBNAIL_SOURCE_NOT_IN_VIDEO"})
    return _render_thumbnail_request(req)


@app.post("/workers/pronunciation/init")
def pronunciation_init():
    """발음 사전 초기화/확인"""
    try:
        result = PronunciationManager.get_instance().initialize()
        return result
    except Exception as e:
        logger.error(f"발음 사전 초기화 실패: {e}")
        raise HTTPException(status_code=500, detail=f"발음 사전 초기화 실패: {e}")


# ============================
# 작업 제어 및 연쇄 삭제 기능
# ============================
import shutil

class StopJobRequest(BaseModel):
    job_id: int

@app.post("/workers/jobs/{job_id}/stop")
def stop_worker_job(job_id: int):
    """작업의 모든 백그라운드 연산을 즉시 중단"""
    try:
        from app.utils.process_manager import stop_job_processes
        stop_job_processes(job_id)
        return {"status": "ok", "message": f"Job {job_id} stopped"}
    except Exception as e:
        logger.error(f"Job {job_id} 중지 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/workers/jobs/{job_id}")
def delete_worker_job(job_id: int):
    """작업 미디어 데이터 디렉토리를 물리적으로 삭제"""
    try:
        job_dir = DATA_DIR / f"jobs/{job_id}"
        if job_dir.exists() and job_dir.is_dir():
            shutil.rmtree(job_dir)
            logger.info(f"Job {job_id} 미디어 디렉토리 삭제 완료: {job_dir}")
            return {"status": "ok", "message": f"Job {job_id} directory deleted"}
        else:
            logger.info(f"Job {job_id} 미디어 디렉토리가 존재하지 않음: {job_dir}")
            return {"status": "ok", "message": f"Job {job_id} directory not found"}
    except Exception as e:
        logger.error(f"Job {job_id} 디렉토리 삭제 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
