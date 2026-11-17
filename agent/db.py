"""Read-only DB access for the tool layer (contract C2/C7)."""
from __future__ import annotations

import threading

import pymysql

import agent.config as cfg
from agent.models import Filter, Match, SongHit

# One connection per thread, reused. Opening a fresh connection per query exhausts
# Windows ephemeral ports under burst load (the eval issues thousands of queries),
# which shows up as "connection refused" mid-run.
_local = threading.local()


def connect():
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, autocommit=True, **cfg.DB)
        _local.conn = conn
    else:
        conn.ping(reconnect=True)
    return conn


def q(sql: str, params: list | tuple = ()) -> list[dict]:
    with connect().cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


# --------------------------------------------------------------------------- #
# enrichment
# --------------------------------------------------------------------------- #
def base_info(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    ph = ",".join(["%s"] * len(ids))
    rows = q(f"""SELECT s.id, s.name, g.name AS artist
                 FROM songs s LEFT JOIN singers g ON s.singer_id=g.id
                 WHERE s.id IN ({ph})""", ids)
    return {r["id"]: r for r in rows}


def meta_map(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    ph = ",".join(["%s"] * len(ids))
    rows = q(f"""SELECT song_id, duration_s, vocal, language
                 FROM song_meta WHERE song_id IN ({ph})""", ids)
    return {r["song_id"]: r for r in rows}


def tag_map(ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {i: {"genres": [], "moods": [], "scenes": []} for i in ids}
    if not ids:
        return out
    ph = ",".join(["%s"] * len(ids))
    # DISTINCT: the same tag can exist under both the llm and jamendo sources
    rows = q(f"""SELECT DISTINCT song_id, tag_type, tag FROM song_tags
                 WHERE song_id IN ({ph}) ORDER BY song_id, tag_type, tag""", ids)
    key = {"genre": "genres", "mood": "moods", "scene": "scenes"}
    for r in rows:
        out.setdefault(r["song_id"], {"genres": [], "moods": [], "scenes": []})[key[r["tag_type"]]].append(r["tag"])
    return out


def to_hits(ordered_ids: list[int],
            scores: dict[int, float] | None = None,
            matches: dict[int, Match] | None = None,
            match_type: str = "tag") -> list[SongHit]:
    scores = scores or {}
    matches = matches or {}
    info, meta, tags = base_info(ordered_ids), meta_map(ordered_ids), tag_map(ordered_ids)
    hits = []
    for rank, sid in enumerate(ordered_ids, 1):
        b = info.get(sid)
        if not b:
            continue
        m = meta.get(sid, {})
        t = tags.get(sid, {"genres": [], "moods": [], "scenes": []})
        hits.append(SongHit(
            song_id=sid, name=b["name"], artist=b["artist"] or "",
            duration_s=m.get("duration_s"), vocal=m.get("vocal"), language=m.get("language"),
            genres=sorted(t["genres"]), moods=sorted(t["moods"]), scenes=sorted(t["scenes"]),
            rank=rank, score=float(scores.get(sid, 0.0)),
            match=matches.get(sid, Match(type=match_type)),  # type: ignore[arg-type]
        ))
    return hits


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #
def keyword_search(text: str, limit: int, exclude: list[int]) -> list[int]:
    """LIKE search over song + singer name.

    NOTE: the schema's ngram FULLTEXT indexes on songs/singers.name are NOT used
    here. On MySQL 8.0.19 an ngram `MATCH ... AGAINST` can crash mysqld
    (init_search@Item_func_match), so we avoid MATCH entirely (see docs).
    """
    like = f"%{text}%"
    ex = f" AND s.id NOT IN ({','.join(['%s']*len(exclude))})" if exclude else ""
    rows = q(f"""SELECT s.id FROM songs s JOIN singers g ON s.singer_id=g.id
                 WHERE (s.name LIKE %s OR g.name LIKE %s){ex}
                 ORDER BY (s.name LIKE %s) DESC, s.id
                 LIMIT {int(limit)}""", [like, like, *exclude, like])
    return [r["id"] for r in rows]


def filter_songs(f: Filter, limit: int, exclude: list[int]) -> list[int]:
    """AND across categories, OR within a category (C7 Filter)."""
    where, args = [], []
    for kind, vals in (("genre", f.genres), ("mood", f.moods), ("scene", f.scenes)):
        if vals:
            ph = ",".join(["%s"] * len(vals))
            where.append(f"EXISTS (SELECT 1 FROM song_tags t WHERE t.song_id=s.id "
                         f"AND t.tag_type=%s AND t.tag IN ({ph}))")
            args += [kind, *vals]
    if f.vocal:
        where.append("m.vocal=%s"); args.append(f.vocal)
    if f.languages:
        ph = ",".join(["%s"] * len(f.languages))
        where.append(f"m.language IN ({ph})"); args += f.languages
    if f.duration_s and (f.duration_s.min or f.duration_s.max):
        if f.duration_s.min is not None:
            where.append("m.duration_s>=%s"); args.append(f.duration_s.min)
        if f.duration_s.max is not None:
            where.append("m.duration_s<=%s"); args.append(f.duration_s.max)
    if exclude:
        ph = ",".join(["%s"] * len(exclude))
        where.append(f"s.id NOT IN ({ph})"); args += exclude
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = q(f"""SELECT s.id FROM songs s JOIN song_meta m ON m.song_id=s.id{clause}
                 ORDER BY s.id LIMIT {int(limit)}""", args)
    return [r["id"] for r in rows]


def song_detail(song_id: int) -> dict | None:
    rows = q("""SELECT s.id, s.name, s.introduction, s.url, s.pic, g.name AS artist,
                       m.duration_s, m.vocal, m.language, m.lrc_source, m.lyric_searchable
                FROM songs s JOIN song_meta m ON m.song_id=s.id
                LEFT JOIN singers g ON g.id=s.singer_id WHERE s.id=%s""", [song_id])
    if not rows:
        return None
    d = rows[0]
    d["tags"] = tag_map([song_id]).get(song_id, {})
    return d


def user_profile(user_id: int, limit: int) -> dict:
    """Most-listened tags from collects + user_plays (C7 get_user_profile)."""
    rows = q("""SELECT t.tag_type, t.tag, COUNT(*) c
                FROM song_tags t
                WHERE t.song_id IN (
                    SELECT song_id FROM collects WHERE user_id=%s
                    UNION SELECT song_id FROM user_plays WHERE user_id=%s)
                GROUP BY t.tag_type, t.tag ORDER BY c DESC LIMIT %s""",
             [user_id, user_id, int(limit)])
    return {"user_id": user_id, "top_tags": rows}


def user_preferences(user_id: int) -> list[dict]:
    """Explicit likes/dislikes written by remember_preference."""
    return q("""SELECT tag, polarity, weight FROM user_preference
                WHERE user_id=%s ORDER BY updated_at DESC""", [user_id])


def execute(sql: str, params: list | tuple = ()) -> int:
    """Write helper (the rest of this module is read-only by convention)."""
    with connect().cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount

