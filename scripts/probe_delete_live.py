"""对正在运行的后端做一次「删除历史记录」端到端体检。

做四件事：
1. 往库里造一条带关联子行的测试记录
2. 通过真实 HTTP 调用 DELETE 删掉它
3. 再删一次，确认返回 404（而不是 405 / 500）
4. 确认 plans / conversations / feedback / sse_events 里都没有残留

用法：
    python scripts/probe_delete_live.py
"""
import os
import sqlite3
import sys
import urllib.error
import urllib.request

# 本机回环地址不要走代理，否则会被代理拦成 502
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
           "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

BASE = "http://127.0.0.1:8000"
PID = "ZZPROBE01"
DB = settings.travel_db_path
CHILD_TABLES = ("conversations", "feedback", "sse_events")


def call(method, path):
    req = urllib.request.Request(
        BASE + path, method=method,
        headers={"User-Agent": "probe", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8")[:160]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")[:160]
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def count_by_plan(conn, table):
    col = "id" if table == "plans" else "plan_id"
    return conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (PID,)
    ).fetchone()[0]


def main():
    conn = sqlite3.connect(DB)
    # 先清干净，避免上一轮残留干扰
    for t in CHILD_TABLES:
        conn.execute(f"DELETE FROM {t} WHERE plan_id = ?", (PID,))
    conn.execute("DELETE FROM plans WHERE id = ?", (PID,))

    conn.execute(
        "INSERT INTO plans (id, destination, days, budget, interests, result,"
        " created_at) VALUES (?,?,?,?,?,?,datetime('now'))",
        (PID, "探针城市", 3, 1000, "[]", "{}"),
    )
    conn.execute(
        "INSERT INTO conversations (id, plan_id, role, content, created_at)"
        " VALUES (?,?,?,?,datetime('now'))",
        ("ZP_C", PID, "user", "hi"),
    )
    conn.execute(
        "INSERT INTO feedback (id, plan_id, rating, comment, created_at)"
        " VALUES (?,?,?,?,datetime('now'))",
        ("ZP_F", PID, 5, "ok"),
    )
    conn.execute(
        "INSERT INTO sse_events (plan_id, event_type, event_data, created_at)"
        " VALUES (?,?,?,datetime('now'))",
        (PID, "status", "{}"),
    )
    conn.commit()
    seeded = {t: count_by_plan(conn, t) for t in CHILD_TABLES}
    print(f"1) 造数据: plans=1, 关联子行 = {seeded}")
    conn.close()

    print(f"2) DELETE /api/history/{PID}        ->", call("DELETE", f"/api/history/{PID}"))
    print(f"3) 再 DELETE 一次（期望 404）        ->", call("DELETE", f"/api/history/{PID}"))
    print("4) GET  /api/history/search         ->",
          call("GET", "/api/history/search?destination=%E6%8E%A2%E9%92%88"))
    print(f"5) GET  /api/history/{PID}（期望 404）->", call("GET", f"/api/history/{PID}"))

    conn = sqlite3.connect(DB)
    left = {"plans": count_by_plan(conn, "plans")}
    left.update({t: count_by_plan(conn, t) for t in CHILD_TABLES})
    conn.close()
    print(f"6) 删除后残留 -> {left}")

    clean = all(v == 0 for v in left.values())
    print("结论:", "全部清理干净 ✅" if clean else "仍有残留 ❌")


if __name__ == "__main__":
    main()
