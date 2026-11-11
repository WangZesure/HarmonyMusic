"""Train the content two-tower and persist a checkpoint.

Run:  python -m recsys.train            (from recommend/)
"""
from __future__ import annotations

import json
import random
from datetime import datetime

import numpy as np
import torch
import torch.nn as nn

from . import config as C
from . import data as D
from .features import history_vector
from .model import ContentTwoTower


def build_examples(plays: list[dict], vecs: dict[int, np.ndarray], seed: int = 0):
    """Turn play records into (history_mean, song_vec, label) triples.

    label 1  : the user actually listened (completion >= 60%)
    label 0  : the user skipped it (completion <= 30%)
    label 0  : NEG_PER_POS random songs the user never played, per positive
    """
    rng = random.Random(seed)
    by_user: dict[int, list[dict]] = {}
    for p in plays:
        by_user.setdefault(p["user_id"], []).append(p)

    all_ids = list(vecs.keys())
    played = {u: {p["song_id"] for p in ps} for u, ps in by_user.items()}
    unplayed = {u: [s for s in all_ids if s not in played[u]] for u in by_user}

    H, S, Y = [], [], []

    def add(h, s, y):
        H.append(h)
        S.append(vecs[s])
        Y.append(float(y))

    for u, ps in by_user.items():
        if len(ps) < 3:
            continue
        for p in ps:
            comp = (p["play_duration"] / p["full_duration"]) if p["full_duration"] else 0.0
            others = [q for q in ps if q["song_id"] != p["song_id"]]
            if not others:
                continue
            hmean = history_vector(others, vecs)

            if comp >= 0.6:
                add(hmean, p["song_id"], 1)
                pool = unplayed[u]
                if pool:
                    for sid in rng.sample(pool, min(C.NEG_PER_POS, len(pool))):
                        add(hmean, sid, 0)
            elif comp <= 0.3:
                add(hmean, p["song_id"], 0)

    if not H:
        raise RuntimeError("没有可用的训练样本：请先运行 recsys.generate")
    return (np.stack(H), np.stack(S), np.array(Y, dtype="float32"))


def train(epochs: int | None = None, seed: int | None = None) -> dict:
    epochs = epochs or C.EPOCHS
    seed = C.SEED if seed is None else seed
    torch.manual_seed(seed)
    np.random.seed(seed)

    songs, vecs = D.load_catalog()
    plays = D.load_plays(source="synthetic")
    if not plays:
        raise RuntimeError("user_plays 里没有 synthetic 数据，先跑 `python -m recsys.generate`")

    H, S, Y = build_examples(plays, vecs, seed=seed)
    print(f"训练样本 {len(Y)} 条（正样本 {int(Y.sum())}，负样本 {int((Y == 0).sum())}）")

    dev = torch.device("cpu")
    Ht = torch.from_numpy(H).to(dev)
    St = torch.from_numpy(S).to(dev)
    Yt = torch.from_numpy(Y).to(dev)

    model = ContentTwoTower(C.IN_DIM, C.HIDDEN, C.OUT_DIM, C.DROPOUT).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=C.LR)
    loss_fn = nn.BCEWithLogitsLoss()

    n = len(Y)
    history = []
    for ep in range(1, epochs + 1):
        model.train()
        perm = torch.randperm(n, device=dev)
        total, seen = 0.0, 0
        for i in range(0, n, C.BATCH):
            idx = perm[i:i + C.BATCH]
            opt.zero_grad()
            logits = model(Ht[idx], St[idx])
            loss = loss_fn(logits, Yt[idx])
            loss.backward()
            opt.step()
            total += float(loss.item()) * len(idx)
            seen += len(idx)
        avg = total / max(seen, 1)
        history.append({"epoch": ep, "loss": round(avg, 5)})
        if ep == 1 or ep % 5 == 0 or ep == epochs:
            print(f"  epoch {ep:2d}/{epochs}  loss={avg:.4f}")

    torch.save({
        "state_dict": model.state_dict(),
        "in_dim": C.IN_DIM, "hidden": C.HIDDEN, "out_dim": C.OUT_DIM,
        "dropout": C.DROPOUT,
    }, C.MODEL_PATH)

    meta = {
        "version": "v2-content-two-tower",
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "songs": len(songs),
        "users": len({p["user_id"] for p in plays}),
        "plays": len(plays),
        "train_examples": int(n),
        "positives": int(Y.sum()),
        "epochs": epochs,
        "seed": seed,
        "loss_curve": history,
        "hyper": {"hidden": C.HIDDEN, "out_dim": C.OUT_DIM, "dropout": C.DROPOUT,
                  "lr": C.LR, "batch": C.BATCH, "neg_per_pos": C.NEG_PER_POS},
    }
    C.META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"模型 -> {C.MODEL_PATH}\n元信息 -> {C.META_PATH}")
    return meta


if __name__ == "__main__":
    train()
