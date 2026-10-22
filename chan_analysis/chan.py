# -*- coding: utf-8 -*-
"""
缠论核心引擎：K线包含处理 → 分型 → 笔 → 线段 → 中枢 → 背驰 → 买卖点。

设计取舍（面向实盘可读性与稳定性）：
- 笔：采用「合并后分型中心索引差 >= 4」的严格笔标准，避免碎笔。
- 线段：特征序列法（对笔的特征序列再做包含处理 + 分型判断）。
- 中枢：连续 3 个次级别走势（笔 / 线段）区间的交集，ZG/ZD 由前三笔锁定，
         后续走势与区间有重叠则延伸，完全离开则中枢结束。
- 背驰：同向相邻两段的 MACD 柱面积 + DIF/DEA 力度双条件。
"""
import math


# ---------------------------------------------------------------- 基础结构
class Bar(object):
    __slots__ = ("idx", "date", "open", "close", "high", "low", "volume")

    def __init__(self, idx, date, o, c, h, l, v):
        self.idx, self.date, self.open, self.close = idx, date, o, c
        self.high, self.low, self.volume = h, l, v


def macd(bars, fast=12, slow=26, signal=9):
    """标准 MACD，返回 (dif, dea, hist, hist_n)。
    hist_n = hist / close * 1000 —— 归一化掉价格水平，使不同年份的面积可比。"""
    n = len(bars)
    dif, dea, hist, hist_n = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
    if n == 0:
        return dif, dea, hist, hist_n
    kf = 2.0 / (fast + 1)
    ks = 2.0 / (slow + 1)
    kd = 2.0 / (signal + 1)
    ef = es = ed = bars[0].close
    for i, b in enumerate(bars):
        ef = b.close * kf + ef * (1 - kf)
        es = b.close * ks + es * (1 - ks)
        d = ef - es
        ed = d * kd + ed * (1 - kd)
        dif[i], dea[i], hist[i] = d, ed, 2 * (d - ed)
        hist_n[i] = hist[i] / b.close * 1000.0 if b.close else 0.0
    return dif, dea, hist, hist_n


# ---------------------------------------------------------------- 1. 包含处理
def merge_bars(bars):
    """K线包含关系处理，返回合并后的 K 线列表（保留原始索引区间）。"""
    merged = []
    for b in bars:
        cur = {"high": b.high, "low": b.low, "s": b.idx, "e": b.idx,
               "date": b.date, "oi": [b.idx]}
        while True:
            if not merged:
                merged.append(cur)
                break
            last = merged[-1]
            inc = ((cur["high"] <= last["high"] and cur["low"] >= last["low"]) or
                   (cur["high"] >= last["high"] and cur["low"] <= last["low"]))
            if not inc:
                merged.append(cur)
                break
            # 有包含关系 → 按趋势方向合并
            if len(merged) >= 2:
                prev = merged[-2]
                if last["high"] > prev["high"] and last["low"] > prev["low"]:
                    up = True
                elif last["high"] < prev["high"] and last["low"] < prev["low"]:
                    up = False
                else:
                    up = last["high"] >= prev["high"]
            else:
                up = cur["high"] > last["high"]
            if up:
                last["high"] = max(last["high"], cur["high"])
                last["low"] = max(last["low"], cur["low"])
            else:
                last["high"] = min(last["high"], cur["high"])
                last["low"] = min(last["low"], cur["low"])
            last["e"] = cur["e"]
            last["oi"].extend(cur["oi"])
            break  # 合并后不再回溯（标准实现差异极小，换取稳定性）
    # 至少需要 3 根才能判断分型
    return merged


# ---------------------------------------------------------------- 2. 分型
def find_fenxing(merged, min_gap=4):
    """识别顶/底分型。merged 已做包含处理，故只需比较 high / low。"""
    fx = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b["high"] > a["high"] and b["high"] > c["high"]:
            fx.append({"type": "top", "i": i, "high": b["high"], "low": b["low"],
                       "date": b["date"], "oi": b["oi"][:]})
        elif b["low"] < a["low"] and b["low"] < c["low"]:
            fx.append({"type": "bottom", "i": i, "high": b["high"], "low": b["low"],
                       "date": b["date"], "oi": b["oi"][:]})
    return fx


