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


def test_policy20_rechecks_policy19_review_from_before_the_finance_specificity_fix():
    """2026-10-13 사용자 재현(job 14, 씬 2): finance_specificity를 종합 점수에서
    빼도록 고친 뒤에도 정책 버전을 올리지 않으면 같은 이미지 해시의 예전
    (버그 있는) 판정이 그대로 캐시 재사용돼 수정이 반영되지 않는다."""
    assert not _cached_review_policy_compatible({
        "policy_version": 19,
        "failure_categories": ["visual_quality_floor"],
        "raw": {"finance_specificity": 10},
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


def test_visual_qa_rejects_unexpected_chart_in_a_general_narrative_scene(tmp_path: Path):
    """2026-10-07 사용자 재현(job 14, 씬 0): "저 군대 안 갑니다!" 인터뷰 발언
    장면처럼 금융 데이터와 무관한 general 장면인데도 Gemini가 막대그래프를
    그렸다. 프롬프트로 "차트 금지"를 요청해도 매번 지켜지지 않으므로,
    비전 검수가 비정보형 장면의 차트를 적발해 재생성을 유발해야 한다."""
    image = tmp_path / "unexpected-chart.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "chart_or_data_visualization_present": True,
        "decision": "review",
        "reason": "금융 데이터와 무관한 장면에 막대그래프가 그려짐",
    })
    scene = {
        "index": 0,
        "image_path": str(image),
        "scene_type": "general",
        "text": "이기혁 선수는 인터뷰에서 저 군대 안 갑니다 라고 외쳤습니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "unexpected_chart_or_data_visualization" in report["reviewed"][0]["failure_categories"]


def test_general_scene_excludes_finance_specificity_from_composite_score(tmp_path: Path):
    """2026-10-13 사용자 재현(job 14, 씬 2): 전역모 논란처럼 금융과 무관한
    일반형 장면은 애초에 '경제적 배경'이 없는 게 정상인데, 종합 점수가
    finance_specificity를 무조건 포함시켜 평균을 끌어내리는 바람에
    다른 항목을 전부 통과한 장면이 visual_quality_floor로 떨어졌다.
    일반형 장면에서는 finance_specificity를 종합 점수에서 빼야 한다."""
    image = tmp_path / "general-scene-no-finance.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        # scene_match/composition/style_adherence/text_integrity/character_identity는
        # 전부 90대지만 finance_specificity만 금융 배경이 없다는 이유로 낮게 나온 상황을 재현
        "finance_specificity": 10,
        "decision": "accept",
        "reason": "분할 화면 구성을 통해 전역모 착용과 비판 상황의 인과 관계를 적절히 표현함",
    })
    scene = {
        "index": 2,
        "image_path": str(image),
        "scene_type": "general",
        "text": "전역모는 군 복무를 마친 뒤 쓰는 모자입니다. 병역 혜택을 기념하는 데 전역모를 썼다는 비판이 곧바로 터졌죠.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert reviewed["score"] >= 78
    assert "visual_quality_floor" not in reviewed["failure_categories"]
    assert reviewed["retry_recommended"] is False


def test_accept_decision_overrides_a_uniformly_mediocre_score_average(tmp_path: Path):
    """2026-10-13 사용자 재현(job 14, 씬 2): 하드 실패가 하나도 없고 모델이
    스스로 "accept"라고 판단했는데, 5개 세부 점수가 고르게 70점대라는 이유만으로
    평균(71)이 78 문턱을 못 넘겨 버려졌다. assess_local_edit_preservation에 이미
    적용한 것과 같은 원칙 — 하드 실패가 없으면 모델의 종합 판정을 숫자 평균보다
    우선한다."""
    image = tmp_path / "uniformly-mediocre.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "scene_match": 70,
        "composition": 68,
        "style_adherence": 72,
        "text_integrity": 75,
        "character_identity": 70,
        "decision": "accept",
        "reason": "전역모 착용과 비판 상황을 2분할 컷으로 적절히 연출했으며 캐릭터 및 작화 스타일 규격을 만족합니다.",
    })
    scene = {
        "index": 2,
        "image_path": str(image),
        "scene_type": "general",
        "text": "전역모는 군 복무를 마친 뒤 쓰는 모자입니다. 병역 혜택을 기념하는 데 전역모를 썼다는 비판이 곧바로 터졌죠.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert reviewed["score"] < 78
    assert reviewed["retry_recommended"] is False
    assert "visual_quality_floor" not in reviewed["failure_categories"]


def test_review_decision_still_forces_retry_despite_no_named_hard_failure(tmp_path: Path):
    """회귀 방지: decision이 "review"(모호)면 하드 실패가 없어도 여전히 재시도를
    강제해야 한다 — accept만 특별 취급한다."""
    image = tmp_path / "ambiguous-decision.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "scene_match": 70,
        "composition": 68,
        "style_adherence": 72,
        "text_integrity": 75,
        "character_identity": 70,
        "decision": "review",
        "reason": "확신이 서지 않음",
    })
    scene = {
        "index": 2,
        "image_path": str(image),
        "scene_type": "general",
        "text": "전역모는 군 복무를 마친 뒤 쓰는 모자입니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert reviewed["retry_recommended"] is True
    assert "visual_quality_floor" in reviewed["failure_categories"]


def test_policy21_rechecks_policy20_review_from_before_the_accept_decision_fix():
    """2026-10-13: decision=accept를 숫자 평균보다 우선하도록 고친 뒤에도 정책
    버전을 올리지 않으면, 같은 이미지 해시의 예전(버그 있는) 판정이 캐시로
    재사용돼 수정이 반영되지 않는다."""
    assert not _cached_review_policy_compatible({
        "policy_version": 20,
        "failure_categories": ["visual_quality_floor"],
        "raw": {"decision": "accept", "scene_match": 70},
    })


def test_metric_scene_still_includes_finance_specificity_in_composite_score(tmp_path: Path):
    """금융/수치 장면(scene_type != general)은 경제적 배경이 실제로 기대되므로
    finance_specificity를 종합 점수에서 빼면 안 된다 — 회귀 방지."""
    image = tmp_path / "metric-scene-low-finance.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "scene_match": 90,
        "composition": 90,
        "style_adherence": 90,
        "text_integrity": 90,
        "character_identity": 90,
        "finance_specificity": 10,
        "decision": "accept",
        "reason": "경제적 배경이 부족함",
    })
    scene = {
        "index": 6,
        "image_path": str(image),
        "scene_type": "metric",
        "text": "체육 분야 병역특례는 1973년 도입 이후 기준이 여러 차례 바뀌어 왔죠.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    reviewed = report["reviewed"][0]
    assert reviewed["score"] < 78


def test_visual_qa_allows_charts_in_an_actual_information_scene(tmp_path: Path):
    """metric/graph 같은 정보형 장면은 차트가 정상 콘텐츠이므로 같은 플래그가
    걸리면 안 된다."""
    image = tmp_path / "expected-chart.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    verdict = _accepted_verdict()
    verdict.update({
        "chart_or_data_visualization_present": True,
        "decision": "accept",
        "reason": "지표형 장면의 정상적인 차트",
    })
    scene = {
        "index": 1,
        "image_path": str(image),
        "scene_type": "metric",
        "text": "코스피 하락폭을 점검합니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert "unexpected_chart_or_data_visualization" not in report["reviewed"][0]["failure_categories"]


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


def test_local_edit_preservation_accepts_when_model_says_accept_despite_low_score(tmp_path: Path):
    """2026-10-12 job 14 재현: 같은 재시도 라운드에서 두 번 확인됨(씬4 score=72,
    씬7 score=70) — 모델이 모든 세부 항목을 통과시키고 decision="accept"라고
    명시했는데도, 점수가 75 미만이라는 이유만으로 코드가 그 판정을 뒤집어
    폐기했다. 모델 자신의 accept 판정과 세부 항목이 모두 일치하면 점수와
    무관하게 통과해야 한다."""
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
        "changed_unrequested_regions": [],
        "preservation_score": 70,
        "decision": "accept",
        "reason": "요청된 의상 변경 외 배경, 구도, 인물 표정이 원본과 동일하게 유지됨",
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        result = assess_local_edit_preservation(
            str(source), str(candidate),
            allowed_change_categories=["character_wardrobe"],
        )

    assert result["passed"] is True


def test_local_edit_preservation_still_uses_score_when_decision_is_ambiguous(tmp_path: Path):
    """decision이 accept/reject 둘 다 아니거나 비어 있을 때는 여전히 세부
    항목+점수 기준이 안전장치로 동작해야 한다(회귀 가드)."""
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
        "changed_unrequested_regions": [],
        "preservation_score": 60,
        "decision": "review",
        "reason": "애매함",
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        result = assess_local_edit_preservation(
            str(source), str(candidate),
            allowed_change_categories=["character_wardrobe"],
        )

    assert result["passed"] is False


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


def test_gemini_200_with_unparseable_body_falls_back_to_claude(tmp_path: Path):
    """2026-10-01 job 13 scene 0 재현(job 12 scene 7과 동일 메시지로 이미
    재현된 바 있음): Gemini가 HTTP 200을 반환했지만 candidates/parts가
    비어 있거나 JSON으로 파싱되지 않으면, 기존 코드는 네트워크 실패나
    429/5xx일 때만 타던 Claude 폴백을 타지 않고 바로
    visual_qa_unavailable 경고를 남긴 채 포기했다. 그 결과 이미지 자체는
    멀쩡한데도 장면 전체가 검수 불능으로 처리됐다. 429/5xx와 마찬가지로
    "검수 자체를 완료 못 함"인 이 경우에도 Claude로 넘어가야 한다."""
    image = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    empty_candidates = Mock(status_code=200)
    empty_candidates.json.return_value = {"candidates": []}
    scene = {
        "index": 0,
        "image_path": str(image),
        "text": "주주환원 방식입니다.",
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "ANTHROPIC_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review", return_value=empty_candidates,
    ), patch(
        "app.utils.visual_qa._claude_visual_review", return_value=_accepted_verdict(),
    ) as fallback:
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert report["warnings"] == []
    assert len(report["reviewed"]) == 1
    fallback.assert_called_once()


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


def test_scene_visual_qa_uses_claude_after_gemini_402_prepayment_depleted(tmp_path: Path):
    """2026-10-14 job 18 재현: Gemini 선불 크레딧이 소진되면
    generativelanguage.googleapis.com이 402 RESOURCE_EXHAUSTED를 반환한다
    (실제 API 직접 호출로 재현 확인). 기존 코드는 429/5xx일 때만 Claude
    폴백을 태웠고 402는 조건에 없어서, 멀쩡한 이미지인데도 씬이 통째로
    visual_qa_unavailable로 보류되며 재시도해도 매번 똑같이 막혔다. 402도
    429/5xx와 동일하게 "검수를 완료 못 함" 상태로 취급해 Claude로 넘어가야
    한다."""
    image = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "navy").save(image)
    payment_required = Mock(status_code=402)
    scene = {
        "index": 1,
        "image_path": str(image),
        "text": "주주환원 방식입니다.",
        "screen_texts": ["주주환원"],
        "art_direction": {"character_required": True},
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "ANTHROPIC_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review", return_value=payment_required,
    ), patch(
        "app.utils.visual_qa._claude_visual_review", return_value=_accepted_verdict(),
    ) as fallback:
        report = assess_visual_alignment([scene], enabled=True, max_scenes=1)

    assert len(report["reviewed"]) == 1
    assert report["warnings"] == []
    fallback.assert_called_once()


def test_local_edit_qa_uses_claude_after_gemini_402_prepayment_depleted(tmp_path: Path):
    """assess_local_edit_preservation 쪽도 같은 402 폴백 누락이 있었다
    (job 18 scene 14 재현: local_edit_qa_unavailable:402)."""
    source = tmp_path / "source.png"
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (1920, 1080), "navy").save(source)
    Image.new("RGB", (1920, 1080), "navy").save(candidate)
    payment_required = Mock(status_code=402)
    accepted_edit_verdict = {
        "composition_preserved": True, "camera_and_crop_preserved": True,
        "character_face_preserved": True, "character_pose_preserved": True,
        "background_preserved": True, "unrelated_props_preserved": True,
        "style_preserved": True, "only_requested_changes": True,
        "repaired_surface_clean": True, "no_blur_smear_or_ghosting": True,
        "preservation_score": 95, "decision": "accept", "reason": "요청된 변경만 반영됨",
    }

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key", "ANTHROPIC_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review", return_value=payment_required,
    ), patch(
        "app.utils.visual_qa._claude_visual_review", return_value=accepted_edit_verdict,
    ) as fallback:
        result = assess_local_edit_preservation(
            str(source), str(candidate),
            allowed_change_categories=["text_unexpected_or_malformed"],
        )

    assert result["status"] != "unavailable"
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


