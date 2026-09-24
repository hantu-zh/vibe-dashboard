# -*- coding: utf-8 -*-
"""gen_strategy_tabs.py — strong.html 三战法 5 日跟踪页数据生成器

从 daily_picks.json 提取 杰克船长 / 船长钓鱼战法 / 高欣季度环比增长
最近 5 个「有选股记录的交易日」的 picks（每战法独立窗口，断更战法取其
最后 5 个活跃日），复用 pick_tracker 的腾讯前复权日K，以「选出日收盘价」
为买入基准计算每只票的 T+1 / T+5 收益率（交易日口径），输出
strategy_tabs.json 供 strong.html 新增的三个 tab 渲染。

与 pick_tracker.py 的区别：pick_tracker 只出聚合胜率；本脚本出逐票明细，
且 K 线缓存按「末根日期 < 今日即刷新」策略更新（pick_tracker 的缓存只增
不刷，老缓存算不出新 T+5）。

输出: strategy_tabs.json
用法: python gen_strategy_tabs.py [--max-age-days 30]
"""
import argparse
import bisect
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pick_tracker as PT  # 复用 _fetch_kline / _market_prefix / _norm_code

DATA_FILE = PT.DATA_FILE
OUT_FILE = os.path.join(PT.paths.w("strategy_tabs.json"))
KLINE_CACHE = os.path.join(PT.paths.w("strategy_tabs_kline_cache.json"))

STRATEGIES = ["杰克船长", "船长钓鱼战法", "高欣季度环比增长"]
WINDOW_DAYS = 5  # 每战法保留的活跃交易日数

CST = timezone(timedelta(hours=8))


def today_str():
    return datetime.now(CST).strftime("%Y-%m-%d")


def load_klines(today, max_age_days):
    """返回 {code: {"dates": [...], "closes": [...]}}，末根过期即整条刷新。"""
    klines = {}
    if os.path.exists(KLINE_CACHE):
        try:
            with open(KLINE_CACHE, encoding="utf-8") as f:
                klines = json.load(f)
        except Exception:
            klines = {}
    return klines


def refresh_kline(opener, code, klines, today):
    """缓存缺失或末根日期早于 today 时重新拉取。"""
    ent = klines.get(code)
    if ent and ent.get("last", "") >= today and ent.get("dates") and ent.get("closes"):
        return ent, False
    mkt = PT._market_prefix(code)
    if not mkt:
        return ent, False
    kl = PT._fetch_kline(opener, mkt + code)
    if kl:
        ds, cs = kl
        ent = {"last": ds[-1], "dates": ds, "closes": cs}
        klines[code] = ent
        return ent, True
    return ent, False


def fmt_ret(x):
    return None if x is None else round(x, 2)


def build_tag(key, p):
    """按战法把杂的字段拼成一个可读标签列。"""
    parts = []
    if key == "杰克船长":
        for f in ("level", "action"):
            if p.get(f):
                parts.append(str(p[f]))
        if not parts and p.get("rules"):
            parts.extend(list(p["rules"])[:2])
    elif key == "高欣季度环比增长":
        if p.get("consec"):
            parts.append("连%d" % p["consec"])
        if p.get("vol_ratio"):
            parts.append("量比%.2f" % p["vol_ratio"])
        if p.get("pullback"):
            parts.append("回踩%.2f" % p["pullback"])
    return " · ".join(parts)


