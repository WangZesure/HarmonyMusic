# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

Three independent modules that together form a music app, plus SQL to seed the database:

| Path | What it is | Stack |
|------|-----------|-------|
| `myMusic/` | Mobile client | HarmonyOS / ArkTS (ArkUI), DevEco Studio SDK 5.0.2(14) |
| `openKoa/` | Backend REST API | Node.js + Koa 2, Sequelize over MySQL, JWT |
| `recommend/` | AI recommendation engine (content two-tower, v2) | Python + PyTorch |
| `music.sql`, `database_init.sql` | Identical MySQL schema dumps for the `music` DB | MySQL 8 |
| `recommend/sql_scripts/mymusic/` | Seed data + the `user_plays` table used by the model | MySQL 8 |
| `archive/featureToDo.md` | Roadmap for a planned "搜歌 Agent" (semantic/multi-turn search). Not implemented. |

The modules are NOT wired together by a build system — they communicate over HTTP (`3000`) and over a child-process call into Python. There is no root-level package manager, no monorepo tooling.

## Commands

### Backend (`openKoa/`)
```bash
cd openKoa
npm install            # node_modules is not committed — required first
npm run dev            # nodemon bin/www (auto-reload)
npm start              # node bin/www
npm run prd            # pm2 start bin/www
```
- Serves on port `3000` (override with `PORT` env var). `npm test` is a stub that exits 1 — there are no JS tests.
- Requires a running MySQL. Connection config lives in `config/db.js` (database `mymusic`, user `root`, password `123456`, host `127.0.0.1`).

### Recommendation engine (`recommend/`)
**Active implementation is `recsys/` (v2, PyTorch).** `src/` is the abandoned TensorFlow v1 — read it only for history.
```bash
cd recommend
PY="D:/apps/tools/Conda/python.exe"   # torch lives here, NOT in the system python
$PY -m recsys.generate   # 200 synthetic users × 15 plays -> user_plays (source='synthetic', id>=100000)
$PY -m recsys.train      # -> artifacts/two_tower_v2.pt + meta_v2.json
$PY -m recsys.evaluate   # leave-one-out + profile-level vs popularity/tag-match/random
$PY scripts/api/simple_recommend.py <userId> <count>   # JSON on stdout — called by Koa
```
- The song tower consumes the **frozen bge-m3 vector** from `data_pipeline/data/index/songs_v1` (no ID embeddings),
  so cold start works; the user tower is the completion-weighted mean of the user's history vectors.
- `features.history_vector` must stay identical across train / evaluate / serve, or the model sees a different
  user representation at inference time.
- `artifacts/*.pt` is gitignored — rebuild with `recsys.train`. `artifacts/*.json` (meta / eval / profiles) are committed.
- Koa calls it via `RECOMMEND_PYTHON` (set in `.env`) and caches results for 10 min (`REC_CACHE_MS`),
  because `import torch` alone costs ~5s per process spawn.
- Legacy v1 (`src/`, `main.py`, `scripts/data_generation/`) trains on the OLD 10-song demo corpus and is not wired in.

### Client (`myMusic/`)
Build/run/test through **DevEco Studio** (there is no committed `hvigorw` wrapper). Open the `myMusic` directory as the project; unit tests live in `entry/src/test/` (run on a host) and `entry/src/ohosTest/` (instrumented, on device/emulator), both using `@ohos/hypium`. Run a single test by opening the `.test.ets` file in DevEco and using the gutter run action on the `it(...)` case. Lint config is `code-linter.json5` (security + performance rule sets).

## Architecture

### End-to-end request flow (the key thing to understand)
```
ArkTS app ──HTTP──> Koa (openKoa) ──Sequelize──> MySQL (mymusic)
                         │
                         └─ child_process.exec('python scripts/api/simple_recommend.py user count')
                                  │  (cwd = recommend/)
                                  └─> TensorFlow two-tower model ──> JSON on stdout
```
`openKoa/controller/api.js · getRecommendSongs` shells out to Python, scrapes the JSON blob out of stdout with the regex `/\{.*\}/s` (TensorFlow prints warnings that would otherwise break `JSON.parse`), then re-queries MySQL via Sequelize to hydrate full song records and attaches `recommendScore` / `reason`. On any failure it degrades to "popular songs" (first N by `id`) — see the `catch` block. The client's `getRecommendSongList` and `getRecommendSongs` both route here.

