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


# ── 降级链 destination 透传（修复「梅州返回吉隆坡」）─────────


class TestFallbackKeepsDestination:
    """降级到 search_knowledge 时必须带上 destination。

    search_knowledge 靠 destination 做归属过滤，缺了它就退化成
    「全库找语义最近」，库外目的地（实测梅州）会命中吉隆坡/新加坡。
    """

    def test_adapt_args_passes_destination(self):
        from app.crew import _adapt_args

        args = _adapt_args(
            "optimize_route", "search_knowledge",
            {"destination": "梅州", "days": 3},
        )
        assert args["destination"] == "梅州"
        assert args["query"] == "梅州"

    def test_adapt_args_keeps_original_query(self):
        from app.crew import _adapt_args

        args = _adapt_args(
            "search_knowledge", "search_knowledge",
            {"query": "梅州 景点", "destination": "梅州"},
        )
        assert args["query"] == "梅州 景点"
        assert args["destination"] == "梅州"

    def test_recovery_injects_destination_when_missing(self):
        """_adapt_args 没带出来时，recovery 层兜底补上。"""
        from app.crew import _execute_tool_with_recovery, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        captured = []

        def fake_execute(tool, args):
            captured.append((tool, dict(args)))
            if tool == "optimize_route":
                # 让原工具失败，才会走到降级分支
                return {"success": False, "text": "路线优化失败: 未找到目的地数据"}
            return {"success": True, "text": "ok"}

        with patch("app.crew._execute_tool", side_effect=fake_execute):
            _execute_tool_with_recovery(
                "optimize_route", {"destination": "梅州", "days": 3},
                "planner_all", trace, destination="梅州",
            )
        last_tool, last_args = captured[-1]
        assert last_tool == "search_knowledge"
        # 这一条是回归防线：曾经这里只有 query，没有 destination
        assert last_args.get("destination") == "梅州"


# ── 终局结论不应触发重试/降级（修复重复搜索死循环）──────────


class TestTerminalResultNoRetry:
    """「知识库未覆盖」是确定结论，不是执行故障。

    曾经它被当成 failed，触发重试+降级；库外城市每次必然失败，
    导致同一工具被反复调用（实测 4 次），8 轮额度烧光。
    """

    def test_uncovered_destination_marks_terminal(self):
        from app.crew import _execute_tool

        with patch("app.crew.search_knowledge", return_value=[]), \
             patch("app.crew.is_destination_covered", return_value=False):
            result = _execute_tool(
                "search_knowledge",
                {"query": "梅州 景点", "destination": "梅州"},
            )
        assert result["success"] is False
        assert result.get("terminal") is True
        assert "未覆盖" in result["text"]

    def test_terminal_skips_retry_and_fallback(self):
        from app.crew import _execute_tool_with_recovery, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        calls = []

        def counting_execute(tool, args):
            calls.append(tool)
            return {"success": False, "terminal": True, "text": "知识库未覆盖「梅州」"}

        with patch("app.crew._execute_tool", side_effect=counting_execute):
            out = _execute_tool_with_recovery(
                "search_knowledge", {"query": "梅州", "destination": "梅州"},
                "planner_all", trace, destination="梅州",
            )
        # 只应执行 1 次：不重试、不降级
        assert calls == ["search_knowledge"]
        assert "未覆盖" in out
        assert trace.retries == 0

    def test_ordinary_failure_still_retries(self):
        """普通失败（非终局）必须保持原有重试+降级行为，别误伤。"""
        from app.crew import _execute_tool_with_recovery, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        calls = []

        def counting_execute(tool, args):
            calls.append(tool)
            return {"success": False, "text": "工具执行异常: 网络超时"}

        with patch("app.crew._execute_tool", side_effect=counting_execute):
            _execute_tool_with_recovery(
                "get_weather", {"destination": "梅州"},
                "planner_all", trace, destination="梅州",
            )
        # 原工具重试一次 + 降级到 web_search，共 3 次
        assert len(calls) == 3
        assert calls[0] == "get_weather" and calls[1] == "get_weather"

    def test_terminal_result_is_cached(self):
        """终局结论可缓存：否则同一未覆盖目的地每次都要白跑一遍检索。"""
        from app.crew import _execute_tool
        from app.tool_cache import configure, get_tool_cache

        # 缓存默认关闭（由 main.py 启动时按配置开启），此处显式开启
        cache = configure(True, max_size=64)
        cache.clear()
        calls = []

        def counting(query, **kw):
            calls.append(query)
            return []

        try:
            with patch("app.crew.search_knowledge", side_effect=counting), \
                 patch("app.crew.is_destination_covered", return_value=False):
                args = {"query": "梅州 景点", "destination": "梅州"}
                _execute_tool("search_knowledge", args)
                _execute_tool("search_knowledge", args)
            assert len(calls) == 1, "第二次应命中缓存"
        finally:
            cache.clear()
            configure(False)


