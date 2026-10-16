"""슬라이드형 영상에는 캐릭터 채널의 금화 배지(우상단 워터마크)를 달지 않는다(2026-10-16 운영자 결정: 그냥 없애기)."""
from app.workers.longform_worker import _is_slide_job


def test_slide_scenes_are_detected_by_generation_method_or_visual_type():
    assert _is_slide_job([{"generation_method": "slide"}, {"visual_type": "slide_stock_video"}]) is True
    assert _is_slide_job([{"visual_type": "slide_sequence"}]) is True


def test_character_scenes_keep_the_watermark():
    assert _is_slide_job([{"generation_method": "gemini"}, {"visual_type": "character"}]) is False
    assert _is_slide_job([]) is False
