"""소재 즉석 생성(2026-10-19 운영자 결정): 필요한데 없는 소재를 작업당 예산(5천~1만원) 안에서 만들어 저장하고 다음부터 재사용한다."""
import json

from PIL import Image, ImageDraw

from app.services import slide_motifs
from app.services.slide_motif_factory import MAX_BUDGET_KRW, UNIT_COST_KRW, affordable_count, budget_krw, make_missing_motifs


def _library(tmp_path):
    directory = tmp_path / "motifs"
    directory.mkdir()
    (directory / "library.json").write_text("[]", encoding="utf-8")
    slide_motifs._library.cache_clear()
    return directory


def _generate(prompts):
    def generate(subject, raw_path, *, job_id, scene_key):
        prompts.append((subject, scene_key))
        image = Image.new("RGB", (200, 200), (0, 255, 0))
        ImageDraw.Draw(image).rectangle((50, 50, 150, 150), fill=(255, 201, 51), outline=(27, 34, 48), width=6)
        image.save(raw_path)
    return generate


def _naming(system, messages, max_tokens):
    return json.dumps({"items": [
        {"slug": "cushion", "subject": "a soft square cushion", "label": "완충재", "aliases": ["쿠션", "충격 흡수"], "skip": False},
        {"slug": "shock_wave", "subject": "concentric shock waves", "label": "충격파", "aliases": ["충격"], "skip": False},
        {"slug": "falling_animation", "subject": "", "label": "", "aliases": [], "skip": True},
    ]}, ensure_ascii=False)


def _orders():
    return [{"type": "motif", "name": "cushion", "description": "충격을 흡수하는 완충재"},
            {"type": "motif", "name": "shock_wave", "description": "퍼져 나가는 충격파"},
            {"type": "motion_part", "name": "pillar_disappear", "description": "기둥이 사라지는 움직임"}]


def test_budget_defaults_to_5000_and_never_exceeds_10000():
    assert budget_krw(None) == 5000 and budget_krw(7000) == 7000 and budget_krw(50_000) == MAX_BUDGET_KRW == 10_000
    assert affordable_count(5000) == 14 and affordable_count(10_000) == 29 and affordable_count(300) == 0


def test_missing_motifs_are_made_registered_and_reusable_within_the_budget(tmp_path):
    directory, prompts = _library(tmp_path), []

    result = make_missing_motifs(
        _orders(), job_id=9100, budget=UNIT_COST_KRW, directory=directory, generate=_generate(prompts),
        llm_call=_naming, vision_call=lambda system, text, images: json.dumps({"ok": True, "reason": "ok"}),
    )

    assert [c["slug"] for c in result["created"]] == ["cushion"]          # 예산이 한 장뿐이라 첫 주문만
    assert result["spent_krw"] == UNIT_COST_KRW and len(prompts) == 1     # 동작(motion_part) 주문은 소재가 아니라 건너뛴다
    assert slide_motifs.resolve_motif("쿠션", directory) == "완충재"        # 별칭으로 바로 쓸 수 있다
    entry = json.loads((directory / "library.json").read_text(encoding="utf-8"))[0]
    assert entry["source"]["on_demand"] is True and entry["source"]["review"] == "pending"   # 운영자 육안 확인 대기 표시
    assert (directory / "cushion.png").is_file()


def test_a_motif_that_fails_the_vision_check_is_not_registered_but_still_costs_budget(tmp_path):
    directory = _library(tmp_path)

    result = make_missing_motifs(
        _orders()[:1], job_id=9100, budget=5000, directory=directory, generate=_generate([]), llm_call=_naming,
        vision_call=lambda system, text, images: json.dumps({"ok": False, "reason": "글자가 보인다"}),
    )

    assert result["created"] == [] and result["spent_krw"] == UNIT_COST_KRW
    assert "검수 불합격" in result["failed"][0]["reason"]
    assert not (directory / "cushion.png").exists() and json.loads((directory / "library.json").read_text(encoding="utf-8")) == []


def test_already_registered_motifs_and_unaffordable_budgets_make_nothing(tmp_path):
    directory = _library(tmp_path)
    prompts = []
    make_missing_motifs(_orders()[:1], job_id=1, budget=5000, directory=directory, generate=_generate(prompts), llm_call=_naming,
                        vision_call=lambda s, t, i: json.dumps({"ok": True}))

    again = make_missing_motifs(_orders()[:1], job_id=1, budget=5000, directory=directory, generate=_generate(prompts), llm_call=_naming,
                                vision_call=lambda s, t, i: json.dumps({"ok": True}))
    broke = make_missing_motifs(_orders(), job_id=1, budget=100, directory=directory, generate=_generate(prompts), llm_call=_naming)

    assert again["created"] == [] and broke["created"] == [] and len(prompts) == 1   # 이미 있으면 다시 만들지 않고, 예산이 없으면 만들지 않는다
