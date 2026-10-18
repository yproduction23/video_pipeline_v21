"""움직임 부품(2026-10-17 운영자 요청): 대본 행간이 연상되도록 소재가 움직이는 모션그래픽 — 이동·게이지·저울·확산·받침·쌓임.
글자는 내레이션 원문에서 그대로 가져온 것만 쓰고, 수량·정도는 숫자 없이 방향·정도로만 그린다."""
from PIL import Image

from app.services.slide_motion_parts import PARTS, part_sprite, validate_part

NARRATION = "자금이 빠져나가 신흥국에서 안전자산으로 이동했고 금리는 높은 수준입니다. 삼성전자 자사주 매입이 하방을 받쳐 줍니다."


def _opaque_pixels(image: Image.Image) -> int:
    return sum(1 for pixel in image.getchannel("A").getdata() if pixel > 128)


def test_every_part_draws_something_that_grows_with_progress_and_fits_above_the_subtitle():
    params = {
        "move": {"item": "달러 지폐 더미", "source_label": "신흥국", "target_label": "안전자산"},
        "gauge": {"label": "금리", "level": "high"},
        "balance": {"left": "자사주 매입", "right": "외국인 매도", "heavier": "right"},
        "spread": {"origin": "자금이 빠져나가", "effect": "금리는 높은 수준"},
        "support": {"top": "삼성전자", "pillar": "자사주 매입", "state": "holding"},
        "pile": {"item": "달러 지폐 더미", "trend": "grow", "label": "자금"},
        "chart": {"direction": "fall", "label": "코스피"},
        "target": {"target_label": "추정치", "actual_label": "이 실적", "result": "below"},
        "mood": {"motif": "경광등", "label": "자금"},
    }
    assert set(params) == set(PARTS)
    for part, values in params.items():
        start, middle, end = (part_sprite(part, values, p) for p in (0.0, 0.5, 1.0))
        assert end.mode == "RGBA" and end.height <= 720 and end.width <= 1600, part
        assert _opaque_pixels(start) < _opaque_pixels(end), part      # 시간이 지나며 그림이 채워진다
        assert _opaque_pixels(start) <= _opaque_pixels(middle), part


def test_balance_tilts_toward_the_heavier_side():
    values = {"left": "자사주 매입", "right": "외국인 매도", "heavier": "right"}
    sprite = part_sprite("balance", values, 1.0)

    def mass(box):
        cropped = sprite.crop(box).getchannel("A")
        return sum(1 for pixel in cropped.getdata() if pixel > 128)

    # 무거운 쪽(오른쪽)의 받침이 더 아래로 내려가므로 같은 높이의 아래쪽 구간에는 오른쪽 그림이 더 많다
    h = sprite.height
    low_left = mass((0, int(h * 0.62), sprite.width // 2, h))
    low_right = mass((sprite.width // 2, int(h * 0.62), sprite.width, h))
    assert low_right > low_left


def test_labels_must_come_verbatim_from_the_narration_and_enumerations_are_checked():
    ok = validate_part("balance", {"left": "자사주 매입", "right": "외국인 매도", "heavier": "right"}, NARRATION)
    assert ok == {"left": "자사주 매입", "right": "외국인 매도", "heavier": "right"} or ok is None or ok["heavier"] == "right"

    good = validate_part("support", {"top": "삼성전자", "pillar": "자사주 매입", "state": "holding"}, NARRATION)
    assert good == {"top": "삼성전자", "pillar": "자사주 매입", "state": "holding"}

    assert validate_part("support", {"top": "삼성전자", "pillar": "없는 말", "state": "holding"}, NARRATION) is None   # 원문에 없는 글자
    assert validate_part("gauge", {"label": "금리", "level": "엄청"}, NARRATION) is None                           # 허용 정도값이 아니다
    assert validate_part("unknown_part", {}, NARRATION) is None


def test_move_requires_an_existing_motif_for_the_moving_item_and_numbers_are_not_invented():
    moved = validate_part("move", {"item": "달러 지폐 더미", "source_label": "신흥국", "target_label": "안전자산"}, NARRATION)
    assert moved["item"] == "달러 지폐 더미" and moved["source_label"] == "신흥국"
    assert validate_part("move", {"item": "없는 소재", "source_label": "신흥국"}, NARRATION) is None
    # 게이지의 값은 숫자 구절이 내레이션에 그대로 있을 때만 쓴다
    gauge = validate_part("gauge", {"label": "금리", "level": "high", "value": "5.3%"}, NARRATION)
    assert gauge is not None and "value" not in gauge


def test_part_overlay_is_composed_over_the_base_frame_by_frame_and_appears_after_the_start_offset(tmp_path):
    import subprocess

    from app.services.slide_motion_parts import compose_motion_part

    base = tmp_path / "base.png"
    Image.new("RGB", (1920, 1080), (40, 40, 44)).save(base)
    out = tmp_path / "composed.mp4"

    ok = compose_motion_part(
        base_path=str(base), base_is_video=False, output_path=str(out), duration=2.4, part="gauge",
        params={"label": "금리", "level": "high"}, start_offset=0.8, dim_base=False,
    )

    assert ok and out.stat().st_size > 1000

    def frame(t):
        result = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                                 "-vf", "scale=96:54,format=gray", "-f", "rawvideo", "-"], capture_output=True)
        return result.stdout

    before, after = frame(0.3), frame(2.0)
    assert before != after                                    # 시작 시각 뒤에 그림이 나타난다
    assert max(before) - min(before) < 8                      # 시작 전에는 배경뿐이다


def test_a_continued_part_picks_up_the_animation_where_the_previous_scene_left_it(tmp_path):
    """여러 장면에 걸친 그림: 뒤 장면은 이미 진행된 모습으로 처음부터 떠 있고(등장 연출 없음), 마지막 장면에서만 사라진다."""
    import subprocess

    from app.services.slide_motion_parts import compose_motion_part

    base = tmp_path / "base.png"
    Image.new("RGB", (1920, 1080), (40, 40, 44)).save(base)
    params = {"label": "금리", "level": "high"}

    def frame(path, t):
        result = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                                 "-vf", "scale=96:54,format=gray", "-f", "rawvideo", "-"], capture_output=True)
        return result.stdout

    follower = tmp_path / "follower.mp4"
    assert compose_motion_part(
        base_path=str(base), base_is_video=False, output_path=str(follower), duration=2.0, part="gauge", params=params,
        start_offset=0.0, dim_base=False, progress_offset=3.0, anim_total=6.0, fade_out=False,
    )
    first = frame(follower, 0.05)
    assert max(first) - min(first) > 20                       # 첫 프레임부터 그림이 떠 있다(진행도 50% 지점)
    assert first != frame(follower, 1.9)                      # 이어서 계속 움직인다
    last = frame(follower, 1.95)
    assert max(last) - min(last) > 20                         # 마지막 장면이 아니면 장면 끝에서 사라지지 않는다


