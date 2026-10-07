"""V5 정보 장면의 무문자 이미지 프롬프트를 구성한다."""
from __future__ import annotations

import re

from app.utils.art_direction import SHARED_STYLE_LOCK_PROMPT, SHARED_MASCOT_STYLE_LOCK_PROMPT, ARCHETYPE_TO_COSTUME

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.v5.scene.scene_type_archetypes import ArchetypeSelection


MASCOT_STYLE_BIBLE = (
    "a friendly anthropomorphic GOLD COIN mascot character with a perfectly round golden face and embossed rim, "
    "expressive cartoon eyes and a readable scene-appropriate expression. Keep it recognizably within the same channel mascot family "
    "and ink language while allowing face expression, eye openness, brows, mouth, costume, headwear, pose, and frame size to vary. "
    "Never restyle the mascot as a flat token, "
    "a different cartoon character, a document, a card, a sheet of paper, a tablet, a phone, a monitor, a rectangle, "
    "or a different illustration medium"
)

# 참조 장면들은 캐릭터의 허용 범위를 보여 주며 한 얼굴을 복제하는 모델시트가 아니다.
MASCOT_IDENTITY_LOCK = (
    "CHARACTER CONTINUITY RANGE: use the shared round gold-coin species, rim motif, compact cartoon limbs, and channel ink style visible across the scene references. "
    "FACIAL CONTINUITY RANGE: preserve the same recognizable coin-character family without copying a single model-sheet face. Eye shape, pupil treatment, iris detail, "
    "blush, nose, brows, and mouth may simplify or intensify for shock, comedy, confidence, or distance when the expression remains deliberately drawn and coherent. "
    "Reject only a genuinely different character species, incompatible face design, or nearly featureless generic emoji. "
    "Do not copy one reference's eyelashes, neutral smile, navy suit, hat, body ratio, white cutout outline, or foreground scale into every scene. "
    "A worried, confident, comic, scientific, formal, or investigative version may look different while remaining recognizably in the same channel character family."
)


# 기존 8종 승인본의 미감을 현재 실전 경로에서도 명시적으로 고정한다. 이 계약은
# 사실값·수치의 생성 지시가 아니라 선·명암·무대 밀도에 관한 그림체 계약이다.
V5_STYLE_CONTRACT_VERSION = "2026-08-25-r8-job52-range-scene-local-v1"
BASE_STYLE_LOCK_PROMPT = SHARED_STYLE_LOCK_PROMPT

# 2026-08-04: 헤드웨어 락을 "항상 갈색 페도라"에서 "의상별 헤드웨어 1개만,
# 절대 두 개를 겹치지 않음"으로 바꿨다. 경제사냥꾼 채널은 씬의 역할(교수=학사모,
# 사회자=민머리+턱시도 등)에 맞춰 모자를 바꾸므로, 페도라를 모든 씬에 강제하면
# 그 채널 문법을 재현할 수 없다. 대신 실제 헤드웨어 지정은 COSTUME_MAP 각 항목이
# 전담하고, 여기서는 "정확히 하나만" 규칙만 고정한다. 얼굴·손·발 정체성 락
# (MASCOT_IDENTITY_LOCK)은 이 변경과 무관하게 그대로 유지된다.
MASCOT_STYLE_LOCK_PROMPT = SHARED_MASCOT_STYLE_LOCK_PROMPT

STYLE_LOCK_PROMPT = f"{BASE_STYLE_LOCK_PROMPT} {MASCOT_STYLE_LOCK_PROMPT}"

# 기존 상수 이름을 사용하는 호출부의 호환성을 유지한다.
V5_CINEMATIC_CARTOON_STYLE_CONTRACT = STYLE_LOCK_PROMPT

CHARACTER_FRAME_CONTRACT = (
    "Follow this scene's own visual hierarchy. The mascot may be left, center, right, small within a large environment, medium beside data, "
    "or dominant for a reaction beat. Full body, waist-up, or action framing is allowed when compositionally justified. "
    "Do not automatically place a giant board on the left or force the character into the right foreground."
)

# 정보형 data_lab은 7/30 벤치마크의 짙고 촘촘한 무대 미감을 기준으로 삼는다.
# 검증 수치의 위치·내용을 AI에게 맡기는 지시가 아니라, 배경의 재질과 밀도를 고정하는 계약이다.
DATA_LAB_BENCHMARK_VISUAL_TREATMENT = (
    " DATA-LAB RANGE: use the supplied approved laboratory and market-analysis scene references for line language and information density. "
    "The actual room, palette, camera, number of displays, costume, and character scale must follow this scene's semiconductor or market meaning; "
    "do not default to a broadcast studio with cameras and one curved wall."
)

EMOTION_MAP = {
    "surprise": "shocked wide-eyed expression, open mouth, sweat drop",
    "happy": "bright cheerful smile, sparkling eyes, one hand waving",
    "explain": "calm friendly explaining expression, gentle smile",
    "confidence": "smug confident half-lidded eyes, subtle smirk",
    "concern": "worried frightened face, spiral or wide eyes, sweat drops",
    "alarm": "panicked alarmed face, mouth open shouting",
}
# 07 문서(경제사냥꾼 심층분석) §1의 8종 의상 관찰을 반영한다. 기존 6종은
# 페도라를 유지하고(브랜드 일관성 우선), 신규 2종(professor의 학사모 교체 포함
# 총 3곳)은 채널 관찰 그대로 헤드웨어를 역할에 맞게 바꾼다. "no fedora" 절은
# 위 CHARACTER_FRAME_CONTRACT/STYLE_LOCK_PROMPT의 "정확히 하나만" 규칙과
# 결합해, 모델이 페도라와 새 헤드웨어를 동시에 그리는 것을 막는다.
COSTUME_MAP = {
    "analyst": "a scene-appropriate market analyst outfit selected from the channel reference range; hat, glasses, headset, or goggles are optional only when useful",
    "professor": "an academic explainer outfit suited to this scene; a mortarboard, jacket, glasses, or pointer may be used without copying them into unrelated scenes",
    "reporter": "a location-appropriate reporter or presenter outfit; formal suit, cap, weather gear, or no hat may be chosen for this scene",
    "formal": "a scene-appropriate formal outfit chosen for the event rather than a mandatory fedora-and-navy uniform",
    "safety_vest": "industrial safety clothing only when the setting genuinely involves machinery, construction, a port, or physical hazard",
    "vest": "a practical investigative or analyst outfit suited to the scene's metaphor",
    "tuxedo_host": "a formal stage-host outfit whose color and accessories fit this particular event",
    "architect_planner": "a planning or engineering outfit only when blueprints, construction, or physical design is central to the narration",
}
POSE_MAP = {
    "point_left": "holding a wooden pointer, pointing to the left toward the visual explanation",
    "present": "one arm extended presenting, open palm gesture",
    "alarmed_run": "running forward in panic, holding a red megaphone",
    "calculator_hold": "holding up a large chunky calculator-shaped prop with a shocked look",
    "think": "hand on chin in a thinking pose, holding a pen",
}


