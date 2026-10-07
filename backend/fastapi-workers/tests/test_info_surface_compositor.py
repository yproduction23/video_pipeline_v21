from pathlib import Path
import hashlib

import numpy as np
from PIL import Image
import pytest

from app.v5.scene.runtime_contract import attach_v5_scene_contracts, plan_v5_scene_contract

cv2 = pytest.importorskip("cv2")

from app.services.info_surface.contracts import InfoItem, InfoSurfacePlan, SurfaceContract
from app.services.info_surface.detector import detect_surface_quad, detection_from_normalized_region
from app.services.info_surface.warp_compositor import composite_planar, diegetic_supersample_factor, render_data_cutaway
from app.workers.images_worker import ImagesWorker, _restore_raw_before_deterministic_overlay


def _input():
    image = np.full((540, 960, 3), (34, 48, 61), dtype=np.uint8)
    marker = (224, 208, 174); border = (72, 48, 30)
    quad = np.array([[540, 100], [860, 130], [820, 410], [510, 370]], np.int32)
    cv2.fillConvexPoly(image, quad, border[::-1])
    inner = np.array([[550, 111], [849, 139], [811, 399], [520, 360]], np.int32)
    cv2.fillConvexPoly(image, inner, marker[::-1])
    cv2.line(image, (680, 80), (700, 430), (12, 12, 12), 8)
    return image, marker, border


def test_v5_primary_region_detection_is_exact_and_rejects_out_of_bounds():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    detection = detection_from_normalized_region(image, (.25, .20, .50, .40))
    assert detection.confidence == 1.0
    assert detection.quad.tolist() == [
        [320.0, 144.0],
        [960.0, 144.0],
        [960.0, 432.0],
        [320.0, 432.0],
    ]
    assert cv2.countNonZero(detection.surface_mask) > 0
    with pytest.raises(ValueError, match="캔버스"):
        detection_from_normalized_region(image, (.90, .20, .20, .40))


def test_diegetic_supersample_is_derived_from_quad_size():
    logical = (1427, 1239)
    large = np.array([[300, 100], [1700, 100], [1700, 950], [300, 950]], dtype=np.float32)
    small = np.array([[500, 200], [900, 200], [900, 500], [500, 500]], dtype=np.float32)
    large_result = diegetic_supersample_factor(logical, large, text_candidates=("수령",))
    small_result = diegetic_supersample_factor(logical, small, text_candidates=("수령",))
    assert large_result["factor"] < small_result["factor"]
    assert large_result["final_output_scale"] > small_result["final_output_scale"]


def test_compositor_keeps_larger_diagram_canvas_without_pre_shrink():
    source, marker, border = _input()
    contract = SurfaceContract(surface_kind="hanging_tag", marker_rgb=marker, border_rgb=border, area_ratio_min=.05, area_ratio_max=.35, preferred_region={"x": .45, "y": .10, "width": .48, "height": .72})
    detection = detect_surface_quad(source, contract)
    assert detection is not None
    plan = InfoSurfacePlan(scene_id="large-diagram", render_mode="DIEGETIC_WARP", surface=contract, items=[InfoItem(item_id="chart", role="chart")])
    chart = {"verified": True, "source_ref": "fixture", "visual_kind": "indexed_comparison", "label": "비용", "comparison_basis": "2026-07-21 = 100", "comparison_values": [{"label": "기준", "value": 100}, {"label": "절감", "value": 72}]}
    content = Image.new("RGBA", (2200, 1800), (0, 0, 0, 0))
    composite_planar(Image.fromarray(cv2.cvtColor(source, cv2.COLOR_BGR2RGB)), chart, plan, detection, content_override=content)
    assert plan.chart_render_metadata["canvas_size"] == [2200, 1800]


def test_compositor_uses_narrative_override_without_hero_stat_values():
    source, marker, border = _input()
    contract = SurfaceContract(surface_kind="monitor", marker_rgb=marker, border_rgb=border, area_ratio_min=.05, area_ratio_max=.35, preferred_region={"x": .45, "y": .10, "width": .48, "height": .72})
    detection = detect_surface_quad(source, contract)
    assert detection is not None
    plan = InfoSurfacePlan(scene_id="map-clouds", render_mode="DIEGETIC_WARP", surface=contract, items=[InfoItem(item_id="chart", role="chart")], diagram_kind="map_clouds")
    chart = {"verified": True, "source_ref": "fixture", "external_rates": [{"label": "전기차", "value": "100%"}]}
    content = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
    result = composite_planar(Image.fromarray(cv2.cvtColor(source, cv2.COLOR_BGR2RGB)), chart, plan, detection, content_override=content)
    assert result.size == (960, 540)
    assert plan.chart_render_metadata["bars"] == []


