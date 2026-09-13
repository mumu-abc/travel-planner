"""
独立评测运行器 — 支持单 Agent baseline 与消融对比。

用法：
    python -m eval.eval_runner --cases 3
    python -m eval.eval_runner --cases 3 --mode multi
    python -m eval.eval_runner --cases 3 --ablation   # multi vs single vs sequential
    python -m eval.eval_runner --cases 3 --no-reflection
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings

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

REQUIRED_KEYWORDS = {
    "has_weather": ["温度", "天气", "°C", "预报"],
    "has_attractions": ["景点", "门票", "游览"],
    "has_budget": ["$", "预算", "美元", "住宿"],
    "has_food": ["餐厅", "美食", "推荐", "人均"],
    "has_safety": ["安全", "紧急", "电话"],
}


def check_keywords(content: str) -> dict:
    results = {}
    for key, keywords in REQUIRED_KEYWORDS.items():
        results[key] = any(kw in content for kw in keywords)
    return results


def keyword_coverage(content: str) -> float:
    results = check_keywords(content)
    return sum(results.values()) / len(results)


JUDGES = [
    {
        "name": "completeness",
        "role": "完整性",
        "prompt": (
            "评审旅行计划完整性（景点/天气/行程/预算/美食/安全）。"
            "只输出JSON，不要解释：{\"score\":8,\"reason\":\"...\"}"
        ),
    },
    {
        "name": "practicality",
        "role": "实用性",
        "prompt": "评审旅行实用性(景点/餐厅/交通/价格)。只输出JSON：{\"score\":7,\"reason\":\"一句话\"}",
    },
    {
        "name": "structure",
        "role": "结构化",
        "prompt": (
            "评审旅行计划结构（标题/分段/列表/长度>1500字/可读性）。"
            "只输出JSON，不要解释：{\"score\":7,\"reason\":\"...\"}"
        ),
    },
]


def _strip_think(text: str) -> str:
    """剥离思考模型的 <think>...</think> 块，避免干扰 JSON 提取。"""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_json_score(text: str) -> Optional[dict]:
    """从评委回复中尽力提取 JSON（code block / 裸对象 / score 数字）。"""
    code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_match:
        try:
            return json.loads(code_match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r'\{[^{}]*"score"\s*:\s*\d+[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    score_match = re.search(r'(?:score|"score"|分数|评分)["\s:：]*(\d+)', text)
    if score_match:
        return {"score": int(score_match.group(1)), "reason": text[:100]}
    return None


def judge_score(client: OpenAI, judge: dict, content: str, max_attempts: int = 2) -> dict:
    """
    单评委打分。返回 {"score": int|None, "reason": str, "status": "ok"|"failed"}。
    失败（API 异常 / 无法解析）时 score=None，绝不伪造分数。
    """
    last_err = ""
    for attempt in range(max_attempts):
        try:
            resp = client.chat.completions.create(
                model=settings.llm_model,
                messages=[
                    {"role": "system", "content": judge["prompt"]},
                    {"role": "user", "content": f"以下是旅行计划：\n\n{content[:4000]}"},
                ],
                temperature=0.1,
                max_tokens=2048,
            )
            text = _strip_think(resp.choices[0].message.content or "")
            parsed = _extract_json_score(text)
            if parsed is not None:
                score = int(parsed.get("score", -1))
                if 0 <= score <= 10:
                    return {"score": score, "reason": str(parsed.get("reason", ""))[:200], "status": "ok"}
            last_err = f"无法从评委回复提取分数: {text[:120]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:150]}"
        if attempt < max_attempts - 1:
            time.sleep(2 * (attempt + 1))  # 429/瞬断退避后重试

    return {"score": None, "reason": last_err, "status": "failed"}


def evaluate_plan(client: OpenAI, content: str) -> dict:
    """三评委评分。全部评委失败时 status=judge_failed，不产出伪造分数。"""
    scores = {}
    for judge in JUDGES:
        scores[judge["name"]] = judge_score(client, judge, content)

    valid = [s["score"] for s in scores.values() if s["status"] == "ok" and s["score"] is not None]
    all_failed = not valid
    if all_failed:
        median_score = 0
    else:
        valid.sort()
        median_score = valid[len(valid) // 2]

    kw_cov = keyword_coverage(content)

    return {
        "judges": scores,
        "median": median_score,
        "overall": round(median_score * 10, 1),
        "keyword_coverage": round(kw_cov * 100, 1),
        "char_count": len(content),
        "judges_ok": len(valid),
        "status": "judge_failed" if all_failed else "ok",
    }


# ── 降级/失败识别：这些情况不得计入「成功」，否则平均分被污染 ──

_OFFLINE_MARKERS = ("AI 服务暂时不可用", "离线版", "基础信息，仅供参考")
_AGENT_FAIL_MARKER = "生成失败"


def _classify_run(content: str, usage: dict, ev: dict) -> str:
    """
    根据产出与用量判定 run 状态：
      ok              — 正常生成且评委可打分
      no_llm          — 0 次 LLM 调用（走了本地离线降级，测的不是 Agent）
      degraded_offline— 内容带离线降级标记
      judge_failed    — 三个评委全部失败（配额/解析），无法评分
      agent_failed    — 所有 Agent 产出均为失败占位
    """
    if usage.get("llm_calls", 0) == 0:
        return "no_llm"
    if any(m in content for m in _OFFLINE_MARKERS):
        return "degraded_offline"
    if ev.get("status") == "judge_failed":
        return "judge_failed"
    if content.count(_AGENT_FAIL_MARKER) >= 3:
        return "agent_failed"
    return "ok"


def run_one(client: OpenAI, case: dict, mode: str, enable_reflection: bool) -> dict:
    from app.crew import build_travel_crew
    from app.usage import get_usage, estimate_cost_usd

    usage = get_usage()
    usage.reset()

    print(f"    [{mode}{' /no-refl' if not enable_reflection else ''}] {case['id']} ...", end=" ", flush=True)
    start = time.time()
    try:
        content = build_travel_crew(
            case["destination"], case["days"], case["budget"], case["interests"],
            language="中文",
            mode=mode,
            enable_reflection=enable_reflection,
            enable_routing=(mode == "multi"),
        )
        elapsed = round(time.time() - start, 1)
        ev = evaluate_plan(client, content)
        snap = usage.snapshot()
        snap["approx_cost_usd"] = round(
            estimate_cost_usd(snap["approx_input_tokens"], snap["approx_output_tokens"]), 4
        )
        status = _classify_run(content, snap, ev)
        reason = ""
        if status != "ok":
            reason = next(
                (s["reason"] for s in ev.get("judges", {}).values() if s.get("status") == "failed"),
                status,
            )
            print(f"{status.upper()} | {reason[:80]} | {elapsed}s")
            return {
                "case": case,
                "mode": mode,
                "enable_reflection": enable_reflection,
                "content": content,
                "eval": ev,
                "elapsed": elapsed,
                "usage": snap,
                "status": status,
                "reason": reason,
            }
        print(
            f"OK | score={ev['overall']} | kw={ev['keyword_coverage']}% | "
            f"{elapsed}s | ~{snap['approx_total_tokens']}tok | ${snap['approx_cost_usd']}"
        )
        return {
            "case": case,
            "mode": mode,
            "enable_reflection": enable_reflection,
            "content": content,
            "eval": ev,
            "elapsed": elapsed,
            "usage": snap,
            "status": "ok",
        }
    except Exception as e:
        elapsed = round(time.time() - start, 1)
        print(f"FAIL | {e} | {elapsed}s")
        return {
            "case": case,
            "mode": mode,
            "enable_reflection": enable_reflection,
            "content": "",
            "eval": {"overall": 0, "keyword_coverage": 0, "char_count": 0, "judges": {}, "status": "error"},
            "elapsed": elapsed,
            "usage": {},
            "status": "error",
            "reason": f"{type(e).__name__}: {str(e)[:150]}",
        }


def _agg(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "ok"]
    scores = [r["eval"]["overall"] for r in ok]
    mean = sum(scores) / len(scores) if scores else 0.0
    if len(scores) >= 2:
        var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1)
        std = var ** 0.5
    else:
        std = 0.0
    if not ok:
        return {"n": 0, "score": 0, "std": 0, "kw": 0, "chars": 0, "time": 0, "tokens": 0, "cost": 0, "llm": 0, "tools": 0}
    toks = [r.get("usage", {}).get("approx_total_tokens", 0) for r in ok]
    costs = [r.get("usage", {}).get("approx_cost_usd", 0) for r in ok]
    llms = [r.get("usage", {}).get("llm_calls", 0) for r in ok]
    tools = [r.get("usage", {}).get("tool_calls", 0) for r in ok]
    return {
        "n": len(ok),
        "score": mean,
        "std": std,
        "kw": sum(r["eval"]["keyword_coverage"] for r in ok) / len(ok),
        "chars": sum(r["eval"]["char_count"] for r in ok) / len(ok),
        "time": sum(r["elapsed"] for r in ok) / len(ok),
        "tokens": sum(toks) / len(ok) if toks else 0,
        "cost": sum(costs) / len(ok) if costs else 0,
        "llm": sum(llms) / len(llms) if llms else 0,
        "tools": sum(tools) / len(tools) if tools else 0,
    }


def run_eval(
    num_cases: int = 15,
    mode: str = "multi",
    enable_reflection: bool = True,
    ablation: bool = False,
) -> str:
    """
    ablation=True 时跑 multi / single / sequential 三组，输出对比表。
    """
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    cases = EVAL_CASES[:num_cases]

    if ablation:
        variants = [
            ("multi", True),
            ("single", True),
            ("sequential", True),
            ("multi", False),  # 去反思
        ]
    else:
        variants = [(mode, enable_reflection)]

    all_rows: list[dict] = []
    print(f"\n{'='*60}")
    print(f"  评测开始 | {len(cases)} case × {len(variants)} 变体 | 模型: {settings.llm_model}")
    print(f"{'='*60}\n")

    for m, refl in variants:
        print(f"\n▶ 变体 mode={m} reflection={'on' if refl else 'off'}")
        for i, case in enumerate(cases, 1):
            print(f"[{i}/{len(cases)}]", end=" ")
            all_rows.append(run_one(client, case, m, refl))

    return _generate_report(all_rows, settings.llm_model, ablation=ablation)


def _generate_report(results: list[dict], model: str, ablation: bool = False) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 评测报告",
        "",
        f"- **时间**: {now}",
        f"- **模型**: {model}",
        f"- **用例数**: {len({r['case']['id'] for r in results})}",
        f"- **是否消融**: {'是' if ablation else '否'}",
        "",
    ]

    # ── 汇总 ──
    groups: dict[str, list[dict]] = {}
    for r in results:
        key = f"{r['mode']}|refl={'on' if r['enable_reflection'] else 'off'}"
        groups.setdefault(key, []).append(r)

    lines.extend([
        "## 配置对比",
        "",
        "只有 `status=ok`（正常生成 + 评委可打分）的 case 计入平均分；离线降级 / 评委失败 / Agent 全失败单独列在「失败与降级明细」。",
        "",
        "| 配置 | 成功 | 平均分 | 波动σ | 关键词 | 平均长度 | 平均耗时 | ~Token | ~成本$ | LLM次 | 工具次 |",
        "|------|------|--------|--------|--------|----------|----------|--------|--------|-------|--------|",
    ])
    for key, rows in groups.items():
        a = _agg(rows)
        score_cell = f"**{a['score']:.1f}**" if a["n"] else "—"
        lines.append(
            f"| {key} | {a['n']}/{len(rows)} | {score_cell} | ±{a['std']:.1f} | {a['kw']:.1f}% | "
            f"{a['chars']:.0f} | {a['time']:.1f}s | {a['tokens']:.0f} | {a['cost']:.4f} | "
            f"{a['llm']:.1f} | {a['tools']:.1f} |"
        )

    if ablation and "multi|refl=on" in groups and "single|refl=on" in groups:
        multi = _agg(groups["multi|refl=on"])
        single = _agg(groups["single|refl=on"])
        if multi["n"] and single["n"]:
            delta = multi["score"] - single["score"]
            lines.extend([
                "",
                "## 关键结论",
                "",
                f"- multi vs single 平均分差：**{delta:+.1f}**（正数表示多 Agent 更优；n 分别为 {multi['n']}/{single['n']}）",
                f"- multi 平均耗时 {multi['time']:.0f}s vs single {single['time']:.0f}s",
                f"- multi ~成本 ${multi['cost']:.4f} vs single ${single['cost']:.4f}",
                "- n<3 时分差不具备统计意义，请扩大 case 数后复测，不要只报最好一次。",
                "- Token/成本为字符粗估，仅用于横向对比，不是账单。",
            ])
        else:
            lines.extend([
                "",
                "## 关键结论",
                "",
                "> ⚠️ multi 或 single 本轮无有效成功样本（见失败明细），无法给出质量结论。请恢复配额后复测。",
            ])

    # ── 失败与降级明细 ──
    bad_rows = [r for r in results if r["status"] != "ok"]
    if bad_rows:
        lines.extend([
            "",
            "## 失败与降级明细",
            "",
            "以下 case 未计入平均分（避免污染结论）：",
            "",
            "| 配置 | Case | 状态 | 原因 |",
            "|------|------|------|------|",
        ])
        for r in bad_rows:
            key = f"{r['mode']}|refl={'on' if r['enable_reflection'] else 'off'}"
            reason = r.get("reason", "") or r["status"]
            lines.append(f"| {key} | {r['case']['id']} | {r['status']} | {reason[:80]} |")
        lines.append("")

    # ── 逐 case 明细（按配置分组）──
    lines.extend(["", "## 明细", ""])
    for key, rows in groups.items():
        lines.append(f"### 配置 `{key}`")
        lines.append("")
        lines.append("| Case | 目的地 | 完整性 | 实用性 | 结构化 | 中位分 | 关键词 | 耗时 | 状态 |")
        lines.append("|------|--------|--------|--------|--------|--------|--------|------|------|")
        for r in rows:
            case = r["case"]
            ev = r["eval"]
            judges = ev.get("judges", {})
            def _js(name):
                s = judges.get(name, {})
                return s.get("score") if s.get("score") is not None else "-"
            c, p, s = _js("completeness"), _js("practicality"), _js("structure")
            status = r["status"]
            lines.append(
                f"| {case['id']} | {case['destination']} | {c} | {p} | {s} | "
                f"**{ev['overall'] if r['status'] == 'ok' else '—'}** | {ev['keyword_coverage']}% | {r['elapsed']}s | {status} |"
            )
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Travel Planner 评测运行器（含 baseline 消融）")
    parser.add_argument("--cases", type=int, default=3, help="评测用例数量")
    parser.add_argument("--output", type=str, default="eval/report.md")
    parser.add_argument("--mode", type=str, default="multi", choices=["multi", "sequential", "single"])
    parser.add_argument("--no-reflection", action="store_true", help="关闭自我反思")
    parser.add_argument("--ablation", action="store_true", help="multi/single/sequential/no-refl 对比")
    args = parser.parse_args()

    report = run_eval(
        num_cases=args.cases,
        mode=args.mode,
        enable_reflection=not args.no_reflection,
        ablation=args.ablation,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"\n报告已生成: {output_path}")


if __name__ == "__main__":
    main()
