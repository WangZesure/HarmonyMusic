"""LangGraph prototype parity tests.

Same fake LLM / fake registry as test_agent_v6, but the orchestration is the
StateGraph in agent/core/graph_loop.py. These tests pin the behaviour that the
hand-written loop's tests already cover, so the two engines can be compared.
"""
from __future__ import annotations

import uuid

import pytest

import agent.core.graph_loop as gl
from agent.session_store import key_for, store
from agent.tests.test_agent_v6 import (
    FakeBadJSON,
    FakeLLM,
    FakeSearchSpam,
    FakeSearchThenText,
    _fake_registry,
)


@pytest.fixture
def patched(monkeypatch):
    store.ensure_schema()
    monkeypatch.setattr(gl.registry, "run", _fake_registry)
    yield


def _sid() -> str:
    return "gtest-" + uuid.uuid4().hex[:10]


def test_graph_songs_and_idempotency(patched):
    sid = _sid()
    try:
        out = gl.run("q", sid, user_id=1, turn_id="t1", llm=FakeLLM())
        assert out["type"] == "songs"
        assert out["songs"][0]["song_id"] == 1
        state = store.load(key_for(1, sid))
        assert state["last_turn_id"] == "t1"
        assert 1 in state["seen"]

        fake2 = FakeLLM()
        out2 = gl.run("q", sid, user_id=1, turn_id="t1", llm=fake2)
        assert out2 == out
        assert fake2.n == 0
    finally:
        store.delete(key_for(1, sid))


def test_graph_clarify(patched):
    sid = _sid()
    try:
        out = gl.run("zero", sid, user_id=1, turn_id="c1", llm=FakeLLM(mode="clarify"))
        assert out["type"] == "clarify"
        assert out["slot"] == "filters.languages"
        state = store.load(key_for(1, sid))
        assert state["pending_question"]["slot"] == "filters.languages"
    finally:
        store.delete(key_for(1, sid))


def test_graph_search_limit_then_max_steps(patched):
    sid = _sid()
    try:
        llm = FakeSearchSpam()
        out = gl.run("q", sid, user_id=1, turn_id="lim1", llm=llm)
        trace = out["trace"]
        assert llm.n == gl.MAX_STEPS
        searches = [t for t in trace if t.get("tool") == "search_songs" and t.get("ok")]
        limited = [t for t in trace if t.get("error") == "search_limit"]
        assert len(searches) == gl.MAX_SEARCHES
        assert len(limited) == gl.MAX_STEPS - gl.MAX_SEARCHES
        assert out["degraded"]["reasons"] == ["max_steps"]
    finally:
        store.delete(key_for(1, sid))


def test_graph_invalid_json_not_executed(patched, monkeypatch):
    called = []

    def spy(name, args, ctx):
        called.append(name)
        return _fake_registry(name, args, ctx)

    monkeypatch.setattr(gl.registry, "run", spy)
    sid = _sid()
    try:
        out = gl.run("q", sid, user_id=1, turn_id="j1", llm=FakeBadJSON())
        assert out["type"] == "songs"
        assert called == ["search_songs", "finish"]
        assert any(t.get("error") == "invalid_json" for t in out["trace"])
    finally:
        store.delete(key_for(1, sid))


def test_graph_songs_without_finish_degrades(patched):
    sid = _sid()
    try:
        out = gl.run("q", sid, user_id=1, turn_id="nf1", llm=FakeSearchThenText())
        assert out["type"] in ("songs", "refusal")
        assert out["degraded"]["reasons"] == ["no_finish"]
    finally:
        store.delete(key_for(1, sid))
