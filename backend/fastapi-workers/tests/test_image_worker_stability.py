import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import runtime_config
from app.utils.retry_policy import classify_image_error
from app.workers.images_worker import (
    ImagesWorker,
    _bounded_text_generation_prompt,
    _clear_scene_from_request_review,
    _image_prompt_cache_key,
    _image_provider_for_attempt,
    _requires_full_scene_regeneration,
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


if __name__ == "__main__":
    unittest.main()
