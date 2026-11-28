#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当期间简报归档成半年可回看的历史站点结构。

输入：brief_site/（当期间网页 + 音频 + timeline）
输出：publish/  （整站，推送到 GitHub Pages 的 brief/ 目录）

    publish/
    index.html              入口：最新一期 + 往期列表（按日期倒序）
    archive/
      YYYY-MM-DD/
        index.html          该期网页
        timeline.json
        audio/narration.mp3
        img/                该期面板截图（可选）

保留策略：只留最近 KEEP_DAYS=180 天（约半年）的归档，超期目录自动删除。
网页内的音频是相对路径 audio/narration.mp3，放进各期子目录后各自独立可用。
"""
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE / "brief_site"
PUB = HERE / "publish"
ARCH = PUB / "archive"
KEEP_DAYS = 180

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def prune():
    if not ARCH.exists():
        return []
    cutoff = datetime.now().date() - timedelta(days=KEEP_DAYS)
    removed = []
    for d in ARCH.iterdir():
        if d.is_dir() and DATE_RE.match(d.name):
            try:
                dd = datetime.strptime(d.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if dd < cutoff:
                shutil.rmtree(d)
                removed.append(d.name)
    return removed


def collect_issues():
    issues = []
    if not ARCH.exists():
        return issues
    for d in ARCH.iterdir():
        if not (d.is_dir() and DATE_RE.match(d.name)):
            continue
        tl = d / "timeline.json"
        title = "每日市场语音简报"
        dur = 0
        if tl.exists():
            try:
                t = json.loads(tl.read_text(encoding="utf-8"))
                title = t.get("title", title)
                dur = round(t.get("total", 0))
            except Exception:
                pass
        issues.append({"date": d.name, "title": title, "dur": dur,
                       "url": f"archive/{d.name}/index.html"})
    issues.sort(key=lambda x: x["date"], reverse=True)
    return issues


def history_html(issues):
    if not issues:
        return ""
    rows = []
    for it in issues:
        rows.append(
            f'<a class="hrow" href="{it["url"]}">'
            f'<span class="hd">{it["date"]}</span>'
            f'<span class="ht">{it["title"]}</span>'
            f'<span class="hdur">{it["dur"]}s</span></a>'
        )
    return (
        '<section class="hist">'
        '<div class="hist-h">往期回顾 · 保留最近半年</div>'
        + "".join(rows)
        + '</section>'
    )


def inject_history(page_text, issues):
    block = history_html(issues)
    return page_text.replace('<div id="history"></div>', block)


def build_landing(latest_page, issues):
    """入口页 = 最新一期完整内容 + 往期列表"""
    return inject_history(latest_page, issues)


def main():
    if not (SITE / "index.html").exists():
        raise SystemExit("brief_site/index.html 不存在，先跑 build_page.py")
    t = json.loads((SITE / "timeline.json").read_text(encoding="utf-8"))
    date = t.get("date") or datetime.now().strftime("%Y-%m-%d")
    if not DATE_RE.match(date):
        raise SystemExit(f"日期格式异常: {date}")

    PUB.mkdir(exist_ok=True)
    ARCH.mkdir(exist_ok=True)

    # 归档当期间
    dest = ARCH / date
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "audio").mkdir(parents=True)
    shutil.copy2(SITE / "index.html", dest / "index.html")
    shutil.copy2(SITE / "timeline.json", dest / "timeline.json")
    shutil.copy2(SITE / "audio" / "narration.mp3", dest / "audio" / "narration.mp3")
    imgsrc = SITE / "img"
    if imgsrc.exists():
        shutil.copytree(imgsrc, dest / "img", dirs_exist_ok=True)

    removed = prune()
    issues = collect_issues()

    # 各期页面都带往期栏，但保留各自的截图与正文，不拿当期覆盖历史
    latest_page = (dest / "index.html").read_text(encoding="utf-8")
    for it in issues:
        p = ARCH / it["date"] / "index.html"
        if p.exists():
            p.write_text(inject_history(p.read_text(encoding="utf-8"), issues), encoding="utf-8")

    (PUB / "index.html").write_text(build_landing(latest_page, issues), encoding="utf-8")

    # 入口页就是最新一期的完整页面，它引用同级 audio/timeline/img，
    # 因此这些资源必须同时复制到 publish 根目录，否则线上播放器 404、没有声音。
    shutil.copy2(SITE / "timeline.json", PUB / "timeline.json")
    (PUB / "audio").mkdir(exist_ok=True)
    shutil.copy2(SITE / "audio" / "narration.mp3", PUB / "audio" / "narration.mp3")
    if imgsrc.exists():
        shutil.copytree(imgsrc, PUB / "img", dirs_exist_ok=True)

    print(f"归档日期 {date} · 共 {len(issues)} 期 · 清理超期 {removed or '无'}")
    print("OK ->", PUB / "index.html")
    print("OK ->", dest)
    print("最近 5 期:", [x["date"] for x in issues[:5]])


if __name__ == "__main__":
    main()
