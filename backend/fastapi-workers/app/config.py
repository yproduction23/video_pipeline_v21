import os

# 모든 이미지 워커가 같은 영속 볼륨과 프로젝트 식별자를 사용해야 한다.
GEMINI_REQUEST_STATE_PATH = os.getenv("GEMINI_REQUEST_STATE_PATH", "/app/data/provider_requests/gemini.sqlite3")
GEMINI_PROJECT_SCOPE = os.getenv("GEMINI_PROJECT_SCOPE", "default")

APP_MODE = os.getenv("APP_MODE", "local")

# S3/MinIO
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = os.getenv("S3_BUCKET_NAME", "ai-video-assets")

# Redis
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

# API Keys (prod only)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", "eleven_v3")
KLING_API_KEY = os.getenv("KLING_API_KEY", "")
GOOGLE_AI_API_KEY = os.getenv("GOOGLE_AI_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")
BFL_API_KEY = os.getenv("BFL_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
V5_BFL_ENABLED = os.getenv("V5_BFL_ENABLED", "false").lower() in {"1", "true", "yes"}
if V5_BFL_ENABLED and not BFL_API_KEY:
    raise RuntimeError("V5_BFL_ENABLED=true이면 BFL_API_KEY가 필요합니다.")

# ─── 신규: 시장 데이터 / 뉴스 API Keys ─────────────────
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")       # 미국 주식 뉴스·시세
FRED_API_KEY = os.getenv("FRED_API_KEY", "")              # 연준 거시경제 지표
DART_API_KEY = os.getenv("DART_API_KEY", "")              # 한국 공시 PER/PBR (선택)
NAVER_API_HUB_ENABLED = os.getenv("NAVER_API_HUB_ENABLED", "false").lower() in {"1", "true", "yes"}
NAVER_API_HUB_CLIENT_ID = os.getenv("NAVER_API_HUB_CLIENT_ID", "")
NAVER_API_HUB_CLIENT_SECRET = os.getenv("NAVER_API_HUB_CLIENT_SECRET", "")
if NAVER_API_HUB_ENABLED and (not NAVER_API_HUB_CLIENT_ID or not NAVER_API_HUB_CLIENT_SECRET):
    raise RuntimeError(
        "NAVER_API_HUB_ENABLED=true이면 NAVER_API_HUB_CLIENT_ID와 "
        "NAVER_API_HUB_CLIENT_SECRET이 모두 필요합니다"
    )

# Claude 모델 — 프로젝트 고정값. 임의 변경 절대 금지.
# (keyword_worker.py에 "claude-sonnet-5"라는 오타가 있었고,
#  providers/real/llm.py에는 구형 모델이 하드코딩되어 있었던 것을
#  이 상수로 통일해서 재발을 방지합니다.)
CLAUDE_MODEL = "claude-sonnet-4-6"
ANTHROPIC_PROMPT_CACHE_ENABLED = os.getenv("ANTHROPIC_PROMPT_CACHE_ENABLED", "true").lower() in {"1", "true", "yes"}
ANTHROPIC_PROMPT_CACHE_TTL = os.getenv("ANTHROPIC_PROMPT_CACHE_TTL", "5m")

# YouTube 공개 지표 성과등급의 기본값. runtime_config를 통해 무중단으로 조정한다.
KEYWORD_SCORE_WEIGHT_MULTIPLE = float(os.getenv("KEYWORD_SCORE_WEIGHT_MULTIPLE", "0.5"))
KEYWORD_SCORE_WEIGHT_VELOCITY = float(os.getenv("KEYWORD_SCORE_WEIGHT_VELOCITY", "0.3"))
KEYWORD_SCORE_WEIGHT_LIKE = float(os.getenv("KEYWORD_SCORE_WEIGHT_LIKE", "0.1"))
KEYWORD_SCORE_WEIGHT_COMMENT = float(os.getenv("KEYWORD_SCORE_WEIGHT_COMMENT", "0.1"))
KEYWORD_LIKE_RATE_BENCHMARK = float(os.getenv("KEYWORD_LIKE_RATE_BENCHMARK", "0.02"))
KEYWORD_COMMENT_RATE_BENCHMARK = float(os.getenv("KEYWORD_COMMENT_RATE_BENCHMARK", "0.002"))
# Small channels can produce a large view/subscriber multiple from a single
# lucky upload. They do not qualify as automatic recommendation evidence.
# 추천 근거는 "작은 채널의 우연한 한 건"을 막으면서도 일주일 안의
# 신선한 시장 이슈를 충분히 확보해야 한다. 3천 구독자/500 조회 + 구독자 수의 1% 조회 기준은
# 최신 대형 채널 영상도 포함하되, 반응이 거의 없는 영상은 제외한다.
# 구독자 대비 조회수는 "구독자 수의 1%"처럼 퍼센트로 UI에 표시한다.
KEYWORD_MIN_SOURCE_SUBSCRIBERS = int(os.getenv("KEYWORD_MIN_SOURCE_SUBSCRIBERS", "3000"))
KEYWORD_MIN_SOURCE_VIEWS = int(os.getenv("KEYWORD_MIN_SOURCE_VIEWS", "500"))
KEYWORD_MIN_SOURCE_VIEWER_MULTIPLE = float(os.getenv("KEYWORD_MIN_SOURCE_VIEWER_MULTIPLE", "0.01"))
KEYWORD_OUTPERFORMER_RECENT_MULTIPLE = float(os.getenv("KEYWORD_OUTPERFORMER_RECENT_MULTIPLE", "1.5"))
KEYWORD_OUTPERFORMER_MIN_BASELINE_COUNT = int(os.getenv("KEYWORD_OUTPERFORMER_MIN_BASELINE_COUNT", "10"))
KEYWORD_OUTPERFORMER_BASELINE_CAP_PER_REQ = int(os.getenv("KEYWORD_OUTPERFORMER_BASELINE_CAP_PER_REQ", "10"))
KEYWORD_EXCLUDE_LIVE = os.getenv("KEYWORD_EXCLUDE_LIVE", "true").lower() in {"1", "true", "yes"}

# ══════════════════════════════════════════════════════════
# 파이프라인 동작 파라미터 초기값 (신규)
#
# 여기 값은 "컨테이너 시작 시 1회 로딩되는 기본값"입니다. 클라이언트
# 피드백에 맞춰 실시간으로 조정하려면 이 파일을 고치지 말고
# GET/POST /pipeline/config API(= app/runtime_config.py)를 쓰세요.
# 그러면 Docker 재빌드 없이 다음 Job부터 즉시 반영됩니다.
# ══════════════════════════════════════════════════════════
# Fast post-generation speed-up flattens pauses and emphasis.  Keep narration
# close to natural speed; reduce the script target accordingly to preserve the
# requested video duration instead of compressing the actor's performance.
# 음성 속도를 0.82로 낮춤 (기존 0.9 대비 약 -10%). ElevenLabs 네이티브 범위(0.7~1.2) 내 조정.
TTS_SPEED = float(os.getenv("TTS_SPEED", "0.9"))
# Measured Korean long-form narration pace for the configured channel voice.
# 1분(60초) TTS 음성 분량을 충족하기 위해 분당 650자 기준을 적용한다. (650자 / 60초 ≈ 11자/초)
CHARS_PER_MINUTE = int(os.getenv("CHARS_PER_MINUTE", "650"))
SCENE_DURATION_SEC = float(os.getenv("SCENE_DURATION_SEC", "5.5"))
# 한국어 한 줄 자막은 의미 단위를 해치지 않는 15~20자 범위를 목표로 한다.
SUBTITLE_MAX_CHARS = int(os.getenv("SUBTITLE_MAX_CHARS", "18"))
# 최종 조립 영상은 30fps CFR로 인코딩한다. 자막 시작점을 이 프레임 격자에
# 맞춰야 렌더러가 강제 정렬 시각보다 한 프레임 먼저 표시하지 않는다.
SUBTITLE_FRAME_RATE = float(os.getenv("SUBTITLE_FRAME_RATE", "30"))
# nearest는 이전 프레임으로만 당기던 방식과 다음 프레임으로만 미루던 방식의
# 중간값이다. 최종 음성 문자 경계에 가장 가까운 출력 프레임을 선택한다.
SUBTITLE_START_FRAME_POLICY = os.getenv("SUBTITLE_START_FRAME_POLICY", "nearest").lower()
SUBTITLE_FONT_SIZE = int(os.getenv("SUBTITLE_FONT_SIZE", "72"))
SUBTITLE_THEME = os.getenv("SUBTITLE_THEME", "economy")  # economy | knowledge
IMAGE_HEADLINE_OVERLAY = os.getenv("IMAGE_HEADLINE_OVERLAY", "false").lower() in {"1", "true", "yes"}
_SUPPORTED_IMAGE_PROVIDERS = {"openai", "gemini", "fal"}
# 2026-09-29: 회사 Gemini 계정이 무기명 카드라 결제 등록이 Google 정책상
# 구조적으로 불가능해, OpenAI를 메인으로 두고 Fal/Gemini를 fallback으로
# 쓰기로 했다(사용자 승인, 채널 캐릭터 참조 이미지 파일럿 통과).
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "gemini").lower()
if IMAGE_PROVIDER not in _SUPPORTED_IMAGE_PROVIDERS:
    raise RuntimeError(f"지원하지 않는 이미지 생성 공급자입니다: {IMAGE_PROVIDER}")
