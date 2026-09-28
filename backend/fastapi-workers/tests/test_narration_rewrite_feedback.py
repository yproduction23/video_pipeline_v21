"""job 148(2026-08-05) 재현: 1분짜리 목표(target_chars=360)에서 재편집 결과가
문장 수는 만족했지만 총 분량이 허용 범위(약 331~414자)를 벗어났다. 이전
코드는 이 실패도 "scenes=N (need >= M)"이라며 실제로는 통과한 장면 수를
원인으로 잘못 로그하고, 엉뚱하게 "문장 수를 늘리라"는 교정 지시를 다음
시도에 보냈다. _rewrite_dialogue_to_target이 실제로 실패한 조건(총 분량 vs
장면 수 vs 문장당 길이)을 구분해 정확한 교정 지시를 보내는지 검증한다.

2026-08-05, 사용자 명시 지시로 문장 길이 목표를 26~38자에서 15~20자로
낮췄다(긴 회사명을 위한 하드 상한 28자). target_chars=360이면 target_scene_count는
round(360/18)=20, minimum_scene_count는 ceil(20*0.855)=18이므로, 아래
테스트는 20개 문장으로 이 새 최소치를 만족시킨다."""
from __future__ import annotations

import json

from app.workers.script_worker import ScriptWorker, _dialogue_char_count, _structured_script_from_dialogue_lines


def _worker() -> ScriptWorker:
    worker = ScriptWorker()
    worker._llm_provider_log = []
    return worker


def _source_script_body() -> str:
    lines = [f"원문 문장 {index}번을 설명하는 내용입니다." for index in range(11)]
    return _structured_script_from_dialogue_lines(lines)


def test_rewrite_reports_total_length_not_scene_count_when_only_length_fails(monkeypatch, caplog):
    # 20개 문장이 만들어지면 minimum_scene_count(18) 이상이라 장면 수 조건은
    # 항상 통과하지만, 총 글자 수는 매번 target_chars(360)의 허용 범위를
    # 넘겨 실패하게 만든다. 3번째 시도에서야 범위 안으로 들어와 성공한다.
    # 각 문장은 새 하드 상한(28자) 이내라 문장당 길이 조건도 항상 통과한다.
    worker = _worker()
    overshoot_lines = [f"이 문장은 목표보다 훨씬 길게 늘어난 문장입니다 {i:02d}" for i in range(1, 21)]
    ok_lines = [f"이 문장은 목표 분량에 맞춘 문장입니다 {i:02d}" for i in range(1, 21)]
    responses = [
        json.dumps(overshoot_lines, ensure_ascii=False),
        json.dumps(overshoot_lines, ensure_ascii=False),
        json.dumps(ok_lines, ensure_ascii=False),
    ]
    calls: list[str] = []

    def fake_llm(system, messages, max_tokens):
        calls.append(messages[0]["content"])
        return responses[len(calls) - 1]

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_llm)

    with caplog.at_level("WARNING"):
        result = worker._rewrite_dialogue_to_target(_source_script_body(), target_chars=360)

    warnings = [record.message for record in caplog.records if record.levelname == "WARNING"]
    assert any("total length contract" in message for message in warnings)
    assert not any("scene-count contract" in message for message in warnings)
    # correction feedback given to the 3rd call must mention total length, not scene count
    assert "총" in calls[2] and "문장 수" in calls[2] and "필요합니다" not in calls[2]
    assert "이 문장은 목표 분량에 맞춘 문장입니다" in result


def test_rewrite_reports_scene_count_when_too_few_lines_returned(monkeypatch, caplog):
    worker = _worker()
    monkeypatch.setattr(
        worker, "_call_llm_with_fallback",
        lambda *a, **k: json.dumps(["문장 하나만 왔습니다."], ensure_ascii=False),
    )

    with caplog.at_level("WARNING"):
        worker._rewrite_dialogue_to_target(_source_script_body(), target_chars=360)

    warnings = [record.message for record in caplog.records if record.levelname == "WARNING"]
    assert any("scene-count contract" in message for message in warnings)
    assert not any("total length contract" in message for message in warnings)


def test_rewrite_returns_closest_attempt_when_all_five_stay_below_target(monkeypatch, caplog):
    """2026-09-28 재현: 5번 재시도 모두 총 분량 미달로 실패하면, 이전 코드는
    원문(script_body)을 그대로 되돌렸다. 원문은 애초에 이 재편집을 부른
    원인이므로, 시도 중 목표에 가장 가까웠던 결과보다 항상 더 나쁘다.
    이 상태로 상류 시간 계약 검사(actual=1696, target=1800류)가 다시 실패해
    영상 생성 전체가 크래시했다. 가장 가까웠던 시도를 반환해야 한다."""
    worker = _worker()
    very_short = [f"짧음{i:02d}" for i in range(1, 21)]
    closer = [f"오늘은 조금 더 긴 문장 {i:02d}" for i in range(1, 21)]
    attempts = [very_short, very_short, closer, very_short, very_short]
    calls: list[str] = []

    def fake_llm(system, messages, max_tokens):
        calls.append(messages[0]["content"])
        return json.dumps(attempts[len(calls) - 1], ensure_ascii=False)

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_llm)

    expected_best = _dialogue_char_count(_structured_script_from_dialogue_lines(closer))
    worst = _dialogue_char_count(_structured_script_from_dialogue_lines(very_short))
    assert worst < expected_best < 342  # 두 후보 다 목표(360, 허용 하한 342) 미달, closer가 더 가깝다

    original = _source_script_body()
    with caplog.at_level("WARNING"):
        result = worker._rewrite_dialogue_to_target(original, target_chars=360)

    assert len(calls) == 5  # 5회 모두 소진하고 포기
    assert result != original
    assert _dialogue_char_count(result) == expected_best
    assert any("가장 가까웠던" in record.message for record in caplog.records)


def test_rewrite_accepts_short_caption_ending_that_stays_in_same_image(monkeypatch, caplog):
    worker = _worker()
    lines = ["오늘 코스피가 6860포인트를 기록했습니다."] * 18
    calls = 0

    def fake_llm(*args, **kwargs):
        nonlocal calls
        calls += 1
        return json.dumps(lines, ensure_ascii=False)

    monkeypatch.setattr(worker, "_call_llm_with_fallback", fake_llm)
    with caplog.at_level("WARNING"):
        result = worker._rewrite_dialogue_to_target(_source_script_body(), target_chars=360)

    assert calls == 1
    assert result.count("오늘 코스피가 6860포인트를 기록했습니다.") == 18
    assert any("동일 이미지 청크 계획" in record.message for record in caplog.records)
