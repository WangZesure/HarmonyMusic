"""Catalog + synthetic behaviour generation.

Design notes (why the data looks like this):

* Users are simulated from the **real tag distribution of the 500-song corpus**,
  not from random ids.  Each user gets a taste profile (1-3 genres, 1-3 moods,
  1-2 scenes) sampled with corpus frequency as weight, so popular tastes are
  more common but the long tail still appears.
* A play is either *exploitation* (a song matching the profile) or *exploration*
  (a uniformly random song).  Completion rate and is_liked are derived from how
  well the song matches the profile plus noise, which is what makes the signal
  learnable at all — the previous generator paired random users with random
  songs, so there was nothing to learn.
* Plays are written with ``source='synthetic'`` and ``user_id >= 100000``
  (contract C1) so they never mix with real registrations or real plays.
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta

import numpy as np
import pymysql

from . import config as C


# ------------------------------------------------------------------ db ----
def connect():
    return pymysql.connect(**C.DB)


def load_catalog() -> tuple[list[dict], dict[int, np.ndarray]]:
    """Return (songs, {song_id: 1024-d unit vector}).

    songs: [{id, name, artist, genres, moods, scenes, duration_s}]
    """
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT s.id, s.name, IFNULL(g.name,''), IFNULL(m.duration_s,0)
            FROM songs s
            LEFT JOIN singers g ON g.id = s.singer_id
            LEFT JOIN song_meta m ON m.song_id = s.id
        """)
        base = {r[0]: {"id": r[0], "name": r[1], "artist": r[2], "duration_s": int(r[3] or 0),
                       "genres": set(), "moods": set(), "scenes": set()} for r in cur.fetchall()}

        cur.execute("SELECT song_id, tag_type, tag FROM song_tags")
        for sid, typ, tag in cur.fetchall():
            s = base.get(sid)
            if not s:
                continue
            if typ == "genre":
                s["genres"].add(tag)
            elif typ == "mood":
                s["moods"].add(tag)
            elif typ == "scene":
                s["scenes"].add(tag)
    finally:
        conn.close()

    songs = list(base.values())

    ids = _load_index_ids()
    emb = np.load(C.INDEX_DIR / "songs_v1" / "embeddings.npy")
    vecs = {sid: emb[i].astype("float32") for i, sid in enumerate(ids)}
    # 只保留既有标签又有向量的歌
    songs = [s for s in songs if s["id"] in vecs]
    return songs, vecs


def _load_index_ids() -> list[int]:
    import json
    raw = json.loads((C.INDEX_DIR / "songs_v1" / "ids.json").read_text(encoding="utf-8"))
    return [int(x) for x in raw]


