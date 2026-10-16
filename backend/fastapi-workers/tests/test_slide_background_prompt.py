"""Gemini 실사 배경에 글자·수치·전광판이 들어가지 않게 하는 입력 정리 (2026-10-16 20번 Job).

1번 장면은 내레이션 문장이 전광판에 그대로 찍히고, 6·19번은 가짜 KOSPI 수치판이 그려져 하단 자막과 이중이
됐다. 스톡 검색용 검색어("stock market ... screen numbers")가 그대로 Gemini에 들어간 것이 원인이었다."""
from app.services import slide_background as sb
from app.services.slide_background import sanitize_photo_query


def test_screen_and_number_words_are_removed_from_the_scene_description():
    cleaned = sanitize_photo_query("crowded subway station commuters with phones, red numbers screen chart")

    assert "screen" not in cleaned and "numbers" not in cleaned and "chart" not in cleaned
    assert "subway station commuters" in cleaned


def test_finance_screen_prone_queries_fall_back_to_street_scenery():
    cleaned = sanitize_photo_query("stock market index falling red numbers screen")

    assert cleaned == sb._FALLBACK_SCENE


def test_prompt_forbids_legible_text_and_never_embeds_screens(monkeypatch):
    captured = {}

    class FakeProvider:
        def generate_image(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.providers.real.image.NanaBananaProvider", FakeProvider)
    monkeypatch.setattr(
        "app.utils.budget.ProviderRequestAudit.for_job", classmethod(lambda cls, **kw: object()),
    )

    sb.generate_korean_slide_background(
        "stock market crash red screen numbers falling — scene context: 코스피가 급락했습니다.",
        "/tmp/out.png", job_id=1, scene_key="slide_bg:0",
    )

    prompt = captured["prompt"]
    assert "NO legible text" in prompt and "no electronic signboards" in prompt
    assert "screen numbers falling" not in prompt
    assert "never write it into the image" in prompt
