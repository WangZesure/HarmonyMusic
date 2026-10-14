"""1.6 Generate seed + check SQL for the new corpus (contracts C1/C2/C5).

Writes `db/seed_v1.sql` and `db/check_v1.sql` from `selected.json` +
`lyrics_final.json` + `tags.json`. Import order (db/schema_v1.sql DROPs+recreates
every table, so this file only INSERTs):

    mysql -u root -p < db/schema_v1.sql
    mysql -u root -p < db/seed_v1.sql
    mysql -u root -p < db/check_v1.sql      # every query should return 0 / expected

    python data_pipeline/07_load_mysql.py            # just write the SQL
    python data_pipeline/07_load_mysql.py --apply    # schema + seed via pymysql

Rules (per DocsForMusicAgent/between12.md):
  * IDs are Jamendo track_id / artist_id (C1); url is /stream/{id}, no client_id (C5).
  * names are html-unescaped; fields that overflow are a hard error, never truncated.
  * old songs/singers/song_lists/collects/comments are NOT re-imported; the 2 admins,
    2 test consumers and 3 swipers from music.sql are re-inserted (IDs unchanged).
  * Playlists are generated from mood/scene tags with >= 8 songs.
  * user_plays is left empty this round.
"""
from __future__ import annotations

import argparse
import html
import sys
from collections import defaultdict

import yaml

import config
import common

log = common.get_logger()

SPEEDS = {"verylow", "low", "medium", "high", "veryhigh"}
LRC_SOURCES = {"lrclib", "aligned", "asr", "none"}
PLAYLIST_MIN = 8

# Re-inserted verbatim from music.sql (IDs unchanged).
ADMINS = [
    (1, "admin", "e10adc3949ba59abbe56e057f20f883e", "2025-07-09 04:11:35", "2025-07-09 04:11:44"),
    (2, "admin1", "e10adc3949ba59abbe56e057f20f883e", "2025-07-09 04:11:38", "2025-07-09 04:11:47"),
]
CONSUMERS = [
    (1, "user2", "e10adc3949ba59abbe56e057f20f883e", "1", "19922584707", "1666193045@qq.com",
     "2025-03-12 00:00:00", "sad", "天津", "/img/singerPic/1635182970215liudehua.jpg",
     "2025-03-12 21:54:01", "2025-05-27 01:15:00", "2025-05-27 01:15:00", "2025-05-27 01:15:00"),
    (2, "user1", "e10adc3949ba59abbe56e057f20f883e", "1", "19922584707", "1666193045@qq.com",
     "2025-03-12 00:00:00", "sad", "北京", "/img/singerPic/1635182970215liudehua.jpg",
     "2025-05-21 23:06:44", "2025-05-21 23:06:44", "2025-05-27 01:15:00", "2025-05-27 01:15:00"),
]
SWIPERS = [
    (1, "图1", "https://www.baidu.com", "focus1.jpg", 0, 0, "2025-07-03 05:21:23", "2025-07-03 05:21:25"),
    (2, "图2", "https://www.baidu.com", "focus2.jpg", 0, 0, "2025-07-03 05:21:55", "2025-07-03 05:21:57"),
    (3, "图3", "htts://www.baidu.com", "focus3.jpg", 0, 0, "2025-07-03 05:22:27", "2025-07-03 05:22:30"),
]


class FieldTooLong(Exception):
    pass


def estr(s) -> str:
    s = "" if s is None else str(s)
    return (s.replace("\\", "\\\\").replace("'", "\\'")
            .replace("\n", "\\n").replace("\r", "\\r"))


def q(s) -> str:
    return "'" + estr(s) + "'"


def qn(s) -> str:
    return "NULL" if s is None or s == "" else "'" + estr(s) + "'"


def fit(value, maxlen: int, field: str) -> str:
    """Hard-fail on overflow instead of silently truncating (between12 rule 2)."""
    if len(value) > maxlen:
        raise FieldTooLong(f"{field}: length {len(value)} > {maxlen}: {value[:80]!r}")
    return value


def ins(table: str, cols: list[str], rows: list[str]) -> list[str]:
    if not rows:
        return []
    return [f"INSERT INTO `{table}` ({', '.join('`'+c+'`' for c in cols)}) VALUES\n"
            + ",\n".join(rows) + ";"]


