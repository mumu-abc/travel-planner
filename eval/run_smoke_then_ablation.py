"""冒烟 → 消融 一体化跑批（带熔断）

为什么要这个脚本：
  直接 --cases 3 --ablation 是 4 变体 × 3 case = 12 次完整 pipeline，
  一旦环境有问题（降级输出、模型返回空、配额不足），会白烧一小时和大量额度。
  本脚本先跑 1 次验证「确实产出了真实内容」，通过后才继续，并在连续失败时熔断。

用法：
    python eval/run_smoke_then_ablation.py             # 冒烟 + 3 case 消融
    python eval/run_smoke_then_ablation.py --cases 3   # 显式指定
    python eval/run_smoke_then_ablation.py --smoke-only

退出码：
    0 全部完成
    3 冒烟未通过（未消耗消融额度）
    4 中途熔断（连续失败，保护额度）
"""

import argparse
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()


def build_client():
    from app.config import settings
    from openai import OpenAI

    return OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    ), settings


def smoke(client, settings) -> int:
    """跑 1 个 case，确认产出的是真实内容而不是离线降级。"""
    from eval.eval_runner import EVAL_CASES, _generate_report, run_one

    case = EVAL_CASES[0]
    print(f"[冒烟] case={case['id']} ({case.get('destination','')}) mode=multi refl=on")
    print("      预计 4-7 分钟…", flush=True)

    t0 = time.time()
    r = run_one(client, case, "multi", True)
    elapsed = time.time() - t0

    status = r.get("status", "")
    reason = r.get("reason", "")
    usage = r.get("usage", {}) or {}
    llm_calls = usage.get("llm_calls", 0)
    score = (r.get("eval") or {}).get("overall", 0)
    chars = (r.get("eval") or {}).get("char_count", 0)

    print(f"[冒烟] status={status} llm_calls={llm_calls} score={score} "
          f"chars={chars} elapsed={elapsed:.0f}s")

    # 落盘冒烟报告，留证据
    out = Path("eval/report_smoke.md")
    out.write_text(
        _generate_report([r], settings.llm_model, ablation=False),
        encoding="utf-8",
    )

    problems = []
    if status != "ok":
        problems.append(f"状态不是 ok：{status} / {reason}")
    if llm_calls < 1:
        problems.append("0 次 LLM 调用 —— 这是离线降级输出，不是真实生成")
    if chars < 500:
        problems.append(f"内容仅 {chars} 字，疑似空输出或降级文案")
    if score <= 0:
        problems.append("评委打 0 分")

    if problems:
        print()
        print("[X] 冒烟未通过，已终止（没有消耗消融额度）：")
        for p in problems:
            print(f"    - {p}")
        print()
        print("    报告: eval/report_smoke.md")
        return 3

    print("[OK] 冒烟通过 —— 产出的是真实 LLM 内容，开始跑消融")
    print()
    return 0


def ablation(client, settings, n_cases: int, max_consec_fail: int = 3) -> int:
    """跑消融，连续失败达到阈值就熔断，避免把额度烧穿。"""
    from eval.eval_runner import EVAL_CASES, _generate_report, run_one

    configs = [
        ("multi", True),
        ("single", True),
        ("sequential", True),
        ("multi", False),
    ]
    cases = EVAL_CASES[:n_cases]
    rows = []
    consec_fail = 0
    total = len(configs) * len(cases)
    done = 0
    t0 = time.time()

    for mode, refl in configs:
        print(f"\n=== 变体 {mode} | refl={'on' if refl else 'off'} ===", flush=True)
        for case in cases:
            done += 1
            print(f"  [{done}/{total}] {case['id']} {case.get('destination','')} …",
                  end=" ", flush=True)
            r = run_one(client, case, mode, refl)
            rows.append(r)

            ok = r.get("status") == "ok"
            llm = (r.get("usage") or {}).get("llm_calls", 0)
            print(f"{'ok' if ok else 'FAIL'} score={r.get('eval',{}).get('overall',0)} "
                  f"llm={llm} {r.get('elapsed',0)}s", flush=True)

            # 每跑完一个变体就落盘一次，中断不丢数据
            Path("eval/report_ablation.partial.md").write_text(
                _generate_report(rows, settings.llm_model, ablation=True),
                encoding="utf-8",
            )

            if ok:
                consec_fail = 0
            else:
                consec_fail += 1
                reason = str(r.get("reason", "")) + str(r.get("status", ""))
                if consec_fail >= max_consec_fail:
                    print()
                    print(f"[!] 连续 {consec_fail} 次失败，熔断（保护剩余额度）")
                    print(f"    最后原因: {reason[:200]}")
                    Path("eval/report_ablation.md").write_text(
                        _generate_report(rows, settings.llm_model, ablation=True),
                        encoding="utf-8",
                    )
                    print("    已保存: eval/report_ablation.md（不完整，看头部说明）")
                    return 4

    Path("eval/report_ablation.md").write_text(
        _generate_report(rows, settings.llm_model, ablation=True),
        encoding="utf-8",
    )
    print(f"\n[OK] 消融完成，用时 {(time.time()-t0)/60:.0f} 分钟")
    print("     报告: eval/report_ablation.md")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=3, help="每个变体跑几个 case")
    ap.add_argument("--smoke-only", action="store_true", help="只跑冒烟")
    ap.add_argument("--no-smoke", action="store_true", help="跳过冒烟直接跑消融（不推荐）")
    args = ap.parse_args()

    client, settings = build_client()

    if not args.no_smoke:
        rc = smoke(client, settings)
        if rc != 0:
            return rc
        if args.smoke_only:
            return 0

    return ablation(client, settings, args.cases)


if __name__ == "__main__":
    sys.exit(main())
