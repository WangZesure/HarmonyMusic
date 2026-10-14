"""Ablation switches (phase3loop P2). Set AGENT_ABLATE=no_fuzzy,no_artist,... to turn
modules off for an ablation run. Empty by default -> normal behaviour."""
from __future__ import annotations

import os

ABLATE = {x.strip() for x in (os.environ.get("AGENT_ABLATE", "") or "").split(",") if x.strip()}


def set_active(names) -> None:
    """Switch the active ablation set at runtime (for in-process ablation runs)."""
    global ABLATE
    ABLATE = set(names)


def on(name: str) -> bool:
    return name in ABLATE
