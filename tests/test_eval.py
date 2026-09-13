"""
评测框架 — 多评委交叉评分 + 5 个维度。
用 3 个不同角度的 LLM 评委独立打分，取中位数，提高评分可靠性。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

import pytest
from openai import OpenAI

from app.config import settings

# ── 评测用例 ──────────────────────────────────────────────


EVAL_CASES = [
    {"id": "T001", "name": "东京短途", "destination": "东京, 日本", "days": 3, "budget": 1500, "interests": "美食,动漫"},
    {"id": "T002", "name": "巴黎长途", "destination": "巴黎, 法国", "days": 10, "budget": 5000, "interests": "艺术,美食,历史"},
    {"id": "T003", "name": "曼谷低预算", "destination": "曼谷, 泰国", "days": 5, "budget": 800, "interests": "美食,寺庙"},
    {"id": "T004", "name": "首尔购物", "destination": "首尔, 韩国", "days": 4, "budget": 2000, "interests": "购物,美食,韩剧"},
    {"id": "T005", "name": "新加坡亲子", "destination": "新加坡", "days": 5, "budget": 3000, "interests": "亲子,美食,自然"},
    {"id": "T006", "name": "成都国内", "destination": "成都, 中国", "days": 3, "budget": 1000, "interests": "美食,熊猫,历史"},
    {"id": "T007", "name": "伦敦文化", "destination": "伦敦, 英国", "days": 7, "budget": 4000, "interests": "博物馆,历史,戏剧"},
    {"id": "T008", "name": "悉尼自然", "destination": "悉尼, 澳大利亚", "days": 6, "budget": 3500, "interests": "自然,海滩,野生动物"},
    {"id": "T009", "name": "迪拜奢华", "destination": "迪拜, 阿联酋", "days": 5, "budget": 5000, "interests": "奢华,建筑,购物"},
    {"id": "T010", "name": "巴厘岛度假", "destination": "巴厘岛, 印尼", "days": 7, "budget": 2000, "interests": "海滩,瑜伽,文化"},
    {"id": "T011", "name": "纽约都市", "destination": "纽约, 美国", "days": 5, "budget": 4000, "interests": "博物馆,百老汇,美食"},
    {"id": "T012", "name": "柏林历史", "destination": "柏林, 德国", "days": 4, "budget": 2000, "interests": "历史,建筑,啤酒"},
    {"id": "T013", "name": "清迈休闲", "destination": "清迈, 泰国", "days": 5, "budget": 800, "interests": "寺庙,美食,自然"},
    {"id": "T014", "name": "马尔代夫蜜月", "destination": "马尔代夫", "days": 5, "budget": 5000, "interests": "海滩,水上活动,SPA"},
    {"id": "T015", "name": "香港购物", "destination": "香港, 中国", "days": 3, "budget": 2000, "interests": "购物,美食,夜景"},
]


# ── 多评委系统 ────────────────────────────────────────────


JUDGES = [
    {
        "name": "completeness_judge",
        "role": "完整性评审",
        "prompt": (
            "你是旅行规划完整性评审专家。请检查以下旅行计划是否覆盖了五个核心维度：\n"
            "1. 目的地调研（景点、天气、交通、文化）\n"
            "2. 每日行程安排（具体到上午/下午/晚上）\n"
            "3. 预算分析（有具体金额和分类）\n"
            "4. 美食推荐（有具体餐厅名和菜品）\n"
            "5. 安全指南（有紧急电话和注意事项）\n\n"
            "请从1-10打分，并说明哪些维度覆盖得好，哪些缺失。\n"
            "输出JSON：{\"score\": 8, \"covered\": [\"调研\",\"行程\"], \"missing\": [\"安全\"], \"reason\": \"...\"}"
        ),
    },
    {
        "name": "practicality_judge",
        "role": "实用性评审",
        "prompt": (
            "你是旅行规划实用性评审专家。请检查以下旅行计划是否包含具体可执行的信息：\n"
            "1. 有真实的景点名称和门票价格\n"
            "2. 有具体的餐厅名称和人均消费\n"
            "3. 有交通方式和预估时间\n"
            "4. 有货币换算和实际金额\n"
            "5. 有营业时间、预约提示等实用信息\n\n"
            "请从1-10打分，并举出计划中具体/不具体的例子。\n"
            "输出JSON：{\"score\": 7, \"good_examples\": [\"浅草寺门票免费\"], \"bad_examples\": [\"预算不够具体\"], \"reason\": \"...\"}"
        ),
    },
    {
        "name": "structure_judge",
        "role": "结构化评审",
        "prompt": (
            "你是旅行规划结构化评审专家。请检查以下旅行计划的排版和组织：\n"
            "1. 有清晰的标题和分段\n"
            "2. 使用列表和编号\n"
            "3. 长度充实（不少于1500字）\n"
            "4. 信息层次清晰，易于阅读\n"
            "5. 各部分之间有逻辑衔接\n\n"
            "请从1-10打分。\n"
            "输出JSON：{\"score\": 8, \"length\": 3000, \"has_headers\": true, \"has_lists\": true, \"reason\": \"...\"}"
        ),
    },
]


def _parse_json_from_response(content: str) -> dict:
    """从 LLM 回复中提取 JSON"""
    json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass
    return {"score": 5, "reason": "JSON 解析失败"}


def judge_single(client: OpenAI, judge: dict, result: str, case: dict) -> dict:
    """单个评委评分"""
    user_msg = (
        f"旅行目的地：{case['destination']}\n"
        f"旅行天数：{case['days']}天\n"
        f"总预算：${case['budget']}\n"
        f"兴趣偏好：{case['interests']}\n\n"
        f"--- 待评审的旅行计划 ---\n\n{result}"
    )

    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": judge["prompt"]},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        content = resp.choices[0].message.content or ""
        scores = _parse_json_from_response(content)
        return {
            "judge": judge["name"],
            "score": min(10, max(1, int(scores.get("score", 5)))),
            "reason": scores.get("reason", ""),
            "details": scores,
        }
    except Exception as e:
        return {
            "judge": judge["name"],
            "score": 5,
            "reason": f"评分失败: {str(e)}",
            "details": {},
        }


def multi_judge_evaluate(result: str, case: dict) -> dict:
    """
    多评委交叉评分。
    3 个评委并行打分，取中位数作为最终分数。
    """
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    def _judge(judge):
        return judge_single(client, judge, result, case)

    with ThreadPoolExecutor(max_workers=3) as executor:
        judge_results = list(executor.map(_judge, JUDGES))

    # 取中位数
    scores = [jr["score"] for jr in judge_results]
    scores.sort()
    median_score = scores[len(scores) // 2]

    # 综合分 = 中位数 * 10（转百分制）
    overall = median_score * 10

    return {
        "case_id": case["id"],
        "case_name": case["name"],
        "overall": round(overall, 1),
        "median_score": median_score,
        "judge_results": judge_results,
        "length": len(result),
    }


# ── 评委一致性分析（Pearson 相关系数）────────────────────────


def calculate_judge_agreement(results: list[dict]) -> dict:
    """
    计算评委间一致性：两两 Pearson 相关系数。

    高相关（>0.7）表示评委对质量排序基本一致，
    低相关（<0.4）表示评委视角差异大，需校准。
    """
    judge_names = [j["name"] for j in JUDGES]
    judge_scores: dict[str, list[float]] = {name: [] for name in judge_names}

    for r in results:
        if "error" in r:
            continue
        for jr in r.get("judge_results", []):
            jname = jr["judge"]
            if jname in judge_scores:
                judge_scores[jname].append(float(jr["score"]))

    # 计算两两 Pearson
    pairs = []
    for i, name_a in enumerate(judge_names):
        for name_b in judge_names[i + 1:]:
            scores_a = judge_scores.get(name_a, [])
            scores_b = judge_scores.get(name_b, [])

            if len(scores_a) < 2 or len(scores_b) < 2:
                corr = 0.0
            else:
                n = min(len(scores_a), len(scores_b))
                a = scores_a[:n]
                b = scores_b[:n]
                mean_a = sum(a) / n
                mean_b = sum(b) / n
                cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
                std_a = (sum((x - mean_a) ** 2 for x in a)) ** 0.5
                std_b = (sum((y - mean_b) ** 2 for y in b)) ** 0.5
                corr = cov / (std_a * std_b) if std_a > 0 and std_b > 0 else 0.0

            pairs.append({
                "judge_a": name_a,
                "judge_b": name_b,
                "pearson_r": round(corr, 3),
                "n_samples": len(scores_a),
            })

    avg_corr = sum(p["pearson_r"] for p in pairs) / len(pairs) if pairs else 0

    return {
        "pairs": pairs,
        "avg_pearson": round(avg_corr, 3),
        "interpretation": (
            "高一致性（r>0.7）" if avg_corr > 0.7 else
            "中等一致性（0.4<r≤0.7）" if avg_corr > 0.4 else
            "低一致性（r≤0.4），评委视角差异大"
        ),
    }


# ── 兼容旧接口 ────────────────────────────────────────────


def evaluate_result(result: str, case: dict) -> dict:
    """综合评估（兼容旧接口）"""
    eval_result = multi_judge_evaluate(result, case)

    # 从评委结果中提取各维度分数
    completeness = 50
    practicality = 50
    structure = 50

    for jr in eval_result["judge_results"]:
        if jr["judge"] == "completeness_judge":
            completeness = jr["score"] * 10
        elif jr["judge"] == "practicality_judge":
            practicality = jr["score"] * 10
        elif jr["judge"] == "structure_judge":
            structure = jr["score"] * 10

    return {
        "case_id": case["id"],
        "case_name": case["name"],
        "completeness": round(completeness, 1),
        "practicality": round(practicality, 1),
        "structure": round(structure, 1),
        "overall": eval_result["overall"],
        "length": eval_result["length"],
        "judge_results": eval_result["judge_results"],
    }


# ── 评测测试 ──────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.parametrize("case", EVAL_CASES, ids=[c["id"] for c in EVAL_CASES])
def test_eval_case(case):
    """评测单个用例"""
    from app.crew import build_travel_crew

    result = build_travel_crew(
        destination=case["destination"],
        days=case["days"],
        budget=case["budget"],
        interests=case["interests"],
    )

    scores = evaluate_result(result, case)

    # 基本质量断言
    assert scores["overall"] >= 50, f"综合分不足: {scores['overall']}"


@pytest.mark.slow
def test_generate_eval_report():
    """生成评测报告"""
    from app.crew import build_travel_crew

    results = []
    for case in EVAL_CASES:
        try:
            result = build_travel_crew(
                destination=case["destination"],
                days=case["days"],
                budget=case["budget"],
                interests=case["interests"],
            )
            scores = evaluate_result(result, case)
            results.append(scores)
        except Exception as e:
            results.append({
                "case_id": case["id"],
                "case_name": case["name"],
                "error": str(e),
                "overall": 0,
            })

    # 计算平均分
    valid = [r for r in results if "error" not in r]
    if valid:
        avg = {
            "completeness": round(sum(r["completeness"] for r in valid) / len(valid), 1),
            "practicality": round(sum(r["practicality"] for r in valid) / len(valid), 1),
            "structure": round(sum(r["structure"] for r in valid) / len(valid), 1),
            "overall": round(sum(r["overall"] for r in valid) / len(valid), 1),
        }
    else:
        avg = {"completeness": 0, "practicality": 0, "structure": 0, "overall": 0}

    # ── 评委一致性分析 ──
    agreement = calculate_judge_agreement(results)

    report = generate_report(results, avg, agreement)

    report_path = Path(__file__).parent.parent / "eval" / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")

    assert len(results) == len(EVAL_CASES)
    assert avg["overall"] >= 45


def generate_report(results: list[dict], avg: dict, agreement: dict = None) -> str:
    """生成 Markdown 评测报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# 旅行规划智能体评测报告（多评委交叉评分）",
        "",
        f"**生成时间**: {now}",
        f"**评测用例数**: {len(results)}",
        f"**评分方式**: 3 个 LLM 评委独立打分，取中位数",
        f"**评委**: 完整性评审 + 实用性评审 + 结构化评审",
        "",
        "## 综合评分",
        "",
        "| 维度 | 平均分 |",
        "|------|--------|",
        f"| 完整性 | {avg['completeness']} |",
        f"| 实用性 | {avg['practicality']} |",
        f"| 结构化 | {avg['structure']} |",
        f"| **综合** | **{avg['overall']}** |",
        "",
        "## 详细结果",
        "",
        "| 用例 | 完整性 | 实用性 | 结构化 | 综合 | 字数 |",
        "|------|--------|--------|--------|------|------|",
    ]

    for r in results:
        if "error" in r:
            lines.append(f"| {r['case_id']} {r['case_name']} | - | - | - | ❌ | - |")
        else:
            lines.append(
                f"| {r['case_id']} {r['case_name']} | {r['completeness']} | "
                f"{r['practicality']} | {r['structure']} | {r['overall']} | {r['length']} |"
            )

    lines.extend([
        "",
        "## 评分标准",
        "",
        "采用**多评委交叉评分**，3 个 LLM 评委从不同维度独立打分（1-10 分），取中位数：",
        "",
        "- **完整性评审**: 是否覆盖调研/行程/预算/美食/安全五个方面",
        "- **实用性评审**: 是否包含具体可执行的信息（真实景点名、价格、餐厅）",
        "- **结构化评审**: 排版是否清晰，是否有标题/列表/分段",
        "",
        "多评委机制避免单一评委的偏见，中位数过滤极端分数。",
        "",
    ])

    # ── 评委一致性分析 ──
    if agreement:
        lines.extend([
            "## 评委一致性分析",
            "",
            f"**平均 Pearson 相关系数**: {agreement['avg_pearson']}",
            f"**一致性评估**: {agreement['interpretation']}",
            "",
            "| 评委 A | 评委 B | Pearson r | 样本数 |",
            "|--------|--------|-----------|--------|",
        ])
        for pair in agreement["pairs"]:
            lines.append(
                f"| {pair['judge_a']} | {pair['judge_b']} | "
                f"{pair['pearson_r']} | {pair['n_samples']} |"
            )
        lines.extend([
            "",
            "> Pearson r > 0.7：高一致性 | 0.4-0.7：中等一致性 | < 0.4：需校准",
            "",
        ])

    return "\n".join(lines)


