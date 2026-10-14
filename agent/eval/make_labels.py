"""Helper to build eval/semantic_labels.jsonl (needs human judgement).

Prints, for each seed Chinese query, the top-10 semantic_search hits with their
tags, so you can pick the genuinely relevant song_ids and paste them into
eval/semantic_labels.jsonl as {"query": ..., "relevant_song_ids": [...]}.

    python -m agent.eval.make_labels [--limit 10]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import agent.index as index
import agent.registry as registry
import agent.tools  # noqa: F401

LABELS_FILE = Path(__file__).parent / "semantic_labels.jsonl"

# 30 seed queries spanning mood / scene / language / fuzzy description.
SEED_QUERIES = [
    "适合深夜一个人听的伤感歌", "跑步时听的很有劲的歌", "学习时听的纯音乐",
    "咖啡厅里轻松的爵士", "失恋后想哭的歌", "开车兜风的摇滚",
    "婚礼上播放的浪漫歌曲", "下雨天适合听的平静音乐", "很燃的运动健身音乐",
    "睡前放松助眠的音乐", "怀旧的老歌", "充满希望和正能量的歌",
    "适合派对热闹的电子舞曲", "民谣风格的温暖歌曲", "史诗感的电影配乐",
    "慵懒的午后氛围音乐", "适合旅行的轻快歌曲", "神秘氛围的电子乐",
    "愤怒宣泄的硬核摇滚", "浪漫的情歌", "适合通勤的欢快流行",
    "冥想用的安静音乐", "有节奏感的说唱", "适合散步的柔和旋律",
    "带点忧郁的独立音乐", "欢快的夏日歌曲", "适合专注工作的器乐",
    "感人至深的抒情歌", "带点神秘感的氛围电子", "适合清晨醒来的轻音乐",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()
    try:
        index._encoder()
    except Exception:  # noqa: BLE001
        pass
    for q in SEED_QUERIES:
        res = registry.run("semantic_search", {"query": q, "limit": a.limit})
        print(f"\n=== {q}")
        for i in (res.data or {}).get("items", []):
            tags = "/".join(i["moods"] + i["scenes"] + i["genres"][:1])
            print(f"  {i['song_id']:>8}  {i['name'][:40]:40}  {tags}")
    print(f"\nWrite the relevant ones to {LABELS_FILE} as "
          "{\"query\": \"...\", \"relevant_song_ids\": [...]} per line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
