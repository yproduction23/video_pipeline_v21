import pytest

from app.v5.scene.prompt_builder import MASCOT_IDENTITY_LOCK, SceneSpec, build_prompt
from app.v5.scene.scene_type_archetypes import (
    ARCHETYPE_SURFACES,
    ArchetypeSelection,
    recommend_v5_archetype,
)


def _new_archetype_selection(archetype: str, scene_type: str = "graph") -> ArchetypeSelection:
    surfaces = ARCHETYPE_SURFACES[archetype]
    return ArchetypeSelection(
        scene_type=scene_type,
        archetype=archetype,
        physical_surfaces=surfaces,
        primary_physical_surface=surfaces[0],
        alternatives=(),
        selection_reason="테스트용 신규 archetype primary 표면 선택",
    )


def test_information_scene_uses_a_surface_candidate_without_forcing_one_board_layout():
    selection = recommend_v5_archetype({
        "scene_type": "metric",
        "content": "시장의 급락과 변동률을 설명합니다.",
    })
    spec = SceneSpec("metric-risk", selection.archetype, "concern", "formal", "present")

    prompt = build_prompt(spec, scene_type_selection=selection).lower()

    assert "in-scene fact-surface contract" in prompt
    assert "is a preferred information anchor, not a mandatory single-board layout" in prompt
    assert "other gauge, screen, map, placard, document, and console may carry only scene-approved exact strings" in prompt
    assert "do not invent decorative labels, sample figures, pseudo-text, or filler ticker symbols" in prompt
    assert "diegetic text placement" not in prompt
    assert "populated analog gauges, labeled control dials" not in prompt
    assert "never place a number or text in a floating card" in prompt
    assert "fixed prop count or studio layout" in prompt
    assert "do not include any visible typographic mark" not in prompt
    assert "channel visual range" in prompt


