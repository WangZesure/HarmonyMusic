"""Baselines B0–B3 for phase-3 comparison (phase3 §0)."""
from __future__ import annotations

import json
from pathlib import Path

import agent.db as db
import agent.registry as registry
import agent.tools  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data_pipeline" / "data"


def _dedupe(xs):
    out = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out


def _cards():
    sel = json.loads((DATA / "selected.json").read_text(encoding="utf-8"))
    tags = json.loads((DATA / "tags.json").read_text(encoding="utf-8"))
    return [{"id": int(t["id"]), "name": t["name"], "artist": t["artist_name"],
             "tags": ",".join((tags.get(str(t["id"]), {}).get("genres", [])
                               + tags.get(str(t["id"]), {}).get("moods", [])
                               + tags.get(str(t["id"]), {}).get("scenes", []))),
             "summary": tags.get(str(t["id"]), {}).get("summary", "")}
            for t in sel]


class B0:
    """LLM guesses song titles from the query, then fuzzy-matches to the corpus."""
    name = "B0_llm_guess"

    def __init__(self):
        from agent.core.llm import LLM
        self.llm = LLM()

    def search(self, query: str, k: int = 5) -> list[int]:
        out = self.llm.complete_json([
            {"role": "system", "content": "只输出 JSON。"},
            {"role": "user", "content": f"用户想听：{query}\n"
             '猜 5 首符合的歌曲，输出 {"titles": ["歌名", ...]}'}])
        ids = []
        for title in out.get("titles", []):
            rows = db.q("SELECT id FROM songs WHERE name LIKE %s LIMIT 2", [f"%{title[:24]}%"])
            ids += [r["id"] for r in rows]
        return _dedupe(ids)[:k]


class B1:
    """Whole corpus in the prompt — the baseline the Agent must beat (phase3 §0)."""
    name = "B1_full_corpus"

    def __init__(self):
        from agent.core.llm import LLM
        self.llm = LLM()
        self.corpus = "\n".join(f"{c['id']}|{c['name']}|{c['artist']}|{c['tags']}" for c in _cards())
        self.ids = {c["id"] for c in _cards()}

    def search(self, query: str, k: int = 5) -> list[int]:
        out = self.llm.complete_json([
            {"role": "system", "content": "你在从给定曲库里挑歌，只输出 JSON，只能选列表里出现过的 id。"},
            {"role": "user", "content": f"曲库(id|歌名|歌手|标签)：\n{self.corpus}\n\n"
             f"用户需求：{query}\n挑最合适的 {k} 首，输出 {{\"song_ids\": [...]}}"}])
        return [int(x) for x in out.get("song_ids", []) if int(x) in self.ids][:k]


class B2:
    """Single vector search, no parsing / no rerank."""
    name = "B2_semantic_only"

    def search(self, query: str, k: int = 5) -> list[int]:
        res = registry.run("semantic_search", {"query": query, "limit": k})
        return [i["song_id"] for i in (res.data or {}).get("items", [])]


class B3:
    """The original app's LIKE search."""
    name = "B3_like"

    def search(self, query: str, k: int = 5) -> list[int]:
        return db.keyword_search(query, k, [])


class Agent:
    """The phase-3 pipeline (A)."""
    name = "A_agent"

    def __init__(self):
        self.last_gray = False

    def search(self, query: str, k: int = 5) -> list[int]:
        from agent.core.pipeline import search
        r = search(query)
        self.last_gray = bool(r.get("gray", False))   # gray-zone -> excluded from eval
        return [s["song_id"] for s in r["songs"]][:k]
