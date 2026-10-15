from app.workers.script_worker import (
    ScriptWorker,
    _classify_scene_types,
    _needs_dialogue_length_rewrite,
    reclassify_scenes,
)


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


def test_dialogue_rewrite_placeholder_prompt_ko_does_not_force_text_classification():
    """2026-10-07 사용자 재현(job 14, 씬 0): title 수정 후에도 여전히
    "text"로 분류됐다. _structured_script_from_dialogue_lines()가 모든
    장면에 공통으로 넣는 고정 플레이스홀더 prompt_ko("대사 의미를 설명하는
    한국형 금융 상황 장면")의 "의미"가 텍스트형 신호와 우연히 겹친 것이
    원인이었다. 이 틀 문구는 실제 시각 프롬프트가 아니므로 분류 근거에서
    빠져야 한다."""
    scene = _classify_scene_types([{
        "section": "intro",
        "content": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "prompt_ko": "대사 의미를 설명하는 한국형 금융 상황 장면",
    }])[0]

    assert scene["scene_type"] == "general"


def test_style_suffix_digit_in_prompt_en_does_not_force_a_spurious_metric_classification():
    """2026-10-14 사용자 재현(job 15): 흉기 난동 사건을 다루는 범죄 뉴스
    대본인데, 숫자·수치 신호가 전혀 없는 인트로·결론 장면이 "metric"으로
    분류돼 엉뚱한 주식 하락 차트가 그려졌다. 원인은 모든 장면의 prompt_en에
    공통으로 들어가는 화풍 태그 "original 2D Korean editorial illustration"의
    숫자 "2"(2D)를 수치 신호로 오인한 것이었다 — title/prompt_ko에서 이미
    같은 종류의 버그를 두 번 고쳤던 것과 동일한 패턴. prompt_en은 AI가 쓴
    시각 프롬프트이지 실제 내러티브 신호가 아니므로 분류 근거에서 빠져야
    한다."""
    scene = _classify_scene_types([{
        "section": "intro",
        "content": "양손에 흉기를 묶고 대낮 마트에 나타났습니다. 단순한 흉기 난동이 아니었습니다.",
        "text": "양손에 흉기를 묶고 대낮 마트에 나타났습니다. 단순한 흉기 난동이 아니었습니다.",
        "prompt_en": (
            "large discount mart exterior daytime, multiple police vehicles with "
            "lights on parked outside, police line tape across entrance, tense "
            "emergency scene, original 2D Korean editorial illustration, bold ink "
            "outlines, cel shading"
        ),
    }])[0]

    assert scene["scene_type"] != "metric"


def test_data_section_alone_does_not_force_a_metric_classification_without_real_signals():
    """2026-10-14 사용자 재현(job 18 scene 5·7): 흉기 난동 범죄 뉴스
    대본에서 "그것도 전자발찌를 찬 상태에서 이 범행을 저질렀습니다"처럼
    수치가 전혀 없는 장면이, 서사 구조상 "data" 단계에 배치됐다는 이유만으로
    metric으로 분류됐다. metric 분류는 이미지 프롬프트 생성 단계에서
    "금융 애니메이션 채널·경제 상황" 시스템 프롬프트를 강제로 씌워, 전자발찌
    내용과 무관한 데이터랩풍(SF스러운) 배경이 나오는 원인이 됐다. section은
    서사 구조상의 위치일 뿐 내용을 보장하지 않으므로, 실제 수치 신호가 없으면
    metric이 아니라 general로 분류돼야 한다."""
    scene = _classify_scene_types([{
        "section": "data",
        "content": "그것도 전자발찌를 찬 상태에서 이 범행을 저질렀습니다.",
    }])[0]

    assert scene["scene_type"] == "general"


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


def test_reclassify_scenes_refreshes_metadata_but_keeps_narration_byte_identical():
    """2026-10-07 사용자 재현(job 14, 씬 0): 2026-10-06에 승인된 대본은 오늘
    고친 분류 버그(자동 번호 제목, 플레이스홀더 문구) 수정의 혜택을 못 받고
    DB에 저장된 옛 scene_type·배경 설정을 그대로 쓴다. reclassify_scenes는
    Claude를 호출하지 않고 scene_type과 art_direction의 topic/setting/props만
    최신 코드로 다시 계산하며, 승인 대본 원문(TTS/자막의 단일 기준)은 한
    글자도 바꾸지 않는다."""
    stale_scene = {
        "title": "장면 001",
        "section": "intro",
        "content": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "text": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "text_for_tts": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "screen_texts": ["저 군대 안 갑니다!"],
        "archetype": "briefing_podium",
        "specific_props": "마이크 앞에 놓인 금메달, 조명이 쏟아지는 발표대",
        "scene_type": "metric",  # 옛 코드가 남긴 오분류
        "selection_reason": "대본에 검증 대상 수치·등락·규모 표현이 있어 지표형으로 분류",
        "art_direction": {
            "family": "hero_metaphor",
            "topic": "finance",
            "setting": "premium Korean finance editorial studio",
            "props": ["financial chart silhouette", "briefing screen", "document folder"],
            "wardrobe": "tailored navy analyst suit with a gold accent",
            "character_required": True,
        },
    }

    result = reclassify_scenes([stale_scene])
    scene = result[0]

    # 서사 신호만으로 다시 분류하면 general로 바뀐다 (더는 metric이 아님).
    assert scene["scene_type"] == "general"
    # 배경도 더는 금융으로 강제되지 않는다.
    assert scene["art_direction"]["topic"] != "finance"
    assert "finance" not in scene["art_direction"]["setting"].lower()
    # 이미 올바른 archetype/소품 지시(스크립트 단계 결정)는 그대로 유지된다.
    assert scene["archetype"] == "briefing_podium"
    assert scene["specific_props"] == stale_scene["specific_props"]
    # 승인 대본 원문은 한 글자도 바뀌지 않는다.
    assert scene["content"] == stale_scene["content"]
    assert scene["text"] == stale_scene["text"]
    assert scene["text_for_tts"] == stale_scene["text_for_tts"]
    assert scene["screen_texts"] == stale_scene["screen_texts"]
    # art_direction의 다른 필드(family/wardrobe 등)도 그대로 유지된다.
    assert scene["art_direction"]["family"] == "hero_metaphor"
    assert scene["art_direction"]["wardrobe"] == "tailored navy analyst suit with a gold accent"


def test_short_dialogue_requests_a_length_rewrite_before_tts():
    assert _needs_dialogue_length_rewrite("가" * 900, 1_000) is True
    assert _needs_dialogue_length_rewrite("가" * 920, 1_000) is True
    assert _needs_dialogue_length_rewrite("가" * 950, 1_000) is False
    assert _needs_dialogue_length_rewrite("가" * 1_151, 1_000) is True
