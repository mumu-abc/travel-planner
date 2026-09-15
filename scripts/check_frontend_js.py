"""把 index.html 里的 <script> 抽出来做语法检查。

为什么需要这个：前端 JS 写错了浏览器只会白屏报错，没有构建步骤兜底，
所以在提交前用 node --check 过一遍语法。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "frontend" / "index.html"

NODE = r"C:\Users\钟利林\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"


def main() -> int:
    html = HTML.read_text(encoding="utf-8")
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    if not blocks:
        print("未找到内联 <script> 块")
        return 1

    print(f"找到 {len(blocks)} 个内联 script 块，总长 {sum(len(b) for b in blocks)} 字符")
    ok = True
    for i, code in enumerate(blocks, 1):
        # 包一层 async 函数，允许顶层 await / return
        wrapped = "async function __probe__(){\n" + code + "\n}\n"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(wrapped)
            tmp = f.name
        r = subprocess.run([NODE, "--check", tmp], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  ✓ 块 {i} 语法通过")
        else:
            ok = False
            print(f"  ✗ 块 {i} 语法错误:\n{r.stderr[:2000]}")
        Path(tmp).unlink(missing_ok=True)

    # 额外检查：进度面板的 6 个锚点 id 是否都存在于 HTML 中
    needed = [
        "progressPanel", "progressStage", "progressTimer",
        "progressFill", "progressDetail", "progressHint",
    ]
    missing = [n for n in needed if f'id="{n}"' not in html]
    if missing:
        ok = False
        print(f"  ✗ 缺少 DOM 锚点: {missing}")
    else:
        print(f"  ✓ 进度面板 {len(needed)} 个 DOM 锚点齐全")

    # 检查进度函数是否都被调用（防止写了没接上）
    for fn in ("progressStart", "progressStage", "progressDetail", "progressDone"):
        calls = len(re.findall(rf"\b{fn}\(", html)) - 1  # 减去定义处
        if calls <= 0:
            ok = False
            print(f"  ✗ {fn} 定义了但从未被调用")
        else:
            print(f"  ✓ {fn} 被调用 {calls} 次")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
