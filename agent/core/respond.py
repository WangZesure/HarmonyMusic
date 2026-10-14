"""Response generation (phase3 §2.5). song_id/name/artist are filled by CODE;
the LLM only writes reasons, and any id outside the candidate set is dropped."""
from __future__ import annotations

import agent.db as db
from agent.core.intent import Intent


def build(intent: Intent, ranked: list[tuple[int, float]], matches: dict[int, dict],
          llm=None, user_id: int | None = None) -> tuple[list[dict], str]:
    top = ranked[: intent.count]
    ids = [s for s, _ in top]
    if not ids:
        return [], "曲库里没有找到符合条件的歌。"

    hits = db.to_hits(ids, {s: sc for s, sc in top}, matches, match_type="semantic")

    # reasons via LLM, constrained to the candidate ids
    reasons: dict[int, str] = {}
    reason_error: str | None = None
    try:
        from agent.core.llm import LLM
        llm = llm or LLM()
        cards = "\n".join(
            f"{h.song_id}|{h.name}|{h.artist}|{','.join(h.moods+h.scenes+h.genres)}|"
            f"{getattr(matches.get(h.song_id), 'line_text', '') or ''}" for h in hits)
        out = llm.complete_json([
            {"role": "system", "content": "你在给用户推荐歌曲。只能引用卡里的事实，为每首写一句中文推荐理由。只输出 JSON。"},
            {"role": "user", "content": f"用户需求：{intent.semantic_query or ''}\n候选(id|歌名|歌手|标签|命中歌词)：\n{cards}\n"
             '输出 {"reasons": [{"song_id": <id>, "reason": "..."}]}'}], temperature=0.3)
        for r in out.get("reasons", []):
            try:
                sid = int(r["song_id"])
                if sid in ids:                       # structural anti-hallucination
                    reasons[sid] = str(r.get("reason", ""))[:120]
            except (KeyError, ValueError, TypeError):
                continue
    except Exception as e:  # noqa: BLE001  — log, don't swallow silently (F4)
        import logging
        reason_error = f"{type(e).__name__}: {e}"[:160]
        logging.getLogger("agent").warning("reason generation failed: %s", reason_error)

    songs = [{**h.model_dump(), "reason": reasons.get(h.song_id, "")} for h in hits]
    return songs, "为你找到以下歌曲：", reason_error
