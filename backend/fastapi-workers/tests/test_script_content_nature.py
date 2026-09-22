"""등급별로 대본 프롬프트·팩트체크·근거 처리가 갈리는지, FACTUAL은 종전과 같은지 고정한다."""
from __future__ import annotations

import json

from app.utils import content_nature as cn
from app.workers import script_worker as sw
from app.workers.script_worker import ScriptWorker


def _script(scene_count):
    blocks = []
    for index in range(1, scene_count + 1):
        blocks.append(
            f"## 장면 {index:03d}\n[대사]\n부산상어 이야기의 배경과 흐름을 자세히 설명합니다 {index:02d}\n"
            "[비주얼 설명 (한국어)]\n캐릭터\n[비주얼 프롬프트 (영어)]\nscene\n[감정]\nneutral"
        )
    meta = "\n\n## 메타데이터\n[추천 제목]\n제목\n[추천 썸네일]\nbg\n[더보기 설명]\n설명\n[쇼츠 대본]\n요약"
    return "\n\n".join(blocks) + meta


def _capture_generation(monkeypatch, nature):
    worker = ScriptWorker()
    worker._llm_provider_log = []
    captured = {}

    def fake_call(system, messages, max_tokens):
        captured.setdefault("system", system)
        captured.setdefault("user", messages[-1]["content"])
        return _script(20)

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_call)
    worker._generate_with_verified_facts(
        keyword="부산상어", category_label="x", target_minutes=1, target_chars=360,
        verified_facts=[{"fact": "사실", "figure": "1", "source_field": "테스트", "confidence": 0.9}],
        market_data={}, selected_terms=["부산상어"], keyword_news=[],
        length_contract={"target_seconds": 60}, narrative_plan={"plan_id": "t", "story_beats": []},
        content_nature=nature,
    )
    return captured


def test_factual_generation_prompt_is_unchanged(monkeypatch):
    captured = _capture_generation(monkeypatch, None)
    assert captured["system"].startswith(sw.SCRIPT_SYSTEM_PROMPT)
    assert "<content_nature>" not in captured["user"]


def test_explainer_generation_uses_non_finance_prompt_and_directive(monkeypatch):
    captured = _capture_generation(monkeypatch, cn.EXPLAINER)
    assert "금융 콘텐츠" not in captured["system"]
    assert "궁금" in captured["system"]
    assert "<content_nature>EXPLAINER</content_nature>" in captured["user"]
    assert "알려져 있" in captured["user"]


def test_story_generation_requires_disclosure_directive(monkeypatch):
    captured = _capture_generation(monkeypatch, cn.STORY)
    assert "<content_nature>STORY</content_nature>" in captured["user"]
    assert "지어낸" in captured["user"]


def _fact_check_prompts(monkeypatch, nature):
    worker = ScriptWorker()
    worker._llm_provider_log = []
    systems, users = [], []
    fact_json = json.dumps([{"fact": "f", "figure": "", "source_field": "s", "source_ref": ["s"],
                             "confidence": 0.5, "cross_verified": False, "contradiction_detected": False}])

    def fake_call(system, messages, max_tokens):
        systems.append(system)
        users.append(messages[0]["content"])
        return fact_json

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_call)
    worker._multi_round_fact_check("부산상어", "라벨", {}, ["부산상어"], [], 5, [], content_nature=nature)
    return systems, users


def test_factual_fact_check_keeps_market_prompt(monkeypatch):
    systems, users = _fact_check_prompts(monkeypatch, None)
    assert set(systems) == {sw.FACT_CHECK_SYSTEM_PROMPT}
    assert "위 실제 시장 데이터에서" in users[0]


def test_explainer_fact_check_uses_explainer_prompt(monkeypatch):
    systems, users = _fact_check_prompts(monkeypatch, cn.EXPLAINER)
    assert set(systems) == {cn.fact_check_prompt(cn.EXPLAINER, sw.FACT_CHECK_SYSTEM_PROMPT)}
    assert "위 실제 시장 데이터에서" not in users[0]
    assert "제공된 자료에서" in users[0]


