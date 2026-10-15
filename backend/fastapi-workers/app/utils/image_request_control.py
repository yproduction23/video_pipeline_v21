"""이미지 POST의 단일 재시도 소유자. 동일 공유 볼륨의 프로세스 간 예약을 직렬화한다."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
from datetime import datetime
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from app import runtime_config

logger = logging.getLogger(__name__)


NEEDS_REVIEW_COOLDOWN_SECONDS = 24 * 60 * 60


class ImageRequestHeld(RuntimeError):
    """새 POST 없이 영속 상태를 보존하고 호출자를 반환한다."""

    def __init__(self, reason: str, *, status: str = "needs_review", next_allowed_at: float = 0):
        self.reason = reason
        self.status = status
        self.next_allowed_at = next_allowed_at
        super().__init__(f"이미지 요청 보류: {reason}; 상태={status}; 재개가능시각={next_allowed_at:.3f}")


def canonical_scene(key: str) -> str:
    """원본·배경·표면 재생성이 동일 장면 상한을 공유한다. 계약 hash는 키에 넣지 않는다."""
    # 보조 파일럿도 scene_021 형식을 사용한다. 구분자나 0 채움으로 한도가
    # 분리되지 않게 하되, 임의 이름의 끝 숫자를 다른 장면으로 합치지 않는다.
    match = re.search(r"(?:^|:)(?:image|background|template_regen|scene)[:_-](\d+)(?::variation:\d+)?$", key)
    return f"scene:{int(match[1])}" if match else key


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_request_review(job_dir: Path, job_id: int, held: list[dict]) -> None:
    """이미지 요청 단계의 미완료 상태를 남긴다. cleared는 전체 영상 승인과 다르다."""
    path = job_dir / "image_request_review.json"
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps({"job_id": job_id, "requires_manual_review": bool(held),
        "request_gate_cleared": not held, "assembly_allowed": not held, "scenes": held}, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(staged, path)


def assert_request_review_cleared(job_id: int, root: Path = Path("/app/data/jobs")) -> None:
    """조립을 직접 호출해 보류 장면을 우회하지 못하게 한다. 기존 작업은 기존 검증을 유지한다."""
    path = root / str(job_id) / "images" / "image_request_review.json"
    if not path.exists():
        return
    try:
        review = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ImageRequestHeld("이미지 보류 상태 파일을 검증할 수 없음") from exc
    if not isinstance(review, dict) or review.get("job_id") != job_id or review.get("request_gate_cleared") is not True or review.get("scenes"):
        raise ImageRequestHeld("미완료 이미지 요청 존재; Fal/조립 진입 차단")


def payload_evidence(payload: dict, *, endpoint: str, model: str, tier: str) -> dict:
    """전송 본문을 그대로 해시하되 키·base64·프롬프트 원문은 저장하지 않는다."""
    import base64
    parts = payload["contents"][0]["parts"]
    return {
        "endpoint": endpoint, "model": model, "service_tier": tier,
        "payload_hash_basis": "canonical_json_utf8_sorted_keys",
        "payload_sha256": digest(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()),
        "prompt_sha256": digest("\n".join(p["text"] for p in parts if "text" in p).encode()),
        "references": [
            {"sha256": digest(base64.b64decode(p["inlineData"]["data"])),
             "byte_count": len(base64.b64decode(p["inlineData"]["data"])),
             "mime_type": p["inlineData"]["mimeType"]}
            for p in parts if "inlineData" in p
        ],
    }


def retry_after_seconds(value: str | None, now: float) -> float:
    if not value:
        return 0.0
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
            seconds = dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp() - now
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


def validate_provider_status_check(check: object) -> dict:
    """사람이 확인한 공식 상태 대시보드 결과를 재도전 원장 계약으로 검증한다."""
    if not isinstance(check, dict):
        raise ImageRequestHeld("공급자 상태 확인 기록이 없음")
    source = str(check.get("source") or "").strip()
    if not source.startswith("https://status.cloud.google.com/"):
        raise ImageRequestHeld("공급자 상태 확인 출처가 Google Cloud 공식 대시보드가 아님")
    try:
        checked_at = datetime.fromisoformat(str(check.get("checked_at") or ""))
    except ValueError as exc:
        raise ImageRequestHeld("공급자 상태 확인 시각이 ISO-8601 형식이 아님") from exc
    if checked_at.tzinfo is None:
        raise ImageRequestHeld("공급자 상태 확인 시각에 시간대가 없음")
    result = str(check.get("result") or "").strip()
    if result not in {"no_official_incident", "official_incident_posted"}:
        raise ImageRequestHeld("공급자 상태 확인 판정값이 허용 목록과 다름")
    incident_url = check.get("incident_url")
    if result == "official_incident_posted" and not str(incident_url or "").startswith(
        "https://status.cloud.google.com/"
    ):
        raise ImageRequestHeld("공급자 상태 확인에 공식 장애 링크가 없음")
    return {
        "source": source,
        "checked_at": checked_at.isoformat(),
        "result": result,
        "incident_url": str(incident_url) if incident_url else None,
    }


class ImageRequestControl:
    """BEGIN IMMEDIATE로 검사와 점유를 원자화한다. 예약의 자동 만료/카운터 초기화는 없다."""

    def __init__(self, *, path: Path | None = None, clock=None, uniform=None):
        cfg = runtime_config.get()
        from app import config
        self.path = path or Path(cfg.get("gemini_request_state_path", config.GEMINI_REQUEST_STATE_PATH))
        self.project = str(cfg.get("gemini_project_scope", "default"))
        # 2026-10-13 job 14 씬2 재현: 이 영속 상한이 images_worker.py의 단일 요청
        # 내부 재시도 상한(max_retries = max(3, min(gemini_retry_max, 8)))보다
        # 낮으면, 정상 교정 과정이 재시도 기회를 다 쓰기 전에 "상한 도달"로 조기
        # 차단된다. 기본값은 runtime_config.py에서 gemini_retry_max와 같은 공식으로
        # 맞춰두지만, 명시적으로 더 낮게 설정된 값(과거 한도 재현 등)은 그대로
        # 존중한다 — 절대 천장(8)만 넘지 못하게 막는다.
        self.limit = min(8, max(1, int(cfg.get("gemini_scene_request_limit", 3))))
        self.base = max(1.0, float(cfg.get("gemini_pro_retry_base_seconds", 20)))
        self.clock = clock or time.time
        self.uniform = uniform or random.uniform

    def _db(self):
        db = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS scenes ("
                "scope TEXT, scene TEXT, count INTEGER, n INTEGER, next REAL, active TEXT, status TEXT, "
                "first_needs_review_at REAL, review_cycle INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY(scope, scene))"
            )
            # 기존 운영 DB를 삭제하거나 새 경로로 우회하지 않고 제자리에서 확장한다.
            columns = {row[1] for row in db.execute("PRAGMA table_info(scenes)")}
            if "first_needs_review_at" not in columns:
                db.execute("ALTER TABLE scenes ADD COLUMN first_needs_review_at REAL")
            if "review_cycle" not in columns:
                db.execute("ALTER TABLE scenes ADD COLUMN review_cycle INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE TABLE IF NOT EXISTS projects (name TEXT PRIMARY KEY, n INTEGER, next REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, scope TEXT, scene TEXT, project TEXT, completed INTEGER DEFAULT 0)")
            db.execute("BEGIN IMMEDIATE")
            return db
        except (OSError, sqlite3.Error) as exc:
            if db is not None:
                db.close()
            raise ImageRequestHeld("영속 요청 상태 저장소를 사용할 수 없음") from exc

    def reserve(self, *, scope: str, scene: str, token: str, legacy_count: int = 0,
                review_retry_of: str | None = None, expected_failure_n: int | None = None,
                review_reopen_of: str | None = None,
                review_reopen_first_needs_review_at: float | None = None,
                review_reopen_legacy_first_needs_review_at: float | None = None,
                review_reopen_override_cooldown: bool = False) -> dict:
        scene = canonical_scene(scene)
        db = self._db()
        try:
            row = db.execute(
                "SELECT count,n,next,active,status,first_needs_review_at,review_cycle "
                "FROM scenes WHERE scope=? AND scene=?", (scope, scene),
            ).fetchone()
            count, n, deadline, active, status, first_review_at, review_cycle = (
                row or (0, 0, 0, None, "ready", None, 0)
            )
            count = max(count, legacy_count)
            reopen_requested = bool(review_reopen_of)
            legacy_first_review_recovered = False
            if reopen_requested and status == "needs_review" and first_review_at is None:
                if (
                    review_reopen_legacy_first_needs_review_at is None
                    or not math.isfinite(review_reopen_legacy_first_needs_review_at)
                    or review_reopen_legacy_first_needs_review_at
                    != review_reopen_first_needs_review_at
                ):
                    raise ImageRequestHeld("정책 도입 전 검수 시각 복원 근거 불일치")
                # 이 값은 상위 감사 계층이 직전 원장의 timezone-aware completed_at과
                # 정확히 대조한 경우에만 전달한다. 임의 승인 시각은 받지 않는다.
                first_review_at = review_reopen_legacy_first_needs_review_at
                legacy_first_review_recovered = True
            if count >= self.limit and not reopen_requested:
                first_review_at = first_review_at if first_review_at is not None else self.clock()
                db.execute(
                    "INSERT OR REPLACE INTO scenes "
                    "(scope,scene,count,n,next,active,status,first_needs_review_at,review_cycle) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (scope, scene, count, n, deadline, active, "needs_review", first_review_at, review_cycle),
                )
                db.commit()
                raise ImageRequestHeld("장면 누적 요청 상한 도달")
            if active:
                raise ImageRequestHeld("응답 미확정 예약 존재; 자동 재요청 금지")
            if status == "needs_review" and review_retry_of and not reopen_requested:
                raise ImageRequestHeld("검수 상태 장면은 냉각 재도전 승인 계약이 필요함")
            reopened_from = None
            cooldown_override_used = False
            if reopen_requested:
                previous = db.execute(
                    "SELECT id,completed,project FROM attempts WHERE scope=? AND scene=? ORDER BY rowid DESC LIMIT 1",
                    (scope, scene),
                ).fetchone()
                if (
                    status != "needs_review"
                    or previous != (review_reopen_of, 1, self.project)
                    or n != expected_failure_n
                    or n < 1
                    or first_review_at is None
                    or review_reopen_first_needs_review_at != first_review_at
                ):
                    raise ImageRequestHeld("냉각 후 재도전 승인과 검수 이력 불일치")
                reopen_at = first_review_at + NEEDS_REVIEW_COOLDOWN_SECONDS
                if self.clock() < reopen_at and not review_reopen_override_cooldown:
                    raise ImageRequestHeld(
                        "24시간 냉각 미경과; 즉시 재도전은 사용자 명시 승인이 필요함",
                        status="needs_review", next_allowed_at=reopen_at,
                    )
                reopened_from = first_review_at
                cooldown_override_used = self.clock() < reopen_at
                count = 0
                first_review_at = None
                review_cycle += 1
                status = "ready"
            if review_retry_of:
                # 감사 원장이 검증한 단일 승인만 받는다. 상태 삭제/카운터 리셋 없이
                # 직전 완료 요청의 다음 슬롯을 같은 트랜잭션에서 점유한다.
                previous = db.execute(
                    "SELECT id,completed,project FROM attempts WHERE scope=? AND scene=? ORDER BY rowid DESC LIMIT 1",
                    (scope, scene)).fetchone()
                if (previous != (review_retry_of, 1, self.project) or count != legacy_count
                        or n != expected_failure_n or n < 1):
                    raise ImageRequestHeld("승인 재시도의 직전 요청/실패 이력 불일치")
            if status == "needs_review" and not review_retry_of:
                raise ImageRequestHeld("영구 오류 또는 검수 차단 상태")
            project = db.execute("SELECT n,next FROM projects WHERE name=?", (self.project,)).fetchone()
            deadline = max(deadline, project[1] if project else 0)
            if self.clock() < deadline:
                raise ImageRequestHeld("냉각시간 미경과", status="deferred", next_allowed_at=deadline)
            db.execute(
                "INSERT OR REPLACE INTO scenes "
                "(scope,scene,count,n,next,active,status,first_needs_review_at,review_cycle) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (scope, scene, count + 1, n, deadline, token, "reserved", first_review_at, review_cycle),
            )
            db.execute("INSERT INTO attempts(id,scope,scene,project) VALUES (?,?,?,?)", (token, scope, scene, self.project))
            db.commit()
            return {
                "scene_attempt": count + 1, "failure_n": n, "scene": scene,
                "project_scope": self.project, "review_cycle": review_cycle,
                "reopened_after_cooldown": reopen_requested,
                "reopened_from_first_needs_review_at": reopened_from,
                "cooldown_override_used": cooldown_override_used,
                "legacy_first_needs_review_at_recovered": legacy_first_review_recovered,
            }
        finally:
            db.close()

    def operator_reopen(self, *, scope: str, scene: str, approved_by: str, override_cooldown: bool = False) -> dict:
        """사람이 명시적으로 승인한 재도전만 needs_review 상태를 푼다.

        2026-10-14 사용자 재현(job 18 scene 11): reserve()의 review_retry_of/
        review_reopen_of 경로는 전부 budget.py의 단일 503 재시도 감사
        체인(ProviderRequestAudit.before_attempt)에서만 호출되고, 그 체인은
        직전 원장 항목이 정확히 http_503이어야만 통과한다. 그런데 scene 11은
        비전 검수 콘텐츠 거부(의미 불일치)로 needs_review에 들어갔지 503이
        아니어서, 실제로는 이 needs_review를 풀 수 있는 API 경로가 전혀 없었다
        (코드는 있지만 아무 호출자도 없는 죽은 경로). 이 메서드는 콘텐츠
        거부로 막힌 장면을 위한 별도의, 더 단순한 운영자 승인 경로다 — 비용
        예약/원장 매칭 로직은 전혀 건드리지 않고, 요청 상한 카운터만
        초기화해 다음 일반 재시도가 정상적으로 새 예약을 받게 한다.

        24시간 냉각이 지나지 않았으면 override_cooldown=True(운영자의 명시적
        "지금 바로" 승인)가 있어야만 통과한다.
        """
        scene = canonical_scene(scene)
        db = self._db()
        try:
            row = db.execute(
                "SELECT count,n,next,active,status,first_needs_review_at,review_cycle "
                "FROM scenes WHERE scope=? AND scene=?", (scope, scene),
            ).fetchone()
            if not row:
                raise ImageRequestHeld("재도전 승인 대상 장면 기록 없음")
            count, n, deadline, active, status, first_review_at, review_cycle = row
            if status != "needs_review":
                raise ImageRequestHeld(f"검수 대기 상태가 아니라 승인 대상이 아님 (현재 상태={status})")
            now = self.clock()
            reopen_at = (first_review_at or now) + NEEDS_REVIEW_COOLDOWN_SECONDS
            if first_review_at is not None and now < reopen_at and not override_cooldown:
                raise ImageRequestHeld(
                    "24시간 냉각 미경과; 즉시 재도전은 명시적 승인(override_cooldown)이 필요함",
                    status="needs_review", next_allowed_at=reopen_at,
                )
            next_review_cycle = review_cycle + 1
            db.execute(
                "UPDATE scenes SET count=0,n=0,next=0,active=NULL,status='ready',"
                "first_needs_review_at=NULL,review_cycle=? WHERE scope=? AND scene=?",
                (next_review_cycle, scope, scene),
            )
            db.commit()
            logger.warning(
                "운영자 재도전 승인: scope=%s scene=%s approved_by=%s review_cycle=%s->%s "
                "override_cooldown=%s 직전 누적 요청=%s",
                scope, scene, approved_by, review_cycle, next_review_cycle, override_cooldown, count,
            )
            return {
                "scene": scene, "previous_status": status, "previous_count": count,
                "review_cycle": next_review_cycle, "override_cooldown_used": bool(override_cooldown),
            }
        finally:
            db.close()

    def finish(self, token: str, *, retryable: bool, permanent: bool = False, retry_after: str | None = None) -> dict:
        db = self._db()
        try:
            attempt = db.execute("SELECT scope,scene,project,completed FROM attempts WHERE id=?", (token,)).fetchone()
            if not attempt:
                raise ImageRequestHeld("영속 예약 식별자 없음")
            scope, scene, project, completed = attempt
            count, n, deadline, active, status, first_review_at, review_cycle = db.execute(
                "SELECT count,n,next,active,status,first_needs_review_at,review_cycle "
                "FROM scenes WHERE scope=? AND scene=?", (scope, scene),
            ).fetchone()
            if not completed:
                if active != token:
                    raise ImageRequestHeld("예약 소유권 불일치")
                if retryable:
                    pn, pnext = db.execute("SELECT n,next FROM projects WHERE name=?", (project,)).fetchone() or (0, 0)
                    # 프로젝트 연쇄 실패도 영속화한다. 상한 이후에도 equal jitter를 유지한다.
                    b = min(300.0, self.base * 2 ** min(max(n, pn), 20))
                    delay = max(self.uniform(b / 2, b), retry_after_seconds(retry_after, self.clock()))
                    deadline = max(deadline, pnext, self.clock() + delay)
                    n += 1
                    db.execute("INSERT OR REPLACE INTO projects VALUES (?,?,?)", (project, pn + 1, deadline))
                status = "needs_review" if permanent or (retryable and count >= self.limit) else ("deferred" if retryable else "ready")
                if status == "needs_review" and first_review_at is None:
                    first_review_at = self.clock()
                db.execute(
                    "UPDATE scenes SET n=?,next=?,active=NULL,status=?,first_needs_review_at=? "
                    "WHERE scope=? AND scene=?",
                    (n, deadline, status, first_review_at, scope, scene),
                )
                db.execute("UPDATE attempts SET completed=1 WHERE id=?", (token,))
                db.commit()
            return {
                "scene_attempt": count, "failure_n": n, "next_allowed_at": deadline,
                "status": status, "first_needs_review_at": first_review_at,
                "review_cycle": review_cycle,
            }
        finally:
            db.close()

    def assert_dispatchable(self, token: str) -> None:
        """예약 후 다른 워커가 503을 받은 경우에도 POST 직전에 공유 냉각을 확인한다."""
        db = self._db()
        try:
            row = db.execute("SELECT project,completed FROM attempts WHERE id=?", (token,)).fetchone()
            if not row or row[1]:
                raise ImageRequestHeld("유효한 미완료 예약 없음")
            project = db.execute("SELECT next FROM projects WHERE name=?", (row[0],)).fetchone()
            if project and self.clock() < project[0]:
                raise ImageRequestHeld("공유 프로젝트 냉각", status="deferred", next_allowed_at=project[0])
        finally:
            db.close()
