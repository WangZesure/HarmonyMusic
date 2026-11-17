"""End-to-end smoke test for the whole HTTP chain (acceptance §5/§7).

Runs against RUNNING services — it does not start them:

    D:\\apps\\tools\\Conda\\python.exe -m uvicorn agent.app:app --port 8000   # Agent
    node openKoa/bin/www                                                    # Koa :3000
    D:\\apps\\tools\\Conda\\python.exe -m agent.e2e_smoke                    # this script

Why it exists: the eval sets were all green while `换一首`/`更X一点` returned **0 songs**
for lyric sessions (F7) — a whole-turn defect no offline metric covered. Everything here
goes through Koa + JWT + SSE exactly like the App does, so "评测集全绿" can't hide a
broken user path again.

  env: KOA_URL (default http://127.0.0.1:3000), AGENT_URL (default http://127.0.0.1:8000)
       E2E_USER / E2E_PASSWORD (default user1 / 123456)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

KOA = os.environ.get("KOA_URL", "http://127.0.0.1:3000")
AGENT = os.environ.get("AGENT_URL", "http://127.0.0.1:8000")
USER = os.environ.get("E2E_USER", "user1")
PASSWORD = os.environ.get("E2E_PASSWORD", "123456")
# Fresh sessions per run: a fixed sid would inherit the previous run's MySQL
# state (seen/last_shown/recent) and make the smoke test non-reproducible.
RUN = uuid.uuid4().hex[:6]

_checks: list[tuple[bool, str]] = []


def _post(url: str, obj: dict, headers: dict | None = None, raw: bool = False):
    req = urllib.request.Request(
        url, data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode("utf-8")
    return body if raw else json.loads(body)


def check(ok: bool, label: str, detail: str = "") -> bool:
    _checks.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    return bool(ok)


def sse_events(body: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for chunk in body.split("\n\n"):
        if chunk.startswith("event: "):
            head, _, rest = chunk.partition("\n")
            out.setdefault(head[7:], []).append(rest[6:] if rest.startswith("data: ") else "")
    return out


def _get(url: str):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_agent(timeout_s: int = 90) -> bool:
    """Wait for the warm-up thread (bge-m3 + reranker) before the first tool call."""
    import time
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            if _get(f"{AGENT}/health").get("ready"):
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    return False


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"Koa={KOA}  Agent={AGENT}  user={USER}\n")

    # 0) warm-up ---------------------------------------------------------------
    check(wait_agent(), "Agent 已预热（/health.ready）")
    # 1) Agent service, direct -------------------------------------------------
    print("[1] POST /agent/search (直连 Agent)")
    try:
        d = _post(f"{AGENT}/agent/search", {"query": "适合深夜一个人听的伤感歌曲", "count": 3})
        songs = (d.get("data") or {}).get("songs") or []
        check(d.get("ok") and songs, "直连 /agent/search 返回歌曲", f"{len(songs)} 首")
        check(all(s.get("reason") for s in songs), "推荐理由非空 (F4)")
    except Exception as e:  # noqa: BLE001
        check(False, "Agent 服务可达", repr(e)[:90])
        return 1

    # 2) login -----------------------------------------------------------------
    print("\n[2] POST /login (Koa + JWT)")
    try:
        login = _post(f"{KOA}/login", {"username": USER, "password": PASSWORD})
        token = (login.get("data") or {}).get("token", "")
    except Exception as e:  # noqa: BLE001
        token = ""
        check(False, "Koa /login", repr(e)[:90])
    if not check(bool(token), "拿到 JWT"):
        return 1

    def chat(msg: str, sid: str) -> tuple[dict, dict]:
        body = _post(f"{KOA}/agent/chat", {"message": msg, "sessionId": sid},
                     {"authorization": token}, raw=True)
        ev = sse_events(body)
        if "result" not in ev:
            return {}, ev
        return json.loads(ev["result"][0]), ev

    # 3) single turn over SSE --------------------------------------------------
    print("\n[3] POST /agent/chat SSE（JWT → status → result）")
    r, ev = chat("适合深夜一个人听的伤感歌曲", f"e2e-single-{RUN}")
    check(ev.get("status") is not None and "result" in ev, "SSE 事件含 status + result",
          f"{ {k: len(v) for k, v in ev.items()} }")
    check(len(r.get("songs") or []) > 0, "描述类查询返回歌曲", f"{len(r.get('songs') or [])} 首")

    # 4) multi-turn on a LYRIC session (F7 lives here) -------------------------
    print("\n[4] 多轮 · 歌词会话（F7 回归点）")
    sid = f"e2e-lyric-{RUN}"
    r1, _ = chat("有首歌歌词是 I search for the strength", sid)
    first = [s["name"] for s in r1.get("songs") or []]
    check(len(first) > 0, "turn1 歌词查询作答", f"{len(first)} 首")
    for turn, msg in [(2, "换一首"), (3, "更欢快一点"), (4, "不要伤感")]:
        rn, _ = chat(msg, sid)
        names = [s["name"] for s in rn.get("songs") or []]
        check(len(names) > 0, f"turn{turn} 「{msg}」必须返回歌曲，不能回「不太确定」",
              f"{len(names)} 首 {names[:2]}")
        if turn == 2:
            check(not (set(names) & set(first)), "turn2 与 turn1 不重合（已排除 seen）")

    # 5) refusal path (must not have regressed) --------------------------------
    print("\n[5] 拒答不回退")
    r5, _ = chat("Coldplay 的 Yellow", f"e2e-refuse-{RUN}")
    text5 = (r5.get("message") or "") + (r5.get("question") or "")
    check(not (r5.get("songs") or []), "同名异歌手：不返回歌曲")
    check("Coldplay" in text5, "拒答/澄清文案点名 Coldplay", text5[:46])

    failed = [label for ok, label in _checks if not ok]
    print(f"\n{'=' * 62}\n{len(_checks) - len(failed)}/{len(_checks)} passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