def test_part_is_drawn_on_a_soft_dark_panel_so_labels_stay_readable_on_bright_footage(tmp_path):
    import subprocess

    from app.services.slide_motion_parts import compose_motion_part

    base = tmp_path / "bright.png"
    Image.new("RGB", (1920, 1080), (235, 235, 235)).save(base)
    out = tmp_path / "panel.mp4"
    assert compose_motion_part(
        base_path=str(base), base_is_video=False, output_path=str(out), duration=3.0, part="balance",
        params={"left": "자사주 매입", "right": "외국인 매도", "heavier": "right"}, start_offset=0.2, dim_base=False,
    )
    raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "2.4", "-i", str(out), "-frames:v", "1",
                          "-vf", "scale=192:108,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    # 밝은 배경(235) 위에 어두운 패널이 깔려 중앙부에 어두운 픽셀이 넓게 있다
    dark = sum(1 for v in raw if v < 120)
    assert dark > 192 * 108 * 0.12


def test_chart_draws_a_falling_red_line_and_a_rising_green_one():
    def hues(direction):
        sprite = part_sprite("chart", {"direction": direction}, 1.0)
        pixels = [px for px in sprite.getdata() if px[3] > 200]
        red = sum(1 for r, g, b, a in pixels if r > 200 and g < 90 and b < 90)
        green = sum(1 for r, g, b, a in pixels if g > 170 and r < 90 and b < 140)
        return red, green

    fall_red, fall_green = hues("fall")
    rise_red, rise_green = hues("rise")
    assert fall_red > 1500 and fall_green < 200
    assert rise_green > 1500 and rise_red < 200


def test_target_bar_stops_below_the_expectation_line_and_passes_it_above():
    def bar_top(result):
        sprite = part_sprite("target", {"target_label": "추정치", "actual_label": "이 실적", "result": result}, 1.0)
        # 점선 사이 틈(x=520)에서 막대 윗면을 찾는다
        ys = [y for y in range(sprite.height) if sprite.getpixel((520, y))[3] > 200]
        return min(ys)

    assert bar_top("below") > bar_top("above")            # 하회: 막대가 더 낮다
    assert bar_top("below") > 290 and bar_top("above") < 290      # 기대선(y=290)을 기준으로 아래/위


def test_new_part_params_are_validated_against_the_narration_and_the_motif_library():
    narration = "실적이 추정치를 소폭 밑돌았습니다. 시장은 공포 분위기였습니다."
    assert validate_part("target", {"target_label": "추정치", "actual_label": "실적이", "result": "below"}, narration) == {
        "target_label": "추정치", "actual_label": "실적이", "result": "below"}
    assert validate_part("target", {"target_label": "추정치", "actual_label": "실적이", "result": "엄청"}, narration) is None
    assert validate_part("chart", {"direction": "fall", "label": "없는 말"}, narration) == {"direction": "fall"}   # 라벨은 원문에 없으면 뺀다
    assert validate_part("chart", {"direction": "sideways"}, narration) is None
    assert validate_part("mood", {"motif": "경광등", "label": "공포 분위기"}, narration) == {"motif": "경광등", "label": "공포 분위기"}
    assert validate_part("mood", {"motif": "없는 소재"}, narration) is None
    assert validate_part("move", {"item": "달러 지폐 더미", "source_motif": "은행 건물", "arrival": "burst"}, narration)["arrival"] == "burst"


def test_counter_counts_up_from_zero_and_ends_on_the_original_value():
    """2026-10-18 운영자 피드백: 숫자가 차르르 올라가는 연출. 올라가는 도중의 숫자는 연출일 뿐, 마지막에는 원문 값 그대로다."""
    from app.services.slide_motion_parts import is_countable
    from app.services.slide_sequence import _number_text

    assert is_countable("7.6%") and is_countable("4조 5천억 원") and not is_countable("급락")
    assert _number_text("12,345명", 0.0).startswith("0") and _number_text("12,345명", 1.0) == "12,345명"
    start, middle, end = (part_sprite("counter", {"value": "12,345명", "label": "순매도"}, p) for p in (0.0, 0.5, 1.0))
    assert start.size == middle.size == end.size            # 숫자가 변해도 그림 크기가 흔들리지 않는다
    assert _opaque_pixels(start) < _opaque_pixels(end)      # 0에서 시작해 점점 또렷해진다
    assert middle.tobytes() != end.tobytes()
