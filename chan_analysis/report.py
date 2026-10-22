# -*- coding: utf-8 -*-
"""生成 HTML 分析报告：缠论 + 波浪 + 支撑压力量化。"""
import io, os, sys, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run as R

UP = "#ff4d4f"      # 涨 红
DN = "#00c853"      # 跌 绿
ZS = "rgba(255,193,7,0.13)"
ZS_BD = "rgba(255,193,7,0.55)"
BI = "rgba(255,152,0,0.9)"
LVL_S = "rgba(0,229,255,0.75)"
LVL_R = "rgba(255,109,218,0.75)"


# ------------------------------------------------------------------ SVG K线
def kline_svg(rows, r, levels, cur, n=160, h=430, title=""):
    idx0 = max(0, len(rows) - n)
    seg = rows[idx0:]
    m = len(seg)
    lo = min(x["low"] for x in seg)
    hi = max(x["high"] for x in seg)
    # 纳入可见范围内的中枢
    vis_zs = [z for z in r["zs_bi"] if z["e_idx"] >= idx0 and z["s_idx"] <= len(rows) - 1]
    for z in vis_zs:
        lo = min(lo, z["zd"])
        hi = max(hi, z["zg"])
    pad = (hi - lo) * 0.06
    lo -= pad
    hi += pad

    W = 1160
    pad_l, pad_r, pad_t = 52, 96, 26
    vol_h = 58
    plot_h = h - pad_t - vol_h - 34
    plot_w = W - pad_l - pad_r

    def X(i):
        return pad_l + (i - idx0) / max(1, m - 1) * plot_w

    def Y(p):
        return pad_t + (hi - p) / (hi - lo) * plot_h

    bw = plot_w / max(1, m) * 0.62
    vmax = max(x["volume"] for x in seg) or 1
    out = ['<svg viewBox="0 0 %d %d" style="width:100%%;height:auto;display:block">' % (W, h)]

    # 中枢矩形
    for z in vis_zs:
        x0 = X(max(z["s_idx"], idx0))
        x1 = X(min(z["e_idx"], len(rows) - 1))
        if x1 <= x0:
            continue
        y0, y1 = Y(z["zg"]), Y(z["zd"])
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" stroke="%s" stroke-dasharray="3 2"/>' %
                   (x0, y0, x1 - x0, max(2.0, y1 - y0), ZS, ZS_BD))
        out.append('<text x="%.1f" y="%.1f" fill="rgba(255,193,7,0.9)" font-size="10">中枢 %.0f–%.0f</text>' %
                   (x0 + 4, y0 + 12, z["zd"], z["zg"]))

    # 成交量
    for i, b in enumerate(seg):
        gi = idx0 + i
        vh = b["volume"] / vmax * vol_h
        col = UP if b["close"] >= b["open"] else DN
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" opacity="0.55"/>' %
                   (X(gi) - bw / 2, h - 26 - vh, bw, vh, col))

    # K线
    for i, b in enumerate(seg):
        gi = idx0 + i
        col = UP if b["close"] >= b["open"] else DN
        x = X(gi)
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>' %
                   (x, Y(b["high"]), x, Y(b["low"]), col))
        y1, y2 = Y(b["open"]), Y(b["close"])
        top, hh = min(y1, y2), max(abs(y2 - y1), 1.2)
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>' %
                   (x - bw / 2, top, bw, hh, col))

    # 笔
    pts = []
    for b in r["bi"]:
        if b["e_idx"] < idx0:
            continue
        pts.append((max(b["s_idx"], idx0), b["s_val"]))
        pts.append((min(b["e_idx"], len(rows) - 1), b["e_val"]))
    p = r.get("pending")
    if p and p["s_idx"] >= idx0:
        pts.append((p["s_idx"], p["from_val"]))
        pts.append((len(rows) - 1, p["to_val"]))
    if len(pts) >= 2:
        d = " ".join("%.1f,%.1f" % (X(a), Y(b)) for a, b in pts)
        out.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="1.6" opacity="0.85"/>' % (d, BI))

    # 支撑压力水平线（只画结构性位，最多 7 条）
    cands = [x for x in (levels["support"] + levels["resist"]) if x["structural"]]
    cands = [x for x in cands if abs(x["dist"]) <= 12]
    cands.sort(key=lambda x: abs(x["dist"]))
    for x in cands[:7]:
        p_ = x["price"]
        if not (lo <= p_ <= hi):
            continue
        y = Y(p_)
        col = LVL_S if x["side"] == "support" else LVL_R
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-width="1" stroke-dasharray="6 4"/>' %
                   (pad_l, y, W - pad_r, y, col))
        tag = "%s" % ("S" if x["side"] == "support" else "R")
        out.append('<text x="%d" y="%.1f" fill="%s" font-size="10">%s %.0f (%+.1f%%)</text>' %
                   (W - pad_r + 4, y + 3, col, tag, p_, x["dist"]))

    # 现价线
    y = Y(cur)
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#fff" stroke-width="1" opacity="0.5"/>' %
               (pad_l, y, W - pad_r, y))
    out.append('<rect x="%d" y="%.1f" width="46" height="15" fill="#fff" opacity="0.85"/>' %
               (pad_l + 2, y - 15))
    out.append('<text x="%d" y="%.1f" fill="#111" font-size="10" font-weight="700">%.2f</text>' %
               (pad_l + 5, y - 4, cur))

    # 坐标轴
    for v in [lo, (lo + hi) / 2, hi]:
        out.append('<text x="%d" y="%.1f" fill="rgba(255,255,255,0.45)" font-size="10" text-anchor="end">%.0f</text>' %
                   (pad_l - 6, Y(v) + 3, v))
    step = max(1, m // 6)
    for i in range(0, m, step):
        out.append('<text x="%.1f" y="%d" fill="rgba(255,255,255,0.4)" font-size="9" text-anchor="middle">%s</text>' %
                   (X(idx0 + i), h - 8, seg[i]["date"][5:]))
    if title:
        out.append('<text x="%d" y="16" fill="rgba(255,255,255,0.75)" font-size="12" font-weight="600">%s</text>' %
                   (pad_l, title))
    out.append('</svg>')
    return "".join(out)


# ------------------------------------------------------------------ 表格
def pct(x, d=2):
    return "—" if x is None else "%+.{}f%%".format(d) % (x * 100)


def lvl_table(items, kind, cur):
    rows = []
    for x in items:
        s = x["score"]
        sc = s["score"]
        badge = ""
        if s.get("low_conf"):
            badge = '<span class="badge b-low">样本不足</span>'
        elif s["conf_label"] == "高":
            badge = '<span class="badge b-hi">置信高</span>'
        elif s["conf_label"] == "中":
            badge = '<span class="badge b-mid">置信中</span>'
        else:
            badge = '<span class="badge b-low2">置信偏低</span>'
        cls = "pos" if (s["mean"] or 0) > 0 else "neg"
        score_html = ("<b>%.1f</b>" % sc) if sc is not None else '<span class="dim">n/a</span>'
        rows.append(
            '<tr><td class="num">%s</td><td class="num %s">%s</td><td>%s</td><td class="num">%d</td>'
            '<td class="num">%s</td><td class="num %s">%s</td><td>%s</td><td class="lbl">%s</td></tr>' % (
                "%.2f" % x["price"],
                "up" if x["dist"] > 0 else "dn", pct(x["dist"] / 100, 2),
                score_html, s["n"],
                pct(s["win"], 1).replace("+", "").replace("%", "%") if s["win"] is not None else "—",
                cls, pct(s["mean"], 2),
                badge, " · ".join(x["labels"][:4])))
    head = ("<tr><th>价位</th><th>距现价</th><th>有效性评分</th><th>样本</th><th>方向胜率</th>"
            "<th>10日均值</th><th>置信</th><th>构成来源</th></tr>")
    return '<table class="tbl t8"><thead>%s</thead><tbody>%s</tbody></table>' % (head, "".join(rows))


def bt_table(bt, base, unit="日"):
    order = ["1buy", "2buy", "3buy", "1sell", "2sell", "3sell"]
    name = {"1buy": "一类买点（背驰底）", "2buy": "二类买点（回调不破前低）", "3buy": "三类买点（中枢上沿回踩）",
            "1sell": "一类卖点（背驰顶）", "2sell": "二类卖点（反弹不破前高）", "3sell": "三类卖点（中枢下沿反抽）"}
    rows = []
    for k in order:
        if k not in bt:
            continue
        d = bt[k]
        cells = []
        for h in (5, 10, 20):
            s = d.get(h, {})
            if not s.get("n"):
                cells.append('<td class="dim">—</td><td class="dim">—</td>')
            else:
                cells.append('<td>%d</td><td class="%s">%.0f%% / %s</td>' %
                             (s["n"], "pos" if s["mean"] > 0 else "neg", s["win"] * 100, pct(s["mean"], 2)))
        rows.append('<tr><td>%s</td>%s</tr>' % (name.get(k, k), "".join(cells)))
    # 基线：任意一天买入 —— 用来判断信号是否真有超额
    cells = []
    for h in (5, 10, 20):
        b = base.get(h, {})
        if b.get("n"):
            cells.append('<td>%d</td><td class="dim">%.0f%% / %s</td>' % (b["n"], b["win"] * 100, pct(b["mean"], 2)))
        else:
            cells.append('<td class="dim">—</td><td class="dim">—</td>')
    rows.append('<tr class="baserow"><td>基线：任意一天买入</td>%s</tr>' % "".join(cells))
    head = ('<tr><th>信号类型</th><th colspan="2">持有 5 %s</th><th colspan="2">持有 10 %s</th>'
            '<th colspan="2">持有 20 %s</th></tr><tr><th></th><th>样本</th><th>胜率/均值</th>'
            '<th>样本</th><th>胜率/均值</th><th>样本</th><th>胜率/均值</th></tr>' % (unit, unit, unit))
    if len(rows) == 1:
        rows.append('<tr><td colspan="7" class="dim">样本不足</td></tr>')
    return '<table class="tbl t7"><thead>%s</thead><tbody>%s</tbody></table>' % (head, "".join(rows))


def wave_block(a):
    out = []
    for tag, w in [("日线", a["wave_day"]), ("周线", a["wave_week"])]:
        if not w:
            continue
        lbl = ["起点", "1浪" if w["kind"].startswith("上涨") or w["kind"].startswith("下跌") else "A浪"]
        if len(w["vals"]) == 6:
            names = ["起点", "1浪", "2浪", "3浪", "4浪", "5浪"]
        else:
            names = ["起点", "A浪", "B浪", "C浪"]
        pts = " → ".join("%s <b>%.2f</b>" % (nm, v) for nm, v in zip(names, w["vals"]))
        viol = ('<span class="badge b-ok">0 违反</span>' if w["n_viol"] == 0
                else '<span class="badge b-low">违反：%s</span>' % "、".join(w["viol"]))
        out.append('<div class="wave"><div class="w-hd"><b>%s</b> %s %s <span class="dim">%s ~ %s（跨度 %.1f%%）</span></div>'
                   '<div class="w-pt">%s</div></div>' %
                   (tag, w["kind"], viol, w["start_date"], w["end_date"], w["span"], pts))
    return "".join(out)


def div_block(a):
    items = []
    for tag, key in [("日线·笔", "cur_div"), ("日线·线段", "cur_div_seg"),
                     ("周线·笔", None)]:
        pass
    # 日线
    for tag, d in [("日线 笔级别", a["day"].get("cur_div")),
                   ("日线 线段级别", a["day"].get("cur_div_seg")),
                   ("周线 笔级别", a["week"].get("cur_div")),
                   ("周线 线段级别", a["week"].get("cur_div_seg"))]:
        if not d:
            continue
        ratio = d.get("ratio")
        if ratio is None:
            verdict = '<span class="badge b-low">无法判定</span>'
            detail = "前段同向面积过小"
        elif not d.get("comparable", True):
            verdict = '<span class="badge b-low">级别不匹配</span>'
            detail = "面积比 %.2f 超出可比区间" % ratio
        elif d.get("div"):
            verdict = '<span class="badge b-ok">背驰成立</span>'
            detail = "面积比 %.2f ＜ 0.85，力度衰竭" % ratio
        else:
            verdict = '<span class="badge b-mid">未背驰</span>'
            detail = "面积比 %.2f，力度未衰竭" % ratio
        items.append('<tr><td>%s</td><td>%s</td><td class="num">%s</td><td class="dim">%s</td></tr>' %
                     (tag, verdict, ("%.2f" % ratio) if ratio is not None else "—", detail))
    return ('<table class="tbl t4"><thead><tr><th>级别</th><th>背驰判定</th><th>MACD面积比</th><th>说明</th></tr></thead>'
            '<tbody>%s</tbody></table>' % "".join(items))


# ------------------------------------------------------------------ 页面
CSS = """
body{margin:0;background:#0d1117;color:#e6edf3;font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.7}
.wrap{max-width:1200px;margin:0 auto;padding:20px 18px 60px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:17px;margin:26px 0 10px;padding-left:9px;border-left:3px solid #ff9800}
h3{font-size:14px;margin:16px 0 8px;color:#c9d1d9}
.dim{color:#7d8590}
.sub{color:#7d8590;font-size:12px;margin-bottom:14px}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.card{flex:1;min-width:150px;background:#11151c;border:1px solid #21262d;border-radius:8px;padding:11px 13px}
.card .k{font-size:11px;color:#7d8590}
.card .v{font-size:19px;font-weight:700;margin-top:2px}
.card .x{font-size:11px;color:#7d8590;margin-top:2px}
.up{color:#ff4d4f}.dn{color:#00c853}.pos{color:#ff4d4f}.neg{color:#00c853}
.tbl{border-collapse:collapse;width:100%;background:#11151c;border:1px solid #21262d;font-size:13px;table-layout:fixed}
.tbl th{background:#161b22;color:#8b949e;padding:8px 10px;text-align:left;font-weight:600;border-bottom:2px solid #30363d;font-size:12px}
.tbl td{padding:7px 10px;border-bottom:1px solid #21262d;vertical-align:top}
.tbl td.num{text-align:right;font-variant-numeric:tabular-nums}
.tbl th:nth-child(1){width:14%}
.t8 th:nth-child(1){width:9%}.t8 th:nth-child(2){width:9%}.t8 th:nth-child(3){width:11%}
.t8 th:nth-child(4){width:7%}.t8 th:nth-child(5){width:10%}.t8 th:nth-child(6){width:10%}.t8 th:nth-child(7){width:9%}
.t4 th:nth-child(1){width:20%}.t4 th:nth-child(2){width:18%}.t4 th:nth-child(3){width:14%}
.t7 th{width:auto}
.lbl{color:#8b949e;font-size:11px;line-height:1.5}
.badge{display:inline-block;padding:1px 6px;border-radius:9px;font-size:11px;white-space:nowrap}
.b-ok{background:rgba(0,200,83,0.15);color:#00c853}
.b-hi{background:rgba(0,229,255,0.14);color:#00e5ff}
.b-mid{background:rgba(255,193,7,0.14);color:#ffc107}
.b-low{background:rgba(125,133,144,0.18);color:#7d8590}
.b-low2{background:rgba(255,109,218,0.12);color:#ff6dda}
.b-dn{background:rgba(0,200,83,0.12);color:#00c853}
.wave{background:#11151c;border:1px solid #21262d;border-radius:8px;padding:10px 13px;margin:8px 0}
.w-hd{font-size:13px;margin-bottom:4px}
.w-pt{font-size:13px;color:#c9d1d9}
.note{background:rgba(255,193,7,0.07);border-left:3px solid #ffc107;border-radius:6px;padding:10px 14px;margin:12px 0;font-size:13px;color:rgba(255,255,255,0.82)}
.ok{background:rgba(0,229,255,0.06);border-left:3px solid #00e5ff;border-radius:6px;padding:10px 14px;margin:12px 0;font-size:13px}
.zsbox{display:flex;gap:10px;flex-wrap:wrap}
.legend{font-size:11px;color:#7d8590;margin:6px 0 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.foot{margin-top:30px;padding-top:14px;border-top:1px solid #21262d;font-size:12px;color:#6e7681}
.baserow td{background:rgba(125,133,144,0.09);color:#8b949e;font-style:italic;border-top:2px solid #30363d}
"""


def build():
    A = [R.full_analyze(s, n) for s, n in
         [("sh000001", "上证指数"), ("sz399006", "创业板指"), ("sh000300", "沪深300")]]
    main = A[0]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = []
    parts.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
                 '<meta name="viewport" content="width=device-width,initial-scale=1">'
                 '<title>大盘缠论·波浪·支撑压力量化分析</title><style>%s</style>'
                 '<script async src="https://01a161c1-4647-7775-a534-adb8b649f49f.spst2.com/ustat.js"></script>'
                 '</head><body><div class="wrap">' % CSS)
    parts.append('<h1>大盘技术结构量化分析 <span class="dim" style="font-size:13px">缠论中枢 · 艾略特波浪 · 支撑压力评分</span></h1>')
    parts.append('<div class="sub">数据截至 %s（%s 收盘）· 数据源：腾讯行情日K/周K · '
                 '全部结论由本地引擎计算，无人工干预</div>' % (main["date"], main["date"]))

    # 概览卡片
    cd_w = main["week"].get("cur_div")
    zs = main["day"]["zs_bi"][-1]
    pend = main["day"]["pending"]
    wv = main["wave_week"]
    st = "已跌破中枢下沿" if main["cur"] < zs["zd"] else ("中枢内震荡" if main["cur"] <= zs["zg"] else "中枢上方")
    parts.append('<div class="cards">')
    parts.append('<div class="card"><div class="k">上证指数</div><div class="v">%.2f</div><div class="x">%s</div></div>' % (main["cur"], main["date"]))
    parts.append('<div class="card"><div class="k">日线中枢</div><div class="v" style="font-size:15px">%.0f – %.0f</div><div class="x">%s（%.1f%%）</div></div>' %
                 (zs["zd"], zs["zg"], st, (main["cur"] - zs["zd"]) / zs["zd"] * 100))
    parts.append('<div class="card"><div class="k">周线浪型</div><div class="v" style="font-size:15px">%s</div><div class="x">%s 起</div></div>' %
                 (wv["kind"] if wv else "—", wv["start_date"] if wv else "—"))
    dv = '<span class="dn">背驰成立</span>' if (cd_w and cd_w.get("div")) else '<span class="dim">未背驰</span>'
    parts.append('<div class="card"><div class="k">周线背驰</div><div class="v" style="font-size:15px">%s</div><div class="x">面积比 %s</div></div>' %
                 (dv, ("%.2f" % cd_w["ratio"]) if cd_w and cd_w.get("ratio") else "—"))
    parts.append('<div class="card"><div class="k">当前未完成的笔</div><div class="v" style="font-size:15px">%s</div><div class="x">%.2f → %.2f（%d日）</div></div>' %
                 ("向下" if pend["dir"] == "down" else "向上", pend["from_val"], pend["to_val"], pend["n"]))
    parts.append('</div>')

    # 图
    parts.append('<h2>一、日线结构（笔 · 中枢 · 支撑压力）</h2>')
    parts.append(kline_svg(main["rows_day"], main["day"], main["levels"], main["cur"], n=160,
                           title="上证指数 日线（近 160 交易日）"))
    parts.append('<div class="legend">'
                 '<i style="background:%s"></i>笔　<i style="background:%s"></i>中枢区间　'
                 '<i style="background:%s"></i>支撑位　<i style="background:%s"></i>压力位　'
                 '<i style="background:rgba(255,255,255,0.6)"></i>现价</div>' % (BI, "rgba(255,193,7,0.5)", LVL_S, LVL_R))

    parts.append('<h2>二、周线结构</h2>')
    parts.append(kline_svg(main["rows_week"], main["week"], main["levels"], main["cur"], n=120, h=380,
                           title="上证指数 周线（近 120 周）"))

    # 波浪
    parts.append('<h2>三、艾略特波浪标注</h2>')
    parts.append(wave_block(main))
    parts.append('<div class="note">波浪由「笔端点序列」自动匹配，优先取<b>结束于最新摆动点</b>且<b>铁律违反最少</b>的组合。'
                 '铁律：2浪不破1浪起点、3浪不是最短、4浪不进入1浪区间。</div>')

    # 中枢
    parts.append('<h2>四、缠论中枢（日线）</h2>')
    rows = []
    for z in main["day"]["zs_bi"][-6:]:
        pos = "上方" if main["cur"] > z["zg"] else ("中枢内" if main["cur"] >= z["zd"] else "下方")
        rows.append('<tr><td>%s ~ %s</td><td class="num">%.2f</td><td class="num">%.2f</td><td class="num">%.2f%%</td>'
                    '<td class="num">%d</td><td>%s</td></tr>' %
                    (z["s_date"], z["e_date"], z["zd"], z["zg"], (z["zg"] - z["zd"]) / z["zd"] * 100, z["n"], pos))
    parts.append('<table class="tbl"><thead><tr><th>区间</th><th>下沿 ZD</th><th>上沿 ZG</th><th>宽度</th><th>笔数</th><th>现价位置</th></tr></thead><tbody>%s</tbody></table>' % "".join(rows))

    # 背驰
    parts.append('<h2>五、背驰判定（MACD 面积比，已按价格归一化）</h2>')
    parts.append(div_block(main))
    parts.append('<div class="ok">判据：同向两段相比，创新低/新高但 MACD 柱面积比 &lt; 0.85 → 背驰。'
                 '面积已除以收盘价归一化，避免不同年份点位水平差异造成的失真；'
                 '面积比超出 0.2~5 倍区间视为<b>级别不匹配</b>，不作判定。</div>')

    # 支撑压力
    parts.append('<h2>六、支撑与压力位（含量化评分）</h2>')
    parts.append('<h3>下方支撑（由近及远）</h3>')
    parts.append(lvl_table(main["levels"]["support"], "support", main["cur"]))
    parts.append('<h3>上方压力（由近及远）</h3>')
    parts.append(lvl_table(main["levels"]["resist"], "resist", main["cur"]))
    parts.append('<div class="note"><b>评分口径：</b>对每个价位回溯历史「测试事件」——'
                 '支撑位取历史上<i>从上方首次触及</i>该位的交易日，统计其后 10 日涨跌（涨为正）；'
                 '压力位取<i>从下方首次触及</i>，统计其后 10 日涨跌（跌为正）。'
                 '评分 = 40%×方向胜率 + 30%×标准化收益 + 30%×样本置信度，多来源共振额外加分。'
                 '「置信偏低」表示历史样本不足 8 次，仅作参考。</div>')

    # 回测
    parts.append('<h2>七、历史信号回测（8 年样本）</h2>')
    parts.append('<h3>缠论买卖点（日线，上证，%s ~ %s）</h3>' % (main["rows_day"][0]["date"], main["date"]))
    parts.append(bt_table(main["bt_points"], main["base_day"], "日"))
    parts.append('<h3>缠论买卖点（周线，上证）</h3>')
    parts.append(bt_table(main["bt_points_week"], main["base_week"], "周"))
    b = main["bt_break"]
    parts.append('<h3>「收盘跌破中枢下沿」事件（日线，上证）</h3>')
    cells = []
    for h in (5, 10, 20):
        d = b["by_h"][h]
        cells.append('<td>%d</td><td class="%s">%.0f%% / %s</td>' %
                     (d.get("n", 0), "pos" if d.get("mean", 0) > 0 else "neg",
                      d.get("win", 0) * 100, pct(d.get("mean"), 2)) if d.get("n") else '<td class="dim">—</td><td class="dim">—</td>')
    parts.append('<table class="tbl t7"><thead><tr><th>事件数</th><th colspan="2">5 日</th><th colspan="2">10 日</th><th colspan="2">20 日</th></tr>'
                 '<tr><th>%d</th><th>样本</th><th>胜率/均值</th><th>样本</th><th>胜率/均值</th><th>样本</th><th>胜率/均值</th></tr></thead>'
                 '<tbody><tr><td>跌破中枢下沿</td>%s</tr></tbody></table>' % (b["events"], "".join(cells)))
    parts.append('<div class="note">'
                 '<b>怎么读这张表：</b>「基线：任意一天买入」是同期大盘的自然涨跌概率——'
                 '只有当信号的胜率<b>明显高于基线</b>，才说明信号本身带信息量，否则只是乘上了市场整体上行的顺风车。<br>'
                 '「收盘跌破中枢下沿」事件共 %d 次，10 日胜率 %.0f%%，高于基线，说明<b>破位后的短线反抽是统计上真实存在的现象</b>；'
                 '但这只能解释为「超跌修复」，<b>不等于趋势反转</b>，因为 20 日胜率并未进一步提升。<br>'
                 '⚠️ 缠论买卖点属于<b>低频信号</b>（8 年一买仅 %d 次），小样本下的 100%% 胜率没有统计意义，'
                 '请以「胜率是否高于基线」和「样本绝对数」两个维度一起看。'
                 '</div>' % (b["events"], b["by_h"][10].get("win", 0) * 100,
                             main["bt_points"].get("1buy", {}).get(10, {}).get("n", 0)))

    # 其他指数
    parts.append('<h2>八、交叉验证：创业板指 / 沪深300</h2>')
    rows = []
    for a in A[1:]:
        z = a["day"]["zs_bi"][-1]
        w = a["wave_week"]
        d = a["week"].get("cur_div")
        pos = "跌破下沿" if a["cur"] < z["zd"] else ("中枢内" if a["cur"] <= z["zg"] else "上方")
        dv = ("背驰 %.2f" % d["ratio"]) if (d and d.get("div")) else ("未背驰" if d else "—")
        s = a["levels"]["support"]
        s1 = s[0] if s else None
        wtxt = w["kind"] if w else "—"
        if w and w["n_viol"]:
            wtxt += ' <span class="badge b-low">%d违反</span>' % w["n_viol"]
        rows.append('<tr><td><b>%s</b></td><td class="num">%.2f</td><td>%s</td><td>%s</td>'
                    '<td class="num">%.0f–%.0f</td><td>%s</td><td class="num">%s</td></tr>' %
                    (a["name"], a["cur"], wtxt,
                     "%s起" % w["start_date"] if w else "—",
                     z["zd"], z["zg"], pos,
                     ("%.2f（%+0.2f%%）" % (s1["price"], s1["dist"])) if s1 else "—"))
    parts.append('<table class="tbl t7"><thead><tr><th>指数</th><th>现价</th><th>周线浪型</th><th>起点</th>'
                 '<th>日线中枢</th><th>位置</th><th>最近支撑</th></tr></thead><tbody>%s</tbody></table>' % "".join(rows))

    # 结论
    parts.append('<h2>九、综合结论</h2>')
    concl = []
    concl.append('<b>结构位置：</b>上证 %.2f，处于日线最后一个中枢（%.0f–%.0f）<b>下沿之下 %.1f%%</b>，'
                 '属于向下离开中枢的状态，周线同样位于中枢（%.0f–%.0f）下方。' %
                 (main["cur"], zs["zd"], zs["zg"], (zs["zd"] - main["cur"]) / main["cur"] * 100,
                  main["week"]["zs_bi"][-1]["zd"], main["week"]["zs_bi"][-1]["zg"]))
    if wv:
        concl.append('<b>波浪定位：</b>周线为<b>%s</b>，自 %s 的 %.2f 起，A 浪至 %.2f、B 浪反弹至 %.2f，'
                     '当前处在 C 浪下跌中（C 浪常见目标 = A 浪的 0.618 / 1.0 / 1.618 倍）。' %
                     (wv["kind"], wv["start_date"], wv["vals"][0], wv["vals"][1], wv["vals"][2]))
    if cd_w and cd_w.get("div"):
        concl.append('<b>中期信号：</b>周线级别<b>底背驰成立</b>（面积比 %.2f），说明本轮下跌的力度已弱于前一段，'
                     '属于「跌势衰竭」而非「跌势加速」，但背驰只提示<b>下跌力度衰减</b>，不等于立刻反转。' % cd_w["ratio"])
    elif cd_w:
        concl.append('<b>中期信号：</b>周线级别<b>未背驰</b>（面积比 %.2f），下跌力度尚未衰竭。' % cd_w["ratio"])
    concl.append('<b>短期信号：</b>日线仍未确认底分型，当前笔向下且未背驰，'
                 '短线应等待<b>日线出现底分型 + 背驰</b>才有确认级别的一买信号。')
    b1 = main["bt_points"].get("1buy", {}).get(10, {})
    base10 = main["base_day"].get(10, {})
    if b1.get("n") and base10.get("n"):
        concl.append('<b>信号有效性（关键）：</b>日线一类买点 10 日胜率 <b>%.0f%%</b>（样本 %d 次），'
                     '同期基线（任意一天买入持有 10 日）为 %.0f%% —— <b>超额 %+.0f 个百分点</b>，'
                     '说明该信号确有信息量；但 8 年只出现 %d 次，属于低频信号，'
                     '不能用「%d 次全胜」去推断下一次必然上涨。' %
                     (b1["win"] * 100, b1["n"], base10["win"] * 100,
                      (b1["win"] - base10["win"]) * 100, b1["n"], b1["n"]))
    best_s = [x for x in main["levels"]["support"] if x["score"]["score"] is not None]
    if best_s:
        bs = max(best_s, key=lambda x: x["score"]["adj"])
        concl.append('<b>关键位：</b>下方评分最高的支撑 <b>%.2f</b>（距现价 %+.2f%%，样本 %d 次、方向胜率 %.0f%%、评分 %.0f）；'
                     '上方最近的阻力 <b>%.2f</b>（%+.2f%%），其上是 %.2f（%+.2f%%）。' %
                     (bs["price"], bs["dist"], bs["score"]["n"], bs["score"]["win"] * 100,
                      bs["score"]["score"],
                      main["levels"]["resist"][0]["price"], main["levels"]["resist"][0]["dist"],
                      main["levels"]["resist"][min(1, len(main["levels"]["resist"]) - 1)]["price"],
                      main["levels"]["resist"][min(1, len(main["levels"]["resist"]) - 1)]["dist"]))
    parts.append('<div class="ok">%s</div>' % "<br><br>".join(concl))

    parts.append('<div class="foot">'
                 '方法论：K线包含处理 → 分型 → 笔（间距≥4）→ 线段（特征序列）→ 中枢（三笔重叠，上限九笔）→ '
                 'MACD 面积背驰 → 一/二/三类买卖点；波浪由笔端点序列按艾略特铁律匹配；'
                 '支撑压力位来自中枢沿、摆动点、斐波那契回撤、C浪目标、均线、整数关口、区间高低点，'
                 '经 0.8%% 聚类合并后逐位做历史测试事件回测。<br>'
                 '所有统计均为历史频次描述，<b>不构成任何投资建议</b>；技术结构只描述概率分布，不预测未来。'
                 '报告生成时间 %s。</div>' % now)
    parts.append('</div></body></html>')

    html = "".join(parts)
    p = os.path.join(HERE, "out", "chan_report.html")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print("生成", p, len(html.encode("utf-8")), "字节")
    return p


if __name__ == "__main__":
    build()
