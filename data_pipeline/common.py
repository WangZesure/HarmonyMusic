"""Shared helpers for the data pipeline.

Provides: a configured logger, a disk-cached + retrying HTTP client, a thin
Jamendo API wrapper, and lyric-text cleaning used by several steps.

Scripts run as `python data_pipeline/NN_*.py`, so `data_pipeline/` is on
sys.path and `import config` / `import common` resolve directly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Iterator

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

import config

USER_AGENT = "HarmonyMusicDataPipeline/0.1 (non-commercial research; contact: local)"

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
_LOG = logging.getLogger("pipeline")


def get_logger() -> logging.Logger:
    if not _LOG.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))
        _LOG.addHandler(handler)
        _LOG.setLevel(logging.INFO)
    return _LOG


log = get_logger()


# --------------------------------------------------------------------------- #
# JSON I/O
# --------------------------------------------------------------------------- #
def save_json(path: Path | str, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_json(path: Path | str, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# HTTP with on-disk cache + retries
# --------------------------------------------------------------------------- #
class HttpError(RuntimeError):
    pass


class HttpClient:
    """GETs JSON, caching every response to disk so reruns don't burn API quota."""

    def __init__(self, use_cache: bool = True, timeout: int = 30):
        self.use_cache = use_cache
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    @staticmethod
    def _cache_file(namespace: str, key: str) -> Path:
        d = config.CACHE_DIR / namespace
        d.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return d / f"{digest}.json"

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        retry=retry_if_exception_type((requests.RequestException, HttpError)),
        reraise=True,
    )
    def get_json(
        self,
        url: str,
        params: dict | None = None,
        namespace: str = "generic",
        extra_headers: dict | None = None,
    ) -> Any:
        params = params or {}
        key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        cache_file = self._cache_file(namespace, key)
        if self.use_cache and cache_file.exists():
            return load_json(cache_file)

        resp = self.session.get(
            url, params=params, timeout=self.timeout, headers=extra_headers or {}
        )
        if resp.status_code >= 500:
            raise HttpError(f"HTTP {resp.status_code} from {url}")
        if resp.status_code >= 400:
            # 4xx are not retried by tenacity (we raise a plain error below)
            raise HttpError(f"HTTP {resp.status_code} from {resp.url}")
        data = resp.json()
        # Never cache a Jamendo failure/empty envelope — otherwise a transient
        # error is replayed from disk forever and silently poisons later runs.
        failed = isinstance(data, dict) and str(
            data.get("headers", {}).get("status", "")
        ).lower() == "failed"
        if self.use_cache and not failed:
            save_json(cache_file, data)
        return data

    def get_json_or_none(
        self, url: str, params: dict | None = None, namespace: str = "generic"
    ) -> Any:
        """Like get_json but returns None on 404 (LRCLIB 'not found' is normal).

        Caches the 404 as a marker so reruns don't re-query missing entries.
        """
        params = params or {}
        key = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        cache_file = self._cache_file(namespace, key)
        if self.use_cache and cache_file.exists():
            data = load_json(cache_file)
            return None if isinstance(data, dict) and data.get("__not_found__") else data

        resp = self.session.get(url, params=params, timeout=self.timeout)
        if resp.status_code == 404:
            if self.use_cache:
                save_json(cache_file, {"__not_found__": True})
            return None
        if resp.status_code >= 400:
            raise HttpError(f"HTTP {resp.status_code} from {resp.url}")
        data = resp.json()
        if self.use_cache:
            save_json(cache_file, data)
        return data