@dataclass(frozen=True)
class Archetype:
    stage: str
    props: str
    lighting: str
    visual_detail: str


ARCHETYPES = {
    "port_emergency": Archetype(
        "a stormy shipping port at night, container cranes and a cargo ship in the background, wet reflective dock",
        "stacked shipping containers, a large cargo ship, harbor cranes, red warning beacons, crashing waves",
        "dark stormy sky, lightning strike, heavy rain, red emergency light glow",
        "rain streaks, beacon glows, container color blocks, and wave silhouettes",
    ),
    "retail_shock": Archetype(
        "a bright modern grocery store checkout counter, shelves of products behind, a conveyor belt",
        "a large chunky checkout device with color buttons, a conveyor belt, densely stocked product shelves",
        "bright clean fluorescent store lighting, a red alert glow around the checkout device",
        "product silhouettes, alert-color light rings, and color-coded shelf and package blocks without glyphs; no extra monitors or digital signage",
    ),
    "classroom": Archetype(
        "a cozy university lecture classroom with a large green teaching wall and wooden desks",
        "a big teaching wall with a world map silhouette and arrows, a wooden pointer, green banker lamps, an open book",
        "warm indoor classroom light, soft glow from desk lamps, subtle sparkles",
        "chalk-like map outlines, arrows, circles, and connected geometric marks without glyphs",
    ),
    "weather_map": Archetype(
        "a TV weather studio with a large curved illuminated map wall and broadcast cameras on tripods",
        "a giant map wall, storm clouds over the map, a pointer, studio cameras, ceiling spotlights",
        "cool blue studio lighting, map-wall glow, spotlight beams",
        "storm-cloud icons, directional arrows, and colored weather bands without glyphs",
    ),
    "risk_control_room": Archetype(
        "a unified high-end market risk control room with one continuous curved operations wall and a broad central floor",
        "a cracked shipping crate beside a closed golden umbrella, analog risk gauges, color-only chart silhouettes, alert lamps, and a large physical console",
        "a continuous cool-blue ambient room light with localized red warning glow and warm protective gold highlights, without a dividing line",
        "connected circuit paths, curved signal arcs, red-to-gold light gradients, gauge needles, and abstract non-linguistic chart forms in one uninterrupted space",
    ),
    "trade_calculator": Archetype(
        "a stately trade calculation chamber centered on one large brass balance scale mounted on an engraved stone plinth",
        "one large brass balance scale, its broad engraved front plinth, and a small stack of closed ledger books",
        "dark navy chamber light with focused amber spotlight and restrained red-alert rim light",
        "engraved metal texture, balance-chain detail, and non-writing etched directional marks only on the scale setting",
    ),
    "data_lab": Archetype(
        "a narration-specific financial analysis environment: semiconductor laboratory, production-line observation room, brokerage control room, or research workspace as the content requires",
        "scene-specific analytical equipment, physical market or industry objects, and only the information surfaces needed by this narration",
        "scene-dependent laboratory, market-room, industrial, or presentation lighting derived from the intended mood",
        "layered equipment, chart forms, physical industry detail, and approved typography arranged in the supplied channel scene references' editorial-comic visual range",
    ),
    "briefing_podium": Archetype(
        "a formal policy and CEO press briefing room with one broad briefing surface embedded flush into the center back wall behind a central podium",
        "a central podium, fixed microphones, press seating, broadcast cameras, ceiling lights, flag-color ribbons without emblems, and floor cables",
        "calm neutral press-room lighting with a focused warm key light and soft blue wall illumination",
        "non-writing briefing diagrams, microphone forms, camera silhouettes, ribbon colors, and cable lines",
    ),
    "real_estate_office": Archetype(
        "a refined real-estate and interest-rate consultation office with one broad guidance board fixed flush to the wall behind the consultation desk",
        "a consultation desk, loose papers, file folders, a physical calculator, a house model, a window with exterior home silhouettes, and banker lamps",
        "warm professional office lighting with soft window light and a subtle cool reflection on the wall board",
        "non-writing house silhouettes, material-rich folders, blank calculator display, color-only guidance icons, and desk-light glow",
    ),
    "job_market_hall": Archetype(
        "a public employment consultation hall with one broad employment-trend guidance board fixed flush to the wall behind a central consultation counter",
        "a consultation counter, booth dividers, a small mechanical paper ticket dispenser with no display, closed resume folders, bags, guide lights, job seekers, and color-only career icons",
        "bright civic interior lighting with a calm welcoming glow and restrained accent colors",
        "non-writing career icons, queue-machine shapes, folder materials, divider lines, and color-only employment silhouettes",
    ),
    # earnings_stage: 기업실적·분기발표·EPS·배당 서사 전용 무대
    # briefing_podium과 달리 금융 실적 발표에 특화된 기업용 연단 + 무대 조명을 사용한다.
    "earnings_stage": Archetype(
        "a formal corporate quarterly earnings announcement stage with one broad results-presentation mural embedded flush into the center back wall behind an executive podium and desk",
        "an executive desk, a central branded podium without real logos, broadcast cameras on tripods, overhead stage spotlights, investor-seating rows, floor microphone stands, and cable bundles",
        "bright professional stage lighting with focused warm key spotlights, cool-blue fill on the results-mural surface, and subtle directional financial-indicator glow from floor-mounted strips",
        "non-writing financial chart silhouettes, color-coded bar-outline shapes, rising-or-falling directional arrows without numbers, podium trim details, and floor cable lines",
    ),
}


