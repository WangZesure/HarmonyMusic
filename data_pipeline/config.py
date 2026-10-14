"""Shared configuration for the data pipeline (stage 1).

Loads `.env` from the repo root and exposes paths + constants. Every script in
`data_pipeline/` imports from here so paths resolve against the repo root, not
the current working directory.

Run any script as a module-from-directory:
    python data_pipeline/01_fetch_jamendo.py
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # scripts that need env will fail loudly with a clear message
    load_dotenv = None  # type: ignore[assignment]

PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_DIR.parent

DATA_DIR = PIPELINE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = DATA_DIR / "reports"
LRC_DIR = DATA_DIR / "lrc"
AUDIO_DIR = DATA_DIR / "audio"
VOCAB_DIR = PIPELINE_DIR / "vocab"
VOCAB_FILE = VOCAB_DIR / "tags_zh.yaml"
CANDIDATES_FILE = DATA_DIR / "candidates.json"
SELECTED_FILE = DATA_DIR / "selected.json"
LYRICS_FILE = DATA_DIR / "lyrics.json"
LYRICS_FINAL_FILE = DATA_DIR / "lyrics_final.json"
TAGS_FILE = DATA_DIR / "tags.json"
SQL_DIR = PROJECT_ROOT / "db"   # schema_v1.sql / seed_v1.sql / check_v1.sql live here

for _d in (RAW_DIR, CACHE_DIR, PROCESSED_DIR, REPORTS_DIR, LRC_DIR, AUDIO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

if load_dotenv is not None:
    load_dotenv(PROJECT_ROOT / ".env")

# --- Jamendo ---------------------------------------------------------------
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "").strip()
JAMENDO_BASE = "https://api.jamendo.com/v3.0"
JAMENDO_PAGE_SIZE = 200  # API hard max

# --- DeepSeek --------------------------------------------------------------
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
DEEPSEEK_CHAT_MODEL = os.getenv("DEEPSEEK_CHAT_MODEL", "deepseek-chat").strip()

# --- LRCLIB (free, no key) -------------------------------------------------
LRCLIB_BASE = "https://lrclib.net/api"

# --- MySQL (shared with openKoa) ------------------------------------------
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "123456")
DB_NAME = os.getenv("DB_NAME", "mymusic")


def db_config() -> dict:
    return {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "charset": "utf8mb4",
    }


# --- Selection targets (see DocsForMusicAgent/phase12.md §1.2) -------------
DURATION_MIN = 90          # seconds
DURATION_MAX = 420
CANDIDATE_TARGET = 2000    # candidate pool size pulled from Jamendo
SELECT_VOCAL = 400         # of the 500 selected tracks
SELECT_INSTRUMENTAL = 100
LYRIC_MIN_LINES = 4        # a vocal track with less than this is dropped
LYRIC_MIN_CHARS = 80

# C4: lyric_searchable is true only when lrc_source in (lrclib, aligned) AND
# lrc_quality >= this threshold. ASR lyrics are never searchable by default.
LRC_QUALITY_MIN = 0.6
# song_lyric_lines.text is varchar(512); a longer line is a data defect -> drop the
# song's lyrics entirely (between12 forbids truncation).
MAX_LYRIC_LINE_CHARS = 512
CONTRACT_VERSION = "v1"

# Audio format stored in the `url` field. mp32 = VBR good quality; the plan
# recommends streaming rather than downloading, so this only affects the URL.
JAMENDO_AUDIOFORMAT = "mp32"


def resolve(path: str | os.PathLike) -> Path:
    """Resolve a path relative to PROJECT_ROOT if not already absolute."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p
