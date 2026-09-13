"""
预算优化器 — 使用 PuLP 线性规划进行约束优化。
在总预算约束下，最大化旅行体验覆盖度，同时满足住宿/餐饮/交通等各类约束。
PuLP 不可用时自动回退到比例分配方案。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.knowledge.destinations import DESTINATIONS

try:
    import pulp
    _HAS_PULP = True
except ImportError:
    _HAS_PULP = False


@dataclass
class BudgetAllocation:
    """预算分配方案"""
    tier: str
    accommodation: float
    food: float
    transport: float
    attractions: float
    shopping: float
    daily_total: float
    trip_total: float
    currency: str
    currency_name: str
    solver: str = "proportional"  # "lp" or "proportional"


def _parse_price(price_str: str) -> float:
    """
    从价格字符串中提取美元金额。

    支持格式：
      - "免费" / "free" → 0
      - "$XX" / "XX美元" → 直接取
      - "约￥XX元" / "XX日元" / "XX泰铢" → 按汇率换算
      - 纯数字 → 直接取
    """
    if not price_str:
        return 0.0

    text = price_str.strip().lower()

    # 免费
    if any(kw in text for kw in ["免费", "free", "无", "不需要"]):
        return 0.0

    # 已是美元
    usd_match = re.search(r'\$\s*([\d,.]+)', text)
    if usd_match:
        try:
            return float(usd_match.group(1).replace(',', ''))
        except ValueError:
            pass

    usd_match2 = re.search(r'([\d,.]+)\s*(?:美元|usd)', text)
    if usd_match2:
        try:
            return float(usd_match2.group(1).replace(',', ''))
        except ValueError:
            pass

    # 人民币 → USD
    cny_match = re.search(r'(?:约|≈|￥|¥)?\s*([\d,.]+)\s*(?:元|人民币|cny|rmb)', text)
    if cny_match:
        try:
            return float(cny_match.group(1).replace(',', '')) / 7.0
        except ValueError:
            pass

    # 人民币简写 "约￥XX" 或 "￥XX"
    cny_short = re.search(r'[￥¥]\s*([\d,.]+)', text)
    if cny_short:
        try:
            return float(cny_short.group(1).replace(',', '')) / 7.0
        except ValueError:
            pass

    # 日元 → USD
    jpy_match = re.search(r'([\d,.]+)\s*(?:日元|yen|jpy)', text)
    if jpy_match:
        try:
            return float(jpy_match.group(1).replace(',', '')) / 150.0
        except ValueError:
            pass

    # 泰铢 → USD
    thb_match = re.search(r'([\d,.]+)\s*(?:泰铢|baht|thb)', text)
    if thb_match:
        try:
            return float(thb_match.group(1).replace(',', '')) / 35.0
        except ValueError:
            pass

    # 韩元 → USD
    krw_match = re.search(r'([\d,.]+)\s*(?:韩元|won|krw)', text)
    if krw_match:
        try:
            return float(krw_match.group(1).replace(',', '')) / 1300.0
        except ValueError:
            pass

    # 回退：提取第一个数字
    numbers = re.findall(r'[\d,.]+', text.replace(',', ''))
    if numbers:
        try:
            return float(numbers[0])
        except ValueError:
            pass
    return 0.0


def _get_local_prices(destination: str) -> dict:
    """从知识库获取当地价格信息"""
    info = None
    for key, data in DESTINATIONS.items():
        if key in destination:
            info = data
            break

    if not info:
        return {}

    attraction_prices = []
    for attr in info.get("attractions", []):
        price = _parse_price(attr.get("ticket", "0"))
        if price > 0:
            attraction_prices.append(price)

    restaurant_prices = []
    for rest in info.get("restaurants", []):
        price = _parse_price(rest.get("budget") or rest.get("price", "0"))
        if price > 0:
            restaurant_prices.append(price)

    budget_ref = info.get("budget_estimate", {})

    return {
        "currency": info["currency"],
        "currency_name": {
            "JPY": "日元", "EUR": "欧元", "THB": "泰铢", "KRW": "韩元",
            "SGD": "新加坡元", "CNY": "人民币", "GBP": "英镑", "AUD": "澳元",
            "AED": "迪拉姆", "IDR": "印尼盾", "USD": "美元", "HKD": "港币",
            "MYR": "林吉特", "TWD": "新台币",
        }.get(info["currency"], info["currency"]),
        "attraction_prices": attraction_prices,
        "avg_attraction_price": sum(attraction_prices) / len(attraction_prices) if attraction_prices else 0,
        "restaurant_prices": restaurant_prices,
        "avg_restaurant_price": sum(restaurant_prices) / len(restaurant_prices) if restaurant_prices else 0,
        "budget_ref": budget_ref,
        "attraction_count": len(info.get("attractions", [])),
    }


# ── LP 求解器 ────────────────────────────────────────────


def _solve_budget_lp(
    total_usd: float,
    days: int,
    avg_ticket: float,
    avg_meal: float,
    attractions_per_day: int,
    tier_mult: dict[str, float],
) -> dict[str, float]:
    """
    用 PuLP 线性规划求解预算分配。
    目标：最大化"体验覆盖度"（景点数 * 权重 + 餐饮质量 * 权重 + ...）
    约束：各类别之和 ≤ total_usd, 住宿 ≥ 下限, 餐饮 ≥ 下限, ...
    """
    prob = pulp.LpProblem("BudgetOptimization", pulp.LpMaximize)

    # 决策变量：各类别预算（美元）
    acc = pulp.LpVariable("accommodation", lowBound=0, cat='Continuous')
    food = pulp.LpVariable("food", lowBound=0, cat='Continuous')
    trans = pulp.LpVariable("transport", lowBound=0, cat='Continuous')
    tickets = pulp.LpVariable("attractions", lowBound=0, cat='Continuous')
    shop = pulp.LpVariable("shopping", lowBound=0, cat='Continuous')

    # ── 约束 ──────────────────────────────────────────
    # 1. 总预算
    prob += acc + food + trans + tickets + shop <= total_usd

    # 2. 门票：至少覆盖计划景点数
    min_tickets = avg_ticket * attractions_per_day * days
    prob += tickets >= min_tickets

    # 3. 餐饮：至少覆盖每天3餐的最低标准
    min_food = avg_meal * 3 * days * tier_mult["food_min_ratio"]
    prob += food >= min_food

    # 4. 住宿：至少 days 晚
    min_acc = (total_usd * 0.15) * tier_mult["acc_min_ratio"]
    prob += acc >= min_acc

    # 5. 交通：至少基础交通费
    min_trans = (total_usd * 0.05)
    prob += trans >= min_trans

    # 6. 比例上限：住宿不超过 40%, 购物不超过 25%
    prob += acc <= total_usd * 0.40 * tier_mult["acc_max_ratio"]
    prob += shop <= total_usd * 0.25 * tier_mult["shop_max_ratio"]

    # ── 目标函数：最大化体验覆盖度 ────────────────────
    # 景点覆盖（每元门票覆盖的景点数）权重最高
    # 餐饮质量（每元餐饮的满意度）次之
    # 住宿舒适度、交通便捷度、购物满足度递减
    objective = (
        tickets * 3.0 / max(avg_ticket, 1)  # 景点覆盖
        + food * 2.0 / max(avg_meal, 1)      # 餐饮质量
        + acc * 1.5 / max(days, 1)           # 住宿舒适度
        + trans * 1.0                        # 交通便捷度
        + shop * 0.5                         # 购物满足度
    )
    prob += objective

    prob.solve(pulp.PULP_CBC_CMD(msg=0))

    if prob.status != 1:
        return None

    return {
        "accommodation": pulp.value(acc),
        "food": pulp.value(food),
        "transport": pulp.value(trans),
        "attractions": pulp.value(tickets),
        "shopping": pulp.value(shop),
    }


# ── 三档方案生成 ────────────────────────────────────────────


def optimize_budget(
    destination: str,
    total_usd: float,
    days: int,
) -> list[BudgetAllocation]:
    """
    生成三档预算方案（经济/舒适/豪华）。
    优先使用 LP 求解器，不可用时回退到比例分配。
    """
    prices = _get_local_prices(destination)
    if not prices:
        return []

    currency = prices["currency"]
    currency_name = prices["currency_name"]

    avg_ticket = prices.get("avg_attraction_price", 0)
    avg_meal = prices.get("avg_restaurant_price", 0)
    attractions_per_day = min(3, max(1, prices.get("attraction_count", 5) // days))

    # 三档系数
    tiers = {
        "经济": {
            "food_min_ratio": 0.6,
            "acc_min_ratio": 0.5,
            "acc_max_ratio": 0.6,
            "shop_max_ratio": 0.3,
        },
        "舒适": {
            "food_min_ratio": 0.8,
            "acc_min_ratio": 0.8,
            "acc_max_ratio": 0.8,
            "shop_max_ratio": 0.6,
        },
        "豪华": {
            "food_min_ratio": 1.0,
            "acc_min_ratio": 1.2,
            "acc_max_ratio": 1.0,
            "shop_max_ratio": 1.0,
        },
    }

    # 比例分配回退方案
    fallback_pcts = {
        "经济": (0.25, 0.30, 0.15, 0.20, 0.10),
        "舒适": (0.30, 0.25, 0.15, 0.15, 0.15),
        "豪华": (0.35, 0.20, 0.15, 0.10, 0.20),
    }

    results = []

    for tier_name, mults in tiers.items():
        use_lp = _HAS_PULP

        if use_lp:
            solution = _solve_budget_lp(total_usd, days, avg_ticket, avg_meal,
                                        attractions_per_day, mults)

        if not use_lp or solution is None:
            # 回退到比例分配
            acc_p, food_p, trans_p, attr_p, shop_p = fallback_pcts[tier_name]
            acc_total = total_usd * acc_p
            food_total = total_usd * food_p
            trans_total = total_usd * trans_p
            attr_total = total_usd * attr_p
            shop_total = total_usd * shop_p
            solver_name = "proportional"
        else:
            acc_total = solution["accommodation"]
            food_total = solution["food"]
            trans_total = solution["transport"]
            attr_total = solution["attractions"]
            shop_total = solution["shopping"]
            solver_name = "lp (CBC)"

        daily_total = (acc_total + food_total + trans_total + shop_total) / days + attr_total / days

        results.append(BudgetAllocation(
            tier=tier_name,
            accommodation=round(acc_total / days, 2),
            food=round(food_total / days, 2),
            transport=round(trans_total / days, 2),
            attractions=round(attr_total, 2),
            shopping=round(shop_total, 2),
            daily_total=round(daily_total, 2),
            trip_total=round(total_usd, 2),
            currency=currency,
            currency_name=currency_name,
            solver=solver_name,
        ))

    return results


def format_budget_plans(plans: list[BudgetAllocation], exchange_rate: float = 1.0) -> str:
    """格式化预算方案为文本"""
    if not plans:
        return "无法生成预算方案。"

    lines = ["💰 预算优化方案（LP 约束求解）", ""]

    for plan in plans:
        lines.append(f"### {plan.tier}方案 [{plan.solver}]")
        lines.append(f"- 住宿：${plan.accommodation}/晚")
        lines.append(f"- 餐饮：${plan.food}/天")
        lines.append(f"- 交通：${plan.transport}/天")
        lines.append(f"- 门票：${plan.attractions}（总计）")
        lines.append(f"- 购物：${plan.shopping}（总计）")
        lines.append(f"- **日均：${plan.daily_total}**")
        lines.append(f"- **总计：${plan.trip_total}**")
        if exchange_rate > 1:
            lines.append(f"- 当地货币：{round(plan.trip_total * exchange_rate, 0)} {plan.currency_name}")
        lines.append("")

    return "\n".join(lines)


def adjust_budget_for_constraints(
    destination: str,
    total_usd: float,
    days: int,
    min_accommodation: float = 0,
    max_food_per_day: float = 0,
    must_visit: list[str] = None,
) -> BudgetAllocation:
    """
    带用户约束的预算优化。
    使用 LP 在用户约束下重新求解，无 LP 时做手动调整。
    """
    if _HAS_PULP:
        prices = _get_local_prices(destination)
        if prices:
            avg_ticket = prices.get("avg_attraction_price", 0)
            avg_meal = prices.get("avg_restaurant_price", 0)
            attractions_per_day = min(3, max(1, prices.get("attraction_count", 5) // days))

            # 必去景点门票
            must_visit_cost = 0
            if must_visit:
                for key, data in DESTINATIONS.items():
                    if key in destination:
                        for attr in data.get("attractions", []):
                            if any(m in attr["name"] for m in must_visit):
                                must_visit_cost += _parse_price(attr.get("ticket", "0"))
                        break

            prob = pulp.LpProblem("ConstrainedBudget", pulp.LpMaximize)

            acc = pulp.LpVariable("acc", lowBound=0)
            food = pulp.LpVariable("food", lowBound=0)
            trans = pulp.LpVariable("trans", lowBound=0)
            tickets = pulp.LpVariable("tickets", lowBound=0)
            shop = pulp.LpVariable("shop", lowBound=0)

            prob += acc + food + trans + tickets + shop <= total_usd
            prob += tickets >= avg_ticket * attractions_per_day * days + must_visit_cost
            prob += food >= avg_meal * 3 * days * 0.8

            if min_accommodation > 0:
                prob += acc >= min_accommodation * days
            if max_food_per_day > 0:
                prob += food <= max_food_per_day * days

            prob += trans >= total_usd * 0.05
            prob += acc <= total_usd * 0.40
            prob += shop <= total_usd * 0.25

            prob += (tickets * 3.0 / max(avg_ticket, 1)
                     + food * 2.0 / max(avg_meal, 1)
                     + acc * 1.5 / max(days, 1)
                     + trans * 1.0 + shop * 0.5)

            prob.solve(pulp.PULP_CBC_CMD(msg=0))

            if prob.status == 1:
                daily_total = ((pulp.value(acc) + pulp.value(food)
                                + pulp.value(trans) + pulp.value(shop)) / days
                               + pulp.value(tickets) / days)
                return BudgetAllocation(
                    tier="自定义（LP 约束求解）",
                    accommodation=round(pulp.value(acc) / days, 2),
                    food=round(pulp.value(food) / days, 2),
                    transport=round(pulp.value(trans) / days, 2),
                    attractions=round(pulp.value(tickets), 2),
                    shopping=round(pulp.value(shop), 2),
                    daily_total=round(daily_total, 2),
                    trip_total=round(total_usd, 2),
                    currency=prices["currency"],
                    currency_name=prices["currency_name"],
                    solver="lp (CBC) + user constraints",
                )

    # 回退：比例分配 + 手动调整
    plans = optimize_budget(destination, total_usd, days)
    if not plans:
        return None

    base_plan = plans[1]  # 舒适

    if min_accommodation > 0 and base_plan.accommodation < min_accommodation:
        diff = (min_accommodation - base_plan.accommodation) * days
        base_plan.accommodation = min_accommodation
        base_plan.food = max(0, base_plan.food - diff / days / 2)
        base_plan.shopping = max(0, base_plan.shopping - diff / 2)

    if max_food_per_day > 0 and base_plan.food > max_food_per_day:
        diff = (base_plan.food - max_food_per_day) * days
        base_plan.food = max_food_per_day
        base_plan.shopping += diff

    if must_visit:
        for key, data in DESTINATIONS.items():
            if key in destination:
                for attr in data.get("attractions", []):
                    if any(m in attr["name"] for m in must_visit):
                        price = _parse_price(attr.get("ticket", "0"))
                        base_plan.attractions = max(0, base_plan.attractions - price)
                break

    return base_plan
