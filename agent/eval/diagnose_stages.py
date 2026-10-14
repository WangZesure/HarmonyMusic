"""Per-query stage trace: where does the gold song get lost?

For every entry, prints the gold song's position after each pipeline step
(each recall route, RRF fusion, hard filter, rerank) plus the B2 position, then
a summary of failures by stage.

    python -m agent.eval.diagnose_stages [--file golden_dev.jsonl] [--out diag.json]
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

import agent.registry as registry
import agent.tools  # noqa: F401
from agent.core import rank as K
from agent.core import recall as R
from agent.core import verify as V
from agent.core.intent import parse

EVAL_DIR = Path(__file__).parent


def trace(e: dict) -> dict:
    q, gold = e["query"], set(e.get("gold_ids") or [])
    it = parse(q)
    routes, matches, _meta = R.recall(it)
    lyric_conf = _meta.get("lyric_conf", {})
    thr = V.LYRIC_MIN_CONF if (it.lyric_fragment or "lyric" in it.intents) else V.RELEVANCE_MIN

    def pos(ids):
        return next((i + 1 for i, x in enumerate(ids) if x in gold), None)

    fused = K.rrf(routes)
    fused_order = sorted(fused, key=lambda s: -fused[s])
    cand = [s for s in fused_order if K.satisfies(s, it.filters)][:30]
    refused_named = V.named_not_found(it, routes)
    ranked = [] if refused_named else K.rank(it, routes, matches, lyric_conf=lyric_conf)
    top = ranked[0][1] if ranked else None
    b2 = [i["song_id"] for i in
          (registry.run("semantic_search", {"query": q, "limit": 30}).data or {}).get("items", [])]
    return {
        "id": e["id"], "type": e["type"], "q": q,
        "intents": it.intents, "title": it.title,
        "frag": it.lyric_fragment, "frag_en": it.lyric_fragments_en, "sem": it.semantic_query,
        "filters": {k: v for k, v in it.filters.model_dump(exclude_none=True).items() if v},
        "route_pos": {k: pos(v) for k, v in routes.items()},
        "fused_pos": pos(fused_order), "after_filter_pos": pos(cand),
        "final_pos": pos([s for s, _ in ranked]),
        "top_score": round(top, 3) if top is not None else None,
        "gold_score": next((round(sc, 3) for s, sc in ranked if s in gold), None),
        "refused_named": refused_named,
        "low_conf": bool(ranked and top < thr),
        "b2_pos": pos(b2),
    }


def stage(r: dict) -> str:
    if r["type"] == "absent":
        refused = r["refused_named"] or r["low_conf"] or r["top_score"] is None
        return "absent_refused" if refused else "absent_answered"
    if r["refused_named"]:
        return "named_refusal"
    if r["fused_pos"] is None:
        return "not_recalled"
    if r["after_filter_pos"] is None:
        return "hard_filter_drop"
    if r["final_pos"] is None:
        return "cut_top30"
    if r["low_conf"]:
        return "low_conf_refusal"
    if r["final_pos"] > 5:
        return "rerank_below5" + ("_was_top5" if r["after_filter_pos"] <= 5 else "")
    return "HIT"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="golden_dev.jsonl")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    entries = [json.loads(l) for l in (EVAL_DIR / a.file).read_text(encoding="utf-8").splitlines() if l.strip()]
    recs = []
    for e in entries:
        r = trace(e)
        r["stage"] = stage(r)
        recs.append(r)
        if r["stage"] not in ("HIT", "absent_refused"):
            print(json.dumps(r, ensure_ascii=False), flush=True)
    print("\n== failures by stage ==")
    for (t, s), n in sorted(collections.Counter((r["type"], r["stage"]) for r in recs).items()):
        print(f"  {t:9} {s:22} {n}")
    if a.out:
        Path(a.out).write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