# 이미지 품질을 예산 때문에 낮추면 사실 장면과 일반 장면의 품질 계약이
# 달라진다. 운영 경로는 Nano Banana Pro 단일 tier로 고정한다.
IMAGE_QUALITY_TIER = os.getenv("IMAGE_QUALITY_TIER", "pro").lower()
if IMAGE_QUALITY_TIER != "pro":
    raise RuntimeError("IMAGE_QUALITY_TIER는 pro만 허용합니다. flash/hybrid는 지원하지 않습니다.")
PRO_IMAGE_MAX_SCENES = int(os.getenv("PRO_IMAGE_MAX_SCENES", "48"))
# Batch API has a 24-hour completion SLO, so it is an economy/background mode,
# not the default path for a user waiting for a finished video.
GEMINI_PRO_BATCH_ENABLED = os.getenv("GEMINI_PRO_BATCH_ENABLED", "false").lower() in {"1", "true", "yes"}
# Batch has a separate prepaid-credit contract. Never switch an interactive
# long-form job onto it unless an operator explicitly enables it.
GEMINI_PRO_BATCH_FALLBACK_ENABLED = os.getenv("GEMINI_PRO_BATCH_FALLBACK_ENABLED", "false").lower() in {"1", "true", "yes"}
GEMINI_SERVICE_TIER = os.getenv("GEMINI_SERVICE_TIER", "priority").lower()
if GEMINI_SERVICE_TIER not in {"priority", "standard"}:
    raise RuntimeError("GEMINI_SERVICE_TIER는 priority 또는 standard만 허용합니다.")
