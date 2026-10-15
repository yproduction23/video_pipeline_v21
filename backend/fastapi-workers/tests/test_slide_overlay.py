"""오버레이 계획의 결정론적 검증과 렌더·애니메이션 합성 산출물을 확인한다."""
import shutil
import subprocess

import pytest
from PIL import Image

from app.services.slide_overlay import (
    compose_animated_overlay,
    plan_slide_overlays,
    render_bullet_overlay,
    render_disclaimer_banner,
    render_key_text_overlay,
    render_news_clip_overlay,
    ensure_board_background,
    render_big_number_overlay,
    render_compare_numbers_layers,
    render_flow_layers,
    render_equation_overlay,
    render_stat_overlay,
    validate_overlay_plan,
)

NARRATION = "코스피는 7.6퍼센트, S&P500은 1.1퍼센트 하락했습니다. 외국인이 4조 5천억 원을 던졌습니다."
BRIEFS = {3: {"narration": NARRATION}, 4: {"narration": "숫자 없는 문장입니다.", "has_article": True}}


def test_stat_items_must_appear_verbatim_in_the_scene_narration():
    plan = validate_overlay_plan(
        [{"index": 3, "type": "stat_chart", "title": "낙폭 비교", "items": [
            {"label": "코스피", "value_text": "7.6퍼센트"},
            {"label": "S&P500", "value_text": "1.1퍼센트"},
            {"label": "나스닥", "value_text": "1.88퍼센트"},  # 내레이션에 없는 수치 → 버림
        ]}],
        BRIEFS,
    )
    assert [i["value_text"] for i in plan[3]["items"]] == ["7.6퍼센트", "1.1퍼센트"]
    assert plan[3]["items"][0]["value"] == 7.6


def test_chart_needs_at_least_two_grounded_items():
    plan = validate_overlay_plan(
        [{"index": 3, "type": "stat_chart", "title": "비교", "items": [
            {"label": "코스피", "value_text": "7.6퍼센트"},
            {"label": "나스닥", "value_text": "9.9퍼센트"},
        ]}],
        BRIEFS,
    )
    assert plan == {}


def test_title_cannot_introduce_numbers_absent_from_narration():
    plan = validate_overlay_plan(
        [{"index": 3, "type": "stat_chart", "title": "7배 차이", "items": [
            {"label": "코스피", "value_text": "7.6퍼센트"},
            {"label": "S&P500", "value_text": "1.1퍼센트"},
        ]}],
        BRIEFS,
    )
    assert plan == {}


def test_key_text_must_be_a_verbatim_span_of_the_narration():
    ok = validate_overlay_plan([{"index": 3, "type": "key_text", "text": "4조 5천억 원을 던졌습니다"}], BRIEFS)
    assert ok[3]["type"] == "key_text" and ok[3]["text"] == "4조 5천억 원을 던졌습니다"
    assert ok[3]["icon"] is None and ok[3]["explain"] is False
    invented = validate_overlay_plan([{"index": 3, "type": "key_text", "text": "외국인 5조 매도 폭탄"}], BRIEFS)
    assert invented == {}


def test_bullet_items_must_be_narration_spans_and_need_two_or_more():
    briefs = {1: {"narration": "원인은 금리와 환율, 그리고 자사주 매입 종료입니다."}}
    plan = validate_overlay_plan(
        [{"index": 1, "type": "bullet_list", "title": "하락 원인", "items": ["금리", "환율", "자사주 매입 종료", "AI 거품"]}],
        briefs,
    )
    assert plan[1]["items"] == ["금리", "환율", "자사주 매입 종료"]
    single = validate_overlay_plan(
        [{"index": 1, "type": "bullet_list", "title": "원인", "items": ["금리", "AI 거품"]}], briefs,
    )
    assert single == {}


def test_article_card_is_only_allowed_for_scenes_that_have_an_article():
    plan = validate_overlay_plan(
        [{"index": 3, "type": "article_card"}, {"index": 4, "type": "article_card"}], BRIEFS,
    )
    assert list(plan) == [4]


def test_plan_has_no_fixed_quota_or_spacing():
    briefs = {i: {"narration": f"핵심 주장 {i}번 문장입니다."} for i in range(6)}
    raw = [{"index": i, "type": "key_text", "text": f"핵심 주장 {i}번"} for i in range(6)]
    assert sorted(validate_overlay_plan(raw, briefs)) == [0, 1, 2, 3, 4, 5]


def test_plan_failure_returns_none_so_callers_do_not_hide_it():
    def boom(system, messages, max_tokens):
        raise RuntimeError("api down")

    assert plan_slide_overlays(BRIEFS, llm_call=boom) is None


def test_plan_with_nothing_to_plan_is_an_empty_plan_not_a_failure():
    assert plan_slide_overlays({}, llm_call=lambda *a: "{}") == {}


