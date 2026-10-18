"""AI 배경 검수(2026-10-18 작업 20 재현): 스톡 대체 AI 배경이 영어 글자가 든 만화 이미지로 나왔다("sell on good news concept market" 검색어의 'concept'가
만화풍을 불렀고 후속 확인이 없었다). 만든 배경이 실사 사진이고 읽히는 글자가 없는지 Claude가 확인하고, 아니면 한 번 더 엄격하게 만들고, 그래도 아니면 쓰지 않는다."""
import json
from pathlib import Path

import pytest
from PIL import Image

from app.services.slide_background import BackgroundRejected, assess_background, ensure_photo_background, sanitize_photo_query


def _vision(replies):
    calls = []

    def call(system, text, images):
        calls.append(text)
        return json.dumps(replies[min(len(calls) - 1, len(replies) - 1)])

    call.calls = calls
    return call


def test_assess_accepts_a_plain_photo_and_rejects_cartoons_or_visible_text(tmp_path):
    image = tmp_path / "bg.png"
    Image.new("RGB", (64, 36), (90, 90, 90)).save(image)
    assert assess_background(str(image), vision_call=_vision([{"photo": True, "has_text": False, "reason": "ok"}])) == (True, "ok")
    ok, reason = assess_background(str(image), vision_call=_vision([{"photo": False, "has_text": True, "reason": "만화 마스코트와 영어 글자"}]))
    assert ok is False and "만화" in reason
    ok, _ = assess_background(str(image), vision_call=_vision([{"photo": True, "has_text": True, "reason": "간판 글자"}]))
    assert ok is False


def test_failed_check_call_is_not_treated_as_approval(tmp_path):
    image = tmp_path / "bg.png"
    Image.new("RGB", (64, 36), (90, 90, 90)).save(image)

    def broken(system, text, images):
        raise RuntimeError("down")

    ok, reason = assess_background(str(image), vision_call=broken)
    assert ok is False and "확인 실패" in reason


def test_abstract_words_that_invite_cartoons_are_removed_from_the_photo_query():
    cleaned = sanitize_photo_query("sell on good news concept market trader illustration infographic")
    for word in ("concept", "illustration", "infographic"):
        assert word not in cleaned


def test_photo_is_regenerated_once_more_strictly_and_then_rejected(tmp_path):
    out = tmp_path / "bg.png"
    made = []

    def make(prompt, path, strict):
        made.append(strict)
        Image.new("RGB", (64, 36), (90, 90, 90)).save(path)

    ensure_photo_background(make, str(out), vision_call=_vision([{"photo": False, "has_text": False, "reason": "cartoon"},
                                                                 {"photo": True, "has_text": False, "reason": "ok"}]), prompt="p")
    assert made == [False, True]                                  # 두 번째는 엄격한 프롬프트

    made.clear()
    with pytest.raises(BackgroundRejected):
        ensure_photo_background(make, str(out), vision_call=_vision([{"photo": False, "has_text": True, "reason": "text"}]), prompt="p")
    assert made == [False, True]                                  # 재시도는 한 번뿐
