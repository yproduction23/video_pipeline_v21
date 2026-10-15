from pathlib import Path

import pytest
from PIL import Image

from app import runtime_config
from app.main import SingleImageGenerateRequest
from app.utils.image_request_control import ImageRequestHeld
from app.workers.images_worker import GeneratedImageVisualContractError, ImagesWorker


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class _Provider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate_image(self, **kwargs) -> bool:
        self.calls.append(kwargs)
        Image.effect_noise((1920, 1080), 24).convert("RGB").save(kwargs["output_path"])
        return True


def test_single_scene_request_preserves_complete_scene_contract() -> None:
    fields = SingleImageGenerateRequest.model_fields

    assert "scene_meta" in fields


def test_single_scene_regeneration_uses_the_operational_image_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    provider = _Provider()
    worker = ImagesWorker()
    inspections: list[str] = []

    monkeypatch.setattr(
        "app.providers.factory.get_image_provider",
        lambda: provider,
    )
    monkeypatch.setattr(
        "app.workers.images_worker._inspect_generated_textless_image",
        lambda scene, path: inspections.append(f"base:{Path(path).name}") or {"passed": True},
    )
    monkeypatch.setattr(
        "app.workers.images_worker._inspect_generated_visual_image",
        lambda scene, path: inspections.append(f"final:{Path(path).name}") or {"passed": True},
    )
    monkeypatch.setattr(
        "app.workers.images_worker._apply_fal_motion_safety_contract",
        lambda scenes: {"eligible": 0, "blocked": len(scenes)},
    )
    monkeypatch.setattr(
        "app.workers.images_worker._load_default_references",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.workers.images_worker.ProviderRequestAudit.for_job",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        "app.workers.images_worker.acquire_image_job_lock",
        lambda job_id: "test-lock",
    )
    monkeypatch.setattr(
        "app.workers.images_worker.release_image_job_lock",
        lambda job_id, token: None,
    )

    result = worker.generate_single_scene(
        scene={
            "index": 7,
            "text": "반도체 생산라인의 수익 구조를 확인합니다.",
            "prompt_en": "A semiconductor production line with one gold coin analyst.",
            "section": "data",
            "screen_texts": [],
            "screen_text_plan": [],
            "bubble_policy": "forbidden",
            "art_direction": {"character_required": True},
        },
        job_id=9007,
        output_dir=tmp_path,
        character_image_path=None,
        character_style_prompt=None,
    )

    assert result["index"] == 7
    assert result["generation_method"] == "single_scene_operational"
    assert result["image_text_contract"]["approved_texts"] == []
    assert result["character_integrity_contract"]["bubble_policy"] == "forbidden"
    assert provider.calls
    assert "FINAL CHARACTER QUALITY BOUNDARY" in provider.calls[0]["prompt"]
    assert provider.calls[0]["gemini_request_audit"] is not None
    assert inspections == [
        "base:scene_007_regenerate_raw.png",
        "final:scene_007_regenerate_final.png",
    ]


