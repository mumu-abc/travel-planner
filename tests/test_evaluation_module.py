"""
评估闭环模块测试 — 评委打分、中位数聚合、回归检测、持久化。
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime


# ── 模拟 OpenAI 响应 ──────────────────────────────────────

def _mock_judge_response(score=8, reason="良好"):
    """创建模拟的评委评分响应"""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = json.dumps({"score": score, "reason": reason})
    return mock_resp


# ── 测试 judge_single ─────────────────────────────────────

class TestJudgeSingle:
    def test_valid_score(self):
        from app.evaluation import judge_single

        client = MagicMock()
        client.chat.completions.create.return_value = _mock_judge_response(8, "覆盖全面")

        case = {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "美食"}
        result = judge_single("规划内容", case, "completeness", client)

        assert result["judge_type"] == "completeness"
        assert result["score"] == 8
        assert result["reason"] == "覆盖全面"

    def test_score_clamped_high(self):
        from app.evaluation import judge_single

        client = MagicMock()
        client.chat.completions.create.return_value = _mock_judge_response(15, "超分")

        case = {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "美食"}
        result = judge_single("规划内容", case, "practicality", client)

        assert result["score"] == 10  # 被钳制到 10

    def test_score_clamped_low(self):
        from app.evaluation import judge_single

        client = MagicMock()
        client.chat.completions.create.return_value = _mock_judge_response(-3, "低分")

        case = {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "美食"}
        result = judge_single("规划内容", case, "structure", client)

        assert result["score"] == 1  # 被钳制到 1

    def test_invalid_json_response(self):
        from app.evaluation import judge_single

        client = MagicMock()
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="这不是JSON"))]
        )

        case = {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "美食"}
        result = judge_single("规划内容", case, "completeness", client)

        assert result["score"] == 5  # 默认分

    def test_llm_error(self):
        from app.evaluation import judge_single

        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("API error")

        case = {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "美食"}
        result = judge_single("规划内容", case, "completeness", client)

        assert result["score"] == 5
        assert "异常" in result["reason"]


# ── 测试 multi_judge_evaluate ─────────────────────────────

class TestMultiJudgeEvaluate:
    def test_median_scoring(self):
        from app.evaluation import multi_judge_evaluate

        client = MagicMock()
        # 3 个评委分别打 7, 8, 9
        client.chat.completions.create.side_effect = [
            _mock_judge_response(7, "完整性一般"),
            _mock_judge_response(9, "实用性高"),
            _mock_judge_response(8, "结构良好"),
        ]

        case = {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "美食"}
        result = multi_judge_evaluate("规划内容", case, client)

        # 中位数是 8
        assert result["overall"] == 80  # 8 * 10
        assert result["completeness"] == 70  # 7 * 10
        assert result["practicality"] == 90  # 9 * 10
        assert result["structure"] == 80  # 8 * 10
        assert len(result["judge_results"]) == 3

    def test_all_same_score(self):
        from app.evaluation import multi_judge_evaluate

        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _mock_judge_response(6, ""),
            _mock_judge_response(6, ""),
            _mock_judge_response(6, ""),
        ]

        case = {"id": "T001", "destination": "东京", "days": 5, "budget": 2000, "interests": "美食"}
        result = multi_judge_evaluate("规划内容", case, client)

        assert result["overall"] == 60


# ── 测试 calculate_judge_agreement ─────────────────────────

class TestJudgeAgreement:
    def test_perfect_agreement(self):
        from app.evaluation import calculate_judge_agreement

        judges = [
            {"judge_type": "completeness", "score": 8, "reason": ""},
            {"judge_type": "practicality", "score": 8, "reason": ""},
            {"judge_type": "structure", "score": 8, "reason": ""},
        ]
        assert calculate_judge_agreement(judges) == 1.0

    def test_one_point_diff(self):
        from app.evaluation import calculate_judge_agreement

        judges = [
            {"judge_type": "completeness", "score": 7, "reason": ""},
            {"judge_type": "practicality", "score": 8, "reason": ""},
            {"judge_type": "structure", "score": 8, "reason": ""},
        ]
        assert calculate_judge_agreement(judges) == 0.9

    def test_two_point_diff(self):
        from app.evaluation import calculate_judge_agreement

        judges = [
            {"judge_type": "completeness", "score": 6, "reason": ""},
            {"judge_type": "practicality", "score": 8, "reason": ""},
            {"judge_type": "structure", "score": 7, "reason": ""},
        ]
        assert calculate_judge_agreement(judges) == 0.8

    def test_large_diff(self):
        from app.evaluation import calculate_judge_agreement

        judges = [
            {"judge_type": "completeness", "score": 2, "reason": ""},
            {"judge_type": "practicality", "score": 9, "reason": ""},
            {"judge_type": "structure", "score": 5, "reason": ""},
        ]
        # 差距 7 → max(0, 1.0 - 0.7) = 0.3
        assert calculate_judge_agreement(judges) == 0.3

    def test_single_judge(self):
        from app.evaluation import calculate_judge_agreement

        judges = [{"judge_type": "completeness", "score": 8, "reason": ""}]
        assert calculate_judge_agreement(judges) == 1.0

    def test_empty(self):
        from app.evaluation import calculate_judge_agreement

        assert calculate_judge_agreement([]) == 1.0


# ── 测试 detect_regressions ────────────────────────────────

class TestDetectRegressions:
    def test_no_baseline(self):
        from app.evaluation import detect_regressions

        current = {"cases": [], "summary": {"avg_overall": 70}}
        result = detect_regressions(current, baseline=None)

        assert result["has_regression"] is False
        assert "无基线" in result["message"]

    def test_no_regression(self):
        from app.evaluation import detect_regressions

        current = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 80, "completeness": 80, "practicality": 80, "structure": 80}}],
            "summary": {"avg_overall": 80, "avg_completeness": 80, "avg_practicality": 80, "avg_structure": 80},
        }
        baseline = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 78, "completeness": 78, "practicality": 78, "structure": 78}}],
            "summary": {"avg_overall": 78, "avg_completeness": 78, "avg_practicality": 78, "avg_structure": 78},
        }

        result = detect_regressions(current, baseline, threshold=5.0)
        assert result["has_regression"] is False

    def test_detects_regression(self):
        from app.evaluation import detect_regressions

        current = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 60, "completeness": 60, "practicality": 60, "structure": 60}}],
            "summary": {"avg_overall": 60, "avg_completeness": 60, "avg_practicality": 60, "avg_structure": 60},
        }
        baseline = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 80, "completeness": 80, "practicality": 80, "structure": 80}}],
            "summary": {"avg_overall": 80, "avg_completeness": 80, "avg_practicality": 80, "avg_structure": 80},
        }

        result = detect_regressions(current, baseline, threshold=5.0)
        assert result["has_regression"] is True
        assert len(result["regressions"]) > 0
        assert any(r["dimension"] == "overall" for r in result["regressions"])

    def test_detects_improvement(self):
        from app.evaluation import detect_regressions

        current = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 90, "completeness": 90, "practicality": 90, "structure": 90}}],
            "summary": {"avg_overall": 90, "avg_completeness": 90, "avg_practicality": 90, "avg_structure": 90},
        }
        baseline = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 70, "completeness": 70, "practicality": 70, "structure": 70}}],
            "summary": {"avg_overall": 70, "avg_completeness": 70, "avg_practicality": 70, "avg_structure": 70},
        }

        result = detect_regressions(current, baseline, threshold=5.0)
        assert len(result["improvements"]) > 0

    def test_summary_delta(self):
        from app.evaluation import detect_regressions

        current = {
            "cases": [],
            "summary": {"avg_overall": 75, "avg_completeness": 80, "avg_practicality": 70, "avg_structure": 75},
        }
        baseline = {
            "cases": [],
            "summary": {"avg_overall": 70, "avg_completeness": 75, "avg_practicality": 68, "avg_structure": 72},
        }

        result = detect_regressions(current, baseline)
        assert result["summary_delta"]["avg_overall"] == 5.0
        assert result["summary_delta"]["avg_completeness"] == 5.0

    def test_threshold_sensitivity(self):
        """分数下降 4 分，阈值 5 不触发，阈值 3 触发"""
        from app.evaluation import detect_regressions

        current = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 70, "completeness": 70, "practicality": 70, "structure": 70}}],
            "summary": {},
        }
        baseline = {
            "cases": [{"case_id": "T001", "destination": "东京", "scores": {"overall": 74, "completeness": 74, "practicality": 74, "structure": 74}}],
            "summary": {},
        }

        r1 = detect_regressions(current, baseline, threshold=5.0)
        assert r1["has_regression"] is False

        r2 = detect_regressions(current, baseline, threshold=3.0)
        assert r2["has_regression"] is True


# ── 测试 EVAL_CASES ───────────────────────────────────────

class TestEvalCases:
    def test_cases_count(self):
        from app.evaluation import EVAL_CASES
        assert len(EVAL_CASES) == 15

    def test_case_structure(self):
        from app.evaluation import EVAL_CASES
        for case in EVAL_CASES:
            assert "id" in case
            assert "destination" in case
            assert "days" in case
            assert "budget" in case
            assert "interests" in case
            assert case["days"] >= 1
            assert case["budget"] > 0

    def test_unique_ids(self):
        from app.evaluation import EVAL_CASES
        ids = [c["id"] for c in EVAL_CASES]
        assert len(ids) == len(set(ids))

    def test_diverse_destinations(self):
        from app.evaluation import EVAL_CASES
        dests = set(c["destination"] for c in EVAL_CASES)
        assert len(dests) >= 10  # 至少覆盖 10 个不同目的地


# ── 测试 JUDGE_PROMPTS ────────────────────────────────────

class TestJudgePrompts:
    def test_three_judges(self):
        from app.evaluation import JUDGE_PROMPTS
        assert "completeness" in JUDGE_PROMPTS
        assert "practicality" in JUDGE_PROMPTS
        assert "structure" in JUDGE_PROMPTS
        assert len(JUDGE_PROMPTS) == 3

    def test_prompts_contain_json_instruction(self):
        from app.evaluation import JUDGE_PROMPTS
        for prompt in JUDGE_PROMPTS.values():
            assert "JSON" in prompt
            assert "score" in prompt


# ── 数据库集成测试 ─────────────────────────────────────────

class TestEvalDatabase:
    def test_save_and_retrieve(self):
        """测试评测结果的存储和读取"""
        from app.database import Database
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            db = Database(db_path)

            eval_result = {
                "run_id": "eval_test01",
                "run_name": "测试评测",
                "timestamp": datetime.now().isoformat(),
                "cases": [
                    {
                        "case_id": "T001",
                        "destination": "东京",
                        "days": 5,
                        "budget": 2000,
                        "scores": {"overall": 80, "completeness": 85, "practicality": 75, "structure": 80},
                        "judge_results": [],
                        "plan_length": 3000,
                        "agreement": 0.9,
                    }
                ],
                "summary": {
                    "total_cases": 1,
                    "evaluated": 1,
                    "failed": 0,
                    "pass_rate": 1.0,
                    "avg_overall": 80.0,
                    "avg_completeness": 85.0,
                    "avg_practicality": 75.0,
                    "avg_structure": 80.0,
                    "avg_agreement": 0.9,
                },
            }

            # 保存
            run_id = db.save_eval_run(eval_result)
            assert run_id == "eval_test01"

            # 读取列表
            runs = db.get_eval_runs()
            assert len(runs) == 1
            assert runs[0]["run_name"] == "测试评测"

            # 读取详情
            detail = db.get_eval_run("eval_test01")
            assert detail is not None
            assert len(detail["cases"]) == 1
            assert detail["cases"][0]["destination"] == "东京"

            # 最近一次
            latest = db.get_latest_eval_run()
            assert latest is not None
            assert latest["id"] == "eval_test01"

            db.close()

        finally:
            import time
            time.sleep(0.1)  # 等待连接释放
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # Windows 上文件锁可能延迟释放
