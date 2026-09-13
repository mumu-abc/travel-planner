"""
数据库单元测试 — SQLite 持久化存储测试。
使用临时数据库文件，不污染生产数据。
"""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def test_db():
    """创建临时数据库实例"""
    from app.database import Database

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        db = Database(db_path=db_path)
        yield db
        db.close()
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


class TestDatabaseCRUD:
    """数据库 CRUD 操作测试"""

    def test_save_and_get_plan(self, test_db):
        """保存规划后应能通过 ID 查回"""
        plan_id = test_db.save_plan(
            destination="东京, 日本",
            days=5,
            budget=3000,
            interests="美食,文化",
            result="测试旅行方案",
        )
        assert plan_id is not None
        assert len(plan_id) == 8

        plan = test_db.get_plan(plan_id)
        assert plan is not None
        assert plan["destination"] == "东京, 日本"
        assert plan["days"] == 5
        assert plan["budget"] == 3000
        assert plan["interests"] == "美食,文化"
        assert plan["result"] == "测试旅行方案"

    def test_get_nonexistent_plan(self, test_db):
        """查询不存在的 ID 应返回 None"""
        plan = test_db.get_plan("nonexist")
        assert plan is None

    def test_history_pagination(self, test_db):
        """历史记录分页应正确"""
        for i in range(5):
            test_db.save_plan("巴黎", 3, 2000, "艺术", f"结果{i}")

        total, records = test_db.get_history(limit=3, offset=0)
        assert total == 5
        assert len(records) == 3

        total2, records2 = test_db.get_history(limit=3, offset=3)
        assert total2 == 5
        assert len(records2) == 2

    def test_search_plans(self, test_db):
        """按目的地搜索"""
        test_db.save_plan("东京, 日本", 3, 1500, "美食", "东京方案")
        test_db.save_plan("大阪, 日本", 3, 1500, "美食", "大阪方案")
        test_db.save_plan("巴黎, 法国", 3, 1500, "艺术", "巴黎方案")

        results = test_db.search_plans("日本")
        assert len(results) == 2

        results = test_db.search_plans("巴黎")
        assert len(results) == 1

    def test_cleanup_old_records(self, test_db):
        """清理旧记录"""
        from datetime import datetime, timedelta
        import uuid

        # 插入一条"旧"记录
        old_date = (datetime.now() - timedelta(days=40)).isoformat()
        conn = test_db._get_conn()
        conn.execute(
            "INSERT INTO plans (id, destination, days, budget, interests, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:8], "旧记录", 1, 100, "test", "old", old_date),
        )
        conn.commit()

        deleted = test_db.cleanup_old_records(days=30)
        assert deleted == 1

        total, _ = test_db.get_history()
        assert total == 0


class TestConversationCRUD:
    """多轮对话数据库操作测试"""

    def test_save_and_get_conversation(self, test_db):
        """保存对话记录后应能查回"""
        plan_id = test_db.save_plan("东京", 3, 1500, "美食", "东京方案")

        test_db.save_conversation(plan_id, "user", "把第三天换成海边")
        test_db.save_conversation(plan_id, "assistant", "好的，已调整...")

        history = test_db.get_conversation_history(plan_id)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "把第三天换成海边"
        assert history[1]["role"] == "assistant"

    def test_conversation_ordering(self, test_db):
        """对话历史应按时间正序"""
        plan_id = test_db.save_plan("巴黎", 5, 3000, "艺术", "巴黎方案")

        for i in range(5):
            test_db.save_conversation(plan_id, "user", f"问题{i}")

        history = test_db.get_conversation_history(plan_id)
        assert len(history) == 5
        for i, record in enumerate(history):
            assert record["content"] == f"问题{i}"

    def test_empty_conversation(self, test_db):
        """无对话记录的规划应返回空列表"""
        plan_id = test_db.save_plan("首尔", 3, 2000, "购物", "首尔方案")
        history = test_db.get_conversation_history(plan_id)
        assert len(history) == 0
