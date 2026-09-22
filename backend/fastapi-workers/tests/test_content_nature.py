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
    assert "지어낸" in cn.nature_directive(cn.STORY)
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
    sections = [{"content": "오늘은 지어낸 이야기를 들려드립니다."}]
    assert cn.ensure_story_disclosure(sections)[0]["content"] == sections[0]["content"]


def test_explainer_without_facts_is_grounded_in_benchmark():
    analysis = {"topic_keyword": "부산상어 재유행", "reasons": ["익숙한 소재의 재유행"]}
    facts = cn.ground_explainer_facts(cn.EXPLAINER, [], "부산상어", analysis, [{"title": "부산상어 챌린지"}])
    assert facts and any("다루는 화제: 부산상어" in f["fact"] for f in facts)
    assert any("부산상어 챌린지" in f["fact"] for f in facts)


def test_grounding_leaves_existing_facts_and_other_natures_alone():
    existing = [{"fact": "확인된 사실"}]
    assert cn.ground_explainer_facts(cn.EXPLAINER, existing, "k", None, None) is existing
    assert cn.ground_explainer_facts(cn.FACTUAL, [], "k", None, None) == []
    assert cn.ground_explainer_facts(cn.STORY, [], "k", None, None) == []


def test_explainer_directive_forbids_invented_specifics():
    directive = cn.nature_directive(cn.EXPLAINER)
    assert "만들지 마세요" in directive
    assert "화제가 된 영상" in directive


def test_explainer_prompts_forbid_using_video_performance_numbers():
    assert "성과 수치" in cn.fact_check_prompt(cn.EXPLAINER, "x")
    assert "성과 수치" in cn.nature_directive(cn.EXPLAINER)


def test_seed_facts_lead_with_transcript_summary_when_available():
    facts = cn.story_seed_facts("면접", {"content_summary": "늙어버린 25살이 면접에서 진실을 말한다"}, None)
    assert any("영상의 실제 줄거리" in f["fact"] and f["source_field"] == "transcript" for f in facts)


def test_explainer_directive_prefers_content_summary_over_guessing_from_tags():
    assert "content_summary" in cn.nature_directive(cn.EXPLAINER)


def test_story_directive_reuses_only_premise_and_structure():
    directive = cn.nature_directive(cn.STORY)
    assert "story_premise" in directive and "story_beats" in directive
    assert "재사용하지 마세요" in directive


def test_sanitize_story_inputs_drops_plot_summary_and_video_details():
    analysis = {"topic_keyword": "t", "content_summary": "은조와 태문의 이야기", "story_premise": "경고를 전하는 낯선 사람"}
    videos = [{"title": "제목", "description": "은조가 나오는 설명", "tags": ["은조", "태문"]}]

    clean_analysis, clean_videos = cn.sanitize_story_inputs(analysis, videos)

    assert "content_summary" not in clean_analysis
    assert clean_analysis["story_premise"] == "경고를 전하는 낯선 사람"
    assert clean_videos == [{"title": "제목"}]
    assert "content_summary" in analysis  # 원본은 건드리지 않는다


def test_sanitize_story_inputs_handles_missing_values():
    assert cn.sanitize_story_inputs(None, None) == (None, [])


def test_seed_facts_use_premise_and_beats_without_names():
    facts = cn.story_seed_facts("경고", {"story_premise": "낯선 사람이 경고한다", "story_beats": ["경고", "의심", "위기"]}, None)
    texts = " ".join(f["fact"] for f in facts)
    assert "낯선 사람이 경고한다" in texts and "경고 → 의심 → 위기" in texts


def test_disclosure_now_says_invented():
    assert "지어낸" in cn.DISCLOSURE_SENTENCE
    out = cn.ensure_story_disclosure([{"content": "옛날 옛적에 한 선비가 살았습니다."}])
    assert out[0]["content"].startswith(cn.DISCLOSURE_SENTENCE)


def test_non_factual_script_prompt_replaces_finance_scene_block():
    fact = SCRIPT_PROMPT + (
        "\n\n🎯 씬 구성 규칙 (비주얼 프롬프트 작성의 핵심!):\n"
        "- 대본의 경제 상황을 물리적인 공간이나 은유적인 상황으로 치환하여 표현하세요.\n"
        "  * (예시) 신주 발행 / 통화량 증가 ➡️ 돈을 찍어내는 거대한 윤전기가 있는 공장\n"
        "- 반드시 이 스타일 태그로 끝낼 것: original 2D Korean finance editorial comic, bold ink outlines, cel shading\n"
        "  6. [화면 문구] : 숫자·단위는 <verified_facts>의 원문과 정확히 같아야 합니다. 무조건 []로 피하지 마세요.\n\n"
        "절대 금지사항:\n- 확정적 미래 예측 금지\n"
    )
    for nature in (cn.EXPLAINER, cn.STORY):
        out = cn.script_system_prompt(nature, fact)
        assert "신주 발행" not in out
        assert "돈을 찍어내는 거대한 윤전기" not in out
        assert "verified_facts>의 원문과 정확히 같아야" not in out
        assert "무조건 []로 피하지 마세요" not in out
        assert "original 2D Korean finance editorial comic" not in out
        assert "절대 금지사항:\n- 확정적 미래 예측 금지" in out  # 뒤쪽 규칙은 보존
        assert "[화면 문구]" in out and "[대사]" in out and "[비주얼 프롬프트 (영어)]" in out


def test_non_factual_script_prompt_survives_missing_markers():
    # SCRIPT_SYSTEM_PROMPT에 씬 구성 규칙 마커가 없어도(향후 문구 변경 등) 예외 없이 동작한다.
    out = cn.script_system_prompt(cn.EXPLAINER, "간단한 프롬프트")
    assert "간단한 프롬프트" in out
