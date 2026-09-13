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

    # ── 搜索工具 ──────────────────────────────────────────
    search_max_results: int = 5

    # ── 并发控制 ──────────────────────────────────────────
    rate_limit_rps: float = 10.0      # 每秒最大请求数
    rate_limit_concurrent: int = 5     # 最大并发请求数
    rate_limit_retries: int = 3        # 最大重试次数

    # ── Agent 执行策略 ────────────────────────────────────
    # multi | sequential | single
    # 默认 single：消融实测 multi 质量打平但慢 ~1.8×/贵 ~2.3×（见 eval/report_ablation.md），
    # 按数据选性价比档；multi 保留为「五板块齐全」深度档，前端可切。
    pipeline_mode: str = "single"
    # 自我反思会额外 1-2 次 LLM 调用；演示/评测可关
    enable_reflection: bool = True
    max_tool_rounds: int = 3
    # 并行层错峰启动间隔（秒），降低 429
    parallel_stagger_sec: float = 1.5
    # 工具成本预算：限制 web_search 等昂贵工具次数（0=不限制）
    max_web_search_calls: int = 2

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
