# 环境搭建与启动

从零把整套系统跑起来。**按顺序做，第 3 节的启动顺序有内存原因，别打乱。**

> 本文只讲"怎么跑"。架构与设计看 [`DocsForMusicAgent/README.md`](DocsForMusicAgent/README.md)。

---

## 0. 系统由什么组成

| 组件 | 目录 | 端口 | 语言 / 栈 |
|---|---|---|---|
| 鸿蒙客户端 | `myMusic/` | — | ArkTS（DevEco Studio） |
| 后端 API | `openKoa/` | **3000** | Node + Koa 2 + Sequelize |
| 搜歌 Agent | `agent/` | **8000** | Python + FastAPI |
| 推荐系统 | `recommend/` | — | Python + PyTorch（子进程调用） |
| 数据库 | `db/` | **3306** | MySQL 8 |

```
App ──HTTP──> Koa :3000 ──Sequelize──> MySQL :3306
                  │
                  ├── POST /agent/chat ──> Agent :8000
                  └── exec(python) ──────> recommend/recsys
```

---

## 1. 前置要求

| 项 | 版本（本项目实测） | 说明 |
|---|---|---|
| 操作系统 | Windows 11 | 路径示例按 Windows 写 |
| MySQL | 8.0.19 | 库名 `mymusic`，`root` / `123456` |
| Node.js | v24 | Koa 后端 |
| Python | 3.13 + **torch** | **必须用带 torch 的解释器**，见下 |
| DevEco Studio | 带 HarmonyOS SDK | 只有客户端需要 |

> **关键**：系统默认的 `python`（3.14）**没有 torch**。
> 本项目统一用 `D:\apps\tools\Conda\python.exe`。下文的 `$PY` 都指它。

```bash
PY="D:/apps/tools/Conda/python.exe"
$PY -c "import torch; print(torch.__version__)"   # 应能打印版本
```

---

## 2. 一次性准备

### 2.1 代码与环境变量

```bash
git clone <repo> HarmonyMusic
cd HarmonyMusic
cp .env.example .env
```

编辑 `.env`，**至少填这三项**：

| 键 | 说明 |
|---|---|
| `JAMENDO_CLIENT_ID` | 播放地址 302 跳转要用（https://devportal.jamendo.com 免费申请） |
| `DEEPSEEK_API_KEY` | 意图解析、写推荐理由 |
| `RECOMMEND_PYTHON` | **指向带 torch 的解释器**，如 `D:\apps\tools\Conda\python.exe` |

其余（`DB_*`、`AGENT_KEY`、超时）都有默认值。`.env` 已在 `.gitignore` 里，不会误提交。

### 2.2 Python 依赖

```bash
$PY -m pip install -r data_pipeline/requirements.txt   # 数据流水线
$PY -m pip install fastapi uvicorn openai sentence-transformers bm25s pymysql pyyaml python-dotenv
```

推荐系统额外需要 `torch`（已含在上面的环境里）。

### 2.3 数据库

```bash
mysql -uroot -p123456 -e "CREATE DATABASE IF NOT EXISTS mymusic DEFAULT CHARSET utf8mb4"
mysql -uroot -p123456 mymusic < db/schema_v1.sql     # 14 张表（每条 DROP，可重复导入）
mysql -uroot -p123456 mymusic < db/seed_v1.sql       # 500 首歌 + 标签 + 歌词 + 歌单
```

自检：

```bash
$PY -m data_pipeline.tools.sql_selftest    # 导入临时库跑 check_v1.sql，全部应返回期望值
```

预期：`songs=500`、`singers=328`、`song_lyric_lines=11148`、`song_lists=37`。

### 2.4 向量索引 ⚠️

**索引没有进版本库**（30 MB，`data_pipeline/data/` 被 gitignore）。两条路：

**A. 从已有环境拷贝（推荐）**

```bash
# 把这整个目录拷过来
data_pipeline/data/index/
├── index_meta.json
├── songs_v1/          embeddings.npy  ids.json  docs.json
└── lyric_lines_v1/    embeddings.npy  ids.json  meta.json
```

**B. 重新编码**

```bash
$PY data_pipeline/08_build_index.py
```

但 08 的输入（`selected.json` / `tags.json` / `lyrics_final.json`）同样不在版本库里——
要么从原环境拷 `data_pipeline/data/`，要么重跑 00–07 流水线（需要 Jamendo key + GPU 对齐）。
**没有索引，Agent 的语义检索和推荐系统都会退化为不可用。**

验证：

```bash
$PY -c "import numpy as np; e=np.load('data_pipeline/data/index/songs_v1/embeddings.npy'); print(e.shape, e.dtype)"
# 期望：(500, 1024) float32
```

### 2.5 推荐模型

```bash
cd recommend
$PY -m recsys.generate     # 200 合成用户 × 15 条 → user_plays（source='synthetic'）
$PY -m recsys.train        # → artifacts/two_tower_v2.pt
$PY -m recsys.evaluate     # 可选：留一法评测
cd ..
```

> 不训练也能跑：Koa 会退化成"热门兜底"，响应里 `meta.algorithm = popularity`。

---

## 3. 启动

### 3.1 先看内存 ⚠️

模拟器要 **4 GB**，Agent 加载 bge-m3（4.1 G 磁盘）+ 重排模型（2.2 G）也会占数 GB。
宿主机 16 GB 的情况下，**Agent 和模拟器同时抢内存会导致模拟器启动失败**：

```
The host computer's memory is ~1091 MB, which is less than 3
```

**正确顺序**：先起模拟器，再起 Agent。

### 3.2 数据库

确保 MySQL 服务在跑：

```bash
mysql -uroot -p123456 -e "select 1"
```

