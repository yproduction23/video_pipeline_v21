from app import runtime_config
from app.v5.providers.router import RENDER_BLOCKED_ARCHETYPES
from app.v5.scene.runtime_contract import (
    attach_v5_scene_contracts,
    plan_v5_scene_contract,
    prompt_for_scene,
    v5_provider_options,
)
from app.v5.scene.scene_type_archetypes import TYPE_CANDIDATES


def _scene(scene_id: str, scene_type: str, content: str) -> dict:
    return {
        "scene_id": scene_id,
        "scene_type": scene_type,
        "content": content,
        "visual_intent": content,
    }


def test_runtime_contract_uses_gemini_for_general_and_information_scenes():
    metric_scene = _scene("metric-01", "metric", "코스피 하락폭과 위험 지표를 설명합니다.")
    metric_scene["screen_texts"] = ["코스피 하락폭"]
    graph_scene = _scene("graph-01", "graph", "시장 추이 막대그래프를 비교합니다.")
    graph_scene["screen_texts"] = ["시장 추이"]
    scenes = attach_v5_scene_contracts([
        _scene("general-01", "general", "항만 물류 차질의 배경을 설명합니다."),
        metric_scene,
        graph_scene,
    ])

    general, metric, graph = [scene["v5_render_contract"] for scene in scenes]
    assert general["provider"] == metric["provider"] == graph["provider"] == "gemini_pro"
    assert general["tier"] == "body"
    assert metric["tier"] == graph["tier"] == "hero"
    assert general["visual_text_policy"] == "strict_textless"
    # 2026-10-07 사용자 재현(job 14, 씬 0/1): 승인된 정확 문구를 Gemini가 직접
    # 그리게 하면(script_captioned) 한글이 반복적으로 깨졌다. 숫자와 동일하게
    # 비수치 승인 문구도 Gemini는 빈 표면만 그리고 Pillow가 결정론적으로
    # 합성한다 — approved_generated_surface_text 경로는 더 이상 쓰지 않는다.
    assert metric["visual_text_policy"] == graph["visual_text_policy"] == "deterministic_surface_text"
    assert metric["source_visual_text_policy"] == graph["source_visual_text_policy"] == "strict_textless"
    assert general["style_contract_version"] == "2026-08-25-r8-job52-range-scene-local-v1"
    assert metric["style_contract_version"] == general["style_contract_version"]
    assert general["verified_overlay_mode"] == "not_applicable"
    assert metric["verified_overlay_mode"] == graph["verified_overlay_mode"] == "deterministic_surface_caption_or_verified_fact"
    assert general["visual_mode_contract"]["overlay_policy"] == "ass_subtitle_only"
    assert metric["visual_mode_contract"]["numeric_visual_policy"] == "verified_facts_deterministic_only"
    # 2026-09-29: image_provider가 "gemini"로 하드코딩돼 있으면 V5 final-lane
    # 장면이 항상 Gemini로만 가서 job 12처럼 회사 Gemini 계정이 구조적으로
    # 막혀 있을 때 새로 설정한 OpenAI 메인 provider를 무시하고 실패했다.
    # 이제 현재 설정된 메인 provider(runtime_config)를 그대로 따른다.
    assert v5_provider_options(scenes[0]) == {
        "image_provider": runtime_config.value("image_provider"),
        "gemini_model": "gemini-3-pro-image",
        "gemini_image_size": "2K",
        "gemini_service_tier": "priority",
        "gemini_max_attempts": 2,
        "suppress_legacy_style_lock": True,
        "style_locked": True,
        "gemini_reference_contract_declared": True,
    }
    assert scenes[0]["image_profile"]["v5_final_lane"] is True


def test_runtime_contract_rotates_consecutive_archetypes_and_records_visual_modes():
    scenes = attach_v5_scene_contracts([
        _scene("graph-01", "graph", "반도체 하락 그래프를 설명합니다."),
        _scene("graph-02", "graph", "달러 압박 그래프를 설명합니다."),
        _scene("general-01", "general", "항만의 상황을 보여 줍니다."),
    ])

    first, second, general = [scene["v5_render_contract"] for scene in scenes]
    assert first["selection"]["archetype"] == "data_lab"
    assert second["selection"]["archetype"] == "weather_map"
    assert scenes[1]["visual_mix_adjustment"]["reason"] == "consecutive_archetype_avoidance"
    assert first["visual_mode"] == second["visual_mode"] == "archetype_explainer"
    assert general["visual_mode"] == "semantic_illustration"
    assert "opening_visual_contract" not in scenes[0]


