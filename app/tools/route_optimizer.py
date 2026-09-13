"""
行程路线优化器 — 贪心最近邻算法 + 2-opt 局部搜索。
使用真实 GPS 经纬度 + Haversine 距离公式计算景点间实际地理距离。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app.knowledge.destinations import DESTINATIONS
from app.knowledge.coords import CITY_COORDS


@dataclass
class Attraction:
    name: str
    type: str
    ticket: str
    duration: str
    tip: str
    lat: float = 0.0
    lng: float = 0.0


# 兼容旧引用
CITY_CENTERS = CITY_COORDS

ATTRACTION_COORDS: dict[str, tuple[float, float]] = {
    # ── 东京 ──
    "浅草寺": (35.7148, 139.7967),
    "东京塔": (35.6586, 139.7454),
    "涩谷十字路口": (35.6595, 139.7004),
    "明治神宫": (35.6766, 139.6993),
    "秋叶原": (35.7023, 139.7745),
    "新宿御苑": (35.6852, 139.7100),
    "东京晴空塔": (35.7101, 139.8107),
    "筑地外市场": (35.6657, 139.7702),
    "台场": (35.6234, 139.7766),
    "皇居外苑": (35.6852, 139.7528),
    # ── 巴黎 ──
    "埃菲尔铁塔": (48.8584, 2.2945),
    "卢浮宫": (48.8606, 2.3376),
    "凯旋门": (48.8738, 2.2950),
    "巴黎圣母院": (48.8530, 2.3499),
    "奥赛博物馆": (48.8600, 2.3266),
    "蒙马特高地": (48.8867, 2.3430),
    "塞纳河游船": (48.8584, 2.3522),
    "凡尔赛宫": (48.8049, 2.1204),
    "香榭丽舍大街": (48.8698, 2.3079),
    "橘园美术馆": (48.8631, 2.3219),
    # ── 曼谷 ──
    "大皇宫": (13.7500, 100.4913),
    "卧佛寺": (13.7467, 100.4931),
    "郑王庙": (13.7437, 100.4890),
    "恰图恰周末市场": (13.7998, 100.5500),
    "考山路": (13.7581, 100.4970),
    "暹罗海洋世界": (13.7463, 100.5348),
    "水上市场": (13.6846, 100.4956),
    "四面佛": (13.7463, 100.5408),
    "暹罗天地": (13.7280, 100.5105),
    "金佛寺": (13.7400, 100.5018),
    # ── 首尔 ──
    "景福宫": (37.5796, 126.9770),
    "北村韩屋村": (37.5826, 126.9840),
    "明洞": (37.5637, 126.9850),
    "南山塔": (37.5511, 126.9882),
    "弘大": (37.5563, 126.9236),
    "梨泰院": (37.5345, 126.9945),
    "昌德宫": (37.5794, 126.9910),
    "东大门设计广场": (37.5663, 127.0090),
    "广藏市场": (37.5700, 126.9980),
    "汉江公园": (37.5165, 126.9830),
    # ── 新加坡 ──
    "滨海湾花园": (1.2816, 103.8636),
    "鱼尾狮公园": (1.2869, 103.8546),
    "环球影城": (1.2540, 103.8180),
    "牛车水": (1.2819, 103.8438),
    "小印度": (1.3067, 103.8495),
    "植物园": (1.3138, 103.8159),
    "金沙酒店无边泳池": (1.2834, 103.8587),
    "圣淘沙": (1.2494, 103.8323),
    "国家美术馆": (1.2900, 103.8519),
    "夜间动物园": (1.4043, 103.7880),
    # ── 成都 ──
    "大熊猫繁育研究基地": (30.7367, 104.1368),
    "武侯祠": (30.6450, 104.0480),
    "锦里": (30.6433, 104.0483),
    "宽窄巷子": (30.6710, 104.0540),
    "都江堰": (30.9980, 103.6100),
    "春熙路": (30.6590, 104.0810),
    "人民公园": (30.6580, 104.0560),
    "杜甫草堂": (30.6540, 104.0200),
    "青城山": (30.9000, 103.5000),
    "九眼桥": (30.6450, 104.0700),
    # ── 伦敦 ──
    "大英博物馆": (51.5194, -0.1270),
    "白金汉宫": (51.5014, -0.1419),
    "伦敦塔": (51.5081, -0.0759),
    "大本钟": (51.5007, -0.1246),
    "威斯敏斯特教堂": (51.4993, -0.1275),
    "伦敦眼": (51.5033, -0.1196),
    "塔桥": (51.5055, -0.0754),
    "海德公园": (51.5074, -0.1657),
    "国家美术馆": (51.5089, -0.1283),
    "诺丁山": (51.5090, -0.1960),
    # ── 悉尼 ──
    "悉尼歌剧院": (-33.8568, 151.2153),
    "悉尼港大桥": (-33.8522, 151.2108),
    "邦迪海滩": (-33.8908, 151.2743),
    "达令港": (-33.8740, 151.2029),
    "塔龙加动物园": (-33.8361, 151.2396),
    "蓝山国家公园": (-33.6400, 150.2500),
    "岩石区": (-33.8590, 151.2080),
    "皇家植物园": (-33.8636, 151.2166),
    "悉尼水族馆": (-33.8700, 151.2020),
    "曼利海滩": (-33.7950, 151.2860),
    # ── 迪拜 ──
    "哈利法塔": (25.1972, 55.2744),
    "帆船酒店": (25.1413, 55.1853),
    "迪拜购物中心": (25.1970, 55.2796),
    "棕榈岛": (25.1164, 55.1389),
    "迪拜老城区": (25.2650, 55.2980),
    "沙漠冲沙": (25.0000, 55.5000),
    "迪拜喷泉": (25.2014, 55.2722),
    "迪拜博物馆": (25.2632, 55.2973),
    "黄金市集": (25.2695, 55.2960),
    "朱美拉海滩": (25.1483, 55.1908),
    # ── 巴厘岛 ──
    "海神庙": (-8.6212, 115.0870),
    "乌布皇宫": (-8.5069, 115.2628),
    "德格拉朗梯田": (-8.4364, 115.2794),
    "库塔海滩": (-8.7183, 115.1686),
    "圣泉寺": (-8.4323, 115.3519),
    "金巴兰海滩": (-8.7883, 115.1839),
    "乌鲁瓦图断崖": (-8.8290, 115.0840),
    "猴林": (-8.5200, 115.2625),
    "蓝梦岛": (-8.6740, 115.4750),
    "水神庙": (-8.4100, 115.0100),
    # ── 纽约 ──
    "自由女神像": (40.6892, -74.0445),
    "时代广场": (40.7580, -73.9855),
    "中央公园": (40.7829, -73.9654),
    "大都会博物馆": (40.7794, -73.9632),
    "帝国大厦": (40.7484, -73.9857),
    "布鲁克林大桥": (40.7061, -73.9969),
    "911纪念馆": (40.7115, -74.0134),
    "第五大道": (40.7549, -73.9840),
    "百老汇": (40.7560, -73.9860),
    "高线公园": (40.7480, -74.0049),
    # ── 柏林 ──
    "勃兰登堡门": (52.5163, 13.3777),
    "柏林墙纪念馆": (52.5350, 13.3900),
    "博物馆岛": (52.5168, 13.3977),
    "国会大厦": (52.5186, 13.3762),
    "查理检查站": (52.5070, 13.3900),
    "东边画廊": (52.5050, 13.4420),
    "波茨坦广场": (52.5096, 13.3759),
    "蒂尔加滕公园": (52.5145, 13.3500),
    "犹太人纪念碑": (52.5139, 13.3780),
    "哈克市场": (52.5220, 13.3970),
    # ── 清迈 ──
    "双龙寺": (18.7998, 98.9682),
    "帕辛寺": (18.7859, 98.9700),
    "清迈古城": (18.7904, 98.9866),
    "周日夜市": (18.7859, 98.9700),
    "清迈大学": (18.8040, 98.9520),
    "素贴山": (18.8000, 98.9650),
    "夜间动物园": (18.8250, 98.9450),
    "大象自然公园": (18.8500, 98.9500),
    "清迈门夜市": (18.7790, 98.9890),
    "丛林飞跃": (18.9000, 98.9000),
    # ── 马尔代夫 ──
    "水上别墅": (3.2028, 73.2207),
    "浮潜": (3.2100, 73.2200),
    "深潜": (3.2150, 73.2150),
    "海钓": (3.2000, 73.2300),
    "SPA": (3.2028, 73.2207),
    "居民岛探访": (3.1900, 73.2400),
    "日落巡航": (3.2050, 73.2100),
    "水上运动": (3.2100, 73.2250),
    "海底餐厅": (3.2028, 73.2207),
    "无人岛野餐": (3.1800, 73.2500),
    # ── 香港 ──
    "太平山顶": (22.2706, 114.1457),
    "维多利亚港": (22.2856, 114.1577),
    "迪士尼乐园": (22.3134, 114.0415),
    "海洋公园": (22.2464, 114.1737),
    "大屿山大佛": (22.2528, 113.9057),
    "旺角女人街": (22.3160, 114.1700),
    "尖沙咀星光大道": (22.2933, 114.1683),
    "黄大仙祠": (22.3293, 114.1862),
    "中环石板街": (22.2830, 114.1580),
    "南丫岛": (22.2300, 114.1100),
}


def _get_coords(attraction_name: str, destination: str) -> tuple[float, float]:
    """获取景点的真实 GPS 坐标，找不到则用城市中心做确定性偏移"""
    if attraction_name in ATTRACTION_COORDS:
        return ATTRACTION_COORDS[attraction_name]
    # Fallback: 基于景点名 hash 做确定性偏移（不使用随机）
    base_lat, base_lng = CITY_COORDS.get(destination, (0.0, 0.0))
    h = hash(attraction_name)
    lat_off = ((h & 0xFFFF) / 0xFFFF - 0.5) * 0.05
    lng_off = (((h >> 16) & 0xFFFF) / 0xFFFF - 0.5) * 0.05
    return (base_lat + lat_off, base_lng + lng_off)


def _haversine_distance(a: Attraction, b: Attraction) -> float:
    """Haversine 公式计算两个 GPS 坐标间的球面距离（公里）"""
    R = 6371.0  # 地球半径（km）
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlng = math.radians(b.lng - a.lng)
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(h))


def _build_attractions(attractions: list[dict], destination: str) -> list[Attraction]:
    """从知识库数据构建带真实坐标的景点列表"""
    result = []
    for attr in attractions:
        lat, lng = _get_coords(attr["name"], destination)
        result.append(Attraction(
            name=attr["name"],
            type=attr["type"],
            ticket=attr.get("ticket", ""),
            duration=attr.get("duration", ""),
            tip=attr.get("tip", ""),
            lat=lat,
            lng=lng,
        ))
    return result


def _greedy_nearest_neighbor(attractions: list[Attraction]) -> list[Attraction]:
    """贪心最近邻算法：O(n²)"""
    if len(attractions) <= 1:
        return attractions

    visited = [False] * len(attractions)
    route = [attractions[0]]
    visited[0] = True

    for _ in range(len(attractions) - 1):
        current = route[-1]
        nearest_idx = -1
        nearest_dist = float('inf')

        for j, attr in enumerate(attractions):
            if not visited[j]:
                dist = _haversine_distance(current, attr)
                if dist < nearest_dist:
                    nearest_dist = dist
                    nearest_idx = j

        if nearest_idx >= 0:
            route.append(attractions[nearest_idx])
            visited[nearest_idx] = True

    return route


def _two_opt_improve(route: list[Attraction], max_iterations: int = 100) -> list[Attraction]:
    """2-opt 局部搜索：反转路径段消除交叉"""
    if len(route) <= 3:
        return route

    def total_distance(r: list[Attraction]) -> float:
        return sum(_haversine_distance(r[i], r[i + 1]) for i in range(len(r) - 1))

    best_route = route[:]
    best_dist = total_distance(best_route)

    for _ in range(max_iterations):
        improved = False
        for i in range(1, len(best_route) - 1):
            for j in range(i + 1, len(best_route)):
                new_route = best_route[:i] + best_route[i:j + 1][::-1] + best_route[j + 1:]
                new_dist = total_distance(new_route)
                if new_dist < best_dist:
                    best_route = new_route
                    best_dist = new_dist
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return best_route


def optimize_daily_route(
    destination: str,
    attractions: list[dict],
    max_per_day: int = 4,
) -> list[list[dict]]:
    """优化每日行程路线（真实 GPS 距离）"""
    if not attractions:
        return []

    attr_objs = _build_attractions(attractions, destination)
    ordered = _greedy_nearest_neighbor(attr_objs)
    ordered = _two_opt_improve(ordered)

    days = []
    for i in range(0, len(ordered), max_per_day):
        day_attractions = ordered[i:i + max_per_day]
        days.append([
            {
                "name": a.name,
                "type": a.type,
                "ticket": a.ticket,
                "duration": a.duration,
                "tip": a.tip,
                "lat": round(a.lat, 4),
                "lng": round(a.lng, 4),
            }
            for a in day_attractions
        ])

    return days


def optimize_route_from_knowledge(destination: str, days: int) -> dict:
    """从知识库获取景点并优化路线"""
    info = None
    dest_key = None
    for key, data in DESTINATIONS.items():
        if key in destination:
            info = data
            dest_key = key
            break

    if not info:
        return {"error": f"未找到 {destination} 的景点数据"}

    attractions = info.get("attractions", [])
    if not attractions:
        return {"error": "未找到景点数据"}

    per_day = min(4, max(2, len(attractions) // days))
    max_total = days * per_day
    attractions = attractions[:max_total]

    daily_routes = optimize_daily_route(destination, attractions, max_per_day=per_day)

    # 计算总距离
    total_dist = 0.0
    for day in daily_routes:
        for i in range(len(day) - 1):
            a = Attraction(name=day[i]["name"], type="", ticket="", duration="", tip="",
                           lat=day[i]["lat"], lng=day[i]["lng"])
            b = Attraction(name=day[i+1]["name"], type="", ticket="", duration="", tip="",
                           lat=day[i+1]["lat"], lng=day[i+1]["lng"])
            total_dist += _haversine_distance(a, b)

    total_attractions = sum(len(d) for d in daily_routes)

    return {
        "destination": destination,
        "days": days,
        "daily_routes": daily_routes,
        "total_attractions": total_attractions,
        "total_distance_km": round(total_dist, 1),
        "optimization": "greedy_nearest_neighbor + 2-opt (Haversine distance)",
    }


def format_optimized_route(result: dict) -> str:
    """格式化优化路线为文本"""
    if "error" in result:
        return result["error"]

    lines = [
        f"📍 {result['destination']} 优化路线（{result['days']}天）",
        f"🔧 优化算法：{result['optimization']}",
        f"📊 共 {result['total_attractions']} 个景点 | 总路程 {result.get('total_distance_km', 'N/A')} km",
        "",
    ]

    for day_idx, day in enumerate(result["daily_routes"], 1):
        lines.append(f"### Day {day_idx}")
        for i, attr in enumerate(day, 1):
            lines.append(f"  {i}. {attr['name']}（{attr['type']}）| 门票：{attr['ticket']} | 游览：{attr['duration']}")
            if attr.get("lat"):
                lines.append(f"     📍 ({attr['lat']}, {attr['lng']})")
            if attr.get("tip"):
                lines.append(f"     💡 {attr['tip']}")
        lines.append("")

    return "\n".join(lines)
