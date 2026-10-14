"""F1: build the "obscure out-of-corpus lyric" dev subclass (phase3loop §3.3).

These are lyrics that exist but the model does NOT know (so 认出处 can't help and the
confidence threshold must handle them). 20 from Jamendo candidates not selected into
the 500 (10 English original + 10 Chinese paraphrase) + 10 model-invented lyric-like
sentences. Appended to golden_dev.jsonl as type=absent, kind=lyric_obscure.

    python -m agent.eval.build_f1_obscure
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import agent.config as cfg
from agent.core.llm import LLM

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path(__file__).parent


def _good(line: str) -> bool:
    t = (line or "").strip()
    return len(t) >= 18 and sum(c.isalpha() for c in t) >= 12 and not any(
        k in t.lower() for k in ("[", "http", "www", "lyrics", "©"))


def main() -> int:
    cands = json.loads((ROOT / "data_pipeline" / "data" / "candidates.json").read_text(encoding="utf-8"))
    sel = {str(t["id"]) for t in json.loads((ROOT / "data_pipeline" / "data" / "selected.json").read_text(encoding="utf-8"))}
    pool = []
    for c in cands:
        if str(c["id"]) in sel:
            continue
        for ln in (c.get("jamendo_lyrics") or "").splitlines():
            if _good(ln):
                pool.append(ln.strip())
    pool = list(dict.fromkeys(pool))
    random.Random(11).shuffle(pool)
    en = pool[:10]
    zh_src = pool[10:20]

    llm = LLM()
    try:
        made = llm.complete_json([
            {"role": "system", "content": "只输出 JSON。"},
            {"role": "user", "content": "写 10 句**听起来像流行歌词、但并不真实存在**的中文句子"
             "（不要用任何真实歌曲的歌词，不要出现歌名/歌手）。输出 {\"lines\": [\"...\"]}"}]).get("lines", [])[:10]
    except Exception as e:  # noqa: BLE001
        print("made-up lines failed:", str(e)[:80]); made = []

    entries = []
    for q in en:
        entries.append({"type": "absent", "query": f"有一句歌词 {q}", "kind": "lyric_obscure"})
    for src in zh_src:
        try:
            z = llm.complete_json([
                {"role": "system", "content": "只输出 JSON。"},
                {"role": "user", "content": f"把这句英文歌词用中文转述（别照抄、别出现歌名）：{src}\n"
                 '输出 {"query": "..."}'}]).get("query", "").strip()
        except Exception as e:  # noqa: BLE001
            print("paraphrase failed:", str(e)[:60]); z = ""
        if z:
            entries.append({"type": "absent", "query": z, "kind": "lyric_obscure"})
    for line in made:
        entries.append({"type": "absent", "query": f"歌词 {line}", "kind": "lyric_obscure"})

    p = EVAL / "golden_dev.jsonl"
    E = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    E = [e for e in E if e.get("kind") != "lyric_obscure"]        # idempotent
    base = len(E)
    for i, e in enumerate(entries):
        E.append({"id": f"dev-ob{i:02d}", "type": "absent", "query": e["query"], "gold_ids": [],
                  "graded": None, "constraints": None, "split": "dev", "kind": "lyric_obscure"})
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in E) + "\n", encoding="utf-8")
    print(f"added {len(entries)} obscure lyric cases ({base} -> {len(E)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
