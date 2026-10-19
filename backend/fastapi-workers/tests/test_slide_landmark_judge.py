"""외국 랜드마크 검사(2026-10-19 운영자 지적): 한국 증시 문장에 타이베이 101·이스탄불 모스크가 나왔다 — 고른 스톡 프레임을 예/아니오로 한 번 더 확인한다."""
import json

from PIL import Image

from app.services.slide_landmark_judge import looks_foreign


def _frame(tmp_path):
    path = tmp_path / "f.png"
    Image.new("RGB", (64, 36), (90, 90, 90)).save(path)
    return str(path)


def test_a_frame_with_a_recognisable_foreign_landmark_is_rejected(tmp_path):
    ok, reason = looks_foreign(_frame(tmp_path), "코스피 낙폭이 글로벌 증시 대비 컸습니다.",
                               vision_call=lambda s, t, i: json.dumps({"foreign": True, "reason": "타이베이 101"}))

    assert ok is True and "타이베이" in reason


def test_a_generic_or_unclear_frame_passes_and_a_failed_check_never_blocks(tmp_path):
    frame = _frame(tmp_path)

    assert looks_foreign(frame, "x", vision_call=lambda s, t, i: json.dumps({"foreign": False, "reason": "일반 풍경"}))[0] is False

    def broken(system, text, images):
        raise RuntimeError("크레딧 부족")

    blocked, reason = looks_foreign(frame, "x", vision_call=broken)
    assert blocked is False and "검사 실패" in reason          # 확인 호출이 실패해도 스톡 선택을 막지 않는다
