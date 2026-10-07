from pathlib import Path

import pytest
from PIL import Image

from app.workers.images_worker import (
    GeneratedImageTextDetectedError,
    GeneratedImageVisualContractError,
    _bounded_text_generation_prompt,
    _image_prompt_cache_key,
    _inspect_generated_textless_image,
    _inspect_generated_visual_image,
    _stable_image_lineage_fingerprint,
)
from app.utils.image_text_contract import visible_text_contract_result


def _png(tmp_path: Path) -> Path:
    path = tmp_path / "scene.png"
    Image.new("RGB", (64, 36), "navy").save(path)
    return path


def test_ocr_accepts_approved_text_and_defers_nonnumeric_spelling_to_visual_gate(tmp_path):
    scene = {
        "screen_texts": ["SK하이닉스"],
        "screen_text_validation": {"passed": True},
    }
    accepted = _inspect_generated_textless_image(
        scene, str(_png(tmp_path)), ocr_rows=[{"text": "SK하이닉스", "conf": "96"}],
    )
    assert accepted["passed"] is True

    deferred = _inspect_generated_textless_image(
        scene, str(_png(tmp_path)), ocr_rows=[{"text": "SK 하이느", "conf": "96"}],
    )
    assert deferred["passed"] is True
    assert deferred["review_required_nonnumeric_texts"] == ["SK 하이느"]


def test_strict_generated_text_lane_rejects_when_one_approved_label_is_missing(tmp_path):
    scene = {
        "screen_texts": ["경고", "전망치"],
        "screen_text_validation": {"passed": True},
        "generated_text_ocr_policy": {
            "version": "strict-scene-local-generated-text-v1",
            "require_all_approved": True,
            "reject_unapproved": True,
        },
    }

    with pytest.raises(GeneratedImageTextDetectedError, match="승인 문구 누락: 전망치"):
        _inspect_generated_textless_image(
            scene,
            str(_png(tmp_path)),
            ocr_rows=[{"text": "경고", "conf": "96"}],
        )


def test_strict_generated_text_lane_rejects_spaced_company_name_drift(tmp_path):
    scene = {
        "screen_texts": ["SK하이닉스"],
        "screen_text_validation": {"passed": True},
        "generated_text_ocr_policy": {
            "version": "strict-scene-local-generated-text-v1",
            "require_all_approved": True,
            "reject_unapproved": True,
        },
    }
    with pytest.raises(GeneratedImageTextDetectedError):
        _inspect_generated_textless_image(
            scene, str(_png(tmp_path)), ocr_rows=[{"text": "SK 하인스", "conf": "96"}],
        )


def test_strict_generated_text_lane_accepts_complete_fragmented_korean_tokens(tmp_path):
    scene = {
        "screen_texts": ["전망치"],
        "screen_text_validation": {"passed": True},
        "generated_text_ocr_policy": {
            "version": "strict-scene-local-generated-text-v1",
            "require_all_approved": True,
            "reject_unapproved": True,
        },
    }
    rows = [
        {"text": "전", "conf": "95", "word_num": "1", "block_num": "1", "par_num": "1", "line_num": "1"},
        {"text": "망", "conf": "95", "word_num": "2", "block_num": "1", "par_num": "1", "line_num": "1"},
        {"text": "치", "conf": "95", "word_num": "3", "block_num": "1", "par_num": "1", "line_num": "1"},
    ]

    result = _inspect_generated_textless_image(
        scene,
        str(_png(tmp_path)),
        ocr_rows=rows,
    )

    assert result["exact_generated_texts"] == ["전망치"]
    assert result["missing_generated_texts"] == []


def test_strict_generated_text_lane_rejects_readable_background_filler(tmp_path):
    scene = {
        "screen_texts": ["전망치"],
        "screen_text_validation": {"passed": True},
        "generated_text_ocr_policy": {
            "version": "strict-scene-local-generated-text-v1",
            "require_all_approved": True,
            "reject_unapproved": True,
        },
    }
    rows = [
        {"text": "전망치", "conf": "96", "word_num": "1"},
        {"text": "RISK", "conf": "96", "word_num": "1", "block_num": "2"},
    ]

    with pytest.raises(GeneratedImageTextDetectedError, match="RISK"):
        _inspect_generated_textless_image(scene, str(_png(tmp_path)), ocr_rows=rows)


