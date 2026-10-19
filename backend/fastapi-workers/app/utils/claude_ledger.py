"""Claude(Anthropic) 호출 비용 기록 — 지금까지 이미지(Gemini)만 작업별 원장에 남고 Claude 호출은 어디에도 기록되지 않았다.

2026-10-19 운영자 요청: 크레딧이 왜 이렇게 드는지 요약 내역이 필요하다. 24시간에 Claude 호출이 3,500번이었지만 금액은 알 수 없었다.
- 토큰 수는 응답의 usage(실측)를 그대로 기록한다.
- 원화 금액은 토큰 × 단가(설정값, 기본 입력 $3·출력 $15 / 100만 토큰)로 계산한 *추정치*다. 단가는 공식 가격표로 확인하지 못한 값이라
  원장에 cost_status=estimated_unverified_price로 표시한다(Anthropic 콘솔의 Usage와 대조해야 한다).
- 호출하는 쪽은 `claude_create(purpose=..., model=..., ...)`만 쓰면 된다. 작업(job)은 `bind_job(job_id)`로 묶고, 묶지 않으면 기록 없이 호출만 한다."""
from __future__ import annotations

import contextvars
import functools
import logging
import os
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_JOB: contextvars.ContextVar[int | None] = contextvars.ContextVar("claude_ledger_job", default=None)

INPUT_USD_PER_MTOK = float(os.getenv("CLAUDE_INPUT_USD_PER_MTOK", "3.0"))      # 공식 가격표 미확인 — 설정으로 바꿀 수 있다
OUTPUT_USD_PER_MTOK = float(os.getenv("CLAUDE_OUTPUT_USD_PER_MTOK", "15.0"))


@contextmanager
def bind_job(job_id: int | None):
    """이 안에서 일어나는 Claude 호출을 job_id의 원장에 기록한다."""
    token = _JOB.set(int(job_id) if job_id is not None else None)
    try:
        yield
    finally:
        _JOB.reset(token)


def carry_job(fn):
    """스레드 풀로 넘길 함수에 지금의 job 묶음을 붙인다(스레드는 컨텍스트를 물려받지 않는다). 호출마다 job을 다시 묶으므로 여러 스레드가 동시에 써도 안전하다."""
    job_id = _JOB.get()

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with bind_job(job_id):
            return fn(*args, **kwargs)

    return wrapper


def claude_create(*, purpose: str, **kwargs):
    """Anthropic messages.create를 부르고, job이 묶여 있으면 사용 토큰과 추정 비용을 원장에 남긴다."""
    from anthropic import Anthropic

    response = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(**kwargs)
    job_id = _JOB.get()
    if job_id is not None:
        try:
            usage = getattr(response, "usage", None)
            from app.utils.budget import record_claude_usage

            record_claude_usage(
                job_id, purpose=purpose, model=str(kwargs.get("model") or ""),
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0), output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            )
        except Exception:                       # 기록 실패가 영상 제작을 막지 않는다
            logger.exception("Claude 사용량 기록 실패(호출 결과는 그대로 쓴다)")
    return response
