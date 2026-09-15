"""
Agent 集成测试 — Agent 定义、工具配置、ReAct 推理链、并行架构、动态路由。
"""

from __future__ import annotations

import pytest


class TestAgentDefinitions:
    """Agent 定义测试"""

    def test_agents_count(self):
        """应有 5 个 Agent"""
        from app.crew import AGENTS
        assert len(AGENTS) == 5

    def test_agent_names(self):
        """Agent 名称应正确"""
        from app.crew import AGENTS
        names = [a["name"] for a in AGENTS]
        assert "researcher" in names
        assert "planner" in names
        assert "budget" in names
        assert "foodie" in names
        assert "safety" in names

    def test_agents_have_system_prompt(self):
        """每个 Agent 应有系统提示"""
        from app.crew import AGENTS
        for agent in AGENTS:
            assert "system" in agent
            assert len(agent["system"]) > 50

    def test_agents_have_layer(self):
        """每个 Agent 应有 layer 属性（3层串并行架构）"""
        from app.crew import AGENTS
        for agent in AGENTS:
            assert "layer" in agent
            assert agent["layer"] in (1, 2, 3)

    def test_researcher_is_layer1(self):
        """Researcher 应在 Layer 1（先行执行）"""
        from app.crew import AGENTS
        researcher = [a for a in AGENTS if a["name"] == "researcher"][0]
        assert researcher["layer"] == 1

    def test_planner_is_layer2(self):
        """Planner 应在 Layer 2（依赖 Researcher）"""
        from app.crew import AGENTS
        planner = [a for a in AGENTS if a["name"] == "planner"][0]
        assert planner["layer"] == 2

    def test_parallel_agents_are_layer3(self):
        """Budget/Foodie/Safety 应在 Layer 3（并行执行）"""
        from app.crew import AGENTS
        layer3 = [a for a in AGENTS if a["name"] in ("budget", "foodie", "safety")]
        for agent in layer3:
            assert agent["layer"] == 3


class TestToolDefinitions:
    """工具定义测试"""

    def test_tools_count(self):
        """应有 7 个工具"""
        from app.crew import TOOLS
        assert len(TOOLS) == 7

    def test_tool_names(self):
        """工具名称应正确"""
        from app.crew import TOOLS
        names = [t["function"]["name"] for t in TOOLS]
        assert "get_weather" in names
        assert "get_exchange_rate" in names
        assert "search_knowledge" in names
        assert "optimize_route" in names
        assert "optimize_budget" in names
        assert "web_search" in names

    def test_tool_has_parameters(self):
        """每个工具应有参数定义"""
        from app.crew import TOOLS
        for tool in TOOLS:
            func = tool["function"]
            assert "parameters" in func
            assert "properties" in func["parameters"]

    def test_route_tool_mentions_haversine(self):
        """路线优化工具描述应提及 Haversine/GPS"""
        from app.crew import TOOLS
        route_tool = [t for t in TOOLS if t["function"]["name"] == "optimize_route"][0]
        desc = route_tool["function"]["description"]
        assert "GPS" in desc or "Haversine" in desc or "haversine" in desc

    def test_budget_tool_mentions_lp(self):
        """预算优化工具描述应提及线性规划/LP"""
        from app.crew import TOOLS
        budget_tool = [t for t in TOOLS if t["function"]["name"] == "optimize_budget"][0]
        desc = budget_tool["function"]["description"]
        assert "LP" in desc or "线性规划" in desc or "constraint" in desc.lower()


