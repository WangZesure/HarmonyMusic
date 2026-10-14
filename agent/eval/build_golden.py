"""Build the phase-3 golden eval set (phase3 §1).

For each of 6 query types, generates entries with gold answers, split dev/test:
  title(15%) lyric(25%) describe(20%) mood(20%) constraint(15%) absent(5%)

    python -m agent.eval.build_golden --n 100 --out dev

Target songs are sampled from the corpus; a DeepSeek call writes a realistic user
query from the song's card (never copying the title). Constraint gold comes from
SQL; mood relevance is graded 0/1/2 by DeepSeek over semantic top-20.
Writes agent/eval/golden_<out>.jsonl and intent_labels.jsonl (50 annotated intents).
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import yaml

import agent.config as cfg
import agent.db as db
from agent.core.llm import LLM

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data_pipeline" / "data"
EVAL_DIR = Path(__file__).parent

ABSENT = [  # famous songs that are certainly NOT in the Jamendo corpus
    "来首周杰伦的晴天", "放一下 Taylor Swift 的 Love Story", "陈奕迅 富士山下",
    "播放 Ed Sheeran 的 Shape of You", "五月天的倔强", "邓紫棋 泡沫",
    "Adele 的 Hello", "李荣浩 年少有为", "Coldplay 的 Yellow", "薛之谦 演员",
]

MOOD_QUERIES = [
    "下雨天看书时听的安静音乐", "适合深夜一个人听的伤感歌", "跑步时听的很有劲的歌",
    "适合开长途车的歌", "失恋后想哭的歌", "周末午后咖啡馆的慵懒爵士",
    "清晨醒来放点轻松的", "专注工作时听的纯音乐", "派对热闹的电子舞曲",
    "怀旧的老歌", "睡前放松助眠的音乐", "适合婚礼的浪漫歌曲",
]


def load_cards() -> list[dict]:
    sel = {str(t["id"]): t for t in json.loads((DATA / "selected.json").read_text(encoding="utf-8"))}
    tags = json.loads((DATA / "tags.json").read_text(encoding="utf-8"))
    lf = json.loads((DATA / "lyrics_final.json").read_text(encoding="utf-8"))
    lines = json.loads((DATA / "lyric_lines.json").read_text(encoding="utf-8"))
    by_song: dict[str, list] = {}
    for ln in lines:
        by_song.setdefault(str(ln["song_id"]), []).append(ln)
    cards = []
    for sid, t in sel.items():
        tt = tags.get(sid, {})
        lyric_lines = [r["text"] for r in sorted(by_song.get(sid, []), key=lambda r: r["line_no"])]
        cards.append({
            "song_id": int(sid), "name": t["name"], "artist": t["artist_name"],
            "tags": (tt.get("genres", []) + tt.get("moods", []) + tt.get("scenes", [])),
            "summary": tt.get("summary", ""), "vocal": t.get("kind"), "lang": t.get("lang"),
            "duration_s": t.get("duration"), "searchable": lf.get(sid, {}).get("lyric_searchable"),
            "lyric_lines": lyric_lines, "genres": tt.get("genres", []),
            "moods": tt.get("moods", []), "scenes": tt.get("scenes", []),
        })
    return cards


def card_text(c: dict) -> str:
    return (f"歌名: {c['name']} / 歌手: {c['artist']} / 标签: {','.join(c['tags'])} / "
            f"摘要: {c['summary']} / 前几行歌词: {' '.join(c['lyric_lines'][:4])}")


def gen_title(llm: LLM, c: dict, rng: random.Random) -> str:
    typo = rng.random() < 0.4
    return llm.complete_json([
        {"role": "system", "content": "你在构建音乐搜索评测集。只输出 JSON。"},
        {"role": "user", "content": f"歌曲信息：\n{card_text(c)}\n\n"
         f"写一句中文用户搜索请求，指名这首歌（用歌名{'，故意含一个小拼写错误' if typo else '或歌手'}）。"
         '要求自然、口语化。输出 {"query": "..."}'}],
    )["query"]


_CREDIT = re.compile(r"歌词|作曲|作词|编曲|创作|制作|录音|混音|演唱|www\.|http|©|℗|"
                     r"lyrics|music by|arranged|performed|produced|composed|remix", re.I)


def gen_lyric(llm: LLM, c: dict) -> dict | None:
    cand = [l for l in c["lyric_lines"][:12] if len(l) >= 12 and not _CREDIT.search(l)]
    if not cand:
        return None  # no usable lyric line (only credits) -> skip this song
    line = max(cand, key=len)
    mode = random.choice(["quote", "misremember", "zh_paraphrase"])
    instr = {
        "quote": "直接引用这句歌词（可略有出入）",
        "misremember": "凭记忆复述这句歌词，故意记错几个词",
        "zh_paraphrase": "把这句英文歌词的意思用中文转述，别提歌名",
    }[mode]
    q = llm.complete_json([
        {"role": "system", "content": "你在构建音乐搜索评测集。只输出 JSON。"},
        {"role": "user", "content": f"歌曲的一句歌词：{line}\n\n"
         f"写一句中文用户搜索请求，{instr}。输出 {{\"query\": \"...\"}}"}])["query"]
    return {"query": q, "mode": mode}


def gen_describe(llm: LLM, c: dict) -> str:
    return llm.complete_json([
        {"role": "system", "content": "你在构建音乐搜索评测集。只输出 JSON。"},
        {"role": "user", "content": f"歌曲信息：\n{card_text(c)}\n\n"
         "写一句中文用户搜索请求，描述这首歌的氛围/风格/主题，但**不要出现歌名或歌手名**。"
         '输出 {"query": "..."}'}])["query"]


def gen_constraints(rng: random.Random, vocab: dict) -> tuple[dict, str]:
    f: dict = {}
    parts = []
    if rng.random() < 0.5:
        f["vocal"] = rng.choice(["vocal", "instrumental"]); parts.append("纯音乐" if f["vocal"] == "instrumental" else "有人声的")
    if rng.random() < 0.4:
        f["moods"] = [rng.choice(vocab["mood"])]; parts.append(f["moods"][0])
    if rng.random() < 0.3:
        f["scenes"] = [rng.choice(vocab["scene"])]; parts.append(f["scenes"][0])
    if rng.random() < 0.4:
        mx = rng.choice([180, 240, 300]); f["duration_s"] = {"max": mx}; parts.append(f"{mx//60}分钟以内")
    if not f:
        f["moods"] = [rng.choice(vocab["mood"])]; parts.append(f["moods"][0])
    return f, "、".join(parts) + "的歌"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100, help="entries per split")
    ap.add_argument("--out", default="dev")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--no-llm", action="store_true", help="constraint/absent/mood only (no cost)")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    vocab = yaml.safe_load(cfg.VOCAB_FILE.read_text(encoding="utf-8"))
    cards = load_cards()
    llm = LLM()
    try:  # warm bge-m3 so semantic_search doesn't blow the 20s tool timeout
        import agent.index as index
        index._encoder()
    except Exception:  # noqa: BLE001
        pass

    from agent.models import Filter
    entries, intent_labels = [], []
    plan = [("title", 0.15), ("lyric", 0.25), ("describe", 0.20),
            ("mood", 0.20), ("constraint", 0.15), ("absent", 0.05)]
    quotas = {t: round(a.n * p) for t, p in plan}

    def add(typ, query, gold, graded=None, constraints=None, expected_intent=None):
        i = len(entries)
        e = {"id": f"{a.out}-{i:03d}", "type": typ, "query": query,
             "gold_ids": gold, "graded": graded, "constraints": constraints,
             "split": "dev" if a.out == "dev" else "test"}
        entries.append(e)
        if expected_intent is not None and len(intent_labels) < 50:
            intent_labels.append({"id": e["id"], "query": query, "expected_intent": expected_intent})

    # --- title / lyric / describe ---
    if not a.no_llm:
        searchable = [c for c in cards if c["searchable"] and c["lyric_lines"]]
        for _ in range(quotas["title"]):
            c = rng.choice(cards)
            add("title", gen_title(llm, c, rng), [c["song_id"]],
                expected_intent={"intents": ["title"], "title": c["name"]})
        for _ in range(quotas["lyric"]):
            while True:
                c = rng.choice(searchable)
                g = gen_lyric(llm, c)
                if g:
                    break
            add("lyric", g["query"], [c["song_id"]],
                expected_intent={"intents": ["lyric"], "lyric_query": True})
        for _ in range(quotas["describe"]):
            c = rng.choice(searchable or cards)
            add("describe", gen_describe(llm, c), [c["song_id"]],
                expected_intent={"intents": ["describe"]})

    # --- mood / scene (graded) ---
    for i in range(quotas["mood"]):
        q = MOOD_QUERIES[i % len(MOOD_QUERIES)]
        import agent.index as index
        import agent.registry as registry
        import agent.tools  # noqa: F401
        try:
            res = registry.run("semantic_search", {"query": q, "limit": 10})
            ids = [it["song_id"] for it in (res.data or {}).get("items", [])]
        except Exception:  # noqa: BLE001
            ids = []
        graded = {}
        if ids and not a.no_llm:
            cards_by_id = {c["song_id"]: c for c in cards}
            payload = "\n".join(f"{sid}: {card_text(cards_by_id[sid])}" for sid in ids)
            out = llm.complete_json([
                {"role": "system", "content": "你在构建评测集，只输出 JSON。"},
                {"role": "user", "content": f"用户查询：{q}\n\n候选：\n{payload}\n\n"
                 '给每首打相关度 0(无关)/1(沾边)/2(贴切)。输出 {"graded": {"<song_id>": 0|1|2}}'}])
            graded = {int(k): int(v) for k, v in (out.get("graded") or {}).items() if int(v) > 0}
        add("mood", q, list(graded), graded=graded)

    # --- constraint (SQL gold) ---
    for _ in range(quotas["constraint"]):
        f, qtxt = gen_constraints(rng, vocab)
        gold = db.filter_songs(Filter(**f), 200, [])
        add("constraint", qtxt, gold, constraints=f)

    # --- absent ---
    for q in rng.sample(ABSENT, quotas["absent"]):
        add("absent", q, [])

    rng.shuffle(entries)
    out_path = EVAL_DIR / f"golden_{a.out}.jsonl"
    out_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
                        encoding="utf-8")
    if a.out == "dev" and intent_labels:
        (EVAL_DIR / "intent_labels.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in intent_labels) + "\n", encoding="utf-8")
    from collections import Counter
    print(f"wrote {out_path} ({len(entries)} entries)")
    print("  by type:", dict(Counter(e["type"] for e in entries)))
    print(f"  intent labels: {len(intent_labels)}  | LLM stats: {llm.stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
