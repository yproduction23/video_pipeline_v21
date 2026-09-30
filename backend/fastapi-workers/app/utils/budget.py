"""Per-video cost preflight and durable local ledger.

The ledger records configured billable calls, not secrets or provider keys.
It deliberately estimates conservatively with a retry buffer before a job
starts, then records successful calls as it runs.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import runtime_config
from app.utils.image_request_control import (
    ImageRequestControl, ImageRequestHeld, canonical_scene, digest,
    validate_provider_status_check,
)

_LOCK = threading.Lock()


@contextmanager
def _ledger_lock(path: Path):
    """기존 비용 원장의 읽기-수정-쓰기도 프로세스 간에 직렬화한다."""
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(path.suffix + ".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

_GEMINI_IMAGE_RATE_KEYS = {
    "gemini-3-pro-image": "img_cost_pro_2k_usd",
    "gemini-3.1-flash-image": "img_cost_flash_2k_usd",
}


def _gemini_rate_key(model: str, service_tier: str | None = None) -> str | None:
    """모델과 실제 요청 tier에 맞는 중앙 단가 키를 반환한다."""
    if model == "gemini-3-pro-image" and str(service_tier or "").lower() == "priority":
        return "img_cost_pro_priority_2k_usd"
    return _GEMINI_IMAGE_RATE_KEYS.get(model)


def _pro_rate(cfg: dict[str, Any]) -> float:
    """운영 Pro tier의 단가를 선택하되 과거 테스트 설정과 호환한다."""
    if str(cfg.get("gemini_service_tier") or "priority").lower() == "priority":
        return float(cfg.get("img_cost_pro_priority_2k_usd", cfg["img_cost_pro_2k_usd"]))
    return float(cfg["img_cost_pro_2k_usd"])


class ProviderRequestBudgetExceeded(RuntimeError):
    """외부 이미지 요청 한 건을 예약할 수 없을 때 발생한다."""


class ProviderRequestAudit:
    """Gemini HTTP 시도 단위의 영속 예산 게이트와 감사 원장.

    공급자 재시도는 이미지 한 장의 부수 작업이 아니라 별도 과금 가능성이 있는
    외부 요청이다. 따라서 ``before_attempt``는 POST 직전에 비용을 점유하고
    디스크에 기록한다. 프로세스가 응답 대기 중 종료돼도 예약 기록은 남는다.
    """

    def __init__(
        self,
        *,
        path: Path,
        scene_key: str,
        provider: str,
        model: str,
        request_kind: str,
        unit_usd: float,
        usd_krw: float,
        budget_limit_krw: int,
        request_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._path = path
        self._scene_key = scene_key
        self._provider = provider
        self._model = model
        self._request_kind = request_kind
        self._unit_usd = float(unit_usd)
        self._usd_krw = float(usd_krw)
        self._budget_limit_krw = int(budget_limit_krw)
        self._request_metadata = dict(request_metadata or {})
        self._run_id = uuid.uuid4().hex
        self._control = ImageRequestControl() if provider == "gemini" else None
        self._last_token = None

    @classmethod
    def for_job(cls, *, job_id: int, scene_key: str, model: str,
                contract_fingerprint: str | None = None, run_id: str | None = None,
                service_tier: str | None = None) -> "ProviderRequestAudit":
        """V4 공용 비용 원장에 Gemini 시도를 기록한다."""
        rates = runtime_config.get()
        requested_tier = str(service_tier or rates.get("gemini_service_tier") or "priority").lower()
        rate_key = _gemini_rate_key(model, requested_tier)
        if rate_key is None:
            raise ValueError(f"지원하지 않는 Gemini 이미지 생성 모델입니다: {model}")
        preflight = load_preflight(job_id) or {}
        return cls(
            path=_job_path(job_id, "cost_ledger.json"),
            scene_key=scene_key,
            provider="gemini",
            model=model,
            request_kind=(
                "gemini_pro_request"
                if model == "gemini-3-pro-image"
                else "gemini_flash_image_request"
            ),
            unit_usd=float(rates.get(rate_key, rates[_GEMINI_IMAGE_RATE_KEYS[model]])),
            usd_krw=float(rates["usd_krw"]),
            budget_limit_krw=int(preflight.get("budget_limit_krw") or rates["max_budget_per_video_krw"]),
            request_metadata={"policy_version": preflight.get("policy_version", "runtime-default"), "job_id": job_id,
                              "contract_fingerprint": contract_fingerprint, "run_id": run_id,
                              "service_tier_requested": requested_tier},
        )

    @classmethod
    def for_path(
        cls,
        *,
        path: Path,
        scene_key: str,
        model: str,
        unit_usd: float,
        usd_krw: float,
        budget_limit_krw: int,
        request_metadata: dict[str, Any] | None = None,
    ) -> "ProviderRequestAudit":
        """V5처럼 작업 디렉터리가 다른 실행에도 같은 게이트를 사용한다."""
        if model not in _GEMINI_IMAGE_RATE_KEYS:
            raise ValueError(f"지원하지 않는 Gemini 이미지 생성 모델입니다: {model}")
        return cls(
            path=path,
            scene_key=scene_key,
            provider="gemini",
            model=model,
            request_kind=(
                "gemini_pro_request"
                if model == "gemini-3-pro-image"
                else "gemini_flash_image_request"
            ),
            unit_usd=unit_usd,
            usd_krw=usd_krw,
            budget_limit_krw=budget_limit_krw,
            request_metadata=request_metadata,
        )

    @classmethod
    def for_kling(cls, *, job_id: int, scene_key: str) -> "ProviderRequestAudit":
        """Kling image-to-video 요청도 네트워크 호출 전에 비용을 예약한다."""
        rates = runtime_config.get()
        preflight = load_preflight(job_id) or {}
        return cls(
            path=_job_path(job_id, "cost_ledger.json"),
            scene_key=scene_key,
            provider="fal",
            model="kling_image_to_video",
            request_kind="kling_request",
            unit_usd=float(rates["kling_cost_per_clip_usd"]),
            usd_krw=float(rates["usd_krw"]),
            budget_limit_krw=int(preflight.get("budget_limit_krw") or rates["max_budget_per_video_krw"]),
            request_metadata={"policy_version": preflight.get("policy_version", "runtime-default")},
        )

    def before_attempt(self, *, attempt: int, model: str | None = None, evidence: dict | None = None) -> str:
        """POST 직전에 예약을 영속화하고, 초과 시 네트워크 호출을 막는다."""
        if evidence:
            self._assert_job_running()
        amount_krw = _krw(self._unit_usd, self._usd_krw)
        token = uuid.uuid4().hex
        with _ledger_lock(self._path):
            ledger = _load_ledger(self._path)
            # HTTP 실패/타임아웃은 청구 없음의 증거가 아니다. 신규 Gemini 예약은
            # 성공 비용 추정치와 별도로 보수적인 최대 노출액을 기준으로 차단한다.
            exposure = sum(
                int(i.get("reserved_amount_krw", i.get("amount_krw", 0)))
                for i in ledger["items"] if i.get("status") != "not_dispatched"
            )
            projected = max(int(ledger.get("total_krw", 0)), exposure) + amount_krw if self._control else int(ledger.get("total_krw", 0)) + amount_krw
            if projected > self._budget_limit_krw:
                raise ProviderRequestBudgetExceeded(
                    f"이미지 요청 예산 초과: 예약 {amount_krw}원, "
                    f"누적 예정 {projected}원, 상한 {self._budget_limit_krw}원"
                )
            control = {}
            if self._control:
                scene = canonical_scene(self._scene_key)
                prior = [i for i in ledger["items"] if i.get("provider") == "gemini" and canonical_scene(i.get("scene_key", "")) == scene]
                if any(i.get("status") == "reserved" for i in prior):
                    raise ImageRequestHeld("원장에 응답 미확정 예약 존재")
                retry_of = self._request_metadata.get("approved_retry_of")
                reopen_after_cooldown = self._request_metadata.get("approved_reopen_after_cooldown")
                if reopen_after_cooldown not in (None, False, True):
                    raise ImageRequestHeld("냉각 후 재도전 승인 값이 boolean이 아님")
                if reopen_after_cooldown is True and not retry_of:
                    raise ImageRequestHeld("냉각 후 재도전에는 직전 503 승인 식별자가 필요함")
                if reopen_after_cooldown is True:
                    self._request_metadata["provider_status_check"] = validate_provider_status_check(
                        self._request_metadata.get("provider_status_check")
                    )
                # 원장은 평생 이력을 보존하지만 장면 상한은 승인된 재도전 이후의
                # 현재 냉각 구간만 센다. 상태 DB가 유실돼도 원장 마커로 복원한다.
                reopen_indices = [
                    index for index, item in enumerate(prior)
                    if (item.get("request_metadata") or {}).get("approved_reopen_after_cooldown") is True
                ]
                current_window = prior[reopen_indices[-1]:] if reopen_indices else prior
                if retry_of:
                    previous = prior[-1] if prior else {}
                    if (len(prior) != self._request_metadata.get("approved_retry_prior_count")
                            or previous.get("attempt_id") != retry_of
                            or previous.get("status") != "http_503" or previous.get("status_code") != 503
                            or previous.get("model") != (model or self._model)
                            or not evidence or evidence != previous.get("request_evidence")
                            or self._request_metadata.get("contract_fingerprint") != previous.get("contract_fingerprint")
                            or previous.get("request_control", {}).get("failure_n") != self._request_metadata.get("approved_retry_failure_n")):
                        raise ImageRequestHeld("단일 503 재시도 승인과 원장/요청 계약 불일치")
                    if (
                        previous.get("request_control", {}).get("status") == "needs_review"
                        and reopen_after_cooldown is not True
                    ):
                        raise ImageRequestHeld("검수 상태 장면은 냉각 재도전 승인 계약이 필요함")
                    legacy_first_review_at = None
                    if reopen_after_cooldown is True:
                        previous_control = previous.get("request_control") or {}
                        approved_first_review_at = self._request_metadata.get(
                            "approved_reopen_first_needs_review_at"
                        )
                        recorded_first_review_at = previous_control.get("first_needs_review_at")
                        if recorded_first_review_at is None:
                            legacy_review_entry = next(
                                (
                                    item for item in current_window
                                    if (item.get("request_control") or {}).get("status") == "needs_review"
                                ),
                                None,
                            )
                            try:
                                completed_at = datetime.fromisoformat(
                                    str((legacy_review_entry or {}).get("completed_at") or "")
                                )
                            except ValueError as exc:
                                raise ImageRequestHeld("정책 도입 전 최초 검수 완료 시각을 검증할 수 없음") from exc
                            if completed_at.tzinfo is None:
                                raise ImageRequestHeld("정책 도입 전 최초 검수 완료 시각에 시간대가 없음")
                            legacy_first_review_at = completed_at.timestamp()
                            if approved_first_review_at != legacy_first_review_at:
                                raise ImageRequestHeld("승인 검수 시각이 최초 검수 완료 시각과 다름")
                            self._request_metadata[
                                "approved_reopen_first_needs_review_at_source"
                            ] = "legacy_window_first_needs_review_completed_at"
                            self._request_metadata[
                                "approved_reopen_first_needs_review_attempt_id"
                            ] = legacy_review_entry.get("attempt_id")
                        elif recorded_first_review_at != approved_first_review_at:
                            raise ImageRequestHeld("냉각 후 재도전 승인과 최초 검수 시각 불일치")
                        if (
                            previous_control.get("status") != "needs_review"
                            or not isinstance(
                                self._request_metadata.get("approved_reopen_override_cooldown", False),
                                bool,
                            )
                        ):
                            raise ImageRequestHeld("냉각 후 재도전 승인과 최초 검수 시각 불일치")
                window_legacy_count = (
                    len(prior) - reopen_indices[-1]
                    if reopen_indices else len(prior)
                )
                if reopen_after_cooldown is True:
                    window_legacy_count = 0
                control = self._control.reserve(
                    scope=str(self._path.resolve()), scene=scene, token=token,
                    legacy_count=window_legacy_count,
                    review_retry_of=retry_of,
                    expected_failure_n=self._request_metadata.get("approved_retry_failure_n"),
                    review_reopen_of=retry_of if reopen_after_cooldown is True else None,
                    review_reopen_first_needs_review_at=(
                        self._request_metadata.get("approved_reopen_first_needs_review_at")
                        if reopen_after_cooldown is True else None
                    ),
                    review_reopen_legacy_first_needs_review_at=(
                        legacy_first_review_at if reopen_after_cooldown is True else None
                    ),
                    review_reopen_override_cooldown=(
                        self._request_metadata.get("approved_reopen_override_cooldown", False)
                        if reopen_after_cooldown is True else False
                    ),
                )
            item = {
                "kind": self._request_kind,
                "provider": self._provider,
                "model": model or self._model,
                "scene_key": self._scene_key,
                "attempt": int(attempt),
                "attempt_id": token,
                "estimated_usd": self._unit_usd,
                "amount_krw": amount_krw,
                "reserved_amount_krw": amount_krw,
                "cost_status": "unverified_until_console_reconciliation",
                "status": "reserved",
                "reserved_at": datetime.now(timezone.utc).isoformat(),
                "run_id": self._request_metadata.get("run_id") or self._run_id,
                "run_id_source": "caller" if self._request_metadata.get("run_id") else "audit_session",
                "request_control": control,
            }
            if evidence:
                item["request_evidence"] = evidence
                item["contract_fingerprint"] = self._request_metadata.get("contract_fingerprint") or evidence["payload_sha256"]
                item["contract_fingerprint_source"] = "caller" if self._request_metadata.get("contract_fingerprint") else "payload_sha256"
            if self._request_metadata:
                item["request_metadata"] = self._request_metadata
            ledger["items"].append(item)
            ledger["total_krw"] = int(ledger.get("total_krw", 0)) + amount_krw
            ledger["reserved_exposure_krw"] = projected
            ledger["budget_overrun_krw"] = max(0, projected - self._budget_limit_krw)
            _write_ledger(self._path, ledger)
        self._last_token = token
        return token

    def assert_dispatchable(self, token: str) -> None:
        if self._control:
            self._assert_job_running()
            self._control.assert_dispatchable(token)

    def _assert_job_running(self) -> None:
        job_id = self._request_metadata.get("job_id")
        if self._control and job_id is not None:
            from app.utils import process_manager
            client = process_manager._get_redis()
            if client is None:
                raise ImageRequestHeld("Redis 중지 상태 확인 불가; 유료 POST 차단")
            try:
                stopped = client.exists(f"{process_manager.STOP_KEY_PREFIX}{job_id}")
            except Exception as exc:
                raise ImageRequestHeld("Redis 중지 상태 조회 실패") from exc
            if stopped:
                raise ImageRequestHeld("사용자 중지 플래그 활성", status="stopped")

    def after_attempt(
        self,
        token: str,
        *,
        status_code: int | None = None,
        request_id: str | None = None,
        outcome: str,
        retryable: bool = False,
        permanent: bool = False,
        retry_after: str | None = None,
        duration_seconds: float | None = None,
        error_code: str | None = None,
        image_sha256: str | None = None,
        usage_metadata: dict[str, Any] | None = None,
        usage_metadata_status: str | None = None,
        service_tier_observed: str | None = None,
    ) -> dict:
        """응답과 성공 비용 추정치를 기록한다. 실제 청구/보수적 예약 노출액은 별개다."""
        with _ledger_lock(self._path):
            ledger = _load_ledger(self._path)
            control = self._control.finish(token, retryable=retryable, permanent=permanent, retry_after=retry_after) if self._control else {}
            for item in reversed(ledger["items"]):
                if item.get("attempt_id") != token:
                    continue
                item["status"] = outcome
                item["completed_at"] = datetime.now(timezone.utc).isoformat()
                item["request_control"] = {**item.get("request_control", {}), **control}
                item["duration_seconds"] = duration_seconds
                item["error_code"] = error_code
                item["image_sha256"] = image_sha256
                if usage_metadata_status is not None:
                    # 공급자 사용량 객체만 원형 보존한다. 응답 본문/인증 헤더는
                    # 기록하지 않으며 누락된 수치를 0이나 차감식으로 보충하지 않는다.
                    item["usage_metadata_status"] = usage_metadata_status
                    item["usage_metadata"] = usage_metadata
                    item["usage_metadata_source"] = "generateContent.response.usageMetadata"
                    item["usage_metadata_sha256"] = (
                        digest(json.dumps(usage_metadata, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode())
                        if usage_metadata is not None else None
                    )
                if status_code is not None:
                    item["status_code"] = int(status_code)
                if request_id:
                    item["request_id"] = str(request_id)[:256]
                if service_tier_observed:
                    # Priority 요청이 Standard로 graceful downgrade될 수 있어
                    # 요청값과 실제 처리 tier를 별도로 감사한다.
                    item["service_tier_observed"] = str(service_tier_observed)[:32].lower()

                # 성공 추정 합계에서 제외할 뿐 실제 청구 여부는 콘솔 대조 전까지 미확정이다.
                is_success = outcome in ("http_200", "succeeded", "http_201") or (status_code is not None and 200 <= status_code < 300)
                if not is_success:
                    item["amount_krw"] = 0
                    item["cost_status"] = "not_dispatched" if outcome == "not_dispatched" else "excluded_from_success_estimate_billing_unverified"
                break

            # 실패 항목의 성공 추정액은 위에서 0으로 바꿨다. 비이미지 비용도 보존한다.
            recalculated_total = sum(
                int(i.get("amount_krw", 0))
                for i in ledger.get("items", [])
                if isinstance(i, dict)
            )
            ledger["total_krw"] = recalculated_total
            ledger["reserved_exposure_krw"] = sum(int(i.get("reserved_amount_krw", i.get("amount_krw", 0)))
                for i in ledger["items"] if i.get("status") != "not_dispatched")
            limit = self._budget_limit_krw
            ledger["budget_overrun_krw"] = max(0, recalculated_total - limit)
            _write_ledger(self._path, ledger)
        return control

    def record_quality(self, *, image_path: str, outcome: str, category: str, raw_path: str | None = None) -> None:
        """이미지 hash로 HTTP 시도를 연결한다. 불명확하면 임의로 마지막 시도에 연결하지 않는다."""
        path = Path(raw_path or image_path)
        if not path.is_file():
            return
        raw_hash = digest(path.read_bytes())
        final_hash = digest(Path(image_path).read_bytes()) if Path(image_path).is_file() else None
        self._record_quality_hash(raw_hash, final_hash, outcome=outcome, category=category)

    def record_quality_bytes(self, image_bytes: bytes, *, outcome: str, category: str) -> None:
        """V5의 메모리 이미지도 별도 파일/재생성 없이 동일 요청에 연결한다."""
        image_hash = digest(image_bytes)
        self._record_quality_hash(image_hash, image_hash, outcome=outcome, category=category)

    def _record_quality_hash(self, raw_hash: str, final_hash: str | None, *, outcome: str, category: str) -> None:
        with _ledger_lock(self._path):
            ledger = _load_ledger(self._path)
            matches = [i for i in ledger["items"] if i.get("provider") == "gemini"
                       and canonical_scene(i.get("scene_key", "")) == canonical_scene(self._scene_key)
                       and i.get("image_sha256") == raw_hash]
            # 같은 바이트를 여러 요청이 반환했으면 현재 인스턴스의 토큰으로만 구분한다.
            match = next((i for i in matches if i["attempt_id"] == self._last_token), None)
            if match is None and len(matches) == 1:
                match = matches[0]
            event = {"attempt_id": match["attempt_id"] if match else None, "image_sha256": raw_hash,
                     "final_image_sha256": final_hash, "outcome": outcome, "category": category,
                     "scene_key": self._scene_key, "at": datetime.now(timezone.utc).isoformat(),
                     "link_status": "exact_hash" if match else "unresolved"}
            ledger.setdefault("qa_events", []).append(event)
            _write_ledger(self._path, ledger)

    def summary(self) -> dict[str, Any]:
        with _ledger_lock(self._path):
            ledger = _load_ledger(self._path)
            entries = [
                item for item in ledger["items"]
                if item.get("provider") == self._provider and canonical_scene(item.get("scene_key", "")) == canonical_scene(self._scene_key)
            ]
            events = [e for e in ledger.get("qa_events", []) if canonical_scene(e.get("scene_key", "")) == canonical_scene(self._scene_key)]
        return {"attempt_count": len(entries), "entries": entries, "qa_events": events,
                "failure_counts": {
                    "http": sum(int(e.get("status_code") or 0) >= 400 for e in entries),
                    "network": sum(e.get("status") == "network_error" for e in entries),
                    "invalid_image": sum(e.get("status") == "invalid_image" for e in entries),
                    "qa": sum(e["outcome"] == "rejected" and e["category"] != "surface_composition" for e in events),
                    "surface_composition": sum(e["outcome"] == "rejected" and e["category"] == "surface_composition" for e in events),
                }}


def _krw(usd: float, rate: float) -> int:
    return round(usd * rate)


def _estimate(pro_count: int, kling_count: int, cfg: dict[str, Any]) -> int:
    usd = (
        pro_count * _pro_rate(cfg)
        + kling_count * float(cfg["kling_cost_per_clip_usd"])
    )
    return round(_krw(usd, float(cfg["usd_krw"])) * (1 + float(cfg["budget_retry_buffer_pct"]) / 100))


def plan_preflight(
    scene_count: int,
    quality_tier: str,
    requested_pro: int,
    requested_kling: int,
    *,
    template_scene_count: int = 0,
    include_thumbnail: bool = True,
    budget_limit_krw: int | None = None,
    policy_version: str | None = None,
) -> dict[str, Any]:
    """모든 Pro 요청을 사전 예약 가능한지 확인하고 초과 시 즉시 거절한다."""
    if str(quality_tier or "pro").lower() != "pro":
        raise ValueError("이미지 품질 tier는 pro만 허용합니다.")
    cfg = runtime_config.get()
    configured_max = int(cfg["max_budget_per_video_krw"])
    max_budget = configured_max if budget_limit_krw is None else int(budget_limit_krw)
    if max_budget <= 0 or max_budget > configured_max:
        raise ValueError(f"작업별 예산 상한은 1~{configured_max}원 범위여야 합니다.")
    pro_count = max(0, scene_count)
    pro_count += 1  # [TASK 5] 썸네일 1장은 항상 Pro 2K로 고정 렌더링되므로 예산 견적에 +1 반영
    if not include_thumbnail:
        pro_count -= 1
    kling_count = max(0, requested_kling)
    estimated = _estimate(pro_count, kling_count, cfg)

    # 템플릿 장면은 보드 검출 실패 시 한 번만 재생성할 수 있으므로,
    # 해당 장면 tier 비용의 25%를 사전 예비비로 잡는다.
    retry_unit = _pro_rate(cfg)
    template_retry_reserve = round(_krw(template_scene_count * retry_unit * .25, float(cfg["usd_krw"])))
    estimated_with_reserve = estimated + template_retry_reserve
    allowed = estimated_with_reserve <= max_budget
    regen_enabled = allowed
    return {
        "planned_at": datetime.now(timezone.utc).isoformat(), "scene_count": scene_count,
        "quality_tier": "pro", "pro_scene_count": pro_count, "flash_scene_count": 0,
        "kling_clip_count": kling_count, "estimated_cost_krw": estimated_with_reserve, "budget_limit_krw": max_budget,
        "retry_buffer_pct": float(cfg["budget_retry_buffer_pct"]), "allowed": allowed,
        "policy_version": str(policy_version or "runtime-default"),
        "actions": [], "reason": None if allowed else "pro_only_plan_exceeds_budget",
        "template_scene_count": template_scene_count, "template_retry_reserve_krw": template_retry_reserve,
        "template_regeneration_enabled": regen_enabled,
        "rates": {
            "img_cost_pro_2k_usd": _pro_rate(cfg),
            "gemini_service_tier": str(cfg.get("gemini_service_tier") or "priority"),
            "kling_cost_per_clip_usd": cfg["kling_cost_per_clip_usd"],
            "usd_krw": cfg["usd_krw"],
        },
    }


def _job_path(job_id: int, name: str) -> Path:
    path = Path(f"/app/data/jobs/{job_id}")
    path.mkdir(parents=True, exist_ok=True)
    return path / name


def _load_ledger(path: Path) -> dict[str, Any]:
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        ledger = {"items": [], "total_krw": 0}
    except (OSError, ValueError) as exc:
        raise ImageRequestHeld("비용 원장 읽기 실패; 초기화하지 않고 차단") from exc
    if not isinstance(ledger, dict) or not isinstance(ledger.get("items"), list):
        raise ImageRequestHeld("비용 원장 손상; 초기화하지 않고 차단")
    ledger["total_krw"] = int(ledger.get("total_krw", 0))
    return ledger


def _write_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".tmp")
    with staged.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(ledger, ensure_ascii=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def write_preflight(job_id: int, plan: dict[str, Any]) -> None:
    path = _job_path(job_id, "budget_preflight.json")
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, path)


def load_preflight(job_id: int) -> dict[str, Any] | None:
    try:
        return json.loads(_job_path(job_id, "budget_preflight.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def load_cost_ledger_summary(job_id: int) -> dict[str, Any]:
    """Spring/UI가 읽을 수 있는 작업별 비용 원장 스냅샷을 반환한다.

    이미지·Kling처럼 FastAPI가 실제 provider 요청을 보낸 비용의 기준값은 이
    파일이다. 성공했거나 진행 중인 예약 상태만 합계에 포함한다.
    """
    cfg = runtime_config.get()
    path = _job_path(job_id, "cost_ledger.json")
    with _ledger_lock(path):
        ledger = _load_ledger(path)
        items = [dict(item) for item in ledger["items"] if isinstance(item, dict)]
    
    # 이 합계는 성공 추정액이다. 실패 요청의 실청구 여부는 콘솔 검증이 필요하다.
    total = sum(
        int(item.get("amount_krw", 0))
        for item in items
    )
    limit = int(cfg["max_budget_per_video_krw"])
    return {
        "job_id": int(job_id),
        "currency": "KRW",
        "total_krw": total,
        "reserved_exposure_krw": ledger.get("reserved_exposure_krw", total),
        "billing_verified": False,
        "qa_events": ledger.get("qa_events", []),
        "budget_limit_krw": limit,
        "remaining_krw": max(0, limit - total),
        "budget_overrun_krw": max(0, total - limit),
        "items": list(reversed(items[-100:])),
    }


def can_charge_overlay_vision(job_id: int, scene_key: str) -> bool:
    """Reserve vision fallback for at most one attempt per data scene.

    The detector is fail-closed: when this returns false callers use the
    cloud/anchored overlay path rather than making an unbudgeted vision call.
    """
    rate = float(os.getenv("OVERLAY_VISION_COST_USD", "0.03"))
    cfg = runtime_config.get()
    path = _job_path(job_id, "cost_ledger.json")
    with _ledger_lock(path):
        ledger = _load_ledger(path)
        duplicate = any(
            item.get("kind") == "overlay_vision" and item.get("scene_key") == scene_key
            for item in ledger.get("items", []) if isinstance(item, dict)
        )
        projected = int(ledger.get("total_krw", 0)) + _krw(rate, float(cfg["usd_krw"]))
        return not duplicate and projected <= int(cfg["max_budget_per_video_krw"])


def can_charge_openai_image(job_id: int, estimated_usd: float) -> bool:
    """2026-09-29: OpenAI 이미지 생성엔 Gemini의 ProviderRequestAudit 같은
    사전 예산 게이트가 없어서, 크레딧이 완전히 바닥날 때까지 계속 재시도되며
    실제로 1분짜리 테스트 job 하나에 $10 넘게 청구된 사고가 있었다. 요청
    직전에 같은 job의 전체 영상 예산(₩40,000/₩70,000)을 넘지 않는지 확인한다.
    """
    cfg = runtime_config.get()
    path = _job_path(job_id, "cost_ledger.json")
    with _ledger_lock(path):
        ledger = _load_ledger(path)
        projected = int(ledger.get("total_krw", 0)) + _krw(estimated_usd, float(cfg["usd_krw"]))
        return projected <= int(cfg["max_budget_per_video_krw"])


def record_openai_image_cost(job_id: int, actual_usd: float, *, scene_key: str | None = None) -> dict[str, Any]:
    """OpenAI 이미지 생성 1건의 실측 비용(응답 usage 기준)을 원장에 기록한다.

    단가가 씬마다 다르므로(참조 이미지 유무·재시도 프롬프트 길이), record_cost의
    고정 단가 표 대신 실제 비용을 그대로 받는다.
    """
    rates = runtime_config.get()
    amount = _krw(actual_usd, float(rates["usd_krw"]))
    path = _job_path(job_id, "cost_ledger.json")
    with _ledger_lock(path):
        ledger = _load_ledger(path)
        item = {
            "kind": "openai_image", "provider": "openai", "count": 1,
            "amount_krw": amount, "at": datetime.now(timezone.utc).isoformat(),
        }
        if scene_key:
            item["scene_key"] = scene_key
        ledger["items"].append(item)
        ledger["total_krw"] = int(ledger.get("total_krw", 0)) + amount
        limit = int(rates["max_budget_per_video_krw"])
        ledger["budget_overrun_krw"] = max(0, ledger["total_krw"] - limit)
        _write_ledger(path, ledger)
    return ledger


def record_cost(job_id: int, kind: str, count: int = 1, *, scene_key: str | None = None) -> dict[str, Any]:
    """비-Gemini 요청의 비용을 기록한다.

    Gemini는 POST 직전 ``ProviderRequestAudit``가 이미 비용을 점유한다.
    그 뒤 성공 처리에서 다시 기록하면 재시도와 성공 요청이 이중 청구되므로,
    같은 장면 키의 Gemini 시도가 있으면 이 함수는 기존 원장을 그대로 반환한다.
    """
    rates = runtime_config.get()
    if kind == "flash":
        raise ValueError("Flash 비용 기록은 Pro-only 이미지 정책에서 허용되지 않습니다.")
    unit_usd = {
        "pro": _pro_rate(rates),
        "kling": float(rates["kling_cost_per_clip_usd"]),
        "overlay_vision": float(os.getenv("OVERLAY_VISION_COST_USD", "0.03")),
    }.get(kind, 0.0)
    amount = _krw(unit_usd * count, float(rates["usd_krw"]))
    path = _job_path(job_id, "cost_ledger.json")
    with _ledger_lock(path):
        ledger = _load_ledger(path)
        provider_by_kind = {"pro": "gemini", "kling": "fal"}
        if scene_key and provider_by_kind.get(kind) and any(
            item.get("provider") == provider_by_kind[kind]
            and item.get("scene_key") == scene_key
            for item in ledger["items"]
            if isinstance(item, dict)
        ):
            return ledger
        item = {"kind": kind, "count": count, "amount_krw": amount, "at": datetime.now(timezone.utc).isoformat()}
        if scene_key:
            item["scene_key"] = scene_key
        ledger["items"].append(item)
        ledger["total_krw"] = int(ledger.get("total_krw", 0)) + amount
        limit = int(rates["max_budget_per_video_krw"])
        ledger["budget_overrun_krw"] = max(0, ledger["total_krw"] - limit)
        _write_ledger(path, ledger)
    return ledger
