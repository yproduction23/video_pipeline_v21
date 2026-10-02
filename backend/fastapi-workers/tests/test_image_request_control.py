"""실제 유료 호출 없이 WO-REQUEST-01의 실패/재개/동시성 계약을 검증한다."""
from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app import runtime_config
from app.providers.real.image import NanaBananaProvider
from app.utils import budget, process_manager
from app.utils.budget import ProviderRequestAudit
from app.utils.image_request_control import (
    ImageRequestControl, ImageRequestHeld, canonical_scene, retry_after_seconds,
    assert_request_review_cleared, write_request_review,
)


def audit(path, scene="image:21", **metadata):
    return ProviderRequestAudit.for_path(path=path, scene_key=scene, model="gemini-3-pro-image",
        unit_usd=.1, usd_krw=1000, budget_limit_krw=10000, request_metadata=metadata)


@pytest.mark.parametrize("key", ["image:21", "template_regen:21", "background:21", "scene-021", "pilot:image:21", "image:21:variation:1", "scene_021", "pilot:gemini-3-pro-image:scene_021", "image:00021:variation:1"])
def test_scene_aliases_share_one_limit(key):
    assert canonical_scene(key) == "scene:21"


@pytest.mark.parametrize("key", ["forecast_021", "scene_021_details", "company:21", "scene-21a"])
def test_unrecognized_scene_names_are_not_merged(key):
    assert canonical_scene(key) == key


def test_resume_contract_and_model_changes_cannot_reset_total_limit(tmp_path):
    path = tmp_path / "cost.json"
    for i, key in enumerate(["image:21", "template_regen:21", "background:21"]):
        owner = audit(path, key, contract_fingerprint=f"contract-{i}")
        token = owner.before_attempt(attempt=1)
        owner.after_attempt(token, outcome="http_200", status_code=200)
    with pytest.raises(ImageRequestHeld, match="상한"):
        audit(path, "image:21:variation:1", contract_fingerprint="different").before_attempt(attempt=1)
    assert len(json.loads(path.read_text())["items"]) == 3


@pytest.mark.parametrize("legacy_key,new_key", [
    ("image:21", "image:21"),
    ("image:21", "scene-021"),
    ("scene-021", "image:21"),
    ("image:21", "pilot:gemini-3-pro-image:scene_021"),
    ("pilot:gemini-3-pro-image:scene_021", "image:21"),
])
def test_legacy_ledger_counts_cannot_be_reset_by_new_controller(tmp_path, legacy_key, new_key):
    path = tmp_path / "cost.json"
    path.write_text(json.dumps({"items": [{"provider": "gemini", "scene_key": legacy_key, "status": "http_503"}] * 47, "total_krw": 0}))
    with pytest.raises(ImageRequestHeld, match="상한"):
        audit(path, new_key).before_attempt(attempt=1)
    assert len(json.loads(path.read_text())["items"]) == 47


def test_unresolved_reservation_is_not_expired_or_reissued(tmp_path):
    path = tmp_path / "cost.json"
    audit(path).before_attempt(attempt=1)
    with pytest.raises(ImageRequestHeld, match="미확정"):
        audit(path).before_attempt(attempt=1)


def test_post_completion_deadline_persists_with_injected_clock_and_rng(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_config._state, "gemini_pro_retry_base_seconds", 20.)
    now = [1000.]
    path = tmp_path / "control.sqlite3"
    for n, expected in enumerate([20, 40, 80]):
        control = ImageRequestControl(path=path, clock=lambda: now[0], uniform=lambda low, high: high)
        control.reserve(scope="job", scene="image:21", token=str(n))
        now[0] += 17  # 요청 소요시간은 백오프 대기와 구별한다.
        result = control.finish(str(n), retryable=True)
        assert result["next_allowed_at"] - now[0] == expected
        assert result["failure_n"] == n + 1
        resumed = ImageRequestControl(path=path, clock=lambda: now[0])
        with pytest.raises(ImageRequestHeld):
            resumed.reserve(scope="job", scene="image:21", token="early")
        now[0] = result["next_allowed_at"]
    assert result["status"] == "needs_review"


