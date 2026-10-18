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
    "motif_move": {"ready": True, "hint": "소재가 A에서 B로 움직임(자금 유출입, 이동)"},
    "motif_gauge": {"ready": True, "hint": "게이지·계단·온도계처럼 늘거나 줄어드는 정도"},
    "motif_balance": {"ready": True, "hint": "저울·시소처럼 두 힘의 균형이나 기울어짐"},
    "motif_spread": {"ready": True, "hint": "도미노·번짐처럼 충격이 퍼져 나감"},
    "motif_support": {"ready": True, "hint": "아래에서 받침(하방 지지, 안전판)"},
    "motif_chart": {"ready": True, "hint": "꺾은선이 그려지며 하락(빨강)·상승(초록) — 폭락·급등·하락 폭(온도계 대신)"},
    "motif_target": {"ready": True, "hint": "기대선(추정치)에 막대가 못 미치거나(하회) 넘는다(상회)"},
    "motif_mood": {"ready": True, "hint": "분위기 소품이 커지며 떨림 — 공포·패닉·긴장·환호(먹구름, 경광등, 폭죽, 폭탄 등)"},
    "motif_pile": {"ready": True, "hint": "소재가 쌓이거나 줄어듦(누적, 감소)"},
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
    "움직임 부품의 params(글자는 그 장면 대본에 글자 그대로 있는 2~12자 구절만. 숫자·정도는 지어내지 않는다):\n"
    '- motif_move: {"item": 현재 소재 이름(움직여 갈 것), "source_label": 보내는 쪽 구절(선택), "target_label": 받는 쪽 구절(선택)}\n'
    '- motif_gauge: {"label": 구절, "level": "high"|"low"|"rising"|"falling", "value": 대본에 있는 숫자 구절(선택)}\n'
    '- motif_balance: {"left": 구절, "right": 구절, "heavier": "left"|"right"}\n'
    '- motif_spread: {"origin": 시작 구절, "effect": 결과 구절}\n'
    '- motif_support: {"top": 받쳐지는 대상 구절, "pillar": 받치는 것 구절, "state": "holding"|"weakening"}\n'
    '- motif_pile: {"item": 현재 소재 이름, "trend": "grow"|"shrink", "label": 구절(선택)}\n'
    '- motif_chart: {"direction": "fall"|"rise", "label": 구절(선택), "value": 대본에 있는 숫자 구절(선택)}\n'
    '- motif_target: {"target_label": 기대선 구절(예: 추정치), "actual_label": 실제 쪽 구절, "result": "below"|"above"}\n'
    '- motif_mood: {"motif": 현재 소재 이름(분위기 소품), "label": 구절(선택)}\n'
    '- motif_move 추가(선택): "source_motif"/"target_motif": 상자 대신 보낼·받을 쪽 소재 이름, "arrival": "pulse"|"burst"(도착 반응)\n\n'
    "도구 선택 기준(뜻이 정확히 맞을 때만, 애매하면 none):\n"
    "- 폭락·급락·급등·하락 폭 → motif_chart(fall/rise). 온도계(motif_gauge)는 '금리가 높은 수준' 같은 정도·수준을 말할 때만 쓴다.\n"
    "- '불이 붙다/번지다/옮겨붙다' → motif_move(item=불꽃 소재, source_motif=보내는 쪽 도시·소재, target_motif=받는 쪽, arrival=burst). "
    "도미노(motif_spread)는 '연쇄적으로 쓰러진다/차례로 무너진다'를 말할 때만 쓴다.\n"
    "- '실적이 추정치를 밑돌았다/웃돌았다(하회·상회), 기대에 못 미쳤다' → motif_target(저울은 쓰지 않는다 — 어느 쪽이 무거운지로 읽혀 뜻이 뒤집힌다).\n"
    "- 저울(motif_balance)은 '두 힘 중 어느 쪽이 더 크고 결정적이다'를 말할 때만, heavier는 대본이 더 크다·중요하다고 말한 쪽이다.\n"
    "- '공포·패닉·불안 분위기', '환호·기대감' → motif_mood(해당 분위기 소재). 이런 장면에 글자만 띄우지 않는다.\n\n"
    "span_to: 이 그림이 뜻을 계속 갖는 마지막 장면 번호(같은 문장·같은 이야기가 이어지는 동안만. 대본이 다음 이야기로 넘어가면 그 앞 장면까지. 생략하면 그 장면 하나에서 끝난다).\n"
    "반복 금지: 앞 60초 안에 같은 관계·같은 설명을 이미 그림(흐름·이동 등)으로 보여 줬다면 다시 그리지 않는다.\n"
    "importance: 이 그림이 대본 이해에 얼마나 중요한가 1~3(3=이 대본에서 손꼽히게 중요하고 그림이 없으면 이해가 어려운 장면). "
    "그림은 자주 쓰면 정신이 없으므로 중요도가 낮은 장면은 tool을 none으로 두는 편이 낫다.\n\n"
    '출력은 JSON만: {"scenes":[{"index":번호,"evokes":"떠올릴 그림 한 줄","tool":"...","importance":1~3,"span_to":번호,"motifs":["현재 소재 이름"],'
    '"params":{...부품일 때만},"missing":[{"type":"...","name":"...","description":"..."}]}]}'
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
            "params": item.get("params") if isinstance(item.get("params"), dict) else {},
            "importance": int(item["importance"]) if str(item.get("importance") or "").isdigit() and 1 <= int(item["importance"]) <= 3 else 1,
            "span_to": int(item["span_to"]) if str(item.get("span_to") or "").isdigit() else index,
        })
    _fix_invalid_params(concepts, briefs, llm_call)
    ordered = sorted(missing.values(), key=lambda m: (-m["count"], m["name"]))
    return {"version": CONCEPT_VERSION, "concepts": concepts, "missing": ordered}


