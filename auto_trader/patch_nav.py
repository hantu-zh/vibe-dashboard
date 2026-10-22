# -*- coding: utf-8 -*-
"""
patch_nav.py —— 保证 vibe-dashboard 主页导航里有「🤖 自动交易」入口（新窗口打开 trader.html）、
「📈 回测胜率」入口指向双标签回测中心（backtest_l1.html，默认旧多策略 tab）
和「🧪 L1回测」入口（backtest_l1.html#l1，默认威震天 L1 tab）。

背景：仓库的 sync bot 会用自己 job 开始时检出的旧副本整篇覆盖 index.html，
      直接手改 index.html 会被周期性冲掉。本脚本做成幂等自愈补丁：
      每次运行检查主页状态，缺什么补什么，已有则不动（不产生空提交）。

用法：
    python auto_trader/patch_nav.py            # 就地修补（默认读写脚本上级目录的 index.html）
    python auto_trader/patch_nav.py --root DIR # 指定仓库根目录
    python auto_trader/patch_nav.py --check    # 只检查不写入（退出码 0=已是目标状态，1=需要修补）
"""
import os
import re
import sys

ICON_DEFAULT = '\U0001f916'  # 🤖
ANCHOR_TMPL = ('<a href="trader.html" target="_blank" rel="noopener" class="nav-link nav-trader"'
               ' onclick="return openTraderWin(event)">{icon} 自动交易</a>')

# 2026-10-22 新增守护：「🧪 L1回测」入口（backtest_l1.html#l1 → 双标签回测中心，默认 L1 tab）
ANCHOR_L1BT = ('<a href="backtest_l1.html#l1" target="_blank" rel="noopener"'
               ' class="nav-link nav-l1bt">🧪 L1回测</a>')

# 「📈 回测胜率」入口指向双标签回测中心（backtest_l1.html，无 hash → 默认旧多策略 tab，老用户无感）
ANCHOR_BACKTEST = ('<a href="backtest_l1.html" target="_blank" rel="noopener"'
                   ' class="nav-link nav-backtest">📈 回测胜率</a>')

# 抓页面里现存的「自动交易」图标，避免把前端改过的图标覆盖回默认值。
# 先在最该有它的 <a href="trader.html"> 里找；找不到（正是需要补的场景）再全文兜底，
# 用 [^\s<>"'] 排除标签符号，免得把 ">🤖" 这种连标签一起抓进来。
ICON_RE_IN_NAV = re.compile(r'<a[^>]*href="trader\.html"[^>]*>\s*([^\s<>"\']{1,4})\s*自动交易')
ICON_RE_ANY = re.compile(r'([^\s<>"\']{1,4})\s*自动交易')


def _nav_icon(html):
    """沿用页面里已有的「自动交易」图标；取不到才回退默认 🤖。"""
    for rx in (ICON_RE_IN_NAV, ICON_RE_ANY):
        m = rx.search(html)
        if m:
            icon = m.group(1).strip()
            if icon:
                return icon
    return ICON_DEFAULT

# 点击时弹出独立窗口；被浏览器拦截则回退到 a 标签自身的 target="_blank"
JS = """
<script>
  /* MODULE: auto-trader popup window */
  function openTraderWin(e){
    try{
      var w = window.open('trader.html', 'vibeTraderWin',
        'width=1440,height=960,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes');
      if(w){ if(w.focus) w.focus(); if(e && e.preventDefault) e.preventDefault(); return false; }
    }catch(err){}
    return true;  // 弹窗被拦截 -> 交给 <a target="_blank"> 兜底
  }
</script>
"""

MARKER_JS = "MODULE: auto-trader popup window"

CSS = """
<style>
  /* MODULE: auto-trader nav button */
  .nav-trader{background:rgba(0,255,242,.16);color:#00fff2;border-color:rgba(0,255,242,.45);}
  .top-nav .nav-trader:hover{background:rgba(0,255,242,.3);color:#8bfff8;}
</style>
"""

MARKER_CSS = "MODULE: auto-trader nav button"