def _exhaust_scene_window(control, now):
    """503 세 번으로 현재 냉각 구간을 소진한다."""
    result = None
    for index in range(3):
        token = f"failure-{index}"
        control.reserve(scope="job", scene="image:7", token=token)
        result = control.finish(token, retryable=True)
        now[0] = result["next_allowed_at"]
    return result


def test_needs_review_records_first_timestamp_and_never_reopens_automatically(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_config._state, "gemini_pro_retry_base_seconds", 20.)
    now = [1000.]
    control = ImageRequestControl(
        path=tmp_path / "control.sqlite3",
        clock=lambda: now[0],
        uniform=lambda low, high: high,
    )
    result = _exhaust_scene_window(control, now)

    assert result["status"] == "needs_review"
    assert result["first_needs_review_at"] == 1060.
    assert result["review_cycle"] == 0

    now[0] += 86400 * 2
    with pytest.raises(ImageRequestHeld, match="상한"):
        control.reserve(scope="job", scene="image:7", token="automatic-reopen")


def test_explicit_reopen_warns_before_24_hours_and_override_is_audited(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_config._state, "gemini_pro_retry_base_seconds", 20.)
    now = [1000.]
    control = ImageRequestControl(
        path=tmp_path / "control.sqlite3",
        clock=lambda: now[0],
        uniform=lambda low, high: high,
    )
    result = _exhaust_scene_window(control, now)
    review_at = result["first_needs_review_at"]
    now[0] = review_at + 3600

    kwargs = {
        "scope": "job", "scene": "image:7", "token": "reopen",
        "review_reopen_of": "failure-2", "expected_failure_n": 3,
        "review_reopen_first_needs_review_at": review_at,
    }
    with pytest.raises(ImageRequestHeld, match="24시간 냉각") as held:
        control.reserve(**kwargs)
    assert held.value.next_allowed_at == review_at + 86400

    reopened = control.reserve(**kwargs, review_reopen_override_cooldown=True)
    assert reopened["scene_attempt"] == 1
    assert reopened["failure_n"] == 3
    assert reopened["review_cycle"] == 1
    assert reopened["cooldown_override_used"] is True


def test_explicit_reopen_after_24_hours_starts_new_three_attempt_window(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_config._state, "gemini_pro_retry_base_seconds", 20.)
    now = [1000.]
    control = ImageRequestControl(
        path=tmp_path / "control.sqlite3",
        clock=lambda: now[0],
        uniform=lambda low, high: high,
    )
    result = _exhaust_scene_window(control, now)
    review_at = result["first_needs_review_at"]
    now[0] = review_at + 86400

    reopened = control.reserve(
        scope="job", scene="image:7", token="reopen",
        review_reopen_of="failure-2", expected_failure_n=3,
        review_reopen_first_needs_review_at=review_at,
    )
    assert reopened["scene_attempt"] == 1
    assert reopened["review_cycle"] == 1
    assert reopened["cooldown_override_used"] is False
    first = control.finish("reopen", retryable=True)
    assert first["status"] == "deferred"
    assert first["scene_attempt"] == 1

    now[0] = first["next_allowed_at"]
    control.reserve(scope="job", scene="image:7", token="window-2")
    second = control.finish("window-2", retryable=True)
    now[0] = second["next_allowed_at"]
    control.reserve(scope="job", scene="image:7", token="window-3")
    third = control.finish("window-3", retryable=True)
    assert third["status"] == "needs_review"
    assert third["scene_attempt"] == 3
    assert third["review_cycle"] == 1
    assert third["first_needs_review_at"] == now[0]


