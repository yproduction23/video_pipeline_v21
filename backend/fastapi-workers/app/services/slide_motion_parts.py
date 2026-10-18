"""움직임 부품 — 대본 행간이 연상되도록 소재가 움직이는 모션그래픽(이동·게이지·저울·확산·받침·쌓임).

2026-10-17 운영자 요청: 자막을 그대로 쓰거나 엉뚱한 아이콘을 붙이는 대신, 말의 뜻이 그림으로 연상돼야 한다.
연출 단계(slide_concepts)가 장면의 뜻을 읽고 부품과 인자(라벨·방향)를 고르면, 이 파일이 인자를 검증하고(글자는 내레이션 원문에서
그대로, 정도는 열거값으로만 — 수치를 만들어 내지 않는다) 진행도 p(0~1)에 따라 결정론적으로 그린다.

모든 부품은 투명 배경 RGBA 스프라이트(가로 1500·세로 700 이하, 하단 자막 영역을 침범하지 않는 크기)를 돌려준다.
p=0에서는 아무것도 보이지 않고, 진행하면서 그림이 채워진다."""
from __future__ import annotations

import logging
import math
import re
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from app.services.slide_motifs import motif_sprite, resolve_motif

logger = logging.getLogger(__name__)

PARTS = ("move", "gauge", "balance", "spread", "support", "pile")
MAX_LABEL_CHARS = 12
GAUGE_LEVELS = ("high", "low", "rising", "falling")
SUPPORT_STATES = ("holding", "weakening")
PILE_TRENDS = ("grow", "shrink")
CANVAS_MAX = (1500, 700)

_FONT_BOLD = "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"
NAVY = (27, 34, 48, 255)
YELLOW = (255, 201, 51, 255)
PAPER = (244, 245, 248, 255)
LIGHT = (190, 194, 202, 255)
MID = (107, 116, 128, 255)
RED = (229, 57, 53, 255)
WHITE = (255, 255, 255, 255)
SS = 2  # 슈퍼샘플링 배율(가장자리를 부드럽게)


# ---------------------------------------------------------------- 인자 검증 ----

def _compact(text: object) -> str:
    return re.sub(r"[\s.,!?\"'…·\-]+", "", str(text or ""))


def _label(value: object, narration: str) -> Optional[str]:
    """내레이션에 글자 그대로 있는 짧은 라벨만 통과시킨다."""
    text = str(value or "").strip()
    if 2 <= len(text) <= MAX_LABEL_CHARS and _compact(text) in _compact(narration):
        return text
    return None


def validate_part(part: str, params: object, narration: str, context: str = "") -> Optional[dict]:
    """부품 인자를 검증해 정리된 인자를 돌려준다(어긋나면 None). 라벨은 내레이션(과 직전 장면) 원문 그대로여야 한다."""
    if part not in PARTS or not isinstance(params, dict):
        return None
    ground = f"{context} {narration}".strip()
    label = lambda key: _label(params.get(key), ground)  # noqa: E731

    if part == "move":
        item = resolve_motif(params.get("item"))
        if not item:
            return None
        out = {"item": item}
        for key in ("source_label", "target_label"):
            text = label(key)
            if text:
                out[key] = text
        return out
    if part == "gauge":
        text, level = label("label"), str(params.get("level") or "")
        if not text or level not in GAUGE_LEVELS:
            return None
        out = {"label": text, "level": level}
        value = str(params.get("value") or "").strip()
        if value and re.search(r"\d", value) and _compact(value) in _compact(ground):
            out["value"] = value
        return out
    if part == "balance":
        left, right, heavier = label("left"), label("right"), str(params.get("heavier") or "")
        if not left or not right or heavier not in ("left", "right"):
            return None
        return {"left": left, "right": right, "heavier": heavier}
    if part == "spread":
        origin, effect = label("origin"), label("effect")
        return {"origin": origin, "effect": effect} if origin and effect else None
    if part == "support":
        top, pillar, state = label("top"), label("pillar"), str(params.get("state") or "")
        if not top or not pillar or state not in SUPPORT_STATES:
            return None
        return {"top": top, "pillar": pillar, "state": state}
    if part == "pile":
        item, trend = resolve_motif(params.get("item")), str(params.get("trend") or "")
        if not item or trend not in PILE_TRENDS:
            return None
        out = {"item": item, "trend": trend}
        text = label("label")
        if text:
            out["label"] = text
        return out
    return None


