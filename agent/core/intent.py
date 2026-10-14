"""Intent parsing (phase3 §2.1): query -> structured Intent.

Fast path: an obvious title/artist query is answered by BM25 without an LLM call.
Otherwise DeepSeek returns JSON; tags are validated against the C3 vocab in code
(so out-of-vocab tags can't survive, without depending on strict mode).
"""
from __future__ import annotations

import re

import yaml
from pydantic import BaseModel, Field

import agent.config as cfg
from agent.models import DurationRange, Filter

Vocab = dict


def _vocab() -> Vocab:
    with cfg.VOCAB_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Intent(BaseModel):
    intents: list[str] = Field(default_factory=list)   # title/artist/lyric/describe/similar
    title: str | None = None
    artist: str | None = None
    lyric_fragment: str | None = None
    lyric_fragments_en: list[str] = Field(default_factory=list)  # 2-3 English paraphrases
    filters: Filter = Field(default_factory=Filter)
    soft_tags: list[str] = Field(default_factory=list)
    semantic_query: str | None = None
    count: int = 5
    style_of: str | None = None     # "X 风格/那样的歌": X is a style reference, not a name to find (F2)


# colloquial/display language names -> ISO 639-1 (any form the model might emit)
_LANG = {
    "中文": "zh", "汉语": "zh", "普通话": "zh", "chinese": "zh", "mandarin": "zh",
    "粤语": "yue", "广东话": "yue", "cantonese": "yue",
    "英文": "en", "英语": "en", "english": "en",
    "法文": "fr", "法语": "fr", "french": "fr",
    "德文": "de", "德语": "de", "german": "de",
    "日文": "ja", "日语": "ja", "japanese": "ja",
    "韩文": "ko", "韩语": "ko", "korean": "ko",
    "西班牙文": "es", "西班牙语": "es", "spanish": "es",
    "葡萄牙文": "pt", "葡语": "pt", "葡萄牙语": "pt", "portuguese": "pt",
    "意大利文": "it", "意大利语": "it", "italian": "it",
    "俄文": "ru", "俄语": "ru", "russian": "ru",
    "纯音乐": "zxx", "器乐": "zxx", "instrumental": "zxx",
}
_ISO = {"zh", "yue", "en", "fr", "de", "ja", "ko", "es", "pt", "it", "ru", "zxx", "und"}


def norm_lang(x: str) -> str | None:
    """Map any language wording ('法语', 'French', 'fr') to an ISO 639-1 code."""
    if not isinstance(x, str):
        return None
    s = x.strip().lower()
    if s in _ISO:
        return s
    return _LANG.get(x.strip()) or _LANG.get(s)  # try raw then lowercased


_SYSTEM = """你是音乐搜索的意图解析器。把用户的话解析成 JSON，字段：
intents: 从 ["title","artist","lyric","describe","similar"] 里选（可多个）；
title/artist: 用户明确点名的歌名/歌手（没有则 null）；
lyric_fragment: 用户引用的歌词原文（没有则 null）。**第一人称、像歌词的句子（如“我感觉自己老了，时间一去不返”）也要当歌词，放进这里**；
lyric_fragments_en: 若歌词片段是中文，给出 2-3 种英文转述关键词（用于匹配英文歌词）；否则给空数组；
semantic_query: 把需求改写成一句"歌曲卡片"风格的描述（歌名/歌手/风格/情绪/场景/主题），用于向量检索；
filters: 硬约束，仅在用户**明说**时填 {"genres":[],"moods":[],"scenes":[],"vocal":null|"vocal"|"instrumental","languages":[],"duration_s":null|{"min":,"max":}}。**languages 必须用 ISO 639-1 两字母代码**（法语→fr，德语→de，英文→en）；
soft_tags: 你**推测**的标签（只加分，不放 filters）；
count: 需要几首（默认 5）。
标签只能从下面词表里选，不要自造。口语别名请映射到标准标签。
"""


