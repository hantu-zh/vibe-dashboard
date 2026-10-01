#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四周新高（近52周高 + 温和突破 + 不远离25日线）选股生成器。

产出（参照 LST 的「每日冻结快照」结构，便于观察信号连续性与回测）：
  - fourweek_newhigh_history.json  （每日冻结快照，供 strong.html 的「四周新高」tab 渲染）
        { generated, window_days, formula, dates:[...], data:{ "YYYY-MM-DD": [pick,...] } }
  - fourweek_newhigh.json          （最新一日快照，便于核对/其它入口）
  - fourweek_newhigh.csv           （最新一日明细，透明核对）

选股逻辑（日线，收盘对收盘）—— 单日核心信号：
  A. 相对强度：收盘 ≥ 近 250 日(≈52周)最高价 ×(1−NEAR_52W_PCT)（默认 0.95）→ 一年高位。
  B. 温和突破：当日收盘 > 前20日(4周)最高收盘，且突破幅度(收盘/前20日高−1) ≤ GENTLE_MAX
     （默认 3.5%）→ 新高但不透支。
  D. 贴近25日线（用户要求）：收盘/MA25 − 1 ∈ [DEV25_MIN, DEV25_MAX]（默认 [−8%, +12%]）
     → 不远离中线、不过度超买。
  F. 流动性：近5日日均成交额 ≥ AMOUNT_5D_MIN_YI（默认 1 亿）。
  E. 趋势(MA25>MA60)：仅作展示/辅助排序，不再做硬过滤（避免把样本饿死）。

「连续五天」如何实现（参照 LST）：
  不在单日 K 线里硬卡「连续5日」，而是把「当日核心信号」作为每日快照冻结存档，
  跨交易日累计同一标的被连续选出的天数 → consec_days（连创天数）。CI 每日运行后，
  consec_days 自然增长；达到 5 即标记 🔥。首日部署 consec_days 均为 1，随后逐日累积。