### Backend (`openKoa/`)
- `app.js` mounts all middleware and a single router; `routes/index.js` declares every endpoint (no path prefix, so the app is called as `/getSwiper`, `/login`, `/getRecommendSongs`, …).
- `controller/api.js` holds every handler; `model/*.js` are Sequelize table definitions, registered and associated in `model/index.js` (which also calls `sequelize.sync()` on boot). `model/index.js` is the single source of table relationships (singer↔song, song↔song_list via list_song, collect, comment).
- Auth: `utils/token.js` — JWT signed with the literal secret `'secret'` (24h). Authenticated routes wrap the handler in `verifyToken()`, which puts the decoded `{id, username}` on `ctx.user`. Passwords are stored md5-hashed (`controller.api.js · login`).
- Response shape is `{code, message, data}`. Most handlers use `code: 200`, but the collect endpoints use `code: 0` — don't assume a single success code. The client keys off the HTTP status plus `res.success`/`data`, not `code`.
- File uploads: `@koa/multer` to `public/uploads/` via `POST /uploads`; static files are served from `public/`.
- See `recommend/REPORT_v2.md` for the v2 rebuild (content two-tower); the old integration notes are in `recommend/archive/CHANGES.md`.

### Recommendation engine internals (v2)
- `recsys/model.py` — `ContentTwoTower`: song tower `Linear(1024→256)→ReLU→Dropout→Linear(256→64)`, same for the
  user tower; both L2-normalized, score = `5 × cos`, BCE. No ID embeddings anywhere.
- `recsys/data.py` — loads songs + tags + bge-m3 vectors, generates synthetic plays from each user's sampled
  taste profile (1–3 genres / 1–3 moods / 1–2 scenes, weighted by corpus frequency^0.75, 25% exploration).