@dataclass
class SceneSpec:
    scene_id: str
    archetype: str
    emotion: str
    costume: str
    pose: str
    frame_occupancy: float = 0.35
    character_position: Literal["left", "center", "right"] = "right"
    character_required: bool = True
    wardrobe_description: str = ""
    action_description: str = ""
    composition_description: str = ""
    camera_description: str = ""

    def validate(self) -> None:
        for value, mapping, name in (
            (self.archetype, ARCHETYPES, "archetype"),
            (self.emotion, EMOTION_MAP, "emotion"),
            (self.costume, COSTUME_MAP, "costume"),
            (self.pose, POSE_MAP, "pose"),
        ):
            if value not in mapping:
                raise ValueError(f"지원하지 않는 {name}: {value}")
        if not 0.15 <= self.frame_occupancy <= 0.65:
            raise ValueError("frame_occupancy는 0.15~0.65만 허용")
        if self.character_position not in OCCUPANCY_PHRASE:
            raise ValueError(f"지원하지 않는 캐릭터 위치: {self.character_position}")


OCCUPANCY_PHRASE = {
    "right": "The mascot occupies only the far-right 35 percent of the frame in the foreground, full body visible from hat to shoes",
    "center": "The mascot stands centered in the foreground",
    "left": "The mascot occupies the left third of the frame in the foreground",
}


VisualTextPolicy = Literal["diegetic_decorative", "strict_textless", "script_captioned"]


PROP_SURFACE_MAP: dict[str, str] = {
    "weather_map": "large illuminated weather-map wall behind the mascot",
    "classroom": "broad central green chalkboard behind the mascot",
    "port_emergency": "broad painted front face of the single largest foreground shipping container nearest the center dock",
    "briefing_podium": "broad curved presentation wall behind the podium",
    "trade_calculator": "broad engraved front plinth directly beneath the brass balance scale's central pillar",
    "data_lab": "the storyboard-planned laboratory, production-line, control-room, or analysis surface",
    "risk_control_room": "single large central analog gauge dial face embedded at eye level in the curved operations wall",
    "retail_shock": "large checkout price board built into the supermarket counter",
    "real_estate_office": "large market guidance board behind the consultation desk",
    "job_market_hall": "large employment-trend board behind the consultation counter",
}

MANDATORY_TEXT_EMBED_SUFFIX = (
    "The text '{caption}' is PHYSICALLY PART OF THE {surface} — it looks like it was printed, painted, or written ON the physical surface, "
    "not floating in mid-air, not a separate text layer, not a sticker placed on top. "
    "It is physically part of that prop and has the same material texture and lighting as the {surface} around it."
)


def _build_prop_prompt(
    archetype: str,
    caption_en: str,
    direction: str,
    script_visuals: str = "",
    *,
    text_surface_plan: list[dict] | None = None,
) -> str:
    """승인 문구를 장면 의미보다 앞세우지 않고 물리 표면에 배치한다."""
    captions = [value.strip() for value in str(caption_en or "").splitlines() if value.strip()]
    if not captions or any(any(character.isdigit() for character in value) for value in captions):
        raise ValueError("소품 문구는 숫자 없는 승인 대본 문구여야 합니다.")
    surface = PROP_SURFACE_MAP[archetype]
    direction_detail = {
        "down": "a bold BLUE downward trend arrow and descending non-numeric bar-and-line chart",
        "up": "a bold RED upward trend arrow and rising non-numeric bar-and-line chart",
        "neutral": "a balanced amber sideways trend line with connecting concept arrows",
    }[direction]
    rendered = " | ".join(f"'{value}'" for value in captions)
    has_explicit_surface_plan = bool(text_surface_plan)
    holographic_planned = any(
        str(item.get("surface_style") or item.get("material") or "").strip().lower()
        in {"holographic", "transparent_hologram", "translucent_hologram"}
        for item in (text_surface_plan or []) if isinstance(item, dict)
    )
    if has_explicit_surface_plan:
        placement = (
            f"Follow the explicit storyboard text-surface plan: {text_surface_plan}. {surface} is only one candidate when that plan names it; "
            "multiple approved items may share a surface or occupy separate relevant props. "
        )
        hierarchy = "Use the size and hierarchy stated by that plan. "
    else:
        placement = (
            "No text-surface layout was explicitly storyboarded. If the approved wording is useful, place it once as a supporting, "
            "small-to-moderate typographic detail on an existing scene-native prop that already serves the narration. Never create a new board, "
            "floating panel, title card, or oversized display merely to hold the wording. "
        )
        hierarchy = (
            "The script-specific character action, physical objects, and their causal relationship must dominate the frame; "
            "the wording remains visually subordinate. "
        )
    material = (
        "A holographic or translucent treatment is permitted only for the plan item that explicitly requests it; all other text-bearing surfaces remain solid and set-mounted. "
        if holographic_planned else
        "Use a solid opaque scene-mounted monitor, opaque wall-mounted information board, machine gauge, or painted or engraved prop face. Never use a detached translucent glass card or floating hologram. "
    )
    return (
        f"SCENE-LOCAL TYPOGRAPHY: approved exact text items are [{rendered}]. "
        f"{placement}{hierarchy}{material}"
        f"Render every used item verbatim and integrate its typography into the prop's material, perspective, lighting, and ink style. "
        f"Use {direction_detail} where it helps the scene rather than automatically placing it below the words. "
        f"SCRIPT MEANING VISUALS: {script_visuals or 'use non-textual scene-native explanatory objects'}. "
        "Do not make the typography, one board, or one giant word the whole visual idea unless the storyboard explicitly requests that hierarchy. "
        "No numerals or other unapproved Korean or English words."
    )


