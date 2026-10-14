"""FastAPI service exposing the tools (phase 2). The phase-3 Agent loop calls these.

    uvicorn agent.app:app --reload --port 8000
    GET  /health
    GET  /tools                 -> OpenAI function schemas
    POST /tool/{name}  {"...": ...}   [header X-User-Id for personalised tools]
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

import agent.index as index
import agent.registry as registry
import agent.tools  # noqa: F401  (register tools)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # warm the embedding model once so tool calls never pay the cold-load timeout
    try:
        index._encoder()
    except Exception:  # noqa: BLE001
        pass
    yield


app = FastAPI(title="HarmonyMusic 搜歌 Agent - 工具层", version="v1", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "tools": len(registry.TOOLS),
            "embedding_available": index.embedding_available(),
            "index": index.index_meta()}


@app.get("/tools")
def tools() -> dict:
    return {"tools": registry.openai_schemas()}


@app.post("/agent/search")
async def agent_search(request: Request) -> dict:
    """Phase-3 single-turn agent search (non-streaming; SSE is phase 5)."""
    body = await request.json()
    query = (body or {}).get("query", "")
    if not query:
        return {"ok": False, "error": "query required"}
    from agent.core.pipeline import search
    return {"ok": True, "data": search(query)}


@app.post("/agent/chat")
async def agent_chat(request: Request) -> dict:
    """Phase-4 multi-turn search: keeps session constraints + already-shown songs."""
    body = await request.json() or {}
    query = body.get("query", "")
    if not query:
        return {"ok": False, "error": "query required"}
    sid = str(body.get("session_id") or "default")
    uid = request.headers.get("X-User-Id")
    from agent.core.chat import chat
    return {"ok": True, "data": chat(sid, query, int(uid) if uid else None)}


@app.post("/tool/{name}")
async def call_tool(name: str, request: Request) -> dict:
    body = await request.json()
    uid = request.headers.get("X-User-Id")
    ctx = {"user_id": int(uid)} if uid else {}
    return registry.run(name, body, ctx).model_dump()
