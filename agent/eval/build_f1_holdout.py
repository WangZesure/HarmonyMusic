"""F1 holdout set (phase3loop §3.4) — run ONCE after dev tuning, never used to tune.

40 cases:
  * 20 in-corpus lyric  — searchable songs used in NEITHER dev nor test
  * 10 famous out-of-corpus lyric — songs disjoint from dev's
  * 10 obscure out-of-corpus lyric — different Jamendo candidate lines than dev

    python -m agent.eval.build_f1_holdout
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import agent.config as cfg
from agent.core.llm import LLM
from agent.eval.build_golden import gen_lyric, load_cards

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path(__file__).parent

FAMOUS = [  # disjoint from dev's famous set
    ("Sweet Child O' Mine", "Guns N' Roses"), ("Rolling in the Deep", "Adele"),
    ("Blowin' in the Wind", "Bob Dylan"), ("Like a Rolling Stone", "Bob Dylan"),
    ("Smells Like Teen Spirit", "Nirvana"), ("Wonderwall", "Oasis"),
    ("Take Me Home, Country Roads", "John Denver"), ("月亮代表我的心", "邓丽君"),
    ("光年之外", "邓紫棋"), ("平凡之路", "朴树"),
]


def main() -> int:
    used: set[int] = set()
    for f in ("golden_dev.jsonl", "golden_test.jsonl"):
        for l in (EVAL / f).read_text(encoding="utf-8").splitlines():
            if l.strip():
                used |= set(json.loads(l).get("gold_ids") or [])
    cards = [c for c in load_cards() if c["searchable"] and c["lyric_lines"] and c["song_id"] not in used]
    random.Random(7).shuffle(cards)
    llm = LLM()
    out = []
    for i, c in enumerate(cards[:20]):
        g = gen_lyric(llm, c)
        if g:
            out.append({"id": f"ho-ly{i:02d}", "type": "lyric", "query": g["query"],
                        "gold_ids": [c["song_id"]], "graded": None, "constraints": None, "split": "holdout"})
    for i, (title, artist) in enumerate(FAMOUS):
        out.append({"id": f"ho-fm{i:02d}", "type": "absent", "kind": "lyric_famous",
                    "query": f"有句歌词是 {title}", "gold_ids": [], "graded": None,
                    "constraints": None, "split": "holdout"})
    cands = json.loads((ROOT / "data_pipeline" / "data" / "candidates.json").read_text(encoding="utf-8"))
    sel = {str(t["id"]) for t in json.loads((ROOT / "data_pipeline" / "data" / "selected.json").read_text(encoding="utf-8"))}
    lines = []
    for c in cands:
        if str(c["id"]) in sel:
            continue
        for ln in (c.get("jamendo_lyrics") or "").splitlines():
            if len(ln.strip()) >= 18 and sum(ch.isalpha() for ch in ln) >= 12:
                lines.append(ln.strip())
    dev_lines = {json.loads(l)["query"] for l in (EVAL / "golden_dev.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    lines = [l for l in dict.fromkeys(lines) if not any(l in q for q in dev_lines)]
    random.Random(13).shuffle(lines)
    for i, ln in enumerate(lines[:10]):
        out.append({"id": f"ho-ob{i:02d}", "type": "absent", "kind": "lyric_obscure",
                    "query": f"有一句歌词 {ln}", "gold_ids": [], "graded": None,
                    "constraints": None, "split": "holdout"})

    p = EVAL / "golden_holdout_f1.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n", encoding="utf-8")
    from collections import Counter
    print(f"wrote {p} ({len(out)})", dict(Counter(e.get("kind", e["type"]) for e in out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
