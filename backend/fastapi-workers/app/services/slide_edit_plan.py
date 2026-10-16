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

PLAN_VERSION = "slide-edit-plan-v4"
# 2026-10-16 레퍼런스 영상 분석(5초 간격 프레임 전수 분류, 연구 영상 2종 + 벤치마크 1종):
#   기준 영상(32분 경제 해설): 스톡 67% · 어두운 회색 배경+글자 29% · 칠판 2% · 출연자 2%,
#   후편집이 붙은 시간 58%(강조 문구 22 · 목록 15 · 막대 8 · 카드 5 · 숫자 5), 배경 전환 12초 간격,
#   스톡 평균 17초 연속(최대 90초), 회색 배경 평균 8초(최대 30초). 데이터 카드는 어둡게 눌린 스톡 위에 얹힌다.
# v1은 칠판 97%로 계획해 이 분포와 어긋났다(칠판 17~71씬 234초 연속) — 폐기한다.
# 사진(Gemini, 유료)은 소량만 쓴다.
MAX_PHOTO_SCENE_RATIO = 0.08
# 스톡+사진 장면의 하한(시간 기준). 레퍼런스는 약 2/3다.
MIN_FOOTAGE_RATIO = 0.55
# 어두운 배경이 이어질 수 있는 최대 길이(초). 레퍼런스 기준 영상의 최대 30초.
MAX_BOARD_RUN_SECONDS = 30
# 배경 한 덩어리의 평균 길이 하한(초). 레퍼런스는 평균 12~19초마다 바뀐다. 이보다 잦으면 산만하다.
MIN_MEAN_BG_RUN_SECONDS = 9.0
# 후편집이 붙는 장면 비율의 허용 범위(레퍼런스 56~74%).
OVERLAY_RATIO_RANGE = (0.45, 0.80)
# 2026-10-16 운영자 요청: 20번 Job에서 스톡 영상만 후편집 없이 길게 이어지는 부분이 밋밋했다. 레퍼런스(스톡 장면의 41%에
# 후편집)보다 더 촘촘하게, 스톡 장면의 60% 이상에 후편집을 얹고 후편집 없는 스톡이 12초(약 3장면) 넘게 이어지지 않게 한다.
MIN_STOCK_OVERLAY_RATIO = 0.6
MAX_BARE_STOCK_SECONDS = 12.0
# 장당 Gemini 이미지 비용 추정(원장 기준 gemini_pro_request 1건).
GEMINI_IMAGE_EST_KRW = 339
BACKGROUNDS = {"board", "stock", "photo"}

_TYPE_GUIDE_START = _PLAN_SYSTEM_PROMPT.index("오버레이 종류(type):")
_TYPE_GUIDE_END = _PLAN_SYSTEM_PROMPT.index("절대 규칙:")
_TYPE_GUIDE = _PLAN_SYSTEM_PROMPT[_TYPE_GUIDE_START:_TYPE_GUIDE_END]

