import unittest
from unittest.mock import MagicMock, patch
from app.workers.images_worker import STYLE_SUFFIX, ImagesWorker


class TestImagesWorkerStage3Wiring(unittest.TestCase):

    @patch.object(ImagesWorker, "_call_llm", return_value="A physical ticker board displaying USD/KRW Rate dropping to 10M low notch.")
    def test_build_prompt_from_narration_with_entities(self, mock_llm):
        worker = ImagesWorker()
        prompt = worker._build_prompt_from_narration(
            narration="원달러 환율이 10개월 만에 최저치",
            scene_type="comparison",
            title="Scene_6",
            core_entities=["원달러 환율"],
            core_figures=[{"raw": "10개월", "kind": "period"}],
            fictionalized_labels={"원달러 환율": "USD/KRW CORP"},
            visual_mode="article_evidence",
        )
        self.assertIn("CRITICAL ENTITY & FIGURE GROUNDING INSTRUCTIONS", mock_llm.call_args[0][1][0]["content"])
        self.assertIn("USD/KRW Exchange Rate", mock_llm.call_args[0][1][0]["content"])
        self.assertIn("10개월", mock_llm.call_args[0][1][0]["content"])

    @patch.object(ImagesWorker, "_call_llm", return_value="Goldie stands at a press podium, arm raised.")
    def test_metric_scene_still_gets_financial_channel_system_prompt(self, mock_llm):
        """회귀 가드: 수치/차트가 실제로 있는 장면은 기존 금융 채널 프레이밍을 유지해야 한다."""
        worker = ImagesWorker()
        worker._build_prompt_from_narration(
            narration="코스피가 전일 대비 2.3% 상승했습니다",
            scene_type="earnings_stage",
            title="Scene_1",
        )
        system = mock_llm.call_args[0][0]
        self.assertIn("financial animation channel", system)
        self.assertIn("economic situation", system)

    @patch.object(ImagesWorker, "_call_llm", return_value="Goldie stands at a press podium, arm raised.")
    def test_general_scene_gets_neutral_non_financial_system_prompt(self, mock_llm):
        """2026-10-12 job 14 scene 0 재현: 수치·차트 신호가 없는 general 장면도
        archetype(briefing_podium 등)이 scene_type 인자 자리를 채우면서
        기존 코드가 늘 '금융 채널 비주얼 디렉터' 프레이밍을 강제해, 금메달/군
        면제 논란처럼 금융과 무관한 장면에도 Claude가 주가 차트·불꽃놀이를
        계속 그려 넣었다. is_general_scene=True를 넘기면 중립적인 프레이밍을
        써야 하고, 금융 차트를 넣지 말라는 명시적 지시가 있어야 한다."""
        worker = ImagesWorker()
        worker._build_prompt_from_narration(
            narration="이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다",
            scene_type="briefing_podium",
            title="Scene_0",
            is_general_scene=True,
        )
        system = mock_llm.call_args[0][0]
        self.assertNotIn("financial animation channel", system)
        self.assertNotIn("economic situation", system)
        self.assertIn("no financial figures", system)

    @patch.object(ImagesWorker, "_call_llm", return_value="Goldie stands at a press podium, arm raised.")
    def test_general_scene_also_forbids_non_financial_data_boards(self, mock_llm):
        """2026-10-12 job 14 scene 3 재현: 기존 차트 금지 지시문이 '주가
        차트·캔들스틱·코스피'처럼 금융 어휘로만 좁혀져 있어, 일반 격자/표
        형태의 비금융 데이터 보드는 안 막혔다. 비전 QA의
        unexpected_chart_or_data_visualization은 금융 여부를 가리지 않고
        모든 차트·데이터 시각화를 잡으므로, 사전 차단 지시도 똑같이 넓어야
        한다."""
        worker = ImagesWorker()
        worker._build_prompt_from_narration(
            narration="면제 발언 타이밍이 적절했는지 묻는 논의가 이어졌습니다",
            scene_type="briefing_podium",
            title="Scene_3",
            is_general_scene=True,
        )
        system = mock_llm.call_args[0][0]
        self.assertIn("grid", system)
        self.assertIn("table", system)
        self.assertIn("schedule board", system)

    @patch.object(ImagesWorker, "_call_llm", return_value="Goldie stands at a press podium, arm raised.")
    def test_general_scene_requires_korean_national_team_kit_for_athletes(self, mock_llm):
        """2026-10-12 job 14 scene 7 재현: 국가대표 선수 장면인데도 지시가
        없어 "유럽 클럽 유니폼"처럼 국적 불명 복장이 나왔다. 운동선수가
        나오는 장면엔 태극마크/국가색 등 한국 국가대표임을 알아볼 수 있는
        복장을 명시한다."""
        worker = ImagesWorker()
        worker._build_prompt_from_narration(
            narration="국가대표 선수가 병역 의무와 축구 선수 경력 사이에서 고민합니다",
            scene_type="split_stage",
            title="Scene_7",
            is_general_scene=True,
        )
        system = mock_llm.call_args[0][0]
        self.assertIn("Korean national team kit", system)

    @patch.object(ImagesWorker, "_call_llm", return_value="Goldie stands at a press podium, arm raised.")
    def test_general_scene_requires_blank_chalkboards_and_whiteboards(self, mock_llm):
        """2026-10-12 job 14 scene 2 재현: 프롬프트가 "a green chalkboard"라고만
        쓰고 비어있다는 말을 안 하니, Gemini가 학습 데이터의 "칠판엔 뭔가
        적혀있다"는 습성대로 차트를 계속 채워 넣었다(unexpected_chart_or_
        data_visualization 반복 재발). 칠판·화이트보드는 명시적으로 비워두게
        한다."""
        worker = ImagesWorker()
        worker._build_prompt_from_narration(
            narration="논란이 된 전역모를 두고 교실에서 토론이 벌어집니다",
            scene_type="classroom",
            title="Scene_2",
            is_general_scene=True,
        )
        system = mock_llm.call_args[0][0]
        self.assertIn("chalkboard", system.lower())
        self.assertIn("blank", system.lower())

    @patch.object(ImagesWorker, "_call_llm", return_value="Goldie stands at a press podium, arm raised.")
    def test_general_scene_keeps_goldie_holding_the_scenes_central_prop(self, mock_llm):
        """2026-10-12 job 14 scene 2 재현: "배경 인물은 장면에 필요할 때만
        등장" 지시가 모호해서, Claude가 전역모라는 핵심 소품을 Goldie가 아닌
        배경 교사 캐릭터에게 쥐어줬다. 핵심 소품은 반드시 Goldie 본인이
        들거나 착용하게 명시한다."""
        worker = ImagesWorker()
        worker._build_prompt_from_narration(
            narration="논란이 된 전역모를 두고 교실에서 토론이 벌어집니다",
            scene_type="classroom",
            title="Scene_2",
            is_general_scene=True,
        )
        user_content = mock_llm.call_args[0][1][0]["content"]
        self.assertIn("Goldie must be the one holding", user_content)