def _context_for(briefs: dict[int, dict], index: int) -> str:
    keys = sorted(briefs)
    position = keys.index(index)
    return " ".join(briefs[i].get("narration", "") for i in keys[max(0, position - 1):position])


def _fix_invalid_params(concepts: list[dict], briefs: dict[int, dict], llm_call=None) -> None:
    """부품 인자의 글자가 대본 원문과 달라 거절될 장면만 이유를 알려 주고 한 번 더 받는다(2026-10-18 시험: 22개 중 12개가 거절됐다).
    그래도 어긋나면 그 장면은 그림 없이 둔다(지어내지 않는다)."""
    from app.services.slide_motion_parts import validate_part

    def valid(concept: dict) -> bool:
        part = _PART_OF_TOOL.get(concept["tool"])
        return bool(part and validate_part(part, concept.get("params"), briefs[concept["index"]].get("narration", ""),
                                           _context_for(briefs, concept["index"])))

    invalid = [c for c in concepts if _PART_OF_TOOL.get(c["tool"]) and not valid(c)]
    if not invalid:
        return
    lines = [
        f'- 장면 {c["index"]}: 대본 "{briefs[c["index"]].get("narration", "")}" / 고른 도구 {c["tool"]} / 거절된 params '
        f'{json.dumps(c.get("params"), ensure_ascii=False)}'
        for c in invalid
    ]
    prompt = (
        "아래 장면의 부품 params가 거절됐다. 글자(label, source_label, left, origin 같은 문구)는 그 장면 대본(또는 직전 장면)에 "
        "글자 그대로 있는 2~12자 구절이어야 하고, level·heavier·state·trend는 허용된 값이어야 한다. 소재 이름(item)은 현재 소재 목록의 이름이어야 한다. "
        "대본에서 알맞은 구절을 그대로 복사해 params를 다시 쓴다. 맞는 구절이 없으면 tool을 none으로 한다.\n"
        + "\n".join(lines)
        + '\n\nJSON만 반환: {"scenes":[{"index":번호,"tool":"...","params":{...}}]}'
    )
    fixed: dict[int, dict] = {}
    try:
        raw = _call_llm(_SYSTEM, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        for item in json.loads(match.group(0) if match else cleaned).get("scenes") or []:
            fixed[int(item["index"])] = item
    except Exception as exc:
        logger.warning("연출 단계 params 재요청 실패(해당 장면은 그림 없이 둔다): %s", exc)
    for concept in invalid:
        item = fixed.get(concept["index"]) or {}
        tool = str(item.get("tool") or "none").strip()
        if tool in TOOLS and isinstance(item.get("params"), dict) and tool != "none":
            concept["tool"], concept["params"] = tool, item["params"]
            concept["tool_ready"] = bool(TOOLS[tool]["ready"])
        if not valid(concept):
            concept["tool"], concept["tool_ready"], concept["params"] = "none", True, {}


_PART_OF_TOOL = {
    "motif_chart": "chart", "motif_target": "target", "motif_mood": "mood",
    "motif_move": "move", "motif_gauge": "gauge", "motif_balance": "balance",
    "motif_spread": "spread", "motif_support": "support", "motif_pile": "pile",
}
# 숫자·계산이 핵심인 후편집, 기사 카드, 달력은 연출 결과로 덮어쓰지 않는다(숫자가 화면의 중심이다).
_KEEP_OVERLAY_TYPES = {"big_number", "stat_chart", "compare_numbers", "equation", "article_card", "streak", "bullet_list", "flow", "logo_row"}


# 연상 그림은 적게·다양하게·천천히(2026-10-18 운영자 피드백: 너무 빠르고 조잡하다, 같은 장면이 자주 나온다).
MIN_PART_GAP_SECONDS = 20.0       # 두 그림이 시작하는 시각 사이 최소 간격(초)
MAX_SPAN_SCENES = 3               # 한 그림이 이어지는 최대 장면 수
MAX_SPAN_SECONDS = 12.0
REPEAT_WINDOW_SECONDS = 60.0
MAX_ITEM_REPEATS = 2              # 같은 소재(예: 지폐 더미)를 쓸 수 있는 최대 횟수
MAX_ANIM_SECONDS = 8.0


def apply_concepts(plan: dict, concepts: dict | None) -> dict:
    """연출 단계가 고른 움직임 부품을 계획서 장면 후편집으로 바꾼다.

    인자가 검증을 통과한 후보만 쓰고, 중요도 순으로 뽑되 시작 간격·같은 부품 연속·같은 소재 반복을 제한한다(영상 20초당 1개 안팎).
    뽑힌 그림은 뒤따르는 후편집 없는 장면까지(최대 3장면) 이어서 천천히 진행한다."""
    if not concepts:
        return plan
    from app.services.slide_motion_parts import part_phrase, validate_part
    from app.services.slide_timing import overlay_start_offset

    scenes = plan["scenes"]
    by_index = {s["index"]: s for s in scenes}
    order = [s["index"] for s in scenes]
    starts, acc = {}, 0.0
    for scene in scenes:
        starts[scene["index"]] = acc
        acc += scene["duration"]
    limit = max(1, int(acc // MIN_PART_GAP_SECONDS))

    candidates = []
    rejected = 0
    for concept in concepts.get("concepts", []):
        part = _PART_OF_TOOL.get(concept.get("tool"))
        scene = by_index.get(concept.get("index"))
        if not part or not scene or scene.get("revised") or (scene.get("overlay") or {}).get("type") in _KEEP_OVERLAY_TYPES:
            continue
        position = order.index(scene["index"])
        context = " ".join(by_index[i]["narration"] for i in order[max(0, position - 1):position])
        params = validate_part(part, concept.get("params"), scene["narration"], context)
        if not params:
            rejected += 1
            continue
        candidates.append((concept, part, params))
    candidates.sort(key=lambda c: (-int(c[0].get("importance") or 1), c[0]["index"]))

    chosen: list[dict] = []
    occupied: set[int] = set()
    item_uses: dict[str, int] = {}
    for concept, part, params in candidates:
        if len(chosen) >= limit:
            break
        index = concept["index"]
        if index in occupied:
            continue
        start = starts[index]
        if any(abs(c["start"] - start) < MIN_PART_GAP_SECONDS for c in chosen):
            continue
        before = max((c for c in chosen if c["start"] < start), key=lambda c: c["start"], default=None)
        after = min((c for c in chosen if c["start"] > start), key=lambda c: c["start"], default=None)
        if any(c and c["part"] == part for c in (before, after)):
            continue
        item = str(params.get("item") or "")
        if item and item_uses.get(item, 0) >= MAX_ITEM_REPEATS:
            continue
        # 같은 부품에 같은 소재를 60초 안에 다시 쓰지 않는다(같은 설명의 반복, 2026-10-18 피드백).
        if any(c["part"] == part and str(c["params"].get("item") or "") == item and abs(c["start"] - start) < REPEAT_WINDOW_SECONDS
               for c in chosen):
            continue
        # 연출 단계가 "아직 같은 이야기"라고 한 장면(span_to)까지만 이어서 천천히 진행한다(2026-10-18: 이야기가 바뀐 뒤에도 그림이 남았다).
        span = [index]
        total = by_index[index]["duration"]
        position = order.index(index)
        span_to = int(concept.get("span_to") or index)
        for follower in order[position + 1:position + MAX_SPAN_SCENES]:
            if follower > span_to:
                break
            scene = by_index[follower]
            if (
                scene.get("revised") or follower in occupied or total + scene["duration"] > MAX_SPAN_SECONDS
                or scene["background"] != by_index[index]["background"]
                or (scene.get("overlay") and scene["overlay"].get("type") != "key_text")
            ):
                break
            span.append(follower)
            total += scene["duration"]
        head_scene = by_index[index]
        head_start = overlay_start_offset(head_scene.get("chunks") or [], part_phrase(part, params), head_scene["duration"])
        if len(span) > 1:
            anim = max(min(total - head_start - 0.5, MAX_ANIM_SECONDS), 3.0)
        else:
            anim = max(min(head_scene["duration"] - head_start - 0.4, 3.5), 0.8)
        chosen.append({"index": index, "start": start, "part": part, "params": params, "span": span, "anim": anim,
                       "head_start": head_start, "concept": concept})
        occupied.update(span)
        if item:
            item_uses[item] = item_uses.get(item, 0) + 1

    for entry in chosen:
        elapsed = -entry["head_start"]
        for number, index in enumerate(entry["span"]):
            scene = by_index[index]
            scene["overlay"] = {
                "type": "motion_part", "part": entry["part"], "params": entry["params"],
                "explain": scene["background"] == "board", "evokes": entry["concept"].get("evokes", ""), "source": "concept",
                "span": {"head": entry["index"], "index": number, "total_scenes": len(entry["span"]),
                         "elapsed_before": round(max(elapsed, 0.0), 3) if number else 0.0, "anim": round(entry["anim"], 3),
                         "last": number == len(entry["span"]) - 1},
            }
            elapsed += scene["duration"]
    plan["concept_applied"] = {"motion_parts": len(chosen), "scenes": sum(len(c["span"]) for c in chosen), "rejected": rejected}
    plan["concept_version"] = concepts.get("version")
    return plan
