"""
Web 搜索工具 — 通过 DuckDuckGo + Wikipedia API 获取实时网络信息。
"""

from __future__ import annotations

import logging
import urllib.parse

import httpx

logger = logging.getLogger(__name__)

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_WIKI_REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_TIMEOUT = 8


def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo 搜索（使用 ddgs 库）"""
    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
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


def _wiki_search(query: str, max_results: int = 3) -> list[dict]:
    """Wikipedia API 搜索"""
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
                         timeout=_TIMEOUT)
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
                         timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("extract", "")
    except Exception:
        return ""


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    网络搜索：DuckDuckGo + Wikipedia 双源搜索。

    Args:
        query: 搜索关键词
        max_results: 最大结果数

    Returns:
        [{"title": ..., "snippet": ..., "url": ..., "source": ...}, ...]
    """
    logger.info(f"🌐 Web 搜索: {query}")

    results = []

    # 1. DuckDuckGo 搜索
    ddg_results = _ddg_search(query, max_results=max_results)
    results.extend(ddg_results)

    # 2. Wikipedia 补充
    if len(results) < max_results:
        wiki_results = _wiki_search(query, max_results=max_results - len(results))
        for wr in wiki_results:
            if not any(r["title"] == wr["title"] for r in results):
                results.append(wr)

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
