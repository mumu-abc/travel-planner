"""
天气工具 - 使用 Open-Meteo 免费 API，无需 key，全球可用。
"""

from __future__ import annotations

import logging

import httpx

from app.knowledge.coords import CITY_COORDS, get_city_coords

logger = logging.getLogger(__name__)

# WMO 天气码映射
_WMO_CODES = {
    0: "晴朗", 1: "晴", 2: "多云", 3: "阴天",
    45: "雾", 48: "冻雾",
    51: "小雨", 53: "小雨", 55: "中雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    77: "冰雪",
    80: "阵雨", 81: "中阵雨", 82: "大阵雨",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "严重雷暴",
}


def _wmo_desc(code: int) -> str:
    return _WMO_CODES.get(code, f"未知({code})")


def get_weather(destination: str) -> dict:
    """获取目的地天气预报（Open-Meteo API）。

    Args:
        destination: 目的地名称（如"东京"、"Paris"）

    Returns:
        天气数据字典，包含当前天气和3天预报
    """
    city = destination.split(",")[0].strip()

    # 查坐标
    coords = get_city_coords(city)
    if coords:
        lat, lon = coords
    else:
        # 尝试 Open-Meteo 地理编码
        try:
            geo = httpx.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh"},
                timeout=10,
            )
            results = geo.json().get("results", [])
            if results:
                lat, lon = results[0]["latitude"], results[0]["longitude"]
            else:
                return {"city": city, "error": f"未找到城市坐标: {city}"}
        except Exception as e:
            return {"city": city, "error": f"地理编码失败: {e}"}

    try:
        resp = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,apparent_temperature",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "timezone": "auto",
                "forecast_days": 3,
            },
            timeout=15,
        )
        data = resp.json()

        cur = data.get("current", {})
        daily = data.get("daily", {})

        current_wmo = cur.get("weather_code", 0)
        forecast = []
        dates = daily.get("time", [])
        maxs = daily.get("temperature_2m_max", [])
        mins = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])

        for i in range(min(len(dates), 3)):
            forecast.append({
                "date": dates[i],
                "min_temp": mins[i] if i < len(mins) else "?",
                "max_temp": maxs[i] if i < len(maxs) else "?",
                "description": _wmo_desc(codes[i]) if i < len(codes) else "未知",
            })

        return {
            "city": city,
            "current": {
                "temp": cur.get("temperature_2m", "?"),
                "feels_like": cur.get("apparent_temperature", "?"),
                "description": _wmo_desc(current_wmo),
                "humidity": cur.get("relative_humidity_2m", "?"),
                "wind_speed": cur.get("wind_speed_10m", "?"),
            },
            "forecast": forecast,
        }
    except Exception as e:
        logger.error(f"天气查询失败: {e}")
        return {"city": city, "error": str(e)}


def format_weather(weather: dict) -> str:
    """将天气数据格式化为可读文本"""
    if "error" in weather:
        return f"[天气数据获取失败: {weather['error']}]"

    lines = [f"📍 {weather['city']} 天气实况："]
    c = weather["current"]
    lines.append(f"  当前温度: {c['temp']}°C（体感 {c['feels_like']}°C）")
    lines.append(f"  天气状况: {c['description']}")
    lines.append(f"  湿度: {c['humidity']}%，风速: {c['wind_speed']} km/h")
    lines.append("")
    lines.append("未来3天预报：")
    for day in weather["forecast"]:
        lines.append(f"  {day['date']}: {day['min_temp']}~{day['max_temp']}°C, {day['description']}")

    return "\n".join(lines)
