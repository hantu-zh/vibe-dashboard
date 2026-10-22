# -*- coding: utf-8 -*-
"""统一分析流程：多周期缠论 + 波浪 + 支撑压力量化 + 事件回测。"""
import json, io, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chan, elliott, levels

HORIZONS = (5, 10, 20)


def load(sym, period):
    with io.open(os.path.join(HERE, "data", "%s_%s.json" % (sym, period)), encoding="utf-8") as f:
        return json.load(f)


def _swings(r):
    """笔端点 + 进行中段。若方向与最后一个摆动点相同，则合并取极值，
    保证序列顶底交替（否则波浪匹配会因出现连续两个底而整体失败）。"""
    sw = elliott.swings_from_bi(r["bi"])
    p = r.get("pending")
    if not p:
        return sw
    t = "bottom" if p["dir"] == "down" else "top"
    v = p["low"] if p["dir"] == "down" else p["high"]
    if sw and sw[-1][3] == t:
        last = sw[-1]
        if (t == "bottom" and v < last[2]) or (t == "top" and v > last[2]):
            sw[-1] = (p["e_idx"], p["to_date"], v, t)
    else:
        sw.append((p["e_idx"], p["to_date"], v, t))
    return sw


def backtest_points(rows, points, horizons=HORIZONS):
    """缠论买卖点事件回测：买点看涨为正，卖点看跌为正。"""
    n = len(rows)
    agg = {}
    for p in points:
        i = p["idx"]
        kind = p["kind"]
        sign = 1 if kind.endswith("buy") else -1
        for h in horizons:
            j = i + h
            if j >= n:
                continue
            ret = sign * (rows[j]["close"] - rows[i]["close"]) / rows[i]["close"]
            agg.setdefault(kind, {}).setdefault(h, []).append(ret)
    out = {}
    for kind in sorted(agg):
        out[kind] = {}
        for h in horizons:
            xs = agg[kind].get(h, [])
            if not xs:
                out[kind][h] = {"n": 0}
                continue
            s = sorted(xs)
            out[kind][h] = {
                "n": len(xs),
                "win": sum(1 for x in xs if x > 0) / len(xs),
                "mean": sum(xs) / len(xs),
                "med": s[len(s) // 2],
                "worst": s[0], "best": s[-1],
            }
    return out


def baseline(rows, horizons=HORIZONS):
    """基线：任意一天买入并持有的表现 —— 用于判断信号是否真有超额，而非市场整体上行。"""
    n = len(rows)
    out = {}
    for h in horizons:
        xs = [(rows[i + h]["close"] - rows[i]["close"]) / rows[i]["close"]
              for i in range(n - h)]
        if xs:
            s = sorted(xs)
            out[h] = {"n": len(xs), "win": sum(1 for x in xs if x > 0) / len(xs),
                      "mean": sum(xs) / len(xs), "med": s[len(s) // 2]}
        else:
            out[h] = {"n": 0}
    return out


def backtest_breakdown(rows, zs_list, horizons=HORIZONS):
    """「收盘跌破中枢下沿」事件回测 —— 对应当前所处的破位状态。"""
    n = len(rows)
    ev = []
    for z in zs_list:
        for i in range(z["e_idx"] + 1, n):
            if rows[i]["close"] < z["zd"]:
                if i not in ev:
                    ev.append(i)
                break
    ev.sort()
    out = {}
    for h in horizons:
        xs = []
        for i in ev:
            j = i + h
            if j >= n:
                continue
            xs.append((rows[j]["close"] - rows[i]["close"]) / rows[i]["close"])
        if xs:
            s = sorted(xs)
            out[h] = {"n": len(xs), "win": sum(1 for x in xs if x > 0) / len(xs),
                      "mean": sum(xs) / len(xs), "med": s[len(s) // 2],
                      "worst": s[0], "best": s[-1]}
        else:
            out[h] = {"n": 0}
    return {"events": len(ev), "by_h": out, "idxs": ev}


def full_analyze(sym, name):
    day = load(sym, "day")
    week = load(sym, "week")
    rd = chan.analyze(day)
    rw = chan.analyze(week)
    cur = day[-1]["close"]

    sw_d, sw_w = _swings(rd), _swings(rw)
    wd, cd = elliott.pick_current_wave(sw_d)
    ww, cw = elliott.pick_current_wave(sw_w)
    if wd:
        wd["tag"] = "日线"
    if ww:
        ww["tag"] = "周线"

    cands = levels.collect(day, rd, [wd, ww], cur,
                           extra_zs=[(z, "周线") for z in rw["zs_bi"][-2:]],
                           tag="日线")
    groups = levels.cluster(cands)
    ev = levels.evaluate(day, groups, cur, h=10)

    return {
        "sym": sym, "name": name, "cur": cur, "date": day[-1]["date"],
        "day": rd, "week": rw, "rows_day": day, "rows_week": week,
        "wave_day": wd, "wave_day_all": cd[:6],
        "wave_week": ww, "wave_week_all": cw[:6],
        "levels": ev, "groups": groups,
        "bt_points": backtest_points(day, rd["points"]),
        "bt_points_week": backtest_points(week, rw["points"]),
        "bt_break": backtest_breakdown(day, rd["zs_bi"]),
        "base_day": baseline(day),
        "base_week": baseline(week),
    }


if __name__ == "__main__":
    for sym, nm in [("sh000001", "上证指数"), ("sz399006", "创业板指"), ("sh000300", "沪深300")]:
        a = full_analyze(sym, nm)
        print("\n" + "=" * 78)
        print("%s  现价 %.2f  (%s)" % (nm, a["cur"], a["date"]))
        for tag, w in [("日线", a["wave_day"]), ("周线", a["wave_week"])]:
            if w:
                print("  %s浪型: %s  违反%d %s  %s~%s  点位 %s" %
                      (tag, w["kind"], w["n_viol"], w["viol"], w["start_date"],
                       w["end_date"], [round(v, 1) for v in w["vals"]]))
        cd = a["day"]["cur_div"]
        if cd:
            print("  当前段背驰: %s  新低=%s  面积比=%.2f  → %s" %
                  (cd["dir"], cd["new_extreme"], cd["ratio"] or -1,
                   "背驰成立" if cd["div"] else "未背驰"))
        print("  -- 支撑 --")
        for x in a["levels"]["support"][:5]:
            s = x["score"]
            print("    %8.2f (%+5.2f%%) 评分%s 样本%d 胜率%s 均值%s  %s" %
                  (x["price"], x["dist"],
                   "%.1f" % s["score"] if s["score"] is not None else "n/a",
                   s["n"], "%.0f%%" % (s["win"] * 100) if s["win"] is not None else "-",
                   "%+.2f%%" % (s["mean"] * 100) if s["mean"] is not None else "-",
                   "|".join(x["srcs"])))
        print("  -- 压力 --")
        for x in a["levels"]["resist"][:5]:
            s = x["score"]
            print("    %8.2f (%+5.2f%%) 评分%s 样本%d 胜率%s 均值%s  %s" %
                  (x["price"], x["dist"],
                   "%.1f" % s["score"] if s["score"] is not None else "n/a",
                   s["n"], "%.0f%%" % (s["win"] * 100) if s["win"] is not None else "-",
                   "%+.2f%%" % (s["mean"] * 100) if s["mean"] is not None else "-",
                   "|".join(x["srcs"])))
        print("  -- 破位事件回测 --")
        b = a["bt_break"]
        print("    事件数 %d" % b["events"])
        for h in HORIZONS:
            d = b["by_h"][h]
            if d.get("n"):
                print("    %2d日: n=%d 胜率%.1f%% 均值%+.2f%% 中位%+.2f%%" %
                      (h, d["n"], d["win"] * 100, d["mean"] * 100, d["med"] * 100))
