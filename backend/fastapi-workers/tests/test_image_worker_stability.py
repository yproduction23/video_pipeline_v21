import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import runtime_config
from app.utils.retry_policy import classify_image_error
from app.workers.images_worker import (
    FinalImageValidationError,
    GeneratedImageVisualContractError,
    ImagesWorker,
    _base_prompt_from_scene_spec,
    _bounded_text_generation_prompt,
    _clear_scene_from_request_review,
    _image_prompt_cache_key,
    _image_provider_for_attempt,
    _persist_single_scene_visual_qa_review,
    _requires_full_scene_regeneration,
    _review_reasons_including_held_scenes,
    _sanitize_unplanned_prompt_structure,
)


class _Provider:
    def __init__(self):
        self.kwargs = None

    def generate_image(self, **kwargs):
        self.kwargs = kwargs


class _Pressure:
    def __init__(self):
        self.outcomes = []

    def acquire(self):
        return None

    def outcome(self, error=None):
        self.outcomes.append(error)


class ImageWorkerStabilityTests(unittest.TestCase):
    def test_only_transient_provider_errors_are_retryable(self):
        self.assertFalse(classify_image_error(TypeError("bad config")).retryable)
        self.assertFalse(classify_image_error(ValueError("invalid setting")).retryable)
        self.assertFalse(classify_image_error(RuntimeError("invalid local output")).retryable)
        self.assertTrue(classify_image_error(RuntimeError("HTTP 503 unavailable")).retryable)
        self.assertTrue(classify_image_error(TimeoutError("timed out")).retryable)

    def test_image_provider_stays_on_default_until_last_resort(self):
        """2026-09-29 사용자 결정: OpenAI(기본)가 같은 장면에서 계속 QA를
        못 넘기면, 포기하기 전에 이 장면만 Fal.ai로 마지막 시도를 해본다."""
        for attempt in range(3):
            self.assertEqual(
                _image_provider_for_attempt(
                    "openai", attempt=attempt, max_retries=4, force_fal_last_resort=False,
                ),
                "openai",
            )
        self.assertEqual(
            _image_provider_for_attempt(
                "openai", attempt=3, max_retries=4, force_fal_last_resort=False,
            ),
            "fal",
        )
        self.assertEqual(
            _image_provider_for_attempt(
                "openai", attempt=0, max_retries=4, force_fal_last_resort=True,
            ),
            "fal",
        )

    def test_prepaid_credit_exhaustion_is_not_retried_even_when_provider_uses_429(self):
        decision = classify_image_error(RuntimeError(
            "HTTP 429: Your prepayment credits are depleted; status=RESOURCE_EXHAUSTED"
        ))
        self.assertFalse(decision.retryable)
        self.assertEqual(decision.reason, "permanent provider billing/quota response")

    def test_background_layer_uses_registered_runtime_keys(self):
        provider = _Provider()
        pressure = _Pressure()
        with patch("app.workers.images_worker.gemini_pressure", pressure):
            ImagesWorker()._generate_background_layer(provider, "prompt", "/tmp/scene.png", "data", "neutral")
        self.assertEqual(provider.kwargs["gemini_service_tier"], runtime_config.value("gemini_service_tier"))
        self.assertEqual(provider.kwargs["gemini_retry_base_seconds"], runtime_config.value("gemini_pro_retry_base_seconds"))
        self.assertIn("CLEAN PLATE REQUIREMENT", provider.kwargs["prompt"])
        self.assertIn("no hands", provider.kwargs["prompt"].lower())
        self.assertEqual(pressure.outcomes, [None])

    def test_parallel_renderer_accepts_preflight_from_generate_scope(self):
        params = inspect.signature(ImagesWorker._generate_parallel_scenes).parameters
        self.assertIn("budget_preflight", params)

    def test_cached_blank_panel_and_unplanned_speech_bubble_are_sanitized(self):
        prompt = (
            "A market chart falls despite a looming speech bubble with empty claims. "
            "One large blank presentation board stands reserved for post-production values."
        )
        cleaned = _sanitize_unplanned_prompt_structure(
            prompt,
            {"bubble_allowed": False, "deterministic_texts": ["6696포인트"]},
        )
        self.assertNotIn("speech bubble", cleaned.lower())
        self.assertNotIn("blank", cleaned.lower())
        self.assertIn("opaque scene-integrated", cleaned)
        self.assertIn("exact deterministic typography", cleaned)

    def test_detached_numeric_placard_sentence_is_removed_from_cached_prompt(self):
        prompt = (
            "A large display labeled 코스닥 shows a falling graph. "
            "One blank placard panel reserved below the screen awaits exact figures. "
            "A cracked silver coin rests on the podium."
        )
        cleaned = _sanitize_unplanned_prompt_structure(
            prompt,
            {"bubble_allowed": False, "deterministic_texts": ["800.75포인트", "12.58포인트"]},
        )
        self.assertIn("display labeled 코스닥", cleaned)
        self.assertIn("cracked silver coin", cleaned)
        self.assertNotIn("placard", cleaned.lower())
        self.assertNotIn("awaits exact figures", cleaned.lower())

    def test_blur_or_smear_rejection_requires_fresh_scene_candidate(self):
        self.assertTrue(_requires_full_scene_regeneration({
            "failure_categories": ["text_surface_detached_translucent_card", "local_edit_blur_smear_artifact"],
        }))
        self.assertFalse(_requires_full_scene_regeneration({
            "failure_categories": ["text_unexpected_or_malformed"],
        }))
        self.assertTrue(_requires_full_scene_regeneration({
            "failure_categories": ["text_generated_deterministic_numeric"],
        }))

    def test_corrupted_final_file_requires_fresh_scene_candidate_not_a_local_edit(self):
        """2026-10-01 job 12 scene 6 재현: 손상된 최종 파일에는 국소 편집으로
        고칠 유효한 소스가 없으므로 항상 전체 재생성이어야 한다."""
        self.assertTrue(_requires_full_scene_regeneration({
            "failure_categories": ["final_image_invalid"],
        }))

    def test_final_image_validation_error_routes_through_scene_local_retry_not_batch_abort(self):
        """2026-10-01 job 12 scene 6 재현: 공급자 호출은 성공했지만 최종 파일이
        손상된 경우, 예전에는 평범한 RuntimeError라 classify_image_error가
        "unclassified RuntimeError"로 분류해 NonRetryableImageGenerationError로
        배치 전체(무관한 scene 7·8 포함)를 즉시 중단시켰다.
        GeneratedImageVisualContractError를 상속해 다른 장면별 콘텐츠 거부와
        같은 장면 로컬 재시도 경로를 타야 한다."""
        self.assertTrue(issubclass(FinalImageValidationError, GeneratedImageVisualContractError))
        exc = FinalImageValidationError(
            "final image validation failed",
            {"failure_categories": ["final_image_invalid"], "reason": "final image validation failed"},
        )
        self.assertEqual(exc.review["failure_categories"], ["final_image_invalid"])

    def test_screen_text_contract_does_not_invalidate_content_prompt_cache(self):
        common = {
            "index": 0,
            "narration": "삼성전자가 110조 원 규모의 주주환원을 발표했습니다.",
            "scene_type": "metric",
            "visual_mode": "archetype_explainer",
            "character_required": True,
            "core_entities": ["삼성전자"],
            "core_figures": [{"raw": "110조 원"}],
        }
        old_key = _image_prompt_cache_key(**common, screen_texts=[])
        new_key = _image_prompt_cache_key(**common, screen_texts=["삼성전자", "110조 원"])
        self.assertEqual(old_key, new_key)

    def test_financial_value_is_withheld_from_model_prompt_at_render_boundary(self):
        scene = {"screen_texts": ["삼성전자", "110조 원"]}
        prompt = _bounded_text_generation_prompt(
            "A semiconductor ceremony with one blank board reserved for post-production values.",
            audit_target=scene,
        )
        self.assertIn("삼성전자", prompt)
        self.assertNotIn("110조 원", prompt)
        self.assertIn("withheld deterministic strings or values", prompt)
        self.assertNotIn("blank", prompt.lower())

    def test_single_worker_circuit_breaker_does_not_start_the_next_scene(self):
        class AlwaysUnavailable:
            def __init__(self):
                self.sections = []

            def generate_image(self, **kwargs):
                self.sections.append(kwargs["section"])
                raise RuntimeError("HTTP 503 high demand")

        class SequentialPressure:
            def acquire(self):
                return None

            def outcome(self, _error=None):
                return None

            def recommended_concurrency(self, _configured):
                return 1

        provider = AlwaysUnavailable()
        scenes = [
            {
                "title": f"장면 {index + 1}",
                "section": f"scene_{index}",
                "narration": f"승인 내레이션 {index}",
                "prompt_en": f"editorial finance scene {index}",
                "scene_type": "general",
                "visual_mode": "general",
                "art_direction": {"character_required": True},
                "image_profile": {"tier": "pro", "model": "gemini-3-pro-image", "image_size": "2K"},
            }
            for index in range(3)
        ]
        original_value = runtime_config.value
        values = {
            "gemini_retry_max": 3,
            "gemini_pro_retry_base_seconds": 0.5,
            "gemini_max_concurrency": 1,
            "image_same_error_break_count": 1,
            "image_provider": "gemini",
            "gemini_service_tier": "standard",
        }

        def configured_value(key):
            return values[key] if key in values else original_value(key)

        with self.subTest("첫 씬 실패 뒤 다음 씬을 제출하지 않음"):
            with tempfile.TemporaryDirectory() as temp_dir, \
                 patch("app.workers.images_worker.gemini_pressure", SequentialPressure()), \
                 patch("app.workers.images_worker.runtime_config.value", side_effect=configured_value), \
                 patch("app.workers.images_worker.time.sleep", return_value=None):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "IMAGE_PROVIDER_TEMPORARILY_UNAVAILABLE",
                    ):
                        ImagesWorker()._generate_parallel_scenes(
                            scenes_meta=scenes,
                            directed_specs={},
                            market_snapshot={},
                            character_reference_paths=[],
                            character_style_prompt="none",
                            lora_model_id=None,
                            lora_trigger_word=None,
                            lora_scale=None,
                            ai_provider=provider,
                            job_dir=Path(temp_dir),
                            job_id=9001,
                        )

        self.assertEqual(provider.sections, ["scene_0"])


