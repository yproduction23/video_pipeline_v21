import logging

logger = logging.getLogger(__name__)


KLING_MOTION_TEMPLATES = {
    # 공통 선두: 카메라와 기존 사실 표기를 바꾸지 않는다.
    "_style_prefix": (
        "2D cel-shaded cartoon animation. Preserve the exact source image composition, "
        "character identity, line weight, palette, and all existing scene objects. "
        "Camera is locked: no pan, zoom, dolly, roll, shake, reframing, or crop change. "
        "Animate ONLY the mascot character body (a walking step, head nod, or single arm gesture) "
        "OR one non-text physical prop (a lamp flicker, flag sway, steam drift, or coin sparkle). "
        # 2026-10-13 job 14 씬2 재현: 좌우 분할·군중이 있는 구도에서 "마스코트만
        # 움직여라"는 긍정 지시만으로는 Kling이 다른 인물(군중, 발표자, 기자단)
        # 까지 같이 움직여, 화면 대부분이 변하는 global_motion_coverage 판정을
        # 유발했다. 다른 인물을 명시적으로 고정해야 한다.
        "Every other person, official, reporter, crowd member, or background human in the frame "
        "must remain completely frozen — no gestures, no head turns, no shifting, no breathing motion. "
        "STRICTLY FORBIDDEN — DO NOT animate, vibrate, wave, distort, morph, or alter: "
        "any number, any digit, any percentage sign, any price figure, any index value, "
        "any chart line, any Korean or English text, any data board, any information panel, "
        "or any data surface. "
        "Preserve every existing written mark, number, chart shape, and factual overlay exactly; "
        "do not create, erase, translate, redraw, morph, or animate any text or number. "
    ),
    # 공통 네거티브: 숫자/글자 보존과 배경 재설계를 함께 차단한다.
    "_negative": (
        "no 3D rendering, no photorealism, no face morphing, no outfit change, "
        "no color shift, no extra characters, no warped hands, no jitter, "
        "no camera movement, no background redesign, no new screen, no new panel, "
        "no text distortion, no number distortion, no chart redraw, no changing labels, "
        "no changing factual overlay, no crowd motion, no secondary character movement, "
        "no bystander animation, no reporter or official gestures"
    ),
    "chart_shock": (  # 급락/급등 리액션
        "0–1s: the mascot sharply leans back and raises one open hand in a single surprised reaction. "
        "1–3s: the mascot takes one short backward step and settles. "
        "3–5s: one existing alert light or existing chart glow briefly brightens once, then returns to its original state; "
        "three to five small dust or sparkle particles rise and fade. "
        "Do not animate, redraw, or change any chart line, text, number, label, or overlay. Camera remains locked."
    ),
    "pointing_explain": (  # 지표/보드 설명
        "0–2s: the mascot raises the existing pointer or open hand toward the already-visible information surface. "
        "2–4s: make one gentle tap or one short presenting sweep, then stop; do not draw on the surface. "
        "4–5s: the mascot gives one confident nod while a soft existing room light subtly blooms and a few dust motes drift. "
        "Do not animate, redraw, or change any chart, text, number, label, or overlay. Camera remains locked."
    ),
    "thinking_desk": (
        "0-2s: animate ONLY the mascot's arm raising to rest its chin on one hand, and a slow head tilt; "
        "keep the body otherwise still. "
        "2-4s: one slow blink on the mascot's coin face; "
        "one existing desk lamp reflection softly pulses OR steam drifts from an existing cup OR a paper page lightly rustles. "
        "4-5s: a few ambient dust motes drift and fade through the air. "
        "NEVER animate, move, vibrate, or distort: any number, any price, any chart, any data board, "
        "any screen content, any Korean or English text, or any information panel. "
        "Camera remains completely locked."
    ),
    "walking_intro": (  # 오프닝 등장
        "0–3s: the mascot makes exactly two small in-place walking steps within its original footprint; "
        "do not approach the camera or change framing. 3–5s: the mascot stops and gives one short wave. "
        "If an existing physical prop can naturally react, make only that prop sway slightly; otherwise use only a few background particles. "
        "Do not create or change signs, text, numbers, charts, labels, or overlays. Camera remains locked."
    ),
    "celebration": (
        "0–2s: the mascot raises both arms once. 2–4s: one compact joyful hop in place, then land in the same position. "
        "4–5s: one existing warm light or already-present sparkle effect brightens gently and fades. "
        "Do not create trophies, coins, labels, numbers, charts, or text; do not alter any existing overlay. Camera remains locked."
    ),
    "ambient_context": (
        "0s: keep every person and mascot completely still. Animate only one existing practical light, "
        "one existing cable or machine glow, and a few small dust motes. 3s: the glow returns to its original state. "
        "Do not introduce a mascot, person, hand, sign, text, number, chart, or new prop. Camera remains locked."
    ),
}


