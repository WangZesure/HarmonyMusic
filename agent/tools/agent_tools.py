"""Agent-facing tools (v6). These wrap the existing retrieval core; they do not
change it. All tools are read-only except `remember_preference`.

The loop (`agent/core/agent_loop.py`) dispatches `finish` / `ask_user` specially;
the rest go through `registry.run`.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

import agent.config as cfg
import agent.db as db
from agent.agent_models import (
    AskUserArgs,
    DetailArgs,
    FinishArgs,
    ProfileArgs,
    RefineArgs,
    RememberArgs,
    SearchArgs,
)
from agent.models import Filter, ToolResult
from agent.registry import tool


@lru_cache(maxsize=1)
def _vocab() -> dict:
    with cfg.VOCAB_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _canon_one(value: str) -> str | None:
    """Map a colloquial tag (欢快) to the C3 canonical tag (开心)."""
    v = _vocab()
    x = (value or "").strip()
    if not x:
        return None
    for kind in ("genre", "mood", "scene"):
        if x in (v.get(kind) or []):
            return x
    alias = (v.get("aliases") or {}).get(x)
    if isinstance(alias, str):
        alias = [alias]
    for t in alias or []:
        if t in (v.get("genre") or []) or t in (v.get("mood") or []) or t in (v.get("scene") or []):
            return t
    return None


def _clean_tags(values) -> list[str]:
    out, _bad = _clean_tags_strict(values)
    return out


def _clean_tags_strict(values, kind: str | None = None) -> tuple[list[str], list[str]]:
    """Canonicalize tags; also return the ones outside the C3 vocabulary.

    `kind` (genre/mood/scene) additionally rejects a tag filed in the wrong
    bucket (e.g. genres=['伤感']), which used to pass and then match nothing.
    Silently dropping an unknown tag used to turn a hard constraint into no
    constraint at all (e.g. moods=['悲伤'] -> moods=[]), so callers must reject
    the request and name the offending tags instead.
    """
    vocab = _vocab()
    out: list[str] = []
    bad: list[str] = []
    for raw in values or []:
        x = str(raw).strip()
        if not x:
            continue
        c = _canon_one(x)
        if c and (kind is None or c in (vocab.get(kind) or [])):
            if c not in out:
                out.append(c)
        elif x not in bad:
            bad.append(x)
    return out, bad


def _language_codes(values) -> tuple[list[str], list[str]]:
    """'法语'/'French'/'fr' -> 'fr'. Unknown wording is rejected, not dropped."""
    from agent.core.intent import norm_lang
    out: list[str] = []
    bad: list[str] = []
    for raw in values or []:
        x = str(raw).strip()
        if not x:
            continue
        code = norm_lang(x)
        if code:
            if code not in out:
                out.append(code)
        elif x not in bad:
            bad.append(x)
    return out, bad


def _normalize_filter(f: Filter) -> tuple[Filter, list[str]]:
    genres, b1 = _clean_tags_strict(f.genres, "genre")
    moods, b2 = _clean_tags_strict(f.moods, "mood")
    scenes, b3 = _clean_tags_strict(f.scenes, "scene")
    languages, b4 = _language_codes(f.languages)
    bad = list(dict.fromkeys(b1 + b2 + b3 + b4))
    return Filter(
        genres=genres,
        moods=moods,
        scenes=scenes,
        vocal=f.vocal,
        languages=languages,
        duration_s=f.duration_s,
    ), bad



def _has_filters(f: Filter | None) -> bool:
    return bool(f and (f.genres or f.moods or f.scenes or f.vocal or f.languages or f.duration_s))


def _prefs(user_id: int | None) -> list[dict]:
    if not user_id:
        return []
    try:
        return db.user_preferences(user_id)
    except Exception:  # noqa: BLE001 — preference store must not break search
        return []


def _card(h) -> dict:
    m = h.match
    return {
        "song_id": h.song_id,
        "name": h.name,
        "artist": h.artist,
        "duration_s": h.duration_s,
        "vocal": h.vocal,
        "language": h.language,
        "genres": h.genres,
        "moods": h.moods,
        "scenes": h.scenes,
        "score": round(float(h.score), 4),
        "match": None if m is None else {
            "type": m.type,
            "line_text": (m.line_text or "")[:120],
            "start_ms": m.start_ms,
        },
    }


def _facets(ids: list[int]) -> dict:
    if not ids:
        return {"genres": {}, "moods": {}, "scenes": {}, "languages": {}}
    tags = db.tag_map(ids)
    meta = db.meta_map(ids)
    out: dict[str, dict[str, int]] = {"genres": {}, "moods": {}, "scenes": {}, "languages": {}}
    for sid in ids:
        t = tags.get(sid, {})
        for key in ("genres", "moods", "scenes"):
            for tag in t.get(key, []):
                out[key][tag] = out[key].get(tag, 0) + 1
        lang = (meta.get(sid) or {}).get("language")
        if lang:
            out["languages"][lang] = out["languages"].get(lang, 0) + 1
    return {k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:8]) for k, v in out.items()}


@tool("search_songs", SearchArgs,
      "召回新歌。mode=title/artist 用于点名歌名或歌手（歌手也点名时用 artist 字段），"
      "mode=lyric 用于一句歌词，auto/semantic 用于自然语言描述。query 是输入；"
      "filters 是硬约束；exclude_song_ids 用于“换一首”；exclude_tags 用于“不要X”。"
      "返回最多 30 张紧凑卡 + facets + confidence。不要用它做集合减法（那用 refine_candidates）。")
def search_songs(args: SearchArgs, ctx: dict) -> ToolResult:
    from agent.core.intent import Intent
    from agent.core.pipeline import _run

    user_id = ctx.get("user_id")
    filters, bad = _normalize_filter(args.filters or Filter())
    soft, bad_soft = _clean_tags_strict(args.soft_tags)
    exclude_tags, bad_ex = _clean_tags_strict(args.exclude_tags)
    bad = list(dict.fromkeys(bad + bad_soft + bad_ex))
    if bad:
        return ToolResult.fail(
            "INVALID_ARGUMENT",
            f"unknown/misfiled tags or languages {bad}; use the C3 vocabulary "
            f"(moods: 伤感/开心/平静/治愈/燃/浪漫…; genres: 流行/摇滚/民谣/电子…; "
            f"scenes: 深夜/跑步/雨天…; languages: ISO 639-1 like fr/de/en). "
            f"Each key only accepts its own kind: genres/moods/scenes. "
            f"If the user asked for something outside this vocabulary, call "
            f"ask_user with alternatives instead of answering directly.",
        )
    if not args.query and not _has_filters(filters):
        return ToolResult.fail(
            "INVALID_ARGUMENT",
            "search_songs needs query or filters (soft_tags/exclude_tags only is not a recall)",
        )
    if args.mode in ("title", "artist", "lyric") and not args.query:
        return ToolResult.fail("INVALID_ARGUMENT", f"mode={args.mode} requires query")

    # Long-term memory closure: explicit likes add soft tags, dislikes remove songs.
    for p in _prefs(user_id):
        tag = p.get("tag")
        if not tag:
            continue
        if int(p.get("polarity", 1)) < 0:
            if tag not in exclude_tags:
                exclude_tags.append(tag)
        elif tag not in soft:
            soft.append(tag)

    intent = Intent(filters=filters, soft_tags=soft,
                    semantic_query=args.query, count=max(1, args.limit))
    if args.mode == "title":
        intent = Intent(intents=["title"], title=args.query, artist=args.artist,
                        filters=filters, soft_tags=soft, count=max(1, args.limit))
    elif args.mode == "artist":
        intent = Intent(intents=["artist"], artist=args.query, filters=filters,
                        soft_tags=soft, count=max(1, args.limit))
    elif args.mode == "lyric":
        intent = Intent(intents=["lyric"], lyric_fragment=args.query, filters=filters,
                        soft_tags=soft, count=max(1, args.limit))
    if args.query:
        intent, ranked, matches, relaxed, trace, refuse, gray = _run(
            args.query,
            exclude=set(args.exclude_song_ids),
            extra_soft=None,
            intent_override=intent,
            # “换一首” is a continuation: lower the lyric confidence gate (F7).
            gate=not bool(args.exclude_song_ids),
            relax=args.relax,
        )
        eff_filters = intent.filters          # _run may have relaxed filters
    else:
        ids = db.filter_songs(filters, max(30, args.limit + args.offset + 10), args.exclude_song_ids)
        ranked = [(sid, 1.0) for sid in ids]
        matches, relaxed, trace = {}, [], [{"step": "filter", "n": len(ranked)}]
        refuse, gray = None, False
        eff_filters = filters

    if exclude_tags and ranked:
        tagmap = db.tag_map([s for s, _ in ranked])
        bad = set(exclude_tags)
        def _has_bad(sid: int) -> bool:
            t = tagmap.get(sid, {})
            return bool(bad & (set(t.get("genres", [])) | set(t.get("moods", [])) | set(t.get("scenes", []))))
        ranked = [(s, sc) for s, sc in ranked if not _has_bad(s)]

    total = len(ranked)
    page = ranked[args.offset: args.offset + args.limit]
    hits = db.to_hits([s for s, _ in page], {s: sc for s, sc in page}, matches, match_type="semantic")
    items = [_card(h) for h in hits]
    status = "refused" if refuse else ("empty" if not items else ("low_confidence" if gray else "ok"))
    return ToolResult(ok=True, data={
        "status": status,
        "items": items,
        "facets": _facets([s for s, _ in ranked[:60]]),
        "returned": len(items),
        "total_candidates": total,
        "exhausted": args.offset + len(page) >= total,
        "confidence": round(float(page[0][1]), 4) if page else 0.0,
        "message": refuse or "",
        "applied": {
            "filters": eff_filters.model_dump(exclude_none=True),
            "soft_tags": soft,
            "exclude_tags": exclude_tags,
            "relaxed": relaxed,
            "offset": args.offset,
            "limit": args.limit,
        },
        "trace": trace,
    })


@tool("refine_candidates", RefineArgs,
      "只在**本轮** search_songs 的候选集上做确定性减法，绝不召回新歌。"
      "drop_tags 用于“不要X”，keep_tags 用于“只要X”，drop_song_ids 用于“这首不要”。"
      "上一轮的候选已失效，需要重搜请用 search_songs(exclude_tags=…)。")
def refine_candidates(args: RefineArgs, ctx: dict) -> ToolResult:
    state = ctx.get("state") or {}
    last = state.get("last_search") or {}
    cands = last.get("candidates") or []
    if not cands:
        return ToolResult.fail("NOT_FOUND", "no last_search candidates; call search_songs first")
    # A missing stamp is stale too (rows written before turn_id existed). The only
    # exemption is answering the clarify whose turn produced these candidates.
    pending = state.get("pending_question") or {}
    same_as_pending = bool(pending) and pending.get("turn_id") == last.get("turn_id")
    if last.get("turn_id") != ctx.get("turn_id") and not same_as_pending:
        return ToolResult.fail(
            "INVALID_ARGUMENT",
            "stale candidates from a previous turn; call search_songs with exclude_tags",
        )
    drop_tags, bad_drop = _clean_tags_strict(args.drop_tags)
    keep_tags, bad_keep = _clean_tags_strict(args.keep_tags)
    bad = list(dict.fromkeys(bad_drop + bad_keep))
    if bad:
        return ToolResult.fail(
            "INVALID_ARGUMENT",
            f"unknown tags {bad}; use the C3 vocabulary (伤感/开心/平静, 民谣/摇滚, 深夜/雨天…)",
        )
    if not (drop_tags or keep_tags or args.drop_song_ids):
        return ToolResult.fail(
            "INVALID_ARGUMENT",
            "refine_candidates needs drop_tags / keep_tags / drop_song_ids",
        )
    drop_tags, keep_tags = set(drop_tags), set(keep_tags)
    drop_ids = set(args.drop_song_ids)
    kept, dropped = [], {}
    for c in cands:
        sid = int(c["song_id"])
        tags = set(c.get("genres", [])) | set(c.get("moods", [])) | set(c.get("scenes", []))
        if sid in drop_ids:
            dropped[str(sid)] = "drop_song_ids"
            continue
        if drop_tags and (tags & drop_tags):
            dropped[str(sid)] = "drop_tags:" + ",".join(sorted(tags & drop_tags))
            continue
        if keep_tags and not (tags & keep_tags):
            dropped[str(sid)] = "keep_tags"
            continue
        kept.append(c)
    return ToolResult(ok=True, data={
        "items": kept[: args.limit],
        "kept_ids": [int(c["song_id"]) for c in kept],
        "candidates": kept[:60],          # full set for the loop's state, not just the page
        "returned": len(kept[: args.limit]),
        "remaining": len(kept),
        "dropped": dropped,
        "message": ("" if kept else
                    "refine kept 0 songs; call search_songs with exclude_tags, or ask_user"),
        "applied": {
            "drop_tags": sorted(drop_tags),
            "keep_tags": sorted(keep_tags),
            "drop_song_ids": sorted(drop_ids),
        },
    })


@tool("song_detail", DetailArgs,
      "按 song_id 取单曲详情（时长、语种、标签、简介）。JIT：只在用户追问时调用，不要批量预取。")
def get_song_detail(args: DetailArgs, ctx: dict) -> ToolResult:
    d = db.song_detail(args.song_id)
    if not d:
        return ToolResult.fail("NOT_FOUND", f"song {args.song_id} not found")
    tags = d.get("tags") or {}
    return ToolResult(ok=True, data={
        "song_id": d["id"],
        "name": d["name"],
        "artist": d.get("artist") or "",
        "duration_s": d.get("duration_s"),
        "vocal": d.get("vocal"),
        "language": d.get("language"),
        "genres": tags.get("genres", []),
        "moods": tags.get("moods", []),
        "scenes": tags.get("scenes", []),
        "introduction": (d.get("introduction") or "")[:200],
    })


@tool("user_profile", ProfileArgs,
      "读用户画像：隐式 Top 标签 + 显式 like/dislike 偏好。user_id 由运行时注入。")
def get_user_profile(args: ProfileArgs, ctx: dict) -> ToolResult:
    uid = ctx.get("user_id")
    if not uid:
        return ToolResult.fail("INVALID_ARGUMENT", "user_id missing")
    prof = db.user_profile(uid, args.limit)
    return ToolResult(ok=True, data={
        "user_id": uid,
        "top_tags": prof.get("top_tags", []),
        "explicit": _prefs(uid),
    })


@tool("remember_preference", RememberArgs,
      "写长期偏好（like / dislike），只在用户明确表达时调用。"
      "negative 会在 search_songs 内部变成 exclude_tags；weight ≤ 0.3。")
def remember_preference(args: RememberArgs, ctx: dict) -> ToolResult:
    uid = ctx.get("user_id")
    if not uid:
        return ToolResult.fail("INVALID_ARGUMENT", "user_id missing")
    tag = _canon_one(args.tag)
    if not tag:
        return ToolResult.fail("INVALID_ARGUMENT", f"unknown tag {args.tag!r}; use the C3 vocabulary")
    polarity = 1 if args.polarity == "like" else -1
    weight = max(0.05, min(float(args.weight), 0.3))
    db.execute(
        """INSERT INTO user_preference (user_id, tag, polarity, weight, source_turn)
           VALUES (%s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE
             polarity=VALUES(polarity), weight=VALUES(weight),
             source_turn=VALUES(source_turn), updated_at=CURRENT_TIMESTAMP""",
        (uid, tag, polarity, weight, ctx.get("turn_id")),
    )
    return ToolResult(ok=True, data={"tag": tag, "polarity": args.polarity, "weight": weight})


@tool("ask_user", AskUserArgs,
      "约束不足、结果为空或需要用户确认时，向用户提问。写入 pending_question 并结束本轮。")
def ask_user(args: AskUserArgs, ctx: dict) -> ToolResult:
    return ToolResult(ok=True, data={"output": {
        "type": "clarify",
        "question": args.question,
        "options": args.options[:5],
        "slot": args.slot,
    }})


@tool("finish", FinishArgs,
      "最终输出工具。song_ids 必须来自工具返回集；reasons 只能引用卡片字段；"
      "source=template 时理由固定为“歌名 · 歌手”。服务端会做 id 白名单与约束复核。")
def finish(args: FinishArgs, ctx: dict) -> ToolResult:
    allowed = set(ctx.get("allowed_ids") or [])
    illegal = [s for s in args.song_ids if s not in allowed]
    if illegal:
        return ToolResult.fail(
            "INVALID_ARGUMENT",
            f"these ids are not in the allowed set (not returned this turn, or removed by "
            f"refine): {illegal}; use only ids from this turn's search_songs/refine_candidates",
        )

    filters = ctx.get("filters")
    if _has_filters(filters):
        # Same predicate as retrieval (rank.satisfies): with a language filter
        # active, und/zxx/NULL are a mismatch, exactly like db.filter_songs.
        from agent.core.rank import satisfies
        bad = [s for s in args.song_ids if not satisfies(s, filters)]
        if bad:
            return ToolResult.fail(
                "INVALID_ARGUMENT",
                f"these ids violate the active filters: {bad}; pick others or relax the filters",
            )

    hits = db.to_hits(args.song_ids, match_type="semantic")
    by_id = {h.song_id: h for h in hits}
    keep_tags = set(ctx.get("keep_tags") or [])
    if keep_tags:
        bad = [s for s in args.song_ids
               if s in by_id and not (keep_tags & (set(by_id[s].genres) |
                                                   set(by_id[s].moods) |
                                                   set(by_id[s].scenes)))]
        if bad:
            return ToolResult.fail(
                "INVALID_ARGUMENT",
                f"these ids do not match the active keep_tags {sorted(keep_tags)}: {bad}",
            )
    songs = []
    for sid in args.song_ids:
        h = by_id.get(sid)
        if not h:
            continue
        if args.source == "template":
            reason = f"{h.name} · {h.artist}"
        else:
            reason = (args.reasons.get(sid) or args.reasons.get(str(sid))
                      or f"{h.name} · {h.artist}")
        songs.append({**h.model_dump(), "reason": str(reason)[:120]})
    if not songs:
        return ToolResult.fail("INVALID_ARGUMENT", "none of the requested songs could be hydrated")
    return ToolResult(ok=True, data={"output": {
        "type": "songs",
        "songs": songs,
        "message": args.message or "为你找到以下歌曲：",
        "degraded": None,
    }})
