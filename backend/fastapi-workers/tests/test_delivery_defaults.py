import unittest
from unittest.mock import patch

from app import runtime_config
from app.utils.script_length import (
    effective_duration_tolerance,
    effective_tts_measured_duration_tolerance,
    make_length_contract,
)
from app.workers.tts_worker import TtsWorker
from app.workers.script_worker import _is_market_level_forecast


class DeliveryDefaultsTests(unittest.TestCase):
    def test_five_minute_contract_uses_voice_calibrated_baseline(self):
        contract = make_length_contract(5, base_cpm=445, speed=1.0)
        self.assertEqual(contract["target_seconds"], 300)
        self.assertEqual(contract["target_chars"], 2225)

    def test_default_voice_delivery_matches_reference_breaths(self):
        self.assertEqual(runtime_config.value("tts_speed"), 0.9)
        self.assertEqual(runtime_config.value("tts_sentence_pause_ms"), 350)

    @patch("app.utils.script_length.resolve_cpm", return_value=(400.0, 2))
    def test_speed_specific_calibration_is_not_scaled_twice(self, _resolve_cpm):
        contract = make_length_contract(5, base_cpm=445, speed=0.9)

        self.assertEqual(contract["target_chars"], 2000)
        self.assertEqual(contract["effective_cpm"], 400.0)
        self.assertEqual(runtime_config.value("tts_paragraph_pause_ms"), 400)
        self.assertEqual(runtime_config.value("tts_thought_group_pause_ms"), 110)
        characters = [
            {"text": "A", "start": 0.0, "end": 0.1},
            {"text": ".", "start": 0.1, "end": 0.2},
            {"text": "B", "start": 0.2, "end": 0.3},
        ]
        # No native boundary/whitespace: do not cut through connected speech.
        self.assertEqual(TtsWorker._sentence_pause_points(characters), [])

    @patch("app.utils.script_length.resolve_cpm", return_value=(473.87, 1))
    def test_one_matching_real_tts_sample_corrects_the_next_script_length(self, _resolve_cpm):
        contract = make_length_contract(5, base_cpm=445, speed=0.9)

        self.assertEqual(contract["target_chars"], 2369)
        self.assertEqual(contract["effective_cpm"], 473.87)
        self.assertEqual(contract["calibration_samples"], 1)

    @patch("app.runtime_config.value", return_value=0.25)
    @patch("app.utils.script_length.resolve_cpm", return_value=(473.87, 1))
    def test_broad_env_tolerance_cannot_allow_a_short_five_minute_script(self, _resolve_cpm, _value):
        contract = make_length_contract(5, base_cpm=400, speed=0.9)

        self.assertEqual(contract["min_chars"], 2251)
        self.assertEqual(contract["max_chars"], 2487)
        self.assertEqual(contract["tolerance_pct"], 5)
        self.assertEqual(effective_duration_tolerance(0.25), 0.05)

    def test_measured_tts_duration_keeps_the_configured_25_percent_allowance(self):
        """2026-09-29 사용자 재현: job 12가 1분 목표에서 실제 75.6~76.7초가
        나왔는데도 5%(≈3초) 캡에 걸려 계속 실패했다. TTS_DURATION_TOLERANCE
        환경 기본값 자체가 0.25인 것에서 알 수 있듯, 실제 발화 길이는 억양·쉼
        때문에 대본 글자수 계약(5%)보다 자연스러운 편차가 더 커야 한다. 이
        허용치는 글자수 계약용 5% 캡과 분리되어야 한다."""
        self.assertEqual(effective_tts_measured_duration_tolerance(0.25), 0.25)
        # 그래도 설정 실수로 지나치게 커지는 것은 막는다.
        self.assertEqual(effective_tts_measured_duration_tolerance(0.9), 0.50)
        self.assertEqual(effective_tts_measured_duration_tolerance(0.0), 0.01)
        # 대본 글자수 계약용 캡은 그대로 5%로 유지된다(회귀 방지).
        self.assertEqual(effective_duration_tolerance(0.25), 0.05)

    def test_measured_tts_duration_honors_the_2026_10_01_fifty_percent_decision(self):
        """2026-10-01 사용자 결정: 5분·10분·15분 영상도 목표 분량에 정확히
        맞출 필요 없다며 tts_duration_tolerance를 25%→50%로 넓혔다. 그런데
        job 13 재시도(1분 목표, 실측 85.2초=+42%)가 "허용 30% 범위"로
        계속 거부됐다 — runtime_config는 0.5를 반영했지만, 이 모듈의 별도
        안전 상한(MAX_TTS_MEASURED_DURATION_TOLERANCE=0.30, 2026-09-29에
        정한 값)이 그보다 낮게 설정값을 다시 깎고 있었다. 설정 UI/엔드포인트
        수정만으로는 반영되지 않는 숨은 공통 계약 캡이었다. 사용자의 최종
        결정(50%)을 안전 상한에도 반영한다."""
        self.assertEqual(effective_tts_measured_duration_tolerance(0.5), 0.5)
        target_seconds = 60.0
        actual_seconds = 85.2
        allowed_delta = target_seconds * effective_tts_measured_duration_tolerance(0.5)
        self.assertGreaterEqual(allowed_delta, actual_seconds - target_seconds)

    def test_default_images_are_pro_2k_with_a_mascot(self):
        self.assertEqual(runtime_config.value("image_quality_tier"), "pro")

    def test_broad_market_outlook_does_not_require_an_exact_news_headline(self):
        self.assertTrue(_is_market_level_forecast(["미국 주식 하반기 전망"]))
        self.assertFalse(_is_market_level_forecast(["삼성전자 반도체 실적"]))


if __name__ == "__main__":
    unittest.main()
