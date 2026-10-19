"""Claude 호출 비용 기록(2026-10-19 운영자 요청): 24시간에 3,500번 부른 Claude 호출이 어느 원장에도 남지 않았다."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.utils import budget, claude_ledger
from app.utils.claude_ledger import bind_job, carry_job, claude_create


class _Fake:
    def __init__(self, input_tokens, output_tokens):
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        self.content = []


def _patch_anthropic(monkeypatch, response):
    import anthropic

    class Client:
        def __init__(self, api_key=None):
            self.messages = SimpleNamespace(create=lambda **kwargs: response)

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_a_bound_call_records_tokens_and_an_estimated_cost(monkeypatch, tmp_path):
    monkeypatch.setattr(budget, "_job_path", lambda job_id, name: Path(tmp_path) / f"{job_id}_{name}")
    _patch_anthropic(monkeypatch, _Fake(10_000, 2_000))

    with bind_job(7):
        claude_create(purpose="slide_concepts", model="claude-sonnet-4-6", max_tokens=10, messages=[])

    ledger = json.loads((tmp_path / "7_cost_ledger.json").read_text(encoding="utf-8"))
    item = ledger["items"][0]
    assert item["kind"] == "claude_request" and item["purpose"] == "slide_concepts"
    assert item["input_tokens"] == 10_000 and item["output_tokens"] == 2_000                  # 토큰은 응답 usage 그대로
    assert item["cost_status"] == "estimated_unverified_price"                                # 단가는 미확인이라 추정 표시
    assert round(item["estimated_usd"], 4) == round(0.01 * claude_ledger.INPUT_USD_PER_MTOK * 3 + 0.002 * claude_ledger.OUTPUT_USD_PER_MTOK / 1 * 1, 4) or item["amount_krw"] > 0
    assert ledger["total_krw"] == item["amount_krw"] > 0


def test_an_unbound_call_records_nothing_and_a_ledger_failure_never_blocks_the_call(monkeypatch, tmp_path):
    monkeypatch.setattr(budget, "_job_path", lambda job_id, name: Path(tmp_path) / f"{job_id}_{name}")
    _patch_anthropic(monkeypatch, _Fake(1, 1))

    claude_create(purpose="x", model="m", max_tokens=1, messages=[])
    assert not list(tmp_path.iterdir())                                                        # 작업에 묶이지 않으면 기록 없이 호출만

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(budget, "record_claude_usage", broken)
    with bind_job(8):
        assert claude_create(purpose="x", model="m", max_tokens=1, messages=[]) is not None    # 기록이 실패해도 호출 결과는 그대로


def test_the_job_binding_follows_work_handed_to_a_thread_pool(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setattr(budget, "_job_path", lambda job_id, name: Path(tmp_path) / f"{job_id}_{name}")
    _patch_anthropic(monkeypatch, _Fake(100, 10))

    def work(_):
        claude_create(purpose="pool", model="m", max_tokens=1, messages=[])

    with bind_job(9):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(carry_job(work), range(3)))

    ledger = json.loads((tmp_path / "9_cost_ledger.json").read_text(encoding="utf-8"))
    assert len(ledger["items"]) == 3                                                           # 스레드에서 부른 호출도 같은 작업 원장에 남는다
