"""2026-09-28 사용자 재현: job 10 해설형 대본이 검증 사실 어디에도 없는
'추석 연휴가 끝나자마자'라는 시점을 훅으로 지어냈다(실제 검증 사실은 "DMZ에서
지뢰 폭발 사고가 발생했다"뿐, 시점 정보 없음). 기존 규칙은 숫자·날짜 창작만
금지했고 "~하자마자/~직후" 같은 시간 관계 창작은 막지 않았다. 이 규칙은
등급과 무관한 공통 대본 작성 규칙(SCRIPT_SYSTEM_PROMPT)이라 사실형·해설형
모두에 적용돼야 한다."""
from __future__ import annotations

from app.utils.content_nature import EXPLAINER, FACTUAL, STORY, script_system_prompt
from app.workers.script_worker import SCRIPT_SYSTEM_PROMPT


def test_factual_prompt_bans_fabricated_time_relations():
    assert "하자마자" in SCRIPT_SYSTEM_PROMPT
    assert "직후" in SCRIPT_SYSTEM_PROMPT
    assert "시간 관계" in SCRIPT_SYSTEM_PROMPT


def test_time_relation_ban_survives_non_factual_prompt_transform():
    for nature in (EXPLAINER, STORY):
        prompt = script_system_prompt(nature, SCRIPT_SYSTEM_PROMPT)
        assert "하자마자" in prompt
        assert "시간 관계" in prompt


def test_factual_transform_is_identity_and_keeps_the_new_rule():
    prompt = script_system_prompt(FACTUAL, SCRIPT_SYSTEM_PROMPT)
    assert prompt == SCRIPT_SYSTEM_PROMPT
    assert "하자마자" in prompt