def test_exact_narration_term_is_allowed_but_derived_word_and_number_are_not():
    scene = {
        "text": "배당도 주주환원의 방식이지만 자사주 소각과는 다릅니다.",
        "screen_texts": ["주주환원"],
    }

    result = visible_text_contract_result(["자사주", "배당금", "2021"], scene)

    assert result["narration_grounded_texts"] == ["자사주"]
    assert result["review_required_nonnumeric_texts"] == ["배당금"]
    assert result["review_required_numeric_texts"] == ["2021"]
    assert result["unexpected_texts"] == ["2021"]
    assert result["passed"] is False


def test_common_image_gate_rejects_unapproved_numeric_display_without_strict_lane(tmp_path):
    scene = {
        "text": "실적 전망이 엇갈리며 시장의 경계감이 커졌습니다.",
        "screen_texts": ["엇갈림"],
    }
    rows = [
        {"text": "엇갈림", "conf": "96", "word_num": "1"},
        {"text": "0000.000", "conf": "96", "word_num": "2"},
    ]

    with pytest.raises(GeneratedImageTextDetectedError, match="0000.000"):
        _inspect_generated_textless_image(scene, str(_png(tmp_path)), ocr_rows=rows)


def test_visual_gate_rejects_only_reported_scene_categories(tmp_path, monkeypatch):
    image = _png(tmp_path)
    monkeypatch.setattr("app.workers.images_worker.runtime_config.value", lambda key: True)
    monkeypatch.setattr(
        "app.workers.images_worker.assess_visual_alignment",
        lambda scenes, enabled, max_scenes: {
            "warnings": [],
            "reviewed": [{
                "index": 0,
                "failure_categories": ["character_extra_limbs"],
                "reason": "세 번째 손",
                "raw": {"scene_match": 90, "style_adherence": 90},
            }],
        },
    )

    with pytest.raises(GeneratedImageVisualContractError) as raised:
        _inspect_generated_visual_image({"index": 0}, str(image))

    assert raised.value.review["failure_categories"] == ["character_extra_limbs"]


def test_visual_gate_does_not_accept_unclassified_review_recommendation(tmp_path, monkeypatch):
    image = _png(tmp_path)
    monkeypatch.setattr("app.workers.images_worker.runtime_config.value", lambda key: True)
    monkeypatch.setattr(
        "app.workers.images_worker.assess_visual_alignment",
        lambda scenes, enabled, max_scenes: {
            "warnings": [],
            "reviewed": [{
                "index": 0,
                "retry_recommended": True,
                "failure_categories": [],
                "reason": "공통 품질 하한선 미달",
                "raw": {"scene_match": 72, "style_adherence": 76},
            }],
        },
    )

    with pytest.raises(GeneratedImageVisualContractError) as raised:
        _inspect_generated_visual_image({"index": 0}, str(image))

    assert raised.value.review["failure_categories"] == ["visual_quality_floor"]


def test_retry_keeps_information_props_and_targets_only_bad_surface():
    scene = {
        "screen_texts": ["영업이익"],
        "screen_text_validation": {"passed": True},
        "art_direction": {"wardrobe": "navy analyst suit"},
    }
    prompt = _bounded_text_generation_prompt(
        'A dense data lab with a monitor labeled "영업이익" and a second sign reading "SILVER"',
        retry=True,
        audit_target=scene,
    ).lower()

    assert "영업이익" in prompt
    assert "silver" not in prompt
    assert "do not remove screens, boards, diagrams, arrows" in prompt
    assert "natural connected anatomy" in prompt
    assert "soft forehead reflection highlight" in prompt
    assert "do not force one outfit or one neutral expression" in prompt
    assert "one giant board" in prompt
    assert "solid opaque scene-native monitor" in prompt
    assert "detached glass card" in prompt


def test_retry_feedback_asks_to_remove_an_unexpected_chart():
    """2026-10-07 사용자 재현(job 14, 씬 0): 비전 검수가 비정보형 장면의
    차트를 적발해도, 재시도 프롬프트에 그 교정 지시가 없으면 다음 시도도
    같은 차트를 또 그릴 수 있다."""
    scene = {"art_direction": {}}
    prompt = _bounded_text_generation_prompt(
        "A press briefing room with a podium and a presentation screen.",
        retry=True,
        retry_feedback={
            "failure_categories": ["unexpected_chart_or_data_visualization"],
            "reason": "금융 데이터와 무관한 장면에 막대그래프가 그려짐",
        },
        audit_target=scene,
    ).lower()

    assert "remove every bar chart, line graph, pie chart" in prompt
    assert "this narration is not about market or financial data" in prompt


