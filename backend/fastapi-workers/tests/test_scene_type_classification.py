from app.workers.script_worker import ScriptWorker, _classify_scene_types, _needs_dialogue_length_rewrite


def test_scene_type_classification_keeps_reason_for_each_supported_type():
    scenes = _classify_scene_types([
        {
            "section": "data",
            "content": "고점과 종가의 변화를 추이 차트로 비교합니다.",
            "visual_intent": "두 시점의 하락 폭을 선 그래프로 보여 줍니다.",
        },
        {
            "section": "scenario",
            "content": "공급망의 단계와 연결 관계를 차례로 설명합니다.",
        },
        {
            "section": "data",
            "content": "시가총액 규모와 월간 낙폭을 확인합니다.",
        },
        {
            "section": "background",
            "content": "이 용어의 의미를 먼저 정리합니다.",
        },
        {
            "section": "action",
            "content": "투자자가 확인할 관점을 설명합니다.",
        },
    ])

    assert [scene["scene_type"] for scene in scenes] == [
        "graph", "diagram", "metric", "text", "general",
    ]
    assert all(scene["selection_reason"] for scene in scenes)


def test_auto_numbered_scene_title_does_not_force_a_spurious_metric_classification():
    """2026-10-07 사용자 재현(job 14, 씬 0): "저 군대 안 갑니다!" 인터뷰 발언
    장면에 숫자·시장 신호가 전혀 없는데도 "metric"으로 분류돼, V5 아키타입
    추천이 금융 차트가 있는 data_lab으로 떨어졌다. 원인은
    _scene_type_source_text가 "title" 필드도 합쳐서 숫자를 찾는데, 모든
    장면 제목이 "장면 001"처럼 자동 번호가 매겨져 있어 실제 내용과 무관하게
    항상 숫자가 걸렸기 때문이다."""
    scene = _classify_scene_types([{
        "title": "장면 001",
        "section": "intro",
        "content": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
    }])[0]

    assert scene["scene_type"] != "metric"


def test_graph_signal_has_priority_over_data_section_metric_fallback():
    scene = _classify_scene_types([{
        "section": "data",
        "content": "일별 등락률을 막대그래프로 비교합니다.",
    }])[0]

    assert scene["scene_type"] == "graph"
    assert "그래프형" in scene["selection_reason"]


def test_mock_script_response_also_contains_scene_type_contract():
    result = ScriptWorker()._mock_generate("코스피", "코스피", 1, 1)

    assert result["sections"]
    assert all(scene["scene_type"] in {"general", "metric", "graph", "diagram", "text"} for scene in result["sections"])
    assert all(scene["selection_reason"] for scene in result["sections"])


def test_longform_numeric_scenes_are_rebalanced_into_general_situations():
    scenes = _classify_scene_types([
        {"section": "data", "content": f"시장 지표 {index}%의 변화를 설명합니다."}
        for index in range(30)
    ])

    metric_scenes = [scene for scene in scenes if scene["scene_type"] == "metric"]
    general_scenes = [scene for scene in scenes if scene["scene_type"] == "general"]

    assert len(metric_scenes) == 5  # round(30 * 0.18)
    assert len(general_scenes) == 25
    assert all("상한" in scene["selection_reason"] for scene in general_scenes)


def test_longform_diagram_scenes_are_rebalanced_into_general_situations():
    scenes = _classify_scene_types([
        {"section": "scenario", "content": f"공급망 단계와 연결 관계 {index}를 설명합니다."}
        for index in range(30)
    ])

    diagram_scenes = [scene for scene in scenes if scene["scene_type"] == "diagram"]
    general_scenes = [scene for scene in scenes if scene["scene_type"] == "general"]

    assert len(diagram_scenes) == 3  # round(30 * 0.10)
    assert len(general_scenes) == 27
    assert all("상한" in scene["selection_reason"] for scene in general_scenes)


def test_short_dialogue_requests_a_length_rewrite_before_tts():
    assert _needs_dialogue_length_rewrite("가" * 900, 1_000) is True
    assert _needs_dialogue_length_rewrite("가" * 920, 1_000) is True
    assert _needs_dialogue_length_rewrite("가" * 950, 1_000) is False
    assert _needs_dialogue_length_rewrite("가" * 1_151, 1_000) is True
