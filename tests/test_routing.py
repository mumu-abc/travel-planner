"""
路由准确率评测 — 测试动态路由 + 规则 fallback 的准确性。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.crew import _rule_based_route, AGENTS


# ── 规则路由测试 ──────────────────────────────────────────


class TestRuleBasedRoute:
    """规则路由 fallback 测试"""

    def test_full_planning_default(self):
        """默认全面规划需求 → 全部 Agent"""
        result = _rule_based_route("文化")
        assert "researcher" in result
        assert "planner" in result
        assert len(result) == 5

    def test_budget_focused(self):
        """预算关键词 → 包含 budget"""
        result = _rule_based_route("预算 便宜 省钱")
        assert "budget" in result
        assert "researcher" in result

    def test_food_focused(self):
        """美食关键词 → 包含 foodie"""
        result = _rule_based_route("美食 吃 餐厅")
        assert "foodie" in result
        assert "researcher" in result

    def test_safety_focused(self):
        """安全关键词 → 包含 safety"""
        result = _rule_based_route("安全 注意事项")
        assert "safety" in result
        assert "researcher" in result

    def test_english_keywords(self):
        """英文关键词也能匹配"""
        result = _rule_based_route("budget cheap food restaurant safety")
        assert "budget" in result
        assert "foodie" in result
        assert "safety" in result

    def test_always_includes_base(self):
        """任何情况都应包含 researcher 和 planner"""
        for interests in ["文化", "预算", "美食", "安全", "购物", "拍照"]:
            result = _rule_based_route(interests)
            assert "researcher" in result
            assert "planner" in result


# ── LLM 路由测试（mock） ──────────────────────────────────


class TestLLMRoute:
    """LLM 动态路由测试（mock OpenAI）"""

    def _mock_client(self, agents_list):
        """创建 mock OpenAI client"""
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"agents": agents_list})))
        ]
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_resp
        return mock_client

    def test_llm_route_all_agents(self):
        """LLM 返回全部 Agent"""
        from app.crew import _route_agents

        client = self._mock_client(["researcher", "planner", "budget", "foodie", "safety"])
        result = _route_agents(client, "东京", 3, 1000, "文化")
        assert set(result) == {"researcher", "planner", "budget", "foodie", "safety"}

    def test_llm_route_partial(self):
        """LLM 返回部分 Agent"""
        from app.crew import _route_agents

        client = self._mock_client(["researcher", "budget"])
        result = _route_agents(client, "东京", 3, 500, "预算")
        assert "researcher" in result
        assert "budget" in result
        assert "planner" not in result

    def test_llm_route_invalid_filtered(self):
        """LLM 返回无效 Agent 名 → 过滤掉"""
        from app.crew import _route_agents

        client = self._mock_client(["researcher", "invalid_agent", "planner"])
        result = _route_agents(client, "东京", 3, 1000, "文化")
        assert "researcher" in result
        assert "planner" in result
        assert "invalid_agent" not in result

    def test_llm_route_fallback_on_error(self):
        """LLM 报错 → 降级为规则路由"""
        from app.crew import _route_agents

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("429 quota exhausted")
        result = _route_agents(mock_client, "东京", 3, 1000, "美食 预算")
        # 规则路由应包含 foodie 和 budget
        assert "foodie" in result
        assert "budget" in result
        assert "researcher" in result


# ── 路由准确率评测 ──────────────────────────────────────────


class TestRoutingAccuracy:
    """
    路由准确率评测：用预期结果对比实际路由结果。

    当 LLM API 可用时，取消 @pytest.mark.slow 标记可运行完整评测。
    """

    # 测试集：(destination, days, budget, interests, expected_agents)
    TEST_CASES = [
        ("东京", 3, 1000, "文化 历史", {"researcher", "planner", "budget", "foodie", "safety"}),
        ("巴黎", 5, 2000, "预算 便宜", {"researcher", "planner", "budget"}),
        ("首尔", 2, 500, "美食 吃", {"researcher", "planner", "foodie"}),
        ("曼谷", 4, 800, "安全 注意", {"researcher", "planner", "safety"}),
        ("新加坡", 3, 1500, "美食 预算", {"researcher", "planner", "budget", "foodie"}),
        ("大阪", 2, 600, "全面规划", {"researcher", "planner", "budget", "foodie", "safety"}),
        ("京都", 3, 1000, "文化 美食", {"researcher", "planner", "foodie"}),
        ("伦敦", 5, 3000, "budget food", {"researcher", "planner", "budget", "foodie"}),
    ]

    @pytest.mark.slow
    def test_llm_routing_accuracy(self):
        """LLM 路由准确率（需 API key）"""
        from app.crew import _route_agents
        from app.config import settings

        if not settings.llm_api_key:
            pytest.skip("No LLM API key")

        client = MagicMock()  # Will be replaced by real client
        correct = 0
        total = len(self.TEST_CASES)

        for dest, days, budget, interests, expected in self.TEST_CASES:
            try:
                from openai import OpenAI

                real_client = OpenAI(
                    api_key=settings.llm_api_key,
                    base_url=settings.llm_base_url,
                )
                result = _route_agents(real_client, dest, days, budget, interests)
                result_set = set(result)
                if result_set == expected:
                    correct += 1
                else:
                    print(f"  MISMATCH: {dest} {interests}")
                    print(f"    Expected: {expected}")
                    print(f"    Got: {result_set}")
            except Exception as e:
                print(f"  ERROR: {dest} → {e}")

        accuracy = correct / total
        print(f"\n路由准确率: {correct}/{total} = {accuracy:.1%}")
        assert accuracy >= 0.7, f"路由准确率 {accuracy:.1%} 低于 70%"

    def test_rule_based_routing_accuracy(self):
        """规则路由准确率（不需要 API）"""
        correct = 0
        total = len(self.TEST_CASES)

        for dest, days, budget, interests, expected in self.TEST_CASES:
            result = set(_rule_based_route(interests))
            if result == expected:
                correct += 1

        accuracy = correct / total
        print(f"\n规则路由准确率: {correct}/{total} = {accuracy:.1%}")
        # 规则路由不需要 100% 准确，但应该 >= 50%
        assert accuracy >= 0.5, f"规则路由准确率 {accuracy:.1%} 低于 50%"