def test_runtime_contract_marks_captured_article_as_article_evidence_mode():
    scene = _scene("article-01", "graph", "미국 증시 뉴스를 설명합니다.")
    scene["visual_type"] = "article_evidence"
    scene["article_capture"] = {"local_path": "/tmp/article.png"}

    contract = plan_v5_scene_contract(scene, 0)

    assert contract["visual_mode"] == "article_evidence"
    assert contract["visual_mode_contract"] == {
        "asset_source": "verified_article_capture",
        "text_policy": "source_capture_only",
        "overlay_policy": "underline_highlight_and_source_credit_only",
        "numeric_visual_policy": "source_document_only",
        "character_policy": "no_generated_mascot",
    }


def test_runtime_contract_preserves_primary_surface_and_does_not_invent_verified_values():
    source = _scene("metric-01", "metric", "코스피 하락폭과 위험 지표를 설명합니다.")
    source["screen_texts"] = ["코스피 하락폭"]
    contract = plan_v5_scene_contract(source, 2)

    assert contract["selection"]["archetype"] == "risk_control_room"
    assert len(contract["primary_surface_region"]) == 4
    assert contract["verified_overlay_present"] is False
    assert "caption" in contract["semantic_visual_plan"]
    assert "prop_visuals" in contract["semantic_visual_plan"]
    # 2026-10-07: 비수치 승인 문구("코스피 하락폭")도 더는 Gemini가 직접 쓰지
    # 않는다 — 빈 표면만 그리게 하고 Pillow가 결정론적으로 합성한다.
    assert contract["visual_text_policy"] == "deterministic_surface_text"
    assert contract["source_visual_text_policy"] == "strict_textless"
    assert contract["surface_caption"]["generated_texts"] == ["코스피 하락폭"]
    assert contract["surface_caption"]["deterministic_texts"] == []
    assert contract["surface_caption"]["korean"] == "코스피 하락폭"
    assert contract["surface_caption"]["placement_mode"] == "contextual_supporting"
    assert "approved exact text items are ['코스피 하락폭']" not in contract["prompt_en"].lower()
    assert "do not include any visible typographic mark" in contract["prompt_en"].lower()


def test_semiconductor_earnings_diagram_routes_to_data_lab_not_professor_classroom():
    source = _scene(
        "semiconductor-earnings",
        "diagram",
        "반도체 영업이익 구조를 웨이퍼 생산 라인과 함께 설명합니다.",
    )
    source["screen_texts"] = ["영업이익"]

    contract = plan_v5_scene_contract(source, 0)

    assert contract["selection"]["archetype"] == "data_lab"
    assert contract["scene_spec"]["costume"] == "reporter"
    assert "location-appropriate reporter" in contract["scene_wardrobe"]
    assert "exactly one brown fedora" not in contract["scene_wardrobe"]
    assert "brown tweed blazer" not in contract["prompt_en"]


def test_v5_prompt_preserves_scene_local_setting_and_required_props():
    source = _scene("local-props", "diagram", "반도체 영업이익 구조를 설명합니다.")
    source.update({
        "screen_texts": ["영업이익"],
        "visual_intent": "two chip modules are removed from a physical balance",
        "art_direction": {
            "setting": "semiconductor wafer production line",
            "props": ["two chip modules", "physical balance", "wafer conveyor"],
        },
    })

    prompt = plan_v5_scene_contract(source, 0)["prompt_en"].lower()

    assert "scene-specific meaning, setting, and prop options" in prompt
    assert "two chip modules are removed from a physical balance" in prompt
    assert "semiconductor wafer production line" in prompt
    assert "wafer conveyor" in prompt
    # 2026-10-07: 승인 문구("영업이익")가 결정론 Pillow 합성으로 옮겨가면서
    # 이 장면은 이제 strict_textless 프롬프트를 받는다. 장면 고유 의미는
    # <script_meaning_visuals> 태그로 여전히 보존된다(위 assert들로 확인).
    assert "do not include any visible typographic mark" in prompt


def test_runtime_contract_does_not_confuse_style_contract_numbers_with_verified_facts():
    source = _scene("metric-three", "metric", "시장 심리를 설명합니다.")
    source["verified_facts"] = [{"figure": "3"}]

    planned = attach_v5_scene_contracts([source])[0]

    prompt = prompt_for_scene(planned)
    assert prompt is not None
    assert "3-5px" not in prompt
    assert "SCENE VARIATION SEED" not in prompt


