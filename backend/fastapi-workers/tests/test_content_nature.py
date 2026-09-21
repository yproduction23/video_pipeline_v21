from app.utils import content_nature as cn

FACT_PROMPT = "당신은 한국 주식 시장 및 글로벌 매크로/국제정세 전문 팩트체커입니다."
SCRIPT_PROMPT = """당신은 한국 금융 콘텐츠를 위한 오리지널 대본 작가입니다.

작성 원칙:
- 친근하지만 전문적인 톤.
- 수치를 자연스럽게 구어체로 표현: "코스닥이 785포인트를 기록했습니다"
- 투자 조언이 아닌 정보 제공 관점 유지
- 각 씬은 자연스럽게 다음 씬으로 연결
"""


def test_normalize_defaults_to_factual():
    assert cn.normalize_nature(None) == cn.FACTUAL
    assert cn.normalize_nature("weird") == cn.FACTUAL
    assert cn.normalize_nature("story") == cn.STORY
    assert cn.normalize_nature("EXPLAINER") == cn.EXPLAINER


def test_factual_prompts_are_unchanged():
    assert cn.fact_check_prompt(cn.FACTUAL, FACT_PROMPT) == FACT_PROMPT
    assert cn.script_system_prompt(cn.FACTUAL, SCRIPT_PROMPT) == SCRIPT_PROMPT
    assert cn.nature_directive(cn.FACTUAL) == ""


def test_non_factual_fact_check_is_not_market_specific():
    for nature in (cn.EXPLAINER, cn.STORY):
        prompt = cn.fact_check_prompt(nature, FACT_PROMPT)
        assert "주식 시장" not in prompt
        assert prompt != FACT_PROMPT


def test_explainer_fact_check_never_deletes_unverified():
    prompt = cn.fact_check_prompt(cn.EXPLAINER, FACT_PROMPT)
    assert "제외하지" in prompt
    assert "완화" in prompt


def test_non_factual_script_prompt_drops_finance_rules_and_adds_curiosity():
    for nature in (cn.EXPLAINER, cn.STORY):
        prompt = cn.script_system_prompt(nature, SCRIPT_PROMPT)
        assert "금융 콘텐츠" not in prompt
        assert "코스닥이 785포인트" not in prompt
        assert "투자 조언" not in prompt
        assert "궁금" in prompt
        assert "각 씬은 자연스럽게 다음 씬으로 연결" in prompt


def test_directives_carry_disclosure_rules():
    assert "전해 내려오는" in cn.nature_directive(cn.STORY)
    assert "알려져 있" in cn.nature_directive(cn.EXPLAINER)
    assert "궁금" in cn.nature_directive(cn.EXPLAINER)


def test_category_label_for_non_factual_ignores_market_default():
    labels = {"CUSTOM": "주식시장 전반"}
    assert cn.category_label_for(cn.FACTUAL, "CUSTOM", labels) == "주식시장 전반"
    assert cn.category_label_for(cn.EXPLAINER, "CUSTOM", labels) == "화제 콘텐츠 해설"
    assert cn.category_label_for(cn.STORY, "CUSTOM", labels) == "이야기"


def test_story_seed_facts_use_benchmark_only():
    facts = cn.story_seed_facts(
        "부산상어 전설",
        {"topic_keyword": "부산상어 전설", "reasons": ["궁금증 유발"], "title_pattern": "결과 먼저"},
        [{"title": "원본 영상 제목"}],
    )
    assert facts and all(f["contradiction_detected"] is False for f in facts)
    assert any("부산상어" in f["fact"] for f in facts)


def test_story_seed_facts_never_empty():
    assert cn.story_seed_facts("야담", None, None)


def test_ensure_story_disclosure_prepends_when_missing():
    sections = [{"content": "고을에 한 선비가 살았습니다."}, {"content": "어느 날 비가 왔습니다."}]
    out = cn.ensure_story_disclosure(sections)
    assert out[0]["content"].startswith(cn.DISCLOSURE_SENTENCE)
    assert out[1]["content"] == "어느 날 비가 왔습니다."


def test_ensure_story_disclosure_keeps_existing_marker():
    sections = [{"content": "오늘은 전해 내려오는 이야기를 들려드립니다."}]
    assert cn.ensure_story_disclosure(sections)[0]["content"] == sections[0]["content"]
