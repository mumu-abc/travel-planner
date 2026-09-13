"""
用户记忆系统 — 向量存储 + 语义检索 + LLM 自动提取。

记忆类型：
  - fact: 用户事实（如"不吃辣"、"喜欢动漫"、"带小孩出行"）
  - interaction: 交互摘要（如"去过东京3天，主要逛了寺庙和美食街"）
  - preference: 显式偏好（如"偏好经济型住宿"）

存储：
  - SQLite memories 表存原始文本 + 元数据
  - FAISS 索引存向量（复用 bge-small-zh-v1.5 模型）
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── 全局单例（延迟初始化）────────────────────────────────────
_model = None
_index = None
_index_dirty = False  # 删除记忆后标记为 True，下次写入时重建索引
_memory_dim = 512  # bge-small-zh-v1.5 输出维度


def _get_model():
    """延迟加载 embedding 模型"""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("BAAI/bge-small-zh-v1.5")
    return _model


def _get_index():
    """延迟加载或创建 FAISS 索引（脏标记时自动重建）"""
    global _index, _index_dirty

    import faiss

    # 如果标记为脏（删除过记忆），从 SQLite 重建索引
    if _index_dirty and _index is not None:
        _rebuild_index()
        _index_dirty = False
        return _index

    if _index is not None:
        return _index

    index_path = _index_path()
    if index_path.exists():
        try:
            byte_array = index_path.read_bytes()
            _index = faiss.deserialize_index(np.frombuffer(byte_array, dtype=np.uint8))
            logger.info(f"记忆索引已加载: {_index.ntotal} 条向量")
        except Exception as e:
            logger.warning(f"记忆索引加载失败，重建: {e}")
            _index = faiss.IndexFlatIP(_memory_dim)
    else:
        _index = faiss.IndexFlatIP(_memory_dim)
    return _index


def _rebuild_index():
    """从 SQLite 重建 FAISS 索引（删除记忆后调用）"""
    import faiss

    global _index
    logger.info("正在重建 FAISS 记忆索引...")

    db = _get_db()
    conn = db._get_conn()
    _init_memories_table(conn)

    rows = conn.execute("SELECT id, content FROM memories ORDER BY created_at ASC").fetchall()
    _index = faiss.IndexFlatIP(_memory_dim)

    if rows:
        model = _get_model()
        texts = [row[1] for row in rows]
        vecs = model.encode(texts, normalize_embeddings=True).astype("float32")
        _index.add(vecs)
        # 更新 embedding_id 映射
        for new_idx, row in enumerate(rows):
            conn.execute("UPDATE memories SET embedding_id = ? WHERE id = ?", (new_idx, row[0]))
        conn.commit()

    _save_index()
    logger.info(f"FAISS 记忆索引重建完成: {_index.ntotal} 条向量")


def _index_path() -> Path:
    from app.config import PROJECT_ROOT
    return PROJECT_ROOT / "data" / "memory_index" / "memory.faiss"


def _save_index():
    """持久化 FAISS 索引（直接序列化 _index，避免 _get_index() 的竞态）"""
    import faiss
    if _index is None:
        return
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    byte_array = faiss.serialize_index(_index)
    path.write_bytes(byte_array)


# ── 数据库操作 ────────────────────────────────────────────

def _get_db():
    from app.database import db
    return db


def _init_memories_table(conn):
    """确保 memories 表存在"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            destination TEXT,
            tags TEXT,
            embedding_id INTEGER,
            created_at TEXT NOT NULL,
            last_accessed TEXT,
            access_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_memories_destination ON memories(destination)
    """)
    conn.commit()


# ── 记忆 CRUD ─────────────────────────────────────────────

def add_memory(
    content: str,
    memory_type: str = "fact",
    destination: str = "",
    tags: str = "",
) -> str:
    """
    存储一条记忆并建立向量索引。

    Args:
        content: 记忆内容
        memory_type: fact / interaction / preference
        destination: 关联目的地（可选）
        tags: 标签，逗号分隔（可选）

    Returns:
        memory_id
    """
    import uuid
    import faiss

    db = _get_db()
    conn = db._get_conn()
    _init_memories_table(conn)

    memory_id = uuid.uuid4().hex[:8]

    # 编码并存入 FAISS
    model = _get_model()
    vec = model.encode([content], normalize_embeddings=True).astype("float32")
    index = _get_index()
    embedding_id = index.ntotal
    index.add(vec)
    _save_index()

    # 存入 SQLite
    conn.execute(
        "INSERT INTO memories (id, content, memory_type, destination, tags, embedding_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (memory_id, content, memory_type, destination, tags, embedding_id, datetime.now().isoformat()),
    )
    conn.commit()

    logger.info(f"💾 新增记忆 [{memory_type}]: {content[:60]}...")
    return memory_id


def retrieve_memories(
    query: str,
    top_k: int = 5,
    memory_type: Optional[str] = None,
    destination: Optional[str] = None,
) -> list[dict]:
    """
    语义检索相关记忆。

    Args:
        query: 查询文本
        top_k: 返回数量
        memory_type: 限定记忆类型
        destination: 限定目的地

    Returns:
        [{"id", "content", "memory_type", "destination", "tags", "score", "created_at"}, ...]
    """
    db = _get_db()
    conn = db._get_conn()
    _init_memories_table(conn)

    index = _get_index()
    if index.ntotal == 0:
        return []

    # 编码查询
    model = _get_model()
    query_vec = model.encode([query], normalize_embeddings=True).astype("float32")

    # FAISS 检索（多取一些再过滤）
    search_k = min(top_k * 3, index.ntotal)
    scores, indices = index.search(query_vec, search_k)

    # 从 SQLite 获取记忆详情
    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0:
            continue

        row = conn.execute(
            "SELECT * FROM memories WHERE embedding_id = ?", (int(idx),)
        ).fetchone()

        if not row:
            continue

        mem = dict(row)

        # 过滤
        if memory_type and mem["memory_type"] != memory_type:
            continue
        if destination and destination not in (mem.get("destination") or ""):
            continue

        # 更新访问记录
        conn.execute(
            "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (datetime.now().isoformat(), mem["id"]),
        )

        mem["score"] = round(float(score), 4)
        results.append(mem)

        if len(results) >= top_k:
            break

    conn.commit()
    return results


def get_all_memories(memory_type: Optional[str] = None, limit: int = 50) -> list[dict]:
    """获取所有记忆（按时间倒序）"""
    db = _get_db()
    conn = db._get_conn()
    _init_memories_table(conn)

    if memory_type:
        rows = conn.execute(
            "SELECT * FROM memories WHERE memory_type = ? ORDER BY created_at DESC LIMIT ?",
            (memory_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_memory(memory_id: str) -> bool:
    """删除一条记忆（标记 FAISS 索引为脏，下次写入时自动重建）"""
    global _index_dirty
    db = _get_db()
    conn = db._get_conn()
    cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    if cursor.rowcount > 0:
        _index_dirty = True
    return cursor.rowcount > 0


# ── LLM 自动提取 ────────────────────────────────────────────

def extract_memories_from_plan(
    plan_text: str,
    destination: str,
    budget: float,
    interests: str,
) -> list[str]:
    """
    用 LLM 从生成结果中自动提取用户偏好和事实。

    返回提取的记忆列表（已存入数据库）。
    """
    from openai import OpenAI
    from app.config import settings

    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    extract_prompt = (
        "分析以下旅行规划方案，提取关于用户的偏好和事实。\n"
        "只提取明确可推断的信息，不要猜测。\n\n"
        "输出 JSON 数组，每条记忆包含 type 和 content：\n"
        '  - type: "fact"（用户事实，如饮食禁忌、同行人）\n'
        '  - type: "preference"（偏好，如住宿风格、消费习惯）\n'
        '  - type: "interaction"（本次交互摘要，一句话概括）\n\n'
        "示例：\n"
        '[{"type":"fact","content":"用户对海鲜过敏"},'
        '{"type":"preference","content":"偏好经济型住宿，日均预算50美元"},'
        '{"type":"interaction","content":"2024-08 规划了东京3天美食之旅"}]\n\n'
        "如果没有可提取的信息，返回空数组 []。\n"
        "只输出 JSON，不要解释。"
    )

    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": extract_prompt},
                {"role": "user", "content": f"目的地：{destination}\n预算：${budget}\n兴趣：{interests}\n\n规划方案：\n{plan_text[:3000]}"},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        text = resp.choices[0].message.content or ""

        # 提取 JSON 数组
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            return []

        memories_raw = json.loads(match.group())
        saved = []

        for mem in memories_raw:
            if not isinstance(mem, dict) or "content" not in mem:
                continue
            mem_type = mem.get("type", "fact")
            if mem_type not in ("fact", "preference", "interaction"):
                mem_type = "fact"

            # 去重：检查是否已有相似记忆
            existing = retrieve_memories(mem["content"], top_k=1)
            if existing and existing[0].get("score", 0) > 0.85:
                logger.debug(f"跳过重复记忆: {mem['content'][:40]}...")
                continue

            memory_id = add_memory(
                content=mem["content"],
                memory_type=mem_type,
                destination=destination if mem_type != "interaction" else "",
                tags=destination,
            )
            saved.append(memory_id)

        logger.info(f"🧠 从规划中提取了 {len(saved)} 条新记忆")
        return saved

    except Exception as e:
        logger.warning(f"记忆提取失败: {e}")
        return []


# ── 格式化记忆供 Agent 使用 ────────────────────────────────

def format_memories_for_context(memories: list[dict]) -> str:
    """将检索到的记忆格式化为 Agent 可读的上下文文本"""
    if not memories:
        return ""

    lines = ["【用户记忆】"]
    for m in memories:
        type_label = {"fact": "事实", "preference": "偏好", "interaction": "历史"}.get(m["memory_type"], "记忆")
        dest = f"（{m['destination']}）" if m.get("destination") else ""
        lines.append(f"- [{type_label}]{dest} {m['content']}")

    return "\n".join(lines)