# ── 无解任务提前收束（避免 250s 硬凑长文）───────────────────


class TestDataLedger:
    """数据账本：识别「目的地没资料」的无解任务，提前收束。

    实测背景：梅州不在知识库，模型把 search_knowledge 调了 8 次
    （6 次拿到同一句「未覆盖」），再用 204 秒硬写 2380 字满是
    「暂无数据」的长文，总耗时 251 秒。
    """

    def _ledger(self):
        from app.crew import _DataLedger
        return _DataLedger()

    def test_not_hopeless_when_attraction_data_exists(self):
        """拿到景点数据就不算无解，哪怕其它工具失败。"""
        led = self._ledger()
        for _ in range(5):
            led.record("search_knowledge", "1. 【景点】京都 - 清水寺", ok=False)
        led.record("search_knowledge", "1. 【景点】京都 - 清水寺", ok=True)
        assert led.hopeless is False

    def test_not_hopeless_before_enough_calls(self):
        """只试了 2 次还不能下结论，别过早放弃。"""
        led = self._ledger()
        led.record("search_knowledge", "知识库未覆盖", ok=False)
        led.record("web_search", "网络搜索未找到相关信息", ok=False)
        assert led.hopeless is False

    def test_hopeless_when_core_missing_and_mostly_empty(self):
        led = self._ledger()
        led.record("search_knowledge", "知识库未覆盖「梅州」", ok=False)
        led.record("search_knowledge", "知识库未覆盖「梅州」", ok=False)
        led.record("web_search", "网络搜索未找到相关信息", ok=False)
        led.record("get_weather", "梅州天气实况", ok=True)
        assert led.hopeless is True

    def test_hopeless_false_when_success_rate_high(self):
        """空手率不到一半就不算无解（说明只是个别工具失败）。"""
        led = self._ledger()
        led.record("search_knowledge", "知识库未覆盖", ok=False)
        for _ in range(4):
            led.record("get_weather", "天气实况", ok=True)
        assert led.hopeless is False

    def test_snapshot_reports_both_buckets(self):
        led = self._ledger()
        led.record("get_weather", "天气", ok=True)
        led.record("search_knowledge", "知识库未覆盖", ok=False)
        snap = led.snapshot()
        assert "get_weather" in snap["filled"]
        assert "search_knowledge" in snap["empty"]
        assert snap["tool_calls"] == 2