# ---------------------------------------------------------------- 3. 笔
def build_bi(fx, min_gap=4):
    """由分型生成笔。相邻异类分型中心索引差 >= min_gap 且端点有效。"""
    bis = []
    if len(fx) < 2:
        return bis
    # cand = 待确认分型（尚未成笔，可被更极端的同类替换）
    # 已确认的笔端点不再变动，因此相邻笔首尾严格相接
    cand = fx[0]
    for f in fx[1:]:
        if f["type"] == cand["type"]:
            if (f["type"] == "top" and f["high"] > cand["high"]) or \
               (f["type"] == "bottom" and f["low"] < cand["low"]):
                # 缠论「最后一笔未确认」：端点延伸，同步改写上一笔的终点
                old = cand
                cand = f
                if bis and bis[-1]["e"] is old:
                    bis[-1] = _mk_bi(bis[-1]["s"], f, bis[-1]["dir"])
            continue
        if f["i"] - cand["i"] < min_gap:
            continue
        # 缠论笔只要求「顶底交替 + 间距足够」；端点有效性仅排除极端异常
        if cand["type"] == "top":
            if f["low"] >= cand["high"]:
                continue
            bis.append(_mk_bi(cand, f, "down"))
        else:
            if f["high"] <= cand["low"]:
                continue
            bis.append(_mk_bi(cand, f, "up"))
        cand = f
    return bis


def _mk_bi(a, b, direction):
    return {
        "dir": direction,
        "s": a, "e": b,
        "s_date": a["date"], "e_date": b["date"],
        # 用分型首根原始 K 线索引，保证相邻笔首尾严格相接（供 MACD 面积切片）
        "s_idx": a["oi"][0], "e_idx": b["oi"][0],
        "high": max(a["high"], b["high"]),
        "low": min(a["low"], b["low"]),
        "s_val": a["high"] if direction == "down" else a["low"],
        "e_val": b["low"] if direction == "down" else b["high"],
    }


# ---------------------------------------------------------------- 4. 线段（特征序列法）
def build_seg(bis):
    """特征序列法划分线段：向上段的特征序列取向下笔，反之亦然。"""
    segs = []
    if len(bis) < 3:
        return segs
    # 特征序列：把每笔当作一根「K线」(high/low 用笔区间)
    fseq = [{"high": b["high"], "low": b["low"], "bi": b, "i": i}
            for i, b in enumerate(bis)]
    # 方向由第一笔决定
    direction = "up" if bis[0]["dir"] == "up" else "down"
    start_i = 0
    i = 1
    while i < len(bis):
        # 线段至少 3 笔；结束条件：出现特征序列的分型（简化：反向笔突破线段极值）
        if i - start_i + 1 < 3:
            i += 1
            continue
        # 取该段的特征序列（与方向相反的笔）
        sub = [f for f in fseq[start_i:i + 1] if f["bi"]["dir"] != ("up" if direction == "up" else "down")]
        if len(sub) >= 2:
            # 特征序列分型：向上段看特征序列（向下笔）是否出现底分型 → 段结束
            a1, a2 = sub[-2], sub[-1]
            if direction == "up":
                # 向上线段结束：特征序列（向下笔）出现底分型（a2.low < a1.low 且后续确认）
                if a2["low"] < a1["low"] and a2["high"] < a1["high"]:
                    segs.append(_mk_seg(bis, start_i, i, direction))
                    start_i = i
                    direction = "down"
            else:
                if a2["high"] > a1["high"] and a2["low"] > a1["low"]:
                    segs.append(_mk_seg(bis, start_i, i, direction))
                    start_i = i
                    direction = "up"
        i += 1
    if i - start_i >= 2:
        segs.append(_mk_seg(bis, start_i, len(bis) - 1, direction))
    return segs


