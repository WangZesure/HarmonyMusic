# 推荐系统

**当前版本：v2 · 内容双塔（PyTorch）**。完整实验记录见 [`REPORT_v2.md`](REPORT_v2.md)，可视化讲解见 [`recsys.html`](recsys.html)。

> v1（TensorFlow ID 双塔）已废弃，代码留在 `src/` 仅作历史参考。原因见 `REPORT_v2.md §1`。

---

## 它做什么

给一个用户，从 500 首曲库里排出他可能喜欢的歌。两种路径，**响应里会明确标注用了哪条**：

| 情况 | 算法 | `algorithm` 字段 |
|---|---|---|
| 用户有播放记录 | 内容双塔个性化 | `two-tower-v2` |
| 用户没有播放记录 | 热门兜底 | `popularity` |

不会把兜底伪装成个性化。

## 模型

```
歌曲塔： bge-m3 向量(1024, 冻结) → Linear(256) → ReLU → Dropout → Linear(64) → L2
用户塔： 历史歌曲向量的完成率加权平均(1024) → 同上 → L2
打分：   5 × cos(user, song)  →  BCEWithLogitsLoss
```

**没有 ID embedding**，所以：

- 新歌入库就有向量（来自文本卡片），冷启动天然可解；
- 新用户听几首就能被服务；
- 直接复用 `data_pipeline` 建好的 `songs_v1` 索引（500×1024）。

## 目录

```
recsys/
├── config.py     超参与路径（全部可用环境变量覆盖）
├── data.py       曲库/标签/向量加载、合成播放生成、入库
├── features.py   完成率加权的历史向量（训练/评测/推理共用，必须一致）
├── model.py      内容双塔
├── train.py      训练
├── evaluate.py   留一法 + 口味级 + 三个基线
├── generate.py   生成合成播放数据
└── serve.py      推理入口（Koa 调用）

artifacts/
├── two_tower_v2.pt          模型权重（gitignored，可重建）
├── meta_v2.json             训练元信息 + loss 曲线
├── eval_v2.json             完整评测结果
└── synthetic_profiles.json  合成用户的真实偏好（仅评测用）

src/                         ← v1 遗留（TF），不再使用
scripts/api/simple_recommend.py   ← Koa 的调用入口（已改为调 recsys.serve）
```

## 运行

```bash
cd recommend
PY="D:/apps/tools/Conda/python.exe"     # 需要 torch

$PY -m recsys.generate    # 生成 200 用户 × 15 条播放，写入 user_plays
$PY -m recsys.train       # 训练 → artifacts/two_tower_v2.pt
$PY -m recsys.evaluate    # 留一法 + 口味级评测 → artifacts/eval_v2.json

# 单用户推理
$PY scripts/api/simple_recommend.py 100000 6
```

环境变量：`REC_SEED` / `REC_N_USERS` / `REC_PLAYS_PER_USER` / `REC_EXPLORE_RATE` / `REC_EPOCHS`。

## 评测结果（留一法，200 用户 / 500 首）

| 方法 | Hit@10 | Hit@50 | NDCG@10 |
|---|---|---|---|
| **model** | 0.055 | **0.315** | 0.027 |
| tag-match | **0.065** | 0.170 | **0.031** |
| popularity | 0.045 | 0.180 | 0.020 |
| random | 0.005 | 0.100 | 0.002 |

模型明显优于热门和随机；**tag-match 在口味级指标上更强**——因为口味级相关集就是按标签重叠定义的，
标签启发式等于直接优化该指标。这个结论没有粉饰，详见 `REPORT_v2.md §5.3`。

## 与 Koa 的集成

```
Koa /getRecommendSongs?userId=&count=
  └─ exec("$RECOMMEND_PYTHON scripts/api/simple_recommend.py <userId> <count>")
       └─ recsys.serve.recommend()  →  {"success": true, "algorithm": ..., "recommendations": [...]}
```

- **解释器**：脚本需要 torch，系统 python 没有。在 `.env` 里设
  `RECOMMEND_PYTHON=D:\apps\tools\Conda\python.exe`。
- **缓存**：脚本每次 `import torch` 约 5s，Koa 侧对推荐结果做了 10 分钟 TTL 缓存
  （`REC_CACHE_MS` 可调，设 0 关闭）。冷启动 5.5s → 命中缓存 5ms。
- **Koa 侧返回**：`meta.algorithm` / `meta.reason` 会透出，热门兜底时 `reason` 写「热门推荐」而不是匹配度。

## 历史版本（v1，已废弃）

基于 TensorFlow 的 ID 双塔，`src/train_mymusic.py` / `src/predict_mymusic.py` 仍在仓库里，但：

- 训练数据是「100 用户 × **10 首歌**」，那 10 首是旧 demo 曲库的 ID，与现在 500 首对不上；
- 用户/歌曲都是 ID embedding，冷启动无解；
- 未使用 bge-m3 向量；
- 评测只在随机负样本上算 accuracy，没有意义。

旧的集成改动清单在 [`archive/CHANGES.md`](archive/CHANGES.md)。
