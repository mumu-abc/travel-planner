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
(function(){
  function paint(){
    try {
      var drawer = document.getElementById('historyDrawer');
      var list   = document.getElementById('historyList');
      if (!drawer || !list) { document.title = 'ERR no-el'; return; }

      // 必须补 .open，否则抽屉还停在屏幕外（CSS 用 .drawer.open 才归位）
      drawer.classList.add('open');

      // loadHistory() 是 async 的，它回头会把我们塞的内容覆盖掉；
      // 所以这里覆盖 window.fetch，让它的请求永远挂起，不再回写 DOM。
      window.fetch = function(){ return new Promise(function(){}); };

      var rows = [
        {dest:'广东梅州', days:3, budget:1000, date:'2026-09-16'},
        {dest:'东京',     days:5, budget:2500, date:'2026-09-15'},
        {dest:'巴黎',     days:4, budget:3000, date:'2026-09-14'},
      ];
      list.innerHTML = rows.map(function(r, i){
        var confirmCls = i === 0 ? ' confirming' : '';
        var label      = i === 0 ? '确认删除' : '✕';
        return '<div class="history-card">'
          + '<div class="hc-main">'
          +   '<div class="dest">' + r.dest + '</div>'
          +   '<div class="info">' + r.days + '天 · $' + r.budget + ' · ' + r.date + '</div>'
          + '</div>'
          + '<button class="hc-del' + confirmCls + '">' + label + '</button>'
          + '</div>';
      }).join('');

      // 截图里要看到按钮，正常态靠 hover 才显形，这里强制显示
      document.querySelectorAll('.hc-del').forEach(function(b){ b.style.opacity = '1'; });

      document.title = 'OK cards=' + document.querySelectorAll('.history-card').length;
    } catch(e) { document.title = 'ERR ' + e.message; }
  }
  // 等 loadHistory 那一轮微任务先跑完，再覆盖
  if (document.readyState === 'complete') setTimeout(paint, 0);
  else window.addEventListener('load', function(){ setTimeout(paint, 0); });
})();
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

    # 顺带回读一次 DOM 状态，确认注入真的生效（别只靠肉眼看图）
    dump = subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu",
         "--virtual-time-budget=2500", "--dump-dom", tmp_html.as_uri()],
        capture_output=True, text=True,
    ).stdout
    import re
    m = re.search(r"<title>(.*?)</title>", dump, re.S)
    print("  页面自检 ->", (m.group(1).strip() if m else "(读不到 title)"))
    cards = dump.count('class="history-card"')
    print("  DOM 里 history-card 数量 ->", cards)

    tmp_html.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
