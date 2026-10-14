"""Verify / relax / refuse (phase3 §2.4)."""
from __future__ import annotations

from agent.core.intent import Intent
from agent.models import Filter

# Named-entity / relevance thresholds
NAME_MIN_BM25 = 1.0      # BM25 score for title/artist route to count as "found"
RELEVANCE_MIN = 0.30     # sigmoid rerank score below which we say "不太确定"

# FROZEN (2026-10-14, architect): 0.75 is the final lyric operating point.
# Empirical, calibrated on 20 out-of-corpus lyrics + dev lyric (see phase3loop.md).
# DUAL TARGET IS STRUCTURALLY IMPOSSIBLE (out-of-corpus famous lines overlap the
# in-corpus paraphrase band in [0.7, 0.9]) -> refusal is prioritised over recall.
# Acceptance: out-of-corpus refusal >=0.85, dev lyric Hit@5 >=0.90.
# DO NOT re-tune this to chase a score.
LYRIC_MIN_CONF = 0.75
# Gray zone [GRAY_LOW, GRAY_LYRIC_MIN_CONF): don't hard-refuse, don't claim "found" —
# return up to 2 closest songs with a "不太确定" message. Implemented but **disabled**:
# under the architect's "gray 不计入拒答率" counting, enabling it drops absent refusal
# from 0.892 to 0.833 — below the frozen >=0.85 line. Needs an architect call
# (enable + relax the line, or count gray as a soft refusal). Do not enable silently.
GRAY_ENABLED = False
GRAY_LOW = 0.65
RELAX_MAX = 2


def relax_sequence(intent: Intent) -> list[tuple[str, Intent]]:
    """Ordered relaxations. User-stated vocal/language/duration are NEVER relaxed."""
    out: list[tuple[str, Intent]] = []
    if intent.soft_tags:
        out.append(("去掉软偏好", intent.model_copy(update={"soft_tags": []})))
        intent = out[-1][1]
    if intent.filters.scenes:
        f = intent.filters.model_copy(update={"scenes": []})
        out.append(("放宽场景", intent.model_copy(update={"filters": f})))
        intent = out[-1][1]
    if len(intent.filters.moods) > 1:
        f = intent.filters.model_copy(update={"moods": intent.filters.moods[:1]})
        out.append(("放宽次要情绪", intent.model_copy(update={"filters": f})))
        intent = out[-1][1]
    if intent.filters.genres:
        f = intent.filters.model_copy(update={"genres": []})
        out.append(("放宽流派", intent.model_copy(update={"filters": f})))
    return out


def named_not_found(intent: Intent, routes: dict) -> bool:
    """True if the user named a title/artist that is NOT reliably in the corpus."""
    if not (intent.title or intent.artist):
        return False
    if intent.title:
        from agent.core.recall import title_found
        return not title_found(intent.title, intent.artist)
    return not routes.get("title")  # artist-only query with no hit


def artist_mismatch(intent: Intent, routes: dict) -> str | None:
    """If the user named BOTH title and artist: at least one title candidate must be
    by that artist (normalized containment / edit distance). Otherwise refuse."""
    if not (intent.title and intent.artist):
        return None
    import re as _re
    from difflib import SequenceMatcher
    import agent.db as db
    cand = (routes.get("title") or [])[:5]
    if not cand:
        return None  # no title hit at all -> handled by named_not_found

    def norm(x: str) -> str:
        return _re.sub(r"^the", "", (x or "").lower().replace(" ", ""))

    want = norm(intent.artist)
    info = db.base_info(cand)
    for s in cand:
        got = norm(info.get(s, {}).get("artist", ""))
        if got and (want in got or got in want or SequenceMatcher(None, want, got).ratio() >= 0.6):
            return None
    top = info.get(cand[0], {})
    msg = f"曲库里没有 {intent.artist} 的《{intent.title}》。"
    if top.get("name"):
        msg += f"另有一首同名《{top.get('name')}》（歌手：{top.get('artist')}）。"
    return msg
