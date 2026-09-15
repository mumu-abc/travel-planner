"""
Web 搜索工具 — 多后端自动切换，优先国内可达源。

背景（2026-09 无代理国内网络实测，详见 docs/缺陷记录_web搜索国内不可用.md）：

    原实现顺序是 DuckDuckGo → Wikipedia，两者在国内全部握手超时：
        DuckDuckGo 20.7s / 0 条，Wikipedia 9.8s / 0 条，合计 28.3s / 0 条
    而 web_search 又是 4 个工具的兜底终点（search_knowledge /
    search_attraction_context / get_weather / get_exchange_rate），
    等于整条降级链的末端是断的。

现在的后端优先级（前面的可用就用前面的，够数即停）：

    0. 国内官方 API（配了 key 才启用）：智谱 / 百度千帆 / 博查
       —— 有 SLA、合规、有账单可查，是「正式方案」
    1. 必应中国 RSS   0.4s   HTTP 200   中文   免密钥   ← 默认主力（临时方案）
    2. DuckDuckGo    20.7s  超时（国内不可达）          ← 海外备用
    3. Wikipedia      9.8s  超时（国内不可达）          ← 海外备用

官方 API 端点实测（2026-09 无代理国内网络，全部可达并 0.2~0.3s 响应）：
    智谱   open.bigmodel.cn/api/paas/v4/web_search          ¥0.01/次
    百度   qianfan.baidubce.com/v2/ai_search/chat/...       1500 次/月免费
    博查   api.bochaai.com/v1/web-search                    面向 AI 的搜索

    注意：必应 RSS 与官方 API 的区别不是速度，是**合规性与稳定性**——
    RSS 是未公开接口（随时改版/限流），官方 API 有 SLA 和授权。
    只配了 key 就会自动排到最前面。

三层保护：
    1. 主力源排在最前：国内 0.4 秒出结果，不再先去撞 28 秒的墙
    2. 显式短超时：海外源从库默认降到 5 秒
    3. 冷却短路：某后端连续失败 2 次后进入 5 分钟冷却期，期间直接跳过，
       避免「后端已死但每次仍等满超时」——一个慢失败比没有兜底更糟
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_BING_RSS = "https://cn.bing.com/search"
_WIKI_API = "https://en.wikipedia.org/w/api.php"
_WIKI_REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"

# 国内官方搜索 API 端点
_ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"
_BAIDU_URL = "https://qianfan.baidubce.com/v2/ai_search/chat/completions"
_BOCHA_URL = "https://api.bochaai.com/v1/web-search"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_TIMEOUT_CN = 6          # 国内源：实测 0.4s，留足余量
_TIMEOUT_GLOBAL = 5      # 海外源：实测 10~20s，压到 5s
_TIMEOUT_API = 10        # 官方 API：给足时间，它是主力
_TIMEOUT = _TIMEOUT_GLOBAL  # 兼容旧引用

_COOLDOWN_SEC = 300          # 连续失败后的冷却时长
_MAX_CONSECUTIVE_FAILURES = 2

# 官方 API 的 provider 名 → (配置里的 key 字段, 实现函数名)
_OFFICIAL_PROVIDERS = {
    "zhipu": ("zhipu_api_key", "_zhipu_search"),
    "baidu": ("baidu_search_api_key", "_baidu_search"),
    "bocha": ("bocha_api_key", "_bocha_search"),
}

# 后端名 → 实现函数名。新增后端只需在这里加一行 + 写一个 _xxx_search。
# 存函数名而非函数引用，让测试能 patch("app.tools.web_search._bing_search") 替换掉；
# 若直接持有引用，mock 替换不到，测试会打到真实网络。
#
# 官方 API 走 _official_search 统一入口（内部再按 provider 分发），
# 因为「配了哪个 key」是运行期才知道的，注册表里只留一个占位名。
BACKEND_ORDER = ("official", "bing", "duckduckgo", "wikipedia")

_BACKEND_FUNCS = {
    "official": "_official_search",
    "bing": "_bing_search",
    "duckduckgo": "_ddg_search",
    "wikipedia": "_wiki_search",
}

_backend_failures: dict[str, int] = {}
_backend_cooldown_until: dict[str, float] = {}

_TAG_RE = re.compile(r"<[^>]+>")


# ── 官方 API 的密钥读取与选择 ──────────────────────────────

def _get_setting(name: str, default: str = "") -> str:
    """读配置；配置模块不可用时退回环境变量（便于单测与脚本单独使用）。"""
    try:
        from app.config import settings
        return getattr(settings, name, default) or ""
    except Exception:
        return os.environ.get(name.upper(), default) or ""


def available_official_providers() -> list[str]:
    """返回当前配了 key 的官方 provider 列表。

    优先用 settings.search_api_provider 指定的那个；
    未指定（留空）则按 _OFFICIAL_PROVIDERS 的声明顺序取第一个有 key 的。
    """
    forced = _get_setting("search_api_provider").strip().lower()
    if forced:
        entry = _OFFICIAL_PROVIDERS.get(forced)
        if entry and _get_setting(entry[0]).strip():
            return [forced]
        if forced:
            logger.warning(f"search_api_provider={forced!r} 无效或未配 key，忽略")
        return []

    out = []
    for name, (key_field, _) in _OFFICIAL_PROVIDERS.items():
        if _get_setting(key_field).strip():
            out.append(name)
    return out


def _call_backend(name: str, query: str, max_results: int) -> list[dict]:
    """按名字调用后端实现。"""
    fn = globals().get(_BACKEND_FUNCS.get(name, ""))
    if fn is None:
        return []
    return fn(query, max_results)


# ── 后端健康状态（快失败的核心）────────────────────────────

def _backend_usable(name: str) -> bool:
    """该后端当前是否值得尝试（不在冷却期内）。"""
    if name == "official" and not available_official_providers():
        return False  # 没配 key → 根本不该尝试，也谈不上冷却
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


# ── 后端 0：国内官方 API（配了 key 才启用，正式方案）─────────

def _official_search(query: str, max_results: int = 5) -> list[dict]:
    """官方 API 统一入口：按配置挑一个 provider 调。

    这里只试**一个** provider（第一个配了 key 的），不做内部轮询：
    官方 API 是有账单的，静默轮询会让人搞不清钱花在哪家。
    """
    providers = available_official_providers()
    if not providers:
        return []

    name = providers[0]
    fn = globals().get(_OFFICIAL_PROVIDERS[name][1])
    if fn is None:
        return []
    results = fn(query, max_results)
    if results:
        logger.info(f"  官方 API({name}) 返回 {len(results)} 条")
    return results


def _zhipu_search(query: str, max_results: int = 5) -> list[dict]:
    """智谱 Web Search API（¥0.01/次）。

    请求：POST /api/paas/v4/web_search，body 用 search_query / search_engine / count
    响应：{"search_result": [{"title","content","link","media","publish_date"}]}
    """
    key = _get_setting("zhipu_api_key").strip()
    if not key:
        return []
    try:
        resp = httpx.post(
            _ZHIPU_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "search_query": query[:70],
                "search_engine": "search_pro",
                "count": max(1, min(max_results, 50)),
            },
            timeout=_TIMEOUT_API,
        )
        resp.raise_for_status()
        data = resp.json()

        out = []
        for r in (data.get("search_result") or [])[:max_results]:
            out.append({
                "title": r.get("title", ""),
                "snippet": (r.get("content") or "")[:300],
                "url": r.get("link", ""),
                "source": f"智谱·{r.get('media') or 'web'}",
            })
        return out
    except Exception as e:
        logger.warning(f"智谱搜索失败: {e}")
        return []


def _baidu_search(query: str, max_results: int = 5) -> list[dict]:
    """百度千帆 AI 搜索（1500 次/月免费）。

    请求：POST /v2/ai_search/chat/completions
          鉴权 header 是 X-Appbuilder-Authorization（不是标准的 Authorization）
    响应：{"choices":[{"message":{"content":...}}],
          "references":[{"title","content","url","date","web_anchor"}]}

    注意：百度会额外做一次 LLM 归纳（choices 里是生成答案，不是纯搜索结果），
    这里只取 references 原始来源，避免把它的二次生成当成检索结果。
    """
    key = _get_setting("baidu_search_api_key").strip()
    if not key:
        return []
    try:
        resp = httpx.post(
            _BAIDU_URL,
            headers={"X-Appbuilder-Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "messages": [{"role": "user", "content": query}],
                "stream": False,
                "search_mode": "required",   # 不要它自作判断，我们要的就是检索
                "resource_type_filter": [
                    {"type": "web", "top_k": max(1, min(max_results, 10))}
                ],
            },
            timeout=_TIMEOUT_API,
        )
        resp.raise_for_status()
        data = resp.json()

        out = []
        for r in (data.get("references") or [])[:max_results]:
            out.append({
                "title": r.get("title", ""),
                "snippet": (r.get("content") or "")[:300],
                "url": r.get("url", ""),
                "source": f"百度·{r.get('web_anchor') or 'web'}",
            })
        return out
    except Exception as e:
        logger.warning(f"百度搜索失败: {e}")
        return []


def _bocha_search(query: str, max_results: int = 5) -> list[dict]:
    """博查 Web Search API（面向 AI 的搜索）。

    请求：POST /v1/web-search，body 用 query / count / summary
    响应：{"data": {"webPages": {"value": [{"name","url","snippet","summary"}]}}}
    """
    key = _get_setting("bocha_api_key").strip()
    if not key:
        return []
    try:
        resp = httpx.post(
            _BOCHA_URL,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={
                "query": query,
                "count": max(1, min(max_results, 50)),
                "summary": True,
            },
            timeout=_TIMEOUT_API,
        )
        resp.raise_for_status()
        data = resp.json()

        pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
        out = []
        for r in pages[:max_results]:
            out.append({
                "title": r.get("name", ""),
                "snippet": (r.get("summary") or r.get("snippet") or "")[:300],
                "url": r.get("url", ""),
                "source": f"博查·{r.get('siteName') or 'web'}",
            })
        return out
    except Exception as e:
        logger.warning(f"博查搜索失败: {e}")
        return []


# ── 后端 1：必应中国（免密钥主力）──────────────────────────

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


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    网络搜索：官方API → 必应中国 → DuckDuckGo → Wikipedia，够数即停。

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
