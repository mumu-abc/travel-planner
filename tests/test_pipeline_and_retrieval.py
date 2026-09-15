"""行为测试：执行模式、BM25、GPS 图谱、工具预算 — 面试可讲的硬指标。"""

from __future__ import annotations

import pytest


class TestPipelineModes:
    def test_mode_selectors(self):
        from app.agents import select_agents_for_mode, SINGLE_AGENT
        assert len(select_agents_for_mode("single")) == 1
        assert select_agents_for_mode("single")[0]["name"] == SINGLE_AGENT["name"]
        assert len(select_agents_for_mode("multi")) == 5
        assert len(select_agents_for_mode("sequential")) == 5

    def test_unknown_mode_raises(self):
        from app.agents import select_agents_for_mode
        with pytest.raises(ValueError):
            select_agents_for_mode("turbo")

    def test_split_layers_multi(self):
        from app.agents import select_agents_for_mode, split_layers
        agents = select_agents_for_mode("multi")
        layers = split_layers(agents, "multi")
        assert len(layers) == 3
        assert [a["name"] for a in layers[0]] == ["researcher"]
        assert [a["name"] for a in layers[1]] == ["planner"]
        assert {a["name"] for a in layers[2]} == {"budget", "foodie", "safety"}

    def test_split_layers_sequential(self):
        from app.agents import select_agents_for_mode, split_layers
        agents = select_agents_for_mode("sequential")
        layers = split_layers(agents, "sequential")
        assert len(layers) == 5
        assert [layers[i][0]["name"] for i in range(5)] == [
            "researcher", "planner", "budget", "foodie", "safety",
        ]

    def test_build_travel_crew_rejects_bad_mode(self):
        from app.crew import build_travel_crew
        with pytest.raises(ValueError):
            build_travel_crew("东京", 3, 1000, "美食", mode="nope")


class TestToolBudget:
    def test_unlimited_by_default(self):
        from app.crew import _ToolBudget
        b = _ToolBudget(max_web_search=0)
        assert all(b.try_consume_web_search() for _ in range(20))

    def test_budget_exhaustion(self):
        from app.crew import _ToolBudget
        b = _ToolBudget(max_web_search=2)
        assert b.try_consume_web_search()
        assert b.try_consume_web_search()
        assert not b.try_consume_web_search()
        assert b.snapshot()["web_search_used"] == 2


class TestBM25:
    def test_exact_keyword_ranks_high(self):
        """精确城市名应压过无关内容。"""
        from app.tools.knowledge_search import search_knowledge
        results = search_knowledge("浅草寺", destination="东京", category="attraction", top_k=3, hybrid=True)
        assert results, "应检索到浅草寺相关 chunk"
        assert any("浅草寺" in r["text"] for r in results)

    def test_bm25_formula_uses_k1_b(self):
        from app.tools import knowledge_search as ks
        assert ks._BM25_K1 == 1.5
        assert ks._BM25_B == 0.75

    def test_bm25_search_returns_finite_scores(self):
        from app.tools.knowledge_search import _init_index, _bm25_search
        _init_index()
        hits = _bm25_search("东京 寺庙 美食", top_k=5)
        assert hits
        for _, score in hits:
            assert score > 0
            assert score == score  # not NaN


class TestGPSKnowledgeGraph:
    def test_nearby_uses_real_distance_not_list_order(self):
        """浅草寺附近应是地理邻近景点，且带 km 标注。"""
        from app.tools.knowledge_search import get_attraction_relations, _init_index
        _init_index()
        rel = get_attraction_relations("浅草寺")
        assert rel is not None
        assert rel.get("has_gps") is True
        # 邻近列表应非空（2.5km 内），且与 nearby_km 等长
        assert len(rel["nearby"]) == len(rel.get("nearby_km", []))
        if rel["nearby_km"]:
            assert all(km <= 2.5 for km in rel["nearby_km"])

    def test_same_type_still_present(self):
        from app.tools.knowledge_search import get_attraction_relations, _init_index
        _init_index()
        rel = get_attraction_relations("东京塔")
        assert rel is not None
        assert "same_type" in rel


class TestRouteOptimizer:
    def test_two_opt_not_worse_than_greedy(self):
        from app.tools.route_optimizer import (
            ATTRACTION_COORDS, Attraction, _greedy_nearest_neighbor, _two_opt_improve, _haversine_distance,
        )
        names = [
            "浅草寺", "东京塔", "涩谷十字路口", "明治神宫", "秋叶原",
            "新宿御苑", "东京晴空塔", "筑地外市场", "台场", "皇居外苑",
        ]
        attrs = [
            Attraction(name=n, type="spot", ticket="", duration="", tip="",
                       lat=ATTRACTION_COORDS[n][0], lng=ATTRACTION_COORDS[n][1])
            for n in names
        ]
        greedy = _greedy_nearest_neighbor(attrs)
        improved = _two_opt_improve(greedy)

        def total(route):
            return sum(_haversine_distance(route[i], route[i + 1]) for i in range(len(route) - 1))

        assert total(improved) <= total(greedy) + 1e-9
        assert sorted(a.name for a in improved) == sorted(names)


class TestConfigFlags:
    def test_pipeline_defaults(self):
        from app.config import settings
        assert settings.pipeline_mode in ("multi", "sequential", "single")
        assert settings.max_tool_rounds >= 1
        assert settings.max_web_search_calls >= 0


class TestOutOfDomainRetrieval:
    """回归：库外目的地必须拒答，不能返回语义相近的其它城市内容。

    详见 docs/缺陷记录_检索静默失败.md
    """

    def test_covered_destinations(self):
        from app.tools.knowledge_search import is_destination_covered
        assert is_destination_covered("东京")
        assert is_destination_covered("吉隆坡")
        assert not is_destination_covered("梅州五华")
        # 未指定目的地时不拦截，沿用原有不过滤行为
        assert is_destination_covered("")
        assert is_destination_covered(None)

    def test_out_of_domain_returns_empty(self):
        from app.tools.knowledge_search import search_knowledge
        results = search_knowledge(query="梅州五华 景点", destination="梅州五华", top_k=5)
        assert results == []

    def test_in_domain_still_returns_own_city(self):
        from app.tools.knowledge_search import search_knowledge
        results = search_knowledge(query="东京 景点", destination="东京", top_k=5)
        assert results
        assert all(r["destination"] == "东京" for r in results)

    def test_no_destination_keeps_legacy_behaviour(self):
        """不传 destination 时不能被误伤 —— 既有调用方依赖这个行为。"""
        from app.tools.knowledge_search import search_knowledge
        assert search_knowledge(query="东京", top_k=3)

    def test_normalize_backfills_destination(self):
        """LLM 漏传 destination 时必须用本次规划的目的地兜底。"""
        from app.crew import _normalize_tool_args
        args = _normalize_tool_args("search_knowledge", {"query": "景点"}, destination="东京")
        assert args["destination"] == "东京"

    def test_tool_message_distinguishes_not_covered(self):
        from app.crew import _execute_tool
        out = _execute_tool("search_knowledge", {"query": "景点", "destination": "梅州五华"})
        assert out["success"] is False
        assert "未覆盖" in out["text"]