数据：东财优先 → 腾讯兜底 → 新浪三级兜底。沙箱仅新浪可达时加 `sina`。
参数集中在顶部，便于调参。
"""
import json, os, sys, time, urllib.request, urllib.parse

WS = os.path.dirname(os.path.abspath(__file__))
UNIV = os.path.join(WS, "a_shares.json")
OUT_JSON = os.path.join(WS, "..", "fourweek_newhigh.json")
OUT_HISTORY = os.path.join(WS, "..", "fourweek_newhigh_history.json")
OUT_CSV = os.path.join(WS, "..", "fourweek_newhigh.csv")

# ───────── 可调参数（顶部集中） ─────────
LOOKBACK_52W = 250      # 交易日：近一年(48~53周)最高价窗口
NEAR_52W_PCT = 0.05     # A：收盘距52周高 ≤ 5% 视为「近一年高位」
WIN_4W = 20             # 交易日：4周新高窗口
GENTLE_MAX = 0.035      # B：突破幅度 ≤ 3.5% 视为温和（>0 自带，因须创20日新高）
DEV25_MIN = -0.08       # D：偏离25日线下限（不深跌）
DEV25_MAX = 0.12        # D：偏离25日线上限（不过度超买）
AMOUNT_5D_MIN_YI = 1.0  # F：近5日日均成交额下限（亿）
MIN_BARS = 260          # 最少历史交易日（覆盖 250 日窗口 + 缓冲）
FETCH_N = 420           # 单次拉取日线根数（≈250 交易日 + 节假日缓冲）
WINDOW_DAYS = 30        # 保留最近 N 个交易日的冻结快照（>5 以便 consec_days 能累积到 5+）

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://quote.eastmoney.com/"}

FOURWEEK_FORMULA = """{================ 四周新高（近52周高 + 温和突破 + 不远离25日线）================}
N52:=250; N4W:=20;
HH52:=HHV(H,N52);                         {近52周最高价}
NEAR52:=C>=HH52*0.95;                     {收盘在近一年高位(距高≤5%)}
BRKREF:=HHV(REF(C,1),N4W);                {前20日(4周)最高收盘}
BRK:=C>BRKREF;                           {今日创4周新高}
GENTLE:=C/BRKREF<=1.035;                 {突破幅度≤3.5%(温和，不透支)}
MA25V:=MA(C,25);
IND25:=C/MA25V>0.92 AND C/MA25V<1.12;    {不远离25日线(±区间)}
AMT5:=MA(VOL,5)*C*100/1e8;               {近5日均额(亿)}
LIQ:=AMT5>=1;                            {流动性过滤}
XG:=NEAR52 AND BRK AND GENTLE AND IND25 AND LIQ;
{说明：本公式为日线形态(单日信号)，与 gen_fourweek_newhigh.py 口径一致；}
{「连续天数(consec_days)」由每日快照跨交易日累计，非本公式内计算；北交所流动性多数不达标自然剔除。}"""


def _get_json(url, tries=2, base_sleep=1.2):
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = repr(e)
            time.sleep(base_sleep * (i + 1))
    return {"__err__": last}


def _em_secid(code):
    return ("1." if code[0] == "6" else "0.") + code


def _tx_symbol(code):
    return ("sh" if code[0] == "6" else "sz") + code


def fetch_em(code):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode({
        "secid": _em_secid(code), "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "klt": "101", "fqt": "0", "lmt": str(FETCH_N)})
    d = _get_json(url)
    if "__err__" in d:
        return None, []
    dd = d.get("data") or {}
    out = []
    for s in dd.get("klines") or []:
        p = s.split(",")
        if len(p) < 6:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[2]), float(p[4]), float(p[3]), int(p[5])])
        except Exception:
            continue
    return dd.get("name") or "", out


def fetch_tx(code):
    sym = _tx_symbol(code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           + urllib.parse.urlencode({"param": f"{sym},day,,,{FETCH_N},"}))
    d = _get_json(url, tries=1, base_sleep=0.5)
    if "__err__" in d:
        return "", []
    node = (d.get("data") or {}).get(sym) or {}
    k = node.get("day") or node.get("qfqday") or []
    out = []
    for p in k:
        if len(p) < 6:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[2]), float(p[4]), float(p[3]), int(p[5])])
        except Exception:
            continue
    return "", out


def fetch_sina(code):
    c = code[0]
    if c == "6":
        sym = "sh" + code
    elif c in ("8", "9", "4"):
        sym = "bj" + code
    else:
        sym = "sz" + code
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
           + urllib.parse.urlencode({"symbol": sym, "scale": "240", "ma": "no",
                                     "datalen": str(FETCH_N)}))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            txt = r.read().decode("utf-8").strip()
        if not txt.startswith("["):
            return "", []
        d = json.loads(txt)
    except Exception:
        return "", []
    out = []
    for it in d or []:
        try:
            out.append([it["day"][:10], float(it["open"]), float(it["close"]),
                        float(it["low"]), float(it["high"]),
                        int(float(it["volume"]) / 100)])  # 新浪 volume 为股 → 手
        except Exception:
            continue
    return "", out


def fetch_daily(code, src_order):
    for s in src_order:
        try:
            if s == "em":
                name, rows = fetch_em(code)
            elif s == "tx":
                name, rows = fetch_tx(code)
            else:
                name, rows = fetch_sina(code)
            if rows:
                return name, rows, s
        except Exception:
            continue
    return "", [], "none"


def is_excluded(name):
    if not name:
        return False
    u = name.upper()
    return ("ST" in u) or u.startswith("*") or ("退" in u)


def qualifies(closes, highs, vols):
    """返回单日核心信号指标 dict；不满足返回 None。趋势(MA25>MA60)仅展示，不硬过滤。"""
    n = len(closes)
    if n < MIN_BARS:
        return None
    high52 = max(highs[max(0, n - LOOKBACK_52W):n])
    if high52 <= 0:
        return None
    c0 = closes[n - 1]
    near_thr = high52 * (1 - NEAR_52W_PCT)
    if c0 < near_thr:
        return None

    # B. 温和 4 周突破
    ref20 = max(closes[max(0, n - 1 - WIN_4W):n - 1])
    if ref20 <= 0:
        return None
    break_today = c0 / ref20 - 1
    if break_today <= 0 or break_today > GENTLE_MAX:
        return None

    # D. 贴近 25 日线（用户要求）
    ma25 = sum(closes[n - 25:n]) / 25.0
    dev25 = c0 / ma25 - 1
    if dev25 < DEV25_MIN or dev25 > DEV25_MAX:
        return None

    # F. 流动性
    amt5 = sum(vols[n - 5:n]) * 100.0 * c0 / 1e8
    if amt5 < AMOUNT_5D_MIN_YI:
        return None

    # E. 趋势（展示 + 辅助排序，不硬过滤）
    ma60 = sum(closes[n - 60:n]) / 60.0 if n >= 60 else None
    trend = (ma60 is not None) and (ma25 > ma60)
    dist_52w = c0 / high52 - 1

    return {
        "dist_52w_pct": dist_52w * 100,
        "break_pct": break_today * 100,
        "dev25_pct": dev25 * 100,
        "ma25": round(ma25, 2),
        "ma60": round(ma60, 2) if ma60 is not None else None,
        "trend": bool(trend),
        "amount_5d_yi": round(amt5, 2),
        "change_pct": (closes[n - 1] / closes[n - 2] - 1) * 100 if n >= 2 else 0.0,
        "change_5d": (closes[n - 1] / closes[n - 6] - 1) * 100 if n >= 6 else 0.0,
    }


def build_pick(code, name, q, consec_days):
    c0 = None  # price 由调用方在扫描后统一补；此处先占位
    rs = max(0.0, min(10.0, (q["dist_52w_pct"] / 100.0 + NEAR_52W_PCT) / NEAR_52W_PCT * 10))
    rs = round(rs, 1)
    reason = (f"近52周高{q['dist_52w_pct']:+.1f}% | "
              f"4周新高+{q['break_pct']:.1f}% | "
              f"偏离25日线{q['dev25_pct']:+.1f}% | "
              f"连续{consec_days}日 | 5日均额{q['amount_5d_yi']:.1f}亿")
    return {
        "code": code, "name": name,
        "price": None,
        "change_pct": round(q["change_pct"], 2),
        "change_5d": round(q["change_5d"], 2),
        "dist_52w_pct": round(q["dist_52w_pct"], 2),
        "break_pct": round(q["break_pct"], 2),
        "dev25_pct": round(q["dev25_pct"], 2),
        "ma25": q["ma25"], "ma60": q["ma60"], "trend": q["trend"],
        "amount_5d_yi": q["amount_5d_yi"],
        "consec_days": consec_days,
        "score": rs,
        "tag": "fourweek",
        "reason": reason,
    }


def consec_for(code, today, prior_dates, hist_data):
    """同一标的在 prior_dates（均 < today，升序）中，从 today 往前连续被选中的天数 +1。"""
    cnt = 0
    for d in reversed(prior_dates):
        if any(p.get("code") == code for p in hist_data.get(d, [])):
            cnt += 1
        else:
            break
    return cnt + 1


def main():
    src_order = ["em", "tx", "sina"]
    limit = None
    for a in sys.argv[1:]:
        if a == "sina":
            src_order = ["sina"]
        elif a.startswith("--limit="):
            limit = int(a.split("=")[1])

    if not os.path.exists(UNIV):
        print("缺少 a_shares.json（宇宙文件）")
        return
    univ = json.load(open(UNIV, encoding="utf-8")).get("stocks", [])
    print(f"宇宙 {len(univ)} 只，数据源顺序 {src_order}")

    today = time.strftime("%Y-%m-%d")
    # 载入历史快照（用于累计 consec_days 与补算「选出后涨跌幅 / 累计天数」）
    history = {"generated": today, "window_days": WINDOW_DAYS,
               "formula": FOURWEEK_FORMULA, "dates": [], "data": {}}
    if os.path.exists(OUT_HISTORY):
        try:
            history = json.load(open(OUT_HISTORY, encoding="utf-8"))
            history.setdefault("window_days", WINDOW_DAYS)
            history.setdefault("formula", FOURWEEK_FORMULA)
        except Exception:
            pass
    prior_dates = sorted(d for d in history.get("dates", []) if d < today)

    results = []          # 今日命中（不含 price，稍后补）
    latest_close = {}     # code -> 今日收盘（用于补算历史快照的前向收益）
    scanned = 0
    t0 = time.time()
    for it in univ:
        code = it.get("code")
        uname = it.get("name", "")
        if not code:
            continue
        if limit and scanned >= limit:
            break
        scanned += 1
        name, rows, src = fetch_daily(code, src_order)
        if not rows:
            continue
        closes = [r[2] for r in rows]
        highs = [r[4] for r in rows]
        vols = [r[5] for r in rows]
        latest_close[code] = closes[-1]
        if is_excluded(uname) or is_excluded(name):
            continue
        q = qualifies(closes, highs, vols)
        if q:
            cd = consec_for(code, today, prior_dates, history.get("data", {}))
            pk = build_pick(code, uname or name, q, cd)
            pk["price"] = round(closes[-1], 2)
            results.append(pk)
        if scanned % 500 == 0:
            print(f"  [{scanned}/{len(univ)}] 今日命中 {len(results)} 只, {time.time()-t0:.0f}s")

    # 补算历史快照的「选出后涨跌幅 / 累计天数」（用今日收盘）
    all_dates = sorted(set(history.get("dates", [])) | {today})
    for dt in all_dates:
        if dt >= today:
            continue
        for p in history["data"].get(dt, []):
            lc = latest_close.get(p.get("code"))
            p["days_since"] = sum(1 for d in all_dates if dt < d <= today)
            p["post_change_pct"] = round((lc - p["price"]) / p["price"] * 100, 2) if (lc and p.get("price")) else None

    results.sort(key=lambda x: (x["consec_days"], x["score"]), reverse=True)

    # 写入今日快照（覆盖同日）
    history["data"][today] = results
    history["dates"] = sorted(set(history["dates"]) | {today})
    keep = sorted(history["dates"])[-WINDOW_DAYS:]
    history["dates"] = keep
    history["data"] = {d: history["data"][d] for d in keep}
    history["generated"] = today
    json.dump(history, open(OUT_HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"date": today, "picks": results}, open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # CSV 透明核对（最新一日）
    cols = ["code", "name", "price", "change_pct", "change_5d", "dist_52w_pct",
            "break_pct", "dev25_pct", "ma25", "ma60", "trend", "amount_5d_yi",
            "consec_days", "score", "tag", "reason"]
    import csv
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k) for k in cols})
    print(f"完成：扫描 {scanned} 只，今日命中 {len(results)} 只 → {OUT_HISTORY}")


if __name__ == "__main__":
    main()
