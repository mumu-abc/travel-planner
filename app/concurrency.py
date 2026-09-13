"""
并发控制模块 — 令牌桶限流 + 指数退避重试 + 请求队列。

使用方式：
    from app.concurrency import rate_limited_call, RateLimiter
    limiter = RateLimiter(max_rps=10, max_concurrent=5)
    result = rate_limited_call(limiter, client.chat.completions.create, ...)
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimiter:
    """
    令牌桶限流器 + 并发控制 + 请求队列。

    Attributes:
        max_rps: 每秒最大请求数（令牌桶速率）
        max_concurrent: 最大并发请求数
        max_retries: 最大重试次数
        base_delay: 重试基础延迟（秒）
        max_delay: 重试最大延迟（秒）
    """
    max_rps: float = 10.0
    max_concurrent: int = 5
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0

    # 内部状态
    _tokens: float = field(init=False, repr=False)
    _last_refill: float = field(init=False, repr=False)
    _semaphore: threading.Semaphore = field(init=False, repr=False)
    _lock: threading.Lock = field(init=False, repr=False)
    _queue: deque = field(init=False, repr=False)

    def __post_init__(self):
        self._tokens = self.max_rps
        self._last_refill = time.monotonic()
        self._semaphore = threading.Semaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._queue = deque()

    def _refill_tokens(self):
        """令牌桶：按时间流逝补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.max_rps, self._tokens + elapsed * self.max_rps)
        self._last_refill = now

    def acquire(self, timeout: float = 30.0) -> bool:
        """
        获取一个请求令牌。
        返回 True 表示获取成功，False 表示超时。
        """
        # 1. 获取并发槽位
        if not self._semaphore.acquire(timeout=timeout):
            logger.warning("并发控制：等待并发槽位超时")
            return False

        # 2. 获取令牌桶令牌
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill_tokens()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            # 计算等待时间
            wait = 1.0 / self.max_rps
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._semaphore.release()
                logger.warning("并发控制：等待令牌超时")
                return False
            time.sleep(min(wait, remaining))

    def release(self):
        """释放并发槽位"""
        self._semaphore.release()

    @property
    def queue_size(self) -> int:
        """当前等待队列大小"""
        return len(self._queue)

    @property
    def available_concurrent(self) -> int:
        """当前可用并发数（近似）"""
        # 注意：这是近似值，不保证精确
        return self.max_concurrent


# ── 全局限流器实例 ──

_global_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_limiter() -> RateLimiter:
    """获取全局限流器实例（单例）"""
    global _global_limiter
    if _global_limiter is None:
        with _limiter_lock:
            if _global_limiter is None:
                from app.config import settings
                _global_limiter = RateLimiter(
                    max_rps=getattr(settings, 'rate_limit_rps', 10.0),
                    max_concurrent=getattr(settings, 'rate_limit_concurrent', 5),
                    max_retries=getattr(settings, 'rate_limit_retries', 3),
                )
                logger.info(
                    f"并发控制初始化: max_rps={_global_limiter.max_rps}, "
                    f"max_concurrent={_global_limiter.max_concurrent}"
                )
    return _global_limiter


def rate_limited_call(
    limiter: Optional[RateLimiter],
    func: Callable,
    *args,
    **kwargs,
) -> Any:
    """
    带限流和指数退避重试的函数调用。

    Args:
        limiter: 限流器实例，为 None 时直接调用
        func: 要调用的函数
        *args, **kwargs: 函数参数

    Returns:
        函数返回值

    Raises:
        最后一次重试的异常
    """
    if limiter is None:
        return func(*args, **kwargs)

    last_error = None
    for attempt in range(limiter.max_retries + 1):
        # 获取令牌
        if not limiter.acquire(timeout=60.0):
            raise TimeoutError("并发控制：请求排队超时（60秒）")

        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # 判断是否可重试
            is_retryable = any(kw in error_str for kw in [
                'rate', 'limit', '429', '503', 'timeout', 'connection',
                'overloaded', 'busy', 'throttl', 'quota',
            ])

            if not is_retryable or attempt >= limiter.max_retries:
                raise

            # 指数退避
            delay = min(
                limiter.base_delay * (2 ** attempt),
                limiter.max_delay,
            )
            # 添加抖动（±20%）
            import random
            jitter = delay * 0.2 * (2 * random.random() - 1)
            actual_delay = max(0.1, delay + jitter)

            logger.warning(
                f"LLM 调用失败（第 {attempt + 1} 次），{actual_delay:.1f}s 后重试: {e}"
            )
            time.sleep(actual_delay)
        finally:
            limiter.release()

    raise last_error  # type: ignore


class ConcurrencyStats:
    """并发控制统计信息"""

    def __init__(self, limiter: RateLimiter):
        self._limiter = limiter
        self._total_calls = 0
        self._total_retries = 0
        self._total_wait_time = 0.0
        self._lock = threading.Lock()

    def record_call(self, retries: int = 0, wait_time: float = 0.0):
        with self._lock:
            self._total_calls += 1
            self._total_retries += retries
            self._total_wait_time += wait_time

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_calls": self._total_calls,
                "total_retries": self._total_retries,
                "total_wait_time_s": round(self._total_wait_time, 2),
                "avg_wait_time_s": round(
                    self._total_wait_time / max(1, self._total_calls), 2
                ),
                "current_queue_size": self._limiter.queue_size,
                "max_rps": self._limiter.max_rps,
                "max_concurrent": self._limiter.max_concurrent,
            }