# 신규 archetype의 1차 병렬 검증에서 비문자 보조 모니터가 primary 대체물로
# 산개했다. 기존 7개 규칙은 동결하고, 신규 4개에만 화면형 소품 자체를 금지한다.
SCREENLESS_PRIMARY_SURFACE_ARCHETYPES = frozenset({
    "briefing_podium", "real_estate_office", "job_market_hall",
})


# V5의 장식 문자도 프레임 위 UI가 아니라 세트의 일부여야 한다. 이 지시는
# 사실 수치를 AI에게 만들게 하는 용도가 아니며, 검증 수치는 별도 렌더러가
# 나중에 같은 물리 표면 안에서 교체한다.
DIEGETIC_TEXT_GUIDANCE = {
    "port_emergency": (
        "Paint short decorative English stencils only inside the broad flat FRONT panel of the single largest foreground shipping container "
        "nearest the center dock; keep every readable mark wholly inside that one front panel, never on its side wall, end door, roof, or any "
        "other container. Make the wet paint perspective-correct and affected by rain and red beacon light."
    ),
    "retail_shock": (
        "Put short decorative English labels and sample figures only in the built-in receipt window recessed into the upper face of the single "
        "large checkout device; make every mark inherit the device bevel, fluorescent light, and red alert reflection, never on shelf tags or product packaging."
    ),
    "classroom": (
        "Put short decorative English chalk words, arrows, and figures only in the broad central chalk-writing area of the large green "
        "teaching wall; make them dusty chalk integrated with the wall's map outline and diagram marks, never ink on a pinned note or a "
        "digital presentation overlay."
    ),
    "weather_map": (
        "Place short decorative English weather callouts and sample percentages only within the broad central geographic region "
        "of the single curved illuminated map wall; integrate them with map contours, cloud icons, and directional arrows on that same wall. "
        "Warp the lettering with the map perspective and let the cloud, rain, and studio light interact with it."
    ),
    "risk_control_room": (
        "Engrave short decorative English warning words and sample percentages into brass-and-red control plaques, analog gauge faces, and painted floor markings; "
        "make every mark part of the metal, glass, paint, glow, and room perspective."
    ),
    "trade_calculator": (
        "Integrate short decorative English labels, sample figures, and formula fragments into the balance-scale base, printed papers, and glowing wall diagrams; "
        "make them physical ink, engraving, or projected light that follows each prop's perspective."
    ),
    "data_lab": (
        "Embed short decorative English labels, sample figures, and axis ticks into holographic map paths, translucent gauge faces, and console etchings; "
        "make them obey the cyan glow, reflection, depth, and perspective of the physical set."
    ),
    "briefing_podium": (
        "Put short decorative English policy callouts and one simple diagram only inside the single broad policy-briefing surface embedded flush "
        "into the center back wall immediately behind the central podium; make them inherit the room lighting and perspective."
    ),
    "real_estate_office": (
        "Put short decorative English market callouts and sample figures only on the single broad market-and-loan guidance board fixed flush to the wall "
        "directly behind the consultation desk; make them painted or printed into that board's material and perspective."
    ),
    "job_market_hall": (
        "Put short decorative English employment callouts and one simple chart silhouette only inside the single broad employment-trend guidance board "
        "fixed flush to the wall directly behind the central consultation counter."
    ),
}


def _primary_substitute_ban(scene_type_selection: "ArchetypeSelection | None") -> str:
    """특정 무대에서 primary를 그럴듯한 다른 소품으로 바꾸는 현상을 막는다."""
    if scene_type_selection and scene_type_selection.archetype == "trade_calculator":
        return (
            " For this trade-calculator scene, do not substitute the designated plinth with a freestanding information plaque, "
            "desk display, framed dashboard, separate board, podium sign, or any other standalone panel."
        )
    if scene_type_selection and scene_type_selection.archetype == "risk_control_room":
        return (
            " For this risk-control-room scene, do not substitute the designated gauge dial face with a separate warning plaque, "
            "independent console screen, floating alert panel, desk display, framed dashboard, standalone sign, or any other panel. "
            "Every secondary gauge, warning plaque, console screen, control dial, signal lamp, floor marking, wall indicator, "
            "shipping crate, and closed umbrella must remain non-textual. "
        )
    if scene_type_selection and scene_type_selection.archetype == "weather_map":
        return (
            " For this weather-map scene, do not substitute the designated central map-wall region with a freestanding forecast board, "
            "separate weather card, side monitor, desk display, camera-mounted label, lower-third banner, or independent alert panel. "
            "Every studio camera, tripod, pointer, ceiling spotlight, studio floor device, and every map-wall area outside the designated "
            "central region, including cloud icons, directional arrows, and weather bands, must remain non-textual. "
        )
    if scene_type_selection and scene_type_selection.archetype == "classroom":
        return (
            " For this classroom scene, do not substitute the designated central chalk-writing area with a pinned paper note, framed wall "
            "poster, hanging chart, separate blackboard, desk display, open-book page, or independent teaching card. Every wooden desk, "
            "open book, wooden pointer, green banker lamp, pinned paper note, classroom door, clock, and every teaching-wall area outside "
            "the designated central chalk-writing area, including map silhouettes, arrows, circles, and connected marks, must remain non-textual. "
        )
    if scene_type_selection and scene_type_selection.archetype == "port_emergency":
        return (
            " For this port-emergency scene, the designated primary surface is ONLY the broad flat FRONT panel of the single largest foreground "
            "shipping container nearest the center dock. Never place readable marks on that container's side wall, end door, roof, corner post, "
            "or locking bar. Do not substitute the designated front panel with a dock warning plate, customs sign, cargo manifest, ship-name "
            "marking, crane label, freestanding emergency board, separate shipping card, or independent alert panel. Every other stacked shipping "
            "container, every non-front face of the designated container, cargo-ship hull, harbor-crane beam, red warning beacon, dock surface, "
            "customs sign, wave, storm cloud, lightning strike, and rain streak must remain non-textual. "
        )
    if scene_type_selection and scene_type_selection.archetype == "retail_shock":
        return (
            " For this retail-shock scene, do not substitute the designated recessed receipt window with a shelf price tag, product package, "
            "freestanding total-price card, separate POS display, calculator screen, sale placard, desk display, independent checkout panel, "
            "extra wall display, overhead promotional screen, digital signage, or secondary monitor. "
            "Every product shelf, price tag, package, conveyor-belt item, checkout button, receipt roll, secondary screen, store sign, aisle marker, "
            "and alert light must remain non-textual. "
        )
    if scene_type_selection and scene_type_selection.archetype == "briefing_podium":
        return (
            " For this briefing-podium scene, do not substitute the designated flush wall surface with a podium nameplate, teleprompter, press badge, "
            "microphone logo, flag emblem, freestanding briefing board, side screen, lower-third banner, or floating alert panel. Every podium plaque, "
            "fixed microphone, press seat, camera, ceiling light, flag-color ribbon, floor cable, and secondary screen must remain non-textual. "
        )
    if scene_type_selection and scene_type_selection.archetype == "real_estate_office":
        return (
            " For this real-estate-office scene, do not substitute the designated wall-fixed guidance board with a window listing, desk calculator display, "
            "property sign, sale card, loose paper, clipboard, desk monitor, price tag, or freestanding rate panel. The desk calculator display must remain "
            "completely blank and empty, showing no digits, symbols, labels, bars, chart marks, or pseudo-writing; it is a physical calculator, not a second "
            "information surface. Every consultation desk, loose paper, file folder, house model, window, exterior home, lamp, and desk device must remain non-textual. "
        )
    if scene_type_selection and scene_type_selection.archetype == "job_market_hall":
        return (
            " For this job-market-hall scene, do not substitute the designated wall-fixed guidance board with a job-posting sheet, company logo, queue-ticket "
            "display, resume text, kiosk screen, counter placard, vacancy card, freestanding hiring board, or separate notice cluster. Every consultation counter, "
            "booth divider, queue-ticket machine, resume folder, bag, guide light, job seeker, career icon, and secondary wall notice must remain non-textual. "
            "The queue-ticket machine must be a mechanical paper dispenser with no screen or display, and all resume folders must stay closed with no visible paper face. "
        )
    return ""


