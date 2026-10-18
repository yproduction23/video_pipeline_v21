"""단위 테스트는 운영 영속 요청 저장소를 열지 않는다."""
import pytest


@pytest.fixture(autouse=True)
def isolated_image_request_state(tmp_path, monkeypatch):
    from app import config, runtime_config
    path = str(tmp_path / "request_state.sqlite3")
    monkeypatch.setattr(config, "GEMINI_REQUEST_STATE_PATH", path)
    monkeypatch.setitem(runtime_config._state, "gemini_request_state_path", path)


@pytest.fixture(autouse=True)
def approve_ai_backgrounds_by_default(monkeypatch):
    """AI 배경 검수는 실제 Claude 비전을 부르고 실제 이미지가 필요하므로, 비전 호출이 주어지지 않은 단위 테스트에서는 통과시킨다
    (검수 자체는 test_slide_background_gate가 비전 호출을 직접 넣어 확인한다)."""
    from app.services import slide_background

    original = slide_background.assess_background

    def gate(image_path, *, vision_call=None):
        return original(image_path, vision_call=vision_call) if vision_call else (True, "test")

    monkeypatch.setattr(slide_background, "assess_background", gate)
