"""用量粗计测试。"""

from __future__ import annotations


class TestUsage:
    def test_add_and_snapshot(self):
        from app.usage import UsageStats
        u = UsageStats()
        u.add_llm(1000, 200)
        u.add_llm(500, 100)
        u.add_tool("search_knowledge")
        u.add_tool("web_search")
        snap = u.snapshot()
        assert snap["llm_calls"] == 2
        assert snap["tool_calls"] == 2
        assert snap["web_search_calls"] == 1
        assert snap["approx_input_tokens"] == int(1500 / 1.6)
        assert snap["approx_total_tokens"] > 0

    def test_reset(self):
        from app.usage import UsageStats
        u = UsageStats()
        u.add_llm(100, 50)
        u.reset()
        assert u.snapshot()["llm_calls"] == 0

    def test_cost_estimate(self):
        from app.usage import estimate_cost_usd
        c = estimate_cost_usd(1_000_000, 1_000_000, in_per_mtok=0.5, out_per_mtok=1.5)
        assert abs(c - 2.0) < 1e-6
