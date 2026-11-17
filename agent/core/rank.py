"""Fusion + hard filter + rerank (phase3 §2.3)."""
from __future__ import annotations

from functools import lru_cache

import agent.db as db
from agent.core.intent import Intent

# route weights (tuned on dev); higher = more influence in RRF
WEIGHTS = {"title": 1.0, "semantic": 1.0, "similar": 0.8}


def _weight(route: str) -> float:
    if route.startswith("lyric"):
        return 1.2 if route.endswith("_vec") else 1.0
    return WEIGHTS.get(route, 1.0)


K = 60


def rrf(routes: dict[str, list[int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for route, ids in routes.items():
        w = _weight(route)
        for rank, sid in enumerate(ids, 1):
            scores[sid] = scores.get(sid, 0.0) + w / (K + rank)
    return scores


def satisfies(sid: int, f) -> bool:
    if not (f.genres or f.moods or f.scenes or f.vocal or f.languages or f.duration_s):
        return True
    m = db.meta_map([sid]).get(sid, {})
    t = db.tag_map([sid]).get(sid, {"genres": [], "moods": [], "scenes": []})
    if f.vocal and m.get("vocal") != f.vocal:
        return False
    if f.languages and m.get("language") not in f.languages:
        return False   # strict, same as db.filter_songs: und/zxx/NULL cannot satisfy a language filter
    if f.duration_s:
        d = m.get("duration_s") or 0
        if f.duration_s.min is not None and d < f.duration_s.min:
            return False
        if f.duration_s.max is not None and d > f.duration_s.max:
            return False
    if f.genres and not (set(f.genres) & set(t["genres"])):
        return False
    if f.moods and not (set(f.moods) & set(t["moods"])):
        return False
    if f.scenes and not (set(f.scenes) & set(t["scenes"])):
        return False
    return True


@lru_cache(maxsize=1)
def _reranker():
    import os
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from sentence_transformers import CrossEncoder
    # cap max_length: bge-reranker-v2-m3 defaults to 8192, and padding every
    # (short) pair to that makes CPU inference seconds-long.
    return CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=256)


@lru_cache(maxsize=1)
def _cards() -> dict:
    """song_id -> card text (loaded once; was re-read every query)."""
    import json
    import agent.config as cfg
    sel = json.loads((cfg.DATA_DIR / "selected.json").read_text(encoding="utf-8"))
    tags = json.loads((cfg.DATA_DIR / "tags.json").read_text(encoding="utf-8"))
    out = {}
    for t in sel:
        tt = tags.get(str(t["id"]), {})
        tg = tt.get("genres", []) + tt.get("moods", []) + tt.get("scenes", [])
        out[int(t["id"])] = f"{t['name']} / {t['artist_name']} / {','.join(tg)} / {tt.get('summary','')}"
    return out


def _candidate_text(sid: int, match, cards: dict) -> str:
    if match is not None and getattr(match, "line_text", None):
        return f"{cards.get(sid,'')} 命中歌词: {match.line_text}"
    return cards.get(sid, "")


def rank(intent: Intent, routes: dict[str, list[int]], matches: dict[int, dict],
         top_n: int = 30, exclude=None, lyric_conf: dict[int, float] | None = None) -> list[tuple[int, float]]:
    """Return [(song_id, score)] fused + hard-filtered + reranked. `exclude` drops
    already-shown songs (phase-4 '换一首')."""
    exclude = set(exclude or ())
    # Lyric queries: keep the lyric route's REAL confidence (F1), not a fixed 1.0.
    from agent.core import ablate
    if (intent.lyric_fragment or "lyric" in intent.intents) and not ablate.on("lyric_legacy"):
        ly = [s for s in (routes.get("lyric") or [])
              if s not in exclude and satisfies(s, intent.filters)][:top_n]
        if ly:
            lc = lyric_conf or {}
            return [(s, float(lc.get(s, 0.0))) for s in ly]

    fused = rrf(routes)
    # hard filter (C7: filter by metadata, never by the model)
    cand = [s for s in sorted(fused, key=lambda s: -fused[s])
            if s not in exclude and satisfies(s, intent.filters)][:top_n]
    if not cand:
        return []

    # soft-tag bonus
    if intent.soft_tags:
        tm = db.tag_map(cand)
        for s in cand:
            t = tm.get(s, {})
            hit = set(intent.soft_tags) & (set(t["genres"]) | set(t["moods"]) | set(t["scenes"]))
            if hit:
                fused[s] = fused.get(s, 0) + 0.01 * len(hit)

    # rerank with cross-encoder over song cards (+ matched lyric line)
    try:
        cards = _cards()
        ce = _reranker()
        pairs = [(intent.semantic_query or intent.lyric_fragment or intent.title
                  or intent.artist or "",
                  _candidate_text(s, matches.get(s), cards)) for s in cand]
        import math
        raw = ce.predict(pairs)
        q = (intent.semantic_query or intent.lyric_fragment or intent.title or intent.artist or "").lower()
        info = db.base_info(cand)
        scored = []
        for s, x in zip(cand, raw):
            sc = 1.0 / (1.0 + math.exp(-float(x)))       # reranker, 0-1
            nm = (info.get(s, {}).get("name") or "").lower()
            if nm and nm in q and "similar" not in intent.intents:   # F3: never boost the similar seed
                sc += 1.0
            scored.append((s, sc))
        return sorted(scored, key=lambda x: -x[1])
    except Exception:  # noqa: BLE001  (reranker unavailable -> keep RRF order, scores scaled)
        base = [(s, fused.get(s, 0) * 10) for s in cand]
        return base
