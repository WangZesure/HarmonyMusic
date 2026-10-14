"""Score any search method against a golden set (phase3 §0/§5).

    python -m agent.eval.run_eval --file golden_dev.jsonl --methods b2,b3 [--limit 100]

Methods: b0 b1 b2 b3  (and later "agent"). Prints a per-type metrics table.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

EVAL_DIR = Path(__file__).parent


def dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def score_entry(entry: dict, ranked: list[int]) -> dict:
    t = entry["type"]
    gold = entry.get("gold_ids") or []
    if t in ("title", "lyric", "describe", "describe_fuzzy"):
        hit1 = bool(gold and ranked[:1] and ranked[0] in gold)
        hit5 = bool(gold and any(g in ranked[:5] for g in gold))
        mrr = next((1 / (i + 1) for i, x in enumerate(ranked[:5]) if x in gold), 0.0)
        return {"hit1": int(hit1), "hit5": int(hit5), "mrr": mrr}
    if t == "mood":
        graded = {int(k): v for k, v in (entry.get("graded") or {}).items()}
        gains = [graded.get(x, 0) for x in ranked[:5]]
        ideal = sorted(graded.values(), reverse=True)[:5]
        idcg = dcg(ideal)
        return {"ndcg5": (dcg(gains) / idcg) if idcg else 0.0}
    if t == "constraint":
        f = entry.get("constraints") or {}
        import agent.db as db
        from agent.models import Filter
        allowed = set(db.filter_songs(Filter(**f), 500, []))
        sat = [x in allowed for x in ranked[:5]]
        return {"satisfaction5": (sum(sat) / len(sat)) if sat else 1.0}
    if t == "absent":
        return {"refused": int(len(ranked) == 0)}
    return {}


def run_method(method, entries: list[dict], k: int = 5) -> dict:
    per_type: dict[str, list[dict]] = {}
    gray = 0
    for e in entries:
        try:
            ranked = method.search(e["query"], k)
        except Exception as ex:  # noqa: BLE001
            ranked = []
            per_type.setdefault(e["type"], []).append({"err": str(ex)[:60]})
            continue
        if getattr(method, "last_gray", False):
            gray += 1                     # F1 gray zone: not a hit, not a refusal -> excluded
            continue
        per_type.setdefault(e["type"], []).append(score_entry(e, ranked))
    # aggregate
    agg = {}
    for t, rows in per_type.items():
        keys = [k2 for r in rows for k2 in r if k2 != "err"]
        agg[t] = {}
        for key in set(keys):
            vals = [r[key] for r in rows if key in r]
            agg[t][key] = round(sum(vals) / len(vals), 3) if vals else None
    if gray:
        agg["_gray_skipped"] = gray
    return agg


def build_methods(names: list[str]):
    from agent.eval import baselines
    objs = {}
    for n in names:
        n = n.strip().lower()
        if n == "b0": objs[baselines.B0.name] = baselines.B0()
        if n == "b1": objs[baselines.B1.name] = baselines.B1()
        if n == "b2": objs[baselines.B2.name] = baselines.B2()
        if n == "b3": objs[baselines.B3.name] = baselines.B3()
        if n in ("agent", "a"): objs[baselines.Agent.name] = baselines.Agent()
    return objs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="golden_dev.jsonl")
    ap.add_argument("--methods", default="b2,b3")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--k", type=int, default=5)
    a = ap.parse_args()

    entries = [json.loads(l) for l in (EVAL_DIR / a.file).read_text(encoding="utf-8").splitlines() if l.strip()]
    if a.limit:
        entries = entries[: a.limit]
    try:
        import agent.index as index
        index._encoder()
    except Exception:  # noqa: BLE001
        pass
    methods = build_methods(a.methods.split(","))

    print(f"# {a.file}  ({len(entries)} entries)  k={a.k}")
    for name, m in methods.items():
        agg = run_method(m, entries, a.k)
        print(f"\n== {name} ==")
        for t, metrics in agg.items():
            print(f"  {t:11} {metrics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
