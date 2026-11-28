#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把人工确认的重大事件气泡叠加到 gjd.png（国家队持仓代理图）上。

数据源：
  event_inject.json  人工注入口（events 为空则原图不动，自动回落）
  gjd_data.json      线上数据快照（本地缓存 src_gjd_data.json 或现抓）用于日期->x 映射

坐标：gjd.png 由 capture_shots.mjs 以 deviceScaleFactor=2 截 #chartA 得到，
      绘图区 x∈[~92,~2180]、宽 2324，顶部气泡带 y≈45-130。
      日期按在 dates 数组的交易日序号线性映射到绘图区。
用法：python overlay_event.py   （在 capture_shots 之后、build_page 之前）
"""
import json
import sys
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
IMG = HERE / "brief_site" / "img" / "gjd.png"
INJECT = HERE / "event_inject.json"
CACHE = HERE / "gjd_dates_cache.json"
GJD_URL = "https://hantu-zh.github.io/vibe-dashboard/gjd_data.json"

# 绘图区像素边界（基于 2324px 宽截图实测；见 _audit/gjd_grid.png）
PLOT_X0, PLOT_X1 = 92, 2180
BUBBLE_Y = 92          # 新气泡中心 y（比原顶部气泡带略低，避开已标注事件）
BUBBLE_W, BUBBLE_H = 150, 44
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",  # Windows
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",     # Ubuntu
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",               # Ubuntu 备选
]
KIND_COLOR = {
    "policy": (232, 169, 11),   # 琥珀
    "buy": (121, 192, 255),     # 青
    "shock": (255, 92, 92),     # 红
}


def load_dates():
    """读 dates 数组：优先本地缓存（与截图同期），否则抓线上并缓存。"""
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))["dates"]
        except Exception:
            pass
    req = urllib.request.Request(GJD_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read().decode("utf-8"))
    CACHE.write_text(json.dumps({"dates": j["dates"]}, ensure_ascii=False), encoding="utf-8")
    return j["dates"]


def date_x(date, dates):
    """交易日序号线性映射到绘图区 x。超出范围夹到右端。"""
    if date in dates:
        i = dates.index(date)
    else:
        # 非交易日取不超过该日的最近交易日
        i = sum(1 for d in dates if d <= date) - 1
    i = max(0, min(i, len(dates) - 1))
    return PLOT_X0 + (PLOT_X1 - PLOT_X0) * i / max(1, len(dates) - 1)


def main():
    inj = json.loads(INJECT.read_text(encoding="utf-8"))
    events = inj.get("events") or []
    if not events:
        print("inject events empty -> gjd.png 保持原样")
        return
    # 源页面 gjd_holdings.html 已原生渲染事件气泡时，截图自带气泡，
    # 再叠加会重复。注入口显式关闭时跳过（语音仍从 events 播报，不受影响）。
    if not inj.get("overlay_on_brief_image", True):
        print("source page renders event natively -> overlay skipped")
        return
    if not IMG.exists():
        print("WARN gjd.png 不存在，跳过叠加", file=sys.stderr)
        return

    dates = load_dates()
    im = Image.open(IMG).convert("RGB")
    draw = ImageDraw.Draw(im)
    font_path = next((p for p in FONT_CANDIDATES if Path(p).exists()), None)
    try:
        f = ImageFont.truetype(font_path, 17) if font_path else ImageFont.load_default()
    except Exception:
        f = ImageFont.load_default()
    W, H = im.size

    for ev in events:
        cx = date_x(ev["date"], dates)
        # 气泡贴右端时左移，避免超出图片
        x1 = min(cx + BUBBLE_W // 2, W - 6)
        x0 = max(6, x1 - BUBBLE_W)
        y0, y1 = BUBBLE_Y, BUBBLE_Y + BUBBLE_H
        color = KIND_COLOR.get(ev.get("kind"), KIND_COLOR["policy"])
        # 圆角框 + 半透明底（用深色实底模拟，避免 alpha 合成复杂）
        draw.rounded_rectangle([x0, y0, x1, y1], radius=7,
                               fill=(17, 21, 28), outline=color, width=2)
        # 两行文字
        lines = (ev.get("label") or "").split("|")
        ty = y0 + 5
        for ln in lines[:2]:
            draw.text((x0 + 8, ty), ln, font=f, fill=(201, 209, 217))
            ty += 19
        print(f"气泡 {ev['date']} @ x={int(cx)} -> ({int(x0)},{y0})")

    im.save(IMG)
    print(f"OK overlay -> {IMG.name} ({len(events)} 个事件)")


if __name__ == "__main__":
    main()
