"""검증용 잡에 이전 Job의 배경·판정 캐시를 복사한다(2026-10-19 운영자 결정: 검증용 잡을 줄이고, 돌릴 때는 비용을 쓰지 않는다).

검증용 잡(9020~9034)은 각각 새 Job이라 스톡 검색·Gemini 이미지·외국 랜드마크 검사를 처음부터 다시 했다(잡 하나에 Gemini 약 ₩2~7.5천 + Claude 수백 번).
원본 Job의 캐시를 복사해 두면 같은 장면의 배경은 다시 만들지 않고 재사용한다(전체 실행은 reuse_base).

사용: python scripts/seed_test_job.py <원본 job> <새 job> [--data-dir /app/data/jobs] [--dry-run]
복사 대상: slides 폴더의 *_cache.json 과 그 캐시가 가리키는 스톡·AI 배경 파일, landmark_cache.json, evidence 폴더.
복사하지 않는 것: 계획서(edit_plan.json), 비용 원장(cost_ledger.json), 합성 결과 — 새 잡은 계획서부터 새로 만든다."""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path


def _referenced_files(cache_file: Path) -> list[Path]:
    """캐시 JSON 안에서 파일 경로로 보이는 값(절대 경로)을 모은다."""
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found: list[Path] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and re.match(r"^/.+\.(png|jpg|jpeg|mp4|webm)$", value):
            found.append(Path(value))

    walk(data)
    return found


def seed(source_job: int, target_job: int, data_dir: Path, *, dry_run: bool = False) -> dict:
    source = data_dir / str(source_job)
    target = data_dir / str(target_job)
    if not (source / "slides").is_dir():
        raise SystemExit(f"원본 Job {source_job}의 slides 폴더가 없습니다: {source / 'slides'}")
    copied: list[str] = []
    skipped: list[str] = []
    wanted: dict[Path, Path] = {}
    for cache in sorted((source / "slides").glob("*_cache.json")):
        wanted[cache] = target / "slides" / cache.name
        for ref in _referenced_files(cache):
            if str(ref).startswith(str(source)):
                wanted[ref] = target / "slides" / ref.name
    landmark = source / "landmark_cache.json"
    if landmark.is_file():
        wanted[landmark] = target / "landmark_cache.json"
    evidence = source / "evidence"
    for src, dst in wanted.items():
        if not src.is_file():
            skipped.append(str(src))
            continue
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        copied.append(src.name)
    evidence_copied = False
    if evidence.is_dir() and not (target / "evidence").exists():
        if not dry_run:
            shutil.copytree(evidence, target / "evidence")
        evidence_copied = True
    return {"copied": len(copied), "skipped": skipped, "evidence_copied": evidence_copied, "dry_run": dry_run}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source_job", type=int)
    parser.add_argument("target_job", type=int)
    parser.add_argument("--data-dir", default="/app/data/jobs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(seed(args.source_job, args.target_job, Path(args.data_dir), dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