def test_plan_uses_claude_response_and_validates_it():
    def fake(system, messages, max_tokens):
        return (
            '{"overlays":[{"index":3,"type":"key_text","text":"4조 5천억 원을 던졌습니다"},'
            '{"index":4,"type":"article_card"}]}'
        )

    plan = plan_slide_overlays(BRIEFS, llm_call=fake)
    assert plan[3]["type"] == "key_text" and plan[4]["type"] == "article_card"


def _is_full_canvas_rgba(path):
    image = Image.open(path)
    assert image.size == (1920, 1080) and image.mode == "RGBA"
    return image


def test_stat_overlay_renders_a_full_canvas_scrimmed_chart(tmp_path):
    out = tmp_path / "stat.png"
    assert render_stat_overlay(
        title="낙폭 비교",
        items=[{"label": "코스피", "value": 7.6, "value_text": "7.6퍼센트"},
               {"label": "S&P500", "value": 1.1, "value_text": "1.1퍼센트"}],
        output_path=str(out),
    )
    assert _is_full_canvas_rgba(out).getpixel((10, 10))[3] > 100


def test_news_clip_panel_is_centered_and_compact(tmp_path):
    out = tmp_path / "clip.png"
    assert render_news_clip_overlay(
        publisher="한국경제",
        headline="\"조달비용 높은데, AI 투자 가능할까\"…개미 덮친 빅테크 빚투",
        quote="골드만삭스는 AI 투자를 계속하려면 앞으로도 엄청난 돈을 빌려야 하는데 조달 비용이 비싸지는 환경이라고 했다.",
        output_path=str(out),
    )
    image = _is_full_canvas_rgba(out)
    # 정중앙은 불투명한 밝은 종이색 패널, 모서리는 약한 스크림만
    assert image.getpixel((960, 540))[3] == 255
    assert image.getpixel((20, 20))[3] <= 100
    assert image.getpixel((1900, 1060))[3] <= 100


def test_key_text_and_bullet_overlays_render(tmp_path):
    key = tmp_path / "key.png"
    assert render_key_text_overlay(text="4조 5천억 원을 던졌습니다", output_path=str(key))
    _is_full_canvas_rgba(key)
    bullets = tmp_path / "bullets.png"
    assert render_bullet_overlay(title="하락 원인", items=["금리", "환율", "자사주"], output_path=str(bullets))
    _is_full_canvas_rgba(bullets)
    assert not render_bullet_overlay(title="x", items=["하나뿐"], output_path=str(tmp_path / "bad.png"))


def test_disclaimer_banner_is_a_top_strip_with_transparent_rest(tmp_path):
    out = tmp_path / "banner.png"
    assert render_disclaimer_banner(output_path=str(out))
    image = _is_full_canvas_rgba(out)
    assert image.getpixel((960, 70))[3] > 150
    assert image.getpixel((960, 600))[3] == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg 필요")
def test_animated_overlay_appears_after_the_start_offset(tmp_path):
    base = tmp_path / "base.png"
    Image.new("RGB", (1280, 720), (30, 90, 160)).save(base)
    overlay = tmp_path / "overlay.png"
    assert render_key_text_overlay(text="핵심 구절입니다", output_path=str(overlay))
    out = tmp_path / "clip.mp4"
    assert compose_animated_overlay(
        base_path=str(base), overlay_path=str(overlay), output_path=str(out),
        duration=4.0, base_is_video=False,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(out)],
        capture_output=True, text=True,
    )
    assert 3.5 <= float(probe.stdout.strip()) <= 4.5

    def frame(at):
        png = tmp_path / f"f_{at}.png"
        subprocess.run(["ffmpeg", "-y", "-ss", str(at), "-i", str(out), "-frames:v", "1", str(png), "-loglevel", "error"])
        return Image.open(png).convert("RGB")

    before, during = frame(0.05), frame(1.5)
    # 오버레이가 아직 없을 때는 배경색에 가깝고, 나타난 뒤에는 스크림으로 어두워진다
    assert sum(before.getpixel((20, 20))) > sum(during.getpixel((20, 20)))


EQ_NARRATION = "서울에 집을 가진 분들은 15억 원에서 대출 5억 원을 빼면 순자산 10억 원이 됩니다."


def test_equation_requires_grounded_terms_and_correct_arithmetic():
    ok = validate_overlay_plan(
        [{"index": 1, "type": "equation", "label": "대출", "left": ["15억 원", "5억 원"], "ops": ["-"],
          "result": "순자산 10억 원", "icon": "money_stack", "explain": True}],
        {1: {"narration": EQ_NARRATION}},
    )
    assert ok[1]["left"] == ["15억 원", "5억 원"] and ok[1]["ops"] == ["−"]
    assert ok[1]["icon"] == "money_stack" and ok[1]["explain"] is True

    wrong = validate_overlay_plan(
        [{"index": 1, "type": "equation", "left": ["15억 원", "5억 원"], "ops": ["+"], "result": "순자산 10억 원"}],
        {1: {"narration": EQ_NARRATION}},
    )
    assert wrong == {}
    invented = validate_overlay_plan(
        [{"index": 1, "type": "equation", "left": ["20억 원", "5억 원"], "ops": ["−"], "result": "순자산 10억 원"}],
        {1: {"narration": EQ_NARRATION}},
    )
    assert invented == {}