def test_template_surface_does_not_mask_deterministic_ink_with_generated_texture():
    source, marker, border = _input()
    contract = SurfaceContract(surface_kind="monitor", marker_rgb=marker, border_rgb=border, area_ratio_min=.05, area_ratio_max=.35, preferred_region={"x": .45, "y": .10, "width": .48, "height": .72})
    detection = detect_surface_quad(source, contract)
    assert detection is not None
    # 소스의 체인처럼 보드 바깥 요소가 있어도 템플릿 표면의 잉크는 보존한다.
    plan = InfoSurfacePlan(scene_id="template-ink", render_mode="DIEGETIC_WARP", surface=contract, template_id="vault_stages", items=[InfoItem(item_id="chart", role="chart")])
    content = Image.new("RGBA", (1200, 900), (0, 0, 0, 0))
    from PIL import ImageDraw
    ImageDraw.Draw(content).rectangle((250, 250, 950, 650), fill=(255, 0, 0, 255))
    result = composite_planar(Image.fromarray(cv2.cvtColor(source, cv2.COLOR_BGR2RGB)), {"verified": True}, plan, detection, content_override=content)
    red = np.asarray(result.convert("RGB"))[:, :, 0]
    assert int((red > 180).sum()) > 1000


def test_detector_accepts_template_marker_color_alternative():
    image = np.full((540, 960, 3), (34, 48, 61), dtype=np.uint8)
    cv2.rectangle(image, (120, 90), (620, 420), (24, 18, 12), -1)
    # 생성 조명에 따라 밝은 청색 모니터가 청록으로 내려간 실제 v4 사례.
    cv2.rectangle(image, (130, 100), (610, 410), (188, 183, 138), -1)
    contract = SurfaceContract(
        surface_kind="monitor",
        marker_rgb=(216, 240, 248),
        marker_rgb_candidates=[(138, 183, 188)],
        marker_delta_e_max=12,
        border_rgb=(7, 26, 58),
        area_ratio_min=.10,
        area_ratio_max=.55,
        preferred_region={"x": .05, "y": .08, "width": .65, "height": .75},
    )
    detection = detect_surface_quad(image, contract)
    assert detection is not None
    assert detection.confidence >= .55


def test_warp_keeps_chain_above_verified_ink():
    source, marker, border = _input()
    contract = SurfaceContract(surface_kind="hanging_tag", marker_rgb=marker, border_rgb=border, area_ratio_min=.05, area_ratio_max=.35, preferred_region={"x": .45, "y": .10, "width": .48, "height": .72})
    detection = detect_surface_quad(source, contract)
    assert detection is not None
    plan = InfoSurfacePlan(scene_id="fixture", render_mode="DIEGETIC_WARP", surface=contract, items=[InfoItem(item_id="chart", role="chart")])
    chart = {"verified": True, "source_ref": "fixture", "visual_kind": "indexed_comparison", "label": "비용 지수", "comparison_baseline": 100, "comparison_basis": "2026-07-21 = 100", "comparison_values": [{"label": "기준", "value": 100}, {"label": "상한", "value": 110}]}
    result = composite_planar(Image.fromarray(cv2.cvtColor(source, cv2.COLOR_BGR2RGB)), chart, plan, detection)
    # The vertical chain remains dark at its center after composition.
    assert max(result.convert("RGB").getpixel((690, 240))) < 75


def test_data_cutaway_is_a_full_size_one_to_one_replacement():
    image = render_data_cutaway({"verified": True, "source_ref": "fixture", "visual_kind": "indexed_comparison", "label": "비용", "comparison_basis": "2026-07-21 = 100", "comparison_values": [{"label": "A", "value": 100}, {"label": "B", "value": 110}]})
    assert image.size == (1920, 1080)


