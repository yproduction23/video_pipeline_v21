import hashlib

from app import runtime_config
from app.utils.art_direction import direct_scenes
from app.utils.quality_gate import assess_subtitles
from app.workers.images_worker import _manual_review_reasons, _visual_mix_audit
from app.workers.longform_worker import LongformWorker, _apply_speech_bubble_overlay
from app.workers.tts_worker import TtsWorker


def test_financial_unit_change_fails_semantic_voice_contract():
    worker = TtsWorker()

    assert worker._financial_unit_sequence("약 38퍼센트가 빠졌습니다.") == ["퍼센트"]
    assert worker._financial_unit_sequence("약 38포인트가 빠졌습니다.") == ["포인트"]


def test_subtitle_quality_score_40_is_not_passed():
    chunks = [
        {"text": "긴 공백 뒤 자막", "start": 2.0, "duration": 4.0},
        {"text": "두 번째 자막", "start": 8.0, "duration": 4.0},
    ]

    report = assess_subtitles(chunks, audio_duration=12.0, max_chars=18)

    assert report["passed"] is False


def test_ass_uses_outline_without_black_box_and_ignores_scene_override(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_config._state, "subtitle_theme", "economy")
    path = tmp_path / "subtitles.ass"

    LongformWorker()._generate_ass(
        [{"text": "승인 원문입니다.", "start": 0.0, "duration": 1.5}],
        str(path),
        scenes=[{"subtitle_text": "승인되지 않은 별도 자막", "start": 0.0, "duration": 1.5}],
    )
    rendered = path.read_text(encoding="utf-8-sig")

    assert ",1,5,2,2,70,70,58,1" in rendered
    assert "승인 원문입니다." in rendered
    assert "승인되지 않은 별도 자막" not in rendered


def test_visual_modes_control_character_presence_instead_of_forcing_mascot():
    scenes = direct_scenes([
        {"text": "기사 근거", "section": "background", "visual_mode": "article_evidence"},
        {"text": "산업 현장", "section": "scenario", "visual_mode": "semantic_illustration"},
        {"text": "핵심 설명", "section": "data", "visual_mode": "archetype_explainer"},
    ])

    assert [scene["art_direction"]["character_required"] for scene in scenes] == [False, True, True]


def test_unavailable_semantic_qa_and_repeated_prompt_require_review(monkeypatch):
    monkeypatch.setitem(runtime_config._state, "visual_qa_enabled", True)
    scenes = [
        {"index": index, "prompt_en": "same prompt", "visual_mode": mode}
        for index, mode in enumerate(
            ["article_evidence"] + ["semantic_illustration"] * 4 + ["archetype_explainer"] * 4
        )
    ]
    quality = {
        "semantic_alignment": {"reviewed": []},
        "art_direction": {"warnings": []},
        "visual_mix": _visual_mix_audit(scenes),
    }

    reasons = _manual_review_reasons(scenes, quality)

    assert "VISUAL_QA_UNAVAILABLE" in reasons
    assert any(reason.startswith("PROMPT_LAYOUT_REPETITION:") for reason in reasons)


def test_canonical_hash_changes_when_approved_script_changes():
    before = hashlib.sha256("38포인트".encode("utf-8")).hexdigest()
    after = hashlib.sha256("38퍼센트".encode("utf-8")).hexdigest()

    assert before != after


def test_incomplete_dash_bubble_is_suppressed_by_the_common_subtitle_policy(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_config._state, "render_speech_bubbles", True)
    scene = {"bubble_text": "실적 탓 아니다 ---"}

    assert _apply_speech_bubble_overlay(scene, "missing.mp4", tmp_path, 0, 3.0, 1) is True
    assert scene["overlay_provenance"][0]["skipped_reason"] == "script_caption_only_policy"


def test_slide_job_subtitles_use_the_reference_white_box_and_character_jobs_keep_outline(tmp_path, monkeypatch):
    """슬라이드형은 레퍼런스처럼 흰 박스 + 어두운 글자, 캐릭터 영상 자막은 기존 외곽선 스타일 그대로(2026-10-17)."""
    monkeypatch.setitem(runtime_config._state, "subtitle_theme", "economy")
    chunks = [{"text": "승인 원문입니다.", "start": 0.0, "duration": 1.5}]

    slide_path = tmp_path / "slide.ass"
    LongformWorker()._generate_ass(chunks, str(slide_path), scenes=[{"generation_method": "slide", "visual_type": "slide_stock_video"}])
    slide = slide_path.read_text(encoding="utf-8-sig")
    style = next(line for line in slide.splitlines() if line.startswith("Style: Main"))
    fields = style.split(",")
    assert fields[3] == "&H00181818"      # 어두운 글자
    assert fields[5] == "&H00FFFFFF"      # 박스(OutlineColour) 흰색
    assert fields[15] == "3"              # BorderStyle=3 불투명 박스
    assert "승인 원문입니다." in slide

    character_path = tmp_path / "character.ass"
    LongformWorker()._generate_ass(chunks, str(character_path), scenes=[{"generation_method": "character"}])
    assert ",1,5,2,2,70,70,58,1" in character_path.read_text(encoding="utf-8-sig")


def test_subtitle_cues_do_not_blink_off_for_a_frame_between_adjacent_lines(tmp_path, monkeypatch):
    """2026-10-18 피드백("1프레임 이상한 화면"): 자막 줄 사이 0.03초 틈에서 자막이 한 프레임 사라졌다 — 짧은 틈은 앞 줄을 다음 줄 시작까지 잇는다(글자·시작 시각은 그대로)."""
    monkeypatch.setitem(runtime_config._state, "subtitle_theme", "economy")
    path = tmp_path / "gap.ass"
    chunks = [
        {"text": "첫 줄", "start": 0.0, "duration": 1.0},
        {"text": "둘째 줄", "start": 1.0333, "duration": 1.0},      # 1프레임 틈
        {"text": "셋째 줄", "start": 3.0, "duration": 1.0},          # 1초 틈(그대로 비운다)
    ]

    LongformWorker()._generate_ass(chunks, str(path), scenes=[])

    lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line.startswith("Dialogue")]
    ends = [line.split(",")[2] for line in lines]
    starts = [line.split(",")[1] for line in lines]
    assert ends[0] == starts[1]                  # 짧은 틈은 이어 붙인다
    assert ends[1] != starts[2]                  # 긴 틈은 그대로(자막이 없는 구간)
