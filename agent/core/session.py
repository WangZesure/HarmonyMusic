"""Session state for multi-turn search (phase 4).

Keeps the current Intent (constraints) and the songs already shown, so "换一首"
excludes them and "更X一点" edits the constraint. In-memory (single process);
phase 5 can swap in Redis behind the same API.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from agent.core.intent import Intent


@dataclass
class Session:
    id: str
    intent: Intent | None = None
    seen: list[int] = field(default_factory=list)     # song_ids already returned
    turns: int = 0
    updated: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, ttl_s: int = 1800):
        self._d: dict[str, Session] = {}
        self._ttl = ttl_s
        self._lock = threading.Lock()

    def get(self, sid: str) -> Session:
        with self._lock:
            s = self._d.get(sid)
            if s is None or time.time() - s.updated > self._ttl:
                s = Session(id=sid)
                self._d[sid] = s
            return s

    def save(self, s: Session) -> None:
        s.updated = time.time()
        with self._lock:
            self._d[s.id] = s


store = SessionStore()
