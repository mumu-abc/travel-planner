"""
工具层 TTL 缓存 —— 避免同一目的地重复规划时重复消耗外部调用。

设计要点：
    1. 只缓存成功结果。缓存失败会让一次瞬时故障变成持久故障。
    2. 按工具特性分档 TTL：静态知识（图谱检索/路线/预算）可长，
       实时数据（天气/汇率）必须短，否则会拿旧数据糊弄用户。
    3. 线程安全 —— Agent 并行执行 + FastAPI 多线程。
    4. 命中率与「节省的外部调用数」可导出，用于成本量化。

默认关闭，由 ENABLE_TOOL_CACHE 控制，保证行为可回退。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

# 按工具分档 TTL（秒）
TTL_BY_TOOL: dict[str, int] = {
    "search_knowledge": 86_400,           # 静态知识库
    "search_attraction_context": 86_400,  # 静态知识库
    "optimize_route": 86_400,             # 确定性计算（2-opt）
    "optimize_budget": 86_400,            # 确定性计算（LP）
    "get_weather": 1_800,                 # 实时：30 分钟
    "get_exchange_rate": 3_600,           # 实时：1 小时
    "web_search": 3_600,                  # 半实时：1 小时
}
DEFAULT_TTL = 1_800


def cache_key(tool: str, arguments: Any) -> str:
    """由工具名 + 规范化参数生成稳定 key（参数顺序无关）。"""
    try:
        payload = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(arguments)
    digest = hashlib.md5(payload.encode("utf-8")).hexdigest()[:16]
    return f"{tool}:{digest}"


class ToolCache:
    """带 TTL 的 LRU 缓存 + 命中率统计。"""

    def __init__(self, max_size: int = 512, enabled: bool = True) -> None:
        self.max_size = max(1, int(max_size))
        self.enabled = enabled
        self._lock = threading.RLock()
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._expired = 0

    # ── 基础操作 ────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        """命中返回缓存值，未命中/已过期返回 None。"""
        if not self.enabled:
            self._misses += 1
            return None
        with self._lock:
            item = self._store.get(key)
            if item is None:
                self._misses += 1
                return None
            expires_at, value = item
            if expires_at <= time.time():
                # 过期：视为未命中并清理，避免脏数据
                self._store.pop(key, None)
                self._expired += 1
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """写入缓存。ttl 缺省时按工具前缀自动分档。"""
        if not self.enabled:
            return
        tool = key.split(":", 1)[0]
        ttl = ttl if ttl is not None else TTL_BY_TOOL.get(tool, DEFAULT_TTL)
        with self._lock:
            self._store[key] = (time.time() + max(1, int(ttl)), value)
            self._store.move_to_end(key)
            self._sets += 1
            while len(self._store) > self.max_size:
                self._store.popitem(last=False)

    def invalidate(self, key: str) -> bool:
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    # ── 统计 ────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "enabled": self.enabled,
                "hits": self._hits,
                "misses": self._misses,
                "sets": self._sets,
                "expired": self._expired,
                "lookups": total,
                "hit_rate": round(self._hits / total * 100, 1) if total else 0.0,
                # 命中的次数 = 省下的真实外部调用次数
                "saved_calls": self._hits,
                "size": len(self._store),
                "max_size": self.max_size,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._sets = 0
            self._expired = 0


_global_cache = ToolCache(enabled=False)


def get_tool_cache() -> ToolCache:
    return _global_cache


def configure(enabled: bool, max_size: int = 512) -> ToolCache:
    """按配置初始化全局缓存（进程启动时调用一次）。"""
    global _global_cache
    _global_cache = ToolCache(max_size=max_size, enabled=enabled)
    return _global_cache


def render_markdown(cache: Optional[ToolCache] = None) -> str:
    c = cache or _global_cache
    s = c.stats()
    if not s["lookups"]:
        return "_暂无缓存访问记录。_"
    return (
        f"- 缓存 {'开启' if s['enabled'] else '关闭'}，容量 {s['size']}/{s['max_size']}\n"
        f"- 查询 {s['lookups']} 次，命中 {s['hits']} 次，命中率 **{s['hit_rate']}%**\n"
        f"- 节省真实外部调用 **{s['saved_calls']} 次**，过期淘汰 {s['expired']} 条"
    )