def test_claude_visual_review_requests_max_tokens_32000(monkeypatch):
    """2026-09-30 job 12 scene 4 재현: max_tokens=8000에서도 지적 사항이 많은
    장면은 stop_reason=max_tokens로 잘려(len=17168, 닫는 "}" 없음) 검수 전체가
    visual_qa_unavailable로 실패하고 배치 전체가 중단됐다. claude-sonnet-4-6가
    max_tokens=32000을 그대로 받아들이는 것을 실측으로 확인했다(실제 청구는
    사용한 토큰 기준이라 비용 영향 없음). 32000으로 올려 같은 부류의 잘림을
    방지한다."""
    import anthropic

    captured: dict = {}

    class _FakeBlock:
        text = '{"scene_match": 80, "decision": "accept", "reason": "ok"}'

    class _FakeResponse:
        content = [_FakeBlock()]
        stop_reason = "end_turn"

    class _FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse()

    class _FakeAnthropic:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-key")
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)

    payload = {"contents": [{"parts": [{"text": "review this scene"}]}]}
    result = visual_qa_module._claude_visual_review(payload)

    assert captured.get("max_tokens") == 32000
    assert result == {"scene_match": 80, "decision": "accept", "reason": "ok"}


def test_scene_with_no_image_path_at_all_is_skipped_not_crashed(tmp_path: Path):
    """2026-10-15 job 18 재현: 슬라이드형 모드에서 예산 초과 등으로 생성
    자체가 실패한 장면은 image_path도 final_image_path도 전혀 없다.
    과거에는 Path("")가 "현재 디렉터리는 존재한다"로 True를 반환해 이런
    장면이 검수 후보로 잘못 들어갔고, scene["image_path"] 직접 접근이
    KeyError로 죽어 전체 배치의 다른 장면 검수까지 조용히 스킵됐다."""
    good_image = tmp_path / "ok.png"
    Image.new("RGB", (1920, 1080), "navy").save(good_image)
    verdict = _accepted_verdict()
    scene_without_image = {"index": 6, "text": "생성 실패한 장면입니다."}
    scene_with_image = {"index": 7, "image_path": str(good_image), "text": "정상 장면입니다."}

    with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
        "app.utils.visual_qa._post_visual_review",
        return_value=_visual_response(verdict),
    ):
        report = assess_visual_alignment(
            [scene_without_image, scene_with_image], enabled=True, max_scenes=0,
        )

    reviewed_indices = {item["index"] for item in report["reviewed"]}
    assert reviewed_indices == {7}
