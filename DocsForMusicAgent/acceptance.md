# 验收清单（对照 featureToDo.md 六阶段）

> 2026-10-13。逐阶段列出：交付物在哪、怎么验证、证据是什么。评审照着跑即可。

## 0. 评测集（阶段 3 前置）
- 交付：`agent/eval/golden_dev.jsonl`(159)、`golden_test.jsonl`(74)、`golden_holdout_f1.jsonl`(40，F1 用，**只跑一次已跑**)、`intent_labels.jsonl`(50)、`REVIEW.md`
- 验证：`python -m agent.eval.run_eval --file golden_test.jsonl --methods agent,b0,b1,b2,b3`；F1 留出集：`--file golden_holdout_f1.jsonl --methods agent`
- 证据：三类合并 Hit@5 = 1.00（dev/test），见 `phase3_report.md`；dev 由 109 增至 159（F1 新增冷门歌词子类 30 条）

## 1. 数据底座
- 交付：`data_pipeline/{00..08}*.py`、`data/selected.json`、`data/index/`(bge-m3)、`db/{schema,seed,check}_v1.sql`
- 验证：`python data_pipeline/tools/sql_selftest.py`（16/16）；`curl /stream/{id}`（302→Jamendo→音频）；`python data_pipeline/08_*.py` 产出向量库
- 证据：`phase12_report.md`、`db/DELIVERY_v1.md`、`data/index/index_meta.json`

## 2. 工具层
- 交付：`agent/tools/*`（8 工具）、`agent/registry.py`、`agent/app.py`(`/tool/{name}`)
- 验证：`python -m agent.cli list`；`python -m pytest agent/tests -q`（22 passed）；`python -m agent.eval.eval_tools`（歌词混合 Top-5 0.94、标签一致性 1.0）
- 证据：`phase12_report.md`、`agent/README.md`

## 3. Agent 核心（单轮）
- 交付：`agent/core/{intent,recall,rank,verify,respond,pipeline}.py`、`POST /agent/search`
- 验证：`python -m agent.eval.run_eval ...`（成绩表）；`python -m agent.eval.run_ablation`（消融）；`python -m agent.eval.diagnose_stages`（逐条定位）
- 证据：`phase3_report.md`（成绩表+消融+局限）、`phase3_traces.md`（10 条 trace）、`phase3loop.md`（迭代）
- **F1（曲库外歌词拒答）已终结，带未达标项**：最终固化 `LYRIC_MIN_CONF=0.75` + `LYRIC_SOURCE_ENABLED=False`（「认出处」方案已实现，dev 成立、留出集失效故默认关闭）。固化数字 dev/留出集：点名 1.00、知名曲库外 0.70/0.70（线 0.90 ✗）、冷门 0.633/0.80、库内歌词 Hit@5 0.88/0.85（留出集线 0.88 ✗）、库内误认 0/0。**未达标项如实留档**，见 `phase3loop.md` 末节。

## 4. 多轮与记忆
- 交付：`agent/core/{chat,session}.py`、`POST /agent/chat`
- 验证：`python -m agent.core`... 或用 `agent.cli`/`pytest agent/tests`（含 `test_chat.py`）；示例：`来点深夜伤感的歌` → `换一首`（不重合）→ `更欢快一点`（换标签）
- **已端到端复测（2026-10-14，经 Koa SSE 实调）**：`有首歌歌词是 I search for the strength` → `换一首`（5 首、与首轮 0 重合）→ `更欢快一点`（5 首）→ `不要伤感`（5 首）；`Coldplay 的 Yellow` 仍正确拒答。**该轮复测发现并修复了 F7**（歌词会话的续轮原本全部返回 0 首，见 `phase4_report.md` §2.1）。
- 证据：`phase4_report.md`

## 5. 接入 App
- 交付：`openKoa/routes|controller`(`/agent/chat` SSE+JWT)、`myMusic/.../pages/AiSearch.ets`、`main_pages.json`、`Index.ets` 入口
- 验证（后端已实测）：`uvicorn agent.app:app --port 8000` + `node openKoa/bin/www` → `login` → `POST /agent/chat`（SSE `status→result`）
- ⚠️ 客户端需 **DevEco Studio** 编译/真机验证（本环境无）
- 证据：`phase5_report.md`

## 6. 评测、消融、收尾
- 交付：`phase3_report.md`（A vs B0–B3、消融）、`design_and_gaps.md`（取舍总清单）、README 更新
- 验证：`run_eval`（A test 1.00 vs B1 0.61 / B2 0.78）；`run_ablation`（意图解析贡献最大）；**性能**：25 条冷缓存实测 P95 5.00s（线 ≤6s ✅）、单条 2 次 LLM 调用 / 约 1400 token
- 证据：本文件 + `design_and_gaps.md`

## 怎么把整条链路跑起来
```bash
# 依赖（用带 torch 的解释器）
D:\apps\tools\Conda\python.exe -m pip install -r data_pipeline/requirements.txt   # 已装
# 1) 数据/DB/索引 已就绪（db/、data_pipeline/data/、mymusic 库）
# 2) Agent 服务
D:\apps\tools\Conda\python.exe -m uvicorn agent.app:app --port 8000
# 3) Koa 后端
node openKoa/bin/www            # :3000
# 4) 客户端：DevEco 打开 myMusic，装到设备/模拟器，首页点「AI 搜歌」
```

## 未验证 / 需你环境
- 鸿蒙客户端编译与真机（DevEco）。
- 语言归一的真实效果、mood/constraint 的全量代表性（见 `design_and_gaps.md`）。
- **并发下的延迟**（现测为单用户串行）、服务冷启动首条（约 45s，加载模型）。
- **F1 的两条未达标线**（知名拒答 0.70 < 0.90、库内留出集 0.85 < 0.88）——已终结并留档，不再迭代。
- **长期偏好无真实数据**（`user_plays` 0 行、Koa 不写播放记录）——**待你定夺是否补**（见 `architect_report.md` §5）。
