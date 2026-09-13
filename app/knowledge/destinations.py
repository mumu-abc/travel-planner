"""
目的地知识库 — 真实数据，覆盖 15 个热门目的地。
数据来源：公开旅游资料、官方景点网站。

优先从 data/destinations/*.json 加载（可热更新），
JSON 文件不存在时 fallback 到下方硬编码数据。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_JSON_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "destinations"


def _load_from_json() -> dict:
    """从 data/destinations/*.json 加载知识库"""
    if not _JSON_DIR.is_dir():
        return {}
    result = {}
    for f in sorted(_JSON_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result.update(data)
        except Exception as e:
            logger.warning(f"加载 {f.name} 失败: {e}")
    return result


def _get_destinations() -> dict:
    """加载知识库：JSON 优先，fallback 到硬编码"""
    json_data = _load_from_json()
    if json_data:
        logger.info(f"从 JSON 加载 {len(json_data)} 个目的地")
        return json_data
    logger.info("JSON 未找到，使用内置知识库")
    return _BUILTIN_DESTINATIONS


# ── 内置数据（fallback）───────────────────────────────────

_BUILTIN_DESTINATIONS = {
    "东京": {
        "country": "日本",
        "currency": "JPY",
        "language": "日语",
        "timezone": "UTC+9",
        "best_season": "3-5月（樱花季）、10-11月（红叶季）",
        "visa": "中国护照需要签证，可申请单次/多次旅游签",
        "electricity": "110V，A型插头（两脚扁插）",
        "attractions": [
            {"name": "浅草寺", "type": "寺庙", "ticket": "免费", "duration": "1-2小时", "tip": "早上人少，雷门是标志性打卡点"},
            {"name": "东京塔", "type": "地标", "ticket": "1200日元（约￥60）", "duration": "1-2小时", "tip": "夜景更美，可提前网上购票"},
            {"name": "涩谷十字路口", "type": "地标", "ticket": "免费", "duration": "30分钟", "tip": "站在星巴克二楼俯拍最佳"},
            {"name": "明治神宫", "type": "神社", "ticket": "免费", "duration": "1-2小时", "tip": "清晨去人少，可在入口处抽签"},
            {"name": "秋叶原", "type": "购物", "ticket": "免费", "duration": "2-3小时", "tip": "电器和动漫周边天堂，比价后再买"},
            {"name": "新宿御苑", "type": "公园", "ticket": "500日元（约￥25）", "duration": "2小时", "tip": "樱花季必去，禁止饮酒"},
            {"name": "东京晴空塔", "type": "地标", "ticket": "2100日元（约￥105）", "duration": "1-2小时", "tip": "比东京塔更高，350米展望台"},
            {"name": "筑地外市场", "type": "美食", "ticket": "免费", "duration": "2小时", "tip": "早上6-9点最新鲜，寿司大和大和寿司排队最长"},
            {"name": "台场", "type": "综合", "ticket": "免费", "duration": "半天", "tip": "可看自由女神像和彩虹大桥夜景"},
            {"name": "皇居外苑", "type": "历史", "ticket": "免费", "duration": "1-2小时", "tip": "二重桥是经典拍照点，内部参观需预约"},
        ],
        "restaurants": [
            {"name": "一兰拉面（涩谷店）", "type": "拉面", "budget": "1000日元（约￥50）", "must_try": "天然豚骨拉面", "tip": "24小时营业，自助点餐机"},
            {"name": "寿司大（筑地）", "type": "寿司", "budget": "4000日元（约￥200）", "must_try": "omakase套餐", "tip": "凌晨排队，建议5点到"},
            {"name": "鸟贵族", "type": "居酒屋", "budget": "2000日元（约￥100）", "must_try": "烤鸡肉串", "tip": "连锁店，性价比高"},
            {"name": "丸龟制面", "type": "乌冬面", "budget": "800日元（约￥40）", "must_try": "天妇罗乌冬", "tip": "自助取餐，出餐快"},
            {"name": "afuri（原宿店）", "type": "拉面", "budget": "1200日元（约￥60）", "must_try": "柚子盐拉面", "tip": "清爽口味，适合夏天"},
        ],
        "transport": {
            "airport_to_city": "成田机场→新宿：N'EX特快80分钟1270日元 / 羽田机场→新坐：京急线30分钟300日元",
            "daily_pass": "东京地铁24小时券600日元，Suica/Pasmo卡通用",
            "taxi": "起步价660日元（约￥33），短途不划算",
            "tips": "地铁覆盖全城，建议买交通卡，避免高峰期（7:30-9:30）",
        },
        "budget_estimate": {
            "budget": {"daily": "8000-12000日元（约400-600元）", "note": "胶囊酒店+便利店+地铁"},
            "mid_range": {"daily": "15000-25000日元（约750-1250元）", "note": "商务酒店+普通餐厅+地铁"},
            "luxury": {"daily": "30000+日元（约1500+元）", "note": "高端酒店+精致餐厅+出租车"},
        },
        "safety": {
            "level": "非常安全，犯罪率极低",
            "scams": "歌舞伎町有拉客酒吧，不要跟陌生人走",
            "emergency": {"police": "110", "ambulance": "119", "fire": "119"},
            "embassy": "中国驻日大使馆：03-3403-3388",
            "tips": "不需要给小费，地铁安静不要打电话，垃圾随身带走",
        },
    },
    "巴黎": {
        "country": "法国",
        "currency": "EUR",
        "language": "法语",
        "timezone": "UTC+1",
        "best_season": "4-6月、9-10月（气候宜人，游客较少）",
        "visa": "申根签证",
        "electricity": "230V，C/E型插头",
        "attractions": [
            {"name": "埃菲尔铁塔", "type": "地标", "ticket": "电梯26.8€（约￥210）", "duration": "2-3小时", "tip": "提前网上预约，日落时分最美"},
            {"name": "卢浮宫", "type": "博物馆", "ticket": "17€（约￥133）", "duration": "半天-1天", "tip": "周三周五开到21:45，人少"},
            {"name": "凯旋门", "type": "地标", "ticket": "13€（约￥102）", "duration": "1小时", "tip": "登顶可看12条大道辐射"},
            {"name": "巴黎圣母院", "type": "教堂", "ticket": "免费（维修中）", "duration": "30分钟", "tip": "2019年火灾后仍在修复"},
            {"name": "奥赛博物馆", "type": "博物馆", "ticket": "16€（约￥125）", "duration": "2-3小时", "tip": "印象派收藏最全"},
            {"name": "蒙马特高地", "type": "街区", "ticket": "免费", "duration": "2-3小时", "tip": "圣心大教堂+画家广场"},
            {"name": "塞纳河游船", "type": "体验", "ticket": "15€（约￥117）", "duration": "1小时", "tip": "夜游看灯光更浪漫"},
            {"name": "凡尔赛宫", "type": "宫殿", "ticket": "20€（约￥157）", "duration": "半天", "tip": "RER C线可达，周二闭馆"},
            {"name": "香榭丽舍大街", "type": "购物", "ticket": "免费", "duration": "2小时", "tip": "奢侈品旗舰店集中"},
            {"name": "橘园美术馆", "type": "博物馆", "ticket": "12.5€（约￥98）", "duration": "1-2小时", "tip": "莫奈巨幅睡莲真迹"},
        ],
        "restaurants": [
            {"name": "Le Bouillon Chartier", "type": "法餐", "budget": "15-25€（约￥120-200）", "must_try": "法式洋葱汤、鸭腿", "tip": "百年老店，不接受预约"},
            {"name": "Café de Flore", "type": "咖啡馆", "budget": "10-20€（约￥80-160）", "must_try": "热巧克力、可颂", "tip": "萨特和波伏娃常去"},
            {"name": "L'As du Fallafel", "type": "中东菜", "budget": "8-12€（约￥60-95）", "must_try": "法拉费三明治", "tip": "玛黑区排队最长"},
            {"name": "Pierre Hermé", "type": "甜品", "budget": "5-15€（约￥40-120）", "must_try": "马卡龙", "tip": "被称为马卡龙界的毕加索"},
            {"name": "Le Comptoir du Panthéon", "type": "法餐", "budget": "20-35€（约￥160-275）", "must_try": "法式蜗牛", "tip": "拉丁区经典小酒馆"},
        ],
        "transport": {
            "airport_to_city": "戴高乐机场→市中心：RER B线35分钟11.4€ / Orly机场：Orlybus8€",
            "daily_pass": "Navigo Easy卡，单次2.15€，日票8.45€",
            "taxi": "起步价2.6€，机场到市中心50-60€",
            "tips": "地铁覆盖全城，注意扒手，RER线较乱",
        },
        "budget_estimate": {
            "budget": {"daily": "60-80€（约470-630元）", "note": "青旅+超市+地铁"},
            "mid_range": {"daily": "120-200€（约940-1570元）", "note": "三星酒店+普通餐厅+地铁"},
            "luxury": {"daily": "300+€（约2350+元）", "note": "五星酒店+米其林+出租车"},
        },
        "safety": {
            "level": "总体安全，注意扒手",
            "scams": "地铁签名请愿骗局、假金戒指骗局、强卖手绳",
            "emergency": {"police": "17", "ambulance": "15", "fire": "18"},
            "embassy": "中国驻法大使馆：01-49521950",
            "tips": "护照复印件随身带，地铁注意财物，晚上避开18区",
        },
    },
    "曼谷": {
        "country": "泰国",
        "currency": "THB",
        "language": "泰语",
        "timezone": "UTC+7",
        "best_season": "11-2月（凉季，最舒适）",
        "visa": "中国护照落地签15天 / 提前办旅游签60天",
        "electricity": "220V，A/B/C型插头通用",
        "attractions": [
            {"name": "大皇宫", "type": "宫殿", "ticket": "500泰铢（约￥100）", "duration": "2-3小时", "tip": "着装要求严格，不能穿短裤拖鞋"},
            {"name": "卧佛寺", "type": "寺庙", "ticket": "200泰铢（约￥40）", "duration": "1-2小时", "tip": "最大卧佛，可体验泰式按摩"},
            {"name": "郑王庙", "type": "寺庙", "ticket": "100泰铢（约￥20）", "duration": "1小时", "tip": "黎明寺，日落最美"},
            {"name": "恰图恰周末市场", "type": "市场", "ticket": "免费", "duration": "半天", "tip": "15000+摊位，砍价是必须的"},
            {"name": "考山路", "type": "街区", "ticket": "免费", "duration": "晚上", "tip": "背包客天堂，夜生活丰富"},
            {"name": "暹罗海洋世界", "type": "水族馆", "ticket": "990泰铢（约￥200）", "duration": "2-3小时", "tip": "东南亚最大，Siam Paragon地下"},
            {"name": "水上市场", "type": "市场", "ticket": "免费（船费另算）", "duration": "半天", "tip": "丹嫩沙多最出名，早上7-9点最佳"},
            {"name": "四面佛", "type": "寺庙", "ticket": "免费", "duration": "30分钟", "tip": "最灵验，还愿舞20泰铢起"},
            {"name": "暹罗天地", "type": "购物", "ticket": "免费", "duration": "3小时", "tip": "河畔商场，有免费接驳船"},
            {"name": "金佛寺", "type": "寺庙", "ticket": "40泰铢（约￥8）", "duration": "30分钟", "tip": "5.5吨纯金佛像"},
        ],
        "restaurants": [
            {"name": "Thip Samai", "type": "泰餐", "budget": "80-150泰铢（约￥16-30）", "must_try": "泰式炒河粉（Pad Thai）", "tip": "排队名店，晚上6点后去"},
            {"name": "Jay Fai", "type": "泰餐", "budget": "500-1000泰铢（约￥100-200）", "must_try": "蟹肉蛋卷、冬阴功", "tip": "米其林一星街头摊，需预约"},
            {"name": "建兴酒家", "type": "海鲜", "budget": "300-600泰铢（约￥60-120）", "must_try": "咖喱蟹", "tip": "连锁店，游客本地人都爱"},
            {"name": "Nai Mong Hoi Tod", "type": "小吃", "budget": "60-100泰铢（约￥12-20）", "must_try": "蚝煎", "tip": "唐人街老字号"},
            {"name": "After You", "type": "甜品", "budget": "150-300泰铢（约￥30-60）", "must_try": "芒果糯米饭刨冰", "tip": "连锁甜品店，到处都有"},
        ],
        "transport": {
            "airport_to_city": "素万那普机场→市中心：机场快线45泰铢 / 出租车300-400泰铢（含高速费）",
            "daily_pass": "BTS一日票140泰铢，MRT类似",
            "taxi": "起步价35泰铢，打表是必须的",
            "tips": "BTS/MRT覆盖主要景点，突突车要砍价，Grab比出租车便宜",
        },
        "budget_estimate": {
            "budget": {"daily": "800-1500泰铢（约160-300元）", "note": "青旅+路边摊+公共交通"},
            "mid_range": {"daily": "2000-4000泰铢（约400-800元）", "note": "三星酒店+普通餐厅+偶尔打车"},
            "luxury": {"daily": "6000+泰铢（约1200+元）", "note": "五星酒店+精致餐厅+包车"},
        },
        "safety": {
            "level": "旅游区安全，注意财物",
            "scams": "宝石店骗局、tuktuk低价游骗局、假景点关门骗局",
            "emergency": {"police": "191", "ambulance": "1669", "fire": "199"},
            "embassy": "中国驻泰大使馆：02-2450088",
            "tips": "不要摸别人头，脚不要指人，进寺庙脱鞋",
        },
    },
    "首尔": {
        "country": "韩国",
        "currency": "KRW",
        "language": "韩语",
        "timezone": "UTC+9",
        "best_season": "3-5月（樱花）、9-11月（红叶）",
        "visa": "中国护照需要签证",
        "electricity": "220V，C/F型插头",
        "attractions": [
            {"name": "景福宫", "type": "宫殿", "ticket": "3000韩元（约￥16）", "duration": "2小时", "tip": "穿韩服免费入场"},
            {"name": "北村韩屋村", "type": "历史", "ticket": "免费", "duration": "1-2小时", "tip": "传统韩屋建筑群，拍照圣地"},
            {"name": "明洞", "type": "购物", "ticket": "免费", "duration": "半天", "tip": "化妆品天堂，赠品超多"},
            {"name": "南山塔", "type": "地标", "ticket": "11000韩元（约￥59）", "duration": "1-2小时", "tip": "夜景必去，挂同心锁"},
            {"name": "弘大", "type": "街区", "ticket": "免费", "duration": "晚上", "tip": "年轻人聚集地，街头表演多"},
            {"name": "梨泰院", "type": "街区", "ticket": "免费", "duration": "2-3小时", "tip": "异国风情，美食多样"},
            {"name": "昌德宫", "type": "宫殿", "ticket": "3000韩元（约￥16）", "duration": "2小时", "tip": "后花园秘苑需预约"},
            {"name": "东大门设计广场", "type": "建筑", "ticket": "免费", "duration": "1小时", "tip": "扎哈·哈迪德设计，夜景超美"},
            {"name": "广藏市场", "type": "市场", "ticket": "免费", "duration": "2小时", "tip": "小吃天堂，绿豆煎饼必吃"},
            {"name": "汉江公园", "type": "公园", "ticket": "免费", "duration": "2小时", "tip": "可以点炸鸡外卖在江边吃"},
        ],
        "restaurants": [
            {"name": "姜虎东白丁", "type": "烤肉", "budget": "15000-25000韩元（约￥80-135）", "must_try": "五花肉、牛里脊", "tip": "连锁店，分量大"},
            {"name": "土俗村参鸡汤", "type": "参鸡汤", "budget": "18000韩元（约￥97）", "must_try": "参鸡汤", "tip": "景福宫旁，排队名店"},
            {"name": "广藏市场麻药紫菜饭卷", "type": "小吃", "budget": "3000-5000韩元（约￥16-27）", "must_try": "紫菜饭卷", "tip": "市场里排队最长的摊位"},
            {"name": "Myeongdong Kyoja", "type": "面食", "budget": "9000-12000韩元（约￥49-65）", "must_try": "刀削面、饺子", "tip": "明洞老字号"},
            {"name": "Isaac Toast", "type": "快餐", "budget": "3000-5000韩元（约￥16-27）", "must_try": "烤肉吐司", "tip": "韩国国民早餐"},
        ],
        "transport": {
            "airport_to_city": "仁川机场→首尔站：AREX直快43分钟9000韩元 / 普通65分钟4150韩元",
            "daily_pass": "T-money卡通用，单次1250韩元",
            "taxi": "起步价3800韩元（约￥20），深夜加价",
            "tips": "地铁最方便，T-money卡便利店可买可充值",
        },
        "budget_estimate": {
            "budget": {"daily": "50000-80000韩元（约270-430元）", "note": "民宿+便利店+地铁"},
            "mid_range": {"daily": "100000-200000韩元（约540-1080元）", "note": "商务酒店+普通餐厅+地铁"},
            "luxury": {"daily": "300000+韩元（约1620+元）", "note": "高端酒店+精致餐厅+出租车"},
        },
        "safety": {
            "level": "非常安全",
            "scams": "明洞有假韩妆店，注意辨别",
            "emergency": {"police": "112", "ambulance": "119", "fire": "119"},
            "embassy": "中国驻韩大使馆：02-7381038",
            "tips": "地铁老弱优先席不要坐，进屋脱鞋，吃饭不要自己倒酒",
        },
    },
    "新加坡": {
        "country": "新加坡",
        "currency": "SGD",
        "language": "英语/中文/马来语",
        "timezone": "UTC+8",
        "best_season": "全年适宜，避开11-1月雨季",
        "visa": "中国护照需要签证（电子签）",
        "electricity": "230V，G型插头（三脚方插）",
        "attractions": [
            {"name": "滨海湾花园", "type": "花园", "ticket": "28新元（约￥150）", "duration": "3小时", "tip": "超级树灯光秀每晚19:45、20:45免费"},
            {"name": "鱼尾狮公园", "type": "地标", "ticket": "免费", "duration": "30分钟", "tip": "新加坡标志，拍照必去"},
            {"name": "环球影城", "type": "主题公园", "ticket": "62新元（约￥330）", "duration": "1天", "tip": "圣淘沙岛上，工作日人少"},
            {"name": "牛车水", "type": "街区", "ticket": "免费", "duration": "2小时", "tip": "唐人街，美食集中"},
            {"name": "小印度", "type": "街区", "ticket": "免费", "duration": "2小时", "tip": "色彩斑斓，印度文化体验"},
            {"name": "植物园", "type": "花园", "ticket": "免费（国家兰花园5新元）", "duration": "2-3小时", "tip": "UNESCO世界遗产"},
            {"name": "金沙酒店无边泳池", "type": "体验", "ticket": "住客免费/非住客23新元观景台", "duration": "1-2小时", "tip": "57楼无边泳池，城市全景"},
            {"name": "圣淘沙", "type": "综合", "ticket": "入岛4新元", "duration": "1天", "tip": "环球影城+海洋馆+海滩"},
            {"name": "国家美术馆", "type": "博物馆", "ticket": "20新元（约￥107）", "duration": "2-3小时", "tip": "东南亚最大美术馆"},
            {"name": "夜间动物园", "type": "动物园", "ticket": "45新元（约￥240）", "duration": "3小时", "tip": "全球首创夜间动物园"},
        ],
        "restaurants": [
            {"name": "天天海南鸡饭", "type": "鸡饭", "budget": "5-8新元（约￥27-43）", "must_try": "海南鸡饭", "tip": "牛车水麦士威熟食中心，排队最长"},
            {"name": "松发肉骨茶", "type": "肉骨茶", "budget": "8-15新元（约￥43-80）", "must_try": "肉骨茶", "tip": "胡椒味浓，连锁店"},
            {"name": "珍宝海鲜", "type": "海鲜", "budget": "50-100新元（约￥270-530）", "must_try": "辣椒螃蟹", "tip": "克拉码头店，需预约"},
            {"name": "老曾记", "type": "小吃", "budget": "3-5新元（约￥16-27）", "must_try": "咖喱角", "tip": "连锁店，到处都有"},
            {"name": "亚坤", "type": "早餐", "budget": "5-8新元（约￥27-43）", "must_try": "咖椰吐司+半熟蛋", "tip": "新加坡国民早餐"},
        ],
        "transport": {
            "airport_to_city": "樟宜机场→市中心：地铁30分钟2新元 / 出租车20-30新元",
            "daily_pass": "EZ-Link卡通用，单次1-2新元",
            "taxi": "起步价3.9新元，高峰期加价",
            "tips": "地铁覆盖全城，Grab比出租车便宜，不要在地铁吃喝（罚款500新元）",
        },
        "budget_estimate": {
            "budget": {"daily": "50-80新元（约270-430元）", "note": "青旅+熟食中心+地铁"},
            "mid_range": {"daily": "120-200新元（约640-1070元）", "note": "三星酒店+普通餐厅+地铁"},
            "luxury": {"daily": "300+新元（约1600+元）", "note": "金沙酒店+精致餐厅+出租车"},
        },
        "safety": {
            "level": "全球最安全的城市之一",
            "scams": "几乎没有，法律极严",
            "emergency": {"police": "999", "ambulance": "995", "fire": "995"},
            "embassy": "中国驻新加坡大使馆：64180252",
            "tips": "不要乱扔垃圾（罚款1000新元）、不要嚼口香糖、不要在地铁饮食",
        },
    },
    "成都": {
        "country": "中国",
        "currency": "CNY",
        "language": "中文（四川话）",
        "timezone": "UTC+8",
        "best_season": "3-6月、9-11月（避开盛夏湿热）",
        "visa": "国内无需签证",
        "electricity": "220V，A/I型插头",
        "attractions": [
            {"name": "大熊猫繁育研究基地", "type": "动物园", "ticket": "55元", "duration": "半天", "tip": "早上7:30开园，熊猫最活跃"},
            {"name": "武侯祠", "type": "历史", "ticket": "50元", "duration": "2小时", "tip": "三国文化圣地，红墙竹影拍照"},
            {"name": "锦里", "type": "街区", "ticket": "免费", "duration": "2小时", "tip": "小吃一条街，晚上灯笼很美"},
            {"name": "宽窄巷子", "type": "街区", "ticket": "免费", "duration": "2-3小时", "tip": "清朝老街，掏耳朵体验"},
            {"name": "都江堰", "type": "历史", "ticket": "80元", "duration": "半天", "tip": "高铁30分钟可达"},
            {"name": "春熙路", "type": "购物", "ticket": "免费", "duration": "2-3小时", "tip": "太古里IFS熊猫屁股打卡"},
            {"name": "人民公园", "type": "公园", "ticket": "免费", "duration": "2小时", "tip": "鹤鸣茶社喝盖碗茶"},
            {"name": "杜甫草堂", "type": "历史", "ticket": "50元", "duration": "2小时", "tip": "杜甫流寓成都时的故居"},
            {"name": "青城山", "type": "自然", "ticket": "80元", "duration": "1天", "tip": "道教名山，前山人文后山自然"},
            {"name": "九眼桥", "type": "夜生活", "ticket": "免费", "duration": "晚上", "tip": "酒吧一条街，夜景很美"},
        ],
        "restaurants": [
            {"name": "小龙坎火锅", "type": "火锅", "budget": "80-120元", "must_try": "牛油锅底、毛肚", "tip": "连锁店，排队要趁早"},
            {"name": "龙抄手", "type": "小吃", "budget": "20-40元", "must_try": "抄手、担担面", "tip": "春熙路总店"},
            {"name": "陈麻婆豆腐", "type": "川菜", "budget": "30-60元", "must_try": "麻婆豆腐", "tip": "总店在西玉龙街"},
            {"name": "双流老妈兔头", "type": "小吃", "budget": "15-25元/个", "must_try": "麻辣兔头", "tip": "成都特色，要啃"},
            {"name": "鹤鸣茶社", "type": "茶馆", "budget": "15-30元", "must_try": "盖碗茶", "tip": "人民公园内，百年老茶馆"},
        ],
        "transport": {
            "airport_to_city": "双流机场→市中心：地铁10号线30分钟 / 天府机场→市中心：18号线50分钟",
            "daily_pass": "天府通卡，地铁单次2-8元",
            "taxi": "起步价8元，滴滴更方便",
            "tips": "地铁覆盖主要景点，去都江堰坐高铁",
        },
        "budget_estimate": {
            "budget": {"daily": "150-250元", "note": "青旅+路边摊+地铁"},
            "mid_range": {"daily": "300-500元", "note": "快捷酒店+普通餐厅+地铁"},
            "luxury": {"daily": "800+元", "note": "高端酒店+精致餐厅+打车"},
        },
        "safety": {
            "level": "安全，旅游城市",
            "scams": "景区周边高价出租车、锦里高价小吃",
            "emergency": {"police": "110", "ambulance": "120", "fire": "119"},
            "tips": "夏天湿热注意防暑，火锅微辣就很辣",
        },
    },
    "伦敦": {
        "country": "英国",
        "currency": "GBP",
        "language": "英语",
        "timezone": "UTC+0",
        "best_season": "5-9月（夏季最温暖）",
        "visa": "需要英国签证",
        "electricity": "230V，G型插头（三脚方插）",
        "attractions": [
            {"name": "大英博物馆", "type": "博物馆", "ticket": "免费", "duration": "半天-1天", "tip": "罗塞塔石碑和埃及木乃伊必看"},
            {"name": "白金汉宫", "type": "宫殿", "ticket": "30英镑（约￥270）", "duration": "2-3小时", "tip": "夏季开放，换岗仪式11:00"},
            {"name": "伦敦塔", "type": "历史", "ticket": "33.6英镑（约￥300）", "duration": "3小时", "tip": "皇冠珠宝必看"},
            {"name": "大本钟", "type": "地标", "ticket": "免费（外观）", "duration": "30分钟", "tip": "与威斯敏斯特教堂一起游览"},
            {"name": "威斯敏斯特教堂", "type": "教堂", "ticket": "27英镑（约￥243）", "duration": "2小时", "tip": "多位国王加冕地"},
            {"name": "伦敦眼", "type": "地标", "ticket": "32英镑（约￥288）", "duration": "1小时", "tip": "提前网上买票可免排队"},
            {"name": "塔桥", "type": "地标", "ticket": "11.4英镑（约￥103）", "duration": "1小时", "tip": "可走上层玻璃步道"},
            {"name": "海德公园", "type": "公园", "ticket": "免费", "duration": "2小时", "tip": "喂天鹅，演讲者之角"},
            {"name": "国家美术馆", "type": "博物馆", "ticket": "免费", "duration": "半天", "tip": "梵高向日葵真迹"},
            {"name": "诺丁山", "type": "街区", "ticket": "免费", "duration": "2小时", "tip": "周六Portobello集市"},
        ],
        "restaurants": [
            {"name": "Dishoom", "type": "印度菜", "budget": "15-25英镑（约￥135-225）", "must_try": "培根蛋印度卷", "tip": "排队长，建议工作日去"},
            {"name": "Borough Market", "type": "市集", "budget": "10-20英镑（约￥90-180）", "must_try": "各种街头小吃", "tip": "周六最热闹"},
            {"name": "The Ivy", "type": "英式西餐", "budget": "30-50英镑（约￥270-450）", "must_try": "牧羊人派", "tip": "需要提前预约"},
            {"name": "Fish & Chips", "type": "英式快餐", "budget": "10-15英镑（约￥90-135）", "must_try": "炸鱼薯条", "tip": "Poppies和Rock Sole口碑好"},
            {"name": "Sketch", "type": "下午茶", "budget": "50-80英镑（约￥450-720）", "must_try": "下午茶套餐", "tip": "粉红厅拍照绝佳"},
        ],
        "transport": {
            "airport_to_city": "希思罗机场→市中心：Heathrow Express 15分钟22英镑 / 地铁Piccadilly线50分钟5.5英镑",
            "daily_pass": "Oyster卡日封顶8.1英镑，公交日封顶5.25英镑",
            "taxi": "黑色出租车起步价3.8英镑，较贵",
            "tips": "地铁覆盖全城，办Oyster卡最划算，靠左通行",
        },
        "budget_estimate": {
            "budget": {"daily": "60-80英镑（约540-720元）", "note": "青旅+超市+地铁"},
            "mid_range": {"daily": "120-200英镑（约1080-1800元）", "note": "三星酒店+普通餐厅+地铁"},
            "luxury": {"daily": "300+英镑（约2700+元）", "note": "五星酒店+米其林+出租车"},
        },
        "safety": {
            "level": "总体安全，注意扒手",
            "scams": "地铁上注意财物，假警察骗局",
            "emergency": {"police": "999", "ambulance": "999", "fire": "999"},
            "embassy": "中国驻英大使馆：020-72994049",
            "tips": "靠左通行，地铁扶梯靠右站，小费10-15%",
        },
    },
    "悉尼": {
        "country": "澳大利亚",
        "currency": "AUD",
        "language": "英语",
        "timezone": "UTC+10",
        "best_season": "10-4月（春夏，适合户外）",
        "visa": "需要澳大利亚签证（ETA电子签）",
        "electricity": "230V，I型插头（三脚斜插）",
        "attractions": [
            {"name": "悉尼歌剧院", "type": "地标", "ticket": "参观43澳元（约￥200）", "duration": "2小时", "tip": "入内参观需导览，外面拍照免费"},
            {"name": "悉尼港大桥", "type": "地标", "ticket": "免费（步行）/攀登198澳元起", "duration": "1-3小时", "tip": "BridgeClimb需预约"},
            {"name": "邦迪海滩", "type": "海滩", "ticket": "免费", "duration": "半天", "tip": "注意红旗区域不要游泳"},
            {"name": "达令港", "type": "综合", "ticket": "免费", "duration": "半天", "tip": "周六晚有烟花"},
            {"name": "塔龙加动物园", "type": "动物园", "ticket": "49澳元（约￥228）", "duration": "半天", "tip": "可看到考拉和袋鼠"},
            {"name": "蓝山国家公园", "type": "自然", "ticket": "免费（缆车另付）", "duration": "1天", "tip": "三姐妹峰，从中央车站坐火车2小时"},
            {"name": "岩石区", "type": "历史街区", "ticket": "免费", "duration": "2小时", "tip": "周末有市集"},
            {"name": "皇家植物园", "type": "公园", "ticket": "免费", "duration": "2小时", "tip": "歌剧院最佳拍照点"},
            {"name": "悉尼水族馆", "type": "水族馆", "ticket": "46澳元（约￥214）", "duration": "2小时", "tip": "有玻璃底船"},
            {"name": "曼利海滩", "type": "海滩", "ticket": "免费（渡轮16澳元）", "duration": "半天", "tip": "渡轮风景很好"},
        ],
        "restaurants": [
            {"name": "Quay", "type": "高端澳洲菜", "budget": "150-250澳元（约￥700-1160）", "must_try": "雪蟹面", "tip": "需提前预约，港景位"},
            {"name": "Fish Market", "type": "海鲜市场", "budget": "20-40澳元（约￥93-186）", "must_try": "生蚝和龙虾", "tip": "早上最新鲜"},
            {"name": "Harry's Cafe de Wheels", "type": "澳式快餐", "budget": "10-15澳元（约￥47-70）", "must_try": "肉派", "tip": "悉尼经典街头小吃"},
            {"name": "The Grounds", "type": "brunch", "budget": "20-35澳元（约￥93-163）", "must_try": "牛油果吐司", "tip": "网红店，排队久"},
            {"name": "Hurricane's Grill", "type": "烧烤", "budget": "30-50澳元（约￥140-233）", "must_try": "肋眼牛排", "tip": "邦迪海滩附近"},
        ],
        "transport": {
            "airport_to_city": "机场→市中心：Airport Link火车17分钟19.4澳元 / 出租车约50澳元",
            "daily_pass": "Opal卡周日2.8澳元封顶，工作日日封顶16.8澳元",
            "taxi": "起步价3.6澳元，较贵",
            "tips": "火车+渡轮+公交都用Opal卡，周日出行最划算",
        },
        "budget_estimate": {
            "budget": {"daily": "80-120澳元（约370-560元）", "note": "青旅+超市+公交"},
            "mid_range": {"daily": "180-300澳元（约840-1400元）", "note": "三星酒店+普通餐厅"},
            "luxury": {"daily": "500+澳元（约2330+元）", "note": "五星酒店+高端餐厅"},
        },
        "safety": {
            "level": "非常安全",
            "scams": "几乎没有，注意海滩安全（红旗不要下水）",
            "emergency": {"police": "000", "ambulance": "000", "fire": "000"},
            "embassy": "中国驻悉尼总领馆：02-85958002",
            "tips": "防晒霜必备（紫外线极强），海滩注意救生旗",
        },
    },
    "迪拜": {
        "country": "阿联酋",
        "currency": "AED",
        "language": "阿拉伯语（英语通用）",
        "timezone": "UTC+4",
        "best_season": "11-3月（冬季，20-30度）",
        "visa": "中国护照可落地签30天",
        "electricity": "220V，G型插头（三脚方插）",
        "attractions": [
            {"name": "哈利法塔", "type": "地标", "ticket": "169迪拉姆（约￥310）", "duration": "2小时", "tip": "148层观景台需预约"},
            {"name": "帆船酒店", "type": "酒店", "ticket": "参观需预约下午茶", "duration": "2小时", "tip": "下午茶约600迪拉姆"},
            {"name": "迪拜购物中心", "type": "购物", "ticket": "免费", "duration": "半天-1天", "tip": "有水族馆和室内滑冰场"},
            {"name": "棕榈岛", "type": "人工岛", "ticket": "免费（轻轨15迪拉姆）", "duration": "半天", "tip": "亚特兰蒂斯酒店在岛尖"},
            {"name": "迪拜老城区", "type": "历史", "ticket": "免费", "duration": "半天", "tip": "坐水上巴士1迪拉姆过河"},
            {"name": "沙漠冲沙", "type": "体验", "ticket": "200-400迪拉姆（约￥365-730）", "duration": "半天", "tip": "含BBQ晚餐和表演"},
            {"name": "迪拜喷泉", "type": "表演", "ticket": "免费", "duration": "30分钟", "tip": "每晚6点起每30分钟一场"},
            {"name": "迪拜博物馆", "type": "博物馆", "ticket": "3迪拉姆（约￥5）", "duration": "1小时", "tip": "了解迪拜历史"},
            {"name": "黄金市集", "type": "市场", "ticket": "免费", "duration": "2小时", "tip": "可以砍价"},
            {"name": "朱美拉海滩", "type": "海滩", "ticket": "免费", "duration": "半天", "tip": "帆船酒店最佳拍照点"},
        ],
        "restaurants": [
            {"name": "Al Mallah", "type": "中东菜", "budget": "30-60迪拉姆（约￥55-110）", "must_try": "shawarma", "tip": "当地人最爱的街头小吃"},
            {"name": "Pierchic", "type": "海鲜", "budget": "300-500迪拉姆（约￥550-915）", "must_try": "海鲜拼盘", "tip": "建在海上的餐厅"},
            {"name": "Ravi Restaurant", "type": "巴基斯坦菜", "budget": "20-40迪拉姆（约￥37-73）", "must_try": "黄油鸡", "tip": "性价比之王"},
            {"name": "Atmosphere", "type": "高端西餐", "budget": "500-800迪拉姆（约￥915-1460）", "must_try": "牛排", "tip": "哈利法塔122层"},
            {"name": "迪拜购物中心美食广场", "type": "快餐", "budget": "30-60迪拉姆（约￥55-110）", "must_try": "各种国际美食", "tip": "选择多，价格合理"},
        ],
        "transport": {
            "airport_to_city": "迪拜机场→市中心：地铁红线30分钟6迪拉姆 / 出租车约50迪拉姆",
            "daily_pass": "Nol银卡地铁单次4-8迪拉姆，日票22迪拉姆",
            "taxi": "起步价12迪拉姆，比地铁贵但方便",
            "tips": "地铁分金卡车厢和女士车厢，普通车厢最便宜",
        },
        "budget_estimate": {
            "budget": {"daily": "200-350迪拉姆（约365-640元）", "note": "经济酒店+地铁+普通餐厅"},
            "mid_range": {"daily": "500-1000迪拉姆（约915-1830元）", "note": "四星酒店+中档餐厅+出租车"},
            "luxury": {"daily": "2000+迪拉姆（约3660+元）", "note": "七星酒店+高端餐厅"},
        },
        "safety": {
            "level": "非常安全，犯罪率极低",
            "scams": "出租车绕路，注意正规出租车",
            "emergency": {"police": "999", "ambulance": "998", "fire": "997"},
            "embassy": "中国驻阿联酋大使馆：02-4434276",
            "tips": "尊重当地文化，公共场合不要亲昵，斋月期间白天不要公开饮食",
        },
    },
    "巴厘岛": {
        "country": "印尼",
        "currency": "IDR",
        "language": "印尼语（旅游区英语通用）",
        "timezone": "UTC+8",
        "best_season": "4-10月（旱季）",
        "visa": "免签30天",
        "electricity": "230V，C/F型插头",
        "attractions": [
            {"name": "海神庙", "type": "寺庙", "ticket": "60000印尼盾（约￥27）", "duration": "2小时", "tip": "日落时分最美"},
            {"name": "乌布皇宫", "type": "宫殿", "ticket": "免费", "duration": "1小时", "tip": "晚上有传统舞蹈表演"},
            {"name": "德格拉朗梯田", "type": "自然", "ticket": "50000印尼盾（约￥23）", "duration": "2-3小时", "tip": "早上光线最好"},
            {"name": "库塔海滩", "type": "海滩", "ticket": "免费", "duration": "半天", "tip": "适合冲浪初学者"},
            {"name": "圣泉寺", "type": "寺庙", "ticket": "60000印尼盾（约￥27）", "duration": "1-2小时", "tip": "需围纱笼"},
            {"name": "金巴兰海滩", "type": "海滩", "ticket": "免费", "duration": "半天", "tip": "海边BBQ看日落"},
            {"name": "乌鲁瓦图断崖", "type": "自然", "ticket": "50000印尼盾（约￥23）", "duration": "2小时", "tip": "傍晚有Kecak舞表演"},
            {"name": "猴林", "type": "自然", "ticket": "80000印尼盾（约￥36）", "duration": "1-2小时", "tip": "看好手机和帽子"},
            {"name": "蓝梦岛", "type": "海岛", "ticket": "快艇往返500000印尼盾（约￥228）", "duration": "1天", "tip": "恶魔之泪和梦幻海滩"},
            {"name": "水神庙", "type": "寺庙", "ticket": "75000印尼盾（约￥34）", "duration": "1-2小时", "tip": "湖中寺庙，很上镜"},
        ],
        "restaurants": [
            {"name": "Locavore", "type": "创意印尼菜", "budget": "500000-800000印尼盾（约￥228-365）", "must_try": "本地食材创意料理", "tip": "需提前预约"},
            {"name": "Bebek Bengil", "type": "印尼菜", "budget": "100000-200000印尼盾（约￥46-91）", "must_try": "脏鸭餐", "tip": "乌布名店"},
            {"name": "Warung Babi Guling Ibu Oka", "type": "印尼菜", "budget": "50000-100000印尼盾（约￥23-46）", "must_try": "烤乳猪", "tip": "中午就卖完"},
            {"name": "La Plancha", "type": "西班牙菜", "budget": "200000-400000印尼盾（约￥91-183）", "must_try": "海鲜饭", "tip": "彩色豆袋沙滩座"},
            {"name": "Nasi Ayam Bu Mangku", "type": "印尼菜", "budget": "30000-50000印尼盾（约￥14-23）", "must_try": "鸡肉饭", "tip": "当地人的最爱"},
        ],
        "transport": {
            "airport_to_city": "机场→库塔/水明漾：出租车10-20分钟100000印尼盾",
            "daily_pass": "无公共交通，需包车或骑摩托车",
            "taxi": "Grab打车为主，起步价约30000印尼盾",
            "tips": "没有公共交通，建议Grab打车或包车，摩托车租一天约70000印尼盾",
        },
        "budget_estimate": {
            "budget": {"daily": "300000-500000印尼盾（约137-228元）", "note": "民宿+当地餐厅+摩托车"},
            "mid_range": {"daily": "800000-1500000印尼盾（约365-685元）", "note": "泳池别墅+中档餐厅+包车"},
            "luxury": {"daily": "3000000+印尼盾（约1370+元）", "note": "五星度假村+SPA+私人管家"},
        },
        "safety": {
            "level": "总体安全，注意财物",
            "scams": "换汇陷阱（不要在街边换），假导游",
            "emergency": {"police": "110", "ambulance": "118", "fire": "113"},
            "embassy": "中国驻登巴萨总领馆：0361-239902",
            "tips": "喝瓶装水，注意防晒，寺庙需围纱笼",
        },
    },
    "纽约": {
        "country": "美国",
        "currency": "USD",
        "language": "英语",
        "timezone": "UTC-5",
        "best_season": "4-6月、9-11月（春秋最舒适）",
        "visa": "需要美国签证（B1/B2旅游签）",
        "electricity": "120V，A/B型插头",
        "attractions": [
            {"name": "自由女神像", "type": "地标", "ticket": "渡轮24美元（约￥173）", "duration": "半天", "tip": "皇冠票需提前2个月预约"},
            {"name": "时代广场", "type": "地标", "ticket": "免费", "duration": "1小时", "tip": "跨年夜最热闹"},
            {"name": "中央公园", "type": "公园", "ticket": "免费", "duration": "半天", "tip": "可以骑自行车"},
            {"name": "大都会博物馆", "type": "博物馆", "ticket": "30美元（约￥216）", "duration": "半天-1天", "tip": "周日纽约居民随意付费"},
            {"name": "帝国大厦", "type": "地标", "ticket": "44美元（约￥317）", "duration": "2小时", "tip": "夜景更好，Top of the Rock也很推荐"},
            {"name": "布鲁克林大桥", "type": "地标", "ticket": "免费", "duration": "1-2小时", "tip": "步行过桥，日落时分最美"},
            {"name": "911纪念馆", "type": "纪念馆", "ticket": "免费（博物馆26美元）", "duration": "2小时", "tip": "需提前预约"},
            {"name": "第五大道", "type": "购物", "ticket": "免费", "duration": "半天", "tip": "蒂芙尼、苹果旗舰店"},
            {"name": "百老汇", "type": "演出", "ticket": "80-200美元（约￥576-1440）", "duration": "3小时", "tip": "TKTS折扣亭买当日票"},
            {"name": "高线公园", "type": "公园", "ticket": "免费", "duration": "1-2小时", "tip": "废弃铁路改造的空中花园"},
        ],
        "restaurants": [
            {"name": "Katz's Delicatessen", "type": "美式熟食", "budget": "20-30美元（约￥144-216）", "must_try": "咸牛肉三明治", "tip": "百年老店"},
            {"name": "Joe's Pizza", "type": "披萨", "budget": "5-10美元（约￥36-72）", "must_try": "纽约薄底披萨", "tip": "格林威治村经典"},
            {"name": "Shake Shack", "type": "汉堡", "budget": "15-25美元（约￥108-180）", "must_try": "ShackBurger", "tip": "纽约起家的连锁"},
            {"name": "Le Bernardin", "type": "法式海鲜", "budget": "150-300美元（约￥1080-2160）", "must_try": "龙虾", "tip": "米其林三星，需提前预约"},
            {"name": "Chinatown街头小吃", "type": "中餐", "budget": "8-15美元（约￥58-108）", "must_try": "小笼包", "tip": "法拉盛比曼哈顿唐人街更正宗"},
        ],
        "transport": {
            "airport_to_city": "JFK→曼哈顿：AirTrain+地铁1小时8美元 / 出租车约70美元",
            "daily_pass": "地铁单次2.9美元，7天无限卡34美元",
            "taxi": "起步价3美元，黄色出租车是标志",
            "tips": "地铁24小时运行，7天卡最划算，高峰期打车反而慢",
        },
        "budget_estimate": {
            "budget": {"daily": "80-120美元（约576-864元）", "note": "青旅+快餐+地铁"},
            "mid_range": {"daily": "200-350美元（约1440-2520元）", "note": "三星酒店+普通餐厅+地铁"},
            "luxury": {"daily": "500+美元（约3600+元）", "note": "五星酒店+米其林+出租车"},
        },
        "safety": {
            "level": "总体安全，注意地铁和夜间安全",
            "scams": "假出租车、时代广场强制合影收费、地铁上注意财物",
            "emergency": {"police": "911", "ambulance": "911", "fire": "911"},
            "embassy": "中国驻纽约总领馆：212-2449392",
            "tips": "地铁注意安全，晚上避免去偏僻街区，小费15-20%",
        },
    },
    "柏林": {
        "country": "德国",
        "currency": "EUR",
        "language": "德语（英语通用）",
        "timezone": "UTC+1",
        "best_season": "5-9月（夏季最温暖）",
        "visa": "申根签证",
        "electricity": "230V，C/F型插头",
        "attractions": [
            {"name": "勃兰登堡门", "type": "地标", "ticket": "免费", "duration": "30分钟", "tip": "柏林必打卡"},
            {"name": "柏林墙纪念馆", "type": "纪念馆", "ticket": "免费", "duration": "2小时", "tip": "东边画廊最长的一段"},
            {"name": "博物馆岛", "type": "博物馆群", "ticket": "联票22欧元（约￥173）", "duration": "1天", "tip": "佩加蒙博物馆最值得"},
            {"name": "国会大厦", "type": "政治", "ticket": "免费（需预约）", "duration": "1-2小时", "tip": "玻璃穹顶可俯瞰全城"},
            {"name": "查理检查站", "type": "历史", "ticket": "免费（博物馆16欧元）", "duration": "1小时", "tip": "冷战标志性地点"},
            {"name": "东边画廊", "type": "艺术", "ticket": "免费", "duration": "1-2小时", "tip": "最长的柏林墙涂鸦段"},
            {"name": "波茨坦广场", "type": "现代", "ticket": "免费", "duration": "1小时", "tip": "现代建筑群"},
            {"name": "蒂尔加滕公园", "type": "公园", "ticket": "免费", "duration": "2小时", "tip": "胜利纪念柱在中间"},
            {"name": "犹太人纪念碑", "type": "纪念碑", "ticket": "免费", "duration": "1小时", "tip": "2711块混凝土碑"},
            {"name": "哈克市场", "type": "街区", "ticket": "免费", "duration": "2小时", "tip": "周末市集和咖啡馆"},
        ],
        "restaurants": [
            {"name": "Curry 36", "type": "德式快餐", "budget": "3-5欧元（约￥24-39）", "must_try": "咖喱香肠", "tip": "柏林标志性街头小吃"},
            {"name": "Mustafa's Gemüse Kebab", "type": "土耳其菜", "budget": "5-8欧元（约￥39-63）", "must_try": "蔬菜烤肉卷", "tip": "排队长但值得"},
            {"name": "Zur Letzten Instanz", "type": "德国菜", "budget": "15-25欧元（约￥118-196）", "must_try": "猪肘", "tip": "柏林最老的餐厅"},
            {"name": "Markthalle Neun", "type": "市集", "budget": "10-20欧元（约￥78-157）", "must_try": "各种本地小吃", "tip": "周四有街头食品集市"},
            {"name": "Tim Raue", "type": "高端亚洲菜", "budget": "100-200欧元（约￥785-1570）", "must_try": "创意亚洲料理", "tip": "米其林二星"},
        ],
        "transport": {
            "airport_to_city": "BER机场→市中心：火车40分钟3.8欧元 / 出租车约50欧元",
            "daily_pass": "AB区日票8.8欧元，周票36欧元",
            "taxi": "起步价3.9欧元",
            "tips": "公共交通覆盖全城，买日票最划算，自行车也很方便",
        },
        "budget_estimate": {
            "budget": {"daily": "40-60欧元（约314-471元）", "note": "青旅+超市+公交"},
            "mid_range": {"daily": "100-180欧元（约785-1413元）", "note": "三星酒店+普通餐厅+公交"},
            "luxury": {"daily": "300+欧元（约2355+元）", "note": "五星酒店+高端餐厅"},
        },
        "safety": {
            "level": "总体安全",
            "scams": "假请愿签名骗局，地铁上注意财物",
            "emergency": {"police": "110", "ambulance": "112", "fire": "112"},
            "embassy": "中国驻德大使馆：030-275880",
            "tips": "英语通用度高，周日商店关门，博物馆周一闭馆",
        },
    },
    "清迈": {
        "country": "泰国",
        "currency": "THB",
        "language": "泰语（旅游区英语通用）",
        "timezone": "UTC+7",
        "best_season": "11-2月（凉季）",
        "visa": "免签30天",
        "electricity": "220V，A/B/C型插头",
        "attractions": [
            {"name": "双龙寺", "type": "寺庙", "ticket": "30泰铢（约￥6）", "duration": "2小时", "tip": "可俯瞰全城"},
            {"name": "帕辛寺", "type": "寺庙", "ticket": "免费", "duration": "1小时", "tip": "古城内最著名的寺庙"},
            {"name": "清迈古城", "type": "历史", "ticket": "免费", "duration": "半天", "tip": "可以骑自行车逛"},
            {"name": "周日夜市", "type": "市集", "ticket": "免费", "duration": "晚上", "tip": "从帕辛寺延伸出来"},
            {"name": "清迈大学", "type": "校园", "ticket": "观光车60泰铢", "duration": "1小时", "tip": "校园很美"},
            {"name": "素贴山", "type": "自然", "ticket": "免费", "duration": "半天", "tip": "双龙寺在山顶"},
            {"name": "夜间动物园", "type": "动物园", "ticket": "800泰铢（约￥160）", "duration": "3小时", "tip": "有夜间游览车"},
            {"name": "大象自然公园", "type": "体验", "ticket": "2500泰铢（约￥500）", "duration": "半天", "tip": "不骑象，观察和喂食"},
            {"name": "清迈门夜市", "type": "市集", "ticket": "免费", "duration": "晚上", "tip": "当地人的夜市"},
            {"name": "丛林飞跃", "type": "体验", "ticket": "2000-3000泰铢（约￥400-600）", "duration": "半天", "tip": "多平台可选"},
        ],
        "restaurants": [
            {"name": "Khao Soi Khun Yai", "type": "泰北菜", "budget": "50-80泰铢（约￥10-16）", "must_try": "咖喱面", "tip": "清迈必吃"},
            {"name": "SP Chicken", "type": "烤鸡", "budget": "80-150泰铢（约￥16-30）", "must_try": "烤鸡", "tip": "本地人排队"},
            {"name": "Huen Phen", "type": "泰北菜", "budget": "100-200泰铢（约￥20-40）", "must_try": "泰北香肠", "tip": "中午和晚上不同风格"},
            {"name": "周六夜市小吃", "type": "街头小吃", "budget": "30-80泰铢（约￥6-16）", "must_try": "芒果糯米饭", "tip": "瓦洛洛市场附近"},
            {"name": "Cafe de Nimman", "type": "咖啡馆", "budget": "80-150泰铢（约￥16-30）", "must_try": "泰式奶茶", "tip": "宁曼路网红店"},
        ],
        "transport": {
            "airport_to_city": "机场→古城：红色双条车30泰铢 / 出租车约150泰铢",
            "daily_pass": "红色双条车城内20-30泰铢/次",
            "taxi": "Grab打车为主，起步约50泰铢",
            "tips": "古城内步行或骑自行车，出城用Grab或包双条车",
        },
        "budget_estimate": {
            "budget": {"daily": "500-1000泰铢（约100-200元）", "note": "青旅+街头小吃+双条车"},
            "mid_range": {"daily": "1500-3000泰铢（约300-600元）", "note": "精品酒店+餐厅+包车"},
            "luxury": {"daily": "5000+泰铢（约1000+元）", "note": "五星度假村+SPA+私人导游"},
        },
        "safety": {
            "level": "非常安全",
            "scams": "宝石店骗局（不要跟陌生人去买宝石），tuktuk车绕路",
            "emergency": {"police": "191", "ambulance": "1669", "fire": "199"},
            "embassy": "中国驻清迈总领馆：053-280380",
            "tips": "寺庙要脱鞋，不要摸头，女性不要碰僧侣",
        },
    },
    "马尔代夫": {
        "country": "马尔代夫",
        "currency": "MVR",
        "language": "迪维希语（英语通用）",
        "timezone": "UTC+5",
        "best_season": "11-4月（旱季）",
        "visa": "免签30天",
        "electricity": "230V，G型插头（三脚方插）",
        "attractions": [
            {"name": "水上别墅", "type": "住宿体验", "ticket": "含在酒店费用中", "duration": "全程", "tip": "马尔代夫的标志"},
            {"name": "浮潜", "type": "水上活动", "ticket": "30-80美元（约￥216-576）", "duration": "半天", "tip": "珊瑚礁浮潜必体验"},
            {"name": "深潜", "type": "水上活动", "ticket": "80-150美元（约￥576-1080）", "duration": "半天", "tip": "有PADI认证的潜点"},
            {"name": "海钓", "type": "体验", "ticket": "50-100美元（约￥360-720）", "duration": "半天", "tip": "日落海钓最浪漫"},
            {"name": "SPA", "type": "休闲", "ticket": "100-300美元（约￥720-2160）", "duration": "2小时", "tip": "水上SPA是特色"},
            {"name": "居民岛探访", "type": "文化", "ticket": "50-100美元（约￥360-720）", "duration": "1天", "tip": "了解当地生活"},
            {"name": "日落巡航", "type": "体验", "ticket": "50-100美元（约￥360-720）", "duration": "2小时", "tip": "可能看到海豚"},
            {"name": "水上运动", "type": "水上活动", "ticket": "30-80美元（约￥216-576）", "duration": "半天", "tip": "帆板、皮划艇等"},
            {"name": "海底餐厅", "type": "餐饮", "ticket": "200-500美元（约￥1440-3600）", "duration": "2小时", "tip": "需提前预约"},
            {"name": "无人岛野餐", "type": "体验", "ticket": "100-200美元（约￥720-1440）", "duration": "半天", "tip": "私人沙滩体验"},
        ],
        "restaurants": [
            {"name": "度假村主餐厅", "type": "国际菜", "budget": "含在全包套餐中", "must_try": "海鲜自助", "tip": "全包套餐最划算"},
            {"name": "Ithaa海底餐厅", "type": "高端西餐", "budget": "200-500美元（约￥1440-3600）", "must_try": "海鲜套餐", "tip": "全球唯一全海景海底餐厅"},
            {"name": "当地咖啡馆", "type": "本地菜", "budget": "5-15美元（约￥36-108）", "must_try": "金枪鱼咖喱", "tip": "在马累或居民岛才有"},
            {"name": "烧烤晚餐", "type": "BBQ", "budget": "50-100美元（约￥360-720）", "must_try": "龙虾BBQ", "tip": "沙滩烛光晚餐"},
            {"name": "度假村酒吧", "type": "酒吧", "budget": "10-30美元（约￥72-216）", "must_try": "热带鸡尾酒", "tip": "日落时分最佳"},
        ],
        "transport": {
            "airport_to_city": "马累机场→度假村：快艇/水飞，由度假村安排",
            "daily_pass": "岛内步行即可，岛间需快艇/水飞",
            "taxi": "马累出租车约5美元",
            "tips": "度假村会安排接送，水飞约400-600美元往返，快艇约150-300美元",
        },
        "budget_estimate": {
            "budget": {"daily": "200-400美元（约1440-2880元）", "note": "居民岛民宿+当地餐厅"},
            "mid_range": {"daily": "500-1000美元（约3600-7200元）", "note": "四星度假村+全包套餐"},
            "luxury": {"daily": "2000+美元（约14400+元）", "note": "五星水上别墅+私人管家"},
        },
        "safety": {
            "level": "非常安全",
            "scams": "几乎没有，注意防晒和水上安全",
            "emergency": {"police": "119", "ambulance": "102", "fire": "118"},
            "embassy": "中国驻马尔代夫大使馆：00960-3307200",
            "tips": "防晒必备，注意珊瑚不要踩，禁止带酒入境",
        },
    },
    "香港": {
        "country": "中国",
        "currency": "HKD",
        "language": "粤语（英语和普通话通用）",
        "timezone": "UTC+8",
        "best_season": "10-12月（秋冬最舒适）",
        "visa": "港澳通行证+签注",
        "electricity": "220V，G型插头（三脚方插）",
        "attractions": [
            {"name": "太平山顶", "type": "地标", "ticket": "缆车来回62港币（约￥56）", "duration": "2-3小时", "tip": "夜景最美"},
            {"name": "维多利亚港", "type": "地标", "ticket": "免费（天星小轮3港币）", "duration": "1小时", "tip": "每晚8点灯光秀"},
            {"name": "迪士尼乐园", "type": "主题公园", "ticket": "639港币（约￥575）", "duration": "1天", "tip": "工作日人少"},
            {"name": "海洋公园", "type": "主题公园", "ticket": "498港币（约￥448）", "duration": "1天", "tip": "有大熊猫"},
            {"name": "大屿山大佛", "type": "景点", "ticket": "免费（缆车另付）", "duration": "半天", "tip": "昂坪360缆车很壮观"},
            {"name": "旺角女人街", "type": "市场", "ticket": "免费", "duration": "2小时", "tip": "可以砍价"},
            {"name": "尖沙咀星光大道", "type": "地标", "ticket": "免费", "duration": "1小时", "tip": "看维港全景"},
            {"name": "黄大仙祠", "type": "寺庙", "ticket": "免费", "duration": "1小时", "tip": "求签很灵"},
            {"name": "中环石板街", "type": "历史", "ticket": "免费", "duration": "30分钟", "tip": "拍照打卡点"},
            {"name": "南丫岛", "type": "海岛", "ticket": "渡轮20港币", "duration": "半天", "tip": "徒步+海鲜"},
        ],
        "restaurants": [
            {"name": "添好运", "type": "点心", "budget": "50-100港币（约￥45-90）", "must_try": "酥皮焗叉烧包", "tip": "最便宜的米其林"},
            {"name": "兰芳园", "type": "茶餐厅", "budget": "40-80港币（约￥36-72）", "must_try": "丝袜奶茶", "tip": "中环老店"},
            {"name": "一乐烧鹅", "type": "烧腊", "budget": "60-120港币（约￥54-108）", "must_try": "烧鹅", "tip": "米其林一星"},
            {"name": "麦文记", "type": "面食", "budget": "40-80港币（约￥36-72）", "must_try": "鲜虾云吞面", "tip": "佐敦老店"},
            {"name": "龙景轩", "type": "高端粤菜", "budget": "500-1000港币（约￥450-900）", "must_try": "点心", "tip": "米其林三星，需提前预约"},
        ],
        "transport": {
            "airport_to_city": "机场快线24分钟110港币 / 巴士40港币",
            "daily_pass": "八达通卡，地铁单程4-30港币",
            "taxi": "起步价27港币",
            "tips": "八达通卡通用，地铁覆盖全城，叮叮车2.3港币最便宜",
        },
        "budget_estimate": {
            "budget": {"daily": "300-500港币（约270-450元）", "note": "经济酒店+茶餐厅+地铁"},
            "mid_range": {"daily": "800-1500港币（约720-1350元）", "note": "三星酒店+普通餐厅"},
            "luxury": {"daily": "3000+港币（约2700+元）", "note": "五星酒店+高端餐厅"},
        },
        "safety": {
            "level": "非常安全",
            "scams": "几乎没有",
            "emergency": {"police": "999", "ambulance": "999", "fire": "999"},
            "embassy": "中央人民政府驻港联络办：00852-28314333",
            "tips": "地铁内不能饮食，电梯靠右站，小费不强制",
        },
    },
}

# ── 加载知识库（JSON 优先，fallback 到内置数据）──────────────
DESTINATIONS = _get_destinations()


def get_destination_info(destination: str) -> dict | None:
    """根据目的地名称获取知识库数据"""
    for key, info in DESTINATIONS.items():
        if key in destination:
            return info
    return None


def list_destinations() -> list[str]:
    """列出所有支持的目的地"""
    return list(DESTINATIONS.keys())