_SYSTEM_PROMPT = (
    "너는 한국 경제 해설 유튜브의 영상 편집 디렉터다. 승인 내레이션 전체(번호, 길이, 문장)를 읽고 장면마다 "
    "'배경'과 '후편집'을 한 번에 계획한다.\n\n"
    "이 채널의 레퍼런스 영상(32분 경제 해설, 프레임을 5초마다 전수 분류한 결과)의 실제 구성:\n"
    "- 화면의 약 2/3(67%)가 스톡 영상이다. 문장 내용을 글자 그대로 보여 주는 영상이 아니라 주제·분위기에 어울리는 "
    "상징적인 일반 영상(계산기, 국기, 빌딩 숲, 고민하는 사람, 서류, 항구, 도시 야경 등)이다.\n"
    "- 약 1/4(29%)은 어두운 회색 배경 위에 큰 문구·숫자·도식만 나오는 장면이고, 초록 칠판 챕터 카드는 2% 정도다.\n"
    "- 후편집(강조 문구·목록·막대 그래프·카드·큰 숫자)은 전체 시간의 약 58%에 붙고 나머지 약 40%는 자막만 나온다. "
    "데이터 카드·목록은 어둡게 눌린 스톡 영상 위에 얹히는 경우가 많다(스톡 장면의 41%).\n"
    "- 배경은 평균 12초(약 3장면)마다 바뀐다. 스톡은 평균 17초(3~4장면) 이어지고, 회색 배경은 평균 8초, "
    "최대 30초(7장면 이내)까지만 이어진 뒤 스톡이 끼어든다. 회색 배경이 길게 이어지면 지루하다.\n\n"
    "배경(background):\n"
    '- "stock": 기본값에 가까운 주력 배경(전체 시간의 약 60~70%). "stock_queries"에 스톡 사이트에서 찾을 '
    "영어 검색어 2개(촬영 가능한 구체적 피사체나 상징적 소재, 화면·그래프·숫자·글자 금지). 내용이 추상적이어도 "
    "주제에 어울리는 상징 영상(예: 금리 → 은행 건물, 불안 → 근심하는 사람)을 고른다.\n"
    '- "board": 어두운 회색 배경. 문구·숫자·도식 자체가 주인공인 장면(핵심 주장, 큰 숫자, 비교, 인과 흐름, 정리). '
    "전체의 약 25~30%이고 연속 7장면(30초)을 넘기지 않는다.\n"
    '- "photo": 스톡으로는 못 구하는 구체적 장면이 문장의 핵심일 때만(전체의 8% 이하, 드물게). '
    '"photo_brief"에 촬영 가능한 장소·인물·사물을 영어 한 문장으로. 화면·전광판·차트·간판·글자·숫자는 절대 '
    "언급하지 않는다.\n"
    "- board 장면은 반드시 overlay를 가진다. stock 장면도 문장의 숫자·핵심 주장·목록·비교를 overlay로 얹는다(스톡 장면의 "
    "60% 이상). 후편집 없이 영상과 자막만 나오는 스톡이 연속 3장면(약 12초)을 넘지 않게 한다. 기사 증거([article: …])가 "
    "붙은 장면은 article_card를 우선 쓴다.\n"
    "- key_text는 자막 문장을 그대로 되풀이하지 말고 핵심어(2~3단어, 16자 이내)만 뽑는다.\n"
    "- 후편집이 붙는 장면은 전체의 약 55~60%다(모든 장면에 붙이지 않는다). 연속 3장면 이상 같은 종류의 overlay를 "
    "반복하지 않는다. 종류 비중은 강조 문구(key_text) 약 40%, 목록(bullet_list) 25%, 막대(stat_chart)·"
    "수치 비교 15%, 큰 숫자(big_number) 10%, 흐름도(flow)·계산식 10% 정도.\n\n"
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
        # 기사 증거 유무(has_article)는 넣지 않는다: 수집이 실행마다 달라져 승인한 계획서가 조용히 바뀌었다.
        [PLAN_VERSION, [[i, briefs[i].get("narration", ""), round(float(briefs[i].get("duration") or 0), 2)]
                        for i in sorted(briefs)]],
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

    # 3) 사진(유료 Gemini) 상한: 초과분은 칠판으로 내린다(하이라이트 구간은 지킨다). 스톡은 비용이 없어 상한이 없고,
    #    대신 칠판 연속 길이와 스톡 하한은 plan_violations()가 검사해 부분 재계획으로 바로잡는다.
    limit = max(1, int(len(scenes) * MAX_PHOTO_SCENE_RATIO))  # 아주 짧은 영상도 사진 1장은 허용
    photos = [s for s in scenes if s["background"] == "photo"]
    demoted: list[int] = []
    if len(photos) > limit:
        def keep_priority(scene: dict) -> tuple:
            in_highlight = highlight_end_index is not None and scene["index"] <= highlight_end_index
            return (0 if in_highlight else 1, scene["index"])

        for scene in sorted(photos, key=keep_priority)[limit:]:
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


def plan_violations(plan: dict) -> dict:
    """레퍼런스 분석 기준(스톡 비중, 칠판 연속 길이, 후편집 비율)에 맞는지 검사한다.

    위반은 조용히 넘기지 않고 계획서에 남기며, 자동 보정(부분 재계획)과 화면 표시의 기준이 된다."""
    scenes = plan.get("scenes", [])
    total = sum(s["duration"] for s in scenes) or 1.0
    footage_seconds = sum(s["duration"] for s in scenes if s["background"] != "board")
    board_runs: list[dict] = []
    run: list[dict] = []

    def close_run() -> None:
        seconds = round(sum(s["duration"] for s in run), 1)
        if run and seconds > MAX_BOARD_RUN_SECONDS:
            board_runs.append({"scenes": [s["index"] for s in run], "seconds": seconds})

    for scene in scenes:
        if scene["background"] == "board":
            run.append(scene)
        else:
            close_run()
            run = []
    close_run()
    stock_scenes = [s for s in scenes if s["background"] == "stock"]
    stock_overlay_ratio = sum(1 for s in stock_scenes if s.get("overlay")) / max(len(stock_scenes), 1)
    bare_stock_runs: list[dict] = []
    bare: list[dict] = []

    def close_bare() -> None:
        seconds = round(sum(s["duration"] for s in bare), 1)
        if bare and seconds > MAX_BARE_STOCK_SECONDS:
            bare_stock_runs.append({"scenes": [s["index"] for s in bare], "seconds": seconds})

    for scene in scenes:
        if scene["background"] == "stock" and not scene.get("overlay"):
            bare.append(scene)
        else:
            close_bare()
            bare = []
    close_bare()
    overlay_ratio = sum(1 for s in scenes if s.get("overlay")) / max(len(scenes), 1)
    block_count = 1 + sum(1 for a, b in zip(scenes, scenes[1:]) if a["background"] != b["background"]) if scenes else 0
    mean_run_seconds = total / block_count if block_count else 0.0
    violations: list[str] = []
    if block_count > 1 and mean_run_seconds < MIN_MEAN_BG_RUN_SECONDS:
        violations.append("bg_switch_too_frequent")
    if board_runs:
        violations.append("board_run_too_long")
    if footage_seconds / total < MIN_FOOTAGE_RATIO:
        violations.append("footage_ratio_low")
    if not (OVERLAY_RATIO_RANGE[0] <= overlay_ratio <= OVERLAY_RATIO_RANGE[1]):
        violations.append("overlay_ratio_out_of_range")
    if bare_stock_runs:
        violations.append("bare_stock_run_too_long")
    if len(stock_scenes) >= 3 and stock_overlay_ratio < MIN_STOCK_OVERLAY_RATIO:
        violations.append("stock_overlay_ratio_low")
    return {
        "violations": violations,
        "footage_ratio": round(footage_seconds / total, 3),
        "overlay_ratio": round(overlay_ratio, 3),
        "stock_overlay_ratio": round(stock_overlay_ratio, 3),
        "bare_stock_runs": bare_stock_runs,
        "mean_bg_run_seconds": round(mean_run_seconds, 1),
        "long_board_runs": board_runs,
    }


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
    plan["quality"] = plan_violations(plan)
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
