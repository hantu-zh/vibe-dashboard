# -*- coding: utf-8 -*-
"""king/picks.json 刷新脚本（GitHub Actions 每日运行）。

用与 bt/engine.py 完全相同的公式口径，在「最近 N 根不复权日线」窗口上重算：
  · 最新交易日 / 最近出票日的主口径选股
  · 最近 40 条出票记录
  · 备选池（关闭大盘过滤）
  · 近 60 个交易日去重清单
不需要 8 年历史，也不需要 results.json —— 只读最近 260 根日线即可覆盖全部指标窗口
（EMA50 收敛 + ATR14 + 12 日金叉窗口，260 根足够）。

数据源：新浪 hs_a 列表 + 腾讯不复权日K（两者在 GitHub Actions 均可访问）。
失败时保留旧 picks.json（不写文件），避免把线上刷成空数据。
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
OUT_DEFAULT = ROOT / "king" / "picks.json"

# 与 results.json 里 final.params 保持一致（改公式时两处同步）
P = dict(cross_win=12, hhv_n=6, hhv_ratio=0.92, price_cap=28.0, ma20_flat=True,
         up_days=4, bias_max=0.05, atr_max=0.035, mkt_filter=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def _get(url, referer, timeout=20, encoding=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
        raw = r.read()
    return raw.decode(encoding or "utf-8-sig", "replace")


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 股票池
def fetch_universe(retries=3):
    base = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=%d&num=100&sort=symbol&asc=1&node=hs_a")
    out, seen = [], set()
    empty_streak = 0
    for page in range(1, 90):
        got = None
        for k in range(retries):
            try:
                got = json.loads(_get(base % page, "https://finance.sina.com.cn",
                                      timeout=20, encoding="utf-8"))
                break
            except Exception as e:
                if k == retries - 1:
                    log("  [warn] universe page %d: %s" % (page, e))
                time.sleep(1 + k)
        if not got:
            empty_streak += 1
            if empty_streak >= 3:
                break
            continue
        empty_streak = 0
        for x in got:
            sym = str(x.get("symbol", ""))
            code = str(x.get("code", ""))
            name = str(x.get("name", "")).strip()
            if not sym.startswith(("sh6", "sz0", "sz3")):
                continue
            if sym in seen:
                continue
            seen.add(sym)
            out.append({"sym": sym, "code": code, "name": name})
        if len(got) < 100:
            break
    out.sort(key=lambda r: r["sym"])
    return out


# ---------------------------------------------------------------- K线
def parse_bars(rows):
    """腾讯 K 线行 -> (dates[], o,h,l,c,v arrays)

    注意腾讯字段顺序是 [日期, 开, 收, 高, 低, 量]——收盘在第三位，不是第五位。
    （2026-11-15 实测：2026-11-13 该行 ['4047.77','4016.29','4052.06','4004.50'] 分别对应
     开/收/高/低，按 OHLC 顺序解析会把最低价当成收盘价。）
    """
    ds, o, h, l, c, v = [], [], [], [], [], []
    for r in rows:
        try:
            ds.append(str(r[0]))
            o.append(float(r[1]))
            c.append(float(r[2]))
            h.append(float(r[3]))
            l.append(float(r[4]))
            v.append(float(r[5]))
        except (TypeError, ValueError, IndexError):
            continue
    return ds, (np.array(o, float), np.array(h, float), np.array(l, float),
                np.array(c, float), np.array(v, float))


def fetch_kline(sym, days=260, retries=2, raw=True):
    """腾讯日K。raw=True -> 不复权（与回测口径一致）"""
    if raw:
        url = ("https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=%s,day,,,%d"
               % (sym, days))
        keys = ("day",)
    else:
        url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq"
               % (sym, days))
        keys = ("qfqday", "day")
    for k in range(retries + 1):
        try:
            j = json.loads(_get(url, "https://finance.qq.com", timeout=20))
            d = (j.get("data") or {}).get(sym) or {}
            for kk in keys:
                rows = d.get(kk) or []
                if rows:
                    return parse_bars(rows)
            return None
        except Exception:
            if k == retries:
                return None
            time.sleep(0.6 + k * 0.8)


# ---------------------------------------------------------------- 指标
def ema(x, n):
    a = 2.0 / (n + 1.0)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x, n):
    out = np.full_like(x, np.nan)
    if len(x) >= n:
        cs = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def signals_for_stock(o, h, l, c, v, p=P):
    """返回 {date_index: True} 的布尔数组（与 date 列表对齐），以及辅助指标。"""
    n = len(c)
    res = np.zeros(n, bool)
    if n < 60:
        return res, {}
    ma5, ma10, ma20 = sma(c, 5), sma(c, 10), sma(c, 20)
    e50 = ema(c, 50)

    prev = np.roll(c, 1)
    prev[0] = np.nan
    cross_5_10 = (ma5 > ma10) & (prev_ma(ma5) <= prev_ma(ma10))
    cross_5_20 = (ma5 > ma20) & (prev_ma(ma5) <= prev_ma(ma20))
    cross_10_20 = (ma10 > ma20) & (prev_ma(ma10) <= prev_ma(ma20))

    def cnt_in(mask, w):
        """近 w 日内该事件至少出现一次（含当日）"""
        out = np.zeros(n, bool)
        cs = np.cumsum(np.insert(mask.astype(np.int16), 0, 0))
        for t in range(n):
            lo = max(0, t - w + 1)
            out[t] = (cs[t + 1] - cs[lo]) > 0
        return out

    c1 = cnt_in(cross_5_10, int(p["cross_win"]))
    c2 = cnt_in(cross_5_20, int(p["cross_win"]))
    c3 = cnt_in(cross_10_20, int(p["cross_win"]))

    # HHV(H, n)
    hn = int(p["hhv_n"])
    hh = np.full(n, np.nan)
    for t in range(n):
        lo = max(0, t - hn + 1)
        hh[t] = h[lo:t + 1].max()

    up = np.zeros(n, bool)
    up[1:] = c[1:] > c[:-1]
    upd = int(p["up_days"])
    upn = np.zeros(n, bool)
    for t in range(n):
        if t >= upd:
            upn[t] = up[t - upd + 1:t + 1].all()

    bias = c / ma20 - 1.0

    # TR 不能给首日用 NaN 播种：sma() 走 cumsum，一处 NaN 会污染整条序列
    pc = np.empty(n)
    pc[0] = c[0]
    pc[1:] = c[:-1]
    tr = np.maximum.reduce([h - l, np.abs(h - pc), np.abs(l - pc)])
    atr = sma(tr, 14) / c

    with np.errstate(invalid="ignore"):
        m = (c1 & c2 & c3 & (ma5 > ma20) & (c > e50) & (c > hh * p["hhv_ratio"])
             & upn & (c < p["price_cap"]) & (bias <= p["bias_max"]) & (atr <= p["atr_max"]))
    if p.get("ma20_flat", True):
        m &= (ma20 >= prev_ma(ma20))
    m &= np.isfinite(c) & np.isfinite(ma20) & np.isfinite(e50) & np.isfinite(tr)
    res = np.where(np.isnan(m), False, m)

    r20 = np.full(n, np.nan)
    if n > 20:
        r20[20:] = c[20:] / c[:-20] - 1.0
    aux = {"vol_ratio": safe_div(v, sma(v, 20)),
           "bias20": bias,
           "ret20": r20}
    return res, aux


def prev_ma(x):
    y = np.roll(x, 1)
    y[0] = np.nan
    return y


def safe_div(a, b):
    with np.errstate(invalid="ignore", divide="ignore"):
        r = a / b
    return np.where(np.isfinite(r), r, np.nan)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--days", type=int, default=260)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    t0 = time.time()
    log("[1/4] 股票池 ...")
    uni = fetch_universe()
    log("      %d 只" % len(uni))
    if len(uni) < 3000:
        log("FATAL 股票池数量异常（%d），保留旧 picks.json 不覆盖" % len(uni))
        return 2
    if args.limit:
        uni = uni[:args.limit]

    log("[2/4] 沪深300 ...")
    idx = fetch_kline("sh000300", days=args.days)
    if not idx:
        log("FATAL 取不到沪深300，保留旧 picks.json")
        return 2
    idates, (io, ih, il, ic, iv) = idx
    idx_ma20 = sma(ic, 20)
    mkt_up = bool(np.isfinite(idx_ma20[-1]) and ic[-1] > idx_ma20[-1])
    log("      %s 收 %.2f / MA20 %.2f -> mkt_up=%s"
        % (idates[-1], ic[-1], idx_ma20[-1], mkt_up))
    cal = idates  # 交易日历以沪深300为准
    cpos = {d: i for i, d in enumerate(cal)}

    log("[3/4] 个股日线（%d 只，%d 并发）..." % (len(uni), args.workers))
    done = [0]
    sig_by_date = {}   # date -> list[(code,name,c,vr,bias,r20)]
    got = [0]

    def work(item):
        sym, code, name = item["sym"], item["code"], item["name"]
        kl = fetch_kline(sym, days=args.days)
        done[0] += 1
        if done[0] % 500 == 0:
            log("      %d/%d  用时 %.0fs" % (done[0], len(uni), time.time() - t0))
        if not kl:
            return None
        ds, (o, h, l, c, v) = kl
        if len(c) < 60:
            return None
        m, aux = signals_for_stock(o, h, l, c, v)
        out = []
        for t in np.where(m)[0]:
            dt = ds[t]
            if dt not in cpos:
                continue
            out.append((dt, {
                "code": code, "name": name, "close": round(float(c[t]), 2),
                "vol_ratio": round(float(aux["vol_ratio"][t]), 2)
                if np.isfinite(aux["vol_ratio"][t]) else 0.0,
                "bias20": round(float(aux["bias20"][t]), 4),
                "ret20": round(float(aux["ret20"][t]), 4),
            }))
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, r in enumerate(ex.map(work, uni)):
            if r is None:
                continue
            got[0] += 1
            for dt, rec in r:
                sig_by_date.setdefault(dt, []).append(rec)
    log("      有效标的 %d/%d，命中日期 %d 个" % (got[0], len(uni), len(sig_by_date)))

    # 大盘过滤：逐日判定（沪深300 当日收盘 > 当日 MA20 才允许该日出信号）
    mkt_by_date = {dt: bool(np.isfinite(idx_ma20[i]) and ic[i] > idx_ma20[i])
                   for i, dt in enumerate(cal)}
    if P["mkt_filter"]:
        sig_main = {dt: rows for dt, rows in sig_by_date.items() if mkt_by_date.get(dt)}
    else:
        sig_main = dict(sig_by_date)
    log("      大盘过滤开启日占比 %.1f%%（逐日判定）"
        % (100.0 * sum(mkt_by_date.values()) / max(1, len(mkt_by_date))))

    def day_list(store, dt):
        rows = list(store.get(dt, []))
        rows.sort(key=lambda x: -x["vol_ratio"])
        return rows

    last_day = cal[-1]
    last_sig = max(sig_main) if sig_main else None
    gap = (len(cal) - 1 - cpos[last_sig]) if last_sig else None

    # 最近 40 条
    records = []
    for dt in sorted(sig_main, reverse=True):
        for x in day_list(sig_main, dt):
            records.append({"date": dt, **x})
            if len(records) >= 40:
                break
        if len(records) >= 40:
            break

    # 备选池：关闭大盘过滤的最新交易日
    fallback = day_list(sig_by_date, last_day)

    # 近 60 个交易日去重（关闭大盘过滤）
    recent = {}
    for dt in cal[-60:]:
        for x in sig_by_date.get(dt, []):
            recent[x["code"]] = {"date": dt, **x}
    recent60 = sorted(recent.values(), key=lambda r: (-int(r["date"].replace("-", "")), r["code"]))

    n_sig_days = len(sig_main)
    total = sum(len(v) for v in sig_main.values())
    stat = {
        "n_signal_days": n_sig_days,
        "n_days": len(cal),
        "avg_per_signal_day": round(total / n_sig_days, 2) if n_sig_days else 0,
        "total_signals": total,
        "recent60_unique": len(recent60),
        "window_days": args.days,
        "coverage": "%d/%d" % (got[0], len(uni)),
    }

    bj = timezone(timedelta(hours=8))
    out = {
        "updated_at": datetime.now(bj).strftime("%Y-%m-%d %H:%M"),
        "data_date": last_day,
        "window_start": cal[0],
        "params": P,
        "status": {
            "last_signal_date": last_sig,
            "gap_days": gap,
            "data_last": last_day,
            "mkt": {"date": cal[-1], "close": round(float(ic[-1]), 2),
                    "ma20": round(float(idx_ma20[-1]), 2), "above_ma20": mkt_up,
                    "diff_pct": round(float(ic[-1] / idx_ma20[-1] - 1) * 100, 2)},
            "market_filter_on": True,
            "open_position": bool(gap == 0) if gap is not None else False,
        },
        "records": records,
        "recent60": recent60,
        "fallback": {"date": last_day, "picks": fallback},
        "stat": stat,
        "note": "由 king_picks.py 每日自动重算；窗口 %d 根日线（%s ~ %s），大盘过滤=沪深300 收盘>MA20。"
                % (args.days, cal[0], cal[-1]),
    }

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(json.dumps(out, ensure_ascii=False, indent=1).encode("utf-8"))
    log("[4/4] 写入 %s  %d bytes  用时 %.0fs"
        % (dest, dest.stat().st_size, time.time() - t0))
    log("      last_signal=%s gap=%s records=%d recent60=%d fallback=%d mkt_up=%s"
        % (last_sig, gap, len(records), len(recent60), len(fallback), mkt_up))
    return 0


if __name__ == "__main__":
    sys.exit(main())
