"""
旅行规划路由 - 同步 + SSE 流式输出。
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from openai import OpenAI

from app.config import settings
from app.crew import build_travel_crew, AGENTS, SINGLE_AGENT
from app.database import db
from app.skeleton import build_skeleton, skeleton_meta
from typing import Optional

from app.models import (
    ConversationRecord,
    FeedbackRequest,
    FeedbackResponse,
    FollowUpRequest,
    FollowUpResponse,
    PlanResponse,
    TripRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/plan", tags=["plan"])


def _learn_preferences(req: TripRequest):
    """从用户请求中提取偏好并存储"""
    try:
        # 记录最近目的地
        db.set_preference("last_destination", req.destination)
        # 记录预算区间
        if req.budget < 500:
            db.set_preference("budget_tier", "经济")
        elif req.budget < 2000:
            db.set_preference("budget_tier", "舒适")
        else:
            db.set_preference("budget_tier", "豪华")
        # 记录兴趣关键词
        db.set_preference("interests", req.interests)
    except Exception as e:
        logger.debug(f"偏好记录失败: {e}")

_executor = ThreadPoolExecutor(max_workers=4)


@router.post("", response_model=PlanResponse)
async def plan_trip(req: TripRequest):
    """同步生成旅行计划"""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: build_travel_crew(
                req.destination, req.days, req.budget, req.interests, req.language,
                mode=req.mode,
                enable_reflection=req.enable_reflection,
            ),
        )
        plan_id = db.save_plan(req.destination, req.days, req.budget, req.interests, result)
        _learn_preferences(req)
        return PlanResponse(status="ok", plan_id=plan_id, result=result)
    except Exception as e:
        logger.error(f"生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成失败: {str(e)}")


def _agent_label(name: str) -> str:
    for a in AGENTS:
        if a["name"] == name:
            return a["label"]
    if name == SINGLE_AGENT["name"]:
        return SINGLE_AGENT["label"]
    return name


@router.post("/stream")
async def plan_trip_stream(req: TripRequest):
    """SSE：progressive 时先推本地骨架，再多 Agent 填充；支持断线重连。"""

    def run_crew(q: queue.Queue):
        plan_id = db.create_plan_pending(req.destination, req.days, req.budget, req.interests)
        try:
            # ── 骨架优先：不调 LLM，秒级可读 ──
            if req.progressive:
                try:
                    skel = build_skeleton(req.destination, req.days, req.budget, req.interests)
                    meta = skeleton_meta(req.destination, req.days, req.budget)
                    skel_event = {
                        "type": "skeleton",
                        "content": skel,
                        "message": "📋 已生成本地骨架，AI 正在填充细节…",
                        "progress": 15,
                        "meta": meta,
                    }
                    q.put(skel_event)
                    db.save_sse_event(plan_id, "skeleton", skel_event)
                except Exception as e:
                    logger.warning(f"骨架生成失败（继续完整流程）: {e}")

            # ── 骨架预览模式：只要骨架，不启动 Agent（不调 LLM）──
            if req.skeleton_only:
                db.update_plan_result(plan_id, "(skeleton_only 预览，未生成完整计划)")
                q.put(None)
                return

            def on_step(agent_name: str, content: str, progress: int):
                event = {
                    "type": "agent_done",
                    "agent": agent_name,
                    "message": f"✅ {_agent_label(agent_name)} 完成",
                    "content": content,
                    "progress": progress,
                }
                q.put(event)
                db.save_sse_event(plan_id, "agent_done", event)

            def on_token(agent_name: str, token: str):
                event = {
                    "type": "token",
                    "agent": agent_name,
                    "token": token,
                }
                q.put(event)

            def on_event(event: dict):
                q.put(event)
                if event.get("type") in ("thought", "action", "observation"):
                    db.save_sse_event(plan_id, event["type"], event)

            result = build_travel_crew(
                req.destination, req.days, req.budget, req.interests, req.language,
                step_callback=on_step,
                token_callback=on_token,
                event_callback=on_event,
                mode=req.mode,
                enable_reflection=req.enable_reflection,
            )

            db.update_plan_result(plan_id, result)
            _learn_preferences(req)

            final_event = {
                "type": "final",
                "plan_id": plan_id,
                "content": result,
                "progress": 100,
            }
            q.put(final_event)
            db.save_sse_event(plan_id, "final", final_event)

        except Exception as e:
            logger.error(f"Crew 执行失败: {e}", exc_info=True)
            db.update_plan_result(plan_id, f"[生成失败] {str(e)}")
            q.put({"type": "error", "message": f"生成失败: {str(e)}", "plan_id": plan_id})

        q.put(None)

    def event_generator():
        q: queue.Queue = queue.Queue()
        thread = threading.Thread(target=run_crew, args=(q,), daemon=True)
        thread.start()

        mode_hint = req.mode or settings.pipeline_mode
        if req.skeleton_only:
            start_msg = "📋 骨架预览模式（本地生成，不调用 AI）…"
        elif req.progressive:
            start_msg = "📋 先生成本地骨架，再启动 AI Agent…"
        else:
            start_msg = "🚀 正在启动 AI Agent…"
        yield f"data: {json.dumps({'type': 'status', 'message': start_msg, 'progress': 5, 'mode': mode_hint}, ensure_ascii=False)}\n\n"

        while True:
            try:
                event = q.get(timeout=300)
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'error', 'message': '生成超时，请重试'}, ensure_ascii=False)}\n\n"
                break
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{plan_id}/events")
async def get_plan_events(plan_id: str, after_id: int = 0):
    """获取已持久化的 SSE 事件（用于断线重连）"""
    try:
        events = db.get_sse_events(plan_id, after_id=after_id)
        return {"status": "ok", "events": events, "count": len(events)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── 多轮对话端点 ──────────────────────────────────────────


def _summarize_history(history: list[dict], client: OpenAI) -> Optional[list[dict]]:
    """当对话历史过长时，用 LLM 摘要早期对话，避免 context 爆炸。"""
    SUMMARY_THRESHOLD = 10

    if len(history) <= SUMMARY_THRESHOLD:
        return None

    # 取前 N-4 条做摘要，保留最近 4 条原文
    to_summarize = history[:-4]
    recent = history[-4:]

    dialogue_text = "\n".join(
        f"{r['role']}: {r['content'][:300]}" for r in to_summarize
    )

    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": "请将以下对话摘要为关键要点（200字以内），保留用户提出的主要需求和助手的修改决定。"},
                {"role": "user", "content": dialogue_text},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        summary = resp.choices[0].message.content or ""
        logger.info(f"对话历史摘要: {len(to_summarize)} 条 → {len(summary)} 字")

        # 返回摘要 + 最近对话
        summarized_history = [
            {"role": "assistant", "content": f"[早期对话摘要] {summary}"}
        ] + recent
        return summarized_history
    except Exception as e:
        logger.warning(f"摘要失败，使用截断: {e}")
        return recent


def _build_followup_messages(
    plan_id: str, user_message: str, history: list[dict]
) -> list[dict]:
    """构建多轮对话的完整消息列表（含上下文摘要）"""
    plan = db.get_plan(plan_id)
    if not plan:
        raise ValueError("规划记录不存在")
    if not plan.get("result"):
        raise ValueError("规划记录尚未完成，请等待生成结束后再追加")

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    # 历史过长时做摘要
    effective_history = history
    summary_note = ""
    if len(history) > 10:
        summarized = _summarize_history(history, client)
        if summarized:
            effective_history = summarized
            summary_note = "（早期对话已自动摘要）"

    system_msg = {
        "role": "system",
        "content": (
            "你是一位专业的旅行规划助手。用户之前已经生成了一份旅行计划，"
            "现在用户想要对计划进行修改或追问。请基于原始计划内容，"
            "结合用户的追加需求，给出详细的修改建议或回答。\n\n"
            "回复要求：\n"
            "- 保持与原始计划一致的格式风格（Markdown）\n"
            "- 只修改用户提到的部分，未提到的保持不变\n"
            "- 如果用户的需求需要调整预算或行程，给出具体建议\n"
            "- 使用中文回复（除非用户用其他语言提问）"
        ),
    }

    context_msg = {
        "role": "user",
        "content": f"以下是我之前的旅行计划（目的地：{plan['destination']}，{plan['days']}天，预算${plan['budget']}）{summary_note}：\n\n{plan['result']}",
    }

    messages = [system_msg, context_msg]
    for record in effective_history:
        messages.append({"role": record["role"], "content": record["content"]})

    messages.append({"role": "user", "content": user_message})

    return messages


@router.post("/followup", response_model=FollowUpResponse)
async def plan_followup(req: FollowUpRequest):
    """多轮对话 - 基于已有计划进行追加/修改"""
    try:
        # 保存用户消息
        db.save_conversation(req.plan_id, "user", req.message)

        # 获取对话历史
        history = db.get_conversation_history(req.plan_id)

        # 构建 LLM 消息
        messages = _build_followup_messages(req.plan_id, req.message, history)

        # 调用 LLM（同步，在线程池执行）
        def call_llm():
            client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
            resp = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                max_tokens=4096,
                temperature=0.7,
            )
            return resp.choices[0].message.content

        loop = asyncio.get_event_loop()
        reply = await loop.run_in_executor(_executor, call_llm)

        # 保存助手回复
        db.save_conversation(req.plan_id, "assistant", reply)

        # 返回完整历史
        updated_history = db.get_conversation_history(req.plan_id)
        history_records = [
            ConversationRecord(**r) for r in updated_history
        ]

        return FollowUpResponse(
            status="ok",
            reply=reply,
            plan_id=req.plan_id,
            history=history_records,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"多轮对话失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"对话失败: {str(e)}")


@router.get("/conversation/{plan_id}")
async def get_conversation(plan_id: str):
    """获取指定规划的对话历史"""
    history = db.get_conversation_history(plan_id)
    return {
        "status": "ok",
        "plan_id": plan_id,
        "history": [ConversationRecord(**r) for r in history],
    }


# ── 反馈端点 ──────────────────────────────────────────────


@router.get("/route/{destination}")
async def get_route(destination: str, days: int = Query(default=3, ge=1, le=30)):
    """获取目的地的优化路线数据（含 GPS 坐标），用于地图可视化"""
    try:
        from app.tools.route_optimizer import optimize_route_from_knowledge
        result = optimize_route_from_knowledge(destination, days)
        return {"status": "ok", "route": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/feedback/stats")
async def feedback_stats():
    """获取各目的地的反馈统计（必须在 /{plan_id}/feedback 之前注册，避免路径冲突）"""
    try:
        stats = db.get_feedback_stats()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/{plan_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(plan_id: str, req: FeedbackRequest):
    """提交用户反馈（评分 + 评论）"""
    plan = db.get_plan(plan_id)
    if not plan:
        return FeedbackResponse(status="error", feedback_id="")
    feedback_id = db.save_feedback(plan_id, req.rating, req.comment)
    return FeedbackResponse(status="ok", feedback_id=feedback_id)


@router.get("/{plan_id}/feedback")
async def get_feedback(plan_id: str):
    """获取指定规划的反馈"""
    records = db.get_feedback(plan_id)
    return {"status": "ok", "plan_id": plan_id, "feedback": records}


# ── 记忆管理 API ──────────────────────────────────────────

@router.get("/memories")
async def list_memories(
    memory_type: Optional[str] = Query(default=None, description="记忆类型: fact/preference/interaction"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """获取所有记忆"""
    try:
        from app.memory import get_all_memories
        memories = get_all_memories(memory_type=memory_type, limit=limit)
        return {"status": "ok", "count": len(memories), "memories": memories}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/memories/search")
async def search_memories(
    q: str = Query(..., min_length=1, description="搜索查询"),
    top_k: int = Query(default=5, ge=1, le=20),
    memory_type: Optional[str] = Query(default=None),
    destination: Optional[str] = Query(default=None),
):
    """语义检索记忆"""
    try:
        from app.memory import retrieve_memories
        results = retrieve_memories(
            query=q, top_k=top_k, memory_type=memory_type, destination=destination,
        )
        return {"status": "ok", "count": len(results), "memories": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/memories")
async def add_memory_manual(
    content: str = Query(..., min_length=1),
    memory_type: str = Query(default="fact"),
    destination: str = Query(default=""),
    tags: str = Query(default=""),
):
    """手动添加记忆"""
    try:
        from app.memory import add_memory
        memory_id = add_memory(
            content=content, memory_type=memory_type, destination=destination, tags=tags,
        )
        return {"status": "ok", "memory_id": memory_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str):
    """删除记忆"""
    try:
        from app.memory import delete_memory as mem_delete
        deleted = mem_delete(memory_id)
        if deleted:
            return {"status": "ok", "message": "已删除"}
        return {"status": "error", "message": "记忆不存在"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── 评测 API ──────────────────────────────────────────────

@router.post("/eval/run")
async def run_eval(
    run_name: str = Query(default="", description="评测轮次名称"),
    case_ids: Optional[str] = Query(default=None, description="指定用例ID，逗号分隔"),
):
    """
    触发一轮评测。同步执行（耗时较长，适合小规模测试）。

    用例数量可通过 case_ids 控制，如 "T001,T002" 只跑 2 条。
    """
    try:
        from app.evaluation import run_evaluation, EVAL_CASES, detect_regressions

        cases = EVAL_CASES
        if case_ids:
            ids = [c.strip() for c in case_ids.split(",")]
            cases = [c for c in EVAL_CASES if c["id"] in ids]
            if not cases:
                return {"status": "error", "message": f"未找到指定用例: {case_ids}"}

        result = run_evaluation(cases=cases, run_name=run_name)

        # 持久化
        db.save_eval_run(result)

        # 回归检测
        regression = detect_regressions(result)
        result["regression"] = regression

        return {"status": "ok", "evaluation": result}

    except Exception as e:
        logger.error(f"评测失败: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


@router.get("/eval/runs")
async def list_eval_runs(limit: int = Query(default=20, ge=1, le=100)):
    """获取历史评测列表"""
    try:
        runs = db.get_eval_runs(limit=limit)
        return {"status": "ok", "count": len(runs), "runs": runs}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/eval/run/{run_id}")
async def get_eval_run(run_id: str):
    """获取单次评测详情"""
    try:
        run = db.get_eval_run(run_id)
        if not run:
            return {"status": "error", "message": "评测记录不存在"}
        return {"status": "ok", "evaluation": run}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/eval/latest")
async def get_latest_eval():
    """获取最近一次评测"""
    try:
        latest = db.get_latest_eval_run()
        if not latest:
            return {"status": "ok", "evaluation": None, "message": "暂无评测记录"}
        run = db.get_eval_run(latest["id"])
        return {"status": "ok", "evaluation": run}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/eval/compare")
async def compare_evals(
    a: str = Query(..., description="基线评测 ID"),
    b: str = Query(..., description="对比评测 ID"),
):
    """对比两次评测结果"""
    try:
        from app.evaluation import detect_regressions

        run_a = db.get_eval_run(a)
        run_b = db.get_eval_run(b)
        if not run_a:
            return {"status": "error", "message": f"评测 {a} 不存在"}
        if not run_b:
            return {"status": "error", "message": f"评测 {b} 不存在"}

        comparison = detect_regressions(run_b, run_a)
        return {
            "status": "ok",
            "baseline": {"id": a, "name": run_a.get("run_name", ""), "summary": run_a.get("summary", {})},
            "current": {"id": b, "name": run_b.get("run_name", ""), "summary": run_b.get("summary", {})},
            "comparison": comparison,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
