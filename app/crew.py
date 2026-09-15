"""
5 Agent 协作引擎 — Function Calling + 自我反思 + 3层执行 + 动态路由。

架构：
  1. Router Agent 分析用户意图，动态决定调用哪些 Agent
  2. Layer 1: Researcher → 信息收集
  3. Layer 2: Planner → 行程规划（共享 Researcher 成果）
  4. Layer 3（并行）: Budget + Foodie + Safety（共享 Researcher + Planner 成果）
  5. 每个 Agent: 工具调用 → 自我反思
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from openai import OpenAI

from app.config import settings

# LangSmith 可观测性（可选，未配置时降级为 no-op）
try:
    from langsmith import traceable, wrap_openai
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGSMITH_TRACING"] = "true"
        _LANGSMITH_ENABLED = True
        logger = logging.getLogger(__name__)
        logger.info(f"LangSmith tracing enabled → project={settings.langsmith_project}")
    else:
        _LANGSMITH_ENABLED = False
        # no-op decorator：未配置时不影响任何功能
        def traceable(*args, **kwargs):
            if len(args) == 1 and callable(args[0]) and not kwargs:
                return args[0]
            def decorator(fn):
                return fn
            return decorator
        def wrap_openai(client):
            return client
except ImportError:
    _LANGSMITH_ENABLED = False
    def traceable(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        def decorator(fn):
            return fn
        return decorator
    def wrap_openai(client):
        return client
from app.tools.real_weather import get_weather, format_weather
from app.tools.real_exchange import get_exchange_rate, format_exchange
from app.tools.knowledge_search import (
    search_knowledge,
    format_search_results,
    search_with_context,
    is_destination_covered,
)
from app.tools.route_optimizer import optimize_route_from_knowledge, format_optimized_route
from app.tools.budget_optimizer import optimize_budget, format_budget_plans
from app.tools.web_search import (web_search, format_web_search,
                                  web_search_available)
from app.agents import (
    AGENTS,
    AGENT_MAP as _AGENT_MAP,
    REFLECTION_PROMPTS,
    SINGLE_AGENT,
    PIPELINE_MODES,
    select_agents_for_mode,
    split_layers,
)

logger = logging.getLogger(__name__)

from app.usage import get_usage
from app.tool_metrics import (
    OK,
    OK_RETRY,
    OK_FALLBACK,
    FAILED,
    TERMINAL,
    # 用别名：本模块已有一个同名的 AgentTrace.tool_calls 记录类型，避免覆盖
    ToolCallRecord as ToolMetricRecord,
    get_tool_metrics,
)
from app.tool_cache import cache_key, get_tool_cache


def _note_llm_io(messages: list, output_text: str) -> None:
    """粗计 LLM 输入/输出字符，供评测估算 token 成本。"""
    try:
        in_c = 0
        for m in messages:
            if isinstance(m, dict):
                c = m.get("content") or ""
            else:
                c = getattr(m, "content", None) or ""
            in_c += len(str(c)) if c else 0
        get_usage().add_llm(in_c, len(output_text or ""))
    except Exception:
        pass


# ── ReAct 推理记录 ──────────────────────────────────────────


@dataclass
class ReActStep:
    """单步 ReAct 推理记录"""
    thought: str = ""
    action: str = ""
    action_args: dict = field(default_factory=dict)
    observation: str = ""


@dataclass
class ToolCallRecord:
    agent: str
    tool: str
    args: dict
    result_preview: str


@dataclass
class AgentTrace:
    """单个 Agent 的完整执行轨迹"""
    name: str
    label: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    react_steps: list[ReActStep] = field(default_factory=list)
    retries: int = 0
    output_length: int = 0
    reflection_passed: bool = False


# ── 工具定义（OpenAI function calling 格式）──────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_attraction_context",
            "description": "检索景点详细信息，包括附近景点和同类景点推荐。当需要深入了解某个景点或规划景点间路线时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "景点名称或查询，如'浅草寺'或'东京寺庙'",
                    },
                    "destination": {
                        "type": "string",
                        "description": "限定城市（可选）",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取目的地的实时天气信息，包括当前温度、天气状况和未来3天预报。当用户询问天气、需要根据天气安排行程时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "目的地名称，如'东京'或'巴黎'",
                    }
                },
                "required": ["destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_exchange_rate",
            "description": "获取美元到目的地当地货币的汇率，并计算换算后的金额。当需要做预算、价格换算时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "目的地名称，如'东京'或'巴黎'",
                    },
                    "amount_usd": {
                        "type": "number",
                        "description": "要换算的美元金额",
                    },
                },
                "required": ["destination", "amount_usd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "从目的地知识库中检索信息。可查询景点、餐厅、交通、预算、安全等。当需要了解目的地的具体信息时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索关键词或问题，如'东京的寺庙景点'或'巴黎的米其林餐厅'",
                    },
                    "destination": {
                        "type": "string",
                        "description": "限定城市（必填）。缺失时检索会命中语义相近的其它城市内容，"
                                       "因此调用方会自动用本次规划的目的地兜底",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["attraction", "restaurant", "transport", "budget", "safety", "basic"],
                        "description": "限定类别（可选）：attraction=景点, restaurant=餐厅, transport=交通, budget=预算, safety=安全, basic=基本信息",
                    },
                },
                "required": ["query", "destination"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_route",
            "description": "用贪心最近邻+2-opt算法优化景点游览路线（基于真实GPS坐标+Haversine距离）。输入目的地和天数，返回分日优化路线。",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "目的地名称",
                    },
                    "days": {
                        "type": "integer",
                        "description": "旅行天数",
                    },
                },
                "required": ["destination", "days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_budget",
            "description": "使用线性规划(PuLP)在约束条件下优化预算分配，生成经济/舒适/豪华三档方案。",
            "parameters": {
                "type": "object",
                "properties": {
                    "destination": {
                        "type": "string",
                        "description": "目的地名称",
                    },
                    "total_usd": {
                        "type": "number",
                        "description": "总预算（美元）",
                    },
                    "days": {
                        "type": "integer",
                        "description": "旅行天数",
                    },
                },
                "required": ["destination", "total_usd", "days"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "在互联网上搜索实时信息（DuckDuckGo + Wikipedia）。当知识库中没有的信息（如最新活动、节日、临时关闭、最新价格变动等）需要使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如'东京 2024 樱花花期'或'巴黎 奥运 期间 交通'",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大结果数（默认5）",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# Agent 定义已拆至 app.agents（便于单测与消融）



def _execute_tool(name: str, arguments: dict) -> dict:
    """
    执行工具调用（带 TTL 缓存与指标埋点），返回结构化结果。

    Returns:
        {"success": True, "text": "格式化文本"} 或
        {"success": False, "text": "错误信息"}

    命中缓存时额外带 "_cached": True，供上层区分「真实执行」与「复用结果」——
    复用的调用不计入原始成功率，否则缓存会掩盖工具的真实稳定性。
    """
    get_usage().add_tool(name)
    cache = get_tool_cache()
    key = cache_key(name, arguments)

    if cache.enabled:
        hit = cache.get(key)
        if hit is not None:
            result = dict(hit)
            result["_cached"] = True
            return result

    started = time.perf_counter()
    result = _execute_tool_uncached(name, arguments)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    get_tool_metrics().record_raw(name, bool(result.get("success")), elapsed_ms)

    # 只缓存「成功」与「终局结论」两类结果：
    #   - 成功：复用省时间
    #   - 终局结论：确定性的（知识库有没有这个城市不会在两次调用间改变），
    #     不缓存的话同一个未覆盖目的地会被反复问，每次白跑一遍 5s 检索
    # 唯独不缓存「执行失败」：那会把一次瞬时故障固化成持久故障。
    if (result.get("success") or result.get("terminal")) and cache.enabled:
        cache.set(key, {k: v for k, v in result.items() if not k.startswith("_")})
    return result


def _execute_tool_uncached(name: str, arguments: dict) -> dict:
    """真正执行工具本体（不含缓存与埋点），便于单测隔离。"""
    try:
        if name == "get_weather":
            data = get_weather(arguments["destination"])
            if "error" in data:
                return {"success": False, "text": f"天气获取失败: {data['error']}"}
            return {"success": True, "text": format_weather(data)}

        elif name == "get_exchange_rate":
            data = get_exchange_rate(arguments["destination"], arguments["amount_usd"])
            if "error" in data:
                return {"success": False, "text": f"汇率获取失败: {data['error']}"}
            return {"success": True, "text": format_exchange(data)}

        elif name == "search_knowledge":
            results = search_knowledge(
                query=arguments["query"],
                destination=arguments.get("destination"),
                category=arguments.get("category"),
                top_k=5,
            )
            if not results:
                dest = arguments.get("destination") or ""
                if dest and not is_destination_covered(dest):
                    # 区分「库里没有这个城市」和「城市有但没查到」——
                    # 前者应换城市或走 web_search，后者只是这次没命中
                    # 注意：不能直接写"改用 web_search"，联网也可能不可用
                    # （国内实测海外引擎全超时，见 docs/缺陷记录_web搜索国内不可用.md）
                    hint = ("可尝试 web_search 联网查询"
                            if web_search_available()
                            else "联网搜索当前不可用")
                    return {
                        "success": False,
                        # terminal=True 表示「这是个确定结论，不是执行故障」：
                        # 重试和降级都救不了它，重试只会白等一轮。
                        "terminal": True,
                        "text": f"知识库未覆盖「{dest}」，本地无可用资料。{hint}；"
                                f"两者都无结果时，请直接告知用户「暂不支持该目的地」，"
                                f"不要用其它城市的内容代替。",
                    }
                return {"success": False, "text": "知识库未找到相关信息"}
            return {"success": True, "text": format_search_results(results)}

        elif name == "optimize_route":
            result = optimize_route_from_knowledge(
                destination=arguments["destination"],
                days=arguments["days"],
            )
            if "error" in result:
                return {"success": False, "text": f"路线优化失败: {result['error']}"}
            return {"success": True, "text": format_optimized_route(result)}

        elif name == "optimize_budget":
            plans = optimize_budget(
                destination=arguments["destination"],
                total_usd=arguments["total_usd"],
                days=arguments["days"],
            )
            if not plans:
                return {"success": False, "text": "预算优化失败: 未找到目的地数据"}
            return {"success": True, "text": format_budget_plans(plans)}

        elif name == "search_attraction_context":
            text = search_with_context(
                query=arguments["query"],
                destination=arguments.get("destination", ""),
            )
            if not text or text == "未找到相关信息。":
                return {"success": False, "text": "未找到景点详情"}
            return {"success": True, "text": text}

        elif name == "web_search":
            results = web_search(
                query=arguments["query"],
                max_results=arguments.get("max_results", 5),
            )
            if not results:
                if not web_search_available():
                    # 区分「搜了但没结果」和「根本连不上」：
                    # 前者可以换个词再试，后者再试也是白等
                    return {
                        "success": False,
                        "text": "联网搜索当前不可用（所有搜索后端均无响应），"
                                "请依赖本地知识库，或如实告知用户资料不足。",
                    }
                return {"success": False, "text": "网络搜索未找到相关信息"}
            return {"success": True, "text": format_web_search(results)}

        else:
            return {"success": False, "text": f"未知工具: {name}"}

    except Exception as e:
        logger.error(f"工具 {name} 执行失败: {e}")
        return {"success": False, "text": f"工具执行异常: {str(e)}"}


# ── ReAct 参数适配：LLM 传的参数名可能和函数期望的不一致 ─────────

def _normalize_tool_args(name: str, args: dict, destination: str = "") -> dict:
    """
    在文本 ReAct 模式下，LLM 可能传错参数名。
    此函数根据工具名自动适配参数：
      - search_knowledge: 缺 query 时用 destination/category/raw 代替
      - get_exchange_rate: 缺 amount_usd 时默认 1000
      - web_search: 缺 query 时用 destination/raw 代替
      - get_weather: 缺 destination 时用 query/raw 代替
    """
    adapted = dict(args)

    # 用实际目的地作为兜底，而非硬编码"东京"
    fallback_dest = destination or adapted.get("destination", "")

    if name == "search_knowledge":
        if "query" not in adapted or not adapted["query"]:
            adapted["query"] = (
                adapted.get("destination")
                or adapted.get("category")
                or adapted.get("raw")
                or fallback_dest
                or "travel"
            )
        # destination 必须带上：缺失时检索不做归属过滤，库外目的地会命中
        # 语义相近的其它城市内容（详见 docs/缺陷记录_检索静默失败.md）
        if not adapted.get("destination"):
            adapted["destination"] = fallback_dest

    elif name == "get_exchange_rate":
        if "amount_usd" not in adapted:
            adapted["amount_usd"] = 1000
        if "destination" not in adapted:
            adapted["destination"] = adapted.get("query") or fallback_dest

    elif name == "web_search":
        if "query" not in adapted or not adapted["query"]:
            adapted["query"] = (
                adapted.get("destination")
                or adapted.get("raw")
                or fallback_dest
                or "travel"
            )

    elif name == "get_weather":
        if "destination" not in adapted:
            adapted["destination"] = (
                adapted.get("query")
                or adapted.get("city")
                or fallback_dest
            )

    return adapted


# ── 工具错误恢复：失败重试 + 降级 fallback ─────────────────────

_TOOL_FALLBACKS: dict[str, list[str]] = {
    "search_knowledge": ["web_search"],
    "search_attraction_context": ["search_knowledge", "web_search"],
    "web_search": ["search_knowledge"],
    "get_weather": ["web_search"],
    "get_exchange_rate": ["web_search"],
    "optimize_route": ["search_knowledge"],
    "optimize_budget": ["search_knowledge"],
}


def _adapt_args(orig_tool: str, fallback_tool: str, args: dict) -> dict:
    """将原工具的参数适配为 fallback 工具的参数"""
    if fallback_tool == "web_search":
        if orig_tool == "get_weather":
            return {"query": f"{args.get('destination', '')} 天气 forecast"}
        if orig_tool == "get_exchange_rate":
            return {"query": f"USD to {args.get('destination', '')} exchange rate"}
        if orig_tool == "search_knowledge":
            return {"query": args.get("query", "")}
        if orig_tool == "optimize_route":
            return {"query": f"{args.get('destination', '')} tourist attractions route"}
        if orig_tool == "optimize_budget":
            return {"query": f"{args.get('destination', '')} travel budget costs"}
        return {"query": str(args)}
    if fallback_tool == "search_knowledge":
        # destination 必须一起带上：search_knowledge 靠它做归属过滤，
        # 缺失时会退化成「全库找语义最近」，库外目的地会命中无关城市
        # （实测梅州 → 吉隆坡/新加坡，见 docs/缺陷记录_检索静默失败.md）
        return {
            "query": str(args.get("query") or args.get("destination") or ""),
            "destination": str(args.get("destination") or ""),
        }
    return args


def _record_tool_outcome(
    tool: str,
    status: str,
    started: float,
    retries: int = 0,
    fallback_to: str = "",
    cached: bool = False,
    error: str = "",
) -> None:
    """把一次逻辑调用（工具 + 重试 + 降级）的最终结果写入指标。

    埋点属于旁路观测，任何异常都不能影响生成主流程，故整体兜底。
    """
    try:
        get_tool_metrics().record(
            ToolMetricRecord(
                tool=tool,
                status=status,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                retries=retries,
                fallback_to=fallback_to,
                cached=cached,
                error=error,
            )
        )
    except Exception:
        logger.debug("工具指标记录失败（不影响主流程）", exc_info=True)


@traceable(run_name="tool_execution")
def _execute_tool_with_recovery(
    name: str,
    arguments: dict,
    agent_name: str,
    trace: AgentTrace,
    max_retries: int = 1,
    destination: str = "",
    ledger: Optional["_DataLedger"] = None,
) -> str:
    """
    带错误恢复的工具执行：
      1. 执行原工具
      2. 失败则重试一次
      3. 仍失败则尝试 fallback 工具
      4. 全部失败则返回降级提示

    ledger 非空时，把「这次逻辑调用有没有拿到有效数据」记进账本，
    供上层判断是否属于无解任务（详见 _DataLedger）。
    记账在此处做而不是在调用方：只有这里能拿到结构化的 result，
    靠解析返回文本判断成败太脆（"知识库未找到相关信息"这类文案会漏判）。
    """
    started = time.perf_counter()
    result = _execute_tool(name, _normalize_tool_args(name, arguments, destination))
    cached = bool(result.get("_cached"))

    if result["success"]:
        _record_tool_outcome(name, OK, started, cached=cached)
        if ledger is not None:
            ledger.record(name, result["text"], ok=True)
        return result["text"]

    # ── 终局结论：这是确定答案，不是执行故障 ──
    # 典型场景：知识库未覆盖该目的地。重试和降级都改变不了结论，
    # 但重试要白等一轮、降级还要再跑一次搜索，库外城市会被反复问同一件事
    # （实测同一工具被调用 4 次，8 轮额度烧光，耗时 60s → 250s）。
    if result.get("terminal"):
        _record_tool_outcome(name, TERMINAL, started, cached=cached)
        if ledger is not None:
            ledger.record(name, result["text"], ok=False)
        return result["text"]

    # ── 重试一次 ──
    if max_retries > 0:
        logger.warning(f"  ⚠️ [{agent_name}] 工具 {name} 首次失败，重试中...")
        trace.retries += 1
        result = _execute_tool(name, _normalize_tool_args(name, arguments, destination))
        cached = bool(result.get("_cached"))
        if result["success"]:
            _record_tool_outcome(name, OK_RETRY, started, retries=1, cached=cached)
            if ledger is not None:
                ledger.record(name, result["text"], ok=True)
            return result["text"]

    # ── 尝试 fallback ──
    attempts = 2 if max_retries > 0 else 1
    for fallback in _TOOL_FALLBACKS.get(name, []):
        # 明知道会失败的兜底就不要去试：它既救不了请求，又白等一轮超时。
        # 后端健康状态由 web_search 模块维护（连续失败后进入冷却期）。
        if fallback == "web_search" and not web_search_available():
            logger.warning(f"  ⏭️ [{agent_name}] 跳过降级到 web_search（后端全部不可用）")
            continue
        logger.warning(f"  🔄 [{agent_name}] {name} 失败，降级到 {fallback}")
        trace.retries += 1
        adapted_args = _adapt_args(name, fallback, arguments)
        # 补最后一道 destination 兜底：无论 _adapt_args 有没有带，
        # 都把本次规划的目的地塞进去，避免降级检索退化成全库搜索命中无关城市
        if destination and not adapted_args.get("destination"):
            adapted_args["destination"] = destination
        fallback_result = _execute_tool(fallback, adapted_args)
        attempts += 1
        if fallback_result["success"]:
            _record_tool_outcome(
                name,
                OK_FALLBACK,
                started,
                retries=attempts - 1,
                fallback_to=fallback,
                cached=bool(fallback_result.get("_cached")),
            )
            if ledger is not None:
                # 降级拿到的数据同样算数，但记在 fallback 工具名下
                ledger.record(fallback, fallback_result["text"], ok=True)
            return f"[降级自 {name} → {fallback}] {fallback_result['text']}"

    # ── 全部失败：返回降级提示 ──
    _record_tool_outcome(
        name,
        FAILED,
        started,
        retries=max(0, attempts - 1),
        error=str(result.get("text", ""))[:200],
    )
    if ledger is not None:
        ledger.record(name, str(result.get("text", "")), ok=False)
    return (
        f"⚠️ 工具 {name} 及其降级方案均执行失败。"
        f"请基于已有信息继续生成，缺失的部分标注'暂无数据'。"
    )


# ── 统一 Agent 执行引擎（Function Calling）────────────────────

def _stream_final_output(
    client: OpenAI,
    agent: dict,
    messages: list,
    limiter,
    token_callback: Optional[Callable[[str, str], None]] = None,
) -> str:
    """流式或非流式生成最终输出。

    注意：本项目的 LLM 是思维链模型（mimo-v2.5-pro），它回答前会先生成
    一段 reasoning_content。若 max_tokens 被思维链吃满，content 会是空串
    （实测 finish_reason=length / content 长度 0）。所以这里：
      1. 给足 token 预算（思维链 + 正文），而不是只按正文估；
      2. 拿到空文本时降级为一次非流式重试，并按 finish_reason 记录原因。
    """
    from app.concurrency import rate_limited_call

    if token_callback:
        stream = rate_limited_call(
            limiter,
            client.chat.completions.create,
            model=settings.llm_model,
            messages=messages,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            stream=True,
        )
        output = ""
        finish = ""
        for chunk in stream:
            if chunk.choices and chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                output += delta
                token_callback(agent["name"], delta)
        _note_llm_io(messages, output)
        if output.strip():
            return output
        # 空输出：很可能是思维链吃满 token，非流式再要一次
        logger.warning(
            f"  ⚠️ [{agent['label']}] 流式输出为空（finish_reason={finish}），"
            f"改用非流式重试"
        )
        if finish == "length":
            messages = messages + [{
                "role": "user",
                "content": "请直接输出方案正文，省略思考过程，不要重复分析。",
            }]

    resp = rate_limited_call(
        limiter,
        client.chat.completions.create,
        model=settings.llm_model,
        messages=messages,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
    )
    text = resp.choices[0].message.content or ""
    if not text.strip():
        logger.warning(
            f"  ⚠️ [{agent['label']}] 非流式输出仍为空"
            f"（finish_reason={resp.choices[0].finish_reason}）"
        )
    _note_llm_io(messages, text)
    return text


def _self_reflect(
    client: OpenAI,
    agent: dict,
    messages: list,
    output: str,
    trace: AgentTrace,
    limiter,
    event_callback: Optional[Callable[[dict], None]] = None,
    enabled: Optional[bool] = None,
) -> str:
    """自我反思：检查输出质量，不通过则修正。enabled=False 时跳过（消融/快路径）。"""
    from app.concurrency import rate_limited_call

    if enabled is None:
        enabled = settings.enable_reflection
    if not enabled:
        return output

    reflection_prompt = REFLECTION_PROMPTS.get(agent["name"])
    if not reflection_prompt or len(output) <= 200:
        return output

    if event_callback:
        event_callback({
            "type": "thought", "agent": agent["name"],
            "content": "正在自我反思检查...",
        })

    reflect_messages = messages + [
        {"role": "assistant", "content": output},
        {"role": "user", "content": reflection_prompt},
    ]

    try:
        reflect_resp = rate_limited_call(
            limiter,
            client.chat.completions.create,
            model=settings.llm_model,
            messages=reflect_messages,
            temperature=0.1,
            max_tokens=500,
        )
        reflection = reflect_resp.choices[0].message.content or ""

        if "PASS" not in reflection.upper():
            logger.info(f"  🔄 [{agent['label']}] 自我反思不通过，修正中...")
            trace.retries += 1

            fix_messages = reflect_messages + [
                {"role": "assistant", "content": reflection},
                {"role": "user", "content": "请根据上述检查结果，修正你的输出。只输出修正后的完整内容，不要解释修改了什么。"},
            ]

            fix_resp = rate_limited_call(
                limiter,
                client.chat.completions.create,
                model=settings.llm_model,
                messages=fix_messages,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
            )
            fixed = (fix_resp.choices[0].message.content or "").strip()
            # 修正结果必须比原文更像正文才采纳：
            # 模型在"修正"这一步经常直接回一句 PASS / 好的 / 已修正，
            # 或者返回空，直接把已有正文覆盖成垃圾（实测最终输出就变成 "PASS"）
            if _is_valid_plan_text(fixed, min_len=80):
                output = fixed
            elif fixed:
                logger.warning(
                    f"  ⚠️ [{agent['label']}] 修正结果不像正文（{fixed[:40]!r}），"
                    f"保留原输出"
                )
            else:
                logger.warning(f"  ⚠️ [{agent['label']}] 修正结果为空，保留原输出")
            trace.reflection_passed = False
        else:
            trace.reflection_passed = True
            logger.info(f"  ✅ [{agent['label']}] 自我反思通过")
    except Exception as e:
        logger.warning(f"  ⚠️ [{agent['label']}] 反思失败: {e}")
        trace.reflection_passed = True

    return output


class _ToolBudget:
    """昂贵工具（web_search）的调用预算，跨 Agent 共享。"""

    def __init__(self, max_web_search: int = 0):
        import threading as _t
        self._lock = _t.Lock()
        self.max_web_search = max_web_search  # 0 = unlimited
        self.web_search_used = 0

    def try_consume_web_search(self) -> bool:
        if self.max_web_search <= 0:
            return True
        with self._lock:
            if self.web_search_used >= self.max_web_search:
                return False
            self.web_search_used += 1
            return True

    def snapshot(self) -> dict:
        return {
            "max_web_search": self.max_web_search,
            "web_search_used": self.web_search_used,
        }


class _DataLedger:
    """记录本次规划「拿到了哪些类别的真实数据」。

    存在的意义是识别「无解任务」：目的地不在知识库、联网也拿不到有效内容时，
    模型会反复换词重试同一个工具（实测 search_knowledge 被调 8 次，6 次
    得到同一句「未覆盖」），然后花两百多秒硬凑一篇满是「暂无数据」的长文。
    与其让它空转再硬写，不如早点让它承认数据不足。
    """

    # 哪些工具能提供「景点类」数据。判断无解任务时只看这一类：
    # 没有景点就没法做行程，而天气/汇率这类边角数据有也不解决问题。
    CORE_TOOLS = ("search_knowledge", "search_attraction_context", "web_search")

    def __init__(self) -> None:
        import threading as _t
        self._lock = _t.Lock()
        self.filled: set[str] = set()
        self.empty: set[str] = set()
        self.core_filled: set[str] = set()
        self.tool_calls = 0
        self.empty_calls = 0

    def record(self, tool: str, result_text: str, ok: bool) -> None:
        with self._lock:
            self.tool_calls += 1
            if ok:
                self.filled.add(tool)
                if tool in self.CORE_TOOLS:
                    self.core_filled.add(tool)
            else:
                self.empty_calls += 1
                self.empty.add(tool)

    @property
    def hopeless(self) -> bool:
        """是否已可判定「数据不足以支撑完整方案」。

        条件：景点类数据一个都没拿到 + 至少试过 4 次工具 + 空手率过半。
        宁可错杀（提前给诚实短答）也不要放过：一个 20 秒的诚实回复
        对用户的价值，高于 250 秒的满屏「暂无数据」。
        """
        with self._lock:
            if self.core_filled:
                return False
            return self.tool_calls >= 4 and self.empty_calls / self.tool_calls >= 0.5

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "filled": sorted(self.filled),
                "core_filled": sorted(self.core_filled),
                "empty": sorted(self.empty),
                "tool_calls": self.tool_calls,
                "empty_calls": self.empty_calls,
            }


def _process_tool_calls(
    agent: dict,
    tool_calls: list,
    messages: list,
    trace: AgentTrace,
    event_callback: Optional[Callable[[dict], None]] = None,
    destination: str = "",
    tool_budget: Optional[_ToolBudget] = None,
    ledger: Optional["_DataLedger"] = None,
) -> None:
    """执行工具调用并将结果追加到消息列表"""
    for tc in tool_calls:
        func_name = tc.function.name
        try:
            func_args = json.loads(tc.function.arguments)
        except json.JSONDecodeError:
            func_args = {}

        # 成本预算：昂贵工具优先本地降级
        if func_name == "web_search" and tool_budget and not tool_budget.try_consume_web_search():
            logger.info(f"  💸 [{agent['label']}] web_search 预算耗尽，改走本地知识库")
            func_name = "search_knowledge"
            if "query" not in func_args:
                func_args = {"query": str(func_args)}

        logger.info(f"  🔧 [{agent['label']}] Action: {func_name}({func_args})")
        if event_callback:
            event_callback({
                "type": "action", "agent": agent["name"],
                "content": f"{func_name}({json.dumps(func_args, ensure_ascii=False)[:200]})",
            })

        result = _execute_tool_with_recovery(
            func_name, func_args, agent["name"], trace,
            destination=destination, ledger=ledger,
        )

        trace.tool_calls.append(ToolCallRecord(
            agent=agent["name"],
            tool=func_name,
            args=func_args,
            result_preview=result[:100],
        ))

        if trace.react_steps and not trace.react_steps[-1].action:
            trace.react_steps[-1].action = func_name
            trace.react_steps[-1].action_args = func_args
            trace.react_steps[-1].observation = result[:200]

        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": result,
        })

        logger.info(f"  📋 [{agent['label']}] Observation: {result[:100]}...")
        if event_callback:
            event_callback({
                "type": "observation", "agent": agent["name"],
                "content": result[:300],
            })


def _looks_like_tool_dump(text: str) -> bool:
    """判断输出是否像工具调用伪文本（不是给人看的方案）。"""
    if not text:
        return True
    t = text.strip()
    low = t[:200].lower()
    if t.startswith("<tool_call>") or t.startswith("{\n  \"name\"") or t.startswith("{\"name\""):
        return True
    if t.startswith("function=") or t.startswith("<function"):
        return True
    # 开头就是 JSON 工具负载
    if low.lstrip().startswith('{"name"') or low.lstrip().startswith('{"arguments"'):
        return True
    return False


def _is_valid_plan_text(text: str, min_len: int = 120) -> bool:
    """是否可当作最终方案正文（非伪工具、足够长）。"""
    t = (text or "").strip()
    if len(t) < min_len:
        return False
    return not _looks_like_tool_dump(t)


def _ensure_valid_final_output(
    client: OpenAI,
    agent: dict,
    messages: list,
    output: str,
    limiter,
) -> str:
    """
    拒收「伪最终」（原 crew 中段内联逻辑抽出，便于单测与复述）。
    1) 伪正文/过短 → 有 tools 对话上强制重写
    2) 仍失败 → 无 tools 再生成（不把脏流推给前端）
    3) 再失败 → 明确错误串
    """
    from app.concurrency import rate_limited_call

    if _is_valid_plan_text(output, min_len=120):
        return output or ""

    logger.warning(
        f"  ⚠️ [{agent['label']}] 最终输出无效（len={len(output or '')}），强制重写…"
    )
    rewrite_msg = (
        "不要输出任何工具调用、JSON 或 XML 标签。\n"
        "请只根据已有对话中的工具结果，输出完整、可读的最终 Markdown 正文。\n"
        "必须包含分节标题；信息缺失处写「暂无数据」，不要中断。\n"
        "禁止以 <tool_call> 或 { 开头。"
    )
    try:
        resp = rate_limited_call(
            limiter,
            client.chat.completions.create,
            model=settings.llm_model,
            messages=messages + [
                {
                    "role": "assistant",
                    "content": (output or "")[:400] or "(空)",
                },
                {"role": "user", "content": rewrite_msg},
            ],
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )
        rewritten = (resp.choices[0].message.content or "").strip()
        _note_llm_io(messages, rewritten)
        if _is_valid_plan_text(rewritten, min_len=80):
            return rewritten

        logger.warning(f"  ⚠️ [{agent['label']}] 重写仍无效，无工具再生成一次")
        output = _stream_final_output(
            client,
            agent,
            messages
            + [
                {
                    "role": "user",
                    "content": (
                        "请输出完整旅行方案 Markdown 正文。"
                        "禁止工具调用。缺失写「暂无数据」。"
                    ),
                }
            ],
            limiter,
            token_callback=None,  # 外层统一推送，避免脏流先到前端
        )
        if not _is_valid_plan_text(output, min_len=80):
            return "（生成失败：未能产出有效正文，请重试或改用 single 模式）"
        return output
    except Exception as e:
        logger.warning(f"  强制重写失败: {e}")
        if not _is_valid_plan_text(output, min_len=80):
            return "（生成失败：模型未返回有效正文）"
        return output or ""


def _run_agent_with_tools(
    client: OpenAI,
    agent: dict,
    user_msg: str,
    trace: AgentTrace,
    max_tool_rounds: Optional[int] = None,
    token_callback: Optional[Callable[[str, str], None]] = None,
    event_callback: Optional[Callable[[dict], None]] = None,
    limiter=None,
    destination: str = "",
    enable_reflection: Optional[bool] = None,
    tool_budget: Optional[_ToolBudget] = None,
) -> str:
    """
    统一 Agent 执行引擎（Function Calling + 多轮工具调用 + 可选自我反思）。

    流程：Thought → Action(工具调用) → Observation → ... → 最终输出 → [自我反思]
    """
    from app.concurrency import rate_limited_call
    if max_tool_rounds is None:
        max_tool_rounds = settings.max_tool_rounds
    messages = [
        {"role": "system", "content": agent["system"]},
        {"role": "user", "content": user_msg},
    ]

    output = ""
    ledger = _DataLedger()

    for round_idx in range(max_tool_rounds):
        resp = rate_limited_call(
            limiter,
            client.chat.completions.create,
            model=settings.llm_model,
            messages=messages,
            tools=TOOLS,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
        )

        msg = resp.choices[0].message
        _note_llm_io(messages, msg.content or "")

        thought_text = (msg.content or "").strip()
        if thought_text and msg.tool_calls:
            trace.react_steps.append(ReActStep(thought=thought_text[:500]))
            logger.info(f"  💭 [{agent['label']}] Thought: {thought_text[:100]}...")
            if event_callback:
                event_callback({"type": "thought", "agent": agent["name"], "content": thought_text[:300]})

        # ── 无工具调用 → 最终输出 ──
        if not msg.tool_calls:
            output = msg.content or ""
            break

        messages.append(msg)

        # ── 执行工具调用 ──
        _process_tool_calls(
            agent, msg.tool_calls, messages, trace, event_callback,
            destination=destination, tool_budget=tool_budget, ledger=ledger,
        )

        # ── 无解任务提前收束 ──
        # 目的地不在知识库、联网也没拿到有效景点数据时，继续转下去只会
        # 反复问同一个问题（实测 8 次工具调用里 6 次空手），最后硬写一篇
        # 满是「暂无数据」的长文，耗时 250s。这里直接引导它给出诚实短答。
        if ledger.hopeless:
            logger.warning(
                f"  🛑 [{agent['label']}] 判定数据不足以支撑完整方案"
                f"（{ledger.snapshot()}），提前收束"
            )
            if event_callback:
                event_callback({
                    "type": "thought", "agent": agent["name"],
                    "content": "关键数据缺失，改为输出诚实说明…",
                })
            messages.append({
                "role": "user",
                "content": (
                    "【系统提示】检测到当前目的地缺少可用的本地资料，联网也未能取到"
                    "景点等关键信息。请立即停止调用工具，直接输出一份简短的诚实答复：\n"
                    "1. 说明该目的地的本地资料暂未收录；\n"
                    "2. 只写确实拿到的信息（如天气），拿不到的不要猜、不要编；\n"
                    "3. 建议用户换一个已支持的目的地，或配置联网搜索 API；\n"
                    "4. 总长控制在 300 字以内，不要分五个板块硬凑。"
                ),
            })
            output = _stream_final_output(client, agent, messages, limiter, token_callback)
            break
    else:
        # 工具轮次耗尽，强制生成最终输出
        output = _stream_final_output(client, agent, messages, limiter, token_callback)

    # ── 拒收「伪最终」（逻辑见 _ensure_valid_final_output）──
    output = _ensure_valid_final_output(client, agent, messages, output, limiter)

    # ── 自我反思（可关）──
    validated = output
    output = _self_reflect(
        client, agent, messages, output, trace, limiter, event_callback,
        enabled=enable_reflection,
    )

    # 反思是正文校验之后的一道后门：它可能把已通过的正文改坏。
    # 这里做最后一道闸——反思把输出改废了就退回校验前的那一版。
    if not _is_valid_plan_text(output, min_len=80) and _is_valid_plan_text(validated, min_len=80):
        logger.warning(
            f"  ⚠️ [{agent['label']}] 反思后输出无效（{len(output or '')}字），"
            f"回退到反思前版本"
        )
        output = validated

    # ── 最终输出推送（反思后再推送，确保流式内容与最终结果一致）──
    if token_callback and output:
        token_callback(agent["name"], output)

    trace.output_length = len(output)
    return output


# ── 动态路由：根据用户意图选择 Agent ──────────────────────────


@traceable(run_name="dynamic_routing")
def _route_agents(
    client: OpenAI,
    destination: str,
    days: int,
    budget: float,
    interests: str,
    limiter=None,
) -> list[str]:
    """
    Router Agent：分析用户意图，动态决定调用哪些 Agent。
    返回需要执行的 Agent name 列表。
    """
    route_prompt = (
        "你是一个旅行规划路由器。根据用户需求，决定需要调用哪些 Agent。\n"
        "可选 Agent：researcher（目的地调研）、planner（行程规划）、"
        "budget（预算分析）、foodie（美食推荐）、safety（安全指南）。\n\n"
        "规则：\n"
        "- researcher 是基础 Agent，通常都需要\n"
        "- 如果用户明确只要某一项（如'只要预算'），可以跳过其他\n"
        "- 如果是全面规划需求，全部调用\n\n"
        "输出 JSON：{\"agents\": [\"researcher\", \"planner\", ...]}"
    )

    user_msg = (
        f"目的地：{destination}\n天数：{days}\n预算：${budget}\n兴趣：{interests}"
    )

    try:
        from app.concurrency import rate_limited_call
        resp = rate_limited_call(
            limiter,
            client.chat.completions.create,
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": route_prompt},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        content = resp.choices[0].message.content or ""
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            names = json.loads(match.group())
            valid = [n for n in names if n in _AGENT_MAP]
            if valid:
                return valid
    except Exception as e:
        logger.warning(f"LLM 路由失败，降级为规则路由: {e}")

    # 规则路由 fallback：基于关键词匹配
    return _rule_based_route(interests)


def _rule_based_route(interests: str) -> list[str]:
    """
    规则路由 fallback：当 LLM 不可用时基于关键词决定调用哪些 Agent。
    """
    agents = ["researcher", "planner"]
    interests_lower = interests.lower()

    if any(kw in interests_lower for kw in ["预算", "便宜", "省钱", "性价比", "budget", "cheap"]):
        agents.append("budget")
    if any(kw in interests_lower for kw in ["美食", "吃", "餐厅", "料理", "food", "restaurant"]):
        agents.append("foodie")
    if any(kw in interests_lower for kw in ["安全", "危险", "注意", "safety", "safe"]):
        agents.append("safety")

    # 默认全调（全面规划需求）
    if len(agents) == 2:
        agents = [a["name"] for a in AGENTS]

    return agents


# ── 主入口：并行 Agent 执行 ────────────────────────────────────


@traceable(run_name="travel_planner_pipeline")
def build_travel_crew(
    destination: str,
    days: int,
    budget: float,
    interests: str,
    language: str = "中文",
    step_callback: Optional[Callable[[str, str, int], None]] = None,
    token_callback: Optional[Callable[[str, str], None]] = None,
    event_callback: Optional[Callable[[dict], None]] = None,
    mode: Optional[str] = None,
    enable_reflection: Optional[bool] = None,
    enable_routing: bool = True,
) -> str:
    """
    Agent 执行引擎（Function Calling + 可配置执行模式 + 错误恢复）。

    mode:
      multi      — 动态路由 + 3 层串并行（默认产品路径）
      sequential — 5 Agent 严格串行（消融：去并行）
      single     — 单 Agent 一把梭（消融 baseline）

    enable_reflection: None 时用 settings.enable_reflection
    enable_routing: 仅 multi 模式生效；消融时可关
    """
    mode = mode or settings.pipeline_mode
    if mode not in PIPELINE_MODES:
        raise ValueError(f"unknown mode: {mode}, expected one of {PIPELINE_MODES}")
    if enable_reflection is None:
        enable_reflection = settings.enable_reflection

    client = wrap_openai(OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        max_retries=5,
        timeout=120,
    ))

    from app.concurrency import get_limiter
    limiter = get_limiter()
    tool_budget = _ToolBudget(max_web_search=settings.max_web_search_calls)

    lang_hint = f"请用{language}输出。"

    base_request = (
        f"旅行目的地：{destination}\n"
        f"旅行天数：{days} 天\n"
        f"总预算：${budget}\n"
        f"兴趣偏好：{interests}\n"
        f"{lang_hint}"
    )

    # 注入用户历史偏好（来自 user_preferences 表）
    try:
        from app.database import Database
        db = Database()
        prefs = db.get_all_preferences()
        if prefs:
            pref_parts = []
            if "budget_tier" in prefs:
                pref_parts.append(f"预算档位={prefs['budget_tier']}")
            if "interests" in prefs:
                pref_parts.append(f"历史兴趣={prefs['interests']}")
            if "last_destination" in prefs:
                pref_parts.append(f"上次目的地={prefs['last_destination']}")
            if pref_parts:
                base_request += f"用户历史偏好：{'，'.join(pref_parts)}\n"
    except Exception:
        pass

    # ── 反馈闭环 ────────────────────────────────────────
    try:
        from app.database import Database
        fb_db = Database()
        fb_records = fb_db.get_feedback_by_destination(destination, limit=5)
        if fb_records:
            feedback_parts = []
            for fb in fb_records:
                if fb["rating"] <= 3:
                    feedback_parts.append(
                        f"⚠️ 用户对{destination}规划评分较低({fb['rating']}/5)"
                        + (f"，原因：{fb['comment']}" if fb.get("comment") else "")
                        + "，请特别注意避免类似问题。"
                    )
                elif fb["rating"] >= 4:
                    feedback_parts.append(
                        f"✅ 用户对{destination}规划评分较高({fb['rating']}/5)"
                        + (f"，好评：{fb['comment']}" if fb.get("comment") else "")
                    )
            if feedback_parts:
                base_request += f"\n【历史反馈参考】\n{''.join(feedback_parts[-3:])}\n"
                logger.info(f"📊 注入 {len(feedback_parts)} 条历史反馈")
    except Exception:
        pass

    # ── 记忆系统 ─────────────────────────────────────────
    try:
        from app.memory import retrieve_memories, format_memories_for_context
        memories = retrieve_memories(
            query=f"{destination} {interests}",
            top_k=5,
            destination=destination,
        )
        if memories:
            memory_context = format_memories_for_context(memories)
            base_request += f"\n{memory_context}\n"
            logger.info(f"🧠 注入 {len(memories)} 条相关记忆")
    except Exception as e:
        logger.debug(f"记忆检索跳过: {e}")

    # ── 选择 Agent ──────────────────────────────────────
    if mode == "single":
        selected_agents = [SINGLE_AGENT]
        enable_routing = False
    elif mode == "sequential":
        selected_agents = list(AGENTS)
        enable_routing = False
    else:
        if enable_routing:
            selected_names = _route_agents(client, destination, days, budget, interests, limiter=limiter)
            selected_agents = [_AGENT_MAP[n] for n in selected_names if n in _AGENT_MAP]
            if not selected_agents:
                selected_agents = list(AGENTS)
            core_agents = {"researcher", "safety"}
            for core_name in core_agents:
                if core_name not in [a["name"] for a in selected_agents]:
                    selected_agents.append(_AGENT_MAP[core_name])
                    logger.info(f"  🔒 强制包含核心 Agent: {core_name}")
        else:
            selected_agents = list(AGENTS)

    logger.info(f"🧭 mode={mode} agents={[a['name'] for a in selected_agents]}")

    all_layers = split_layers(selected_agents, mode)

    total_agents = len(selected_agents)
    completed = 0
    traces: list[AgentTrace] = []
    result_parts: list[str] = []
    context_parts: list[str] = []

    def _run_agent(agent, user_msg):
        trace = AgentTrace(name=agent["name"], label=agent["label"])
        # 单 Agent 覆盖 5 板块，需要更多工具轮次，避免过早强制收束
        rounds = 8 if agent["name"] == "planner_all" else None
        try:
            content = _run_agent_with_tools(
                client, agent, user_msg, trace,
                token_callback=token_callback,
                event_callback=event_callback,
                limiter=limiter,
                destination=destination,
                enable_reflection=enable_reflection,
                tool_budget=tool_budget,
                max_tool_rounds=rounds,
            )
        except Exception as e:
            logger.error(f"[{agent['label']}] 执行失败: {e}")
            content = f"[{agent['label']}] 生成失败: {str(e)}"
        return agent, content, trace

    def _on_agent_done(agent, content, trace):
        nonlocal completed
        context_parts.append(content)
        result_parts.append(f"## {agent['label']}\n\n{content}")
        traces.append(trace)
        completed += 1
        progress = int(completed / total_agents * 100)
        if step_callback:
            step_callback(agent["name"], content, progress)

    # ── 逐层执行 ────────────────────────────────────────
    for layer in all_layers:
        shared_context = "\n\n".join(context_parts) if context_parts else ""

        if len(layer) == 1:
            agent = layer[0]
            user_msg = base_request
            if shared_context:
                user_msg += "\n\n━━━ 前序 Agent 的工作成果 ━━━\n" + shared_context
            agent_result, content, trace = _run_agent(agent, user_msg)
            _on_agent_done(agent_result, content, trace)
        else:
            stagger = settings.parallel_stagger_sec

            def _run_parallel(agent, idx):
                user_msg = base_request
                if shared_context:
                    user_msg += "\n\n━━━ 前序 Agent 的工作成果 ━━━\n" + shared_context
                if idx > 0 and stagger > 0:
                    time.sleep(idx * stagger)
                return _run_agent(agent, user_msg)

            with ThreadPoolExecutor(max_workers=min(4, len(layer))) as executor:
                futures = {executor.submit(_run_parallel, a, i): a for i, a in enumerate(layer)}
                for future in as_completed(futures):
                    agent_result, content, trace = future.result()
                    _on_agent_done(agent_result, content, trace)

    # ── 统计 ────────────────────────────────────────────
    total_tools = sum(len(t.tool_calls) for t in traces)
    total_retries = sum(t.retries for t in traces)
    total_thoughts = sum(len(t.react_steps) for t in traces)

    failed_count = sum(1 for p in result_parts if "生成失败" in p)
    if failed_count == len(result_parts) and failed_count > 0:
        logger.warning("⚠️ 所有 Agent 执行失败，启用本地工具降级方案")
        return _generate_fallback_plan(destination, days, budget, interests)

    logger.info(
        f"📊 执行统计: mode={mode} | {len(traces)} Agent | {total_tools} 次工具调用 | "
        f"{total_thoughts} 步推理 | {total_retries} 次自我修正 | "
        f"reflection={'on' if enable_reflection else 'off'} | "
        f"web_search={tool_budget.web_search_used}/{tool_budget.max_web_search or '∞'}"
    )

    final_result = "\n\n---\n\n".join(result_parts)

    try:
        from app.memory import extract_memories_from_plan
        extract_memories_from_plan(final_result, destination, budget, interests)
    except Exception as e:
        logger.debug(f"记忆提取跳过: {e}")

    return final_result


def _generate_fallback_plan(
    destination: str, days: int, budget: float, interests: str
) -> str:
    """
    当 LLM API 不可用时，用本地工具生成基础旅行攻略。
    不依赖 LLM，只使用知识库检索 + 路线优化 + 预算优化。
    """
    parts = [f"# 🗺️ {destination} {days}日旅行攻略（离线版）\n\n> ⚠️ AI 服务暂时不可用，以下为基础信息，仅供参考。\n"]

    # 1. 知识库检索
    # 必须显式传 destination：否则库外目的地会命中语义相近的其它城市内容，
    # 输出看着像答案、实际与查询无关（详见 docs/缺陷记录_检索静默失败.md）
    try:
        results = search_knowledge(
            query=destination, destination=destination, top_k=10
        )
        if results:
            parts.append("## 📍 目的地信息\n")
            for i, r in enumerate(results[:5], 1):
                parts.append(f"{i}. {r.get('text', '')[:200]}\n")
        else:
            parts.append(
                f"## 📍 目的地信息\n（知识库暂未覆盖「{destination}」，无可用基础信息）\n"
            )
    except Exception:
        parts.append("## 📍 目的地信息\n（知识库暂无数据）\n")

    # 2. 路线优化
    try:
        route_result = optimize_route_from_knowledge(destination, days)
        route_text = format_optimized_route(route_result)
        parts.append(f"## 🗺️ 路线规划\n\n{route_text}\n")
    except Exception as e:
        parts.append(f"## 🗺️ 路线规划\n（路线优化失败: {e}）\n")

    # 3. 预算优化
    try:
        budget_plans = optimize_budget(destination, budget, days)
        budget_text = format_budget_plans(budget_plans)
        parts.append(f"## 💰 预算规划\n\n{budget_text}\n")
    except Exception as e:
        parts.append(f"## 💰 预算规划\n（预算优化失败: {e}）\n")

    # 4. 天气（可能超时，但试试）
    try:
        weather_data = get_weather(destination)
        weather_text = format_weather(weather_data)
        parts.append(f"## 🌤️ 天气信息\n\n{weather_text}\n")
    except Exception:
        parts.append("## 🌤️ 天气信息\n（天气服务暂不可用）\n")

    parts.append("---\n> 💡 AI 服务恢复后可获取更详细的个性化攻略。")

    return "\n\n".join(parts)
