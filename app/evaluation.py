"""
评估闭环引擎 — 多评委评分 + 回归检测 + 持久化。

评测流程：
  1. run_evaluation() 遍历测试用例
  2. 每条用例调 build_travel_crew() 生成规划
  3. 3 个 LLM 评委独立打分（完整性/实用性/结构化）
  4. 取中位数，存入 SQLite
  5. 与历史基线对比，检测回归
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# ── 评测用例 ──────────────────────────────────────────────

EVAL_CASES: list[dict] = [
    {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "动漫,美食,神社"},
    {"id": "T002", "destination": "巴黎", "days": 4, "budget": 3000, "interests": "艺术,博物馆,美食"},
    {"id": "T003", "destination": "曼谷", "days": 3, "budget": 800, "interests": "寺庙,夜市,按摩"},
    {"id": "T004", "destination": "首尔", "days": 4, "budget": 1500, "interests": "K-pop,美食,购物"},
    {"id": "T005", "destination": "新加坡", "days": 3, "budget": 2000, "interests": "美食,花园,建筑"},
    {"id": "T006", "destination": "成都", "days": 3, "budget": 1000, "interests": "大熊猫,火锅,茶馆"},
    {"id": "T007", "destination": "伦敦", "days": 5, "budget": 3500, "interests": "博物馆,历史,公园"},
    {"id": "T008", "destination": "悉尼", "days": 4, "budget": 2500, "interests": "海滩,歌剧院,自然"},
    {"id": "T009", "destination": "迪拜", "days": 3, "budget": 4000, "interests": "购物,建筑,沙漠"},
    {"id": "T010", "destination": "巴厘岛", "days": 5, "budget": 1500, "interests": "海滩,寺庙,瑜伽"},
    {"id": "T011", "destination": "纽约", "days": 5, "budget": 4000, "interests": "博物馆,百老汇,美食"},
    {"id": "T012", "destination": "柏林", "days": 4, "budget": 2000, "interests": "历史,艺术,啤酒"},
    {"id": "T013", "destination": "清迈", "days": 4, "budget": 600, "interests": "寺庙,夜市,按摩"},
    {"id": "T014", "destination": "马尔代夫", "days": 4, "budget": 5000, "interests": "潜水,蜜月,海滩"},
    {"id": "T015", "destination": "香港", "days": 3, "budget": 2000, "interests": "美食,购物,维港"},
]

# ── 评委系统提示 ──────────────────────────────────────────

JUDGE_PROMPTS = {
    "completeness": (
        "你是一个旅行规划完整性评审专家。评估以下旅行规划是否覆盖了 5 个核心板块：\n"
        "1. 目的地研究（文化背景、最佳季节、交通方式）\n"
        "2. 每日行程（具体景点、时间安排、路线合理性）\n"
        "3. 预算分析（住宿、餐饮、交通、门票的详细估算）\n"
        "4. 美食推荐（具体餐厅名称、特色菜品、价格范围）\n"
        "5. 安全指南（当地安全提示、紧急联系方式、保险建议）\n\n"
        "打分标准：\n"
        "- 9-10: 5 个板块全部详尽覆盖\n"
        "- 7-8: 覆盖 4 个板块，细节较充分\n"
        "- 5-6: 覆盖 3 个板块，但细节不足\n"
        "- 3-4: 只覆盖 1-2 个板块\n"
        "- 1-2: 内容极度简略\n\n"
        "返回 JSON：{\"score\": 整数1-10, \"reason\": \"简短理由\"}"
    ),
    "practicality": (
        "你是一个旅行规划实用性评审专家。评估以下旅行规划的实际可执行性：\n"
        "1. 景点名称是否真实存在（不是泛泛的\"某博物馆\"）\n"
        "2. 价格估算是否合理（不是随意编造的数字）\n"
        "3. 交通方式是否具体（地铁线路、航班号、步行距离）\n"
        "4. 餐厅推荐是否真实可查\n"
        "5. 时间安排是否现实（不会一天安排 10 个景点）\n\n"
        "打分标准：\n"
        "- 9-10: 所有信息具体可查，价格合理，时间安排现实\n"
        "- 7-8: 大部分信息具体，少量模糊\n"
        "- 5-6: 部分信息具体，但有明显编造或不合理\n"
        "- 3-4: 多处信息模糊或不可信\n"
        "- 1-2: 几乎全是泛泛而谈\n\n"
        "返回 JSON：{\"score\": 整数1-10, \"reason\": \"简短理由\"}"
    ),
    "structure": (
        "你是一个旅行规划结构化评审专家。评估以下旅行规划的呈现质量：\n"
        "1. 是否有清晰的标题和分段\n"
        "2. 是否合理使用列表、表格等结构化元素\n"
        "3. 内容长度是否充分（至少 1500 字符）\n"
        "4. 信息层次是否清晰（总览→每日详情→实用信息）\n"
        "5. 是否易于快速浏览和查找信息\n\n"
        "打分标准：\n"
        "- 9-10: 结构完美，层次分明，使用多种格式元素\n"
        "- 7-8: 结构清晰，但可以更细致\n"
        "- 5-6: 有基本结构，但信息组织混乱\n"
        "- 3-4: 缺少结构，大段文字堆砌\n"
        "- 1-2: 无结构可言\n\n"
        "返回 JSON：{\"score\": 整数1-10, \"reason\": \"简短理由\"}"
    ),
}


# ── 评委打分 ──────────────────────────────────────────────


def _get_client() -> OpenAI:
    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=60,
    )


def judge_single(
    result: str,
    case: dict,
    judge_type: str,
    client: Optional[OpenAI] = None,
) -> dict:
    """
    单个评委打分。

    Args:
        result: 生成的旅行规划文本
        case: 评测用例 {"id", "destination", "days", "budget", "interests"}
        judge_type: completeness / practicality / structure
        client: OpenAI 客户端（可选）

    Returns:
        {"judge_type": str, "score": int, "reason": str}
    """
    client = client or _get_client()
    prompt = JUDGE_PROMPTS.get(judge_type, JUDGE_PROMPTS["completeness"])

    case_info = (
        f"目的地：{case['destination']}\n"
        f"天数：{case['days']} 天\n"
        f"预算：${case['budget']}\n"
        f"兴趣：{case['interests']}"
    )

    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"旅行需求：\n{case_info}\n\n生成的规划：\n{result[:4000]}"},
            ],
            temperature=0.1,
            max_tokens=200,
        )
        text = resp.choices[0].message.content or ""

        # 解析 JSON
        match = re.search(r'\{[^}]+\}', text)
        if match:
            data = json.loads(match.group())
            score = int(data.get("score", 5))
            reason = data.get("reason", "")
        else:
            score = 5
            reason = "评分解析失败"

        score = max(1, min(10, score))
        return {"judge_type": judge_type, "score": score, "reason": reason}

    except Exception as e:
        logger.warning(f"评委 {judge_type} 评分失败: {e}")
        return {"judge_type": judge_type, "score": 5, "reason": f"评分异常: {str(e)}"}


def multi_judge_evaluate(
    result: str,
    case: dict,
    client: Optional[OpenAI] = None,
) -> dict:
    """
    3 个评委并行打分，取中位数。

    Returns:
        {
            "overall": int (0-100),
            "completeness": int (0-100),
            "practicality": int (0-100),
            "structure": int (0-100),
            "judge_results": [dict, dict, dict],
        }
    """
    client = client or _get_client()

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(judge_single, result, case, jtype, client): jtype
            for jtype in JUDGE_PROMPTS
        }
        results = {}
        for future in futures:
            jtype = futures[future]
            try:
                results[jtype] = future.result()
            except Exception as e:
                results[jtype] = {"judge_type": jtype, "score": 5, "reason": str(e)}

    scores = [r["score"] for r in results.values()]
    median_score = sorted(scores)[1]  # 3 个数的中位数

    return {
        "overall": median_score * 10,
        "completeness": results["completeness"]["score"] * 10,
        "practicality": results["practicality"]["score"] * 10,
        "structure": results["structure"]["score"] * 10,
        "judge_results": list(results.values()),
    }


def calculate_judge_agreement(judge_results: list[dict]) -> float:
    """
    计算评委一致性（简化版：评分差距越小一致性越高）。

    Returns:
        0.0 ~ 1.0，1.0 表示完全一致
    """
    if len(judge_results) < 2:
        return 1.0

    scores = [r["score"] for r in judge_results]
    max_diff = max(scores) - min(scores)

    # 最大差距 0 → 1.0, 差距 1 → 0.9, 差距 2 → 0.8, ...
    agreement = max(0.0, 1.0 - max_diff * 0.1)
    return round(agreement, 2)


# ── 批量评测 ──────────────────────────────────────────────


def run_evaluation(
    cases: Optional[list[dict]] = None,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
    run_name: str = "",
) -> dict:
    """
    批量评测入口。

    Args:
        cases: 评测用例列表，默认使用 EVAL_CASES
        progress_callback: 进度回调 (case_id, completed, total)
        run_name: 评测轮次名称

    Returns:
        {
            "run_id": str,
            "run_name": str,
            "timestamp": str,
            "cases": [单条结果...],
            "summary": {汇总统计},
        }
    """
    from app.crew import build_travel_crew

    cases = cases or EVAL_CASES
    run_id = f"eval_{uuid.uuid4().hex[:8]}"
    client = _get_client()
    case_results = []
    total = len(cases)

    logger.info(f"📊 开始评测 [{run_name or run_id}]: {total} 条用例")

    for idx, case in enumerate(cases):
        case_id = case["id"]
        logger.info(f"  [{idx+1}/{total}] {case_id}: {case['destination']}")

        try:
            # 生成规划
            plan = build_travel_crew(
                destination=case["destination"],
                days=case["days"],
                budget=case["budget"],
                interests=case["interests"],
            )

            # 评委打分
            eval_result = multi_judge_evaluate(plan, case, client)

            case_result = {
                "case_id": case_id,
                "destination": case["destination"],
                "days": case["days"],
                "budget": case["budget"],
                "interests": case["interests"],
                "scores": {
                    "overall": eval_result["overall"],
                    "completeness": eval_result["completeness"],
                    "practicality": eval_result["practicality"],
                    "structure": eval_result["structure"],
                },
                "judge_results": eval_result["judge_results"],
                "plan_length": len(plan),
                "agreement": calculate_judge_agreement(eval_result["judge_results"]),
            }

        except Exception as e:
            logger.error(f"  ❌ {case_id} 评测失败: {e}")
            case_result = {
                "case_id": case_id,
                "destination": case["destination"],
                "days": case["days"],
                "budget": case["budget"],
                "interests": case["interests"],
                "scores": {"overall": 0, "completeness": 0, "practicality": 0, "structure": 0},
                "judge_results": [],
                "plan_length": 0,
                "agreement": 0,
                "error": str(e),
            }

        case_results.append(case_result)

        if progress_callback:
            progress_callback(case_id, idx + 1, total)

    # 汇总统计
    valid = [c for c in case_results if c["scores"]["overall"] > 0]
    n_valid = len(valid)

    summary = {
        "total_cases": total,
        "evaluated": n_valid,
        "failed": total - n_valid,
        "pass_rate": round(sum(1 for c in valid if c["scores"]["overall"] >= 50) / max(n_valid, 1), 2),
        "avg_overall": round(sum(c["scores"]["overall"] for c in valid) / max(n_valid, 1), 1),
        "avg_completeness": round(sum(c["scores"]["completeness"] for c in valid) / max(n_valid, 1), 1),
        "avg_practicality": round(sum(c["scores"]["practicality"] for c in valid) / max(n_valid, 1), 1),
        "avg_structure": round(sum(c["scores"]["structure"] for c in valid) / max(n_valid, 1), 1),
        "min_overall": min((c["scores"]["overall"] for c in valid), default=0),
        "max_overall": max((c["scores"]["overall"] for c in valid), default=0),
        "avg_agreement": round(sum(c.get("agreement", 0) for c in valid) / max(n_valid, 1), 2),
    }

    result = {
        "run_id": run_id,
        "run_name": run_name or f"评测 {datetime.now().strftime('%m-%d %H:%M')}",
        "timestamp": datetime.now().isoformat(),
        "cases": case_results,
        "summary": summary,
    }

    logger.info(
        f"📊 评测完成: 平均分 {summary['avg_overall']}, "
        f"通过率 {summary['pass_rate']*100:.0f}%, "
        f"评委一致性 {summary['avg_agreement']}"
    )

    return result


# ── 回归检测 ──────────────────────────────────────────────


def detect_regressions(
    current: dict,
    baseline: Optional[dict] = None,
    threshold: float = 5.0,
) -> dict:
    """
    对比当前评测与基线，检测回归。

    Args:
        current: 当前评测结果
        baseline: 基线评测结果（None 则自动获取最近一次）
        threshold: 分数下降阈值（超过则标记为回归）

    Returns:
        {
            "has_regression": bool,
            "regressions": [{"case_id", "dimension", "current", "baseline", "drop"}],
            "improvements": [{"case_id", "dimension", "current", "baseline", "gain"}],
            "summary_delta": {"overall": +3.2, "completeness": -1.5, ...},
        }
    """
    if baseline is None:
        try:
            from app.database import db
            latest = db.get_latest_eval_run()
            if latest:
                baseline = db.get_eval_run(latest["id"])
        except Exception:
            pass

    if not baseline:
        return {
            "has_regression": False,
            "regressions": [],
            "improvements": [],
            "summary_delta": {},
            "message": "无基线数据，跳过回归检测",
        }

    regressions = []
    improvements = []

    # 用例级别对比
    baseline_cases = {c["case_id"]: c for c in baseline.get("cases", [])}

    for case in current.get("cases", []):
        cid = case["case_id"]
        if cid not in baseline_cases:
            continue

        base_case = baseline_cases[cid]
        for dim in ("overall", "completeness", "practicality", "structure"):
            curr_score = case["scores"].get(dim, 0)
            base_score = base_case["scores"].get(dim, 0)
            diff = curr_score - base_score

            if diff < -threshold:
                regressions.append({
                    "case_id": cid,
                    "destination": case["destination"],
                    "dimension": dim,
                    "current": curr_score,
                    "baseline": base_score,
                    "drop": abs(diff),
                })
            elif diff > threshold:
                improvements.append({
                    "case_id": cid,
                    "destination": case["destination"],
                    "dimension": dim,
                    "current": curr_score,
                    "baseline": base_score,
                    "gain": diff,
                })

    # 汇总级别对比
    summary_delta = {}
    curr_summary = current.get("summary", {})
    base_summary = baseline.get("summary", {})
    for key in ("avg_overall", "avg_completeness", "avg_practicality", "avg_structure", "pass_rate"):
        if key in curr_summary and key in base_summary:
            summary_delta[key] = round(curr_summary[key] - base_summary[key], 2)

    return {
        "has_regression": len(regressions) > 0,
        "regressions": regressions,
        "improvements": improvements,
        "summary_delta": summary_delta,
    }
