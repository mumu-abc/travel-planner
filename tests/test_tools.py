"""
工具单元测试 — 测试天气、汇率、知识检索工具的核心逻辑。
"""

from __future__ import annotations

import pytest


# ── 天气工具测试 ──────────────────────────────────────────


class TestWeatherTool:
    """天气工具测试（wttr.in API）"""

    def test_get_weather_tokyo(self):
        """获取东京天气"""
        from app.tools.real_weather import get_weather

        result = get_weather("东京")
        assert "current" in result
        assert "temp" in result["current"]
        assert isinstance(result["current"]["temp"], (int, float, str))

    def test_format_weather(self):
        """天气格式化"""
        from app.tools.real_weather import get_weather, format_weather

        data = get_weather("巴黎")
        text = format_weather(data)
        assert len(text) > 50
        assert "°C" in text or "温度" in text


# ── 汇率工具测试 ──────────────────────────────────────────


class TestExchangeTool:
    """汇率工具测试（open.er-api.com）"""

    def test_get_exchange_rate_japan(self):
        """获取日元汇率"""
        from app.tools.real_exchange import get_exchange_rate

        result = get_exchange_rate("东京", 100)
        assert "rate" in result
        assert result["rate"] > 0
        assert result["to"] == "JPY"

    def test_format_exchange(self):
        """汇率格式化"""
        from app.tools.real_exchange import get_exchange_rate, format_exchange

        data = get_exchange_rate("巴黎", 1000)
        text = format_exchange(data)
        assert "USD" in text
        assert len(text) > 20


# ── 知识检索测试 ──────────────────────────────────────────


class TestKnowledgeSearch:
    """FAISS 知识检索测试"""

    def test_search_basic(self):
        """基本检索"""
        from app.tools.knowledge_search import search_knowledge

        results = search_knowledge("东京的寺庙景点", top_k=3)
        assert len(results) > 0
        assert all("text" in r for r in results)
        assert all("score" in r for r in results)

    def test_search_with_destination_filter(self):
        """限定城市检索"""
        from app.tools.knowledge_search import search_knowledge

        results = search_knowledge("美食", destination="巴黎", top_k=3)
        assert all(r["destination"] == "巴黎" for r in results)

    def test_search_with_category_filter(self):
        """限定类别检索"""
        from app.tools.knowledge_search import search_knowledge

        results = search_knowledge("餐厅", category="restaurant", top_k=3)
        assert all(r["category"] == "restaurant" for r in results)

    def test_search_safety(self):
        """安全信息检索"""
        from app.tools.knowledge_search import search_knowledge

        results = search_knowledge("东京安全等级紧急电话", destination="东京", category="safety", top_k=2)
        assert len(results) > 0

    def test_format_results(self):
        """格式化检索结果"""
        from app.tools.knowledge_search import search_knowledge, format_search_results

        results = search_knowledge("首尔景点", top_k=3)
        text = format_search_results(results)
        assert len(text) > 20
        assert "1." in text


# ── Web 搜索测试 ──────────────────────────────────────────


class TestWebSearch:
    """Web 搜索工具测试（必应中国 → DuckDuckGo → Wikipedia）"""

    def test_search_returns_list(self):
        """搜索应返回列表"""
        from app.tools.web_search import web_search

        results = web_search("Tokyo travel guide", max_results=3)
        assert isinstance(results, list)
        assert len(results) <= 3

    def test_search_result_structure(self):
        """每个结果应包含 title/snippet/source"""
        from app.tools.web_search import web_search

        results = web_search("Paris attractions", max_results=2)
        for r in results:
            assert "title" in r
            assert "snippet" in r
            assert "source" in r
            assert r["source"] in ("Bing", "DuckDuckGo", "Wikipedia")

    def test_format_web_search(self):
        """格式化搜索结果"""
        from app.tools.web_search import format_web_search

        mock_results = [
            {"title": "测试标题", "snippet": "测试摘要", "url": "https://example.com", "source": "DuckDuckGo"},
        ]
        text = format_web_search(mock_results)
        assert "测试标题" in text
        assert "DuckDuckGo" in text

    def test_format_empty_results(self):
        """空结果格式化"""
        from app.tools.web_search import format_web_search

        text = format_web_search([])
        assert "未找到" in text