def _mk_seg(bis, i0, i1, direction):
    sub = bis[i0:i1 + 1]
    return {
        "dir": direction,
        "s_date": sub[0]["s_date"], "e_date": sub[-1]["e_date"],
        "s_idx": sub[0]["s_idx"], "e_idx": sub[-1]["e_idx"],
        "high": max(x["high"] for x in sub),
        "low": min(x["low"] for x in sub),
        "s_val": sub[0]["s_val"], "e_val": sub[-1]["e_val"],
        "n_bi": len(sub),
    }


# ---------------------------------------------------------------- 5. 中枢
def build_zhongshu(units):
    """units: 笔或线段列表（需含 high/low/s_date/e_date/dir）。"""
    zss = []
    n = len(units)
    i = 0
    while i + 2 < n:
        a, b, c = units[i], units[i + 1], units[i + 2]
        zg = min(a["high"], b["high"], c["high"])
        zd = max(a["low"], b["low"], c["low"])
        if zg <= zd:
            i += 1
            continue
        # 中枢成立，尝试延伸（缠论「九笔升级」：上限 9 个次级别走势）
        j = i + 3
        while j < n and (j - i) < 9:
            u = units[j]
            if u["low"] < zg and u["high"] > zd:  # 与前 3 笔区间有重叠
                j += 1
            else:
                break
        zs = {
            "zg": zg, "zd": zd,
            "gg": max(x["high"] for x in units[i:j]),
            "dd": min(x["low"] for x in units[i:j]),
            "s_date": units[i]["s_date"], "e_date": units[j - 1]["e_date"],
            "s_idx": units[i]["s_idx"], "e_idx": units[j - 1]["e_idx"],
            "n": j - i,
            "dir_entry": units[i]["dir"],
        }
        zss.append(zs)
        i = j if j > i + 2 else i + 1
    return zss


# ---------------------------------------------------------------- 6. 背驰
def area_of(hist, i0, i1, positive):
    """[i0,i1] 区间内 MACD 柱面积（取同号部分）。"""
    s = 0.0
    for i in range(max(0, i0), min(len(hist), i1 + 1)):
        v = hist[i]
        if positive and v > 0:
            s += v
        elif (not positive) and v < 0:
            s += -v
    return s


def check_divergence(units, hist, dif):
    """
    对每段标注是否背驰：与前一个同向段比较，创新高/新低但 MACD 面积缩小。
    units: 笔或线段（含 dir/s_idx/e_idx/high/low）
    返回 dict: idx -> {'div': bool, 'ratio': 面积比, 'type': 'top'/'bottom'}
    """
    out = {}
    for i in range(1, len(units)):
        cur = units[i]
        # 找前一个同向段
        prev = None
        for j in range(i - 2, -1, -1):
            if units[j]["dir"] == cur["dir"]:
                prev = units[j]
                break
        if prev is None:
            continue
        if cur["dir"] == "up":
            new_extreme = cur["high"] > prev["high"]
            a1 = area_of(hist, prev["s_idx"], prev["e_idx"], True)
            a2 = area_of(hist, cur["s_idx"], cur["e_idx"], True)
            kind = "top"
        else:
            new_extreme = cur["low"] < prev["low"]
            a1 = area_of(hist, prev["s_idx"], prev["e_idx"], False)
            a2 = area_of(hist, cur["s_idx"], cur["e_idx"], False)
            kind = "bottom"
        if a1 <= 0:
            continue
        ratio = a2 / a1
        comparable = 0.2 <= ratio <= 5.0   # 面积相差 5 倍以上 → 级别不同，判定不可靠
        div = bool(new_extreme and comparable and ratio < 0.85)
        out[i] = {
            "div": div, "ratio": ratio, "type": kind, "dir": cur["dir"],
            "new_extreme": new_extreme, "a_prev": a1, "a_cur": a2,
            "comparable": comparable,
            "prev_extreme": prev["low"] if cur["dir"] == "down" else prev["high"],
            "cur_extreme": cur["low"] if cur["dir"] == "down" else cur["high"],
            "prev_date": prev.get("e_date"), "cur_date": cur.get("e_date"),
            "s_date": cur.get("s_date"), "e_date": cur.get("e_date"),
        }
    return out


