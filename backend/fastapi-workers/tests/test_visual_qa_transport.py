import base64
import io
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import pytest
from PIL import Image
import app.utils.visual_qa as visual_qa_module

from app.utils.visual_qa import (
    _cached_review_policy_compatible,
    _encode_review_image,
    _post_visual_review,
    assess_local_edit_preservation,
    assess_visual_alignment,
)


@pytest.fixture(autouse=True)
def _reset_visual_qa_circuit():
    visual_qa_module._VISUAL_QA_GEMINI_OPEN_UNTIL = 0.0
    yield
    visual_qa_module._VISUAL_QA_GEMINI_OPEN_UNTIL = 0.0


def test_policy17_rechecks_policy16_review_without_proportion_and_occurrence_fix():
    assert not _cached_review_policy_compatible({
        "policy_version": 16,
        "failure_categories": [],
        "raw": {"detached_translucent_text_card_present": False},
    })


def test_policy17_rechecks_policy14_detached_card_verdict():
    assert not _cached_review_policy_compatible({
        "policy_version": 14,
        "failure_categories": ["text_surface_detached_translucent_card"],
        "raw": {"detached_translucent_text_card_present": True},
    })


def _visual_response(verdict: dict) -> Mock:
    response = Mock(status_code=200)
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": __import__("json").dumps(verdict)}]}}]
    }
    return response


def _accepted_verdict() -> dict:
    return {
        "scene_match": 95,
        "finance_specificity": 94,
        "composition": 92,
        "style_adherence": 93,
        "text_integrity": 100,
        "character_identity": 95,
        "repetition_risk": 5,
        "visible_texts": [],
        "unexpected_or_malformed_texts": [],
        "missing_approved_texts": [],
        "speech_bubble_present": False,
        "anatomy_pass": True,
        "extra_limbs_or_hands": False,
        "main_mascot_anatomy_pass": True,
        "main_mascot_extra_limbs_or_hands": False,
        "coin_disc_dominant_silhouette": True,
        "compact_leg_proportion_match": True,
        "costume_wrap_preserves_coin_dominance": True,
        "wardrobe_match": True,
        "wardrobe_role_match": True,
        "face_identity_match": True,
        "face_construction_quality_pass": True,
        "separate_sclera_region_visible": True,
        "warm_brown_iris_region_visible": True,
        "expression_role_match": True,
        "style_family_match": True,
        "scene_information_density_match": True,
        "deterministic_surface_scale_match": True,
        "scene_causal_story_match": True,
        "minimal_dot_eye_face": False,
        "detached_translucent_text_card_present": False,
        "detached_unmounted_text_card_present": False,
        "text_surface_has_visible_physical_mount_or_frame": True,
        "text_surface_is_visibly_transparent_or_holographic": False,
        "text_surface_integrated_with_set": True,
        "visual_medium_violation": False,
        "semantic_contradiction": False,
        "malformed_or_factual_text_error": False,
        "unsupported_numeric_or_factual_values": [],
        "unexpected_or_ambiguous_props": [],
        "unexpected_text_regions": [],
        "white_sticker_halo_present": False,
        "missing_required_props": [],
        "number_panel_only": False,
        "decision": "accept",
        "reason": "장면 계약 일치",
    }


