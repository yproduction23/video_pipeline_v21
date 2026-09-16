"""Original editorial profiles for spoken Korean finance scripts.

The profile deliberately captures reusable storytelling mechanics rather than
the wording, catchphrases, or distinctive voice of a named channel or writer.
Facts remain controlled by ScriptWorker's verified-facts boundary.
"""
from __future__ import annotations

import re
from typing import Any


DEFAULT_SCRIPT_STYLE_PROFILE = "original_finance_storyteller_v1"

# 특정 채널의 문체가 아닌, 검증 가능한 편집 장치만 정의한 하우스 스타일이다.
# signature_phrases는 권리 검수를 마친 사람이 별도 설정으로 추가한다.
HOUSE_STYLE_V1 = {
    "register": {
        "person": "formal_polite",
        "audience_terms": ["여러분", "우리"],
        "stake_framing": ["우리 계좌", "내 돈"],
    },
    "required_devices": {
        "longform": ["D1", "D2", "D4", "D5", "D6", "D8", "ending_nondirective"],
        "shorts": ["D1", "D4", "twist", "D6", "D8", "ending_nondirective"],
        "min_counts": {
            "fake_reader_q_longform": 2,
            "fake_reader_q_shorts": 1,
            "analogy_coverage_ratio": 0.8,
            "fear_reframe_longform": 1,
        },
    },
    "banned": {
        "signature_phrases": [],
        "buy_sell_verbs": ["매수", "매도", "사라", "팔아라", "담아라", "익절", "손절하라"],
        "hype": ["무조건 폭락", "무조건 급등", "확실하게 오른다", "지금 안 사면 후회"],
    },
    "hook_types": ["number_shock", "number_first", "question", "contrast", "loss_aversion", "belief_reversal"],
    # 제공된 측정 보충 노트에서 확정한 주식 타깃의 구조 수치다. 특정 채널의
    # 문장·표현을 복제하지 않고, 생성물의 호흡과 오프닝만 비교한다.
    "benchmark_stock_targets": {
        "formal_polite_ratio_min": 0.9,
        "opening_number_within_first_three_min": 1,
        "number_shock_or_direct_address_required": True,
        "shorts_sentence_chars_min": 15,
        "shorts_sentence_chars_max": 22,
    },
}