# ── 非慢速测试（不需要 LLM）────────────────────────────────


class TestJudgeAgreement:
    """评委一致性分析测试（不需要 LLM API）"""

    def test_calculate_judge_agreement_basic(self):
        """基本一致性计算"""
        mock_results = [
            {
                "judge_results": [
                    {"judge": "completeness_judge", "score": 8},
                    {"judge": "practicality_judge", "score": 7},
                    {"judge": "structure_judge", "score": 9},
                ],
            },
            {
                "judge_results": [
                    {"judge": "completeness_judge", "score": 7},
                    {"judge": "practicality_judge", "score": 6},
                    {"judge": "structure_judge", "score": 8},
                ],
            },
            {
                "judge_results": [
                    {"judge": "completeness_judge", "score": 9},
                    {"judge": "practicality_judge", "score": 8},
                    {"judge": "structure_judge", "score": 9},
                ],
            },
        ]

        agreement = calculate_judge_agreement(mock_results)

        assert "pairs" in agreement
        assert "avg_pearson" in agreement
        assert "interpretation" in agreement
        assert len(agreement["pairs"]) == 3  # C(3,2) = 3 pairs
        assert -1 <= agreement["avg_pearson"] <= 1

    def test_calculate_judge_agreement_empty(self):
        """空结果不应崩溃"""
        agreement = calculate_judge_agreement([])
        assert agreement["avg_pearson"] == 0
        assert len(agreement["pairs"]) == 3

    def test_generate_report_with_agreement(self):
        """报告应包含一致性分析"""
        mock_avg = {"completeness": 70, "practicality": 65, "structure": 75, "overall": 70}
        mock_results = [
            {
                "case_id": "T001",
                "case_name": "测试",
                "completeness": 70,
                "practicality": 65,
                "structure": 75,
                "overall": 70,
                "length": 2000,
            },
        ]
        mock_agreement = {
            "pairs": [
                {"judge_a": "completeness_judge", "judge_b": "practicality_judge", "pearson_r": 0.85, "n_samples": 3},
                {"judge_a": "completeness_judge", "judge_b": "structure_judge", "pearson_r": 0.72, "n_samples": 3},
                {"judge_a": "practicality_judge", "judge_b": "structure_judge", "pearson_r": 0.68, "n_samples": 3},
            ],
            "avg_pearson": 0.75,
            "interpretation": "高一致性（r>0.7）",
        }

        report = generate_report(mock_results, mock_avg, mock_agreement)

        assert "评委一致性分析" in report
        assert "Pearson" in report
        assert "0.75" in report
        assert "高一致性" in report