- `recsys/features.py` — `history_vector()`: completion-weighted mean. **Train/eval/serve must share it.**
- `recsys/evaluate.py` — two protocols: leave-one-out (strict, single held-out play) and profile-level
  (relevance = songs matching the user's true profile ≥0.4). The profile-level metric is tag-defined, so the
  `tag-match` baseline is near-oracle on it — documented in `REPORT_v2.md §5.3`, not a model failure.
- `recsys/serve.py` — returns `{algorithm, reason, recommendations}`; `algorithm` is `two-tower-v2` or
  `popularity` (cold start). Koa surfaces it in `meta.algorithm`.

### Agent service (`agent/`)
A Python search Agent layered on the corpus + tools (`agent/README.md`,
`DocsForMusicAgent/agent_architecture_v6_official.html`).
Run it with an interpreter that has torch: `D:\apps\tools\Conda\python.exe` (see Gotchas).

**Current path (v6 single-agent Run):**
- `agent/core/agent_loop.py` — `Input → Model → Tools → … → finish / ask_user / text`;
  5 steps, 2 `search_songs`, 45s, 20k tokens; `on_event` callback for SSE progress.
- `agent/tools/agent_tools.py` — agent-facing tools: `search_songs`, `refine_candidates`,
  `song_detail`, `user_profile`, `remember_preference`, `ask_user`, `finish`.
- `agent/prompts/system.md` — Instructions (search routine, clarification, output rules).
- `agent/session_store.py` + `db/migrations/001_agent_sessions.sql` — MySQL
  `agent_sessions` / `user_preference`; key `{uid}:{session_id}`, `turn_id` idempotency,
  per-session lock (single worker).
- Output: `type = songs / clarify / refusal / text` + `degraded`.

**Still present (legacy / direct paths):**
- `agent/core/pipeline.py` — fixed `intent → recall → rank → verify → respond`;
  used by `/agent/search` and eval baselines. `_run(..., relax=False)` is the agent path.
- `agent/core/chat.py` / `session.py` — old regex multi-turn path (baseline only).
- `agent/tools/basic.py`, `personal.py`, `vector.py`, `bm25.py` — 8 underlying tools.
- `agent/eval/` — golden sets, `run_eval` B0–B3, `run_ablation`, `export_traces`,
  `diagnose_stages`, plus `build_agent_golden` / `run_agent_eval` (30 multi-turn cases).

**HTTP:** `POST /agent/chat` (v6 loop, non-streaming), `POST /agent/chat/stream`
(SSE `status` + final `result`; Koa proxies it), `POST /agent/search` (legacy
single-turn pipeline), `POST /tool/{name}` (registry). `AGENT_KEY` is optional;
when set, Koa sends `X-Agent-Key`. Model cache lives in `resources/hf_cache/`.

### Client (`myMusic/`)
- Entry page `pages/Index.ets` (hosts the 4 tabs: Home / LikeSongList / Comment / Mine). Routable pages are whitelisted in `entry/src/main/resources/base/profile/main_pages.json`; navigation uses `@ohos.router.pushUrl`.
- Networking is centralized: `pages/common/ConfigUtil.ets` holds `SERVER` / `SERVER_URL` (currently `http://localhost:3000`) and timeouts; `pages/common/HttpUtil.ets` exposes `HttpGet`/`HttpPost`, which auto-inject the `authorization` header pulled from Preferences; `pages/common/Api.ets` maps one exported function per backend endpoint. **To point the app at a non-local backend (device/emulator), change `ConfigUtil.SERVER` to the host LAN IP — `localhost` only works for the emulator.**
- Layering: `pages/services/*` (network orchestration), `pages/model/AppModel.ets` + `paramModel.ets` (types/params), `pages/widget/*` (reusable UI such as `SongItem`, `NavBar`), `pages/utils/*` (player + audio capture `AvPlayerUtils`/`PlayService`/`AudioCapturer`).
- Cross-component state uses `@StorageLink` + `PersistentStorage` for login/user info (`userInfo`, `isLogin`) and `@ohos.events.emitter` for player state updates (`EmitEventType.UPDATE_STATE`). The token is stored under preferences store `userinfoStore` key `token`.
- `EntryAbility` requests `ohos.permission.MICROPHONE` at startup (voice search); `INTERNET` is declared in `module.json5`.

## Database

- **`db/schema_v1.sql` is authoritative** (14 tables, replaces `music.sql`). Every
  table starts with `DROP TABLE`, so importing it rebuilds the whole DB. Import order:
  `schema_v1.sql` → `seed_v1.sql` → start Koa (Koa's `sequelize.sync()` no longer alters
  the schema; verify with `SHOW CREATE TABLE`).
- `db/seed_v1.sql` is GENERATED by `data_pipeline/07_load_mysql.py`; never edit by hand.
  It holds the 500-song corpus (IDs = Jamendo track/artist ids, `url` = `/stream/{id}`,
  no `client_id`), 2 admins + 2 test consumers + 3 swipers re-inserted from music.sql,
  and tag-generated playlists.
- `db/check_v1.sql`: every query returns 0 / its expected value. `tools/sql_selftest.py`
  imports into a temp DB (`mymusic_selftest`), runs the checks, then drops it.
- New tables (contract C2): `song_meta` (one row/song: duration, vocal, language, speed,
  lrc_source/quality, lyric_searchable), `song_tags` (genre/mood/scene, source jamendo|llm),
  `song_lyric_lines` (per-line ms; FULLTEXT ngram). `songs.lyric` and `song_lyric_lines`
  are both produced by `common.parse_lrc` so they cannot disagree.
- `user_plays` exists and currently holds ~3005 rows; the recommender uses them when present.
- Agent tables (v6): `db/migrations/001_agent_sessions.sql` creates `agent_sessions` and
  `user_preference`; the Agent lifespan runs the same DDL idempotently. They sit outside
  the 14 corpus tables and are not created by `schema_v1.sql`.
- `music.sql` / `database_init.sql` are the OLD schema+data and are superseded by `db/`.

## Gotchas
- `myMusic/build-profile.json5` ships with a signing config pointing at another machine's macOS paths (`/Users/ruanchaochao/.ohos/...`) and is currently modified in the working tree — it will not sign without reconfiguring signing in DevEco.
- The README is aspirational in places: it says the DB is `music` with `your_password`, and claims `npm test` and `pytest tests/` exist. Trust the code (`mymusic`/`123456`, no tests) over the README.
- Repo contains macOS zip artifacts (`.DS_Store`, `__MACOSX/`, `openKoa/public/uploads.zip`) and an empty stray dir `recommend/9ONdya6qyNaNR`.
- No `node_modules` (openKoa) or `models/` (recommend) are committed; both must be produced locally before the stack runs end to end.
- **Do NOT run `MATCH ... AGAINST` on the ngram FULLTEXT indexes** (`songs.name`,
  `singers.name`, `song_lyric_lines.text`). On the installed MySQL **8.0.19** that
  query aborts `mysqld` (`init_search@Item_func_match` → server crash, error
  `2003 connection refused` until the service is restarted as admin). All keyword/
  lyric search uses `LIKE` instead (`agent/db.py`). The indexes can be dropped —
  needs a schema change (ask first).
- The agent needs `bge-m3` + MySQL on the same host, so run it with an interpreter
  that has torch: `D:\apps\tools\Conda\python.exe` (NOT the default `python`, which is 3.14 without torch).
