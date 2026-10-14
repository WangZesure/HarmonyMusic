"""1.2 Select the final corpus from the candidate pool.

Reads `data/candidates.json`, applies eligibility rules, stratifies by mood so
every mood is searchable, cleans whatever Jamendo lyrics exist, and writes:

  data/selected.json         the chosen tracks (with cleaned lyrics + buckets)
  data/reports/02_select.md  human-readable distribution report

    python data_pipeline/02_select.py

Rules (phase12.md §1.2, adjusted for the live API):
  * duration 90-420s and `audiodownload_allowed == true` (else /tracks/file 404s);
  * ~400 vocal + ~100 instrumental, >=10 per mood tag where the pool allows;
  * lyrics are NOT required of vocal tracks — Jamendo only supplies ~30% of them
    and the rest are filled by LRCLIB (03) / forced ASR (04). Tracks that already
    carry Jamendo lyrics are simply preferred when filling each bucket.
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict

import yaml

import config
import common

log = common.get_logger()


def load_vocab() -> dict:
    with config.VOCAB_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def mood_tags_for(track: dict, vmap: dict, allowed_moods: set[str]) -> list[str]:
    """Map a track's English vartags to Chinese mood tags, restricted to the vocab."""
    out: list[str] = []
    for vt in track.get("vartags") or []:
        for zh in vmap.get(str(vt).lower(), []):
            if zh in allowed_moods and zh not in out:
                out.append(zh)
    return out


def prepare(track: dict, vocab: dict) -> dict:
    """Attach cleaned lyrics + mood buckets. Never drops a track.

    Jamendo's `lyrics` field is unreliable — it is often empty, or just the
    artist/song name. Only keep it when it looks like real lyrics (enough lines
    and characters); junk is cleared so LRCLIB (03) / ASR (04) fill it instead.
    """
    allowed_moods = set(vocab["mood"])
    t = dict(track)
    t["mood_tags"] = mood_tags_for(t, vocab["jamendo_vartag_map"], allowed_moods)
    cleaned = common.clean_lyrics(t.get("jamendo_lyrics", ""))
    valid = (common.lyric_line_count(cleaned) >= config.LYRIC_MIN_LINES
             and len(cleaned) >= config.LYRIC_MIN_CHARS)
    t["clean_lyrics"] = cleaned if valid else ""
    t["has_jamendo_lyrics"] = valid
    t["lyric_lines"] = common.lyric_line_count(t["clean_lyrics"])
    return t


def stratified_pick(pool: list[dict], target: int, moods: list[str],
                    rng: random.Random, taken: set[str]) -> list[dict]:
    """Round-robin across mood buckets so each mood is represented, then fill.

    Prefers tracks that already have Jamendo lyrics within each bucket.
    """
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in pool:
        if str(t["id"]) in taken:
            continue
        for tag in (t["mood_tags"] or ["其他"]):
            buckets[tag].append(t)

    for b in buckets.values():
        rng.shuffle(b)
        b.sort(key=lambda t: not t["has_jamendo_lyrics"])  # lyrics-first, stable

    picked: list[dict] = []
    seen_ids: set[str] = set()

    def take_from(tag: str) -> bool:
        q = buckets.get(tag, [])
        while q:
            cand = q.pop(0)
            cid = str(cand["id"])
            if cid in seen_ids or cid in taken:
                continue
            seen_ids.add(cid)
            picked.append(cand)
            return True
        return False

    while len(picked) < target:
        progressed = False
        for tag in moods + ["其他"]:
            if len(picked) >= target:
                break
            if take_from(tag):
                progressed = True
        if not progressed:
            break
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the final corpus")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    candidates = common.load_json(config.DATA_DIR / "candidates.json")
    if not candidates:
        log.error("data/candidates.json missing or empty - run 01_fetch_jamendo.py first.")
        return 2

    vocab = load_vocab()
    moods: list[str] = vocab["mood"]

    kept: list[dict] = []
    dropped_no_dl = dropped_dur = 0
    for c in candidates:
        if not c.get("audiodownload_allowed"):
            dropped_no_dl += 1
            continue
        if not (config.DURATION_MIN <= (c.get("duration") or 0) <= config.DURATION_MAX):
            dropped_dur += 1
            continue
        kept.append(prepare(c, vocab))

    vocal = [t for t in kept if t.get("kind") == "vocal"]
    instrumental = [t for t in kept if t.get("kind") == "instrumental"]
    log.info("eligible: %d (vocal=%d instrumental=%d) | dropped: dl=%d dur=%d",
             len(kept), len(vocal), len(instrumental), dropped_no_dl, dropped_dur)

    taken: set[str] = set()
    chosen_vocal = stratified_pick(vocal, config.SELECT_VOCAL, moods, rng, taken)
    taken |= {str(t["id"]) for t in chosen_vocal}
    chosen_inst = stratified_pick(instrumental, config.SELECT_INSTRUMENTAL, moods, rng, taken)

    selected = chosen_vocal + chosen_inst
    # label each track with its rarest mood tag so the tail moods are visible
    rarity = Counter(m for t in kept for m in t["mood_tags"])
    for t in selected:
        tags = t["mood_tags"] or ["其他"]
        t["primary_mood"] = min(tags, key=lambda m: (rarity[m], m))

    common.save_json(config.DATA_DIR / "selected.json", selected)
    write_report(selected, moods)

    want = config.SELECT_VOCAL + config.SELECT_INSTRUMENTAL
    if len(selected) < want:
        log.warning("selected %d < target %d - the pool is thin in some buckets. "
                    "Widen fetching (01).", len(selected), want)
    log.info("saved %d selected tracks -> %s", len(selected), config.DATA_DIR / "selected.json")
    return 0


def write_report(selected: list[dict], moods: list[str]) -> None:
    vocal = [t for t in selected if t.get("kind") == "vocal"]
    inst = [t for t in selected if t.get("kind") == "instrumental"]
    mood_counts = Counter(t["primary_mood"] for t in selected)
    with_lyr = [t for t in vocal if t["has_jamendo_lyrics"]]
    lyric_lens = sorted(t["lyric_lines"] for t in with_lyr)

    def median(xs: list[int]) -> int:
        return xs[len(xs) // 2] if xs else 0

    lines = [
        "# 02_select report",
        "",
        f"- selected: {len(selected)}  (vocal {len(vocal)} + instrumental {len(inst)})",
        f"- vocal with Jamendo lyrics: {len(with_lyr)} "
        f"({(100 * len(with_lyr) // len(vocal)) if vocal else 0}% of vocal) "
        f"-> the rest need LRCLIB (03) / ASR (04)",
        f"- lyric lines among those: min={lyric_lens[0] if lyric_lens else 0} "
        f"median={median(lyric_lens)} max={lyric_lens[-1] if lyric_lens else 0}",
        "",
        "## mood distribution (primary tag)",
        "",
    ]
    for m in moods + ["其他"]:
        lines.append(f"- {m}: {mood_counts.get(m, 0)}")

    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORTS_DIR / "02_select.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    common.save_json(config.REPORTS_DIR / "02_select.json", {
        "selected": len(selected),
        "vocal": len(vocal),
        "instrumental": len(inst),
        "vocal_with_jamendo_lyrics": len(with_lyr),
        "mood_counts": dict(mood_counts),
    })


if __name__ == "__main__":
    sys.exit(main())