class TestReActArchitecture:
    """ReAct 推理链 + 并行架构测试"""

    def test_react_step_dataclass(self):
        """ReActStep 数据类应存在"""
        from app.crew import ReActStep
        step = ReActStep(thought="test", action="get_weather")
        assert step.thought == "test"
        assert step.action == "get_weather"
        assert step.observation == ""

    def test_agent_trace_has_react_steps(self):
        """AgentTrace 应包含 react_steps 字段"""
        from app.crew import AgentTrace
        trace = AgentTrace(name="test", label="test")
        assert hasattr(trace, "react_steps")
        assert isinstance(trace.react_steps, list)

    def test_agent_map_exists(self):
        """_AGENT_MAP 应存在且包含所有 Agent"""
        from app.crew import _AGENT_MAP
        assert "researcher" in _AGENT_MAP
        assert "planner" in _AGENT_MAP
        assert "budget" in _AGENT_MAP
        assert "foodie" in _AGENT_MAP
        assert "safety" in _AGENT_MAP

    def test_reflection_prompts_exist(self):
        """每个 Agent 应有反思 prompt（含 single baseline）"""
        from app.crew import REFLECTION_PROMPTS
        assert len(REFLECTION_PROMPTS) == 6
        for name in ["researcher", "planner", "budget", "foodie", "safety", "planner_all"]:
            assert name in REFLECTION_PROMPTS

    def test_route_agents_callable(self):
        """_route_agents 路由函数应存在且可调用"""
        from app.crew import _route_agents
        assert callable(_route_agents)

    def test_execute_tool_dispatch(self):
        """_execute_tool 应正确分发工具调用，返回结构化结果"""
        from app.crew import _execute_tool
        # 测试未知工具
        result = _execute_tool("nonexistent_tool", {})
        assert isinstance(result, dict)
        assert result["success"] is False
        assert "未知工具" in result["text"]

    def test_tool_fallback_map_exists(self):
        """_TOOL_FALLBACKS 应存在且包含降级链"""
        from app.crew import _TOOL_FALLBACKS
        assert "search_knowledge" in _TOOL_FALLBACKS
        assert "web_search" in _TOOL_FALLBACKS["search_knowledge"]

    def test_structured_tool_result(self):
        """_execute_tool 返回的结构化结果应正确标识成功/失败"""
        from app.crew import _execute_tool
        # 成功 case：mock 天气工具
        from unittest.mock import patch
        mock_weather = {"city": "东京", "current": {"temp": 22}, "forecast": []}
        with patch("app.crew.get_weather", return_value=mock_weather), \
             patch("app.crew.format_weather", return_value="东京 22°C"):
            result = _execute_tool("get_weather", {"destination": "东京"})
            assert result["success"] is True
            assert "22°C" in result["text"]

        # 失败 case：工具抛异常
        with patch("app.crew.get_weather", side_effect=Exception("timeout")):
            result = _execute_tool("get_weather", {"destination": "东京"})
            assert result["success"] is False
            assert "timeout" in result["text"]

    def test_adapt_args_for_fallback(self):
        """_adapt_args 应将原工具参数适配为 fallback 工具参数"""
        from app.crew import _adapt_args
        # search_knowledge → web_search
        adapted = _adapt_args("search_knowledge", "web_search", {"query": "东京景点"})
        assert adapted["query"] == "东京景点"

        # get_weather → web_search
        adapted = _adapt_args("get_weather", "web_search", {"destination": "东京"})
        assert "东京" in adapted["query"]
        assert "天气" in adapted["query"]

    def test_execute_tool_with_recovery_exists(self):
        """_execute_tool_with_recovery 应存在且可调用"""
        from app.crew import _execute_tool_with_recovery, AgentTrace
        trace = AgentTrace(name="test", label="test")
        # 正常工具不应触发恢复
        result = _execute_tool_with_recovery(
            "search_knowledge", {"query": "东京景点"}, "test", trace
        )
        assert isinstance(result, str)
        assert len(result) > 0


class TestAgentEngine:
    """统一 Agent 执行引擎测试（Function Calling 模式）"""

    def test_agent_system_prompts_no_react_text(self):
        """Agent 系统提示不应包含旧的 ReAct 文本格式指令"""
        from app.crew import AGENTS
        for agent in AGENTS:
            assert "Thought:" not in agent["system"], f"{agent['name']} 仍包含旧 ReAct 文本格式"
            assert "Action Input:" not in agent["system"], f"{agent['name']} 仍包含旧 ReAct 文本格式"

    def test_agent_system_prompts_have_tool_strategy(self):
        """每个 Agent 的系统提示应包含工具使用策略"""
        from app.crew import AGENTS
        for agent in AGENTS:
            assert "工具使用策略" in agent["system"], f"{agent['name']} 缺少工具使用策略"

    def test_event_callback_types(self):
        """验证 event_callback 支持的事件类型"""
        from app.crew import _run_agent_with_tools
        # 只验证函数签名支持 event_callback 参数
        import inspect
        sig = inspect.signature(_run_agent_with_tools)
        assert "event_callback" in sig.parameters

    def test_build_travel_crew_signature(self):
        """验证 build_travel_crew 支持 event_callback"""
        from app.crew import build_travel_crew
        import inspect
        sig = inspect.signature(build_travel_crew)
        assert "event_callback" in sig.parameters
        assert "token_callback" in sig.parameters
        assert "step_callback" in sig.parameters

    def test_three_layer_execution_order(self):
        """验证 3 层执行架构的 Agent 分层"""
        from app.crew import AGENTS
        researcher = [a for a in AGENTS if a["name"] == "researcher"]
        planner = [a for a in AGENTS if a["name"] == "planner"]
        others = [a for a in AGENTS if a["name"] not in ("researcher", "planner")]
        assert len(researcher) == 1, "应有 1 个 researcher"
        assert len(planner) == 1, "应有 1 个 planner"
        assert len(others) == 3, "应有 3 个其他 Agent (budget, foodie, safety)"


class TestCrewBuild:
    """Crew 构建测试"""

    def test_build_travel_crew_callable(self):
        """build_travel_crew 应是可调用的"""
        from app.crew import build_travel_crew
        assert callable(build_travel_crew)

    def test_looks_like_tool_dump(self):
        """工具调用伪正文应被识别"""
        from app.crew import _looks_like_tool_dump, _is_valid_plan_text
        assert _looks_like_tool_dump("")
        assert _looks_like_tool_dump('<tool_call>\n{"destination":"东京"}\n')
        assert _looks_like_tool_dump('function=search_knowledge')
        assert not _looks_like_tool_dump("# 东京行程\n\n## Day 1\n" + "详细内容" * 80)
        assert not _is_valid_plan_text("太短")
        assert _is_valid_plan_text("# 方案\n" + "x" * 200)

    def test_build_travel_crew_signature(self):
        """build_travel_crew 应接受正确参数"""
        import inspect
        from app.crew import build_travel_crew
        sig = inspect.signature(build_travel_crew)
        params = list(sig.parameters.keys())
        assert "destination" in params
        assert "days" in params
        assert "budget" in params
        assert "interests" in params
        assert "step_callback" in params
