"""기사 본문에서 찾은 실제 연도별·항목별 통계를 막대그래프 카드로 보여준다.

2026-10-14 사용자 결정: 기사 인용 카드만으로는 레퍼런스 채널 느낌이 안
난다 — "이상동기 범죄는 △2023년 46건 △2024년 42건 △2025년 39건" 같은
기사 속 실제 수치 계열은 인포그래픽/그래프로 보여줘야 한다. 숫자는
반드시 정규식으로 기사 원문에서 그대로 추출한다 — LLM이 숫자를 새로
만들거나 고치지 않는다(AGENTS.md: 수치형 데이터는 LLM 생성 금지, 수치
오버레이는 Pillow/FFmpeg로 결정론적 렌더링).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# "△2023년 46건" / "2024년: 42건" 처럼 "라벨 + 숫자 + 단위"가 반복되는
# 패턴을 찾는다. 단위는 기사에 실제로 쓰인 것을 그대로 가져온다.
_SERIES_ITEM_RE = re.compile(
    r"[△▲•]?\s*(\d{4}년|\d{1,2}월|[가-힣A-Za-z]{1,8})\s*[:]?\s*(\d[\d,]*(?:\.\d+)?)\s*(건|%|명|개|원|억|조|만)"
)
# "매년 40건 안팎 발생했다" 같은 요약 어구는 실제 계열 항목이 아니라
# 앞선 숫자들을 뭉뚱그린 재진술이다 — 라벨로 인정하지 않는다.
_NON_LABEL_WORDS = {"매년", "평균", "총", "약", "전체", "누적", "최근"}


def extract_numeric_series(text: str, *, min_items: int = 3, max_gap: int = 20) -> dict | None:
    """문장 안에서 서로 인접한 "라벨: 숫자단위"가 min_items개 이상 이어지면
    하나의 계열로 본다. 문서 전체에서 흩어진 숫자를 억지로 엮지 않는다 —
    매치 사이 간격이 max_gap자를 넘으면 계열이 끊긴 것으로 본다."""
    text = str(text or "")
    matches = [m for m in _SERIES_ITEM_RE.finditer(text) if m.group(1) not in _NON_LABEL_WORDS]
    if len(matches) < min_items:
        return None

    best: list[dict] = []
    best_matches: list[re.Match] = []
    current: list[dict] = []
    current_matches: list[re.Match] = []
    last_end: int | None = None
    for m in matches:
        if last_end is not None and m.start() - last_end > max_gap:
            if len(current) > len(best):
                best, best_matches = current, current_matches
            current, current_matches = [], []
        current.append({"label": m.group(1), "value": float(m.group(2).replace(",", "")), "unit": m.group(3)})
        current_matches.append(m)
        last_end = m.end()
    if len(current) > len(best):
        best, best_matches = current, current_matches
    if len(best) < min_items:
        return None
    # 그래프 제목은 지어내지 않고, 숫자 계열 바로 앞의 원문 문장 조각을
    # 그대로 쓴다(예: "경찰청에 따르면 이상동기 범죄는").
    lead_text = text[:best_matches[0].start()].strip().strip("△▲•").strip()
    return {"items": best, "lead_text": lead_text}


def render_bar_chart_card(
    *,
    title: str,
    items: list[dict],
    publisher: str = "",
    output_path: str,
    canvas: tuple[int, int] = (1920, 1080),
) -> bool:
    title = str(title or "").strip()
    if not title or not items:
        return False
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.font_manager import FontProperties

        font_path = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
        bold_path = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
        regular = FontProperties(fname=font_path) if Path(font_path).exists() else None
        bold = FontProperties(fname=bold_path) if Path(bold_path).exists() else regular

        width, height = canvas
        fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
        fig.patch.set_facecolor("#0A0E18")
        ax = fig.add_axes((0.08, 0.16, 0.84, 0.54))
        ax.set_facecolor("#0A0E18")

        labels = [str(it["label"]) for it in items]
        values = [float(it["value"]) for it in items]
        unit = str(items[0].get("unit") or "")
        bars = ax.bar(labels, values, color="#F7C948", width=0.5)
        top = max(values) if values else 1.0
        for rect, val in zip(bars, values):
            ax.text(
                rect.get_x() + rect.get_width() / 2, rect.get_height() + top * 0.03,
                f"{val:g}{unit}", ha="center", va="bottom", color="#FFFFFF",
                fontsize=26, fontproperties=bold,
            )
        ax.set_ylim(0, top * 1.25)
        ax.tick_params(colors="#C8D4E6", labelsize=24)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.get_yaxis().set_visible(False)
        for label in ax.get_xticklabels():
            label.set_fontproperties(bold)

        fig.text(0.08, 0.86, title, color="#FFFFFF", fontsize=40, fontproperties=bold, va="top")
        if publisher:
            fig.text(0.08, 0.07, f"출처: {publisher}", color="#8FA3BF", fontsize=22, fontproperties=regular)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, facecolor=fig.get_facecolor())
        plt.close(fig)
        return Path(output_path).exists() and Path(output_path).stat().st_size > 4_000
    except Exception:
        logger.exception("통계 차트 카드 렌더링 실패")
        return False
