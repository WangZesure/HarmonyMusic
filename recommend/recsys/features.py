"""Shared feature construction — train / evaluate / serve must agree exactly.

The user representation is a **completion-weighted mean of the vectors of the
songs they played**: a song they listened to 95% counts far more than one they
skipped after 10 seconds.  A plain mean blurs 14-20 songs into a point that no
longer resembles any of them, which measurably hurt ranking.
"""
from __future__ import annotations

import numpy as np

from . import config as C


def completion(p: dict) -> float:
    fd = p.get("full_duration") or 0
    if not fd:
        return 0.0
    return max(0.0, min(1.0, (p.get("play_duration") or 0) / fd))


def history_vector(plays: list[dict], vecs: dict[int, np.ndarray],
                   max_history: int | None = None) -> np.ndarray:
    """Weighted mean of the history song vectors.

    plays: [{song_id, play_duration, full_duration}] in chronological order.
    """
    ps = plays[-(max_history or C.MAX_HISTORY):]
    ps = [p for p in ps if p["song_id"] in vecs]
    if not ps:
        return np.zeros(C.IN_DIM, dtype="float32")
    w = np.array([max(0.05, completion(p)) for p in ps], dtype="float32")
    v = np.stack([vecs[p["song_id"]] for p in ps])
    return ((w[:, None] * v).sum(0) / w.sum()).astype("float32")
