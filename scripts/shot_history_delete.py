"""给「历史记录删除」做离线渲染截图。

历史抽屉默认是关的，而且列表要联网取数据。这里直接注入：
打开抽屉 + 写入几条假的卡片（含正常态与「确认删除」态），
不依赖后端就能看到 UI 长什么样。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "frontend" / "index.html"
OUT = ROOT / "docs" / "shots"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


PROBE = r"""
window.addEventListener('load', function(){
  try {
    // 1) 打开历史抽屉
    document.getElementById('historyDrawer').classList.add('open');

    // 2) 塞入假数据（结构必须和 loadHistory 产出一致）
    var rows = [
      {dest:'广东梅州', days:3, budget:1000, date:'2026-09-16'},
      {dest:'东京',     days:5, budget:2500, date:'2026-09-15'},
      {dest:'巴黎',     days:4, budget:3000, date:'2026-09-14'},
    ];
    document.getElementById('historyList').innerHTML = rows.map(function(r, i){
      var confirmCls = i === 0 ? ' confirming' : '';
      var label = i === 0 ? '确认删除' : '✕';
      return '<div class="history-card">'
        + '<div class="hc-main">'
        +   '<div class="dest">' + r.dest + '</div>'
        +   '<div class="info">' + r.days + '天 · $' + r.budget + ' · ' + r.date + '</div>'
        + '</div>'
        + '<button class="hc-del' + confirmCls + '">' + label + '</button>'
        + '</div>';
    }).join('');

    // 3) 让第一张卡片（待确认态）和其余卡片都保持按钮可见，
    //    否则截图里只有 hover 才显示，看不到效果
    document.querySelectorAll('.hc-del').forEach(function(b){
      b.style.opacity = '1';
    });
  } catch(e) { document.title = 'ERR ' + e.message; }
});
"""


def main() -> int:
    if not HTML.exists():
        print(f"找不到 {HTML}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    src = HTML.read_text(encoding="utf-8")

    idx = src.rfind("</script>")
    patched = src[:idx] + "\n" + PROBE + "\n" + src[idx:]
    tmp_html = OUT / "_history_del.html"
    tmp_html.write_text(patched, encoding="utf-8")

    png = OUT / "history_delete.png"
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
        print(f"  ✗ 截图失败: {r.stderr[:400]}")
    tmp_html.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
