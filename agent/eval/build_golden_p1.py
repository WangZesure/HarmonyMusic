"""P1-2: expand the golden sets (phase3loop P1-2).

APPENDS to golden_dev.jsonl / golden_test.jsonl:
  * absent  >= 12 (dev) — same-name-diff-artist, artist-not-in-corpus, title-not-in-corpus
  * constraint — SQL gold, scored by constraint-satisfaction@5
  * mood — DeepSeek-graded 0/1/2 over semantic top-10, scored by nDCG@5
  * describe_fuzzy — describe queries NOT copied from the card summary (reports separately)

    python -m agent.eval.build_golden_p1 [--dev-absent 12]
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import agent.db as db
import agent.registry as registry
import agent.tools  # noqa: F401
from agent.core.llm import LLM
from agent.eval.build_golden import card_text, gen_constraints, load_cards

EVAL_DIR = Path(__file__).parent

ARTISTS = ["周杰伦", "Taylor Swift", "Ed Sheeran", "Adele", "Coldplay",
           "陈奕迅", "邓紫棋", "五月天", "李荣浩", "Bruno Mars"]
FAMOUS_SONGS = ["晴天", "Bohemian Rhapsody", "Shape of You", "富士山下", "Hello",
                "Yesterday", "海阔天空", "Someone Like You", "Counting Stars", "起风了"]


def add_absent(entries, n, seed):
    rng = random.Random(seed)
    cards = load_cards()
    vocab = {}
    kinds = ["same_name_diff_artist", "artist_not_in_corpus", "title_not_in_corpus"]
    for i in range(n):
        kind = kinds[i % len(kinds)]
        if kind == "same_name_diff_artist":
            c = rng.choice(cards)
            q = f"{rng.choice(ARTISTS)} 的 {c['name']}"
        elif kind == "artist_not_in_corpus":
            q = rng.choice(ARTISTS) + rng.choice(["的歌", "的歌单", "随便放点"])
        else:
            q = rng.choice(FAMOUS_SONGS)
        entries.append({"id": f"x{i:03d}", "type": "absent", "query": q,
                        "gold_ids": [], "graded": None, "constraints": None,
                        "split": entries[0]["split"] if entries else "dev", "kind": kind})
    return entries


def add_constraint(entries, n, seed, vocab):
    """Only keep constraint combos whose satisfying set has >= 5 songs, so a correct
    agent needs no relaxation and satisfaction@5 can reach 1.0."""
    rng = random.Random(seed)
    from agent.models import Filter
    made, tries = 0, 0
    while made < n and tries < n * 40:
        tries += 1
        f, qtxt = gen_constraints(rng, vocab)
        gold = db.filter_songs(Filter(**f), 200, [])
        if len(gold) < 5:
            continue
        entries.append({"id": f"c{made:03d}", "type": "constraint", "query": qtxt,
                        "gold_ids": gold, "graded": None, "constraints": f,
                        "split": entries[0]["split"] if entries else "dev"})
        made += 1
    return entries


def add_mood(entries, n, llm, seed):
    from agent.eval.build_golden import MOOD_QUERIES
    rng = random.Random(seed)
    cards_by_id = {c["song_id"]: c for c in load_cards()}
    qs = rng.sample(MOOD_QUERIES, min(n, len(MOOD_QUERIES)))
    for i, q in enumerate(qs):
        res = registry.run("semantic_search", {"query": q, "limit": 10})
        ids = [it["song_id"] for it in (res.data or {}).get("items", [])]
        graded = {}
        if ids:
            payload = "\n".join(f"{sid}: {card_text(cards_by_id[sid])}" for sid in ids)
            out = llm.complete_json([
                {"role": "system", "content": "你在构建评测集，只输出 JSON。"},
                {"role": "user", "content": f"用户查询：{q}\n\n候选：\n{payload}\n\n"
                 '给每首打相关度 0(无关)/1(沾边)/2(贴切)。输出 {"graded": {"<song_id>": 0|1|2}}'}])
            graded = {int(k): int(v) for k, v in (out.get("graded") or {}).items() if int(v) > 0}
        entries.append({"id": f"m{i:03d}", "type": "mood", "query": q,
                        "gold_ids": list(graded), "graded": graded, "constraints": None,
                        "split": entries[0]["split"] if entries else "dev"})
    return entries


def add_describe_fuzzy(entries, n, llm, seed):
    rng = random.Random(seed)
    cards = [c for c in load_cards() if c["searchable"]]
    for i in range(n):
        c = rng.choice(cards)
        q = llm.complete_json([
            {"role": "system", "content": "你在构建音乐搜索评测集，只输出 JSON。"},
            {"role": "user", "content":
             f"标签：{','.join(c['tags'])}；主题关键词：{c['summary']}\n\n"
             "写一句**模糊**的中文搜索请求：像真实用户那样凭印象描述，"
             "**禁止照抄上面的摘要句子**，只能用标签和主题词，不要出现歌名/歌手。"
             '输出 {"query": "..."}'}])["query"]
        entries.append({"id": f"f{i:03d}", "type": "describe_fuzzy", "query": q,
                        "gold_ids": [c["song_id"]], "graded": None, "constraints": None,
                        "split": entries[0]["split"] if entries else "dev"})
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-absent", type=int, default=12)
    ap.add_argument("--test-absent", type=int, default=10)
    ap.add_argument("--constraint", type=int, default=10)
    ap.add_argument("--mood", type=int, default=10)
    ap.add_argument("--describe-fuzzy", type=int, default=10)
    a = ap.parse_args()

    import yaml
    import agent.config as cfg
    vocab = yaml.safe_load(cfg.VOCAB_FILE.read_text(encoding="utf-8"))
    llm = LLM()
    try:
        import agent.index as index
        index._encoder()
    except Exception:  # noqa: BLE001
        pass

    for split, n_absent, seed in (("dev", a.dev_absent, 101), ("test", a.test_absent, 202)):
        path = EVAL_DIR / f"golden_{split}.jsonl"
        entries = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        base = [e for e in entries]                        # keep existing frozen entries
        new: list[dict] = []
        add_absent(new, n_absent, seed)
        add_constraint(new, a.constraint if split == "dev" else 0, seed + 1, vocab)
        add_mood(new, a.mood if split == "dev" else 0, llm, seed + 2)
        add_describe_fuzzy(new, a.describe_fuzzy if split == "dev" else 0, llm, seed + 3)
        # renumber new ids to <split>-xNNN and clean helper key
        for i, e in enumerate(new):
            e["id"] = f"{split}-{e['id']}"
            e.pop("kind", None)
        out = base + new
        path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n",
                        encoding="utf-8")
        from collections import Counter
        print(f"{split}: {len(base)} -> {len(out)}  {dict(Counter(e['type'] for e in out))}")
    print("LLM stats:", llm.stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