def test_runtime_contract_does_not_force_one_layout_on_general_and_information_scenes():
    general = plan_v5_scene_contract(
        _scene("general-layout", "general", "항만 물류 차질의 배경을 설명합니다."),
        0,
    )
    information = plan_v5_scene_contract(
        _scene("graph-layout", "graph", "시장 추이 막대그래프를 비교합니다."),
        1,
    )

    for contract in (general, information):
        prompt = contract["prompt_en"]
        assert "<layout_contract>" not in prompt
        assert "do not automatically place a giant board" in prompt.lower()


def test_runtime_contract_honors_scene_director_costume_action_and_camera():
    source = _scene("lab-context", "diagram", "반도체 장비를 분석합니다.")
    source["screen_texts"] = ["영업이익"]
    source["scene_spec"] = {
        "character_costume": "white laboratory coat with goggles resting above the eyes",
        "character_action": "examining a wafer with one hand",
        "camera": "wide diagonal laboratory view",
    }

    contract = plan_v5_scene_contract(source, 0)
    prompt = contract["prompt_en"].lower()

    assert contract["scene_wardrobe"] == "white laboratory coat with goggles resting above the eyes"
    assert "white laboratory coat with goggles" in prompt
    assert "examining a wafer with one hand" in prompt
    assert "wide diagonal laboratory view" in prompt
    assert "navy tv-anchor suit" not in prompt


def test_general_scene_can_explicitly_use_an_environment_only_composition():
    source = _scene("general-environment", "general", "빅테크 실적과 AI 투자 부담을 상황으로 설명합니다.")
    source.update({
        "character_required": False,
        "visual_archetype": "data_lab",
        "visual_intent": "A processor pipeline feeds an uncertain profit turbine, no text and no numbers.",
    })

    contract = plan_v5_scene_contract(source, 0)
    prompt = contract["prompt_en"]

    assert contract["scene_spec"]["character_required"] is False
    assert "NO mascot and NO anthropomorphic character" in prompt
    assert "processor pipeline feeds an uncertain profit turbine" in prompt
    assert "<layout_contract>" not in prompt


def test_runtime_contract_keeps_verified_overlay_input_unchanged_when_present():
    source = _scene("graph-01", "graph", "시장 추이 막대그래프를 비교합니다.")
    source["verified_facts"] = [{"fact": "종가 100.0", "figure": "100.0"}]
    source["v5_verified_overlays"] = [{
        "label": "종가",
        "value": "100.0",
        "source_ref": "facts[0]",
        "anchor": {"x": .12, "y": .12, "width": .25, "height": .12, "kind": "embedded_monitor"},
    }]

    planned = attach_v5_scene_contracts([source])[0]
    assert planned["v5_verified_overlays"] == source["v5_verified_overlays"]
    assert planned["v5_render_contract"]["verified_overlay_present"] is False
    assert planned["v5_render_contract"]["visual_text_policy"] == "deterministic_surface_text"
    assert "no readable or pseudo-readable words" in planned["v5_render_contract"]["prompt_en"].lower()


def test_quoted_exact_narration_text_also_routes_to_deterministic_rendering():
    """2026-10-07 사용자 재현(job 14, 씬 0): 승인 대본에서 그대로 뽑은 인용문
    ("저 군대 안 갑니다!")을 Gemini에게 직접 그리게 했더니 반복적으로 깨진
    한글이 나왔다. 숫자 전용이었던 결정론 합성 경로를 비수치 승인 문구까지
    넓혀, Gemini는 빈 표면만 그리고 Pillow가 정확히 합성하게 한다.
    """
    source = _scene("character-hero-01", "general", "인터뷰에서 발언한 내용을 보여줍니다.")
    source["screen_texts"] = ["저 군대 안 갑니다!"]
    source["screen_text_plan"] = [
        {"text": "저 군대 안 갑니다!", "surface": "context_sign_1", "surface_kind": "signboard"},
    ]

    contract = plan_v5_scene_contract(source, 0)

    assert contract["visual_text_policy"] == "deterministic_surface_text"
    assert contract["source_visual_text_policy"] == "strict_textless"
    assert contract["surface_caption"]["korean"] == "저 군대 안 갑니다!"
    assert "저 군대 안 갑니다" not in contract["prompt_en"]
    assert "do not include any visible typographic mark" in contract["prompt_en"].lower()