def _strip_block(html, marker, open_tag, close_tag):
    """删除包含 marker 的整块 <style>/<script>。"""
    i = html.find(marker)
    if i < 0:
        return html, False
    s = html.rfind(open_tag, 0, i)
    e = html.find(close_tag, i)
    if s < 0 or e < 0 or e < s:
        return html, False
    return html[:s] + html[e + len(close_tag):], True


def _strip_modal(html):
    """删除弹窗 div（从 MODULE 注释开始，到最外层 </div> 结束）。"""
    s = html.find("<!-- MODULE: auto-trader")
    if s < 0:
        return html, False
    f = html.find("trader-foot", s)
    if f < 0:
        return html, False
    # trader-foot 所在 div -> 其父 .trader-dialog -> 其父 .trader-modal
    e = html.find("</div>", f)
    for _ in range(2):
        nxt = html.find("</div>", e + 1)
        if nxt < 0:
            break
        e = nxt
    return html[:s] + html[e + len("</div>"):], True


def patch(html):
    """返回 (新 html, 是否发生修改)。幂等。"""
    orig = html
    changed = False
    # 先记录当前图标（下面会删掉旧锚点，删除前必须取值）
    icon = _nav_icon(html)

    # 1) 清掉历史形态的触发器（弹窗按钮 / 旧锚点），避免重复插入
    html, r = _strip_modal(html)
    changed |= r
    html, r = _strip_block(html, "/* MODULE: auto-trader modal", "<style>", "</style>")
    changed |= r
    html, r = _strip_block(html, "/* MODULE: auto-trader modal control */", "<script>", "</script>")
    changed |= r

    html, r = re.subn(
        r'<button[^>]*onclick="openTraderModal\(\)"[^>]*>.*?</button>', "", html, flags=re.S)
    changed |= bool(r)
    html, r = re.subn(r'<a[^>]*href="trader\.html"[^>]*>.*?</a>', "", html, flags=re.S)
    changed |= bool(r)
    # 旧版底部固定板块
    s = html.find('<section id="auto-trader"')
    if s >= 0:
        e = html.find("</section>", s)
        if e > s:
            html = html[:s] + html[e + len("</section>"):]
            changed = True

    # 2) 导航里插入新窗口链接（先清理删除留下的空行）
    m = re.search(r"(<nav class=\"top-nav\">.*?)(</nav>)", html, re.S)
    if m:
        block = m.group(0)
        cleaned = re.sub(r"\n[ \t]*\n+(?=[ \t]*<)", "\n", block)
        cleaned = re.sub(r"\n[ \t]*\n+(?=[ \t]*</nav>)", "\n", cleaned)
        if cleaned != block:
            html = html[:m.start(1)] + cleaned + html[m.end(1):]
            changed = True
            m = re.search(r"(<nav class=\"top-nav\">.*?)(</nav>)", html, re.S)
        if "nav-trader" not in cleaned:
            indent = "        "
            html = (html[:m.end(1)] + "\n" + indent + ANCHOR_TMPL.format(icon=icon)
                    + "\n      " + html[m.end(1):])
            changed = True

    # 2.5) 双标签回测中心：a) 「📈 回测胜率」入口 href 修正为 backtest_l1.html
    #      （sync bot 冲回 backtest_report.html 时改回来；该页默认展示旧多策略 tab，老用户无感）
    #      b) 确保「🧪 L1回测」入口存在（跟在「回测胜率」后，带 #l1 默认 L1 tab）
    m = re.search(r'(<nav class="top-nav">.*?)(</nav>)', html, re.S)
    if m:
        nav = m.group(0)
        # a) 「📈 回测胜率」入口缺失 → 补（插在「自动交易」入口前，保持导航顺序）
        if "nav-backtest" not in nav:
            t = re.search(r'<a[^>]*class="nav-link nav-trader"[^>]*>.*?</a>', nav, re.S)
            if t:
                pos = m.start(1) + t.start()
                html = html[:pos] + ANCHOR_BACKTEST + "\n        " + html[pos:]
            else:
                html = html[:m.end(1)] + "\n        " + ANCHOR_BACKTEST + "\n      " + html[m.end(1):]
            changed = True
            m = re.search(r'(<nav class="top-nav">.*?)(</nav>)', html, re.S)
            nav = m.group(0)
        # b) 已有入口 href 被 sync bot 冲回 backtest_report.html → 修正（href 与 class 先后不定，抓整签替换）
        fixed = re.sub(r'<a[^>]*class="nav-link nav-backtest"[^>]*>.*?</a>',
                       lambda mm: mm.group(0).replace('href="backtest_report.html"',
                                                      'href="backtest_l1.html"'),
                       nav, flags=re.S)
        if fixed != nav:
            html = html[:m.start(1)] + fixed + html[m.end(1):]
            changed = True
            m = re.search(r'(<nav class="top-nav">.*?)(</nav>)', html, re.S)
            nav = m.group(0)
        # c) 「🧪 L1回测」入口：缺失才补（跟在「回测胜率」后）；已有则修正 href 补 #l1
        if "nav-l1bt" not in nav:
            anchor_bt = re.search(r'<a[^>]*class="nav-link nav-backtest"[^>]*>.*?</a>', nav, re.S)
            if anchor_bt:
                pos = m.start(1) + anchor_bt.end()
                html = html[:pos] + "\n        " + ANCHOR_L1BT + html[pos:]
            else:
                html = html[:m.end(1)] + "\n        " + ANCHOR_L1BT + "\n      " + html[m.end(1):]
            changed = True
        else:
            l1 = re.search(r'<a[^>]*class="nav-link nav-l1bt"[^>]*>.*?</a>', nav, re.S)
            if l1:
                new_tag = re.sub(r'href="backtest_l1\.html(?:#l1)?"',
                                 'href="backtest_l1.html#l1"', l1.group(0))
                if new_tag != l1.group(0):
                    html = html[:m.start(1)] + nav.replace(l1.group(0), new_tag) + html[m.end(1):]
                    changed = True

    # 3) 主题色样式（缺失才注入）
    if MARKER_CSS not in html and "</head>" in html:
        html = html.replace("</head>", CSS + "</head>", 1)
        changed = True

    # 4) 弹窗函数（缺失才注入；放在 </body> 前，同步 bot 不重建该区域则能保留）
    if "function openTraderWin" not in html and "</body>" in html:
        html = html.replace("</body>", JS + "</body>", 1)
        changed = True

    return html, (html != orig) or changed


