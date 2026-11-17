"""v6 agent tests: loop mechanics, finish validation, refine, aliases.

These tests use a fake LLM and a fake registry; they do not call DeepSeek or
load bge-m3. MySQL is required for the session store and finish validation.
"""
from __future__ import annotations

import uuid

import pytest

import agent.core.agent_loop as loop
import agent.db as db
from agent.agent_models import FinishArgs, RefineArgs
from agent.core.agent_loop import run
from agent.models import Filter, ToolResult
from agent.session_store import key_for, store
from agent.tools.agent_tools import _canon_one, finish, refine_candidates


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, cid, name, arguments):
        self.id = cid
        self.type = "function"
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Usage:
    total_tokens = 10


class _Resp:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]
        self.usage = _Usage()


class FakeLLM:
    def __init__(self, mode="songs"):
        self.n = 0
        self.mode = mode

    def chat(self, messages, tools=None, temperature=0.0, **kwargs):
        self.n += 1
        if self.mode == "clarify":
            return _Resp(_Msg(tool_calls=[_Call("a1", "ask_user",
                                                '{"question":"要哪种语言？","options":["法语"],"slot":"filters.languages"}')]))
        if self.n == 1:
            return _Resp(_Msg(tool_calls=[_Call("c1", "search_songs", '{"query":"q","limit":5}')]))
        if self.n == 2:
            return _Resp(_Msg(tool_calls=[_Call("c2", "finish",
                                                '{"song_ids":[1],"reasons":{"1":"r"},"message":"ok","source":"llm"}')]))
        return _Resp(_Msg(content="done"))


def _fake_registry(name, args, ctx):
    if name == "search_songs":
        card = {"song_id": 1, "name": "A", "artist": "B", "duration_s": 100,
                "vocal": "vocal", "language": "en", "genres": [], "moods": [],
                "scenes": [], "score": 0.9, "match": None}
        return ToolResult(ok=True, data={"items": [card], "applied": {"filters": {}},
                                         "exhausted": False})
    if name == "finish":
        return ToolResult(ok=True, data={"output": {
            "type": "songs",
            "songs": [{"song_id": 1, "name": "A", "artist": "B", "reason": "r"}],
            "message": "ok", "degraded": None}})
    if name == "ask_user":
        return ToolResult(ok=True, data={"output": {
            "type": "clarify", "question": "要哪种语言？", "options": ["法语"],
            "slot": "filters.languages"}})
    return ToolResult.fail("INVALID_ARGUMENT", f"unknown tool {name}")


@pytest.fixture
def patched(monkeypatch):
    store.ensure_schema()
    monkeypatch.setattr(loop.registry, "run", _fake_registry)
    yield
    # no global cleanup needed; each test uses a unique key


def _sid() -> str:
    return "test-" + uuid.uuid4().hex[:10]


def test_canon_alias():
    assert _canon_one("欢快") == "开心"
    assert _canon_one("伤感") == "伤感"
    assert _canon_one("not-a-tag") is None


def test_unknown_tags_and_languages_are_reported():
    from agent.tools.agent_tools import _normalize_filter
    f, bad = _normalize_filter(Filter(moods=["悲伤"]))
    assert bad == ["悲伤"] and not f.moods
    f, bad = _normalize_filter(Filter(moods=["欢快"], languages=["法语"]))
    assert f.moods == ["开心"] and f.languages == ["fr"] and not bad
    f, bad = _normalize_filter(Filter(languages=["俄语"]))
    assert f.languages == ["ru"] and not bad
    f, bad = _normalize_filter(Filter(languages=["Klingon"]))
    assert bad == ["Klingon"] and not f.languages
    # a mood filed as a genre is rejected, not silently matched against nothing
    f, bad = _normalize_filter(Filter(genres=["伤感"]))
    assert bad == ["伤感"] and not f.genres


def test_refine_candidates():
    state = {"last_search": {"candidates": [
        {"song_id": 1, "name": "a", "artist": "x", "genres": [], "moods": ["伤感"], "scenes": []},
        {"song_id": 2, "name": "b", "artist": "y", "genres": [], "moods": ["开心"], "scenes": []},
    ]}}
    res = refine_candidates(RefineArgs(drop_tags=["伤感"], limit=5), {"state": state})
    assert res.ok
    assert [i["song_id"] for i in res.data["items"]] == [2]
    assert "1" in res.data["dropped"]


def test_finish_validation(patched):
    row = db.q("SELECT id FROM songs ORDER BY id LIMIT 1")
    sid = int(row[0]["id"])
    tags = db.tag_map([sid]).get(sid, {})
    mood = (tags.get("moods") or [None])[0]
    filters = Filter(moods=[mood]) if mood else Filter()

    ok = finish(FinishArgs(song_ids=[sid], reasons={sid: "r"}, message="m", source="template"),
                {"allowed_ids": {sid}, "filters": filters})
    assert ok.ok
    assert ok.data["output"]["songs"][0]["reason"]

    # template reasons are fixed to "name · artist" even if the model sends one
    info = db.base_info([sid])[sid]
    assert ok.data["output"]["songs"][0]["reason"] == f"{info['name']} · {info['artist']}"

    # an id that the tools did not return is rejected before hydration
    outside = finish(FinishArgs(song_ids=[sid], reasons={}, message="m"),
                     {"allowed_ids": set(), "filters": Filter()})
    assert not outside.ok
    assert outside.error.code == "INVALID_ARGUMENT"

    bad = finish(FinishArgs(song_ids=[999999999], reasons={}, message="m"),
                 {"allowed_ids": {999999999}, "filters": Filter()})
    assert not bad.ok

    if mood:
        other = db.q("""SELECT song_id FROM song_tags WHERE tag_type='mood' AND tag<>%s
                        LIMIT 1""", [mood])
        if other:
            oid = int(other[0]["song_id"])
            viol = finish(FinishArgs(song_ids=[oid], reasons={}, message="m"),
                          {"allowed_ids": {oid}, "filters": filters})
            assert not viol.ok


