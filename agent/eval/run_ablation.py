"""Run all ablations in ONE process (phase3loop P2) — models load once.

    python -m agent.eval.run_ablation [--file golden_dev.jsonl]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import agent.core.ablate as ablate
import agent.index as index
from agent.core import rank as rank_mod
from agent.core.pipeline import rank_ids
from agent.eval.run_eval import run_method

EVAL = Path(__file__).parent

CONFIGS = [
    ("baseline", []),
    ("no_intent (原句直连 B2)", ["no_intent"]),
    ("no_lang_norm", ["no_lang_norm"]),
    ("lyric_legacy (RRF+卡片重排)", ["lyric_legacy"]),
    ("no_artist", ["no_artist"]),
    ("no_fuzzy", ["no_fuzzy"]),
]


class AgentRank:
    def __init__(self):
        self.last_gray = False

    def search(self, query: str, k: int = 5) -> list[int]:
        flag: dict = {}
        ids = rank_ids(query, flag=flag)[:k]
        self.last_gray = bool(flag.get("gray"))
        return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="golden_dev.jsonl")
    a = ap.parse_args()
    entries = [json.loads(l) for l in (EVAL / a.file).read_text(encoding="utf-8").splitlines() if l.strip()]

    try:
        index._encoder()
        rank_mod._reranker()
    except Exception:  # noqa: BLE001
        pass

    results = {}
    for name, cfg in CONFIGS:
        ablate.set_active(cfg)
        agg = run_method(AgentRank(), entries, 5)
        results[name] = agg
        print(f"== {name} ==")
        for t in ("title", "lyric", "describe", "describe_fuzzy", "absent", "constraint", "mood"):
            if t in agg:
                print(f"   {t:14} {agg[t]}")
    (EVAL / "ablation.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote agent/eval/ablation.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
