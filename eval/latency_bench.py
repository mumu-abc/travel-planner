"""
骨架优先延迟基准 — 量化「先骨架后填充」的首屏体感。

只打 skeleton_only=true 的请求（不调 LLM、零成本），测量真实 HTTP+SSE 链路上：
  - ttfb      : 首字节延迟（连接建立 + status 事件）
  - skeleton  : 骨架事件完整到达（用户看到可读行程的时刻）
  - total     : 到 [DONE]

用法：
    python -m eval.latency_bench --n 20 --concurrency 4
    python -m eval.latency_bench --n 10 --concurrency 1 --output eval/report_latency.md

结果写入 eval/report_latency.md（P50/P95 等），可直接引用到简历/面试。
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_BODY = {
    "destination": "东京, 日本",
    "days": 3,
    "budget": 1500,
    "interests": "美食,动漫",
    "skeleton_only": True,
}


def _wait_health(client: httpx.Client, url: str, timeout_s: int = 180) -> None:
    """等服务就绪（首次启动要加载 FAISS + bge 嵌入模型）。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = client.get(url, timeout=5)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"服务 {timeout_s}s 内未就绪")


def measure_once(client: httpx.Client, url: str, body: dict) -> dict:
    """发一次 SSE 请求，返回 {ttfb, skeleton, total} 毫秒。"""
    t0 = time.perf_counter()
    ttfb = skeleton = total = None
    with client.stream("POST", url, json=body, timeout=60) as resp:
        buf_type = None
        for line in resp.iter_lines():
            now = (time.perf_counter() - t0) * 1000
            if line.startswith("data: "):
                payload = line[6:]
                if ttfb is None:
                    ttfb = now
                if payload == "[DONE]":
                    total = now
                    break
                try:
                    evt = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if evt.get("type") == "skeleton" and skeleton is None:
                    skeleton = now
                    buf_type = "skeleton"
    if skeleton is None:
        skeleton = total  # 未拿到骨架事件视为与总时长相同
    if total is None:
        total = (time.perf_counter() - t0) * 1000
    return {"ttfb": ttfb or 0.0, "skeleton": skeleton, "total": total}


def _pctl(values: list[float], p: float) -> float:
    """线性插值百分位。"""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def run_bench(n: int, concurrency: int, port: int) -> dict:
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    results: list[dict] = []
    try:
        with httpx.Client() as probe:
            _wait_health(probe, f"{base}/api/health")
            # 预热 1 次：模型/索引加载不计入统计
            measure_once(probe, f"{base}/api/plan/stream", DEFAULT_BODY)

        t_start = time.perf_counter()
        if concurrency <= 1:
            with httpx.Client() as client:
                for i in range(n):
                    results.append(measure_once(client, f"{base}/api/plan/stream", DEFAULT_BODY))
                    print(f"  [{i + 1}/{n}] skeleton={results[-1]['skeleton']:.0f}ms")
        else:
            import concurrent.futures as cf

            with httpx.Client() as client:
                with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
                    futs = [ex.submit(measure_once, client, f"{base}/api/plan/stream", DEFAULT_BODY) for _ in range(n)]
                    done = 0
                    for f in cf.as_completed(futs):
                        results.append(f.result())
                        done += 1
                        print(f"  [{done}/{n}] skeleton={results[-1]['skeleton']:.0f}ms")
        wall = time.perf_counter() - t_start
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    return {
        "n": len(results),
        "concurrency": concurrency,
        "wall_s": round(wall, 2),
        "ttfb": [r["ttfb"] for r in results],
        "skeleton": [r["skeleton"] for r in results],
        "total": [r["total"] for r in results],
    }


def _stats_row(name: str, values: list[float]) -> str:
    if not values:
        return f"| {name} | - | - | - | - | - |"
    return (
        f"| {name} | {statistics.mean(values):.0f} | {_pctl(values, 50):.0f} "
        f"| {_pctl(values, 95):.0f} | {min(values):.0f} | {max(values):.0f} |"
    )


def generate_report(r: dict, model_hint: str = "bge-small-zh-v1.5 (本地)") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 骨架优先延迟基准（skeleton-first latency）",
        "",
        f"- **时间**: {now}",
        f"- **请求数**: {r['n']}（另有 1 次预热不计入）",
        f"- **并发**: {r['concurrency']}",
        f"- **端点**: POST /api/plan/stream (SSE), `skeleton_only=true`（纯本地，不调 LLM）",
        f"- **嵌入模型**: {model_hint}",
        "",
        "用户点「生成」到看到可读行程的时刻 = skeleton P95；完整 Agent 填充的耗时另见消融报告。",
        "",
        "| 指标 | 均值(ms) | P50(ms) | P95(ms) | min(ms) | max(ms) |",
        "|------|----------|---------|---------|---------|---------|",
        _stats_row("首字节 TTFB", r["ttfb"]),
        _stats_row("**骨架事件（可读行程可见）**", r["skeleton"]),
        _stats_row("请求完成 total", r["total"]),
        "",
        f"- 墙钟总耗时: {r['wall_s']}s（{r['n']} 请求 × 并发 {r['concurrency']}）",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="骨架优先延迟基准")
    parser.add_argument("--n", type=int, default=20, help="压测请求数")
    parser.add_argument("--concurrency", type=int, default=4, help="并发数")
    parser.add_argument("--port", type=int, default=8765, help="临时服务端口")
    parser.add_argument("--output", type=str, default="eval/report_latency.md")
    args = parser.parse_args()

    print(f"🚀 启动临时服务并预热（首次加载 FAISS + 嵌入模型）...")
    r = run_bench(args.n, args.concurrency, args.port)
    report = generate_report(r)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n报告已生成: {out}")


if __name__ == "__main__":
    main()