class TestEarlyBailOnHopelessTask:
    """无解任务应当提前收束，不再耗完所有工具轮次。"""

    def test_bail_out_emits_honest_short_answer(self):
        from app.crew import _run_agent_with_tools, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        rounds = []

        def fake_create(**kw):
            # 每轮都请求调工具（模拟模型反复换个词再问）
            rounds.append(kw)
            tc = MagicMock()
            tc.function.name = "search_knowledge"
            tc.function.arguments = '{"query": "梅州 景点", "destination": "梅州"}'
            tc.id = f"call_{len(rounds)}"
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.tool_calls = [tc]
            resp.choices[0].message.content = ""
            return resp

        # 工具每次都空手，模拟「目的地没资料」。
        # 注意：记账发生在 _execute_tool_with_recovery 内部，
        # 所以这里要 mock 更下层的 _execute_tool，而不是整个 recovery。
        with patch("app.crew._execute_tool",
                   return_value={"success": False,
                                 "text": "知识库未覆盖「梅州」，本地无可用资料。"}), \
             patch("app.crew.web_search_available", return_value=False), \
             patch("app.crew._stream_final_output",
                   return_value="# 梅州\n暂未收录。") as bail_stream, \
             patch("app.crew._is_valid_plan_text", return_value=True):
            client = MagicMock()
            client.chat.completions.create.side_effect = fake_create
            out = _run_agent_with_tools(
                client, {"name": "planner_all", "label": "规划师", "system": "s"},
                "规划梅州", trace, max_tool_rounds=8,
                destination="梅州", enable_reflection=False,
            )
        assert out == "# 梅州\n暂未收录。"
        # 关键断言：没有耗完 8 轮，提前收束了
        assert len(rounds) < 8, f"应在判定无解后收束，实际跑了 {len(rounds)} 轮"
        assert bail_stream.called

    def test_solvable_task_runs_to_completion(self):
        """能拿到数据的任务不该被误判成无解。"""
        from app.crew import _run_agent_with_tools, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        rounds = []

        def fake_create(**kw):
            rounds.append(kw)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            # 第一轮调工具，第二轮直接给正文
            if len(rounds) == 1:
                tc = MagicMock()
                tc.function.name = "search_knowledge"
                tc.function.arguments = '{"query": "京都 景点", "destination": "京都"}'
                tc.id = "call_1"
                resp.choices[0].message.tool_calls = [tc]
                resp.choices[0].message.content = ""
            else:
                resp.choices[0].message.tool_calls = None
                resp.choices[0].message.content = "# 京都3日方案\n" + "正文。" * 60
            return resp

        with patch("app.crew._execute_tool",
                   return_value={"success": True,
                                 "text": "1. 【景点】京都 - 清水寺（历史）：门票400日元"}), \
             patch("app.crew._is_valid_plan_text", return_value=True):
            client = MagicMock()
            client.chat.completions.create.side_effect = fake_create
            out = _run_agent_with_tools(
                client, {"name": "planner_all", "label": "规划师", "system": "s"},
                "规划京都", trace, max_tool_rounds=8,
                destination="京都", enable_reflection=False,
            )
        assert "京都" in out
        # 正常完成：第二轮拿到正文，没有触发提前收束
        assert len(rounds) == 2


# ── 空输出重试（思维链模型吃满 token）───────────────────────


class TestEmptyStreamRetry:
    """思维链模型会把 token 花在 reasoning_content 上，正文可能为空。

    实测：finish_reason=length 且 content 长度 0，导致「最终输出无效（len=0）」。
    """

    def _fake_stream(self, chunks):
        for c in chunks:
            yield c

    def test_empty_stream_falls_back_to_non_stream(self):
        from app.crew import _stream_final_output

        # 流式：全部空 delta，finish_reason=length
        def empty_chunk():
            ch = MagicMock()
            ch.choices = [MagicMock()]
            ch.choices[0].delta.content = None
            ch.choices[0].finish_reason = "length"
            return ch

        client = MagicMock()
        client.chat.completions.create.side_effect = [
            iter([empty_chunk()]),
            MagicMock(**{
                "choices": [MagicMock(message=MagicMock(content="# 正常正文"))]
            }),
        ]
        out = _stream_final_output(
            client, {"name": "planner_all", "label": "规划师"},
            [{"role": "user", "content": "q"}], None,
            token_callback=lambda a, t: None,
        )
        assert out == "# 正常正文"

    def test_llm_max_tokens_used(self):
        """生成时必须用配置驱动的 token 额度，而不是硬编码 4096。"""
        from app.crew import _budget_for_chars, _stream_final_output
        from app.config import settings

        captured = {}

        def capture(**kw):
            captured.update(kw)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "# 正文"
            return resp

        client = MagicMock()
        client.chat.completions.create.side_effect = capture
        _stream_final_output(
            client, {"name": "planner_all", "label": "规划师"},
            [{"role": "user", "content": "q"}], None,
            token_callback=None,
        )
        assert captured["max_tokens"] == _budget_for_chars(settings.max_output_chars)
        assert captured["max_tokens"] > 4096


