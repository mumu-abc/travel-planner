"""
SQLite 持久化存储 - 线程安全的历史记录管理。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.config import settings, PROJECT_ROOT


class Database:
    """线程安全的 SQLite 数据库管理器"""

    def __init__(self, db_path: Optional[str] = None):
        path = db_path or settings.travel_db_path
        if not Path(path).is_absolute():
            path = str(PROJECT_ROOT / path)
        self.db_path = path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取线程本地的数据库连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._local.conn = sqlite3.connect(
                self.db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        """初始化数据库表"""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                destination TEXT NOT NULL,
                days INTEGER NOT NULL,
                budget REAL NOT NULL,
                interests TEXT NOT NULL,
                result TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_plans_created_at
            ON plans(created_at DESC)
        """)
        # 多轮对话表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES plans(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_plan_id
            ON conversations(plan_id, created_at)
        """)
        # 用户反馈表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES plans(id)
            )
        """)
        # 用户偏好记忆表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        # SSE 事件持久化表（断线重连用）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sse_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_data TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES plans(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sse_plan_id
            ON sse_events(plan_id, id)
        """)
        # 评测轮次表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_runs (
                id TEXT PRIMARY KEY,
                run_name TEXT,
                total_cases INTEGER,
                evaluated INTEGER,
                avg_overall REAL,
                avg_completeness REAL,
                avg_practicality REAL,
                avg_structure REAL,
                pass_rate REAL,
                avg_agreement REAL,
                summary_json TEXT,
                created_at TEXT
            )
        """)
        # 评测详情表
        conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                destination TEXT,
                days INTEGER,
                budget REAL,
                overall REAL,
                completeness REAL,
                practicality REAL,
                structure REAL,
                plan_length INTEGER,
                agreement REAL,
                judge_json TEXT,
                created_at TEXT,
                FOREIGN KEY (run_id) REFERENCES eval_runs(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_eval_scores_run_id
            ON eval_scores(run_id)
        """)
        conn.commit()

    def save_plan(
        self,
        destination: str,
        days: int,
        budget: float,
        interests: str,
        result: str,
    ) -> str:
        """保存旅行规划记录，返回 plan_id"""
        plan_id = uuid.uuid4().hex[:8]
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO plans (id, destination, days, budget, interests, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plan_id, destination, days, budget, interests, result, datetime.now().isoformat()),
        )
        conn.commit()
        return plan_id

    def get_history(
        self, limit: int = 20, offset: int = 0
    ) -> tuple[int, list[dict]]:
        """获取历史记录，返回 (总数, 记录列表)"""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM plans ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        records = [dict(row) for row in rows]
        return total, records

    def get_plan(self, plan_id: str) -> Optional[dict]:
        """根据 ID 获取规划记录"""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return dict(row) if row else None

    def create_plan_pending(self, destination: str, days: int, budget: float, interests: str) -> str:
        """创建一条待完成的规划记录（SSE 流式场景），返回 plan_id"""
        plan_id = uuid.uuid4().hex[:8]
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO plans (id, destination, days, budget, interests, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plan_id, destination, days, budget, interests, "", datetime.now().isoformat()),
        )
        conn.commit()
        return plan_id

    def update_plan_result(self, plan_id: str, result: str) -> None:
        """更新规划记录的结果"""
        conn = self._get_conn()
        conn.execute("UPDATE plans SET result = ? WHERE id = ?", (result, plan_id))
        conn.commit()

    def search_plans(self, destination: str, limit: int = 10) -> list[dict]:
        """按目的地搜索"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM plans WHERE destination LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{destination}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def cleanup_old_records(self, days: int = 30) -> int:
        """清理超过指定天数的旧记录（级联删除关联的对话、反馈、SSE 事件）"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._get_conn()
        # 先查出要删除的 plan_id，用于级联删除关联表
        old_plans = conn.execute(
            "SELECT id FROM plans WHERE created_at < ?", (cutoff,)
        ).fetchall()
        if not old_plans:
            return 0
        plan_ids = [row[0] for row in old_plans]
        placeholders = ",".join("?" * len(plan_ids))
        # 级联删除关联数据
        conn.execute(f"DELETE FROM conversations WHERE plan_id IN ({placeholders})", plan_ids)
        conn.execute(f"DELETE FROM feedback WHERE plan_id IN ({placeholders})", plan_ids)
        conn.execute(f"DELETE FROM sse_events WHERE plan_id IN ({placeholders})", plan_ids)
        cursor = conn.execute(f"DELETE FROM plans WHERE id IN ({placeholders})", plan_ids)
        conn.commit()
        return cursor.rowcount

    def delete_plan(self, plan_id: str) -> bool:
        """删除单条规划记录，返回是否真的删掉了。

        必须连带删掉关联的对话、反馈、SSE 事件：只删 plans 那行的话，
        追问记录和评分会变成「孤儿行」留在库里 —— 界面上看不见，
        但会一直占空间，而且如果以后按 plan_id 反查会查到脏数据。
        （与 cleanup_old_records 用的是同一套级联顺序。）
        """
        conn = self._get_conn()
        # 先确认存在，避免「删了 0 行」也返回成功让前端误判
        row = conn.execute("SELECT id FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM conversations WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM feedback WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM sse_events WHERE plan_id = ?", (plan_id,))
        conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
        conn.commit()
        return True

    # ── 多轮对话方法 ──────────────────────────────────────

    def save_conversation(self, plan_id: str, role: str, content: str) -> str:
        """保存一条对话记录，返回 record_id"""
        record_id = uuid.uuid4().hex[:8]
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO conversations (id, plan_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (record_id, plan_id, role, content, datetime.now().isoformat()),
        )
        conn.commit()
        return record_id

    def get_conversation_history(self, plan_id: str) -> list[dict]:
        """获取指定规划的完整对话历史"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM conversations WHERE plan_id = ? ORDER BY created_at ASC",
            (plan_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        """关闭数据库连接"""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    # ── 反馈方法 ──────────────────────────────────────────

    def save_feedback(self, plan_id: str, rating: int, comment: str = "") -> str:
        """保存用户反馈"""
        feedback_id = uuid.uuid4().hex[:8]
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO feedback (id, plan_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)",
            (feedback_id, plan_id, rating, comment, datetime.now().isoformat()),
        )
        conn.commit()
        return feedback_id

    def get_feedback(self, plan_id: str) -> list[dict]:
        """获取指定规划的反馈"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM feedback WHERE plan_id = ? ORDER BY created_at DESC", (plan_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_feedback_by_destination(self, destination: str, limit: int = 10) -> list[dict]:
        """获取指定目的地的历史反馈（关联 plans 表）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT f.*, p.destination FROM feedback f "
            "JOIN plans p ON f.plan_id = p.id "
            "WHERE p.destination LIKE ? ORDER BY f.created_at DESC LIMIT ?",
            (f"%{destination}%", limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_feedback_stats(self) -> list[dict]:
        """获取各目的地的反馈统计"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT p.destination, COUNT(*) as count, AVG(f.rating) as avg_rating "
            "FROM feedback f JOIN plans p ON f.plan_id = p.id "
            "GROUP BY p.destination ORDER BY avg_rating DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    # ── 偏好记忆方法 ──────────────────────────────────────

    def set_preference(self, key: str, value: str) -> None:
        """设置用户偏好"""
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO user_preferences (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.now().isoformat()),
        )
        conn.commit()

    def get_preference(self, key: str) -> Optional[str]:
        """获取用户偏好"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT value FROM user_preferences WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def get_all_preferences(self) -> dict:
        """获取所有用户偏好"""
        conn = self._get_conn()
        rows = conn.execute("SELECT key, value FROM user_preferences").fetchall()
        return {row[0]: row[1] for row in rows}

    # ── SSE 事件持久化方法 ──────────────────────────────────

    def save_sse_event(self, plan_id: str, event_type: str, event_data: dict) -> None:
        """保存 SSE 事件（用于断线重连）"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO sse_events (plan_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?)",
            (plan_id, event_type, json.dumps(event_data, ensure_ascii=False), datetime.now().isoformat()),
        )
        conn.commit()

    def get_sse_events(self, plan_id: str, after_id: int = 0) -> list[dict]:
        """获取 SSE 事件（断线重连用）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, event_type, event_data, created_at FROM sse_events WHERE plan_id = ? AND id > ? ORDER BY id ASC",
            (plan_id, after_id),
        ).fetchall()
        return [
            {"id": row[0], "type": row[1], "data": json.loads(row[2]), "created_at": row[3]}
            for row in rows
        ]

    def cleanup_old_sse_events(self, days: int = 7) -> int:
        """清理旧的 SSE 事件"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn = self._get_conn()
        cursor = conn.execute("DELETE FROM sse_events WHERE created_at < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount

    # ── 评测方法 ──────────────────────────────────────────

    def save_eval_run(self, eval_result: dict) -> str:
        """保存一轮评测结果，返回 run_id"""
        run_id = eval_result["run_id"]
        summary = eval_result.get("summary", {})
        conn = self._get_conn()

        conn.execute(
            "INSERT INTO eval_runs (id, run_name, total_cases, evaluated, "
            "avg_overall, avg_completeness, avg_practicality, avg_structure, "
            "pass_rate, avg_agreement, summary_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                eval_result.get("run_name", ""),
                summary.get("total_cases", 0),
                summary.get("evaluated", 0),
                summary.get("avg_overall", 0),
                summary.get("avg_completeness", 0),
                summary.get("avg_practicality", 0),
                summary.get("avg_structure", 0),
                summary.get("pass_rate", 0),
                summary.get("avg_agreement", 0),
                json.dumps(eval_result, ensure_ascii=False),
                eval_result.get("timestamp", datetime.now().isoformat()),
            ),
        )

        for case in eval_result.get("cases", []):
            conn.execute(
                "INSERT INTO eval_scores (run_id, case_id, destination, days, budget, "
                "overall, completeness, practicality, structure, plan_length, agreement, "
                "judge_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    case["case_id"],
                    case.get("destination", ""),
                    case.get("days", 0),
                    case.get("budget", 0),
                    case["scores"]["overall"],
                    case["scores"]["completeness"],
                    case["scores"]["practicality"],
                    case["scores"]["structure"],
                    case.get("plan_length", 0),
                    case.get("agreement", 0),
                    json.dumps(case.get("judge_results", []), ensure_ascii=False),
                    eval_result.get("timestamp", datetime.now().isoformat()),
                ),
            )

        conn.commit()
        return run_id

    def get_eval_runs(self, limit: int = 20) -> list[dict]:
        """获取历史评测列表"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_eval_run(self, run_id: str) -> Optional[dict]:
        """获取单次评测详情"""
        conn = self._get_conn()
        run_row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if not run_row:
            return None

        run = dict(run_row)
        score_rows = conn.execute(
            "SELECT * FROM eval_scores WHERE run_id = ? ORDER BY case_id",
            (run_id,),
        ).fetchall()
        run["cases"] = [dict(r) for r in score_rows]

        # 解析 summary_json
        if run.get("summary_json"):
            try:
                full = json.loads(run["summary_json"])
                run["summary"] = full.get("summary", {})
            except Exception:
                pass

        return run

    def get_latest_eval_run(self) -> Optional[dict]:
        """获取最近一次评测"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# 全局数据库实例
db = Database()
