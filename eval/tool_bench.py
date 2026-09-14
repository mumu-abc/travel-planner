"""
工具可靠性与缓存基准 —— 把「Agent 有多稳」变成可复现的数字。

回答三个 Agent 岗必问的问题：
    1. 工具调用成功率多少？（本地工具实测）
    2. 工具挂了会怎样？重试 / 降级的触发率是多少？（容错链路注入，确定性）
    3. 重复规划同一目的地，能省多少外部调用？（缓存命中率实测）

设计取舍：
    - 默认只跑**本地工具**（知识检索 / 路线 / 预算 / 景点详情），不依赖网络也
      不消耗 LLM 配额，任何人都能一键复现。网络工具用 --include-network 开启。
    - 容错链路用**故障注入**而非碰运气等真实故障：结果是确定的，可回归测试。
    - 缓存收益用「同一批参数跑两轮」实测：第一轮 miss、第二轮 hit，
      直观对应「用户第二次规划同一目的地」。

复现：`python -m eval.tool_bench --cities 10 --rounds 2`
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CITIES = ["东京", "巴黎", "曼谷", "成都", "首尔", "新加坡", "伦敦", "纽约", "清迈", "迪拜"]

# 本地工具（不联网、不烧 token）
LOCAL_CASES: list[tuple[str, dict]] = [
    ("search_knowledge", {"query": "必去景点", "destination": "{city}"}),
    ("search_attraction_context", {"query": "景点推荐", "destination": "{city}"}),
    ("optimize_route", {"destination": "{city}", "days": 3}),
    ("optimize_budget", {"destination": "{city}", "total_usd": 2000, "days": 3}),
]

# 网络工具（可能失败，失败本身也是真实信号，故默认关闭）
NETWORK_CASES: list[tuple[str, dict]] = [
    ("get_weather", {"destination": "{city}"}),
    ("get_exchange_rate", {"destination": "{city}", "amount_usd": 1000}),
]


def _fill(args: dict, city: str) -> dict:
    return {k: (v.replace("{city}", city) if isinstance(v, str) else v) for k, v in args.items()}


# ── Part A / C：真实工具实测 + 缓存收益 ───────────────────────

def bench_real_tools(cities: list[str], rounds: int, include_network: bool) -> dict:
    import app.crew as crew
    from app.tool_cache import configure, get_tool_cache
    from app.tool_metrics import get_tool_metrics

    configure(enabled=True, max_size=512)
    metrics = get_tool_metrics()
    metrics.reset()
    get_tool_cache().reset_stats()

    cases = list(LOCAL_CASES) + (NETWORK_CASES if include_network else [])
    total = 0
    ok = 0
    per_tool: dict[str, list[int]] = {}
    started = time.perf_counter()

    for _ in range(rounds):
        for city in cities:
            for tool, raw_args in cases:
                args = _fill(raw_args, city)
                res = crew._execute_tool(tool, args)
                total += 1
                bucket = per_tool.setdefault(tool, [0, 0])
                bucket[1] += 1
                if res.get("success"):
                    ok += 1
                    bucket[0] += 1

    elapsed = time.perf_counter() - started
    cache = get_tool_cache().stats()

    return {
        "cities": len(cities),
        "rounds": rounds,
        "tools": len(cases),
        "calls": total,
        "ok": ok,
        "success_rate": round(ok / total * 100, 1) if total else 0.0,
        "elapsed_sec": round(elapsed, 1),
        "per_tool": {t: {"ok": v[0], "calls": v[1],
                         "rate": round(v[0] / v[1] * 100, 1) if v[1] else 0.0}
                     for t, v in sorted(per_tool.items())},
        "cache": cache,
        "metrics": metrics.summary(),
    }


# ── Part B：容错链路故障注入（确定性）────────────────────────

def bench_fault_tolerance() -> dict:
    """注入四类故障，验证重试 / fallback 被正确触发且被正确统计。"""
    import app.crew as crew
    from app.tool_cache import configure
    from app.tool_metrics import get_tool_metrics

    configure(enabled=False)  # 注入测试不希望缓存干扰
    metrics = get_tool_metrics()
    metrics.reset()
    trace = crew.AgentTrace(name="bench", label="bench")

    orig_exec = crew._execute_tool
    orig_fb = dict(crew._TOOL_FALLBACKS)

    def _always_fail(name, args):
        return {"success": False, "text": "注入故障"}

    try:
        # 1) 一次成功
        crew._execute_tool = lambda n, a: {"success": True, "text": "ok"}
        crew._execute_tool_with_recovery("case_ok", {}, "bench", trace)

        # 2) 首次失败、重试成功
        seq = iter([{"success": False, "text": "e"}, {"success": True, "text": "ok"}])
        crew._execute_tool = lambda n, a: next(seq)
        crew._execute_tool_with_recovery("case_retry", {}, "bench", trace)

        # 3) 原工具与重试都失败，降级成功
        crew._TOOL_FALLBACKS["case_fallback"] = ["search_knowledge"]
        crew._execute_tool = lambda n, a: {"success": n == "search_knowledge", "text": "ok"}
        crew._execute_tool_with_recovery("case_fallback", {}, "bench", trace)

        # 4) 全链路失败
        crew._TOOL_FALLBACKS["case_dead"] = []
        crew._execute_tool = _always_fail
        crew._execute_tool_with_recovery("case_dead", {}, "bench", trace)
    finally:
        crew._execute_tool = orig_exec
        crew._TOOL_FALLBACKS.clear()
        crew._TOOL_FALLBACKS.update(orig_fb)

    s = metrics.summary()
    return {
        "injected": 4,
        "ok": s["ok"],
        "ok_retry": s["ok_retry"],
        "ok_fallback": s["ok_fallback"],
        "failed": s["failed"],
        "availability": s["availability"],
        "clean_rate": s["clean_rate"],
        "recovery_rate": s["recovery_rate"],
        "retries": s["retries"],
        "by_tool": metrics.by_tool(),
    }


# ── 报告 ────────────────────────────────────────────────

def render_markdown(real: dict, fault: dict) -> str:
    lines = [
        "## 工具调用可靠性（本地工具实测）",
        "",
        f"- 用例：{real['cities']} 个目的地 × {real['tools']} 个工具 × {real['rounds']} 轮 "
        f"= {real['calls']} 次调用",
        f"- 成功 {real['ok']}/{real['calls']}，成功率 **{real['success_rate']}%**，"
        f"总耗时 {real['elapsed_sec']}s",
        "",
        "| 工具 | 成功/调用 | 成功率 |",
        "|------|-----------|--------|",
    ]
    for tool, v in real["per_tool"].items():
        lines.append(f"| `{tool}` | {v['ok']}/{v['calls']} | **{v['rate']}%** |")

    c = real["cache"]
    saved_pct = round(c["saved_calls"] / c["lookups"] * 100, 1) if c["lookups"] else 0.0
    lines.extend([
        "",
        "## 工具缓存收益（重复规划同一目的地）",
        "",
        f"- 同一批参数跑 {real['rounds']} 轮：查询 {c['lookups']} 次，命中 {c['hits']} 次",
        f"- 命中率 **{c['hit_rate']}%**，节省真实外部调用 **{c['saved_calls']} 次（{saved_pct}%）**",
        "- TTL 分档：静态检索/路线/预算 24h，天气 30min，汇率与网络搜索 1h",
        "- 只缓存成功结果，失败不入库（避免把瞬时故障固化成持久故障）",
        "",
        "## 容错链路（故障注入，确定性验证）",
        "",
        f"- 注入 4 类故障：一次成功 {fault['ok']} · 重试后成功 {fault['ok_retry']} · "
        f"降级后成功 {fault['ok_fallback']} · 全链路失败 {fault['failed']}",
        f"- 最终可用率 **{fault['availability']}%**，一次成功率 {fault['clean_rate']}%，"
        f"容错依赖度 {fault['recovery_rate']}%",
        f"- 累计重试/降级 {fault['retries']} 次",
        "",
        "| 工具 | 调用 | 一次成功 | 重试后成功 | 降级后成功 | 失败 | 最终可用率 |",
        "|------|------|----------|------------|------------|------|------------|",
    ])
    for tool, b in fault["by_tool"].items():
        lines.append(
            f"| `{tool}` | {b['calls']} | {b['ok']} | {b['ok_retry']} | {b['ok_fallback']} | "
            f"{b['failed']} | **{b['availability']}%** |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="工具可靠性与缓存基准")
    ap.add_argument("--cities", type=int, default=10, help="目的地数量")
    ap.add_argument("--rounds", type=int, default=2, help="重复轮数（>1 才能测缓存）")
    ap.add_argument("--include-network", action="store_true", help="一并测网络工具（可能失败/较慢）")
    ap.add_argument("--out", default="eval/report_tools.md")
    args = ap.parse_args()

    cities = CITIES[: max(1, min(args.cities, len(CITIES)))]
    print(f"▶ 实测本地工具：{len(cities)} 城 × {args.rounds} 轮 …")
    real = bench_real_tools(cities, args.rounds, args.include_network)
    print(f"  成功率 {real['success_rate']}%  缓存命中率 {real['cache']['hit_rate']}%")

    print("▶ 容错链路故障注入 …")
    fault = bench_fault_tolerance()
    print(f"  最终可用率 {fault['availability']}%  容错依赖度 {fault['recovery_rate']}%")

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "# 工具可靠性与缓存基准报告\n\n"
        + render_markdown(real, fault)
        + "\n\n> 复现：`python -m eval.tool_bench --cities 10 --rounds 2`\n",
        encoding="utf-8",
    )
    print(f"\n报告已写入: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
