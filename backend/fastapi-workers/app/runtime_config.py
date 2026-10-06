"""
app/runtime_config.py — 재빌드 없이 즉시 조정 가능한 파이프라인 파라미터

지금까지 TTS 속도, ElevenLabs 목소리 설정, 씬 길이 같은 값을 바꾸려면
매번 코드를 열어 숫자를 고치고 Docker 이미지를 다시 빌드해야 했습니다.
클라이언트 피드백 대응 한 번 할 때마다 Antigravity 에이전트 세션을 새로
띄우고 빌드→테스트를 반복하는 구조라 rate limit이 금방 소진되는
원인 중 하나였습니다.

이 모듈은 그 값들을 프로세스 메모리에 올려두고, main.py의
GET/POST /pipeline/config API로 즉시 읽고 쓸 수 있게 합니다.

config.py의 값들은 "컨테이너가 처음 뜰 때의 초기값(환경변수 기준)"이고,
실제 워커들이 매 요청마다 참조하는 값은 이 모듈의 _state 입니다.

주의:
  - 이 값들은 프로세스 메모리에만 있습니다. 컨테이너가 재시작되면
    config.py의 환경변수 기본값으로 다시 초기화됩니다.
  - fastapi-workers 컨테이너를 여러 개로 수평 확장할 계획이 있다면,
    이 저장소를 Redis로 옮기는 것을 권장합니다 (REDIS_HOST가 이미
    docker-compose에 있어 전환 비용은 크지 않습니다).
"""
from app import config as _cfg