def test_explicit_opaque_equipment_surface_is_binding_without_global_layout():
    scene = {
        "screen_texts": ["영업이익"],
        "screen_text_plan": [{
            "text": "영업이익",
            "surface": "existing solid machine-mounted production monitor",
            "surface_style": "opaque_equipment_display",
            "prominence": "secondary",
            "max_occurrences": 1,
        }],
        "screen_text_validation": {"passed": True},
    }

    prompt = _bounded_text_generation_prompt(
        'A semiconductor production line with an existing monitor labeled "영업이익"',
        audit_target=scene,
    ).lower()

    assert "existing solid machine-mounted production monitor" in prompt
    assert "solid opaque scene-mounted monitor" in prompt
    assert "detached translucent or holographic text card is forbidden" in prompt
    assert "unlettered scene-integrated visual surface" not in prompt


def test_generated_wording_prompt_does_not_present_bracket_or_json_artifacts():
    scene = {
        "screen_texts": ["엇갈림"],
        "screen_text_plan": [{
            "text": "엇갈림",
            "surface": "existing split-stage monitor",
            "purpose": "decorative emphasis",
            "max_occurrences": 1,
        }],
        "screen_text_validation": {"passed": True},
    }

    prompt = _bounded_text_generation_prompt(
        "A split market stage with opposing arrows.",
        audit_target=scene,
    )

    assert "case-sensitive string list" not in prompt
    assert "Follow these scene-specific text placements" not in prompt
    assert '[{"text"' not in prompt
    assert "[엇갈림]" not in prompt
    assert "Approved wording 1 is 엇갈림." in prompt
    assert "brackets, braces, quotation marks" in prompt


def test_surface_occurrence_count_rejects_duplicate_even_when_token_list_is_deduplicated():
    scene = {
        "screen_texts": ["엇갈림"],
        "screen_text_plan": [{
            "text": "엇갈림",
            "surface": "context_sign_1",
            "max_occurrences": 1,
        }],
        "screen_text_validation": {"passed": True},
    }

    result = visible_text_contract_result(
        ["엇갈림"], scene, occurrence_counts={"엇갈림": 2},
    )

    assert result["occurrence_counts"] == {"엇갈림": 2}
    assert result["duplicate_texts"] == ["엇갈림"]
    assert result["passed"] is False


def test_semantic_deterministic_values_are_not_reintroduced_into_model_prompt():
    scene = {
        "text_render_policy": "semantic_roles_v1",
        "screen_texts": ["코스피", "143조 원"],
        "screen_text_validation": {"passed": True},
        "screen_text_plan": [
            {"text": "코스피", "surface": "profit_monitor", "purpose": "information",
             "region": [0.08, 0.08, 0.84, 0.28]},
            {"text": "143조 원", "surface": "profit_monitor", "purpose": "information",
             "source_ref": "facts[0]", "region": [0.08, 0.48, 0.84, 0.36]},
        ],
    }
    prompt = _bounded_text_generation_prompt(
        'A data lab monitor displaying exactly "KOSPI 143 trillion won".',
        audit_target=scene,
    )
    assert "코스피" not in prompt
    assert "143조 원" not in prompt
    assert "later deterministic typography" in prompt
    assert "exactly one storyboard-essential physical" in prompt.lower()
    assert "calm uniform interior" in prompt.lower()
    assert "other props remain detailed" in prompt.lower()


def test_existing_prompt_cannot_leak_translated_financial_values_to_gemini():
    scene = {
        "screen_texts": ["PER 4배", "6860포인트"],
        "screen_text_validation": {"passed": True},
        "core_figures": [{"raw": "4배"}, {"raw": "6860포인트"}],
    }

    prompt = _bounded_text_generation_prompt(
        "A 2D classroom balance compares PER 4x while a market screen shows 6,860 points.",
        audit_target=scene,
    )

    assert "2D classroom" in prompt
    assert "4x" not in prompt
    assert "6,860" not in prompt
    assert "verified financial multiple" in prompt
    assert "verified market level" in prompt


