"""给前端进度面板做离线渲染截图。

思路：直接加载 index.html，用 JS 注入触发进度面板的各个状态
（正常推进 / 卡住 90 秒 / 完成），再截图，这样不用真跑一次 5 分钟的规划
就能看到面板长什么样。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "frontend" / "index.html"
OUT = ROOT / "docs" / "shots"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


SCENARIOS = {
    "progress_normal": """
      progressStart();
      progressStage('正在查询景点资料', null);
      progressDetail('工具：search_attraction_context');
      document.getElementById('progressTimer').textContent = '48s';
      document.getElementById('progressFill').style.width = '42%';
    """,
    "progress_stalled": """
      progressStart();
      progressStage('模型正在思考与规划', null);
      progressDetail('当前目的地资料稀缺，正在尝试联网补充');
      document.getElementById('progressTimer').textContent = '2m15s';
      document.getElementById('progressFill').style.width = '85%';
      PROGRESS.elPanel.classList.add('stalled');
      document.getElementById('progressHint').textContent =
        '当前阶段较慢（模型推理中），仍在正常执行，请继续等待';
    """,
}


def main() -> int:
    if not HTML.exists():
        print(f"找不到 {HTML}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    src = HTML.read_text(encoding="utf-8")

    for name, js in SCENARIOS.items():
        # 在 </script> 前插入一段「加载后自动执行」的探针
        probe = (
            "\nwindow.addEventListener('load', function(){\n"
            "  try {\n" + js + "\n  } catch(e){ document.title='ERR '+e.message; }\n"
            "});\n"
        )
        idx = src.rfind("</script>")
        patched = src[:idx] + probe + src[idx:]
        tmp_html = OUT / f"_{name}.html"
        tmp_html.write_text(patched, encoding="utf-8")

        png = OUT / f"{name}.png"
        cmd = [
            EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--virtual-time-budget=2500",
            "--screenshot=" + str(png),
            "--window-size=1180,900",
            tmp_html.as_uri(),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if png.exists():
            print(f"  ✓ {png.name}  ({png.stat().st_size // 1024} KB)")
        else:
            print(f"  ✗ {name} 截图失败: {r.stderr[:400]}")
        # 中间态 HTML 只是渲染用的临时文件（每份 60KB），截完就删，
        # 免得污染 docs/shots/ 让人以为它是交付物
        tmp_html.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
