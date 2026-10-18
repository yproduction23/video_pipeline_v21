"""연출 단계 — 대본의 행간을 읽고 "시청자가 떠올릴 그림"을 장면마다 먼저 적은 뒤, 그 그림을 만들 도구를 고른다.

2026-10-17 운영자 요청: 모션그래픽은 자막을 그대로 옮기거나 엉뚱한 아이콘을 붙이는 것이 아니라, 대본의 뜻(행간)이 연상되는 그림이어야 한다
("8거래일 연속"이면 달력에 칸 8개가 나오고 X가 차례로 쳐지듯이). 규칙으로 표현을 하나씩 잡는 방식은 끝이 없으므로, Claude가 뜻을 읽고
도구 목록에서 고른다. 도구나 소재가 없으면 즉석에서 그리지 않고 주문 목록(missing)에 모은다 — 같은 화풍으로 미리 만들어 계속 쓴다.

이 단계는 이미지·영상 비용이 들지 않는다(Claude 호출 1회). 결과는 계획서의 후편집·시퀀스 설계가 소비하도록 이어 붙이기 전의 판단 자료다."""
from __future__ import annotations

import json
import logging
import os
import re

from app.services.slide_motifs import motif_labels, resolve_motif

logger = logging.getLogger(__name__)

CONCEPT_VERSION = "slide-concept-v1"

# 지금 만들어져 있는 도구(ready)와, 필요성만 모으는 계획 중인 움직임 부품(planned).
TOOLS: dict[str, dict] = {
    "none": {"ready": True, "hint": "후편집 없이 영상만(감정·연결·질문 문장 등 그림으로 옮길 내용이 없다)"},
    "key_text": {"ready": True, "hint": "핵심어 한 줄(그림으로 옮길 수 없는 주장·용어)"},
    "big_number": {"ready": True, "hint": "숫자 하나를 크게(변화량·규모)"},
    "motif_title_number": {"ready": True, "hint": "소재 일러스트 + 큰 숫자(금액=지폐 더미, 반도체=칩 등)"},
    "compare_pair": {"ready": True, "hint": "두 대상을 +, vs, →로 잇는 비교"},
    "rows": {"ready": True, "hint": "같은 대상의 두 시점·조건 수치 행"},
    "bars": {"ready": True, "hint": "같은 단위 수치 2~4개를 막대로 비교"},
    "list": {"ready": True, "hint": "이유·조건을 번호로 나열"},
    "flow": {"ready": True, "hint": "원인→결과 2~3단계 연결"},
    "streak": {"ready": True, "hint": "N일(거래일·주·개월) 연속: 달력 칸에 X가 차례로 쳐진다"},
    "equation": {"ready": True, "hint": "계산 과정(A − B = C)"},
    "article_card": {"ready": True, "hint": "보도 인용 카드"},
    "motif_move": {"ready": False, "hint": "소재가 A에서 B로 움직임(자금 유출입, 이동)"},
    "motif_gauge": {"ready": False, "hint": "게이지·계단·온도계처럼 늘거나 줄어드는 정도"},
    "motif_balance": {"ready": False, "hint": "저울·시소처럼 두 힘의 균형이나 기울어짐"},
    "motif_spread": {"ready": False, "hint": "도미노·번짐처럼 충격이 퍼져 나감"},
    "motif_support": {"ready": False, "hint": "아래에서 받침(하방 지지, 안전판)"},
    "motif_pile": {"ready": False, "hint": "소재가 쌓이거나 줄어듦(누적, 감소)"},
}