# ─────────────────────────────────────────────────────────────────────────────
# V5 정책 계약 테스트 (WO-6A)
# 배경: 563b6fb / fdc2da1 에서 확정된 정책 —
#   "수치 전달은 ASS 자막·TTS가 100% 전담한다.
#    이미지 파이프라인은 배경·소품·캐릭터·브랜드/지수 라벨에만 집중한다."
# 이 6개 테스트는 그 정책이 코드에 올바르게 반영됐는지를 검증한다.
# chart warp / DIEGETIC_WARP / DATA_CUTAWAY 경로의 기능 테스트는 별도 WO-6B에서 추가한다.
# ─────────────────────────────────────────────────────────────────────────────


def test_v5_no_image_overlay_for_market_chart_without_explicit_contract(tmp_path):
    """V5 정책 ①: v5_render_contract 없는 market_chart 씬은 이미지를 수정하지 않는다."""
    path = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "#2A3A4A").save(path)
    original_bytes = path.read_bytes()

    scene = {
        "index": 1,
        "market_chart": {
            "verified": True,
            "source_ref": "fixture",
            "visual_kind": "indexed_comparison",
            "label": "비용",
            "comparison_basis": "2026-07-21 = 100",
            "comparison_values": [{"label": "기준", "value": 100}, {"label": "상한", "value": 110}],
        },
        # v5_render_contract 없음 → 즉시 return 경로
    }

    ImagesWorker()._apply_image_overlays(scene, str(path))

    assert path.read_bytes() == original_bytes, "chart warp 경로가 실행됐으나 V5에서는 실행되지 않아야 함"
    assert "info_surface_plan" not in scene
    assert "final_image_path" not in scene
    assert "source_image_path" not in scene


def test_v5_verified_overlay_present_is_always_false():
    """V5 정책 ②: plan_v5_scene_contract는 verified_overlay_present를 항상 False로 설정한다."""
    # general 씬 (market_chart 포함)
    contract_general = plan_v5_scene_contract(
        {
            "scene_type": "general",
            "narration": "코스피 지수가 2,650포인트를 기록했습니다.",
            "market_chart": {
                "verified": True,
                "comparison_values": [{"label": "기준", "value": 100}],
            },
        },
        index=0,
    )
    assert contract_general["verified_overlay_present"] is False
    assert contract_general["verified_overlay_mode"] == "not_applicable"

    # information 씬에 해당하는 유효 V5 타입
    contract_info = plan_v5_scene_contract(
        {
            "scene_type": "metric",
            "narration": "PER이란 주가수익비율입니다.",
            "screen_texts": ["PER", "주가수익비율"],
        },
        index=1,
    )
    # 2026-10-07: 비수치 승인 문구도 결정론 Pillow 합성으로 옮겨 Gemini가
    # 직접 쓰지 않는다(job 14 씬 0/1 깨진 한글 재현 이후 결론).
    assert contract_info["verified_overlay_present"] is False
    assert contract_info["visual_text_policy"] == "deterministic_surface_text"
    assert contract_info["source_visual_text_policy"] == "strict_textless"
    assert contract_info["surface_caption"]["generated_texts"] == ["PER", "주가수익비율"]
    assert contract_info["verified_overlay_mode"] == "deterministic_surface_caption_or_verified_fact"


def test_general_scene_routes_approved_financial_text_to_deterministic_surface():
    """일반형도 대본 승인 수치가 있으면 실제 장면 표면에 정확히 렌더링한다."""
    contract = plan_v5_scene_contract(
        {
            "scene_type": "general",
            "narration": "코스피는 6597포인트 수준까지 밀렸습니다.",
            "screen_texts": ["코스피", "6597포인트"],
            "screen_text_validation": {"passed": True},
        },
        index=19,
    )

    # 2026-10-07: 수치("6597포인트")와 비수치("코스피") 승인 문구가 섞여도
    # 이제 둘 다 Gemini가 아니라 결정론 Pillow 합성 대상이다.
    assert contract["source_visual_text_policy"] == "strict_textless"
    assert contract["visual_text_policy"] == "deterministic_surface_text"
    assert contract["surface_caption"]["generated_texts"] == ["코스피"]
    assert contract["surface_caption"]["deterministic_texts"] == ["6597포인트"]
    assert contract["surface_caption"]["korean"] == "6597포인트\n코스피"


