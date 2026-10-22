# -*- coding: utf-8 -*-
"""
支撑 / 压力位汇总 + 量化评分。

候选来源：缠论中枢 ZD/ZG、摆动点（笔端点）、斐波那契回撤、C 浪目标、均线、整数关口、区间高低点。
量化方法（核心）：对每一个价位，回到历史里找「测试事件」，统计测试后的实际反应。
  · 支撑位（现价在上方）：历史上从上方首次跌到该位 ±tol，看之后 H 日涨跌 → 涨为正
  · 压力位（现价在下方）：历史上从下方首次涨到该位 ±tol，看之后 H 日涨跌 → 跌为正
评分 = 0.45×胜率 + 0.35×标准化收益 + 0.20×样本置信度
"""
TOL = 0.005          # 触及容差 0.5%
GAP_MIN = 5          # 同一事件至少间隔这么多根 K 线才算新事件
MERGE_PCT = 0.008    # 聚类合并阈值 0.8%

WEIGHT = {"中枢": 3.0, "摆动点": 2.0, "C浪目标": 2.0,
          "斐波那契": 1.5, "均线": 1.5, "整数关口": 1.0, "区间高低": 2.0}


def ma(rows, n):
    if len(rows) < n:
        return None
    return sum(r["close"] for r in rows[-n:]) / n


# ---------------------------------------------------------------- 候选位收集
def collect(rows, r, waves, cur, extra_zs=None, tag=""):
    """rows: 原始K线; r: chan.analyze 结果; waves: 浪型列表(可含多周期); cur: 现价
    extra_zs: [(中枢, 标签前缀)] 用于并入更高级别（周线）中枢"""
    cands = []

    def add(price, src, label):
        if price is None or price <= 0:
            return
        # 只保留距现价 15% 以内的位（太远的没有操作意义）
        if abs(price - cur) / cur > 0.15:
            return
        cands.append({"price": float(price), "src": src, "label": label})

    # 1) 缠论中枢
    for z in r["zs_bi"][-3:]:
        add(z["zd"], "中枢", "%s中枢下沿 %s" % (tag, z["s_date"][2:].replace("-", "/")))
        add(z["zg"], "中枢", "%s中枢上沿 %s" % (tag, z["s_date"][2:].replace("-", "/")))
    for z in r.get("zs_seg", [])[-2:]:
        add(z["zd"], "中枢", "%s线段中枢下沿" % tag)
        add(z["zg"], "中枢", "%s线段中枢上沿" % tag)
    for z, pfx in (extra_zs or []):
        add(z["zd"], "中枢", "%s中枢下沿 %s" % (pfx, z["s_date"][2:].replace("-", "/")))
        add(z["zg"], "中枢", "%s中枢上沿 %s" % (pfx, z["s_date"][2:].replace("-", "/")))

    # 2) 摆动点（最近 14 个笔端点）
    sw = []
    for b in r["bi"][-14:]:
        sw.append((b["s_idx"], b["s_date"], b["s_val"]))
        sw.append((b["e_idx"], b["e_date"], b["e_val"]))
    for _, d, v in sorted(set(sw), key=lambda x: x[0])[-14:]:
        add(v, "摆动点", "笔端点 %s" % d[2:].replace("-", "/"))

    # 3) 斐波那契 + C 浪目标（可并入多个周期的浪型）
    import elliott
    for wv in (waves or []):
        if not wv:
            continue
        pfx = wv.get("tag", "")
        for f in elliott.fib_levels(wv, cur):
            src = "C浪目标" if f["grp"] == "ctarget" else "斐波那契"
            add(f["price"], src, "%s%s" % (pfx, f["label"]))

    # 4) 均线
    for n in (20, 60, 120, 250):
        v = ma(rows, n)
        add(v, "均线", "MA%d" % n)

    # 5) 区间高低点（近 250 / 120 / 60 日）
    for n in (250, 120, 60):
        seg = rows[-n:] if len(rows) >= n else rows
        add(max(x["high"] for x in seg), "区间高低", "近%d日最高" % n)
        add(min(x["low"] for x in seg), "区间高低", "近%d日最低" % n)

    # 6) 整数关口
    step = 100.0
    base = (int(cur / step)) * step
    for k in range(-6, 7):
        add(base + k * step, "整数关口", "%d 整数关" % int(base + k * step))

    return cands


