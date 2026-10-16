"""캐릭터 없는 "슬라이드형" 시각 모드 — 승인 내레이션에서 헤드라인 텍스트와
배경 영상 검색어를 생성한다.

2026-10-14 사용자 결정: archetype/V5 캐릭터 시스템이 비금융 주제에서 반복적으로
의미 불일치를 일으켜, 캐릭터 없이 "헤드라인 텍스트 + 무료 스톡 영상" 스타일의
대안 시각 모드를 추가한다. 이 함수는 내레이션 원문을 그대로 반환하는 게 아니라
화면에 표시할 "짧은 헤드라인"만 새로 만든다 — AGENTS.md 규칙상 승인 대본의
내레이션 원문(자막·TTS 기준)은 변경 대상이 아니므로, 헤드라인은 자막과는 별개의
보조 그래픽 텍스트다.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "너는 한국 뉴스 유튜브 채널의 모션그래픽 디렉터다. 캐릭터 없이 데이터 "
    "그래픽과 스톡 영상만으로 장면을 구성한다. 승인된 내레이션 한 문장을 읽고, "
    "화면에 큼직하게 띄울 짧은 한국어 헤드라인과, 그 장면에 어울리는 무료 스톡 "
    "영상을 찾기 위한 영어 검색어를 만든다. 내레이션의 사실·숫자·단위를 새로 "
    "만들거나 바꾸지 말고, 이미 승인된 문장 안의 핵심만 압축한다.\n\n"
    # 2026-10-14 사용자 재현 1: "배경이 안 맞는 게 많아, 직관적이어야 해" —
    # 추상적/은유적 검색어(예: "tension", "concept of justice")는 스톡
    # 사이트에서 장면과 무관한 결과가 많이 나왔다.
    # 2026-10-14 사용자 재현 2: "불특정 다수를 겨냥한 흉기 난동" 문장에서
    # 문장 전체를 뭉뚱그려 "kitchen knife"로 검색하니 범죄 맥락이 사라지고
    # 양파 써는 요리 영상이 나왔다(스톡 사이트에는 범죄용 칼 영상보다 요리
    # 칼 영상이 압도적으로 많음). 사용자 지침: 문장을 통째로 요약하지 말고,
    # 문장 안에서 "가장 안전하고 구체적으로 촬영 가능한 한 부분"을 찾아야
    # 한다 — 예를 들어 "불특정 다수를 겨냥한 흉기"라면 흉기 자체보다 "불특정 "
    # "다수"(많은 사람이 있는 공공장소) 쪽을, "시민이 의자로 막았다"라면 "
    # "의자로 막는 동작" 자체를 검색어로 삼는다.\n"
    "검색어를 만들 때 지켜야 할 것:\n"
    "1. 문장 전체를 요약하지 말고, 문장 안에서 가장 구체적이고 명확하게 촬영된 "
    "스톡 영상이 있을 법한 '한 장면'만 골라낸다.\n"
    "2. 흉기·무기 같은 단어를 그대로 검색어에 쓰면 안 된다 — 스톡 사이트에서는 "
    "요리·공구 영상으로 뒤섞여 나온다. 대신 그 문장이 묘사하는 상황(예: 사람이 "
    "많은 공공장소, 경찰 출동, 체포 장면, 특정 행동)을 검색어로 쓴다.\n"
    "3. 행동이 구체적이면(예: '의자로 막았다', '전자발찌를 찼다') 그 행동 자체를 "
    "검색어로 쓴다 — 'person' 같은 일반 명사보다 구체적 행동이 결과가 더 정확하다.\n"
    "4. 영어 검색어는 카메라로 실제 찍을 수 있는 구체적 피사체여야 하며, 텍스트나 "
    "로고가 들어간 그래픽을 검색어로 쓰지 않는다.\n"
    # 2026-10-16 운영자 요청(Gemini 비용 절감): 첫 검색어에 맞는 스톡이 없으면 곧바로 유료
    # AI 배경으로 넘어갔다. 같은 장면 의미를 다른 각도로 찍은 대체 검색어를 함께 받아
    # 스톡 적중률을 높인다(적합성 판정은 여전히 내레이션 맥락 기준 Claude 선택이 한다).
    "5. pexels_alt_queries에는 같은 장면의 의미를 다른 각도·더 넓은 피사체로 촬영한 영어 검색어 "
    "2개를 넣는다(예: 아파트 단지 항공 촬영 → \"apartment complex\", \"city skyline residential\"). "
    "둘 다 카메라로 실제 찍을 수 있는 구체적 피사체여야 하고, 첫 검색어와 같은 말이면 안 된다.\n"
    "JSON만 반환한다."
)

_FEW_SHOT_HINT = (
    '예: "매도세가 이번 주 방향을 가른다" -> {"headline": "매도가 멈추나", "pexels_query": "stock market trading floor", '
    '"pexels_alt_queries": ["stock exchange screens", "traders watching monitors"]}\n'
    '예: "그것도 전자발찌를 찬 상태에서 이 범행을 저질렀습니다" -> {"headline": "전자발찌 착용 중 범행", "pexels_query": "close up ankle monitor bracelet leg"}\n'
    '예: "불특정 다수를 겨냥한 흉기 난동, 이번이 처음이 아닌 겁니다" -> '
    '{"headline": "반복되는 무차별 범죄", "pexels_query": "crowded public place many people walking"} '
    '(흉기 자체가 아니라 "불특정 다수"=공공장소의 많은 사람에 초점)\n'
    '예: "마트 의자를 들어 가해자를 막고 제압에 가담했다" -> '
    '{"headline": "의자 들어 가해자 제압", "pexels_query": "person lifting chair defensively"} '
    '(의자를 드는 구체적 동작에 초점)'
)


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 300)
    api_key = os.environ["ANTHROPIC_API_KEY"]
    from anthropic import Anthropic

    response = Anthropic(api_key=api_key).messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


_CANDIDATE_SELECTION_SYSTEM_PROMPT = (
    "너는 한국 뉴스 유튜브의 자료영상 선택 담당자다. 승인된 내레이션 한 문장과, "
    "그 장면에 쓸 수 있는 무료 스톡 영상 후보들의 태그 목록을 받는다. 각 후보가 "
    "실제로 내레이션 내용과 맥락상 맞는지 판단해 가장 적합한 후보의 번호를 "
    "고른다. 단어가 우연히 겹치는 것과 실제로 장면이 맞는 것은 다르다 — 태그에 "
    "나라 이름이나 흔한 단어 하나만 겹쳐도 내용이 완전히 다르면 고르지 않는다.\n\n"
    # 2026-10-14 job 18 v5 재현: "시민이 먼저 나섰다"에 유럽 화재 진압 항공샷을,
    # "불안한 지하철 이용객"에 공포영화풍 클로즈업 얼굴을 골랐다 — 둘 다 15개
    # 후보 중 억지로 "그나마 비슷한" 걸 고른 경우다. 분위기나 감정 단어만
    # 비슷하고 실제 장소·행동·사물이 다르면 틀린 선택이다.
    "엄격 기준:\n"
    "1. 후보는 내레이션이 묘사하는 구체적 장소·행동·사물을 실제로 보여줘야 "
    "한다. 비슷한 '분위기'나 '감정'만 느껴지고 실제 장면 내용이 다르면 고르지 "
    "않는다 (예: 지하철 이용객 불안감 장면에 공포영화풍 얼굴 클로즈업을 고르면 "
    "안 된다. 시민이 나서는 장면에 화재 진압 장면을 고르면 안 된다).\n"
    "2. 15개 후보 중 하나를 억지로 고르는 것보다, 맞는 게 하나도 없으면 "
    "null을 반환하는 게 낫다 — 틀린 영상을 쓰는 것보다 AI로 새로 만든 배경이 "
    "항상 더 안전하다. 확신이 서지 않으면 null을 선택한다.\n"
    '3. {"index": 숫자 또는 null} 형태의 JSON만 반환한다.'
)


_RELAXED_SELECTION_SYSTEM_PROMPT = (
    "너는 한국 경제 해설 유튜브의 자료영상 선택 담당자다. 이 채널은 문장 내용을 글자 그대로 보여 주는 영상이 아니라, "
    "주제·분위기에 어울리는 상징적인 일반 스톡 영상(계산기, 국기, 빌딩 숲, 고민하는 사람, 서류, 항구, 도시 야경 등)을 "
    "화면 대부분에 깐다(레퍼런스 영상 분석: 화면의 약 2/3). 내레이션 한 문장과 후보 영상의 태그 목록을 받는다. "
    "기준: 1) 문장의 주제·분위기·상황과 어울리면 고른다. 정확히 같은 장면일 필요는 없다(예: 금리 이야기 → 은행·빌딩·계산기, "
    "불안 → 근심하는 사람). 2) 문장과 반대되는 분위기, 폭력·노출 등 부적절한 영상, 글자·로고·광고판이 큰 영상, "
    "특정 상표가 주인공인 영상은 제외한다. 3) 어울리는 후보가 하나라도 있으면 가장 어울리는 번호를 고른다. "
    "정말 하나도 없을 때만 null. 4) {\"index\": 숫자 또는 null} 형태의 JSON만 반환한다."
)

def select_best_candidate(
    narration: str, candidates: list[dict], *, llm_call=None, relaxed: bool = False,
) -> int | None:
    """후보 영상 목록 중 내레이션 맥락에 실제로 맞는 것의 색인을 Claude에게
    판단시킨다. 2026-10-14 사용자 재현: 검색어-태그 단어 겹침 개수로는
    "bonsai/japanese"가 "ankle monitor GPS"와 전혀 안 겹치는데도 통과되거나,
    "water lilies, lotus... korea"가 "handcuffed man arrested"와 무관한데도
    "korea" 하나로 통과되는 등 신뢰할 수 없었다. 맞는 후보가 없으면 None을
    반환해 AI 배경 생성 폴백으로 넘어가게 한다."""
    narration = str(narration or "").strip()
    if not narration or not candidates:
        return None
    lines = [f"{i}: {str(c.get('tags') or '').strip()}" for i, c in enumerate(candidates)]
    prompt = (
        f'내레이션: "{narration}"\n\n'
        "후보 목록(번호: 태그):\n" + "\n".join(lines) + "\n\n"
        '가장 적합한 후보의 번호만 고르거나, 맞는 게 없으면 null을 반환해라. '
        '{"index": 숫자 또는 null} 형태의 JSON만 반환해라.'
    )
    try:
        system = _RELAXED_SELECTION_SYSTEM_PROMPT if relaxed else _CANDIDATE_SELECTION_SYSTEM_PROMPT
        raw = _call_llm(system, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        index = data.get("index")
        if index is None:
            return None
        index = int(index)
        if 0 <= index < len(candidates):
            return index
        logger.warning("영상 후보 선택 응답이 범위를 벗어남: %r (후보 %d개)", index, len(candidates))
        return None
    except Exception as exc:
        logger.warning("영상 후보 선택 실패, 후보 없음으로 처리: %s", exc)
        return None


def _clean_alt_queries(raw, *, exclude: str) -> list[str]:
    """대체 검색어: 문자열만, 중복·빈 값 제거, 최대 2개."""
    if not isinstance(raw, list):
        return []
    seen = {exclude.lower()}
    cleaned: list[str] = []
    for item in raw:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if 2 <= len(text) <= 60 and text.lower() not in seen:
            seen.add(text.lower())
            cleaned.append(text)
        if len(cleaned) == 2:
            break
    return cleaned


def generate_slide_direction(narration: str, *, llm_call=None) -> dict:
    """내레이션 한 문장에서 {"headline": str, "pexels_query": str}를 만든다.

    LLM 호출이 실패하거나 거부되면, 내레이션 앞부분을 그대로 헤드라인으로 쓰고
    검색어는 일반적인 뉴스 자료화면으로 대체한다 — 장면 하나의 생성 실패가
    전체 배치를 막지 않는다.
    """
    narration = str(narration or "").strip()
    if not narration:
        return {"headline": "", "pexels_query": "news broadcast studio"}
    prompt = f'{_FEW_SHOT_HINT}\n\n내레이션: "{narration}"\n\nJSON만 반환:'
    try:
        raw = _call_llm(_SYSTEM_PROMPT, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        headline = str(data.get("headline") or "").strip()
        query = str(data.get("pexels_query") or "").strip()
        if headline and query:
            result = {"headline": headline, "pexels_query": query}
            alternatives = _clean_alt_queries(data.get("pexels_alt_queries"), exclude=query)
            if alternatives:
                result["alt_queries"] = alternatives
            return result
        logger.warning("슬라이드 연출 응답에 headline/pexels_query 누락, 폴백 사용: %r", data)
    except Exception as exc:
        logger.warning("슬라이드 연출 생성 실패, 폴백 사용: %s", exc)
    return {
        "headline": narration[:20],
        "pexels_query": "korean news broadcast city street",
    }
