"""Multi-route recall (phase3 §2.2). Each route returns top-k song_ids; lyric routes
also carry the matched line so the reranker can score against it."""
from __future__ import annotations

import agent.registry as registry
import agent.tools  # noqa: F401
from agent.core.intent import Intent
from agent.models import Match
from agent.tools import bm25
import agent.db as db
from difflib import SequenceMatcher
from functools import lru_cache

K = 30


def COMBINE(b: float, v: float) -> float:
    """Combine BM25 (normalized) and vector sim for a song hit by both (P0-1)."""
    return max(b, v)          # alternative: 0.6 * b + 0.4 * v


@lru_cache(maxsize=1)
def _names():
    return [(r["id"], (r["name"] or "").lower()) for r in db.q("SELECT id, name FROM songs")]


def _lev(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def fuzzy_title(title: str, artist: str | None = None) -> int | None:
    """Best corpus song whose name is close to `title` (typos / abbreviations).

    phase3loop P1-1: ratio >= 0.75, OR query is a prefix/substring of the name
    (`str` ⊂ `star`), OR edit distance <= 2 with the longer side >= 4 chars.
    If an artist is given, only that artist's songs are considered.
    """
    t = (title or "").lower().strip()
    if not t:
        return None
    from agent.core import ablate
    if ablate.on("no_fuzzy"):
        return None
    if artist:
        rows = db.q("""SELECT s.id, s.name FROM songs s JOIN singers g ON g.id=s.singer_id
                       WHERE g.name LIKE %s""", [f"%{artist}%"])
        if not rows:
            return None                       # artist not in corpus
        cands = [(r["id"], (r["name"] or "").lower()) for r in rows]
    else:
        cands = _names()

    best, bs = None, 0.0
    for sid, nm in cands:
        if nm == t:
            return sid
        score = SequenceMatcher(None, t, nm).ratio()
        if t in nm or nm in t:
            if min(len(t), len(nm)) >= 3:
                score = max(score, 0.9)       # abbreviation / partial name
        elif max(len(t), len(nm)) >= 4 and _lev(t, nm) <= 2:
            score = max(score, 0.85)          # 1-2 char typo
        if score > bs:
            bs, best = score, sid
    return best if bs >= 0.75 else None


def _dedupe(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def _has_filters(f) -> bool:
    return bool(f.genres or f.moods or f.scenes or f.vocal or f.languages or f.duration_s)


# Only lyric_searchable songs are searchable (contract C4 / F6).
_SEARCHABLE = " AND song_id IN (SELECT song_id FROM song_meta WHERE lyric_searchable=1)"


def _lyric_route(intent: Intent, k: int):
    """Rank the lyric route by a REAL confidence and return (order, matches, conf).

    confidence = max(LIKE-hit, token-coverage of the matched line, vector sim).
    BM25 raw score is NOT used (its per-query max normalization makes the top always
    1.0 -> useless for "is this in the corpus") — F1.
    """
    import agent.index as index
    import agent.db as db
    import re
    from agent.tools.bm25 import _tok
    _CJK = re.compile(r"[一-鿿]")
    frags = ([intent.lyric_fragment] if intent.lyric_fragment else []) + list(intent.lyric_fragments_en or [])
    like_best: dict[int, float] = {}
    cov_best: dict[int, float] = {}
    vec_best: dict[int, float] = {}
    lines: dict[int, dict] = {}
    for q in frags:
        if not q:
            continue
        original = (q == intent.lyric_fragment)
        if original and not _CJK.search(q):
            for r in db.q("SELECT song_id, line_no, start_ms, text FROM song_lyric_lines "
                          "WHERE text LIKE %s" + _SEARCHABLE + " LIMIT %s", [f"%{q}%", k * 3]):
                like_best[r["song_id"]] = 1.0
                lines.setdefault(r["song_id"], {**r, "text": r["text"]})
            qtok = set(_tok(q))
            if qtok:
                for h in bm25.search_lyrics(q, k * 3):
                    cov = len(qtok & set(_tok(h["text"]))) / len(qtok)
                    cov_best[h["song_id"]] = max(cov_best.get(h["song_id"], 0.0), cov)
                    lines.setdefault(h["song_id"], h)
        try:
            vec = index.lyrics_vector_search(index.encode(q), k * 3)
        except Exception:  # noqa: BLE001
            vec = []
        for sid, line_no, start_ms, text, sim in vec:
            vec_best[sid] = max(vec_best.get(sid, 0.0), float(sim))
            lines.setdefault(sid, {"song_id": sid, "line_no": line_no,
                                   "start_ms": start_ms, "text": text})

    def conf(sid: int) -> float:
        vals = [v for v in (like_best.get(sid), cov_best.get(sid), vec_best.get(sid)) if v is not None]
        return max(vals) if vals else 0.0

    conf_map = {s: conf(s) for s in set(like_best) | set(cov_best) | set(vec_best)}
    order = sorted(conf_map, key=lambda s: -conf_map[s])

    # Rerank the top candidates with a cross-encoder against the MATCHED LYRIC LINE
    # (query = the lyric fragment). This is the confidence used to refuse
    # out-of-corpus lyrics (F1) and to order the head.
    try:
        from agent.core.rank import _reranker
        ce = _reranker()
        allfrags = [f for f in frags if f]
        head = order[:15]
        if allfrags and head:
            best = [0.0] * len(head)
            for f in allfrags:                      # include the English paraphrases:
                sc = ce.predict([(f, lines[s]["text"]) for s in head])  # cross-lang for zh, same-lang for en
                best = [max(b, float(x)) for b, x in zip(best, sc)]
            conf_map.update({s: b for s, b in zip(head, best)})
            # keep the coverage/vector ORDER (preserves Hit@5); the reranker score
            # is used only as the refusal confidence (F1).
    except Exception:  # noqa: BLE001  (reranker unavailable -> keep coverage/vec conf)
        pass

    return order, {s: Match(type="lyric", line_no=lines[s]["line_no"],
                            line_text=lines[s]["text"], start_ms=lines[s]["start_ms"]) for s in order}, conf_map


def title_found(title: str, artist: str | None = None) -> bool:
    """Is `title` really in the corpus? Requires an exact/substring name match
    (len>=4) or a fuzzy match — NOT just shared BM25 tokens (phase3loop P1-2:
    'Someone Like You' must not match 'Kill You')."""
    t = (title or "").strip().lower()
    if not t:
        return False
    for _sid, nm in _names():
        if nm == t:
            return True
        if (t in nm or nm in t) and min(len(t), len(nm)) >= 4:
            return True
    return fuzzy_title(title, artist) is not None


def recall(intent: Intent, k: int = K):
    """Returns (routes, matches, meta) where meta has lyric_conf (F1) and similar_seed (F3)."""
    routes: dict[str, list[int]] = {}
    matches: dict[int, dict] = {}
    lyric_conf: dict[int, float] = {}
    similar_seed: int | None = None

    if intent.title or intent.artist:
        q = intent.title or intent.artist
        ids = [sid for sid, _ in bm25.search_titles(q, k)]
        if not ids and intent.title:
            f = fuzzy_title(intent.title, intent.artist)   # typo/abbrev match, artist-scoped if given
            if f:
                ids = [f]
        routes["title"] = ids

    # lyric: rank the route internally by confidence (P0-1 / F1)
    if intent.lyric_fragment or intent.lyric_fragments_en:
        order, best_lines, lyric_conf = _lyric_route(intent, k)
        routes["lyric"] = order
        matches.update(best_lines)

    if intent.semantic_query:
        args = {"query": intent.semantic_query, "limit": k}
        if _has_filters(intent.filters):
            args["filters"] = intent.filters.model_dump(exclude_none=True)
        res = registry.run("semantic_search", args)
        routes["semantic"] = [i["song_id"] for i in (res.data or {}).get("items", [])]

    if "similar" in intent.intents:
        seed_q = intent.title or intent.artist or intent.semantic_query
        seeds = [sid for sid, _ in bm25.search_titles(seed_q or "", 1)]
        if seeds:
            similar_seed = seeds[0]
            res = registry.run("similar_songs", {"song_id": similar_seed, "limit": k})
            routes["similar"] = [i["song_id"] for i in (res.data or {}).get("items", [])]
            # F3: the seed itself must never rank (it would win the name-bonus rule)
            for r in routes:
                routes[r] = [s for s in routes[r] if s != similar_seed]

    return routes, matches, {"lyric_conf": lyric_conf, "similar_seed": similar_seed}