def part_phrase(part: str, params: dict) -> str:
    """등장 시각을 정할 대표 구절(그 부품이 처음 가리키는 말)."""
    for key in ("source_label", "origin", "left", "top", "label", "target_label"):
        if params.get(key):
            return str(params[key])
    return ""


# ---------------------------------------------------------------- 그리기 도구 ----

def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def _ramp(p: float, start: float, length: float) -> float:
    return min(max((p - start) / length, 0.0), 1.0)


def _font(size: int):
    try:
        return ImageFont.truetype(_FONT_BOLD, size * SS)
    except OSError:
        return ImageFont.load_default()


def _layer(size: tuple[int, int]) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image)


def _scaled(box):
    """좌표(사각형 4값, 선 4값, 점 목록)를 슈퍼샘플링 배율로 키운다."""
    if box and isinstance(box[0], (tuple, list)):
        return [(x * SS, y * SS) for x, y in box]
    return tuple(int(v * SS) for v in box)


def _text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, size: int, fill, anchor: str = "mm") -> None:
    draw.text((xy[0] * SS, xy[1] * SS), text, font=_font(size), fill=fill, anchor=anchor)


def _with_alpha(layer: Image.Image, factor: float) -> Image.Image:
    if factor >= 1.0:
        return layer
    out = layer.copy()
    out.putalpha(out.getchannel("A").point(lambda v, f=max(factor, 0.0): int(v * f)))
    return out


def _finish(layers: list[Image.Image], size: tuple[int, int]) -> Image.Image:
    base = Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
    for layer in layers:
        base.alpha_composite(layer)
    return base.resize(size, Image.LANCZOS)


def _label_chip(text: str, size: int = 46, fill=PAPER, color=NAVY) -> Image.Image:
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    font = _font(size)
    width = int(probe.textlength(text, font=font)) + 56 * SS
    height = int(size * 1.7) * SS
    chip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(chip)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=height // 2, fill=fill, outline=NAVY, width=4 * SS)
    draw.text((width // 2, height // 2), text, font=font, fill=color, anchor="mm")
    return chip


def _paste_center(layer: Image.Image, sprite: Image.Image, cx: float, cy: float) -> None:
    layer.alpha_composite(sprite, (int(cx * SS - sprite.width / 2), int(cy * SS - sprite.height / 2)))


def _motif(label: str, height: int) -> Optional[Image.Image]:
    art = motif_sprite(label, height=height * SS)
    return art


# ---------------------------------------------------------------- 부품별 그림 ----

def _move(params: dict, p: float) -> Image.Image:
    size = (1500, 560)
    fixed, fixed_draw = _layer(size)
    boxes = ((260, 300), (1240, 300))
    for (cx, cy), key in zip(boxes, ("source_label", "target_label")):
        fixed_draw.rounded_rectangle(_scaled((cx - 150, cy - 125, cx + 150, cy + 125)), radius=36 * SS, fill=PAPER, outline=NAVY, width=8 * SS)
        fixed_draw.rectangle(_scaled((cx - 150, cy - 125, cx + 150, cy - 85)), fill=YELLOW)
        if params.get(key):
            _text(fixed_draw, (cx, cy + 185), params[key], 54, WHITE)
    arrow, arrow_draw = _layer(size)
    reach = _ease(_ramp(p, 0.15, 0.7))
    x0, x1 = 430, 430 + (1070 - 430) * reach
    for x in range(int(x0), int(x1), 60):
        arrow_draw.line(_scaled((x, 300, min(x + 34, x1), 300)), fill=YELLOW, width=14 * SS)
    if reach > 0.98:
        arrow_draw.polygon(_scaled([(1070, 262), (1118, 300), (1070, 338)]), fill=YELLOW)
    item, travel = Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0)), _ease(_ramp(p, 0.15, 0.7))
    art = _motif(params["item"], 170)
    if art is not None and p > 0.15:
        cx = boxes[0][0] + (boxes[1][0] - boxes[0][0]) * travel
        cy = boxes[0][1] - 10 - 70 * math.sin(math.pi * travel)
        _paste_center(item, art, cx, cy)
    return _finish([_with_alpha(fixed, _ramp(p, 0.0, 0.15)), _with_alpha(arrow, 1.0), item], size)


