"""2026-10-15 사용자 결정: 벤치마크 영상 기반 Job은 제목·태그·설명(=썸네일로
이어지는 입력)까지 벤치마크와 "결"이 같아야 한다. /workers/youtube/metadata가
benchmark_title/benchmark_tags/benchmark_description을 받으면 그 내용을 스타일
참고로 프롬프트에 포함하고, 받지 않으면 기존처럼 스크립트만으로 생성해야 한다."""
import json

from fastapi.testclient import TestClient

import app.main as main_module

client = TestClient(main_module.app)


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeTextBlock(text)]


class _FakeAnthropic:
    def __init__(self, response_json):
        self._response_json = response_json
        self.messages = self
        self.captured_kwargs = None

    def create(self, **kwargs):
        self.captured_kwargs = kwargs
        return _FakeMessage(json.dumps(self._response_json))


def _fake_response():
    return {"titles": ["제목1", "제목2", "제목3"], "description": "설명", "tags": ["태그1", "태그2"]}


def test_benchmark_fields_are_included_in_prompt_as_style_reference(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_client = _FakeAnthropic(_fake_response())
    monkeypatch.setattr("anthropic.Anthropic", lambda api_key: fake_client)

    response = client.post("/workers/youtube/metadata", json={
        "script_text": "스크립트 본문",
        "is_shorts": False,
        "benchmark_title": "벤치마크 실제 제목입니다",
        "benchmark_tags": ["벤치태그1", "벤치태그2"],
        "benchmark_description": "벤치마크 설명",
    })

    assert response.status_code == 200
    system_prompt = fake_client.captured_kwargs["system"]
    user_prompt = fake_client.captured_kwargs["messages"][-1]["content"]
    system_text = json.dumps(system_prompt, ensure_ascii=False) if not isinstance(system_prompt, str) else system_prompt

    assert "결" in system_text or "style" in system_text.lower()
    assert "verbatim" in system_text.lower() or "copyright" in system_text.lower()
    assert "벤치마크 실제 제목입니다" in user_prompt
    assert "벤치태그1" in user_prompt


def test_no_benchmark_fields_means_no_benchmark_block_in_prompt(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    fake_client = _FakeAnthropic(_fake_response())
    monkeypatch.setattr("anthropic.Anthropic", lambda api_key: fake_client)

    response = client.post("/workers/youtube/metadata", json={
        "script_text": "스크립트 본문",
        "is_shorts": False,
    })

    assert response.status_code == 200
    user_prompt = fake_client.captured_kwargs["messages"][-1]["content"]
    assert "<benchmark>" not in user_prompt