_SYSTEM = (
    "너는 한국 경제 해설 유튜브의 연출가다. 대본을 장면 단위로 읽고, 장면마다 '시청자가 그 말을 들으며 머릿속에 떠올릴 그림'을 "
    "먼저 한 줄로 적는다. 그 그림이 곧 화면에 나올 모션그래픽이다.\n\n"
    "원칙:\n"
    "1. 그림은 말의 뜻(행위·관계·수량·변화)을 옮긴 것이다. 자막 문장을 다시 쓴 글이나 막연한 장식(톱니바퀴, 벨, 돋보기)은 그림이 아니다.\n"
    "   예) '8거래일 연속 팔았다' → 달력 칸 8개에 X가 하루씩 쳐진다 / '돈이 한꺼번에 빠져나갔다' → 상자에서 돈다발이 쏟아져 나간다 /\n"
    "   '하방을 받쳐 주는 역할' → 아래에서 기둥이 받친다 / '충격이 번졌다' → 도미노가 차례로 쓰러진다.\n"
    "2. 그림으로 옮길 내용이 없는 장면(감정, 연결, 질문, 정리)은 tool을 none으로 두고 억지로 만들지 않는다.\n"
    "3. 도구(tool)는 아래 목록에서만 고른다. ready=false인 도구는 아직 없지만 필요하면 그대로 고른다(필요성을 모으는 것이다).\n"
    "4. 소재(motifs)는 '현재 소재' 목록의 이름만 그대로 쓴다. 목록에 없지만 필요한 소재나 움직임은 missing에 적는다: "
    '{"type": "motif" 또는 "motion_part", "name": 영문 소문자 슬러그, "description": 한국어 한 줄}. '
    "특정 사건 전용 그림이 아니라 다른 대본에서도 재사용할 수 있는 일반 소재만 요청한다(예: 열린 상자, 받치는 기둥).\n"
    "5. 대본에 없는 숫자·사실을 만들지 않는다. 그림은 뜻만 옮기고 숫자·문구는 후편집이 내레이션 원문에서 가져온다.\n\n"
    "도구 목록:\n" + "\n".join(f"- {name} (ready={str(meta['ready']).lower()}): {meta['hint']}" for name, meta in TOOLS.items()) + "\n\n"
    '출력은 JSON만: {"scenes":[{"index":번호,"evokes":"떠올릴 그림 한 줄","tool":"...","motifs":["현재 소재 이름"],'
    '"missing":[{"type":"...","name":"...","description":"..."}]}]}'
)


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 12000)
    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6", max_tokens=12000, system=system, messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def design_concepts(briefs: dict[int, dict], *, llm_call=None) -> dict | None:
    """장면별 {index, evokes, tool, tool_ready, motifs, missing}와 합친 주문 목록을 돌려준다. 호출이 실패하면 None(지어내지 않는다)."""
    if not briefs:
        return None
    lines = [f"{index}: {briefs[index].get('narration', '')}" for index in sorted(briefs)]
    labels = motif_labels()
    prompt = (
        "현재 소재: " + (", ".join(labels) if labels else "(없음)") + "\n\n대본(번호: 문장):\n" + "\n".join(lines) + "\n\nJSON만 반환:"
    )
    try:
        raw = _call_llm(_SYSTEM, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
    except Exception as exc:
        logger.warning("연출 단계(행간 읽기) 실패: %s", exc)
        return None

    replies = {}
    for item in data.get("scenes") or []:
        try:
            replies[int(item["index"])] = item
        except (KeyError, TypeError, ValueError):
            continue

    missing: dict[str, dict] = {}

    def order(entry: dict, scene: int) -> None:
        name = str(entry.get("name") or "").strip()[:60]
        if not name:
            return
        kind = "motion_part" if str(entry.get("type")) == "motion_part" else "motif"
        record = missing.setdefault(name, {"type": kind, "name": name, "description": str(entry.get("description") or "")[:120],
                                           "count": 0, "scenes": []})
        if scene not in record["scenes"]:
            record["scenes"].append(scene)
            record["count"] += 1

    concepts: list[dict] = []
    for index in sorted(briefs):
        item = replies.get(index) or {}
        tool = str(item.get("tool") or "none").strip()
        if tool not in TOOLS:
            tool = "none"
        motifs: list[str] = []
        for name in item.get("motifs") or []:
            label = resolve_motif(name)
            if label:
                motifs.append(label)
            else:  # 라이브러리에 없는 소재는 쓰지 않고 주문한다
                order({"type": "motif", "name": str(name), "description": "연출 단계가 요청한 소재(설명 없음)"}, index)
        for entry in item.get("missing") or []:
            if isinstance(entry, dict):
                order(entry, index)
        if tool == "none":
            motifs = []
        concepts.append({
            "index": index, "evokes": str(item.get("evokes") or "").strip()[:120], "tool": tool,
            "tool_ready": bool(TOOLS[tool]["ready"]), "motifs": list(dict.fromkeys(motifs)),
        })
    ordered = sorted(missing.values(), key=lambda m: (-m["count"], m["name"]))
    return {"version": CONCEPT_VERSION, "concepts": concepts, "missing": ordered}
