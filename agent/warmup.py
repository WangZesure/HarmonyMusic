"""Process-wide readiness flag.

CLI/tests start with READY set so `run()` never waits. The FastAPI lifespan
clears it, warms the models in a background thread, then sets it again; this
lets uvicorn bind the port immediately instead of refusing connections during
the ~45s model warm-up.
"""
from __future__ import annotations

import threading

READY = threading.Event()
READY.set()
