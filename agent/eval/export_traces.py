"""Export 10 example traces (5 success / 5 boundary), phase3loop P2.

    python -m agent.eval.export_traces

Writes agent/eval/traces.json and DocsForMusicAgent/phase3_traces.md.
"""
from __future__ import annotations

import json
from pathlib import Path

import agent.db as db
from agent.core.pipeline import search

ROOT = Path(__file__).resolve().parents[2]
EVAL = Path(__file__).parent

SUCCESS = [
    "Move a Little Closer",                       # exact title
    "有句歌词大概是说 move a little closer",         # lyric (near-quote)
    "有时候没有你我简直无法呼吸",                     # Chinese paraphrase of English lyrics
    "求一首深夜伤感的德语民谣",                       # describe + hard filters
    "推荐点适合跑步的燃曲",                           # vibe
]
BOUNDARY = [
    "Coldplay 的 Yellow",                          # named + wrong artist -> refuse
    "求一首叫 Night Str 的歌，Sergey Gulevich 唱的",  # typo title -> fuzzy hit
    "来首周杰伦的晴天",                             # title not in corpus -> refuse
    "Someone Like You",                            # title not in corpus -> refuse
    "纯音乐、史诗、睡前的歌",                        # hard-constraint combo
]


def one(query: str) -> dict:
    r = search(query)
    names = {s["song_id"]: s["name"] for s in r["songs"]}
    for s in r["songs"]:
        s["name"] = names.get(s["song_id"], s.get("name"))
    return {"query": query, "message": r["message"], "relaxed": r["relaxed"],
            "songs": [{"song_id": s["song_id"], "name": s["name"], "artist": s["artist"],
                       "moods": s["moods"], "scenes": s["scenes"], "reason": s.get("reason", ""),
                       "match": s.get("match")} for s in r["songs"]],
            "trace": r["trace"]}


def main() -> int:
    out = {"success": [one(q) for q in SUCCESS], "boundary": [one(q) for q in BOUNDARY]}
    (EVAL / "traces.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 阶段 3 典型 trace（成功 5 / 边界 5）", ""]
    for title, key in (("成功样例", "success"), ("边界样例", "boundary")):
        md.append(f"## {title}")
        for t in out[key]:
            md.append(f"\n### `{t['query']}`")
            md.append(f"- 回复：{t['message']}" + (f"（放宽：{t['relaxed']}）" if t['relaxed'] else ""))
            for s in t["songs"][:5]:
                m = s.get("match") or {}
                lyn = f" ⟵ 「{m.get('line_text')}」@{m.get('start_ms')}ms" if m.get("line_text") else ""
                md.append(f"- `{s['song_id']}` {s['name']} — {s['artist']} "
                          f"[{'/'.join(s['moods']+s['scenes'])}]{lyn}")
            steps = " → ".join(f"{st['step']}" + (f"({st.get('n')})" if st.get('n') is not None else "")
                               for st in t["trace"])
            md.append(f"- 流水线：{steps}")
    (ROOT / "DocsForMusicAgent" / "phase3_traces.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("wrote agent/eval/traces.json + DocsForMusicAgent/phase3_traces.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
