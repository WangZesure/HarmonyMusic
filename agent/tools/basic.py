"""DB-backed tools: keyword search, tag filter, song detail."""
from __future__ import annotations

import agent.config as cfg
import agent.db as db
from agent.models import DetailArgs, KeywordArgs, SongHit, TagsArgs, ToolResult, Match
from agent.registry import tool


def _lim(n: int) -> int:
    return max(1, min(int(n), cfg.MAX_LIMIT))


@tool("search_by_keyword", KeywordArgs,
      "按歌名/歌手/专辑关键词精确搜索歌曲。适合“周杰伦的晴天”这类明确指名请求。")
def search_by_keyword(args: KeywordArgs, ctx: dict) -> ToolResult:
    ids = db.keyword_search(args.query, _lim(args.limit), args.exclude_ids)
    return ToolResult.ok_items(db.to_hits(ids, match_type="keyword"))


@tool("filter_by_tags", TagsArgs,
      "按结构化标签（风格genre/情绪mood/场景scene）过滤歌曲，标签取值必须在受控词表内。")
def filter_by_tags(args: TagsArgs, ctx: dict) -> ToolResult:
    ids = db.filter_songs(args.filters, _lim(args.limit), args.exclude_ids)
    return ToolResult.ok_items(db.to_hits(ids, match_type="tag"))


@tool("get_song_detail", DetailArgs,
      "查询单首歌的摘要信息（歌名、歌手、标签、时长、语种），不返回完整歌词。")
def get_song_detail(args: DetailArgs, ctx: dict) -> ToolResult:
    d = db.song_detail(args.song_id)
    if not d:
        return ToolResult.fail("NOT_FOUND", f"song {args.song_id} not found")
    hits = db.to_hits([args.song_id], match_type="keyword")
    return ToolResult(ok=True, data={"items": [h.model_dump() for h in hits],
                                     "introduction": d.get("introduction") or ""},
                      error=None)
