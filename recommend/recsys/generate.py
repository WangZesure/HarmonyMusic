"""Generate synthetic play records and write them into user_plays.

Run:  python -m recsys.generate      (from recommend/)
"""
from __future__ import annotations

import json
from collections import Counter

from . import config as C
from . import data as D


def main() -> int:
    songs, _vecs = D.load_catalog()
    print(f"曲库 {len(songs)} 首")

    plays, profiles = D.generate_synthetic_plays(songs)
    per_user = Counter(p["user_id"] for p in plays)
    comp = [p["play_duration"] / p["full_duration"] for p in plays if p["full_duration"]]
    liked = sum(p["is_liked"] for p in plays)

    print(f"合成用户 {len(per_user)} 个，播放 {len(plays)} 条")
    print(f"  每人播放数  min/median/max = "
          f"{min(per_user.values())}/{sorted(per_user.values())[len(per_user)//2]}/{max(per_user.values())}")
    print(f"  完成率均值  {sum(comp)/len(comp):.3f}")
    print(f"  is_liked    {liked} 条 ({liked/len(plays):.1%})")

    inserted, removed = D.write_plays(plays)
    print(f"写入 user_plays：新增 {inserted} 条，清理旧 synthetic {removed} 条")

    # 生成时的真实偏好，评测用（模型看不到）
    prof_path = C.ARTIFACT_DIR / "synthetic_profiles.json"
    prof_path.write_text(json.dumps({str(k): v for k, v in profiles.items()},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"真实偏好 -> {prof_path}")
    print(f"（用户 ID 从 {C.SYNTH_USER_BASE} 起，source='synthetic'，真实播放记录不受影响）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
