"""
工具可观测性测试 —— 指标、缓存、埋点。

全部 mock，不调用 LLM、不触碰真实记忆库与 FAISS 索引。
重点覆盖两条容易被写错的逻辑：
  1. 缓存命中不能被算成「真实执行成功」，否则缓存会掩盖工具的真实稳定性
  2. 埋点属于旁路观测，任何异常都不得影响生成主流程
"""

from __future__ import annotations

import time

import pytest

import app.crew as crew
from app import tool_cache as tc
from app import tool_metrics as tm


# ── 指标聚合 ────────────────────────────────────────────

class TestToolMetrics:
    def test_summary_counts_each_status(self):
        m = tm.ToolMetrics()
        m.record(tm.ToolCallRecord("a", tm.OK, 10))
        m.record(tm.ToolCallRecord("a", tm.OK_RETRY, 20, retries=1))
        m.record(tm.ToolCallRecord("a", tm.OK_FALLBACK, 30, fallback_to="b"))
        m.record(tm.ToolCallRecord("a", tm.FAILED, 40))

        s = m.summary()
        assert s["calls"] == 4
        assert (s["ok"], s["ok_retry"], s["ok_fallback"], s["failed"]) == (1, 1, 1, 1)
        # 4 次里 1 次全失败 → 最终可用率 75%
        assert s["availability"] == 75.0
        # 只有 1 次一次成功 → 一次成功率 25%
        assert s["clean_rate"] == 25.0
        # 重试 + 降级 = 2 次 → 容错依赖度 50%
        assert s["recovery_rate"] == 50.0

    def test_raw_success_rate_is_independent_of_fallback(self):
        """原始成功率只统计真实执行，不含降级后的补刀。"""
        m = tm.ToolMetrics()
        m.record_raw("a", False, 5)   # 原工具失败
        m.record_raw("b", True, 5)    # fallback 成功
        assert m.summary()["raw_calls"] == 2
        assert m.summary()["raw_success_rate"] == 50.0

    def test_empty_metrics_do_not_divide_by_zero(self):
        m = tm.ToolMetrics()
        assert m.summary()["calls"] == 0
        assert m.summary()["availability"] == 0.0
        assert m.by_tool() == {}

    def test_by_tool_percentiles(self):
        m = tm.ToolMetrics()
        for ms in (100, 200, 300, 400, 500):
            m.record(tm.ToolCallRecord("t", tm.OK, ms))
        b = m.by_tool()["t"]
        assert b["p50_ms"] == 300
        assert b["availability"] == 100.0


# ── 缓存 ────────────────────────────────────────────────

class TestToolCache:
    def test_hit_and_miss_accounting(self):
        c = tc.ToolCache(enabled=True)
        c.get("x:1")                      # miss
        c.set("x:1", {"success": True})
        c.get("x:1")                      # hit
        c.get("x:2")                      # miss
        s = c.stats()
        assert (s["hits"], s["misses"]) == (1, 2)
        assert s["hit_rate"] == round(1 / 3 * 100, 1)
        assert s["saved_calls"] == 1

    def test_disabled_cache_never_hits(self):
        c = tc.ToolCache(enabled=False)
        c.set("x:1", {"success": True})
        assert c.get("x:1") is None
        assert c.stats()["hits"] == 0

    def test_expired_entry_is_miss_and_purged(self):
        c = tc.ToolCache(enabled=True)
        c.set("x:1", {"success": True}, ttl=1)
        c._store["x:1"] = (time.time() - 1, {"success": True})  # 手动置为已过期
        assert c.get("x:1") is None
        assert c.stats()["expired"] == 1
        assert "x:1" not in c._store

    def test_lru_eviction_at_max_size(self):
        c = tc.ToolCache(max_size=2, enabled=True)
        c.set("a:1", 1)
        c.set("b:1", 2)
        c.set("c:1", 3)
        assert len(c._store) == 2
        assert c.get("a:1") is None  # 最早写入的被淘汰

    def test_cache_key_is_order_insensitive(self):
        assert tc.cache_key("t", {"a": 1, "b": 2}) == tc.cache_key("t", {"b": 2, "a": 1})

    def test_ttl_tiers_exist_for_realtime_tools(self):
        """实时工具 TTL 必须明显短于静态知识，否则会拿旧数据糊弄用户。"""
        assert tc.TTL_BY_TOOL["get_weather"] < tc.TTL_BY_TOOL["search_knowledge"]
        assert tc.TTL_BY_TOOL["get_exchange_rate"] < tc.TTL_BY_TOOL["optimize_route"]


