"""
记忆检索离线评测 —— 量化「用户记忆」到底有没有用。

背景：
    app/memory.py 有 396 行实现（写入 / 向量索引 / 语义检索 / 偏好抽取），
    但从未被验证过。这个脚本用「带标准答案的查询集」回答一个问题：
    用户的偏好记忆，在新一次规划请求里能不能被正确召回来？

方法：
    1. 造 12 条记忆（8 条真实偏好 + 4 条无关噪声）
    2. 用 8 个**措辞完全不同**的查询去检索，每个查询标注唯一 gold 记忆
    3. 对比语义检索（FAISS + bge-small-zh）与字面 baseline（字符 2-gram Jaccard）
    4. 报 Recall@1 / Recall@3 / MRR

为什么查询措辞要和记忆不同：
    如果查询和记忆字面高度重合，关键词匹配也能做对，就测不出语义检索的价值。
    真实场景里用户说的是「成都怎么玩」，记忆里存的是「不吃辣」——这正是难点。

不污染真实数据：全程使用临时目录 + 独立 SQLite / FAISS 索引。
不调用 LLM：纯检索评测，API 配额耗尽也能跑。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 评测集：8 条偏好记忆（gold）+ 4 条无关噪声 ──────────────────

MEMORIES: list[dict] = [
    # ── gold 记忆：用户偏好（措辞口语化、简短）──
    {"content": "我不吃辣，一点辣椒都受不了", "type": "preference", "gold_key": "diet_nospicy"},
    {"content": "我是素食主义者，不吃任何肉类", "type": "preference", "gold_key": "diet_veg"},
    {"content": "预算比较紧，喜欢便宜的青旅和路边摊", "type": "preference", "gold_key": "budget_low"},
    {"content": "我不喜欢早起，行程最好都安排在下午", "type": "preference", "gold_key": "pace_late"},
    {"content": "出行主要靠公共交通，很少打车", "type": "preference", "gold_key": "transit"},
    {"content": "特别喜欢博物馆和历史遗迹", "type": "preference", "gold_key": "interest_history"},
    {"content": "讨厌人挤人的热门景点", "type": "preference", "gold_key": "avoid_crowd"},
    {"content": "带孩子出行，需要适合亲子的安排", "type": "preference", "gold_key": "family_kid"},
    # ── 噪声记忆：无关事实，用来检验检索的抗干扰能力 ──
    {"content": "东京的地铁系统非常复杂，有十几条线路", "type": "fact", "gold_key": None},
    {"content": "去年冬天大阪下了一场大雪", "type": "fact", "gold_key": None},
    {"content": "日元汇率最近波动比较大", "type": "fact", "gold_key": None},
    {"content": "护照还有三年才到期", "type": "fact", "gold_key": None},
]

# 查询刻意用「规划请求」的口吻，与记忆措辞几乎不重叠
QUERIES: list[dict] = [
    {"query": "帮我规划成都三天，餐饮方面有什么要注意的吗", "gold": "diet_nospicy"},
    {"query": "这次去曼谷，我吃什么比较合适", "gold": "diet_veg"},
    {"query": "只有两千块能不能玩得下来，住宿怎么选省钱", "gold": "budget_low"},
    {"query": "行程别排太满，上午我想休息", "gold": "pace_late"},
    {"query": "在城里移动一般怎么走比较划算", "gold": "transit"},
    {"query": "有哪些地方能了解当地古代文化", "gold": "interest_history"},
    {"query": "有没有冷门一点的去处，别去网红打卡点", "gold": "avoid_crowd"},
    {"query": "我们一家三口，小朋友六岁，适合去哪", "gold": "family_kid"},
]


# ── 指标（纯函数，便于单测）────────────────────────────────

def bigram_jaccard(a: str, b: str) -> float:
    """字符 2-gram Jaccard 相似度 —— 无监督字面匹配 baseline。"""
    def grams(s: str) -> set[str]:
        s = "".join(ch for ch in s if not ch.isspace())
        return {s[i:i + 2] for i in range(len(s) - 1)} or {s}
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def rank_of(ranked_ids: list[str], gold_id: str) -> int:
    """gold 在结果中的排名（1-based），未命中返回 0。"""
    for i, mid in enumerate(ranked_ids, start=1):
        if mid == gold_id:
            return i
    return 0


def recall_at_k(ranks: list[int], k: int) -> float:
    """Recall@k：排名 <= k 的命中占比。ranks 中 0 表示未命中。"""
    if not ranks:
        return 0.0
    hit = sum(1 for r in ranks if 0 < r <= k)
    return hit / len(ranks)


def mrr(ranks: list[int]) -> float:
    """平均倒数排名，未命中记 0 分。"""
    if not ranks:
        return 0.0
    return sum(1.0 / r if r > 0 else 0.0 for r in ranks) / len(ranks)


# ── 评测主流程 ────────────────────────────────────────────

def run_bench(top_k: int = 3, verbose: bool = True) -> dict:
    """在临时目录里跑完整评测，返回指标字典。"""
    import app.memory as memory
    from app.database import Database

    tmp = Path(tempfile.mkdtemp(prefix="memory_bench_"))
    db_path = tmp / "bench.db"
    index_file = tmp / "memory.faiss"

    orig_get_db, orig_index_path = memory._get_db, memory._index_path
    orig_index = memory._index

    try:
        bench_db = Database(str(db_path))
        memory._get_db = lambda: bench_db
        memory._index_path = lambda: index_file
        memory._index = None  # 强制在临时目录新建索引

        # 1) 写入记忆，记录 gold_key -> memory_id
        key_to_id: dict[str, str] = {}
        id_to_content: dict[str, str] = {}
        for m in MEMORIES:
            mid = memory.add_memory(
                content=m["content"],
                memory_type=m["type"],
                destination="",
                tags="bench",
            )
            id_to_content[mid] = m["content"]
            if m["gold_key"]:
                key_to_id[m["gold_key"]] = mid

        # 2) 语义检索
        sem_ranks: list[int] = []
        rows: list[dict] = []
        for q in QUERIES:
            gold_id = key_to_id[q["gold"]]
            hits = memory.retrieve_memories(q["query"], top_k=top_k)
            ranked = [h["id"] for h in hits]
            r = rank_of(ranked, gold_id)
            sem_ranks.append(r)
            rows.append({
                "query": q["query"],
                "gold": id_to_content[gold_id],
                "rank": r,
                "top1": (hits[0]["content"] if hits else "")[:28],
            })

        # 3) 字面 baseline（同样在全部记忆上排序）
        base_ranks: list[int] = []
        for q in QUERIES:
            gold_id = key_to_id[q["gold"]]
            scored = sorted(
                id_to_content.items(),
                key=lambda kv: bigram_jaccard(q["query"], kv[1]),
                reverse=True,
            )
            base_ranks.append(rank_of([mid for mid, _ in scored[:top_k * 3]], gold_id))

        result = {
            "n_memories": len(MEMORIES),
            "n_gold": len(QUERIES),
            "n_noise": sum(1 for m in MEMORIES if not m["gold_key"]),
            "top_k": top_k,
            "semantic": {
                "recall@1": round(recall_at_k(sem_ranks, 1) * 100, 1),
                "recall@3": round(recall_at_k(sem_ranks, 3) * 100, 1),
                "mrr": round(mrr(sem_ranks), 3),
                "ranks": sem_ranks,
                "misses": sum(1 for r in sem_ranks if r == 0),
            },
            "baseline": {
                "recall@1": round(recall_at_k(base_ranks, 1) * 100, 1),
                "recall@3": round(recall_at_k(base_ranks, 3) * 100, 1),
                "mrr": round(mrr(base_ranks), 3),
                "ranks": base_ranks,
                "misses": sum(1 for r in base_ranks if r == 0),
            },
            "rows": rows,
        }

        if verbose:
            print(f"\n记忆检索评测（{len(MEMORIES)} 条记忆 / {len(QUERIES)} 个查询，top_k={top_k}）")
            print("-" * 72)
            for r in rows:
                mark = f"第{r['rank']}位" if r["rank"] else "未召回"
                print(f"  {mark:<6} | {r['query'][:22]:<24} | gold: {r['gold'][:16]}")
            print("-" * 72)
            s, b = result["semantic"], result["baseline"]
            print(f"  语义检索  Recall@1 {s['recall@1']}%  Recall@3 {s['recall@3']}%  MRR {s['mrr']}")
            print(f"  字面基线  Recall@1 {b['recall@1']}%  Recall@3 {b['recall@3']}%  MRR {b['mrr']}")
            print(f"  语义相对基线 MRR 提升：{(s['mrr'] - b['mrr']) / b['mrr'] * 100:.0f}%" if b["mrr"] else "")

        return result
    finally:
        memory._get_db = orig_get_db
        memory._index_path = orig_index_path
        memory._index = orig_index


def render_markdown(result: dict) -> str:
    s, b = result["semantic"], result["baseline"]
    lines = [
        "## 记忆检索命中率",
        "",
        f"- 语料：{result['n_memories']} 条记忆（{result['n_gold']} 条用户偏好 + {result['n_noise']} 条无关噪声）",
        f"- 查询：{result['n_gold']} 条**措辞与记忆不重合**的规划请求，每条标注唯一 gold",
        "",
        "| 方法 | Recall@1 | Recall@3 | MRR | 未召回 |",
        "|------|----------|----------|-----|--------|",
        f"| 语义检索（FAISS + bge-small-zh） | **{s['recall@1']}%** | **{s['recall@3']}%** | "
        f"**{s['mrr']}** | {s['misses']} |",
        f"| 字面基线（字符 2-gram Jaccard） | {b['recall@1']}% | {b['recall@3']}% | "
        f"{b['mrr']} | {b['misses']} |",
        "",
        "### 逐条明细",
        "",
        "| 查询 | 应召回的记忆 | 语义排名 | 基线排名 |",
        "|------|--------------|----------|----------|",
    ]
    for r, br in zip(result["rows"], b["ranks"]):
        lines.append(
            f"| {r['query'][:26]} | {r['gold'][:18]} | "
            f"{r['rank'] or '未召回'} | {br or '未召回'} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="记忆检索离线评测")
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--out", default="eval/report_memory.md")
    args = ap.parse_args()

    result = run_bench(top_k=args.top_k)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# 记忆检索评测报告\n\n"
        + render_markdown(result)
        + "\n\n> 复现：`python -m eval.memory_bench --top-k 3`\n",
        encoding="utf-8",
    )
    print(f"\n报告已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