def test_existing_request_database_is_migrated_without_resetting_counts(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE scenes (scope TEXT, scene TEXT, count INTEGER, n INTEGER, "
            "next REAL, active TEXT, status TEXT, PRIMARY KEY(scope, scene))"
        )
        db.execute(
            "INSERT INTO scenes VALUES (?,?,?,?,?,?,?)",
            ("job", "scene:7", 2, 2, 0., None, "deferred"),
        )
    control = ImageRequestControl(path=path, clock=lambda: 1000.)
    reserved = control.reserve(scope="job", scene="image:7", token="third")
    assert reserved["scene_attempt"] == 3
    assert reserved["failure_n"] == 2
    assert reserved["review_cycle"] == 0
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(scenes)")}
    assert {"first_needs_review_at", "review_cycle"} <= columns


def test_historical_two_attempt_needs_review_cannot_bypass_cooldown_when_limit_is_three(
    tmp_path, monkeypatch,
):
    monkeypatch.setitem(runtime_config._state, "gemini_scene_request_limit", 2)
    now = [1000.]
    path = tmp_path / "control.sqlite3"
    control = ImageRequestControl(
        path=path, clock=lambda: now[0], uniform=lambda low, high: high,
    )
    for index in range(2):
        token = f"historical-{index}"
        control.reserve(scope="job", scene="image:7", token=token)
        state = control.finish(token, retryable=True)
        now[0] = state["next_allowed_at"]
    review_at = state["first_needs_review_at"]
    assert state["status"] == "needs_review"

    monkeypatch.setitem(runtime_config._state, "gemini_scene_request_limit", 3)
    resumed = ImageRequestControl(path=path, clock=lambda: now[0])
    with pytest.raises(ImageRequestHeld, match="냉각 재도전"):
        resumed.reserve(
            scope="job", scene="image:7", token="legacy-approved-retry",
            legacy_count=2, review_retry_of="historical-1", expected_failure_n=2,
        )

    now[0] = review_at + 86400
    reopened = resumed.reserve(
        scope="job", scene="image:7", token="approved-reopen",
        legacy_count=0, review_retry_of="historical-1", expected_failure_n=2,
        review_reopen_of="historical-1",
        review_reopen_first_needs_review_at=review_at,
    )
    assert reopened["scene_attempt"] == 1
    assert reopened["review_cycle"] == 1


def test_shared_project_backoff_caps_at_300_but_jitter_remains(tmp_path, monkeypatch):
    monkeypatch.setitem(runtime_config._state, "gemini_pro_retry_base_seconds", 20.)
    now = [0.]
    ranges = []
    control = ImageRequestControl(path=tmp_path / "control.sqlite3", clock=lambda: now[0],
        uniform=lambda low, high: ranges.append((low, high)) or low)
    for n in range(8):
        # 다른 job/scene에서도 같은 공급자 냉각을 공유한다.
        control.reserve(scope=f"job{n}", scene="image:0", token=str(n))
        result = control.finish(str(n), retryable=True)
        with pytest.raises(ImageRequestHeld, match="냉각"):
            control.reserve(scope="another", scene="image:1", token="early")
        now[0] = result["next_allowed_at"]
    assert ranges[:4] == [(10, 20), (20, 40), (40, 80), (80, 160)]
    assert ranges[4:] == [(150, 300)] * 4


@pytest.mark.parametrize("header", ["900", format_datetime(datetime.fromtimestamp(1900, timezone.utc), usegmt=True)])
def test_retry_after_beyond_local_cap_and_http_date(tmp_path, header):
    control = ImageRequestControl(path=tmp_path / "control.sqlite3", clock=lambda: 1000., uniform=lambda a, b: a)
    control.reserve(scope="job", scene="image:0", token="x")
    result = control.finish("x", retryable=True, retry_after=header)
    assert result["next_allowed_at"] == 1900


@pytest.mark.parametrize("header", [None, "invalid", "NaN", "inf", "-4"])
def test_invalid_retry_after_does_not_create_unbounded_or_negative_sleep(header):
    assert retry_after_seconds(header, 1000) == 0


def _process_reserve(db_path, token, queue):
    try:
        control = ImageRequestControl(path=Path(db_path))
        control.reserve(scope="job", scene="image:21", token=token)
        queue.put("reserved")
    except ImageRequestHeld:
        queue.put("held")


def test_concurrent_processes_cannot_double_reserve(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = [ctx.Process(target=_process_reserve, args=(str(tmp_path / "shared.sqlite3"), str(i), queue)) for i in range(6)]
    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert results.count("reserved") == 1
    assert results.count("held") == 5


def test_ledger_cross_thread_reservation_and_budget_remain_atomic(tmp_path):
    path = tmp_path / "ledger.json"
    def reserve(index):
        owner = audit(path, "image:21" if index % 2 else "template_regen:21")
        try:
            token = owner.before_attempt(attempt=1)
            owner.after_attempt(token, status_code=200, outcome="http_200")
            return "ok"
        except ImageRequestHeld:
            return "held"
    with ThreadPoolExecutor(max_workers=8) as pool:
        result = list(pool.map(reserve, range(20)))
    assert result.count("ok") <= 3
    assert len(json.loads(path.read_text())["items"]) == result.count("ok")


def test_corrupt_ledger_fails_closed_without_new_request(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{broken")
    with pytest.raises(ImageRequestHeld):
        audit(path).before_attempt(attempt=1)
    assert path.read_text() == "{broken"


def test_audit_is_required_and_does_not_fall_back(monkeypatch, tmp_path):
    monkeypatch.setattr("requests.post", lambda *a, **k: pytest.fail("POST 금지"))
    with pytest.raises(ImageRequestHeld, match="감사 객체 없음"):
        NanaBananaProvider()._generate_gemini_api("prompt", str(tmp_path / "x.png"), "test-key",
            model="gemini-3-pro-image", image_size="2K")


@pytest.mark.parametrize("stopped,available", [(True, True), (False, False)])
def test_redis_stop_or_unavailable_blocks_post(monkeypatch, tmp_path, stopped, available):
    class Redis:
        def exists(self, key):
            assert key == "job:stopped:99"
            return stopped
    monkeypatch.setattr(process_manager, "_get_redis", lambda: Redis() if available else None)
    monkeypatch.setattr("requests.post", lambda *a, **k: pytest.fail("중지 중 POST 금지"))
    owner = audit(tmp_path / "ledger.json", job_id=99)
    with pytest.raises(ImageRequestHeld):
        NanaBananaProvider()._generate_gemini_api("prompt", str(tmp_path / "x.png"), "test-key",
            model="gemini-3-pro-image", image_size="2K", request_audit=owner)
    assert owner.summary()["entries"] == []


def test_exact_payload_reference_output_hash_and_qa_join(monkeypatch, tmp_path):
    data = BytesIO()
    Image.new("RGB", (3, 3), "red").save(data, "PNG")
    encoded = base64.b64encode(data.getvalue()).decode()
    ref = tmp_path / "ref.png"
    ref.write_bytes(data.getvalue())
    payloads = []
    class Response:
        status_code = 200
        headers = {"x-goog-request-id": "provider-request-1"}
        def json(self):
            return {"candidates": [{"content": {"parts": [{"inlineData": {"data": encoded}}]}}]}
    def post(*args, **kwargs):
        payloads.append(kwargs["json"])
        return Response()
    monkeypatch.setattr("requests.post", post)
    owner = audit(tmp_path / "ledger.json", contract_fingerprint="approved-contract", run_id="run-1")
    output = tmp_path / "output.png"
    NanaBananaProvider()._generate_gemini_api("approved prompt", str(output), "SECRET-KEY-NOT-FOR-LOGS", [str(ref)],
        model="gemini-3-pro-image", image_size="2K", request_audit=owner)
    owner.record_quality(image_path=str(output), outcome="rejected", category="text_qa")
    owner.record_quality(image_path=str(output), outcome="rejected", category="surface_composition")
    summary = owner.summary()
    item = summary["entries"][0]
    evidence = item["request_evidence"]
    assert item["contract_fingerprint"] == "approved-contract"
    assert item["run_id"] == "run-1"
    assert evidence["payload_sha256"] == hashlib.sha256(json.dumps(payloads[0], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert evidence["references"][0]["sha256"] == hashlib.sha256(ref.read_bytes()).hexdigest()
    assert evidence["references"][0]["byte_count"] == len(ref.read_bytes())
    assert item["image_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert item["request_id"] == "provider-request-1"
    assert item["duration_seconds"] >= 0
    assert all(e["attempt_id"] == item["attempt_id"] for e in summary["qa_events"])
    assert summary["failure_counts"] == {"http": 0, "network": 0, "invalid_image": 0, "qa": 1, "surface_composition": 1}
    serialized = json.dumps(summary)
    assert "SECRET-KEY" not in serialized and encoded not in serialized


def test_http_200_without_image_is_not_qa_rejection(monkeypatch, tmp_path):
    class Response:
        status_code = 200
        headers = {}
        def json(self):
            return {"candidates": []}
    monkeypatch.setattr("requests.post", lambda *a, **k: Response())
    owner = audit(tmp_path / "ledger.json")
    with pytest.raises(ImageRequestHeld, match="해석 실패"):
        NanaBananaProvider()._generate_gemini_api("p", str(tmp_path / "output.png"), "test-key",
            model="gemini-3-pro-image", image_size="2K", request_audit=owner)
    assert owner.summary()["failure_counts"]["invalid_image"] == 1
    assert owner.summary()["failure_counts"]["qa"] == 0


def test_failure_does_not_refund_unknown_billing_exposure(tmp_path):
    owner = ProviderRequestAudit.for_path(path=tmp_path / "cost.json", scene_key="image:1",
        model="gemini-3-pro-image", unit_usd=.1, usd_krw=1000, budget_limit_krw=100)
    token = owner.before_attempt(attempt=1)
    owner.after_attempt(token, status_code=503, outcome="http_503")
    with pytest.raises(budget.ProviderRequestBudgetExceeded):
        owner.before_attempt(attempt=1)


def test_post_reservation_rechecks_shared_project_cooldown(tmp_path):
    control = ImageRequestControl(path=tmp_path / "state.sqlite3", clock=lambda: 1000., uniform=lambda a, b: b)
    control.reserve(scope="job1", scene="image:0", token="first")
    control.reserve(scope="job2", scene="image:0", token="second")
    control.finish("first", retryable=True)
    with pytest.raises(ImageRequestHeld, match="공유 프로젝트"):
        control.assert_dispatchable("second")


def test_invalid_runtime_limit_cannot_raise_cap_or_switch_state_database():
    with pytest.raises(ValueError):
        runtime_config.update(gemini_scene_request_limit=4)
    with pytest.raises(ValueError):
        runtime_config.update(gemini_request_state_path="different.sqlite3")


def test_worker_shared_cooldown_skips_scenes_without_recovery_or_false_completion(tmp_path, monkeypatch):
    from app.workers import images_worker
    from app.workers.images_worker import ImagesWorker
    class Pressure:
        def acquire(self):
            pass
        def outcome(self, *args):
            pass
        def recommended_concurrency(self, count):
            return 1
    class Provider:
        posts = 0
        def generate_image(self, **kwargs):
            owner = kwargs["gemini_request_audit"]
            token = owner.before_attempt(attempt=1)
            self.posts += 1
            state = owner.after_attempt(token, status_code=503, outcome="http_503", retryable=True)
            raise ImageRequestHeld("HTTP 503", status=state["status"], next_allowed_at=state["next_allowed_at"])
    monkeypatch.setattr(images_worker, "gemini_pressure", Pressure())
    monkeypatch.setattr(images_worker, "is_job_stopped", lambda job_id: False)
    monkeypatch.setattr(budget, "_job_path", lambda job, name: tmp_path / name)
    provider = Provider()
    scenes = [{"section": f"scene_{i}", "narration": f"승인 내레이션 {i}", "prompt_en": f"editorial scene {i}",
               "scene_type": "general", "visual_mode": "general", "art_direction": {"character_required": True},
               "image_profile": {"tier": "pro", "model": "gemini-3-pro-image", "image_size": "2K"}} for i in range(3)]
    for _ in range(2):
        worker = ImagesWorker()
        worker.tts_subtitle_sync = {}
        worker.evidence_audit = {}
        worker.visual_mix_plan = {}
        # 2026-10-02: 보류된 장면만 있어도(완료 0건) 더 이상 예외를 던지지 않고
        # requires_manual_review=True인 정상 응답을 돌려준다 — 완료된 장면이
        # 하나라도 섞여 있을 때 그걸 통째로 버리지 않기 위한 같은 계약이다.
        response = worker._generate_parallel_scenes(scenes_meta=scenes, directed_specs={}, market_snapshot={},
            character_reference_paths=[], character_style_prompt="none", lora_model_id=None,
            lora_trigger_word=None, lora_scale=None, ai_provider=provider, job_dir=tmp_path, job_id=99)
        assert response["scenes"] == []
        assert response["requires_manual_review"] is True
    assert provider.posts == 1
    review = json.loads((tmp_path / "image_request_review.json").read_text())
    assert review["assembly_allowed"] is False
    assert len(review["scenes"]) == 3
    assert all(scene["status"] == "deferred" for scene in review["scenes"])
    assert not (tmp_path / "images_manifest.json").exists()


def test_assembly_guard_blocks_held_scenes_and_accepts_only_cleared_gate(tmp_path):
    directory = tmp_path / "99" / "images"
    directory.mkdir(parents=True)
    write_request_review(directory, 99, [{"index": 2, "status": "needs_review"}])
    with pytest.raises(ImageRequestHeld, match="Fal/조립"):
        assert_request_review_cleared(99, root=tmp_path)
    write_request_review(directory, 99, [])
    assert_request_review_cleared(99, root=tmp_path)


def test_direct_assembly_is_blocked_before_loading_tts_or_invoking_motion(monkeypatch):
    from app.utils import image_request_control
    from app.workers.longform_worker import LongformWorker
    def held(job_id):
        raise ImageRequestHeld("미완료")
    monkeypatch.setattr(image_request_control, "assert_request_review_cleared", held)
    with pytest.raises(ImageRequestHeld, match="미완료"):
        LongformWorker().assemble("not-json", "not-json", "not-json", job_id=99)


def test_v5_router_qa_uses_exact_request_hash(monkeypatch, tmp_path):
    from app.v5.providers.router import ImageProviderRouter, RenderSpec
    from app.v5.providers.bfl_flux_provider import ImageResult
    from app.v5.scene.prompt_builder import BENCHMARK_SCENES
    from app.v5.scene.quality_gate import QualityGate
    owner = audit(tmp_path / "cost.json")
    image_bytes = b"mock-image-bytes"
    token = owner.before_attempt(attempt=1)
    owner.after_attempt(token, outcome="http_200", status_code=200, image_sha256=hashlib.sha256(image_bytes).hexdigest())
    card = object()
    monkeypatch.setattr(QualityGate, "score", lambda *args: card)
    monkeypatch.setattr(QualityGate, "next_action", lambda *args, **kwargs: "pass")
    spec = RenderSpec(BENCHMARK_SCENES[0], "prompt", "hero", request_audit=owner)
    result = ImageResult(image_bytes, "gemini-3-pro-image", 2048, 1152, None, None, "mock-request")
    assert ImageProviderRouter._score_audited(result, spec) is card
    event = owner.summary()["qa_events"][0]
    assert event["attempt_id"] == token
    assert event["category"] == "v5_qa"
    assert event["outcome"] == "passed"


def test_audit_metadata_does_not_invalidate_scene_lineage():
    from app.workers.images_worker import _stable_image_lineage_fingerprint
    scene = {"text": "승인 원문 143조 원", "prompt_en": "approved scene", "image_profile": {"tier": "pro"}}
    kwargs = dict(character_style_prompt="approved", character_reference_paths=[], use_composite=False,
        character_poses_dir=None, lora_model_id=None, lora_trigger_word=None, lora_scale=None)
    before = _stable_image_lineage_fingerprint(scene, **kwargs)
    after = _stable_image_lineage_fingerprint({**scene, "_request_job_id": 99, "_request_raw_path": "/tmp/raw.png"}, **kwargs)
    assert before == after
