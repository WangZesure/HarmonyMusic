#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Koa 调用的推荐入口。

用法：  python scripts/api/simple_recommend.py <userId> [count]
输出：  {"success": true, "user_id": ..., "algorithm": ..., "recommendations": [...]}

必须用带 torch 的解释器跑（本机是 D:\\apps\\tools\\Conda\\python.exe）。
Koa 侧通过 RECOMMEND_PYTHON 环境变量指定，见 openKoa/controller/api.js。
"""
import json
import sys
from pathlib import Path

# cwd 是 recommend/，但直接跑脚本时 __file__ 也在 recommend/scripts/api/ 下，
# 显式把 recommend/ 加进来更稳
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "缺少用户ID参数"}, ensure_ascii=True))
        return 1

    try:
        user_id = int(sys.argv[1])
    except ValueError:
        print(json.dumps({"error": "用户ID必须是整数"}, ensure_ascii=True))
        return 1
    top_k = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    try:
        from recsys import serve

        result = serve.recommend(user_id, top_k)
        recs = [{"song_id": int(sid), "score": round(float(score), 6)}
                for sid, score in result["recommendations"]]
        print(json.dumps({
            "success": True,
            "user_id": user_id,
            "algorithm": result["algorithm"],
            "reason": result["reason"],
            "recommendations": recs,
            "count": len(recs),
        }, ensure_ascii=True))
        return 0
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": f"推荐生成失败: {type(e).__name__}: {e}"},
                         ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