def test_general_numeric_scene_restores_raw_before_resume_overlay():
    """일반형 수치 씬도 재개할 때 합성된 최종본이 아니라 원본에서 다시 시작한다."""
    assert _restore_raw_before_deterministic_overlay({
        "scene_type": "general",
        "narration": "코스피는 6597포인트까지 밀렸습니다.",
        "screen_texts": ["코스피", "6597포인트"],
        "screen_text_validation": {"passed": True},
    }) is True
    assert _restore_raw_before_deterministic_overlay({
        "scene_type": "general",
        "narration": "시장은 크게 흔들렸습니다.",
        "screen_texts": [],
    }) is False


def test_v5_attach_contracts_does_not_inject_verified_overlays():
    """V5 정책 ③: attach_v5_scene_contracts는 v5_verified_overlays를 자동 주입하지 않는다."""
    scenes = [
        {
            "scene_type": "general",
            "narration": "삼성전자 반도체 실적이 개선됐습니다.",
            "verified_facts": [{"indicator": "KOSPI", "value": "2,650", "unit": "pt"}],
            "market_chart": {
                "verified": True,
                "comparison_values": [{"label": "기준", "value": 100}],
            },
        }
    ]
    result = attach_v5_scene_contracts(scenes)
    attached = result[0]

    assert "v5_verified_overlays" not in attached, (
        "V5 정책에서 v5_verified_overlays는 자동 주입되지 않는다. "
        "수치는 ASS 자막·TTS가 전담한다."
    )


def test_v5_market_chart_scene_image_is_not_modified_after_apply_overlays(tmp_path):
    """V5 정책 ④: v5_verified_overlays 없는 V5 씬은 _apply_image_overlays 후 이미지가 원본과 동일하다."""
    path = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "#1E3A5F").save(path)
    original_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    scene = {
        "index": 2,
        "market_chart": {"verified": True, "source_ref": "fixture"},
        "v5_render_contract": {"visual_mode": "semantic_illustration"},
        # v5_verified_overlays 없음 — attach_v5_scene_contracts가 주입하지 않음
    }

    ImagesWorker()._apply_image_overlays(scene, str(path))

    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_sha256, (
        "v5_verified_overlays 없는 V5 씬은 이미지를 수정하지 않아야 함"
    )


def test_v5_verified_facts_route_to_subtitles_not_image_overlay():
    """V5 정책 ⑤: verified_facts가 있어도 v5_verified_overlays는 생성되지 않는다."""
    scenes = [
        {
            "scene_type": "general",
            "narration": "코스피가 2,650pt를 기록했습니다.",
            "verified_facts": [{"indicator": "KOSPI", "value": "2,650", "unit": "pt"}],
        }
    ]
    result = attach_v5_scene_contracts(scenes)
    attached = result[0]

    assert "v5_verified_overlays" not in attached
    assert attached.get("v5_render_contract", {}).get("verified_overlay_present") is False


def test_v5_graph_archetype_also_skips_chart_bake(tmp_path):
    """V5 정책 ⑥: graph 씬 타입의 market_chart 씬도 이미지 합성을 수행하지 않는다."""
    path = tmp_path / "scene.png"
    Image.new("RGB", (1920, 1080), "#0D1B2A").save(path)
    original_bytes = path.read_bytes()

    scene = {
        "index": 3,
        "scene_type": "graph",
        "narration": "반도체 수출 지수가 이번 분기 상승했습니다.",
        "market_chart": {
            "verified": True,
            "comparison_values": [{"label": "A", "value": 100}, {"label": "B", "value": 130}],
        },
        # v5_render_contract 없음
    }

    ImagesWorker()._apply_image_overlays(scene, str(path))

    assert path.read_bytes() == original_bytes
    assert "info_surface_plan" not in scene


# ─────────────────────────────────────────────────────────────────────────────
# WO-6C: verified_facts → v5_verified_overlays 자동 주입 테스트
# _build_v5_verified_overlays가 생성한 overlay가
# (a) attach_v5_scene_contracts를 통해 씬에 주입되고
# (b) downstream facts_from_verified_scene 검증을 통과하는지 확인한다.
# ─────────────────────────────────────────────────────────────────────────────