def test_visual_qa_rejects_an_unexplained_weapon_like_or_ambiguous_prop(tmp_path: Path):
    image = tmp_path / "ambiguous-prop.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "unexpected_or_ambiguous_props": ["캐릭터 오른손의 총·무전기·레버처럼 보이는 검은 물체"],
        "decision": "review",
        "reason": "소품의 역할과 부착 관계를 판별할 수 없음",
    })
    scene = {
        "index": 28,
        "image_path": str(image),
        "text": "대형주의 흐름을 점검합니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "unexpected_or_ambiguous_prop" in report["reviewed"][0]["failure_categories"]
    assert report["accuracy_metrics"]["scenes"][0]["items"]["unexpected_visual_anomaly"] == "fail"


def test_visual_qa_rejects_long_human_proportions_but_not_job52_costume_wrap(tmp_path: Path):
    image = tmp_path / "long-legs.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "coin_disc_dominant_silhouette": True,
        "compact_leg_proportion_match": False,
        "costume_wrap_preserves_coin_dominance": True,
        "decision": "review",
        "reason": "의상은 허용 범위지만 다리가 동전 지름보다 길다",
    })
    scene = {
        "index": 7,
        "image_path": str(image),
        "scene_spec": {"character_costume": "laboratory coat"},
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    failures = report["reviewed"][0]["failure_categories"]
    assert "character_leg_proportion" in failures
    assert "character_costume_wrap" not in failures


def test_visual_qa_no_longer_hard_fails_on_sclera_iris_separation(tmp_path: Path):
    """2026-09-29 사용자 결정: 눈 구조(공막·홍채 분리) 요구사항은 채널
    운영자가 필요 없다고 판단해 하드 실패 대상에서 제외했다. 다른 캐릭터
    해부학 검사(다리 비율, 의상 등)는 그대로 유지한다."""
    image = tmp_path / "black-oval-catchlight.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "face_construction_quality_pass": True,
        "separate_sclera_region_visible": False,
        "warm_brown_iris_region_visible": False,
        "decision": "review",
        "reason": "검은 타원 안 캐치라이트만 있고 흰자와 갈색 홍채가 분리되지 않음",
    })
    scene = {
        "index": 7,
        "image_path": str(image),
        "scene_spec": {"character_costume": "laboratory coat and goggles"},
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    failures = report["reviewed"][0]["failure_categories"]
    assert "character_sclera_structure" not in failures
    assert "character_warm_brown_iris" not in failures


def test_visual_qa_prompt_no_longer_asks_reviewer_to_require_sclera_iris_layers(tmp_path: Path):
    """2026-09-29: hard_failures 매핑만 지우면 리뷰어 LLM이 여전히 공막/홍채를
    검사 기준에 포함시켜 decision=review/score<78로 재시도를 유발할 수 있다
    (job 12에서 실제로 재현됨). 리뷰 프롬프트 자체에서 이 요구사항을 지워야
    한다."""
    image = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    scene = {
        "index": 0,
        "image_path": str(image),
        "art_direction": {"character_required": True},
    }
    captured = {}

    def fake_post(payload, api_key):
        captured["prompt"] = payload["contents"][0]["parts"][0]["text"]
        return _visual_response(_accepted_verdict())

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        side_effect=fake_post,
    ):
        assess_visual_alignment([scene], enabled=True, max_scenes=1)

    prompt = captured["prompt"]
    assert "do not require a separated white sclera" in prompt.lower()
    assert "separately inspect the broad white sclera" not in prompt
    assert "solid black oval is not sclera and fails" not in prompt