def batch_stats(rets1, rets5, n):
    def agg(rs):
        if not rs:
            return None
        return {"win": round(sum(1 for x in rs if x > 0) / len(rs) * 100, 1),
                "avg": round(sum(rs) / len(rs), 2)}
    return {"n": n,
            "t1": agg(rets1), "t5": agg(rets5)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=int, default=0,
                    help="窗口向前最多回看 N 个自然日（0=不限制，按活跃日取）")
    args = ap.parse_args()

    if not os.path.exists(DATA_FILE):
        print("[strategy_tabs] daily_picks.json 不存在: %s" % DATA_FILE)
        return 1
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    today = today_str()
    date_keys = sorted(k for k in data.keys()
                       if len(k) == 10 and k[4] == "-" and isinstance(data[k], dict))
    if args.max_age_days > 0:
        cutoff = (datetime.now(CST) - timedelta(days=args.max_age_days)).strftime("%Y-%m-%d")
        date_keys = [d for d in date_keys if d >= cutoff]

    # 每战法取最近 5 个有 picks 的活跃日
    windows = {}
    for key in STRATEGIES:
        days = []
        for d in reversed(date_keys):
            tv = data[d].get(key)
            picks = tv.get("picks") if isinstance(tv, dict) else tv
            if isinstance(picks, list) and picks:
                days.append(d)
            if len(days) >= WINDOW_DAYS:
                break
        windows[key] = list(reversed(days))
        print("[strategy_tabs] %s 活跃窗口: %s" % (key, windows[key] or "无记录"))

    # 收集窗口内唯一代码并拉K线（带过期刷新缓存）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    klines = load_klines(today, args.max_age_days)
    need = set()
    for key in STRATEGIES:
        for d in windows[key]:
            for p in data[d].get(key, {}).get("picks", []):
                c = PT._norm_code(p.get("code", ""))
                if c:
                    need.add(c)
    fetched = 0
    t0 = time.time()
    for code in sorted(need):
        _, did = refresh_kline(opener, code, klines, today)
        fetched += 1 if did else 0
        time.sleep(0.1)
    with open(KLINE_CACHE, "w", encoding="utf-8") as f:
        json.dump(klines, f, ensure_ascii=False, separators=(",", ":"))
    print("[strategy_tabs] 唯一股票 %d 只, 刷新 %d 只, 耗时 %.1fs"
          % (len(need), fetched, time.time() - t0))

    data_through = max((e["dates"][-1] for e in klines.values()
                        if e.get("dates")), default=None)

    # 逐票计算 T+1 / T+5
    out_strategies = []
    for key in STRATEGIES:
        days_out = []
        total = {"n": 0, "t1": [], "t5": []}
        for d in windows[key]:
            tv = data[d].get(key, {})
            picks = tv.get("picks", [])
            r1s, r5s = [], []
            picks_out = []
            for p in picks:
                code = PT._norm_code(p.get("code", ""))
                ent = klines.get(code) if code else None
                base = t1 = t5 = t5_date = None
                if ent:
                    ds, cs = ent["dates"], ent["closes"]
                    i = bisect.bisect_left(ds, d)
                    if i < len(ds):
                        base = cs[i]  # 选出日收盘（与回测口径一致）
                        if i + 1 < len(ds):
                            t1 = (cs[i + 1] / base - 1) * 100
                            r1s.append(t1)
                        if i + 5 < len(ds):
                            t5 = (cs[i + 5] / base - 1) * 100
                            r5s.append(t5)
                            t5_date = ds[i + 5]
                picks_out.append({
                    "code": code or p.get("code", ""),
                    "name": p.get("name", ""),
                    "price": p.get("price"),       # 选出时点价（盘中批价为实时价）
                    "close": base,                  # 选出日收盘（买入基准）
                    "score": p.get("score"),
                    "tag": build_tag(key, p),
                    "t1_pct": fmt_ret(t1),
                    "t5_pct": fmt_ret(t5),
                    "t5_date": t5_date,
                })
                total["n"] += 1
            st = batch_stats(r1s, r5s, len(picks_out))
            total["t1"].extend(r1s)
            total["t5"].extend(r5s)
            days_out.append({"date": d, "time": tv.get("time", ""),
                             "stats": st, "picks": picks_out})
        ts = batch_stats(total["t1"], total["t5"], total["n"])
        out_strategies.append({"key": key, "window": windows[key],
                               "summary": ts, "days": days_out})

    out = {
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "data_through": data_through,
        "note": "以选出日收盘价为买入基准；T+1/T+5 为交易日口径；「—」=尚未到期或无数据",
        "strategies": out_strategies,
    }
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    print("[OK] strategy_tabs.json 已生成")
    for s in out_strategies:
        t5 = s["summary"]["t5"]
        print("  %-10s 近%d批 %d只 | T+5: %s" % (
            s["key"], len(s["days"]), s["summary"]["n"],
            ("胜率%.1f%% 均%+.2f%%" % (t5["win"], t5["avg"])) if t5 else "样本不足"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