class TestOutputLengthCap:
    """生成时间几乎全由正文长度决定（实测 6200 字 ≈ 163s）。

    prompt 里写「1800~2800 字」模型不听（实测仍写到 6000+），
    所以必须从 token 额度上物理掐断，否则耗时压不下来。
    """

    def test_budget_scales_with_chars(self):
        from app.crew import _budget_for_chars

        small = _budget_for_chars(1000)
        big = _budget_for_chars(3000)
        assert small < big, "字数上限越大，token 额度也应越大"

    def test_budget_always_below_ceiling(self):
        """生效前提：按字数算出的额度必须小于安全天花板，否则等于没设限。"""
        from app.crew import _budget_for_chars
        from app.config import settings

        budget = _budget_for_chars(settings.max_output_chars)
        assert budget < settings.llm_max_tokens, (
            f"额度 {budget} 未低于天花板 {settings.llm_max_tokens}，字数上限不生效"
        )

    def test_zero_means_unlimited(self):
        from app.crew import _budget_for_chars
        from app.config import settings

        assert _budget_for_chars(0) == settings.llm_max_tokens

    def test_budget_has_room_for_thinking_chain(self):
        """思维链模型会先想再写；额度只顾正文会让思考吃满、正文为空。"""
        from app.crew import _THINKING_RESERVE, _budget_for_chars

        chars = 3200
        budget = _budget_for_chars(chars)
        body_need = chars * 2.0
        assert budget - body_need >= _THINKING_RESERVE * 0.9

    def test_stream_uses_capped_budget(self):
        """端到端：真正发出去的 max_tokens 是被字数裁过的值。"""
        from app.crew import _budget_for_chars, _stream_final_output
        from app.config import settings

        captured = {}

        def capture(**kw):
            captured.update(kw)
            resp = MagicMock()
            resp.choices = [MagicMock()]
            resp.choices[0].message.content = "# 正文"
            return resp

        client = MagicMock()
        client.chat.completions.create.side_effect = capture
        _stream_final_output(
            client, {"name": "planner_all", "label": "规划师"},
            [{"role": "user", "content": "q"}], None, token_callback=None,
        )
        expect = _budget_for_chars(settings.max_output_chars)
        assert captured["max_tokens"] == expect
        assert captured["max_tokens"] < settings.llm_max_tokens

    def test_truncated_output_is_accepted(self):
        """被截断的正文仍然是有效正文，不能因为 finish_reason=length 就丢掉。"""
        from app.crew import _stream_final_output

        def chunk():
            ch = MagicMock()
            ch.choices = [MagicMock()]
            ch.choices[0].delta.content = "# 东京行程\nDay 1 ..."
            ch.choices[0].finish_reason = "length"
            return ch

        client = MagicMock()
        client.chat.completions.create.side_effect = [iter([chunk()])]
        out = _stream_final_output(
            client, {"name": "planner_all", "label": "规划师"},
            [{"role": "user", "content": "q"}], None,
            token_callback=lambda a, t: None,
        )
        assert "东京行程" in out





class TestReflectionCannotBreakOutput:
    """自我反思是正文校验之后的一道后门。

    它可能把已通过的正文覆盖成 "PASS"（模型把反思 prompt 的
    "全部满足回复 PASS" 误当成对输出的回答），用户最终只看到 PASS。
    """

    def _long_plan(self) -> str:
        return "# 梅州3日方案\n\n## 一、目的地研究\n" + "梅州客家文化介绍。" * 30

    def test_pass_from_fix_is_rejected(self):
        from app.crew import _self_reflect, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        good = self._long_plan()

        # 第一次调用：反思不通过；第二次：修正却回了 "PASS"
        reflect = MagicMock()
        reflect.choices = [MagicMock()]
        reflect.choices[0].message.content = "缺少预算部分"
        fix = MagicMock()
        fix.choices = [MagicMock()]
        fix.choices[0].message.content = "PASS"
        client = MagicMock()
        client.chat.completions.create.side_effect = [reflect, fix]

        out = _self_reflect(
            client, {"name": "planner_all", "label": "规划师", "system": "s"},
            [{"role": "user", "content": "q"}], good, trace, None,
            enabled=True,
        )
        assert out == good, "修正返回 PASS 时必须保留原文，不能被覆盖"

    def test_empty_fix_is_rejected(self):
        from app.crew import _self_reflect, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        good = self._long_plan()

        reflect = MagicMock()
        reflect.choices = [MagicMock()]
        reflect.choices[0].message.content = "缺少预算"
        fix = MagicMock()
        fix.choices = [MagicMock()]
        fix.choices[0].message.content = ""
        client = MagicMock()
        client.chat.completions.create.side_effect = [reflect, fix]

        out = _self_reflect(
            client, {"name": "planner_all", "label": "规划师", "system": "s"},
            [{"role": "user", "content": "q"}], good, trace, None,
            enabled=True,
        )
        assert out == good

    def test_valid_fix_is_adopted(self):
        """正常修正（返回更完整的正文）仍要生效，别把好功能一起关掉。"""
        from app.crew import _self_reflect, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        good = self._long_plan()
        better = good + "\n\n## 二、预算\n" + "预算明细。" * 30

        reflect = MagicMock()
        reflect.choices = [MagicMock()]
        reflect.choices[0].message.content = "缺少预算部分"
        fix = MagicMock()
        fix.choices = [MagicMock()]
        fix.choices[0].message.content = better
        client = MagicMock()
        client.chat.completions.create.side_effect = [reflect, fix]

        out = _self_reflect(
            client, {"name": "planner_all", "label": "规划师", "system": "s"},
            [{"role": "user", "content": "q"}], good, trace, None,
            enabled=True,
        )
        assert out == better

    def test_guard_reverts_when_reflection_returns_junk(self):
        """最后一道闸：反思把输出改废了就回退到反思前版本。"""
        from app.crew import _run_agent_with_tools, AgentTrace

        trace = AgentTrace(name="planner_all", label="规划师")
        good = self._long_plan()

        with patch("app.crew._ensure_valid_final_output", return_value=good), \
             patch("app.crew._self_reflect", return_value="PASS"), \
             patch("app.crew._process_tool_calls"):
            out = _run_agent_with_tools(
                MagicMock(), {"name": "planner_all", "label": "规划师",
                              "system": "s"},
                "规划梅州", trace, destination="梅州",
                enable_reflection=True,
            )
        assert out == good


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

    def test_bing_is_first_keyless_backend(self):
        """必应是免密钥后端里的首选：国内唯一可达源。

        （官方 API 排在它前面，但要配 key 才启用。）
        """
        from app.tools import web_search as ws

        assert ws.BACKEND_ORDER[0] == "official"
        assert ws.BACKEND_ORDER.index("bing") < ws.BACKEND_ORDER.index("duckduckgo")
        assert ws.BACKEND_ORDER.index("bing") < ws.BACKEND_ORDER.index("wikipedia")

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


