"""Regression tests for the architect fixes F1–F4 + Coldplay/Night Str (F5).

LLM-free: fixed Intents avoid parsing; a fake LLM covers the reason path.
Requires MySQL + local models (bge-m3 / reranker) — that's the point of these tests.
"""
from __future__ import annotations

from agent.core.intent import Intent
from agent.core.pipeline import rank_ids


def test_f1_out_of_corpus_lyric_refused():
    # clearly out-of-corpus lyrics (conf below the frozen 0.75) must be refused.
    # ('天青色等烟雨…' is omitted on purpose: it coincidentally overlaps a corpus
    # line and lands above the threshold — a known, documented leak.)
    for frag in ["we will we will rock you", "is this the real life is this just fantasy"]:
        ids = rank_ids("", intent_override=Intent(intents=["lyric"], lyric_fragment=frag))
        assert ids == [], f"should refuse out-of-corpus lyric: {frag}"


def test_f1_in_corpus_lyric_answered():
    flag: dict = {}
    ids = rank_ids("", intent_override=Intent(intents=["lyric"], lyric_fragment="I search for the strength"),
                   flag=flag)
    assert ids and not flag.get("gray"), "an in-corpus lyric must be confidently answered"


def test_f2_style_reference_not_refused():
    # "周杰伦风格的歌": style_of set, title/artist cleared -> semantic search, not refusal
    it = Intent(intents=["describe"], semantic_query="周杰伦 中国风 R&B 流行",
                style_of="周杰伦")
    assert rank_ids("", intent_override=it), "style reference must return songs, not refuse"


def test_f3_similar_seed_not_first():
    seed = 1133549  # 'Night Train'
    it = Intent(intents=["similar", "title"], title="Night Train", semantic_query="Night Train")
    ids = rank_ids("", intent_override=it)
    assert ids and ids[0] != seed, f"similar query must not return the seed itself first: {ids[:3]}"


def test_f4_reason_not_empty_for_lyric():
    from agent.core import respond
    from agent.models import Match

    class FakeLLM:
        def complete_json(self, *a, **k):
            return {"reasons": [{"song_id": 7359, "reason": "测试理由"}]}

    it = Intent(intents=["lyric"], semantic_query="x")
    matches = {7359: Match(type="lyric", line_no=1, line_text="move a little closer", start_ms=1000)}
    songs, _msg, err = respond.build(it, [(7359, 0.9)], matches, llm=FakeLLM())
    assert songs and songs[0]["reason"] == "测试理由"
    assert err is None


def test_coldplay_wrong_artist_refused():
    # named title + wrong artist -> refuse (no songs), message mentions the mismatch
    from agent.core.pipeline import search
    r = search("Coldplay 的 Yellow")
    assert r["songs"] == []
    assert "Coldplay" in r["message"]


def test_typo_title_found():
    it = Intent(intents=["title"], title="Night Str", artist="Sergey Gulevich")
    ids = rank_ids("", intent_override=it)
    assert 1693791 in ids[:5], f"'Night Str' should find 'Night Star': {ids[:3]}"
