"""상장사 로고 (2026-10-17 운영자 요청: 아는 회사가 나오면 로고로 보여 주면 더 와닿는다)."""
from __future__ import annotations

import json
import re

from PIL import Image

from app.services import slide_logos
from app.services.slide_edit_plan import assemble_plan
from app.services.slide_sequence import board_runs, design_sequence



def _approving(payload):
    """설계 응답은 payload로, 아이콘 적합성 판정 요청에는 모든 항목을 통과시키는 가짜 LLM."""
    def call(system, messages, max_tokens):
        if "아이콘 검수자" in system:
            ids = re.findall(r"^(\d+)\. 아이콘=", messages[0]["content"], re.M)
            return json.dumps({"ok": [int(i) for i in ids]})
        return json.dumps(payload, ensure_ascii=False)

    return call

def _library(tmp_path, monkeypatch):
    for slug, size in (("samsung", (400, 120)), ("hynix", (300, 300))):
        Image.new("RGBA", size, (20, 40, 160, 255)).save(tmp_path / f"{slug}.png")
    (tmp_path / "library.json").write_text(json.dumps([
        {"slug": "samsung", "label": "삼성전자", "aliases": ["삼성", "Samsung"], "file": "samsung.png"},
        {"slug": "hynix", "label": "SK하이닉스", "aliases": ["하이닉스"], "file": "hynix.png"},
        {"slug": "ghost", "label": "없는파일", "aliases": [], "file": "missing.png"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(slide_logos, "LOGO_DIR", tmp_path)
    slide_logos._library.cache_clear()


def test_logos_resolve_only_on_an_exact_name_or_alias_and_skip_missing_files(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)

    assert slide_logos.resolve_logo("삼성전자") == "삼성전자"
    assert slide_logos.resolve_logo("삼성") == "삼성전자" and slide_logos.resolve_logo("samsung") == "삼성전자"
    assert slide_logos.resolve_logo("logo:하이닉스") == "SK하이닉스"
    assert slide_logos.resolve_logo("삼성전기") is None  # 비슷한 이름으로 다른 회사 로고를 쓰지 않는다
    assert slide_logos.resolve_logo("없는파일") is None  # 파일이 없으면 등록되지 않는다
    assert slide_logos.logo_labels() == ["삼성전자", "SK하이닉스"]
    assert "삼성전자" in slide_logos.logo_prompt_hint()


def test_logo_sprite_has_no_white_card_and_turns_dark_neutral_marks_white(tmp_path, monkeypatch):
    """2026-10-17 운영자 피드백: "누끼 따서 흰 배경 없이". 배경은 투명, 검은 로고는 흰색으로 바꿔 어두운 화면에서 읽히게 한다."""
    _library(tmp_path, monkeypatch)
    Image.new("RGBA", (300, 300), (255, 255, 255, 255)).save(tmp_path / "hynix.png")  # 흰 배경 PNG
    image = Image.open(tmp_path / "hynix.png")
    for x in range(100, 200):
        for y in range(100, 200):
            image.putpixel((x, y), (10, 10, 10, 255))  # 검은 마크
    image.save(tmp_path / "hynix.png")
    slide_logos._library.cache_clear()

    colored, dark = slide_logos.logo_sprite("삼성전자"), slide_logos.logo_sprite("SK하이닉스")

    assert colored.getpixel((2, 2))[3] == 0 and dark.getpixel((2, 2))[3] == 0  # 모서리는 투명(흰 카드 없음)
    center = dark.getpixel((dark.width // 2, dark.height // 2))
    assert center[:3] == (255, 255, 255) and center[3] == 255  # 검은 마크는 흰색으로
    brand = colored.getpixel((colored.width // 2, colored.height // 2))
    assert brand[:3] == (20, 40, 160)  # 색이 있는 로고는 색을 지킨다
    assert slide_logos.logo_sprite("없는회사") is None


def test_a_compare_pair_of_two_companies_draws_logos_in_the_icon_slots(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    narrations = ["삼성전자와 하이닉스가 함께 외국인 매도의 중심이었습니다."]
    briefs = {0: {"narration": narrations[0], "context": "", "duration": 8.0}}
    plan = assemble_plan(briefs, [{"index": 0, "background": "board", "overlay": {"type": "key_text", "text": "삼성전자와 하이닉스가"}}], job_id=1)
    topic = {"template": "compare_pair", "scene": 0, "anchor": "삼성전자와", "op": "+",
             "a": {"label": "삼성전자", "icon": "삼성전자"}, "b": {"label": "하이닉스", "icon": "SK하이닉스", "anchor": "하이닉스가"}}

    seq = design_sequence(board_runs(plan)[0], plan, llm_call=_approving({"topics": [topic]}))

    icons = [e for e in seq["elements"] if e["kind"] == "icon"]
    assert sorted(e["content"]["name"] for e in icons) == ["logo:SK하이닉스", "logo:삼성전자"]
    assert icons[0]["pos"][1] == icons[1]["pos"][1]
