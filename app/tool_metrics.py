"""
工具调用可观测性 —— 成功率 / 降级率 / 重试率 / 耗时 / 缓存命中率。

为什么需要它：
    Agent 系统的可靠性不取决于「能不能跑通一次」，而取决于
    「工具挂了怎么办、多久恢复、降级到哪一层」。本模块把
    app/crew.py 里已有的重试 + fallback 链变成可量化的指标：

      - ok            一次调用成功
      - ok_retry      重试后成功
      - ok_fallback   降级到备用工具后成功
      - failed        原工具 + 全部 fallback 均失败

    只有 ok 算「干净成功」；ok_retry / ok_fallback 说明该工具
    存在稳定性问题。success_rate = (1 - failed) / total 是
    「最终可用率」，clean_rate = ok / total 是「一次成功率」，
    两者差距越大说明链路越依赖容错。

线程安全：Agent 并行执行，所有状态在锁内更新。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Optional

# 最终状态
OK = "ok"
OK_RETRY = "ok_retry"
OK_FALLBACK = "ok_fallback"
FAILED = "failed"
# 终局结论：工具给出了确定答案（如"知识库未覆盖该目的地"），
# 不是执行故障，重试/降级都无法改变结论，故单列一档。
# 计入可用性（不算失败），但不计入 clean_rate（不算"一次成功"）。
TERMINAL = "terminal"

# 原始（单次）调用结果
RAW_OK = "raw_ok"
RAW_FAIL = "raw_fail"

_FINAL_STATUSES = (OK, OK_RETRY, OK_FALLBACK, FAILED, TERMINAL)

_STATUS_LABEL = {
    OK: "一次成功",
    OK_RETRY: "重试后成功",
    OK_FALLBACK: "降级后成功",
    FAILED: "全链路失败",
    TERMINAL: "终局结论",
}


@dataclass
class ToolCallRecord:
    """一次「逻辑工具调用」的完整记录（含重试与降级）。"""

    tool: str
    status: str
    elapsed_ms: int
    retries: int = 0
    fallback_to: str = ""
    cached: bool = False
    error: str = ""
    ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = time.time()


class ToolMetrics:
    """线程安全的工具调用指标收集器。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: list[ToolCallRecord] = []
        # 原始调用（不含重试/降级展开），用于算 fallback 触发前的失败率
        self._raw_ok = 0
        self._raw_total = 0
        self._raw_by_tool: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [ok, total]
        self._raw_ms_by_tool: dict[str, list[int]] = defaultdict(list)

    # ── 记录 ────────────────────────────────────────────

    def record_raw(self, tool: str, success: bool, elapsed_ms: int) -> None:
        """记录一次真实执行的原始调用（重试/降级各算一次）。"""
        with self._lock:
            self._raw_total += 1
            self._raw_by_tool[tool][1] += 1
            self._raw_ms_by_tool[tool].append(max(0, int(elapsed_ms)))
            if success:
                self._raw_ok += 1
                self._raw_by_tool[tool][0] += 1

    def record(self, record: ToolCallRecord) -> None:
        """记录一次逻辑调用（工具 + 重试 + 降级的最终结果）。"""
        with self._lock:
            self._records.append(record)

    # ── 聚合 ────────────────────────────────────────────

    def by_tool(self) -> dict[str, dict]:
        """按工具聚合最终状态。"""
        with self._lock:
            buckets: dict[str, dict] = {}
            for r in self._records:
                b = buckets.setdefault(
                    r.tool,
                    {"calls": 0, OK: 0, OK_RETRY: 0, OK_FALLBACK: 0, FAILED: 0,
                     "cached": 0, "retries": 0, "ms": [], "fallbacks": []},
                )
                b["calls"] += 1
                if r.status in _FINAL_STATUSES:
                    b[r.status] += 1
                if r.cached:
                    b["cached"] += 1
                b["retries"] += r.retries
                b["ms"].append(r.elapsed_ms)
                if r.fallback_to:
                    b["fallbacks"].append(r.fallback_to)

            out: dict[str, dict] = {}
            for tool, b in buckets.items():
                n = b["calls"]
                failed = b[FAILED]
                ms = sorted(b["ms"])
                out[tool] = {
                    "calls": n,
                    "ok": b[OK],
                    "ok_retry": b[OK_RETRY],
                    "ok_fallback": b[OK_FALLBACK],
                    "failed": failed,
                    # 最终可用率：只要没走完全失败就算可用
                    "availability": round((n - failed) / n * 100, 1) if n else 0.0,
                    # 一次成功率：不需要重试也不需要降级
                    "clean_rate": round(b[OK] / n * 100, 1) if n else 0.0,
                    # 容错依赖度：越高说明该工具越不稳
                    "recovery_rate": round((b[OK_RETRY] + b[OK_FALLBACK]) / n * 100, 1) if n else 0.0,
                    "retries": b["retries"],
                    "cached": b["cached"],
                    "p50_ms": ms[len(ms) // 2] if ms else 0,
                    "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else 0,
                }
            return dict(sorted(out.items()))

    def _raw_rate(self) -> float:
        """原始（单次真实执行）成功率，可重入锁内调用。"""
        with self._lock:
            return round(self._raw_ok / self._raw_total * 100, 1) if self._raw_total else 0.0

    def summary(self) -> dict:
        """全局汇总。"""
        with self._lock:
            n = len(self._records)
            if not n:
                # 只有原始执行记录（例如工具被直接调用、未走 recovery）时，
                # 仍要如实反映原始成功率，不能因为没走完整链路就报 0。
                return {
                    "calls": 0, "ok": 0, "ok_retry": 0, "ok_fallback": 0, "failed": 0,
                    "terminal": 0,
                    "availability": 0.0, "clean_rate": 0.0, "recovery_rate": 0.0,
                    "retries": 0, "cached": 0,
                    "raw_calls": self._raw_total,
                    "raw_success_rate": self._raw_rate(),
                    "p50_ms": 0, "p95_ms": 0,
                }
            status = {s: sum(1 for r in self._records if r.status == s) for s in _FINAL_STATUSES}
            ms = sorted(r.elapsed_ms for r in self._records)
            actual = n - status[TERMINAL]  # 真实执行次数（终局结论是"问过了"，不算执行）
            return {
                "calls": n,
                "ok": status[OK],
                "ok_retry": status[OK_RETRY],
                "ok_fallback": status[OK_FALLBACK],
                "failed": status[FAILED],
                "terminal": status[TERMINAL],
                # availability 用真实执行次数做分母：终局结论不是故障，不该拉低可用性
                "availability": round((actual - status[FAILED]) / actual * 100, 1) if actual else 0.0,
                "clean_rate": round(status[OK] / actual * 100, 1) if actual else 0.0,
                "recovery_rate": round((status[OK_RETRY] + status[OK_FALLBACK]) / actual * 100, 1) if actual else 0.0,
                "retries": sum(r.retries for r in self._records),
                "cached": sum(1 for r in self._records if r.cached),
                "raw_calls": self._raw_total,
                "raw_success_rate": self._raw_rate(),
                "p50_ms": ms[len(ms) // 2],
                "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))],
            }

    def records(self) -> list[dict]:
        with self._lock:
            return [asdict(r) for r in self._records]

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._raw_ok = 0
            self._raw_total = 0
            self._raw_by_tool.clear()
            self._raw_ms_by_tool.clear()


_global_metrics = ToolMetrics()


def get_tool_metrics() -> ToolMetrics:
    return _global_metrics


def status_label(status: str) -> str:
    return _STATUS_LABEL.get(status, status)


def render_markdown(metrics: Optional[ToolMetrics] = None) -> str:
    """把指标渲染成 markdown 表格，供评测报告引用。"""
    m = metrics or _global_metrics
    s = m.summary()
    if not s["calls"]:
        return "_暂无工具调用记录。_"

    lines = [
        "| 工具 | 调用 | 一次成功 | 重试后成功 | 降级后成功 | 失败 | 最终可用率 | 容错依赖度 | P50 | P95 |",
        "|------|------|----------|------------|------------|------|------------|------------|-----|-----|",
    ]
    for tool, b in m.by_tool().items():
        lines.append(
            f"| `{tool}` | {b['calls']} | {b['ok']} | {b['ok_retry']} | {b['ok_fallback']} | "
            f"{b['failed']} | **{b['availability']}%** | {b['recovery_rate']}% | "
            f"{b['p50_ms']}ms | {b['p95_ms']}ms |"
        )
    lines.extend([
        "",
        f"- 逻辑调用 {s['calls']} 次，最终可用率 **{s['availability']}%**，"
        f"一次成功率 {s['clean_rate']}%，容错依赖度 {s['recovery_rate']}%",
        f"- 原始执行 {s['raw_calls']} 次（含重试/降级展开），原始成功率 {s['raw_success_rate']}%",
        f"- 累计重试 {s['retries']} 次，缓存命中 {s['cached']} 次",
        f"- 端到端耗时 P50 {s['p50_ms']}ms / P95 {s['p95_ms']}ms",
    ])
    return "\n".join(lines)
