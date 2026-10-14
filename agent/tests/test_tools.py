"""Unit tests for the tool layer (phase-2 acceptance: every tool has tests).

    python -m pytest agent/tests -q       # run with an interpreter that has the deps

Only DB-backed behaviour is asserted here; embedding tools are checked for shape
so the suite passes even where sentence-transformers is unavailable.
"""
from __future__ import annotations

import agent.registry as registry
import agent.tools  # noqa: F401  (registers tools)


def test_all_tools_registered():
    expected = {"search_by_keyword", "search_by_lyrics", "filter_by_tags", "semantic_search",
                "similar_songs", "get_user_profile", "recommend_for_user", "get_song_detail"}
    assert expected <= set(registry.TOOLS)


def test_schemas_shape():
    schemas = registry.openai_schemas()
    assert len(schemas) == len(registry.TOOLS)
    for s in schemas:
        assert s["type"] == "function"
        f = s["function"]
        assert f["name"] and f["description"]
        assert f["parameters"]["type"] == "object"


def test_keyword_finds_known_song():
    r = registry.run("search_by_keyword", {"query": "closer", "limit": 5})
    assert r.ok and r.data["items"]
    assert any("closer" in i["name"].lower() for i in r.data["items"])


def test_filter_by_tags_returns_tagged_songs():
    r = registry.run("filter_by_tags", {"filters": {"moods": ["伤感"]}, "limit": 5})
    assert r.ok
    for i in r.data["items"]:
        assert "伤感" in i["moods"]


def test_exclude_ids_honoured():
    first = registry.run("filter_by_tags", {"filters": {"moods": ["伤感"]}, "limit": 3})
    ids = [i["song_id"] for i in first.data["items"]]
    second = registry.run("filter_by_tags",
                          {"filters": {"moods": ["伤感"]}, "limit": 3, "exclude_ids": ids})
    assert not (set(ids) & {i["song_id"] for i in second.data["items"]})


def test_song_detail_not_found():
    r = registry.run("get_song_detail", {"song_id": 999999999})
    assert not r.ok and r.error.code == "NOT_FOUND"


def test_invalid_args():
    r = registry.run("filter_by_tags", {"nope": 1})
    assert not r.ok and r.error.code == "INVALID_ARGUMENT"


def test_empty_result_is_success():
    # a tag combination that should match nothing -> ok:true, empty items (C7)
    r = registry.run("filter_by_tags",
                     {"filters": {"genres": ["中国风"], "scenes": ["婚礼"]}, "limit": 5})
    assert r.ok and isinstance(r.data["items"], list)


def test_result_size_budget():
    """C7: 10 hits should stay compact (no lyric/url/pic)."""
    r = registry.run("filter_by_tags", {"filters": {"moods": ["伤感"]}, "limit": 10})
    blob = str(r.model_dump())
    assert "lyric" not in blob and "/stream/" not in blob
    assert len(blob.encode("utf-8")) <= 4096
