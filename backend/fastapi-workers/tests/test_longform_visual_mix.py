import pytest

from app.utils.narration_contract import compact_text
from app.utils.caption_segmentation import split_script_into_caption_chunks
from app.utils.script_delivery import pace_sections_for_runtime
from app.workers.script_worker import (
    _caption_chunk_issues,
    _minimum_scene_count,
    _parse_dialogue_sentence_array,
    _split_sections_for_visual_pacing,
    _structured_script_from_dialogue_lines,
    _validate_scene_delivery,
)
from app.v5.scene.longform_visual_mix import apply_longform_visual_mix
from app.v5.scene.runtime_contract import attach_v5_scene_contracts


def _scene(index: int, text: str, scene_type: str = "general") -> dict:
    return {
        "scene_id": f"scene-{index:03d}",
        "scene_type": scene_type,
        "content": text,
        "text": text,
        "visual_intent": text,
    }


def test_runtime_pacing_preserves_narration_and_targets_five_to_six_seconds():
    source = [_scene(index, "시장 흐름을 차분하게 살펴봅니다.") for index in range(120)]
    paced = pace_sections_for_runtime(source, 300)

    assert compact_text(" ".join(scene["content"] for scene in source)) == compact_text(
        " ".join(scene["content"] for scene in paced)
    )
    assert 45 <= len(paced) <= 60
    assert all(scene["runtime_pacing"]["visible_char_count"] > 0 for scene in paced)
    planned = [
        chunk["text"]
        for scene in paced
        for chunk in scene["caption_chunk_plan"]["chunks"]
    ]
    assert planned == split_script_into_caption_chunks(
        " ".join(scene["content"] for scene in paced),
        max_chars=18,
    )
    assert all(scene["runtime_pacing"]["caption_chunk_count"] >= 1 for scene in paced)


def test_visual_pacing_never_splits_a_sentence_at_a_word_boundary():
    source = [_scene(0, "이 문장은 공백을 제외하면 매우 길지만 마침표 하나로 끝나는 완결 문장이어서 이미지 수를 맞추려고 중간을 잘라서는 안 되는 하나의 온전한 생각 단위입니다.")]
    scenes = _split_sections_for_visual_pacing(source)

    assert len(scenes) == 1
    assert scenes[0]["content"] == source[0]["content"]
    with pytest.raises(ValueError, match="52자"):
        _validate_scene_delivery(scenes, target_scene_count=1)


def test_visual_pacing_split_does_not_duplicate_stale_text_for_tts():
    """2026-10-06 job 14 재현: 유승민 대한체육회장 문장이 TTS에 두 번 들렸다.

    원본 장면에 이미 text_for_tts가 채워진 채로 들어오면(narration_contract가
    분할 전에 먼저 채워둔 값), 분할 시 dict(source)가 그 값을 그대로 복사해
    content/text는 올바르게 나뉘어도 text_for_tts는 각 조각 모두 분할 전
    원본 전체 문장을 그대로 유지했다. Spring의 narrationFromMeta()는
    text_for_tts를 최우선으로 읽으므로, 같은 문장이 TTS 입력에 두 번
    들어갔다."""
    first_sentence = "유승민 대한체육회장은 국가대표답게 품위를 갖춰야 한다고 지적했습니다."
    second_sentence = "안철수 의원은 아시안게임 병역특례 제도 자체를 재고해야 한다고 주장했습니다."
    combined = f"{first_sentence} {second_sentence}"
    source = [{
        "scene_id": "scene-004",
        "scene_type": "general",
        "content": combined,
        "text": combined,
        "visual_intent": combined,
        # narration_contract가 분할 전에 이미 채워둔 전체 문장(실제 재현 조건).
        "text_for_tts": combined,
    }]

    scenes = _split_sections_for_visual_pacing(source)

    assert len(scenes) == 2
    assert scenes[0]["content"] == first_sentence
    assert scenes[1]["content"] == second_sentence
    # 핵심 회귀 검증: text_for_tts도 content/text와 같은 조각으로 갈라져야
    # 한다 — 분할 전 원본 전체 문장을 양쪽에 그대로 복사해서는 안 된다.
    assert scenes[0]["text_for_tts"] == first_sentence
    assert scenes[1]["text_for_tts"] == second_sentence