def test_numeric_display_sanitizer_does_not_leave_decimal_suffix_fragment():
    scene = {
        "screen_texts": [],
        "screen_text_validation": {"passed": True},
    }

    prompt = _bounded_text_generation_prompt(
        "A monitor with 8888.11 and rising bars.",
        audit_target=scene,
    )

    assert "8888.11" not in prompt
    assert ".11" not in prompt
    assert "unlettered scene-integrated" in prompt
    assert "digits" in prompt


def test_semantic_entity_name_is_removed_when_not_approved_for_the_current_scene():
    scene = {
        "screen_texts": ["외국인"],
        "screen_text_validation": {"passed": True},
    }

    prompt = _bounded_text_generation_prompt(
        "A KOSPI monitoring panel with arrows showing investor flow.",
        audit_target=scene,
    )

    assert "KOSPI" not in prompt
    assert "the relevant scene entity monitoring panel" in prompt
    assert "Approved wording 1 is 외국인." in prompt


def test_sanitized_deterministic_surface_does_not_keep_conflicting_diagram_marks():
    """문자 제거 자리와 후속 결정론 표면 계약이 서로 반대 지시를 내리면 안 된다."""
    scene = {
        "screen_texts": ["143조 원"],
        "screen_text_validation": {"passed": True},
    }

    prompt = _bounded_text_generation_prompt(
        'A central monitor displays "143조 원" in bold digits.',
        audit_target=scene,
    )

    assert "non-linguistic shapes" not in prompt
    assert "calm uniform interior" in prompt
    assert "no marks, symbols, line art" in prompt


def test_reference_file_content_changes_resume_fingerprint(tmp_path):
    reference = tmp_path / "style.png"
    Image.new("RGB", (8, 8), "red").save(reference)
    ctx = {
        "text": "승인된 같은 장면 원문",
        "prompt_ko": "승인된 같은 장면 원문",
        "style_profile": "editorial_comic_2d",
        "image_profile": {"tier": "pro", "model": "gemini-3-pro-image"},
    }
    options = {
        "character_style_prompt": "goldie",
        "character_reference_paths": [str(reference)],
        "use_composite": False,
        "character_poses_dir": None,
        "lora_model_id": None,
        "lora_trigger_word": None,
        "lora_scale": None,
    }
    first = _stable_image_lineage_fingerprint(ctx, **options)
    Image.new("RGB", (8, 8), "blue").save(reference)
    second = _stable_image_lineage_fingerprint(ctx, **options)

    assert first != second


def test_resume_fingerprint_tracks_final_prompt_not_derived_scene_direction(tmp_path):
    ctx = {
        "text": "승인된 같은 장면 원문",
        "prompt_en": "the final cached scene prompt",
        "prompt_ko": "승인된 같은 장면 원문",
        "scene_spec": {"camera": "first transient expression"},
        "art_direction": {"temporary": "first"},
        "style_profile": "editorial_comic_2d",
        "image_profile": {"tier": "pro", "model": "gemini-3-pro-image"},
    }
    options = {
        "character_style_prompt": "goldie",
        "character_reference_paths": [],
        "use_composite": False,
        "character_poses_dir": None,
        "lora_model_id": None,
        "lora_trigger_word": None,
        "lora_scale": None,
    }

    first = _stable_image_lineage_fingerprint(ctx, **options)
    derived_only = _stable_image_lineage_fingerprint({
        **ctx,
        "scene_spec": {"camera": "second transient expression"},
        "art_direction": {"temporary": "second"},
    }, **options)
    changed_prompt = _stable_image_lineage_fingerprint({
        **ctx,
        "prompt_en": "a genuinely changed final scene prompt",
    }, **options)

    assert first == derived_only
    assert first != changed_prompt


def test_prompt_cache_key_keeps_content_prompt_separate_from_scene_text_contract():
    options = {
        "index": 0,
        "narration": "승인 장면",
        "scene_type": "general",
        "visual_mode": "general",
        "character_required": True,
        "core_entities": ["Samsung Electronics"],
        "core_figures": [],
        "screen_texts": ["삼성전자"],
    }
    first = _image_prompt_cache_key(**options)
    assert first == _image_prompt_cache_key(**options)
    assert first == _image_prompt_cache_key(**{**options, "screen_texts": ["SK하이닉스"]})
    assert first != _image_prompt_cache_key(**options, policy_version=6)