def test_refine_candidates_stale_turn_rejected():
    state = {"last_search": {"turn_id": "old", "candidates": [
        {"song_id": 1, "name": "a", "artist": "x", "genres": [], "moods": ["伤感"], "scenes": []},
    ]}}
    res = refine_candidates(RefineArgs(drop_tags=["伤感"], limit=5),
                            {"state": state, "turn_id": "new"})
    assert not res.ok
    assert res.error.code == "INVALID_ARGUMENT"
    # same turn, or the clarify answer whose turn produced these candidates, is allowed
    assert refine_candidates(RefineArgs(drop_tags=["伤感"], limit=5),
                             {"state": state, "turn_id": "old"}).ok
    assert refine_candidates(RefineArgs(drop_tags=["伤感"], limit=5),
                             {"state": {**state, "pending_question": {
                                 "slot": "filters.languages", "turn_id": "old"}},
                              "turn_id": "new"}).ok
    # a pending question from a DIFFERENT turn must not resurrect these candidates
    assert not refine_candidates(RefineArgs(drop_tags=["伤感"], limit=5),
                                 {"state": {**state, "pending_question": {
                                     "slot": "filters.languages", "turn_id": "other"}},
                                  "turn_id": "new"}).ok


class FakeBadJSON:
    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None, temperature=0.0, **kwargs):
        self.n += 1
        if self.n == 1:
            return _Resp(_Msg(tool_calls=[_Call("x1", "search_songs", "{not json")]))
        if self.n == 2:
            return _Resp(_Msg(tool_calls=[_Call("x2", "search_songs", '{"query":"q","limit":5}')]))
        if self.n == 3:
            return _Resp(_Msg(tool_calls=[_Call("x3", "finish",
                                                '{"song_ids":[1],"reasons":{"1":"r"},"message":"ok","source":"llm"}')]))
        return _Resp(_Msg(content="done"))


def test_invalid_json_args_not_executed(patched, monkeypatch):
    called = []

    def spy(name, args, ctx):
        called.append(name)
        return _fake_registry(name, args, ctx)

    monkeypatch.setattr(loop.registry, "run", spy)
    sid = _sid()
    try:
        out = run("q", sid, user_id=1, turn_id="j1", llm=FakeBadJSON())
        assert out["type"] == "songs"
        assert called == ["search_songs", "finish"]      # the malformed call never ran
        assert any(t.get("error") == "invalid_json" for t in out["trace"])
    finally:
        store.delete(key_for(1, sid))


class FakeSearchSpam:
    """Always wants another search: exercises MAX_SEARCHES then MAX_STEPS."""

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None, temperature=0.0, **kwargs):
        self.n += 1
        return _Resp(_Msg(tool_calls=[
            _Call(f"s{self.n}", "search_songs", '{"query":"q","limit":5}')]))


def test_search_limit_then_max_steps(patched):
    sid = _sid()
    try:
        llm = FakeSearchSpam()
        out = run("q", sid, user_id=1, turn_id="lim1", llm=llm)
        trace = out["trace"]
        assert llm.n == loop.MAX_STEPS                      # 5 model steps, then stop
        searches = [t for t in trace if t.get("tool") == "search_songs" and t.get("ok")]
        limited = [t for t in trace if t.get("error") == "search_limit"]
        assert len(searches) == loop.MAX_SEARCHES
        assert len(limited) == loop.MAX_STEPS - loop.MAX_SEARCHES
        assert out["degraded"]["reasons"] == ["max_steps"]
        assert out["degraded"]["at_step"] == loop.MAX_STEPS
    finally:
        store.delete(key_for(1, sid))


class FakeSearchThenText:
    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None, temperature=0.0, **kwargs):
        self.n += 1
        if self.n == 1:
            return _Resp(_Msg(tool_calls=[_Call("c1", "search_songs", '{"query":"q","limit":5}')]))
        return _Resp(_Msg(content="这几首都不错。"))


def test_songs_without_finish_degrades(patched):
    sid = _sid()
    try:
        out = run("q", sid, user_id=1, turn_id="nf1", llm=FakeSearchThenText())
        assert out["type"] in ("songs", "refusal")
        assert out["degraded"]["reasons"] == ["no_finish"]
    finally:
        store.delete(key_for(1, sid))


def test_loop_songs_and_idempotency(patched):
    sid = _sid()
    try:
        out = run("q", sid, user_id=1, turn_id="t1", llm=FakeLLM())
        assert out["type"] == "songs"
        assert out["songs"][0]["song_id"] == 1
        state = store.load(key_for(1, sid))
        assert state["last_turn_id"] == "t1"
        assert 1 in state["seen"]

        fake2 = FakeLLM()
        out2 = run("q", sid, user_id=1, turn_id="t1", llm=fake2)
        assert out2 == out
        assert fake2.n == 0                     # idempotency: no LLM call
    finally:
        store.delete(key_for(1, sid))


def test_loop_clarify_pending(patched):
    sid = _sid()
    try:
        out = run("zero", sid, user_id=1, turn_id="c1", llm=FakeLLM(mode="clarify"))
        assert out["type"] == "clarify"
        assert out["slot"] == "filters.languages"
        state = store.load(key_for(1, sid))
        assert state["pending_question"]["slot"] == "filters.languages"
    finally:
        store.delete(key_for(1, sid))
