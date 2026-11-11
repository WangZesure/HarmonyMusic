"""Evaluation: leave-one-out (strict) + profile-level relevance (realistic).

Two protocols, because they answer different questions:

1. **Leave-one-out (LOO)** — hold out each user's *last* play and see whether the
   model ranks that exact song first. This is the standard protocol, but it is
   harsh here: a user has ~200 taste-matching songs in a 500-song catalog, so
   "the one song they happened to play last" is not uniquely relevant. Reported
   honestly, not hidden.

2. **Profile-level** — the generator knows each user's true taste profile
   (genres/moods/scenes). Songs matching >= 40% of that profile form the relevant
   set. We then measure Recall@K / Precision@K / NDCG@K over that set, which is
   what "did the model learn this user's taste" actually means. The model never
   sees the profile — only the plays.

Baselines: popularity (no personalisation), tag-match (content heuristic derived
from the history), random.

Run:  python -m recsys.evaluate      (from recommend/)
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict

import numpy as np
import torch

from . import config as C
from . import data as D
from .features import history_vector
from .model import ContentTwoTower

KS = (10, 20, 50)
PROFILE_KS = (10, 20)
RELEVANT_MATCH = 0.4          # 与生成时一致：匹配度 >= 0.4 视为「口味内」
RANDOM_TRIALS = 5             # 随机基线的蒙特卡洛次数


def _ndcg_at(rank: int, k: int) -> float:
    return 1.0 / math.log2(rank + 2) if 0 <= rank < k else 0.0


def _ndcg_binary(ranked: list[int], relevant: set[int], k: int) -> float:
    gains = [1.0 if sid in relevant else 0.0 for sid in ranked[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted([1.0] * min(len(relevant), k) + [0.0] * max(0, k - len(relevant)), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def _profile_match(song: dict, prof: tuple[set, set, set]) -> float:
    g, m, sc = prof
    total = len(g) + len(m) + len(sc)
    if not total:
        return 0.0
    return (len(song["genres"] & g) + len(song["moods"] & m) + len(song["scenes"] & sc)) / total


def _pseudo_profile(hist_songs: list[dict], top_n=(2, 2, 1)) -> tuple[set, set, set]:
    """Profile guessed from the history's tags — the tag-match baseline's input."""
    g = Counter(t for s in hist_songs for t in s["genres"]).most_common(top_n[0])
    m = Counter(t for s in hist_songs for t in s["moods"]).most_common(top_n[1])
    sc = Counter(t for s in hist_songs for t in s["scenes"]).most_common(top_n[2])
    return {t for t, _ in g}, {t for t, _ in m}, {t for t, _ in sc}


def _tag_score(song: dict, prof) -> float:
    g, m, sc = prof
    return float(len(song["genres"] & g) + len(song["moods"] & m) + len(song["scenes"] & sc))


def load_model() -> ContentTwoTower:
    ckpt = torch.load(C.MODEL_PATH, map_location="cpu", weights_only=False)
    m = ContentTwoTower(ckpt["in_dim"], ckpt["hidden"], ckpt["out_dim"], ckpt["dropout"])
    m.load_state_dict(ckpt["state_dict"])
    m.eval()
    return m


def evaluate() -> dict:
    songs, vecs = D.load_catalog()
    by_id = {s["id"]: s for s in songs}
    plays = D.load_plays(source="synthetic")
    if not plays:
        raise RuntimeError("没有 synthetic 播放记录，先跑 `python -m recsys.generate`")

    prof_path = C.ARTIFACT_DIR / "synthetic_profiles.json"
    if not prof_path.exists():
        raise RuntimeError(f"缺少真实偏好 {prof_path}，请重新运行 `python -m recsys.generate`")
    raw_prof = json.loads(prof_path.read_text(encoding="utf-8"))
    profiles = {int(u): (set(p["genres"]), set(p["moods"]), set(p["scenes"]))
                for u, p in raw_prof.items()}

    model = load_model()
    all_ids = [s["id"] for s in songs]
    song_mat = torch.from_numpy(np.stack([vecs[s] for s in all_ids]))
    with torch.no_grad():
        song_emb = model.song_emb(song_mat).numpy()

    by_user: dict[int, list[dict]] = defaultdict(list)
    for p in plays:
        by_user[p["user_id"]].append(p)

    pop = Counter()                      # popularity 只统计 histories，避免泄漏留出项
    for ps in by_user.values():
        for p in ps[:-1]:
            pop[p["song_id"]] += 1

    rng = random.Random(C.SEED)
    loo = defaultdict(list)              # method -> ranks
    prof_metrics = defaultdict(lambda: defaultdict(list))
    n_users = 0

    for uid, ps in by_user.items():
        if len(ps) < 3 or uid not in profiles:
            continue
        n_users += 1
        test = ps[-1]["song_id"]
        hist = [p["song_id"] for p in ps[:-1]]
        exclude = set(hist)
        cand = [s for s in all_ids if s not in exclude]
        truth = profiles[uid]
        relevant = {s for s in cand if _profile_match(by_id[s], truth) >= RELEVANT_MATCH}

        # ---------- model ----------
        hmean = history_vector(ps[:-1], vecs)
        with torch.no_grad():
            u = model.user_emb(torch.from_numpy(hmean).unsqueeze(0)).numpy()[0]
        scores = song_emb @ u
        order = np.argsort(-scores)
        model_ranked = [all_ids[i] for i in order if all_ids[i] not in exclude]

        # ---------- popularity ----------
        pop_ranked = sorted(cand, key=lambda s: (-pop.get(s, 0), s))

        # ---------- tag-match ----------
        pseudo = _pseudo_profile([by_id[s] for s in hist if s in by_id])
        tag_ranked = sorted(cand, key=lambda s: (-_tag_score(by_id[s], pseudo), s))

        rankings = {"model": model_ranked, "popularity": pop_ranked, "tag-match": tag_ranked}
        for name, ranked in rankings.items():
            rank = ranked.index(test) if test in ranked else len(all_ids)
            loo[name].append(rank)
            if relevant:
                topk = set(ranked[:10])
                prof_metrics[name]["recall@10"].append(len(topk & relevant) / len(relevant))
                prof_metrics[name]["precision@10"].append(len(topk & relevant) / 10)
            for k in PROFILE_KS:
                prof_metrics[name][f"ndcg@{k}"].append(_ndcg_binary(ranked, relevant, k))

        # ---------- random ----------
        loo["random"].append(rng.randrange(len(cand)))
        if relevant:
            for _ in range(RANDOM_TRIALS):
                r = rng.sample(cand, len(cand))
                topk = set(r[:10])
                prof_metrics["random"]["recall@10"].append(len(topk & relevant) / len(relevant))
                prof_metrics["random"]["precision@10"].append(len(topk & relevant) / 10)
                for k in PROFILE_KS:
                    prof_metrics["random"][f"ndcg@{k}"].append(_ndcg_binary(r, relevant, k))

    result = {"users": n_users, "catalog": len(all_ids),
              "relevant_avg": round(sum(len([s for s in all_ids if _profile_match(by_id[s], profiles[u]) >= RELEVANT_MATCH])
                                        for u in profiles if u in by_user) / max(n_users, 1), 1),
              "leave_one_out": {}, "profile_level": {}}

    for name, ranks in loo.items():
        result["leave_one_out"][name] = {
            **{f"hit@{k}": round(sum(1 for r in ranks if r < k) / len(ranks), 4) for k in KS},
            **{f"ndcg@{k}": round(sum(_ndcg_at(r, k) for r in ranks) / len(ranks), 4) for k in KS},
            "mrr": round(sum(1.0 / (r + 1) for r in ranks) / len(ranks), 4),
        }

    for name, d in prof_metrics.items():
        result["profile_level"][name] = {k: round(sum(v) / len(v), 4) for k, v in d.items() if v}

    out = C.ARTIFACT_DIR / "eval_v2.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    r = evaluate()
    print(f"评测：{r['users']} 个用户，候选 {r['catalog']} 首，"
          f"平均每个用户 {r['relevant_avg']} 首「口味内」\n")

    cols = ["hit@10", "hit@20", "hit@50", "ndcg@10", "mrr"]
    print("【留一法】命中用户最后一次播放（严格）")
    print(f"{'方法':<12}" + "".join(f"{c:>10}" for c in cols))
    for name in ("model", "tag-match", "popularity", "random"):
        m = r["leave_one_out"][name]
        print(f"{name:<12}" + "".join(f"{m[c]:>10.4f}" for c in cols))

    cols2 = ["recall@10", "precision@10", "ndcg@10", "ndcg@20"]
    print("\n【口味级】命中用户真实偏好集合（更贴近实际体验）")
    print(f"{'方法':<12}" + "".join(f"{c:>12}" for c in cols2))
    for name in ("model", "tag-match", "popularity", "random"):
        m = r["profile_level"][name]
        print(f"{name:<12}" + "".join(f"{m.get(c, 0):>12.4f}" for c in cols2))

    print(f"\n结果 -> {C.ARTIFACT_DIR / 'eval_v2.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
