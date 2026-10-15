from app.services.slide_scene_direction import (
    generate_slide_direction,
    select_best_candidate,
    _SYSTEM_PROMPT,
)


def test_generate_slide_direction_parses_llm_json_response():
    def fake_llm(system, messages, max_tokens):
        return '{"headline": "전자발찌, 범행을 막지 못했다", "pexels_query": "ankle monitor bracelet closeup"}'

    result = generate_slide_direction(
        "그것도 전자발찌를 찬 상태에서 이 범행을 저질렀습니다.", llm_call=fake_llm,
    )

    assert result == {
        "headline": "전자발찌, 범행을 막지 못했다",
        "pexels_query": "ankle monitor bracelet closeup",
    }


def test_generate_slide_direction_strips_markdown_code_fences():
    def fake_llm(system, messages, max_tokens):
        return '```json\n{"headline": "매도가 멈추나", "pexels_query": "stock trading floor"}\n```'

    result = generate_slide_direction("매도세가 이번 주 방향을 가른다.", llm_call=fake_llm)

    assert result["headline"] == "매도가 멈추나"


def test_generate_slide_direction_falls_back_when_llm_refuses_or_errors():
    def fake_llm(system, messages, max_tokens):
        raise RuntimeError("API down")

    result = generate_slide_direction("불특정 다수를 겨냥한 흉기 난동입니다.", llm_call=fake_llm)

    assert result["headline"]
    assert result["pexels_query"]


def test_generate_slide_direction_falls_back_when_response_is_missing_fields():
    def fake_llm(system, messages, max_tokens):
        return '{"headline": "", "pexels_query": ""}'

    result = generate_slide_direction("테스트 내레이션입니다.", llm_call=fake_llm)

    assert result["headline"] == "테스트 내레이션입니다."[:20]
    assert result["pexels_query"]


def test_generate_slide_direction_handles_empty_narration():
    assert generate_slide_direction("") == {"headline": "", "pexels_query": "news broadcast studio"}


def test_system_prompt_warns_against_whole_sentence_weapon_keywords():
    """2026-10-14 job 18 재현: "불특정 다수를 겨냥한 흉기 난동"을 통째로
    "kitchen knife"로 검색해 요리 영상이 나왔다. 시스템 프롬프트가 흉기
    단어를 그대로 쓰지 말라는 지침과, 문장 속 구체적 부분(행동/장소)에
    집중하라는 지침을 담고 있는지 확인한다."""
    assert "흉기" in _SYSTEM_PROMPT
    assert "요리" in _SYSTEM_PROMPT
    assert "구체적" in _SYSTEM_PROMPT


def test_select_best_candidate_returns_the_index_the_llm_chooses():
    candidates = [
        {"tags": "water lilies, lotus, pond, republic of korea"},
        {"tags": "handcuffs, police, arrest, seoul"},
    ]

    def fake_llm(system, messages, max_tokens):
        assert "lotus" in messages[0]["content"]
        assert "경찰이 남성을 체포했다" in messages[0]["content"]
        return '{"index": 1}'

    result = select_best_candidate("경찰이 남성을 체포했다.", candidates, llm_call=fake_llm)

    assert result == 1


def test_select_best_candidate_returns_none_when_llm_says_no_candidate_fits():
    """2026-10-14 job 18 재현: "water lilies, lotus, pond... korea"는 "korea"
    단어만 겹쳤을 뿐 체포 장면과 전혀 무관하다 — LLM이 null을 반환하면 AI
    배경 생성 폴백으로 넘어가야 한다(해당 후보를 억지로 쓰면 안 된다)."""
    candidates = [{"tags": "water lilies, lotus, pond, republic of korea"}]

    def fake_llm(system, messages, max_tokens):
        return '{"index": null}'

    result = select_best_candidate("핸드폰을 뺏으려던 남성이 경찰에 체포됐다.", candidates, llm_call=fake_llm)

    assert result is None


def test_select_best_candidate_treats_out_of_range_index_as_no_match():
    candidates = [{"tags": "city, street"}]

    def fake_llm(system, messages, max_tokens):
        return '{"index": 5}'

    assert select_best_candidate("테스트 내레이션", candidates, llm_call=fake_llm) is None


def test_select_best_candidate_falls_back_to_none_on_llm_error():
    candidates = [{"tags": "city, street"}]

    def fake_llm(system, messages, max_tokens):
        raise RuntimeError("API down")

    assert select_best_candidate("테스트 내레이션", candidates, llm_call=fake_llm) is None


def test_select_best_candidate_returns_none_for_empty_candidate_list():
    assert select_best_candidate("테스트 내레이션", [], llm_call=lambda *a, **k: '{"index": 0}') is None


def test_candidate_selection_prompt_rejects_mood_only_matches():
    """2026-10-14 job 18 v5 재현: "시민이 먼저 나섰다"에 유럽 화재 진압 항공샷을,
    "불안한 지하철 이용객"에 공포영화풍 얼굴 클로즈업을 골랐다 — 둘 다 실제
    장면 내용이 아니라 막연한 분위기만 보고 억지로 고른 오답이었다. 시스템
    프롬프트가 분위기만으로 고르지 말라는 지침과, 애매하면 null을 쓰라는
    지침을 담고 있는지 확인한다."""
    from app.services.slide_scene_direction import _CANDIDATE_SELECTION_SYSTEM_PROMPT

    assert "분위기" in _CANDIDATE_SELECTION_SYSTEM_PROMPT
    assert "null" in _CANDIDATE_SELECTION_SYSTEM_PROMPT.lower()