def build_kling_motion_prompt(scene_or_motion_type) -> dict:
    """장면 메타데이터를 반영한 고정 카메라 프롬프트와 네거티브를 반환한다."""
    scene = scene_or_motion_type if isinstance(scene_or_motion_type, dict) else {}
    motion_type = str(scene.get("motion_type") if scene else scene_or_motion_type or "walking_intro")

    # emotion → motion_type 자동 매핑 (스크립트가 명시적 motion_type을 지정하지 않은 경우만 적용)
    # WO-2에서 도입한 감정 오버라이드(상승→happy, 하락→concern)와 연동해
    # 캐릭터 동작이 대본 맥락과 일치하도록 한다.
    _EMOTION_TO_MOTION: dict[str, str] = {
        "happy": "celebration",
        "confidence": "celebration",
        "concern": "chart_shock",
        "alarm": "chart_shock",
        "explain": "pointing_explain",
        "surprise": "chart_shock",
    }
    mapped = None
    if scene and not scene.get("motion_type"):
        emotion_value = str(scene.get("emotion_tag") or scene.get("emotion") or "").strip().lower()
        mapped = _EMOTION_TO_MOTION.get(emotion_value)
        if mapped:
            motion_type = mapped

    # 2026-10-13 사용자 재현(job 14): motion_type도 비어 있고 emotion_tag도
    # 매핑표에 없는(흔한 경우 "neutral") 장면은 상황과 무관한 범용
    # walking_intro("두 걸음 걷고 손 흔들기")로 떨어져, 단상에서 발언하는
    # 장면 같은 곳에서도 어색한 모션이 나왔다. archetype/구도 family 이름에서
    # 상황에 맞는 동작을 한 번 더 유추한다 — 정확한 매핑이 아니라 최선의
    # 추정이므로, 명시적 motion_type/emotion_tag가 있으면 항상 그게 우선한다.
    _ARCHETYPE_KEYWORD_TO_MOTION: tuple[tuple[str, str], ...] = (
        ("podium", "pointing_explain"),
        ("briefing", "pointing_explain"),
        ("classroom", "pointing_explain"),
        ("data_lab", "pointing_explain"),
        ("weather_map", "pointing_explain"),
        ("desk", "thinking_desk"),
        ("calculator", "thinking_desk"),
        ("office", "thinking_desk"),
        ("shock", "chart_shock"),
        ("emergency", "chart_shock"),
        ("risk_control", "chart_shock"),
        ("celebration", "celebration"),
        ("job_market_hall", "celebration"),
    )
    if scene and not scene.get("motion_type") and not mapped:
        archetype_value = str(
            scene.get("archetype")
            or (scene.get("art_direction") or {}).get("family")
            or ""
        ).strip().lower()
        if archetype_value:
            for keyword, template in _ARCHETYPE_KEYWORD_TO_MOTION:
                if keyword in archetype_value:
                    motion_type = template
                    break

    # 핵심 수치 데이터(core_figures/market_chart)가 있는 씬은
    # ambient_context(최소 정적 모션)로 강제 — 숫자 왜곡 방지
    # v5_verified_overlays 기반 use_kling=False 마킹(images_worker.py:869-873)과
    # 독립적인 별도 방어층. 둘 다 적용될 때는 use_kling=False가 우선(Fal 자체 미사용).
    if scene and (scene.get("core_figures") or scene.get("market_chart")):
        motion_type = "ambient_context"
    elif scene and not bool(scene.get("character_required", True)) and not scene.get("motion_type"):
        motion_type = "ambient_context"
    elif motion_type not in KLING_MOTION_TEMPLATES:
        logger.warning("Unknown motion type: %s. Defaulting to walking_intro.", motion_type)
        motion_type = "walking_intro"

    # 대본/에디터가 준 동작·감정·편집 신호를 실제 요청에 반영한다. 값이
    # 비어 있으면 템플릿만 사용하며, 숫자/사실 데이터를 새로 만들지 않는다.
    action = str(scene.get("character_action") or scene.get("action") or "").strip()
    emotion = str(scene.get("emotion_tag") or scene.get("emotion") or "").strip()
    edit = str(scene.get("edit_marker") or scene.get("edit") or "").strip()
    metadata_clause = " ".join(
        clause for clause in (
            f"Character action: {action}." if action else "",
            f"Emotion: {emotion}." if emotion else "",
            f"Edit beat: {edit}." if edit else "",
        ) if clause
    )

    style_prefix = KLING_MOTION_TEMPLATES["_style_prefix"]
    if scene and not bool(scene.get("character_required", True)):
        style_prefix = style_prefix.replace(
            "Animate ONLY the mascot character body (a walking step, head nod, or single arm gesture) "
            "OR one non-text physical prop (a lamp flicker, flag sway, steam drift, or coin sparkle). ",
            "Animate only one existing non-text physical prop (a lamp flicker, flag sway, steam drift); "
            "do not introduce a mascot or person. ",
        )

    return {
        "prompt": style_prefix + metadata_clause + " " + KLING_MOTION_TEMPLATES[motion_type],
        "negative_prompt": KLING_MOTION_TEMPLATES["_negative"],
    }