FINANCE_STORYTELLER_GUIDE = """
<editorial_style_profile name="original_finance_storyteller_v1">
Write an original Korean finance-storytelling script. Do not imitate, name,
quote, paraphrase, or reproduce the distinctive phrasing of any existing
creator, channel, or source. Use only the general editorial mechanics below.

Narrative architecture:
1. Opening (first 10%): begin with one familiar situation, tension, contrast,
   or a precise viewer question. State why the topic matters before explaining
   terminology. Do not reveal the entire conclusion in the first sentence.
2. Context (next 20%): give only the background a viewer needs, then connect
   each fact to a concrete consequence rather than listing numbers.
3. Investigation (middle 40%): alternate "what happened → why it happened →
   what would change the interpretation". Place a small turn, trade-off, or
   uncertainty between major facts to maintain forward motion.
4. Decision frame (next 20%): separate observed facts from interpretation and
   present conditions to watch, not buy/sell instructions or predictions.
5. Closing (last 10%): return to the opening question and leave the viewer
   with one memorable monitoring point and a natural next-question.

Spoken Korean craft:
- Write as a calm, perceptive narrator speaking to one viewer in consistent,
  natural polite Korean. Formal declarative endings such as ``~습니다/~입니다``
  are the default, but limited conversational honorific endings such as
  ``~인 겁니다``, ``~겠죠``, ``~고요``, ``~해볼까요?`` may be used when they
  improve spoken rhythm. Never mix banmal or casual ``해요체`` into the script.
- Write for one continuous TTS performance, not for line-by-line reading.
  Sentence length is determined by natural Korean breathing and meaning, not
  by caption width. A spoken sentence may contain a comma at a real thought
  boundary when the speaker would naturally take a light breath.
- For long-form narration, mix short emphasis sentences with medium explanatory
  sentences. Most explanatory sentences should naturally fall around 24-42
  Korean characters excluding spaces; allow up to 52 characters when one idea
  is clearer as a single sentence. Do not force a complete thought into 15-20
  characters merely to fit a subtitle line.
- Avoid three or more consecutive sentences with the same declarative function
  or the same ``~습니다/~입니다`` cadence. Change sentence function only when the
  facts support it: explanation → question → reversal → emphasis → reason.
- A real question must end in ``?`` and use a Korean interrogative ending such
  as ``~까요?`` or ``~습니까?`` so the narrator can lift the final intonation.
  Follow it immediately with a fact-based answer. Do not turn statements into
  questions just to add rhythm.
- Captions are a separate display layer: they will be split after synthesis at
  comma and meaning boundaries into roughly 15-20-character phrases. Do not
  insert unnatural punctuation, sentence endings, filler, or line breaks into
  the narration merely to control caption length.
- Keep short emphasis sentences as occasional beats, but never stack three
  short fragments in a row. If several adjacent facts belong to one thought,
  connect them into a natural spoken sentence instead of producing a checklist.
- Mix short emphasis sentences with medium explanatory sentences. One scene
  must carry one idea, one emotional beat, and one transition to the next.
- Translate a number into scale, comparison, cause, or consequence in the
  same or following sentence. Do not pile up bare figures.
- Use rhetorical questions sparingly (roughly 2–5 per 10 minutes), and answer
  them with evidence. Avoid exaggerated urgency, manufactured fear, filler,
  and repetitive "정리하면" endings.
- Make uncertainty legible: say what is confirmed, what is an interpretation,
  and what data would invalidate it.

Integrity rules:
- The verified facts are a hard boundary. Never add an unverified statistic,
  date, source, quote, causal claim, or forecast merely to improve drama.
- Preserve the requested scene structure and all [대사]/visual/pose labels.
- This is an original house style, not a simulation of a particular channel.
</editorial_style_profile>
""".strip()


def get_script_style_guide(
    profile: str | None = None,
    *,
    format_name: str = "longform",
    house_style_enabled: bool = False,
) -> str:
    """Return the supported original profile, safely falling back to default."""
    # Keeping one explicit profile makes future approved house-style variants
    # additive without allowing unreviewed creator imitation through a request.
    if not house_style_enabled:
        return FINANCE_STORYTELLER_GUIDE

    required = HOUSE_STYLE_V1["required_devices"].get(format_name, HOUSE_STYLE_V1["required_devices"]["longform"])
    return f"""{FINANCE_STORYTELLER_GUIDE}

<house_style_rules>
이 규칙은 특정 창작자의 문장이나 시그니처를 따라 하라는 뜻이 아니다.
검증된 사실을 설명하는 우리 채널의 편집 규칙으로만 사용한다.

- 레지스터: 반말·해요체를 섞지 않는 자연스러운 존댓말을 유지한다. 기본은 ``~습니다/~입니다``지만,
  실제 설명 흐름에 필요한 구간에서는 ``~인 겁니다``, ``~겠죠``, ``~고요``, ``~해볼까요?`` 같은
  준구어 존댓말을 제한적으로 사용해 동일 종결의 연속을 피한다. "여러분", "우리"를 과용하지 말고,
  사실의 영향을 "우리 계좌" 또는 "내 돈" 관점으로 연결할 때만 사용한다.
- 필수 장치: {", ".join(required)}.
- D1은 첫 3초 안에 검증된 숫자 하나와 질문 또는 대조를 함께 둔다. 숫자는 verified_facts의 값만 쓴다.
- 쇼츠는 첫 3문장 안에 검증된 숫자를 하나 이상 두고, 숫자 쇼크 또는 "님들" 같은 직접 호명 중 하나로 시작한다.
- 쇼츠의 평균 문장 길이는 대체로 15~22자로 유지하되, 뜻이 완결된 짧은 문장을 자막 폭 때문에 분절하지 않는다.
- D2는 롱폼에서만 초반에 일반적인 말로 오늘 다룰 항목 수를 예고한다.
- D3은 초보자가 헷갈릴 질문 한 줄을 새로 만들고, 바로 검증된 근거로 답한다.
- D4 비유는 추상 개념의 이해를 돕는 새 일상 비유만 쓴다. 다른 대본의 비유나 표현을 재사용하지 않는다.
- D5는 불안한 해석을 제시하되 공포 조장으로 끝내지 말고, 해석을 바꾸는 검증 조건을 제시한다.
- D6은 확인할 체크포인트 목록을 만들되 매수·매도 지시로 연결하지 않는다.
- D8은 남의 뉴스가 시청자의 판단에 왜 중요한지 연결하되 수익을 보장하지 않는다.
- 결말은 매수·매도·보유 지시가 아니라 다음에 확인할 조건 또는 지표로 끝낸다.
</house_style_rules>""".strip()


