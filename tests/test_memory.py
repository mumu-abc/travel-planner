"""
记忆系统测试 — CRUD、向量检索、LLM 提取、去重、上下文格式化。
"""

import json
import pytest
from unittest.mock import patch, MagicMock
import numpy as np


# ── 模拟依赖 ──────────────────────────────────────────────

MOCK_MODEL = MagicMock()
MOCK_INDEX = MagicMock()


@pytest.fixture
def mock_memory_deps(monkeypatch):
    """模拟 sentence_transformers 和 faiss"""
    # 模拟 embedding 模型
    mock_model_instance = MagicMock()
    mock_model_instance.encode.return_value = np.random.rand(1, 512).astype("float32")
    monkeypatch.setattr("app.memory._get_model", lambda: mock_model_instance)

    # 模拟 FAISS 索引
    mock_index_instance = MagicMock()
    mock_index_instance.ntotal = 0
    mock_index_instance.search.return_value = (
        np.array([[0.9, 0.8, 0.7]]),
        np.array([[0, 1, 2]]),
    )
    monkeypatch.setattr("app.memory._get_index", lambda: mock_index_instance)
    monkeypatch.setattr("app.memory._save_index", lambda: None)

    return mock_model_instance, mock_index_instance


@pytest.fixture
def mock_db(monkeypatch):
    """模拟数据库"""
    mock_db_instance = MagicMock()
    mock_conn = MagicMock()
    mock_db_instance._get_conn.return_value = mock_conn

    # 模拟 fetchone 返回记忆记录
    mock_conn.execute.return_value.fetchone.return_value = {
        "id": "test0001",
        "content": "用户不吃辣",
        "memory_type": "fact",
        "destination": "东京",
        "tags": "东京",
        "embedding_id": 0,
        "created_at": "2024-01-01T00:00:00",
        "last_accessed": None,
        "access_count": 0,
    }
    mock_conn.execute.return_value.fetchall.return_value = []

    monkeypatch.setattr("app.memory._get_db", lambda: mock_db_instance)
    return mock_db_instance, mock_conn


# ── 测试 add_memory ────────────────────────────────────────

class TestAddMemory:
    def test_add_fact_memory(self, mock_memory_deps, mock_db):
        from app.memory import add_memory

        mid = add_memory("用户不吃辣", memory_type="fact", destination="东京")
        assert len(mid) == 8
        mock_db[0]._get_conn.assert_called()

    def test_add_preference_memory(self, mock_memory_deps, mock_db):
        from app.memory import add_memory

        mid = add_memory("偏好经济型住宿", memory_type="preference")
        assert len(mid) == 8

    def test_add_interaction_memory(self, mock_memory_deps, mock_db):
        from app.memory import add_memory

        mid = add_memory(
            "2024-08 规划了东京3天美食之旅",
            memory_type="interaction",
            destination="东京",
        )
        assert len(mid) == 8

    def test_add_memory_calls_faiss(self, mock_memory_deps, mock_db):
        from app.memory import add_memory

        mock_model, mock_index = mock_memory_deps
        add_memory("测试内容")
        mock_model.encode.assert_called_once()
        mock_index.add.assert_called_once()

    def test_add_memory_saves_index(self, mock_memory_deps, monkeypatch):
        from app.memory import add_memory

        saved = []
        monkeypatch.setattr("app.memory._save_index", lambda: saved.append(True))
        mock_db_instance = MagicMock()
        mock_conn = MagicMock()
        mock_db_instance._get_conn.return_value = mock_conn
        monkeypatch.setattr("app.memory._get_db", lambda: mock_db_instance)

        add_memory("测试持久化")
        assert len(saved) == 1


# ── 测试 retrieve_memories ─────────────────────────────────

