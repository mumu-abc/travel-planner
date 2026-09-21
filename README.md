# AI 旅行规划助手

<!-- 推送到 GitHub 后取消注释并替换 <user>/<repo>：
[![CI](https://github.com/<user>/<repo>/actions/workflows/ci.yml/badge.svg)](https://github.com/<user>/<repo>/actions/workflows/ci.yml)
-->

> 手搓 OpenAI Function Calling 多 Agent 管线 · 3 层串并行 · 可消融评测 · 混合 RAG（FAISS + Okapi BM25）· GPS 路线优化 · LP 预算 · SSE 断线重连

**这个项目在回答两个问题**：
1. **体感**：多 Agent 一跑几分钟，用户等不了——骨架优先能否把「可读行程」压进 1 秒？
2. **价值**：多 Agent 相比单 Agent，质量/延迟/成本到底差多少？值不值？

默认管线是 **single**（按消融数据定的性价比档）；同仓库提供 `multi` / `sequential` / 关反思 等消融模式，用同一套 LLM 评委对比。评测器对**离线降级、评委解析失败、Agent 全失败**的 run 自动隔离，失败 case 不计入平均分（失败明细单独列出），杜绝「0 分污染结论」。

---

## 为什么不是「又一个 travel demo」

| 常见课设问题 | 本项目做法 |
|-------------|-----------|
| 只堆 multi-agent 功能，说不清收益 | `mode=single/sequential/multi` 可切换，评测输出对比表 |
| 知识图谱用列表相邻冒充 | 邻近关系由真实 GPS + Haversine ≤2.5km 计算，并返回距离 |
| BM25 只是 TF 累加 | Okapi BM25（k1=1.5, b=0.75）+ 标准 IDF |
| 反思/工具轮次不可控 | `ENABLE_REFLECTION` / `MAX_TOOL_ROUNDS` / `MAX_WEB_SEARCH_CALLS` 可配置 |
| 评测只报最好一次 | 消融报告按配置分组，失败 case 也写入 |

---

## 架构

```
POST /api/plan/stream (SSE)
        │
        ├─ progressive=true（默认）: 先推本地骨架（知识库+路线+LP，秒级）
        │
        ▼
  RateLimiter（令牌桶 + Semaphore + 指数退避）
        │
        ▼
  mode=multi ──► Router（可关）──► L1 Researcher
                                      │
                                      ▼
                                  L2 Planner
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
                 Budget            Foodie            Safety   ← ThreadPool 并行
                    │                 │                 │
                    └─────────────────┴─────────────────┘
                                      │
                                      ▼
                         混合检索 / 路线 / 预算 / 天气 / 汇率
```

> 上图为 `multi` 深度档的数据流；产品默认 `single` 为单 Agent 覆盖同样 5 板块，工具链相同。

| 模式 | 含义 | 用途 |
|------|------|------|
| `single` | 单 Agent 覆盖 5 板块 | **产品默认**（n=3 消融：质量至少相当，快 1.6×/省 2.8×/σ=0） |
| `multi` | 路由 + 3 层串并行 | 深度档：要五板块齐全时用 |
| `sequential` | 5 Agent 严格串行 | 消融：去掉并行 |

请求体可传：`mode`、`enable_reflection`、`progressive`（默认 true）。  
前端可选「先出骨架」和执行模式。

Agent 定义在 `app/agents.py`，执行引擎在 `app/crew.py`，骨架在 `app/skeleton.py`。

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env   # 填 LLM_API_KEY

# 首次运行需构建知识库索引（FAISS + BM25，约 1 分钟）
python -m app.knowledge.ingest

python -m uvicorn app.main:app --reload --port 8000
# 打开 http://localhost:8000
```

### 骨架优先延迟基准（可复现）

```bash
python -m eval.latency_bench --n 20 --concurrency 4
```

2026-09-14 实测（本机，`skeleton_only=true` 纯本地链路，不调 LLM）：

| 指标 | P50 | P95 |
|------|-----|-----|
| 首字节 TTFB | 5ms | 16ms |
| **骨架事件（可读行程可见）** | **803ms** | **1080ms** |

→ 用户点「生成」后 **≈1 秒**看到分日行程骨架；完整 Agent 填充（3–6 分钟）在后台进行，对比「白屏等几分钟」的常见 demo。

### 工具可靠性 / 缓存 / 记忆基准（可复现，不依赖 LLM 配额）

Agent 的可靠性不能只靠「跑通一次」证明。三个脚本把已有功能变成可量化指标，全部**离线可跑**：

```bash
python -m eval.tool_bench   --cities 10 --rounds 2   # 工具成功率 + 缓存命中率 + 容错注入
python -m eval.memory_bench --top-k 3                # 记忆检索 Recall@1/3、MRR
```

**① 工具成功率**（10 城 × 4 工具 × 2 轮 = 80 次调用，2026-09-14 实测）

| 工具 | 成功/调用 | 成功率 |
|------|-----------|--------|
| `search_knowledge` | 20/20 | 100% |
| `optimize_route` | 20/20 | 100% |
| `optimize_budget` | 20/20 | 100% |
| `search_attraction_context` | 16/20 | **80%** |

整体 **95%**。最弱的一环是景点详情（部分城市缺少细分条目）——这个数字是**照实报的**，不是挑好看的说。

**② 容错链路**（故障注入，确定性验证，非碰运气）

| 状态 | 含义 | 实测 |
|------|------|------|
| `ok` | 一次成功 | 1/4 |
| `ok_retry` | 重试后成功 | 1/4 |
| `ok_fallback` | 降级到备用工具后成功 | 1/4 |
| `failed` | 原工具 + 全部 fallback 均失败 | 1/4 |

→ 4 类注入故障里系统救回 3 类：最终可用率 75%，一次成功率 25%，**容错依赖度 50%**。
**注意：这是人为注入的故障分布，用来确定性地验证容错链路是否生效并防止回归，不代表真实系统的故障率**——真实可靠性看上一节的 95% 成功率。区分「最终可用率」和「一次成功率」的价值在于：只看前者会掩盖工具本身的不稳定。埋点落在 `app/tool_metrics.py`，旁路记录，异常不影响主流程。

**③ 工具缓存收益**（同一批参数跑 2 轮）

命中率 **47.5%**，第二轮节省 **38 次真实外部调用**。TTL 分档：静态检索/路线/预算 24h，天气 30min，汇率与搜索 1h——**只缓存「成功」与「终局结论」两类**：成功率复用省时间，终局结论（知识库没这座城）是确定性的、重复问结果不变；唯独不缓存执行失败，避免把瞬时故障固化成持久故障。

**④ 记忆检索**（12 条记忆 / 8 个查询，查询措辞与记忆刻意不重合）

| 方法 | Recall@1 | Recall@3 | MRR |
|------|----------|----------|-----|
| 语义检索（FAISS + bge-small-zh） | **75%** | **87.5%** | **0.812** |
| 字面基线（字符 2-gram Jaccard） | 37.5% | 50% | 0.507 |

→ MRR 相对基线 **+60%**。这是「记忆系统到底有没有用」的直接证据：用户说「成都怎么玩」，系统能召回「我不吃辣」。8 条里有 1 条未召回（`report_memory.md` 里如实列出）。

运行时指标可实时查看：`GET /api/metrics/tools`、`GET /api/metrics/cache`。

### 评测 / 消融

> **跑之前先查配额**（1 秒出结果，不消耗有意义额度）：
> ```bash
> python -m eval.eval_runner --check-quota
> ```
> 配额没恢复时会直接退出（退出码 2），**不会产出无效报告**——因为 429 时 pipeline 会静默走离线降级，跑满一小时得到的全是 0 次 LLM 调用的样本，旧报告里「0 分却标 ok」就是这么来的。

```bash
# 快速验证（1 case × 4 变体，约 20 分钟）
python -m eval.eval_runner --cases 1 --ablation --output eval/report_ablation.md

# 正式：3 case × 4 变体（multi / single / sequential / multi-无反思）
python -m eval.eval_runner --cases 3 --ablation --output eval/report_ablation.md
```

每个变体跑完都会写入 `*.partial.md`，**中途中断不丢已完成样本**。Windows 可直接用 `.\scripts\run_ablation.ps1 -Cases 3`（会自动挑一个装了依赖的 Python，并先做配额自检）。

报告含平均分、关键词覆盖、耗时、**~Token / ~成本（字符粗估，仅横向对比）**。  
评测器内置失败判定：0 次 LLM 调用（离线降级）、评委解析失败、Agent 全失败的 case **不计入平均分**，单独列入「失败与降级明细」——宁可少一个样本，不要一个被污染的结论。  
操作细节见 `eval/消融对照说明.md`。

**2026-09-15 完整消融（3 case × 4 配置，T001 东京 / T002 巴黎 / T003 曼谷）**：

| 配置 | 样本 | 平均分 | 波动σ | 平均耗时 | ~成本 |
|------|------|--------|--------|----------|-------|
| **single + 反思** | 3/3 | **80.0** | **±0.0** | **202s** | **$0.0097** |
| multi + 反思 | 3/3 | 76.7 | ±5.8 | 324s | $0.0275 |
| multi 无反思 | 3/3 | 76.7 | ±5.8 | 226s | $0.0349 |
| sequential + 反思 | 2/2 | 75.0 | ±7.1 | 471s | $0.0418 |

**三条结论：**

1. **multi vs single 分差 −3.3，但 σ=5.8（分差小于波动）** —— 正确表述是「质量未见显著差异」，
   不能说 single 更好。但质量相当的前提下 single 快 1.6×、省 2.8×、且三次全部 80 分零波动，
   故默认档选 single。这个决策的本质是**用数据否定了自己设计的多 Agent 架构**。
2. **反思机制未观测到质量收益** —— 开关反思得分完全一致（76.7，三个 case 逐个相同），
   关掉后反而快 30%。n=3 且评分逐个重合，存在巧合可能，但足以说明「反思没带来可观测提升」。
3. **sequential 最差且最慢**（471s，是 single 的 2.3×）—— 并行的价值在延迟，不在质量。

完整报告见 `eval/report_ablation.md`（含逐 case 明细与可靠性说明）。  

### 测试

```bash
pytest -q
pytest -m slow   # 含 LLM
```

---

## 核心实现（可深挖点）

### 1. 骨架优先（用户端体感）
点生成后先用本地知识库 + 路线优化 + LP 预算拼出可读骨架（不调 LLM），SSE 事件 `skeleton` 推给前端立刻展示；Agent 在后台填美食/安全/细节，`final` 整体替换。解决「等几分钟白屏」。

### 2. 执行模式与消融
`build_travel_crew(..., mode=..., enable_reflection=...)`  
`app/agents.split_layers()` 按模式切层。评测侧 `eval/eval_runner.py --ablation` 真正调用多组配置。

### 3. 工具成本预算
`web_search` 有全局次数上限（`MAX_WEB_SEARCH_CALLS`），超限自动降级到本地 `search_knowledge`。  
意图：贵工具不是想调就调，做「成本感知路由」。

**联网搜索后端（2026-09 起）**：主后端为 **必应中国 RSS**（实测 0.4s / 中文 / 免密钥），
DuckDuckGo 与 Wikipedia 降为海外备用。原因是实测国内无代理环境下
DuckDuckGo 20.7s、Wikipedia 9.8s，`web_search` 合计 **28.3s / 0 条**，
而它是 4 个工具的兜底终点，整条降级链末端是断的。

三层保护：① 主力源排最前 ② 海外源显式超时 5s ③ **冷却短路**
（后端连续失败 2 次 → 冷却 5 分钟直接跳过，避免「后端已死但每次仍等满超时」）。
后端注册表可插拔（`BACKEND_ORDER` / `_BACKEND_FUNCS`），换后端只改一处。
详见 `docs/缺陷记录_web搜索国内不可用.md`。

### 4. Okapi BM25 + FAISS 混合
查询侧用 k1/b 饱和公式，不是建索引时写死分。融合权重语义 0.7 / 关键词 0.3。

### 5. GPS 知识图谱
`ATTRACTION_COORDS` 真实经纬度 → Haversine → 半径 2.5km 内按距离排序，输出 `nearby` + `nearby_km`。  
同类型关系保留。

### 6. 路线优化
贪心最近邻 + 2-opt；测试保证 2-opt 路径不差于贪心。

### 7. LP 预算
PuLP 在总预算与分项上下限下分配；不可用时回退比例分配。

### 8. 稳定性
- 工具失败：重试 → fallback 工具链 → 降级文案  
- LLM 全挂：本地离线攻略（知识库 + 路线 + 预算）  
- SSE：事件持久化 + `GET /api/plan/{id}/events?after_id=` 回放  
- 限流：令牌桶 10 RPS + 并发 5 + 指数退避抖动

---

## API

| 接口 | 方法 | 说明 |
|------|------|------|
| `POST /api/plan/stream` | POST | SSE：thought/action/observation/token/agent_done |
| `POST /api/plan/followup` | POST | 多轮追改 |
| `POST /api/plan/{id}/feedback` | POST | 1–5 星反馈（会注入后续 prompt） |
| `GET /api/plan/{id}/events` | GET | 断线重连回放 |
| `GET /api/history` | GET | 历史分页 |
| `GET /api/history/search` | GET | 按目的地搜索历史 |
| `DELETE /api/history/{id}` | DELETE | 删除单条历史（级联清理追问/评分/SSE 事件，不存在返回 404） |
| `GET /api/health` | GET | 健康检查 |

---

## 项目结构

```
app/
  agents.py           # Agent 定义 / 反思 prompt / 模式切层
  crew.py             # FC 引擎 + 模式执行 + 工具恢复 + 预算
  concurrency.py      # 令牌桶 + 退避
  evaluation.py       # 多评委模块
  memory.py           # 偏好 + 向量记忆
  tool_metrics.py     # 工具成功率/降级率/耗时埋点（旁路，异常不影响主流程）
  tool_cache.py       # 工具层 TTL 缓存 + 命中率（只缓存成功与终局结论）
  routers/metrics.py  # GET /api/metrics/tools|cache 实时观测
  tools/
    knowledge_search.py  # FAISS + Okapi BM25 + GPS 图谱
    route_optimizer.py   # Haversine + 贪心 + 2-opt
    budget_optimizer.py  # PuLP LP
eval/
  eval_runner.py      # 消融评测 CLI
  latency_bench.py    # 骨架首字/骨架延迟 P50/P95 压测
  tool_bench.py       # 工具成功率 + 缓存命中率 + 容错故障注入
  memory_bench.py     # 记忆检索 Recall@1/3、MRR（vs 字面基线）
tests/
  test_pipeline_and_retrieval.py  # 模式/BM25/GPS/预算行为测试
  test_tool_observability.py      # 埋点/缓存/容错/记忆指标（全 mock）
```

---

## 设计决策 Q&A

**Q: 多 Agent 比单 Agent 好在哪？**  
A: 分两个维度说，结论不一样。**质量维度**：n=3 消融里 multi 76.7(±5.8) vs single 80.0(±0.0)，分差 3.3 小于波动 5.8 —— 只能说「未见显著差异」，不能说谁更好。**工程维度**：multi 有四条 single 拿不到的东西 —— ① 并行（sequential 471s → multi 324s，同 5 Agent 省 1.45× 墙钟且质量不掉）② 上下文隔离（single 要把 5 板块 + 全部工具返回塞进 1 个 context、8 轮内跑完）③ 失败隔离（单 Agent 挂了不拖垮其余板块）④ 可独立替换（改预算只需重跑 Budget Agent，single 只能整体重跑）。代价是 2.8× token（16.7 vs 6.3 次 LLM 调用）。所以默认档选 single，multi 留作深挖档。

**Q: 既然没测出质量提升，为什么还保留 multi？**  
A: 三条。① n=3 只够说「没观测到差异」，不够说「没有差异」，样本量不足以砍掉它；② multi/sequential/single 三档可切换的抽象（动态路由、分层调度、并行执行器）本身是工程量，也是我得出这个结论的实验设施；③ 上下文隔离的收益理论上随任务变长而放大 —— 3 case 的短行程测不出来，这是下一步要补的实验（10 天多城市）。**如果 n=10 复测仍然没有质量差异，我会把 multi 正式降级为实验档。**

**Q: 为什么不全并行？**  
A: Planner 依赖 Researcher 的调研结果；Budget/Foodie/Safety 互不依赖且共享前两层上下文。`sequential` 模式就是用来量「并行省了多少墙钟时间、有没有掉质量」。

**Q: 知识图谱是真图吗？**  
A: 是地理邻近图 + 类型边，不是通用 KG。邻近用 GPS≤2.5km 计算并返回公里数；类型边来自知识库 `type` 字段。不假装是 Wikidata。

**Q: BM25 为什么自己写？**  
A: 依赖轻、中文按字 + 英文按词可解释。查询期用 Okapi 公式；和 FAISS 语义分归一化后 0.7/0.3 融合。可以做消融：hybrid on/off。

**Q: 反思为什么可关？**  
A: 每次反思最多多 2 次 LLM 调用。线上默认开，评测/演示用 `ENABLE_REFLECTION=false` 换延迟。

**Q: 工具失败怎么办？**  
A: 原工具重试 1 次 → `_TOOL_FALLBACKS` 链（如 knowledge→web）→ 告知模型「暂无数据」。全 Agent 失败走本地离线方案。

**Q: 断线怎么办？**  
A: thought/action/observation/final 落 SQLite；`after_id` 游标回放。token 事件不落库（量太大）。

**Q: 延迟多少？**  
A: 完整 multi+反思约 3–6 分钟（5 个 LLM 角色 × 多轮工具）。这是当前设计上限；优化方向是裁 Agent、关反思、并行层内流式、或改成「先骨架后填充」。

---

## 已知诚实边界

- 知识库为 28 城静态 JSON + 少量 Markdown，不是实时 OTA 数据。  
- 评委是 LLM-as-judge，有偏差；正式对比应加人工抽检。  
- Token/成本为字符粗估，不是计费账单。  
- 消融结论基于 **n=3**（sequential 为 n=2），仍属方向性结论；分差（3.3）小于波动（σ=5.8），
  严格意义上**未观察到显著差异**，因此不宣称「single 质量更好」。
- 跑批期间外网不通，web_search 全部降级到本地知识库；各配置受影响一致，横向对比有效，
  但绝对分数偏低。  
- 联网搜索后端用的是**必应中国 RSS**——属于必应公开输出格式，但是**未公开接口**，
  可能改版或限流。生产环境应改用官方 Search API（Tavily / Serper 域名可达，需 key）。
  **当前只保住了「能用」，没做到「合规」。**
- 检索相关评测数字（工具成功率等）在 B 档修复后**尚未重跑**，需要 LLM 配额。  
- 单机 SQLite，无鉴权多租户；定位是可深挖的 Agent 工程作品，不是 SaaS。  
- 工具成功率 95% **只覆盖本地工具**（知识检索/路线/预算/景点详情）；天气、汇率、网络搜索走外部 API，未计入该数字，用 `--include-network` 可单独测。  
- 缓存命中率 47.5% 来自「同参数跑 2 轮」的合成负载，真实重复查询率取决于用户行为。  
- 记忆评测用的是 12 条**构造语料**，不是真实用户数据；它证明的是检索方法的相对优势（vs 字面基线 +60% MRR），不是线上效果。  
- 容错链路的 4 类故障是**注入**的（确定性可回归），不代表生产环境的真实故障分布。