# Pace and retry synchronous Pro 2K requests without changing their model.
GEMINI_PRO_MAX_ATTEMPTS = int(os.getenv("GEMINI_PRO_MAX_ATTEMPTS", "5"))
GEMINI_PRO_RETRY_BASE_SECONDS = float(os.getenv("GEMINI_PRO_RETRY_BASE_SECONDS", "10"))
GEMINI_PRO_REQUEST_DELAY_SECONDS = float(os.getenv("GEMINI_PRO_REQUEST_DELAY_SECONDS", "3"))
# Bound image API fan-out so a long job is faster without turning rate limits
# into missing scenes.  These are runtime-tunable through /pipeline/config.
GEMINI_PARALLEL_ENABLED = os.getenv("GEMINI_PARALLEL_ENABLED", "true").lower() in {"1", "true", "yes"}
GEMINI_MAX_CONCURRENCY = int(os.getenv("GEMINI_MAX_CONCURRENCY", "3"))
GEMINI_RETRY_MAX = int(os.getenv("GEMINI_RETRY_MAX", "3"))
IMAGE_SAME_ERROR_BREAK_COUNT = int(os.getenv("IMAGE_SAME_ERROR_BREAK_COUNT", "5"))
GEMINI_RPM_SOFT_CAP = int(os.getenv("GEMINI_RPM_SOFT_CAP", "60"))
GEMINI_ADAPTIVE_BACKOFF_ENABLED = os.getenv("GEMINI_ADAPTIVE_BACKOFF_ENABLED", "true").lower() in {"1", "true", "yes"}
LONGFORM_SCENE_MAX_WORKERS = int(os.getenv("LONGFORM_SCENE_MAX_WORKERS", "2"))
VISUAL_QA_ENABLED = os.getenv("VISUAL_QA_ENABLED", "true").lower() in {"1", "true", "yes"}
# Vision QA is an anchor-sample review, not a second API call for every one
# of 240 scenes. File/codec validation still covers every scene.
VISUAL_QA_MAX_SCENES = int(os.getenv("VISUAL_QA_MAX_SCENES", "64"))