def _gauge(params: dict, p: float) -> Image.Image:
    size = (1100, 700)
    shell, d = _layer(size)
    tube = (230, 40, 360, 540)
    d.rounded_rectangle(_scaled(tube), radius=65 * SS, fill=PAPER, outline=NAVY, width=8 * SS)
    d.ellipse(_scaled((170, 500, 420, 690)), fill=PAPER, outline=NAVY, width=8 * SS)
    for k in range(6):
        y = 90 + k * 78
        d.line(_scaled((380, y, 430, y)), fill=LIGHT, width=6 * SS)
    start, end = {"high": (0.3, 0.9), "low": (0.7, 0.15), "rising": (0.15, 0.85), "falling": (0.85, 0.15)}[params["level"]]
    level = start + (end - start) * _ease(_ramp(p, 0.15, 0.75))
    liquid, ld = _layer(size)
    top = 520 - level * 440
    ld.rounded_rectangle(_scaled((248, top, 342, 560)), radius=40 * SS, fill=YELLOW)
    ld.ellipse(_scaled((186, 516, 404, 674)), fill=YELLOW)
    if level > 0.78:
        ld.rounded_rectangle(_scaled((248, top, 342, top + 46)), radius=30 * SS, fill=RED)
    text, td = _layer(size)
    _text(td, (700, 250), params["label"], 96, WHITE)
    if params.get("value"):
        _text(td, (700, 370), params["value"], 110, YELLOW)
    return _finish([_with_alpha(shell, _ramp(p, 0.0, 0.15)), _with_alpha(liquid, _ramp(p, 0.05, 0.15)), _with_alpha(text, _ramp(p, 0.1, 0.2))], size)


def _balance(params: dict, p: float) -> Image.Image:
    size = (1500, 700)
    frame, d = _layer(size)
    pivot = (750, 190)
    d.polygon(_scaled([(690, 640), (810, 640), (790, 560), (710, 560)]), fill=MID, outline=NAVY)
    d.rectangle(_scaled((728, 190, 772, 580)), fill=PAPER, outline=NAVY, width=6 * SS)
    d.ellipse(_scaled((pivot[0] - 38, pivot[1] - 38, pivot[0] + 38, pivot[1] + 38)), fill=YELLOW, outline=NAVY, width=6 * SS)
    tilt = math.radians(13) * _ease(_ramp(p, 0.15, 0.7)) * (1 if params["heavier"] == "right" else -1)
    half = 520
    ends = [(pivot[0] + sign * half * math.cos(tilt), pivot[1] + sign * half * math.sin(tilt)) for sign in (-1, 1)]
    arms, ad = _layer(size)
    ad.line(_scaled((ends[0][0], ends[0][1], ends[1][0], ends[1][1])), fill=NAVY, width=34 * SS)
    ad.line(_scaled((ends[0][0], ends[0][1], ends[1][0], ends[1][1])), fill=YELLOW, width=22 * SS)
    pans, pd = _layer(size)
    for (ex, ey), key in zip(ends, ("left", "right")):
        pan_y = ey + 250
        pd.line(_scaled((ex, ey, ex - 130, pan_y)), fill=NAVY, width=7 * SS)
        pd.line(_scaled((ex, ey, ex + 130, pan_y)), fill=NAVY, width=7 * SS)
        pd.rounded_rectangle(_scaled((ex - 190, pan_y, ex + 190, pan_y + 36)), radius=18 * SS, fill=YELLOW, outline=NAVY, width=6 * SS)
        chip = _label_chip(params[key], 44)
        _paste_center(pans, chip, ex, pan_y - 52)
    return _finish([_with_alpha(frame, _ramp(p, 0.0, 0.15)), _with_alpha(arms, _ramp(p, 0.0, 0.15)), _with_alpha(pans, _ramp(p, 0.05, 0.2))], size)