def test_generate_skips_market_data_and_fact_check_for_story(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    worker = ScriptWorker()
    calls = {"market": 0, "fact_check": 0}

    class Collector:
        def collect_for_category(self, *args, **kwargs):
            calls["market"] += 1
            return {"kospi": 1}

    worker.collector = Collector()

    def stop_fact_check(*args, **kwargs):
        calls["fact_check"] += 1
        raise AssertionError("STORY는 팩트체크 호출 금지")

    monkeypatch.setattr(worker, "_multi_round_fact_check", stop_fact_check)
    monkeypatch.setattr(sw, "_collect_keyword_news", lambda terms: [])

    class Halt(Exception):
        pass

    def halt(*args, **kwargs):
        raise Halt()

    monkeypatch.setattr(worker, "_generate_with_verified_facts", halt)
    try:
        worker.generate("부산상어", "CUSTOM", 5, content_nature="STORY")
    except (Halt, RuntimeError):
        pass
    assert calls == {"market": 0, "fact_check": 0}


def test_generate_collects_market_data_for_factual(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    worker = ScriptWorker()
    calls = {"market": 0}

    class Collector:
        def collect_for_category(self, *args, **kwargs):
            calls["market"] += 1
            return {"kospi": 1}

    worker.collector = Collector()

    class Halt(Exception):
        pass

    def halt(*args, **kwargs):
        raise Halt()

    monkeypatch.setattr(sw, "_collect_keyword_news", lambda terms: [])
    monkeypatch.setattr(worker, "_multi_round_fact_check", halt)
    try:
        worker.generate("코스피 전망", "KOREAN_STOCKS", 5)
    except (Halt, RuntimeError):
        pass
    assert calls["market"] == 1


def test_topic_anchor_padding_only_for_factual():
    sections = [{"content": "이야기가 시작됩니다."}, {"content": "이야기가 끝납니다."}]

    factual, applied = sw._anchor_topic_boundaries(sections, "부산상어 재유행")
    assert applied == ["opening", "ending"]
    assert "계속 확인하죠" in factual[-1]["content"]

    for nature in (cn.EXPLAINER, cn.STORY):
        out, applied = sw._anchor_topic_boundaries(sections, "부산상어 재유행", nature)
        assert applied == []
        assert out == sections


def test_apply_flow_qa_contract_does_not_require_topic_boundaries_for_non_factual():
    flow_qa = {"passed": True, "deterministic": {"repetitions": [], "rhetorical_rhythm": {"passed": True},
                                                  "spoken_pacing": {"passed": True}}}
    script = (
        "훅으로 여는 문장입니다.\n"
        "부산상어 재유행이 시작된 배경을 살펴봅니다.\n"
        "부산상어 재유행은 예상 밖의 경로로 퍼졌습니다.\n"
        "그 이유를 하나씩 짚어보겠습니다.\n"
        "다시 한번 생각해볼 지점입니다."
    )

    factual = sw._apply_flow_qa_contract(dict(flow_qa), script, "부산상어 재유행")
    assert factual["passed"] is False  # 사실형은 여전히 도입부 연결을 요구한다
    assert "도입부를" in factual["revision_instruction"]

    for nature in ("EXPLAINER", "STORY"):
        result = sw._apply_flow_qa_contract(dict(flow_qa), script, "부산상어 재유행", content_nature=nature)
        assert result["passed"] is True
        assert "도입부를" not in result["revision_instruction"]
        assert result["deterministic"]["topic_boundaries"]["passed"] is False  # 값 자체는 그대로 기록


def test_apply_flow_qa_contract_still_fails_non_factual_on_other_gates():
    flow_qa = {"passed": True, "deterministic": {"repetitions": [{"sentence_indexes": [1, 2]}],
                                                  "rhetorical_rhythm": {"passed": True},
                                                  "spoken_pacing": {"passed": True}}}
    result = sw._apply_flow_qa_contract(flow_qa, "문장입니다. 문장입니다.", "부산상어", content_nature="EXPLAINER")
    assert result["passed"] is False