def assess_storytelling(
    sections: list[dict[str, Any]],
    script: str,
    *,
    format_name: str = "longform",
    house_style_enabled: bool = False,
) -> dict[str, Any]:
    """Provide transparent editorial QA without judging factual correctness.

    Scores are intentionally diagnostic: they flag a script that reads like an
    outline or number dump so an operator can revise it in semi-automatic mode.
    They never rewrite facts or claim that a named creator was replicated.
    """
    narration = " ".join(str(s.get("content") or s.get("text") or "") for s in sections).strip()
    source = narration or script or ""
    normalized = re.sub(r"\s+", " ", source)
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", normalized) if s.strip()]
    question_count = sum("?" in s for s in sentences)
    direct_address_count = len(re.findall(r"(?:여러분|님들|우리|너|지금|우리가|보시면|생각해\s*볼)", normalized))
    transition_count = len(re.findall(r"(?:그런데|반대로|다만|그래서|문제는|여기서)", normalized))
    figure_count = len(re.findall(r"\d", normalized))
    avg_length = round(sum(len(s.replace(" ", "")) for s in sentences) / len(sentences), 1) if sentences else 0

    signals = {
        "opening_hook": bool(re.search(r"(?:왜|무엇|정말|바로|지금|처음|결국|상황)", normalized[:300])),
        "story_transitions": transition_count,
        "viewer_connection": direct_address_count,
        "evidence_translation": bool(figure_count and re.search(r"(?:의미|영향|이유|때문|보여|해석)", normalized)),
        "closing_monitoring_point": bool(re.search(r"(?:확인|지켜볼|봐야|관찰)", normalized[-500:])),
    }
    checks = sum(bool(value) for value in signals.values())
    score = min(100, 35 + checks * 11 + min(12, transition_count * 2) + (8 if 18 <= avg_length <= 58 else 0))
    suggestions: list[str] = []
    if not signals["opening_hook"]:
        suggestions.append("첫 씬에 시청자의 상황 또는 핵심 질문을 한 문장으로 추가하세요.")
    if transition_count < max(2, len(sections) // 12):
        suggestions.append("사실 나열 사이에 원인·반전·조건을 잇는 전환 문장을 보강하세요.")
    if figure_count and not signals["evidence_translation"]:
        suggestions.append("각 핵심 수치 뒤에 왜 중요한지 또는 비교 기준을 붙이세요.")
    if not signals["closing_monitoring_point"]:
        suggestions.append("마지막에 매수·매도 지시 대신 다음에 확인할 지표를 남기세요.")

    result = {
        "profile": DEFAULT_SCRIPT_STYLE_PROFILE,
        "score": score,
        "signals": signals,
        "metrics": {
            "scene_count": len(sections),
            "sentence_count": len(sentences),
            "avg_sentence_chars": avg_length,
            "rhetorical_question_count": question_count,
        },
        "suggestions": suggestions,
    }
    if house_style_enabled:
        result["house_style"] = {
            "enabled": True,
            "format": format_name,
            "required_devices": HOUSE_STYLE_V1["required_devices"].get(format_name, []),
            "register": HOUSE_STYLE_V1["register"]["person"],
        }
    return result
