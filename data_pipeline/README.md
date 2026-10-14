# data_pipeline — 阶段 1 数据底座

Builds the 500-track corpus + vector index that the song-search Agent runs on.
Design lives in `DocsForMusicAgent/phase12.md`; this folder implements §1.1–1.7.

Scripts are run in numeric order and are resumable — each reads only the
previous step's output from disk, so any step can be rerun on its own.

```
00_check_stream.py   # 1.0  feasibility: is a Jamendo URL playable? (run FIRST)
01_fetch_jamendo.py  # 1.1  pull candidate pool   -> data/candidates.json
02_select.py         # 1.2  pick 500 + clean tags  -> data/selected.json
03_lrclib.py         # 1.3a lyrics: LRCLIB synced, else plain -> data/lyrics.json
04_align.py          # 1.3b GPU: demucs + forced-align/ASR -> lrc/ (ON SERVER)
05_qc_lrc.py         # 1.4  LRC quality gate        -> data/lyrics_final.json
06_llm_tagging.py    # 1.5  DeepSeek tags+summary+lang -> data/tags.json
07_load_mysql.py     # 1.6  seed SQL + lyric_lines.json -> sql/seed_v1.sql
08_build_index.py    # 1.7  bge-m3 vector index     -> index/ (ON SERVER)
```

The GPU steps (04, 08) are uploaded and run on the server via `tools/gpu.py`.
Fetch the results back with `tools/gpu.py get <remote-dir> <local-dir>`.

## SQL (contract C2)

```bash
mysql -u root -p mymusic < data_pipeline/sql/schema_v1.sql   # new tables
mysql -u root -p mymusic < data_pipeline/sql/seed_v1.sql     # 500-song corpus
# or:  python data_pipeline/07_load_mysql.py --apply
```

* `sql/schema_v1.sql` — `song_meta`, `song_tags`, `song_lyric_lines`. The existing
  `songs`/`singers` tables are reused; only their column *meaning* is fixed by C1/C5.
* `sql/seed_v1.sql` — GENERATED, do not edit. Replaces old songs/singers, IDs are
  Jamendo track/artist ids, `songs.url` is `/stream/{id}` and no `client_id` appears.
* `songs.lyric` and `song_lyric_lines` both come from `common.parse_lrc` (C2).


## Prerequisites you must provide

1. **Jamendo Client ID** — register a free non-commercial app at
   <https://devportal.jamendo.com> and copy the Client ID. *Only you can do this.*
2. **DeepSeek API key** — for step 06 and the agent.
3. **A GPU server** — for step 04 (Demucs + stable-ts). Local CPU is too slow for
   a full run at 500 tracks.

## GPU server (verified 2026-10, step 04 target)

An AutoDL container at `connect.westb.seetacloud.com:51474` (creds in `.env` as
`GPU_SSH` / `GPU_SSH_PASSWORD`):

- **GPU** RTX 3080 Ti 12 GB · driver 580 / CUDA 13.0 · 48 CPU · 440 GB RAM
- **Python** at `/root/miniconda3/bin/python` (3.12.3) — *not* on the non-login PATH
- **Disk** 30 G system + 50 G at `/root/autodl-tmp`
- **Network** GitHub ✓, Jamendo ✓, apt via Huawei mirror ✓,
  **HuggingFace ✗ (blocked)** — set `HF_ENDPOINT=https://hf-mirror.com` (reachable)
  before downloading any model; pip via the Tsinghua mirror.
- Not yet installed: `torch`, `demucs`, `stable-ts`, `ffmpeg`.

There is no `sshpass` locally; use a `paramiko` script (reads `.env`) to drive the
server non-interactively.


Put 1 and 2 in a repo-root `.env` (copy `.env.example`). `.env` is gitignored.

## Setup

```bash
pip install -r data_pipeline/requirements.txt          # fetching/tagging/DB
pip install -r data_pipeline/requirements-gpu.txt       # only on the GPU server (step 04)
cp .env.example .env                                     # then fill in the keys
```

## Run

```bash
python data_pipeline/00_check_stream.py    # feasibility gate — do not skip
python data_pipeline/01_fetch_jamendo.py   # ~2000 candidates (cached)
python data_pipeline/02_select.py          # 500 tracks + report
```

Everything lands under `data_pipeline/data/` (gitignored). Raw Jamendo responses
are cached in `data_pipeline/data/cache/jamendo/`, so re-running 01 is free and
won't consume API quota — pass `--no-cache` only when you deliberately want a refetch.

## Notes / corrections to the plan (found by probing the live API)

- **`lang` filter is broken** on this API tier — it returns `Internal Error` for
  every value (even `en`), so the plan's "pull Chinese tracks first via `lang=zh`"
  is not possible. The corpus is English/instrumental; Chinese requests will be
  matched later via Chinese tags + bge-m3's cross-lingual ability.
- **`audiodownload_allowed` is a returned field, not a filter.** Jamendo's
  `/tracks` API has no such query parameter, so 02 filters on it in code.
- **The `/tracks/file` stream URL 404s when a track forbids download.** That is
  why selection *requires* `audiodownload_allowed == true`.
- **Query shape matters.** `vocalinstrumental=vocal` *alone* returns tag-less
  tracks; combining it **with** a tag (`fuzzytags=happy`) returns fully-tagged
  tracks with real lyric coverage. 01 fans out over tags per vocal/instrumental.
- **Jamendo lyrics are sparse and dirty** (~56% of selected vocal tracks have
  real lyrics; the field is otherwise empty or just the artist name). 02 drops
  anything that fails a line/char threshold, so those tracks get lyrics from
  LRCLIB (03) or forced ASR on the GPU (04) instead.
- **Playback uses the redirect URL** `…/tracks/file/?client_id=…&id=…&action=stream`
  (302 → Jamendo storage; verified against the live API). Whether the HarmonyOS
  AVPlayer follows the redirect still needs one manual device test.