# ── crew 埋点（故障注入，确定性）──────────────────────────

class TestCrewInstrumentation:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        metrics = tm.ToolMetrics()
        monkeypatch.setattr(crew, "get_tool_metrics", lambda: metrics)
        monkeypatch.setattr(crew, "_TOOL_FALLBACKS", {})
        tc.configure(enabled=False)
        self.metrics = metrics
        self.trace = crew.AgentTrace(name="t", label="T")
        yield
        tc.configure(enabled=False)

    def test_clean_success_recorded_as_ok(self, monkeypatch):
        monkeypatch.setattr(crew, "_execute_tool", lambda n, a: {"success": True, "text": "x"})
        crew._execute_tool_with_recovery("tool", {}, "a", self.trace)
        assert self.metrics.summary()["ok"] == 1
        assert self.metrics.records()[0]["status"] == tm.OK

    def test_retry_success_recorded_as_ok_retry(self, monkeypatch):
        seq = iter([{"success": False, "text": "e"}, {"success": True, "text": "x"}])
        monkeypatch.setattr(crew, "_execute_tool", lambda n, a: next(seq))
        crew._execute_tool_with_recovery("tool", {}, "a", self.trace)
        rec = self.metrics.records()[0]
        assert rec["status"] == tm.OK_RETRY
        assert rec["retries"] == 1

    def test_fallback_success_records_target_tool(self, monkeypatch):
        monkeypatch.setitem(crew._TOOL_FALLBACKS, "tool", ["backup"])
        monkeypatch.setattr(
            crew, "_execute_tool", lambda n, a: {"success": n == "backup", "text": "x"}
        )
        crew._execute_tool_with_recovery("tool", {}, "a", self.trace)
        rec = self.metrics.records()[0]
        assert rec["status"] == tm.OK_FALLBACK
        assert rec["fallback_to"] == "backup"

    def test_total_failure_recorded_as_failed(self, monkeypatch):
        monkeypatch.setattr(crew, "_execute_tool", lambda n, a: {"success": False, "text": "boom"})
        out = crew._execute_tool_with_recovery("tool", {}, "a", self.trace)
        assert "失败" in out  # 仍返回可继续生成的降级提示
        assert self.metrics.records()[0]["status"] == tm.FAILED

    def test_metrics_failure_never_breaks_pipeline(self, monkeypatch):
        """埋点是旁路：指标模块炸了也必须照常返回结果。"""
        monkeypatch.setattr(crew, "_execute_tool", lambda n, a: {"success": True, "text": "x"})
        monkeypatch.setattr(
            crew, "get_tool_metrics",
            lambda: (_ for _ in ()).throw(RuntimeError("metrics down")),
        )
        out = crew._execute_tool_with_recovery("tool", {}, "a", self.trace)
        assert out == "x"

    def test_cache_hit_is_not_counted_as_real_execution(self, monkeypatch):
        """缓存命中不得污染原始成功率，否则缓存会掩盖工具真实稳定性。"""
        calls = []

        def fake_exec(name, args):
            calls.append(name)
            res = {"success": True, "text": "x"}
            res["_cached"] = len(calls) > 1  # 第二次起视为命中缓存
            return res

        monkeypatch.setattr(crew, "_execute_tool", fake_exec)
        metrics = tm.ToolMetrics()
        monkeypatch.setattr(crew, "get_tool_metrics", lambda: metrics)

        crew._execute_tool_with_recovery("tool", {}, "a", self.trace)
        crew._execute_tool_with_recovery("tool", {}, "a", self.trace)

        assert metrics.summary()["cached"] == 1
        # 缓存命中记为逻辑成功，但不是一次真实执行
        assert metrics.summary()["raw_calls"] == 0


# ── 记忆评测纯函数 ───────────────────────────────────────

class TestMemoryBenchMetrics:
    def test_rank_of_and_recall(self):
        from eval.memory_bench import rank_of, recall_at_k, mrr

        assert rank_of(["a", "b", "c"], "b") == 2
        assert rank_of(["a", "b"], "z") == 0
        # 未命中记 0，不计入 recall
        assert recall_at_k([1, 2, 0], 1) == pytest.approx(1 / 3)
        assert recall_at_k([1, 2, 0], 3) == pytest.approx(2 / 3)
        assert mrr([1, 0]) == 0.5

    def test_bigram_jaccard_symmetry_and_identity(self):
        from eval.memory_bench import bigram_jaccard

        assert bigram_jaccard("abc", "abc") == 1.0
        assert bigram_jaccard("abc", "xyz") == 0.0
        assert bigram_jaccard("不吃辣", "辣不吃") > 0
