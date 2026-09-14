"""
旅行规划智能体 - FastAPI 主入口
多 Agent Function Calling + 3层串并行 + SSE + SQLite
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.config import settings
from app.database import db
from app.models import HealthResponse
from app.tool_cache import configure
from app.routers import history, metrics, plan

# ── 日志配置 ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── 生命周期管理 ──────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期"""
    logger.info("✈️ 旅行规划助手启动中...")
    masked_key = settings.llm_api_key[:4] + "****" + settings.llm_api_key[-4:] if len(settings.llm_api_key) > 8 else "****"
    logger.info(f"   LLM: {settings.llm_model} @ {settings.llm_base_url} (key={masked_key})")
    logger.info(f"   pipeline_mode: {settings.pipeline_mode}, reflection: {settings.enable_reflection}")
    logger.info(f"   数据库: {settings.travel_db_path}")
    logger.info(f"   端口: {settings.port}")
    _cache = configure(
        enabled=settings.enable_tool_cache,
        max_size=settings.tool_cache_max_size,
    )
    logger.info(f"   工具缓存: {'开启' if _cache.enabled else '关闭'} (容量 {_cache.max_size})")
    yield
    db.close()
    logger.info("👋 旅行规划助手已关闭")


# ── FastAPI 应用 ──────────────────────────────────────────
app = FastAPI(
    title="AI 旅行规划助手",
    description="多 Agent Function Calling 协作，支持 multi/sequential/single 消融模式",
    version="4.0",
    lifespan=lifespan,
)

# ── 注册路由 ──────────────────────────────────────────────
app.include_router(plan.router)
app.include_router(history.router)
app.include_router(metrics.router)


# ── 首页 ──────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    frontend_path = Path(__file__).parent.parent / "frontend" / "index.html"
    return frontend_path.read_text(encoding="utf-8")


# ── 健康检查 ──────────────────────────────────────────────
@app.get("/api/health", response_model=HealthResponse)
async def health():
    """健康检查（真正检测数据库连接）"""
    db_ok = False
    try:
        conn = db._get_conn()
        conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version="4.0",
        agents=5,
        db_connected=db_ok,
    )


# ── 直接运行 ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