class TestOfficialSearchBackends:
    """国内官方搜索 API 后端（智谱 / 百度 / 博查）接线测试。

    背景：必应 RSS 是未公开接口（临时方案），官方 API 才是正式方案。
    这里不真调 API（没有 key），只验证「配置→选择→降级」这条链路。
    """

    def test_no_key_means_official_backend_skipped(self):
        from app.tools import web_search as ws

        assert ws.available_official_providers() == []
        assert ws._backend_usable("official") is False

    def test_official_is_first_in_order(self):
        """官方 API 有 key 时应优先于免密钥的必应 RSS。"""
        from app.tools import web_search as ws

        assert ws.BACKEND_ORDER[0] == "official"
        assert "bing" in ws.BACKEND_ORDER

    def test_provider_selected_when_key_present(self):
        from app.tools import web_search as ws
        from app.config import settings

        settings.zhipu_api_key = "test-key"
        try:
            assert ws.available_official_providers() == ["zhipu"]
            assert ws._backend_usable("official") is True
        finally:
            settings.zhipu_api_key = ""

    def test_explicit_provider_wins(self):
        from app.tools import web_search as ws
        from app.config import settings

        settings.zhipu_api_key = "k1"
        settings.bocha_api_key = "k2"
        settings.search_api_provider = "bocha"
        try:
            assert ws.available_official_providers() == ["bocha"]
        finally:
            settings.zhipu_api_key = ""
            settings.bocha_api_key = ""
            settings.search_api_provider = ""

    def test_broken_key_does_not_break_fallback_chain(self):
        """官方 API key 错了也不该拖垮整体：应继续落到必应。"""
        from app.tools import web_search as ws
        from app.config import settings

        settings.zhipu_api_key = "definitely-invalid-key"
        ws.reset_backend_state()
        try:
            with patch.object(ws, "_zhipu_search", return_value=[]), \
                 patch.object(ws, "_bing_search", return_value=[
                     {"title": "T", "snippet": "s", "url": "", "source": "Bing"}
                 ]):
                results = ws.web_search("东京 景点", max_results=1)
            assert len(results) == 1
            assert results[0]["source"] == "Bing"
        finally:
            settings.zhipu_api_key = ""
            ws.reset_backend_state()

    def test_get_setting_falls_back_to_env(self):
        """配置模块读不到时退回环境变量（脚本单独跑时也要能用）。"""
        import os

        from app.tools import web_search as ws

        os.environ["BOCHA_API_KEY"] = "env-key"
        try:
            with patch.dict("sys.modules", {"app.config": None}):
                assert ws._get_setting("bocha_api_key") == "env-key"
        finally:
            os.environ.pop("BOCHA_API_KEY", None)


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
