# -*- coding: utf-8 -*-
"""资金流温度（历史分位预警）生成器。

指标定义：
  ratio = 核心池（20 只）滚动 20 日累计净申赎 ÷ 核心池总市值（份额×收盘）
        —— 消除 ETF 规模逐年膨胀后的不可比问题。

信号判定：
  底部承接区：ratio ≥ 90 分位 且 指数 ≤ 35 分位（120 日区间）
  顶部出逃区：ratio ≤ 10 分位 且 指数 ≥ 65 分位

统计口径（重要）：
  信号天然成段连续（一个区段可持续 1~35 个交易日），若逐日打点，同一波信号会被
  重复计数几十次，样本看似上百、实则只有十几个独立事件，胜率与中位数会被系统性
  放大。因此 stats / dist 一律按「连续区段」去重——每个区段只取首日（即真正可执行
  的入场点），ndays 保留逐日天数供对照。

输出 目标路径（默认 web/data/signal.json，仓库布局下 etf/data/signal.json）。
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 与 collect.py 同款 env 覆盖：仓库布局下脚本在 etf_flow/、产物写 etf/data/signal.json
sys.path.insert(0, os.environ.get("ETF_SCRIPTS", str(ROOT / "scripts")))
from collect import cached, fetch_daily, fetch_index, fetch_shares  # noqa: E402

CONFIG = Path(os.environ.get("ETF_CONFIG", ROOT / "config" / "universe.json"))
OUT = Path(os.environ.get("ETF_SIGNAL", ROOT / "web" / "data" / "signal.json"))

CALIBER = "每个连续信号区只算一次（取出现首日），避免同一波信号被逐日重复计样"


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    core = [c.split(".") for c in cfg["core"]]
    codes = [c[0] for c in core]
    log = print

    log("[1/3] 核心 20 只份额全历史 ...")
    shares = cached("sig_shares", lambda: fetch_shares(codes, 4000))

    log("[2/3] 核心 20 只日线 2000 根 ...")
    daily = cached("sig_daily",
                   lambda: {c: fetch_daily(c, m, 2000) for c, m in core})

    log("[3/3] 上证指数 ...")
    idx = cached("sig_idx", lambda: fetch_index(2000))

    days = sorted(d for d in idx if d)
    px = {c: {r["d"]: r for r in rows} for c, rows in daily.items()}

    # 逐日：成交额、净申赎（Δ份额×当日收盘）、总市值
    amt, flow, mv = [], [], []
    ff = {c: None for c in codes}
    prev = {c: None for c in codes}
    for d in days:
        a = f = m = 0.0
        for c in codes:
            s = shares.get(c, {}).get(d)
            if s is not None:
                if prev[c] is not None:
                    p = px[c].get(d) or {}
                    if p.get("close"):
                        f += (s - prev[c]) * p["close"]
                prev[c] = s
                ff[c] = s
            p = px[c].get(d) or {}
            if ff[c] and p.get("close"):
                m += ff[c] * p["close"]
            if p.get("amount"):
                a += p["amount"]
        amt.append(a)
        flow.append(f)
        mv.append(m if m > 0 else None)

    n = len(days)
    flow20 = [None] * n
    for i in range(19, n):
        seg = flow[i - 19:i + 1]
        if all(x is not None for x in seg):
            flow20[i] = sum(seg)
    ratio = [None] * n
    for i in range(n):
        if flow20[i] is not None and mv[i]:
            ratio[i] = flow20[i] / mv[i] * 100  # %

    # 全历史分位
    vals = sorted(x for x in ratio if x is not None)

    def pct(x):
        if x is None:
            return None
        lo, hi = 0, len(vals)
        while lo < hi:
            mid = (lo + hi) // 2
            if vals[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        return lo / len(vals) * 100

    pr = [pct(x) for x in ratio]

    # 指数 120 日分位
    import bisect
    pidx = [None] * n
    for i in range(119, n):
        seg = [idx[days[k]] for k in range(i - 119, i + 1) if idx[days[k]]]
        if len(seg) == 120:
            j = bisect.bisect_left(sorted(seg), idx[days[i]])
            pidx[i] = j / 119 * 100

    # 前向收益统计
    def fwd(i, k):
        j = i + k
        return (idx[days[j]] / idx[days[i]] - 1.0) if j < n and idx[days[i]] else None

    def worst(i, k):
        j = min(i + k, n - 1)
        v = [idx[days[t]] for t in range(i + 1, j + 1) if idx[days[t]]]
        return min(v) / idx[days[i]] - 1.0 if v and idx[days[i]] else None

    def quantile(v, q):
        s = sorted(v)
        k = max(0, min(len(s) - 1, int(round(q / 100.0 * (len(s) - 1)))))
        return s[k]

    def zone_starts(sel):
        """连续区段的首日下标。去重口径的唯一入口。"""
        if not sel:
            return []
        out, p = [sel[0]], sel[0]
        for i in sel[1:]:
            if i != p + 1:
                out.append(i)
            p = i
        return out

    # 收益分布（直方图用）：按区段去重，给明细不只给均值
    def dist_of(sel):
        zs = zone_starts(sel)
        if not zs:
            return None
        out = {}
        for k, key in ((20, "t20"), (120, "t120")):
            v = [fwd(i, k) for i in zs]
            v = [round(x * 100, 1) for x in v if x is not None]
            if not v:
                continue
            out[key] = {
                "vals": v,
                "n": len(v),
                "ndays": len(sel),
                "med": round(quantile(v, 50), 1),
                "p10": round(quantile(v, 10), 1),
                "p90": round(quantile(v, 90), 1),
                "min": round(min(v), 1),
                "max": round(max(v), 1),
            }
        return out

    # 连续区间（周线 K 线上画背景带用）
    def zones_of(sel):
        if not sel:
            return []
        zs, s, p = [], sel[0], sel[0]
        for i in sel[1:]:
            if i == p + 1:
                p = i
                continue
            zs.append({"s": days[s], "e": days[p], "n": p - s + 1})
            s = p = i
        zs.append({"s": days[s], "e": days[p], "n": p - s + 1})
        return zs

    # 统计：n 为区段数（独立事件），ndays 为逐日天数（对照用）
    # tXX = 均值（兼容旧字段），tXXm = 中位数（面板主口径），tXXw = 胜率
    def stats_of(sel):
        zs = zone_starts(sel)
        if not zs:
            return None
        out = {"n": len(zs), "ndays": len(sel)}
        for k, key in ((20, "t20"), (60, "t60"), (120, "t120")):
            v = [fwd(i, k) for i in zs]
            v = [x for x in v if x is not None]
            if v:
                out[key] = round(sum(v) / len(v) * 100, 1)
                out[key + "m"] = round(quantile(v, 50) * 100, 1)
                out[key + "w"] = round(sum(1 for x in v if x > 0) / len(v) * 100)
        md = [worst(i, 60) for i in zs]
        md = [x for x in md if x is not None]
        if md:
            out["md60"] = round(sum(md) / len(md) * 100, 1)
            out["md60m"] = round(quantile(md, 50) * 100, 1)
        return out

    start = 140
    bot = [i for i in range(start, n)
           if pr[i] is not None and pr[i] >= 90 and pidx[i] is not None and pidx[i] <= 35]
    top = [i for i in range(start, n)
           if pr[i] is not None and pr[i] <= 10 and pidx[i] is not None and pidx[i] >= 65]

    levels = {}
    for q, key in ((10, "p10"), (25, "p25"), (50, "p50"), (75, "p75"), (90, "p90")):
        levels[key] = round(vals[int(len(vals) * q / 100)], 3)

    i = n - 1
    now_ratio, now_pr, now_pidx = ratio[i], pr[i], pidx[i]
    if now_pr is not None and now_pr >= 90 and (now_pidx or 100) <= 35:
        state, light = "bottom", "buy"
    elif now_pr is not None and now_pr <= 10 and (now_pidx or 0) >= 65:
        state, light = "top", "sell"
    elif now_pr is not None and now_pr >= 75 and (now_pidx or 100) <= 50:
        state, light = "warm", "buy"
    elif now_pr is not None and now_pr <= 25 and (now_pidx or 0) >= 50:
        state, light = "cool", "sell"
    else:
        state, light = "neutral", "idle"

    # 失效线：近 20 个交易日最低收盘（收盘口径，对应「跌破近月低点」）
    rec = [(days[t], idx[days[t]]) for t in range(max(0, n - 20), n) if idx[days[t]]]
    invalid = None
    if rec:
        dmin, pmin = min(rec, key=lambda x: x[1])
        invalid = {"date": dmin, "price": round(pmin, 2)}

    # 信号连续区间：周线 K 线背景带 + 当前区间续期天数
    zones = {"bottom": zones_of(bot), "top": zones_of(top)}
    cur_zone = None
    zl = zones["bottom"] if state == "bottom" else (zones["top"] if state == "top" else [])
    if zl and zl[-1]["e"] == days[-1]:
        cur_zone = dict(zl[-1])

    sig = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "def": "核心池20只 · 滚动20日累计净申赎 ÷ 总市值（%）· 历史分位",
        "caliber": CALIBER,
        "dates": days[start:],
        "ratio": [None if x is None else round(x, 3) for x in ratio[start:]],
        "pr": [None if x is None else round(x) for x in pr[start:]],
        "pidx": [None if x is None else round(x) for x in pidx[start:]],
        "levels": levels,
        "marks": {
            "bottom": [days[i] for i in bot],
            "top": [days[i] for i in top],
        },
        "stats": {"bottom": stats_of(bot), "top": stats_of(top)},
        "dist": {"bottom": dist_of(bot), "top": dist_of(top)},
        "zones": zones,
        "now": {
            "date": days[i],
            "ratio": None if now_ratio is None else round(now_ratio, 3),
            "pr": None if now_pr is None else round(now_pr),
            "pidx": None if now_pidx is None else round(now_pidx),
            "flow20": round(flow20[i] / 1e8, 1) if flow20[i] is not None else None,
            "state": state,
            "light": light,
            "zone": cur_zone,
            "invalid": invalid,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(sig, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    log("OK %s | 当前 ratio=%.2f%% pr=%.0f%% pidx=%.0f%% → %s" % (
        OUT, now_ratio or 0, now_pr or -1, now_pidx or -1, state))
    for side in ("bottom", "top"):
        st = sig["stats"][side]
        log("%-6s 区段 %s 个（逐日 %s 天）| T+20 中位 %s%% | T+60 中位 %s%% 胜率 %s%% | T+120 中位 %s%%" % (
            side, st and st["n"], st and st["ndays"],
            st and st.get("t20m"), st and st.get("t60m"), st and st.get("t60w"),
            st and st.get("t120m")))
    log("zones b/t=%d/%d | now zone=%s | invalid=%s" % (
        len(zones["bottom"]), len(zones["top"]), cur_zone, invalid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
