"""Build the 30-case multi-turn golden set (target state; property-based).

    python -m agent.eval.build_agent_golden

Each case is checked by properties, not fixed gold ids:
  more      -> turn 2 keeps the first-turn filters, excludes seen ids, no repeats
  refine    -> final ids satisfy the case's final filters, no repeat of turn 1
  negative  -> final ids carry none of the dropped tags
  select    -> second turn is a text answer detailing last_shown[1]
  clarify   -> clarify/refusal, and pending_question set when clarify
  absent    -> refusal/clarify for a title/artist not in the corpus

Each case carries its own `first_filters` (derived from its query) so a model
that ignores the query cannot pass by returning any songs at all.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("golden_multiturn.jsonl")

# (query, expected hard filters for turn 0)
FIRST = [
    ("深夜伤感的歌", {"moods": ["伤感"], "scenes": ["深夜"]}),
    ("适合跑步的开心歌", {"moods": ["开心"], "scenes": ["跑步"]}),
    ("安静的民谣", {"moods": ["平静"], "genres": ["民谣"]}),
    ("浪漫的爵士", {"moods": ["浪漫"], "genres": ["爵士"]}),
    ("雨天听的歌", {"scenes": ["雨天"]}),
]
CLARIFY = [
    "有没有俄语的儿童歌曲",
    "有没有冰岛语的朋克",
    "有没有阿拉伯语的古典",
    "有没有泰语的摇滚",
    "有没有荷兰语的乡村",
]
ABSENT = [
    ("Bohemian Rhapsody", "title"),
    ("周杰伦", "artist"),
    ("晴天", "title"),
    ("Taylor Swift", "artist"),
    ("Adele", "artist"),
]


def _minus(f: dict, tag: str) -> dict:
    """Drop one tag from a filter dict; keep the rest of the turn-0 constraints."""
    out = {}
    for k, v in f.items():
        if k in ("genres", "moods", "scenes"):
            v = [x for x in v if x != tag]
        if v:
            out[k] = v
    return out


def _refined(f: dict) -> dict:
    """'更X一点' replaces the mood but keeps non-conflicting genre/scene constraints."""
    out = {k: v for k, v in f.items() if k in ("genres", "scenes")}
    out["moods"] = ["开心"]
    return out


def build() -> list[dict]:
    cases: list[dict] = []
    for i, (q, f) in enumerate(FIRST, 1):
        cases.append({
            "id": f"more-{i:02d}", "user_id": 1,
            "check": {"kind": "more", "first_filters": f},
            "turns": [{"query": q, "expect": {"type": "songs"}},
                      {"query": "换一首", "expect": {"type": "songs"}}],
        })
    for i, (q, f) in enumerate(FIRST, 1):
        cases.append({
            "id": f"refine-{i:02d}", "user_id": 1,
            "check": {"kind": "refine", "first_filters": f,
                      "final_filters": _refined(f)},
            "turns": [{"query": q, "expect": {"type": "songs"}},
                      {"query": "更欢快一点", "expect": {"type": "songs"}}],
        })
    for i, (q, f) in enumerate(FIRST, 1):
        cases.append({
            "id": f"negative-{i:02d}", "user_id": 1,
            "check": {"kind": "negative", "first_filters": f,
                      "final_filters": _minus(f, "伤感"), "drop_tags": ["伤感"]},
            "turns": [{"query": q, "expect": {"type": "songs"}},
                      {"query": "不要伤感", "expect": {"type": "songs"}}],
        })
    for i, (q, f) in enumerate(FIRST, 1):
        cases.append({
            "id": f"select-{i:02d}", "user_id": 1,
            "check": {"kind": "select", "first_filters": f, "detail_index": 1},
            "turns": [{"query": q, "expect": {"type": "songs"}},
                      {"query": "第二首讲什么", "expect": {"type": "text"}}],
        })
    for i, q in enumerate(CLARIFY, 1):
        if i == 5:
            # exercise pending_question -> answer on the next turn
            cases.append({
                "id": f"clarify-{i:02d}", "user_id": 1,
                "check": {"kind": "clarify_answer",
                          "final_filters": {"languages": ["ru"], "genres": ["民谣"]},
                          "pending_languages": ["ru"]},
                "turns": [
                    {"query": q, "expect": {"type": "clarify"}},
                    {"query": "那就听俄语民谣吧", "expect": {"type": "songs"}},
                ],
            })
        else:
            cases.append({
                "id": f"clarify-{i:02d}", "user_id": 1,
                "check": {"kind": "clarify", "clarify_slot": "filters.languages"},
                "turns": [{"query": q, "expect": {"type": "clarify"}}],
            })
    for i, (q, mode) in enumerate(ABSENT, 1):
        query = f"有没有《{q}》这首歌" if mode == "title" else f"有没有 {q} 的歌"
        cases.append({
            "id": f"absent-{i:02d}", "user_id": 1, "check": {"kind": "absent"},
            "turns": [{"query": query, "expect": {"type": ["refusal", "clarify"]}}],
        })
    return cases


def main() -> int:
    cases = build()
    OUT.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n",
                   encoding="utf-8")
    print(f"wrote {len(cases)} cases -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
