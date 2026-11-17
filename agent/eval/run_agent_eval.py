"""Score the v6 agent on multi-turn golden cases (property-based).

    python -m agent.eval.run_agent_eval --limit 5
    python -m agent.eval.run_agent_eval --limit 5 --baseline

Checks per case: type, final constraints, no-repeat for 换一首, dropped tags,
clarify/pending, absent handling, search/finish/ask_user tool assertions,
max steps, legal ids. `--baseline` also runs the old chat()+classify_turn.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

import agent.db as db
from agent.core.agent_loop import run
from agent.models import Filter
from agent.session_store import key_for, store

EVAL_DIR = Path(__file__).parent


def _ids(out: dict) -> list[int]:
    return [int(s["song_id"]) for s in (out.get("songs") or []) if "song_id" in s]


def _run_agent_case(case: dict, user_id: int, engine: str = "hand") -> dict:
    sid = f"eval-{case['id']}-{uuid.uuid4().hex[:6]}"
    store.delete(key_for(user_id, sid))
    if engine == "graph":
        from agent.core.graph_loop import run as run_fn
    else:
        from agent.core.agent_loop import run as run_fn
    outputs, lat, tokens, steps, tools = [], [], 0, 0, []
    try:
        for i, turn in enumerate(case["turns"]):
            t0 = time.time()
            out = run_fn(turn["query"], sid, user_id, f"{case['id']}-t{i}")
            lat.append(time.time() - t0)
            outputs.append(out)
            trace = out.get("trace") or []
            tokens += sum(int(x.get("tokens") or 0) for x in trace)
            steps += sum(1 for x in trace if x.get("actor") == "llm")
            tools += [x.get("tool") for x in trace if x.get("tool")]
        state = store.load(key_for(user_id, sid))
    finally:
        store.delete(key_for(user_id, sid))
    return {"outputs": outputs, "state": state, "lat": lat,
            "tokens": tokens, "steps": steps, "tools": tools}


def _run_baseline_case(case: dict, user_id: int) -> dict:
    from agent.core.chat import chat
    sid = f"base-{case['id']}-{uuid.uuid4().hex[:6]}"
    outputs, lat = [], []
    for turn in case["turns"]:
        t0 = time.time()
        r = chat(sid, turn["query"], user_id)
        lat.append(time.time() - t0)
        songs = r.get("songs") or []
        outputs.append({"type": "songs" if songs else "refusal",
                        "songs": songs, "message": r.get("message", ""), "trace": []})
    return {"outputs": outputs, "state": {}, "lat": lat,
            "tokens": 0, "steps": 0, "tools": []}


def _tool_trace(out: dict, tool: str) -> list[dict]:
    return [x for x in (out.get("trace") or [])
            if x.get("tool") == tool and x.get("ok") is True]


def _check(case: dict, result: dict, is_agent: bool) -> list[str]:
    reasons: list[str] = []
    turns, outputs = case["turns"], result["outputs"]
    check = case["check"]
    for i, (turn, out) in enumerate(zip(turns, outputs)):
        exp = turn["expect"]["type"]
        expected = exp if isinstance(exp, list) else [exp]
        if out.get("type") not in expected:
            reasons.append(f"turn{i}: type {out.get('type')} not in {expected}")

    final = outputs[-1]
    final_ids = _ids(final)
    if final_ids:
        info = db.base_info(final_ids)
        if len(info) != len(set(final_ids)):
            reasons.append("final: unknown song ids")

    from agent.core.rank import satisfies

    # Turn 0 must satisfy the constraints implied by ITS query — otherwise a
    # query-ignoring agent could pass the whole suite with any songs.
    first_filters = check.get("first_filters")
    if first_filters:
        f0 = Filter(**first_filters)
        ids0 = _ids(outputs[0])
        if not ids0:
            reasons.append("turn0: no songs for the first query")
        bad0 = [s for s in ids0 if not satisfies(s, f0)]
        if bad0:
            reasons.append(f"turn0: out-of-filter {bad0}")

    kind = check["kind"]
    if kind == "more":
        first = set(_ids(outputs[0]))
        if first & set(final_ids):
            reasons.append(f"more: repeated {sorted(first & set(final_ids))}")
        f = Filter(**(check.get("first_filters") or {}))
        bad = [s for s in final_ids if not satisfies(s, f)]
        if bad:
            reasons.append(f"more: out-of-first-filters {bad}")
        excl = any(set((x.get("args") or {}).get("exclude_song_ids") or []) & first
                   for x in _tool_trace(final, "search_songs"))
        if is_agent and not excl:
            reasons.append("more: second turn did not exclude seen ids")
    elif kind == "refine":
        f = Filter(**(check.get("final_filters") or {}))
        bad = [s for s in final_ids if not satisfies(s, f)]
        if bad:
            reasons.append(f"refine: out-of-filter {bad}")
        if set(final_ids) & set(_ids(outputs[0])):
            reasons.append("refine: repeated first-turn ids")
    elif kind == "negative":
        drop = set(check.get("drop_tags") or [])
        tags = db.tag_map(final_ids)
        bad = [s for s in final_ids
               if drop & (set(tags.get(s, {}).get("genres", [])) |
                          set(tags.get(s, {}).get("moods", [])) |
                          set(tags.get(s, {}).get("scenes", [])))]
        if bad:
            reasons.append(f"negative: dropped-tag songs {bad}")
        f = Filter(**(check.get("final_filters") or {}))
        bad = [s for s in final_ids if not satisfies(s, f)]
        if bad:
            reasons.append(f"negative: out-of-retained-filters {bad}")
    elif kind == "clarify_answer":
        if outputs[0].get("type") != "clarify":
            reasons.append("clarify_answer: first turn is not clarify")
        if final.get("type") != "songs":
            reasons.append(f"clarify_answer: second turn {final.get('type')} instead of songs")
        f = Filter(**(check.get("final_filters") or {}))
        bad = [s for s in final_ids if not satisfies(s, f)]
        if bad:
            reasons.append(f"clarify_answer: out-of-filter {bad}")
        want_langs = check.get("pending_languages") or []
        got_langs = (((result.get("state") or {}).get("constraints") or {})
                     .get("languages") or [])
        if want_langs and not set(want_langs) <= set(got_langs):
            reasons.append(f"clarify_answer: constraints.languages={got_langs}, "
                           f"expected to include {want_langs}")
    elif kind == "clarify":
        if final.get("type") != "clarify":
            reasons.append(f"clarify: {final.get('type')} instead of clarify")
        else:
            pending = (result.get("state") or {}).get("pending_question") or {}
            if not (pending.get("slot") or pending.get("question")):
                reasons.append("clarify: pending_question missing slot/question")
            want_slot = check.get("clarify_slot")
            if want_slot and want_slot not in (pending.get("slot") or ""):
                reasons.append(f"clarify: slot {pending.get('slot')!r} does not match {want_slot}")
    elif kind == "absent":
        if final.get("type") not in ("refusal", "clarify"):
            reasons.append(f"absent: {final.get('type')} instead of refusal/clarify")
        elif final.get("songs"):
            reasons.append("absent: answer included songs")
    elif kind == "select":
        if final.get("type") != "text":
            reasons.append(f"select: {final.get('type')} instead of text")
        details = _tool_trace(final, "song_detail")
        if not details:
            reasons.append("select: no successful song_detail")
        else:
            shown = (result.get("state") or {}).get("last_shown") or []
            idx = int(check.get("detail_index", 1))
            if len(shown) <= idx:
                reasons.append(f"select: turn 0 showed {len(shown)} songs, expected > {idx}")
            else:
                want = shown[idx]
                got = (details[-1].get("args") or {}).get("song_id")
                if got != want:
                    reasons.append(f"select: detailed {got}, expected last_shown[{idx}]={want}")
                name = (db.base_info([want]).get(want) or {}).get("name") or ""
                if name and name.lower() not in (final.get("message") or "").lower():
                    reasons.append(f"select: answer does not mention the song {name!r}")
        if not (final.get("message") or "").strip():
            reasons.append("select: empty text answer")

    if is_agent:
        for i, out in enumerate(outputs):
            trace = out.get("trace") or []
            if out.get("degraded") is not None:
                reasons.append(f"turn{i}: degraded {out.get('degraded')}")
            llm_steps = sum(1 for x in trace if x.get("actor") == "llm")
            if llm_steps > 5:
                reasons.append(f"turn{i}: {llm_steps} LLM steps > 5")
            searches = sum(1 for x in trace
                           if x.get("tool") == "search_songs" and x.get("ok") is True)
            if searches > 2:
                reasons.append(f"turn{i}: {searches} searches > 2")
            # Every song in the answer must have come from THIS turn's tools.
            tool_ids: set[int] = set()
            for x in trace:
                if x.get("tool") in ("search_songs", "refine_candidates") and x.get("ok") is True:
                    tool_ids |= set(x.get("ids") or [])
            if out.get("type") == "songs" and tool_ids and not set(_ids(out)) <= tool_ids:
                reasons.append(f"turn{i}: songs not all returned by this turn's tools")
            names = [x.get("tool") for x in trace if x.get("tool")]
            finish_ok = any(x.get("tool") == "finish" and x.get("ok") is True for x in trace)
            ask_ok = any(x.get("tool") == "ask_user" and x.get("ok") is True for x in trace)
            if out.get("type") == "songs" and not finish_ok:
                reasons.append(f"turn{i}: songs without successful finish")
            if out.get("type") == "clarify" and not ask_ok:
                reasons.append(f"turn{i}: clarify without successful ask_user")
            if "remember_preference" in names:
                reasons.append(f"turn{i}: unexpected remember_preference")
    return reasons


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def _run(method, cases: list[dict], user_id: int, is_agent: bool, verbose: bool) -> dict:
    rows, fails = [], []
    for case in cases:
        result = method(case, user_id)
        reasons = _check(case, result, is_agent)
        ok = not reasons
        deg = sum(1 for out in result["outputs"] if out.get("degraded") is not None)
        rows.append((case["check"]["kind"], ok, result["lat"], result["tokens"],
                     result["steps"], deg))
        if not ok:
            fails.append((case["id"], reasons))
            if verbose:
                print(f"  FAIL {case['id']}: {reasons}")
    total = len(rows)
    passed = sum(1 for r in rows if r[1])
    kinds: dict[str, list[bool]] = {}
    for kind, ok, *_ in rows:
        kinds.setdefault(kind, []).append(ok)
    lat = [x for _, _, ls, _, _, _ in rows for x in ls]
    return {
        "total": total, "passed": passed,
        "by_kind": {k: (sum(v), len(v)) for k, v in kinds.items()},
        "p95_s": round(_pct(lat, 0.95), 2),
        "avg_tokens": round(sum(r[3] for r in rows) / max(1, total)),
        "avg_steps": round(sum(r[4] for r in rows) / max(1, total), 1),
        "degraded": sum(r[5] for r in rows),
        "fails": fails,
    }


def _print(name: str, m: dict) -> None:
    print(f"\n== {name} ==")
    print(f"  success {m['passed']}/{m['total']}  P95 {m['p95_s']}s  "
          f"avg tokens {m['avg_tokens']}  avg steps {m['avg_steps']}  degraded {m['degraded']}")
    for k, (p, n) in sorted(m["by_kind"].items()):
        print(f"  {k:9} {p}/{n}")
    if m["fails"]:
        print("  failures:")
        for cid, reasons in m["fails"][:10]:
            print(f"    {cid}: {reasons}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["hand", "graph"], default="hand",
                    help="hand = agent_loop.run; graph = LangGraph prototype")
    ap.add_argument("--file", default="golden_multiturn.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--kind", default="",
                    help="only more/refine/negative/select/clarify/clarify_answer/absent")
    ap.add_argument("--user", type=int, default=1)
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--min-pass", type=float, default=0.9,
                    help="exit non-zero when the pass rate is below this (0-1)")
    a = ap.parse_args()

    store.ensure_schema()
    cases = [json.loads(l) for l in (EVAL_DIR / a.file).read_text(encoding="utf-8").splitlines()
             if l.strip()]
    if a.kind:
        cases = [c for c in cases if c["check"]["kind"] == a.kind]
    if a.limit:
        cases = cases[: a.limit]

    try:
        import agent.index as index
        index._encoder()
        from agent.core.rank import _reranker
        _reranker()
    except Exception:  # noqa: BLE001
        pass

    agent_m = _run(lambda c, u: _run_agent_case(c, u, a.engine), cases, a.user, True,
                   a.verbose)
    _print("agent-v6", agent_m)
    if agent_m["total"] == 0:
        print("  no cases matched; failing the run")
        return 1
    if a.min_pass and agent_m["total"] and agent_m["passed"] / agent_m["total"] < a.min_pass:
        return 1
    if a.baseline:
        base_m = _run(_run_baseline_case, cases, a.user, False, a.verbose)
        _print("baseline (chat + classify_turn)", base_m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
