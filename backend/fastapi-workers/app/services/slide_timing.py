"""슬라이드형 후편집의 시각 맞춤 — TTS 자막 줄(청크) 시각을 기준으로 한다.

2026-10-17 운영자 피드백: 모션그래픽이 말하는 순간보다 빨리 나온다. 기존에는 장면 길이에 글자 수 비율을 곱한 근사였다.
TTS 청크에는 줄마다 시작·끝 시각이 있으므로, 구절이 속한 줄의 시각을 쓰고 줄 안에서만 글자 비율로 보간한다.
같은 청크 정보로 "그래픽 글자가 자막 줄을 그대로 되풀이하는지"도 판단한다(운영자 피드백: 자막과 강조 텍스트가 같다)."""
from __future__ import annotations

import re

# 자막 줄의 이 비율 이상을 그래픽 글자가 덮으면 같은 말을 두 번 보여 주는 것으로 본다.
DUPLICATE_COVERAGE = 0.5


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
    lines = [compact(chunk.get("text")) for chunk in chunks or []]
    # 한 줄뿐 아니라 이어지는 두세 줄(자막은 한 문장을 줄 여러 개로 나눈다)을 이어 붙인 글과도 비교한다
    # (2026-10-18: "첫 번째 핵심 변수는 미국 금리"가 두 줄에 걸쳐 있어 자막 복사인데 통과했다).
    windows = [line for line in lines] + ["".join(lines[i:i + size]) for size in (2, 3) for i in range(len(lines) - size + 1)]
    for line in windows:
        if len(line) >= 4 and shown in line and len(shown) / len(line) > DUPLICATE_COVERAGE:
            return True
        if len(line) >= 4 and line in shown and len(line) / len(shown) > DUPLICATE_COVERAGE:
            return True
    return False


DEFAULT_OVERLAY_OFFSET = 0.35  # 자막 줄 시각이 없을 때 후편집이 장면 시작 뒤 나오는 시각(초)


def overlay_start_offset(chunks: list[dict], phrase: object, duration: float) -> float:
    """후편집이 장면 안에서 시작하는 시각: 그 구절을 말하기 직전(자막 줄 시각), 없으면 기본값. 계획서와 렌더가 같은 값을 쓴다."""
    spoken = phrase_offset(chunks, phrase)
    if spoken is None:
        return DEFAULT_OVERLAY_OFFSET
    return min(max(spoken - 0.15, 0.2), max(float(duration) - 1.0, 0.2))


def sentence_end_offset(narration: str, chunks: list[dict], duration: float, anchor: object) -> float:
    """구절이 속한 문장이 끝나는 시각(장면 시작 기준 초). 문장 끝은 . ? ! 이고, 자막 줄 시각이 있으면 그 문장의 마지막 줄이 끝나는 시각을 쓴다.

    2026-10-18 운영자 피드백: 이야기가 다음으로 넘어간 뒤에도 앞 그래픽이 남아 있다. 그래픽은 그 구절이 속한 문장이 끝나면(여유 조금) 물러나야 한다."""
    text = str(narration or "")
    target = str(anchor or "")
    position = text.find(target) if target else -1
    if position < 0:
        compact_text, compact_target = compact(text), compact(target)
        found = compact_text.find(compact_target) if compact_target else -1
        if found < 0:
            return float(duration)
        # 공백 없는 위치를 원문 위치로 되돌린다.
        seen = 0
        for index, char in enumerate(text):
            if compact(char):
                if seen == found:
                    position = index
                    break
                seen += 1
        if position < 0:
            return float(duration)
    end = len(text)
    for index in range(position, len(text)):
        if text[index] in ".?!":
            end = index
            break
    tail = compact(text[max(position, end - 8):end])
    if chunks and tail:
        joined, spans = "", []
        for chunk in chunks:
            piece = compact(chunk.get("text"))
            if piece:
                spans.append((len(joined), len(joined) + len(piece), float(chunk["end"])))
                joined += piece
        found = joined.rfind(tail)
        if found >= 0:
            last_char = found + len(tail) - 1
            for begin, finish, chunk_end in spans:
                if begin <= last_char < finish:
                    return round(min(chunk_end, float(duration)), 2)
    ratio = end / max(len(text), 1)
    return round(min(ratio * float(duration), float(duration)), 2)


def tts_windows(scenes: list[dict]) -> list[tuple[float, float]] | None:
    """장면별 TTS 실제 구간(시작, 끝). 조립(롱폼)이 쓰는 구간과 같다: 첫 장면은 0초에서, 그다음 장면은 앞 장면이 끝난 시각에서 시작하고
    각 장면은 자기 마지막 자막 줄이 끝나는 시각에 끝난다(대본 계약 caption_chunk_contract의 end). 계약 시각이 하나라도 없으면 None.

    2026-10-19 운영자 피드백(20번 Job): 이미지 단계는 추정 길이(합계 300초)로, 조립은 TTS 실제 구간(289.9초)으로 일해 후반 장면 71개 중 58개의
    자막 줄 시각이 0초로 깨졌고 그래픽이 말하기보다 최대 4초 일찍 떴다. 두 단계가 같은 시간축을 쓰게 한다."""
    windows: list[tuple[float, float]] = []
    previous = 0.0
    for scene in scenes:
        contract = scene.get("caption_chunk_contract")
        if not isinstance(contract, dict) or contract.get("end") is None:
            return None
        try:
            end = float(contract["end"])
        except (TypeError, ValueError):
            return None
        if end <= previous:
            return None
        windows.append((previous, end))
        previous = end
    return windows


