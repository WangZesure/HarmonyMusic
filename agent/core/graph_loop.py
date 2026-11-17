"""LangGraph version of the v6 Run loop (same contract as agent_loop.run).

    START -> guard --(budget ok)--> llm --(tool_calls)--> tools --+
               |                    |                             |
               |                    +--(no tool_calls)--> finalize |
               +--(cancel/limit)-----------------------> finalize |
                                                              ^    |
    tools --(finish / ask_user / degrade)---------------------+    |
    tools --(continue)--> guard ----------------------------------+

The domain state and tool dispatch live in `run_support.py`; this module is
only the state graph. Fast Path and turn-id idempotency stay outside the graph,
as in the design (deterministic exceptions to the Run).
"""
from __future__ import annotations

import logging
from functools import cache
from typing import Callable, TypedDict

from langgraph.graph import StateGraph, START, END

import agent.registry as registry  # noqa: F401 — re-exported for tests
import agent.tools  # noqa: F401 — registers tools
from agent.agent_models import Filter
from agent.core.llm import LLM
from agent.core.run_support import (
    MAX_SEARCHES,
    MAX_STEPS,
    MSG_ERROR,
    MSG_MAX_STEPS,
    RunContext,
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


class GraphState(TypedDict, total=False):
    ctx: RunContext


# --------------------------------------------------------------------------- #
# nodes — each one is a single step of the hand-written loop
# --------------------------------------------------------------------------- #

def guard_node(state: GraphState) -> dict:
    ctx = state["ctx"]
    out = guard_output(ctx)
    if out is not None:
        ctx.output = out
    return {"ctx": ctx}


def llm_node(state: GraphState) -> dict:
    ctx = state["ctx"]
    msg = call_model(ctx)
    if not ctx.last_calls:
        ctx.output = classify_no_calls(ctx, msg)
    return {"ctx": ctx}


def tools_node(state: GraphState) -> dict:
    ctx = state["ctx"]
    out = handle_tool_calls(ctx, ctx.last_calls, ctx.llm_calls)
    if out is not None:
        ctx.output = out
    return {"ctx": ctx}


def finalize_node(state: GraphState) -> dict:
    ctx = state["ctx"]
    out = ctx.output or degraded_output("max_steps", MAX_STEPS, ctx.candidates,
                                        ctx.trace, MSG_MAX_STEPS, ctx.fallback)
    finalize_and_save(ctx, out)
    return {"ctx": ctx}


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #

def route_after_guard(state: GraphState) -> str:
    return "finalize" if state["ctx"].output else "llm"


def route_after_llm(state: GraphState) -> str:
    return "tools" if state["ctx"].last_calls else "finalize"


def route_after_tools(state: GraphState) -> str:
    return "finalize" if state["ctx"].output else "guard"


@cache
def _graph():
    g = StateGraph(GraphState)
    g.add_node("guard", guard_node)
    g.add_node("llm", llm_node)
    g.add_node("tools", tools_node)
    g.add_node("finalize", finalize_node)
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", route_after_guard, ["llm", "finalize"])
    g.add_conditional_edges("llm", route_after_llm, ["tools", "finalize"])
    g.add_conditional_edges("tools", route_after_tools, ["guard", "finalize"])
    g.add_edge("finalize", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# entry point, same contract as agent_loop.run
# --------------------------------------------------------------------------- #

def run(query: str, session_id: str, user_id: int | None = None,
        turn_id: str | None = None, llm: LLM | None = None,
        on_event: Callable[[dict], None] | None = None,
        should_stop: Callable[[], bool] | None = None) -> dict:
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
            final = _graph().invoke({"ctx": ctx}, config={"recursion_limit": 50})
            return final["ctx"].output
        except Exception as e:  # noqa: BLE001 — never leave the client without a response
            logging.getLogger("agent").warning("graph run failed: %s", e, exc_info=True)
            out = degraded_output(f"error:{type(e).__name__}", 0, ctx.candidates,
                                  ctx.trace, MSG_ERROR, ctx.fallback)
            return finalize_and_save(ctx, out)