# ---------------------------------------------------------------- 聚类
def cluster(cands):
    """按 MERGE_PCT 合并成区域，中心为加权平均。"""
    items = sorted(cands, key=lambda c: c["price"])
    groups = []
    for c in items:
        w = WEIGHT.get(c["src"], 1.0)
        if groups and (c["price"] - groups[-1]["_last"]) / groups[-1]["_last"] <= MERGE_PCT:
            g = groups[-1]
            g["_sum"] += c["price"] * w
            g["_w"] += w
            g["_last"] = c["price"]
            g["labels"].append("%s·%s" % (c["src"], c["label"]))
            g["srcs"].add(c["src"])
            g["n_raw"] += 1
        else:
            groups.append({"_sum": c["price"] * w, "_w": w, "_last": c["price"],
                           "labels": ["%s·%s" % (c["src"], c["label"])],
                           "srcs": {c["src"]}, "n_raw": 1})
    out = []
    for g in groups:
        srcs = sorted(g["srcs"])
        out.append({
            "price": g["_sum"] / g["_w"],
            "labels": g["labels"],
            "srcs": srcs,
            "n_raw": g["n_raw"],
            "strength": g["_w"],          # 来源权重之和 = 结构强度
            # 纯整数关口不构成结构位，仅作为其它位的附注
            "structural": not (len(srcs) == 1 and srcs[0] == "整数关口"),
        })
    return out


# ---------------------------------------------------------------- 量化评估
def test_level(rows, level, side, horizons=(5, 10, 20)):
    """
    统计该价位的历史测试事件与后续表现。
    side: 'support'（现价在上方，看反弹）/ 'resist'（现价在下方，看回落）
    返回 {h: {'n':..,'win':..,'mean':..,'med':..}}
    """
    n = len(rows)
    events = []
    last_i = -10 ** 9
    for i in range(1, n - 1):
        c = rows[i]
        if side == "support":
            # 从上方触及：收盘仍在上方（未有效跌破），但盘中探到
            hit = (c["close"] > level) and (c["low"] <= level * (1 + TOL))
        else:
            hit = (c["close"] < level) and (c["high"] >= level * (1 - TOL))
        if not hit:
            continue
        if i - last_i < GAP_MIN:
            continue
        last_i = i
        events.append(i)
    res = {}
    for h in horizons:
        rets = []
        for i in events:
            j = i + h
            if j >= n:
                continue
            r = (rows[j]["close"] - rows[i]["close"]) / rows[i]["close"]
            rets.append(-r if side == "resist" else r)
        if rets:
            rets_sorted = sorted(rets)
            res[h] = {
                "n": len(rets),
                "win": sum(1 for x in rets if x > 0) / len(rets),
                "mean": sum(rets) / len(rets),
                "med": rets_sorted[len(rets) // 2],
                "worst": rets_sorted[0], "best": rets_sorted[-1],
            }
        else:
            res[h] = {"n": 0, "win": None, "mean": None, "med": None,
                      "worst": None, "best": None}
    return {"events": len(events), "by_h": res}


def score_of(stat, h=10, strength=1.0):
    """综合评分 0~100，另给 adj（按样本量衰减后的排序分）。样本 <3 视为样本不足。"""
    s = stat["by_h"][h]
    if s["n"] < 3:
        return {"score": None, "adj": 0.0, "n": s["n"], "win": s["win"],
                "mean": s["mean"], "low_conf": True,
                "conf_label": "样本不足"}
    win = s["win"]
    ret_norm = max(-1.0, min(1.0, s["mean"] / 0.015))
    conf = min(s["n"] / 15.0, 1.0)
    score = 100 * (0.40 * win + 0.30 * (ret_norm + 1) / 2 + 0.30 * conf)
    score += min(6.0, (strength - 1.0) * 1.5)      # 多来源共振，最多 +6
    score = max(0.0, min(100.0, score))
    # 排序分：样本 <10 时线性衰减（最低 35%），避免「4 个样本 75% 胜率」霸榜
    adj = score * max(0.35, min(s["n"] / 10.0, 1.0))
    label = "高" if s["n"] >= 15 else ("中" if s["n"] >= 8 else "偏低")
    return {"score": score, "adj": adj, "n": s["n"], "win": win,
            "mean": s["mean"], "low_conf": False, "conf_label": label}


def evaluate(rows, groups, cur, h=10):
    """对每个聚类位做量化评估；返回 {'support':[...], 'resist':[...]}，组内按评分降序。"""
    sup, res = [], []
    for g in groups:
        p = g["price"]
        side = "support" if p < cur else "resist"
        stat = test_level(rows, p, side)
        sc = score_of(stat, h=h, strength=g["strength"])
        item = {
            "price": p, "side": side, "labels": g["labels"], "srcs": g["srcs"],
            "strength": g["strength"], "n_raw": g["n_raw"],
            "structural": g["structural"],
            "dist": (p - cur) / cur * 100,
            "stat": stat, "score": sc,
        }
        (sup if side == "support" else res).append(item)
    sup.sort(key=lambda x: x["price"], reverse=True)   # 支撑：由近及远
    res.sort(key=lambda x: x["price"])                 # 压力：由近及远
    # 另给一份按「有效性」排序的副本（供报告里的优先级排序）
    for lst in (sup, res):
        lst.sort(key=lambda x: -(x["score"]["adj"] if x["score"]["adj"] else 0))
        rank = {id(x): i + 1 for i, x in enumerate(lst)}
        for x in lst:
            x["rank_by_score"] = rank[id(x)]
        lst.sort(key=lambda x: x["price"], reverse=(lst is sup))
    return {"support": sup, "resist": res}