# Article evidence overlays are post-production assets. They never become
# Gemini/Kling prompt text and can be toggled without a redeploy.
RENDER_SPEECH_BUBBLES = os.getenv("RENDER_SPEECH_BUBBLES", "false").lower() in {"1", "true", "yes"}
RENDER_ARTICLE_EVIDENCE = os.getenv("RENDER_ARTICLE_EVIDENCE", "true").lower() in {"1", "true", "yes"}
THUMBNAIL_V2_ENABLED = os.getenv("THUMBNAIL_V2_ENABLED", "true").lower() in {"1", "true", "yes"}
THUMBNAIL_HEADLINE_MAX_LINES = int(os.getenv("THUMBNAIL_HEADLINE_MAX_LINES", "4"))
THUMBNAIL_HEADLINE_MIN_FONT_PX = int(os.getenv("THUMBNAIL_HEADLINE_MIN_FONT_PX", "64"))
ARTICLE_ALLOWED_PUBLISHERS_ONLY = os.getenv("ARTICLE_ALLOWED_PUBLISHERS_ONLY", "true").lower() in {"1", "true", "yes"}
ARTICLE_EVIDENCE_AUTO_ENABLED = os.getenv("ARTICLE_EVIDENCE_AUTO_ENABLED", "true").lower() in {"1", "true", "yes"}
EVIDENCE_MAX_SCENES = int(os.getenv("EVIDENCE_MAX_SCENES", "12"))
EVIDENCE_MAX_SEARCHES_PER_SCENE = int(os.getenv("EVIDENCE_MAX_SEARCHES_PER_SCENE", "3"))
EVIDENCE_MIN_SENTENCE_SIMILARITY = float(os.getenv("EVIDENCE_MIN_SENTENCE_SIMILARITY", "0.85"))
# 2026-10-06 사용자 요청: 대본에 등장하는 구체적 소품/사물(예: "전역모")의
# 실제 모습을 Gemini가 참고할 수 있도록, NAVER API HUB 이미지 검색으로 실물
# 참조 사진을 찾아 캐릭터/화풍 참조와 별도 슬롯으로 추가한다. 신규 외부 연동
# 이므로 기본값은 꺼둔다 — 검증 전까지는 기존 이미지 생성 흐름에 영향 없음.
PROP_REFERENCE_SEARCH_ENABLED = os.getenv("PROP_REFERENCE_SEARCH_ENABLED", "false").lower() in {"1", "true", "yes"}
PROP_REFERENCE_MAX_TERMS_PER_SCENE = int(os.getenv("PROP_REFERENCE_MAX_TERMS_PER_SCENE", "1"))
SCENE_REVEAL_ENABLED = os.getenv("SCENE_REVEAL_ENABLED", "true").lower() in {"1", "true", "yes"}
BUBBLE_FONT_MAX_PX = int(os.getenv("BUBBLE_FONT_MAX_PX", "110"))
BUBBLE_FONT_MIN_PX = int(os.getenv("BUBBLE_FONT_MIN_PX", "68"))
SUBTITLE_SAFE_AREA_PCT = float(os.getenv("SUBTITLE_SAFE_AREA_PCT", "18"))

ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "dlKJ5VptCbYxal4doUO5")
ELEVENLABS_STABILITY = float(os.getenv("ELEVENLABS_STABILITY", "0.35"))
ELEVENLABS_SIMILARITY_BOOST = float(os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75"))
ELEVENLABS_STYLE = float(os.getenv("ELEVENLABS_STYLE", "0.0"))
# 한국어 금융 내레이션은 발음 안정성을 우선해 다국어 v2를 기본으로 사용한다.
# 감정 연기가 필요한 구간만 환경 변수로 v3를 명시적으로 선택할 수 있다.
TTS_MODEL_INTRO = os.getenv("TTS_MODEL_INTRO", ELEVENLABS_TTS_MODEL)
TTS_MODEL_BODY = os.getenv("TTS_MODEL_BODY", ELEVENLABS_TTS_MODEL)
# v7 검수 기준: 같은 목소리의 정체성은 유지하면서 질문 끝과 설명부의
# 미세한 높낮이를 살릴 수 있도록 안정성을 과도하게 고정하지 않는다.
TTS_STABILITY_INTRO = float(os.getenv("TTS_STABILITY_INTRO", "0.5"))
TTS_STABILITY_BODY = float(os.getenv("TTS_STABILITY_BODY", "0.5"))
TTS_CER_THRESHOLD = float(os.getenv("TTS_CER_THRESHOLD", "0.05"))
TTS_MAX_RETRIES = int(os.getenv("TTS_MAX_RETRIES", "3"))
# AI 음성은 과한 압축·정규화만으로도 합성음처럼 들릴 수 있다. 원본 성우의
# 미세한 높낮이와 호흡을 보존하기 위해 기본 마스터링은 끈다.
TTS_POSTPROCESS_ENABLED = os.getenv("TTS_POSTPROCESS_ENABLED", "false").lower() in {"1", "true", "yes"}
# ElevenLabs already supplies natural Korean sentence breaths.  Do not splice
# extra silence at every punctuation mark; it makes narration staccato.
TTS_SENTENCE_PAUSE_MS = int(os.getenv("TTS_SENTENCE_PAUSE_MS", "350"))
TTS_PARAGRAPH_PAUSE_MS = int(os.getenv("TTS_PARAGRAPH_PAUSE_MS", "400"))
# ElevenLabs의 원래 억양은 보존하되, 문장 경계가 자연스럽게 연결되도록 70ms 미세 호흡을 더한다.
# 문장 그룹 간 미세 호흡 70ms → 110ms 상향. 자막 싱크는 강제 정렬로 유지.
TTS_THOUGHT_GROUP_PAUSE_MS = int(os.getenv("TTS_THOUGHT_GROUP_PAUSE_MS", "110"))
TTS_DURATION_TOLERANCE = float(os.getenv("TTS_DURATION_TOLERANCE", "0.25"))

BGM_VOLUME = float(os.getenv("BGM_VOLUME", "0.12"))

# Image-to-video is deliberately limited to the contiguous opening hook.  All
# later scenes are rendered as static images: no FFmpeg zoom, pan, or transition
# effects are permitted because they can introduce visible jitter in charts,
# captions, and aligned numerical graphics.
INTRO_MOTION_SECONDS_SHORT = float(os.getenv("INTRO_MOTION_SECONDS_SHORT", "45"))
INTRO_MOTION_SECONDS_LONG = float(os.getenv("INTRO_MOTION_SECONDS_LONG", "60"))
INTRO_MOTION_SHORT_THRESHOLD = float(os.getenv("INTRO_MOTION_SHORT_THRESHOLD", "600"))
INTRO_KLING_MAX_CLIPS = int(os.getenv("INTRO_KLING_MAX_CLIPS", "12"))  # legacy API compatibility
INTRO_MOTION_CLIP_COUNT = int(os.getenv("INTRO_MOTION_CLIP_COUNT", "12"))
INTRO_MOTION_CLIP_SECONDS = int(os.getenv("INTRO_MOTION_CLIP_SECONDS", "5"))
INTRO_MOTION_ENABLED = os.getenv("INTRO_MOTION_ENABLED", "True").lower() == "true"
INTRO_MOTION_TEST_MODE = os.getenv("INTRO_MOTION_TEST_MODE", "False").lower() == "true"
MAX_IMAGE_HOLD_SECONDS = int(os.getenv("MAX_IMAGE_HOLD_SECONDS", "8"))

# Budget values are placeholders: replace them with the current AI Studio/Fal
# console rates before production.  The preflight never hard-codes a price.
IMG_COST_FLASH_1K_USD = float(os.getenv("IMG_COST_FLASH_1K_USD", "0.045"))
IMG_COST_FLASH_2K_USD = float(os.getenv("IMG_COST_FLASH_2K_USD", "0.101"))
IMG_COST_PRO_2K_USD = float(os.getenv("IMG_COST_PRO_2K_USD", "0.134"))
IMG_COST_PRO_PRIORITY_2K_USD = float(os.getenv("IMG_COST_PRO_PRIORITY_2K_USD", "0.24192"))
KLING_COST_PER_CLIP_USD = float(os.getenv("KLING_COST_PER_CLIP_USD", "0.35"))  # 5 sec × $0.07/sec, audio off
USD_KRW = float(os.getenv("USD_KRW", "1400"))
# 작업별 실제 상한은 Spring PricingConfig가 전달한다. 20분 미만: ₩40,000 / 20분 이상: ₩70,000.
# 이 값은 전역 계약에서 허용한 최대치(70,000원)를 넘는 런타임 설정을 차단한다.
MAX_BUDGET_PER_VIDEO_KRW = min(70_000, int(os.getenv("MAX_BUDGET_PER_VIDEO_KRW", "70000")))
GEMINI_TEST_IMAGE_BUDGET_KRW = int(os.getenv("GEMINI_TEST_IMAGE_BUDGET_KRW", "5000"))
BUDGET_RETRY_BUFFER_PCT = float(os.getenv("BUDGET_RETRY_BUFFER_PCT", "10"))

# 대본 하우스 스타일은 운영자가 단계적으로 활성화한다. 기본값은 기존 채널
# 동작을 보존하며, 활성화된 작업에서만 확정 전 하드 게이트가 적용된다.
SCRIPT_HOUSE_STYLE_ENABLED = os.getenv("SCRIPT_HOUSE_STYLE_ENABLED", "false").lower() in {"1", "true", "yes"}
SCRIPT_PATTERN_NUMBERS_ENABLED = os.getenv("SCRIPT_PATTERN_NUMBERS_ENABLED", "false").lower() in {"1", "true", "yes"}
SCRIPT_PATTERN_ANALOGY_ENABLED = os.getenv("SCRIPT_PATTERN_ANALOGY_ENABLED", "false").lower() in {"1", "true", "yes"}
SCRIPT_PATTERN_FAKE_QUESTION_ENABLED = os.getenv("SCRIPT_PATTERN_FAKE_QUESTION_ENABLED", "false").lower() in {"1", "true", "yes"}
SCRIPT_PATTERN_LLM_LABELING_ENABLED = os.getenv("SCRIPT_PATTERN_LLM_LABELING_ENABLED", "false").lower() in {"1", "true", "yes"}
SCRIPT_NARRATIVE_PLANNING_ENABLED = os.getenv("SCRIPT_NARRATIVE_PLANNING_ENABLED", "true").lower() in {"1", "true", "yes"}
SCRIPT_FLOW_QA_ENABLED = os.getenv("SCRIPT_FLOW_QA_ENABLED", "true").lower() in {"1", "true", "yes"}
