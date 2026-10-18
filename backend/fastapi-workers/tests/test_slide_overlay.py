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
    assert [i["value_text"] for i in plan[3]["items"]] == ["7.6%", "1.1%"]  # 화면에는 % 기호
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
        [{"index": 3, "type": "key_text", "text": "4조 5천억 원을 던졌습니다", "icon": "unicorn"}], BRIEFS,
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
    assert [r["value"] for r in ok[1]["rows"]] == ["11.8%", "7.9%"]  # 화면에는 % 기호
    invented = dict(entry, rows=[{"label": "현재", "value": "11.8퍼센트"}, {"label": "과거", "value": "6.1퍼센트"}])
    assert validate_overlay_plan([invented], {1: {"narration": REF_NARRATION}}) == {}


def test_flow_needs_two_grounded_labels_and_drops_unknown_icons():
    ok = validate_overlay_plan(
        [{"index": 1, "type": "flow", "nodes": [{"label": "자산", "icon": "building"}, {"label": "현금", "icon": "unicorn"}]}],
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


def test_wrap_breaks_at_word_boundaries_and_never_leaves_a_lone_syllable():
    """2026-10-16 재현: 글자 단위 줄바꿈이 "겁니/다", "마/무리"처럼 단어 중간을 잘랐다."""
    from PIL import ImageDraw, ImageFont

    from app.services.slide_overlay import _wrap_text

    font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 52)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for text in ("외국인 이탈이 핵심 변수였던 겁니다", "삼성전자의 자사주 매입은 이미 마무리", "자사주 매입이 끝났기 때문이라는 설명"):
        width = int(draw.textlength(text, font=font) * 0.62)
        lines = _wrap_text(draw, text, font, width, 3)
        assert " ".join(lines) == text  # 단어를 쪼개지 않는다
        assert all(draw.textlength(line, font=font) <= width + 1 for line in lines)
        if len(lines) > 1:
            assert min(len(line) for line in lines) >= 3  # 한 글자 줄이 없다


def test_wrap_still_splits_a_single_overlong_word():
    from PIL import ImageDraw, ImageFont

    from app.services.slide_overlay import _wrap_text

    font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 52)
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap_text(draw, "삼성전자하이닉스네이버카카오", font, 300, 3)

    assert len(lines) > 1 and all(draw.textlength(line, font=font) <= 301 for line in lines)


def test_percent_sign_is_spelled_like_the_narration_before_verbatim_validation():
    """2026-10-16 재현: Claude가 내레이션의 "7.57퍼센트"를 "7.57%"로 옮겨 후편집이 통째로 거절됐다."""
    briefs = {0: {"narration": "코스피는 7.57퍼센트 폭락했죠."}}

    plan = validate_overlay_plan([{"index": 0, "type": "key_text", "text": "7.57% 폭락했죠"}], briefs)

    assert plan[0]["text"] == "7.57% 폭락했죠"  # 검증은 내레이션 표기로 하고, 화면에는 % 기호로 보여 준다


def test_compose_retries_once_when_ffmpeg_times_out_under_load(monkeypatch, tmp_path):
    """2026-10-16 20번 Job: 4K 스톡을 병렬로 합성하다 일부 ffmpeg가 240초 시간 초과로 멈춰 후편집 8장면이 배경만 남았다.
    시간 초과는 한 번 재시도하고, 실패 원인을 로그에 남긴다."""
    import subprocess

    from app.services import slide_overlay

    calls = []
    out = tmp_path / "o.mp4"

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, 240)
        out.write_bytes(b"x" * 2000)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(slide_overlay.subprocess, "run", fake_run)
    base = tmp_path / "b.png"
    overlay = tmp_path / "ov.png"
    Image.new("RGB", (64, 36)).save(base)
    Image.new("RGBA", (64, 36)).save(overlay)

    ok = slide_overlay.compose_animated_overlay(
        base_path=str(base), overlay_path=str(overlay), output_path=str(out), duration=3.0, base_is_video=False,
    )

    assert ok is True and len(calls) == 2
    assert "-threads" in calls[0] and "-nostdin" in calls[0]


