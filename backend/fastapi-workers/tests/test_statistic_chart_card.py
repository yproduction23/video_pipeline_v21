from PIL import Image

from app.utils.statistic_chart_card import extract_numeric_series, render_bar_chart_card


def test_extracts_the_real_year_over_year_series_from_a_live_article_sentence():
    """2026-10-14 job 18 재현: 머니투데이 기사 실제 문장("경찰청에 따르면
    이상동기 범죄는 △2023년 46건 △2024년 42건 △2025년 39건으로 매년
    40건 안팎 발생했다")에서 숫자 계열을 그대로 뽑아낼 수 있어야 한다 —
    라이브 기사에서 가져온 원문 그대로다."""
    sentence = "경찰청에 따르면 이상동기 범죄는 △2023년 46건 △2024년 42건 △2025년 39건으로 매년 40건 안팎 발생했다."

    series = extract_numeric_series(sentence)

    assert series is not None
    assert [item["label"] for item in series["items"]] == ["2023년", "2024년", "2025년"]
    assert [item["value"] for item in series["items"]] == [46.0, 42.0, 39.0]
    assert all(item["unit"] == "건" for item in series["items"])
    # 제목은 지어내지 않고 숫자 계열 바로 앞의 원문 문장 조각을 그대로 쓴다.
    assert series["lead_text"] == "경찰청에 따르면 이상동기 범죄는"


def test_returns_none_when_fewer_than_min_items_found():
    sentence = "코스피가 2.5% 올랐다."

    assert extract_numeric_series(sentence) is None


def test_does_not_merge_numbers_from_unrelated_parts_of_a_long_document():
    """같은 글 안에서도 서로 멀리 떨어진 숫자(다른 문단)를 억지로 하나의
    계열로 엮지 않는다."""
    text = (
        "2023년 46건 2024년 42건 2025년 39건이었다. "
        + ("딴 얘기 " * 10)
        + "그리고 전혀 다른 2030년 1건이 추가로 보도됐다."
    )

    series = extract_numeric_series(text)

    assert series is not None
    assert len(series["items"]) == 3


def test_renders_a_real_png_bar_chart(tmp_path):
    output_path = tmp_path / "chart.png"
    items = [
        {"label": "2023년", "value": 46.0, "unit": "건"},
        {"label": "2024년", "value": 42.0, "unit": "건"},
        {"label": "2025년", "value": 39.0, "unit": "건"},
    ]

    ok = render_bar_chart_card(
        title="연도별 이상동기 범죄 발생 건수", items=items, publisher="머니투데이", output_path=str(output_path),
    )

    assert ok is True
    assert output_path.is_file()
    with Image.open(output_path) as img:
        assert img.size == (1920, 1080)


def test_render_returns_false_for_empty_items(tmp_path):
    output_path = tmp_path / "chart.png"

    ok = render_bar_chart_card(title="제목", items=[], output_path=str(output_path))

    assert ok is False
    assert not output_path.is_file()