def test_single_scene_regeneration_retries_after_a_qa_rejection_instead_of_crashing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """2026-10-14 job 18 scene 5 재현: 단일 장면 재생성 경로는 재시도가
    전혀 없어, 비전 검수가 한 번이라도 거부하면("전자발찌 착용이 나레이션과
    불일치, 기본 정장 착용 상태") 그대로 처리되지 않은 예외가 500으로
    사용자에게 노출됐다. 메인 배치 경로처럼 교정 피드백을 반영해 다시
    시도하고, 두 번째 시도에서 통과하면 정상 완료돼야 한다."""
    provider = _Provider()
    worker = ImagesWorker()
    monkeypatch.setitem(runtime_config._state, "gemini_retry_max", 3)

    monkeypatch.setattr("app.providers.factory.get_image_provider", lambda: provider)
    monkeypatch.setattr(
        "app.workers.images_worker._inspect_generated_textless_image",
        lambda scene, path: {"passed": True},
    )
    calls = {"n": 0}

    def fake_visual_qa(scene, path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise GeneratedImageVisualContractError(
                "장면 비전 계약 위반: character_wardrobe",
                {"failure_categories": ["character_wardrobe"], "reason": "전자발찌 미착용"},
            )
        return {"passed": True}

    monkeypatch.setattr("app.workers.images_worker._inspect_generated_visual_image", fake_visual_qa)
    monkeypatch.setattr(
        "app.workers.images_worker._apply_fal_motion_safety_contract",
        lambda scenes: {"eligible": 0, "blocked": len(scenes)},
    )
    monkeypatch.setattr("app.workers.images_worker._load_default_references", lambda: [])
    monkeypatch.setattr("app.workers.images_worker.ProviderRequestAudit.for_job", lambda **kwargs: object())
    monkeypatch.setattr("app.workers.images_worker.acquire_image_job_lock", lambda job_id: "test-lock")
    monkeypatch.setattr("app.workers.images_worker.release_image_job_lock", lambda job_id, token: None)

    result = worker.generate_single_scene(
        scene={
            "index": 5,
            "text": "그것도 전자발찌를 찬 상태에서 이 범행을 저질렀습니다.",
            "prompt_en": "close view of an ankle with an electronic monitoring bracelet.",
            "section": "data",
            "art_direction": {"character_required": True},
        },
        job_id=9018,
        output_dir=tmp_path,
        character_image_path=None,
        character_style_prompt=None,
    )

    assert result["index"] == 5
    assert calls["n"] == 2
    assert len(provider.calls) == 2


def test_single_scene_regeneration_holds_instead_of_promoting_a_bad_image_after_exhausting_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """재시도를 다 써도 계속 거부되면, 품질 게이트를 낮춰 나쁜 이미지를
    승격하지 말고 ImageRequestHeld로 보류해야 한다(main.py가 409로 매핑)."""
    provider = _Provider()
    worker = ImagesWorker()
    monkeypatch.setitem(runtime_config._state, "gemini_retry_max", 3)

    monkeypatch.setattr("app.providers.factory.get_image_provider", lambda: provider)
    monkeypatch.setattr(
        "app.workers.images_worker._inspect_generated_textless_image",
        lambda scene, path: {"passed": True},
    )

    def always_rejects(scene, path):
        raise GeneratedImageVisualContractError(
            "장면 비전 계약 위반: character_wardrobe",
            {"failure_categories": ["character_wardrobe"], "reason": "전자발찌 미착용"},
        )

    monkeypatch.setattr("app.workers.images_worker._inspect_generated_visual_image", always_rejects)
    monkeypatch.setattr(
        "app.workers.images_worker._apply_fal_motion_safety_contract",
        lambda scenes: {"eligible": 0, "blocked": len(scenes)},
    )
    monkeypatch.setattr("app.workers.images_worker._load_default_references", lambda: [])
    monkeypatch.setattr("app.workers.images_worker.ProviderRequestAudit.for_job", lambda **kwargs: object())
    monkeypatch.setattr("app.workers.images_worker.acquire_image_job_lock", lambda job_id: "test-lock")
    monkeypatch.setattr("app.workers.images_worker.release_image_job_lock", lambda job_id, token: None)

    with pytest.raises(ImageRequestHeld):
        worker.generate_single_scene(
            scene={
                "index": 5,
                "text": "그것도 전자발찌를 찬 상태에서 이 범행을 저질렀습니다.",
                "prompt_en": "close view of an ankle with an electronic monitoring bracelet.",
                "section": "data",
                "art_direction": {"character_required": True},
            },
            job_id=9018,
            output_dir=tmp_path,
            character_image_path=None,
            character_style_prompt=None,
        )

    assert len(provider.calls) == 3


def test_single_scene_regeneration_does_not_silently_fallback_to_a_chart() -> None:
    source = Path("app/main.py").read_text(encoding="utf-8")
    endpoint = source.split("async def generate_single_image", 1)[1].split(
        "# ============================\n# [S2-2]", 1
    )[0]

    assert "Matplotlib 차트 폴백" not in endpoint
    assert "_render_section" not in endpoint


def test_spring_has_no_metadata_free_single_scene_bypass() -> None:
    source = (
        REPOSITORY_ROOT
        / "backend/spring-app/src/main/java/com/pipeline/video/service/FastApiClient.java"
    ).read_text(encoding="utf-8")

    assert source.count('postJson(fastApiUrl + "/workers/images/generate-single"') == 1
    assert 'bodyMap.put("scene_meta"' in source
