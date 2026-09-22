"""Style/TTS metadata derived from approved narration without rewriting facts.

This module is deliberately post-generation: it does not modify wording,
numbers, dates, entities, or source facts.  It only supplies production cues
used by the image editor, TTS tag mapper, and Gate 3 review UI.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.utils.sentence_splitter import split_sentences
from app.utils.caption_segmentation import split_script_into_caption_chunks
from app.utils.narration_contract import normalize_scene_sentence_boundaries, text_sha256

PHASES = (
    ("hook", "Hook & Question", 0.08),
    ("context", "Context & Escalation", 0.55),
    ("twist", "Twist & Insight", 0.85),
    ("resolution", "Resolution & CTA", 1.00),
)
EMOTIONS = ("neutral", "highlight", "surprised", "worried", "happy")
FORBIDDEN_PATTERNS = ("무조건", "확실합니다", "무조건 수익", "지금 사야", "반드시 오릅니다")


def default_style_mix(category: str, is_shorts: bool = False) -> dict[str, float]:
    """Return the channel's delivery ratio for every supported content category.

    The ratio controls presentation only (storytelling rhythm versus explanatory
    delivery).  It must never change the verified facts, figures, or source
    context supplied to the script worker.
    """
    category = (category or "CUSTOM").upper()
    longform_mix = {
        "KOSPI": (0.70, 0.30),
        "KOSDAQ": (0.75, 0.25),
        "US_STOCKS": (0.60, 0.40),
        "INDIVIDUAL_STOCK": (0.65, 0.35),
        "ASSOCIATED_STOCKS": (0.65, 0.35),
        "GLOBAL_MACRO": (0.50, 0.50),
        "CRYPTO": (0.65, 0.35),
        "CUSTOM": (0.60, 0.40),
    }
    shorts_mix = {
        "KOSPI": (0.80, 0.20),
        "KOSDAQ": (0.80, 0.20),
        "US_STOCKS": (0.70, 0.30),
        "INDIVIDUAL_STOCK": (0.75, 0.25),
        "ASSOCIATED_STOCKS": (0.75, 0.25),
        "GLOBAL_MACRO": (0.60, 0.40),
        "CRYPTO": (0.75, 0.25),
        "CUSTOM": (0.70, 0.30),
    }
    storytelling, knowledge = (shorts_mix if is_shorts else longform_mix).get(
        category,
        (shorts_mix if is_shorts else longform_mix)["CUSTOM"],
    )
    return {"economic_hunter": storytelling, "knowledge_bite": knowledge}


def _phase(index: int, total: int) -> tuple[str, str]:
    position = (index + 1) / max(total, 1)
    for key, label, boundary in PHASES:
        if position <= boundary:
            return key, label
    return PHASES[-1][0], PHASES[-1][1]


def _emotion(text: str, phase: str, pose: str) -> str:
    lower = text.lower()
    if any(token in lower for token in ("급락", "하락", "위험", "불확실", "우려", "리스크")):
        return "worried"
    if any(token in lower for token in ("반전", "그런데", "뜻밖", "의외", "왜")):
        return "surprised"
    if any(token in lower for token in ("상승", "개선", "기회", "회복")):
        return "happy"
    if any(char.isdigit() for char in text) or pose in {"pointing", "explaining"}:
        return "highlight"
    return "surprised" if phase == "hook" else "neutral"


def _action(pose: str, emotion: str) -> str:
    if pose == "pointing" or emotion == "highlight":
        return "pointer_up"
    if pose in {"thinking", "worried"} or emotion == "worried":
        return "arms_crossed"
    if pose in {"explaining", "surprised"} or emotion == "surprised":
        return "hands_open"
    return "neutral"


def _edit_marker(text: str, emotion: str) -> str:
    if any(char.isdigit() for char in text):
        return "data_overlay"
    if emotion in {"surprised", "highlight"}:
        return "emphasis_zoom"
    return "scene_change"


def pace_sections_for_runtime(
    sections: list[dict[str, Any]], target_seconds: int, *, min_seconds: float = 4.5,
    target_seconds_per_scene: float = 5.5, max_seconds: float = 6.5,
    subtitle_max_chars: int = 18,
) -> list[dict[str, Any]]:
    """짧은 대사 조각을 5~6초 체류 장면으로 다시 묶는다.

    이 단계는 대본의 단어·문장 순서를 바꾸지 않는다. TTS와 ASS가 소비할
    원문은 유지한 채, 같은 흐름의 짧은 문장만 하나의 화면에 묶는다. 따라서
    5분 영상이 9장 정지화면으로 늘어지는 일과 2~3초마다 무의미하게 화면이
    바뀌는 일을 모두 막는다.
    """
    if not sections or target_seconds <= 0:
        return [dict(section) for section in sections]
    # LLM이 장면 수를 맞추며 문장 중간에서 블록을 나눈 경우,
    # 시간 버킷을 계산하기 전에 완결문을 복원한다. 발화 원문은 불변이다.
    sections, _ = normalize_scene_sentence_boundaries(sections)
    visible = [len(re.sub(r"\s+", "", str(item.get("content") or item.get("text") or ""))) for item in sections]
    total_chars = sum(visible)
    if total_chars <= 0:
        return [dict(section) for section in sections]
    chars_per_second = total_chars / float(target_seconds)
    minimum = max(1, round(chars_per_second * min_seconds))
    target = max(minimum, round(chars_per_second * target_seconds_per_scene))
    maximum = max(target, round(chars_per_second * max_seconds))

    fragments: list[dict[str, Any]] = []
    for source in sections:
        raw_text = str(source.get("content") or source.get("text") or "").strip()
        sentences = split_sentences(raw_text)
        # 대본 작가가 두 문장을 한 블록에 남긴 경우에만 문장 경계에서 푼다.
        # 절대 글자 수로 잘라 TTS와 자막 원문을 훼손하지 않는다.
        if len(sentences) > 1 and len(re.sub(r"\s+", "", raw_text)) > maximum:
            for sentence in sentences:
                item = dict(source)
                item["content"] = sentence
                item["text"] = sentence
                item["text_for_tts"] = sentence
                fragments.append(item)
        else:
            fragments.append(dict(source))

    paced: list[dict[str, Any]] = []
    bucket: list[dict[str, Any]] = []
    bucket_chars = 0

    def flush() -> None:
        nonlocal bucket, bucket_chars
        if not bucket:
            return
        first = dict(bucket[0])
        texts = [str(item.get("content") or item.get("text") or "").strip() for item in bucket]
        merged = " ".join(text for text in texts if text)
        first["content"] = merged
        first["text"] = merged
        first["text_for_tts"] = merged
        # bucket[0]은 흔히 원래 씬 하나가 여러 조각으로 쪼개진 첫 조각이거나, 두 씬의
        # 경계에 걸친 조각이다. bucket[0]의 screen_text_validation·scene_rejected를
        # 그대로 물려주면, 그 씬 하나에 있던 판정이 이 씬에서 갈라져 나온 모든 화면에
        # 복제된다(예: 화면 문구 JSON이 깨진 씬 하나가 재분할 후 8~10개 장면 전부를
        # "거부"로 만들어 수동 검토를 강제한 사례). 여기서는 조각들의 screen_texts만
        # 합치고, 실제 판정(merged 문구가 이 화면 한정 텍스트에 그대로 있는지)은
        # script_worker.py가 재분할 이후 다시 계산한다.
        merged_screen_texts: list[str] = []
        for item in bucket:
            for value in item.get("screen_texts") or []:
                text_value = str(value or "").strip()
                if text_value and text_value not in merged_screen_texts:
                    merged_screen_texts.append(text_value)
        first["screen_texts"] = merged_screen_texts[:3]
        first["bubble_text"] = next(
            (str(item.get("bubble_text") or "").strip() for item in bucket if str(item.get("bubble_text") or "").strip()),
            "",
        )
        first["screen_text_validation"] = None  # script_worker.py가 재계산할 때까지의 표시값
        first["bubble_validation"] = None
        first["scene_rejected"] = False
        planned_caption_chunks = split_script_into_caption_chunks(
            merged,
            max_chars=subtitle_max_chars,
        )
        first["caption_chunk_plan"] = {
            "version": "caption-plan-v1",
            "subtitle_max_chars": subtitle_max_chars,
            "scene_text_sha256": text_sha256(merged),
            "chunks": [
                {
                    "local_index": chunk_index,
                    "text": chunk_text,
                    "text_sha256": text_sha256(chunk_text),
                }
                for chunk_index, chunk_text in enumerate(planned_caption_chunks)
            ],
        }
        first["source_section_indexes"] = [
            index for item in bucket for index in (item.get("source_section_indexes") or [item.get("scene_id")])
        ]
        first["runtime_pacing"] = {
            "target_seconds_per_scene": target_seconds_per_scene,
            "source_fragment_count": len(bucket),
            "visible_char_count": bucket_chars,
            "caption_chunk_count": len(planned_caption_chunks),
        }
        paced.append(first)
        bucket = []
        bucket_chars = 0

    for source in fragments:
        char_count = len(re.sub(r"\s+", "", str(source.get("content") or source.get("text") or "")))
        candidate_count = bucket_chars + char_count
        # 이미 최소 읽기 길이에 도달한 경우만 다음 조각으로 넘긴다. 단일 문장이
        # 상한보다 길면 문장 중간을 자르지 않고 독립 장면으로 보존한다.
        if bucket and candidate_count > maximum and bucket_chars >= minimum:
            flush()
        bucket.append(dict(source))
        bucket_chars += char_count
        if bucket_chars >= target:
            flush()
    flush()
    for idx, scene in enumerate(paced, start=1):
        scene["scene_id"] = f"script_scene_{idx:03d}"
        scene["title"] = f"장면 {idx:03d}"
    return paced


def annotate_sections(sections: list[dict[str, Any]], target_seconds: int) -> list[dict[str, Any]]:
    """Add only metadata; existing section content stays byte-for-byte intact."""
    total_chars = sum(len(re.sub(r"\s+", "", str(item.get("content") or item.get("text") or ""))) for item in sections) or 1
    elapsed = 0.0
    output: list[dict[str, Any]] = []
    for index, source in enumerate(sections):
        item = dict(source)
        text = str(item.get("content") or item.get("text") or "").strip()
        phase, phase_name = _phase(index, len(sections))
        seconds = max(0.5, target_seconds * len(re.sub(r"\s+", "", text)) / total_chars)
        emotion = _emotion(text, phase, str(item.get("pose") or ""))
        item.update({
            "phase": phase, "phase_name": phase_name,
            "start_seconds": round(elapsed, 2), "end_seconds": round(elapsed + seconds, 2),
            "emotion_tag": emotion, "character_action": _action(str(item.get("pose") or ""), emotion),
            "edit_marker": _edit_marker(text, emotion), "text_for_tts": text,
        })
        item["sentences"] = [{
            "sentence_id": f"s{index + 1:03d}_{sentence_index + 1}", "text": sentence,
            "text_for_tts": sentence, "emotion_tag": _emotion(sentence, phase, str(item.get("pose") or "")),
            "character_action": item["character_action"], "edit_marker": _edit_marker(sentence, emotion),
            "estimated_seconds": round(seconds * len(sentence) / max(len(text), 1), 2),
            # References are attached only when a fact-bundle ID exists; we
            # intentionally never invent a citation merely because text has a number.
            "fact_refs": [], "market_data_refs": [],
        } for sentence_index, sentence in enumerate(split_sentences(text))]
        elapsed += seconds
        output.append(item)
    return output


def validate_delivery(sections: list[dict[str, Any]]) -> dict[str, Any]:
    violations: list[dict[str, str]] = []
    tags: list[str] = []
    for scene in sections:
        text = str(scene.get("content") or scene.get("text") or "")
        for forbidden in FORBIDDEN_PATTERNS:
            if forbidden in text:
                violations.append({"type": "FORBIDDEN_PHRASE", "scene": str(scene.get("title", "")), "value": forbidden})
        tags.append(str(scene.get("emotion_tag") or "neutral"))
        for sentence in scene.get("sentences") or []:
            if re.search(r"\d+(?:\.\d+)?%", str(sentence.get("text_for_tts") or "")):
                violations.append({"type": "RAW_PERCENT_FOR_TTS", "scene": str(scene.get("title", "")), "value": sentence["text_for_tts"]})
    max_run = 0
    current = 0
    previous = None
    for tag in tags:
        current = current + 1 if tag == previous else 1
        previous = tag
        max_run = max(max_run, current)
    if max_run >= 5:
        violations.append({"type": "LOW_EMOTION_DIVERSITY", "scene": "sequence", "value": str(max_run)})
    phase_counts = Counter(str(scene.get("phase") or "") for scene in sections)
    return {"passed": not violations, "violations": violations, "phase_counts": dict(phase_counts), "emotion_counts": dict(Counter(tags))}
