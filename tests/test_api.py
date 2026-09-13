"""
API 测试 - 测试 FastAPI 端点。
使用 TestClient 进行集成测试。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端"""
    from app.main import app

    return TestClient(app)


class TestHealthEndpoint:
    """健康检查端点测试"""

    def test_health(self, client):
        """GET /api/health 应返回 ok"""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agents"] == 5


class TestIndexEndpoint:
    """首页端点测试"""

    def test_index(self, client):
        """GET / 应返回 HTML"""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestPlanEndpoint:
    """规划端点测试"""

    @patch("app.routers.plan.build_travel_crew")
    def test_plan_sync_success(self, mock_crew, client):
        """POST /api/plan 成功应返回结果"""
        mock_crew.return_value = "测试旅行方案内容"

        resp = client.post("/api/plan", json={
            "destination": "东京, 日本",
            "days": 5,
            "budget": 3000,
            "interests": "美食,文化",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["result"] == "测试旅行方案内容"
        assert data["plan_id"] is not None

    @patch("app.routers.plan.build_travel_crew")
    def test_plan_sync_error(self, mock_crew, client):
        """POST /api/plan 失败应返回 HTTP 500"""
        mock_crew.side_effect = Exception("LLM 调用失败")

        resp = client.post("/api/plan", json={
            "destination": "东京, 日本",
            "days": 5,
            "budget": 3000,
        })
        assert resp.status_code == 500
        data = resp.json()
        assert "LLM 调用失败" in data["detail"]

    def test_plan_invalid_input(self, client):
        """无效输入应返回 422"""
        resp = client.post("/api/plan", json={
            "destination": "",  # 空目的地
            "days": 0,          # 无效天数
            "budget": -1,       # 无效预算
        })
        assert resp.status_code == 422


class TestHistoryEndpoint:
    """历史记录端点测试"""

    def test_history_empty(self, client):
        """空历史应返回空列表"""
        resp = client.get("/api/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "records" in data

    def test_history_pagination(self, client):
        """分页参数应正常工作"""
        resp = client.get("/api/history?limit=5&offset=0")
        assert resp.status_code == 200


class TestFollowupEndpoint:
    """多轮对话端点测试"""

    @patch("app.routers.plan.OpenAI")
    @patch("app.routers.plan.build_travel_crew")
    def test_followup_after_plan(self, mock_crew, mock_openai, client):
        """生成计划后应能追问（followup 的 LLM 调用须 mock，避免依赖真实配额）"""
        from types import SimpleNamespace
        mock_crew.return_value = "测试旅行方案"
        fake_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="已将第二天行程调整为购物。"))]
        )
        mock_openai.return_value.chat.completions.create.return_value = fake_resp

        # 先生成一个计划
        resp = client.post("/api/plan", json={
            "destination": "东京, 日本",
            "days": 3,
            "budget": 2000,
            "interests": "美食",
        })
        plan_id = resp.json()["plan_id"]

        # 追问
        resp2 = client.post("/api/plan/followup", json={
            "plan_id": plan_id,
            "message": "把第二天换成购物行程",
        })
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["status"] == "ok"
        assert len(data["reply"]) > 0
        assert len(data["history"]) >= 2  # user + assistant

    def test_followup_nonexistent_plan(self, client):
        """追问不存在的计划应返回 HTTP 400"""
        resp = client.post("/api/plan/followup", json={
            "plan_id": "nonexist",
            "message": "修改行程",
        })
        assert resp.status_code == 400

    @patch("app.routers.plan.build_travel_crew")
    def test_conversation_history(self, mock_crew, client):
        """获取对话历史"""
        mock_crew.return_value = "测试方案"

        resp = client.post("/api/plan", json={
            "destination": "巴黎, 法国",
            "days": 5,
            "budget": 4000,
        })
        plan_id = resp.json()["plan_id"]

        # 获取空对话历史
        resp2 = client.get(f"/api/plan/conversation/{plan_id}")
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["status"] == "ok"
        assert len(data["history"]) == 0
