"""job 없음(2026-09-22): 화면 문구 거부 하나가 재분할 후 여러 화면 전부에 복제되어
해설형·창작형(그리고 원리상 사실형도) 대본이 실제로는 문제없는데도 수동 검토로
넘어가던 문제를 재현하고 고정한다.

pace_sections_for_runtime이 bucket[0]의 screen_text_validation/scene_rejected를
그대로 물려주는 대신 screen_texts만 병합하고, script_worker._revalidate_paced_scenes가
병합된 화면 기준으로 판정을 다시 계산해야 한다.
"""
from __future__ import annotations

from app.utils.script_delivery import pace_sections_for_runtime
from app.workers.script_worker import _revalidate_paced_scenes


def _scene(content, screen_texts=None, scene_rejected=False, bubble_text=""):
    return {
        "content": content,
        "text": content,
        "screen_texts": screen_texts or [],
        "screen_text_validation": {"passed": not scene_rejected, "reasons": ["stale"] if scene_rejected else []},
        "bubble_text": bubble_text,
        "bubble_validation": {"passed": True, "reasons": [], "matched_sources": [], "numeric_tokens": []},
        "scene_rejected": scene_rejected,
    }


def test_pacing_does_not_clone_one_scenes_rejection_onto_every_split_fragment():
    # 긴 씬 하나가 여러 문장으로 쪼개질 만큼 길고, 원래 씬은 (버그가 있던) 거부 상태다.
    long_content = " ".join([f"이것은 {i}번째 문장입니다." for i in range(1, 9)])
    source = [_scene(long_content, screen_texts=["잘못된 문구"], scene_rejected=True)]

    paced = pace_sections_for_runtime(source, target_seconds=40, min_seconds=2, target_seconds_per_scene=3, max_seconds=4)

    assert len(paced) > 1  # 실제로 여러 화면으로 쪼개졌는지 확인(재현 전제)
    assert all(scene["scene_rejected"] is False for scene in paced)
    assert all(scene["screen_text_validation"] is None for scene in paced)  # 재계산 전 표시값


def test_pacing_merges_screen_texts_from_every_fragment_in_a_bucket():
    source = [
        _scene("첫 조각입니다.", screen_texts=["첫 문구"]),
        _scene("둘째 조각입니다.", screen_texts=["둘째 문구"]),
    ]

    paced = pace_sections_for_runtime(source, target_seconds=10, min_seconds=1, target_seconds_per_scene=100, max_seconds=200)

    assert len(paced) == 1
    assert paced[0]["screen_texts"] == ["첫 문구", "둘째 문구"]


def test_revalidate_recomputes_against_merged_content_not_stale_flag():
    # 병합 이후 화면 문구가 실제 병합 대사에 있으면 통과해야 한다(예전엔 원래 씬의
    # 오래된 거부 판정이 그대로 남아 있었다).
    scenes = [{
        "content": "캐치캐치 안무를 예나가 직접 만들었습니다.",
        "screen_texts": ["캐치캐치"],
        "screen_text_validation": None,
        "bubble_text": "",
        "bubble_validation": None,
        "scene_rejected": False,
    }]

    result = _revalidate_paced_scenes(scenes)

    assert result[0]["scene_rejected"] is False
    assert result[0]["screen_text_validation"]["passed"] is True


def test_revalidate_still_rejects_text_not_grounded_in_merged_content():
    scenes = [{
        "content": "캐치캐치 안무를 예나가 직접 만들었습니다.",
        "screen_texts": ["다른 화면에서 온 문구"],
        "screen_text_validation": None,
        "bubble_text": "",
        "bubble_validation": None,
        "scene_rejected": False,
    }]

    result = _revalidate_paced_scenes(scenes)

    assert result[0]["scene_rejected"] is True
    assert "screen_text_not_verbatim" in result[0]["screen_text_validation"]["reasons"][0]


def test_revalidate_handles_empty_screen_texts_and_bubble():
    scenes = [_scene("아무 문구도 없는 화면입니다.")]

    result = _revalidate_paced_scenes(scenes)

    assert result[0]["scene_rejected"] is False
    assert result[0]["screen_text_validation"]["passed"] is True
