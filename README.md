# HarmonyMusic · 智能音乐 App（搜歌 Agent + 推荐系统）

一个完整的音乐应用：**鸿蒙客户端 + Koa 后端 + 搜歌 Agent + 推荐系统**，曲库是自建的 500 首 Jamendo（CC 协议）歌曲，带逐句时间戳歌词。

> 设计与评测记录在 [`DocsForMusicAgent/`](DocsForMusicAgent/README.md)，**先看那个索引**。

---

## 架构

```
鸿蒙 App (myMusic/, ArkTS)
   │  HTTP :3000（JSON / SSE）
   ▼
Koa 后端 (openKoa/)  ── Sequelize ──►  MySQL mymusic（14 张表，500 首）
   │  /stream/:id → 302 跳 Jamendo
   │
   ├─ POST /agent/chat ──►  搜歌 Agent (agent/, FastAPI :8000)
   │                          DeepSeek + bge-m3 + bge-reranker + BM25
   │
   └─ exec(python) ──────►  推荐系统 (recommend/, PyTorch 内容双塔)
```

| 目录 | 是什么 | 技术栈 |
|---|---|---|
| `myMusic/` | 鸿蒙客户端（首页 / 我的歌单 / 评论 / 我的 + 播放页 + AI 搜歌） | HarmonyOS ArkTS |
| `openKoa/` | 唯一的对外 REST 入口，JWT 鉴权 | Koa 2 + Sequelize |
| `agent/` | 搜歌 Agent：意图 → 召回 → 排序 → 校验 → 回复 | FastAPI + PyTorch |
| `recommend/` | 内容双塔推荐（歌曲塔吃 bge-m3 向量） | PyTorch |
| `data_pipeline/` | 离线建库：Jamendo → 歌词对齐 → 打标签 → 入库 → 建索引 | Python |
| `db/` | `schema_v1.sql`（权威）+ `seed_v1.sql`（生成） | MySQL 8 |
| `DocsForMusicAgent/` | 设计、契约、评测、迭代记录 | — |

**模块之间没有构建系统耦合**，只通过 HTTP 和子进程通信，每个模块都能单独起、单独测。

---

## 快速启动

> 前置：MySQL 8 在 `127.0.0.1:3306`，库名 `mymusic`（`root` / `123456`）。
> Agent 和推荐系统需要 torch，用 `D:\apps\tools\Conda\python.exe`（系统 python 没有）。

```bash
# 1) 数据库（首次）
mysql -uroot -p < db/schema_v1.sql
mysql -uroot -p mymusic < db/seed_v1.sql

# 2) 搜歌 Agent（:8000）
D:\apps\tools\Conda\python.exe -m uvicorn agent.app:app --port 8000

# 3) Koa 后端（:3000）
cd openKoa && node bin/www

# 4) 鸿蒙客户端：DevEco 打开 myMusic/ 编译运行
#    模拟器详细步骤见 myMusic/RUN_ON_EMULATOR.md
```

`.env` 里需要 `JAMENDO_CLIENT_ID`、`DEEPSEEK_API_KEY`、`RECOMMEND_PYTHON`（指向带 torch 的解释器）。
模板见 `.env.example`。

**模拟器注意**：App 里写的是 `localhost:3000`，模拟器里的 localhost 不是宿主机，需要
`hdc rport tcp:3000 tcp:3000` 打通。

---

## 关键数字

**曲库**：500 首（328 位歌手）、11,148 行逐句歌词（6,823 行可检索）、5,510 个标签、37 个歌单。

**搜歌 Agent**（test 集，k=5）：

| | Agent | B1 整库塞 prompt | B2 单次向量 | B0 直接猜 |
|---|---|---|---|---|
| title+lyric+describe 合并 Hit@5 | **1.000** | 0.610 | 0.780 | ≈0 |
| 歌词片段 Hit@5 | **1.000** | 0.125 | 0.625 | 0.042 |
| 曲库没有时拒答 | **1.000** | 0.067 | 0 | — |

回归：`pytest agent/tests` **22 passed**、`python -m agent.e2e_smoke` **12/12 passed**。
**未达标项**：曲库外歌词拒答按子类拆开后，知名 0.70（目标 0.90）、冷门 0.633 —— 已终结，原因见
[`DocsForMusicAgent/phase3_report.md`](DocsForMusicAgent/phase3_report.md)。

**推荐系统**（留一法，200 合成用户 / 500 首）：Hit@50 **0.315**，对比热门 0.180、随机 0.100。
完整实验（含没跑赢的基线）见 [`recommend/REPORT_v2.md`](recommend/REPORT_v2.md)。

---

## 文档导航

| 想了解 | 去看 |
|---|---|
| 全部设计文档的索引与阅读顺序 | [`DocsForMusicAgent/README.md`](DocsForMusicAgent/README.md) |
| 冻结契约（ID / 表 / 词表 / 接口） | [`DocsForMusicAgent/contracts_v1.md`](DocsForMusicAgent/contracts_v1.md) |
| 设计取舍与已知短板 | [`DocsForMusicAgent/design_and_gaps.md`](DocsForMusicAgent/design_and_gaps.md) |
| 验收清单 | [`DocsForMusicAgent/acceptance.md`](DocsForMusicAgent/acceptance.md) |
| 架构图（可视化） | [`DocsForMusicAgent/architecture.html`](DocsForMusicAgent/architecture.html) |
| **Agent 数据结构 + 真实 trace** | [`DocsForMusicAgent/agent.html`](DocsForMusicAgent/agent.html) |
| 推荐系统实验 | [`recommend/REPORT_v2.md`](recommend/REPORT_v2.md) · 可视化 [`recommend/recsys.html`](recommend/recsys.html) |
| 模拟器跑通步骤 | [`myMusic/RUN_ON_EMULATOR.md`](myMusic/RUN_ON_EMULATOR.md) |
| 数据流水线 | [`data_pipeline/README.md`](data_pipeline/README.md) |
| Agent 服务 | [`agent/README.md`](agent/README.md) |
| 工程约定（给 AI/新人的说明） | [`CLAUDE.md`](CLAUDE.md) |

---

## 已知限制（摘要）

- 曲库全英文 / 纯音乐，无中文歌（Jamendo 的 `lang` 过滤在本 API 失效）。
- 推荐系统的训练数据是**合成的**，指标上限由合成分布决定，不等于真实效果。
- 客户端只在模拟器验证过，未在真机跑。
- `user_plays` 的真实播放记录还很少（个位数），长期偏好基本没数据。
- 详细清单见 [`DocsForMusicAgent/design_and_gaps.md`](DocsForMusicAgent/design_and_gaps.md)。