def load_plays(source: str | None = None, min_user_id: int | None = None) -> list[dict]:
    """Read user_plays as [{user_id, song_id, play_time, play_duration, full_duration}]."""
    sql = ("SELECT user_id, song_id, play_time, play_duration, full_duration "
           "FROM user_plays")
    where, args = [], []
    if source:
        where.append("source = %s")
        args.append(source)
    if min_user_id is not None:
        where.append("user_id >= %s")
        args.append(min_user_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY user_id, play_time, id"

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        return [{"user_id": r[0], "song_id": r[1], "play_time": r[2],
                 "play_duration": r[3], "full_duration": r[4]} for r in cur.fetchall()]
    finally:
        conn.close()


# ------------------------------------------------------------ generation ----
def _weighted_tags(songs: list[dict], key: str) -> tuple[list[str], list[float]]:
    cnt = Counter(t for s in songs for t in s[key])
    tags = list(cnt.keys())
    # 频次的 0.75 次方：压一下头部，让长尾标签也有机会被选中
    weights = [cnt[t] ** 0.75 for t in tags]
    return tags, weights


def _sample_profile(rng: random.Random, songs: list[dict]) -> tuple[set, set, set]:
    out = []
    for key, ks in (("genres", (1, 2, 2, 3)), ("moods", (1, 2, 2, 3)), ("scenes", (1, 1, 2))):
        tags, weights = _weighted_tags(songs, key)
        k = min(rng.choice(ks), len(tags))
        picked = set()
        while len(picked) < k:
            picked.add(rng.choices(tags, weights=weights, k=1)[0])
        out.append(picked)
    return out[0], out[1], out[2]


def _match(song: dict, prof: tuple[set, set, set]) -> float:
    """Fraction of the user's profile tags that this song satisfies (0..1)."""
    g, m, sc = prof
    total = len(g) + len(m) + len(sc)
    if not total:
        return 0.0
    hit = len(song["genres"] & g) + len(song["moods"] & m) + len(song["scenes"] & sc)
    return hit / total


def generate_synthetic_plays(songs: list[dict], n_users: int | None = None,
                             plays_per_user: int | None = None,
                             seed: int | None = None
                             ) -> tuple[list[dict], dict[int, dict]]:
    """Returns (plays, profiles).

    profiles[u] = {"genres": [...], "moods": [...], "scenes": [...]} — the ground
    truth used to generate the user's plays. It is only for evaluation; the model
    never sees it.
    """
    n_users = n_users or C.N_USERS
    plays_per_user = plays_per_user or C.PLAYS_PER_USER
    rng = random.Random(seed if seed is not None else C.SEED)

    by_id = {s["id"]: s for s in songs}
    all_ids = [s["id"] for s in songs]
    # 预计算每首歌的标签集合，供匹配打分
    for s in songs:
        s["_all"] = s["genres"] | s["moods"] | s["scenes"]

    plays: list[dict] = []
    profiles: dict[int, dict] = {}
    for u in range(n_users):
        user_id = C.SYNTH_USER_BASE + u
        prof = _sample_profile(rng, songs)
        profiles[user_id] = {"genres": sorted(prof[0]), "moods": sorted(prof[1]),
                             "scenes": sorted(prof[2])}

        # 候选池：按匹配度排序，前 40% 视为「口味内」
        scored = sorted(all_ids, key=lambda sid: _match(by_id[sid], prof), reverse=True)
        top = scored[: max(20, int(len(scored) * 0.4))]

        # 同一用户不重复听同一首（避免 leave-one-out 的歧义）
        chosen: list[int] = []
        guard = 0
        while len(chosen) < plays_per_user and guard < plays_per_user * 20:
            guard += 1
            if rng.random() < C.EXPLORE_RATE:
                sid = rng.choice(all_ids)
                base = 0.15
            else:
                sid = rng.choice(top)
                base = 0.55
            if sid in chosen:
                continue
            chosen.append(sid)

            m = _match(by_id[sid], prof)
            completion = base + 0.45 * m + rng.gauss(0, 0.08)
            completion = max(0.02, min(1.0, completion))
            full = by_id[sid]["duration_s"] or 180
            plays.append({
                "user_id": user_id,
                "song_id": sid,
                "play_duration": int(round(completion * full)),
                "full_duration": int(full),
                "is_liked": 1 if (completion > 0.85 and m >= 0.4) else 0,
                "source": "synthetic",
            })
    return plays, profiles


def write_plays(plays: list[dict]) -> tuple[int, int]:
    """Replace this generator's synthetic rows, then insert. Real plays are untouched.

    Returns (inserted, removed).
    """
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM user_plays WHERE source='synthetic' AND user_id >= %s",
                    (C.SYNTH_USER_BASE,))
        removed = cur.rowcount

        # 每个用户的播放按时间递增铺开（越靠后越近），leave-one-out 才有「最后一次」可言
        by_user: dict[int, list[dict]] = {}
        for p in plays:
            by_user.setdefault(p["user_id"], []).append(p)

        now = datetime.now()
        rows = []
        for uid, ps in by_user.items():
            for i, p in enumerate(ps):
                t = now - timedelta(minutes=10 * (len(ps) - i))
                rows.append((uid, p["song_id"], p["play_duration"], p["full_duration"],
                             p.get("is_liked", 0), t, p.get("source", "synthetic")))

        cur.executemany("""
            INSERT INTO user_plays
                (user_id, song_id, play_duration, full_duration, is_liked, play_time,
                 device_type, source)
            VALUES (%s, %s, %s, %s, %s, %s, 'mobile', %s)
        """, rows)
        conn.commit()
        return len(rows), removed
    finally:
        conn.close()
