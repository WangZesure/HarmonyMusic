"""In-memory BM25 keyword search over song titles/artists and lyric lines (phase3 §2.2).

Replaces the crashing MySQL FULLTEXT. Built lazily from MySQL, cached in-process.
English-oriented tokenizer (lowercase alnum + single CJK chars); Chinese queries
are matched by the vector route instead.
"""
from __future__ import annotations

import re

import bm25s

import agent.db as db

_TOKEN = re.compile(r"[a-z0-9]+|[一-鿿]")
_songs = None   # (retriever, song_ids)
_lyrics = None  # (retriever, rows)  rows: parallel to corpus = {song_id,line_no,start_ms,text}


def _tok(s: str) -> list[str]:
    return _TOKEN.findall((s or "").lower())


def _build_songs():
    rows = db.q("""SELECT s.id, s.name, g.name AS artist
                   FROM songs s LEFT JOIN singers g ON g.id=s.singer_id""")
    ids = [r["id"] for r in rows]
    corpus = [_tok(f"{r['name']} {r['artist'] or ''}") for r in rows]
    ret = bm25s.BM25()
    ret.index(corpus, show_progress=False)
    return ret, ids


def _build_lyrics():
    # only lyric_searchable songs (contract C4 / F6): ASR-only lyrics are excluded
    rows = db.q("SELECT song_id, line_no, start_ms, text FROM song_lyric_lines "
                "WHERE song_id IN (SELECT song_id FROM song_meta WHERE lyric_searchable=1)")
    corpus = [_tok(r["text"]) for r in rows]
    ret = bm25s.BM25()
    ret.index(corpus, show_progress=False)
    return ret, rows


def songs():
    global _songs
    if _songs is None:
        _songs = _build_songs()
    return _songs


def lyrics():
    global _lyrics
    if _lyrics is None:
        _lyrics = _build_lyrics()
    return _lyrics


def search_titles(query: str, k: int = 30) -> list[tuple[int, float]]:
    ret, ids = songs()
    tok = _tok(query)
    if not tok:
        return []
    idx, scores = ret.retrieve([tok], k=min(k, len(ids)), show_progress=False)
    return [(ids[i], float(s)) for i, s in zip(idx[0], scores[0]) if s > 0]


def search_lyrics(query: str, k: int = 30) -> list[dict]:
    ret, rows = lyrics()
    tok = _tok(query)
    if not tok:
        return []
    idx, scores = ret.retrieve([tok], k=min(k, len(rows)), show_progress=False)
    out = []
    for i, s in zip(idx[0], scores[0]):
        if s <= 0:
            continue
        r = rows[i]
        out.append({"song_id": r["song_id"], "line_no": r["line_no"],
                    "start_ms": r["start_ms"], "text": r["text"], "score": float(s)})
    return out