def test_explicit_max_occurrences_one_rejects_two_visible_approved_labels(tmp_path: Path):
    image = tmp_path / "duplicate-label.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({"visible_texts": ["대형주", "대형주"]})
    scene = {
        "index": 28,
        "image_path": str(image),
        "text": "대형주 흐름을 살펴봅니다.",
        "screen_texts": ["대형주"],
        "screen_text_plan": [{
            "text": "대형주",
            "purpose": "decorative",
            "max_occurrences": 1,
        }],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert reviewed["raw"]["approved_text_occurrence_counts"] == {"대형주": 2}
    assert "text_approved_duplicate" in reviewed["failure_categories"]


def test_visual_qa_enforces_common_floor_while_preserving_scene_variation(tmp_path: Path):
    image = tmp_path / "role-specific.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "face_construction_quality_pass": False,
        "expression_role_match": False,
        "wardrobe_role_match": False,
        "style_family_match": False,
        "scene_information_density_match": False,
        "deterministic_surface_scale_match": False,
        "scene_causal_story_match": False,
        "decision": "review",
    })
    scene = {
        "index": 7,
        "image_path": str(image),
        "screen_texts": ["143조 원"],
        "screen_text_validation": {"passed": True},
        "scene_spec": {
            "character_costume": "semiconductor laboratory coat",
            "character_emotion": "curious surprise",
            "character_action": "examining two chips",
        },
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert set(report["reviewed"][0]["failure_categories"]) >= {
        "character_face_quality",
        "character_expression_role",
        "character_wardrobe",
        "style_family_mismatch",
        "scene_information_density",
        "deterministic_surface_oversized",
        "scene_semantic_underexplained",
    }


def test_review_copy_is_compact_jpeg_without_changing_source(tmp_path: Path):
    source = tmp_path / "scene.png"
    Image.new("RGB", (2400, 1350), "navy").save(source)
    original = source.read_bytes()

    encoded = _encode_review_image(source)
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as review:
        assert review.format == "JPEG"
        assert max(review.size) <= 1280

    assert source.read_bytes() == original


def test_transient_timeout_is_retried_once():
    ok = Mock(status_code=200)
    with patch(
        "app.utils.visual_qa.requests.post",
        side_effect=[requests.Timeout("lost"), ok],
    ) as post, patch("app.utils.visual_qa.time.sleep"):
        response = _post_visual_review({"contents": []}, "key")

    assert response is ok
    assert post.call_count == 2
    assert "models/gemini-3.7-flash:generateContent" in post.call_args.args[0]


def test_client_contract_error_is_not_retried():
    bad = Mock(status_code=400)
    with patch("app.utils.visual_qa.requests.post", return_value=bad) as post:
        response = _post_visual_review({"contents": []}, "key")

    assert response is bad
    assert post.call_count == 1


def test_open_gemini_qa_circuit_uses_claude_without_waiting_for_gemini(tmp_path: Path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (320, 180), "navy").save(image)
    scene = {"index": 0, "image_path": str(image), "art_direction": {"character_required": True}}

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch.object(
        visual_qa_module,
        "_VISUAL_QA_GEMINI_OPEN_UNTIL",
        visual_qa_module.time.monotonic() + 60,
    ), patch(
        "app.utils.visual_qa._post_visual_review",
        side_effect=AssertionError("열린 회로에서 Gemini를 호출하면 안 됨"),
    ), patch(
        "app.utils.visual_qa._claude_visual_review",
        return_value=_accepted_verdict(),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert report["reviewed"][0]["failure_categories"] == []


def test_local_edit_preservation_rejects_new_unrelated_prop(tmp_path: Path):
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (1920, 1080), "navy").save(source)
    Image.new("RGB", (1920, 1080), "gold").save(candidate)
    verdict = {
        "composition_preserved": False,
        "camera_and_crop_preserved": True,
        "character_face_preserved": True,
        "character_pose_preserved": True,
        "background_preserved": True,
        "unrelated_props_preserved": False,
        "style_preserved": True,
        "only_requested_changes": False,
        "added_unrequested_objects": ["노트북"],
        "removed_unrequested_objects": ["태블릿"],
        "changed_unrequested_regions": ["전경 회의 테이블"],
        "preservation_score": 72,
        "decision": "reject",
        "reason": "문자 수정 범위를 넘어 전경 소품이 교체됨",
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ) as post:
        result = assess_local_edit_preservation(
            str(source), str(candidate),
            allowed_change_categories=["text_unexpected_or_malformed"],
        )

    assert result["status"] == "completed"
    assert result["passed"] is False
    assert result["added_unrequested_objects"] == ["노트북"]
    payload_parts = post.call_args.args[0]["contents"][0]["parts"]
    assert len(payload_parts) == 3


def test_local_edit_preservation_accepts_text_surface_only_change(tmp_path: Path):
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (1920, 1080), "navy").save(source)
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    verdict = {
        "composition_preserved": True,
        "camera_and_crop_preserved": True,
        "character_face_preserved": True,
        "character_pose_preserved": True,
        "background_preserved": True,
        "unrelated_props_preserved": True,
        "style_preserved": True,
        "only_requested_changes": True,
        "repaired_surface_clean": True,
        "no_blur_smear_or_ghosting": True,
        "added_unrequested_objects": [],
        "removed_unrequested_objects": [],
        "changed_unrequested_regions": ["문서 안쪽 글자"],
        "preservation_score": 96,
        "decision": "accept",
        "reason": "문서 글자만 제거됨",
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        result = assess_local_edit_preservation(
            str(source), str(candidate),
            allowed_change_categories=["text_unexpected_or_malformed"],
        )

    assert result["passed"] is True


def test_local_edit_preservation_rejects_a_blurred_text_patch(tmp_path: Path):
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (1920, 1080), "navy").save(source)
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    verdict = {
        "composition_preserved": True,
        "camera_and_crop_preserved": True,
        "character_face_preserved": True,
        "character_pose_preserved": True,
        "background_preserved": True,
        "unrelated_props_preserved": True,
        "style_preserved": True,
        "only_requested_changes": True,
        "repaired_surface_clean": False,
        "no_blur_smear_or_ghosting": False,
        "added_unrequested_objects": [],
        "removed_unrequested_objects": [],
        "changed_unrequested_regions": ["게이지 중앙의 흐린 얼룩"],
        "preservation_score": 84,
        "decision": "reject",
        "reason": "삭제 영역이 진흙처럼 번짐",
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        result = assess_local_edit_preservation(
            str(source), str(candidate),
            allowed_change_categories=["text_unexpected_or_malformed"],
        )

    assert result["passed"] is False


def test_scene_visual_qa_uses_claude_only_after_gemini_server_failure(tmp_path: Path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    unavailable = Mock(status_code=503)
    scene = {
        "index": 1,
        "image_path": str(image),
        "text": "주주환원 방식입니다.",
        "screen_texts": ["주주환원"],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "ANTHROPIC_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review", return_value=unavailable,
    ), patch(
        "app.utils.visual_qa._claude_visual_review", return_value=_accepted_verdict(),
    ) as fallback:
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert len(report["reviewed"]) == 1
    assert report["warnings"] == []
    fallback.assert_called_once()


def test_visual_qa_rejects_malformed_korean_extra_hand_and_unapproved_bubble(tmp_path: Path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "visible_texts": ["운영주익"],
        "unexpected_or_malformed_texts": ["운영주익"],
        "malformed_or_factual_text_error": True,
        "speech_bubble_present": True,
        "anatomy_pass": False,
        "extra_limbs_or_hands": True,
        "main_mascot_anatomy_pass": False,
        "main_mascot_extra_limbs_or_hands": True,
        "white_sticker_halo_present": True,
        "missing_required_props": ["physical balance"],
        "decision": "review",
        "reason": "승인 문구 훼손과 세 번째 손",
    })
    scene = {
        "index": 7,
        "image_path": str(image),
        "text": "코스피 영업이익을 설명합니다.",
        "screen_texts": ["영업이익"],
        "art_direction": {
            "character_required": True,
            "wardrobe": "navy finance-presenter suit",
            "required_props": ["physical balance"],
            "forbid_white_sticker_halo": True,
        },
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=64)

    reviewed = report["reviewed"][0]
    assert reviewed["retry_recommended"] is True
    assert set(reviewed["failure_categories"]) >= {
        "text_unexpected_or_malformed",
        "speech_bubble_unapproved",
        "character_anatomy",
        "character_extra_limbs",
        "character_white_sticker_halo",
        "scene_required_props_missing",
    }


def test_visual_qa_allows_expressive_pupil_simplification_but_rejects_detached_text_card(tmp_path: Path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "minimal_dot_eye_face": True,
        "detached_translucent_text_card_present": True,
        "detached_unmounted_text_card_present": True,
        "text_surface_has_visible_physical_mount_or_frame": False,
        "text_surface_is_visibly_transparent_or_holographic": True,
        "text_surface_integrated_with_set": False,
        "decision": "review",
        "reason": "얼굴 단순화와 분리된 투명 카드",
    })
    scene = {
        "index": 0,
        "image_path": str(image),
        "text": "영업이익 구조를 설명합니다.",
        "screen_texts": ["영업이익"],
        "screen_text_plan": [{
            "text": "영업이익",
            "surface": "machine-mounted monitor",
            "surface_style": "opaque_equipment_display",
        }],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert set(report["reviewed"][0]["failure_categories"]) >= {
        "text_surface_detached_translucent_card",
    }
    assert "character_face_simplified" not in report["reviewed"][0]["failure_categories"]


def test_visual_qa_keeps_mounted_opaque_stage_board(tmp_path: Path):
    image = tmp_path / "mounted-board.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "visible_texts": ["40조 원"],
        "detached_translucent_text_card_present": True,
        "detached_unmounted_text_card_present": False,
        "text_surface_has_visible_physical_mount_or_frame": True,
        "text_surface_is_visibly_transparent_or_holographic": False,
        "text_surface_integrated_with_set": False,
        "reason": "흰 게시판에 검은 프레임과 두 개의 지지대가 보임",
    })
    scene = {
        "index": 13,
        "image_path": str(image),
        "text": "SK하이닉스는 주주환원에 40조 원을 투입합니다.",
        "screen_texts": ["SK하이닉스", "40조 원"],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "text_surface_detached_translucent_card" not in report["reviewed"][0]["failure_categories"]


def test_supporting_crowd_hands_and_model_guessed_duplicate_do_not_reject_mascot(tmp_path: Path):
    image = tmp_path / "crowd.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "visible_texts": ["주주환원"],
        "approved_text_occurrence_counts": {"주주환원": 2},
        "anatomy_pass": False,
        "extra_limbs_or_hands": True,
        "main_mascot_anatomy_pass": True,
        "main_mascot_extra_limbs_or_hands": False,
        "decision": "review",
        "reason": "전경 관중 손이 보이지만 주인공은 정상",
    })
    scene = {
        "index": 0,
        "image_path": str(image),
        "text": "주주환원 방식입니다.",
        "screen_texts": ["주주환원"],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert "character_extra_limbs" not in reviewed["failure_categories"]
    assert "character_anatomy" not in reviewed["failure_categories"]
    assert "text_approved_duplicate" not in reviewed["failure_categories"]
    assert reviewed["raw"]["approved_text_occurrence_counts"] == {"주주환원": 1}


def test_contextual_unlisted_label_is_not_a_text_error_when_review_says_it_is_coherent(tmp_path: Path):
    image = tmp_path / "context_label.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "visible_texts": ["삼성전자", "STOCK"],
        "unexpected_or_malformed_texts": ["STOCK"],
        "text_integrity": 85,
        "malformed_or_factual_text_error": False,
        "decision": "review",
        "reason": "STOCK은 장면 맥락에 맞는 정상 라벨",
    })
    scene = {
        "index": 10,
        "image_path": str(image),
        "text": "삼성전자 지분 관계를 살펴봅니다.",
        "screen_texts": ["삼성전자"],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "text_unexpected_or_malformed" not in report["reviewed"][0]["failure_categories"]


def test_illustrative_score_and_background_equation_are_not_treated_as_financial_data(tmp_path: Path):
    image = tmp_path / "classroom.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    equation = "z = lg(a²/2)²"
    verdict = _accepted_verdict()
    verdict.update({
        "visible_texts": ["67", equation],
        "unsupported_numeric_or_factual_values": [],
        "unexpected_text_regions": [],
        "malformed_or_factual_text_error": False,
        "text_integrity": 88,
        "decision": "accept",
    })
    scene = {
        "index": 11,
        "image_path": str(image),
        "text": "이 분석의 신뢰도가 높지는 않습니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert reviewed["failure_categories"] == []
    assert reviewed["raw"]["unexpected_or_malformed_texts"] == []


def test_unapproved_real_financial_value_is_still_rejected(tmp_path: Path):
    image = tmp_path / "market.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "visible_texts": ["영업이익 67조"],
        "unsupported_numeric_or_factual_values": ["영업이익 67조"],
        "unexpected_text_regions": [
            {"text": "영업이익 67조", "bbox": [.25, .4, .16, .08], "confidence": .96},
        ],
        "malformed_or_factual_text_error": True,
        "text_integrity": 55,
        "decision": "review",
    })
    scene = {
        "index": 11,
        "image_path": str(image),
        "text": "영업이익의 방향을 살펴봅니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert reviewed["failure_categories"] == ["text_unexpected_or_malformed"]
    assert reviewed["raw"]["unexpected_or_malformed_texts"] == ["영업이익 67조"]


def test_obvious_localized_inpainting_smear_is_a_hard_failure(tmp_path: Path):
    image = tmp_path / "smear.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "obvious_localized_blur_or_smear_artifact_present": True,
        "decision": "review",
        "reason": "게이지 중앙에 의사문자 제거 후 흐린 얼룩이 남음",
    })
    scene = {
        "index": 16,
        "image_path": str(image),
        "text": "배당의 즉각성을 비교합니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert report["reviewed"][0]["failure_categories"] == ["local_edit_blur_smear_artifact"]


def test_visual_qa_default_capacity_covers_all_48_scenes(tmp_path: Path):
    scenes = []
    for index in range(48):
        image = tmp_path / f"scene_{index:03d}.png"
        Image.new("RGB", (64, 36), "navy").save(image)
        scenes.append({
            "index": index,
            "image_path": str(image),
            "art_direction": {"character_required": True},
        })

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(_accepted_verdict()),
    ) as post:
        report = assess_visual_alignment(scenes, enabled=True, max_scenes=64)

    assert len(report["reviewed"]) == 48
    assert report["skipped"] == []
    assert post.call_count == 48


