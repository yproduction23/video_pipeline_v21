"""Fal 폴링 대기 시간: 기본 180초는 그대로 두고, 슬라이드 Kling 후작업만 더 길게 기다릴 수 있다.
(2026-10-17 시험: 180초에 포기한 요청 9건이 실패 처리됐지만 fal에서는 계속 진행·과금될 수 있다.)"""
from app.providers.real import video as video_module
from app.providers.real.video import KlingProvider


class _Resp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}
        self.text = ""

    def json(self):
        return self._data


def _run(monkeypatch, tmp_path, **kwargs):
    polls = []

    def fake_post(url, **kw):
        return _Resp(200, {"request_id": "r1", "status_url": "https://s", "response_url": "https://r"})

    def fake_get(url, **kw):
        polls.append(url)
        return _Resp(200, {"status": "IN_QUEUE"})

    monkeypatch.setattr(video_module.requests, "post", fake_post)
    monkeypatch.setattr(video_module.requests, "get", fake_get)
    monkeypatch.setattr(video_module.time, "sleep", lambda s: None)
    provider = KlingProvider.__new__(KlingProvider)
    ok, request_id = provider._generate_fal_api("p", str(tmp_path / "o.mp4"), 5, "key", "https://img", **kwargs)
    return ok, request_id, len(polls)


def test_default_poll_window_is_180_seconds(monkeypatch, tmp_path):
    ok, request_id, polls = _run(monkeypatch, tmp_path)
    assert (ok, request_id, polls) == (False, "r1", 36)


def test_poll_window_can_be_extended(monkeypatch, tmp_path):
    ok, request_id, polls = _run(monkeypatch, tmp_path, poll_timeout_sec=600)
    assert (ok, request_id, polls) == (False, "r1", 120)
