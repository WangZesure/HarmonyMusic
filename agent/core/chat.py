"""Multi-turn chat (phase 4): session state, "换一首", "更X一点", long-term preference.

    from agent.core.chat import chat
    chat("s1", "来点深夜伤感的歌")        # new search
    chat("s1", "换一首")                  # excludes already-shown songs
    chat("s1", "更欢快一点")              # edits the mood constraint
"""
from __future__ import annotations

import re

import yaml

import agent.config as cfg
import agent.db as db
from agent.core import pipeline
from agent.core.intent import Intent, parse
from agent.core.session import store

_VOCAB = None


def _vocab() -> dict:
    global _VOCAB
    if _VOCAB is None:
        with cfg.VOCAB_FILE.open("r", encoding="utf-8") as f:
            _VOCAB = yaml.safe_load(f)
    return _VOCAB


def _map_tag(word: str) -> tuple[str, str] | None:
    """Map a spoken word to (tag_type, tag) if it's a known mood/scene/genre."""
    v = _vocab()
    w = (word or "").strip()
    for typ, key in (("mood", "mood"), ("scene", "scene"), ("genre", "genre")):
        if w in v[key]:
            return typ, w
    alias = v.get("aliases", {})
    if w in alias and alias[w] in v["mood"]:
        return "mood", alias[w]
    for typ, key in (("mood", "mood"), ("scene", "scene")):
        for t in v[key]:
            if t and (t in w or w in t):
                return typ, t
    return None


def classify_turn(query: str) -> tuple[str, tuple[str, str] | None]:
    q = query.strip()
    if re.search(r"换(一)?[首个]|再来一首|再换|其他的|别的|其他的歌|其他", q):
        return "more", None
    m = re.search(r"^(?:更|再|要更|换成|改成)\s*([^\s，。]{1,6}?)\s*(?:一点|一些|点儿|些|点)?(?:的)?(?:歌|音乐|曲子)?$", q)
    if m:
        tag = _map_tag(m.group(1))
        if tag:
            return "refine", tag
    n = re.search(r"(?:不要|别放|去掉|不想听|除了)\s*([^\s，。]{1,6})", q)
    if n:
        tag = _map_tag(n.group(1))
        if tag:
            return "negative", tag
    return "new", None


def _profile_soft(user_id: int, k: int = 3) -> list[str]:
    try:
        return [t["tag"] for t in db.user_profile(user_id, k)["top_tags"]]
    except Exception:  # noqa: BLE001
        return []


def chat(session_id: str, query: str, user_id: int | None = None) -> dict:
    sess = store.get(session_id)
    action, tag = classify_turn(query)
    if sess.intent is None and action in ("more", "refine", "negative"):
        action, tag = "new", None          # no session context -> treat as a fresh query
    extra_soft = _profile_soft(user_id, 3) if user_id else []
    intent_override = None
    exclude: list[int] = []

    if action == "more" and sess.intent is not None:
        intent_override = sess.intent
        exclude = list(sess.seen)
    elif action == "refine" and sess.intent is not None and tag:
        typ, t = tag
        f = sess.intent.filters.model_copy()
        if typ == "mood":
            f = f.model_copy(update={"moods": [t]})
        elif typ == "scene":
            f = f.model_copy(update={"scenes": [t]})
        elif typ == "genre":
            f = f.model_copy(update={"genres": [t]})
        intent_override = sess.intent.model_copy(update={"filters": f, "semantic_query": f"{t}的歌"})
    elif action == "negative" and sess.intent is not None and tag:
        _typ, t = tag
        intent_override = sess.intent  # drop the tag from any soft/filter, exclude shown with it
        exclude = [s for s in sess.seen if t in _tags_of(s)]

    if intent_override is None:
        intent_override = parse(query)

    result = pipeline.search(query, user_id=user_id, exclude=exclude,
                             extra_soft=extra_soft, intent_override=intent_override,
                             gate=(action == "new"))   # continuations: see verify.CONTINUATION_MIN
    result["action"] = action
    result["session_id"] = session_id

    # update session
    sess.intent = intent_override
    sess.turns += 1
    sess.seen = list(dict.fromkeys(sess.seen + [s["song_id"] for s in result["songs"]]))[-40:]
    store.save(sess)
    result["suggestions"] = ["换一首", "更欢快一点", "再伤感一点"]
    return result


def _tags_of(song_id: int) -> set[str]:
    t = db.tag_map([song_id]).get(song_id, {})
    return set(t["genres"]) | set(t["moods"]) | set(t["scenes"])
