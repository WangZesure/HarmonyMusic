"""Fixed pipeline ①→⑤ (phase3 §2). `_run` does ①–④ and returns the ranked ids;
`search` adds ⑤ (reasons); `rank_ids` skips ⑤ for evaluation/ablation."""
from __future__ import annotations

import time

from agent.core import rank as rank_mod
from agent.core import recall as recall_mod
from agent.core import respond as respond_mod
from agent.core import verify
from agent.core.intent import parse


def _run(query: str, exclude=None, extra_soft=None, intent_override=None, gate: bool = True,
         relax: bool = True):
    """①→④. Returns (intent, ranked, matches, relaxed, trace, refuse_message|None).

    `gate=False` (continuation turns: 换一首 / 更X一点 / 不要X) lowers the confidence
    threshold to `CONTINUATION_MIN` — see verify.CONTINUATION_MIN for why.
    `relax=False` disables the automatic relax loop (the agent tool decides)."""
    trace = []
    exclude = set(exclude or ())

    t0 = time.time()
    from agent.core import ablate
    if intent_override is not None:
        intent = intent_override
    elif ablate.on("no_intent"):
        from agent.core.intent import Intent
        intent = Intent(intents=["describe"], semantic_query=query, count=5)
    else:
        intent = parse(query)                                             # ①
    if extra_soft:
        intent = intent.model_copy(update={"soft_tags": list(dict.fromkeys(intent.soft_tags + list(extra_soft)))})
    trace.append({"step": "intent", "query": query, "intents": intent.intents,
                  "filters": intent.filters.model_dump(), "soft_tags": intent.soft_tags,
                  "exclude": sorted(exclude), "ms": round((time.time() - t0) * 1000)})

    routes, matches, meta = recall_mod.recall(intent)                     # ②
    lyric_conf = meta.get("lyric_conf", {})
    if exclude:
        routes = {k: [s for s in v if s not in exclude] for k, v in routes.items()}
    trace.append({"step": "recall", "sizes": {k: len(v) for k, v in routes.items()}})

    relaxed: list[str] = []
    is_lyric = bool(intent.lyric_fragment or "lyric" in intent.intents)
    thr = (verify.LYRIC_MIN_CONF if is_lyric else verify.RELEVANCE_MIN) if gate else verify.CONTINUATION_MIN

    if verify.named_not_found(intent, routes):
        who = intent.title or intent.artist
        return intent, [], matches, relaxed, trace, \
            (f"曲库里没有《{who}》这首歌" if intent.title else f"曲库里没有 {who} 的歌") + "，要不要试试按风格或氛围描述来找？", False

    if not ablate.on("no_artist"):
        mm = verify.artist_mismatch(intent, routes)
        if mm:
            return intent, [], matches, relaxed, trace, mm, False

    # F1 (Melo-style 曲库反查): if the model recognised the lyric's origin, decide here
    # BEFORE the lyric confidence threshold. Recognised famous lyrics are exactly the
    # ones a single threshold can't separate.
    src = intent.lyric_source
    if verify.LYRIC_SOURCE_ENABLED and src is not None and src.confidence == "high" and src.title and is_lyric:
        from agent.core.recall import fuzzy_title
        sid = fuzzy_title(src.title, src.artist)
        who = (f"{src.artist}《{src.title}》" if src.artist else f"《{src.title}》")
        trace.append({"step": "lyric_source", "title": src.title, "artist": src.artist,
                      "in_corpus": sid is not None})
        if sid is None:
            return intent, [], matches, relaxed, trace, f"这句出自 {who}，曲库里没有这首歌。", False
        fill = [s for s in (routes.get("lyric") or routes.get("semantic") or []) if s != sid]
        ranked = [(sid, 1.0)] + [(s, 0.9 - i * 0.01) for i, s in enumerate(fill[: max(0, intent.count - 1)])]
        return intent, ranked, matches, relaxed, trace, None, False

    ranked = rank_mod.rank(intent, routes, matches, exclude=exclude, lyric_conf=lyric_conf)  # ③
    trace.append({"step": "rank", "n": len(ranked), "top": ranked[0] if ranked else None})

    cur = intent
    rounds = 0                                                            # ④
    while relax and (len(ranked) < cur.count or (ranked and ranked[0][1] < thr)) \
            and rounds < verify.RELAX_MAX:
        seq = verify.relax_sequence(cur)
        if not seq:
            break
        desc, cur = seq[0]
        relaxed.append(desc)
        routes, matches, meta = recall_mod.recall(cur)
        lyric_conf = meta.get("lyric_conf", {})
        if exclude:
            routes = {k: [s for s in v if s not in exclude] for k, v in routes.items()}
        ranked = rank_mod.rank(cur, routes, matches, exclude=exclude, lyric_conf=lyric_conf)
        rounds += 1
        trace.append({"step": "relax", "applied": desc, "n": len(ranked)})

    if not ranked:
        return cur, [], matches, relaxed, trace, "没有找到符合的歌，换个说法或放宽条件试试？", False
    if ranked[0][1] < thr:
        if verify.GRAY_ENABLED and is_lyric and ranked[0][1] >= verify.GRAY_LOW:
            # F1 gray zone: don't hard-refuse, but don't claim "found" either —
            # return up to 2 closest songs with a "不太确定" message.
            trace.append({"step": "gray", "conf": round(ranked[0][1], 3)})
            return cur, ranked[:2], matches, relaxed, trace, None, True
        return cur, [], matches, relaxed, trace, "不太确定你想找的是哪首，能再具体描述一下吗？", False
    return cur, ranked, matches, relaxed, trace, None, False


def rank_ids(query: str, exclude=None, extra_soft=None, intent_override=None,
             flag: dict | None = None, gate: bool = True) -> list[int]:
    """Ranked song ids only (no reason generation) — for eval/ablation.
    `flag` (optional) receives {"gray": bool} so the eval can count gray separately."""
    intent, ranked, _m, _r, _t, refuse, gray = _run(query, exclude, extra_soft, intent_override, gate)
    if flag is not None:
        flag["gray"] = gray
    return [] if refuse else [s for s, _ in ranked][: intent.count]


def search(query: str, user_id: int | None = None, exclude=None,
           extra_soft=None, intent_override=None, gate: bool = True, relax: bool = True) -> dict:
    intent, ranked, matches, relaxed, trace, refuse, gray = _run(
        query, exclude, extra_soft, intent_override, gate, relax)
    if refuse:
        return _result([], refuse, relaxed, trace)
    songs, message, reason_error = respond_mod.build(intent, ranked, matches, user_id=user_id)  # ⑤
    trace.append({"step": "respond", "n": len(songs), "reason_error": reason_error})
    if gray:
        message = "不太确定曲库里有没有这句，下面是最接近的：" + message
    elif intent.style_of:
        message = f"曲库没有 {intent.style_of}，下面是风格接近的：" + message
    if relaxed:
        message = f"（放宽了：{'、'.join(relaxed)}）" + message
    return _result(songs, message, relaxed, trace, gray=gray)


def _result(songs, message, relaxed, trace, gray: bool = False):
    return {"songs": songs, "message": message, "relaxed": relaxed, "trace": trace, "gray": gray}
