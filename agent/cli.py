"""Manual tool runner for debugging (phase 2 verification).

    python -m agent.cli list
    python -m agent.cli schemas
    python -m agent.cli search_by_keyword '{"query":"love"}'
    python -m agent.cli get_user_profile '{}' --user 1
    python -m agent.cli search_by_lyrics '{"query":"move a little closer"}' --user 1
"""
from __future__ import annotations

import argparse
import json
import sys

import agent.registry as registry
import agent.tools  # noqa: F401  (registers tools)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", help="tool name, or 'list' / 'schemas'")
    ap.add_argument("args", nargs="?", default="{}", help="JSON args")
    ap.add_argument("--user", type=int, default=None, help="inject user_id (runtime only)")
    a = ap.parse_args()

    if a.tool == "list":
        for name, (model, _fn, desc) in registry.TOOLS.items():
            print(f"{name:20} {desc}")
        return 0
    if a.tool == "schemas":
        print(json.dumps(registry.openai_schemas(), ensure_ascii=False, indent=2))
        return 0

    try:
        args = json.loads(a.args)
    except json.JSONDecodeError as e:
        print("bad JSON args:", e)
        return 2
    ctx = {"user_id": a.user} if a.user is not None else {}
    # warm the encoder so the timed tool call only pays for encoding, not the
    # one-off ~10s bge-m3 load (which would otherwise blow the 20s tool timeout).
    try:
        import agent.index as index
        index._encoder()
    except Exception:  # noqa: BLE001
        pass
    result = registry.run(a.tool, args, ctx)
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