class StyleSuffixKoreanVisualIdentityTests(unittest.TestCase):
    """2026-10-12 사용자 피드백(job 14 scene 3): 그림체 라벨의 'Korean'과 달리
    배경·소품·복장에는 한국적으로 알아볼 수 있는 요소가 전혀 지시되지 않아,
    군대 장면이 국적 불명의 범용 군사기지처럼 그려졌다. 모든 장면 프롬프트에
    공통으로 붙는 STYLE_SUFFIX에 한국 시각 정체성 요소를 추가한다."""

    def test_style_suffix_requests_korean_visual_identity_markers(self):
        self.assertIn("Taegeukgi", STYLE_SUFFIX)
        self.assertIn("ROK military", STYLE_SUFFIX)
        self.assertIn("KBS", STYLE_SUFFIX)
        self.assertIn("Korean public-institution", STYLE_SUFFIX)

    def test_style_suffix_describes_the_actual_taegeukgi_design(self):
        """2026-10-12 사용자 피드백(job 14 scene 4): 'Taegeukgi colors or emblem'
        처럼 모호하게 쓰니 Gemini가 실제 태극 문양 대신 횃불 같은 임의의 국가
        상징을 지어냈다. 흰 바탕·빨강파랑 태극 원·검은 4괘라는 실제 구성을
        명시해 다른 국기나 가상 엠블럼으로 대체되지 않게 한다."""
        self.assertIn("red-and-blue taegeuk circle", STYLE_SUFFIX)
        self.assertIn("four black trigrams", STYLE_SUFFIX)
        self.assertNotIn("Taegeukgi (Korean flag) colors or emblem", STYLE_SUFFIX)

    def test_style_suffix_signage_instruction_defers_to_text_contract(self):
        """한글 간판 지시가 기존 '승인 안 된 글자 금지' 문자 계약과 충돌하면 안 된다."""
        self.assertIn("without inventing readable", STYLE_SUFFIX)


if __name__ == "__main__":
    unittest.main()