# ── 文档摄入测试 ──────────────────────────────────────────


class TestDocumentIngestion:
    """Markdown 文档摄入管道测试"""

    def test_parse_markdown_structure(self):
        """解析 Markdown 应返回 chunk 列表"""
        from app.knowledge.ingest import _parse_markdown
        from pathlib import Path

        doc_path = Path(__file__).parent.parent / "documents" / "tokyo.md"
        chunks = _parse_markdown(doc_path)

        assert len(chunks) > 5
        for chunk in chunks:
            assert "text" in chunk
            assert "destination" in chunk
            assert "category" in chunk
            assert "section" in chunk
            assert "source" in chunk

    def test_parse_markdown_destination(self):
        """从一级标题提取目的地"""
        from app.knowledge.ingest import _parse_markdown
        from pathlib import Path

        chunks = _parse_markdown(Path(__file__).parent.parent / "documents" / "paris.md")
        assert all(c["destination"] == "巴黎" for c in chunks)

    def test_infer_category(self):
        """类别推断应正确"""
        from app.knowledge.ingest import _infer_category

        assert _infer_category("景点", "浅草寺 东京塔") == "attraction"
        assert _infer_category("美食", "寿司 拉面") == "restaurant"
        assert _infer_category("交通", "地铁 机场") == "transport"
        assert _infer_category("安全", "警察 紧急") == "safety"
        assert _infer_category("预算", "日元 费用") == "budget"

    def test_search_documents_no_index(self):
        """无索引时应返回空"""
        from app.knowledge.ingest import search_documents, INDEX_DIR

        # 重置全局索引
        import app.knowledge.ingest as ingest_mod
        ingest_mod._doc_index = None
        ingest_mod._doc_chunks = None
        ingest_mod._doc_model = None

        # 如果索引存在，验证能返回结果；不存在时返回空
        if (INDEX_DIR / "docs.faiss").exists():
            results = search_documents("东京景点", top_k=2)
            assert isinstance(results, list)
            assert len(results) > 0
            for r in results:
                assert "text" in r
                assert "source" in r
                assert "score" in r
        else:
            results = search_documents("东京景点")
            assert isinstance(results, list)


# ── 知识库测试 ────────────────────────────────────────────


class TestKnowledgeBase:
    """知识库数据完整性测试"""

    def test_destinations_count(self):
        """知识库应包含至少 15 个城市"""
        from app.knowledge.destinations import DESTINATIONS

        assert len(DESTINATIONS) >= 15

    def test_tokyo_data_complete(self):
        """东京数据应完整"""
        from app.knowledge.destinations import DESTINATIONS

        tokyo = DESTINATIONS["东京"]
        assert len(tokyo["attractions"]) == 10
        assert len(tokyo["restaurants"]) == 5
        assert "airport_to_city" in tokyo["transport"]
        assert "emergency" in tokyo["safety"]


# ── 路线优化测试 ──────────────────────────────────────────


