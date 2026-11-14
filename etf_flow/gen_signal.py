# -*- coding: utf-8 -*-
"""资金流温度（历史分位预警）生成器。

指标定义：
  ratio = 核心池（20 只）滚动 20 日累计净申赎 ÷ 核心池总市值（份额×收盘）
        —— 消除 ETF 规模逐年膨胀后的不可比问题。

信号判定（历史回测 2018-08 ~ 至今）：
  底部承接区：ratio ≥ 90 分位 且 指数 ≤ 35 分位（120 日区间）
              → n=96, T+20 +2.3%(胜68%), T+120 +3.4%(胜62%), 后60最深 -3.6%
  顶部出逃区：ratio ≤ 10 分位 且 指数 ≥ 65 分位
              → n=124, T+20 -1.3%(胜31%), T+120 -3.0%(胜22%), 后60最深 -6.6%

输出 web/data/signal.json（约 120KB），随快照一同提交。
"""
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 与 collect.py 同款 env 覆盖：仓库布局下脚本在 etf_flow/、产物写 etf/data/signal.json
sys.path.insert(0, os.environ.get("ETF_SCRIPTS", str(ROOT / "scripts")))
from collect import cached, fetch_daily, fetch_index, fetch_shares  # noqa: E402

CONFIG = Path(os.environ.get("ETF_CONFIG", ROOT / "config" / "universe.json"))
OUT = Path(os.environ.get("ETF_SIGNAL", ROOT / "web" / "data" / "signal.json"))


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

    def stats_of(sel):
        if not sel:
            return None
        out = {"n": len(sel)}
        for k, key in ((20, "t20"), (60, "t60"), (120, "t120")):
            v = [fwd(i, k) for i in sel]
            v = [x for x in v if x is not None]
            if v:
                out[key] = round(sum(v) / len(v) * 100, 1)
                out[key + "w"] = round(sum(1 for x in v if x > 0) / len(v) * 100)
        md = [worst(i, 60) for i in sel]
        md = [x for x in md if x is not None]
        if md:
            out["md60"] = round(sum(md) / len(md) * 100, 1)
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

    sig = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "def": "核心池20只 · 滚动20日累计净申赎 ÷ 总市值（%）· 历史分位",
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
        "now": {
            "date": days[i],
            "ratio": None if now_ratio is None else round(now_ratio, 3),
            "pr": None if now_pr is None else round(now_pr),
            "pidx": None if now_pidx is None else round(now_pidx),
            "flow20": round(flow20[i] / 1e8, 1) if flow20[i] is not None else None,
            "state": state,
            "light": light,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(sig, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    log("OK %s | 当前 ratio=%.2f%% pr=%.0f%% pidx=%.0f%% → %s" % (
        OUT, now_ratio or 0, now_pr or -1, now_pidx or -1, state))
    log("bottom n=%s | top n=%s" % (
        sig["stats"]["bottom"] and sig["stats"]["bottom"]["n"],
        sig["stats"]["top"] and sig["stats"]["top"]["n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
