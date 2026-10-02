"""2026-10-02 channel_b 재현: "기본 포즈 15종 생성"을 처음 눌러봤더니 15개
포즈 전부 "이미지 요청 보류: 영속 요청 감사 객체 없음; 유료 POST 차단"으로
실패했다. ai_provider.generate_image() 호출에 gemini_request_audit가 전혀
전달되지 않아, 모든 유료 Gemini 호출을 막는 안전장치(예산 추적 객체 없이는
과금하지 않음)에 바로 걸린 것이다. 이 기능은 이번이 첫 실제 사용이라
지금까지 아무도 이 버그를 발견하지 못했다. channel_id는 재현용 fixture일
뿐이며, 어떤 채널에서 호출해도 동일하게 막혀 있었다."""
from pathlib import Path
from unittest.mock import patch

from PIL import Image


def test_pose_generation_passes_a_request_audit_so_paid_calls_are_not_blocked(tmp_path, monkeypatch):
    from app.workers.character_library_worker import CharacterLibraryWorker

    captured_kwargs = []

    class FakeProvider:
        def generate_image(self, **kwargs):
            captured_kwargs.append(kwargs)
            if kwargs.get("gemini_request_audit") is None:
                raise RuntimeError("이미지 요청 보류: 영속 요청 감사 객체 없음; 유료 POST 차단")
            Image.new("RGB", (512, 512), "navy").save(kwargs["output_path"], "PNG")

    monkeypatch.setattr(
        "app.providers.factory.get_image_provider", lambda: FakeProvider(),
    )
    monkeypatch.setattr(
        CharacterLibraryWorker, "POSES_BASE_DIR", tmp_path,
    )

    worker = CharacterLibraryWorker()
    with patch.object(worker, "_remove_background", side_effect=lambda raw, final: (
        Path(final).write_bytes(Path(raw).read_bytes()) or final
    )):
        result = worker.generate_library(
            "channel_b", "cute gold coin mascot", pose_names=["neutral"],
        )

    assert result["errors"] == []
    assert len(result["generated"]) == 1
    assert captured_kwargs[0]["gemini_request_audit"] is not None
