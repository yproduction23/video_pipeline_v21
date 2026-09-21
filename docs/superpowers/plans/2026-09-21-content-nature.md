# 콘텐츠 성격(사실성 등급) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (인라인 실행). Steps use checkbox (`- [ ]`) syntax.

**Goal:** 사실형/해설형/창작형 등급을 채널 기본값 + 작업별 확정으로 정하고, 등급별로 팩트체크·대본 프롬프트·근거 처리를 갈아 끼운다.

**Architecture:** 등급은 Spring `VideoJob.contentNature`에 저장되어 `/workers/script/generate`의 `content_nature`로 전달된다. FastAPI는 새 모듈 `app/utils/content_nature.py`에 등급별 프롬프트·정책을 모으고, `ScriptWorker`는 등급이 FACTUAL이면 기존 경로를 그대로 쓴다(회귀 없음).

**Tech Stack:** FastAPI/pytest(docker cp 방식), Spring Boot 3.5/JUnit(gradle docker), React.

**Spec:** `docs/superpowers/specs/2026-09-21-content-nature-design.md`

## Global Constraints
- 기본 등급은 FACTUAL이며 등급이 없거나 알 수 없는 값이면 FACTUAL로 취급한다(기존 채널·작업 동작 불변).
- FACTUAL 경로의 기존 프롬프트 문구·검증은 바꾸지 않는다.
- 삭제·반려는 "명백한 오류"에만. 검증 실패는 완화 표현/표시로 처리.
- 대본 중심은 시청자의 궁금증(수치는 재료). 세 등급 프롬프트에 공통 반영.
- 벤치마크 문장 복제 금지(유형·구조·리듬만 참고).
- 테스트: FastAPI는 `docker cp` 후 `docker exec pipeline_fastapi pytest`, Spring은 gradle docker 이미지.

## 파일 구조
- Create `backend/fastapi-workers/app/utils/content_nature.py` — 등급 상수, 정규화, 프롬프트·정책 함수, 창작형 씨드 사실, 도입 표기 보장.
- Modify `backend/fastapi-workers/app/workers/script_worker.py` — 등급 연동.
- Modify `backend/fastapi-workers/app/main.py` — 요청 필드.
- Create `backend/fastapi-workers/tests/test_content_nature.py`, `tests/test_script_content_nature.py`.
- Create `backend/spring-app/src/main/java/com/pipeline/video/domain/ContentNature.java`.
- Modify `ChannelProfile.java`, `VideoJob.java`, `CreateJobRequest.java`, `JobResponse.java`, `JobService.java`, `ScriptService.java`, `FastApiClient.java` + 기존 테스트 3개의 mock 인자.
- Create `frontend/src/lib/contentNature.js`; modify `Admin.jsx`, `JobNew.jsx`, `JobDetail.jsx`(뱃지).

---

### Task 1: FastAPI 등급 모듈

**Files:** Create `app/utils/content_nature.py`, `tests/test_content_nature.py`

**Interfaces — Produces:**
`FACTUAL/EXPLAINER/STORY: str`, `normalize_nature(value) -> str`, `category_label_for(nature, category, labels) -> str`, `fact_check_prompt(nature, factual_prompt) -> str`, `script_system_prompt(nature, factual_prompt) -> str`, `nature_directive(nature) -> str`, `story_seed_facts(keyword, benchmark_analysis, source_videos) -> list[dict]`, `ensure_story_disclosure(sections) -> list[dict]`, `DISCLOSURE_SENTENCE`.

- [ ] **Step 1: 실패 테스트 작성** — `tests/test_content_nature.py`:

```python
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
    assert "삭제" in prompt or "제외하지" in prompt
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
    sections = [{"content": "옛 고을에 한 선비가 살았습니다."}, {"content": "어느 날 비가 왔습니다."}]
    out = cn.ensure_story_disclosure(sections)
    assert out[0]["content"].startswith(cn.DISCLOSURE_SENTENCE)
    assert out[1]["content"] == "어느 날 비가 왔습니다."


def test_ensure_story_disclosure_keeps_existing_marker():
    sections = [{"content": "오늘은 전해 내려오는 이야기를 들려드립니다."}]
    assert cn.ensure_story_disclosure(sections)[0]["content"] == sections[0]["content"]
```

- [ ] **Step 2: 실패 확인** — `docker cp` 후 `docker exec pipeline_fastapi pytest tests/test_content_nature.py -q` → ImportError.

- [ ] **Step 3: 구현** — `app/utils/content_nature.py`:

