"""Configuration for the v2 content-based recommender.

Everything is overridable by environment variables so the experiment can be
re-run with different settings without editing code.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]           # recommend/
REPO = ROOT.parent                                    # HarmonyMusic/
INDEX_DIR = REPO / "data_pipeline" / "data" / "index"  # bge-m3 vectors built by 08_build_index.py
ARTIFACT_DIR = ROOT / "artifacts"                     # checkpoints / histories / eval output
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def _load_env(path: Path = REPO / ".env") -> dict[str, str]:
    """Minimal .env reader — avoids depending on python-dotenv."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


_ENV = _load_env()
DB = dict(
    host=os.environ.get("DB_HOST") or _ENV.get("DB_HOST", "127.0.0.1"),
    user=os.environ.get("DB_USER") or _ENV.get("DB_USER", "root"),
    password=os.environ.get("DB_PASSWORD") or _ENV.get("DB_PASSWORD", "123456"),
    database=os.environ.get("DB_NAME") or _ENV.get("DB_NAME", "mymusic"),
    charset="utf8mb4",
)

# ---------------------------------------------------------------- data ----
SEED = int(os.environ.get("REC_SEED", 20261111))
N_USERS = int(os.environ.get("REC_N_USERS", 200))
PLAYS_PER_USER = int(os.environ.get("REC_PLAYS_PER_USER", 15))
# 契约 C1：合成用户 ID 从 100000 起，和真实注册用户隔开
SYNTH_USER_BASE = 100000
# 每个用户每次播放有这么多概率去听「口味之外」的歌（探索）
EXPLORE_RATE = float(os.environ.get("REC_EXPLORE_RATE", 0.25))

# --------------------------------------------------------------- model ----
IN_DIM = 1024          # bge-m3 维度，与 data/index/songs_v1 一致
HIDDEN = 256
OUT_DIM = 64
DROPOUT = 0.2
EPOCHS = int(os.environ.get("REC_EPOCHS", 30))
BATCH = 256
LR = 1e-3
NEG_PER_POS = 4        # 每个正样本采几个负样本
MAX_HISTORY = 20       # 用户塔最多取最近多少首

MODEL_PATH = ARTIFACT_DIR / "two_tower_v2.pt"
META_PATH = ARTIFACT_DIR / "meta_v2.json"
