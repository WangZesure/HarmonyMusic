"""1.5 DeepSeek tagging + Chinese summary + language detection (contract C3).

For every selected vocal track (and every instrumental, using metadata only), asks
`deepseek-chat` for:
  * genres / moods / scenes chosen ONLY from vocab/tags_zh.yaml (anything else is
    dropped in code);
  * a one-sentence Chinese summary (`songs.introduction`, <= 255 chars);
  * the lyric language as an ISO 639-1 code (fills the 211 tracks Jamendo left
    empty; instrumental -> "zxx").

Writes `data/tags.json`:  { song_id: {genres, moods, scenes, summary, language} }
Responses are cached per song under `data/cache/deepseek/`, so reruns are free.

    python data_pipeline/06_llm_tagging.py [--limit N] [--workers 8]
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml
from openai import OpenAI
from tqdm import tqdm

import config
import common

log = common.get_logger()

SYSTEM = (
    "You are a music librarian. Given a song's metadata and (for vocal tracks) "
    "its lyrics, output STRICT JSON only. Every tag MUST be copied verbatim from "
    "the provided vocabulary; choose 1-4 per category, omit a category if unsure."
)


def load_vocab() -> dict:
    with config.VOCAB_FILE.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_prompt(track: dict, lyrics_final: dict, vocab: dict) -> str:
    tid = str(track["id"])
    lf = lyrics_final.get(tid, {})
    lyr = (lf.get("lrc") or "")
    # strip timestamps, keep the first ~40 lines
    body_lines = [ln.split("]", 1)[-1].strip() for ln in lyr.splitlines() if "]" in ln][:40]
    lyrics_txt = "\n".join(body_lines) if body_lines else "(instrumental / no lyrics)"
    jam = ", ".join((track.get("genres") or []) + (track.get("vartags") or []))
    return (
        f"name: {track.get('name')}\n"
        f"artist: {track.get('artist_name')}\n"
        f"jamendo_tags: {jam or '(none)'}\n"
        f"jamendo_language: {track.get('lang') or '(unknown)'}\n"
        f"kind: {track.get('kind')}\n"
        f"lyrics:\n{lyrics_txt}\n\n"
        f"genres vocab: {vocab['genre']}\n"
        f"moods vocab: {vocab['mood']}\n"
        f"scenes vocab: {vocab['scene']}\n\n"
        'Return JSON: {"genres": [...], "moods": [...], "scenes": [...], '
        '"summary": "<=40 Chinese characters, one sentence>", '
        '"language": "ISO 639-1 code, or zxx for instrumental, und if unknown"}'
    )


def clean_tags(items, allowed: set[str]) -> list[str]:
    out = []
    for t in items or []:
        t = str(t).strip()
        if t in allowed and t not in out:
            out.append(t)
    return out


def tag_one(client: OpenAI, model: str, track: dict, lyrics_final: dict, vocab: dict) -> dict:
    allowed = {"genre": set(vocab["genre"]), "mood": set(vocab["mood"]), "scene": set(vocab["scene"])}
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": build_prompt(track, lyrics_final, vocab)}],
        response_format={"type": "json_object"},
        temperature=0.3,
        max_tokens=400,
    )
    data = json.loads(resp.choices[0].message.content)
    is_inst = track.get("kind") != "vocal"
    lang = str(data.get("language") or "").strip().lower()
    if is_inst:
        lang = "zxx"
    elif len(lang) > 5 or not lang:
        lang = track.get("lang") if len(str(track.get("lang") or "")) == 2 else "und"
    summary = str(data.get("summary") or "").strip()[:255]
    return {
        "genres": clean_tags(data.get("genres"), allowed["genre"]),
        "moods": clean_tags(data.get("moods"), allowed["mood"]),
        "scenes": clean_tags(data.get("scenes"), allowed["scene"]),
        "summary": summary,
        "language": lang,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek tagging")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if not config.DEEPSEEK_API_KEY:
        log.error("DEEPSEEK_API_KEY not set in .env")
        return 2

    selected = common.load_json(config.SELECTED_FILE)
    lyrics_final = common.load_json(config.LYRICS_FINAL_FILE, default={}) or {}
    if not selected or not lyrics_final:
        log.error("need selected.json and lyrics_final.json (run 05 first).")
        return 2
    vocab = load_vocab()

    existing = common.load_json(config.TAGS_FILE, default={}) or {}
    todo = [t for t in selected if str(t["id"]) not in existing]
    if args.limit:
        todo = todo[: args.limit]
    log.info("tagging %d tracks (%d already cached)", len(todo), len(existing))

    client = OpenAI(api_key=config.DEEPSEEK_API_KEY, base_url=config.DEEPSEEK_BASE_URL)
    out = dict(existing)

    def work(track):
        tid = str(track["id"])
        rec = tag_one(client, config.DEEPSEEK_CHAT_MODEL, track, lyrics_final, vocab)
        common.save_json(config.CACHE_DIR / "deepseek" / f"{tid}.json", rec)
        return tid, rec

    ok = fail = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(work, t): t for t in todo}
        for fut in tqdm(as_completed(futs), total=len(futs), desc="tagging", unit="song"):
            t = futs[fut]
            try:
                tid, rec = fut.result()
                out[tid] = rec
                ok += 1
            except Exception as e:
                log.warning("tagging failed %s: %s", t.get("id"), str(e)[:120])
                fail += 1
            if (ok + fail) % 25 == 0:
                common.save_json(config.TAGS_FILE, out)  # periodic checkpoint

    common.save_json(config.TAGS_FILE, out)
    log.info("tagged ok=%d failed=%d -> %s", ok, fail, config.TAGS_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
