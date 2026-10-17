"""슬라이드형 씬별 생성의 진행 상황 기록.

2026-10-15 사용자 요청: 씬별 생성을 누르면 장면이 몇 개로 나뉘었는지 보이고, 만들어진
장면이 하나씩 쌓여 보여야 한다. 생성 요청은 끝날 때까지 한 번의 HTTP 호출이라, 워커가
장면 하나를 끝낼 때마다 진행 파일(progress.json)과 작은 썸네일을 남기고 UI가 이를 폴링한다.
진행 파일은 표시 전용이다 — 산출물·계보의 기준은 생성 응답과 DB 자산이다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

THUMB_WIDTH = 320


def progress_dir(job_id: int, data_dir: Path) -> Path:
    return data_dir / "jobs" / str(job_id) / "slides"


def progress_path(job_id: int, data_dir: Path) -> Path:
    return progress_dir(job_id, data_dir) / "progress.json"


def thumb_path(job_id: int, index: int, data_dir: Path) -> Path:
    return progress_dir(job_id, data_dir) / f"thumb_{index:03d}.jpg"


class SlideProgress:
    """스레드 안전한 진행 상황 기록기. 모든 쓰기는 원자적 파일 교체로 한다."""

    def __init__(self, job_id: int, data_dir: Path, *, keep_existing: bool = False):
        """keep_existing: 일부 장면만 다시 만드는 실행이면 이전 진행 기록을 유지하고
        대상 장면만 갱신한다(전체 목록이 사라지지 않게)."""
        self.job_id = job_id
        self.data_dir = data_dir
        self.keep_existing = keep_existing
        self._lock = threading.Lock()
        self._state: dict = {
            "job_id": job_id,
            "status": "running",
            "total": 0,
            "done": 0,
            "started_at": time.time(),
            "scenes": [],
        }

    def preparing(self) -> None:
        """장면 준비(대본·TTS 계보 검증, 기사 증거 부착) 중 — 이전 실행의 진행 파일을 비운다."""
        with self._lock:
            previous = read_progress(self.job_id, self.data_dir) if self.keep_existing else None
            if previous and previous.get("scenes"):
                self._state.update(
                    status="preparing", total=previous.get("total", 0),
                    done=previous.get("done", 0), scenes=previous["scenes"],
                )
            else:
                self._state.update(status="preparing", total=0, done=0, scenes=[])
            self._flush()

    def start(self, scenes: list[dict]) -> None:
        """장면 분할 결과를 먼저 알린다: 전체 개수와 장면별 내레이션 요약."""
        with self._lock:
            self._state["status"] = "running"
            pending = {
                scene["index"]: {
                    "index": scene["index"],
                    "text": str(scene.get("text") or "")[:60],
                    "status": "pending",
                }
                for scene in scenes
            }
            if self.keep_existing and self._state["scenes"]:
                merged = {item["index"]: item for item in self._state["scenes"]}
                merged.update(pending)
            else:
                merged = pending
            self._state["scenes"] = [merged[i] for i in sorted(merged)]
            self._state["total"] = len(self._state["scenes"])
            self._state["done"] = sum(1 for s in self._state["scenes"] if s["status"] in {"done", "failed"})
            self._flush()

    def scene_done(self, index: int, out: dict) -> None:
        thumb = self._make_thumb(index, out)
        failed = bool(out.get("slide_generation_error"))
        with self._lock:
            for item in self._state["scenes"]:
                if item["index"] != index:
                    continue
                item.update(
                    status="failed" if failed else "done",
                    visual_type=out.get("visual_type"),
                    # 후편집이 얹힌 뒤의 visual_type과 별개로, 배경 층이 무엇인지(Kling 후작업 대상 판단용)
                    base_type=((out.get("slide_layers") or {}).get("base") or {}).get("type") or out.get("visual_type"),
                    overlay_type=out.get("overlay_type"),
                    has_thumb=thumb,
                    # 돋보기(확대 보기)용: 완성된 장면의 원본 파일. 영상이 있으면 영상을 보여준다.
                    image_path=str(out.get("image_path") or "") or None,
                    video_path=str(out.get("stock_video_path") or "") or None,
                    disclaimer_banner=bool(out.get("disclaimer_banner")),
                    flags=list(out.get("quality_flags") or []),
                    error=out.get("slide_generation_error") or out.get("overlay_failure_reason"),
                )
                break
            self._state["done"] = sum(1 for s in self._state["scenes"] if s["status"] in {"done", "failed"})
            self._flush()

    def finish(self, status: str = "completed") -> None:
        with self._lock:
            self._state["status"] = status
            self._state["finished_at"] = time.time()
            self._flush()

    def _make_thumb(self, index: int, out: dict) -> bool:
        source = out.get("image_path")
        if not source or not Path(str(source)).is_file():
            return False
        try:
            with Image.open(source) as image:
                image = image.convert("RGB")
                height = max(1, round(image.height * THUMB_WIDTH / image.width))
                image.resize((THUMB_WIDTH, height)).save(thumb_path(self.job_id, index, self.data_dir), "JPEG", quality=78)
            return True
        except Exception:
            logger.warning("썸네일 생성 실패: job=%s scene=%s", self.job_id, index, exc_info=True)
            return False

    def _flush(self) -> None:
        path = progress_path(self.job_id, self.data_dir)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".json.tmp")
            temp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except Exception:
            logger.warning("진행 파일 기록 실패: job=%s", self.job_id, exc_info=True)


def read_progress(job_id: int, data_dir: Path) -> dict | None:
    path = progress_path(job_id, data_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
