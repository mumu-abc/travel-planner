"""
Web 搜索工具 — 多后端自动切换，优先国内可达源。

背景（2026-09 无代理国内网络实测，详见 docs/缺陷记录_web搜索国内不可用.md）：

    原实现顺序是 DuckDuckGo → Wikipedia，两者在国内全部握手超时：
        DuckDuckGo 20.7s / 0 条，Wikipedia 9.8s / 0 条，合计 28.3s / 0 条
    而 web_search 又是 4 个工具的兜底终点（search_knowledge /
    search_attraction_context / get_weather / get_exchange_rate），
    等于整条降级链的末端是断的。

现在的顺序（实测数据）：
    1. 必应中国 RSS   0.4s   HTTP 200   中文   免密钥   ← 主力
    2. DuckDuckGo    20.7s  超时（国内不可达）          ← 海外备用
    3. Wikipedia      9.8s  超时（国内不可达）          ← 海外备用

三层保护：
    1. 主力源排在最前：国内 0.4 秒出结果，不再先去撞 28 秒的墙
    2. 显式短超时：海外源从库默认降到 5 秒
    3. 冷却短路：某后端连续失败 2 次后进入 5 分钟冷却期，期间直接跳过，
       避免「后端已死但每次仍等满超时」——一个慢失败比没有兜底更糟
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_BING_RSS = "https://cn.bing.com/search"
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_WIKI_REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_TIMEOUT_CN = 6          # 国内源：实测 0.4s，留足余量
_TIMEOUT_GLOBAL = 5      # 海外源：实测 10~20s，压到 5s
_TIMEOUT = _TIMEOUT_GLOBAL  # 兼容旧引用

_COOLDOWN_SEC = 300          # 连续失败后的冷却时长
_MAX_CONSECUTIVE_FAILURES = 2

_backend_failures: dict[str, int] = {}
_backend_cooldown_until: dict[str, float] = {}

_TAG_RE = re.compile(r"<[^>]+>")


# ── 后端健康状态（快失败的核心）────────────────────────────

def _backend_usable(name: str) -> bool:
    """该后端当前是否值得尝试（不在冷却期内）。"""
    return time.monotonic() >= _backend_cooldown_until.get(name, 0.0)


def _mark_backend_failure(name: str) -> None:
    n = _backend_failures.get(name, 0) + 1
    _backend_failures[name] = n
    if n >= _MAX_CONSECUTIVE_FAILURES:
        _backend_cooldown_until[name] = time.monotonic() + _COOLDOWN_SEC
        logger.warning(
            f"搜索后端 {name} 连续失败 {n} 次，进入冷却 {_COOLDOWN_SEC}s，期间直接跳过"
        )


def _mark_backend_success(name: str) -> None:
    _backend_failures.pop(name, None)
    _backend_cooldown_until.pop(name, None)


def reset_backend_state() -> None:
    """清空后端健康状态（主要供测试使用）。"""
    _backend_failures.clear()
    _backend_cooldown_until.clear()


def web_search_available() -> bool:
    """当前是否值得尝试联网搜索：至少一个后端不在冷却期。

    供 crew.py 的降级链判断——后端全凉时不去降级，直接给模型明确信号，
    而不是再白等一轮超时。
    """
    return any(_backend_usable(n) for n in BACKEND_ORDER)


def _clean(text: str) -> str:
    """去掉 RSS 描述里残留的 HTML 标签。"""
    return _TAG_RE.sub("", text or "").strip()


# ── 后端 1：必应中国（国内主力）────────────────────────────

def _bing_search(query: str, max_results: int = 5) -> list[dict]:
    """必应中国 RSS 搜索：国内 0.4s 可达、返回中文结果、无需密钥。"""
    try:
        resp = httpx.get(
            _BING_RSS,
            params={"q": query, "format": "rss"},
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT_CN,
            follow_redirects=True,
        )
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        out = []
        for item in root.findall(".//item")[:max_results]:
            out.append({
                "title": _clean(item.findtext("title") or ""),
                "snippet": _clean(item.findtext("description") or "")[:300],
                "url": (item.findtext("link") or "").strip(),
                "source": "Bing",
            })
        return out
    except Exception as e:
        logger.warning(f"必应搜索失败: {e}")
        return []


# ── 后端 2：DuckDuckGo（海外备用）──────────────────────────

def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo 搜索（使用 ddgs 库）。国内实测不可达，仅作海外备用。"""
    try:
        from ddgs import DDGS

        results = []
        try:
            client = DDGS(timeout=_TIMEOUT_GLOBAL)
        except TypeError:
            client = DDGS()  # 旧版本不接受 timeout 参数
        with client as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "snippet": r.get("body", ""),
                    "url": r.get("href", ""),
                    "source": "DuckDuckGo",
                })
        return results
    except Exception as e:
        logger.warning(f"DuckDuckGo 搜索失败: {e}")
        return []


