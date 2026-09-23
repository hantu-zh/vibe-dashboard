#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连板股启动前20交易日特征分析。
读取 limitup_raw.txt（连板>=5 候选：code|name|date|days），
逐只拉取日K线，重算连续涨停区间（>=5板），对每段连板的"首板前20个交易日"
计算形态/量价/市值特征，输出 samples.csv 与 profile.json。

注意：本模块仅被 screen_similar.py 以函数方式复用
（get_kline / compute_window_features / is_limit_up / limit_pct），
main() 用于一次性重算画像，日常不触发。K线数据源为公开接口
（东财优先，限流时回退腾讯/新浪，见 kline_src.py），无私有依赖。
"""
import json, statistics, sys, os, re, urllib.request, urllib.parse, time

WS = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(WS, "limitup_raw.txt")
OUT_CSV = os.path.join(WS, "samples.csv")
OUT_JSON = os.path.join(WS, "profile.json")
KL_START = "2026-04-01"
KL_END = "2026-09-10"

EM_HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def _em_secid(code):
    # 6开头=上交所(1)，其余(0/3)=深交所(0)
    return ("1." if code[0] == "6" else "0.") + code


def _em_get_json(url, tries=3):
    last_err = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=EM_HEAD)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last_err = repr(e)
            time.sleep(1.5 * (i + 1))
    print("  em_get fail", url[:80], last_err)
    return {}


def limit_pct(code, name):
    if code.startswith("sh688") or code.startswith("sz300") or code.startswith("sz301"):
        return 0.20
    if "ST" in name.upper():
        return 0.05
    return 0.10


def get_kline(code):
    """日K线。返回 升序 的 {date,open,last,high,low,volume}。
    数据源：东财优先，限流时自动兜底腾讯（见 kline_src.py）。"""
    bare = code[2:] if code[:2] in ("sh", "sz") else code
    try:
        import kline_src
        bars = kline_src.get_bars(bare)
        if bars:
            bars.sort(key=lambda b: b["date"])
            return bars
    except Exception:
        pass
    # 兜底链失效时退回原始东财实现
    secid = _em_secid(bare)
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode({
        "secid": secid, "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56",
        "klt": "101", "fqt": "0", "beg": "0", "end": "20500101"})
    d = _em_get_json(url)
    kls = (d.get("data") or {}).get("klines") or []
    bars = []
    for s in kls:
        p = s.split(",")
        try:
            bars.append({
                "date": p[0],
                "open": float(p[1]),
                "last": float(p[2]),
                "high": float(p[3]),
                "low": float(p[4]),
                "volume": int(p[5]),
            })
        except Exception:
            continue
    bars.sort(key=lambda b: b["date"])
    return bars


def get_quote(code):
    """东方财富个股概况。返回 total_shares(股) 与 total_market_cap(元)。"""
    c = code[2:] if code[:2] in ("sh", "sz") else code
    secid = _em_secid(c)
    url = "https://push2.eastmoney.com/api/qt/stock/get?" + urllib.parse.urlencode({
        "secid": secid, "fields": "f43,f57,f58,f84,f85,f116,f117"})
    d = _em_get_json(url)
    info = {"total_shares": None, "total_market_cap": None}
    dd = d.get("data") or {}
    try:
        info["total_shares"] = float(dd.get("f85")) if dd.get("f85") else None
        info["total_market_cap"] = float(dd.get("f116")) if dd.get("f116") else None
    except Exception:
        pass
    return info


def is_limit_up(bar, prev_close, lp):
    if prev_close <= 0:
        return False
    ret = bar["last"] / prev_close - 1
    closed_at_high = (bar["high"] - bar["last"]) <= max(0.02, prev_close * 0.002)
    return ret >= (lp - 0.005) and closed_at_high


def find_runs(bars, lp):
    """返回连续涨停>=5的run列表，每项含 (start_idx, end_idx, length)。"""
    flags = []
    for i, b in enumerate(bars):
        pc = bars[i-1]["last"] if i > 0 else None
        flags.append(is_limit_up(b, pc, lp) if pc is not None else False)
    runs = []
    i = 0
    n = len(flags)
    while i < n:
        if flags[i]:
            j = i
            while j < n and flags[j]:
                j += 1
            length = j - i
            if length >= 5:
                runs.append((i, j-1, length))
            i = j
        else:
            i += 1
    return runs


def mean(x):
    return sum(x)/len(x) if x else 0.0


def stdev(x):
    return statistics.pstdev(x) if len(x) > 1 else 0.0


def compute_window_features(bars, launch_idx, shares):
    """launch_idx 为首板(第一个涨停)索引；窗口=其前20个交易日。"""
    win_start = launch_idx - 20
    if win_start < 1:
        return None
    w = bars[win_start:launch_idx]  # 长度~20
    m = len(w)
    if m < 15:
        return None
    closes = [b["last"] for b in w]
    highs = [b["high"] for b in w]
    lows = [b["low"] for b in w]
    vols = [b["volume"] for b in w]
    exch = [float(b.get("exchange", 0) or 0) for b in w]
    daily_rets = [closes[k]/closes[k-1]-1 for k in range(1, m)]
    pre_ret20 = closes[-1]/closes[0] - 1
    pre_vol = stdev(daily_rets)
    pre_amp = (max(highs)-min(lows))/mean(closes)
    vol_last5 = mean(vols[-5:])
    vol_first10 = mean(vols[:10])
    vol_ratio = vol_last5/vol_first10 if vol_first10 else 0
    vol_min_ratio = min(vols)/mean(vols)
    # 最大回撤
    peak = closes[0]; mdd = 0
    for c in closes:
        peak = max(peak, c)
        mdd = max(mdd, (peak-c)/peak)
    # 跳空高开(>=3%)
    gap_ups = 0
    for k in range(1, m):
        if w[k]["open"] > w[k-1]["last"]*1.03:
            gap_ups += 1
    # MA（基于启动前已有数据）
    def ma(idx_back):
        s = launch_idx - idx_back
        e = launch_idx  # 不含首板
        seg = [bars[t]["last"] for t in range(s, e)]
        return mean(seg)
    ma5 = ma(5); ma10 = ma(10); ma20 = ma(20)
    ma_align = 1 if (ma5 > ma10 > ma20) else 0
    dist_from_high = (closes[-1] - max(highs))/max(highs)
    last3_ret = closes[-1]/closes[-4] - 1 if m >= 4 else 0
    last_day_vol_ratio = vols[-1]/mean(vols)
    launch_price = bars[launch_idx]["last"]
    launch_mcap_yi = (launch_price * shares / 1e8) if shares else None
    return {
        "pre_ret20": pre_ret20,
        "pre_vol": pre_vol,
        "pre_amp": pre_amp,
        "vol_ratio_l5_f10": vol_ratio,
        "vol_min_ratio": vol_min_ratio,
        "max_dd": mdd,
        "gap_ups": gap_ups,
        "ma_align": ma_align,
        "dist_from_high": dist_from_high,
        "last3_ret": last3_ret,
        "last_day_vol_ratio": last_day_vol_ratio,
        "exch_avg": mean(exch),
        "launch_price": launch_price,
        "launch_mcap_yi": launch_mcap_yi,
        "ma5": ma5, "ma10": ma10, "ma20": ma20,
    }


def main():
    # 候选去重
    cands = {}
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 4:
                continue
            code, name, date, days = parts[0], parts[1], parts[2], parts[3]
            cands.setdefault(code, {"name": name, "max_days": 0, "dates": []})
            cands[code]["max_days"] = max(cands[code]["max_days"], int(days))
            cands[code]["dates"].append(date)
    print(f"候选去重后: {len(cands)} 只")

    samples = []
    profiles_meta = []
    for code, meta in cands.items():
        name = meta["name"]
        lp = limit_pct(code, name)
        bars = get_kline(code)
        q = get_quote(code)
        shares = q.get("total_shares")
        if not bars:
            print(f"  skip {code} {name}: 无K线")
            continue
        runs = find_runs(bars, lp)
        if not runs:
            print(f"  {code} {name}: K线重算无>=5连板(扫描可能已标记但有断层/复权)")
            continue
        for (s, e, length) in runs:
            feat = compute_window_features(bars, s, shares)
            if not feat:
                continue
            launch_date = bars[s]["date"]
            end_date = bars[e]["date"]
            rec = {
                "code": code, "name": name, "limit_pct": lp,
                "launch_date": launch_date, "end_date": end_date,
                "run_length": length, "max_obs_days": meta["max_days"],
                "total_shares": shares,
            }
            rec.update(feat)
            samples.append(rec)
        print(f"  {code} {name}: {len(runs)} 段连板")

    # 写出CSV
    if samples:
        keys = list(samples[0].keys())
        with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(keys) + "\n")
            for s in samples:
                f.write(",".join(str(s.get(k, "")) for k in keys) + "\n")

    # 聚合画像
    def median(vals):
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        n = len(vals)
        if n % 2:
            return vals[n//2]
        return (vals[n//2-1]+vals[n//2])/2

    def pct_cond(vals, fn):
        vals = [v for v in vals if v is not None]
        if not vals:
            return None
        return round(100*sum(1 for v in vals if fn(v))/len(vals), 1)

    numeric_keys = ["pre_ret20", "pre_vol", "pre_amp", "vol_ratio_l5_f10", "vol_min_ratio",
                    "max_dd", "gap_ups", "ma_align", "dist_from_high", "last3_ret",
                    "last_day_vol_ratio", "exch_avg", "launch_mcap_yi"]
    profile = {"n_samples": len(samples), "n_stocks": len(cands)}
    for k in numeric_keys:
        vals = [s[k] for s in samples]
        profile[k] = {"median": median(vals), "mean": round(mean(vals), 4) if vals else None,
                      "min": min(v for v in vals if v is not None) if vals else None,
                      "max": max(v for v in vals if v is not None) if vals else None}
    profile["pct_vol_contract"] = pct_cond([s["vol_ratio_l5_f10"] for s in samples], lambda v: v < 1.0)
    profile["pct_ma_align"] = pct_cond([s["ma_align"] for s in samples], lambda v: v == 1)
    profile["pct_small_cap_30"] = pct_cond([s["launch_mcap_yi"] for s in samples], lambda v: v is not None and v < 30)
    profile["pct_small_cap_50"] = pct_cond([s["launch_mcap_yi"] for s in samples], lambda v: v is not None and v < 50)
    profile["pct_pre_ret_neg"] = pct_cond([s["pre_ret20"] for s in samples], lambda v: v < 0)
    profile["pct_pre_ret_within_10"] = pct_cond([s["pre_ret20"] for s in samples], lambda v: -0.10 <= v <= 0.10)
    profile["pct_last3_up"] = pct_cond([s["last3_ret"] for s in samples], lambda v: v > 0)
    profile["pct_low_amp"] = pct_cond([s["pre_amp"] for s in samples], lambda v: v < 0.20)

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"profile": profile, "samples": samples}, f, ensure_ascii=False, indent=2)
    print(f"\n完成。样本段数={len(samples)}，候选股={len(cands)}")
    print(f"画像摘要: 中位数启动前20日收益={profile['pre_ret20']['median']}, "
          f"波动率={profile['pre_vol']['median']}, 振幅={profile['pre_amp']['median']}, "
          f"量比(后5/前10)={profile['vol_ratio_l5_f10']['median']}")
    print(f"小市值(<30亿)占比={profile['pct_small_cap_30']}%, 量缩占比={profile['pct_vol_contract']}%, MA多头占比={profile['pct_ma_align']}%")


if __name__ == "__main__":
    main()
