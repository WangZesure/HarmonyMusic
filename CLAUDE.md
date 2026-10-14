# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

Three independent modules that together form a music app, plus SQL to seed the database:

| Path | What it is | Stack |
|------|-----------|-------|
| `myMusic/` | Mobile client | HarmonyOS / ArkTS (ArkUI), DevEco Studio SDK 5.0.2(14) |
| `openKoa/` | Backend REST API | Node.js + Koa 2, Sequelize over MySQL, JWT |
| `recommend/` | AI recommendation engine (two-tower neural net) | Python + TensorFlow 2.15 |
| `music.sql`, `database_init.sql` | Identical MySQL schema dumps for the `music` DB | MySQL 8 |
| `recommend/sql_scripts/mymusic/` | Seed data + the `user_plays` table used by the model | MySQL 8 |
| `featureToDo.md` | Roadmap for a planned "搜歌 Agent" (semantic/multi-turn search). Not implemented. |

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
```bash
cd recommend
pip install -r requirements.txt   # tensorflow==2.15.0, numpy, pandas, scikit-learn, pymysql
python main.py check              # verify dirs/files + DB row counts
python main.py generate           # regenerate data/mymusic_generated/*.json
python main.py train              # train -> models/mymusic_twin_tower.keras + mappings.npy
python main.py predict            # demo predictions for users 3/4/5
python main.py all                # generate + train + predict
python scripts/api/simple_recommend.py <userId> <count>   # JSON on stdout — called by Koa
```
- `python -m pytest tests/` is documented in the README but **no `tests/` directory exists**.
- `models/` is gitignored and absent — `predict` fails with `模型文件不存在` until you run `train`. `data/mymusic_processed/mappings.npy` and `interactions.npy` *are* committed.
- All DB access assumes `mymusic` / `root` / `123456` (hardcoded in `main.py check`, `src/utils/mymusic_data_loader.py`, `src/predict_mymusic.py`).

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
- See `recommend/CHANGES.md` for the change that replaced the home-page "recommended playlists" with AI single-song recommendations.

### Recommendation engine (`recommend/`)
- `src/train_mymusic.py` and `src/predict_mymusic.py` are the active scripts wired into `main.py`; the unsuffixed `src/train.py` / `src/predict.py` are earlier generic versions.
- Model (`build_mymusic_twin_tower_model`): user tower `Embedding → LSTM(128) → Dense(64)`, song tower `Embedding → Dense(64) → Flatten`, combined by a dot product. The custom `dot_product_func` Lambda and its `custom_objects` registration **must stay byte-identical between train and predict** or `load_model` fails.
- IDs are string keys `user_{id}` / `song_{id}` mapped to integer indices; the dict `{user_to_idx, song_to_idx}` is persisted with `np.save` to `data/mymusic_processed/mappings.npy`. Labels are `1` when play completion > 60% or `is_liked == 1`; negatives are random unplayed user-song pairs (same count as positives).
- Hyperparameters are duplicated in two places — `src/config.py` (`MyMusicConfig`) and again inline in `src/train_mymusic.py`. If you change one, change both.
- `get_song_info_from_db` imports `mysql.connector`, which is **not** in `requirements.txt` (it degrades gracefully inside a `try/except`; the real hydrating of song details normally happens in Koa instead).

### Agent service (`agent/`)
A Python search Agent layered on the corpus + tools (`agent/README.md`, `DocsForMusicAgent/phase3_report.md`).
Run it with an interpreter that has torch: `D:\apps\tools\Conda\python.exe` (see Gotchas).
- `agent/core/` — fixed pipeline `intent → recall → rank → verify → respond` (`pipeline.search`),
  DeepSeek client (`llm.py`), multi-turn chat + session state (`chat.py`, `session.py`), ablation switches (`ablate.py`).
- `agent/tools/` — the 8 read-only tools + `bm25.py` (in-memory BM25 replacing the crashing MySQL FULLTEXT).
- `agent/eval/` — golden sets, `run_eval`, baselines B0–B3, `run_ablation`, `export_traces`, `diagnose_stages`.
- HTTP: `POST /agent/search {query}` (single-turn), `POST /agent/chat {session_id,query}` (multi-turn;
  `换一首` excludes already-shown songs, `更X一点` edits the constraint), `POST /tool/{name}`. `uvicorn agent.app:app`.
- Model cache lives in `resources/hf_cache/` (`agent/config.py` sets `HF_HOME`/`HF_HUB_OFFLINE`).

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
- `user_plays` exists but is empty this round; the recommender falls back to popular songs.
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