```python
"""콘텐츠 성격(사실성 등급)별 대본 생성 정책. FACTUAL은 기존 동작을 그대로 유지한다."""
from __future__ import annotations

import re
from typing import Any, Optional

FACTUAL = "FACTUAL"
EXPLAINER = "EXPLAINER"
STORY = "STORY"
NATURES = (FACTUAL, EXPLAINER, STORY)

DISCLOSURE_SENTENCE = "지금부터 들려드릴 이야기는 전해 내려오는 이야기입니다."
_DISCLOSURE_MARKER = re.compile(r"전해\s*내려|전해지는|옛날|옛적|이야기|야담|설화|지어낸|창작")

_CURIOSITY = (
    "대본의 중심은 시청자가 궁금해할 지점을 짚으며 이야기를 끌고 가는 것입니다. "
    "수치·통계·연표는 이야기를 받치는 재료일 뿐이며 나열하지 마세요. "
    "첫 문장에서 가장 궁금한 지점을 던지고, 질문을 열었으면 곧바로 답으로 이어 가세요."
)

_FACT_CHECK_EXPLAINER = f"""당신은 화제 콘텐츠·이슈 해설 영상의 자료 검토자입니다.

역할:
- 벤치마크 영상 분석과 수집된 자료에서 영상에 쓸 수 있는 내용을 정리합니다.
- 벤치마크 영상이 다룬 내용은 이미 검토된 원본으로 보고, 확인 시스템이 못 찾았다는 이유로 제외하지 않습니다.
- 확인하지 못한 수치·인용·날짜는 삭제하지 말고 confidence를 낮추고 완화 표현("~로 알려져 있습니다")이 필요함을 표시합니다.
- 삭제·모순 표시는 자료끼리 명백히 충돌하는 경우에만 합니다.
- 전문가·출처마다 시각이 갈리는 내용은 하나로 단정하지 말고 "누가 그렇게 본다"로 정리합니다.

절대 금지:
- 제공된 자료에 없는 구체적 수치·날짜·인용의 창작
- 추측을 확정 사실처럼 표현

{_CURIOSITY}"""

_FACT_CHECK_STORY = f"""당신은 창작·야담형 영상의 소재 정리자입니다.

역할:
- 이 영상은 사실 보도가 아니라 전해 내려오는 이야기입니다. 줄거리와 등장 요소를 정리합니다.
- 실제 사건·실존 인물·통계처럼 보이는 사실 주장이 섞이지 않게만 점검합니다.

{_CURIOSITY}"""

_DIRECTIVES = {
    EXPLAINER: (
        "<content_nature>EXPLAINER</content_nature>\n"
        "이 영상은 화제 콘텐츠 해설입니다. 시장 데이터·투자 관점으로 끌고 가지 마세요. "
        "확인된 내용은 단정적으로, 확인이 덜 된 내용은 \"알려져 있습니다\"·\"영상마다 설명이 엇갈립니다\" 같은 "
        "완화 표현으로 쓰세요. 전문가마다 의견이 갈리면 \"A는 이렇게 보고 B는 다르게 봅니다\"로 관점을 밝히세요. "
        "시청자가 궁금해할 지점을 짚으며 이야기를 끌고 가세요.\n"
    ),
    STORY: (
        "<content_nature>STORY</content_nature>\n"
        "이 영상은 전해 내려오는 이야기입니다. 도입부에서 \"전해 내려오는 이야기\"임을 밝히고, "
        "실제 통계·실존 인물의 사실 주장처럼 들리는 문장은 쓰지 마세요. "
        "시청자가 다음이 궁금해지도록 장면을 이어 가세요.\n"
    ),
}

_LABELS = {EXPLAINER: "화제 콘텐츠 해설", STORY: "이야기"}
_FINANCE_RULE_MARKERS = ("수치를 자연스럽게 구어체로", "투자 조언이 아닌")


def normalize_nature(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in NATURES else FACTUAL


def category_label_for(nature: str, category: str, labels: dict[str, str]) -> str:
    nature = normalize_nature(nature)
    if nature != FACTUAL:
        return _LABELS[nature]
    return labels.get(category, "주식시장")


def fact_check_prompt(nature: str, factual_prompt: str) -> str:
    nature = normalize_nature(nature)
    if nature == EXPLAINER:
        return _FACT_CHECK_EXPLAINER
    if nature == STORY:
        return _FACT_CHECK_STORY
    return factual_prompt


def script_system_prompt(nature: str, factual_prompt: str) -> str:
    nature = normalize_nature(nature)
    if nature == FACTUAL:
        return factual_prompt
    lines = [
        line for line in factual_prompt.splitlines()
        if not any(marker in line for marker in _FINANCE_RULE_MARKERS)
    ]
    text = "\n".join(lines).replace("한국 금융 콘텐츠를 위한", f"{_LABELS[nature]} 영상을 위한")
    return f"{text}\n\n{_CURIOSITY}"


def nature_directive(nature: str) -> str:
    return _DIRECTIVES.get(normalize_nature(nature), "")


def story_seed_facts(keyword: str, benchmark_analysis: Optional[dict], source_videos: Optional[list]) -> list[dict]:
    """창작형은 팩트체크 대신 벤치마크 줄거리·구조를 소재로 삼는다. 항상 1건 이상 반환한다."""
    def seed(text: str, ref: str) -> dict:
        return {
            "fact": text, "figure": "", "source_field": ref, "source_ref": [ref],
            "confidence": 0.6, "cross_verified": False, "contradiction_detected": False,
        }

    facts = [seed(f"이야기의 소재: {keyword}", "topic")]
    analysis = benchmark_analysis or {}
    topic = str(analysis.get("topic_keyword") or "").strip()
    if topic and topic != keyword:
        facts.append(seed(f"벤치마크가 다룬 주제: {topic}", "benchmark_analysis"))
    for reason in analysis.get("reasons") or []:
        facts.append(seed(f"관심을 끄는 요소: {reason}", "benchmark_analysis"))
    if analysis.get("title_pattern"):
        facts.append(seed(f"이야기를 여는 구조 참고: {analysis['title_pattern']}", "benchmark_analysis"))
    for video in (source_videos or [])[:3]:
        title = str((video or {}).get("title") or "").strip()
        if title:
            facts.append(seed(f"참고 영상 주제: {title}", "source_video"))
    return facts


def ensure_story_disclosure(sections: list[dict]) -> list[dict]:
    """창작형은 도입부에 '전해 내려오는 이야기' 표기가 없으면 결정론적으로 붙인다."""
    if not sections:
        return sections
    head = " ".join(str(s.get("content") or s.get("text") or "") for s in sections[:2])
    if _DISCLOSURE_MARKER.search(head):
        return sections
    out = [dict(s) for s in sections]
    key = "content" if "content" in out[0] or "text" not in out[0] else "text"
    out[0][key] = f"{DISCLOSURE_SENTENCE} {str(out[0].get(key) or '').strip()}".strip()
    return out
```

