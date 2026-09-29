"""2026-09-29 사용자 재현: job 12 이미지 생성이 "metric 유형에 허용되지 않은
archetype: briefing_podium"으로 실패했다. art_direction.py의 장면별 archetype
선택(모든 씬에 공통으로 적용되는 별도 LLM/키워드 폴백)과 scene_type_archetypes.py의
scene_type 분류(metric/graph/diagram/text/general)는 서로 다른 두 분류기다.
전자가 이미 고른 archetype("briefing_podium")을 _distinct_archetype_input()이
visual_archetype으로 그대로 넘기면, recommend_v5_archetype()이 이를 scene_type
호환 여부 검증 없이 강제로 밀어넣다가 _selection()에서 하드 크래시가 난다.
두 분류기가 불일치할 수 있다는 전제 자체는 정상이므로(예: 금융 아닌 해설형
대본에 여론조사 퍼센트가 나오면 scene_type=metric이 되지만, art_direction은
금융 채널 전용 맥락으로 학습돼 있어 무관한 archetype을 고를 수 있다), 강제
archetype이 실제 scene_type과 맞지 않으면 크래시 대신 그 scene_type에 맞는
추천 로직으로 안전하게 넘어가야 한다."""
from __future__ import annotations

from app.v5.scene.scene_type_archetypes import TYPE_CANDIDATES, recommend_v5_archetype


def test_forced_archetype_incompatible_with_scene_type_falls_back_instead_of_crashing():
    selection = recommend_v5_archetype({
        "scene_type": "metric",
        "visual_archetype": "briefing_podium",
        "content": "리얼미터 조사 기준으로 지지율이 37.9퍼센트로 나타났습니다.",
    })

    assert selection.archetype in TYPE_CANDIDATES["metric"]
    assert selection.archetype != "briefing_podium"


def test_forced_archetype_compatible_with_scene_type_is_still_respected():
    selection = recommend_v5_archetype({
        "scene_type": "graph",
        "visual_archetype": "briefing_podium",
        "content": "아무 내용",
    })

    assert selection.archetype == "briefing_podium"
    assert selection.selection_reason == "파일럿 장면 다양성을 위한 명시적 archetype 선택"
