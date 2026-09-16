"""
历史记录路由 - 查询和搜索历史规划。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

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


@router.delete("/{plan_id}")
async def delete_plan(plan_id: str):
    """删除单条历史规划记录（连带其追问记录、评分与 SSE 事件）。

    路由顺序注意：本接口必须放在 GET /search 之后定义，
    否则 /api/history/search 会被 /{plan_id} 抢先匹配（plan_id="search"）。
    当前文件里 /search 在前、/{plan_id} 在后，顺序是对的。
    """
    ok = db.delete_plan(plan_id)
    if not ok:
        # 删除不存在的记录返回 404，前端据此提示「记录已不存在」并刷新列表
        raise HTTPException(status_code=404, detail="记录不存在或已被删除")
    return {"status": "ok", "deleted": plan_id}