- [ ] **Step 4: 통과 확인** — pytest 동일 명령 → PASS.
- [ ] **Step 5: 커밋** — `git add ...; git commit -m "feat(script): 콘텐츠 성격 등급 모듈 추가"`

---

### Task 2: ScriptWorker에 등급 연동

**Files:** Modify `script_worker.py`, `main.py`; Create `tests/test_script_content_nature.py`

**Interfaces — Consumes:** Task 1 함수들. **Produces:** `ScriptWorker.generate(..., content_nature: Optional[str] = None)`, 요청 필드 `content_nature: Optional[str] = None`.

수정 지점(모두 `nature = normalize_nature(content_nature)` 기준, FACTUAL은 무변경):
1. `generate()` 시그니처에 `content_nature` 추가; `category_label = category_label_for(nature, category, CATEGORY_LABELS)`.
2. 시장 데이터 수집: `if not market_data and nature == FACTUAL:` 로 제한, 아니면 `market_data = market_data if nature == FACTUAL else {}`.
3. 팩트체크: STORY면 `_multi_round_fact_check` 생략하고 `all_facts = story_seed_facts(...)`, `fact_check_log=["story: seed facts"]`. 그 외는 `_multi_round_fact_check(..., content_nature=nature)`에서 `fact_check_prompt(nature, FACT_CHECK_SYSTEM_PROMPT)` 사용(3곳). EXPLAINER는 R1 task 첫 줄의 "실제 시장 데이터에서"를 "제공된 자료에서"로 교체(FACTUAL은 원문 유지).
4. `_generate_with_verified_facts(..., content_nature=None)`: 시스템 프롬프트를 `script_system_prompt(nature, SCRIPT_SYSTEM_PROMPT)`로, `benchmark_block` 앞에 `nature_directive(nature)` 삽입.
5. 반환 직전: STORY면 `sections = ensure_story_disclosure(sections)` 및 `full_script` 재구성(`_narration_from_sections`); 비FACTUAL이면 `_ensure_no_unverified_financial_numbers` 호출 생략, `unverified_numbers = False`.
6. 결과 dict에 `"content_nature": nature` 포함.
7. `main.py`의 `ScriptGenerateRequest.content_nature`와 `generate(... content_nature=request.content_nature)`.

