"""2026-10-13 job 14 씬2 재현: 좌우 분할·군중이 있는 구도에서 Kling이 마스코트
외의 다른 인물(군중, 발표자)까지 움직여 화면 대부분이 변하는
global_motion_coverage 판정을 유발했다("active_tile_ratio": 0.767). "마스코트만
움직여라"는 긍정 지시만으로는 부족해, 다른 인물을 명시적으로 고정해야 한다."""
from app.services.kling_prompt_builder import build_kling_motion_prompt


def test_prompt_explicitly_freezes_other_people_in_the_frame():
    result = build_kling_motion_prompt({"motion_type": "walking_intro"})

    assert "other person" in result["prompt"]
    assert "remain completely frozen" in result["prompt"]


def test_negative_prompt_forbids_crowd_and_secondary_character_motion():
    result = build_kling_motion_prompt({"motion_type": "walking_intro"})

    assert "no crowd motion" in result["negative_prompt"]
    assert "no secondary character movement" in result["negative_prompt"]


def test_single_mascot_scene_still_gets_the_requested_template_body():
    """회귀 방지: 군중 고정 지시가 추가됐다고 기존 단일 마스코트 템플릿 본문이
    깨지면 안 된다."""
    result = build_kling_motion_prompt({"motion_type": "celebration"})

    assert "one compact joyful hop in place" in result["prompt"]


def test_unknown_motion_type_still_falls_back_to_walking_intro():
    result = build_kling_motion_prompt({"motion_type": "", "emotion_tag": "neutral"})

    assert "two small in-place walking steps" in result["prompt"]


def test_podium_archetype_with_neutral_emotion_gets_pointing_explain_not_walking():
    """2026-10-13 사용자 재현(job 14, 씬4): motion_type이 비어있고
    emotion_tag="neutral"(매핑표에 없음)인 단상 발언 장면이 상황과 무관한
    "걷고 손 흔들기" 모션으로 떨어졌다. archetype에서 상황에 맞는 동작을
    유추해야 한다."""
    result = build_kling_motion_prompt({
        "motion_type": "", "emotion_tag": "neutral", "archetype": "briefing_podium",
    })

    assert "gentle tap or one short presenting sweep" in result["prompt"]
    assert "two small in-place walking steps" not in result["prompt"]


def test_calculator_archetype_with_no_motion_type_gets_thinking_desk():
    result = build_kling_motion_prompt({"archetype": "trade_calculator"})

    assert "chin on one hand" in result["prompt"]


def test_unrecognized_archetype_still_falls_back_to_walking_intro():
    """회귀 방지: archetype 키워드와 매칭되는 게 없으면 기존처럼 범용
    walking_intro로 떨어져야 한다."""
    result = build_kling_motion_prompt({"archetype": "some_future_archetype"})

    assert "two small in-place walking steps" in result["prompt"]


def test_explicit_motion_type_always_overrides_archetype_inference():
    result = build_kling_motion_prompt({
        "motion_type": "celebration", "archetype": "briefing_podium",
    })

    assert "one compact joyful hop in place" in result["prompt"]


def test_emotion_mapping_takes_priority_over_archetype_inference():
    result = build_kling_motion_prompt({
        "motion_type": "", "emotion_tag": "alarm", "archetype": "briefing_podium",
    })

    assert "sharply leans back" in result["prompt"]
