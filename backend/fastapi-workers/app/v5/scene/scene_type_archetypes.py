"""씬 타입에 맞는 V5 무대 후보를 고르는 순수 규칙이다.

이 모듈은 아직 ScriptWorker, 프롬프트 빌더, 이미지 생성기와 연결되지 않는다.
검증된 씬 타입이 어떤 물리 정보 소품을 가진 무대와 어울리는지만 설명 가능한
형태로 반환한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.v5.scene.prompt_builder import ARCHETYPES


SCENE_TYPES = frozenset({"general", "metric", "graph", "diagram", "text"})


@dataclass(frozen=True)
class ArchetypeSelection:
    """프롬프트 생성 이전의 무대 추천 결과."""

    scene_type: str
    archetype: str
    alternatives: tuple[str, ...]
    selection_reason: str
    physical_surfaces: tuple[str, ...]
    primary_physical_surface: str


# 각 무대의 실제 ARCHETYPES/DIEGETIC_TEXT_GUIDANCE 정의에서 확인한, 정보가
# 장면 속 사물로 들어갈 수 있는 물리 표면이다. 별도 UI 카드나 빈 패널은 후보로
# 기록하지 않는다.
ARCHETYPE_SURFACES: dict[str, tuple[str, ...]] = {
    "port_emergency": (
        "the broad painted front side of the single largest foreground shipping container nearest the center dock",
        "dock warning plates",
        "customs signs",
    ),
    "retail_shock": (
        "the built-in receipt window recessed into the upper face of the single large checkout device",
        "shelf price tags",
        "product packaging",
    ),
    "classroom": (
        "the broad central chalk-writing area of the large green teaching wall",
        "pinned paper notes",
    ),
    "weather_map": (
        "the broad central geographic region of the single large curved illuminated map wall",
        "cloud-icon callouts",
        "broadcast map arrows",
    ),
    "risk_control_room": (
        "the single large central analog gauge dial face embedded at eye level in the curved operations wall",
        "metal warning plaques",
        "control console",
    ),
    "trade_calculator": (
        "the broad engraved front plinth directly beneath the scale's central pillar",
        "printed documents",
        "wall diagrams",
    ),
    "data_lab": (
        "the storyboard-planned laboratory, production-line, control-room, or analysis surface",
        "scene-specific equipment surfaces",
        "contextual chart or diagram props",
    ),
    "briefing_podium": (
        "the single broad policy-briefing surface embedded flush into the center back wall immediately behind the central podium",
        "podium nameplates",
        "teleprompters",
    ),
    "real_estate_office": (
        "the single broad market-and-loan guidance board fixed flush to the wall directly behind the consultation desk",
        "desk calculator displays",
        "window listings",
    ),
    "job_market_hall": (
        "the single broad employment-trend guidance board fixed flush to the wall directly behind the central consultation counter",
        "queue-ticket displays",
        "job-posting sheets",
    ),
    # earnings_stage: 실적 발표 무대 — 기업실적·EPS·배당 서사 전용
    "earnings_stage": (
        "the single broad corporate results-presentation mural embedded flush into the center back wall directly behind the executive podium",
        "earnings podium nameplates",
        "floor teleprompter stands",
    ),
}


# QualityGate가 primary 밖의 화면형 보조 소품만 검사하도록 쓰는 정규화 좌표다.
# LayoutPlan.fact_overlay는 후처리 안전영역이므로, 실제 세트 안의 물리 primary 표면
# 좌표와 혼용하지 않는다. 각 값은 (x, y, width, height)이며 0~1 프레임 비율이다.
PRIMARY_SURFACE_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "port_emergency": (0.10, 0.44, 0.35, 0.32),
    "retail_shock": (0.28, 0.34, 0.28, 0.26),
    "classroom": (0.06, 0.08, 0.54, 0.54),
    "weather_map": (0.06, 0.10, 0.64, 0.53),
    "risk_control_room": (0.33, 0.23, 0.34, 0.38),
    # 저울 기둥이 아니라 화면 하단의 넓은 저울 받침 전면을 primary로 쓴다.
    "trade_calculator": (0.36, 0.68, 0.38, 0.25),
    "data_lab": (0.12, 0.12, 0.47, 0.48),
    "briefing_podium": (0.23, 0.16, 0.56, 0.46),
    "real_estate_office": (0.03, 0.10, 0.46, 0.48),
    "job_market_hall": (0.27, 0.03, 0.48, 0.56),
    # earnings_stage: briefing_podium과 유사한 넓은 센터-백 표면 (살짝 더 넓고 낮음)
    "earnings_stage": (0.15, 0.08, 0.68, 0.54),
}


def primary_surface_region(archetype: str) -> tuple[float, float, float, float]:
    """화면형 보조 소품 탐지에서 제외할 primary 물리 표면 좌표를 반환한다."""
    try:
        x, y, width, height = PRIMARY_SURFACE_REGIONS[archetype]
    except KeyError as exc:
        raise ValueError(f"primary 표면 좌표가 없는 archetype: {archetype}") from exc
    if not (0.0 <= x < 1.0 and 0.0 <= y < 1.0 and width > 0.0 and height > 0.0 and x + width <= 1.0 and y + height <= 1.0):
        raise ValueError(f"primary 표면 좌표 범위가 잘못된 archetype: {archetype}")
    return x, y, width, height


# 숫자·그래프형 장면은 반드시 물리 표면이 이미 정의된 무대만 후보로 둔다.
# 첫 항목은 문맥 신호가 없을 때의 기본값이고, 이후 항목은 사람 검토용 대안이다.
TYPE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "graph": ("data_lab", "weather_map", "trade_calculator", "briefing_podium"),
    "metric": ("data_lab", "risk_control_room", "retail_shock", "trade_calculator", "weather_map", "port_emergency"),
    "diagram": ("classroom", "trade_calculator", "data_lab"),
    "text": ("classroom", "risk_control_room", "port_emergency"),
    "general": (
        "port_emergency",
        "classroom",
        "risk_control_room",
        "briefing_podium",
        "real_estate_office",
        "job_market_hall",
        "weather_map",
        "data_lab",
        "trade_calculator",
        "earnings_stage",    # 기업실적·EPS·배당 전용 — Phase 3-A에서 완전 구현
    ),
}


_GRAPH_MAP_HINTS = ("지도", "지역", "국가", "날씨", "map", "region", "country", "weather")
_GRAPH_COMPARISON_HINTS = ("막대", "비율", "공식", "bar", "formula")
_METRIC_RETAIL_HINTS = ("가격", "판매", "소비", "매장", "장바구니", "price", "retail", "checkout", "total")
_METRIC_RISK_HINTS = ("하락", "급락", "낙폭", "위험", "경고", "손실", "변동", "drop", "decline", "risk", "loss", "volatile")
# 흐름도라도 실제 지표의 상승·하락을 설명하는 경우에는 교실 판서보다 넓은
# 물리 그래프 벽이 있는 data_lab을 쓴다. 숫자는 여전히 후처리 사실층에서만 넣는다.
_DIAGRAM_DATA_LAB_HINTS = (
    "네트워크", "노드", "연결망", "홀로그램", "상승 추세", "하락 추세",
    "반도체", "웨이퍼", "칩", "영업이익", "생산 라인", "생산라인",
    "network", "node", "hologram", "trend", "semiconductor", "wafer", "chip", "production line",
)
_GENERAL_CLASSROOM_HINTS = ("설명", "원리", "배경", "explain", "principle", "background")
_TEXT_PORT_HINTS = ("항만", "물류", "컨테이너", "수출", "port", "logistics", "container", "shipping")

# ── 주식·금융 특화 힌트 (KOSPI/KOSDAQ/미국증시/기업실적) ─────────────────────
_STOCK_MARKET_BRIEFING_HINTS: tuple[str, ...] = (
    "코스피", "kospi", "코스닥", "kosdaq", "나스닥", "nasdaq",
    "s&p", "sp500", "다우", "dow", "뉴욕증시", "미국증시",
    "증시", "주가지수", "시장지수", "종합주가지수",
)
_STOCK_EARNINGS_HINTS: tuple[str, ...] = (
    "기업실적", "실적발표", "영업이익", "순이익", "eps", "roe",
    "어닝", "earning", "분기실적", "어닝서프라이즈",
    "배당", "주주환원", "자사주", "buyback",
)
_STOCK_RISK_HINTS: tuple[str, ...] = (
    "폭락", "급락", "패닉", "셀오프", "sell-off", "충격",
    "공매도", "위험자산", "리스크", "변동성", "vix",
    "서킷브레이커", "circuit breaker", "하한가",
)
_STOCK_CALCULATOR_HINTS: tuple[str, ...] = (
    "포트폴리오", "portfolio", "매수전략", "매도전략", "비중", "리밸런싱",
    "rebalancing", "수익률", "roi", "투자전략", "자산배분",
    "etf", "종목선정", "스크리닝",
)


def _scene_text(scene: dict) -> str:
    # narration/script/text_for_tts 키를 앞에 추가한다 (WO-1과 동일 우선순위 정책).
    fields = (
        "narration", "script", "narration_text", "text_for_tts",
        "title", "content", "text", "prompt_ko", "prompt_en", "visual_intent",
    )
    return "\n".join(str(scene.get(field) or "") for field in fields).lower()


def _has_any(text: str, hints: tuple[str, ...]) -> bool:
    return any(hint in text for hint in hints)


def _selection(scene_type: str, archetype: str, reason: str) -> ArchetypeSelection:
    candidates = TYPE_CANDIDATES[scene_type]
    if archetype not in candidates:
        raise ValueError(f"{scene_type} 유형에 허용되지 않은 archetype: {archetype}")
    if archetype not in ARCHETYPES or archetype not in ARCHETYPE_SURFACES:
        raise ValueError(f"물리 정보 표면이 정의되지 않은 archetype: {archetype}")
    surfaces = ARCHETYPE_SURFACES[archetype]
    return ArchetypeSelection(
        scene_type=scene_type,
        archetype=archetype,
        selection_reason=reason,
        primary_physical_surface=surfaces[0],
        physical_surfaces=surfaces,
        alternatives=tuple(candidate for candidate in candidates if candidate != archetype),
    )


def recommend_v5_archetype(scene: dict) -> ArchetypeSelection:
    """이미 분류된 scene_type과 기존 대본 문맥으로 V5 무대를 추천한다.

    이 함수는 이미지 프롬프트를 만들지 않고, 입력 씬을 수정하지도 않는다.
    """
    scene_type = str(scene.get("scene_type") or "").lower()
    if scene_type not in SCENE_TYPES:
        raise ValueError(f"지원하지 않는 scene_type: {scene_type}")
    text = _scene_text(scene)
    forced_archetype = str(scene.get("visual_archetype") or "").strip()
    if forced_archetype:
        if forced_archetype in TYPE_CANDIDATES[scene_type]:
            return _selection(scene_type, forced_archetype, "파일럿 장면 다양성을 위한 명시적 archetype 선택")
        # art_direction.py의 장면별 archetype 선택과 이 scene_type 분류는 서로
        # 다른 분류기다. 둘이 어긋날 수 있다(예: 금융 채널 전용 아트 디렉터가
        # 해설형 대본의 여론조사 퍼센트 장면에 맞지 않는 archetype을 고른 경우).
        # 강제 archetype을 무시하고 scene_type에 맞는 추천으로 안전하게 넘어간다.

    if scene_type == "graph":
        if _has_any(text, _GRAPH_MAP_HINTS):
            return _selection("graph", "weather_map", "지도·지역 기반 그래프이므로 지도 벽과 기상 아이콘이 있는 weather_map을 추천")
        if _has_any(text, _GRAPH_COMPARISON_HINTS):
            return _selection("graph", "trade_calculator", "비교·막대·비율 표현이므로 저울·문서·벽면 다이어그램이 있는 trade_calculator를 추천")
        return _selection("graph", "data_lab", "일반 시계열·시장 그래프이므로 지도 경로·계기면·콘솔이 있는 data_lab을 추천")

    if scene_type == "metric":
        if _has_any(text, _METRIC_RETAIL_HINTS):
            return _selection("metric", "retail_shock", "가격·판매·합계 지표이므로 계산대 내장 영수증 창이 있는 retail_shock을 추천")
        if _has_any(text, _METRIC_RISK_HINTS):
            return _selection("metric", "risk_control_room", "하락·위험·변동 지표이므로 계기판·경고 명판·콘솔이 있는 risk_control_room을 추천")
        return _selection("metric", "data_lab", "일반 시장 지표이므로 홀로그램 계기면과 콘솔이 있는 data_lab을 추천")

    if scene_type == "diagram":
        if _has_any(text, _DIAGRAM_DATA_LAB_HINTS):
            return _selection("diagram", "data_lab", "네트워크·홀로그램·추세 흐름이므로 넓은 물리 그래프 벽이 있는 data_lab을 추천")
        return _selection("diagram", "classroom", "흐름·구조 설명이므로 판서와 화살표를 담을 강의 벽이 있는 classroom을 추천")

    if scene_type == "text":
        if _has_any(text, _TEXT_PORT_HINTS):
            return _selection("text", "port_emergency", "항만·물류 핵심 문구이므로 컨테이너 물리 표면이 있는 port_emergency를 추천")
        return _selection("text", "classroom", "짧은 정의·핵심 문구는 분필 판서와 고정 메모가 있는 classroom을 추천")

    # 일반 서사에서도 항만·물류 장소 단서가 있으면 "배경 설명" 같은
    # 보조 표현보다 실제 무대를 우선한다. 그렇지 않으면 항만 서사가
    # classroom으로 잘못 매핑될 수 있다.
    if _has_any(text, _TEXT_PORT_HINTS):
        return _selection("general", "port_emergency", "항만·물류 장소 단서가 있어 캐릭터 중심의 port_emergency를 추천")

    # ── 주식·금융 특화 라우팅 ────────────────────────────────────────────
    # 폭락·패닉·리스크 → risk_control_room (시황 단서보다 위험 경보 서사를 우선)
    if _has_any(text, _STOCK_RISK_HINTS):
        return _selection(
            "general", "risk_control_room",
            "폭락·패닉·변동성 서사이므로 경보 계기판이 있는 risk_control_room을 추천",
        )
    # 대형 증시 지수 / 시황 → 방송 발표 무대 (공신력 있는 뉴스 브리핑 분위기)
    if _has_any(text, _STOCK_MARKET_BRIEFING_HINTS):
        return _selection(
            "general", "briefing_podium",
            "주가지수·증시 시황 서사이므로 방송 발표 연단인 briefing_podium을 추천",
        )
    # 기업실적·EPS·배당 → earnings_stage (Phase 3-A 완전 구현)
    if _has_any(text, _STOCK_EARNINGS_HINTS):
        return _selection(
            "general", "earnings_stage",
            "기업실적·EPS·배당 서사이므로 실적 발표 무대인 earnings_stage를 추천",
        )
    # 포트폴리오·매매전략·ETF → trade_calculator
    if _has_any(text, _STOCK_CALCULATOR_HINTS):
        return _selection(
            "general", "trade_calculator",
            "매매전략·포트폴리오·ETF 서사이므로 계산기·저울 소품이 있는 trade_calculator를 추천",
        )

    if _has_any(text, _GENERAL_CLASSROOM_HINTS):
        return _selection("general", "classroom", "일반 설명·배경 장면이므로 캐릭터 설명 연출이 자연스러운 classroom을 추천")
    return _selection("general", "briefing_podium", "수치 표면이 필요 없는 일반 서사 장면이므로 방송 발표 무대인 briefing_podium을 추천")


def validate_archetype_mapping() -> None:
    """등록된 후보가 실제 V5 archetype과 물리 표면을 모두 갖는지 확인한다."""
    for scene_type, candidates in TYPE_CANDIDATES.items():
        if scene_type not in SCENE_TYPES:
            raise ValueError(f"지원하지 않는 매핑 scene_type: {scene_type}")
        if not candidates:
            raise ValueError(f"후보가 없는 scene_type: {scene_type}")
        for archetype in candidates:
            if archetype not in ARCHETYPES:
                raise ValueError(f"ARCHETYPES에 없는 후보: {archetype}")
            if not ARCHETYPE_SURFACES.get(archetype):
                raise ValueError(f"물리 표면이 없는 후보: {archetype}")
    for archetype in ARCHETYPE_SURFACES:
        primary_surface_region(archetype)
