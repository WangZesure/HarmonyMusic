"""MySQL-backed session store for the v6 agent (agent_sessions).

Only the structured notes are stored, not the transcript:

    {
      "constraints": {...},
      "rejected_tags": [...],
      "seen": [song_id, ...],
      "last_shown": [song_id, ...],
      "last_search": {"query": ..., "filters": {...}, "candidates": [...],
                      "exhausted": bool, "turn_id": "..."},   # turn_id makes stale
                                                              # candidates detectable
      "pending_question": {turn_id, slot, question, options} | None,
      "summary": "...",
      "recent": [{"role": ..., "content": ...}],   # last 8 turns, for continuity
      "last_turn_id": "...",
      "last_output": {...}          # idempotency cache for retries
    }

The per-session lock is in-process (single uvicorn worker). If the service
ever runs multiple workers, replace `SessionStore.lock` with MySQL GET_LOCK
or Redis; the public interface stays the same.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pymysql

import agent.config as cfg

TTL_SECONDS = 30 * 60
_SCHEMA = Path(__file__).resolve().parents[1] / "db" / "migrations" / "001_agent_sessions.sql"


def key_for(user_id: int | None, session_id: str) -> str:
    uid = "anon" if user_id is None else str(user_id)
    return f"{uid}:{session_id}"


def _connect():
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, autocommit=True, **cfg.DB)


class SessionStore:
    def __init__(self, ttl_s: int = TTL_SECONDS):
        self.ttl_s = ttl_s
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def ensure_schema(self) -> None:
        """Idempotent bootstrap; the same DDL lives in db/migrations/001_agent_sessions.sql."""
        raw = _SCHEMA.read_text(encoding="utf-8")
        sql = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("--"))
        with _connect() as conn, conn.cursor() as cur:
            for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                cur.execute(stmt)

    def load(self, key: str) -> dict[str, Any]:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT state_json, updated_at FROM agent_sessions WHERE session_key=%s", (key,))
            row = cur.fetchone()
        if not row:
            return {}
        if time.time() - row["updated_at"].timestamp() > self.ttl_s:
            self.delete(key)
            return {}
        return json.loads(row["state_json"])

    def save(self, key: str, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False)
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_sessions (session_key, state_json, version)
                   VALUES (%s, %s, 1)
                   ON DUPLICATE KEY UPDATE
                     state_json=VALUES(state_json),
                     version=version+1,
                     updated_at=CURRENT_TIMESTAMP""",
                (key, payload),
            )

    def delete(self, key: str) -> None:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM agent_sessions WHERE session_key=%s", (key,))

    def cleanup(self) -> int:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM agent_sessions WHERE updated_at < NOW() - INTERVAL {int(self.ttl_s)} SECOND")
            return cur.rowcount

    @contextmanager
    def lock(self, key: str):
        with self._guard:
            if len(self._locks) > 1024:            # bounded lock map
                for k, lk in list(self._locks.items()):
                    if not lk.locked():
                        self._locks.pop(k, None)
            lk = self._locks.setdefault(key, threading.Lock())
        lk.acquire()
        try:
            yield
        finally:
            lk.release()

    def reset_locks(self) -> None:
        with self._guard:
            self._locks.clear()


store = SessionStore()
