"""모션그래픽 등장 시각을 자막 줄(TTS 청크) 시각에 맞춘다(2026-10-17 운영자 피드백: "편집과 싱크가 안 맞아 모션그래픽이 빨리 나온다").
그래픽 글자가 자막 줄을 그대로 되풀이하지 않는지도 같은 청크 정보로 판단한다."""
from app.services.slide_timing import duplicates_subtitle, phrase_offset, scene_chunks

TTS_CHUNKS = [
    {"index": 0, "text": "앞 장면입니다", "start": 0.0, "end": 2.0},
    {"index": 1, "text": "외국인은 8거래일 연속", "start": 10.0, "end": 12.0},
    {"index": 2, "text": "코스피를 팔았습니다", "start": 12.0, "end": 14.0},
]
MAPPING = {"chunk_start_index": 1, "chunk_end_index": 2}


def test_scene_chunks_are_relative_to_the_scene_start():
    chunks = scene_chunks(TTS_CHUNKS, MAPPING, scene_start=10.0)

    assert chunks == [
        {"text": "외국인은 8거래일 연속", "start": 0.0, "end": 2.0},
        {"text": "코스피를 팔았습니다", "start": 2.0, "end": 4.0},
    ]


def test_scene_chunks_without_mapping_or_chunks_are_empty():
    assert scene_chunks([], MAPPING, 0.0) == []
    assert scene_chunks(TTS_CHUNKS, None, 0.0) == []


def test_phrase_offset_is_the_time_the_phrase_is_spoken_not_the_scene_start():
    chunks = scene_chunks(TTS_CHUNKS, MAPPING, scene_start=10.0)

    assert phrase_offset(chunks, "코스피를") >= 2.0           # 둘째 줄에서 말한다
    assert phrase_offset(chunks, "외국인은") < 0.5            # 첫 줄 처음
    assert phrase_offset(chunks, "8거래일") < phrase_offset(chunks, "코스피를")
    assert phrase_offset(chunks, "없는 구절") is None


def test_phrase_offset_handles_a_phrase_that_spans_two_chunks_and_whitespace_differences():
    chunks = scene_chunks(TTS_CHUNKS, MAPPING, scene_start=10.0)

    assert 1.0 < phrase_offset(chunks, "연속 코스피를") < 3.0
    assert phrase_offset(chunks, "8 거래일") is not None


def test_overlay_text_that_covers_a_subtitle_line_is_a_duplicate():
    chunks = [{"text": "긴급 진단에 나섰습니다.", "start": 0.0, "end": 2.0},
              {"text": "수급 불안이라는 진단이었습니다.", "start": 2.0, "end": 4.0}]

    assert duplicates_subtitle("긴급 진단에 나섰습니다", chunks)          # 자막 줄 그대로
    assert duplicates_subtitle("긴급 진단에 나섰", chunks)                # 거의 전부
    assert not duplicates_subtitle("긴급 진단", chunks)                   # 핵심어만
    assert not duplicates_subtitle("수급 불안", chunks)
    assert not duplicates_subtitle("긴급 진단에 나섰습니다", [])          # 비교할 자막이 없으면 막지 않는다