def test_unknown_icon_is_dropped_but_the_overlay_stays():
    plan = validate_overlay_plan(
        [{"index": 3, "type": "key_text", "text": "4조 5천억 원을 던졌습니다", "icon": "rocket"}], BRIEFS,
    )
    assert plan[3]["icon"] is None


def test_icons_and_equation_render_to_full_canvas(tmp_path):
    key = tmp_path / "key_icon.png"
    assert render_key_text_overlay(text="순자산 10억 원", icon="money_stack", output_path=str(key))
    image = _is_full_canvas_rgba(key)
    # 아이콘 영역에는 스크림(알파 120)이 아니라 불투명한 도형 픽셀이 있어야 한다
    region = image.crop((830, 330, 1090, 590))
    assert max(a for _, _, _, a in region.getdata()) == 255
    eq = tmp_path / "eq.png"
    assert render_equation_overlay(label="대출", left=["15억 원", "5억 원"], ops=["−"],
                                   result="순자산 10억 원", icon="house", output_path=str(eq))
    _is_full_canvas_rgba(eq)


def test_board_background_is_dark_reused_and_deterministic_to_reuse(tmp_path):
    out = tmp_path / "board.png"
    assert ensure_board_background(str(out))
    first = out.read_bytes()
    assert ensure_board_background(str(out))
    assert out.read_bytes() == first
    image = Image.open(out).convert("RGB")
    assert image.size == (1920, 1080)
    assert sum(image.getpixel((960, 540))) < 3 * 90


REF_NARRATION = "순자산 10억 원은 현재 상위 11.8퍼센트, 과거에는 상위 7.9퍼센트였습니다. 자산에서 현금으로 이어집니다. 숫자는 8,343입니다."


def test_big_number_value_must_be_verbatim_and_contain_a_digit():
    ok = validate_overlay_plan([{"index": 1, "type": "big_number", "value": "8,343", "label": "건수"}],
                               {1: {"narration": REF_NARRATION}})
    assert ok[1]["value"] == "8,343"
    bad = validate_overlay_plan([{"index": 1, "type": "big_number", "value": "9,999"}], {1: {"narration": REF_NARRATION}})
    assert bad == {}


def test_compare_numbers_needs_grounded_rows_and_headline():
    entry = {"index": 1, "type": "compare_numbers", "headline": "순자산 10억 원", "icon": "money_stack",
             "rows": [{"label": "현재", "value": "11.8퍼센트"}, {"label": "과거", "value": "7.9퍼센트"}]}
    ok = validate_overlay_plan([entry], {1: {"narration": REF_NARRATION}})
    assert [r["value"] for r in ok[1]["rows"]] == ["11.8퍼센트", "7.9퍼센트"]
    invented = dict(entry, rows=[{"label": "현재", "value": "11.8퍼센트"}, {"label": "과거", "value": "6.1퍼센트"}])
    assert validate_overlay_plan([invented], {1: {"narration": REF_NARRATION}}) == {}


def test_flow_needs_two_grounded_labels_and_drops_unknown_icons():
    ok = validate_overlay_plan(
        [{"index": 1, "type": "flow", "nodes": [{"label": "자산", "icon": "building"}, {"label": "현금", "icon": "rocket"}]}],
        {1: {"narration": REF_NARRATION}},
    )
    assert [n["icon"] for n in ok[1]["nodes"]] == ["building", None]


def test_layered_renderers_produce_staggered_layers(tmp_path):
    cmp_paths = render_compare_numbers_layers(
        headline="순자산 10억 원", icon="money_stack",
        rows=[{"label": "현재", "value": "11.8퍼센트"}, {"label": "과거", "value": "7.9퍼센트"}],
        output_path=str(tmp_path / "cmp.png"),
    )
    assert len(cmp_paths) == 3
    first = _is_full_canvas_rgba(cmp_paths[0])
    later = _is_full_canvas_rgba(cmp_paths[1])
    assert first.getpixel((5, 5))[3] > 0          # 첫 레이어에만 스크림
    assert later.getpixel((5, 5))[3] == 0          # 이후 레이어는 새 요소만
    flow_paths = render_flow_layers(
        nodes=[{"label": "자산", "icon": "building"}, {"label": "현금", "icon": "money_stack"}],
        output_path=str(tmp_path / "flow.png"),
    )
    assert len(flow_paths) == 2
    big = render_big_number_overlay(value="8,343", label="건수", output_path=str(tmp_path / "big.png"))
    assert big and _is_full_canvas_rgba(big[0])
