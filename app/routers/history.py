"""
历史记录路由 - 查询和搜索历史规划。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.database import db
from app.models import HistoryResponse, PlanRecord

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("", response_model=HistoryResponse)
async def get_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """获取历史规划记录（分页）"""
    total, records = db.get_history(limit=limit, offset=offset)
    return HistoryResponse(
        total=total,
        records=[PlanRecord(**r) for r in records],
    )


@router.get("/search")
async def search_history(
    destination: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(default=10, ge=1, le=50),
):
    """按目的地搜索历史记录"""
    records = db.search_plans(destination, limit=limit)
    return {
        "total": len(records),
        "records": [PlanRecord(**r) for r in records],
    }


@router.get("/{plan_id}")
async def get_plan(plan_id: str):
    """根据 ID 获取单个规划记录"""
    record = db.get_plan(plan_id)
    if not record:
        return {"status": "error", "message": "记录不存在"}
    return {"status": "ok", "record": PlanRecord(**record)}