def apply_tts_windows(scenes: list[dict]) -> bool:
    """장면의 시작·끝·길이를 TTS 실제 구간으로 덮어쓴다(추정값은 estimated_seconds_before_tts에 남긴다). 적용했으면 True."""
    windows = tts_windows(scenes)
    if not windows:
        return False
    for scene, (start, end) in zip(scenes, windows):
        scene.setdefault("estimated_seconds_before_tts", scene.get("duration") or scene.get("estimated_seconds"))
        scene["start_seconds"] = round(start, 3)
        scene["end_seconds"] = round(end, 3)
        scene["duration"] = round(end - start, 3)
    return True


# ---- 속도(2026-10-19 운영자 피드백: 싱크는 맞추되 글이 읽기 전에 사라지고 전체적으로 빠르다) ----
READ_BASE_SECONDS = 1.4          # 글 한 줄을 읽는 데 필요한 기본 시간
READ_PER_CHAR_SECONDS = 0.17     # 글자당 추가 시간(초당 약 6글자)
PART_ANIM_SECONDS = 4.0          # 연상 부품 한 장면 안의 진행 시간(이전 3.5초)
PART_PEAK_RATIO = 0.55           # 부품의 핵심 장면(받침이 눌림·선이 꺾임 등)이 오는 진행 비율 — 이 지점이 말하는 순간에 온다
PART_MIN_START_SECONDS = 0.25


def read_seconds(kind: str, content: dict | None) -> float:
    """요소 하나가 화면에 있어야 읽을 수 있는 최소 시간(초). 글자 수·항목 수로 정한다."""
    content = content or {}
    if kind == "text":
        return READ_BASE_SECONDS + READ_PER_CHAR_SECONDS * len(str(content.get("text") or ""))
    if kind == "number":
        return 2.2 + READ_PER_CHAR_SECONDS * len(str(content.get("label") or ""))
    if kind == "list":
        items = content.get("items") or []
        return 1.8 + 1.3 * len(items) + READ_PER_CHAR_SECONDS * 0.5 * sum(len(str(i)) for i in items)
    if kind == "flow":
        nodes = content.get("nodes") or []
        return 1.8 + 1.0 * len(nodes)
    if kind in ("bars", "rows"):
        items = content.get("items") or content.get("rows") or []
        return 2.4 + 0.7 * len(items)
    if kind == "streak":
        return 3.5
    return 1.2


def overlay_read_seconds(overlay: dict | None) -> float:
    """장면 단위 후편집(계획서 overlay)이 화면에 있어야 읽을 수 있는 최소 시간."""
    overlay = overlay or {}
    kind = overlay.get("type")
    if kind == "key_text":
        return read_seconds("text", {"text": overlay.get("text")})
    if kind == "big_number":
        return read_seconds("number", {"label": overlay.get("label")})
    if kind in ("bullet_list",):
        return read_seconds("list", {"items": overlay.get("items")})
    if kind == "flow":
        return read_seconds("flow", {"nodes": overlay.get("nodes")})
    if kind in ("stat_chart", "compare_numbers"):
        return read_seconds("bars", {"items": overlay.get("items") or overlay.get("rows")})
    if kind == "logo_row":
        return 1.8
    return 1.2


def part_timing(spoken: float | None, duration: float, *, total: float | None = None) -> tuple[float, float]:
    """연상 부품의 (시작 시각, 진행 시간).

    핵심 장면(진행의 PART_PEAK_RATIO 지점)이 그 구절을 말하는 순간에 오도록 먼저 천천히 시작한다 — 말하는 순간에야 시작하면 앞에서 빈 화면이 길었다.
    total은 여러 장면에 걸친 부품의 전체 시간이다(첫 장면 기준 시작만 정한다)."""
    available = float(total if total is not None else duration)
    anim = max(min(available - PART_MIN_START_SECONDS - 0.5, PART_ANIM_SECONDS if total is None else max(total - 1.0, PART_ANIM_SECONDS)), 0.8)
    if spoken is None:
        return round(min(0.35, max(duration - 1.0, PART_MIN_START_SECONDS)), 2), round(anim, 2)
    start = min(max(spoken - PART_PEAK_RATIO * anim, PART_MIN_START_SECONDS), max(duration - 1.0, PART_MIN_START_SECONDS))
    return round(start, 2), round(anim, 2)
