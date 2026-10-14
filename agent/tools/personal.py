"""Personalization tools: user profile + recommendation (C7)."""
from __future__ import annotations

import agent.config as cfg
import agent.db as db
from agent.models import ProfileArgs, RecommendArgs, ToolResult
from agent.registry import tool


def _lim(n: int) -> int:
    return max(1, min(int(n), cfg.MAX_LIMIT))


def _played_and_collected(user_id: int) -> list[int]:
    rows = db.q("""SELECT song_id FROM collects WHERE user_id=%s
                   UNION SELECT song_id FROM user_plays WHERE user_id=%s""",
                [user_id, user_id])
    return [r["song_id"] for r in rows]


@tool("get_user_profile", ProfileArgs,
      "读取当前用户最常听的标签（来自收藏和播放记录），作为偏好依据。user_id 由系统注入。")
def get_user_profile(args: ProfileArgs, ctx: dict) -> ToolResult:
    uid = ctx.get("user_id")
    if not uid:
        return ToolResult.fail("INVALID_ARGUMENT", "user_id 未注入")
    return ToolResult(ok=True, data=db.user_profile(int(uid), _lim(args.limit)), error=None)


@tool("recommend_for_user", RecommendArgs,
      "按用户口味推荐歌曲；给 candidates 时对这批候选重排。"
      "（双塔模型在空 user_plays 下退化为标签偏好推荐。）")
def recommend_for_user(args: RecommendArgs, ctx: dict) -> ToolResult:
    limit = _lim(args.limit)
    uid = ctx.get("user_id")
    tags: list[tuple[str, str]] = []
    if uid:
        tags = [(t["tag_type"], t["tag"]) for t in db.user_profile(int(uid), 5)["top_tags"]]

    # rerank mode: reorder given candidates by how many profile tags they hit
    if args.candidates:
        cand = [s for s in args.candidates if s not in args.exclude_ids]
        tmap = db.tag_map(cand)
        want = {t for _, t in tags}

        def hits(sid):
            tg = tmap.get(sid, {})
            got = set(tg["genres"]) | set(tg["moods"]) | set(tg["scenes"])
            return len(want & got)
        cand.sort(key=lambda s: -hits(s))
        return ToolResult.ok_items(db.to_hits(cand[:limit], match_type="cf"))

    # overall mode
    if tags:
        ors = " OR ".join(["(tag_type=%s AND tag=%s)"] * len(tags))
        sql_args = [x for pair in tags for x in pair]
        excl = ""
        if uid:
            played = _played_and_collected(int(uid))
            if played:
                excl = f" AND song_id NOT IN ({','.join(['%s']*len(played))})"
                sql_args += played
        rows = db.q(f"SELECT DISTINCT song_id FROM song_tags WHERE ({ors}){excl} "
                    f"LIMIT {limit}", sql_args)
        ids = [r["song_id"] for r in rows]
    else:
        ids = []  # empty profile -> fall through to popularity
    if not ids:
        ids = [r["id"] for r in db.q(f"SELECT id FROM songs ORDER BY id LIMIT {limit}")]
        return ToolResult.ok_items(db.to_hits(ids, match_type="cf"))
    return ToolResult.ok_items(db.to_hits(ids, match_type="cf"))
