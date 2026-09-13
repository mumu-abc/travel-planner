"""LLM 用量粗计 — 便于评测报告估算 token / 成本（非精确计费）。"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


@dataclass
class UsageStats:
    llm_calls: int = 0
    approx_input_chars: int = 0
    approx_output_chars: int = 0
    tool_calls: int = 0
    web_search_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_llm(self, input_chars: int, output_chars: int) -> None:
        with self._lock:
            self.llm_calls += 1
            self.approx_input_chars += max(0, int(input_chars))
            self.approx_output_chars += max(0, int(output_chars))

    def add_tool(self, name: str) -> None:
        with self._lock:
            self.tool_calls += 1
            if name == "web_search":
                self.web_search_calls += 1

    def snapshot(self) -> dict:
        with self._lock:
            # 中英混合粗估：约 1.6 字符 ≈ 1 token（偏保守，便于横向对比）
            in_tok = int(self.approx_input_chars / 1.6)
            out_tok = int(self.approx_output_chars / 1.6)
            return {
                "llm_calls": self.llm_calls,
                "tool_calls": self.tool_calls,
                "web_search_calls": self.web_search_calls,
                "approx_input_tokens": in_tok,
                "approx_output_tokens": out_tok,
                "approx_total_tokens": in_tok + out_tok,
            }

    def reset(self) -> None:
        with self._lock:
            self.llm_calls = 0
            self.approx_input_chars = 0
            self.approx_output_chars = 0
            self.tool_calls = 0
            self.web_search_calls = 0


_global_usage = UsageStats()


def get_usage() -> UsageStats:
    return _global_usage


def estimate_cost_usd(
    input_tokens: int,
    output_tokens: int,
    in_per_mtok: float = 0.5,
    out_per_mtok: float = 1.5,
) -> float:
    """粗估美元成本；默认价可按你实际模型改。仅用于横向对比，不是账单。"""
    return (input_tokens / 1_000_000) * in_per_mtok + (output_tokens / 1_000_000) * out_per_mtok
