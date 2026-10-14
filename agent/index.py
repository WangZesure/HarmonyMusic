"""Vector index access (bge-m3, numpy) — contract C6/C7.

The encoder is loaded lazily; if sentence-transformers/torch aren't importable the
embedding tools raise UNAVAILABLE rather than crashing the service.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

import agent.config as cfg

_MODEL_NAME = "BAAI/bge-m3"


class Unavailable(RuntimeError):
    pass


def _load_collection(name: str):
    d = Path(cfg.INDEX_DIR) / name
    emb = np.load(d / "embeddings.npy")
    ids = json.loads((d / "ids.json").read_text(encoding="utf-8"))
    docs = ids
    if (d / "docs.json").exists():
        docs = json.loads((d / "docs.json").read_text(encoding="utf-8"))
    meta = None
    if (d / "meta.json").exists():
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return emb, ids, docs, meta


@lru_cache(maxsize=1)
def songs():
    return _load_collection("songs_v1")


@lru_cache(maxsize=1)
def lyric_lines():
    return _load_collection("lyric_lines_v1")


@lru_cache(maxsize=1)
def index_meta() -> dict:
    return json.loads((Path(cfg.INDEX_DIR) / "index_meta.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _encoder():
    try:
        import os
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        from sentence_transformers import SentenceTransformer
    except Exception as e:  # noqa: BLE001
        raise Unavailable(f"sentence-transformers not available: {e}")
    return SentenceTransformer(_MODEL_NAME)


def encode(text: str) -> np.ndarray:
    """L2-normalized query vector (cosine == dot product)."""
    vec = _encoder().encode([text], normalize_embeddings=True, convert_to_numpy=True)
    return vec[0].astype("float32")


def _rank(emb: np.ndarray, ids: list, qvec: np.ndarray, allowed: set[int] | None,
          exclude: set[int], limit: int) -> list[tuple]:
    sims = emb @ qvec
    order = np.argsort(-sims)
    out = []
    for idx in order:
        sid = int(ids[idx])
        if sid in exclude or (allowed is not None and sid not in allowed):
            continue
        out.append((idx, sid, float(sims[idx])))
        if len(out) >= limit:
            break
    return out


def semantic_search(qvec: np.ndarray, allowed: set[int] | None, exclude: list[int], limit: int):
    emb, ids, _, _ = songs()
    return [(sid, score) for _, sid, score in _rank(emb, ids, qvec, allowed, set(exclude), limit)]


def similar_by_song(song_id: int, exclude_singer_id: int | None, limit: int):
    emb, ids, _, _ = songs()
    pos = {int(s): i for i, s in enumerate(ids)}
    if song_id not in pos:
        return []
    qvec = emb[pos[song_id]]
    sims = emb @ qvec
    order = np.argsort(-sims)
    out = []
    for idx in order:
        sid = int(ids[idx])
        if sid == song_id:
            continue
        out.append((sid, float(sims[idx])))
        if len(out) >= limit * 3:  # pad; caller filters by singer
            break
    return out


def lyrics_vector_search(qvec: np.ndarray, limit: int):
    emb, ids, _, meta = lyric_lines()
    sims = emb @ qvec
    order = np.argsort(-sims)
    out = []
    for idx in order:
        lid = ids[idx]
        sid, line_no = lid.split(":")
        out.append((int(sid), int(line_no), meta[idx]["start_ms"], meta[idx].get("text"), float(sims[idx])))
        if len(out) >= limit:
            break
    return out


def embedding_available() -> bool:
    try:
        _encoder()
        return True
    except Unavailable:
        return False