def _screenless_nonprimary_contract(scene_type_selection: "ArchetypeSelection | None") -> str:
    """신규 4개에서 primary 외 화면형 소품을 형태 자체로 금지한다."""
    if not scene_type_selection or scene_type_selection.archetype not in SCREENLESS_PRIMARY_SURFACE_ARCHETYPES:
        return ""
    return (
        " Outside the designated primary surface, do not create any monitor, screen, display, dashboard, kiosk, digital signage, "
        "teleprompter, or screen-shaped rectangular information device at all, even when blank or filled only with color blocks. "
        "Use only scene-native physical objects, lighting, material texture, abstract geometry, and non-screen silhouettes for background density. "
    )


def _third_attempt_layout_ban(scene_type_selection: "ArchetypeSelection | None") -> str:
    """P2-F가 두 번 반복된 신규 두 무대의 구체적 화면형 대체물을 막는다."""
    if not scene_type_selection:
        return ""
    if scene_type_selection.archetype == "job_market_hall":
        return (
            " Do not create any ceiling-mounted or wall-mounted monitor, and the area in front of the consultation counter must not contain any kiosk, "
            "screen-bearing terminal, or digital display of any kind. Keep that area limited to a physical counter edge, closed folders, bags, people, "
            "and a small mechanical paper ticket dispenser without any display. "
        )
    return ""