def test_attach_contracts_injects_overlays_for_facts_with_evidence_fields():
    """WO-6C ①: figure·fact 원문이 있는 verified_fact는 v5_verified_overlays로 자동 주입된다."""
    scenes = [
        {
            "scene_type": "general",
            "narration": "코스피가 2,650포인트를 기록했습니다.",
            # figure·fact 원문이 있어야 downstream 검증을 통과한다
            "verified_facts": [
                {
                    "indicator": "KOSPI",
                    "value": "2,650",
                    "unit": "pt",
                    "figure": "2,650pt",
                    "fact": "코스피 2,650 포인트 기록",
                }
            ],
        }
    ]
    result = attach_v5_scene_contracts(scenes)
    attached = result[0]

    assert "v5_verified_overlays" in attached, (
        "figure·fact 원문이 있는 verified_fact는 v5_verified_overlays로 주입돼야 한다"
    )
    overlays = attached["v5_verified_overlays"]
    assert isinstance(overlays, list) and len(overlays) == 1
    overlay = overlays[0]
    assert overlay["source_ref"] == "facts[0]"
    assert overlay["value"] == "2,650"
    assert overlay["label"] == "KOSPI"
    assert overlay["visualization"] == "text"
    assert isinstance(overlay["anchor"], dict)
    assert all(k in overlay["anchor"] for k in ("x", "y", "width", "height", "kind"))

    # 계약에도 반영됐는지 확인
    contract = attached.get("v5_render_contract", {})
    assert contract.get("verified_overlay_present") is True
    assert contract.get("verified_overlay_mode") == "diegetic_surface_fact"


def test_attach_contracts_uses_only_fact_whose_value_occurs_in_current_scene():
    """다른 장면의 검증 사실이 목록 앞에 있어도 현재 장면 표면으로 새지 않는다."""
    scenes = [{
        "scene_type": "metric",
        "narration": "코스피가 2,650포인트를 기록했습니다.",
        "screen_texts": ["코스피 2,650포인트"],
        "verified_facts": [
            {
                "indicator": "NASDAQ",
                "value": "18,500",
                "figure": "18,500포인트",
                "fact": "나스닥 18,500포인트 기록",
            },
            {
                "indicator": "KOSPI",
                "value": "2,650",
                "figure": "2,650포인트",
                "fact": "코스피 2,650포인트 기록",
            },
        ],
    }]

    attached = attach_v5_scene_contracts(scenes)[0]

    assert attached["v5_verified_overlays"] == [{
        "source_ref": "facts[1]",
        "value": "2,650",
        "label": "KOSPI",
        "visualization": "text",
        "anchor": {
            **attached["v5_verified_overlays"][0]["anchor"],
        },
    }]
    assert "18,500" not in str(attached["v5_verified_overlays"])


def test_attach_contracts_skips_injection_when_value_not_in_evidence():
    """WO-6C ②: value가 figure·fact 원문에 없으면 주입하지 않는다 (WO-6A 기존 정책 유지 확인)."""
    scenes = [
        {
            "scene_type": "general",
            "narration": "코스피 상황을 살펴보겠습니다.",
            # figure·fact 없음 → evidence = "" → value 검증 불통과
            "verified_facts": [
                {"indicator": "KOSPI", "value": "2,650", "unit": "pt"}
            ],
        }
    ]
    result = attach_v5_scene_contracts(scenes)
    attached = result[0]

    assert "v5_verified_overlays" not in attached
    assert attached.get("v5_render_contract", {}).get("verified_overlay_present") is False


def test_injected_overlays_pass_downstream_validation():
    """WO-6C ③: 자동 생성된 v5_verified_overlays는 downstream 검증을 실제로 통과한다."""
    from app.v5.overlay.diegetic_fact_overlay import facts_from_verified_scene

    scene = {
        "scene_type": "general",
        "narration": "코스피가 2,650포인트를 기록했습니다.",
        "verified_facts": [
            {
                "indicator": "KOSPI",
                "value": "2,650",
                "unit": "pt",
                "figure": "2,650pt",
                "fact": "코스피 2,650 포인트 기록",
            }
        ],
    }
    result = attach_v5_scene_contracts([scene])
    attached = result[0]

    # 자동 주입 확인
    assert "v5_verified_overlays" in attached

    # downstream 검증 (facts_from_verified_scene)이 예외 없이 통과하는지 확인
    # 이 함수가 예외를 던지면 이미지 생성 실패로 이어지므로 반드시 통과해야 한다
    try:
        facts = facts_from_verified_scene(attached)
        assert len(facts) == 1, f"facts 수 불일치: {len(facts)}"
        assert facts[0].value == "2,650"
        assert facts[0].label == "KOSPI"
    except ValueError as exc:
        raise AssertionError(
            f"자동 주입된 overlay가 downstream 검증을 통과하지 못했다: {exc}"
        ) from exc
