"""벤치마크 영상의 공개 정보로 "뜨는 이유"를 Claude 1회 호출로 분석한다."""
from __future__ import annotations

import json
import os
import re
from typing import Callable

HOOK_TYPES = {"number_context", "belief_reversal", "time_contrast", "hidden_context", "direct_question", "human_stake"}

SYSTEM = """당신은 한국 유튜브 콘텐츠 분석가입니다.
주어진 영상의 공개 정보(제목·태그·설명·통계)만으로 이 영상이 왜 반응을 얻는지 분석하세요.
제공되지 않은 사실과 수치는 만들지 마세요. 다른 창작자의 문장을 그대로 인용하지 말고 유형과 구조만 요약하세요.
JSON 객체만 반환하세요."""


def claude_llm_call(system: str, user: str, max_tokens: int = 800) -> str:
    from anthropic import Anthropic
    from app.config import CLAUDE_MODEL

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    response = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def analyze_benchmark(llm_call: Callable[..., str], video: dict) -> dict:
    facts = {key: video.get(key) for key in ("title", "channelTitle", "tags", "description", "views", "viewsPerHour", "outperformanceIndex", "durationSeconds")}
    prompt = f"""영상 공개 정보: {json.dumps(facts, ensure_ascii=False)}

아래 스키마로 분석하세요.
{{
  "topic_keyword": "이 영상의 핵심 주제를 명사구로, 40자 이내 (클릭베이트 어투·느낌표 제거)",
  "reasons": ["이 영상이 반응을 얻는 이유 2~3개, 각 60자 이내"],
  "hook_type": "number_context|belief_reversal|time_contrast|hidden_context|direct_question|human_stake 중 하나",
  "title_pattern": "제목의 구조를 한 줄로 (예: 결과를 먼저 던지고 과정을 궁금하게 만듦)"
}}"""
    raw = llm_call(SYSTEM, prompt, 800)
    match = re.search(r"\{[\s\S]*\}", raw or "")
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ValueError("분석 응답을 JSON으로 읽을 수 없습니다.")
    topic = str(data.get("topic_keyword") or "").strip()[:40]
    reasons = [str(item).strip()[:60] for item in (data.get("reasons") or []) if str(item).strip()][:3]
    hook_type = data.get("hook_type") if data.get("hook_type") in HOOK_TYPES else None
    return {
        "topic_keyword": topic or None,
        "reasons": reasons,
        "hook_type": hook_type,
        "title_pattern": str(data.get("title_pattern") or "").strip()[:120],
    }
