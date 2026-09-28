"""2026-09-28 사용자 재현: job 11 해설형(1분) 대본이 verified_facts 어디에도 없는
새 주장을 지어냈다 — "유엔사가 신속 조사를 지시했으나 한국군이 거부했다",
"한국군과 미군이 파악한 사고 위치가 다를 수 있다" 등. 기존 규칙은 숫자·날짜·
시간 관계 창작만 금지했고, 행위자·행동을 통째로 지어내는 것은 막지 않았다.
이 규칙은 등급과 무관한 공통 대본 작성 규칙(SCRIPT_SYSTEM_PROMPT)이라
사실형·해설형·이야기형 모두에 적용돼야 한다."""
from __future__ import annotations

from app.utils.content_nature import EXPLAINER, FACTUAL, STORY, script_system_prompt
from app.workers.script_worker import SCRIPT_SYSTEM_PROMPT


def test_factual_prompt_bans_fabricated_claims_and_actors():
    assert "행위자" in SCRIPT_SYSTEM_PROMPT
    assert "지어내" in SCRIPT_SYSTEM_PROMPT


def test_fabricated_claim_ban_survives_non_factual_prompt_transform():
    for nature in (EXPLAINER, STORY):
        prompt = script_system_prompt(nature, SCRIPT_SYSTEM_PROMPT)
        assert "행위자" in prompt
        assert "지어내" in prompt


def test_factual_transform_is_identity_and_keeps_the_new_rule():
    prompt = script_system_prompt(FACTUAL, SCRIPT_SYSTEM_PROMPT)
    assert prompt == SCRIPT_SYSTEM_PROMPT
    assert "행위자" in prompt
