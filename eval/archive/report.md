# 评测报告

- **时间**: 2026-08-20 19:20
- **模型**: mimo-v2.5-pro
- **用例数**: 3（成功 3）
- **平均分**: 80.0/100
- **关键词覆盖率**: 86.7%
- **平均输出长度**: 7695 字符
- **平均耗时**: 365.8s

## 详细结果

| # | Case | 目的地 | 完整性 | 实用性 | 结构化 | 中位分 | 关键词 | 耗时 |
|---|------|--------|--------|--------|--------|--------|--------|------|
| 1 | 东京短途 | 东京, 日本 | 8 | 8 | 9 | **80** | 80.0% | 336.9s |
| 2 | 巴黎长途 | 巴黎, 法国 | 8 | 8 | 9 | **80** | 100.0% | 393.9s |
| 3 | 曼谷低预算 | 曼谷, 泰国 | 8 | 5 | 9 | **80** | 80.0% | 366.7s |

## 关键词覆盖统计

- **has_weather**: 3/3 (100%)
- **has_attractions**: 3/3 (100%)
- **has_budget**: 3/3 (100%)
- **has_food**: 3/3 (100%)
- **has_safety**: 1/3 (33%)

## 评委详情

### T001 东京短途
- **completeness**: 8/10 — 计划全面覆盖景点、天气、行程、预算和美食，但缺少明确的安全建议。
- **practicality**: 8/10 — 计划详细实用，景点和餐厅选择经典，交通建议具体可行，价格预算合理且灵活。
- **structure**: 9/10 — 标题使用表情符号和关键词清晰命名，分段逻辑合理从总览到细节，列表格式整齐便于扫描，内容长度超过1500字且信息丰富，可读性高通过多样格式增强。
- **关键词覆盖**: {'has_weather': True, 'has_attractions': True, 'has_budget': True, 'has_food': True, 'has_safety': False}

### T002 巴黎长途
- **completeness**: 8/10 — 行程规划详细合理，景点和美食推荐全面，但天气预报仅覆盖前三天，安全提示较简略，预算信息分散无汇总。
- **practicality**: 8/10 — 行程规划详细全面，景点多样、餐厅推荐具体、交通路线清晰、价格透明，实用性高，但部分餐厅需预约或价格偏高。
- **structure**: 9/10 — 计划结构层次清晰，标题和子标题明确划分主题；分段合理，包括行前准备、天气预报和每日行程；列表使用得当，便于信息读取；长度超过1500字，内容详实；可读性高，通过符号、表格和重点提示增强了可理解性。
- **关键词覆盖**: {'has_weather': True, 'has_attractions': True, 'has_budget': True, 'has_food': True, 'has_safety': True}

### T003 曼谷低预算
- **completeness**: 8/10 — The plan comprehensively covers attractions, weather, daily itinerary, and food recommendations with detailed schedules. However, budget estimates appear underestimated (e.g., ticket costs exceed allocated amount), and safety precautions are not addressed.
- **practicality**: 5/10 — 解析失败: 
- **structure**: 9/10 — 旅行计划结构优秀，标题分段清晰，大量使用列表、表格和项目符号增强可读性，长度超过1500字，但结尾部分稍有不完整。
- **关键词覆盖**: {'has_weather': True, 'has_attractions': True, 'has_budget': True, 'has_food': True, 'has_safety': False}
