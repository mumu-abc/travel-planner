"""
配置管理 - 使用 pydantic-settings 统一管理所有配置项。
支持 .env 文件和环境变量两种方式。
"""

import os
from pathlib import Path

from pydantic_settings import BaseSettings

# 强制 HuggingFace 离线模式，避免下载模型时超时
os.environ.setdefault("HF_HUB_OFFLINE", "1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用配置"""

    # ── LLM 配置 ──────────────────────────────────────────
    llm_api_key: str = ""
    llm_base_url: str = "https://token-plan-cn.xiaomimimo.com/v1"
    llm_model: str = "mimo-v2.5-pro"
    llm_temperature: float = 0.7
    llm_max_retries: int = 3
    llm_timeout: int = 120
    # 单次生成的最大 token 数 —— 这是「安全天花板」，不是目标值。
    # 本项目用的 mimo-v2.5-pro 是思维链模型，它会先输出一段 reasoning_content
    # 再写正文；4096 会被思维链吃满导致正文为空（实测 finish_reason=length /
    # content 长度 0），所以天花板给得很宽松，真正的长度约束交给
    # max_output_chars（见下），它算出的额度一定小于这个天花板才生效。
    llm_max_tokens: int = 12288
    # 调工具阶段用的额度：这一阶段几乎不需要长正文，只要一个函数名 + 参数，
    # 给 12288 纯属浪费（模型会顺手多写思考）。压到 2048 能显著降低这一轮的延迟。
    llm_max_tokens_tool: int = 2048
    # 最终正文的目标字数上限。实测思维链模型会无视 prompt 里的「1800~2800 字」
    # 写到 6000+ 字，而生成时间与字数近乎线性（163s/6200字 ≈ 时间全花在这），
    # 所以这里按字数反推一个 token 硬上限，让它在物理层面写不了那么长。
    # 3200 字 × 2.0 token/字 + 2500 思考预留 = 8900 tokens，低于天花板 12288，
    # 因此这个上限真正生效（写超会被 finish_reason=length 截断）。0 = 不限制。
    max_output_chars: int = 3200

    # ── 搜索工具 ──────────────────────────────────────────
    search_max_results: int = 5

    # 联网搜索的官方 API 密钥（可选，留空则退回免密钥后端）。
    # 实测（2026-09 无代理国内网络）这些域名全部可达且 0.2~0.3s 响应：
    #   智谱   open.bigmodel.cn   ¥0.01/次
    #   百度   qianfan.baidubce.com  1500 次/月免费
    #   博查   api.bochaai.com  面向 AI 的搜索
    # 任何一个填了就会自动排到必应 RSS 前面（官方接口有 SLA，比未公开接口可靠）。
    # provider 留空 = 自动按「谁有 key 用谁」的顺序挑。
    search_api_provider: str = ""          # zhipu | baidu | bocha | ""（自动）
    zhipu_api_key: str = ""
    baidu_search_api_key: str = ""
    bocha_api_key: str = ""
    # 官方 API 调用超时（秒）
    search_api_timeout: int = 10

    # ── 并发控制 ──────────────────────────────────────────
    rate_limit_rps: float = 10.0      # 每秒最大请求数
    rate_limit_concurrent: int = 5     # 最大并发请求数
    rate_limit_retries: int = 3        # 最大重试次数

    # ── Agent 执行策略 ────────────────────────────────────
    # multi | sequential | single
    # 默认 single：消融实测 multi 质量未见显著差异但慢 ~1.6×/贵 ~2.8×（见 eval/report_ablation.md），
    # 按数据选性价比档；multi 保留为「五板块齐全」深度档，前端可切。
    pipeline_mode: str = "single"
    # 自我反思会额外 1-2 次 LLM 调用；演示/评测可关
    enable_reflection: bool = True
    max_tool_rounds: int = 3
    # 并行层错峰启动间隔（秒），降低 429
    parallel_stagger_sec: float = 1.5
    # 工具成本预算：限制 web_search 等昂贵工具次数（0=不限制）
    max_web_search_calls: int = 2

    # ── 可观测性与缓存 ────────────────────────────────────
    # 工具层 TTL 缓存：同一目的地重复规划时复用静态检索/确定性计算结果。
    # 实时工具（天气/汇率）TTL 仅 30/60 分钟，不会拿旧数据糊弄用户。
    enable_tool_cache: bool = True
    tool_cache_max_size: int = 512

    # ── 服务器配置 ────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # ── 数据库（使用 travel_db_path 避免与系统 DB_PATH 冲突）─────────────────────────────────────────────
    travel_db_path: str = str(PROJECT_ROOT / "data" / "travel.db")

    # ── LangSmith 可观测性 ────────────────────────────────
    langsmith_api_key: str = ""
    langsmith_project: str = "travel-planner"
    langsmith_tracing: bool = False

    # ── 历史记录 ──────────────────────────────────────────
    history_max_days: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