def build_system() -> str:
    v = _vocab()
    aliases = "\n".join(f"{k} -> {t}" for k, t in v.get("aliases", {}).items())
    return (_SYSTEM
            + f"\ngenres: {v['genre']}\nmoods: {v['mood']}\nscenes: {v['scene']}"
            + f"\n口语别名映射:\n{aliases}")


def fast_path(query: str):
    """Return (song_id, title, artist) if the query is an obvious title/artist hit."""
    from agent.tools import bm25
    q = query.strip().strip("。！？?.")
    if len(q) > 30 or any(w in q for w in ("想要", "推荐", "适合", "听点", "来点", "有没有", "找一首", "找一下")):
        return None
    hits = bm25.search_titles(q, 3)
    if not hits:
        return None
    import agent.db as db
    info = db.base_info([hits[0][0]])
    name = info.get(hits[0][0], {}).get("name", "")
    if not (name and name.lower() in q.lower()):   # query must literally contain the title
        return None
    artist = None                                   # extract "<artist> 的 <title>" / "by <artist>"
    m = re.match(r"^(.*?)\s*的\s*(.+)$", q)
    if m and name.lower() in m.group(2).lower():
        artist = m.group(1).strip() or None
    return hits[0][0], name, artist


def _clean_tags(xs, allowed: set[str]) -> list[str]:
    out = []
    for x in xs or []:
        x = str(x).strip()
        if x in allowed and x not in out:
            out.append(x)
    return out


def parse(query: str, llm=None) -> Intent:
    fp = fast_path(query)
    if fp:
        _sid, name, artist = fp
        return Intent(intents=["title"], title=name, artist=artist, semantic_query=query)

    from agent.core.llm import LLM
    llm = llm or LLM()
    data = llm.complete_json([
        {"role": "system", "content": build_system()},
        {"role": "user", "content": query}], temperature=0.0)

    v = _vocab()
    allowed = {"genre": set(v["genre"]), "mood": set(v["mood"]), "scene": set(v["scene"])}
    f = data.get("filters") or {}
    dur = f.get("duration_s")
    from agent.core import ablate
    if ablate.on("no_lang_norm"):
        langs = [x for x in (f.get("languages") or []) if isinstance(x, str)][:4]
    else:
        langs = [c for x in (f.get("languages") or []) if (c := norm_lang(x))][:4]
    filt = Filter(
        genres=_clean_tags(f.get("genres"), allowed["genre"]),
        moods=_clean_tags(f.get("moods"), allowed["mood"]),
        scenes=_clean_tags(f.get("scenes"), allowed["scene"]),
        vocal=f.get("vocal") if f.get("vocal") in ("vocal", "instrumental") else None,
        languages=langs,
        duration_s=DurationRange(**dur) if isinstance(dur, dict) else None,
    )
    soft = _clean_tags(data.get("soft_tags"), allowed["mood"] | allowed["scene"] | allowed["genre"])
    lf_en = data.get("lyric_fragments_en") or []
    if not lf_en and data.get("lyric_fragment_en"):
        lf_en = [data["lyric_fragment_en"]]
    lf_en = [str(x)[:80] for x in lf_en if isinstance(x, (str, int))][:3]
    title, artist = data.get("title"), data.get("artist")
    # F2: "X 风格 / 像 X 那样的歌" is a STYLE reference — don't treat X as a name to find.
    style_of = None
    if (title or artist) and re.search(r"风格|那样的|那样的歌|那种味儿|那味儿|像.{1,12}一样", query):
        style_of = artist or title
        title, artist = None, None
    return Intent(
        intents=[i for i in (data.get("intents") or []) if i in
                 ("title", "artist", "lyric", "describe", "similar")],
        title=title, artist=artist,
        lyric_fragment=data.get("lyric_fragment"), lyric_fragments_en=lf_en,
        filters=filt, soft_tags=soft, semantic_query=data.get("semantic_query") or query,
        count=max(1, min(int(data.get("count") or 5), cfg.MAX_LIMIT)),
        style_of=style_of,
    )
