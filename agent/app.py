"""FastAPI service exposing the tools (phase 2). The phase-3 Agent loop calls these.

    uvicorn agent.app:app --reload --port 8000
    GET  /health
    GET  /tools                 -> OpenAI function schemas
    POST /tool/{name}  {"...": ...}   [header X-User-Id for personalised tools]
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

import agent.index as index
import agent.registry as registry
import agent.tools  # noqa: F401  (register tools)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from agent.warmup import READY

    READY.clear()

    def warm() -> None:
        try:
            index._encoder()
        except Exception:  # noqa: BLE001
            pass
        try:
            from agent.core.rank import _reranker
            _reranker()
        except Exception:  # noqa: BLE001
            pass
        try:
            from agent.session_store import store as sessions
            sessions.ensure_schema()
            sessions.cleanup()
        except Exception:  # noqa: BLE001
            pass
        finally:
            READY.set()

    threading.Thread(target=warm, daemon=True).start()
    yield


app = FastAPI(title="HarmonyMusic 搜歌 Agent - 工具层", version="v1", lifespan=lifespan)


@app.middleware("http")
async def agent_key_guard(request: Request, call_next):
    """Optional shared-key guard; loopback-only is the default deployment."""
    required = os.getenv("AGENT_KEY", "").strip()
    if required and request.url.path != "/health":
        if request.headers.get("X-Agent-Key") != required:
            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return await call_next(request)


def _engine_run():
    """Run engine: hand-written loop (default) or the LangGraph prototype."""
    if os.getenv("AGENT_ENGINE", "hand").strip().lower() == "graph":
        from agent.core.graph_loop import run
    else:
        from agent.core.agent_loop import run
    return run


@app.get("/health")
def health() -> dict:
    from agent.warmup import READY
    ready = READY.is_set()
    # Do not touch the models here: /health is polled during warm-up and would
    # start a second concurrent load.
    return {"ok": ready, "ready": ready, "tools": len(registry.TOOLS),
            "embedding_available": ready,
            "session_store": "mysql",
            "index": index.index_meta()}


@app.get("/tools")
def tools() -> dict:
    return {"tools": registry.openai_schemas()}


@app.post("/agent/search")
async def agent_search(request: Request) -> dict:
    """Phase-3 single-turn agent search (non-streaming; SSE is phase 5)."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed body must be a 200 JSON error, not a 500
        return {"ok": False, "error": "invalid JSON body"}
    query = (body or {}).get("query", "")
    if not query:
        return {"ok": False, "error": "query required"}
    from agent.core.pipeline import search
    return {"ok": True, "data": search(query)}


@app.post("/agent/chat")
async def agent_chat(request: Request) -> dict:
    """Multi-turn search through the v6 Run loop (single agent)."""
    try:
        body = await request.json() or {}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "invalid JSON body"}
    query = body.get("query", "")
    if not query:
        return {"ok": False, "error": "query required"}
    sid = str(body.get("session_id") or "default")
    turn_id = body.get("turn_id")
    uid = request.headers.get("X-User-Id")
    run = _engine_run()
    data = await asyncio.to_thread(run, query, sid, int(uid) if uid else None, turn_id)
    return {"ok": True, "data": data}


@app.post("/agent/chat/stream")
async def agent_chat_stream(request: Request):
    """SSE variant: status events while the Run works, then one result event."""
    try:
        body = await request.json() or {}
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    query = body.get("query", "")
    if not query:
        return JSONResponse({"ok": False, "error": "query required"}, status_code=400)
    sid = str(body.get("session_id") or "default")
    turn_id = body.get("turn_id")
    uid = request.headers.get("X-User-Id")
    q: queue.Queue = queue.Queue()
    stop = threading.Event()

    def worker():
        try:
            run = _engine_run()
            out = run(query, sid, int(uid) if uid else None, turn_id,
                      on_event=lambda e: q.put(("status", e)),
                      should_stop=stop.is_set)
            q.put(("result", out))
        except Exception as e:  # noqa: BLE001
            q.put(("error", {"message": str(e)}))
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    async def watch_client():
        """Abandon the Run when the browser/app goes away."""
        try:
            while not stop.is_set():
                if await request.is_disconnected():
                    stop.set()
                    return
                await asyncio.sleep(0.5)
        except Exception:  # noqa: BLE001
            pass

    watcher = asyncio.create_task(watch_client())

    async def gen():
        try:
            while True:
                item = await asyncio.to_thread(q.get)
                if item is None:
                    break
                event, data = item
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            stop.set()
            watcher.cancel()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/tool/{name}")
async def call_tool(name: str, request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "invalid JSON body"}
    uid = request.headers.get("X-User-Id")
    ctx = {"user_id": int(uid)} if uid else {}
    return registry.run(name, body, ctx).model_dump()