def jamendo_tags(track: dict, vocab: dict) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    moods, scenes = set(vocab["mood"]), set(vocab["scene"])
    for vt in track.get("vartags") or []:
        for zh in vocab["jamendo_vartag_map"].get(str(vt).lower(), []):
            if zh in moods:
                out.add(("mood", zh))
            elif zh in scenes:
                out.add(("scene", zh))
    for g in track.get("genres") or []:
        for zh in vocab["jamendo_genre_map"].get(str(g).lower(), []):
            out.add(("genre", zh))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate seed_v1.sql + check_v1.sql")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    selected = common.load_json(config.SELECTED_FILE)
    lyrics_final = common.load_json(config.LYRICS_FINAL_FILE, default={}) or {}
    tags = common.load_json(config.TAGS_FILE, default={}) or {}
    if not (selected and lyrics_final and tags):
        log.error("need selected.json + lyrics_final.json + tags.json (run 02/05/06).")
        return 2
    with config.VOCAB_FILE.open("r", encoding="utf-8") as f:
        vocab = yaml.safe_load(f)

    singer_rows: dict[int, str] = {}
    song_pic: dict[int, str] = {}
    songs_rows, meta_rows, tag_rows, line_rows = [], [], [], []
    line_objs = []
    tag_objs: list[tuple[int, str, str, str]] = []
    tag_seen: set = set()

    for t in selected:
        tid = int(t["id"])
        aid = int(t["artist_id"])
        tf = lyrics_final.get(str(tid), {})
        tt = tags.get(str(tid), {})
        lrc = tf.get("lrc") or ""
        pic = t.get("album_image") or t.get("image") or ""
        name = html.unescape(t.get("name") or "")
        artist = html.unescape(t.get("artist_name") or "")
        summary = html.unescape(tt.get("summary") or "")
        song_pic[tid] = pic

        if aid not in singer_rows:
            singer_rows[aid] = "(" + ", ".join([
                str(aid), q(fit(artist, 128, "singers.name")), q(""), q(pic), q(""), q(""), q("")]) + ")"

        songs_rows.append("(" + ", ".join([
            str(tid), str(aid), q(fit(name, 128, "songs.name")),
            q(fit(summary, 255, "songs.introduction")), q(pic), q(lrc),
            q(f"/stream/{tid}")]) + ")")

        src = tf.get("lrc_source") or "none"
        meta_rows.append("(" + ", ".join([
            str(tid), str(int(t.get("duration") or 0)),
            q("vocal" if t.get("kind") == "vocal" else "instrumental"),
            q(tt.get("language") or ("zxx" if t.get("kind") != "vocal" else "und")),
            qn(t.get("speed") if t.get("speed") in SPEEDS else None),
            qn((t.get("releasedate") or "")[:10] or None),
            q(t.get("license_ccurl") or ""), q(t.get("shareurl") or ""),
            q(src if src in LRC_SOURCES else "none"),
            qn(tf.get("lrc_quality")), "1" if tf.get("lyric_searchable") else "0",
            q(config.CONTRACT_VERSION)]) + ")")

        for typ, key in (("genre", "genres"), ("mood", "moods"), ("scene", "scenes")):
            for tag in tt.get(key) or []:
                k = (tid, typ, tag, "llm")
                if k not in tag_seen:
                    tag_seen.add(k); tag_objs.append(k)
        for typ, tag in jamendo_tags(t, vocab):
            k = (tid, typ, tag, "jamendo")
            if k not in tag_seen:
                tag_seen.add(k); tag_objs.append(k)

        for row in common.parse_lrc(lrc, t.get("duration")):
            fit(row["text"], 512, "song_lyric_lines.text")
            line_rows.append("(" + ", ".join([
                str(tid), str(row["line_no"]), str(row["start_ms"]),
                str(row["end_ms"]), q(row["text"])]) + ")")
            line_objs.append({"song_id": tid, "line_no": row["line_no"],
                              "start_ms": row["start_ms"], "end_ms": row["end_ms"], "text": row["text"]})

    for tid, typ, tag, source in tag_objs:
        fit(tag, 16, "song_tags.tag")
        tag_rows.append("(" + ", ".join([str(tid), q(typ), q(tag), q(source)]) + ")")

    # --- generated playlists: one per mood/scene tag with >= PLAYLIST_MIN songs ---
    by_tag: dict[tuple[str, str], list[int]] = defaultdict(list)
    for tid, typ, tag, _ in tag_objs:
        if typ in ("mood", "scene") and tid not in by_tag[(typ, tag)]:
            by_tag[(typ, tag)].append(tid)
    list_rows, listsong_rows = [], []
    lid = 0
    for (typ, tag), sids in sorted(by_tag.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(sids) < PLAYLIST_MIN:
            continue
        lid += 1
        list_rows.append("(" + ", ".join([str(lid), q(tag), q(song_pic.get(sids[0], "")),
                                          q(f"按「{tag}」标签自动生成的歌单"), q(tag)]) + ")")
        for sid in sids:
            listsong_rows.append(f"({sid}, {lid})")

    # --- assemble seed ---
    L: list[str] = [
        "-- Seed data v1 - GENERATED by 07_load_mysql.py, do not edit.",
        "-- Import AFTER db/schema_v1.sql (which drops+recreates every table).",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
    ]
    L += ins("admins", ["id", "username", "password", "createdAt", "updatedAt"],
             ["(" + ", ".join(str(v) if i == 0 else q(v) for i, v in enumerate(r)) + ")" for r in ADMINS])
    L += ins("consumers", ["id", "username", "password", "sex", "phone_num", "email", "birth",
                           "introduction", "location", "avatar", "create_time", "update_time",
                           "createdAt", "updatedAt"],
             ["(" + ", ".join(str(v) if i == 0 else q(v) for i, v in enumerate(r)) + ")" for r in CONSUMERS])
    L += ins("swipers", ["id", "title", "url", "imgurl", "state", "order", "createdAt", "updatedAt"],
             ["(" + ", ".join((str(v) if i in (0, 4, 5) else q(v)) for i, v in enumerate(r)) + ")" for r in SWIPERS])
    L += ins("singers", ["id", "name", "sex", "pic", "birth", "location", "introduction"],
             list(singer_rows.values()))
    L += ins("songs", ["id", "singer_id", "name", "introduction", "pic", "lyric", "url"], songs_rows)
    L += ins("song_lists", ["id", "title", "pic", "introduction", "style"], list_rows)
    L += ins("list_songs", ["song_id", "song_list_id"], listsong_rows)
    L += ins("song_meta", ["song_id", "duration_s", "vocal", "language", "speed", "release_date",
                           "license_url", "share_url", "lrc_source", "lrc_quality",
                           "lyric_searchable", "pipeline_version"], meta_rows)
    L += ins("song_tags", ["song_id", "tag_type", "tag", "source"], tag_rows)
    L += ins("song_lyric_lines", ["song_id", "line_no", "start_ms", "end_ms", "text"], line_rows)
    L += ["SET FOREIGN_KEY_CHECKS = 1;"]

    config.SQL_DIR.mkdir(parents=True, exist_ok=True)
    (config.SQL_DIR / "seed_v1.sql").write_text("\n".join(L) + "\n", encoding="utf-8")
    write_check_sql(vocab)
    common.save_json(config.DATA_DIR / "lyric_lines.json", line_objs)

    log.info("wrote db/seed_v1.sql + db/check_v1.sql")
    log.info("  singers=%d songs=%d playlists=%d list_songs=%d meta=%d tags=%d lines=%d",
             len(singer_rows), len(songs_rows), lid, len(listsong_rows),
             len(meta_rows), len(tag_rows), len(line_rows))

    if args.apply:
        import pymysql
        from pymysql.constants import CLIENT
        for name in ("schema_v1.sql", "seed_v1.sql"):
            sql = (config.SQL_DIR / name).read_text(encoding="utf-8")
            conn = pymysql.connect(**config.db_config(), client_flag=CLIENT.MULTI_STATEMENTS)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            finally:
                conn.close()
            log.info("applied %s", name)
    return 0


def write_check_sql(vocab: dict) -> None:
    tags = sorted(set(vocab["genre"]) | set(vocab["mood"]) | set(vocab["scene"]))
    whitelist = ", ".join("'" + t.replace("'", "''") + "'" for t in tags)
    sql = f"""-- db/check_v1.sql - every SELECT should return the value named in its alias.
-- Run after db/seed_v1.sql.  mysql -u root -p < db/check_v1.sql
USE `mymusic`;

SELECT COUNT(*) AS `songs.expect_500` FROM songs;
SELECT COUNT(*) AS `orphan_singer.expect_0`
  FROM songs s LEFT JOIN singers g ON s.singer_id = g.id WHERE g.id IS NULL;
SELECT COUNT(*) AS `bad_url.expect_0` FROM songs WHERE url <> CONCAT('/stream/', id);
SELECT COUNT(*) AS `client_id_leak.expect_0`
  FROM songs WHERE url LIKE '%client_id%' OR lyric LIKE '%client_id%';
SELECT COUNT(*) AS `bad_pic.expect_0` FROM songs WHERE pic NOT LIKE 'https://%';
SELECT COUNT(*) AS `amp_in_song.expect_0`
  FROM songs WHERE name LIKE '%&amp;%' OR introduction LIKE '%&amp;%';
SELECT COUNT(*) AS `amp_in_singer.expect_0` FROM singers WHERE name LIKE '%&amp;%';
SELECT COUNT(*) AS `meta_mismatch.expect_0` FROM (
  SELECT s.id FROM songs s LEFT JOIN song_meta m ON s.id = m.song_id
  GROUP BY s.id HAVING COUNT(m.song_id) <> 1) t;
SELECT COUNT(*) AS `instrumental_meta.expect_0`
  FROM song_meta m JOIN songs s ON s.id = m.song_id
  WHERE m.vocal = 'instrumental' AND (m.language <> 'zxx' OR s.lyric <> '');
SELECT COUNT(*) AS `instrumental_lines.expect_0`
  FROM song_meta m JOIN song_lyric_lines l ON l.song_id = m.song_id WHERE m.vocal = 'instrumental';
SELECT COUNT(*) AS `tag_out_of_vocab.expect_0` FROM song_tags WHERE tag NOT IN ({whitelist});
SELECT COUNT(*) AS `searchable_no_lines.expect_0` FROM song_meta m
  WHERE m.lyric_searchable = 1
    AND NOT EXISTS (SELECT 1 FROM song_lyric_lines l WHERE l.song_id = m.song_id);
-- songs.lyric 与 song_lyric_lines 行数必须一致（normalize_lrc 保证每行以 \\n 结尾）
SELECT COUNT(*) AS `lyric_line_count_mismatch.expect_0` FROM songs s
  LEFT JOIN (SELECT song_id, COUNT(*) c FROM song_lyric_lines GROUP BY song_id) l ON l.song_id = s.id
  WHERE (CHAR_LENGTH(s.lyric) - CHAR_LENGTH(REPLACE(s.lyric, '\\n', ''))) <> COALESCE(l.c, 0);
SELECT COUNT(*) AS `nonmonotonic_time.expect_0` FROM song_lyric_lines a
  JOIN song_lyric_lines b ON a.song_id = b.song_id AND b.line_no = a.line_no + 1
  WHERE b.start_ms <= a.start_ms;
SELECT COUNT(*) AS `orphan_list_song.expect_0` FROM list_songs ls
  LEFT JOIN songs s ON ls.song_id = s.id
  LEFT JOIN song_lists sl ON ls.song_list_id = sl.id
  WHERE s.id IS NULL OR sl.id IS NULL;
-- 歌词可检索（用 LIKE；ngram FULLTEXT 的 MATCH 会让 mysqld 8.0.19 崩溃，不用）
SELECT COUNT(*) AS `lyric_like_hit.expect_ge1` FROM song_lyric_lines
  WHERE text LIKE '%love%';
"""
    (config.SQL_DIR / "check_v1.sql").write_text(sql, encoding="utf-8")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FieldTooLong as e:
        log.error("field overflow: %s", e)
        sys.exit(3)
