"""Tool registry: registration, OpenAI function-calling schema, timed+logged runs (C7)."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, Type

from pydantic import BaseModel, ValidationError

import agent.config as cfg
from agent.models import ToolResult

# name -> (args_model, fn, description)
TOOLS: dict[str, tuple[Type[BaseModel], Callable, str]] = {}
_LOG = cfg.LOG_DIR / "calls.jsonl"
_POOL = ThreadPoolExecutor(max_workers=8)


def tool(name: str, args_model: Type[BaseModel], description: str):
    def deco(fn: Callable):
        TOOLS[name] = (args_model, fn, description)
        return fn
    return deco


def _log_call(name, args, result: ToolResult, ms: float):
    try:
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": name, "args": args,
                "ok": result.ok,
                "error": (result.error.code if result.error else None),
                "n_items": len((result.data or {}).get("items", [])) if result.data else 0,
                "ms": round(ms, 1),
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def run(name: str, raw_args: dict, ctx: dict | None = None) -> ToolResult:
    """Validate args, run the tool with a timeout, log the call, return ToolResult."""
    ctx = ctx or {}
    if name not in TOOLS:
        return ToolResult.fail("INVALID_ARGUMENT", f"unknown tool: {name}")
    args_model, fn, _ = TOOLS[name]
    try:
        args = args_model(**raw_args)
    except ValidationError as e:
        return ToolResult.fail("INVALID_ARGUMENT", str(e.errors()[:3]))
    start = time.time()
    try:
        fut = _POOL.submit(fn, args, ctx)
        result = fut.result(timeout=cfg.TOOL_TIMEOUT_S)
    except FutureTimeout:
        result = ToolResult.fail("TIMEOUT", f"{name} exceeded {cfg.TOOL_TIMEOUT_S}s")
    except Exception as e:  # noqa: BLE001
        code = "UNAVAILABLE" if "not available" in str(e).lower() else "INTERNAL"
        result = ToolResult.fail(code, f"{type(e).__name__}: {e}")
    _log_call(name, raw_args, result, (time.time() - start) * 1000)
    return result


def openai_schemas() -> list[dict]:
    """DeepSeek/OpenAI-compatible function schemas (per tool arg model)."""
    out = []
    for name, (args_model, _, desc) in TOOLS.items():
        schema = args_model.model_json_schema()
        schema.pop("title", None)
        out.append({"type": "function", "function": {
            "name": name, "description": desc, "parameters": schema}})
    return out
