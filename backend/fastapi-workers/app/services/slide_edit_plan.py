"""슬라이드형 "편집 계획서" — 씬별 생성에 앞서 배경과 후편집을 한 번에 계획·저장한다.

2026-10-16 운영자 결정(20번 Job 검토): TTS가 끝나면 장면마다 즉석에서 "스톡 → 없으면 Gemini"로 배경을
정하던 방식은 의도 없이 사진 비용만 늘리고(20번: Gemini 41장, 후편집 15%) 결과를 보고서야 무엇이 정해졌는지
알 수 있었다. 계획서는 레퍼런스(10~33초)처럼 칠판 위 모션그래픽을 주력으로 하고, 스톡·사진은 구체적인
현장이 필요한 구간에만 제한적으로 쓰도록 구간별 계획을 먼저 만든다.

- 이 계획서는 사람이 편집하는 문서가 아니라 자동화 파이프라인의 계약이다(저장·감사·실행 입력).
- 실행(SlidesWorker)은 계획서를 그대로 읽는다. 계획서가 없는 과거 Job은 기존 동작을 유지한다.
- 내레이션 원문(TTS·자막 기준)은 건드리지 않는다. 화면 글자는 모두 검증된 원문 구절이다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from app.services.slide_overlay import _PLAN_SYSTEM_PROMPT, validate_overlay_plan

logger = logging.getLogger(__name__)

PLAN_VERSION = "slide-edit-plan-v1"
# 스톡 영상 + 사진 장면의 상한(전체 장면 대비). 나머지는 칠판 모션그래픽이다.
MAX_PHOTO_SCENE_RATIO = 0.30
# 장당 Gemini 이미지 비용 추정(원장 기준 gemini_pro_request 1건).
GEMINI_IMAGE_EST_KRW = 339
BACKGROUNDS = {"board", "stock", "photo"}

_TYPE_GUIDE_START = _PLAN_SYSTEM_PROMPT.index("오버레이 종류(type):")
_TYPE_GUIDE_END = _PLAN_SYSTEM_PROMPT.index("절대 규칙:")
_TYPE_GUIDE = _PLAN_SYSTEM_PROMPT[_TYPE_GUIDE_START:_TYPE_GUIDE_END]

_SYSTEM_PROMPT = (
    "너는 한국 경제 해설 유튜브의 모션그래픽 편집 디렉터다. 승인 내레이션 전체(번호, 길이, 문장)를 읽고 장면마다 "
    "'배경'과 '후편집'을 한 번에 계획한다. 이 채널의 레퍼런스 영상은 대부분의 시간을 어두운 칠판 위에 텍스트·"
    "아이콘·도식·숫자가 가볍게 나타나는 모션그래픽으로 보여 주고, 스톡 영상이나 사진은 구체적인 현장이 꼭 "
    "필요한 구간에서만 짧게 쓴다.\n\n"
    "배경(background):\n"
    '- "board": 어두운 칠판. 기본값이다. 주장·원인·수치·용어·질문·전망·정리 등 대부분의 장면.\n'
    '- "stock": 실제 촬영 영상이 의미를 더하는 구체적인 장소·행동(출근길, 공장, 거래소 건물 외관 등). '
    '"stock_queries"에 스톡 사이트에서 찾을 영어 검색어 2개(구체적 피사체, 화면·그래프·숫자 금지).\n'
    '- "photo": 스톡으로는 못 구하는 구체적 장면이 문장의 핵심일 때만(드물게). "photo_brief"에 촬영 가능한 '
    "장소·인물·사물을 영어 한 문장으로. 화면·전광판·차트·간판·글자·숫자는 절대 언급하지 않는다.\n"
    "- stock과 photo를 합쳐 전체 장면의 약 30% 이하로 쓴다. 비슷한 배경이 이어지면 한 구간으로 묶어 같은 "
    "배경을 연속 사용해도 된다.\n"
    "- board 장면은 반드시 overlay를 가진다(key_text 이상). 주장 문장에는 핵심 구절과 맞는 icon을 붙인다.\n"
    "- 연속 3장면 이상 같은 종류의 overlay를 반복하지 않는다. 숫자가 있으면 big_number·compare_numbers·"
    "stat_chart를, 인과·순서는 flow를, 나열은 bullet_list를, 계산은 equation을 우선 쓴다.\n"
    "- stock/photo 장면에는 가벼운 key_text만 얹는다(도식은 board에서).\n\n"
    + _TYPE_GUIDE +
    "절대 규칙: 모든 text·items·value_text·left·result는 그 장면 내레이션(또는 직전 두 장면)에 적힌 글자를 "
    "그대로 옮긴다. 숫자를 계산·반올림·추정·새로 만들지 않는다.\n"
    "각 장면 줄은 이렇게 쓴다 — 출력은 JSON만:\n"
    '{"scenes":[{"index":번호,"background":"board|stock|photo","stock_queries":[...],"photo_brief":"...",'
    '"reason":"30자 이내 이유","overlay":{"type":"...", ...}}]}'
)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def plan_input_hash(briefs: dict[int, dict]) -> str:
    """같은 내레이션·같은 계획 버전이면 같은 해시 — 계획서 재사용 기준."""
    payload = json.dumps(
        [PLAN_VERSION, [[i, briefs[i].get("narration", ""), round(float(briefs[i].get("duration") or 0), 2),
                         bool(briefs[i].get("has_article"))] for i in sorted(briefs)]],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fallback_key_phrase(narration: str) -> str:
    """오버레이가 검증을 통과하지 못한 칠판 장면용 — 원문에서 그대로 떼어 낸 4~28자 구절."""
    text = re.sub(r"\s+", " ", str(narration or "")).strip()
    clauses = [c.strip(" ,.!?\"'") for c in re.split(r"[.!?,]|\s—\s", text) if c.strip()]
    candidates = [c for c in clauses if 4 <= len(c) <= 28]
    if candidates:
        return max(candidates, key=lambda c: (bool(re.search(r"\d", c)), len(c)))
    head = text[:28].rsplit(" ", 1)[0] if len(text) > 28 else text
    return head.strip(" ,.!?") or text[:28]


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 9000)
    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6", max_tokens=9000, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def _clean_queries(raw) -> list[str]:
    queries: list[str] = []
    for item in raw or []:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if 2 <= len(text) <= 60 and text.lower() not in {q.lower() for q in queries}:
            queries.append(text)
    return queries[:2]


def build_edit_plan(
    briefs: dict[int, dict], *, job_id: int, highlight_end_index: int | None = None, llm_call=None,
) -> dict | None:
    """장면별 배경·후편집 계획서를 만든다. Claude 호출/파싱 실패면 None(조용히 넘기지 않는다)."""
    if not briefs:
        return None
    lines = []
    for index in sorted(briefs):
        brief = briefs[index]
        article = ""
        if brief.get("has_article"):
            article = f" [article: {brief.get('article_brief', '')}{' / 수치계열 있음' if brief.get('has_numeric_series') else ''}]"
        lines.append(f"{index} ({float(brief.get('duration') or 0):.1f}초): {brief.get('narration', '')}{article}")
    prompt = "승인 내레이션 목록(번호 (길이): 문장):\n" + "\n".join(lines) + "\n\nJSON만 반환:"
    try:
        raw = _call_llm(_SYSTEM_PROMPT, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        entries = [e for e in (data.get("scenes") or []) if isinstance(e, dict)]
    except Exception as exc:
        logger.warning("편집 계획서 생성 실패: %s", exc)
        return None
    if not entries:
        return None
    return assemble_plan(briefs, entries, job_id=job_id, highlight_end_index=highlight_end_index)


def assemble_plan(
    briefs: dict[int, dict], entries: list[dict], *, job_id: int, highlight_end_index: int | None = None,
) -> dict:
    """Claude 응답을 결정론적으로 검증·보정해 계획서로 만든다(테스트 가능한 순수 함수)."""
    by_index: dict[int, dict] = {}
    for entry in entries:
        try:
            by_index.setdefault(int(entry.get("index")), entry)
        except (TypeError, ValueError):
            continue

    # 1) 후편집 검증(원문 일치·계산 검산·미지 아이콘 제거)은 기존 검증기를 그대로 쓴다.
    raw_overlays = []
    for index, entry in by_index.items():
        overlay = entry.get("overlay")
        if isinstance(overlay, dict) and index in briefs:
            raw_overlays.append({**overlay, "index": index})
    validated = validate_overlay_plan(raw_overlays, briefs)

    # 2) 배경 결정 + 보정
    scenes: list[dict] = []
    for index in sorted(briefs):
        entry = by_index.get(index, {})
        background = str(entry.get("background") or "").strip().lower()
        if background not in BACKGROUNDS:
            background = "board"
        scenes.append({
            "index": index,
            "background": background,
            "stock_queries": _clean_queries(entry.get("stock_queries")) if background == "stock" else [],
            "photo_brief": str(entry.get("photo_brief") or "").strip()[:240] if background == "photo" else "",
            "reason": str(entry.get("reason") or "").strip()[:60],
        })

    # 3) 사진·스톡 상한: 초과분은 칠판으로 내린다(뒤쪽 사진부터, 하이라이트 구간은 지킨다).
    limit = max(1, int(len(scenes) * MAX_PHOTO_SCENE_RATIO))  # 아주 짧은 영상도 사진 1장은 허용
    footage = [s for s in scenes if s["background"] != "board"]
    demoted: list[int] = []
    if len(footage) > limit:
        def keep_priority(scene: dict) -> tuple:
            in_highlight = highlight_end_index is not None and scene["index"] <= highlight_end_index
            return (0 if in_highlight else 1, 0 if scene["background"] == "stock" else 1, scene["index"])

        for scene in sorted(footage, key=keep_priority)[limit:]:
            scene.update(background="board", stock_queries=[], photo_brief="")
            demoted.append(scene["index"])

    for scene in scenes:
        index = scene["index"]
        brief = briefs[index]
        overlay = validated.get(index)
        board = scene["background"] == "board"
        if board and not overlay:
            phrase = fallback_key_phrase(brief.get("narration", ""))
            overlay = {"type": "key_text", "text": phrase, "icon": None, "explain": True, "source": "fallback_phrase"}
        if overlay:
            overlay = dict(overlay)
            overlay["explain"] = board
        scene["overlay"] = overlay
        # 스톡을 못 찾으면 사진 대신 칠판으로 내려가므로 그때 쓸 구절을 미리 둔다(결정론적).
        if scene["background"] == "stock":
            scene["fallback_key_text"] = fallback_key_phrase(brief.get("narration", ""))
        scene["duration"] = round(float(brief.get("duration") or 0), 2)
        scene["narration"] = brief.get("narration", "")

    plan = {
        "version": PLAN_VERSION,
        "job_id": job_id,
        "input_hash": plan_input_hash(briefs),
        "created_at": time.time(),
        "highlight_end_index": highlight_end_index,
        "scenes": scenes,
    }
    refresh_plan(plan, demoted)
    return plan


def refresh_plan(plan: dict, demoted: list[int] | None = None) -> None:
    """장면 목록에서 구간·요약·총 길이를 다시 계산한다(전체 계획과 부분 재계획 공통)."""
    scenes = plan["scenes"]
    highlight_end_index = plan.get("highlight_end_index")
    segments: list[dict] = []
    for scene in scenes:
        if segments and segments[-1]["background"] == scene["background"] \
                and segments[-1]["scenes"][-1] == scene["index"] - 1:
            segments[-1]["scenes"].append(scene["index"])
            segments[-1]["seconds"] = round(segments[-1]["seconds"] + scene["duration"], 2)
        else:
            segments.append({
                "background": scene["background"], "scenes": [scene["index"]], "seconds": scene["duration"],
            })
    by_index = {s["index"]: s for s in scenes}
    for number, segment in enumerate(segments, start=1):
        first, last = segment["scenes"][0], segment["scenes"][-1]
        segment["id"] = number
        segment["range"] = [first, last]
        types = [by_index[i]["overlay"]["type"] for i in segment["scenes"] if by_index[i]["overlay"]]
        segment["overlay_types"] = sorted(set(types))
        segment["highlight"] = highlight_end_index is not None and last <= highlight_end_index

    photo = sum(1 for s in scenes if s["background"] == "photo")
    stock = sum(1 for s in scenes if s["background"] == "stock")
    overlay_counts: dict[str, int] = {}
    for scene in scenes:
        if scene["overlay"]:
            overlay_counts[scene["overlay"]["type"]] = overlay_counts.get(scene["overlay"]["type"], 0) + 1
    previous = (plan.get("summary") or {}).get("demoted_to_board", [])
    plan["scene_count"] = len(scenes)
    plan["total_seconds"] = round(sum(s["duration"] for s in scenes), 1)
    plan["segments"] = segments
    plan["summary"] = {
        "board": len(scenes) - photo - stock, "stock": stock, "photo": photo,
        "overlay_scenes": sum(1 for s in scenes if s["overlay"]),
        "overlay_types": overlay_counts,
        "demoted_to_board": demoted if demoted is not None else previous,
        # 스톡 미적중 시 사진이 아니라 칠판으로 내려가므로 Gemini는 photo 장면만 쓴다.
        "gemini_images_expected": photo,
        "gemini_krw_expected": photo * GEMINI_IMAGE_EST_KRW,
    }


# ---- 저장·읽기 ------------------------------------------------------------

def plan_path(job_id: int, data_dir: Path) -> Path:
    return Path(data_dir) / "jobs" / str(job_id) / "slides" / "edit_plan.json"


def save_plan(plan: dict, data_dir: Path) -> None:
    path = plan_path(int(plan["job_id"]), data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def load_plan(job_id: int, data_dir: Path) -> dict | None:
    try:
        data = json.loads(plan_path(job_id, data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == PLAN_VERSION else None


def plan_matches(plan: dict | None, briefs: dict[int, dict]) -> bool:
    return bool(plan) and plan.get("input_hash") == plan_input_hash(briefs)
