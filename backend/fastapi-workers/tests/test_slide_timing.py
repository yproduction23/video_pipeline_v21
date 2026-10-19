"""모션그래픽 등장 시각을 자막 줄(TTS 청크) 시각에 맞춘다(2026-10-17 운영자 피드백: "편집과 싱크가 안 맞아 모션그래픽이 빨리 나온다").
그래픽 글자가 자막 줄을 그대로 되풀이하지 않는지도 같은 청크 정보로 판단한다."""
from app.services.slide_timing import duplicates_subtitle, phrase_offset, scene_chunks, sentence_end_offset

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


def test_sentence_end_offset_uses_the_chunk_that_finishes_the_sentence():
    narration = "뉴욕에서 투매가 나왔습니다. 서울도 급락했습니다."
    chunks = [{"text": "뉴욕에서 투매가 나왔습니다.", "start": 0.0, "end": 2.0}, {"text": "서울도 급락했습니다.", "start": 2.0, "end": 4.0}]

    assert sentence_end_offset(narration, chunks, 4.0, "뉴욕에서 투매가") == 2.0
    assert sentence_end_offset(narration, chunks, 4.0, "서울도") == 4.0
    # 자막 줄 시각이 없으면 글자 비율로 근사한다
    assert 1.5 < sentence_end_offset(narration, [], 4.0, "뉴욕에서") < 2.5
    # 구절을 찾지 못하면 장면 끝까지 남긴다(일찍 지우지 않는다)
    assert sentence_end_offset(narration, chunks, 4.0, "없는 구절") == 4.0


def test_a_phrase_spanning_two_subtitle_lines_is_still_a_duplicate():
    """2026-10-18 운영자 피드백(4:17): 자막 두 줄에 걸친 문장을 빨간 글씨로 그대로 또 썼는데 줄마다 따로 비교해 통과했다."""
    chunks = [{"text": "리서치 센터장들이 공통으로"}, {"text": "꼽은 첫 번째 핵심"}, {"text": "변수는 미국 금리입니다."}]

    assert duplicates_subtitle("첫 번째 핵심 변수는 미국 금리", chunks) is True          # 줄 둘에 걸친 복사
    assert duplicates_subtitle("미국 금리", chunks) is False                           # 짧은 핵심어는 복사가 아니다
    assert duplicates_subtitle("공통으로", chunks) is False


def test_scene_windows_follow_the_tts_contract_like_the_assembly_does():
    """2026-10-19 20번 Job: 이미지 단계의 장면 길이(추정, 합계 300초)가 조립의 TTS 구간(289.9초)과 달라 후반 장면의 자막 줄 시각이 0초로 깨졌다."""
    from app.services.slide_timing import apply_tts_windows, scene_chunks, tts_windows

    scenes = [
        {"duration": 4.0, "caption_chunk_contract": {"chunk_start_index": 0, "chunk_end_index": 1, "end": 3.6}},
        {"duration": 5.0, "caption_chunk_contract": {"chunk_start_index": 2, "chunk_end_index": 3, "end": 7.9}},
    ]
    chunks = [{"text": "가", "start": 0.4, "end": 2.0}, {"text": "나", "start": 2.1, "end": 3.6},
              {"text": "다", "start": 3.97, "end": 5.9}, {"text": "라", "start": 6.1, "end": 7.9}]

    assert tts_windows(scenes) == [(0.0, 3.6), (3.6, 7.9)]                   # 앞 장면이 끝난 시각에서 다음 장면이 시작
    assert apply_tts_windows(scenes) is True
    assert [s["duration"] for s in scenes] == [3.6, 4.3] and scenes[1]["start_seconds"] == 3.6
    rel = scene_chunks(chunks, scenes[1]["caption_chunk_contract"], scenes[1]["start_seconds"])
    assert [(c["start"], c["end"]) for c in rel] == [(0.37, 2.3), (2.5, 4.3)]  # 장면 안 상대 시각이 0으로 깨지지 않는다
    # 계약 시각이 없으면 건드리지 않는다
    assert tts_windows([{"duration": 4.0}]) is None and apply_tts_windows([{"duration": 4.0}]) is False


def test_read_time_grows_with_text_and_items_so_text_never_vanishes_before_it_can_be_read():
    """2026-10-19 운영자 피드백: 글이 읽기 전에 없어진다 — 요소마다 글자 수·항목 수로 읽는 시간을 정한다."""
    from app.services.slide_timing import overlay_read_seconds, read_seconds

    short, long_ = read_seconds("text", {"text": "금리 상승"}), read_seconds("text", {"text": "미국 장기금리가 방향을 틀면"})
    assert 1.8 < short < long_ and long_ > 3.0
    assert read_seconds("list", {"items": ["가", "나", "다"]}) > read_seconds("list", {"items": ["가", "나"]})
    assert read_seconds("flow", {"nodes": [{}, {}, {}]}) > read_seconds("flow", {"nodes": [{}, {}]})
    assert overlay_read_seconds({"type": "key_text", "text": "지수를 끌어내렸습니다"}) > 3.0


def test_motion_part_starts_early_so_its_key_moment_lands_on_the_spoken_phrase():
    """2026-10-19 운영자 결정: 연상 부품은 핵심 장면(진행의 55% 지점)이 말하는 순간에 오도록 먼저 천천히 시작한다 — 말하는 순간에야 시작하면 앞이 비었다."""
    from app.services.slide_timing import PART_ANIM_SECONDS, PART_PEAK_RATIO, part_timing

    start, anim = part_timing(4.2, 5.6)                  # "하방"을 4.2초에 말하는 5.6초 장면(3:44 재현)
    assert anim == PART_ANIM_SECONDS and abs((start + PART_PEAK_RATIO * anim) - 4.2) < 0.05   # 핵심 장면이 말하는 순간
    assert start < 2.2                                                                      # 이전에는 약 4초를 비워 뒀다
    early, _ = part_timing(0.5, 5.0)
    assert early == 0.25                                                                    # 말이 장면 첫머리면 바로 시작
    none_start, _ = part_timing(None, 5.0)
    assert none_start <= 0.35                                                               # 시각 정보가 없으면 기본값