def test_caption_contract_rejects_only_short_trailing_fragments():
    issues = _caption_chunk_issues([
        "오늘 코스피가 6860포인트를 기록했습니다.",
        "안녕.",
    ], max_chars=18)

    assert len(issues) == 1
    assert issues[0]["short_chunks"] == ["기록했습니다."]


def test_scene_delivery_keeps_short_trailing_caption_fragment_in_same_image(caplog):
    scene = _scene(0, "오늘 코스피가 6860포인트를 기록했습니다.")
    with caplog.at_level("WARNING"):
        _validate_scene_delivery(
            [scene], target_scene_count=1, subtitle_max_chars=18,
        )

    paced = pace_sections_for_runtime([scene], 6, subtitle_max_chars=18)
    planned = [chunk["text"] for chunk in paced[0]["caption_chunk_plan"]["chunks"]]
    assert planned == ["오늘 코스피가 6860포인트를", "기록했습니다."]
    assert len(paced) == 1
    assert any("동일 이미지 안에 묶어" in record.message for record in caplog.records)


def test_minimum_scene_count_tolerates_the_undershoot_observed_in_job_147():
    # job 147 (2026-08-04): target=55 scenes for a 5-minute video, Claude
    # repeatedly wrote 49-52 complete sentences. The old flat "target-2"
    # tolerance (53) rejected all of these and failed the whole job after
    # 3 retries. The floor follows the current total-length lower bound and the
    # 52-char absolute cap, so it accepts 52 without
    # imposing an unrelated image-count rule at SCRIPT time.
    assert _minimum_scene_count(55) == 37
    assert 46 <= 52  # the actual undershoot that kept failing


def test_minimum_scene_count_scales_with_target_instead_of_a_flat_offset():
    # A flat "-2" tolerance is far tighter, relatively, for a long video
    # (216/218 = 99%) than a short one (53/55 = 96%) -- backwards from what
    # sampling noise would suggest. A percentage floor treats both the same.
    assert _minimum_scene_count(11) == 8
    assert _minimum_scene_count(218) == 144


def test_minimum_sentence_count_matches_total_length_lower_bound():
    # 목표 문장 수와 총분량 하한을 52자 절대 상한으로 함께 환산한다.
    assert _minimum_scene_count(100) == 66


def test_structured_dialogue_rewrite_requires_json_array_and_preserves_lines():
    lines = _parse_dialogue_sentence_array('["첫 번째 완결 문장입니다.", "두 번째 완결 문장입니다."]')
    structured = _structured_script_from_dialogue_lines(lines)

    assert lines == ["첫 번째 완결 문장입니다.", "두 번째 완결 문장입니다."]
    assert "[대사]" in structured
    assert "첫 번째 완결 문장입니다." in structured
    assert _parse_dialogue_sentence_array("1. 문장") == []
    assert _parse_dialogue_sentence_array("결과입니다.\n[\"한 문장입니다.\"]") == ["한 문장입니다."]


def test_longform_mix_preserves_article_evidence_and_assigns_other_two_modes():
    source = [
        _scene(0, "기사 근거를 설명합니다.", "graph"),
        *[_scene(index, "반도체와 환율 흐름의 영향을 살펴봅니다.", "metric") for index in range(1, 31)],
    ]
    source[0].update({
        "visual_kind": "article_scene",
        "article_capture": {"local_path": "/tmp/article.png"},
    })

    mixed, audit = apply_longform_visual_mix(source)
    contracted = attach_v5_scene_contracts(mixed)

    assert compact_text(" ".join(scene["content"] for scene in source)) == compact_text(
        " ".join(scene["content"] for scene in mixed)
    )
    assert audit["counts"]["article_evidence"] == 1
    assert audit["counts"]["semantic_illustration"] > 0
    assert audit["counts"]["archetype_explainer"] > 0
    assert contracted[0]["v5_render_contract"]["visual_mode"] == "article_evidence"
    assert all(
        scene["v5_render_contract"]["visual_text_policy"] == "strict_textless"
        for scene in contracted if scene["visual_mode"] == "semantic_illustration"
    )
    assert all(
        scene["v5_render_contract"]["visual_text_policy"] == "strict_textless"
        for scene in contracted
        if scene["visual_mode"] == "archetype_explainer" and not scene.get("screen_texts")
    )
