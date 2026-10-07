import json
from pathlib import Path

from app.v5.scene.prompt_builder import ARCHETYPES
from app.v5.scene.scene_type_archetypes import (
    ARCHETYPE_SURFACES,
    PRIMARY_SURFACE_REGIONS,
    TYPE_CANDIDATES,
    primary_surface_region,
    recommend_v5_archetype,
    validate_archetype_mapping,
)


def test_dialogue_rewrite_placeholder_does_not_route_general_scenes_to_classroom():
    """2026-10-07 사용자 재현(job 14, 씬 0): scene_type을 general로 고친
    뒤에도 archetype이 classroom으로 떨어졌다. _scene_text()가 title과
    prompt_ko를 포함하는데, 길이 재작성을 거친 장면의 prompt_ko는
    _structured_script_from_dialogue_lines()가 넣는 고정 플레이스홀더
    ("대사 의미를 설명하는...")였고 그 "설명"이 classroom 힌트와 우연히
    겹쳤다. 실제 대사 내용에는 "설명" 같은 단어가 전혀 없다."""
    selection = recommend_v5_archetype({
        "scene_type": "general",
        "title": "장면 001",
        "content": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "prompt_ko": "대사 의미를 설명하는 한국형 금융 상황 장면",
    })

    assert selection.archetype != "classroom"


def test_mapping_candidates_all_exist_and_have_physical_information_surfaces():
    validate_archetype_mapping()

    for candidates in TYPE_CANDIDATES.values():
        for archetype in candidates:
            assert archetype in ARCHETYPES
            assert ARCHETYPE_SURFACES[archetype]


def test_every_primary_surface_has_a_valid_normalized_region_for_quality_gate():
    assert set(PRIMARY_SURFACE_REGIONS) == set(ARCHETYPE_SURFACES)
    for archetype in ARCHETYPE_SURFACES:
        x, y, width, height = primary_surface_region(archetype)
        assert 0.0 <= x < 1.0
        assert 0.0 <= y < 1.0
        assert x + width <= 1.0
        assert y + height <= 1.0


def test_graph_metric_diagram_and_text_choose_information_dense_stages():
    graph = recommend_v5_archetype({"scene_type": "graph", "content": "지수 추이 차트"})
    metric = recommend_v5_archetype({"scene_type": "metric", "content": "시장의 급락과 변동률"})
    diagram = recommend_v5_archetype({"scene_type": "diagram", "content": "공급망의 단계와 인과 관계"})
    text = recommend_v5_archetype({"scene_type": "text", "content": "용어의 정의와 핵심 문구"})

    assert graph.archetype == "data_lab"
    assert metric.archetype == "risk_control_room"
    assert diagram.archetype == "classroom"
    assert text.archetype == "classroom"
    assert all(selection.physical_surfaces for selection in (graph, metric, diagram, text))
    assert all(
        selection.primary_physical_surface == selection.physical_surfaces[0]
        for selection in (graph, metric, diagram, text)
    )


def test_port_logistics_text_can_select_the_container_information_surface():
    selection = recommend_v5_archetype({"scene_type": "text", "content": "항만 물류와 컨테이너 수출 경고"})

    assert selection.archetype == "port_emergency"
    assert selection.primary_physical_surface == (
        "the broad painted front side of the single largest foreground shipping container nearest the center dock"
    )


def test_general_port_narrative_prefers_port_stage_over_background_wording():
    selection = recommend_v5_archetype({
        "scene_type": "general",
        "content": "A mascot explains the background while walking through a busy port with containers.",
    })

    assert selection.archetype == "port_emergency"


def test_v5_kospi_pilot_three_scenes_have_expected_stage_recommendations():
    path = Path(__file__).parents[1] / "out/v5_pilot/kospi_july_2026/v5_pilot_input.json"
    pilot = json.loads(path.read_text(encoding="utf-8"))
    scene_types = ("graph", "graph", "metric")

    recommendations = [
        recommend_v5_archetype({
            "scene_type": scene_type,
            "title": scene["scene_id"],
            "content": scene["narration"],
            "visual_intent": scene["visual_intent"],
        })
        for scene, scene_type in zip(pilot["scenes"], scene_types)
    ]

    assert [result.archetype for result in recommendations] == [
        "data_lab", "trade_calculator", "risk_control_room",
    ]
    assert all(result.selection_reason for result in recommendations)
    assert [result.primary_physical_surface for result in recommendations] == [
        "the storyboard-planned laboratory, production-line, control-room, or analysis surface",
        "the broad engraved front plinth directly beneath the scale's central pillar",
        "the single large central analog gauge dial face embedded at eye level in the curved operations wall",
    ]


def test_invalid_or_unclassified_scene_type_is_rejected():
    try:
        recommend_v5_archetype({"scene_type": "unknown"})
    except ValueError as exc:
        assert "scene_type" in str(exc)
    else:
        raise AssertionError("지원하지 않는 scene_type은 실패해야 합니다.")
