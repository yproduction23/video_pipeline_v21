import pytest

from app.workers import script_worker
from app.workers.script_worker import ScriptWorker


def _generate(worker: ScriptWorker):
    return worker.generate(
        keyword="삼성전자",
        category="INDIVIDUAL_STOCK",
        target_minutes=1,
        market_data={"source": "test"},
        job_id=999,
    )


def test_generate_raises_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        _generate(ScriptWorker())


def test_generate_raises_on_anthropic_exception(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        script_worker,
        "_collect_keyword_news",
        lambda _terms, content_nature=None: [{"title": "삼성전자 테스트 기사", "url": "https://example.test/news"}],
    )
    worker = ScriptWorker()

    def _failing_llm(*_args, **_kwargs):
        raise RuntimeError("Anthropic API 잔액 부족")

    monkeypatch.setattr(worker, "_call_llm_with_fallback", _failing_llm)

    with pytest.raises(RuntimeError, match="스크립트 생성 실패.*잔액 부족"):
        _generate(worker)


def test_mock_generate_still_works_directly():
    result = ScriptWorker()._mock_generate("테스트", "개별 종목", 1, 999)

    assert result is not None
    assert result["used_real_llm"] is False


def test_no_mock_result_returned_on_failure(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = None

    try:
        result = _generate(ScriptWorker())
    except RuntimeError:
        pass

    assert result is None