def _fact_surface_contract(
    spec: SceneSpec,
    scene_type_selection: "ArchetypeSelection | None",
    visual_text_policy: VisualTextPolicy,
    *,
    text_surface_plan: list[dict] | None = None,
) -> str:
    """검증 사실을 장면 속 물리 소품에만 배치하도록 프롬프트 계약을 만든다."""
    if scene_type_selection is None:
        return ""
    if scene_type_selection.archetype != spec.archetype:
        raise ValueError(
            "scene_type archetype 추천과 SceneSpec archetype이 일치하지 않습니다: "
            f"{scene_type_selection.archetype} != {spec.archetype}"
        )
    if scene_type_selection.scene_type == "general":
        return ""
    if not scene_type_selection.physical_surfaces or not scene_type_selection.primary_physical_surface:
        raise ValueError("정보형 씬에는 최소 한 개의 물리 정보 표면이 필요합니다.")

    # 비수치 문구만 있고 표면 계획이 없는 장면에 primary 표면 계약을 강제하면
    # 모델이 대본의 사물 관계 대신 거대한 단일 보드를 만들었다. 결정론 수치나
    # 명시된 표면 계획이 있을 때만 이 좌표/표면 계약을 활성화한다.
    has_explicit_surface_plan = bool(text_surface_plan)
    if visual_text_policy == "script_captioned" and not has_explicit_surface_plan:
        return ""

    primary_surface = scene_type_selection.primary_physical_surface
    # 표면 종류는 후보이며, 과거 재시도용 단일 표면·보조 화면 금지 규칙을
    # 모든 새 장면에 재사용하지 않는다.
    substitute_ban = ""
    screenless_nonprimary_contract = ""
    third_attempt_layout_ban = ""
    nonprimary_surface_types = "gauge, map, placard, document, and console"
    if not screenless_nonprimary_contract:
        nonprimary_surface_types = "gauge, screen, map, placard, document, and console"
    base = (
        f"IN-SCENE FACT-SURFACE CONTRACT: this is a {scene_type_selection.scene_type} scene. "
        f"{primary_surface} is a preferred information anchor, not a mandatory single-board layout. "
        "Any planned information surface must be a perspective-correct part of the illustrated set rather than a pasted overlay. "
        f"Other {nonprimary_surface_types} may carry only scene-approved exact strings; otherwise use chart shapes, needles, color blocks, and material texture without pseudo-text. "
        f"{substitute_ban}{screenless_nonprimary_contract}{third_attempt_layout_ban}"
    )
    if visual_text_policy == "strict_textless":
        # 2026-10-07 사용자 재현(job 14, 씬 0): "계약으로만" 빈 표면을 요청하면
        # Gemini가 그 표면 전체를 차트·게이지로 빽빽하게 채워, 나중에
        # add_surface_caption이 글자를 얹을 깨끗한 빈 자리를 못 찾았다.
        # 명시된 표면 계획이 있으면 그 표면의 종류(예: signboard)를 직접
        # 지목해, 장면의 나머지 부분과 달리 그 표면 하나만은 장식 없이
        # 비워 두도록 요구한다.
        planned_surface_detail = ""
        if has_explicit_surface_plan:
            plan_item = next(
                (item for item in (text_surface_plan or []) if isinstance(item, dict)), {}
            )
            surface_kind = str(plan_item.get("surface_kind") or "sign").strip() or "sign"
            surface_description = str(plan_item.get("surface_description") or "").strip()
            planned_surface_detail = (
                f" In addition to the richly detailed surfaces above, include one separate, clearly bordered, physical {surface_kind}"
                + (f" ({surface_description})" if surface_description else "")
                + " reserved for a short caption to be composited afterward. "
                "Unlike every other surface in this scene, that one object stays plain, evenly lit, and free of charts, gauges, "
                "diagrams, arrows, or decoration — a genuinely blank physical object, not a dense analytical surface. "
                "Do not substitute it with a chart, monitor, or dashboard."
            )
        return base + planned_surface_detail + (
            "Do not draw text, digits, symbols, chart labels, or factual values there. Instead render a fully finished, "
            "unlabelled physical material surface with dense non-numeric analytical detail: abstract grid lines, unlabeled rising or falling "
            "traces, pie-slice color shapes, arrows, world-map silhouettes, and decorative equation-like marks that are not readable characters. "
            "Keep enough calm contrast for later deterministic compositing. "
            "Never create a blank monitor, detached UI card, floating panel, or separate number display."
        )
    if visual_text_policy == "script_captioned":
        holographic_planned = any(
            str(item.get("surface_style") or item.get("material") or "").strip().lower()
            in {"holographic", "transparent_hologram", "translucent_hologram"}
            for item in (text_surface_plan or []) if isinstance(item, dict)
        )
        material_contract = (
            "A translucent or holographic treatment is allowed only on the exact plan item that explicitly requests it. "
            if holographic_planned else
            "Use only solid opaque scene-mounted displays, opaque wall-mounted information boards, machine gauges, or painted or engraved prop faces; never a detached translucent card or floating hologram. "
        )
        return base + (
            "Only the exact script-derived Korean or English items named in the typography section may be readable. "
            "Their number, hierarchy, and surfaces follow the scene plan; do not add other words or figures. "
            + material_contract
        )
    return base + (
        "Do not invent decorative labels, sample figures, pseudo-text, or filler ticker symbols. Keep the primary prop visually rich through "
        "material, lighting, non-linguistic diagrams, arrows, and color blocks. Never place a number or text in a floating card, detached "
        "rectangular UI, separate LCD widget, or pasted-on panel."
    )