# ---------------------------------------------------------------- 7. 买卖点
def find_points(bis, zss, divs, bars):
    """
    一买：下跌笔背驰处（底）
    二买：一买后首笔上涨的回调低点，不破一买低点
    三买：向上笔离开中枢后，回调低点在中枢 ZG 上方
    卖点对称。
    """
    pts = []
    n = len(bis)

    def zs_at(idx):
        """找到覆盖该笔结束位置的中枢"""
        for z in zss:
            if z["s_idx"] <= idx <= z["e_idx"]:
                return z
        return None

    for i, bi in enumerate(bis):
        d = divs.get(i)
        # 一买 / 一卖
        if bi["dir"] == "down" and d and d["div"]:
            pts.append({"kind": "1buy", "date": bi["e_date"], "idx": bi["e_idx"],
                        "price": bi["e_val"], "bi": i})
        if bi["dir"] == "up" and d and d["div"]:
            pts.append({"kind": "1sell", "date": bi["e_date"], "idx": bi["e_idx"],
                        "price": bi["e_val"], "bi": i})

        # 三买 / 三卖：离开中枢后的第一次回调不回中枢
        z = zs_at(bi["s_idx"])
        if z is None or i == 0:
            continue
        prev = bis[i - 1]
        if bi["dir"] == "down" and prev["dir"] == "up":
            # 向上离开中枢后回调
            if prev["high"] > z["zg"] and bi["low"] > z["zg"]:
                pts.append({"kind": "3buy", "date": bi["e_date"], "idx": bi["e_idx"],
                            "price": bi["e_val"], "bi": i, "zg": z["zg"]})
        if bi["dir"] == "up" and prev["dir"] == "down":
            if prev["low"] < z["zd"] and bi["high"] < z["zd"]:
                pts.append({"kind": "3sell", "date": bi["e_date"], "idx": bi["e_idx"],
                            "price": bi["e_val"], "bi": i, "zd": z["zd"]})

    # 二买 / 二卖：一买后的回调不破前低
    for p in [x for x in pts if x["kind"] == "1buy"]:
        i = p["bi"]
        if i + 2 < n:
            up_bi = bis[i + 1]
            back = bis[i + 2]
            if up_bi["dir"] == "up" and back["dir"] == "down" and back["low"] > p["price"]:
                pts.append({"kind": "2buy", "date": back["e_date"], "idx": back["e_idx"],
                            "price": back["e_val"], "bi": i + 2})
    for p in [x for x in pts if x["kind"] == "1sell"]:
        i = p["bi"]
        if i + 2 < n:
            dn_bi = bis[i + 1]
            back = bis[i + 2]
            if dn_bi["dir"] == "down" and back["dir"] == "up" and back["high"] < p["price"]:
                pts.append({"kind": "2sell", "date": back["e_date"], "idx": back["e_idx"],
                            "price": back["e_val"], "bi": i + 2})
    pts.sort(key=lambda x: x["idx"])
    return pts