def test_script_captioned_contract_embeds_exact_caption_on_the_primary_prop():
    selection = _new_archetype_selection("data_lab")
    prompt = build_prompt(
        SceneSpec("captioned-data-lab", selection.archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
        visual_text_policy="script_captioned",
        semantic_caption="SEMICONDUCTOR GOES DOWN",
        semantic_direction="down",
    ).lower()

    assert "approved exact text items are ['semiconductor goes down']" in prompt
    assert "integrate its typography into the prop's material, perspective, lighting, and ink style" in prompt
    assert "no numerals or other unapproved korean or english words" in prompt
    assert "location-appropriate reporter or presenter outfit" in prompt
    assert "exactly one brown fedora" not in prompt


def test_unplanned_caption_keeps_scene_content_before_supporting_typography():
    selection = _new_archetype_selection("weather_map")
    prompt = build_prompt(
        SceneSpec("caption-first", selection.archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
        visual_text_policy="script_captioned",
        semantic_caption="FEAR IS COOLING",
        semantic_direction="down",
    )

    assert prompt.index("<character>") < prompt.index("<scene_local_typography>")
    assert "SCENE-LOCAL TYPOGRAPHY" in prompt
    assert "No text-surface layout was explicitly storyboarded" in prompt
    assert "Never create a new board" in prompt
    assert "<semantic_surface>" not in prompt
    assert "<fact_surface_contract>" not in prompt
    assert "Do not make the typography, one board, or one giant word" in prompt


def test_explicit_text_surface_plan_enables_planned_surface_contract():
    selection = _new_archetype_selection("weather_map")
    prompt = build_prompt(
        SceneSpec("planned-comparison", selection.archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
        visual_text_policy="script_captioned",
        semantic_caption="CURRENT\nDOWNGRADE",
        semantic_direction="down",
        text_surface_plan=[
            {"text": "CURRENT", "surface": "left forecast zone"},
            {"text": "DOWNGRADE", "surface": "right forecast zone"},
        ],
    )

    assert "Follow the explicit storyboard text-surface plan" in prompt
    assert "<semantic_surface>" in prompt
    assert "<fact_surface_contract>" in prompt


def test_general_scene_with_explicit_surface_plan_still_embeds_the_approved_caption():
    """2026-10-06 사용자 재현(job 14, 씬 0): "저 군대 안 갑니다!"는 승인 대본에서
    정확히 추출된 문구이고 signboard 표면 계획까지 있었는데도, scene_type이
    "general"이라는 이유만으로 visual_text_policy가 strict_textless로 강등되어
    Gemini 프롬프트에 그 문구가 전혀 전달되지 않았다. scene_type은 승인 문구를
    화면에 쓸 수 있는지를 결정하지 않는다 (runtime_contract.py의 기존 주석과
    동일한 원칙) — 명시적 표면 계획이 있으면 general이어도 그대로 반영해야 한다.
    """
    selection = _new_archetype_selection("briefing_podium", scene_type="general")
    prompt = build_prompt(
        SceneSpec("general-with-plan", selection.archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
        visual_text_policy="script_captioned",
        semantic_caption="저 군대 안 갑니다!",
        text_surface_plan=[
            {"text": "저 군대 안 갑니다!", "surface": "context_sign_1", "surface_kind": "signboard"},
        ],
    )

    assert "SCENE-LOCAL TYPOGRAPHY" in prompt
    assert "저 군대 안 갑니다!" in prompt
    assert "<semantic_surface>" in prompt
    assert "do not include any visible typographic mark" not in prompt.lower()


def test_general_scene_without_a_surface_plan_still_downgrades_to_strict_textless():
    """계획이 전혀 없는 general 장면까지 억지로 소품 문구를 넣으면 안 되므로,
    기존 안전 동작(strict_textless 강등)은 그대로 유지돼야 한다."""
    selection = _new_archetype_selection("briefing_podium", scene_type="general")
    prompt = build_prompt(
        SceneSpec("general-without-plan", selection.archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
        visual_text_policy="script_captioned",
        semantic_caption="저 군대 안 갑니다!",
    ).lower()

    assert "do not include any visible typographic mark" in prompt
    assert "scene-local typography" not in prompt


def test_general_scene_with_surface_plan_but_unmapped_archetype_falls_back_safely():
    """PROP_SURFACE_MAP에 없는 archetype(예: earnings_stage)에서는 KeyError로
    죽는 대신 기존 strict_textless 안전 경로로 폴백해야 한다."""
    selection = _new_archetype_selection("earnings_stage", scene_type="general")
    prompt = build_prompt(
        SceneSpec("general-unmapped-archetype", selection.archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
        visual_text_policy="script_captioned",
        semantic_caption="저 군대 안 갑니다!",
        text_surface_plan=[
            {"text": "저 군대 안 갑니다!", "surface": "context_sign_1", "surface_kind": "signboard"},
        ],
    ).lower()

    assert "do not include any visible typographic mark" in prompt


def test_v5_costumes_are_contextual_ranges_not_one_fixed_uniform():
    from app.v5.scene.prompt_builder import COSTUME_MAP

    assert len(set(COSTUME_MAP.values())) == len(COSTUME_MAP)
    assert all("exactly one brown fedora" not in value.lower() for value in COSTUME_MAP.values())
    assert "laboratory" in COSTUME_MAP["safety_vest"].lower() or "industrial" in COSTUME_MAP["safety_vest"].lower()
    assert "mandatory fedora-and-navy uniform" in COSTUME_MAP["formal"].lower()


def test_prompt_contains_a_character_continuity_range_not_a_frozen_face():
    prompt = build_prompt(SceneSpec("identity", "weather_map", "explain", "reporter", "present"))

    assert MASCOT_IDENTITY_LOCK in prompt
    assert "Do not copy one reference's eyelashes" in prompt
    assert "worried, confident, comic, scientific, formal, or investigative" in prompt


def test_weather_map_does_not_force_one_map_surface_or_character_size():
    prompt = build_prompt(SceneSpec("weather-small-mascot", "weather_map", "explain", "reporter", "present"))

    assert "follow this scene's own visual hierarchy" in prompt.lower()
    assert "one large curved map wall is the only map" not in prompt.lower()
    assert "do not automatically place a giant board" in prompt.lower()


def test_strict_textless_contract_reserves_the_same_prop_without_text_conflict():
    selection = recommend_v5_archetype({
        "scene_type": "graph",
        "content": "시장 추이 차트를 설명합니다.",
    })
    spec = SceneSpec("graph-data", selection.archetype, "explain", "reporter", "present")

    prompt = build_prompt(
        spec,
        visual_text_policy="strict_textless",
        scene_type_selection=selection,
    ).lower()

    assert "in-scene fact-surface contract" in prompt
    assert "do not draw text, digits, symbols, chart labels, or factual values there" in prompt
    assert "never create a blank monitor, detached ui card" in prompt
    assert "must be engraved, painted, chalked, printed, or projected directly" not in prompt
    assert "do not include any visible typographic mark" in prompt


def test_strict_textless_with_explicit_surface_plan_demands_one_blank_signboard():
    """2026-10-07 사용자 재현(job 14, 씬 0): "계약 문장"만으로 빈 표면을
    요청했더니 Gemini가 표면 전체를 차트로 꽉 채워, 나중에
    add_surface_caption이 글자를 얹을 빈 자리를 못 찾았다(실제 유료
    테스트로 확인). 명시된 표면 계획이 있으면 그 종류(signboard 등)를
    직접 지목해 그 표면 하나는 장식 없이 비워 두라고 요구해야 한다."""
    selection = recommend_v5_archetype({
        "scene_type": "metric",
        "content": "인터뷰 발언을 설명합니다.",
    })
    spec = SceneSpec("metric-quote", selection.archetype, "explain", "reporter", "present")

    prompt = build_prompt(
        spec,
        visual_text_policy="strict_textless",
        scene_type_selection=selection,
        text_surface_plan=[{
            "text": "저 군대 안 갑니다!",
            "surface": "context_sign_1",
            "surface_kind": "signboard",
        }],
    ).lower()

    assert "bordered, physical signboard" in prompt
    assert "stays plain, evenly lit, and free of charts, gauges" in prompt
    assert "do not substitute it with a chart, monitor, or dashboard" in prompt
    assert "do not draw text, digits, symbols, chart labels, or factual values there" in prompt


def test_general_strict_textless_scene_does_not_request_chart_iconography():
    """2026-10-07 사용자 재현(job 14, 씬 0): "저 군대 안 갑니다!" 기자회견
    장면처럼 정보형이 아닌 일반 서사 장면인데도, strict_textless의 공용
    배경 지시문이 "차트·게이지·막대/곡선 실루엣"을 요구해 금융 데이터와
    전혀 무관한 장면에 막대그래프가 계속 그려졌다. 정보형이 아니면 차트류
    도상을 아예 요구하지 않아야 한다."""
    selection = recommend_v5_archetype({
        "scene_type": "general",
        "content": "인터뷰 발언을 설명합니다.",
    })
    spec = SceneSpec("general-quote", selection.archetype, "explain", "reporter", "present")

    prompt = build_prompt(
        spec,
        visual_text_policy="strict_textless",
        scene_type_selection=selection,
        text_surface_plan=[{
            "text": "저 군대 안 갑니다!",
            "surface": "context_sign_1",
            "surface_kind": "signboard",
        }],
    ).lower()

    assert "do not add charts, gauges, dials, bar or curve silhouettes" in prompt
    assert "abstract bar and curve silhouettes without axes" not in prompt
    assert "color-only control dials" not in prompt
    assert "reserved for a short caption to be composited afterward" in prompt


def test_general_scene_with_explicit_plan_gets_the_stronger_blank_surface_contract():
    """2026-10-07 사용자 재현(job 14, 씬 0): 차트 문구를 없앤 뒤에도 Gemini가
    신호판 화면에 (차트는 아니지만) 다이어그램을 그려 결정론 합성이 빈
    표면을 못 찾았다. fact_surface_contract는 "signboard 표면은 차트·게이지·
    다이어그램·장식 없이 완전히 비어 있어야 한다"는 더 구체적인 계약을
    이미 갖고 있었지만, scene_type이 "general"이면 명시된 표면 계획이
    있어도 무조건 건너뛰었다. 계획이 있으면 general이어도 이 계약을
    받아야 한다."""
    selection = recommend_v5_archetype({
        "scene_type": "general",
        "content": "인터뷰 발언을 설명합니다.",
    })
    spec = SceneSpec("general-quote-plan", selection.archetype, "explain", "reporter", "present")

    prompt = build_prompt(
        spec,
        visual_text_policy="strict_textless",
        scene_type_selection=selection,
        text_surface_plan=[{
            "text": "저 군대 안 갑니다!",
            "surface": "context_sign_1",
            "surface_kind": "signboard",
        }],
    ).lower()

    assert "in-scene fact-surface contract" in prompt
    assert "bordered, physical signboard" in prompt
    assert "free of charts, gauges, diagrams, arrows, or decoration" in prompt


def test_general_scene_does_not_receive_a_number_or_fact_surface_instruction():
    selection = recommend_v5_archetype({
        "scene_type": "general",
        "content": "항만의 긴장된 현장을 설명합니다.",
    })
    spec = SceneSpec("general-port", selection.archetype, "alarm", "safety_vest", "alarmed_run")

    prompt = build_prompt(spec, scene_type_selection=selection).lower()

    assert "fact-surface contract" not in prompt
    assert "does not request numbers, chart ticks, metric displays, or factual data readouts" in prompt
    assert "do not include unapproved readable or pseudo-readable text" in prompt
    assert "sample figures" not in prompt
    assert "data-lab benchmark visual treatment" not in prompt


def test_information_data_lab_uses_supplied_approved_scene_range_without_one_studio_template():
    selection = _new_archetype_selection("data_lab")
    prompt = build_prompt(
        SceneSpec("data-lab-benchmark-style", selection.archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
    ).lower()

    assert selection.archetype == "data_lab"
    assert "data-lab range" in prompt
    assert "supplied approved laboratory and market-analysis scene references" in prompt
    assert "job 52" not in prompt
    assert "do not default to a broadcast studio" in prompt
    assert "one curved wall" in prompt


def test_mismatched_archetype_selection_fails_before_prompt_generation():
    selection = recommend_v5_archetype({"scene_type": "graph", "content": "시장 추이 차트"})
    mismatched = SceneSpec("wrong", "weather_map", "explain", "reporter", "present")

    with pytest.raises(ValueError, match="archetype"):
        build_prompt(mismatched, scene_type_selection=selection)


def test_trade_calculator_keeps_scale_as_a_candidate_without_banning_other_planned_surfaces():
    selection = recommend_v5_archetype({
        "scene_type": "graph",
        "content": "비교 막대와 비율을 설명합니다.",
    })
    spec = SceneSpec("trade-primary", selection.archetype, "confidence", "vest", "think")

    prompt = build_prompt(spec, scene_type_selection=selection).lower()

    assert selection.archetype == "trade_calculator"
    assert "broad engraved front plinth directly beneath the scale's central pillar" in prompt
    assert "preferred information anchor, not a mandatory single-board layout" in prompt
    assert "do not substitute the designated plinth" not in prompt


def test_risk_control_room_does_not_force_every_secondary_surface_to_be_blank():
    selection = recommend_v5_archetype({
        "scene_type": "metric",
        "content": "시장의 급락과 변동률을 설명합니다.",
    })
    spec = SceneSpec("risk-primary", selection.archetype, "concern", "formal", "present")

    prompt = build_prompt(spec, scene_type_selection=selection).lower()

    assert selection.archetype == "risk_control_room"
    assert "single large central analog gauge dial face embedded at eye level in the curved operations wall" in prompt
    assert "other gauge, screen, map, placard, document, and console may carry only scene-approved exact strings" in prompt
    assert "every secondary gauge" not in prompt


def test_weather_map_surface_is_a_candidate_not_an_exclusive_layout():
    selection = recommend_v5_archetype({
        "scene_type": "graph",
        "content": "미국 지역별 날씨와 시장 변동률 지도 그래프를 설명합니다.",
    })
    spec = SceneSpec("weather-primary", selection.archetype, "explain", "reporter", "present")

    prompt = build_prompt(spec, scene_type_selection=selection).lower()

    assert selection.archetype == "weather_map"
    assert "broad central geographic region of the single large curved illuminated map wall" in prompt
    assert "preferred information anchor, not a mandatory single-board layout" in prompt
    assert "freestanding forecast board" not in prompt


def test_classroom_chalk_area_is_not_the_only_possible_storyboard_surface():
    selection = recommend_v5_archetype({
        "scene_type": "diagram",
        "content": "공급망의 단계와 인과 관계를 설명하는 흐름도입니다.",
    })
    spec = SceneSpec("classroom-primary", selection.archetype, "explain", "professor", "point_left")

    prompt = build_prompt(spec, scene_type_selection=selection).lower()

    assert selection.archetype == "classroom"
    assert "broad central chalk-writing area of the large green teaching wall" in prompt
    assert "preferred information anchor, not a mandatory single-board layout" in prompt
    assert "pinned paper note, framed wall poster" not in prompt


def test_port_and_retail_offer_contextual_surface_candidates_without_exclusive_bans():
    port_selection = recommend_v5_archetype({"scene_type": "text", "content": "항만 물류 컨테이너 경고"})
    port = build_prompt(
        SceneSpec("port-primary", port_selection.archetype, "alarm", "safety_vest", "alarmed_run"),
        scene_type_selection=port_selection,
    ).lower()
    retail_selection = recommend_v5_archetype({"scene_type": "metric", "content": "매장 가격과 소비 지표"})
    retail = build_prompt(
        SceneSpec("retail-primary", retail_selection.archetype, "surprise", "analyst", "calculator_hold"),
        scene_type_selection=retail_selection,
    ).lower()

    assert "single largest foreground shipping container nearest the center dock" in port
    assert "preferred information anchor, not a mandatory single-board layout" in port
    assert "dock warning plate, customs sign" not in port
    assert "built-in receipt window recessed into the upper face of the single large checkout device" in retail
    assert "preferred information anchor, not a mandatory single-board layout" in retail
    assert "extra wall display, overhead promotional screen" not in retail


@pytest.mark.parametrize(
    "archetype",
    ("briefing_podium", "real_estate_office", "job_market_hall"),
)
def test_new_archetypes_do_not_apply_old_retry_specific_screen_bans_globally(archetype: str):
    selection = _new_archetype_selection(archetype)
    prompt = build_prompt(
        SceneSpec(f"{archetype}-screenless", archetype, "explain", "reporter", "present"),
        scene_type_selection=selection,
    ).lower()

    assert "outside the designated primary surface, do not create any monitor" not in prompt
    assert "screen-shaped rectangular information device at all" not in prompt
    assert "do not invent decorative labels, sample figures, pseudo-text, or filler ticker symbols" in prompt


def test_real_estate_and_job_market_devices_follow_scene_local_text_plan():
    real_estate = build_prompt(
        SceneSpec("real-estate-blank-device", "real_estate_office", "explain", "reporter", "present"),
        scene_type_selection=_new_archetype_selection("real_estate_office"),
    ).lower()
    job_market = build_prompt(
        SceneSpec("job-market-blank-device", "job_market_hall", "explain", "reporter", "present"),
        scene_type_selection=_new_archetype_selection("job_market_hall"),
    ).lower()

    assert "desk calculator display must remain completely blank" not in real_estate
    assert "scene-approved exact strings" in real_estate
    assert "queue-ticket machine must be a mechanical paper dispenser" not in job_market
    assert "scene-approved exact strings" in job_market


@pytest.mark.parametrize(
    ("archetype", "required_rule"),
    (
        (
            "job_market_hall",
            "do not create any ceiling-mounted or wall-mounted monitor, and the area in front of the consultation counter must not contain any kiosk",
        ),
    ),
)
def test_old_third_attempt_rule_is_not_promoted_to_a_global_contract(archetype: str, required_rule: str):
    prompt = build_prompt(
        SceneSpec(f"{archetype}-third-attempt", archetype, "explain", "reporter", "present"),
        scene_type_selection=_new_archetype_selection(archetype),
    ).lower()

    assert prompt.count(required_rule) == 0
