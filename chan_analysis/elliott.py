# -*- coding: utf-8 -*-
"""
艾略特波浪：摆动点提取 → 浪型匹配（推动浪 / 调整浪）→ 斐波那契位。

摆动点直接取缠论笔的端点序列（已过滤噪音），比 ZIG 更贴合结构。
波浪铁律：
  1) 2 浪回调不跌破 1 浪起点
  2) 3 浪永远不是 1/3/5 三个推动浪中最短的
  3) 4 浪回调不进入 1 浪的价格区间（4 浪底 > 1 浪顶）
另加常见指引：1/5 浪等长或 0.618 关系、2 浪回撤 0.5~0.786、3 浪常为 1 浪的 1.618。
"""
FIB_RETR = [0.236, 0.382, 0.5, 0.618, 0.786]
FIB_EXT = [1.0, 1.272, 1.618, 2.0, 2.618]


def swings_from_bi(bis):
    """笔端点 → 摆动点序列 [(idx, date, val, type)]，首点是笔起点，其后每笔终点。"""
    if not bis:
        return []
    out = [(bis[0]["s_idx"], bis[0]["s_date"], bis[0]["s_val"],
            "bottom" if bis[0]["dir"] == "up" else "top")]
    for b in bis:
        out.append((b["e_idx"], b["e_date"], b["e_val"],
                    "top" if b["dir"] == "up" else "bottom"))
    return out


def _rules_impulse_up(v):
    """v = [p0(底), p1(顶), p2(底), p3(顶), p4(底), p5(顶)]"""
    viol = []
    if not (v[2] > v[0]):
        viol.append("2浪跌破1浪起点")
    if not (v[4] > v[1]):
        viol.append("4浪进入1浪区间")
    if not (v[3] > v[1]):
        viol.append("3浪未创新高")
    l1, l3, l5 = v[1] - v[0], v[3] - v[2], v[5] - v[4]
    if l3 < l1 and l3 < l5:
        viol.append("3浪最短")
    return viol, {"l1": l1, "l3": l3, "l5": l5}


def _rules_impulse_dn(v):
    """v = [p0(顶), p1(底), p2(顶), p3(底), p4(顶), p5(底)]"""
    viol = []
    if not (v[2] < v[0]):
        viol.append("2浪反弹突破1浪起点")
    if not (v[4] < v[1]):
        viol.append("4浪进入1浪区间")
    if not (v[3] < v[1]):
        viol.append("3浪未创新低")
    l1, l3, l5 = v[0] - v[1], v[2] - v[3], v[4] - v[5]
    if l3 < l1 and l3 < l5:
        viol.append("3浪最短")
    return viol, {"l1": l1, "l3": l3, "l5": l5}


def _rules_abc_dn(v):
    """v = [p0(顶), A(底), B(顶), C(底)]"""
    viol = []
    if not (v[1] < v[0]):
        viol.append("A浪未下跌")
    if not (v[2] < v[0]):
        viol.append("B浪突破起点（非标准锯齿）")
    if not (v[3] < v[1]):
        viol.append("C浪未创新低")
    return viol, {}


def _rules_abc_up(v):
    """v = [p0(底), A(顶), B(底), C(顶)]"""
    viol = []
    if not (v[1] > v[0]):
        viol.append("A浪未上涨")
    if not (v[2] > v[0]):
        viol.append("B浪跌破起点（非标准锯齿）")
    if not (v[3] > v[1]):
        viol.append("C浪未创新高")
    return viol, {}


