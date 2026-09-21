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
- 참고 영상의 조회수·좋아요·댓글 수·시간당 조회수 같은 성과 수치는 사실로 추출하지 않습니다. 영상이 얼마나 뜨는지가 아니라 영상이 다루는 내용을 정리합니다.
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
        "자료(<verified_facts>, <youtube_topic_context>, <benchmark_points>)에 없는 구체적 사건·인물·장소·수치는 만들지 마세요. "
        "소재의 정체가 자료로 확인되지 않으면 사건을 단정하지 말고 \"화제가 된 영상\" 같은 일반적인 표현으로 풀어 가세요. "
        "참고 영상의 조회수·좋아요·성과 수치는 본문 재료로 쓰지 말고, 영상이 다루는 내용과 사람들이 궁금해할 이유를 이야기하세요. "
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


def story_seed_facts(keyword: str, benchmark_analysis: Optional[dict], source_videos: Optional[list],
                     topic_prefix: str = "이야기의 소재") -> list[dict]:
    """창작형은 팩트체크 대신 벤치마크 줄거리·구조를 소재로 삼는다. 항상 1건 이상 반환한다."""
    def seed(text: str, ref: str) -> dict:
        return {
            "fact": text, "figure": "", "source_field": ref, "source_ref": [ref],
            "confidence": 0.6, "cross_verified": False, "contradiction_detected": False,
        }

    facts = [seed(f"{topic_prefix}: {keyword}", "topic")]
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


def ground_explainer_facts(nature: str, verified_facts: list, keyword: str,
                           benchmark_analysis: Optional[dict], source_videos: Optional[list]) -> list:
    """해설형에서 확인된 사실이 없으면 벤치마크 자료를 참고 근거로 삼아 소재를 추측하지 않게 한다."""
    if normalize_nature(nature) != EXPLAINER or verified_facts:
        return verified_facts
    return story_seed_facts(keyword, benchmark_analysis, source_videos, topic_prefix="다루는 화제")
