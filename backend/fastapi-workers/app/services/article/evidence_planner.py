"""Attach attributable Korean article evidence to approved script scenes.

This planner never rewrites narration.  It selects a verified fact already
present in a scene, resolves an exact sentence from a reviewed public article,
captures that sentence with DOM coordinates, and attaches visual metadata to
the existing scene id.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from bs4 import BeautifulSoup

from app import runtime_config
from app.config import REDIS_HOST, REDIS_PORT
from app.services.naver_api_hub import naver_api_hub_configured
from app.models.article_evidence import (
    ArticleCandidate,
    ArticleSource,
    EvidenceCaptureRequest,
)
from app.services.article.source_policy import assert_korean, publisher_for_url
from app.services.article_discovery import ArticleDiscoveryService, ArticleDiscoveryUnavailable
from app.services.evidence_capture import (
    EvidenceCaptureService,
    validate_public_http_url,
)
from app.services.scene_frames.emphasis_policy import BodyEmphasis, EmphasisPlan
from app.services.verbatim_guard import validate as validate_verbatim
from app.utils.statistic_chart_card import extract_numeric_series

logger = logging.getLogger(__name__)
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))

_NUMBER = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?\s*(?:%|퍼센트|원|달러|포인트|배|조|억|만)?")
_TOKEN = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9._-]{1,}")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_DIRECTION_GROUPS = (
    {"상승", "급등", "증가", "올랐", "반등", "강세", "돌파"},
    {"하락", "급락", "감소", "내렸", "약세", "붕괴"},
)
_STOPWORDS = {
    "그리고", "하지만", "때문", "대한", "관련", "시장", "현재", "이번",
    "것으로", "있습니다", "합니다", "했습니다", "입니다", "따르면",
    "오전", "오후", "거래일", "대비", "포인트",
}


class NarrationHashMismatch(RuntimeError):
    code = "NARRATION_HASH_MISMATCH"


@dataclass
class EvidencePlannerResult:
    scenes: list[dict[str, Any]]
    audit: dict[str, Any]


def narration_text(scene: dict[str, Any]) -> str:
    return str(scene.get("content") or scene.get("text") or "").strip()


def narration_hash(scene: dict[str, Any]) -> str:
    return hashlib.sha256(narration_text(scene).encode("utf-8")).hexdigest()


def _normalise(value: object) -> str:
    text = str(value or "").lower()
    text = text.replace("퍼센트", "%").replace(",", "")
    text = re.sub(r"(\d+(?:\.\d+)?)천", lambda m: str(float(m.group(1)) * 1000).removesuffix(".0"), text)
    return re.sub(r"\s+", "", text)


def _numbers(value: object) -> set[str]:
    return {_normalise(match.group(0)) for match in _NUMBER.finditer(str(value or ""))}


def _tokens(value: object) -> list[str]:
    # 2026-10-14 job 18 재현: "49분경"처럼 숫자+한글 단위가 붙은 표현에서
    # _TOKEN 정규식은 숫자로 시작하는 부분을 못 잡고 뒤쪽 한글만("분경")
    # 토큰으로 뽑아낸다. "분경"은 실제 단어가 아니어서 검색어에 섞이면
    # 네이버 뉴스 검색이 0건을 반환했다(검증된 사실조차 증거를 못 찾고
    # EVIDENCE_SENTENCE_UNVERIFIED로 건너뛰는 근본 원인이었다). 원문에서
    # 숫자에 바로 붙어 시작하는 매치는 독립 단어가 아니라 그 숫자 표현의
    # 꼬리 조각이므로 토큰에서 제외한다.
    text = str(value or "")
    values = []
    for match in _TOKEN.finditer(text):
        if match.start() > 0 and text[match.start() - 1].isdigit():
            continue
        lowered = match.group(0).lower().strip("._-")
        if lowered not in _STOPWORDS and len(lowered) >= 2 and lowered not in values:
            values.append(lowered)
    return values


def _direction(value: str) -> int:
    for index, group in enumerate(_DIRECTION_GROUPS, start=1):
        if any(word in value for word in group):
            return index
    return 0


_CONFIRMATION_SYSTEM_PROMPT = (
    "너는 한국 뉴스 유튜브의 팩트체크 담당자다. 대본에 이미 검증된 사실 한 "
    "문장과, 실제 기사에서 그대로 추출한 문장 후보 목록을 받는다. 각 후보가 "
    "그 사실을 실제로 '확인'해주는지 판단해 가장 명확히 확인해주는 후보의 "
    "번호를 고른다.\n\n"
    "엄격 기준:\n"
    "1. 어순이나 표현이 달라도(예: '벌어졌다' vs '발생했다') 같은 사건·같은 "
    "수치를 가리키면 확인으로 인정한다. 문자 그대로 똑같을 필요는 없다.\n"
    "2. 단, 다른 사건이거나 다른 수치(예: 다른 날짜, 다른 피해자 수)를 "
    "말하고 있으면 고르지 않는다.\n"
    "3. 이 문장은 화면에 실제 기사 스크린샷으로 캡처되어 인용으로 쓰인다 — "
    "확신이 서지 않으면 null을 선택한다. 틀린 인용보다 캡처 없이 넘어가는 "
    "게 항상 낫다.\n"
    '4. {"index": 숫자 또는 null} 형태의 JSON만 반환한다.'
)


def _call_llm(system: str, prompt: str, *, llm_call=None) -> str:
    if callable(llm_call):
        return llm_call(system, [{"role": "user", "content": prompt}], 300)
    from app.config import CLAUDE_MODEL

    api_key = os.environ["ANTHROPIC_API_KEY"]
    from anthropic import Anthropic

    response = Anthropic(api_key=api_key).messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


def _select_confirming_sentence(fact_text: str, candidates: list[str], *, llm_call=None) -> int | None:
    """실제 기사에서 추출한 문장 중, 검증된 사실을 의미상 확인해주는 문장의
    색인을 Claude에게 판단시킨다. 2026-10-14 job 18 재현 참고: 문자열 유사도
    0.85 기준으로는 "이날 흉기 난동은 오후 1시49분쯤 ... 벌어졌다"가 같은
    사건을 정확히 확인해주는데도(유사도 0.484) 전부 탈락했다. LLM은 문장을
    새로 쓰지 않고 candidates 중 하나를 고르기만 하므로, 선택된 문장은
    여전히 원문 그대로 DOM 캡처에 쓰인다."""
    if not candidates:
        return None
    lines = [f"{i}: {sentence}" for i, sentence in enumerate(candidates)]
    prompt = (
        f'검증된 사실: "{fact_text}"\n\n'
        "기사에서 추출한 문장 후보:\n" + "\n".join(lines) + "\n\n"
        '가장 명확히 확인해주는 후보의 번호만 고르거나, 없으면 null을 '
        '반환해라. {"index": 숫자 또는 null} 형태의 JSON만 반환해라.'
    )
    try:
        raw = _call_llm(_CONFIRMATION_SYSTEM_PROMPT, prompt, llm_call=llm_call)
        cleaned = re.sub(r"```json|```", "", str(raw or "")).strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        data = json.loads(match.group(0) if match else cleaned)
        index = data.get("index")
        if index is None:
            return None
        index = int(index)
        if 0 <= index < len(candidates):
            return index
        logger.warning("증거 문장 선택 응답이 범위를 벗어남: %r (후보 %d개)", index, len(candidates))
        return None
    except Exception as exc:
        logger.warning("증거 문장 선택 실패, 후보 없음으로 처리: %s", exc)
        return None


def _fact_text(fact: dict[str, Any]) -> str:
    values: list[str] = []
    normalized = ""
    for key in ("fact", "claim", "entity", "figure"):
        value = str(fact.get(key) or "").strip()
        if value and _normalise(value) not in normalized:
            values.append(value)
            normalized += _normalise(value)
    return " ".join(values)


def _source_ref_indexes(scene: dict[str, Any]) -> set[int]:
    indexes: set[int] = set()
    for raw in scene.get("source_refs") or []:
        match = re.fullmatch(r"(?:verified_)?facts\[(\d+)]", str(raw))
        if match:
            indexes.add(int(match.group(1)))
    return indexes


def _scene_fact_score(scene: dict[str, Any], fact: dict[str, Any], index: int) -> float:
    text = narration_text(scene)
    if index in _source_ref_indexes(scene):
        return 12.0
    fact_text = _fact_text(fact)
    figure = str(fact.get("figure") or "")
    score = 0.0
    if figure and _normalise(figure) in _normalise(text):
        score += 3.0
    phase = str(scene.get("phase") or scene.get("section") or "").lower()
    if phase in {"hook", "intro", "twist", "transition"}:
        score += 2.0
    terms = _tokens(fact_text)
    shared = [term for term in terms if term in text.lower()]
    score += min(3.0, len(shared))
    if re.search(r"[A-Z][A-Za-z0-9._-]+|[가-힣]{2,}(?:전자|그룹|정부|대통령|위원회|지수)", fact_text):
        score += 1.0
    return score


def _query_for(scene: dict[str, Any], fact: dict[str, Any]) -> tuple[str, list[str]]:
    # 2026-10-14 job 18 재현: verified_facts의 figure는 흔히 "2026-10-13 13:49 /
    # 인천시 남동구 홈플러스 구월점" 같은 "일시 / 장소" 복합 문자열이다. 과거
    # 코드는 이 raw 문자열을 토큰화 없이 그대로 검색어에 붙여, 슬래시·콜론·
    # ISO 날짜가 뒤섞인 검색어(예: "... 2026-10-13 13:49 / 인천시 ...")가
    # 만들어졌다 — 실제 기사 어디에도 이 표기가 그대로 나타나지 않아 네이버
    # 뉴스 검색이 0건을 반환했다(그 결과 evidence_planner가 모든 사실을
    # EVIDENCE_SENTENCE_UNVERIFIED로 건너뜀). figure는 이미 _fact_text()를
    # 통해 fact_text에 포함되어 _tokens(fact_text)로 토큰화되므로(날짜 숫자는
    # _TOKEN 정규식이 애초에 매칭하지 않음), figure를 다시 raw로 덧붙일
    # 필요가 없다 — 중복이자 검색어 오염의 원인이었다.
    fact_text = _fact_text(fact)
    terms = _tokens(fact_text)
    entity = str(fact.get("entity") or "").strip()
    selected = [entity] if entity else []
    for term in terms:
        if entity and _normalise(entity) in _normalise(term):
            continue
        if term in selected:
            continue
        selected.append(term)
        if len(selected) >= 5:
            break
    if len(selected) < 2:
        selected.extend(_tokens(scene.get("title") or narration_text(scene))[:3])
    return " ".join(dict.fromkeys(selected))[:100], selected


def _key_phrase(quote: str, preferred_numbers: set[str] | None = None) -> str | None:
    matches = list(_NUMBER.finditer(quote))
    preferred = preferred_numbers or set()
    match = next(
        (item for item in matches if _normalise(item.group(0)) in preferred),
        matches[0] if matches else None,
    )
    if not match:
        return None
    left, right = match.start(), match.end()
    if left > 0 and quote[left - 1] in "([":
        left -= 1
    if right < len(quote) and quote[right] in ")]":
        right += 1
    # Include the following predicate token ("상승한", "감소했다") while
    # retaining an exact DOM-searchable source slice.
    next_start = right
    while next_start < len(quote) and quote[next_start].isspace():
        next_start += 1
    next_end = next_start
    while next_end < len(quote) and not quote[next_end].isspace():
        next_end += 1
    if next_end > next_start:
        right = next_end
    return quote[left:right].strip(" ,.")


class ArticleBodyResolver:
    """Read only reviewed public article containers and return source sentences."""

    def sentences(self, candidate: ArticleCandidate) -> list[str]:
        # 2026-10-14 job 18 재현: follow_redirects=False 때문에 매일경제
        # 기사가 "https://www.mk.co.kr/article/..." -> "https://www.mk.co.kr/
        # news/society/..."로 URL 구조만 바뀐 정상적인 같은 매체 리다이렉트
        # 에서도 그냥 예외를 던지며 통째로 버려졌다 — 실제로는 의자로 가해자를
        # 제압한 시민을 다룬 정확한 기사였다. 리다이렉트를 따라가되, 승인
        # 매체 밖으로 벗어난 경우(예: mbn.mk.co.kr -> mbn.co.kr, 허용 목록에
        # 없음)는 그대로 거부한다 — 보안 경계(미검증 도메인 신뢰 금지)는
        # 그대로 유지한다.
        url = validate_public_http_url(candidate.url)
        rule = publisher_for_url(url)
        enforce_allowlist = bool(runtime_config.value("article_allowed_publishers_only"))
        if enforce_allowlist and rule is None:
            return []
        response = httpx.get(
            url,
            timeout=12,
            follow_redirects=True,
            headers={"User-Agent": "VideoPipelineEvidence/3.0 (public-source-resolver)"},
        )
        response.raise_for_status()
        final_rule = publisher_for_url(str(response.url))
        if enforce_allowlist and final_rule is None:
            logger.info("article candidate redirected outside the approved publisher allowlist: %s -> %s", url, response.url)
            return []
        rule = final_rule or rule
        soup = BeautifulSoup(response.text, "html.parser")
        for selector in (rule.excludes if rule else ("header", "nav", "aside", "footer")):
            for node in soup.select(selector):
                node.decompose()
        containers = []
        for selector in (rule.containers if rule else ("article",)):
            containers.extend(soup.select(selector))
        body = " ".join(node.get_text(" ", strip=True) for node in containers)
        assert_korean(str(soup)[:2000], soup.title.get_text(" ", strip=True) if soup.title else "", body[:4000])
        return [
            re.sub(r"\s+", " ", sentence).strip()
            for sentence in _SENTENCE.split(body)
            if 15 <= len(re.sub(r"\s+", " ", sentence).strip()) <= 500
        ]


class ArticleEvidencePlanner:
    def __init__(
        self,
        *,
        discovery: ArticleDiscoveryService | None = None,
        capture: EvidenceCaptureService | None = None,
        sentence_resolver: ArticleBodyResolver | None = None,
        audit_dir: Path | None = None,
        llm_call=None,
    ):
        self._discovery_injected = discovery is not None
        self.discovery = discovery or ArticleDiscoveryService()
        self.capture = capture or EvidenceCaptureService()
        self.sentence_resolver = sentence_resolver or ArticleBodyResolver()
        self.audit_dir = audit_dir or DATA_DIR
        self.llm_call = llm_call

    def attach(
        self,
        *,
        job_id: int,
        scenes: list[dict[str, Any]],
        verified_facts: list[dict[str, Any]],
    ) -> EvidencePlannerResult:
        output = copy.deepcopy(scenes)
        hashes_before = {str(scene.get("scene_id") or index): narration_hash(scene) for index, scene in enumerate(output)}
        audit: dict[str, Any] = {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "selected": [],
            "skipped": [],
        }
        if not bool(runtime_config.value("article_evidence_auto_enabled")):
            audit["status"] = "disabled"
            return EvidencePlannerResult(output, audit)
        if not verified_facts:
            audit["status"] = "unavailable"
            audit["reason"] = "verified_facts_missing"
            self._persist_audit(job_id, audit)
            return EvidencePlannerResult(output, audit)

        candidates: list[tuple[float, int, int]] = []
        for scene_index, scene in enumerate(output):
            if scene.get("article_capture"):
                continue
            for fact_index, fact in enumerate(verified_facts):
                score = _scene_fact_score(scene, fact, fact_index)
                if score >= 3.0:
                    candidates.append((score, scene_index, fact_index))
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

        used_scenes: set[int] = set()
        max_scenes = max(0, int(runtime_config.value("evidence_max_scenes")))
        for evidence_score, scene_index, fact_index in candidates:
            if len(used_scenes) >= max_scenes:
                break
            if scene_index in used_scenes:
                continue
            scene = output[scene_index]
            fact = verified_facts[fact_index]
            result = self._resolve_scene(job_id, scene, fact, fact_index, evidence_score)
            if result is None:
                audit["skipped"].append({
                    "scene_id": str(scene.get("scene_id") or scene_index),
                    "fact_index": fact_index,
                    "reason": "EVIDENCE_SENTENCE_UNVERIFIED",
                })
                continue
            capture, plan, selected_audit, numeric_series = result
            scene["visual_kind"] = "article_scene"
            scene["visual_type"] = "article_evidence"
            scene["article_capture"] = capture.model_dump(mode="json")
            if numeric_series:
                scene["article_capture"]["numeric_series"] = numeric_series
            scene["emphasis_plan"] = plan.model_dump(mode="json")
            scene["key_phrase"] = capture.key_phrase or ""
            scene["source_credit"] = f"출처: {capture.publisher} · {capture.published_at or ''}".rstrip(" ·")
            scene["evidence_match"] = selected_audit
            scene.setdefault("verified_facts", verified_facts)
            used_scenes.add(scene_index)
            audit["selected"].append(selected_audit)

        for index, scene in enumerate(output):
            scene_id = str(scene.get("scene_id") or index)
            after = narration_hash(scene)
            if hashes_before[scene_id] != after:
                raise NarrationHashMismatch(f"NARRATION_HASH_MISMATCH: scene={scene_id}")
        audit["narration_hashes"] = hashes_before
        audit["selected_count"] = len(audit["selected"])
        self._persist_audit(job_id, audit)
        return EvidencePlannerResult(output, audit)

    def _resolve_scene(
        self,
        job_id: int,
        scene: dict[str, Any],
        fact: dict[str, Any],
        fact_index: int,
        evidence_score: float,
    ):
        query, terms = _query_for(scene, fact)
        if not query:
            return None
        try:
            article_candidates = self._discover_cached(
                query,
                terms,
                max(3, int(runtime_config.value("evidence_max_searches_per_scene"))),
            )
        except (ArticleDiscoveryUnavailable, httpx.HTTPError, ValueError) as exc:
            logger.warning("article discovery unavailable for scene %s: %s", scene.get("scene_id"), exc)
            return None
        checked_urls: list[str] = []
        for candidate in article_candidates[: int(runtime_config.value("evidence_max_searches_per_scene"))]:
            checked_urls.append(candidate.url)
            publisher_rule = publisher_for_url(candidate.url)
            publisher = str(
                candidate.publisher
                or (publisher_rule.name if publisher_rule is not None else "")
            ).strip()
            if not publisher:
                logger.info("article candidate rejected without attributable publisher: %s", candidate.url)
                continue
            try:
                sentences = self.sentence_resolver.sentences(candidate)
            except Exception as exc:
                logger.info("article candidate rejected before quote resolution: %s (%s)", candidate.url, exc)
                continue
            match = self._best_sentence(narration_text(scene), fact, sentences)
            if match is None:
                continue
            similarity, quote = match
            key = _key_phrase(quote, _numbers(fact.get("figure") or ""))
            requested_body = self._body_policy(quote)
            request = EvidenceCaptureRequest(
                job_id=job_id,
                source_url=candidate.url,
                quote=quote,
                key_phrase=key,
                source_title=candidate.title,
                publisher=publisher,
                published_at=candidate.published_at,
                source=ArticleSource(
                    url=candidate.url,
                    publisher=publisher,
                ),
            )
            try:
                capture = self.capture.capture_dom(request)
            except Exception as exc:
                logger.info("article quote capture rejected: %s (%s)", candidate.url, exc)
                continue
            plan = EmphasisPlan(body=requested_body)
            downgraded = False
            if requested_body == BodyEmphasis.RECT and not capture.key_phrase_bboxes:
                plan = EmphasisPlan(body=BodyEmphasis.HIGHLIGHT)
                downgraded = True
            selected_audit = {
                "scene_id": str(scene.get("scene_id") or scene.get("id") or ""),
                "query": query,
                "terms": terms,
                "candidate_urls": checked_urls,
                "selected_url": candidate.url,
                "publisher": publisher,
                "quote": quote,
                "key_phrase": capture.key_phrase,
                "similarity": round(similarity, 4),
                "evidence_score": evidence_score,
                "fact_ref": f"facts[{fact_index}]",
                "requested_body": requested_body.value,
                "effective_body": plan.body.value,
                "downgraded": downgraded,
                "narration_sha256": narration_hash(scene),
            }
            # 2026-10-14 사용자 결정: 확인 문장과 같은 기사 안에 실제 연도별·
            # 항목별 숫자 계열(예: "△2023년 46건 △2024년 42건 △2025년
            # 39건")이 있으면 함께 넘긴다 — 텍스트 인용보다 그래프가 더
            # 명확한 레퍼런스 채널 편집 관행에 맞춘다. 숫자는 기사 문장에서
            # 정규식으로 그대로 추출하며, 반드시 "확인된" 기사에서만
            # 가져온다(검색만 되고 검증 안 된 기사의 숫자는 쓰지 않음).
            numeric_series = None
            for sentence in sentences:
                numeric_series = extract_numeric_series(sentence)
                if numeric_series:
                    break
            return capture, plan, selected_audit, numeric_series
        return None

    def _best_sentence(self, scene_text: str, fact: dict[str, Any], sentences: list[str]) -> tuple[float, str] | None:
        # 2026-10-14 job 18 재현: 기존에는 문자열 유사도(rapidfuzz ratio)가
        # 0.85 이상이어야 문장을 채택했다. 실제 기사 "이날 흉기 난동은 오후
        # 1시49분쯤 홈플러스 인천구월점이 입점한 대형 복합상가에서 벌어졌다"는
        # 같은 사건을 정확히 확인해주는데도 어순·표현이 달라 유사도 0.484로
        # 전부 탈락했다 — cross_verified=true인 사실조차 증거를 못 찾고
        # EVIDENCE_SENTENCE_UNVERIFIED로 건너뛰는 근본 원인이었다. 채널
        # 운영자 결정(2026-10-14): 문자열 유사도 대신 Claude가 "이 문장이 이
        # 사실을 실제로 확인해주는가"를 의미 기반으로 판단한다(오늘 Pixabay
        # 배경영상 매칭을 고칠 때와 동일한 방식). LLM은 문장을 새로 쓰지
        # 않고 이미 라이브 페이지에서 추출된 문장 중 하나를 고르기만 하므로,
        # 선택된 문장은 여전히 원문 그대로 DOM에서 검색·캡처된다.
        fact_text = _fact_text(fact)
        required_numbers = _numbers(fact_text)
        fact_direction = _direction(fact_text)
        fact_terms = set(_tokens(fact_text)[:8])
        candidates: list[str] = []
        for sentence in sentences:
            # 날짜·시각처럼 서로 다른 단위의 숫자가 fact_text에 여럿 섞여
            # 있으면(연/월/일/시/분) 자연스러운 한 문장이 전부를 동시에
            # 포함하는 경우가 거의 없다 — 하나라도 겹치면 통과시키고, 완전히
            # 다른 숫자(예: 다른 통계치)가 아닌지는 아래 validate_verbatim이
            # 별도로 막는다.
            if required_numbers and not (required_numbers & _numbers(sentence)):
                continue
            if fact_direction and _direction(sentence) not in {0, fact_direction}:
                continue
            if fact_terms and not (fact_terms & set(_tokens(sentence))):
                continue
            numeric_gate = validate_verbatim(sentence, {"verified_facts": [fact]})
            if required_numbers and not numeric_gate.passed:
                continue
            candidates.append(sentence)
            if len(candidates) >= 8:
                break
        if not candidates:
            return None
        index = _select_confirming_sentence(fact_text, candidates, llm_call=self.llm_call)
        if index is None:
            return None
        return 1.0, candidates[index]

    @staticmethod
    def _body_policy(quote: str) -> BodyEmphasis:
        if _numbers(quote):
            return BodyEmphasis.HIGHLIGHT_UNDERLINE
        if any(marker in quote for marker in ('"', "'", "말했다", "밝혔다", "설명했다")):
            return BodyEmphasis.HIGHLIGHT
        return BodyEmphasis.RECT if len(quote) <= 45 else BodyEmphasis.HIGHLIGHT

    def _discover_cached(self, query: str, terms: list[str], limit: int) -> list[ArticleCandidate]:
        key = "article:discover:v3:" + hashlib.sha256(
            json.dumps([query, terms, limit], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        client = self._redis()
        if client is not None:
            try:
                cached = client.get(key)
                if cached:
                    return [ArticleCandidate.model_validate(item) for item in json.loads(cached)]
            except Exception:
                pass
        result = self.discovery.discover(query, terms, limit)
        if client is not None:
            try:
                client.setex(key, 86_400, json.dumps([item.model_dump(mode="json") for item in result], ensure_ascii=False))
            except Exception:
                pass
        return result

    @staticmethod
    def _redis():
        try:
            import redis

            return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_timeout=1)
        except Exception:
            return None

    def _persist_audit(self, job_id: int, audit: dict[str, Any]) -> None:
        target = self.audit_dir / "jobs" / str(job_id) / "evidence" / "evidence_plan.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        client = self._redis()
        if client is not None:
            try:
                client.setex(f"evidence:audit:{job_id}", 7 * 86_400, json.dumps(audit, ensure_ascii=False))
            except Exception as exc:
                logger.warning("evidence audit Redis persistence deferred: %s", exc)
