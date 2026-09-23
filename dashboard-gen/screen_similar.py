#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依据【数据实测】的连板启动前画像，从当前市场筛选"形态类似、尚未启动"的潜在标的。
重要：打分维度来自 analyze.py 实测分位数，而非先验假设。
画像要点（48段样本中位数）：
  - 小市值（中位37亿，67%<50亿）
  - 启动前20日走平/下跌（中位-0.9%，54%下跌，p25跌17%）
  - 区间振幅不小（中位25.8%），20日内普遍有回撤（中位13.6%）
  - 启动前一日距20日高点平均低13%（低位启动，非逼近前高）
  - 量能温和回升（后5/前10 中位1.055，56%量增）
  - 均线多数未理顺（MA多头仅31%）

── 2026-09-23 改造：去除 WorkBuddy 私有 westock-tool 依赖 ──
原初筛池由 `westock filter` 表达式产出（只能在本机 WorkBuddy 运行时跑）。
现改为东方财富公开选股接口 push2.eastmoney.com/api/qt/clist/get
（带重试，全A股拉取后在客户端做市值/涨跌幅/换手/价 过滤），其余打分、
K线、相似度逻辑完全不变。K线优先复用本流水线刚生成的 kline_cache.json，
缺失时回退 analyze.get_kline（东财+腾讯/新浪兜底）。整套脚本仅用标准库，
可直接在 GitHub Actions 运行。
"""
import json, os, sys, urllib.request, urllib.parse, time

WS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WS)
import analyze as A  # 纯标准库：get_kline / compute_window_features / is_limit_up / limit_pct

EM_HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
OUT_CSV = os.path.join(WS, "similar.csv")
OUT_JSON = os.path.join(WS, "similar.json")
# kline_cache.json 位于仓库根（dashboard-gen 的上一级）
KCACHE_PATH = os.path.normpath(os.path.join(WS, "..", "kline_cache.json"))
POOL_CAP = 200  # 控制 K 线抓取规模，与原 westock --limit 150 同量级


def _em_json(url, tries=4):
    """东财 clist 带重试；限流/断连是常态，重试即可恢复。返回 (dict, err)。"""
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=EM_HEAD)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8")), None
        except Exception as e:
            last = repr(e)
            time.sleep(1.5 * (i + 1))
    return None, last


def get_pool():
    """初筛池：东财公开 clist 拉全A股 → 客户端做市值/涨跌幅/换手/价 过滤。
    等价于原 westock 表达式：
      intersect([TotalMV>2e9, TotalMV<1.2e10, ChangePCT<9.5, TurnoverRate>0.8, ClosePrice<=30])
    并剔除 ST/*ST/退市股、股价>30。
    返回 [(prefixed_code, name, mcap_yi, price), ...]
    """
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # 沪深A股 + 科创板 + 创业板（含北交见下方前缀）
    fields = "f12,f14,f2,f3,f20,f8"
    rows, pn, total = [], 1, None
    while True:
        url = "https://push2.eastmoney.com/api/qt/clist/get?" + urllib.parse.urlencode({
            "pn": str(pn), "pz": "5000", "po": "1", "np": "1", "fltt": "2",
            "invt": "2", "fid": "f3", "fs": fs, "fields": fields})
        d, err = _em_json(url)
        if d is None:
            print(f"[warn] 东财 clist 第{pn}页失败: {err}", file=sys.stderr)
            break
        data = d.get("data") or {}
        diff = data.get("diff") or []
        if total is None:
            total = data.get("total") or 0
        if not diff:
            break
        rows.extend(diff)
        if len(rows) >= total or pn >= 6:
            break
        pn += 1
    print(f"东财 clist 拉取全A股: {len(rows)} / {total} 只")
    out = []
    for it in rows:
        code = str(it.get("f12") or "")
        if not code.isdigit():
            continue
        name = it.get("f14") or ""
        try:
            price = float(it.get("f2"))
            mcap = float(it.get("f20"))      # 元
            chg = float(it.get("f3"))        # %
            tor = float(it.get("f8"))        # %
        except Exception:
            continue
        # 市值 2~120 亿（元）
        if mcap < 2e9 or mcap > 1.2e10:
            continue
        # 非涨停（预留启动空间）
        if chg >= 9.5:
            continue
        # 换手 > 0.8%
        if tor <= 0.8:
            continue
        # 股价 <= 30
        if price > 30:
            continue
        # 剔除 ST / *ST / 退市
        up = name.upper().replace(" ", "")
        if "ST" in up or "退" in up:
            continue
        pref = "sh" if code[0] == "6" else ("bj" if code[0] in "894" else "sz")
        out.append((pref + code, name, mcap / 1e8, price))
    print(f"  市值/换手/涨停/价 过滤后: {len(out)} 只")
    return out[:POOL_CAP]


def is_excluded(name, price):
    """硬性剔除规则：ST/*ST/退市股 + 股价 > 30 元。"""
    if not name:
        return True
    up = name.upper().replace(" ", "")
    if "ST" in up or "退" in up:
        return True
    if price is not None and price > 30:
        return True
    return False


def similarity(feat, mcap):
    """0~100 相似度，维度与权重来自实测画像。"""
    if not feat:
        return 0, {}
    sc = {}
    # 1) 小市值（中位37亿，p75 60亿）
    if mcap is not None:
        sc["小市值"] = 1.0 if mcap < 50 else (0.6 if mcap < 100 else 0.2)
    else:
        sc["小市值"] = 0.5
    # 2) 前期未提前拉升 / 走弱（p25~p75: -17%~+9%）
    pr = feat["pre_ret20"]
    sc["前期弱势"] = 1.0 if -0.20 <= pr <= 0.10 else (0.5 if -0.35 <= pr <= 0.20 else 0.1)
    # 3) 处于区间中低位（中位-13%，p10 -35%）
    dfh = feat["dist_from_high"]
    sc["低位蓄势"] = 1.0 if -0.35 <= dfh <= -0.03 else (0.45 if -0.50 <= dfh <= 0.0 else 0.1)
    # 4) 量能温和回升（中位1.055，56%量增）
    vr = feat["vol_ratio_l5_f10"]
    sc["量能回升"] = 1.0 if 0.8 <= vr <= 2.0 else (0.5 if 0.5 <= vr < 0.8 or 2.0 < vr <= 3.0 else 0.15)
    # 5) 振幅处于实测区间（中位25.8%，p10~p90 16%~53%）
    amp = feat["pre_amp"]
    sc["振幅相当"] = 1.0 if 0.15 <= amp <= 0.55 else (0.4 if amp < 0.70 else 0.15)
    # 6) 窗口内有过回撤（中位13.6%）
    dd = feat["max_dd"]
    sc["已有回撤"] = 1.0 if dd >= 0.08 else (0.6 if dd >= 0.05 else 0.2)
    # 7) 波动温和（中位3.1%，p75 3.7%）
    vol = feat["pre_vol"]
    sc["波动温和"] = 1.0 if vol <= 0.055 else (0.5 if vol <= 0.075 else 0.2)
    weights = {"小市值": 1.3, "前期弱势": 1.3, "低位蓄势": 1.2, "量能回升": 1.2,
               "振幅相当": 0.8, "已有回撤": 0.7, "波动温和": 0.6}
    total = sum(sc[k] * weights[k] for k in sc)
    return round(100 * total / sum(weights.values()), 1), sc


def _load_kcache():
    try:
        d = json.load(open(KCACHE_PATH, encoding="utf-8"))
        return d.get("stocks") or {}
    except Exception:
        return {}


def bars_for(code, kcache):
    """优先复用本流水线刚生成的 kline_cache.json（[date,o,c,l,h,v]），
    否则回退 analyze.get_kline（东财+腾讯/新浪）。返回 bars 升序。"""
    bare = code[2:] if code[:2] in ("sh", "sz", "bj") else code
    rec = kcache.get(bare)
    if rec and rec.get("kline"):
        bars = []
        for r in rec["kline"]:
            try:
                bars.append({"date": r[0], "open": float(r[1]), "last": float(r[2]),
                             "low": float(r[3]), "high": float(r[4]), "volume": int(r[5])})
            except Exception:
                continue
        if len(bars) >= 25:
            return bars
    return A.get_kline(code)


def main():
    kcache = _load_kcache()
    pool = get_pool()
    if not pool:
        print("[error] 初筛池为空（东财 clist 可能限流/不可用），"
              "保留上一版 strong_stocks，本次跳过。", file=sys.stderr)
        sys.exit(1)
    # 硬性剔除：ST/退市 + 股价>30（取K线前过滤，节省调用）
    kept = [(c, n, mv, px) for (c, n, mv, px) in pool if not is_excluded(n, px)]
    n_st = len(pool) - len(kept)
    print(f"初筛池: {len(pool)} 只 → 去ST/去股价>30 后: {len(kept)} 只（剔除 {n_st} 只）")
    results = []
    for code, name, mcap, px in kept:
        bars = bars_for(code, kcache)
        if not bars or len(bars) < 25:
            continue
        price = bars[-1]["last"]          # 以K线最新收盘价复核股价
        if price > 30:                     # 双保险：filter 值可能滞后
            continue
        lp = A.limit_pct(code, name or "")
        last = bars[-1]
        prev = bars[-2]["last"] if len(bars) > 1 else last["last"]
        if A.is_limit_up(last, prev, lp):  # 已涨停=已启动，排除
            continue
        feat = A.compute_window_features(bars, len(bars) - 1, None)
        if not feat:
            continue
        score, detail = similarity(feat, mcap)
        rec = {"code": code, "name": name, "score": score, "detail": detail,
               "mcap_yi": mcap, "price": price}
        for k in ["pre_ret20", "pre_vol", "pre_amp", "vol_ratio_l5_f10", "vol_min_ratio",
                  "max_dd", "gap_ups", "ma_align", "dist_from_high", "last3_ret",
                  "last_day_vol_ratio", "exch_avg"]:
            rec[k] = feat[k]
        # 附带最近30个交易日K线，供弹窗直接画图（前端无需再请求行情接口）
        rec["kline"] = [{"d": b["date"][5:], "o": b["open"], "h": b["high"],
                         "l": b["low"], "c": b["last"], "v": b["volume"]}
                        for b in bars[-30:]]
        results.append(rec)
    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:25]
    if top:
        keys = ["code", "name", "score", "mcap_yi", "price", "pre_ret20", "pre_amp",
                "vol_ratio_l5_f10", "max_dd", "dist_from_high", "last3_ret", "ma_align", "exch_avg"]
        try:
            with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
                f.write(",".join(keys) + "\n")
                for r in top:
                    f.write(",".join(
                        f"{r.get(k, '')}" if not isinstance(r.get(k), float) else f"{r[k]:.4f}"
                        for k in keys) + "\n")
        except Exception:
            pass
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(top, f, ensure_ascii=False, indent=2)
    print(f"评分完成，top {len(top)}。")
    for r in top[:12]:
        print(f"  {r['code']} {r['name']} 分={r['score']} 价={r['price']} 市值={r['mcap_yi']}亿 "
              f"前20日={r['pre_ret20']:.3f} 振幅={r['pre_amp']:.3f} 量比={r['vol_ratio_l5_f10']:.2f} "
              f"距高={r['dist_from_high']:.3f} 回撤={r['max_dd']:.3f}")


if __name__ == "__main__":
    main()
