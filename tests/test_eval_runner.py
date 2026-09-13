"""
eval_runner 修复的行为测试（不调 LLM）：
  - 降级/失败 run 不得计入平均分（离线降级、0 次 LLM 调用、评委全失败）
  - 评委解析失败不得伪造分数
  - 报告必须把失败 case 单独列出
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval import eval_runner as er


# ── 伪 OpenAI 客户端 ─────────────────────────────────────────


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self._responses[min(self.calls - 1, len(self._responses) - 1)]
        if isinstance(item, Exception):
            raise item

        class _R:
            def __init__(self, content):
                self.choices = [SimpleNamespace(message=SimpleNamespace(content=content))]

        return _R(item)


class _FakeClient:
    def __init__(self, responses):
        self.chat = SimpleNamespace(completions=_FakeCompletions(responses))


JUDGE = er.JUDGES[0]


# ── 评委解析 ─────────────────────────────────────────────────


class TestExtractJsonScore:
    def test_plain_json(self):
        assert er._extract_json_score('{"score": 8, "reason": "ok"}')["score"] == 8

    def test_code_block(self):
        text = "评审如下：\n```json\n{\"score\": 7, \"reason\": \"好\"}\n```"
        assert er._extract_json_score(text)["score"] == 7

    def test_think_block_stripped(self):
        text = "<think>让我想想…评分应该是 9</think>{\"score\": 9, \"reason\": \"完整\"}"
        assert er._extract_json_score(text)["score"] == 9

    def test_score_regex_fallback(self):
        assert er._extract_json_score("我认为 score: 6 分")["score"] == 6

    def test_unparseable_returns_none(self):
        assert er._extract_json_score("这是一段没有任何分数的废话") is None


class TestJudgeScore:
    def test_never_fabricates_score_on_parse_failure(self, monkeypatch):
        monkeypatch.setattr(er.time, "sleep", lambda *_: None)
        client = _FakeClient(["抱歉，我无法评分。", "仍然没有 JSON。"])
        result = er.judge_score(client, JUDGE, "计划正文")
        assert result["status"] == "failed"
        assert result["score"] is None

    def test_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(er.time, "sleep", lambda *_: None)
        client = _FakeClient([RuntimeError("429 rate limited"), '{"score": 8, "reason": "r"}'])
        result = er.judge_score(client, JUDGE, "计划正文")
        assert result["status"] == "ok"
        assert result["score"] == 8

    def test_out_of_range_score_rejected(self, monkeypatch):
        monkeypatch.setattr(er.time, "sleep", lambda *_: None)
        client = _FakeClient(['{"score": 42, "reason": "x"}', '{"score": 42}'])
        result = er.judge_score(client, JUDGE, "计划正文")
        assert result["status"] == "failed"
        assert result["score"] is None


# ── run 状态分类：核心修复 ───────────────────────────────────


class TestClassifyRun:
    def test_ok(self):
        usage = {"llm_calls": 18}
        ev = {"status": "ok"}
        assert er._classify_run("# 东京计划\n\n正常内容" * 50, usage, ev) == "ok"

    def test_zero_llm_calls_is_not_ok(self):
        """离线降级（0 次 LLM）绝不能被标成 ok — 这是上一版 0 分行的根源。"""
        usage = {"llm_calls": 0}
        ev = {"status": "ok"}
        assert er._classify_run("# 离线版攻略" * 100, usage, ev) == "no_llm"

    def test_offline_marker_is_degraded(self):
        usage = {"llm_calls": 5}
        ev = {"status": "ok"}
        content = "正文" * 300 + "AI 服务暂时不可用，以下为基础信息，仅供参考。"
        assert er._classify_run(content, usage, ev) == "degraded_offline"

    def test_all_judges_failed(self):
        usage = {"llm_calls": 18}
        ev = {"status": "judge_failed"}
        assert er._classify_run("正常内容" * 300, usage, ev) == "judge_failed"

    def test_all_agents_failed(self):
        usage = {"llm_calls": 3}
        ev = {"status": "ok"}
        content = "[Researcher] 生成失败: x\n[Planner] 生成失败: y\n[Budget] 生成失败: z"
        assert er._classify_run(content, usage, ev) == "agent_failed"


class TestEvaluatePlan:
    def test_all_judges_failed_marks_judge_failed(self, monkeypatch):
        monkeypatch.setattr(er.time, "sleep", lambda *_: None)
        client = _FakeClient(["no json here"])
        ev = er.evaluate_plan(client, "计划内容" * 100)
        assert ev["status"] == "judge_failed"
        assert ev["overall"] == 0

    def test_partial_judge_failure_uses_valid_median(self, monkeypatch):
        monkeypatch.setattr(er.time, "sleep", lambda *_: None)
        # 调用顺序：评委1(试2次) → 评委2(试2次) → 评委3(1次成功) → 只有评委3有效
        client = _FakeClient([
            "garbage", "garbage",
            "garbage", "garbage",
            '{"score": 8, "reason": "r"}',
        ])
        ev = er.evaluate_plan(client, "计划内容" * 100)
        assert ev["status"] == "ok"
        assert ev["judges_ok"] == 1
        assert ev["overall"] == 80


# ── 聚合与报告 ───────────────────────────────────────────────


def _mk_row(mode: str, status: str, score: float = 80.0, case_id: str = "T001") -> dict:
    return {
        "case": {"id": case_id, "destination": "东京"},
        "mode": mode,
        "enable_reflection": True,
        "eval": {"overall": score, "keyword_coverage": 100.0, "char_count": 3000, "judges": {}},
        "elapsed": 100.0,
        "usage": {"approx_total_tokens": 10000, "approx_cost_usd": 0.01, "llm_calls": 10, "tool_calls": 5},
        "status": status,
        "reason": "fake reason" if status != "ok" else "",
    }


class TestAgg:
    def test_failed_rows_excluded(self):
        rows = [_mk_row("multi", "ok", 80), _mk_row("multi", "no_llm", 0), _mk_row("multi", "judge_failed", 0)]
        a = er._agg(rows)
        assert a["n"] == 1
        assert a["score"] == 80.0

    def test_all_failed_zero_n(self):
        a = er._agg([_mk_row("multi", "no_llm")])
        assert a["n"] == 0

    def test_std_computed(self):
        rows = [_mk_row("multi", "ok", 80), _mk_row("multi", "ok", 90, "T002")]
        a = er._agg(rows)
        assert a["std"] == pytest.approx(7.07, rel=0.05)


class TestRunOneDegraded:
    """run_one 集成：离线降级输出必须被标为 no_llm，而不是 ok。"""

    def test_offline_fallback_is_not_ok(self, monkeypatch):
        monkeypatch.setattr(er.time, "sleep", lambda *_: None)

        import app.crew as crew_mod
        import app.usage as usage_mod

        offline_plan = "# 🗺️ 东京 3日旅行攻略（离线版）\n\n> ⚠️ AI 服务暂时不可用，以下为基础信息，仅供参考。\n" + "内容" * 300

        monkeypatch.setattr(crew_mod, "build_travel_crew", lambda *a, **k: offline_plan)

        class _FakeUsage:
            def reset(self):
                pass

            def snapshot(self):
                return {
                    "approx_input_tokens": 0,
                    "approx_output_tokens": 0,
                    "approx_total_tokens": 0,
                    "llm_calls": 0,
                    "tool_calls": 0,
                }

        monkeypatch.setattr(usage_mod, "get_usage", lambda: _FakeUsage())

        client = _FakeClient([RuntimeError("429")])
        case = {"id": "T001", "name": "东京", "destination": "东京, 日本", "days": 3, "budget": 1500, "interests": "美食"}
        row = er.run_one(client, case, mode="single", enable_reflection=False)
        assert row["status"] == "no_llm"
        assert row["status"] != "ok"
        assert row.get("reason")


class TestReport:
    def test_failure_section_rendered(self):
        rows = [_mk_row("multi", "ok", 80), _mk_row("sequential", "no_llm", 0, "T001")]
        report = er._generate_report(rows, "test-model", ablation=True)
        assert "失败与降级明细" in report
        assert "no_llm" in report
        assert "成功" in report

    def test_zero_success_shows_dash_not_zero(self):
        """全失败的配置不能显示 0.0 平均分（会被误读为真实得分）。"""
        rows = [_mk_row("sequential", "no_llm", 0)]
        report = er._generate_report(rows, "test-model", ablation=False)
        assert "— |" in report or "| —" in report
        assert "**0.0**" not in report

    def test_ablation_conclusion_blocked_when_no_valid_data(self):
        rows = [
            _mk_row("multi", "judge_failed", 0),
            _mk_row("single", "judge_failed", 0, "T001"),
        ]
        report = er._generate_report(rows, "test-model", ablation=True)
        assert "无法给出质量结论" in report