_state = {
    "tts_speed": _cfg.TTS_SPEED,
    "chars_per_minute": _cfg.CHARS_PER_MINUTE,
    "scene_duration_sec": _cfg.SCENE_DURATION_SEC,
    "subtitle_max_chars": _cfg.SUBTITLE_MAX_CHARS,
    "subtitle_frame_rate": _cfg.SUBTITLE_FRAME_RATE,
    "subtitle_start_frame_policy": _cfg.SUBTITLE_START_FRAME_POLICY,
    "subtitle_font_size": _cfg.SUBTITLE_FONT_SIZE,
    "subtitle_theme": _cfg.SUBTITLE_THEME,
    "image_headline_overlay": _cfg.IMAGE_HEADLINE_OVERLAY,
    "image_provider": _cfg.IMAGE_PROVIDER,
    "image_quality_tier": _cfg.IMAGE_QUALITY_TIER,
    "gemini_test_image_budget_krw": _cfg.GEMINI_TEST_IMAGE_BUDGET_KRW,
    "pro_image_max_scenes": _cfg.PRO_IMAGE_MAX_SCENES,
    "gemini_pro_batch_enabled": _cfg.GEMINI_PRO_BATCH_ENABLED,
    "gemini_pro_batch_fallback_enabled": _cfg.GEMINI_PRO_BATCH_FALLBACK_ENABLED,
    "gemini_service_tier": _cfg.GEMINI_SERVICE_TIER,
    "gemini_pro_max_attempts": _cfg.GEMINI_PRO_MAX_ATTEMPTS,
    "gemini_pro_retry_base_seconds": _cfg.GEMINI_PRO_RETRY_BASE_SECONDS,
    "gemini_pro_request_delay_seconds": _cfg.GEMINI_PRO_REQUEST_DELAY_SECONDS,
    "gemini_parallel_enabled": _cfg.GEMINI_PARALLEL_ENABLED,
    "gemini_max_concurrency": _cfg.GEMINI_MAX_CONCURRENCY,
    "gemini_retry_max": _cfg.GEMINI_RETRY_MAX,
    # 저장 경로/프로젝트는 배포 계약이다. API로 바꿔 누적 한도를 초기화하지 않는다.
    "gemini_request_state_path": _cfg.GEMINI_REQUEST_STATE_PATH,
    "gemini_project_scope": _cfg.GEMINI_PROJECT_SCOPE,
    "gemini_scene_request_limit": 3,
    "image_same_error_break_count": _cfg.IMAGE_SAME_ERROR_BREAK_COUNT,
    "gemini_rpm_soft_cap": _cfg.GEMINI_RPM_SOFT_CAP,
    "gemini_adaptive_backoff_enabled": _cfg.GEMINI_ADAPTIVE_BACKOFF_ENABLED,
    "longform_scene_max_workers": _cfg.LONGFORM_SCENE_MAX_WORKERS,
    "visual_qa_enabled": _cfg.VISUAL_QA_ENABLED,
    "visual_qa_max_scenes": _cfg.VISUAL_QA_MAX_SCENES,
    "render_speech_bubbles": _cfg.RENDER_SPEECH_BUBBLES,
    "render_article_evidence": _cfg.RENDER_ARTICLE_EVIDENCE,
    "thumbnail_v2_enabled": _cfg.THUMBNAIL_V2_ENABLED,
    "thumbnail_headline_max_lines": _cfg.THUMBNAIL_HEADLINE_MAX_LINES,
    "thumbnail_headline_min_font_px": _cfg.THUMBNAIL_HEADLINE_MIN_FONT_PX,
    "article_allowed_publishers_only": _cfg.ARTICLE_ALLOWED_PUBLISHERS_ONLY,
    "article_evidence_auto_enabled": _cfg.ARTICLE_EVIDENCE_AUTO_ENABLED,
    "evidence_max_scenes": _cfg.EVIDENCE_MAX_SCENES,
    "evidence_max_searches_per_scene": _cfg.EVIDENCE_MAX_SEARCHES_PER_SCENE,
    "evidence_min_sentence_similarity": _cfg.EVIDENCE_MIN_SENTENCE_SIMILARITY,
    "prop_reference_search_enabled": _cfg.PROP_REFERENCE_SEARCH_ENABLED,
    "prop_reference_max_terms_per_scene": _cfg.PROP_REFERENCE_MAX_TERMS_PER_SCENE,
    "scene_reveal_enabled": _cfg.SCENE_REVEAL_ENABLED,
    "bubble_font_max_px": _cfg.BUBBLE_FONT_MAX_PX,
    "bubble_font_min_px": _cfg.BUBBLE_FONT_MIN_PX,
    "subtitle_safe_area_pct": _cfg.SUBTITLE_SAFE_AREA_PCT,
    # Diegetic factual surfaces are applied to the final still before motion.
    # Baked labels remain explicitly disabled pending product approval.
    "info_surface_enabled": True,
    "info_surface_mode_default": "diegetic",
    "info_surface_quad_min_confidence": 0.55,
    "info_surface_texture_strength": 0.20,
    "info_surface_baked_enabled": False,
    # Phase B is opt-in only. These defaults make a fresh deployment unable to
    # create an external provider request until product approval changes them.
    "info_surface_harmonizer_enabled": False,
    "info_surface_harmonizer_provider": "fal_canny",
    "info_surface_harmonizer_reader": "tesseract",
    "info_surface_harmonizer_strength": 0.38,
    "elevenlabs_voice_id": _cfg.ELEVENLABS_VOICE_ID,
    "elevenlabs_stability": _cfg.ELEVENLABS_STABILITY,
    "elevenlabs_similarity_boost": _cfg.ELEVENLABS_SIMILARITY_BOOST,
    "elevenlabs_style": _cfg.ELEVENLABS_STYLE,
    "tts_model_intro": _cfg.TTS_MODEL_INTRO,
    "tts_model_body": _cfg.TTS_MODEL_BODY,
    "tts_stability_intro": _cfg.TTS_STABILITY_INTRO,
    "tts_stability_body": _cfg.TTS_STABILITY_BODY,
    "tts_cer_threshold": _cfg.TTS_CER_THRESHOLD,
    "tts_max_retries": _cfg.TTS_MAX_RETRIES,
    "tts_postprocess_enabled": _cfg.TTS_POSTPROCESS_ENABLED,
    "tts_sentence_pause_ms": _cfg.TTS_SENTENCE_PAUSE_MS,
    "tts_paragraph_pause_ms": _cfg.TTS_PARAGRAPH_PAUSE_MS,
    "tts_thought_group_pause_ms": _cfg.TTS_THOUGHT_GROUP_PAUSE_MS,
    "tts_duration_tolerance": _cfg.TTS_DURATION_TOLERANCE,
    "bgm_volume": _cfg.BGM_VOLUME,
    "intro_motion_seconds_short": _cfg.INTRO_MOTION_SECONDS_SHORT,
    "intro_motion_seconds_long": _cfg.INTRO_MOTION_SECONDS_LONG,
    "intro_motion_short_threshold": _cfg.INTRO_MOTION_SHORT_THRESHOLD,
    "intro_kling_max_clips": _cfg.INTRO_KLING_MAX_CLIPS,
    "intro_motion_clip_count": _cfg.INTRO_MOTION_CLIP_COUNT,
    "intro_motion_clip_seconds": _cfg.INTRO_MOTION_CLIP_SECONDS,
    "intro_motion_enabled": _cfg.INTRO_MOTION_ENABLED,
    "intro_motion_test_mode": _cfg.INTRO_MOTION_TEST_MODE,
    "max_image_hold_seconds": _cfg.MAX_IMAGE_HOLD_SECONDS,
    "img_cost_flash_1k_usd": _cfg.IMG_COST_FLASH_1K_USD,
    "img_cost_flash_2k_usd": _cfg.IMG_COST_FLASH_2K_USD,
    "img_cost_pro_2k_usd": _cfg.IMG_COST_PRO_2K_USD,
    "img_cost_pro_priority_2k_usd": _cfg.IMG_COST_PRO_PRIORITY_2K_USD,
    "kling_cost_per_clip_usd": _cfg.KLING_COST_PER_CLIP_USD,
    "usd_krw": _cfg.USD_KRW,
    "max_budget_per_video_krw": _cfg.MAX_BUDGET_PER_VIDEO_KRW,
    "budget_retry_buffer_pct": _cfg.BUDGET_RETRY_BUFFER_PCT,
    "keyword_score_weight_multiple": _cfg.KEYWORD_SCORE_WEIGHT_MULTIPLE,
    "keyword_score_weight_velocity": _cfg.KEYWORD_SCORE_WEIGHT_VELOCITY,
    "keyword_score_weight_like": _cfg.KEYWORD_SCORE_WEIGHT_LIKE,
    "keyword_score_weight_comment": _cfg.KEYWORD_SCORE_WEIGHT_COMMENT,
    "keyword_min_source_subscribers": _cfg.KEYWORD_MIN_SOURCE_SUBSCRIBERS,
    "keyword_min_source_views": _cfg.KEYWORD_MIN_SOURCE_VIEWS,
    "keyword_min_source_viewer_multiple": _cfg.KEYWORD_MIN_SOURCE_VIEWER_MULTIPLE,
    "keyword_exclude_live": _cfg.KEYWORD_EXCLUDE_LIVE,
    "keyword_like_rate_benchmark": _cfg.KEYWORD_LIKE_RATE_BENCHMARK,
    "keyword_comment_rate_benchmark": _cfg.KEYWORD_COMMENT_RATE_BENCHMARK,
    "script_house_style_enabled": _cfg.SCRIPT_HOUSE_STYLE_ENABLED,
    "script_pattern_numbers_enabled": _cfg.SCRIPT_PATTERN_NUMBERS_ENABLED,
    "script_pattern_analogy_enabled": _cfg.SCRIPT_PATTERN_ANALOGY_ENABLED,
    "script_pattern_fake_question_enabled": _cfg.SCRIPT_PATTERN_FAKE_QUESTION_ENABLED,
    "script_pattern_llm_labeling_enabled": _cfg.SCRIPT_PATTERN_LLM_LABELING_ENABLED,
    "script_narrative_planning_enabled": _cfg.SCRIPT_NARRATIVE_PLANNING_ENABLED,
    "script_flow_qa_enabled": _cfg.SCRIPT_FLOW_QA_ENABLED,
}

