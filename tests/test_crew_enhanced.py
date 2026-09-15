"""
增强测试 — Mock 外部 API，测试错误恢复、降级、边界情况。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ── 工具结构化结果测试 ─────────────────────────────────────


class TestExecuteToolStructured:
    """测试 _execute_tool 返回结构化结果"""

    def test_get_weather_success(self):
        from app.crew import _execute_tool

        mock_data = {"city": "东京", "current": {"temp": 25}, "forecast": []}
        with patch("app.crew.get_weather", return_value=mock_data), \
             patch("app.crew.format_weather", return_value="东京天气 25°C"):
            result = _execute_tool("get_weather", {"destination": "东京"})
            assert result["success"] is True
            assert "25°C" in result["text"]

    def test_get_weather_error(self):
        from app.crew import _execute_tool

        mock_data = {"city": "未知", "error": "未找到城市坐标"}
        with patch("app.crew.get_weather", return_value=mock_data):
            result = _execute_tool("get_weather", {"destination": "未知城市"})
            assert result["success"] is False
            assert "失败" in result["text"]

    def test_search_knowledge_empty(self):
        from app.crew import _execute_tool

        with patch("app.crew.search_knowledge", return_value=[]):
            result = _execute_tool("search_knowledge", {"query": "不存在的内容"})
            assert result["success"] is False

    def test_unknown_tool(self):
        from app.crew import _execute_tool

        result = _execute_tool("nonexistent_tool", {})
        assert result["success"] is False
        assert "未知工具" in result["text"]

    def test_tool_exception(self):
        from app.crew import _execute_tool

        with patch("app.crew.get_weather", side_effect=Exception("网络超时")):
            result = _execute_tool("get_weather", {"destination": "东京"})
            assert result["success"] is False
            assert "网络超时" in result["text"]


# ── 错误恢复测试 ────────────────────────────────────────────


class TestToolRecovery:
    """测试工具错误恢复和 fallback 逻辑"""

    def test_fallback_to_web_search(self):
        from app.crew import _execute_tool_with_recovery
        from app.crew import AgentTrace

        trace = AgentTrace(name="test", label="测试")
        call_count = {"n": 0}

        def mock_execute(name, args):
            call_count["n"] += 1
            if name == "get_weather":
                return {"success": False, "text": "天气获取失败"}
            return {"success": True, "text": "搜索结果"}

        with patch("app.crew._execute_tool", side_effect=mock_execute):
            result = _execute_tool_with_recovery(
                "get_weather", {"destination": "东京"}, "researcher", trace
            )
            assert "降级" in result
            assert "web_search" in result
            assert trace.retries >= 1

    def test_skip_fallback_when_web_search_unavailable(self):
        """web_search 后端全凉时不应再降级到它——白等一轮超时救不了请求"""
        from app.crew import _execute_tool_with_recovery, AgentTrace
        from app.tools import web_search as ws

        trace = AgentTrace(name="test", label="测试")
        ws.reset_backend_state()
        try:
            for name in ws.BACKEND_ORDER:
                ws._mark_backend_failure(name)
                ws._mark_backend_failure(name)

            calls = []

            def mock_execute(name, args):
                calls.append(name)
                return {"success": False, "text": "失败"}

            with patch("app.crew._execute_tool", side_effect=mock_execute):
                _execute_tool_with_recovery(
                    "get_weather", {"destination": "东京"}, "researcher", trace
                )
            assert "web_search" not in calls, "不应降级到不可用的 web_search"
        finally:
            ws.reset_backend_state()

    def test_retry_then_success(self):
        from app.crew import _execute_tool_with_recovery
        from app.crew import AgentTrace

        trace = AgentTrace(name="test", label="测试")
        call_count = {"n": 0}

        def mock_execute(name, args):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"success": False, "text": "临时错误"}
            return {"success": True, "text": "成功"}

        with patch("app.crew._execute_tool", side_effect=mock_execute):
            result = _execute_tool_with_recovery(
                "get_weather", {"destination": "东京"}, "researcher", trace
            )
            assert result == "成功"
            assert trace.retries == 1

    def test_all_failures_return_degraded_message(self):
        from app.crew import _execute_tool_with_recovery
        from app.crew import AgentTrace

        trace = AgentTrace(name="test", label="测试")

        with patch("app.crew._execute_tool", return_value={"success": False, "text": "失败"}):
            result = _execute_tool_with_recovery(
                "get_weather", {"destination": "东京"}, "researcher", trace
            )
            assert "均执行失败" in result
            assert "暂无数据" in result


# ── 参数归一化测试 ──────────────────────────────────────────


class TestNormalizeToolArgs:
    """测试 _normalize_tool_args 边界情况"""

    def test_search_knowledge_missing_query(self):
        from app.crew import _normalize_tool_args

        result = _normalize_tool_args("search_knowledge", {"destination": "东京"})
        assert "query" in result
        assert result["query"] == "东京"

    def test_search_knowledge_empty_query_fallback(self):
        from app.crew import _normalize_tool_args

        result = _normalize_tool_args("search_knowledge", {"query": "", "category": "美食"})
        assert result["query"] == "美食"

    def test_get_exchange_rate_default_amount(self):
        from app.crew import _normalize_tool_args

        result = _normalize_tool_args("get_exchange_rate", {"destination": "东京"})
        assert result["amount_usd"] == 1000

    def test_get_weather_missing_destination(self):
        from app.crew import _normalize_tool_args

        result = _normalize_tool_args("get_weather", {"query": "巴黎天气"})
        assert "destination" in result

    def test_web_search_missing_query(self):
        from app.crew import _normalize_tool_args

        result = _normalize_tool_args("web_search", {"destination": "东京"})
        assert result["query"] == "东京"


# ── 降级方案测试 ────────────────────────────────────────────


class TestFallbackPlan:
    """测试离线降级方案生成"""

    def test_fallback_plan_structure(self):
        from app.crew import _generate_fallback_plan

        with patch("app.crew.search_knowledge", return_value=[{"text": "东京信息"}]), \
             patch("app.crew.optimize_route_from_knowledge", return_value={
                 "destination": "东京", "days": 3, "daily_routes": [],
                 "total_attractions": 5, "total_distance_km": 10, "optimization": "test"
             }), \
             patch("app.crew.format_optimized_route", return_value="路线信息"), \
             patch("app.crew.optimize_budget", return_value=[]), \
             patch("app.crew.format_budget_plans", return_value="预算信息"), \
             patch("app.crew.get_weather", return_value={"city": "东京", "current": {"temp": 25}, "forecast": []}), \
             patch("app.crew.format_weather", return_value="天气信息"):
            result = _generate_fallback_plan("东京", 3, 1500, "美食")
            assert "东京" in result
            assert "离线版" in result

    def test_fallback_plan_handles_all_exceptions(self):
        from app.crew import _generate_fallback_plan

        with patch("app.crew.search_knowledge", side_effect=Exception("DB 错误")), \
             patch("app.crew.optimize_route_from_knowledge", side_effect=Exception("路由错误")), \
             patch("app.crew.optimize_budget", side_effect=Exception("预算错误")), \
             patch("app.crew.get_weather", side_effect=Exception("天气错误")):
            result = _generate_fallback_plan("东京", 3, 1500, "美食")
            # 即使全部失败也应该返回有用信息
            assert "东京" in result
            assert "离线版" in result

    def test_fallback_plan_out_of_domain_has_no_foreign_cities(self):
        """库外目的地不得混入其它城市的内容。

        回归测试：旧版本调 search_knowledge 时漏传 destination，
        导致查「梅州五华」返回吉隆坡 / 孟买 / 布宜诺斯艾利斯的内容
        ——内容皆为真，但与查询完全无关。
        详见 docs/缺陷记录_检索静默失败.md
        """
        from app.crew import _generate_fallback_plan

        with patch("app.crew.optimize_route_from_knowledge", side_effect=Exception("无数据")), \
             patch("app.crew.optimize_budget", side_effect=Exception("无数据")), \
             patch("app.crew.get_weather", side_effect=Exception("无数据")):
            # search_knowledge 故意不 mock —— 必须走真实检索才能覆盖这个 bug
            result = _generate_fallback_plan("梅州五华", 3, 1500, "美食")

        section = result.split("## 🗺️")[0]
        for city in ("吉隆坡", "孟买", "布宜诺斯艾利斯", "巴黎", "纽约", "首尔"):
            assert city not in section, f"库外查询混入了无关城市内容：{city}"
        assert "暂未覆盖" in section


# ── 动态路由测试 ────────────────────────────────────────────


class TestRuleBasedRoute:
    """测试规则路由 fallback"""

    def test_budget_keywords(self):
        from app.crew import _rule_based_route

        agents = _rule_based_route("省钱,便宜,预算有限")
        assert "budget" in agents

    def test_food_keywords(self):
        from app.crew import _rule_based_route

        agents = _rule_based_route("美食,吃货,餐厅")
        assert "foodie" in agents

    def test_safety_keywords(self):
        from app.crew import _rule_based_route

        agents = _rule_based_route("安全,危险,注意事项")
        assert "safety" in agents

    def test_default_all_agents(self):
        from app.crew import _rule_based_route

        agents = _rule_based_route("随便看看")
        assert len(agents) == 5  # 全部 Agent

    def test_always_includes_researcher(self):
        from app.crew import _rule_based_route

        agents = _rule_based_route("只要预算")
        assert "researcher" in agents
        assert "planner" in agents


# ── Web 搜索测试（Mock）────────────────────────────────────


class TestWebSearchMocked:
    """Mock 外部 API 的 Web 搜索测试"""

    def test_ddg_search_returns_list(self):
        from app.tools.web_search import _ddg_search

        mock_results = [{"title": "Tokyo", "body": "Capital of Japan", "href": "https://example.com"}]
        with patch("ddgs.DDGS") as MockDDGS:
            mock_instance = MagicMock()
            mock_instance.text.return_value = mock_results
            MockDDGS.return_value.__enter__ = MagicMock(return_value=mock_instance)
            MockDDGS.return_value.__exit__ = MagicMock(return_value=False)

            results = _ddg_search("Tokyo", max_results=3)
            assert len(results) == 1
            assert results[0]["source"] == "DuckDuckGo"

    def test_ddg_search_handles_exception(self):
        from app.tools.web_search import _ddg_search

        with patch("ddgs.DDGS", side_effect=ImportError("not installed")):
            results = _ddg_search("test")
            assert results == []

    def test_web_search_combined(self):
        from app.tools.web_search import web_search, reset_backend_state

        # 必应排在最前，这里让它返回空，验证会继续往后走海外源
        reset_backend_state()
        with patch("app.tools.web_search._bing_search", return_value=[]), \
             patch("app.tools.web_search._ddg_search", return_value=[
                 {"title": "DDG Result", "snippet": "test", "url": "", "source": "DuckDuckGo"}
             ]), patch("app.tools.web_search._wiki_search", return_value=[
                 {"title": "Wiki Result", "snippet": "test", "url": "", "source": "Wikipedia"}
             ]):
            results = web_search("test query", max_results=5)
            assert len(results) == 2
            sources = [r["source"] for r in results]
            assert "DuckDuckGo" in sources
            assert "Wikipedia" in sources
        reset_backend_state()

    def test_bing_is_first_backend(self):
        """必应必须是首选后端：国内唯一可达源"""
        from app.tools import web_search as ws

        assert ws.BACKEND_ORDER[0] == "bing"

    def test_web_search_short_circuits_when_all_backends_down(self):
        """全部后端冷却时应立刻返回，不再等满超时（快失败）"""
        import time

        from app.tools import web_search as ws

        ws.reset_backend_state()
        try:
            for name in ws.BACKEND_ORDER:
                ws._mark_backend_failure(name)
                ws._mark_backend_failure(name)
            assert ws.web_search_available() is False

            started = time.perf_counter()
            results = ws.web_search("东京 景点", max_results=3)
            elapsed = time.perf_counter() - started
            assert results == []
            assert elapsed < 1.0, f"快失败失效：耗时 {elapsed:.1f}s"
        finally:
            ws.reset_backend_state()

    def test_web_search_empty_query(self):
        from app.tools.web_search import web_search

        assert web_search("", max_results=3) == []
        assert web_search("   ", max_results=3) == []


# ── 价格解析测试 ────────────────────────────────────────────


class TestParsePrice:
    """测试 _parse_price 各种格式"""

    def test_free(self):
        from app.tools.budget_optimizer import _parse_price

        assert _parse_price("免费") == 0.0
        assert _parse_price("Free") == 0.0
        assert _parse_price("不需要门票") == 0.0

    def test_dollar(self):
        from app.tools.budget_optimizer import _parse_price

        assert _parse_price("$25") == 25.0
        assert _parse_price("$1,500") == 1500.0

    def test_cny(self):
        from app.tools.budget_optimizer import _parse_price

        result = _parse_price("约￥70元")
        assert 9 < result < 11  # ~70/7 = 10

    def test_cny_short(self):
        from app.tools.budget_optimizer import _parse_price

        result = _parse_price("￥350")
        assert 49 < result < 51  # ~350/7 = 50

    def test_jpy(self):
        from app.tools.budget_optimizer import _parse_price

        result = _parse_price("1500日元")
        assert 9 < result < 11  # ~1500/150 = 10

    def test_thb(self):
        from app.tools.budget_optimizer import _parse_price

        result = _parse_price("350泰铢")
        assert 9 < result < 11  # ~350/35 = 10

    def test_empty(self):
        from app.tools.budget_optimizer import _parse_price

        assert _parse_price("") == 0.0
        assert _parse_price("未知") == 0.0

    def test_plain_number(self):
        from app.tools.budget_optimizer import _parse_price

        assert _parse_price("500") == 500.0


# ── SSE 端点测试 ────────────────────────────────────────────


class TestSSEEndpoint:
    """SSE 流式端点集成测试"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("ok", "degraded")
        assert "db_connected" in data

    def test_plan_sync_validation(self, client):
        """缺少必填字段应返回 422"""
        resp = client.post("/api/plan", json={})
        assert resp.status_code == 422

    def test_history_endpoint(self, client):
        resp = client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "total" in data
        assert "records" in data

    def test_route_endpoint(self, client):
        resp = client.get("/api/plan/route/东京?days=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "route" in data
        assert data["route"]["destination"] == "东京"
        assert len(data["route"]["daily_routes"]) == 3
        # 验证 GPS 坐标存在
        for day in data["route"]["daily_routes"]:
            for attr in day:
                assert "lat" in attr
                assert "lng" in attr
                assert isinstance(attr["lat"], float)
                assert isinstance(attr["lng"], float)

    def test_route_endpoint_unknown_city(self, client):
        resp = client.get("/api/plan/route/不存在的城市?days=2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "error" in data["route"]


# ── 并发控制测试 ────────────────────────────────────────────


class TestConcurrency:
    """并发限流器测试"""

    def test_limiter_acquire_release(self):
        from app.concurrency import RateLimiter

        limiter = RateLimiter(max_rps=10, max_concurrent=2)
        assert limiter.acquire(timeout=1.0) is True
        limiter.release()

    def test_limiter_concurrent_limit(self):
        from app.concurrency import RateLimiter

        limiter = RateLimiter(max_rps=100, max_concurrent=1)
        assert limiter.acquire(timeout=0.1) is True
        # 第二次获取应该超时（max_concurrent=1）
        assert limiter.acquire(timeout=0.1) is False
        limiter.release()

    def test_rate_limited_call_passes_args(self):
        from app.concurrency import rate_limited_call, RateLimiter

        limiter = RateLimiter(max_rps=10, max_concurrent=5)
        mock_func = MagicMock(return_value="result")

        result = rate_limited_call(limiter, mock_func, "arg1", key="val")
        assert result == "result"
        mock_func.assert_called_once_with("arg1", key="val")

    def test_rate_limited_call_none_limiter(self):
        from app.concurrency import rate_limited_call

        mock_func = MagicMock(return_value="result")
        result = rate_limited_call(None, mock_func, "arg1")
        assert result == "result"

    def test_rate_limited_call_retries_on_retryable_error(self):
        from app.concurrency import rate_limited_call, RateLimiter

        limiter = RateLimiter(max_rps=100, max_concurrent=5, max_retries=2, base_delay=0.01)
        mock_func = MagicMock(side_effect=[Exception("429 rate limit"), "success"])

        result = rate_limited_call(limiter, mock_func)
        assert result == "success"
        assert mock_func.call_count == 2
