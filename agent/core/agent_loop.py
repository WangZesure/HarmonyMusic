"""The v6 Run loop: Input -> Model -> Tools -> results -> ... -> finish/clarify/text.

Core shape is the OpenAI single-agent prototype (Model/Instructions/Tools/Run/
Guardrails). Shared Run machinery lives in `run_support.py`; this module owns
only the hand-written loop (the LangGraph parity implementation is in
`graph_loop.py`).
"""
from __future__ import annotations

import logging
from typing import Callable

import agent.registry as registry
import agent.tools  # noqa: F401 — registers tools
from agent.agent_models import Filter
from agent.core.llm import LLM
from agent.core.run_support import (
    DEADLINE_S,
    MAX_SEARCHES,
    MAX_STEPS,
    MAX_TOKENS,
    MSG_ERROR,
    MSG_MAX_STEPS,
    call_model,
    classify_no_calls,
    degraded_output,
    emit,
    fast_trace,
    finalize_and_save,
    guard_output,
    handle_tool_calls,
    prepare_run,
)
from agent.session_store import key_for, store

__all__ = ["run", "MAX_STEPS", "MAX_SEARCHES", "DEADLINE_S", "MAX_TOKENS"]


def run(query: str, session_id: str, user_id: int | None = None,
        turn_id: str | None = None, llm: LLM | None = None,
        on_event: Callable[[dict], None] | None = None,
        should_stop: Callable[[], bool] | None = None) -> dict:
    """Execute one Run and return the structured Output.

    `should_stop` lets the HTTP layer abandon a Run whose client has disconnected;
    a cancelled Run does not write session state (the user never saw the turn).
    """
    key = key_for(user_id, session_id)
    try:
        from agent.warmup import READY
        READY.wait(timeout=50)
    except Exception:  # noqa: BLE001
        pass

    with store.lock(key):
        prep = prepare_run(query, session_id, user_id, turn_id,
                           llm=llm, on_event=on_event, should_stop=should_stop)
        if prep.cached is not None:
            return prep.cached
        ctx = prep.ctx

        if prep.fast is not None:
            ctx.last_filters = Filter()
            ctx.last_meta = {}
            ctx.trace = fast_trace(prep.fast)
            emit(on_event, {"stage": "fast_path", "text": "精确命中歌名，跳过模型决策…"})
            return finalize_and_save(ctx, prep.fast)

        try:
            if ctx.llm is None:
                ctx.llm = LLM()
            emit(on_event, {"stage": "understanding", "text": "正在理解你的需求…"})

            for _ in range(MAX_STEPS):
                guard = guard_output(ctx)
                if guard is not None:
                    ctx.output = guard
                    break
                msg = call_model(ctx)
                if not ctx.last_calls:
                    ctx.output = classify_no_calls(ctx, msg)
                    break
                stopped = handle_tool_calls(ctx, ctx.last_calls, ctx.llm_calls)
                if stopped is not None:
                    ctx.output = stopped
                    break

            if ctx.output is None:
                ctx.output = degraded_output("max_steps", MAX_STEPS, ctx.candidates,
                                             ctx.trace, MSG_MAX_STEPS, ctx.fallback)
        except Exception as e:  # noqa: BLE001 — never leave the client without a response
            logging.getLogger("agent").warning("agent run failed: %s", e, exc_info=True)
            ctx.output = degraded_output(f"error:{type(e).__name__}", 0, ctx.candidates,
                                         ctx.trace, MSG_ERROR, ctx.fallback)

        return finalize_and_save(ctx, ctx.output)
