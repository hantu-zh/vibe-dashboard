# -*- coding: utf-8 -*-
"""
patch_nav.py —— 保证 vibe-dashboard 主页导航里有「🤖 自动交易」入口（新窗口打开 trader.html）。

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

ANCHOR = '<a href="trader.html" target="_blank" rel="noopener" class="nav-link nav-trader">\U0001f916 自动交易</a>'

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
            html = html[:m.end(1)] + "\n" + indent + ANCHOR + "\n      " + html[m.end(1):]
            changed = True

    # 3) 主题色样式（缺失才注入）
    if MARKER_CSS not in html and "</head>" in html:
        html = html.replace("</head>", CSS + "</head>", 1)
        changed = True

    return html, (html != orig) or changed


def is_ok(html):
    m = re.search(r'(<nav class="top-nav">.*?</nav>)', html, re.S)
    in_nav = bool(m and 'href="trader.html"' in m.group(1) and "nav-trader" in m.group(1))
    clean = "openTraderModal" not in html and 'id="trader-modal"' not in html
    return in_nav and clean


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
    print("[patch_nav] 已修补: 导航入口=%s 清理弹窗=%s" % ('href="trader.html"' in new_html, "openTraderModal" not in new_html))
    if not ok:
        print("[patch_nav] 警告：修补后校验未完全通过", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
