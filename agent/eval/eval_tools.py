"""Phase-2 tool evaluation (phase12 §2.3).

A. 歌词搜歌: sample lyric lines, corrupt them, measure Top-1/Top-5 recall for
   three methods (fulltext-only / vector-only / hybrid RRF).
B. 标签过滤: random filter combos, compare the tool's SQL against an independent
   direct query — must match 100%.
C. 语义检索: nDCG@5 against manually labelled queries in eval/semantic_labels.jsonl
   (skipped if the file is absent).

    python -m agent.eval.eval_tools [--n-lyrics 100] [--n-tags 50]
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import agent.db as db
import agent.index as index
import agent.registry as registry
import agent.tools  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
LINES_FILE = ROOT / "data_pipeline" / "data" / "lyric_lines.json"
LABELS_FILE = Path(__file__).parent / "semantic_labels.jsonl"


def corrupt(text: str, rng: random.Random) -> str:
    """One realistic perturbation per query (phase12 §2.3): a typo, a few deleted
    chars, or keep only half the line."""
    mode = rng.choice(["typo", "delete", "half"])
    words = text.split()
    if mode == "half" and len(words) >= 4:
        return " ".join(words[: max(1, len(words) // 2)])
    chars = list(text)
    if mode == "typo":
        for _ in range(rng.randint(1, 2)):
            if chars:
                chars[rng.randrange(len(chars))] = rng.choice("abcdefghijklmnopqrstuvwxyz")
    else:
        chars = [c for c in chars if rng.random() > 0.12]
    return ("".join(chars) or text).strip()


def _searchable_ids() -> set[int]:
    lf = json.loads((ROOT / "data_pipeline" / "data" / "lyrics_final.json").read_text(encoding="utf-8"))
    return {int(k) for k, v in lf.items() if v.get("lyric_searchable")}


def _ft_ids(query: str, k: int) -> list[int]:
    # LIKE, not MATCH: ngram FULLTEXT crashes mysqld 8.0.19 (see agent/db)
    rows = db.q("SELECT song_id FROM song_lyric_lines WHERE text LIKE %s LIMIT %s",
                [f"%{query}%", k * 3])
    out = []
    for r in rows:
        if r["song_id"] not in out:
            out.append(r["song_id"])
        if len(out) >= k:
            break
    return out


def _vec_ids(query: str, k: int) -> list[int]:
    out = []
    for sid, _ln, _ms, _t, _sc in index.lyrics_vector_search(index.encode(query), k * 3):
        if sid not in out:
            out.append(sid)
        if len(out) >= k:
            break
    return out


def _hyb_ids(query: str, k: int, user=None) -> list[int]:
    res = registry.run("search_by_lyrics", {"query": query, "limit": max(k, 5)},
                       {"user_id": user} if user else {})
    return [i["song_id"] for i in (res.data or {}).get("items", [])]


def eval_lyrics(n: int, seed: int = 7) -> dict:
    rng = random.Random(seed)
    keep_ids = _searchable_ids()  # only songs the lyric index actually covers
    lines = json.loads(LINES_FILE.read_text(encoding="utf-8"))
    lines = [x for x in lines if x["song_id"] in keep_ids and len(x["text"]) >= 12]
    rng.shuffle(lines)
    lines = lines[:n]

    # generate the perturbed queries ONCE so all three methods see the same input
    # (phase3 §6: a shared RNG made the comparison unfair).
    queries = [(row["song_id"], corrupt(row["text"], rng)) for row in lines]

    def score(method) -> dict | None:
        hit1 = hit5 = 0
        try:
            for sid, q in queries:
                ids = method(q, 5)
                if sid in ids[:1]:
                    hit1 += 1
                if sid in ids[:5]:
                    hit5 += 1
        except Exception as e:  # noqa: BLE001  (e.g. MySQL down)
            return {"error": str(e)[:80]}
        return {"top1": round(hit1 / len(queries), 3), "top5": round(hit5 / len(queries), 3)}

    return {"n": len(lines),
            "fulltext": score(lambda q, k: _ft_ids(q, k)),
            "vector": score(lambda q, k: _vec_ids(q, k)),
            "hybrid": score(lambda q, k: _hyb_ids(q, k))}


def eval_tags(n: int, seed: int = 11) -> dict:
    """Cross-check filter_by_tags against an INDEPENDENT SQL formulation (group-by),
    requiring the exact same set (phase12 §2.3)."""
    rng = random.Random(seed)
    import yaml  # noqa: PLC0415
    v = yaml.safe_load((ROOT / "data_pipeline" / "vocab" / "tags_zh.yaml").read_text(encoding="utf-8"))
    from agent.models import Filter

    def direct(f) -> set[int]:
        cats = [("genre", f.genres), ("mood", f.moods), ("scene", f.scenes)]
        cats = [(k, vals) for k, vals in cats if vals]
        if not cats:
            return {r["id"] for r in db.q("SELECT id FROM songs")}
        ors, args = [], []
        for kind, vals in cats:
            ph = ",".join(["%s"] * len(vals))
            ors.append(f"(tag_type=%s AND tag IN ({ph}))")
            args += [kind, *vals]
        sql = (f"SELECT song_id FROM (SELECT song_id, tag_type FROM song_tags "
               f"WHERE {' OR '.join(ors)} GROUP BY song_id, tag_type) x "
               f"GROUP BY song_id HAVING COUNT(*)=%s")
        args.append(len(cats))
        return {r["song_id"] for r in db.q(sql, args)}

    mismatch = 0
    for _ in range(n):
        f = Filter(
            moods=rng.sample(v["mood"], rng.randint(1, 2)) if rng.random() < 0.7 else [],
            scenes=rng.sample(v["scene"], 1) if rng.random() < 0.4 else [],
            genres=rng.sample(v["genre"], 1) if rng.random() < 0.3 else [],
        )
        tool_ids = set(db.filter_songs(f, 500, []))
        if tool_ids != direct(f):
            mismatch += 1
    return {"n": n, "mismatch": mismatch, "consistency": round(1 - mismatch / n, 3)}


def eval_semantic() -> dict | None:
    if not LABELS_FILE.exists():
        return None
    qs = [json.loads(l) for l in LABELS_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    ndcg = []
    for q in qs:
        res = registry.run("semantic_search", {"query": q["query"], "limit": 5})
        got = [i["song_id"] for i in (res.data or {}).get("items", [])]
        rel = set(q.get("relevant_song_ids", []))
        dcg = sum((1 if g in rel else 0) / _log2(i + 2) for i, g in enumerate(got))
        idcg = sum(1 / _log2(i + 2) for i in range(min(len(rel), 5)))
        ndcg.append(dcg / idcg if idcg else 0.0)
    return {"n": len(qs), "ndcg@5": round(sum(ndcg) / len(ndcg), 3) if ndcg else 0.0}


def _log2(x: float) -> float:
    import math
    return math.log2(x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-lyrics", type=int, default=100)
    ap.add_argument("--n-tags", type=int, default=50)
    a = ap.parse_args()

    try:
        index._encoder()  # warm bge-m3 once
    except Exception:  # noqa: BLE001
        pass

    print("== A. 歌词搜歌 recall ==")
    print(json.dumps(eval_lyrics(a.n_lyrics), ensure_ascii=False, indent=2))
    print("\n== B. 标签过滤一致性 ==")
    print(json.dumps(eval_tags(a.n_tags), ensure_ascii=False, indent=2))
    print("\n== C. 语义检索 nDCG@5 ==")
    sem = eval_semantic()
    print(json.dumps(sem, ensure_ascii=False, indent=2) if sem
          else "(skipped: no eval/semantic_labels.jsonl)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
