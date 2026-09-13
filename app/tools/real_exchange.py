"""
汇率工具 - 使用 open.er-api.com 免费 API，无需 key。
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# 目的地 → 货币映射
CURRENCY_MAP = {
    "日本": ("JPY", "日元", "¥"), "东京": ("JPY", "日元", "¥"), "大阪": ("JPY", "日元", "¥"),
    "泰国": ("THB", "泰铢", "฿"), "曼谷": ("THB", "泰铢", "฿"),
    "韩国": ("KRW", "韩元", "₩"), "首尔": ("KRW", "韩元", "₩"),
    "法国": ("EUR", "欧元", "€"), "巴黎": ("EUR", "欧元", "€"),
    "英国": ("GBP", "英镑", "£"), "伦敦": ("GBP", "英镑", "£"),
    "新加坡": ("SGD", "新加坡元", "S$"),
    "美国": ("USD", "美元", "$"), "纽约": ("USD", "美元", "$"),
    "澳大利亚": ("AUD", "澳元", "A$"), "悉尼": ("AUD", "澳元", "A$"),
    "中国": ("CNY", "人民币", "¥"), "北京": ("CNY", "人民币", "¥"),
    "上海": ("CNY", "人民币", "¥"), "成都": ("CNY", "人民币", "¥"),
    "香港": ("HKD", "港币", "HK$"),
    "台湾": ("TWD", "新台币", "NT$"), "台北": ("TWD", "新台币", "NT$"),
    "德国": ("EUR", "欧元", "€"), "柏林": ("EUR", "欧元", "€"),
    "意大利": ("EUR", "欧元", "€"), "罗马": ("EUR", "欧元", "€"),
    "西班牙": ("EUR", "欧元", "€"), "巴塞罗那": ("EUR", "欧元", "€"),
    "迪拜": ("AED", "迪拉姆", "د.إ"),
    "印尼": ("IDR", "印尼盾", "Rp"), "巴厘岛": ("IDR", "印尼盾", "Rp"),
    "马来西亚": ("MYR", "林吉特", "RM"), "吉隆坡": ("MYR", "林吉特", "RM"),
}


def get_exchange_rate(destination: str, amount_usd: float) -> dict:
    """获取汇率并转换金额。

    Args:
        destination: 目的地
        amount_usd: 美元金额

    Returns:
        汇率转换结果
    """
    # 解析货币
    currency_code = "USD"
    currency_name = "美元"
    symbol = "$"
    for key, (code, name, sym) in CURRENCY_MAP.items():
        if key in destination:
            currency_code, currency_name, symbol = code, name, sym
            break

    if currency_code == "USD":
        return {
            "from": "USD", "to": "USD", "rate": 1.0,
            "amount_usd": amount_usd, "amount_local": amount_usd,
            "symbol": "$", "currency_name": "美元",
        }

    try:
        resp = httpx.get(
            "https://open.er-api.com/v6/latest/USD",
            timeout=15,
        )
        data = resp.json()
        if data.get("result") != "success":
            return {"error": "汇率查询失败"}

        rate = data["rates"].get(currency_code, 0)
        if rate == 0:
            return {"error": f"未找到 {currency_code} 汇率"}

        return {
            "from": "USD",
            "to": currency_code,
            "rate": rate,
            "amount_usd": amount_usd,
            "amount_local": round(amount_usd * rate, 2),
            "symbol": symbol,
            "currency_name": currency_name,
        }
    except Exception as e:
        logger.error(f"汇率查询失败: {e}")
        return {"error": str(e)}


def format_exchange(exchange: dict) -> str:
    """将汇率数据格式化为可读文本"""
    if "error" in exchange:
        return f"[汇率数据获取失败: {exchange['error']}]"

    return (
        f"💱 汇率换算：${exchange['amount_usd']} USD = "
        f"{exchange['symbol']}{exchange['amount_local']} {exchange['currency_name']} "
        f"（汇率 1 USD = {exchange['rate']} {exchange['to']}）"
    )
