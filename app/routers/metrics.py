"""
可观测性路由 —— 工具调用成功率 / 降级率 / 缓存命中率。

Agent 系统的可靠性不能只靠「跑通一次」来证明，必须能被持续观测。
这里把内存中的指标暴露成接口，配合 eval/tool_bench.py 即可现场复现数字。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.tool_cache import get_tool_cache
from app.tool_metrics import get_tool_metrics

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("/tools")
async def tool_metrics():
    """工具调用质量：最终可用率、一次成功率、容错依赖度、耗时分位。"""
    m = get_tool_metrics()
    return {
        "summary": m.summary(),
        "by_tool": m.by_tool(),
        "cache": get_tool_cache().stats(),
    }


@router.get("/cache")
async def cache_metrics():
    """工具缓存命中率与节省的外部调用数。"""
    return get_tool_cache().stats()


@router.post("/reset")
async def reset_metrics():
    """清空指标（便于演示前归零）。缓存内容保留，只重置计数。"""
    get_tool_metrics().reset()
    get_tool_cache().reset_stats()
    return {"ok": True}