class ClearSceneFromRequestReviewTests(unittest.TestCase):
    """2026-09-30 job 12 재현: generate_single_scene()이 write_request_review()를
    호출하지 않아, 보류(needs_review)된 장면을 개별 재생성으로 고쳐도
    image_request_review.json이 그대로 남아 longform 조립이 계속 차단됐다.
    job_id·scene index는 재현용 fixture일 뿐이며, 이 함수 자체는 어떤
    job/scene에도 동일하게 동작해야 한다."""

    def test_clears_only_the_fixed_scene_and_leaves_gate_blocked_for_others(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            review_path = job_dir / "image_request_review.json"
            review_path.write_text(json.dumps({
                "job_id": 12,
                "requires_manual_review": True,
                "request_gate_cleared": False,
                "assembly_allowed": False,
                "scenes": [
                    {"index": 0, "status": "needs_review", "reason": "문자 계약 위반"},
                    {"index": 3, "status": "needs_review", "reason": "다른 장면도 보류"},
                ],
            }, ensure_ascii=False), encoding="utf-8")

            _clear_scene_from_request_review(job_dir, 12, 0)

            review = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertEqual([s["index"] for s in review["scenes"]], [3])
            self.assertFalse(review["request_gate_cleared"])
            self.assertFalse(review["assembly_allowed"])

    def test_clears_the_gate_entirely_when_it_was_the_last_held_scene(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            review_path = job_dir / "image_request_review.json"
            review_path.write_text(json.dumps({
                "job_id": 12,
                "requires_manual_review": True,
                "request_gate_cleared": False,
                "assembly_allowed": False,
                "scenes": [{"index": 0, "status": "needs_review", "reason": "문자 계약 위반"}],
            }, ensure_ascii=False), encoding="utf-8")

            _clear_scene_from_request_review(job_dir, 12, 0)

            review = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertEqual(review["scenes"], [])
            self.assertTrue(review["request_gate_cleared"])
            self.assertTrue(review["assembly_allowed"])

    def test_is_a_no_op_when_no_review_gate_file_exists(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            _clear_scene_from_request_review(job_dir, 99, 0)
            self.assertFalse((job_dir / "image_request_review.json").exists())


class PersistSingleSceneVisualQaReviewTests(unittest.TestCase):
    """2026-10-01 job 12 scene 0/4/5 재현: generate_single_scene()으로 고친
    장면이 다음 전체 배치 실행에서 "기존 PNG 검증"의 캐시 히트를 찾지 못해
    Spring의 원본(미보정) 데이터로 처음부터 다시 생성되며 같은 문제로
    반복해서 되돌아갔다. 배치 경로(persist_visual_qa_review)와 동일한
    visual_qa_cache.json에 써야 다음 배치 실행이 이미 통과한 파일을 그대로
    재사용한다."""

    def test_writes_review_keyed_by_scene_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            review = {"policy_version": 19, "image_sha256": "abc123", "decision": "accept"}
            _persist_single_scene_visual_qa_review(job_dir, 4, review)

            cache = json.loads((job_dir / "visual_qa_cache.json").read_text(encoding="utf-8"))
            self.assertEqual(cache["4"], review)

    def test_merges_with_existing_cache_without_dropping_other_scenes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            (job_dir / "visual_qa_cache.json").write_text(
                json.dumps({"1": {"policy_version": 19, "image_sha256": "existing"}}), encoding="utf-8",
            )
            review = {"policy_version": 19, "image_sha256": "new-hash"}
            _persist_single_scene_visual_qa_review(job_dir, 0, review)

            cache = json.loads((job_dir / "visual_qa_cache.json").read_text(encoding="utf-8"))
            self.assertEqual(cache["1"]["image_sha256"], "existing")
            self.assertEqual(cache["0"], review)

    def test_is_a_no_op_when_review_has_no_image_sha256(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            _persist_single_scene_visual_qa_review(job_dir, 0, {"decision": "accept"})
            self.assertFalse((job_dir / "visual_qa_cache.json").exists())


class ReviewReasonsIncludingHeldScenesTests(unittest.TestCase):
    """2026-09-30 job 12 재현: scene 0이 ImageRequestHeld로 보류됐는데도
    _generate_parallel_scenes()의 응답은 requires_manual_review=false를
    반환했다(_manual_review_reasons()가 성공한 장면만 봤기 때문). Spring
    AUTO 모드가 이를 그대로 믿고 IMAGES 게이트를 통과시켜 job이
    ASSEMBLING으로 넘어갔고, 조립은 별도 게이트에서 막혀 job이 앞으로도
    뒤로도 못 가는 상태가 됐다."""

    def test_adds_a_reason_per_held_scene_so_requires_manual_review_becomes_true(self):
        held_scenes = [{"index": 0, "status": "needs_review", "reason": "문자 계약 위반"}]
        result = _review_reasons_including_held_scenes([], held_scenes)
        self.assertEqual(result, ["SCENE_HELD_FOR_REVIEW:scene_0"])
        self.assertTrue(bool(result))

    def test_merges_with_existing_reasons_without_duplicating(self):
        held_scenes = [{"index": 3, "status": "needs_review"}]
        result = _review_reasons_including_held_scenes(
            ["VISUAL_QA_UNAVAILABLE", "SCENE_HELD_FOR_REVIEW:scene_3"], held_scenes,
        )
        self.assertEqual(result, ["SCENE_HELD_FOR_REVIEW:scene_3", "VISUAL_QA_UNAVAILABLE"])

    def test_no_held_scenes_leaves_reasons_unchanged(self):
        self.assertEqual(_review_reasons_including_held_scenes(["ART_DIRECTION:x"], []), ["ART_DIRECTION:x"])


class BasePromptFromSceneSpecTests(unittest.TestCase):
    """2026-09-30 job 12 scene 4/5/6 재현: 배치 경로는 SceneDirector가 지정한
    scene_spec(의상·동작·소품·카메라)이 있으면 build_prompt(spec)으로 구체적
    프롬프트를 만드는데, generate_single_scene()은 scene_spec을 전혀 읽지
    않아 항상 더 일반적인 compile_editorial_prompt로 대체돼 승인 장면의
    구체적 연출 지시가 재생성 시 전부 사라졌다."""

    _VALID_SPEC = {
        "scene_id": "4",
        "narration": "그런데 한국군이 자료 공유와 현장 출입을 거부했다는 겁니다.",
        "headline": "자료 거부",
        "metaphor": "문 앞에서 막히는 장면으로 표현",
        "character_role": "조사 요청자",
        "character_costume": "유엔 하늘색 조끼 차림의 골디, 하얀 장갑, 클립보드 소지",
        "character_action": "골디가 굳게 닫힌 철문 앞에서 손을 뻗은 자세",
        "character_emotion": "당혹스럽고 억울한 표정",
        "setting": "군 시설 외벽, 콘크리트 담장, 철문, 황혼 빛",
        "props": ["철문", "클립보드"],
        "camera": "미디엄샷",
        "side_characters": "",
        "mood": "negative",
    }

    def test_returns_none_when_no_scene_spec(self):
        self.assertIsNone(_base_prompt_from_scene_spec(None))
        self.assertIsNone(_base_prompt_from_scene_spec({}))
        self.assertIsNone(_base_prompt_from_scene_spec({"scene_id": ""}))

    def test_builds_director_prompt_with_the_specific_costume_action_and_setting(self):
        prompt = _base_prompt_from_scene_spec(self._VALID_SPEC)
        self.assertIsNotNone(prompt)
        assert prompt is not None
        self.assertIn("유엔 하늘색 조끼", prompt)
        self.assertIn("철문", prompt)
        self.assertIn("당혹스럽고 억울한 표정", prompt)

    def test_falls_back_to_none_when_spec_reconstruction_fails(self):
        broken = {"scene_id": "4"}  # 필수 필드 누락
        self.assertIsNone(_base_prompt_from_scene_spec(broken))


class VisualQaUnavailableDoesNotAbortTheBatchTests(unittest.TestCase):
    """2026-10-01 job 13 scene 0 재현: Gemini 비전 검수가 200을 반환했지만
    검증 가능한 판정으로 파싱되지 않자(visual_qa_unavailable:200:0),
    VisualQaUnavailableError가 즉시 NonRetryableImageGenerationError로
    바뀌어 대기 중이던 다른 모든 장면의 futures까지 취소시켰다. job 12
    scene 7에서도 같은 메시지로 재현된 바 있어 job 하나만의 문제가
    아니라 공통 계약 버그다. 검수 연결 문제는 이 장면 하나만의 문제이지
    다른 장면과 무관하므로, FinalImageValidationError(이 세션에서 이미
    고친 scene 6 파일 손상 사례)와 같은 원칙으로 장면 로컬 보류
    (ImageRequestHeld)로 가야 한다. 같은 이미지를 다시 과금해 만들지는
    않는다는 VisualQaUnavailableError의 원래 설계 의도는 그대로 유지한다
    (계약 위반 재시도 경로로 승격하지 않음)."""

    def test_one_scenes_qa_connection_failure_still_lets_other_scenes_complete(self):
        from app import runtime_config
        from app.utils import budget
        from app.workers import images_worker
        from app.workers.images_worker import ImagesWorker, VisualQaUnavailableError
        from PIL import Image

        class Provider:
            def __init__(self):
                self.calls = []

            def generate_image(self, **kwargs):
                self.calls.append(kwargs["section"])
                # valid_image()는 15000바이트 초과를 요구한다. 노이즈 이미지는
                # 단색과 달리 PNG 압축으로 작아지지 않아 그 기준을 넘는다.
                Image.effect_noise((640, 360), 60).convert("RGB").save(kwargs["output_path"], "PNG")

        class SequentialPressure:
            def acquire(self):
                return None

            def outcome(self, _error=None):
                return None

            def recommended_concurrency(self, _configured):
                return 1

        def fake_visual_qa(ctx, _img_path):
            if ctx["index"] == 0:
                raise VisualQaUnavailableError("장면 비전 검수를 완료하지 못함: visual_qa_unavailable:200:0")
            return {}

        scenes = [
            {
                "title": f"장면 {index + 1}",
                "section": f"scene_{index}",
                "narration": f"승인 내레이션 {index}",
                "prompt_en": f"editorial finance scene {index}",
                "scene_type": "general",
                "visual_mode": "general",
                "art_direction": {"character_required": True},
                "image_profile": {"tier": "pro", "model": "gemini-3-pro-image", "image_size": "2K"},
            }
            for index in range(3)
        ]
        provider = Provider()
        original_value = runtime_config.value
        values = {
            "gemini_retry_max": 3,
            "gemini_pro_retry_base_seconds": 0.5,
            "gemini_max_concurrency": 1,
            "image_same_error_break_count": 10,
            "image_provider": "gemini",
            "gemini_service_tier": "standard",
        }

        def configured_value(key):
            return values[key] if key in values else original_value(key)

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("app.workers.images_worker.gemini_pressure", SequentialPressure()), \
             patch("app.workers.images_worker.runtime_config.value", side_effect=configured_value), \
             patch("app.workers.images_worker.is_job_stopped", lambda job_id: False), \
             patch.object(budget, "_job_path", lambda job, name: Path(temp_dir) / name), \
             patch.object(ImagesWorker, "_normalize_canvas", lambda self, path: None), \
             patch.object(ImagesWorker, "_apply_image_overlays", lambda self, ctx, path: None), \
             patch("app.workers.images_worker._inspect_generated_textless_image", lambda ctx, path, **kw: {}), \
             patch("app.workers.images_worker._inspect_generated_visual_image", side_effect=fake_visual_qa):
            worker = ImagesWorker()
            worker.tts_subtitle_sync = {}
            worker.evidence_audit = {}
            worker.visual_mix_plan = {}
            response = worker._generate_parallel_scenes(
                scenes_meta=scenes,
                directed_specs={},
                market_snapshot={},
                character_reference_paths=[],
                character_style_prompt="none",
                lora_model_id=None,
                lora_trigger_word=None,
                lora_scale=None,
                ai_provider=provider,
                job_dir=Path(temp_dir),
                job_id=9002,
            )

            # 핵심 회귀 검증: scene 0의 검수 연결 실패가 scene 1·2의 제출/완료를
            # 막지 않아야 한다 (예전에는 NonRetryableImageGenerationError로 바뀌어
            # 대기 중인 futures를 전부 취소시켰다). 그리고 scene 0이 보류돼도
            # 완료된 scene 1·2는 예외로 통째로 버려지지 않고 정상 응답에 담겨야
            # Spring이 SCENE_IMAGE 자산으로 저장할 수 있다.
            self.assertEqual(sorted(provider.calls), ["scene_0", "scene_1", "scene_2"])
            self.assertEqual([s["index"] for s in response["scenes"]], [1, 2])
            self.assertTrue(response["requires_manual_review"])
            self.assertIn("SCENE_HELD_FOR_REVIEW:scene_0", response["review_reasons"])

            review = json.loads((Path(temp_dir) / "image_request_review.json").read_text(encoding="utf-8"))
            self.assertEqual([s["index"] for s in review["scenes"]], [0])


class ContentRejectionExhaustionIsNotMislabeledAsProviderOverloadTests(unittest.TestCase):
    """2026-10-02 job 13 scene 1 재현: Gemini API 호출 자체는 매번
    "공식 Gemini API 이미지 생성 성공"으로 성공했지만, 우리 쪽 비전 계약
    검수가 매 시도(메인 공급자 2회 + 국소 편집 1회 + Fal.ai 대체 1회)를
    계속 거부해 scene 1이 재시도 예산을 모두 소진했다. 이 마지막
    "image generation failed after N attempts" RuntimeError는 원인
    체인(from last_error) 없이 그냥 generic RuntimeError였고, 바깥
    except 블록은 NonRetryableImageGenerationError/VisualQaUnavailableError가
    아닌 예외를 전부 무조건 "일시적 공급자 장애"로 간주해 카운터를
    올렸다. image_same_error_break_count=1이라 단 한 번의 콘텐츠 거부
    소진만으로 "Gemini Pro 과부하"라는, 실제로는 틀린 메시지와 함께
    대기 중인 다른 모든 scene의 futures까지 취소시켰다. 실제로는
    공급자 장애가 전혀 아니라 scene 1 하나의 콘텐츠 계약 문제였다."""

    def test_scene_exhausting_retries_on_content_rejections_is_held_not_treated_as_overload(self):
        from app import runtime_config
        from app.utils import budget
        from app.workers import images_worker
        from app.workers.images_worker import GeneratedImageVisualContractError, ImagesWorker
        from PIL import Image

        class Provider:
            def __init__(self):
                self.calls = []

            def generate_image(self, **kwargs):
                self.calls.append(kwargs["section"])
                Image.effect_noise((640, 360), 60).convert("RGB").save(kwargs["output_path"], "PNG")

        class SequentialPressure:
            def acquire(self):
                return None

            def outcome(self, _error=None):
                return None

            def recommended_concurrency(self, _configured):
                return 1

        def fake_visual_qa(ctx, _img_path):
            if ctx["index"] == 0:
                # Gemini 호출은 매번 성공하지만, 내용은 계속 계약을 위반한다
                # (job 13 scene 1처럼 실제 API 장애가 전혀 아님).
                raise GeneratedImageVisualContractError(
                    "장면 비전 계약 위반: visual_quality_floor",
                    {"failure_categories": ["visual_quality_floor"], "reason": "품질 기준 미달"},
                )
            return {}

        scenes = [
            {
                "title": f"장면 {index + 1}",
                "section": f"scene_{index}",
                "narration": f"승인 내레이션 {index}",
                "prompt_en": f"editorial finance scene {index}",
                "scene_type": "general",
                "visual_mode": "general",
                "art_direction": {"character_required": True},
                "image_profile": {"tier": "pro", "model": "gemini-3-pro-image", "image_size": "2K"},
            }
            for index in range(3)
        ]
        provider = Provider()
        original_value = runtime_config.value
        values = {
            "gemini_retry_max": 2,
            "gemini_pro_retry_base_seconds": 0.5,
            "gemini_max_concurrency": 1,
            "image_same_error_break_count": 1,
            "image_provider": "gemini",
            "gemini_service_tier": "standard",
        }

        def configured_value(key):
            return values[key] if key in values else original_value(key)

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch("app.workers.images_worker.gemini_pressure", SequentialPressure()), \
             patch("app.workers.images_worker.runtime_config.value", side_effect=configured_value), \
             patch("app.workers.images_worker.is_job_stopped", lambda job_id: False), \
             patch.object(budget, "_job_path", lambda job, name: Path(temp_dir) / name), \
             patch.object(ImagesWorker, "_normalize_canvas", lambda self, path: None), \
             patch.object(ImagesWorker, "_apply_image_overlays", lambda self, ctx, path: None), \
             patch("app.workers.images_worker._inspect_generated_textless_image", lambda ctx, path, **kw: {}), \
             patch("app.workers.images_worker._inspect_generated_visual_image", side_effect=fake_visual_qa):
            worker = ImagesWorker()
            worker.tts_subtitle_sync = {}
            worker.evidence_audit = {}
            worker.visual_mix_plan = {}
            response = worker._generate_parallel_scenes(
                scenes_meta=scenes,
                directed_specs={},
                market_snapshot={},
                character_reference_paths=[],
                character_style_prompt="none",
                lora_model_id=None,
                lora_trigger_word=None,
                lora_scale=None,
                ai_provider=provider,
                job_dir=Path(temp_dir),
                job_id=9003,
            )

            # 핵심 회귀 검증: 실제 공급자 장애가 아니므로 "과부하"로 번지지 않고,
            # scene 1·2는 취소되지 않고 계속 진행되며, scene 0이 보류돼도 완료된
            # scene 1·2는 예외로 통째로 버려지지 않고 정상 응답에 담겨야 Spring이
            # SCENE_IMAGE 자산으로 저장할 수 있다.
            self.assertEqual([s["index"] for s in response["scenes"]], [1, 2])
            self.assertTrue(response["requires_manual_review"])
            self.assertIn("SCENE_HELD_FOR_REVIEW:scene_0", response["review_reasons"])
            # scene 0은 거부당할 때마다 재생성을 시도해 여러 번 호출되지만, 핵심은
            # scene 1·2가 취소되지 않고 (최소 1번씩) 시도됐다는 점이다.
            self.assertEqual(set(provider.calls), {"scene_0", "scene_1", "scene_2"})

            review = json.loads((Path(temp_dir) / "image_request_review.json").read_text(encoding="utf-8"))
            self.assertEqual([s["index"] for s in review["scenes"]], [0])


class HeldScenesNoLongerDiscardCompletedResultsTests(unittest.TestCase):
    """2026-10-02 job 13 재현: scene 0·6은 성공했지만 scene 1~5·7~9가 보류되자,
    _generate_parallel_scenes()는 completed scenes(results)를 담은 채로도
    무조건 "Image generation incomplete" RuntimeError를 던져 응답 바디 전체를
    버렸다. Spring의 ImagesService.generateImages()는 이 호출이 예외를
    던지면 result가 끝내 할당되지 않아 성공한 장면조차 SCENE_IMAGE 자산으로
    저장하지 못했다 — UI에 아무 이미지도 안 보이고, 심지어 장면별
    재생성(기존 자산이 있어야 동작)도 쓸 수 없는 상태가 됐다.

    Spring 쪽은 이미 이 상황을 위해 만들어져 있었다: ImagesGenerateResponse에
    requiresManualReview/reviewReasons가 있고, ImagesService.generateImages()는
    정상 응답을 받으면 완료된 장면을 먼저 저장한 뒤 requiresManualReview로
    AUTO 자동확정만 차단한다(ImagesServiceAutoGateTest로 이미 커버됨). 유일한
    버그는 FastAPI가 보류 장면이 하나라도 있으면 그 정상 응답 자체를 Spring에
    전혀 보내지 않았다는 것이다. 보류되지 않고 진짜로 설명되지 않은 실패만
    여전히 예외를 던져야 한다(아래 전송 실패 케이스로 회귀 방지)."""

    def _common_patches(self, temp_dir):
        from app import runtime_config
        from app.utils import budget
        from app.workers import images_worker
        from app.workers.images_worker import ImagesWorker

        original_value = runtime_config.value
        values = {
            "gemini_retry_max": 2,
            "gemini_pro_retry_base_seconds": 0.5,
            "gemini_max_concurrency": 1,
            "image_same_error_break_count": 10,
            "image_provider": "gemini",
            "gemini_service_tier": "standard",
            "visual_qa_enabled": False,
        }

        def configured_value(key):
            return values[key] if key in values else original_value(key)

        return [
            patch("app.workers.images_worker.runtime_config.value", side_effect=configured_value),
            patch("app.workers.images_worker.is_job_stopped", lambda job_id: False),
            patch.object(budget, "_job_path", lambda job, name: Path(temp_dir) / name),
            patch.object(ImagesWorker, "_normalize_canvas", lambda self, path: None),
            patch.object(ImagesWorker, "_apply_image_overlays", lambda self, ctx, path: None),
            patch("app.workers.images_worker._inspect_generated_textless_image", lambda ctx, path, **kw: {}),
        ]

    def test_one_held_scene_still_returns_the_completed_scene_instead_of_raising(self):
        from app.workers.images_worker import ImageRequestHeld, ImagesWorker
        from PIL import Image

        class Provider:
            def __init__(self):
                self.calls = []

            def generate_image(self, **kwargs):
                self.calls.append(kwargs["section"])
                if kwargs["section"] == "scene_1":
                    raise ImageRequestHeld("장면 누적 요청 상한 도달")
                Image.effect_noise((640, 360), 60).convert("RGB").save(kwargs["output_path"], "PNG")

        class SequentialPressure:
            def acquire(self):
                return None

            def outcome(self, _error=None):
                return None

            def recommended_concurrency(self, _configured):
                return 1

        def fake_visual_qa(ctx, _img_path):
            return {}

        scenes = [
            {
                "title": f"장면 {index + 1}",
                "section": f"scene_{index}",
                "narration": f"승인 내레이션 {index}",
                "prompt_en": f"editorial finance scene {index}",
                "scene_type": "general",
                "visual_mode": "general",
                "art_direction": {"character_required": True},
                "image_profile": {"tier": "pro", "model": "gemini-3-pro-image", "image_size": "2K"},
            }
            for index in range(2)
        ]
        provider = Provider()

        with tempfile.TemporaryDirectory() as temp_dir:
            patches = self._common_patches(temp_dir)
            with patch("app.workers.images_worker.gemini_pressure", SequentialPressure()), \
                 patch("app.workers.images_worker._inspect_generated_visual_image", side_effect=fake_visual_qa), \
                 patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                # generate()가 정상 경로에서 채워주는 필드들을 직접 호출 시에는
                # 미리 설정해야 한다(여기서는 성공 응답 조립까지 실제로 도달한다).
                worker = ImagesWorker()
                worker.tts_subtitle_sync = {}
                worker.evidence_audit = {}
                worker.visual_mix_plan = {}
                response = worker._generate_parallel_scenes(
                    scenes_meta=scenes,
                    directed_specs={},
                    market_snapshot={},
                    character_reference_paths=[],
                    character_style_prompt="none",
                    lora_model_id=None,
                    lora_trigger_word=None,
                    lora_scale=None,
                    ai_provider=provider,
                    job_dir=Path(temp_dir),
                    job_id=9004,
                )

            # 핵심 회귀 검증: 예외를 던지는 대신, 완료된 scene 0을 담은 정상
            # 응답을 반환하고 보류된 scene 1은 review_reasons로만 알린다.
            self.assertEqual([s["index"] for s in response["scenes"]], [0])
            self.assertTrue(response["requires_manual_review"])
            self.assertIn("SCENE_HELD_FOR_REVIEW:scene_1", response["review_reasons"])

    def test_a_truly_unaccounted_failure_still_raises_incomplete(self):
        """보류되지 않고 설명도 안 된 실패(아래 임계 미만 일시 오류)까지
        조용히 성공으로 둔갑시키면 안 된다 — 회귀 방지용 대조군."""
        from app.workers.images_worker import ImagesWorker
        from PIL import Image

        class Provider:
            def generate_image(self, **kwargs):
                if kwargs["section"] == "scene_1":
                    raise RuntimeError("HTTP 503 temporarily unavailable")
                Image.effect_noise((640, 360), 60).convert("RGB").save(kwargs["output_path"], "PNG")

        class SequentialPressure:
            def acquire(self):
                return None

            def outcome(self, _error=None):
                return None

            def recommended_concurrency(self, _configured):
                return 1

        scenes = [
            {
                "title": f"장면 {index + 1}",
                "section": f"scene_{index}",
                "narration": f"승인 내레이션 {index}",
                "prompt_en": f"editorial finance scene {index}",
                "scene_type": "general",
                "visual_mode": "general",
                "art_direction": {"character_required": True},
                "image_profile": {"tier": "pro", "model": "gemini-3-pro-image", "image_size": "2K"},
            }
            for index in range(2)
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            patches = self._common_patches(temp_dir)
            with patch("app.workers.images_worker.gemini_pressure", SequentialPressure()), \
                 patch("app.workers.images_worker._inspect_generated_visual_image", lambda ctx, path: {}), \
                 patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    ImagesWorker()._generate_parallel_scenes(
                        scenes_meta=scenes,
                        directed_specs={},
                        market_snapshot={},
                        character_reference_paths=[],
                        character_style_prompt="none",
                        lora_model_id=None,
                        lora_trigger_word=None,
                        lora_scale=None,
                        ai_provider=Provider(),
                        job_dir=Path(temp_dir),
                        job_id=9005,
                    )


class ResumePathVisualQaUnavailableIsAlsoHeldNotRaisedRawTests(unittest.TestCase):
    """2026-10-02 job 13 scene 1 재현(배포 후에도 재현됨): render_one()의
    retry 루프 안에서는 VisualQaUnavailableError를 ImageRequestHeld로
    바꿨지만, retry 루프 시작 "전"에 있는 두 재개(resume) 지름길
    (scene_XXX_raw.png만 있을 때, scene_XXX.png가 지문이 일치해 그대로
    재검증될 때)은 자신만의 try/except를 따로 가지고 있어 저 수정이 닿지
    않았다. 거기서 Gemini QA가 402를 반환하면(visual_qa_unavailable:402:N)
    VisualQaUnavailableError가 그대로 빠져나가 바깥 except가 이를
    NonRetryableImageGenerationError와 똑같이 취급해 배치 전체를
    "non-retryable error"로 중단시켰다 — 정확히 이전에 고친 것과 같은
    부류의 버그가 재개 경로에도 남아있었다."""

    def test_existing_raw_image_whose_resume_qa_check_is_unavailable_is_held_not_raised(self):
        from app import runtime_config
        from app.utils import budget
        from app.workers import images_worker
        from app.workers.images_worker import ImagesWorker, VisualQaUnavailableError
        from PIL import Image

        class Provider:
            def __init__(self):
                self.calls = []

            def generate_image(self, **kwargs):
                self.calls.append(kwargs["section"])
                Image.effect_noise((640, 360), 60).convert("RGB").save(kwargs["output_path"], "PNG")

        class SequentialPressure:
            def acquire(self):
                return None

            def outcome(self, _error=None):
                return None

            def recommended_concurrency(self, _configured):
                return 1

        def fake_visual_qa(ctx, _img_path):
            if ctx["index"] == 0:
                raise VisualQaUnavailableError("장면 비전 검수를 완료하지 못함: visual_qa_unavailable:402:0")
            return {}

        scenes = [
            {
                "title": f"장면 {index + 1}",
                "section": f"scene_{index}",
                "narration": f"승인 내레이션 {index}",
                "prompt_en": f"editorial finance scene {index}",
                "scene_type": "general",
                "visual_mode": "general",
                "art_direction": {"character_required": True},
                "image_profile": {"tier": "pro", "model": "gemini-3-pro-image", "image_size": "2K"},
            }
            for index in range(2)
        ]
        provider = Provider()
        original_value = runtime_config.value
        values = {
            "gemini_retry_max": 2,
            "gemini_pro_retry_base_seconds": 0.5,
            "gemini_max_concurrency": 1,
            "image_same_error_break_count": 10,
            "image_provider": "gemini",
            "gemini_service_tier": "standard",
            "visual_qa_enabled": False,
        }

        def configured_value(key):
            return values[key] if key in values else original_value(key)

        with tempfile.TemporaryDirectory() as temp_dir:
            job_dir = Path(temp_dir)
            # scene 0은 이미 유효한 raw 이미지가 있는 "재개" 상태로 시작한다
            # (최종 scene_000.png는 없음) — 이전 실행에서 이미지 생성 자체는
            # 성공했지만 검수를 마치지 못하고 중단된 경우를 재현한다.
            Image.effect_noise((640, 360), 60).convert("RGB").save(job_dir / "scene_000_raw.png", "PNG")

            with patch("app.workers.images_worker.gemini_pressure", SequentialPressure()), \
                 patch("app.workers.images_worker.runtime_config.value", side_effect=configured_value), \
                 patch("app.workers.images_worker.is_job_stopped", lambda job_id: False), \
                 patch.object(budget, "_job_path", lambda job, name: job_dir / name), \
                 patch.object(ImagesWorker, "_normalize_canvas", lambda self, path: None), \
                 patch.object(ImagesWorker, "_apply_image_overlays", lambda self, ctx, path: None), \
                 patch("app.workers.images_worker._inspect_generated_textless_image", lambda ctx, path, **kw: {}), \
                 patch("app.workers.images_worker._inspect_generated_visual_image", side_effect=fake_visual_qa):
                worker = ImagesWorker()
                worker.tts_subtitle_sync = {}
                worker.evidence_audit = {}
                worker.visual_mix_plan = {}
                response = worker._generate_parallel_scenes(
                    scenes_meta=scenes,
                    directed_specs={},
                    market_snapshot={},
                    character_reference_paths=[],
                    character_style_prompt="none",
                    lora_model_id=None,
                    lora_trigger_word=None,
                    lora_scale=None,
                    ai_provider=provider,
                    job_dir=job_dir,
                    job_id=9006,
                )

            # 핵심 회귀 검증: scene 0의 재개 경로 QA 실패가 scene 1의 제출/완료를
            # 막지 않고, 배치 전체가 "non-retryable error"로 중단되지 않아야 한다.
            self.assertEqual(provider.calls, ["scene_1"])
            self.assertEqual([s["index"] for s in response["scenes"]], [1])
            self.assertTrue(response["requires_manual_review"])
            self.assertIn("SCENE_HELD_FOR_REVIEW:scene_0", response["review_reasons"])


if __name__ == "__main__":
    unittest.main()
