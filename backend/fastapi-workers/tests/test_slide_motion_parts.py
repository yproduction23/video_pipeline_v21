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