def test_key_text_over_footage_must_be_a_short_keyword_not_a_whole_clause():
    """2026-10-16 20번 Job 완성 영상: 스톡 위 문구가 자막과 같은 절("코어위브의 주가가 급락했습니다")을 그대로 되풀이했다.
    영상 위 강조 문구는 14자 이내 핵심어만 허용하고, 칠판 위(explain) 문구는 기존처럼 28자까지 허용한다."""
    briefs = {0: {"narration": "기업 코어위브의 주가가 급락했습니다."}}
    long_text = "코어위브의 주가가 급락했습니다"  # 15자

    assert 0 not in validate_overlay_plan([{"index": 0, "type": "key_text", "text": long_text, "on_footage": True}], briefs)
    assert 0 in validate_overlay_plan([{"index": 0, "type": "key_text", "text": "주가가 급락", "on_footage": True}], briefs)
    assert 0 in validate_overlay_plan([{"index": 0, "type": "key_text", "text": long_text}], briefs)


def test_screen_numbers_show_a_percent_sign_whether_the_narration_says_percent_or_uses_the_symbol():
    """2026-10-16 운영자 요청: 화면에는 "퍼센트" 대신 "%"를 쓴다(레퍼런스도 "-14.4%"). 검증은 내레이션 표기와 글자 그대로 비교한다."""
    spelled = {0: {"narration": "코스피는 7.57퍼센트 폭락했죠."}}
    symbol = {0: {"narration": "코스피는 7.57% 폭락했죠."}}

    for briefs in (spelled, symbol):
        plan = validate_overlay_plan([{"index": 0, "type": "big_number", "value": "7.57%", "label": ""}], briefs)
        assert plan[0]["value"] == "7.57%"
        plan = validate_overlay_plan([{"index": 0, "type": "big_number", "value": "7.57퍼센트", "label": ""}], briefs)
        assert plan[0]["value"] == "7.57%"


def test_red_emphasis_uses_the_bundled_display_font_not_a_faux_bold_stroke():
    """2026-10-16 운영자 피드백: 빨간 강조 글자가 두껍기만 하고 예쁘지 않다. 레퍼런스처럼 굵은 디스플레이 서체를 쓴다."""
    from PIL import ImageFont

    from app.services.slide_overlay import _FONT_DISPLAY, _font

    font = _font(_FONT_DISPLAY, 100)

    assert isinstance(font, ImageFont.FreeTypeFont) and "BlackHanSans" in str(font.path)


def test_board_key_text_is_drawn_in_clean_red_without_a_stroke_halo(tmp_path):
    out = tmp_path / "k.png"

    assert render_key_text_overlay(text="코스피의 구조적 취약점", output_path=str(out), style="board")

    pixels = Image.open(out).convert("RGB").getdata()
    reds = [p for p in pixels if p[0] > 200 and p[1] < 80 and p[2] < 80]
    assert len(reds) > 3000  # 선명한 빨강 글자가 충분히 그려진다


def test_source_credit_is_drawn_bottom_left_and_empty_publisher_is_ignored(tmp_path):
    """레퍼런스의 "자료: 출처" 표기(2026-10-17): 왼쪽 아래에만 그리고, 출처가 없으면 그리지 않는다."""
    from PIL import Image

    from app.services.slide_overlay import add_source_credit

    path = tmp_path / "overlay.png"
    Image.new("RGBA", (1920, 1080), (0, 0, 0, 0)).save(path)

    assert add_source_credit(str(path), "") is False
    assert Image.open(path).getbbox() is None

    assert add_source_credit(str(path), "서울신문 · 위키트리") is True
    box = Image.open(path).getbbox()
    assert box is not None
    assert box[0] < 120 and box[2] < 700          # 왼쪽 가장자리에 붙어 있다
    assert box[1] > 800                           # 화면 아래쪽


def test_key_text_that_repeats_a_subtitle_line_is_rejected_but_keywords_pass():
    """자막과 강조 텍스트가 같은 말이면(운영자 피드백) 거절하고, 자막 줄의 일부 핵심어는 통과한다."""
    chunks = [{"text": "긴급 진단에 나섰습니다.", "start": 0.0, "end": 2.0}]
    briefs = {1: {"narration": "긴급 진단에 나섰습니다.", "chunks": chunks}}

    repeated = validate_overlay_plan([{"index": 1, "type": "key_text", "text": "긴급 진단에 나섰습니다"}], briefs)
    keyword = validate_overlay_plan([{"index": 1, "type": "key_text", "text": "긴급 진단"}], briefs)

    assert repeated == {}
    assert keyword[1]["text"] == "긴급 진단"