# --------------------------------------------------------------------------- #
# Jamendo
# --------------------------------------------------------------------------- #
class JamendoClient:
    def __init__(self, client_id: str | None = None, use_cache: bool = True):
        self.client_id = (client_id or config.JAMENDO_CLIENT_ID).strip()
        if not self.client_id:
            raise RuntimeError(
                "JAMENDO_CLIENT_ID is not set.\n"
                "  1. Register a free app at https://devportal.jamendo.com\n"
                "  2. Copy the Client ID into the repo-root `.env` (see .env.example)"
            )
        self.http = HttpClient(use_cache=use_cache)

    def tracks(self, **params: Any) -> dict:
        query = {"client_id": self.client_id, "format": "json", "limit": config.JAMENDO_PAGE_SIZE}
        query.update({k: v for k, v in params.items() if v is not None})
        data = self.http.get_json(f"{config.JAMENDO_BASE}/tracks/", query, namespace="jamendo")
        headers = data.get("headers", {})
        if headers.get("status") != "success":
            raise HttpError(f"Jamendo error: {headers.get('error_message') or headers}")
        return data

    def iter_tracks(self, target: int, **params: Any) -> Iterator[dict]:
        """Yield up to `target` raw track objects, paging through the API."""
        offset = 0
        yielded = 0
        while yielded < target:
            data = self.tracks(offset=offset, **params)
            results = data.get("results", [])
            if not results:
                return
            for track in results:
                yield track
                yielded += 1
                if yielded >= target:
                    return
            if len(results) < config.JAMENDO_PAGE_SIZE:
                return
            offset += len(results)

    def stream_url(self, track_id: Any, action: str = "stream") -> str:
        """Stable redirect URL for playback. 404s if the track forbids download."""
        return (
            f"{config.JAMENDO_BASE}/tracks/file/"
            f"?client_id={self.client_id}&id={track_id}"
            f"&action={action}&audioformat={config.JAMENDO_AUDIOFORMAT}"
        )


def flatten_track(raw: dict, source_query: str | None = None, kind: str | None = None) -> dict:
    """Normalize a raw Jamendo track object into a flat dict for downstream steps.

    Keeps the fields we actually use and hoists musicinfo.tags.* up one level.
    The full raw page is still cached on disk if we ever need more.

    `kind` ("vocal"/"instrumental") comes from the *query*, not the response:
    Jamendo's vocalinstrumental filter works but the field is usually absent
    from the returned musicinfo, so the query is the only reliable signal.
    """
    mi = raw.get("musicinfo") or {}
    tags = mi.get("tags") or {}
    return {
        "id": raw.get("id"),
        "kind": kind or mi.get("vocalinstrumental"),
        "name": raw.get("name"),
        "duration": raw.get("duration"),
        "artist_id": raw.get("artist_id"),
        "artist_name": raw.get("artist_name"),
        "album_id": raw.get("album_id"),
        "album_name": raw.get("album_name"),
        "album_image": raw.get("album_image"),
        "image": raw.get("image"),
        "audio": raw.get("audio"),
        "audiodownload": raw.get("audiodownload"),
        "audiodownload_allowed": bool(raw.get("audiodownload_allowed", False)),
        "releasedate": raw.get("releasedate"),
        "license_ccurl": raw.get("license_ccurl"),
        "shareurl": raw.get("shareurl"),
        # musicinfo (only present with include=musicinfo)
        "vocalinstrumental": mi.get("vocalinstrumental"),
        "acousticelectric": mi.get("acousticelectric"),
        "speed": mi.get("speed"),
        "gender": mi.get("gender"),
        "lang": mi.get("lang"),
        "genres": list(tags.get("genres") or []),
        "instruments": list(tags.get("instruments") or []),
        "vartags": list(tags.get("vartags") or []),
        # lyrics (only present with include=lyrics)
        "jamendo_lyrics": raw.get("lyrics") or "",
        "source_query": source_query,
    }


# --------------------------------------------------------------------------- #
# Lyric cleaning (used by 02 select, 03 lrclib, 05 qc)
# --------------------------------------------------------------------------- #
_SECTION_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")          # [Chorus], [Verse 1]
_INLINE_TAG_RE = re.compile(r"^\s*\[[^\]]*\]\s*")         # [Chorus] lyric text
_CHORD_TOKEN_RE = re.compile(
    r"^[A-G](#|b)?(maj|min|m|dim|aug|sus|add|no)?\d*(/[A-G](#|b)?)?$"
)


def is_chord_line(line: str) -> bool:
    """True for chord/tab lines like 'Am  F  C  G' or 'C#m7'. Letras only."""
    tokens = line.split()
    if not tokens:
        return False
    if not all(_CHORD_TOKEN_RE.match(tok) for tok in tokens):
        return False
    # A single real word can coincidentally look like a chord (e.g. "A", "Am");
    # require an actual chord-looking token with a letter+optional quality, and
    # reject lines that are a single 1-2 char token to avoid nuking real lyrics.
    return not (len(tokens) == 1 and len(tokens[0]) <= 2)


