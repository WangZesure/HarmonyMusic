"""Shared Run machinery for both engines (hand-written loop and LangGraph).

This module owns everything that is not orchestration:

- prompt / tool schema / session helpers
- the per-turn runtime (`RunContext`)
- `prepare_run` (lock is held by the caller) and `finalize_and_save`
- one tool-dispatch implementation (`handle_tool_calls`) used by both engines
- budget checks (`guard_output`) and no-tool-call classification

Keeping these here means a tool or guardrail fix is made once, not twice.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import agent.config as cfg
import agent.db as db
import agent.registry as registry
import agent.tools  # noqa: F401 — registers tools
from agent.agent_models import AGENT_TOOL_NAMES, Filter
from agent.session_store import key_for, store

MAX_STEPS = 5
MAX_SEARCHES = 2
DEADLINE_S = 45
MAX_TOKENS = 20_000

PROMPT_FILE = Path(__file__).resolve().parents[1] / "prompts" / "system.md"
_SYSTEM: str | None = None
_TOOLS: list[dict] | None = None

MSG_DEADLINE = "这次没能在时限内完成，请重试一次。"
MSG_TOKENS = "这次会话太长，请重试或换个说法。"
MSG_MAX_STEPS = "这次没找到确定的结果，能换个说法吗？"
MSG_FINISH_REJECTED = "结果没有通过校验，请重试或换个说法。"
MSG_TOOL_FAILED = "搜索服务出了点问题，请稍后再试。"
MSG_ERROR = "处理失败，请稍后再试。"
MSG_EMPTY = "没有找到符合的歌，换个说法试试？"
MSG_CANCELLED = "已取消。"


# --------------------------------------------------------------------------- #
# prompt / tools / session
# --------------------------------------------------------------------------- #

def system_prompt() -> str:
    global _SYSTEM
    if _SYSTEM is None:
        _SYSTEM = PROMPT_FILE.read_text(encoding="utf-8")
    return _SYSTEM


def tool_schemas() -> list[dict]:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = [s for s in registry.openai_schemas()
                  if s["function"]["name"] in AGENT_TOOL_NAMES]
    return _TOOLS


def new_session() -> dict:
    return {
        "constraints": {},
        "rejected_tags": [],
        "seen": [],
        "last_shown": [],
        "last_search": {},
        "pending_question": None,
        "summary": "",
        "recent": [],
        "last_turn_id": "",
        "last_output": None,
    }


def state_view(state: dict) -> dict:
    last = state.get("last_search") or {}
    cands = last.get("candidates") or []
    keys = ("song_id", "name", "artist", "genres", "moods", "scenes", "score")
    return {
        "dispatch": state.get("dispatch"),
        "constraints": state.get("constraints") or {},
        "rejected_tags": state.get("rejected_tags") or [],
        "seen": (state.get("seen") or [])[-20:],
        "last_shown": state.get("last_shown") or [],
        "last_search": {
            "query": last.get("query"),
            "filters": last.get("filters"),
            "exhausted": last.get("exhausted"),
            "turn_id": last.get("turn_id"),
            "candidates": [{k: c.get(k) for k in keys} for c in cands[:10]],
        },
        "pending_question": state.get("pending_question"),
        "summary": state.get("summary") or "",
    }


# --------------------------------------------------------------------------- #
# tool messages / trace
# --------------------------------------------------------------------------- #

def tool_message(call_id: str, payload: dict) -> dict:
    """Serialize a tool result for the model; never cut JSON mid-object."""
    p = payload
    try:
        content = json.dumps(p, ensure_ascii=False, default=str)
        if len(content) > 8000 and isinstance(p, dict):
            data = p.get("data")
            items = data.get("items") if isinstance(data, dict) else None
            if isinstance(items, list):
                dropped = 0
                while items and len(content) > 8000:
                    items.pop()
                    dropped += 1
                    content = json.dumps(p, ensure_ascii=False, default=str)
                if dropped:
                    data["truncated_items"] = dropped
                    content = json.dumps(p, ensure_ascii=False, default=str)
            if len(content) > 8000 and isinstance(data, dict):
                for key in ("facets", "kept_ids", "dropped", "languages"):
                    if len(content) <= 8000:
                        break
                    if key in data:
                        data.pop(key, None)
                        content = json.dumps(p, ensure_ascii=False, default=str)
        if len(content) > 8000:
            content = content[:8000] + "…[truncated]"
    except Exception:  # noqa: BLE001
        content = "…[unserializable tool result]"
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def model_payload(res) -> dict:
    """A tool result as the model should see it: no loop-only mirrors."""
    p = res.model_dump()
    data = p.get("data")
    if isinstance(data, dict):
        data.pop("candidates", None)
        data.pop("trace", None)
    return p


_TRACE_ARG_KEYS = ("query", "mode", "artist", "filters", "soft_tags", "exclude_tags",
                   "exclude_song_ids", "limit", "offset", "relax",
                   "drop_tags", "keep_tags", "drop_song_ids", "song_id")


def trace_args(args: dict) -> dict:
    """Small, JSON-safe subset of tool arguments for eval assertions."""
    out: dict[str, Any] = {}
    for k in _TRACE_ARG_KEYS:
        v = args.get(k)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list) and len(v) > 20:
            v = v[:20]
        out[k] = v
    return out


def emit(cb: Callable[[dict], None] | None, payload: dict) -> None:
    if cb:
        try:
            cb(payload)
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------------- #

def assistant_summary(output: dict) -> str:
    if output.get("type") == "songs":
        return f"已返回 {len(output.get('songs') or [])} 首歌（已展示）"
    if output.get("type") == "clarify":
        return "澄清：" + (output.get("question") or "")
    return output.get("message") or ""


def session_summary(state: dict) -> str:
    c = json.dumps(state.get("constraints") or {}, ensure_ascii=False)
    rejected = state.get("rejected_tags") or []
    return f"constraints={c}; rejected={rejected}; seen={len(state.get('seen') or [])}"


def degraded_output(reason: str, step: int, candidates: list[dict], trace: list[dict],
                    message: str, fallback_ids: list[int] | None = None) -> dict:
    deg = {"reasons": [reason], "at_step": step, "partial": bool(candidates or fallback_ids)}
    ids = ([int(c["song_id"]) for c in candidates[:5]] if candidates
           else [int(x) for x in (fallback_ids or [])[:5]])
    if ids:
        try:
            hits = db.to_hits(ids, match_type="semantic")
            songs = [{**h.model_dump(), "reason": f"{h.name} · {h.artist}"} for h in hits]
            if songs:
                return {"type": "songs", "songs": songs, "message": message, "degraded": deg}
        except Exception:  # noqa: BLE001 — degraded must never raise
            pass
    return {"type": "refusal", "songs": [], "message": message, "degraded": deg}


def finalize_output(state: dict, output: dict, pending: dict | None,
                    last_filters: Filter | None, last_meta: dict, query: str,
                    turn_id: str, session_id: str, trace: list[dict]) -> dict:
    output.setdefault("songs", [])
    output.setdefault("message", "")
    output.setdefault("degraded", None)
    output["turn_id"] = turn_id
    output["session_id"] = session_id
    output["trace"] = trace

    if output.get("type") == "songs":
        ids = [int(s["song_id"]) for s in output.get("songs") or [] if "song_id" in s]
        state["seen"] = list(dict.fromkeys((state.get("seen") or []) + ids))[-200:]
        state["last_shown"] = ids[:5]
        state["pending_question"] = None
    elif output.get("type") == "clarify":
        state["pending_question"] = pending
    else:
        state["pending_question"] = None

    if last_filters is not None:
        state["constraints"] = last_filters.model_dump(exclude_none=True)
    if last_meta:
        state["last_search"] = last_meta

    recent = state.get("recent") or []
    recent.append({"role": "user", "content": query})
    recent.append({"role": "assistant", "content": assistant_summary(output)})
    state["recent"] = recent[-8:]
    state["summary"] = session_summary(state)
    return output


def maybe_fast_path(query: str, state: dict) -> dict | None:
    """Only a first-turn, exact title/artist hit bypasses the loop."""
    if state.get("seen") or state.get("last_shown") or state.get("pending_question") or state.get("recent"):
        return None
    try:
        from agent.core.intent import fast_path
        fp = fast_path(query)
    except Exception:  # noqa: BLE001
        return None
    if not fp:
        return None
    song_id, _name, _artist = fp
    hits = db.to_hits([song_id], match_type="keyword")
    if not hits:
        return None
    h = hits[0]
    if _artist:
        want, got = _artist.lower().strip(), (h.artist or "").lower().strip()
        if not (want in got or got in want):
            return None
    return {
        "type": "songs",
        "songs": [{**h.model_dump(), "reason": f"{h.name} · {h.artist}"}],
        "message": "为你找到这首歌：",
        "degraded": None,
    }


# --------------------------------------------------------------------------- #
# per-turn runtime
# --------------------------------------------------------------------------- #

@dataclass
class RunContext:
    session: dict
    key: str
    query: str
    user_id: int | None
    session_id: str
    turn_id: str
    llm: Any = None
    on_event: Callable[[dict], None] | None = None
    should_stop: Callable[[], bool] | None = None
    start: float = field(default_factory=time.time)
    tokens: int = 0
    searches: int = 0
    llm_calls: int = 0
    finish_failures: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    allowed: set[int] = field(default_factory=set)
    excluded: set[int] = field(default_factory=set)
    keep_allowed: list[str] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    last_filters: Filter | None = None
    last_meta: dict = field(default_factory=dict)
    pending: dict | None = None
    fallback: list[int] | None = None
    messages: list[dict] = field(default_factory=list)
    last_calls: list = field(default_factory=list)
    last_message: Any = None
    output: dict | None = None

    @property
    def step(self) -> int:
        return max(1, self.llm_calls + 1)

    def cancelled(self) -> bool:
        try:
            return bool(self.should_stop and self.should_stop())
        except Exception:  # noqa: BLE001
            return False


@dataclass
class PreparedRun:
    ctx: RunContext | None
    cached: dict | None = None
    fast: dict | None = None


def prepare_run(query: str, session_id: str, user_id: int | None,
                turn_id: str | None, llm: Any = None,
                on_event: Callable[[dict], None] | None = None,
                should_stop: Callable[[], bool] | None = None) -> PreparedRun:
    """Load the session and build the runtime. The caller holds the session lock."""
    turn_id = turn_id or uuid.uuid4().hex[:12]
    key = key_for(user_id, session_id)
    try:
        session = store.load(key) or new_session()
    except Exception:  # noqa: BLE001
        session = new_session()
    if turn_id and session.get("last_turn_id") == turn_id and session.get("last_output"):
        return PreparedRun(None, cached=session["last_output"])

    try:
        fast = maybe_fast_path(query, session)
    except Exception:  # noqa: BLE001
        fast = None

    pending = session.get("pending_question")
    last_s = session.get("last_search") or {}
    seed_ok = bool(pending) and last_s.get("turn_id") == (pending or {}).get("turn_id")
    session["dispatch"] = "answer" if pending else "new"

    allowed: set[int] = set()
    excluded: set[int] = set()
    if pending and seed_ok:
        for sid in (session.get("last_shown") or []):
            try:
                allowed.add(int(sid))
            except Exception:  # noqa: BLE001
                pass
        for c in (last_s.get("candidates") or []):
            try:
                allowed.add(int(c["song_id"] if isinstance(c, dict) else c))
            except Exception:  # noqa: BLE001
                pass

    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "system", "content": "SESSION NOTES:\n" +
         json.dumps(state_view(session), ensure_ascii=False)},
    ]
    for m in (session.get("recent") or [])[-6:]:
        messages.append({"role": m.get("role", "user"), "content": m.get("content", "")})
    messages.append({"role": "user", "content": query})

    ctx = RunContext(
        session=session, key=key, query=query, user_id=user_id,
        session_id=session_id, turn_id=turn_id,
        llm=llm, on_event=on_event, should_stop=should_stop,
        last_meta=last_s,
        fallback=session.get("last_shown") if (pending and seed_ok) else None,
        allowed=allowed, excluded=excluded, messages=messages,
    )
    return PreparedRun(ctx, fast=fast)


def finalize_and_save(ctx: RunContext, output: dict) -> dict:
    out = finalize_output(ctx.session, output, ctx.pending, ctx.last_filters,
                          ctx.last_meta, ctx.query, ctx.turn_id, ctx.session_id,
                          ctx.trace)
    ctx.session["last_turn_id"] = ctx.turn_id
    ctx.session["last_output"] = out
    if not ctx.cancelled():
        try:
            store.save(ctx.key, ctx.session)
        except Exception:  # noqa: BLE001
            pass
    ctx.output = out
    return out


def fast_trace(fast: dict) -> list[dict]:
    return [
        {"step": 0, "actor": "fast_path", "tool": "search_songs", "ok": True,
         "ids": [int(s["song_id"]) for s in fast.get("songs") or [] if "song_id" in s]},
        {"step": 0, "tool": "finish", "ok": True, "fast": True},
    ]


# --------------------------------------------------------------------------- #
# one model step
# --------------------------------------------------------------------------- #

def guard_output(ctx: RunContext) -> dict | None:
    """Cancellation / deadline / token / step budget → a final output or None."""
    if ctx.cancelled():
        return {"type": "refusal", "songs": [], "message": MSG_CANCELLED,
                "degraded": {"reasons": ["cancelled"], "at_step": ctx.step,
                             "partial": bool(ctx.candidates)}}
    if time.time() - ctx.start > DEADLINE_S:
        return degraded_output("deadline", ctx.step, ctx.candidates, ctx.trace,
                               MSG_DEADLINE, ctx.fallback)
    if ctx.tokens > MAX_TOKENS:
        return degraded_output("tokens", ctx.step, ctx.candidates, ctx.trace,
                               MSG_TOKENS, ctx.fallback)
    if ctx.llm_calls >= MAX_STEPS:
        return degraded_output("max_steps", MAX_STEPS, ctx.candidates, ctx.trace,
                               MSG_MAX_STEPS, ctx.fallback)
    return None


def call_model(ctx: RunContext) -> Any:
    """One LLM call, appending the assistant message and trace."""
    remaining = DEADLINE_S - (time.time() - ctx.start)
    resp = ctx.llm.chat(
        ctx.messages, tools=tool_schemas(), temperature=0.0,
        timeout=min(cfg.LLM_TIMEOUT_S, max(5.0, remaining)),
        deadline=ctx.start + DEADLINE_S,
    )
    msg = resp.choices[0].message
    total = int(getattr(resp.usage, "total_tokens", 0) or 0) if resp.usage else 0
    calls = list(msg.tool_calls or [])
    assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if calls:
        assistant["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in calls
        ]
    ctx.messages.append(assistant)
    ctx.tokens += total
    ctx.llm_calls += 1
    ctx.last_message = msg
    ctx.last_calls = calls
    ctx.trace.append({"step": ctx.llm_calls, "actor": "llm", "tokens": total,
                      "tool_calls": [c.function.name for c in calls]})
    return msg


def classify_no_calls(ctx: RunContext, msg: Any) -> dict:
    text = (msg.content or "").strip()
    detail_ok = any(x.get("tool") == "song_detail" and x.get("ok") is True
                    for x in ctx.trace)
    finish_ok = any(x.get("tool") == "finish" and x.get("ok") is True
                    for x in ctx.trace)
    otype = "text" if text else "refusal"
    if text and ctx.searches and not ctx.candidates and not detail_ok:
        otype = "refusal"
    if ctx.candidates and not finish_ok:
        return degraded_output("no_finish", ctx.llm_calls, ctx.candidates, ctx.trace,
                               text or "模型没有调用 finish，先给你找到的候选。", None)
    return {"type": otype, "songs": [], "message": text or MSG_EMPTY, "degraded": None}


# --------------------------------------------------------------------------- #
# tool dispatch (shared by both engines)
# --------------------------------------------------------------------------- #

def _tool_ctx(ctx: RunContext) -> dict:
    return {
        "user_id": ctx.user_id,
        "state": ctx.session,
        "allowed_ids": ctx.allowed,
        "filters": ctx.last_filters,
        "keep_tags": ctx.keep_allowed,
        "turn_id": ctx.turn_id,
    }


def handle_tool_calls(ctx: RunContext, calls, step: int) -> dict | None:
    """Execute all tool calls. Returns a final output if the turn must stop."""
    for c in calls:
        name = c.function.name
        try:
            args = json.loads(c.function.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be an object")
        except Exception:  # noqa: BLE001
            ctx.messages.append(tool_message(c.id, {
                "ok": False,
                "error": {"code": "INVALID_ARGUMENT",
                          "message": "arguments must be a valid JSON object"},
            }))
            ctx.trace.append({"step": step, "tool": name, "error": "invalid_json"})
            continue

        if name == "search_songs" and ctx.searches >= MAX_SEARCHES:
            ctx.messages.append(tool_message(c.id, {
                "ok": False,
                "error": {"code": "INVALID_ARGUMENT",
                          "message": "search limit reached; call finish with ids you already have, or ask_user"},
            }))
            ctx.trace.append({"step": step, "tool": name, "error": "search_limit"})
            continue

        emit(ctx.on_event, {"stage": "tool", "text": f"正在调用 {name}…"})
        t0 = time.time()
        res = registry.run(name, args, _tool_ctx(ctx))
        ms = round((time.time() - t0) * 1000)

        if name == "finish":
            if res.ok:
                ctx.trace.append({"step": step, "tool": name, "ok": True, "ms": ms})
                emit(ctx.on_event, {"stage": "finish", "text": "正在整理结果…"})
                return res.data["output"]
            ctx.finish_failures += 1
            ctx.messages.append(tool_message(c.id, res.model_dump()))
            ctx.trace.append({"step": step, "tool": name, "ok": False, "ms": ms})
            if ctx.finish_failures >= 2:
                return degraded_output("finish_rejected", step, ctx.candidates,
                                       ctx.trace, MSG_FINISH_REJECTED, ctx.fallback)
            continue

        if name == "ask_user":
            if res.ok:
                ctx.trace.append({"step": step, "tool": name, "ok": True, "ms": ms})
                emit(ctx.on_event, {"stage": "clarify", "text": "需要你补充一点信息…"})
                ctx.pending = {
                    "turn_id": ctx.turn_id,
                    "slot": args.get("slot", ""),
                    "question": args.get("question", ""),
                    "options": args.get("options", []),
                }
                return res.data["output"]
            ctx.messages.append(tool_message(c.id, res.model_dump()))
            ctx.trace.append({"step": step, "tool": name, "ok": False, "ms": ms})
            continue

        if res.ok:
            data = res.data or {}
            ret_ids = [int(i["song_id"]) for i in (data.get("items") or [])
                       if "song_id" in i]
            if data.get("song_id") is not None:
                ret_ids = [int(data["song_id"])] + ret_ids

            if name == "search_songs":
                _apply_search(ctx, args, data)
            elif name == "refine_candidates":
                _apply_refine(ctx, data, ret_ids)

            ctx.messages.append(tool_message(c.id, model_payload(res)))
            ctx.trace.append({"step": step, "tool": name, "ok": True, "ms": ms,
                              "n_items": len(ret_ids), "ids": ret_ids,
                              "args": trace_args(args)})
        else:
            code = res.error.code if res.error else ""
            if code != "INVALID_ARGUMENT":
                ctx.failures[name] = ctx.failures.get(name, 0) + 1
            ctx.messages.append(tool_message(c.id, res.model_dump()))
            ctx.trace.append({"step": step, "tool": name, "ok": False, "ms": ms,
                              "error": code})
            if ctx.failures.get(name, 0) >= 2:
                return degraded_output("tool_failures:" + name, step, ctx.candidates,
                                       ctx.trace, MSG_TOOL_FAILED, ctx.fallback)
    return None


def _apply_search(ctx: RunContext, args: dict, data: dict) -> None:
    ctx.searches += 1
    items = data.get("items") or []
    applied = data.get("applied") or {}
    emit(ctx.on_event, {"stage": "search", "text": f"找到 {len(items)} 首候选…"})
    ctx.candidates = items
    ctx.allowed.update(int(i["song_id"]) for i in items)
    ctx.excluded.update(int(x) for x in (args.get("exclude_song_ids") or []))
    ctx.allowed.difference_update(ctx.excluded)
    ex_tags = set(applied.get("exclude_tags") or args.get("exclude_tags") or [])
    if ex_tags and ctx.allowed:
        tm = db.tag_map(list(ctx.allowed))
        ctx.allowed = {
            s for s in ctx.allowed
            if not (ex_tags & (set(tm.get(s, {}).get("genres", [])) |
                               set(tm.get(s, {}).get("moods", [])) |
                               set(tm.get(s, {}).get("scenes", []))))
        }
    ctx.keep_allowed = []
    f = applied.get("filters") or args.get("filters") or {}
    ctx.last_filters = Filter(**f) if f else Filter()
    ctx.last_meta = {
        "query": args.get("query"),
        "filters": f,
        "candidates": items[:60],
        "exhausted": data.get("exhausted", False),
        "turn_id": ctx.turn_id,
    }
    ctx.session["last_search"] = ctx.last_meta
    ctx.session["constraints"] = f
    ctx.session["rejected_tags"] = list(dict.fromkeys(
        (ctx.session.get("rejected_tags") or []) +
        (applied.get("exclude_tags") or args.get("exclude_tags") or [])))


def _apply_refine(ctx: RunContext, data: dict, ret_ids: list[int]) -> None:
    applied = data.get("applied") or {}
    kept_ids = [int(x) for x in (data.get("kept_ids") or ret_ids)]
    ctx.allowed = set(kept_ids)
    ctx.candidates = data.get("candidates") or (data.get("items") or [])
    drop = set(applied.get("drop_tags") or [])
    keep = set(applied.get("keep_tags") or [])
    ctx.excluded.update(int(x) for x in (applied.get("drop_song_ids") or []))
    ctx.allowed.difference_update(ctx.excluded)
    base = ctx.last_filters or Filter(**(ctx.session.get("constraints") or {}))
    f = base.model_copy(update={
        "genres": [t for t in base.genres if t not in drop],
        "moods": [t for t in base.moods if t not in drop],
        "scenes": [t for t in base.scenes if t not in drop],
    })
    ctx.last_filters = f
    ctx.keep_allowed = sorted(keep)
    ctx.session["constraints"] = f.model_dump(exclude_none=True)
    if drop:
        ctx.session["rejected_tags"] = list(dict.fromkeys(
            (ctx.session.get("rejected_tags") or []) + sorted(drop)))
    ctx.last_meta = {**ctx.last_meta, "candidates": ctx.candidates[:60],
                     "filters": f.model_dump(exclude_none=True),
                     "turn_id": ctx.turn_id}
    ctx.session["last_search"] = ctx.last_meta