def scan_waves(swings, max_lookback=24):
    """扫描所有可能的浪型组合，返回按（违反数, 跨度, 时间近）排序的候选。"""
    cands = []
    n = len(swings)
    lo = max(0, n - max_lookback)
    for s in range(lo, n):
        for k in (6, 4):
            if s + k > n:
                continue
            pts = swings[s:s + k]
            types = [p[3] for p in pts]
            vals = [p[2] for p in pts]
            tv = "".join(t[0] for t in types)  # b/t 序列
            if k == 6:
                if tv == "btbtbt":
                    viol, extra = _rules_impulse_up(vals)
                    kind = "上涨推动浪"
                elif tv == "tbtbtb":
                    viol, extra = _rules_impulse_dn(vals)
                    kind = "下跌推动浪"
                else:
                    continue
            else:
                if tv == "tbtb":
                    viol, extra = _rules_abc_dn(vals)
                    kind = "ABC 调整（下）"
                elif tv == "btbt":
                    viol, extra = _rules_abc_up(vals)
                    kind = "ABC 反弹（上）"
                else:
                    continue
            span = abs(vals[-1] - vals[0]) / vals[0] * 100
            cands.append({
                "kind": kind, "viol": viol, "n_viol": len(viol),
                "pts": pts, "vals": vals,
                "start_date": pts[0][1], "end_date": pts[-1][1],
                "end_idx": pts[-1][0], "span": span, "extra": extra,
            })
    # 排序：违反少 → 跨度大 → 结束晚
    cands.sort(key=lambda c: (c["n_viol"], -c["span"], -c["end_idx"]))
    return cands


def pick_current_wave(swings, max_lookback=24):
    """取「最近且规则满足最好」的浪型：优先未结束时间最近的 0 违反候选。"""
    cands = scan_waves(swings, max_lookback)
    if not cands:
        return None, []
    # 必须是「正在进行的浪型」：结束于最新摆动点（含 pending 末端）。
    # 若该点没有任何合规组合，宁可返回 None，也不拿一个已结束的旧浪凑数。
    last_idx = max(s[0] for s in swings)
    pool = [c for c in cands if c["end_idx"] == last_idx]
    if not pool:
        return None, cands
    ok = [c for c in pool if c["n_viol"] == 0]
    if ok:
        pool = ok
    # 同等违反数下取跨度大的
    best = max(pool, key=lambda c: (c["end_idx"], -c["n_viol"], c["span"]))
    return best, cands


def fib_levels(wave, current):
    """
    由识别出的浪型产出斐波那契位。
    回调：以浪型的主方向段（0→浪终点）为基准做回撤
    扩展：以 1 浪长度为基准，从 2 浪终点/或末浪起点做扩展
    """
    out = []
    if wave is None:
        return out
    vals = wave["vals"]
    kind = wave["kind"]
    if kind == "上涨推动浪":
        # 回调：以 0→5 为基准
        lo, hi = vals[0], vals[-1]
        base = hi - lo
        for f in FIB_RETR:
            out.append({"price": hi - base * f, "label": "%.1f%% 回撤(0→5浪)" % (f * 100),
                        "grp": "fib"})
        # 扩展：1 浪长度从 4 浪底往上
        l1 = vals[1] - vals[0]
        for f in FIB_EXT:
            out.append({"price": vals[4] + l1 * f, "label": "1浪 %.3f 扩展" % f, "grp": "fib"})
    elif kind == "下跌推动浪":
        lo, hi = vals[-1], vals[0]
        base = hi - lo
        for f in FIB_RETR:
            out.append({"price": lo + base * f, "label": "%.1f%% 反弹(0→5浪)" % (f * 100),
                        "grp": "fib"})
        l1 = vals[0] - vals[1]
        for f in FIB_EXT:
            out.append({"price": vals[4] - l1 * f, "label": "1浪 %.3f 扩展" % f, "grp": "fib"})
    elif kind == "ABC 调整（下）":
        hi, lo = vals[0], vals[-1]
        base = hi - lo
        for f in FIB_RETR:
            out.append({"price": lo + base * f, "label": "%.1f%% 反弹(A→C)" % (f * 100),
                        "grp": "fib"})
        # C 浪目标（艾略特最实用的预测）：以 A 浪长度为基准，从 B 浪顶往下量
        a_len = vals[0] - vals[1]
        for f in (0.618, 1.0, 1.618):
            out.append({"price": vals[2] - a_len * f,
                        "label": "C浪目标 = %.3f×A浪" % f, "grp": "ctarget"})
    elif kind == "ABC 反弹（上）":
        lo, hi = vals[0], vals[-1]
        base = hi - lo
        for f in FIB_RETR:
            out.append({"price": hi - base * f, "label": "%.1f%% 回撤(A→C)" % (f * 100),
                        "grp": "fib"})
        a_len = vals[1] - vals[0]
        for f in (0.618, 1.0, 1.618):
            out.append({"price": vals[2] + a_len * f,
                        "label": "C浪目标 = %.3f×A浪" % f, "grp": "ctarget"})
    return out
