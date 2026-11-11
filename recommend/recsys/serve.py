"""Inference entry point used by Koa's /getRecommendSongs.

Behaviour:
  * user has play history -> content two-tower personalisation
  * user has no history   -> popularity fallback (cold start), clearly labelled

The returned ``algorithm`` field is what lets the caller tell the two apart, so a
demo never silently presents popularity as if it were personalised.
"""
from __future__ import annotations

import functools

import numpy as np
import torch

from . import config as C
from . import data as D
from .features import history_vector
from .evaluate import load_model


@functools.lru_cache(maxsize=1)
def _catalog():
    songs, vecs = D.load_catalog()
    return songs, vecs, [s["id"] for s in songs]


@functools.lru_cache(maxsize=1)
def _song_embeddings():
    _, vecs, ids = _catalog()
    model = load_model()
    mat = torch.from_numpy(np.stack([vecs[s] for s in ids]))
    with torch.no_grad():
        return ids, model.song_emb(mat).numpy()


def _plays_for(user_id: int) -> list[dict]:
    conn = D.connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT user_id, song_id, play_time, play_duration, full_duration
            FROM user_plays WHERE user_id = %s
            ORDER BY play_time, id
        """, (user_id,))
        return [{"user_id": r[0], "song_id": r[1], "play_time": r[2],
                 "play_duration": r[3], "full_duration": r[4]} for r in cur.fetchall()]
    finally:
        conn.close()


def _popularity(k: int, exclude: set[int]) -> list[tuple[int, float]]:
    conn = D.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT song_id, COUNT(*) c FROM user_plays GROUP BY song_id "
                    "ORDER BY c DESC, song_id ASC LIMIT %s", (k + len(exclude),))
        rows = cur.fetchall()
    finally:
        conn.close()
    out = [(int(sid), 1.0 - i * 0.01) for i, (sid, _c) in enumerate(rows) if sid not in exclude]
    return out[:k]


def recommend(user_id: int, k: int = 6) -> dict:
    ids, emb = _song_embeddings()
    songs, vecs, _ = _catalog()
    plays = _plays_for(user_id)
    played = {p["song_id"] for p in plays}

    if not plays:
        recs = _popularity(k, set())
        return {"algorithm": "popularity", "reason": "该用户暂无播放记录（冷启动）",
                "recommendations": recs}

    model = load_model()
    h = history_vector(plays, vecs)
    with torch.no_grad():
        u = model.user_emb(torch.from_numpy(h).unsqueeze(0)).numpy()[0]
    scores = emb @ u
    order = np.argsort(-scores)
    recs = [(ids[i], float(scores[i])) for i in order if ids[i] not in played][:k]

    if not recs:                       # 听过全部 500 首，退回热门
        return {"algorithm": "popularity", "reason": "已听遍曲库",
                "recommendations": _popularity(k, played)}

    return {"algorithm": "two-tower-v2", "reason": f"基于 {len(plays)} 条播放记录",
            "recommendations": recs}
