"""슬라이드형 후편집의 시각 맞춤 — TTS 자막 줄(청크) 시각을 기준으로 한다.

2026-10-17 운영자 피드백: 모션그래픽이 말하는 순간보다 빨리 나온다. 기존에는 장면 길이에 글자 수 비율을 곱한 근사였다.
TTS 청크에는 줄마다 시작·끝 시각이 있으므로, 구절이 속한 줄의 시각을 쓰고 줄 안에서만 글자 비율로 보간한다.
같은 청크 정보로 "그래픽 글자가 자막 줄을 그대로 되풀이하는지"도 판단한다(운영자 피드백: 자막과 강조 텍스트가 같다)."""
from __future__ import annotations

import re

# 자막 줄의 이 비율 이상을 그래픽 글자가 덮으면 같은 말을 두 번 보여 주는 것으로 본다.
DUPLICATE_COVERAGE = 0.7


def compact(text: object) -> str:
    return re.sub(r"[\s.,!?\"'…·\-]+", "", str(text or ""))


def scene_chunks(tts_chunks: list[dict], mapping: dict | None, scene_start: float) -> list[dict]:
    """장면에 속한 자막 줄을 장면 시작 기준 상대 시각으로 돌려준다."""
    if not tts_chunks or not isinstance(mapping, dict):
        return []
    try:
        first, last = int(mapping["chunk_start_index"]), int(mapping["chunk_end_index"])
    except (KeyError, TypeError, ValueError):
        return []
    chunks: list[dict] = []
    for item in tts_chunks[first:last + 1]:
        start = float(item.get("start") or 0.0) - scene_start
        end = float(item.get("end") or (float(item.get("start") or 0.0) + float(item.get("duration") or 0.0))) - scene_start
        chunks.append({"text": str(item.get("text") or ""), "start": round(max(start, 0.0), 3), "end": round(max(end, 0.0), 3)})
    return chunks


def phrase_offset(chunks: list[dict], phrase: object) -> float | None:
    """구절을 말하기 시작하는 시각(장면 시작 기준 초). 구절이 청크 경계를 넘어도 찾고, 못 찾으면 None."""
    target = compact(phrase)
    if not target or not chunks:
        return None
    joined = ""
    spans: list[tuple[int, int, float, float]] = []  # (글자 시작, 글자 끝, 줄 시작 시각, 줄 끝 시각)
    for chunk in chunks:
        text = compact(chunk.get("text"))
        if not text:
            continue
        spans.append((len(joined), len(joined) + len(text), float(chunk["start"]), float(chunk["end"])))
        joined += text
    position = joined.find(target)
    if position < 0:
        return None
    for begin, end, start_time, end_time in spans:
        if begin <= position < end:
            fraction = (position - begin) / max(end - begin, 1)
            return round(start_time + fraction * max(end_time - start_time, 0.0), 2)
    return None


def duplicates_subtitle(text: object, chunks: list[dict]) -> bool:
    """그래픽 글자가 어느 자막 줄의 대부분을 그대로 옮긴 것이면 True. 비교할 자막이 없으면 막지 않는다."""
    shown = compact(text)
    if not shown:
        return False
    for chunk in chunks or []:
        line = compact(chunk.get("text"))
        if len(line) >= 4 and shown in line and len(shown) / len(line) >= DUPLICATE_COVERAGE:
            return True
        if len(line) >= 4 and line in shown and len(line) / len(shown) >= DUPLICATE_COVERAGE:
            return True
    return False
