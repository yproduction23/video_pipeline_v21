"""벤치마크 영상 자막 전체에서 "구성"만 뽑아 낸다 (슬라이드형 한정으로 대본 플랜에 쓴다).

2026-10-16 운영자 요구: 벤치마크와 "내용 흐름까지 결이 같게" 나와야 한다. 지금까지는 자막 앞부분
4,000자로 줄거리 한 줄만 만들었고, 32분짜리 영상이어도 대본은 뉴스 12건에서 뽑은 사실만으로 짧게 끝났다.

이 모듈은 자막 전체를 한 번 읽어 아래만 구조화한다.
- 하이라이트가 미리 보여주는 것, 본론 구간의 순서·역할·비중·주장 유형
- 각 구간을 뉴스로 검증하려면 찾아야 할 질문(검색어)
저작권·사실성 경계: 문장은 인용하지 않고, 영상 속 주장과 수치는 사실이 아니라 "무엇을 다루는지"로만
기록한다. 근거는 이후 뉴스 검색과 팩트체크가 따로 채운다.
"""
from __future__ import annotations

import json
import re
from typing import Callable

MAX_TRANSCRIPT_CHARS = 60_000
MIN_SECTIONS = 3
MAX_SECTIONS = 8

SYSTEM = (
    "당신은 한국 유튜브 영상의 구성을 분석하는 편집 기획자입니다. 영상 자막 전체를 읽고, 이 영상이 어떤 순서로 "
    "무엇을 다루는지 '구성'만 구조화하세요. 규칙: 1) 자막의 문장을 그대로 또는 거의 그대로 옮기지 않습니다. "
    "2) 영상 속 주장과 수치는 사실이 아닙니다. 구체적 수치를 적지 말고 '어떤 종류의 수치를 다루는지'만 씁니다. "
    "3) 자막에 없는 내용은 만들지 않습니다. JSON 객체만 반환하세요."
)

_SCHEMA = """{
  "highlight": {"gist": "영상 맨 앞 하이라이트가 미리 보여주는 핵심 결과·반전을 한 줄로 (120자 이내, 대사 인용 금지)"},
  "sections": [
    {
      "role": "기|승|전|결 중 하나 또는 짧은 역할명(10자 이내)",
      "topic": "이 구간이 다루는 주제를 명사구로 (40자 이내)",
      "claim_type": "수치 제시|원인 분석|반론·통념 뒤집기|사례·비교|전망·시나리오|정리 같은 유형 (15자 이내)",
      "share_pct": 이 구간이 본론에서 차지하는 대략의 비중(정수, 전체 합 100),
      "search_queries": ["이 구간 내용을 뉴스로 검증하려고 찾아야 할 한국어 검색어 1~3개, 각 30자 이내, 수치·인용 금지"]
    }
  ],
  "ending": "결말 유형을 한 줄로 (80자 이내)",
  "tone": "말투·분위기를 한 줄로 (60자 이내)"
}"""


def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _validated_outline(data: dict) -> dict | None:
    sections = []
    for raw in (data.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(raw, dict):
            continue
        topic = _text(raw.get("topic"), 40)
        if not topic:
            continue
        try:
            share = max(0, min(100, int(raw.get("share_pct"))))
        except (TypeError, ValueError):
            share = None
        queries = []
        for item in raw.get("search_queries") or []:
            query = _text(item, 30)
            if len(query) >= 2 and query not in queries:
                queries.append(query)
        sections.append({
            "role": _text(raw.get("role"), 10) or "본론",
            "topic": topic,
            "claim_type": _text(raw.get("claim_type"), 15),
            "share_pct": share,
            "search_queries": queries[:3],
        })
    if len(sections) < MIN_SECTIONS:
        return None
    # 비중이 일부만 비어 있으면 비중 정보를 버리고 순서만 쓴다(잘못된 비율을 강요하지 않는다).
    if any(section["share_pct"] is None for section in sections):
        for section in sections:
            section.pop("share_pct", None)
    gist = _text((data.get("highlight") or {}).get("gist") if isinstance(data.get("highlight"), dict) else "", 120)
    outline: dict = {"sections": sections}
    if gist:
        outline["highlight"] = {"gist": gist}
    ending = _text(data.get("ending"), 80)
    if ending:
        outline["ending"] = ending
    tone = _text(data.get("tone"), 60)
    if tone:
        outline["tone"] = tone
    return outline


def extract_outline(llm_call: Callable[..., str], video: dict, transcript: str | None) -> dict | None:
    """자막 전체에서 구성(outline)을 만든다. 자막이 없거나 응답이 계약을 못 지키면 None."""
    transcript = str(transcript or "").strip()
    if len(transcript) < 400:
        return None
    meta = {key: video.get(key) for key in ("title", "durationSeconds")}
    prompt = (
        f"영상 정보: {json.dumps(meta, ensure_ascii=False)}\n"
        f"영상 자막 전체:\n{transcript[:MAX_TRANSCRIPT_CHARS]}\n\n"
        f"아래 스키마로 구성만 구조화하세요(본론 구간은 {MIN_SECTIONS}~{MAX_SECTIONS}개).\n{_SCHEMA}"
    )
    raw = llm_call(SYSTEM, prompt, 2500)
    match = re.search(r"\{[\s\S]*\}", raw or "")
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return _validated_outline(data)


def outline_search_queries(analysis: dict | None, *, limit: int = 8) -> list[str]:
    """벤치마크 구성에서 검증 검색어를 모은다(중복 제거, 최대 limit개).

    구간마다 첫 검색어를 먼저, 그다음 둘째 검색어 순으로 돌려 담아 뒤쪽 구간(전·결)도 근거 뉴스를 얻게 한다."""
    outline = (analysis or {}).get("outline") if isinstance(analysis, dict) else None
    if not isinstance(outline, dict):
        return []
    per_section = [
        [str(q).strip() for q in (section.get("search_queries") or []) if str(q).strip()]
        for section in outline.get("sections") or [] if isinstance(section, dict)
    ]
    queries: list[str] = []
    for round_index in range(3):
        for section_queries in per_section:
            if round_index < len(section_queries) and section_queries[round_index] not in queries:
                queries.append(section_queries[round_index])
                if len(queries) >= limit:
                    return queries
    return queries
