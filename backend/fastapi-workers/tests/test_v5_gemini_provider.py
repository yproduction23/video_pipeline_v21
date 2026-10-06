"""V5 Gemini 어댑터는 비용 단가 없이 유료 실행을 준비하지 못해야 한다."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.v5.providers.gemini_provider import (
    GeminiModel,
    GeminiProvider,
    GeminiProviderError,
    GEMINI_REFERENCE_CONTRACT_VERSION,
    _load_default_references,
    ensure_gemini_reference_contract,
    select_contextual_reference_paths,
)


def test_gemini_estimate_requires_explicit_approved_rate():
    prior = os.environ.pop("V5_GEMINI_PRO_IMAGE_2K_ESTIMATE_USD", None)
    try:
        try:
            GeminiProvider(api_key="test-key").estimate_cost_usd(GeminiModel.PRO, 2048, 1152)
            assert False, "명시적 단가 없이 비용 추정이 통과했습니다."
        except GeminiProviderError:
            pass
    finally:
        if prior is not None:
            os.environ["V5_GEMINI_PRO_IMAGE_2K_ESTIMATE_USD"] = prior


def test_flash_estimate_requires_its_own_explicit_approved_rate(monkeypatch):
    monkeypatch.setenv("V5_GEMINI_FLASH_IMAGE_2K_ESTIMATE_USD", "0.101")

    assert GeminiProvider(api_key="test-key").estimate_cost_usd(
        GeminiModel.FLASH, 2048, 1152
    ) == 0.101


def test_p1_comparison_forces_one_provider_attempt(tmp_path: Path, monkeypatch):
    observed = {}

    class FakeNanaBananaProvider:
        def generate(self, **kwargs):
            observed.update(kwargs)
            Path(kwargs["output_path"]).write_bytes(b"test-image")

    import app.providers.real.image as image_module
    monkeypatch.setattr(image_module, "NanaBananaProvider", FakeNanaBananaProvider)
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference")
    class Audit:
        def summary(self):
            return {"entries": []}
    audit = Audit()

    result = GeminiProvider(api_key="test-key").generate(
        "prompt", model=GeminiModel.PRO, reference_image_paths=[str(reference)], request_audit=audit,
    )

    assert result.image_bytes == b"test-image"
    assert observed["gemini_max_attempts"] == 1
    assert observed["gemini_request_audit"] is audit
    assert observed["gemini_reference_contract_declared"] is True
    assert observed["character_style_prompt"] == "none"
    assert observed["suppress_legacy_style_lock"] is True
    assert "explicitly selected character reference" in observed["prompt"]
    assert "let the written scene choose expression, costume, action, headwear, and framing" in observed["prompt"]
    assert "do not force every scene into one studio template" in observed["prompt"].lower()


def test_flash_comparison_is_explicit_and_keeps_one_attempt(tmp_path: Path, monkeypatch):
    observed = {}

    class FakeNanaBananaProvider:
        def generate(self, **kwargs):
            observed.update(kwargs)
            Path(kwargs["output_path"]).write_bytes(b"flash-image")

    import app.providers.real.image as image_module
    monkeypatch.setattr(image_module, "NanaBananaProvider", FakeNanaBananaProvider)
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference")

    class Audit:
        def summary(self):
            return {"entries": []}

    result = GeminiProvider(api_key="test-key").generate(
        "prompt",
        model=GeminiModel.FLASH,
        reference_image_paths=[str(reference)],
        request_audit=Audit(),
    )

    assert result.model == "gemini-3.1-flash-image"
    assert observed["gemini_model"] == "gemini-3.1-flash-image"
    assert observed["gemini_max_attempts"] == 1


def test_policy_comparison_propagates_pro_priority_and_flash_high(tmp_path: Path, monkeypatch):
    observed = []

    class FakeNanaBananaProvider:
        def generate(self, **kwargs):
            observed.append(dict(kwargs))
            Path(kwargs["output_path"]).write_bytes(b"comparison-image")

    import app.providers.real.image as image_module
    monkeypatch.setattr(image_module, "NanaBananaProvider", FakeNanaBananaProvider)

    class Audit:
        def summary(self):
            return {"entries": []}

    provider = GeminiProvider(api_key="test-key")
    provider.generate(
        "pro prompt",
        model=GeminiModel.PRO,
        service_tier="priority",
        request_audit=Audit(),
    )
    provider.generate(
        "flash prompt",
        model=GeminiModel.FLASH,
        thinking_level="high",
        request_audit=Audit(),
    )

    assert observed[0]["gemini_service_tier"] == "priority"
    assert observed[0]["gemini_thinking_level"] is None
    assert observed[1]["gemini_service_tier"] == "standard"
    assert observed[1]["gemini_thinking_level"] == "high"


def test_provider_selects_scene_relevant_job52_references_when_callers_omit_them(tmp_path: Path, monkeypatch):
    observed = {}

    class FakeNanaBananaProvider:
        def generate(self, **kwargs):
            observed.update(kwargs)
            Path(kwargs["output_path"]).write_bytes(b"test-image")

    import app.providers.real.image as image_module
    import app.v5.providers.gemini_provider as provider_module

    monkeypatch.setattr(image_module, "NanaBananaProvider", FakeNanaBananaProvider)
    face_range = tmp_path / "channel_character_face_range_v2.png"
    semiconductor = tmp_path / "channel_style_semiconductor_production_scene_v1.png"
    lab = tmp_path / "channel_style_semiconductor_growth_scene_v1.png"
    briefing = tmp_path / "channel_style_job52_briefing.png"
    face_range.write_bytes(b"face-range")
    semiconductor.write_bytes(b"semiconductor")
    lab.write_bytes(b"lab")
    briefing.write_bytes(b"briefing")
    monkeypatch.setattr(
        provider_module,
        "_load_default_references",
        lambda: [str(face_range), str(briefing), str(lab), str(semiconductor)],
    )

    GeminiProvider(api_key="test-key").generate("semiconductor wafer production line", model=GeminiModel.PRO)

    assert observed["character_image_paths"] == [str(face_range), str(semiconductor), str(lab)]
    assert "shared face construction as the identity contract" in observed["prompt"]
    assert "Costume and headwear remain scene-specific" in observed["prompt"]
    assert "detached translucent glass card" in observed["prompt"].lower()


def test_style_only_reference_does_not_turn_into_a_mascot_identity(tmp_path: Path, monkeypatch):
    observed = {}

    class FakeNanaBananaProvider:
        def generate(self, **kwargs):
            observed.update(kwargs)
            Path(kwargs["output_path"]).write_bytes(b"test-image")

    import app.providers.real.image as image_module
    monkeypatch.setattr(image_module, "NanaBananaProvider", FakeNanaBananaProvider)
    style = tmp_path / "style_reference_v4_medium_clean.png"
    style.write_bytes(b"style")

    GeminiProvider(api_key="test-key").generate(
        "NO mascot in this scene", model=GeminiModel.PRO, reference_image_paths=[str(style)],
    )

    assert "No single authoritative mascot model sheet is supplied" in observed["prompt"]
    assert "collectively define the channel's acceptable visual range" in observed["prompt"]
    assert "same face proportions" not in observed["prompt"]


def test_default_reference_bank_uses_job52_scene_range_not_mismatched_single_sheets():
    names = [Path(path).name for path in _load_default_references()]

    assert len(names) == 9
    assert names[0] == "channel_character_face_range_v2.png"
    assert names[1] == "channel_character_face_scene05_v1.png"
    assert "channel_style_semiconductor_growth_scene_v1.png" in names
    assert "channel_style_semiconductor_production_scene_v1.png" in names
    assert "character_reference_v4_identity_clean.png" not in names
    assert "style_reference_v4_medium_clean.png" not in names


def test_semiconductor_context_uses_approved_production_scenes_not_hologram_data_lab():
    selected = select_contextual_reference_paths(
        "semiconductor wafer production line",
        _load_default_references(),
    )

    assert [Path(path).name for path in selected] == [
        "channel_character_face_range_v2.png",
        "channel_style_semiconductor_production_scene_v1.png",
        "channel_style_semiconductor_growth_scene_v1.png",
    ]
    assert "channel_style_job52_data_lab.png" not in [Path(path).name for path in selected]


def test_contextual_reference_selection_preserves_explicit_character_and_uses_only_nearby_style_refs(tmp_path: Path):
    character = tmp_path / "user_character_reference.png"
    character.write_bytes(b"character")
    selected = select_contextual_reference_paths(
        "stormy forecast weather-map outlook",
        [str(character), *_load_default_references()],
    )

    assert selected[0] == str(character)
    assert len(selected) == 3
    assert [Path(path).name for path in selected[1:]] == [
        "channel_style_job52_risk_map.png",
        "channel_style_job52_market_flow.png",
    ]


def test_contextual_reference_selection_can_reduce_only_the_second_style_reference_after_transient_failure():
    selected = select_contextual_reference_paths(
        "semiconductor wafer production line",
        _load_default_references(),
        max_references=2,
    )

    assert [Path(path).name for path in selected] == [
        "channel_character_face_range_v2.png",
        "channel_style_semiconductor_production_scene_v1.png",
    ]


def test_goggles_scene_uses_scene05_face_anchor_without_freezing_costume(tmp_path: Path):
    face_range = tmp_path / "channel_character_face_range_v2.png"
    face_anchor = tmp_path / "channel_character_face_scene05_v1.png"
    data_lab = tmp_path / "channel_style_job52_data_lab.png"
    market = tmp_path / "channel_style_job52_market_flow.png"
    for path in (face_range, face_anchor, data_lab, market):
        path.write_bytes(path.name.encode())

    selected = select_contextual_reference_paths(
        "white lab coat and round scientist goggles in a data laboratory",
        [str(face_range), str(face_anchor), str(data_lab), str(market)],
    )

    assert [Path(path).name for path in selected] == [
        "channel_character_face_range_v2.png",
        "channel_character_face_scene05_v1.png",
        "channel_style_job52_data_lab.png",
    ]


def test_generic_goggles_safety_clause_does_not_select_goggles_anchor(tmp_path: Path):
    face_range = tmp_path / "channel_character_face_range_v2.png"
    face_anchor = tmp_path / "channel_character_face_scene05_v1.png"
    risk = tmp_path / "channel_style_job52_risk_map.png"
    market = tmp_path / "channel_style_job52_market_flow.png"
    for path in (face_range, face_anchor, risk, market):
        path.write_bytes(path.name.encode())

    selected = select_contextual_reference_paths(
        "market risk control room\nCOMMON CROSS-SCENE ACCEPTANCE FLOOR: "
        "goggles or glasses must not erase eye layers",
        [str(face_range), str(face_anchor), str(risk), str(market)],
    )

    assert [Path(path).name for path in selected] == [
        "channel_character_face_range_v2.png",
        "channel_style_job52_risk_map.png",
        "channel_style_job52_market_flow.png",
    ]


def test_goggles_face_anchor_contract_is_larger_face_evidence_not_costume_lock(tmp_path: Path, monkeypatch):
    observed = {}

    class FakeNanaBananaProvider:
        def generate(self, **kwargs):
            observed.update(kwargs)
            Path(kwargs["output_path"]).write_bytes(b"test-image")

    import app.providers.real.image as image_module
    monkeypatch.setattr(image_module, "NanaBananaProvider", FakeNanaBananaProvider)
    refs = []
    for name in (
        "channel_character_face_range_v2.png",
        "channel_character_face_scene05_v1.png",
        "channel_style_job52_data_lab.png",
        "channel_style_job52_market_flow.png",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode())
        refs.append(str(path))

    GeminiProvider(api_key="test-key").generate(
        "white lab coat and round scientist goggles in a data laboratory",
        model=GeminiModel.PRO,
        reference_image_paths=refs,
    )

    assert [Path(path).name for path in observed["character_image_paths"]] == [
        "channel_character_face_range_v2.png",
        "channel_character_face_scene05_v1.png",
        "channel_style_job52_data_lab.png",
    ]
    assert "larger role-matched face crop" in observed["prompt"]
    assert "Do not copy or freeze its expression, costume, goggles, pose" in observed["prompt"]


def test_reference_contract_is_idempotent_when_operational_transport_checks_it_again(tmp_path: Path):
    refs = []
    for name in (
        "channel_character_face_range_v2.png",
        "channel_character_face_scene05_v1.png",
        "channel_style_job52_briefing.png",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode())
        refs.append(str(path))

    once = ensure_gemini_reference_contract("scientist goggles", refs)
    twice = ensure_gemini_reference_contract(once, refs)

    assert twice == once
    assert twice.count(
        f"FINAL GEMINI REFERENCE CONTRACT [{GEMINI_REFERENCE_CONTRACT_VERSION}]:"
    ) == 1


def test_localized_retry_source_is_not_misclassified_as_character_identity(tmp_path: Path):
    refs = []
    for name in (
        "scene_007_rejected.png",
        "channel_character_face_range_v2.png",
        "channel_style_job52_briefing.png",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode())
        refs.append(str(path))

    prompt = ensure_gemini_reference_contract(
        "The FIRST attached image is the previously rejected full scene. Edit that same frame locally.",
        refs,
    )

    assert "Reference image 1 is only the previously rejected full-scene edit source" in prompt
    assert "Reference image 2 contains exact, non-generatively altered face crops" in prompt
    assert "Reference image 1 is an explicitly selected character reference" not in prompt


def test_prop_reference_image_is_appended_after_the_normal_cap_not_counted_in_it(tmp_path: Path):
    character = tmp_path / "user_character_reference.png"
    prop = tmp_path / "prop_reference_jeonyeokmo.png"
    for path in (character, prop):
        path.write_bytes(path.name.encode())

    selected = select_contextual_reference_paths(
        "stormy forecast weather-map outlook",
        [str(character), str(prop), *_load_default_references()],
    )

    # 기존 캐릭터 1 + 화풍 2 = 3장은 그대로이고, 소품 참조는 그 뒤에 추가된다.
    assert len(selected) == 4
    assert Path(selected[0]).name == "user_character_reference.png"
    assert [Path(path).name for path in selected[1:3]] == [
        "channel_style_job52_risk_map.png",
        "channel_style_job52_market_flow.png",
    ]
    assert Path(selected[3]).name == "prop_reference_jeonyeokmo.png"


def test_prop_reference_image_survives_the_reduced_transient_failure_cap(tmp_path: Path):
    prop = tmp_path / "prop_reference_jeonyeokmo.png"
    prop.write_bytes(b"prop")

    selected = select_contextual_reference_paths(
        "semiconductor wafer production line",
        [str(prop), *_load_default_references()],
        max_references=2,
    )

    assert [Path(path).name for path in selected] == [
        "channel_character_face_range_v2.png",
        "channel_style_semiconductor_production_scene_v1.png",
        "prop_reference_jeonyeokmo.png",
    ]


def test_prop_reference_contract_clause_describes_a_real_object_photo_only(tmp_path: Path):
    refs = []
    for name in (
        "channel_character_face_range_v2.png",
        "channel_style_job52_briefing.png",
        "prop_reference_jeonyeokmo.png",
    ):
        path = tmp_path / name
        path.write_bytes(name.encode())
        refs.append(str(path))

    prompt = ensure_gemini_reference_contract("soldier discharge scene", refs)

    assert "Reference images 3 are real-world photographs" in prompt
    assert "never its style, lighting, character, composition" in prompt
    # 소품 참조가 캐릭터 식별 슬롯으로 잘못 분류되면 안 된다.
    assert "Reference image 3 is an explicitly selected character reference" not in prompt
