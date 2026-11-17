"""Agent service config — reuses the data_pipeline config for DB + vocab + paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Model cache + mirror must be set before sentence-transformers/hf_hub load.
# Downloaded resources live under the repo's resources/ dir (D: drive).
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HOME", "D:/1Projects/HarmonyMusic/resources/hf_cache")
# models are pre-downloaded; skip hub network checks so loads are fast/offline
os.environ.setdefault("HF_HUB_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_pipeline"))

import config as dp  # noqa: E402  (data_pipeline/config.py)

DB = dp.db_config()
VOCAB_FILE = dp.VOCAB_FILE
DATA_DIR = dp.DATA_DIR
INDEX_DIR = dp.DATA_DIR / "index"
LOG_DIR = dp.DATA_DIR / "agent_logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TOOL_TIMEOUT_S = 20
LLM_TIMEOUT_S = 30          # per-attempt; the Run deadline is still 45s
DEFAULT_LIMIT = 10
MAX_LIMIT = 20