def test_visual_qa_does_not_turn_unplanned_style_preferences_into_global_hard_failures(tmp_path: Path):
    image = tmp_path / "contextual.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "wardrobe_match": False,
        "white_sticker_halo_present": True,
        "missing_required_props": ["an optional decorative prop"],
        "number_panel_only": True,
        "decision": "accept",
    })
    scene = {
        "index": 0,
        "image_path": str(image),
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=64)

    reviewed = report["reviewed"][0]
    assert reviewed["retry_recommended"] is False
    assert "character_wardrobe" not in reviewed["failure_categories"]
    assert "character_white_sticker_halo" not in reviewed["failure_categories"]
    assert "scene_required_props_missing" not in reviewed["failure_categories"]
    assert "number_panel_only" not in reviewed["failure_categories"]


def test_low_style_score_alone_does_not_force_global_redraw(tmp_path: Path):
    image = tmp_path / "cel_shaded.png"
    Image.new("RGB", (1920, 1080), "gold").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "style_adherence": 35,
        "visual_medium_violation": False,
        "decision": "review",
        "reason": "카메라 선호도는 낮지만 실제 매체는 2D 셀 셰이딩",
    })
    scene = {"index": 0, "image_path": str(image), "art_direction": {"character_required": True}}

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "style_severe_mismatch" not in report["reviewed"][0]["failure_categories"]