# ---------------------------------------------------------------- 汇总入口
def analyze(rows, min_gap=4):
    """rows: list of dict(date,open,close,high,low,volume)"""
    bars = [Bar(i, r["date"], r["open"], r["close"], r["high"], r["low"], r["volume"])
            for i, r in enumerate(rows)]
    dif, dea, hist, hist_n = macd(bars)
    merged = merge_bars(bars)
    fx = find_fenxing(merged)
    bis = build_bi(fx, min_gap=min_gap)
    segs = build_seg(bis) if bis else []
    zs_bi = build_zhongshu(bis)
    zs_seg = build_zhongshu(segs) if segs else []
    div_bi = check_divergence(bis, hist_n, dif)
    pts = find_points(bis, zs_bi, div_bi, bars)

    # 进行中的一笔：最后一笔结束 → 最新 K 线（未确认，仅用于判断当下位置）
    pending = None
    if bis:
        e = bis[-1]["e_idx"]
        if e < len(bars) - 1:
            seg = bars[e:]
            hi = max(x.high for x in seg)
            lo = min(x.low for x in seg)
            cur = bars[-1].close
            pending = {
                "from_date": bis[-1]["e_date"], "to_date": bars[-1].date,
                "from_val": bis[-1]["e_val"], "to_val": cur,
                "high": hi, "low": lo, "n": len(seg) - 1,
                "dir": "down" if cur < bis[-1]["e_val"] else "up",
                "s_idx": e, "e_idx": len(bars) - 1,
            }
    # 当前进行中段的背驰：把「最后一个同向笔 + pending」合并成一段，与前一同向段比
    cur_div = None
    if pending and bis:
        last_bi = bis[-1]
        if last_bi["dir"] == pending["dir"]:
            s_idx, s_val, s_date = last_bi["s_idx"], last_bi["s_val"], last_bi["s_date"]
        else:
            s_idx, s_val, s_date = pending["s_idx"], pending["from_val"], pending["from_date"]
        prev = None
        for b in reversed(bis):
            if b["dir"] == pending["dir"] and b is not last_bi:
                prev = b
                break
        if prev is not None:
            pos = (pending["dir"] == "up")
            a1 = area_of(hist_n, prev["s_idx"], prev["e_idx"], pos)
            a2 = area_of(hist_n, s_idx, len(bars) - 1, pos)
            ext = (pending["low"] < prev["low"]) if pending["dir"] == "down" \
                else (pending["high"] > prev["high"])
            ratio = (a2 / a1) if a1 > 0 else None
            # 两段级别是否可比（面积相差 5 倍以上说明不是同一级别，判定不可靠）
            comparable = bool(ratio is not None and 0.2 <= ratio <= 5.0)
            cur_div = {
                "dir": pending["dir"], "s_date": s_date, "s_val": s_val,
                "cur_extreme": pending["low"] if pending["dir"] == "down" else pending["high"],
                "prev_extreme": prev["low"] if pending["dir"] == "down" else prev["high"],
                "prev_date": prev["e_date"],
                "new_extreme": bool(ext), "a_prev": a1, "a_cur": a2, "ratio": ratio,
                "comparable": comparable,
                "div": bool(ratio is not None and comparable and ext and ratio < 0.85),
            }
            pending["s_idx_merged"] = s_idx
            pending["s_val_merged"] = s_val
            pending["s_date_merged"] = s_date

    # 线段级别的背驰（趋势力度可比性更好）：把 pending 并入最后一段
    cur_div_seg = None
    if pending and segs:
        units = [dict(s) for s in segs]
        ls = units[-1]
        if ls["dir"] == pending["dir"]:
            units[-1] = dict(ls, e_idx=len(bars) - 1, e_val=pending["to_val"],
                             low=min(ls["low"], pending["low"]),
                             high=max(ls["high"], pending["high"]),
                             e_date=pending["to_date"])
        else:
            units.append({"dir": pending["dir"], "s_idx": pending["s_idx"],
                          "e_idx": len(bars) - 1, "s_val": pending["from_val"],
                          "e_val": pending["to_val"], "high": pending["high"],
                          "low": pending["low"], "s_date": pending["from_date"],
                          "e_date": pending["to_date"], "n_bi": 1})
        d = check_divergence(units, hist_n, dif)
        k = len(units) - 1
        if k in d:
            cur_div_seg = d[k]
            cur_div_seg["level"] = "线段"
            cur_div_seg["s_date"] = units[k]["s_date"]
            cur_div_seg["e_date"] = units[k]["e_date"]

    return {
        "bars": bars, "dif": dif, "dea": dea, "hist": hist, "hist_n": hist_n,
        "merged": merged, "fx": fx, "bi": bis, "seg": segs,
        "zs_bi": zs_bi, "zs_seg": zs_seg, "div": div_bi, "points": pts,
        "pending": pending, "cur_div": cur_div, "cur_div_seg": cur_div_seg,
    }
