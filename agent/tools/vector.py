"""Vector / lyric tools: semantic search, similar songs, hybrid lyric search."""
from __future__ import annotations

import agent.config as cfg
import agent.db as db
import agent.index as index
from agent.models import LyricsArgs, Match, SemanticArgs, SimilarArgs, ToolResult
from agent.registry import tool


def _lim(n: int) -> int:
    return max(1, min(int(n), cfg.MAX_LIMIT))


@tool("semantic_search", SemanticArgs,
      "自然语言语义检索歌曲，如“适合深夜的伤感粤语歌”。可先按标签过滤再检索。")
def semantic_search(args: SemanticArgs, ctx: dict) -> ToolResult:
    qvec = index.encode(args.query)
    allowed = set(db.filter_songs(args.filters, 500, [])) if args.filters else None
    res = index.semantic_search(qvec, allowed, args.exclude_ids, _lim(args.limit))
    return ToolResult.ok_items(
        db.to_hits([s for s, _ in res], {s: sc for s, sc in res}, match_type="semantic"))


@tool("similar_songs", SimilarArgs,
      "找和某首歌相似的其他歌曲（向量近邻），会排除同一歌手的歌。")
def similar_songs(args: SimilarArgs, ctx: dict) -> ToolResult:
    seed = db.q("SELECT singer_id FROM songs WHERE id=%s", [args.song_id])
    if not seed:
        return ToolResult.fail("NOT_FOUND", f"song {args.song_id} not found")
    singer_id = seed[0]["singer_id"]
    cand = index.similar_by_song(args.song_id, singer_id, _lim(args.limit) * 4)
    # batch-resolve singers so we can drop same-artist tracks
    singers = {r["id"]: r["singer_id"] for r in
               db.q(f"SELECT id, singer_id FROM songs WHERE id IN "
                    f"({','.join(['%s']*len(cand))})", [s for s, _ in cand])} if cand else {}
    ordered, scores = [], {}
    for sid, sc in cand:
        if sid == args.song_id or sid in args.exclude_ids or singers.get(sid) == singer_id:
            continue
        ordered.append(sid); scores[sid] = sc
        if len(ordered) >= _lim(args.limit):
            break
    return ToolResult.ok_items(db.to_hits(ordered, scores, match_type="similar"))


@tool("search_by_lyrics", LyricsArgs,
      "按歌词片段搜歌，混合全文检索和歌词向量检索（RRF 融合），返回命中的那句歌词和时间戳。")
def search_by_lyrics(args: LyricsArgs, ctx: dict) -> ToolResult:
    limit = _lim(args.limit)
    K = 60  # RRF constant
    fused: dict[str, float] = {}
    info: dict[str, dict] = {}

    def add(rows: list[dict]):
        for rank, r in enumerate(rows, 1):
            k = f"{r['song_id']}:{r['line_no']}"
            fused[k] = fused.get(k, 0.0) + 1.0 / (K + rank)
            info.setdefault(k, r)

    # 1) substring match on lyric lines (LIKE, not MATCH: ngram FULLTEXT crashes
    #    mysqld 8.0.19 — see agent/db.keyword_search). Only searchable songs (F6/C4).
    ft = db.q("""SELECT song_id, line_no, start_ms, text FROM song_lyric_lines
                 WHERE text LIKE %s
                   AND song_id IN (SELECT song_id FROM song_meta WHERE lyric_searchable=1)
                 LIMIT %s""", [f"%{args.query}%", limit * 3])
    add(ft)

    # 2) vector search on lyric lines
    try:
        vec = index.lyrics_vector_search(index.encode(args.query), limit * 3)
        add([{"song_id": s, "line_no": n, "start_ms": ms, "text": t}
             for s, n, ms, t, _sc in vec])
    except index.Unavailable:
        pass  # fulltext-only fallback

    # fuse to song level: keep each song's best line
    best: dict[int, tuple[float, dict]] = {}
    for k, sc in fused.items():
        r = info[k]
        if r["song_id"] in args.exclude_ids:
            continue
        if r["song_id"] not in best or sc > best[r["song_id"]][0]:
            best[r["song_id"]] = (sc, r)
    ordered = sorted(best, key=lambda s: -best[s][0])[:limit]
    matches = {s: Match(type="lyric", line_no=best[s][1]["line_no"],
                        line_text=best[s][1]["text"], start_ms=best[s][1]["start_ms"])
               for s in ordered}
    return ToolResult.ok_items(db.to_hits(ordered, {s: best[s][0] for s in ordered}, matches))
