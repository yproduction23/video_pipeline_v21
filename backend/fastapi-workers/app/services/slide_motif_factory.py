"""소재 즉석 생성 — 연출 단계가 "필요한데 라이브러리에 없다"고 주문한 소재를 작업당 한도 안에서 만들어 저장한다.

2026-10-19 운영자 결정: 연상에 필요한 그림이 없으면 그때그때 만든다. 작업(영상 1건)당 소재 예산(기본 ₩5,000, 최대 ₩10,000)을 두고,
한 번 만든 소재는 라이브러리(assets/motifs)에 저장해 다음 대본부터 재사용한다(소재에 쓰는 돈이 점점 줄어든다).

- 주문 항목: {"type": "motif", "name": 영문 슬러그, "description": 한국어 설명, "count", "scenes"} (slide_concepts의 missing)
- 만든 소재는 Claude 비전이 "설명과 맞는지·글자가 없는지·잘 오려졌는지" 확인하고 통과해야 등록한다(실패해도 비용은 예산에서 뺀다).
- 등록 항목에는 source.on_demand=true, review="pending"을 남겨 운영자가 나중에 육안으로 확인·삭제할 수 있다.
- 예산을 넘는 주문은 만들지 않는다(주문 목록에 남는다)."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from PIL import Image

from app.services import slide_motifs

logger = logging.getLogger(__name__)

UNIT_COST_KRW = 339            # Gemini 이미지 1장(기존 소재 생성 실적)
MAX_BUDGET_KRW = 10_000        # 설정과 상관없이 넘지 않는 상한(운영자: 작업당 5천원~1만원)

_NAMING_SYSTEM = (
    "너는 영상 편집용 소재 일러스트의 이름을 짓는 사람이다. 각 항목은 필요한 소재의 슬러그와 한국어 설명이다. "
    "소재마다 (1) 그림을 그릴 영어 한 문장 subject(글자·숫자·로고 없이 평평한 아이콘풍으로 그릴 수 있는 구체적인 사물 하나, 사람은 얼굴 없이), "
    "(2) 한국어 대표 이름 label(2~8자), (3) 대본에서 쓰는 한국어 별칭 aliases(2~5개, 흔히 쓰는 말)를 정한다. "
    "그릴 수 없는 추상 개념이거나 움직임 자체(애니메이션)를 요구하면 skip=true.\n"
    '출력은 JSON만: {"items":[{"slug":"...","subject":"...","label":"...","aliases":["..."],"skip":false}]}'
)

_ASSESS_SYSTEM = (
    "너는 영상용 소재 일러스트 검수자다. 이미지는 어두운 회색 배경 위에 놓인 투명 배경 소재 그림이다. "
    "(1) 요청한 소재를 알아볼 수 있게 그렸는지, (2) 글자·숫자·로고가 없는지, (3) 가장자리가 잘렸거나 초록색이 번진 곳이 없는지 확인한다. "
    '출력은 JSON만: {"ok": true|false, "reason": "한 줄"}'
)


def budget_krw(configured: object = None) -> int:
    """작업당 소재 예산(원). 설정값이 없으면 5,000원, 10,000원을 넘지 않는다."""
    try:
        value = int(configured if configured is not None else os.getenv("SLIDE_MOTIF_BUDGET_KRW", "5000"))
    except (TypeError, ValueError):
        value = 5000
    return max(0, min(value, MAX_BUDGET_KRW))


def affordable_count(budget: int, already_spent: int = 0) -> int:
    return max(0, (budget - already_spent) // UNIT_COST_KRW)


def _slug(text: object) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(text or "").strip().lower()).strip("_")[:40]


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 3000)
    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6", max_tokens=3000, system=system, messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def _json(raw: object) -> dict:
    cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
    match = re.search(r"\{[\s\S]*\}", cleaned)
    return json.loads(match.group(0) if match else cleaned)


def _default_vision_call(system: str, text: str, images: list[bytes]) -> str:
    import base64

    from anthropic import Anthropic

    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(images[0]).decode()}},
               {"type": "text", "text": text}]
    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(
        model="claude-sonnet-4-6", max_tokens=300, system=system, messages=[{"role": "user", "content": content}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def assess_motif(path: str, description: str, *, vision_call=None) -> tuple[bool, str]:
    """만든 소재가 설명과 맞는지 Claude 비전으로 확인한다. 확인 호출이 실패하면 통과시키지 않는다."""
    import io

    try:
        with Image.open(path) as image:
            sprite = image.convert("RGBA")
            sprite.thumbnail((768, 768))
            backdrop = Image.new("RGBA", sprite.size, (60, 62, 66, 255))
            backdrop.alpha_composite(sprite)
            buffer = io.BytesIO()
            backdrop.convert("RGB").save(buffer, "PNG")
        raw = (vision_call or _default_vision_call)(_ASSESS_SYSTEM, f"요청한 소재: {description}\nJSON만 반환.", [buffer.getvalue()])
        data = _json(raw)
        return bool(data.get("ok")), str(data.get("reason") or "")
    except Exception as exc:
        return False, f"소재 검수 확인 실패: {exc}"


def make_missing_motifs(
    missing: list[dict] | None, *, job_id: int, budget: int | None = None, directory: Path | str | None = None,
    generate=None, llm_call=None, vision_call=None,
) -> dict:
    """주문(missing) 중 소재(type=motif)를 예산 안에서 만들어 라이브러리에 등록한다.

    돌려주는 값: {"created": [{slug, label, aliases, description}], "failed": [...], "skipped": [...], "spent_krw": 원, "budget_krw": 원}"""
    directory = Path(directory or slide_motifs.MOTIF_DIR)
    limit = budget_krw(budget)
    result: dict = {"created": [], "failed": [], "skipped": [], "spent_krw": 0, "budget_krw": limit}
    orders = [m for m in (missing or []) if isinstance(m, dict) and m.get("type") == "motif" and _slug(m.get("name"))]
    if not orders or affordable_count(limit) <= 0:
        return result
    existing = {str(e.get("slug")) for e in _entries(directory)}
    orders = [m for m in orders if _slug(m["name"]) not in existing and not slide_motifs.resolve_motif(m["name"], directory)]
    if not orders:
        return result
    orders = orders[:affordable_count(limit)]
    lines = [f'- slug={_slug(m["name"])} / 설명={str(m.get("description") or "")[:120]}' for m in orders]
    try:
        named = {str(i.get("slug")): i for i in (_json(_call_llm(_NAMING_SYSTEM, "\n".join(lines), llm_call=llm_call)).get("items") or [])}
    except Exception as exc:
        logger.warning("소재 이름 짓기 실패(소재를 만들지 않는다): %s", exc)
        return result
    make = generate or slide_motifs.generate_motif
    stage = Path(tempfile.mkdtemp(prefix="motif_stage_"))     # 원본(초록 배경)은 임시 폴더에만 둔다
    for order in orders:
        slug = _slug(order["name"])
        info = named.get(slug) or {}
        if info.get("skip") or not str(info.get("subject") or "").strip() or not str(info.get("label") or "").strip():
            result["skipped"].append({"slug": slug, "reason": "그릴 수 있는 사물이 아님"})
            continue
        if result["spent_krw"] + UNIT_COST_KRW > limit:
            result["skipped"].append({"slug": slug, "reason": "소재 예산 초과"})
            continue
        raw = stage / f"{slug}_raw.png"
        try:
            make(str(info["subject"]), str(raw), job_id=job_id, scene_key=f"motif:{slug}")
        except Exception as exc:
            result["failed"].append({"slug": slug, "reason": f"생성 실패: {type(exc).__name__}"})
            result["spent_krw"] += UNIT_COST_KRW                 # 요청이 나갔다면 비용은 나간 것으로 본다
            logger.warning("소재 %s 생성 실패: %s", slug, exc)
            continue
        result["spent_krw"] += UNIT_COST_KRW
        try:
            with Image.open(raw) as image:
                keyed = slide_motifs._cropped(slide_motifs.key_out_green(image))
            target = directory / f"{slug}.png"
            keyed.save(target)
        except Exception as exc:
            result["failed"].append({"slug": slug, "reason": f"배경 제거 실패: {exc}"})
            continue
        ok, reason = assess_motif(str(target), str(order.get("description") or info.get("subject")), vision_call=vision_call)
        if not ok:
            target.unlink(missing_ok=True)
            result["failed"].append({"slug": slug, "reason": f"검수 불합격: {reason}"})
            continue
        aliases = [str(a).strip() for a in (info.get("aliases") or []) if str(a).strip()][:5]
        _register(directory, slug, str(info["label"]).strip(), aliases, job_id)
        result["created"].append({"slug": slug, "label": str(info["label"]).strip(), "aliases": aliases,
                                  "description": str(order.get("description") or "")})
    slide_motifs._library.cache_clear()          # 새 소재가 바로 resolve되게 한다
    return result


def _entries(directory: Path) -> list[dict]:
    try:
        return json.loads((directory / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _register(directory: Path, slug: str, label: str, aliases: list[str], job_id: int) -> None:
    entries = _entries(directory)
    entries.append({
        "slug": slug, "label": label, "aliases": aliases, "file": f"{slug}.png",
        "source": {"generator": slide_motifs.GENERATION_MODEL, "style": slide_motifs.STYLE_VERSION, "created": date.today().isoformat(),
                   "ledger_job": job_id, "on_demand": True, "review": "pending"},
    })
    (directory / "library.json").write_text(json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
