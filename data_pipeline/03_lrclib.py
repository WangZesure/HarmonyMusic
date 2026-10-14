"""1.3a Resolve lyrics — try LRCLIB for synced lyrics, else fall back to plain text.

For every selected track this writes `data/lyrics.json` (id -> record). It is the
input to 04 (alignment) and 05 (QC). Resolution priority:

  * instrumental tracks      -> no lyrics at all;
  * LRCLIB synced (LRC)      -> source "lrclib_synced"  (alignment NOT needed);
  * LRCLIB plain             -> source "lrclib_plain"   (04 must align it);
  * Jamendo cleaned lyrics   -> source "jamendo_plain"  (04 must align it);
  * none of the above        -> source null             (04 must fully ASR it).

LRCLIB is free and needs no key. Hit rate for Jamendo's indie catalogue is low
by design — that is exactly why 04 (forced ASR on the GPU) exists.

    python data_pipeline/03_lrclib.py [--limit N] [--sleep S]
"""
from __future__ import annotations

import argparse
import sys
import time

from tqdm import tqdm

import config
import common

log = common.get_logger()


# --------------------------------------------------------------------------- #
# LRCLIB
# --------------------------------------------------------------------------- #
def lrclib_get(http: common.HttpClient, artist: str, title: str,
               album: str | None = None, duration: int | None = None) -> dict | None:
    params = {"artist_name": artist, "track_name": title}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = int(duration)
    try:
        return http.get_json_or_none(f"{config.LRCLIB_BASE}/get", params, namespace="lrclib")
    except Exception as e:
        log.debug("lrclib get failed for %s - %s: %s", artist, title, e)
        return None


def lrclib_search(http: common.HttpClient, artist: str, title: str) -> list[dict]:
    try:
        res = http.get_json_or_none(
            f"{config.LRCLIB_BASE}/search",
            {"track_name": title, "artist_name": artist},
            namespace="lrclib",
        )
        return res or []
    except Exception as e:
        log.debug("lrclib search failed for %s - %s: %s", artist, title, e)
        return []


def best_match(cands: list[dict], duration: int | None, title: str) -> dict | None:
    """Pick the closest candidate by duration, preferring exact title matches."""
    if not cands:
        return None
    tl = title.strip().lower()

    def score(c: dict) -> tuple:
        exact = 0 if str(c.get("trackName", "")).strip().lower() == tl else 1
        dd = abs((c.get("duration") or 0) - (duration or 0)) if duration else 0
        return (exact, dd)

    ranked = sorted(cands, key=score)
    top = ranked[0]
    # reject a match whose duration is wildly off (avoid wrong-song lyrics)
    if duration and top.get("duration") and abs(top["duration"] - duration) > 15:
        # only accept if it was an exact title match
        if str(top.get("trackName", "")).strip().lower() != tl:
            return None
    return top


def resolve(track: dict, http: common.HttpClient) -> dict:
    tid = str(track["id"])
    rec = {"id": track["id"], "kind": track.get("kind"),
           "artist": track.get("artist_name"), "title": track.get("name"),
           "duration": track.get("duration"), "source": None,
           "synced": None, "plain": None, "lrclib_id": None, "match_duration_diff": None}

    if track.get("kind") != "vocal":
        rec["source"] = "instrumental"
        return rec

    artist = (track.get("artist_name") or "").strip()
    title = (track.get("name") or "").strip()
    duration = track.get("duration")

    match = lrclib_get(http, artist, title, track.get("album_name"), duration)
    if not match:
        match = best_match(lrclib_search(http, artist, title), duration, title)

    if match:
        rec["lrclib_id"] = match.get("id")
        if match.get("duration") and duration:
            rec["match_duration_diff"] = round(abs(match["duration"] - duration), 1)
        synced = (match.get("syncedLyrics") or "").strip() or None
        plain = (match.get("plainLyrics") or "").strip() or None
        if synced:
            rec["synced"] = synced
            rec["source"] = "lrclib_synced"
        elif plain:
            rec["plain"] = plain
            rec["source"] = "lrclib_plain"

    if not rec["source"] or rec["source"] == "lrclib_plain":
        jam = (track.get("clean_lyrics") or "").strip()
        if jam:
            rec["plain"] = jam
            rec["source"] = "jamendo_plain"
    if not rec["source"]:
        rec["source"] = None  # 04 will ASR
    return rec


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve lyrics via LRCLIB")
    parser.add_argument("--limit", type=int, default=0, help="only process first N (debug)")
    parser.add_argument("--sleep", type=float, default=0.2, help="seconds between requests")
    args = parser.parse_args()

    selected = common.load_json(config.SELECTED_FILE)
    if not selected:
        log.error("data/selected.json missing - run 02_select.py first.")
        return 2

    existing = common.load_json(config.LYRICS_FILE, default={}) or {}
    http = common.HttpClient(use_cache=True)

    targets = selected[: args.limit] if args.limit else selected
    out = dict(existing)
    for track in tqdm(targets, desc="lrclib", unit="track"):
        tid = str(track["id"])
        if tid in existing and not args.limit:  # resumable: keep prior results
            continue
        out[tid] = resolve(track, http)
        time.sleep(args.sleep)

    common.save_json(config.LYRICS_FILE, out)
    write_report(out, selected, targets)
    return 0


def write_report(out: dict, selected: list[dict], targets: list[dict]) -> None:
    from collections import Counter
    counts = Counter(out[str(t["id"])].get("source") for t in targets
                     if str(t["id"]) in out)
    vocal = [t for t in targets if t.get("kind") == "vocal"]
    need_align = sum(1 for t in vocal
                     if out.get(str(t["id"]), {}).get("source") in ("lrclib_plain", "jamendo_plain"))
    need_asr = sum(1 for t in vocal if out.get(str(t["id"]), {}).get("source") is None)

    lines = ["# 03_lrclib report", "",
             f"- processed: {len(targets)} of {len(selected)} selected", "",
             "## lyric source",
             ""]
    for src in ["lrclib_synced", "lrclib_plain", "jamendo_plain", "instrumental", None]:
        lines.append(f"- {src or 'unresolved (needs full ASR)'}: {counts.get(src, 0)}")
    lines += ["", f"- vocal tracks that still need alignment (04): {need_align}",
              f"- vocal tracks that need full ASR (04): {need_asr}"]

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORTS_DIR / "03_lrclib.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("lyrics resolved -> %s", config.LYRICS_FILE)
    for src in ["lrclib_synced", "lrclib_plain", "jamendo_plain", None]:
        log.info("  %-22s %d", src or "unresolved", counts.get(src, 0))
    log.info("  vocal needing alignment=%d  full-ASR=%d", need_align, need_asr)


if __name__ == "__main__":
    sys.exit(main())