def is_ok(html):
    m = re.search(r'(<nav class="top-nav">.*?</nav>)', html, re.S)
    bt_tag = re.search(r'<a[^>]*class="nav-link nav-backtest"[^>]*>', m.group(1)) if m else None
    bt_href = re.search(r'href="([^"]+)"', bt_tag.group(0)) if bt_tag else None
    bt_ok = bool(bt_href and bt_href.group(1) == "backtest_l1.html")
    in_nav = bool(m and 'href="trader.html"' in m.group(1) and "nav-trader" in m.group(1)
                  and "openTraderWin" in m.group(1)
                  and 'href="backtest_l1.html#l1"' in m.group(1) and "nav-l1bt" in m.group(1)
                  and bt_ok)
    has_fn = "function openTraderWin" in html
    clean = "openTraderModal" not in html and 'id="trader-modal"' not in html
    return in_nav and has_fn and clean


def main():
    argv = sys.argv[1:]
    root = None
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 < len(argv):
            root = argv[i + 1]
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "index.html")

    if not os.path.exists(path):
        print("[patch_nav] 找不到 %s" % path, file=sys.stderr)
        sys.exit(2)

    html = open(path, encoding="utf-8").read()

    if "--check" in argv:
        ok = is_ok(html)
        print("[patch_nav] check: %s" % ("OK 已是目标状态" if ok else "需要修补"))
        sys.exit(0 if ok else 1)

    if is_ok(html):
        print("[patch_nav] 已是目标状态，无需修改")
        return

    new_html, changed = patch(html)
    if not changed:
        print("[patch_nav] 未发生改动（可能结构异常），跳过写入")
        return

    open(path, "w", encoding="utf-8").write(new_html)
    ok = is_ok(new_html)
    print("[patch_nav] 已修补: 交易入口=%s 回测胜率入口=%s L1入口=%s 清理弹窗=%s" % (
        'href="trader.html"' in new_html, 'nav-backtest' in new_html,
        'nav-l1bt' in new_html, "openTraderModal" not in new_html))
    if not ok:
        print("[patch_nav] 警告：修补后校验未完全通过", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