# ── 后端 3：Wikipedia（海外备用）───────────────────────────

def _wiki_search(query: str, max_results: int = 3) -> list[dict]:
    """Wikipedia API 搜索。国内实测不可达，仅作海外备用。"""
    search_params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "format": "json",
        "srlimit": str(max_results),
    }

    results = []
    try:
        resp = httpx.get(_WIKI_API, params=search_params,
                         headers={"User-Agent": "TravelPlanner/3.0"},
                         timeout=_TIMEOUT_GLOBAL)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("query", {}).get("search", [])[:max_results]:
            title = item.get("title", "")
            snippet = item.get("snippet", "").replace(
                '<span class="searchmatch">', '').replace('</span>', '')

            summary = _wiki_summary(title)

            results.append({
                "title": title,
                "snippet": (summary or snippet)[:300],
                "url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title)}",
                "source": "Wikipedia",
            })

    except Exception as e:
        logger.warning(f"Wikipedia 搜索失败: {e}")

    return results


def _wiki_summary(title: str) -> str:
    """获取 Wikipedia 页面摘要"""
    try:
        safe_title = urllib.parse.quote(title.replace(" ", "_"))
        resp = httpx.get(f"{_WIKI_REST}{safe_title}",
                         headers={"User-Agent": "TravelPlanner/3.0"},
                         timeout=_TIMEOUT_GLOBAL)
        resp.raise_for_status()
        return resp.json().get("extract", "")
    except Exception:
        return ""


# ── 统一入口：按优先级逐个尝试，够数即停 ───────────────────

# 后端名 → 实现函数名。新增后端只需在这里加一行 + 写一个 _xxx_search。
# 存函数名而非函数引用，让测试能 patch("app.tools.web_search._bing_search") 替换掉；
# 若直接持有引用，mock 替换不到，测试会打到真实网络。
BACKEND_ORDER = ("bing", "duckduckgo", "wikipedia")

_BACKEND_FUNCS = {
    "bing": "_bing_search",
    "duckduckgo": "_ddg_search",
    "wikipedia": "_wiki_search",
}


def _call_backend(name: str, query: str, max_results: int) -> list[dict]:
    """按名字调用后端实现。"""
    fn = globals().get(_BACKEND_FUNCS.get(name, ""))
    if fn is None:
        return []
    return fn(query, max_results)


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    网络搜索：必应中国 → DuckDuckGo → Wikipedia，够数即停。

    每个后端都受「冷却短路」保护：连续失败 2 次后 5 分钟内直接跳过，
    不再让一次已知不可用的调用占满整条流水线的时间。

    Args:
        query: 搜索关键词
        max_results: 最大结果数

    Returns:
        [{"title": ..., "snippet": ..., "url": ..., "source": ...}, ...]
    """
    if not query or not query.strip():
        return []

    logger.info(f"🌐 Web 搜索: {query}")

    results: list[dict] = []
    seen_titles: set[str] = set()

    for name in BACKEND_ORDER:
        if len(results) >= max_results:
            break

        if not _backend_usable(name):
            logger.debug(f"  跳过 {name}（冷却中）")
            continue

        started = time.perf_counter()
        got = _call_backend(name, query, max_results - len(results))
        elapsed = (time.perf_counter() - started) * 1000

        if got:
            _mark_backend_success(name)
            logger.info(f"  {name} 返回 {len(got)} 条（{elapsed:.0f}ms）")
            for r in got:
                if r["title"] and r["title"] in seen_titles:
                    continue
                seen_titles.add(r["title"])
                results.append(r)
        else:
            _mark_backend_failure(name)
            logger.info(f"  {name} 无结果（{elapsed:.0f}ms）")

    return results[:max_results]


def format_web_search(results: list[dict]) -> str:
    """格式化搜索结果为文本"""
    if not results:
        return "未找到相关网络信息。"

    lines = ["🌐 网络搜索结果：", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['source']}] {r['title']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet'][:200]}")
        if r["url"]:
            lines.append(f"   🔗 {r['url']}")
        lines.append("")

    return "\n".join(lines)
