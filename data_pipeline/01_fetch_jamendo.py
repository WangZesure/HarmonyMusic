"""1.1 Fetch the Jamendo candidate pool (raw JSON cached, deduped).

Pulls a few thousand tracks into `data/candidates.json`. Every raw API page is
cached under `data/cache/jamendo/`, so reruns are free (failed responses are not
cached). 02_select.py trims this to the final corpus.

    python data_pipeline/01_fetch_jamendo.py            # uses cache when present
    python data_pipeline/01_fetch_jamendo.py --no-cache # force refetch (quota!)

Query shape (learned by probing the live API — see README "Notes"):
  * Jamendo's `lang` filter is broken on this tier (`Internal Error` for every
    value), so there is no way to pull "Chinese first". The corpus is English/
    instrumental; Chinese requests are matched later via Chinese tags + bge-m3.
  * `vocalinstrumental=vocal` alone returns tag-less tracks. Combining it WITH a
    mood tag (`fuzzytags=happy`) returns fully-tagged tracks with ~30-55% Jamendo
    lyric coverage. So we fan out over tags, separately for vocal/instrumental.
  * Jamendo lyric coverage is still only ~30% for vocal tracks; the rest get
    lyrics from LRCLIB (03) or forced ASR on the GPU (04). Selection does NOT
    require lyrics.
"""
from __future__ import annotations

import argparse
import sys

from tqdm import tqdm

import config
import common

log = common.get_logger()

# Tags that actually return results, split by what we want to seed the corpus
# with. Jamendo tags are English (genre + mood + instrument + theatre tags).
VOCAL_TAGS = [
    "happy", "sad", "love", "romantic", "calm", "peaceful", "melancholic",
    "nostalgic", "hopeful", "inspiring", "dreamy", "energetic", "uplifting",
    "powerful", "emotional", "dark", "mysterious", "fun", "epic", "dramatic",
    "chill", "groovy", "cool", "party", "summer", "pop", "rock", "jazz",
    "folk", "acoustic", "soul", "blues", "reggae",
]
INSTRUMENTAL_TAGS = [
    "ambient", "calm", "relaxing", "peaceful", "cinematic", "epic", "dreamy",
    "chill", "meditation", "study", "electronica", "piano", "guitar",
    "soundtrack", "atmospheric", "jazz", "classical", "lounge",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Jamendo candidate tracks")
    parser.add_argument("--vocal", type=int, default=1300, help="target vocal tracks")
    parser.add_argument("--instrumental", type=int, default=700, help="target instrumental tracks")
    parser.add_argument("--per-query", type=int, default=60, help="max tracks per tag query")
    parser.add_argument("--no-cache", action="store_true", help="ignore on-disk cache")
    args = parser.parse_args()

    if not config.JAMENDO_CLIENT_ID:
        log.error("JAMENDO_CLIENT_ID not set - copy .env.example to .env and fill it in.")
        return 2

    client = common.JamendoClient(use_cache=not args.no_cache)
    base_params = {
        "include": "musicinfo+licenses+lyrics",
        "audioformat": config.JAMENDO_AUDIOFORMAT,
        "durationbetween": f"{config.DURATION_MIN}_{config.DURATION_MAX}",
    }

    plan = [("vocal", t) for t in VOCAL_TAGS] + [("instrumental", t) for t in INSTRUMENTAL_TAGS]
    quotas = {"vocal": args.vocal, "instrumental": args.instrumental}
    counts = {"vocal": 0, "instrumental": 0}
    seen: dict[str, dict] = {}

    bar = tqdm(total=args.vocal + args.instrumental, desc="candidates", unit="track")
    for kind, tag in plan:
        if counts[kind] >= quotas[kind]:
            continue
        want = min(args.per_query, quotas[kind] - counts[kind])
        try:
            for raw in client.iter_tracks(target=want, **base_params,
                                          vocalinstrumental=kind, fuzzytags=tag):
                flat = common.flatten_track(raw, source_query=f"{kind}:{tag}", kind=kind)
                tid = str(flat["id"])
                if tid in seen:
                    continue
                seen[tid] = flat
                counts[kind] += 1
                bar.update(1)
        except Exception as e:  # a bad query shouldn't kill the whole run
            log.warning("query %s:%s failed: %s", kind, tag, e)
    bar.close()

    tracks = sorted(seen.values(), key=lambda t: t["id"])
    common.save_json(config.DATA_DIR / "candidates.json", tracks)

    n_vocal = sum(1 for t in tracks if t.get("kind") == "vocal")
    n_inst = sum(1 for t in tracks if t.get("kind") == "instrumental")
    n_lyr = sum(1 for t in tracks if (t.get("jamendo_lyrics") or "").strip())
    log.info("saved %d candidates -> %s", len(tracks), config.DATA_DIR / "candidates.json")
    log.info("  vocal=%d instrumental=%d (of which with Jamendo lyrics=%d)",
             n_vocal, n_inst, n_lyr)
    for kind in ("vocal", "instrumental"):
        if counts[kind] < quotas[kind]:
            log.warning("%s quota %d not met (got %d) - widen its tag list.",
                        kind, quotas[kind], counts[kind])
    return 0


if __name__ == "__main__":
    sys.exit(main())
