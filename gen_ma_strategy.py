#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_ma_strategy.py — 量能三维策略选股（基于近似走势特征库）
===========================================================
输入（与 similar.html 同源，每日 18:40 由 GitHub Actions 冻结）：
  - trend_features.json      全 A 快照，每只含 c(收盘价序列,120) / q / ema250 / close 等
  - trend_features_index.json 轻量索引（name/board/industry/ema250/close）
实时（运行期拉取）：
  - 腾讯 qt.gtimg.cn/q=  批量报价：换手率[38] / 量比[49] / 现价[3] / 昨收[4] / 涨幅%[32]
判定：
  按三维策略（启动突破 / 回踩低吸 / 冷门异动）打标签 + 强度分，冻结 ma_strategy_pool.json。

说明（诚实标注，避免造假）：
  特征库只提供收盘价序列 c，无换手率历史；策略里“近N日平均换手率”一律用
  **当日实时换手率**近似（当前冷/热即代表当下状态）。量比亦取实时。
  MA123 用 c 序列前 120 根算 MA120 代理（半年线语义等价）。
"""
import json, os, sys, time, math, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
TF_FILE = os.environ.get("TF_FILE", os.path.join(HERE, "trend_features.json"))
TFI_FILE = os.environ.get("TFI_FILE", os.path.join(HERE, "trend_features_index.json"))
OUT_FILE = os.environ.get("OUT_FILE", os.path.join(HERE, "ma_strategy_pool.json"))

BATCH = 80          # 每批代码数
SLEEP = 0.18        # 批间停顿，避免 503
MAX_RETRY = 4

STRATS = {
    "s1": "启动突破",
    "s2": "回踩低吸",
    "s3": "冷门异动",
}


def mean(xs):
    xs = [x for x in xs if x is not None and x == x]
    return sum(xs) / len(xs) if xs else None


def ma_at(c, n, i):
    """c[0..i] 中最后 n 根的均值（i 为右端下标）。c 假设 旧->新。"""
    if i - n + 1 < 0:
        return None
    return mean(c[i - n + 1:i + 1])


def compute_ma(c):
    L = len(c)
    if L < 60:
        return None
    ma25 = ma_at(c, 25, L - 1)
    ma60 = ma_at(c, 60, L - 1)
    ma120 = ma_at(c, min(120, L), L - 1)  # c 仅120根，MA120=全部均值（半年线代理）
    return ma25, ma60, ma120


def ma_series_rising(c, n, back=5):
    """最后 back 根 MA(n) 是否严格递增。"""
    L = len(c)
    if L < n + back - 1:
        return False
    vals = [ma_at(c, n, L - 1 - k) for k in range(back)][::-1]  # 旧->新
    return all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))


def ma_slope_up_or_flat(c, n, back=5):
    L = len(c)
    if L < n + back - 1:
        return False
    a = ma_at(c, n, L - 1 - (back - 1))
    b = ma_at(c, n, L - 1)
    return b is not None and a is not None and b >= a * 0.999


def golden_cross_recent(c, nfast=25, nslow=60, days=5):
    """近 days 日内 MA(nfast) 上穿 MA(nslow)。"""
    L = len(c)
    if L < nslow + days:
        return False
    for i in range(L - days, L):
        a0, b0 = ma_at(c, nfast, i - 1), ma_at(c, nslow, i - 1)
        a1, b1 = ma_at(c, nfast, i), ma_at(c, nslow, i)
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:
            return True
    return False


def classify(c, price, vr, turnover, chgpct, above_year):
    """返回 [(key, score, reasons)]，只含命中的策略。"""
    ma = compute_ma(c)
    if ma is None:
        return []
    ma25, ma60, ma120 = ma
    out = []

    # 通用均线状态
    bull = (ma25 is not None and ma60 is not None and ma120 is not None
            and ma25 > ma60 > ma120)
    price_gt_ma25 = price > ma25
    price_gt_ma60 = price > ma60
    price_lt_ma60 = price < ma60
    ma25_gt_ma60 = ma25 > ma60
    dev25 = abs(price - ma25) / ma25 if ma25 else 9

    # ── 策略一 · 启动突破 ──
    # 注：特征库 c 仅120根，MA120 斜率无从计算，长期趋势以 above_year(价≥ema250×0.95) 代理。
    s1_pre = (ma25_gt_ma60 and price_gt_ma25 and price_gt_ma60
              and above_year
              and ma_series_rising(c, 25, 5)
              and ma_slope_up_or_flat(c, 60, 5)
              and golden_cross_recent(c, 25, 60, 10))
    s1_vol = (1.8 < vr < 7) and (3 < turnover < 12) and (dev25 < 0.06)
    if s1_pre and s1_vol:
        reasons = []
        score = 60
        if vr >= 3:
            score += 10; reasons.append("量比 %.1f 放量充分" % vr)
        else:
            reasons.append("量比 %.1f（偏弱但达标）" % vr)
        if turnover >= 6:
            score += 8; reasons.append("换手率 %.1f%% 活跃" % turnover)
        else:
            reasons.append("换手率 %.1f%%（未过热）" % turnover)
        reasons.append("均线多头+近期金叉，价格站上半年线")
        out.append(("s1", score, reasons))

    # ── 策略二 · 回踩低吸 ──
    # 长期趋势用 price>ma120（价在半年线上方）代理“MA123 上升状态”（120根数据下 MA120 斜率不可得）。
    s2_pre = (bull and ma25_gt_ma60 and (ma120 is not None and price > ma120))
    s2_vol = (0.5 <= vr <= 1.2) and (turnover > 2.5) and (dev25 < 0.035)
    if s2_pre and s2_vol:
        reasons = []
        score = 60
        if vr <= 0.7:
            score += 10; reasons.append("量比 %.2f 明显缩量" % vr)
        else:
            reasons.append("量比 %.2f 缩量回调" % vr)
        if turnover >= 4:
            score += 6; reasons.append("换手率 %.1f%%（近活跃）" % turnover)
        else:
            reasons.append("换手率 %.1f%%" % turnover)
        reasons.append("回踩MA25获支撑(偏离%.1f%%)，多头排列未坏" % (dev25 * 100))
        out.append(("s2", score, reasons))

    # ── 策略三 · 冷门异动 ──
    # 原策略“近5日均值换手<1.5%（冷门）”与“今日换手>4%”矛盾；特征库无换手历史，
    # 此处以结构签名替代：底部（价在MA60下/附近、刚金叉/将金叉、MA25上翘）+ 今日异动放量。
    s3_pre = ((golden_cross_recent(c, 25, 60, 5) or ma25 >= ma60 * 0.99)
              and price < ma60 * 1.02
              and ma_series_rising(c, 25, 3))
    s3_vol = (vr > 2.3) and (4 < turnover < 18) and (chgpct > 4) and price > ma25 * 0.98
    if s3_pre and s3_vol:
        reasons = []
        score = 60
        if vr >= 4:
            score += 12; reasons.append("量比 %.1f 异动放量" % vr)
        else:
            reasons.append("量比 %.1f（刚启动）" % vr)
        reasons.append("冷门(换手%.1f%%)低位金叉，涨幅%.1f%%站上MA25" % (turnover, chgpct))
        out.append(("s3", score, reasons))

    return out


def gtimg_prefix(code):
    return ("sh" if code[0] == "6" else "sz") + code


def fetch_quotes(codes):
    """批量拉腾讯报价，返回 {code: {vr,turnover,price,prevClose,chgpct}}。"""
    res = {}
    for i in range(0, len(codes), BATCH):
        batch = codes[i:i + BATCH]
        q = ",".join(gtimg_prefix(c) for c in batch)
        url = "https://qt.gtimg.cn/q=" + q
        last_err = None
        for attempt in range(MAX_RETRY):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
                for m in __import__("re").finditer(r'v_(\w+)="([^"]+)"', raw):
                    f = m.group(2).split("~")
                    if len(f) < 50:
                        continue
                    c = m.group(1)
                    # c like sh600519 -> strip prefix
                    code = c[2:] if c[:2] in ("sh", "sz", "bj") else c
                    try:
                        res[code] = {
                            "vr": float(f[49]),
                            "turnover": float(f[38]),
                            "price": float(f[3]),
                            "prevClose": float(f[4]),
                            "chgpct": float(f[32]),
                        }
                    except (ValueError, IndexError):
                        pass
                break
            except Exception as e:
                last_err = e
                time.sleep(0.5 * (attempt + 1))
        else:
            sys.stderr.write("batch fail: %s (%s)\n" % (q[:60], last_err))
        time.sleep(SLEEP)
    return res


def main():
    print("读特征库:", TF_FILE)
    tf = json.load(open(TF_FILE, encoding="utf-8"))
    stocks = tf["stocks"]
    print("  特征库股票数:", len(stocks))

    codes = [s["code"] for s in stocks]
    print("拉取实时报价（%d 只，批大小 %d）..." % (len(codes), BATCH))
    q = fetch_quotes(codes)
    print("  成功获取报价:", len(q))

    out_stocks = []
    counts = {"s1": 0, "s2": 0, "s3": 0}
    dbg = bool(os.environ.get("DEBUG"))
    D = {}
    def dinc(k):
        D[k] = D.get(k, 0) + 1
    for s in stocks:
        code = s["code"]
        c = s.get("c")
        qt = q.get(code)
        if not c or len(c) < 60 or not qt:
            continue
        if dbg: dinc("has_data")
        price = qt["price"]
        vr = qt["vr"]
        turnover = qt["turnover"]
        chgpct = qt["chgpct"]
        ema250 = s.get("ema250") or 0
        above_year = (ema250 > 0 and price >= ema250 * 0.95)
        if dbg:
            if vr > 2: dinc("vr>2")
            if 2 < vr < 7: dinc("vr2_7")
            if turnover > 4: dinc("to>4")
            if above_year: dinc("above_year")
            ma = compute_ma(c)
            if ma:
                m25, m60, m120 = ma
                if m25 > m60 > m120: dinc("bull")
                if price > m25 > m60: dinc("p>m25>m60")
                if golden_cross_recent(c, 25, 60, 5): dinc("gc5")
        hits = classify(c, price, vr, turnover, chgpct, above_year)
        if not hits:
            continue
        strat_list = []
        for key, score, reasons in hits:
            counts[key] += 1
            strat_list.append({
                "key": key,
                "name": STRATS[key],
                "score": score,
                "reasons": reasons,
            })
        strat_list.sort(key=lambda x: -x["score"])
        ma = compute_ma(c)
        out_stocks.append({
            "code": code,
            "name": s.get("name", ""),
            "board": s.get("board", ""),
            "industry": s.get("industry", "—"),
            "price": round(price, 2),
            "chgpct": round(chgpct, 2),
            "turnover": round(turnover, 2),
            "vr": round(vr, 2),
            "ma25": round(ma[0], 2) if ma else None,
            "ma60": round(ma[1], 2) if ma else None,
            "ma120": round(ma[2], 2) if ma else None,
            "above_year": above_year,
            "strategies": strat_list,
            "primary": strat_list[0]["key"],
        })

    # 排序：主力策略优先，再按 score
    pri_order = {"s1": 0, "s2": 1, "s3": 2}
    out_stocks.sort(key=lambda x: (pri_order.get(x["primary"], 9), -max(s["score"] for s in x["strategies"])))

    meta = {
        "generated": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        "source": "trend_features(近似走势特征库) + 腾讯实时报价",
        "universe": len(stocks),
        "matched": len(out_stocks),
        "counts": counts,
        "note": "换手率/量比取当日实时值；历史均值类条件以当日值近似；MA123以MA120代理。",
    }
    json.dump({"meta": meta, "stocks": out_stocks},
              open(OUT_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    if dbg:
        print("DEBUG gates:", json.dumps(D, ensure_ascii=False))
    print("写出:", OUT_FILE, "匹配", len(out_stocks), "只 ->", counts)


if __name__ == "__main__":
    main()
