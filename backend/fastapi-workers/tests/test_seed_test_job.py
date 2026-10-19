"""검증용 잡 캐시 복사(2026-10-19 운영자 결정): 검증용 잡이 배경을 처음부터 다시 만들지 않게 이전 Job의 캐시를 복사한다."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from seed_test_job import seed  # noqa: E402


def test_cache_files_and_the_media_they_point_to_are_copied_but_plans_and_ledgers_are_not(tmp_path):
    data = tmp_path / "jobs"
    slides = data / "20" / "slides"
    slides.mkdir(parents=True)
    clip = slides / "scene_001_stock.mp4"
    clip.write_bytes(b"clip")
    (slides / "scene_001_stock_cache.json").write_text(json.dumps({"video_path": str(clip), "pixabay_id": 9}), encoding="utf-8")
    (slides / "edit_plan.json").write_text("{}", encoding="utf-8")
    (data / "20" / "cost_ledger.json").write_text('{"items": [], "total_krw": 99}', encoding="utf-8")
    (data / "20" / "landmark_cache.json").write_text("{}", encoding="utf-8")
    (data / "20" / "evidence").mkdir()
    (data / "20" / "evidence" / "a.json").write_text("{}", encoding="utf-8")

    result = seed(20, 9035, data)

    target = data / "9035"
    assert (target / "slides" / "scene_001_stock_cache.json").is_file() and (target / "slides" / "scene_001_stock.mp4").read_bytes() == b"clip"
    assert (target / "landmark_cache.json").is_file() and (target / "evidence" / "a.json").is_file()
    assert not (target / "slides" / "edit_plan.json").exists() and not (target / "cost_ledger.json").exists()   # 계획서·비용 원장은 새로 시작한다
    assert result["copied"] == 3 and result["evidence_copied"] is True


def test_dry_run_copies_nothing(tmp_path):
    data = tmp_path / "jobs"
    (data / "20" / "slides").mkdir(parents=True)
    (data / "20" / "slides" / "scene_000_bg_cache.json").write_text("{}", encoding="utf-8")

    result = seed(20, 9036, data, dry_run=True)

    assert result["copied"] == 1 and not (data / "9036").exists()
