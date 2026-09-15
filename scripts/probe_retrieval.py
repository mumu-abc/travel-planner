"""
检索静默失败复现脚本
====================
用途：验证「查询知识库里没有的目的地时，检索器不会拒答，而是返回语义最接近的 k 条」。

对应缺陷记录：docs/缺陷记录_检索静默失败.md

运行：
    python scripts/probe_retrieval.py

预期输出（2026-09-16 实测）：
    查询「东京」      → top-5 全部命中东京，最高分 0.6779
    查询「梅州五华」  → top-5 命中 0 条，分散在吉隆坡/孟买/布宜诺斯艾利斯，最高分 0.5603
    → 两者最高分只差 0.12，说明绝对分数阈值切不开，必须用「目的地归属」判据。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from app.knowledge.destinations import DESTINATIONS  # noqa: E402
from app.tools.knowledge_search import search_knowledge  # noqa: E402

# 库内对照组 / 库外实验组
IN_DOMAIN = "东京"
OUT_DOMAIN = "梅州五华"

print("=" * 66)
print(f"知识库目的地（共 {len(DESTINATIONS)} 个）：")
print("、".join(sorted(DESTINATIONS.keys())))
print()

for probe in ("梅州", "五华", OUT_DOMAIN):
    hit = [d for d in DESTINATIONS if probe in d]
    print(f"库内是否含 '{probe}'：{hit if hit else '无'}")

print("=" * 66)

for q in (IN_DOMAIN, OUT_DOMAIN):
    print(f"\n查询：{q}")
    results = search_knowledge(query=q, top_k=5)
    if not results:
        print("  （无返回）")
        continue

    hits = sum(1 for r in results if r["destination"] in q or q in r["destination"])
    for r in results:
        print(
            f"  score={r['score']:<7} 目的地={r['destination']:<14} {r['text'][:50]}"
        )
    cities = sorted({r["destination"] for r in results})
    print(f"  → 命中 {hits}/{len(results)} 条，涉及 {len(cities)} 个城市：{'、'.join(cities)}")

print("\n" + "=" * 66)
print("判据：命中率（目的地归属集中度），而不是绝对分数阈值。")
