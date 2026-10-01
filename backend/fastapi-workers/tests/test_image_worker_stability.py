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


if __name__ == "__main__":
    unittest.main()
