"""회사 로고 줄(2026-10-18 운영자 피드백, 2:30): 삼성전자·SK하이닉스 같은 회사를 말할 때 로고가 말하는 순간마다 하나씩 뜬다.
로고는 목록에 있는 회사이면서 내레이션에 글자 그대로 나온 회사만 쓴다."""
from app.services.slide_logos import logo_labels, spoken_form
from app.services.slide_overlay import render_logo_row_layers, validate_overlay_plan

NARRATION = "삼성전자와 SK하이닉스가 반도체 반등을 이끌었습니다."
BRIEFS = {2: {"narration": NARRATION}}


def test_spoken_form_is_the_exact_text_in_the_narration_and_prefers_the_longest_alias():
    assert spoken_form("삼성전자", NARRATION) == "삼성전자"
    assert spoken_form("SK하이닉스", NARRATION) == "SK하이닉스"
    assert spoken_form("삼성전자", "엔비디아가 올랐습니다") is None        # 말하지 않은 회사의 로고는 쓰지 않는다
    assert spoken_form("없는회사", NARRATION) is None


def test_logo_row_keeps_only_listed_companies_that_the_narration_names_in_order():
    assert {"삼성전자", "SK하이닉스"} <= set(logo_labels())
    plan = validate_overlay_plan(
        [{"index": 2, "type": "logo_row", "companies": ["삼성전자", "SK하이닉스", "엔비디아", "없는회사", "삼성전자"]}], BRIEFS,
    )

    assert plan[2]["type"] == "logo_row" and plan[2]["explain"] is False
    assert [item["name"] for item in plan[2]["logos"]] == ["삼성전자", "SK하이닉스"]
    assert [item["phrase"] for item in plan[2]["logos"]] == ["삼성전자", "SK하이닉스"]


def test_logo_row_without_any_named_company_is_dropped():
    assert validate_overlay_plan([{"index": 2, "type": "logo_row", "companies": ["엔비디아", "없는회사"]}], BRIEFS) == {}


def test_logo_row_renders_one_centered_layer_per_logo(tmp_path):
    from PIL import Image

    out = tmp_path / "logos.png"
    layers = render_logo_row_layers(
        logos=[{"name": "삼성전자", "phrase": "삼성전자"}, {"name": "SK하이닉스", "phrase": "SK하이닉스"}], output_path=str(out),
    )

    assert layers is not None and len(layers) == 2
    boxes = [Image.open(path).getchannel("A").getbbox() for path in layers]
    assert all(boxes) and boxes[0][2] <= boxes[1][0] + 1                       # 왼쪽에서 오른쪽으로, 겹치지 않는다
    center = (boxes[0][0] + boxes[1][2]) / 2
    assert abs(center - 960) < 40                                              # 줄 전체가 가운데
