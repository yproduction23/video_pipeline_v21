"""계획서 갱신은 달라진 장면만 새로 계획한다 (2026-10-19 운영자 요청: 좋았던 부분이 다시 만들면서 바뀌지 않게).

폐기한 전략: 계획서를 누를 때마다 전체 재생성 — 비용(약 2천 원)이 들고 승인한 장면의 배경·연출이 바뀌었다."""
from __future__ import annotations

import json

import pytest

from app.services import slide_edit_replan
from app.services.slide_edit_plan import assemble_plan, plan_input_hash
from app.services.slide_edit_replan import preserve_existing_plan

TEXTS = [
    "코스피는 7.57퍼센트 급락했습니다.",
    "외국인은 8거래일 연속으로 코스피를 팔았습니다.",
    "원인은 금리와 반도체 쏠림입니다.",
    "이 구조가 버틸 수 있는가가 핵심 질문입니다.",
    "그래서 시장은 다음 발표를 기다립니다.",
]


def _briefs(texts=TEXTS, duration=4.0):
    return {i: {"narration": t, "context": "", "duration": duration, "chunks": [{"text": "자막 줄", "start": 0.0, "end": duration}]}
            for i, t in enumerate(texts)}


def _previous():
    entries = [
        {"index": 0, "background": "board", "overlay": {"type": "big_number", "value": "7.57퍼센트"}},
        {"index": 1, "background": "stock", "stock_queries": ["stock market crash", "trader screen"], "overlay": {"type": "key_text", "text": "8거래일 연속으로"}},
        {"index": 2, "background": "board", "overlay": {"type": "key_text", "text": "금리와 반도체 쏠림"}},
        {"index": 3, "background": "board"},
        {"index": 4, "background": "stock", "stock_queries": ["calendar"], "overlay": None},
    ]
    return assemble_plan(_briefs(), entries, job_id=20)


@pytest.fixture(autouse=True)
def stub_stages(monkeypatch):
    """계획서 단계 함수는 호출 기록만 남긴다(어느 장면에 적용됐는지 확인하려고)."""
    calls = {"stages": [], "sequences": 0}

    def make(name):
        def stage(plan, *, llm_call=None):
            calls["stages"].append((name, [s["index"] for s in plan["scenes"]]))
            return plan
        return stage

    for name in ("fill_missing_icons_in_plan", "strip_unjudged_icons_from_plan", "fix_titles_in_plan", "fix_key_texts_in_plan", "drop_fake_lists_in_plan"):
        monkeypatch.setattr(slide_edit_replan, name, make(name))

    def attach(plan, *, llm_call=None, workers=4):
        calls["sequences"] += 1
        return plan

    monkeypatch.setattr(slide_edit_replan, "attach_sequences", attach)
    return calls


def _no_llm(*_args, **_kwargs):
    raise AssertionError("유지하는 장면에는 Claude를 부르지 않는다")


def test_unchanged_narration_keeps_every_decision_and_never_calls_claude(stub_stages):
    previous = _previous()
    before = json.loads(json.dumps(previous))

    kept = preserve_existing_plan(previous, _briefs(duration=4.6), highlight_end_index=1, llm_call=_no_llm)

    assert kept is not None
    for new, old in zip(kept["scenes"], before["scenes"]):
        for key in ("background", "stock_queries", "photo_brief", "overlay", "reason", "fallback_key_text"):
            assert new.get(key) == old.get(key), (old["index"], key)
        assert new["duration"] == 4.6                        # 길이는 새 값
        assert new["chunks"][0]["end"] == 4.6                # 자막 줄 시각도 새 값
    assert kept["input_hash"] == plan_input_hash(_briefs(duration=4.6))
    assert kept["highlight_end_index"] == 1
    assert kept["preserved"]["kept"] == [0, 1, 2, 3, 4] and kept["preserved"]["replanned"] == []
    assert stub_stages["stages"] == []                       # 검수도 다시 하지 않는다
    assert previous == before                                # 원본 객체는 건드리지 않는다


def test_only_changed_scene_is_replanned_and_reviewed(stub_stages):
    previous = _previous()
    before = json.loads(json.dumps(previous))
    texts = list(TEXTS)
    texts[2] = "원인은 금리와 환율 급등입니다."
    reply = json.dumps({"scenes": [{
        "index": 2, "background": "board", "reason": "새 대본",
        "overlay": {"type": "key_text", "text": "금리와 환율 급등"},
    }]}, ensure_ascii=False)

    kept = preserve_existing_plan(previous, _briefs(texts), llm_call=lambda s, m, t: reply)

    assert kept["preserved"]["replanned"] == [2] and kept["preserved"]["kept"] == [0, 1, 3, 4]
    assert kept["scenes"][2]["narration"] == texts[2]
    assert kept["scenes"][2]["overlay"]["text"] == "금리와 환율 급등"
    for index in (0, 1, 3, 4):                               # 그대로인 장면은 배경·후편집이 바뀌지 않는다
        for key in ("background", "stock_queries", "overlay"):
            assert kept["scenes"][index].get(key) == before["scenes"][index].get(key)
    assert all(indices == [2] for _name, indices in stub_stages["stages"]) and len(stub_stages["stages"]) == 5


@pytest.mark.parametrize("mutate", [
    lambda plan: plan.update(version="old-version"),                    # 계획서 버전이 다르면 유지하지 않는다
    lambda plan: plan["scenes"].pop(),                                  # 장면 번호가 맞지 않는다
])
def test_cannot_preserve_when_plan_shape_differs(mutate):
    previous = _previous()
    mutate(previous)

    assert preserve_existing_plan(previous, _briefs(), llm_call=_no_llm) is None


def test_too_many_changed_scenes_or_no_previous_plan_falls_back_to_full_build():
    texts = [f"{t} 완전히 다른 새 대본입니다." for t in TEXTS]

    assert preserve_existing_plan(_previous(), _briefs(texts), llm_call=_no_llm) is None
    assert preserve_existing_plan(None, _briefs(), llm_call=_no_llm) is None


def test_failed_replan_of_changed_scene_falls_back_instead_of_keeping_a_stale_scene():
    texts = list(TEXTS)
    texts[3] = "이 구조가 오래 갈지는 의문입니다."

    assert preserve_existing_plan(_previous(), _briefs(texts), llm_call=lambda s, m, t: "not json") is None


def test_replanned_scene_drops_only_its_own_base_cache(tmp_path):
    """다시 계획한 장면이 다음 씬별 생성에서 옛 배경을 재사용하지 않게 그 장면의 기록만 지운다."""
    from app.services.slide_edit_replan import drop_scene_base_caches

    for name in ("scene_004_stock_cache.json", "scene_004_ai_cache.json", "scene_005_stock_cache.json", "scene_004_stock.mp4"):
        (tmp_path / name).write_text("{}", encoding="utf-8")

    assert drop_scene_base_caches(tmp_path, [4, 9]) == [4]

    assert sorted(p.name for p in tmp_path.iterdir()) == ["scene_004_stock.mp4", "scene_005_stock_cache.json"]
