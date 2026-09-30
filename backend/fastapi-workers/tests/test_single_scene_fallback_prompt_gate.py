"""2026-09-30 job 12 scene 0 재현: /workers/images/generate-single이
scene_meta에 승인 장면의 실제 art_direction이 있어도 prompt_en이 비어 있으면
(Spring의 text_and_image 모드가 promptEn=null을 보내는 정상 경로 포함) 항상
"Korean finance editorial scene" 하드코딩 art_direction으로 prompt_en을 먼저
만들어 scene에 주입했다. generate_single_scene()은 scene["prompt_en"]이 이미
채워져 있으면 scene_meta의 실제 archetype·mood·props·palette를 쓰는 자기
내부 compile_editorial_prompt 호출을 절대 하지 않으므로, DMZ 지뢰 사고
조사 지연처럼 금융과 무관한 CUSTOM 주제에서도 금융 브리핑 화풍(성장 차트 등)이
강제로 섞여 의미 불일치 QA 거부가 반복됐다. job/scene에 무관한 공통 계약
버그다.
"""
from app.main import _single_scene_needs_generic_fallback_prompt


def test_uses_generic_fallback_when_prompt_and_art_direction_are_both_missing():
    assert _single_scene_needs_generic_fallback_prompt("", None) is True
    assert _single_scene_needs_generic_fallback_prompt("", {"text": "no art direction here"}) is True


def test_does_not_override_approved_scene_art_direction_even_when_prompt_en_is_empty():
    scene_meta = {
        "text": "목함지뢰 증언이 나왔는데도 현장조사는 엿새가 지나서야 이뤄진 것으로 알려져 있습니다.",
        "art_direction": {
            "family": "hero_metaphor",
            "setting": "dim, serious government investigation briefing room",
        },
    }
    assert _single_scene_needs_generic_fallback_prompt("", scene_meta) is False


def test_explicit_prompt_en_always_wins_regardless_of_scene_meta():
    assert _single_scene_needs_generic_fallback_prompt("an already-reviewed prompt", None) is False
    assert _single_scene_needs_generic_fallback_prompt(
        "an already-reviewed prompt", {"art_direction": {"family": "x"}},
    ) is False