- [ ] **Step 1: 실패 테스트** — `tests/test_script_content_nature.py`. 기존 테스트(`test_script_prompt_market_and_hook_rules.py`)의 워커 생성/LLM 스텁 방식을 그대로 따라, (a) 비FACTUAL이면 `collect_for_category`가 호출되지 않는지, (b) STORY는 `_multi_round_fact_check`가 호출되지 않는지, (c) 프롬프트에 `nature_directive`가 들어가는지, (d) FACTUAL 프롬프트가 종전과 동일한지를 검증한다(구현 시 기존 테스트 파일의 fixture를 열어 동일 패턴으로 작성).
- [ ] **Step 2: 실패 확인 / Step 3: 위 지점 수정 / Step 4: 신규 + 기존 script 관련 테스트 전체 통과**(`pytest tests -q -k "script or narrative or candidate or content_nature"`).
- [ ] **Step 5: 커밋**

---

### Task 3: Spring — 등급 저장·전달

**Files:** Create `domain/ContentNature.java`; Modify `ChannelProfile.java`, `VideoJob.java`, `CreateJobRequest.java`, `JobResponse.java`, `JobService.java`, `ScriptService.java`, `FastApiClient.java`, 테스트 3종.

- `ContentNature` enum `{FACTUAL, EXPLAINER, STORY}`.
- `ChannelProfile.contentNature`: `@Enumerated(EnumType.STRING) @Column(name="content_nature", length=20) private ContentNature contentNature;` (null=FACTUAL 취급; Hibernate ddl-auto=update가 컬럼 추가).
- `VideoJob.contentNature` 동일 매핑 + `@Builder.Default`는 쓰지 않고 null 허용.
- `CreateJobRequest.contentNature` (null이면 서비스에서 결정).
- `JobService.createJob`: `resolveNature(request, channelId)` = 요청값 → 채널 기본값 → FACTUAL. `channelProfileRepository`는 이미 주입돼 있으므로(`findByChannelId`) 확인 후 사용.
- `JobResponse.contentNature` 노출.
- `FastApiClient.generateScript(..., Map candidateEvidence, String contentNature)` 오버로드 추가(기존 9인자는 null 위임), body에 `content_nature` 추가.
- `ScriptService.generate`가 `job.getContentNature()`를 전달. 기존 테스트 3개의 `generateScript(` stub은 새 시그니처에 맞춰 마지막 인자 `any()` 추가.
- 신규 테스트 `JobServiceContentNatureTest`(요청 우선 → 채널 기본 → FACTUAL) 작성. Mockito 중첩 stubbing 주의(반환값 먼저 변수로).
- [ ] 실패 테스트 → 구현 → gradle test 전체 통과 → 커밋.

---

### Task 4: 프론트엔드

- `src/lib/contentNature.js`: `NATURE_OPTIONS`(값·라벨·설명), `suggestNature(title, channelTitle)`(야담·설화·전설·옛날이야기·민담·괴담 → STORY; 주식·증시·코스피·나스닥·금리·환율·부동산·경제·정치·선거·정책·물가 → FACTUAL; 그 외 EXPLAINER).
- `Admin.jsx`: 채널 편집에 "콘텐츠 성격 기본값" select 추가, 저장 시 `contentNature` 포함(기존 `...channel` 스프레드에 편집값 추가).
- `JobNew.jsx`: form에 `contentNature`(채널 선택/벤치마크 시 기본값 채움). 벤치마크로 시작하면 `suggestNature`로 추천을 표시("추천: 해설형")하고 채널 기본값과 다르면 안내. 등급 select 제공. 사실형이 아닌 추천인데 사실형을 고르면 경고 문구만 표시. 제출 payload에 `contentNature`.
- `JobDetail.jsx`: 헤더 근처에 등급 뱃지.
- [ ] `npm run build` 통과 후 커밋.

---

### Task 5: 통합 검증

- [ ] FastAPI·Spring 이미지 재빌드(`docker compose up -d --build`), 전체 pytest(script 계열)·gradle test 통과 확인.
- [ ] Chrome(사용자 로그인 세션)에서: 채널 관리에서 등급 저장, 새 작업에서 등급 선택·추천 표시, 작업 상세 뱃지 확인.
- [ ] 해설형·창작형 실제 소재로 5분 대본 각 1편 생성(Anthropic 크레딧 소량 사용) → 도입 표기, 시장 얘기 미혼입, 완화 표현 확인.
- [ ] main 반영은 사용자 확인 후 PR/머지.