def _spread(params: dict, p: float) -> Image.Image:
    size = (1500, 560)
    tiles, d = _layer(size)
    count, base_y = 7, 430
    for i in range(count):
        local = _ease(_ramp(p * 1.25 - i * 0.11, 0.0, 0.3))
        angle = math.radians(78) * local
        pivot = (130 + i * 170 + 40, base_y)
        corners = [(-80, 0), (0, 0), (0, -220), (-80, -220)]   # 오른쪽 아래 모서리를 축으로 쓰러진다
        points = [(pivot[0] + x * math.cos(angle) - y * math.sin(angle), pivot[1] + x * math.sin(angle) + y * math.cos(angle)) for x, y in corners]
        d.polygon(_scaled(points), fill=YELLOW if i == 0 else PAPER, outline=NAVY, width=6 * SS)
    ground, gd = _layer(size)
    gd.line(_scaled((90, base_y + 8, 1410, base_y + 8)), fill=MID, width=8 * SS)
    labels, ld = _layer(size)
    _text(ld, (240, 90), params["origin"], 54, WHITE)
    effect = Image.new("RGBA", labels.size, (0, 0, 0, 0))
    _text(ImageDraw.Draw(effect), (1260, 90), params["effect"], 54, WHITE)
    return _finish([_with_alpha(ground, _ramp(p, 0.0, 0.1)), _with_alpha(tiles, _ramp(p, 0.0, 0.1)),
                    _with_alpha(labels, _ramp(p, 0.05, 0.15)), _with_alpha(effect, _ramp(p, 0.85, 0.15))], size)


def _support(params: dict, p: float) -> Image.Image:
    size = (1200, 700)
    ground_y = 620
    weak = params["state"] == "weakening"
    grow = _ease(_ramp(p, 0.1, 0.7))
    height = 280 * (1 - 0.68 * grow) if weak else 280 * grow
    block_bottom = ground_y - height
    scene, d = _layer(size)
    d.line(_scaled((120, ground_y + 6, 1080, ground_y + 6)), fill=MID, width=8 * SS)
    if height > 4:
        d.rectangle(_scaled((560, block_bottom, 640, ground_y)), fill=PAPER, outline=NAVY, width=6 * SS)
        d.rectangle(_scaled((530, block_bottom, 670, block_bottom + 26)), fill=PAPER, outline=NAVY, width=6 * SS)
        d.rectangle(_scaled((530, ground_y - 22, 670, ground_y)), fill=PAPER, outline=NAVY, width=6 * SS)
    block_top = block_bottom - 170
    d.rounded_rectangle(_scaled((250, block_top, 950, block_bottom)), radius=26 * SS, fill=YELLOW, outline=NAVY, width=8 * SS)
    _text(d, (600, (block_top + block_bottom) / 2), params["top"], 66, NAVY)
    _text(d, (600, ground_y + 56), params["pillar"], 50, WHITE)
    arrow, ad = _layer(size)
    if weak:
        reach = _ease(_ramp(p, 0.3, 0.5))
        ad.rectangle(_scaled((572, block_top - 40 - 150 * reach, 628, block_top - 40)), fill=RED)
        ad.polygon(_scaled([(540, block_top - 40), (660, block_top - 40), (600, block_top + 6)]), fill=RED)
    return _finish([_with_alpha(scene, _ramp(p, 0.0, 0.12)), _with_alpha(arrow, 1.0 if weak else 0.0)], size)


