from PIL import Image

from app.utils.article_headline_card import render_headline_card


def test_renders_a_real_png_with_publisher_headline_and_quote(tmp_path):
    output_path = tmp_path / "card.png"

    ok = render_headline_card(
        publisher="연합뉴스",
        headline="8명 찌른 칼부림에도 '규정 위반 없음'…전자발찌의 역설",
        quote="14일 경찰과 법무부 등에 따르면 전날 오후 1시 40분께 인천 남동구 구월동의 한 대형 상가에서 양손에 테이프로 흉기를 묶은 채 무차별 난동을 벌였다.",
        output_path=str(output_path),
    )

    assert ok is True
    assert output_path.is_file()
    with Image.open(output_path) as img:
        assert img.size == (1920, 1080)


def test_returns_false_for_empty_headline(tmp_path):
    output_path = tmp_path / "card.png"

    ok = render_headline_card(publisher="연합뉴스", headline="", output_path=str(output_path))

    assert ok is False
    assert not output_path.is_file()


def test_renders_without_a_publisher_badge_when_publisher_is_missing(tmp_path):
    output_path = tmp_path / "card.png"

    ok = render_headline_card(publisher="", headline="전자발찌도 못 막은 흉기난동", output_path=str(output_path))

    assert ok is True
    assert output_path.is_file()


def test_renders_headline_only_when_quote_is_missing(tmp_path):
    output_path = tmp_path / "card.png"

    ok = render_headline_card(publisher="머니투데이", headline="전자발찌도 못 막은 흉기난동", output_path=str(output_path))

    assert ok is True
    assert output_path.is_file()


def test_long_quote_is_mechanically_trimmed_not_rewritten(tmp_path):
    """LLM이 본문을 요약/재작성하지 않고 단어 경계에서 기계적으로 자르는지
    확인한다 — 잘린 결과의 시작 부분이 원문 그대로여야 한다."""
    from app.utils.article_headline_card import _trim

    quote = "14일 경찰과 법무부 등에 따르면 전날 오후 1시 40분께 인천 남동구 구월동의 한 대형 상가에서 양손에 테이프로 흉기를 묶은 채 무차별 난동을 벌여 시민 8명을 다치게 했다."
    trimmed = _trim(quote, 40)

    assert quote.startswith(trimmed.rstrip("…").rstrip())
    assert trimmed.endswith("…")
    assert len(trimmed) <= 41


def test_photo_copyright_notice_and_byline_email_are_stripped_from_the_quote():
    """2026-10-14 job 18 재현: 연합뉴스 article_capture.quote가 "재판매 및
    DB 금지] goodluck@yna.co.kr ◇ 8명 찌른 칼부림에도..."로 시작했다 —
    사진 저작권 고지와 기자 이메일이 본문과 함께 추출된 경우다. 카드에는
    실제 기사 본문만 보여야 한다."""
    from app.utils.article_headline_card import _clean_quote

    raw = (
        "재판매 및 DB 금지] goodluck@yna.co.kr ◇ 8명 찌른 칼부림에도 "
        "'규정 위반 없음'…전자발찌의 역설 14일 경찰과 법무부 등에 따르면 "
        "전날 오후 1시 40분께 인천 남동구 구월동의 한 대형 상가에서 "
        "양손에 테이프로 흉기를 묶은 채 무차별 난동을 벌였다."
    )

    cleaned = _clean_quote(raw)

    assert "재판매" not in cleaned
    assert "@" not in cleaned
    assert "◇" not in cleaned
    assert "14일 경찰과 법무부" in cleaned


def test_only_publisher_name_is_shown_no_url_or_date(tmp_path):
    """출처는 발행사 이름 배지로만 표시한다 — URL·발행일 등 상세 인용 표기는
    렌더러에 파라미터 자체가 없어야 한다(레퍼런스 채널 스타일 결정)."""
    import inspect

    from app.utils.article_headline_card import render_headline_card as func

    params = set(inspect.signature(func).parameters)
    assert "url" not in params
    assert "published_at" not in params