### 3.3 模拟器 + 客户端

**模拟器**（首次需要先确认镜像与实例存在，见 [`myMusic/RUN_ON_EMULATOR.md`](myMusic/RUN_ON_EMULATOR.md)）：

```bash
cd /d/HarmonyEmu
printf 'y\n' | "/d/apps/tools/arkstudio/DevEco Studio/tools/emulator/Emulator.exe" \
  -start MyMusicPhone \
  -instancePath "D:\HarmonyEmu\instance" \
  -imageRoot "D:\apps\tools\arkstudio\DevEco Studio\sdk"
```

等 `hdc list targets` 出现 `127.0.0.1:5555`。

**构建 + 签名 + 安装**：

```bash
bash myMusic/scripts/sign_and_install_emulator.sh --install
```

> 脚本自带本地签名链（hvigor 的签名要 DevEco 加密密码，命令行复现不了）。
> 若换机器，签名材料在 `~/.ohos/config/mymusic_debug`，可用 `SIGN_WORK` 覆盖。

**端口转发**（App 里写的是 `localhost:3000`，模拟器里的 localhost 不是宿主机）：

```bash
HDC="/d/apps/tools/arkstudio/DevEco Studio/sdk/default/openharmony/toolchains/hdc.exe"
"$HDC" rport tcp:3000 tcp:3000
```

### 3.4 Agent（:8000）

```bash
cd HarmonyMusic
$PY -m uvicorn agent.app:app --port 8000
```

启动后端口立刻可用，bge-m3 + 重排模型在后台预热，**首个请求最多等约 50 秒**。
Koa 的超时已放宽到 150 s，覆盖预热 + 一次 Run。

### 3.5 Koa（:3000）

```bash
cd openKoa
npm install          # 首次
node bin/www
```

### 3.6 客户端

模拟器里已经装好并自动启动。若没启动：

```bash
"$HDC" shell aa start -a EntryAbility -b com.example.mymusic01
```

---

## 4. 验证

### 4.1 三个健康检查

```bash
curl -s -o /dev/null -w "koa   %{http_code}\n" http://127.0.0.1:3000/getSwiper        # 200
curl -s -o /dev/null -w "agent %{http_code}\n" http://127.0.0.1:8000/docs             # 200
"$HDC" list targets                                                                    # 127.0.0.1:5555
```

### 4.2 端到端

```bash
$PY -m agent.e2e_smoke
# 期望：12/12 passed（直连 Agent、登录、SSE 多轮、拒答）
```

### 4.3 推荐链路

```bash
curl -s "http://127.0.0.1:3000/getRecommendSongs?userId=2&count=3" | head -c 400
```

看 `meta.algorithm`：
- `two-tower-v2` → 走模型（该用户有播放记录）
- `popularity` → 冷启动兜底（用户没有播放记录，或模型没训练）

### 4.4 App 里走一遍

首页加载 → 播放推荐卡片 → 我的 · 听歌记录里出现该歌 → 右上角「AI 搜歌」提问。
**AI 搜歌需要先登录**（`user1` / `123456`）。

---

## 5. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 模拟器启动即退出，日志有 `less than 3` | 内存不足 | 先停 Agent，起模拟器，再起 Agent（§3.1） |
| `/getRecommendSongs` 很慢（5–8 s） | 每次请求都新起 Python，`import torch` 就要 5 s | Koa 侧有 10 分钟缓存（`REC_CACHE_MS`），第二次起是毫秒级 |
| 推荐总是 `popularity` | 该用户没有播放记录，或模型未训练 | 跑 `recsys.generate` + `recsys.train`；或先听几首歌 |
| AI 搜歌第一次超时 | Agent 冷启动要加载 4 GB 模型 | 等预热完成（约 50 s）再试；Koa 超时已设 150 s |
| App 页面空白、请求 401 | 未登录，或 token 过期 | 我的 → 登录 |
| `MATCH ... AGAINST` 把 MySQL 搞崩 | 本机 8.0.19 的 ngram 全文索引 bug | 全项目已改用 `LIKE`；**不要**手动执行 MATCH |
| `git push` 报 `SSL_ERROR_SYSCALL` | 走 Clash 代理时 git 的 OpenSSL 握手失败 | `git config http.sslBackend schannel` |
| DevEco 里签名失败 | `build-profile.json5` 的签名路径是另一台机器的 | 用 `myMusic/scripts/sign_and_install_emulator.sh`，或在 DevEco 里重新配置签名 |

---

## 6. 目录速查

| 想改什么 | 去哪 |
|---|---|
| 意图解析 / 召回 / 排序 | `agent/core/` |
| Agent 的输出格式（songs/clarify/refusal） | `agent/agent_models.py` |
| Agent 的 Instructions | `agent/prompts/system.md` |
| 推荐模型 | `recommend/recsys/` |
| 后端接口 | `openKoa/controller/api.js`、`routes/index.js` |
| 客户端页面 | `myMusic/entry/src/main/ets/pages/` |
| 表结构 | `db/schema_v1.sql` |

---

## 7. 相关文档

| 文档 | 内容 |
|---|---|
| [`DocsForMusicAgent/README.md`](DocsForMusicAgent/README.md) | 设计文档总索引与阅读路径 |
| [`DocsForMusicAgent/agent.html`](DocsForMusicAgent/agent.html) | Agent 数据结构 + 真实 trace |
| [`recommend/recsys.html`](recommend/recsys.html) | 推荐系统的数据与模型 |
| [`myMusic/RUN_ON_EMULATOR.md`](myMusic/RUN_ON_EMULATOR.md) | 模拟器/签名细节 |
| [`CLAUDE.md`](CLAUDE.md) | 工程约定（给 AI / 新人的内部说明） |