def clean_lyrics(text: str) -> str:
    """Strip section markers and chord lines, collapse blank runs, trim."""
    if not text:
        return ""
    out: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.rstrip()
        if _SECTION_RE.match(line):
            continue
        stripped = _INLINE_TAG_RE.sub("", line).strip()
        if not stripped:
            out.append("")  # keep paragraph breaks, collapse later
            continue
        if is_chord_line(stripped):
            continue
        out.append(stripped)

    # collapse 2+ blank lines into one, strip leading/trailing blanks
    cleaned: list[str] = []
    blank = False
    for line in out:
        if line == "":
            if not blank:
                cleaned.append("")
            blank = True
        else:
            cleaned.append(line)
            blank = False
    while cleaned and cleaned[0] == "":
        cleaned.pop(0)
    while cleaned and cleaned[-1] == "":
        cleaned.pop()
    return "\n".join(cleaned)


def lyric_line_count(text: str) -> int:
    return len([ln for ln in (text or "").split("\n") if ln.strip()])


# --------------------------------------------------------------------------- #
# LRC (canonical) — contract C4
# --------------------------------------------------------------------------- #
# The single source of truth for LRC parsing: songs.lyric (07) and
# song_lyric_lines (07) MUST both go through parse_lrc so they cannot disagree.
_LRC_TS_RE = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:\.\d{1,3})?)\]")


def _sec_to_lrc(sec: float) -> str:
    sec = max(0.0, float(sec))
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


def normalize_lrc(text: str, duration_s: float | None = None) -> str:
    """Return strict LRC per C4: one timestamp per line, ascending, no meta lines,
    no empty-text lines, first ts >= 0, last ts < duration.

    Multi-timestamp lines (`[00:01.00][00:05.00]word`) are split into separate
    lines. Meta headers (`[ar:..]`, `[ti:..]`) and timestamp-only lines are
    dropped because their `[..]` does not match the numeric timestamp pattern.
    """
    entries: list[tuple[float, str]] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stamps = list(_LRC_TS_RE.finditer(raw))
        if not stamps:
            continue
        body = raw[stamps[-1].end():].strip()
        if not body:
            continue  # timestamp-only line -> forbidden by C4
        for m in stamps:
            sec = int(m.group(1)) * 60 + float(m.group(2))
            if duration_s is not None and sec >= duration_s:
                continue  # last timestamp must be < duration
            entries.append((sec, body))
    if not entries:
        return ""
    entries.sort(key=lambda e: e[0])
    # enforce strictly-increasing timestamps (C4 "按时间升序"): original_split can
    # give a title line and the first lyric the same time; nudge duplicates +0.01s.
    out: list[tuple[float, str]] = []
    last = -1.0
    for sec, body in entries:
        sec = round(sec, 2)            # LRC is centisecond resolution
        if sec <= last:
            sec = round(last + 0.01, 2)
        last = sec
        out.append((sec, body))
    return "\n".join(f"[{_sec_to_lrc(sec)}]{body}" for sec, body in out) + "\n"


def parse_lrc(text: str, duration_s: float | None = None) -> list[dict]:
    """Parse LRC into rows for song_lyric_lines: {line_no, start_ms, end_ms, text}.

    `end_ms` of a line is the next line's start; the last line ends at the song
    duration (or its own start + 3s if duration is unknown).
    """
    rows: list[dict] = []
    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        m = _LRC_TS_RE.match(raw)
        if not m:
            continue
        body = raw[m.end():].strip()
        if not body:
            continue
        start_ms = int(round((int(m.group(1)) * 60 + float(m.group(2))) * 1000))
        if duration_s is not None and start_ms >= int(duration_s * 1000):
            continue
        rows.append({"line_no": len(rows), "start_ms": start_ms, "end_ms": None, "text": body})
    for i, row in enumerate(rows):
        if i + 1 < len(rows):
            row["end_ms"] = rows[i + 1]["start_ms"]
        else:
            row["end_ms"] = int(duration_s * 1000) if duration_s else row["start_ms"] + 3000
    return rows