def _pile(params: dict, p: float) -> Image.Image:
    size = (1000, 700)
    layer, d = _layer(size)
    total = 6
    eased = _ease(_ramp(p, 0.05, 0.85))
    shown = math.ceil(total * eased) if params["trend"] == "grow" else total - int(5 * eased)
    art = _motif(params["item"], 150)
    for k in range(shown):
        cx, cy = 500 + (14 if k % 2 else -14), 510 - k * 92
        if art is not None:
            _paste_center(layer, art, cx, cy)
    labels, ld = _layer(size)
    if params.get("label"):
        _text(ld, (500, 640), params["label"], 58, WHITE)
    return _finish([layer, _with_alpha(labels, _ramp(p, 0.1, 0.2) if shown else 0.0)], size)


_DRAW = {"move": _move, "gauge": _gauge, "balance": _balance, "spread": _spread, "support": _support, "pile": _pile}


def part_sprite(part: str, params: dict, p: float) -> Image.Image:
    """진행도 p(0~1)의 부품 그림(투명 배경). p=0에서는 거의 비어 있고 1에서 완성된다."""
    return _DRAW[part](params, min(max(float(p), 0.0), 1.0))


# ---------------------------------------------------------------- 영상 위 합성 ----

def compose_motion_part(
    *, base_path: str, base_is_video: bool, output_path: str, duration: float, part: str, params: dict,
    start_offset: float = 0.35, dim_base: bool = False,
) -> bool:
    """배경(영상·정지 이미지) 위에 움직임 부품을 프레임 단위로 그려 얹은 mp4를 만든다.

    부품은 start_offset부터 진행도 p가 0→1로 움직이고(최대 3.5초), 끝나면 마지막 모습을 유지하다가 장면 끝에서 사라진다."""
    import subprocess
    from pathlib import Path

    from app.services.slide_overlay import BASE_DIM_FILTER, CANVAS, _COMPOSE_SLOTS, _FFMPEG_THREADS

    width, height = CANVAS
    fps = 30
    duration = max(float(duration), 0.8)
    start = min(max(float(start_offset), 0.0), max(duration - 0.8, 0.0))
    anim = max(min(duration - start - 0.4, 3.5), 0.8)
    base_input = ["-stream_loop", "-1", "-i", base_path] if base_is_video else ["-loop", "1", "-framerate", str(fps), "-i", base_path]
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-threads", str(_FFMPEG_THREADS), *base_input,
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-filter_complex",
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}"
        f"{BASE_DIM_FILTER if dim_base else ''}[b];[1:v]format=rgba[o];[b][o]overlay=0:0:eof_action=pass",
        "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p", output_path,
        "-loglevel", "error",
    ]
    blank = bytes(width * height * 4)
    frames = max(int(round(duration * fps)), 1)
    final_bytes: Optional[bytes] = None
    try:
        with _COMPOSE_SLOTS:
            process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                for k in range(frames):
                    t = k / fps
                    if t < start:
                        process.stdin.write(blank)
                        continue
                    p = min((t - start) / anim, 1.0)
                    if p >= 1.0 and final_bytes is not None and duration - t > 0.3:
                        process.stdin.write(final_bytes)
                        continue
                    sprite = part_sprite(part, params, p)
                    fade = min((duration - t) / 0.3, 1.0)
                    if fade < 1.0:
                        sprite = _with_alpha(sprite, max(fade, 0.0))
                    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                    top = max(min(500 - sprite.height // 2, 890 - sprite.height), 20)
                    canvas.alpha_composite(sprite, ((width - sprite.width) // 2, top))
                    data = canvas.tobytes()
                    if p >= 1.0 and fade >= 1.0:
                        final_bytes = data
                    process.stdin.write(data)
            finally:
                process.stdin.close()
            process.wait(timeout=300)
        ok = process.returncode == 0 and Path(output_path).is_file() and Path(output_path).stat().st_size > 1000
        if not ok:
            logger.warning("움직임 부품 합성 실패(rc=%s): %s", process.returncode, (process.stderr.read() or b"")[-300:])
        return ok
    except Exception:
        logger.exception("움직임 부품 합성 실패")
        return False