_TYPES = {k: type(v) for k, v in _state.items()}
_DEFAULTS = dict(_state)


def get() -> dict:
    """현재 적용 중인 전체 파라미터 스냅샷을 반환합니다."""
    return dict(_state)


def value(key: str):
    """단일 파라미터 값을 반환합니다. 워커들은 이 함수로 값을 읽습니다."""
    return _state[key]


def update(**kwargs) -> dict:
    """전달된 키만 갱신합니다 (None 값은 무시). 잘못된 키/타입은 에러."""
    for k, v in kwargs.items():
        if v is None:
            continue
        if k not in _state:
            raise KeyError(f"알 수 없는 파이프라인 파라미터: {k}")
        expected = _TYPES[k]
        try:
            normalized = expected(v)
            if k in {"gemini_request_state_path", "gemini_project_scope"} and normalized != _state[k]:
                raise ValueError("요청 상태 저장소/프로젝트 변경은 런타임에 허용되지 않습니다.")
            if k == "gemini_scene_request_limit" and not 1 <= normalized <= 3:
                raise ValueError("이미지 요청 한도는 최초 포함 1~3회입니다.")
            if k == "max_budget_per_video_krw":
                normalized = min(normalized, _cfg.MAX_BUDGET_PER_VIDEO_KRW)
            if k == "image_quality_tier" and normalized != "pro":
                raise ValueError("image_quality_tier는 pro만 허용합니다.")
            if k == "image_provider" and normalized not in {"openai", "gemini", "fal"}:
                raise ValueError("image_provider는 openai/gemini/fal만 허용합니다.")
            _state[k] = normalized
        except (TypeError, ValueError):
            raise ValueError(f"{k}는 {expected.__name__} 타입이어야 합니다: 받은 값={v!r}")
    return dict(_state)


def reset_to_env_defaults() -> dict:
    """config.py의 환경변수 기본값으로 되돌립니다 (응급 복구용)."""
    _state.update(_DEFAULTS)
    return dict(_state)
