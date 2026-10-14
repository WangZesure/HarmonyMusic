"""1.7 Build the vector index (contract C6).

Model `BAAI/bge-m3` (dense, 1024-d, L2-normalized, cosine). Two numpy collections:

  index/songs_v1/        {embeddings.npy, ids.json, docs.json}
  index/lyric_lines_v1/  {embeddings.npy, ids.json, meta.json}   (searchable only)
  index/index_meta.json  model / template / contract version / counts

Song doc template `tpl_v1`:
    "{name} / {artist} / 风格:{genres} / 情绪:{moods} / 场景:{scenes} / {introduction} / {前4行歌词}"
Lyric-line doc: the line plus the following line (a 2-line window); id "{song_id}:{line_no}".

Standalone (uploaded to the GPU server): only needs `sentence-transformers` plus
the four input json files in --workdir.

    python 08_build_index.py --workdir /root/autodl-tmp/music [--batch 64]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")  # direct HF is blocked

MODEL_NAME = "BAAI/bge-m3"
TEMPLATE_VERSION = "tpl_v1"
CONTRACT_VERSION = "v1"
SONGS_COLLECTION = "songs_v1"
LINES_COLLECTION = "lyric_lines_v1"
LINES_PER_SONG_DOC = 4


def load_json(path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def build_song_doc(track: dict, tags: dict, first_lines: list[str]) -> str:
    j = lambda xs: "、".join(xs) if xs else "无"
    return (
        f"{track.get('name','')} / {track.get('artist_name','')} / "
        f"风格:{j(tags.get('genres'))} / 情绪:{j(tags.get('moods'))} / "
        f"场景:{j(tags.get('scenes'))} / {tags.get('summary','')} / "
        f"{' '.join(first_lines)}"
    ).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Build bge-m3 vector index")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    work = Path(args.workdir)
    selected = load_json(work / "selected.json")
    tags = load_json(work / "tags.json", default={}) or {}
    lyrics_final = load_json(work / "lyrics_final.json", default={}) or {}
    lines = load_json(work / "lyric_lines.json", default=[]) or []
    if not selected or not tags:
        print("ERROR: need selected.json and tags.json in", work)
        return 2

    # --- assemble documents ------------------------------------------------- #
    lines_by_song: dict[str, list[dict]] = {}
    for row in lines:
        lines_by_song.setdefault(str(row["song_id"]), []).append(row)
    for v in lines_by_song.values():
        v.sort(key=lambda r: r["line_no"])

    song_ids, song_docs = [], []
    for t in selected:
        sid = str(t["id"])
        tt = tags.get(sid, {})
        first = [r["text"] for r in lines_by_song.get(sid, [])][:LINES_PER_SONG_DOC]
        song_ids.append(sid)
        song_docs.append(build_song_doc(t, tt, first))

    line_ids, line_docs, line_meta = [], [], []
    for t in selected:
        sid = str(t["id"])
        if not (lyrics_final.get(sid) or {}).get("lyric_searchable"):
            continue
        rows = lines_by_song.get(sid, [])
        for i, r in enumerate(rows):
            window = r["text"] + (" " + rows[i + 1]["text"] if i + 1 < len(rows) else "")
            lid = f"{sid}:{r['line_no']}"
            line_ids.append(lid)
            line_docs.append(window)
            line_meta.append({"song_id": int(sid), "line_no": r["line_no"], "start_ms": r["start_ms"]})

    if args.limit:
        song_ids, song_docs = song_ids[: args.limit], song_docs[: args.limit]
        line_ids, line_docs, line_meta = line_ids[: args.limit], line_docs[: args.limit], line_meta[: args.limit]
    print(f"documents: songs={len(song_docs)} lyric_lines={len(line_docs)}")

    # --- encode ------------------------------------------------------------- #
    import numpy as np
    from sentence_transformers import SentenceTransformer
    print("loading", MODEL_NAME, "...")
    # NOTE: bge-m3 ships only pytorch_model.bin, and transformers refuses to load
    # a .bin with torch < 2.6 (CVE-2025-32434). The server must have torch >= 2.6.
    model = SentenceTransformer(MODEL_NAME)

    def encode(docs: list[str]) -> "np.ndarray":
        return model.encode(docs, batch_size=args.batch, normalize_embeddings=True,
                            convert_to_numpy=True, show_progress_bar=True).astype("float32")

    song_emb = encode(song_docs)
    line_emb = encode(line_docs) if line_docs else np.zeros((0, song_emb.shape[1]), "float32")

    # --- save --------------------------------------------------------------- #
    idx = work / "index"
    (idx / SONGS_COLLECTION).mkdir(parents=True, exist_ok=True)
    (idx / LINES_COLLECTION).mkdir(parents=True, exist_ok=True)
    np.save(idx / SONGS_COLLECTION / "embeddings.npy", song_emb)
    save_json(idx / SONGS_COLLECTION / "ids.json", song_ids)
    save_json(idx / SONGS_COLLECTION / "docs.json", song_docs)
    np.save(idx / LINES_COLLECTION / "embeddings.npy", line_emb)
    save_json(idx / LINES_COLLECTION / "ids.json", line_ids)
    save_json(idx / LINES_COLLECTION / "meta.json", line_meta)
    save_json(idx / "index_meta.json", {
        "model": MODEL_NAME, "dim": int(song_emb.shape[1]), "normalized": True,
        "metric": "cosine", "template": TEMPLATE_VERSION, "contract_version": CONTRACT_VERSION,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "collections": {SONGS_COLLECTION: len(song_ids), LINES_COLLECTION: len(line_ids)},
    })
    print(f"OK -> {idx}  songs={len(song_ids)} lines={len(line_ids)} dim={song_emb.shape[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