def build_prompt(
    spec: SceneSpec, *, character_position: str | None = None, layout_instruction: str | None = None,
    visual_text_policy: VisualTextPolicy = "diegetic_decorative",
    scene_type_selection: "ArchetypeSelection | None" = None,
    semantic_direction: str = "neutral",
    semantic_caption: str = "",
    semantic_visual_brief: str = "",
    locale_visual_brief: str = "",
    text_surface_plan: list[dict] | None = None,
) -> str:
    """검증 수치는 후처리로 유지하고, 장식 표기는 선택된 물리 표면에만 요청한다."""
    spec.validate()
    if visual_text_policy not in {"diegetic_decorative", "strict_textless", "script_captioned"}:
        raise ValueError(f"Unsupported visual_text_policy: {visual_text_policy}")
    if semantic_direction not in {"down", "up", "neutral"}:
        raise ValueError(f"Unsupported semantic_direction: {semantic_direction}")
    has_explicit_surface_plan = bool(text_surface_plan)
    fact_surface_contract = _fact_surface_contract(
        spec,
        scene_type_selection,
        visual_text_policy,
        text_surface_plan=text_surface_plan,
    )
    is_general_scene = bool(scene_type_selection and scene_type_selection.scene_type == "general")
    is_selected_information_scene = bool(scene_type_selection and not is_general_scene)
    # 2026-10-06 사용자 재현(job 14, 씬 0): scene_type은 승인 문구를 화면에 쓸 수
    # 있는지를 결정하지 않는다(runtime_contract.py의 기존 주석과 동일한 원칙).
    # "general"로 분류된 대사형 장면도 승인 대본에서 뽑은 정확한 문구와 그걸
    # 적을 표면 계획이 있으면 그대로 반영해야 하며, 지금까지는 이 게이트가
    # scene_type만 보고 조용히 strict_textless로 강등시켜 그 문구가 Gemini
    # 프롬프트에 전혀 전달되지 않았다. PROP_SURFACE_MAP에 없는 archetype에서는
    # 기존 안전 동작(strict_textless)으로 폴백한다.
    has_mapped_surface_plan = has_explicit_surface_plan and spec.archetype in PROP_SURFACE_MAP
    priority_prop_instruction = ""
    if visual_text_policy == "script_captioned":
        if is_selected_information_scene or has_mapped_surface_plan:
            priority_prop_instruction = _build_prop_prompt(
                spec.archetype,
                semantic_caption,
                semantic_direction,
                semantic_visual_brief,
                text_surface_plan=text_surface_plan,
            )
        else:
            visual_text_policy = "strict_textless"
            priority_prop_instruction = ""
    archetype = ARCHETYPES[spec.archetype]
    if spec.character_required:
        wardrobe = spec.wardrobe_description or COSTUME_MAP[spec.costume]
        action = spec.action_description or POSE_MAP[spec.pose]
        character = f"{MASCOT_STYLE_BIBLE}, {MASCOT_IDENTITY_LOCK}, {EMOTION_MAP[spec.emotion]}, {wardrobe}, {action}"
        composition = (
            "Premium economic-explainer cartoon illustration. "
            f"{CHARACTER_FRAME_CONTRACT} "
            + (
                f"BINDING SCENE-SPECIFIC COMPOSITION: {spec.composition_description} "
                "This binding direction overrides generic placement examples; do not swap the specified side, scale, or object hierarchy. "
                if spec.composition_description else ""
            )
            + "Keep readable foreground, midground, and background relationships appropriate to this scene."
        )
        art_direction = V5_CINEMATIC_CARTOON_STYLE_CONTRACT
    else:
        character = (
            "NO mascot and NO anthropomorphic character in this scene. Tell the script meaning through the environment, "
            "physical objects, lighting, scale, and action only."
        )
        composition = (
            "Premium wide situational economic-explainer cartoon illustration with clear foreground, midground, and background depth. "
            "Give the environment and script-specific physical metaphor the full frame; do not reserve a character-shaped empty area."
        )
        art_direction = (
            f"{BASE_STYLE_LOCK_PROMPT} NO mascot, no gold coin person, no face on any object, and no anthropomorphic props."
        )
    scene = (
        f"Use this scene's camera direction: {spec.camera_description or 'choose the framing that best explains this scene'}. "
        f"STAGE: {archetype.stage}. KEY PROPS: {archetype.props}. "
        f"LIGHTING: {archetype.lighting}. DECORATIVE DETAIL: {archetype.visual_detail}. "
        f"LOCAL CONTEXT: {locale_visual_brief or 'use a contextually specific setting'}. "
        "Choose a scene-specific balance of character, objects, charts, text surfaces, and environment. Do not impose a fixed prop count or studio layout."
    )
    if spec.archetype == "data_lab" and is_selected_information_scene:
        art_direction += DATA_LAB_BENCHMARK_VISUAL_TREATMENT
    if visual_text_policy == "diegetic_decorative" and is_general_scene:
        background_information_density = (
            "BACKGROUND INFORMATION DENSITY: build a fully dressed narrative set, not a simple backdrop. "
            "Use scene-native objects, warning lights, weather, tools, documents, and non-quantitative visual texture. "
            "This general scene does not request numbers, chart ticks, metric displays, or factual data readouts. "
            "Do not leave an empty screen or a blank placeholder."
        )
        exclusions = (
            "Do not include unapproved readable or pseudo-readable text, a channel logo, watermark, or an accidental floating dashboard card. "
            "Use a split, board, panel, or inset only when the scene-specific storyboard calls for it. Never add a second coin mascot."
        )
    elif visual_text_policy == "diegetic_decorative" and is_selected_information_scene:
        primary_surface = scene_type_selection.primary_physical_surface
        substitute_ban = ""
        screenless_nonprimary_contract = ""
        third_attempt_layout_ban = ""
        nonprimary_surface_types = "gauges, maps, placards, documents, signs, and consoles"
        if not screenless_nonprimary_contract:
            nonprimary_surface_types = "gauges, screens, maps, placards, documents, signs, and consoles"
        background_information_density = (
            "BACKGROUND INFORMATION DENSITY: build a fully dressed explanatory set, not a simple backdrop. "
            f"Use {primary_surface} when it serves the scene, while allowing other storyboard-planned surfaces and props. "
            "Readable content must come from the scene-local approved text plan; all other detail uses charts, color, material, and objects without pseudo-text. "
            f"{screenless_nonprimary_contract}{third_attempt_layout_ban}"
            "Never leave an empty screen or a blank placeholder."
        )
        exclusions = (
            "DO NOT INCLUDE Korean text, Korean subtitles, a channel logo, watermark, or a generic floating dashboard card. "
            "Do not create a detached rectangular UI widget, free-floating data card, isolated LCD panel, presentation slide, "
            "or a separate POS-style number screen placed on top of the scene. "
            f"{substitute_ban}{screenless_nonprimary_contract}{third_attempt_layout_ban}"
            "Do not make empty monitors, empty boards, empty signboards, blank title cards, or layout-guide frames. "
            "Use split comparisons or multiple surfaces only when requested by the scene plan. Never add a second coin mascot. "
            "The scene must be complete, busy, and meaningful."
        )
    elif visual_text_policy == "diegetic_decorative":
        background_information_density = (
            "BACKGROUND INFORMATION DENSITY: build a fully dressed explanatory set, not a simple backdrop. "
            "Include populated analog gauges, signal lights, dense non-linguistic diagram lines, maps, charts, "
            "stacked sealed documents, warning-color physical props, and integrated scene detail. "
            "Do not use filler English labels, sample figures, equations, ticker symbols, or pseudo-text as decoration. "
            "Every board, gauge, map, checkout prop, and signboard must already contain dense meaningful visual detail; "
            "never leave an empty screen or a blank placeholder."
        )
        exclusions = (
            "DO NOT INCLUDE Korean text, Korean subtitles, a channel logo, watermark, or a generic floating dashboard card. "
            "Do not create a detached rectangular UI widget, free-floating data card, isolated LCD panel, presentation slide, "
            "or a separate POS-style number screen placed on top of the scene. Typography may exist only as a naturally integrated "
            "part of a map, plaque, gauge, product package, sign, container, chalk wall, or other physical stage prop. "
            "Do not make empty monitors, empty boards, empty signboards, blank title cards, or layout-guide frames. "
            "Use split comparisons, comic framing, or inset evidence only when requested by the scene plan. Never add a second coin mascot. "
            "The scene must be complete, busy, and meaningful."
        )
    elif visual_text_policy == "script_captioned":
        background_information_density = (
            "BACKGROUND INFORMATION DENSITY: build a fully dressed explanatory set, not a simple backdrop. "
            f"Use this script-meaning visual brief: {semantic_visual_brief or 'physical charts, maps, analog gauges, arrows, and color blocks'}. "
            "Prioritize the scene-local meaning and relationship; do not replace it with a generic rising or falling chart. "
            "Use physical charts, maps, analog gauges, arrows, color blocks, light signals, and layered scene-native props. "
            "The number and placement of information-bearing props follow this scene's text-surface plan."
        )
        exclusions = (
            "DO NOT INCLUDE any Korean or English text other than the exact approved caption, any numeral, factual value, channel logo, watermark, generic floating dashboard card, "
            "or an accidental detached UI widget. A split, comic panel, inset, or multiple surface layout is allowed only when the scene storyboard plans it. "
            "Never add a second coin mascot."
        )
    else:
        primary_surface = scene_type_selection.primary_physical_surface if is_selected_information_scene else "the main stage prop"
        direction_visual = {
            "down": "a clear descending non-numeric trend line and downward arrow",
            "up": "a clear rising non-numeric trend line and upward arrow",
            "neutral": "a balanced non-numeric trend line and connecting arrows",
        }[semantic_direction]
        if is_selected_information_scene:
            background_information_density = (
                "BACKGROUND INFORMATION DENSITY: build a fully dressed explanatory set, not a simple backdrop. "
                "Include color-only control dials, clusters of signal lights, dense non-linguistic connection lines, "
                "abstract bar and curve silhouettes without axes, map markers, layered physical props, and colored light shapes. "
                f"Use {primary_surface} or another storyboard-planned scene-native zone for {direction_visual} and leave only the exact planned numeric typography area calm enough for deterministic compositing. "
                "Every visual surface must contain meaningful non-linguistic detail rather than an empty placeholder."
            )
        else:
            # 2026-10-07 사용자 재현(job 14, 씬 0): 정보형이 아닌 일반 서사
            # 장면(인터뷰 발언 등)인데도 이 분기가 "차트·게이지·막대/곡선
            # 실루엣"을 요구해, 금융 데이터와 무관한 장면 배경에 막대그래프가
            # 계속 끼어들었다. 정보형이 아니면 장면에 맞는 소품/건축/조명만
            # 요구하고 차트류 도상은 아예 금지한다.
            background_information_density = (
                "BACKGROUND INFORMATION DENSITY: build a fully dressed, story-specific set, not a simple backdrop. "
                "Use only scene-native architecture, furniture, lighting, and props that belong to this narration. "
                "Do not add charts, gauges, dials, bar or curve silhouettes, data readouts, or any market/analytical iconography — "
                "this scene is not about market or financial data. "
                f"On {primary_surface} or another storyboard-planned scene-native zone, leave one calm, visually quiet area "
                "reserved for a short caption to be composited afterward. "
                "Every other surface must contain meaningful, scene-appropriate detail rather than an empty placeholder."
            )
        exclusions = (
            "DO NOT INCLUDE any visible typographic mark. The image must contain no writing-like strokes anywhere: "
            "no readable or pseudo-readable words, glyphs, numerals, captions, branding marks, or watermarks. "
            "Do not draw Roman letters such as X or x, variable symbols, equation fragments, equals signs, plus or minus signs, "
            "colons, axis labels, or any chart annotation that can be read as writing. "
            "The primary surface must remain a physical wall, board, map, gauge, container face, or console surface—never a floating UI card. "
            "A split screen, comic panel, inset, or collage is allowed only when planned by this scene's composition. "
            "The scene must be complete, busy, and meaningful."
        )
    reserve = (
        "Reserve space only for overlays explicitly listed by this scene. Otherwise use the full frame and do not create generic empty safe zones."
    )
    # 장면·구도·사물 관계를 먼저 전달한다. 텍스트 계약을 맨 앞에 두면 작은
    # 승인 문구조차 거대한 헤드라인 보드로 승격되는 경향이 있었다.
    return " ".join((
        f"<character> {character} </character>", f"<composition> {composition} </composition>",
        f"<scene> {scene} </scene>", f"<art_direction> {art_direction} </art_direction>",
        f"<background_information_density> {background_information_density} </background_information_density>",
        f"<script_meaning_visuals> {semantic_visual_brief or 'non-textual scene-native objects'} </script_meaning_visuals>",
        f"<scene_local_typography> {priority_prop_instruction} </scene_local_typography>" if priority_prop_instruction else "",
        # 2026-10-07: 비수치 승인 문구도 결정론 Pillow 합성으로 옮기면서
        # visual_text_policy가 더는 script_captioned가 아닌 장면에도
        # semantic_caption(정확 승인 문구)이 그대로 채워져 들어올 수 있게
        # 됐다. policy와 무관하게 이 태그를 넣으면 "쓰지 말라"는 strict_textless
        # 지시와 문구 자체가 같은 프롬프트에 섞여 Gemini가 그 문구를 베껴
        # 쓰려다 깨진 글자를 낼 위험이 있다 — script_captioned일 때만 넣는다.
        (
            f"<semantic_surface> {semantic_caption} follows the explicit scene text-surface plan with {semantic_direction} direction </semantic_surface>"
            if semantic_caption and has_explicit_surface_plan and visual_text_policy == "script_captioned" else ""
        ),
        f"<fact_surface_contract> {fact_surface_contract} </fact_surface_contract>" if fact_surface_contract else "",
        f"<reserve> {reserve} </reserve>",
        f"<layout_contract> {layout_instruction} </layout_contract>" if layout_instruction else "",
        f"<exclusions> {exclusions} </exclusions>",
    ))


BENCHMARK_SCENES = [
    SceneSpec("bench_01_port", "port_emergency", "alarm", "safety_vest", "alarmed_run", character_position="right"),
    SceneSpec("bench_02_retail", "retail_shock", "surprise", "analyst", "calculator_hold", character_position="left"),
    SceneSpec("bench_03_classroom", "classroom", "happy", "professor", "point_left", character_position="right"),
    SceneSpec("bench_04_classroom2", "classroom", "confidence", "professor", "point_left", character_position="left"),
    SceneSpec("bench_05_weather", "weather_map", "explain", "reporter", "present", character_position="right"),
    SceneSpec("bench_06_risk", "risk_control_room", "confidence", "formal", "present", character_position="center"),
    SceneSpec("bench_07_trade", "trade_calculator", "confidence", "vest", "think", character_position="left"),
    SceneSpec("bench_08_datalab", "data_lab", "explain", "reporter", "present", character_position="center"),
]
