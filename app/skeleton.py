"""
本地骨架生成 — 不依赖 LLM，秒级用知识库拼出可读行程骨架。

用户点「生成」后立刻看到骨架（天/景点/预算区间），
再由多 Agent 填充美食、安全、细节。解决「等几分钟白屏」的体验问题。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _find_destination(destination: str) -> tuple[str, dict] | tuple[None, None]:
    from app.knowledge.destinations import DESTINATIONS
    for key, data in DESTINATIONS.items():
        if key in destination or destination in key:
            return key, data
    # 宽松匹配：去掉空格/逗号后缀
    dest_clean = destination.split(",")[0].strip()
    for key, data in DESTINATIONS.items():
        if key in dest_clean or dest_clean in key:
            return key, data
    return None, None


def build_skeleton(
    destination: str,
    days: int,
    budget: float,
    interests: str = "",
) -> str:
    """
    返回 Markdown 骨架（通常 <1s）。失败时返回友好提示，不抛异常。
    """
    parts: list[str] = []
    dest_key, info = _find_destination(destination)

    if not info:
        return (
            f"# {destination} {days}日行程骨架\n\n"
            f"> 知识库暂无该目的地，正在由 AI Agent 调研，请稍候…\n\n"
            f"- 预算：${budget:.0f}\n"
            f"- 兴趣：{interests or '未填写'}\n"
        )

    country = info.get("country", "")
    currency = info.get("currency", "")
    best = info.get("best_season", "")
    parts.append(f"# {dest_key} · {days}日行程骨架")
    parts.append("")
    parts.append(f"> 即时骨架（本地知识库 + 路线优化，秒出）。AI Agent 正在填充美食 / 安全 / 预算细节…")
    parts.append("")
    parts.append(f"| 项目 | 内容 |")
    parts.append(f"|------|------|")
    parts.append(f"| 国家/地区 | {country} |")
    parts.append(f"| 货币 | {currency} |")
    parts.append(f"| 最佳季节 | {best} |")
    parts.append(f"| 总预算 | ${budget:.0f} |")
    parts.append(f"| 兴趣 | {interests or '通用'} |")
    parts.append("")

    # ── 路线骨架 ──
    try:
        from app.tools.route_optimizer import optimize_route_from_knowledge
        route = optimize_route_from_knowledge(dest_key, days)
        if "error" not in route:
            daily = route.get("daily_routes") or route.get("days") or []
            parts.append("## 每日骨架（GPS 路线已优化）")
            parts.append("")
            for i, day in enumerate(daily, 1):
                theme = ", ".join(a.get("name", "") for a in day[:2])
                parts.append(f"### Day {i} · {theme}")
                for a in day:
                    ticket = a.get("ticket", "")
                    duration = a.get("duration", "")
                    parts.append(f"- **{a.get('name', '')}**（{a.get('type', '')}）门票 {ticket} · 建议 {duration}")
                parts.append("")
    except Exception as e:
        logger.warning(f"骨架路线失败: {e}")
        parts.append("## 每日骨架\n\n（路线优化暂不可用，Agent 将补充）\n")

    # ── 预算骨架 ──
    try:
        from app.tools.budget_optimizer import optimize_budget
        plans = optimize_budget(dest_key, budget, days)
        if plans:
            parts.append("## 预算骨架（线性规划粗解）")
            parts.append("")
            for p in plans[:3]:
                parts.append(
                    f"- **{getattr(p, 'tier', '方案')}**：约 ${getattr(p, 'trip_total', 0):.0f}"
                    f"（日均 ${getattr(p, 'daily_total', 0):.0f}）"
                )
            parts.append("")
    except Exception as e:
        logger.warning(f"骨架预算失败: {e}")

    # ── 待填充占位 ──
    parts.append("## AI 即将补充")
    parts.append("")
    parts.append("- 美食：按天餐厅与人均")
    parts.append("- 安全：紧急电话与骗局提示")
    parts.append("- 细节：天气、交通衔接、门票核对")
    parts.append("")
    parts.append("---")
    parts.append("*骨架已可预览；完整方案完成后会整体替换。*")

    return "\n".join(parts)


def skeleton_meta(destination: str, days: int, budget: float) -> dict:
    dest_key, info = _find_destination(destination)
    return {
        "destination": dest_key or destination,
        "days": days,
        "budget": budget,
        "known": info is not None,
        "currency": (info or {}).get("currency", ""),
    }