class TestRetrieveMemories:
    def test_retrieve_basic(self, mock_memory_deps, mock_db):
        from app.memory import retrieve_memories

        results = retrieve_memories("东京美食")
        assert isinstance(results, list)

    def test_retrieve_empty_index(self, mock_memory_deps, mock_db, monkeypatch):
        from app.memory import retrieve_memories

        mock_index = mock_memory_deps[1]
        mock_index.ntotal = 0
        results = retrieve_memories("任何查询")
        assert results == []

    def test_retrieve_with_type_filter(self, mock_memory_deps, mock_db):
        from app.memory import retrieve_memories

        results = retrieve_memories("测试", memory_type="fact")
        assert isinstance(results, list)

    def test_retrieve_with_destination_filter(self, mock_memory_deps, mock_db):
        from app.memory import retrieve_memories

        results = retrieve_memories("测试", destination="东京")
        assert isinstance(results, list)

    def test_retrieve_respects_top_k(self, mock_memory_deps, mock_db):
        from app.memory import retrieve_memories

        results = retrieve_memories("测试", top_k=2)
        assert len(results) <= 2


# ── 测试 get_all_memories ───────────────────────────────────

class TestGetAllMemories:
    def test_get_all(self, mock_memory_deps, mock_db):
        from app.memory import get_all_memories

        mock_db[0]._get_conn.return_value.execute.return_value.fetchall.return_value = []
        result = get_all_memories()
        assert isinstance(result, list)

    def test_get_by_type(self, mock_memory_deps, mock_db):
        from app.memory import get_all_memories

        mock_db[0]._get_conn.return_value.execute.return_value.fetchall.return_value = []
        result = get_all_memories(memory_type="fact")
        assert isinstance(result, list)


# ── 测试 delete_memory ─────────────────────────────────────

class TestDeleteMemory:
    def test_delete_existing(self, mock_memory_deps, mock_db):
        from app.memory import delete_memory

        mock_db[1].execute.return_value.rowcount = 1
        assert delete_memory("test0001") is True

    def test_delete_nonexistent(self, mock_memory_deps, mock_db):
        from app.memory import delete_memory

        mock_db[1].execute.return_value.rowcount = 0
        assert delete_memory("nonexist") is False


# ── 测试 format_memories_for_context ────────────────────────

class TestFormatMemories:
    def test_format_empty(self):
        from app.memory import format_memories_for_context
        assert format_memories_for_context([]) == ""

    def test_format_fact(self):
        from app.memory import format_memories_for_context
        memories = [{
            "memory_type": "fact",
            "destination": "东京",
            "content": "用户不吃辣",
        }]
        result = format_memories_for_context(memories)
        assert "事实" in result
        assert "用户不吃辣" in result
        assert "东京" in result

    def test_format_preference(self):
        from app.memory import format_memories_for_context
        memories = [{
            "memory_type": "preference",
            "destination": "",
            "content": "偏好经济型住宿",
        }]
        result = format_memories_for_context(memories)
        assert "偏好" in result

    def test_format_interaction(self):
        from app.memory import format_memories_for_context
        memories = [{
            "memory_type": "interaction",
            "destination": "",
            "content": "2024-08 规划了东京之旅",
        }]
        result = format_memories_for_context(memories)
        assert "历史" in result

    def test_format_multiple(self):
        from app.memory import format_memories_for_context
        memories = [
            {"memory_type": "fact", "destination": "东京", "content": "不吃辣"},
            {"memory_type": "preference", "destination": "", "content": "经济型住宿"},
        ]
        result = format_memories_for_context(memories)
        assert "不吃辣" in result
        assert "经济型住宿" in result
        assert result.startswith("【用户记忆】")

    def test_format_unknown_type(self):
        from app.memory import format_memories_for_context
        memories = [{
            "memory_type": "unknown",
            "destination": "",
            "content": "某条记忆",
        }]
        result = format_memories_for_context(memories)
        assert "记忆" in result


# ── 测试 extract_memories_from_plan ─────────────────────────