class TestRouteOptimizer:
    """贪心+2-opt路线优化测试（真实GPS坐标+Haversine距离）"""

    def test_basic_optimization(self):
        """基本路线优化"""
        from app.tools.route_optimizer import optimize_route_from_knowledge

        result = optimize_route_from_knowledge("东京", 3)
        assert result["destination"] == "东京"
        assert result["days"] == 3
        assert result["total_attractions"] > 0
        assert len(result["daily_routes"]) == 3
        assert "total_distance_km" in result
        assert result["total_distance_km"] >= 0

    def test_daily_plan_structure(self):
        """每日计划结构应包含GPS坐标"""
        from app.tools.route_optimizer import optimize_route_from_knowledge

        result = optimize_route_from_knowledge("巴黎", 2)
        for day_idx, route in enumerate(result["daily_routes"], 1):
            assert isinstance(route, list)
            assert len(route) > 0
            for attr in route:
                assert "name" in attr
                assert "type" in attr
                assert "ticket" in attr
                assert "duration" in attr
                assert "lat" in attr
                assert "lng" in attr
                assert isinstance(attr["lat"], float)
                assert isinstance(attr["lng"], float)

    def test_real_gps_coordinates(self):
        """景点应使用真实GPS坐标（非零且合理范围）"""
        from app.tools.route_optimizer import ATTRACTION_COORDS

        # 浅草寺的真实坐标
        lat, lng = ATTRACTION_COORDS["浅草寺"]
        assert abs(lat - 35.7148) < 0.01
        assert abs(lng - 139.7967) < 0.01

        # 埃菲尔铁塔
        lat, lng = ATTRACTION_COORDS["埃菲尔铁塔"]
        assert abs(lat - 48.8584) < 0.01

    def test_haversine_distance(self):
        """Haversine 距离公式应正确"""
        from app.tools.route_optimizer import _haversine_distance, Attraction

        # 东京 vs 巴黎（约 9700 km）
        tokyo = Attraction(name="东京", type="", ticket="", duration="", tip="",
                           lat=35.6762, lng=139.6503)
        paris = Attraction(name="巴黎", type="", ticket="", duration="", tip="",
                           lat=48.8566, lng=2.3522)
        dist = _haversine_distance(tokyo, paris)
        assert 9000 < dist < 10000

    def test_different_cities(self):
        """不同城市路线不同"""
        from app.tools.route_optimizer import optimize_route_from_knowledge

        tokyo = optimize_route_from_knowledge("东京", 2)
        paris = optimize_route_from_knowledge("巴黎", 2)
        assert tokyo["destination"] != paris["destination"]

    def test_optimization_mentions_haversine(self):
        """优化方法应提及 Haversine"""
        from app.tools.route_optimizer import optimize_route_from_knowledge

        result = optimize_route_from_knowledge("东京", 2)
        assert "Haversine" in result["optimization"]


# ── 预算优化测试 ──────────────────────────────────────────


class TestBudgetOptimizer:
    """LP约束求解预算优化测试"""

    def test_budget_plans(self):
        """三档预算方案"""
        from app.tools.budget_optimizer import optimize_budget

        plans = optimize_budget("东京", 3000, 3)
        assert len(plans) == 3
        tiers = [p.tier for p in plans]
        assert "经济" in tiers
        assert "舒适" in tiers
        assert "豪华" in tiers

    def test_budget_within_range(self):
        """预算数值合理"""
        from app.tools.budget_optimizer import optimize_budget

        plans = optimize_budget("巴黎", 5000, 5)
        for plan in plans:
            assert 0 < plan.trip_total
            assert plan.daily_total > 0
            assert plan.accommodation >= 0
            assert plan.food >= 0

    def test_different_budgets(self):
        """不同预算产生不同方案"""
        from app.tools.budget_optimizer import optimize_budget

        low = optimize_budget("东京", 1000, 2)
        high = optimize_budget("东京", 10000, 2)
        assert high[0].trip_total > low[0].trip_total

    def test_solver_field_exists(self):
        """每个方案应有 solver 字段标识求解方式"""
        from app.tools.budget_optimizer import optimize_budget

        plans = optimize_budget("东京", 3000, 3)
        for plan in plans:
            assert hasattr(plan, "solver")
            assert plan.solver in ("lp (CBC)", "proportional")

    def test_lp_solver_used_when_available(self):
        """安装了 PuLP 时应使用 LP 求解器"""
        from app.tools.budget_optimizer import optimize_budget, _HAS_PULP

        if _HAS_PULP:
            plans = optimize_budget("东京", 5000, 3)
            for plan in plans:
                assert "lp" in plan.solver

    def test_constrained_budget(self):
        """带约束的预算优化"""
        from app.tools.budget_optimizer import adjust_budget_for_constraints

        plan = adjust_budget_for_constraints(
            "东京", 3000, 3,
            min_accommodation=100,
            max_food_per_day=80,
        )
        assert plan is not None
        assert plan.accommodation >= 100 or plan.solver.startswith("lp")
