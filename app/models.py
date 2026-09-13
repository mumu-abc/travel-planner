"""
数据模型 - Pydantic schemas 用于请求/响应验证和结构化输出。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── 请求模型 ──────────────────────────────────────────────


class TripRequest(BaseModel):
    """旅行规划请求"""

    destination: str = Field(..., description="旅行目的地", min_length=1, max_length=100)
    days: int = Field(..., description="旅行天数", ge=1, le=30)
    budget: float = Field(..., description="总预算（美元）", ge=100, le=100000)
    interests: str = Field(
        default="美食,文化,历史", description="兴趣偏好", max_length=200
    )
    language: str = Field(default="中文", description="输出语言", max_length=20)
    # multi | sequential | single；空则用服务端默认
    mode: Optional[str] = Field(default=None, description="执行模式 multi/sequential/single")
    enable_reflection: Optional[bool] = Field(default=None, description="是否自我反思，空则用配置")
    progressive: bool = Field(default=True, description="是否先推本地骨架再填细节")
    skeleton_only: bool = Field(
        default=False,
        description="仅生成本地骨架后立即返回（骨架预览/延迟基准，不调用 LLM）",
    )


# ── 响应模型 ──────────────────────────────────────────────


class PlanStatus(str, Enum):
    """规划状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AgentProgress(BaseModel):
    """单个 Agent 的进度"""

    agent_name: str
    status: PlanStatus = PlanStatus.PENDING
    content: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class PlanResponse(BaseModel):
    """旅行规划响应"""

    status: str = "ok"
    plan_id: Optional[str] = None
    result: Optional[str] = None
    message: Optional[str] = None


class PlanRecord(BaseModel):
    """历史记录"""

    id: str
    destination: str
    days: int
    budget: float
    interests: str
    result: str
    created_at: str


class HistoryResponse(BaseModel):
    """历史记录响应"""

    total: int
    records: list[PlanRecord]


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = "ok"
    version: str = "4.0"
    agents: int = 5
    db_connected: bool = True


# ── SSE 事件类型 ──────────────────────────────────────────


class SSEEvent(BaseModel):
    """SSE 事件"""

    type: str  # status | skeleton | agent_done | final | error
    agent: Optional[str] = None
    message: Optional[str] = None
    content: Optional[str] = None
    plan_id: Optional[str] = None
    progress: Optional[int] = None  # 0-100


# ── 多轮对话模型 ──────────────────────────────────────────


class FollowUpRequest(BaseModel):
    """多轮对话追加请求"""

    plan_id: str = Field(..., description="关联的规划 ID")
    message: str = Field(..., description="用户的追加/修改需求", min_length=1, max_length=2000)
    language: str = Field(default="中文", description="输出语言")


class ConversationRecord(BaseModel):
    """单条对话记录"""

    id: str
    plan_id: str
    role: str  # user | assistant
    content: str
    created_at: str


class FollowUpResponse(BaseModel):
    """多轮对话响应"""

    status: str = "ok"
    reply: str = ""
    plan_id: str = ""
    history: list[ConversationRecord] = []


# ── 反馈模型 ──────────────────────────────────────────────


class FeedbackRequest(BaseModel):
    """用户反馈请求"""

    rating: int = Field(..., description="评分 1-5", ge=1, le=5)
    comment: str = Field(default="", description="评论", max_length=1000)


class FeedbackResponse(BaseModel):
    """反馈响应"""

    status: str = "ok"
    feedback_id: str = ""