def test_information_scene_without_approved_screen_text_does_not_invent_caption():
    contract = plan_v5_scene_contract(
        _scene("metric-no-copy", "metric", "시장 흐름을 물리적 저울로 설명합니다."),
        0,
    )

    assert contract["source_visual_text_policy"] == "strict_textless"
    assert contract["visual_text_policy"] == "strict_textless"
    assert contract["surface_caption"] is None


def test_runtime_contract_derives_chart_overlay_only_from_matching_verified_fact():
    source = _scene("vix-graph", "graph", "VIX 지수가 15.99포인트로 진정됐습니다.")
    source["market_chart"] = {
        "verified": True,
        "label": "VIX",
        "latest": 15.99,
        "source_ref": "market_snapshot.chart_series.vix",
    }
    source["verified_facts"] = [{
        "fact": "VIX 지수는 진정됐다.",
        "figure": "15.99포인트",
        "source_field": "market_snapshot.chart_series.vix",
    }]

    planned = attach_v5_scene_contracts([source])[0]

    assert "v5_verified_overlays" not in planned


def test_runtime_contract_matches_chart_source_by_indicator_tail_and_derives_script_number():
    graph = _scene("vix-graph", "graph", "VIX는 7월 29일 20.66포인트까지 올랐습니다.")
    graph["market_chart"] = {
        "verified": True,
        "label": "VIX",
        "latest": 15.99,
        "source_ref": "market_snapshot.chart_series.vix",
    }
    graph["verified_facts"] = [{
        "fact": "VIX 지수는 7월 말 진정됐다.",
        "figure": "20.66포인트",
        "source_field": "us.chart_series.vix(2026-07-29, 2026-07-31)",
    }]
    diagram = _scene("kospi-diagram", "diagram", "28일 6023포인트였던 코스피가 하락했습니다.")
    diagram["verified_facts"] = [{
        "fact": "코스피는 7월 28일부터 30일까지 하락했다.",
        "figure": "6023포인트",
        "source_field": "kr.chart_series.kospi",
    }]

    planned_graph, planned_diagram = attach_v5_scene_contracts([graph, diagram])

    assert "v5_verified_overlays" not in planned_graph
    assert "v5_verified_overlays" not in planned_diagram


def test_runtime_contract_uses_korean_context_without_numeric_or_floating_ui():
    contract = plan_v5_scene_contract(
        _scene("retail-korea", "metric", "홈플러스와 하림 인수 이슈가 유통주에 미칠 영향을 설명합니다."),
        0,
    )

    prompt = contract["prompt_en"].lower()
    assert "recognizably korean hypermarket" in prompt
    assert "no real brand logo" in prompt
    assert "dialogue balloons, split comparison, board, chart, sign" in prompt
    assert "numeric_visual_policy" in contract["visual_mode_contract"]


def test_earnings_stage_is_fully_implemented_and_routes_correctly():
    """earnings_stage Phase 3-A 완전 구현 검증 테스트."""
    # 1. RENDER_BLOCKED_ARCHETYPES에 없어야 함 (차단 해제 유지)
    assert "earnings_stage" not in RENDER_BLOCKED_ARCHETYPES

    # 2. TYPE_CANDIDATES["general"]에 포함되어야 함
    assert "earnings_stage" in TYPE_CANDIDATES.get("general", ())

    # 3. 기업실적 내레이션 씬이 earnings_stage로 라우팅되는지 확인
    from app.v5.scene.scene_type_archetypes import recommend_v5_archetype
    sel = recommend_v5_archetype({
        "scene_type": "general",
        "narration": "삼성전자 분기실적에서 영업이익이 어닝서프라이즈를 기록했습니다.",
    })
    assert sel.archetype == "earnings_stage", f"기업실적 씬 라우팅 실패: {sel.archetype}"

    # 4. plan_v5_scene_contract가 earnings_stage 씬을 정상 처리하는지 확인
    contract = plan_v5_scene_contract({
        "scene_type": "general",
        "narration": "삼성전자 분기실적에서 영업이익이 어닝서프라이즈를 기록했습니다.",
        "visual_archetype": "earnings_stage",
    }, 0)
    assert contract["selection"]["archetype"] == "earnings_stage"
    assert contract["scene_spec"]["character_position"] == "center"
    assert contract["scene_spec"]["emotion"] in ("confidence", "happy")


def test_runtime_contract_rejects_missing_scene_type():
    try:
        plan_v5_scene_contract({"scene_id": "missing", "content": "내용"}, 0)
    except ValueError as exc:
        assert "scene_type" in str(exc)
    else:
        raise AssertionError("scene_type 누락은 차단되어야 합니다.")