class TestExtractMemories:
    def test_extract_success(self, mock_memory_deps, monkeypatch):
        from app.memory import extract_memories_from_plan

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"type": "fact", "content": "用户对海鲜过敏"},
            {"type": "preference", "content": "偏好经济型住宿"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: mock_client)

        # 模拟 retrieve_memories 返回空（无重复）
        monkeypatch.setattr("app.memory.retrieve_memories", lambda *args, **kwargs: [])
        # 模拟 add_memory
        monkeypatch.setattr("app.memory.add_memory", lambda *args, **kwargs: "mem0001")

        result = extract_memories_from_plan("规划内容", "东京", 1000, "美食")
        assert len(result) == 2

    def test_extract_empty_response(self, mock_memory_deps, monkeypatch):
        from app.memory import extract_memories_from_plan

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[]"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: mock_client)

        result = extract_memories_from_plan("规划内容", "东京", 1000, "美食")
        assert result == []

    def test_extract_invalid_json(self, mock_memory_deps, monkeypatch):
        from app.memory import extract_memories_from_plan

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "这不是JSON"

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: mock_client)

        result = extract_memories_from_plan("规划内容", "东京", 1000, "美食")
        assert result == []

    def test_extract_dedup(self, mock_memory_deps, monkeypatch):
        """高相似度记忆应被跳过"""
        from app.memory import extract_memories_from_plan

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"type": "fact", "content": "用户不吃辣"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: mock_client)

        # 模拟已有高相似度记忆
        monkeypatch.setattr(
            "app.memory.retrieve_memories",
            lambda *args, **kwargs: [{"score": 0.95, "content": "用户不吃辣"}],
        )
        add_calls = []
        monkeypatch.setattr("app.memory.add_memory", lambda *args, **kwargs: add_calls.append(1) or "mem0001")

        result = extract_memories_from_plan("规划内容", "东京", 1000, "美食")
        assert len(add_calls) == 0  # 被去重跳过

    def test_extract_llm_error(self, mock_memory_deps, monkeypatch):
        from app.memory import extract_memories_from_plan

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API error")
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: mock_client)

        result = extract_memories_from_plan("规划内容", "东京", 1000, "美食")
        assert result == []

    def test_extract_invalid_memory_type(self, mock_memory_deps, monkeypatch):
        """无效的 memory_type 应该默认为 fact"""
        from app.memory import extract_memories_from_plan

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"type": "invalid_type", "content": "某条记忆"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: mock_client)
        monkeypatch.setattr("app.memory.retrieve_memories", lambda *args, **kwargs: [])

        captured = {}
        def mock_add(*args, **kwargs):
            captured.update(kwargs)
            return "mem0001"
        monkeypatch.setattr("app.memory.add_memory", mock_add)

        extract_memories_from_plan("规划内容", "东京", 1000, "美食")
        assert captured.get("memory_type") == "fact"

    def test_extract_missing_content(self, mock_memory_deps, monkeypatch):
        """缺少 content 的记忆应该被跳过"""
        from app.memory import extract_memories_from_plan

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps([
            {"type": "fact"},  # 缺少 content
            {"type": "fact", "content": "有效记忆"},
        ])

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        monkeypatch.setattr("openai.OpenAI", lambda **kwargs: mock_client)
        monkeypatch.setattr("app.memory.retrieve_memories", lambda *args, **kwargs: [])
        monkeypatch.setattr("app.memory.add_memory", lambda *args, **kwargs: "mem0001")

        result = extract_memories_from_plan("规划内容", "东京", 1000, "美食")
        assert len(result) == 1  # 只有有效的那条


# ── 集成：记忆注入 build_travel_crew ────────────────────────

class TestMemoryIntegration:
    def test_crew_imports_memory(self):
        """crew.py 应该能导入记忆模块"""
        from app.memory import retrieve_memories, extract_memories_from_plan, format_memories_for_context
        assert callable(retrieve_memories)
        assert callable(extract_memories_from_plan)
        assert callable(format_memories_for_context)