def test_actual_non_2d_medium_requires_both_explicit_signal_and_low_score(tmp_path: Path):
    image = tmp_path / "mixed_medium.png"
    Image.new("RGB", (1920, 1080), "gold").save(image)
    verdict = _accepted_verdict()
    verdict.update({"style_adherence": 30, "visual_medium_violation": True, "decision": "review"})
    scene = {"index": 0, "image_path": str(image), "art_direction": {"character_required": True}}

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "style_severe_mismatch" in report["reviewed"][0]["failure_categories"]


def test_approved_financial_text_is_required_even_when_composition_is_not_strict(tmp_path: Path):
    image = tmp_path / "optional.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "missing_approved_texts": ["8.70퍼센트"],
        "composition_plan_match": False,
        "decision": "accept",
    })
    scene = {
        "index": 2,
        "image_path": str(image),
        "text": "주가는 8.70퍼센트 급락했습니다.",
        "screen_texts": ["8.70퍼센트"],
        "scene_spec": {"composition": "split control room"},
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    failures = report["reviewed"][0]["failure_categories"]
    assert "text_missing_approved_numeric" in failures
    assert "scene_composition_plan_mismatch" not in failures


def test_explicit_required_text_and_strict_composition_remain_binding(tmp_path: Path):
    image = tmp_path / "required.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({"composition_plan_match": False, "decision": "review"})
    scene = {
        "index": 3,
        "image_path": str(image),
        "text": "영업이익을 비교합니다.",
        "screen_texts": ["영업이익"],
        "screen_text_plan": [{"text": "영업이익", "required": True}],
        "scene_spec": {"composition": "comparison", "composition_strict": True},
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert set(report["reviewed"][0]["failure_categories"]) >= {
        "text_missing_approved",
        "scene_composition_plan_mismatch",
    }


def test_decorative_bars_around_exact_approved_text_are_not_a_typo(tmp_path: Path):
    image = tmp_path / "decorated-approved.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    decorated = ["|반도체|", "|삼성전자|", "|SK하이닉스|"]
    verdict.update({
        "visible_texts": decorated,
        "unexpected_or_malformed_texts": decorated,
        "malformed_or_factual_text_error": True,
        "text_integrity": 68,
        "decision": "review",
    })
    scene = {
        "index": 18,
        "image_path": str(image),
        "text": "삼성전자와 SK하이닉스 반도체주가 흔들렸습니다.",
        "screen_texts": ["반도체", "삼성전자", "SK하이닉스"],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert "text_unexpected_or_malformed" not in reviewed["failure_categories"]
    assert reviewed["raw"]["unexpected_or_malformed_texts"] == []


def test_two_exact_approved_labels_on_distinct_scene_props_are_allowed(tmp_path: Path):
    image = tmp_path / "two-approved-labels.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "visible_texts": ["반도체", "반도체"],
        "decision": "accept",
    })
    scene = {
        "index": 25,
        "image_path": str(image),
        "text": "AI 반도체 수요를 살펴봅니다.",
        "screen_texts": ["반도체"],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "text_approved_duplicate" not in report["reviewed"][0]["failure_categories"]
