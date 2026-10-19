from app import runtime_config
from app.utils import budget


def test_cost_ledger_summary_includes_reserved_requests(tmp_path, monkeypatch):
    old = runtime_config.value("max_budget_per_video_krw")
    try:
        runtime_config.update(max_budget_per_video_krw=40000)
        monkeypatch.setattr(budget, "_job_path", lambda job_id, name: tmp_path / name)
        audit = budget.ProviderRequestAudit.for_job(
            job_id=7, scene_key="scene:0", model="gemini-3-pro-image")
        audit.before_attempt(attempt=1)
        summary = budget.load_cost_ledger_summary(7)
        assert summary["currency"] == "KRW"
        assert summary["total_krw"] > 0
        assert summary["remaining_krw"] < 40000
        assert summary["items"][0]["status"] == "reserved"
    finally:
        runtime_config.update(max_budget_per_video_krw=old)


def test_cost_ledger_summary_returns_every_item_so_item_sums_match_the_total(tmp_path, monkeypatch):
    """2026-10-19 20번 Job: 항목을 마지막 100건만 주어 Spring의 항목 합계(5,168원)가 total_krw(47,172원)보다 작았고,
    두 값이 누적 비용을 번갈아 덮어쓰며 작업 상태까지 되돌렸다. 항목은 전부 돌려준다."""
    import json

    monkeypatch.setattr(budget, "_job_path", lambda job_id, name: tmp_path / name)
    items = [{"kind": "claude_request", "provider": "anthropic", "amount_krw": 10, "purpose": "x"} for _ in range(250)]
    (tmp_path / "cost_ledger.json").write_text(json.dumps({"items": items, "total_krw": 2500}), encoding="utf-8")

    summary = budget.load_cost_ledger_summary(9)

    assert len(summary["items"]) == 250
    assert sum(item["amount_krw"] for item in summary["items"]) == summary["total_krw"] == 2500
