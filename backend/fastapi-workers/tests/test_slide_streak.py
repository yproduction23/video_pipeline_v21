"""연속 일수 달력(streak): "8거래일 연속"을 들으면 달력에 동그라미 8개가 나오고 X가 차례로 쳐진다(2026-10-17 운영자 요청:
"자막 그대로·엉뚱한 아이콘이 아니라 연상이 되는 진짜 모션그래픽"). 어떤 장면(스톡 위 후편집·회색 배경)에서도 나와야 하므로
내레이션에서 결정론적으로 찾아 계획서에 넣는다."""
from PIL import Image

from app.services.slide_edit_plan import assemble_plan
from app.services.slide_overlay import render_streak_layers, validate_overlay_plan
from app.services.slide_streak import detect_streak


def test_detect_streak_finds_consecutive_period_phrases_only():
    assert detect_streak("외국인은 이 시점까지 8거래일 연속으로 코스피를 팔았습니다.") == {"phrase": "8거래일", "days": 8, "unit": "거래일"}
    assert detect_streak("5일 연속 상승했습니다")["days"] == 5
    assert detect_streak("3주째 하락 중입니다")["unit"] == "주"
    assert detect_streak("코스피는 하루 만에 7.57퍼센트 급락했습니다.") is None      # 연속이 아니다
    assert detect_streak("1거래일 연속") is None and detect_streak("20일 연속 상승") is None  # 범위(2~14) 밖
    assert detect_streak("8거래일 누적 순매도 규모는 약 17조 원입니다.") is None        # 누적은 연속이 아니다


def test_streak_overlay_is_validated_against_the_narration():
    briefs = {1: {"narration": "외국인은 8거래일 연속으로 코스피를 팔았습니다."}}

    ok = validate_overlay_plan([{"index": 1, "type": "streak", "count": "8거래일"}], briefs)
    assert ok[1]["type"] == "streak" and ok[1]["count"] == "8거래일" and ok[1]["days"] == 8

    assert validate_overlay_plan([{"index": 1, "type": "streak", "count": "9거래일"}], briefs) == {}   # 내레이션에 없는 일수
    assert validate_overlay_plan([{"index": 1, "type": "streak", "count": "1거래일"}], briefs) == {}


def test_streak_layers_add_one_x_per_layer_and_end_with_the_phrase(tmp_path):
    out = tmp_path / "streak.png"

    layers = render_streak_layers(count=4, phrase="4거래일", output_path=str(out))

    assert len(layers) == 6                                   # 달력 + X 4개 + 문구
    sizes = [Image.open(p).size for p in layers]
    assert all(size == (1920, 1080) for size in sizes)

    def red(path):
        return sum(1 for r, g, b, a in Image.open(path).convert("RGBA").getdata() if a > 200 and r > 200 and g < 90 and b < 90)

    marks = [red(p) for p in layers[:5]]
    assert marks[0] == 0 and all(a < b for a, b in zip(marks, marks[1:]))


def test_cue_in_the_narration_becomes_a_streak_overlay_even_when_claude_chose_something_else():
    """운영자가 원하는 연상 그래픽을 Claude의 선택에 맡기지 않는다: 연속 일수 표현이 있으면 계획서가 달력으로 바꾼다."""
    narrations = ["외국인은 이 시점까지 8거래일 연속으로 코스피를 팔았습니다.", "순매도 규모는 17조 9310억 원입니다."]
    briefs = {i: {"narration": t, "duration": 4.0} for i, t in enumerate(narrations)}
    entries = [
        {"index": 0, "background": "stock", "stock_queries": ["trader"], "overlay": {"type": "key_text", "text": "8거래일 연속으로"}},
        {"index": 1, "background": "stock", "stock_queries": ["money"], "overlay": {"type": "big_number", "value": "17조 9310억 원"}},
    ]

    plan = assemble_plan(briefs, entries, job_id=1)

    first, second = plan["scenes"]
    assert first["overlay"]["type"] == "streak" and first["overlay"]["days"] == 8
    assert first["overlay"]["explain"] is False                       # 스톡 위에 얹는다
    assert second["overlay"]["type"] == "big_number"                  # 다른 장면은 그대로


def test_streak_on_a_gray_board_scene_becomes_a_sequence_calendar():
    """회색 배경 구간의 시퀀스에서도 같은 달력이 나온다(Claude가 템플릿을 안 골라도 장면 계획서의 달력이 요소로 들어간다)."""
    from app.services.slide_sequence import _fallback_scene_element

    scene = {"index": 3, "overlay": {"type": "streak", "count": "8거래일", "days": 8, "title": ""}}
    element = _fallback_scene_element(scene, {3: 10.0}, 10.0, "e1")

    assert element["kind"] == "streak" and element["content"] == {"count": 8, "phrase": "8거래일"} and element["anim"] == "draw"
