#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
季报驱动逆向筛选系统 — 主管道 pipeline.py  (design.md v1)
顺序(修正A): 业绩(yjbb) → PE(yjbb×gdhs) → 回撤(腾讯K线) → 十大股东(gdfx) → 户数(gdhs) → 解禁(restricted)

用法:
  python pipeline.py                  # 全量运行(读 config.yaml, 拉取并缓存)
  python pipeline.py --no-fetch       # 复用 _cache 已有 CSV, 不重新拉取
  python pipeline.py --smoke          # 小样本冒烟(回撤/解禁只对前 N 只)
  python pipeline.py --limit 300      # 仅对前 N 只做回撤(加速验证)
"""
import os, sys, json, time, argparse, datetime, re, ssl
import urllib.request
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "_cache")
KLINE_DIR = os.path.join(CACHE, "kline")
OUT = os.path.join(HERE, "output")
for d in (CACHE, KLINE_DIR, OUT):
    os.makedirs(d, exist_ok=True)

# ---------- 工具 ----------
def log(*a):
    print(*a, flush=True)

def pick(df, *cands):
    """在 df.columns 中按子串容错找列; 返回第一个命中的列名。"""
    cols = list(df.columns)
    for c in cands:
        for col in cols:
            if c in col:
                return col
    raise KeyError(f"列名未命中 {cands}，现有列: {cols}")

def cached(name):
    p = os.path.join(CACHE, name)
    return p if os.path.exists(p) else None

def bse(code):
    """北交所: 8xxxxx / 4xxxxx / 920xxx —— 单独池, 不混入主筛选。"""
    return str(code).startswith(("8", "4", "920"))

# ---------- 触发窗口 + 数据完整度自检(修正C) ----------
def add_months(d, n):
    """纯标准库月份加减(规避 dateutil 依赖)。"""
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    # 处理月末溢出(如 1月31 +1月 → 2月28/29)
    day = min(d.day, [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return datetime.date(y, m, day)

def should_run_at(cfg, run_date=None):
    """判定 run_date 是否落在 report_period 的有效运行窗口内。
    返回 (ok: bool, msg: str, avail: date|None)。
    逻辑: 滞后数据(户数等)约在 报告期 + offset 月 可用; 之后 stale_after 月内有效, 超期判过时。"""
    if run_date is None:
        run_date = datetime.date.today()
    trig = cfg.get("trigger", {})
    offset = int(trig.get("data_available_offset_months", 4))
    stale = int(trig.get("stale_after_months", 6))
    period = str(cfg["report_period"])
    try:
        rep_end = datetime.date(int(period[:4]), int(period[4:6]), int(period[6:8]))
    except Exception:
        return False, f"report_period 解析失败: {period}", None
    avail = add_months(rep_end, offset)
    if run_date < avail:
        return False, (f"数据未齐: 报告期{period}滞后数据(户数等)约{avail}才完整, "
                       f"当前{run_date}偏早, 结果将基于残缺/陈旧数据"), avail
    months_since = (run_date.year - avail.year) * 12 + (run_date.month - avail.month)
    if months_since > stale:
        return False, (f"数据已过时: 距可用日{avail}已{months_since}月(>阈值{stale}), "
                       f"建议改用更新报告期或确认本周期数据已刷新"), avail
    return True, f"窗口有效(数据可用日{avail}, 距今{months_since}月)", avail

def data_completeness_check(cfg, period, no_fetch=False):
    """对各数据源做完整度自检, 返回 dict(含 per-source 状态与 overall.ok)。
    不强行重跑未齐数据, 仅核对 _cache 中的快照是否达标。"""
    comp = cfg.get("completeness", {})
    min_yjbb = int(comp.get("min_yjbb_rows", 3000))
    min_gdhs = int(comp.get("min_gdhs_rows", 3000))
    min_gdfx = int(comp.get("min_gdfx_rows", 10000))
    req_hist = comp.get("require_history_periods", True)
    min_eps = float(comp.get("min_eps_nonnull_ratio", 0.90))
    report = {"sources": {}, "ok": True, "notes": []}

    def src_status(name, n=None, detail="", ok=True):
        report["sources"][name] = {"rows": n, "detail": detail, "ok": ok}
        if not ok:
            report["ok"] = False

    # yjbb
    try:
        yjbb = fetch_yjbb(period, no_fetch)
        eps_col = pick(yjbb, "每股收益")
        eps_ratio = pd.to_numeric(yjbb[eps_col], errors="coerce").notna().mean()
        ok = (len(yjbb) >= min_yjbb) and (eps_ratio >= min_eps)
        src_status("yjbb", len(yjbb),
                   f"EPS非空率={eps_ratio:.1%}(阈{min_eps:.0%})", ok)
    except Exception as e:
        src_status("yjbb", None, f"读取失败: {e}", False)

    # gdhs 当期
    try:
        gd = fetch_gdhs(period, no_fetch)
        src_status("gdhs_cur", len(gd), "", len(gd) >= min_gdhs)
    except Exception as e:
        src_status("gdhs_cur", None, f"读取失败: {e}", False)

    # gdhs 历史期(若要求)
    hist_periods = cfg.get("holders_count", {}).get("history_periods", [])
    if req_hist and hist_periods:
        for hp in hist_periods:
            try:
                gdh = fetch_gdhs(hp, no_fetch)
                src_status(f"gdhs_{hp}", len(gdh), "", len(gdh) >= 100)
            except Exception as e:
                src_status(f"gdhs_{hp}", None, f"读取失败: {e}", False)
    elif hist_periods:
        report["notes"].append("require_history_periods=false, 跳过历史期核对(连续下降判定将无据)")

    # gdfx
    try:
        gdfx = fetch_gdfx(period, no_fetch)
        src_status("gdfx", len(gdfx), "", len(gdfx) >= min_gdfx)
    except Exception as e:
        src_status("gdfx", None, f"读取失败: {e}", False)

    if not report["ok"]:
        report["notes"].append("存在未达标数据源, 结果可信度下降; 建议重新拉取(--no-fetch 关闭后运行)或检查接口")
    return report

def fmt_completeness(report):
    lines = ["## 数据完整度自检(修正C)", ""]
    for name, s in report["sources"].items():
        mark = "✅" if s["ok"] else "⚠️"
        rows = "N/A" if s["rows"] is None else f"{s['rows']:,}"
        lines.append(f"- {mark} {name}: 行数={rows} {s['detail']}")
    if report["notes"]:
        for n in report["notes"]:
            lines.append(f"  - 注: {n}")
    lines.append(f"- 总体: {'✅ 通过' if report['ok'] else '⚠️ 未完全通过'}")
    return "\n".join(lines) + "\n"

# ---------- 配置 ----------
def load_config(path=os.path.join(HERE, "config.yaml")):
    import yaml
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

# ---------- 数据拉取(带缓存) ----------
def fetch_yjbb(period, no_fetch=False):
    name = f"yjbb_{period}.csv"
    p = cached(name)
    if no_fetch and p:
        return pd.read_csv(p, dtype={"股票代码": str})
    import akshare as ak
    df = ak.stock_yjbb_em(period)
    df.to_csv(os.path.join(CACHE, name), index=False, encoding="utf-8-sig")
    return df

def fetch_gdhs(period, no_fetch=False):
    name = f"gdhs_{period}.csv"
    p = cached(name)
    if no_fetch and p:
        return pd.read_csv(p, dtype={"股票代码": str})
    import akshare as ak
    df = ak.stock_zh_a_gdhs(period)
    df.to_csv(os.path.join(CACHE, name), index=False, encoding="utf-8-sig")
    return df

def fetch_gdfx(period, no_fetch=False):
    name = f"gdfx_{period}.csv"
    p = cached(name)
    if no_fetch and p:
        return pd.read_csv(p, dtype={"股票代码": str})
    import akshare as ak
    df = ak.stock_gdfx_free_holding_analyse_em(period)
    df.to_csv(os.path.join(CACHE, name), index=False, encoding="utf-8-sig")
    return df

def fetch_restricted(code, no_fetch=False):
    import akshare as ak
    return ak.stock_restricted_release_queue_em(str(code))

# ---------- 腾讯 K 线(回撤兜底) ----------
def tencent_kline(code, beg="2025-01-01", end=None, n=320):
    market = "sh" if str(code)[0] in "69" else "sz"
    # 用 kline/kline 端点(稳定返回 day 键); fqkline/get 在本环境仅回 version 无效
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={market}{code},day,,,{n}"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                js = json.loads(resp.read().decode("utf-8"))
            node = js.get("data", {}).get(f"{market}{code}", {})
            rows = node.get("qfqday") or node.get("day") or []
            return rows  # [[date, open, close, high, low, vol, ...], ...]
        except Exception as e:
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    log(f"  [warn] tencent_kline {code} 失败: {last_err}")
    return []

def get_kline(code):
    """带磁盘缓存的 K 线; 返回 DataFrame[date,open,close,high,low,vol]。"""
    code = str(code)
    cf = os.path.join(KLINE_DIR, f"{code}.csv")
    if os.path.exists(cf):
        df = pd.read_csv(cf)
        df["date"] = pd.to_datetime(df["date"])
        return df
    rows = tencent_kline(code)
    if not rows:
        return pd.DataFrame(columns=["date", "open", "close", "high", "low", "vol"])
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["date", "open", "close", "high", "low", "vol"]
    for c in ["open", "close", "high", "low", "vol"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df.to_csv(cf, index=False)
    return df

def compute_drawdown(code, window=120, suspend_gap=10):
    """返回 dict: dd(回撤率), peak, current, n_days, excluded(原因或None)。
    排除: 上市不足 window 日(new), 窗口停牌> suspend_gap 日(suspend)。"""
    df = get_kline(code).sort_values("date")
    n = len(df)
    if n < window:
        return {"dd": None, "excluded": f"new(<{window}日, 实有{n})"}
    recent = df.tail(window)
    # 停牌代理: 期望交易日 ≈ 窗口*(约0.45 年交易日占比); 实际显著偏少→疑似停牌
    span_days = (recent["date"].iloc[-1] - recent["date"].iloc[0]).days
    exp_tdays = int(span_days * 0.45)
    missing = exp_tdays - len(recent)
    if missing > suspend_gap:
        return {"dd": None, "excluded": f"suspend(缺{missing}交易日)"}
    peak = recent["high"].max()
    current = recent["close"].iloc[-1]
    dd = (peak - current) / peak if peak > 0 else 0.0
    return {"dd": float(dd), "peak": float(peak), "current": float(current),
            "n_days": int(n), "excluded": None}

# ---------- 机构硬闸 ----------
INSTITUTION_KEYWORDS = [("社保", "社保"), ("QFII", "QFII"), ("保险", "保险"),
                        ("信托", "信托"), ("私募", "私募"), ("基金", "基金")]

def classify_holder_type(t):
    s = str(t)
    for kw, cat in INSTITUTION_KEYWORDS:
        if kw in s:
            return cat
    return None

def institution_pass(gdfx, code, types_set, private_fund_on):
    """该股票是否含 >=1 个目标机构 且 持股变动=增加/新进。"""
    sub = gdfx[gdfx[pick(gdfx, "股票代码")].astype(str) == str(code)]
    if sub.empty:
        return False, []
    type_col = pick(gdfx, "股东类型")
    chg_col = pick(gdfx, "期末持股-持股变动", "持股变动")
    hits = []
    for _, r in sub.iterrows():
        cat = classify_holder_type(r[type_col])
        if cat is None:
            continue
        if not private_fund_on and cat == "私募":
            continue
        if cat not in types_set:
            continue
        chg = str(r[chg_col])
        if ("增加" in chg) or ("新进" in chg):
            hits.append(cat)
    return (len(hits) > 0), hits

# ---------- 解禁标注 ----------
def unblock_flag(code, window_days=60, as_of=None):
    """未来 window_days 日内是否有解禁批次 → 'YELLOW' / ''。"""
    if as_of is None:
        as_of = datetime.date.today()
    try:
        df = fetch_restricted(code)
    except Exception:
        return ""
    if df is None or len(df) == 0:
        return ""
    date_col = pick(df, "解禁时间", "解禁日期", "时间", "日期")
    for _, r in df.iterrows():
        try:
            d = pd.to_datetime(r[date_col]).date()
        except Exception:
            continue
        delta = (d - as_of).days
        if 0 <= delta <= window_days:
            return "YELLOW"
    return ""

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--check", action="store_true",
                    help="仅运行触发窗口+数据完整度自检, 不跑全量扫描")
    ap.add_argument("--force", action="store_true",
                    help="忽略触发窗口判定(仅测试/补跑用)")
    args = ap.parse_args()

    cfg = load_config()
    period = str(cfg["report_period"])
    hist_periods = cfg.get("holders_count", {}).get("history_periods", [])
    pe_cfg = cfg["pe"]
    holder_cfg = cfg["holder"]
    dd_cfg = cfg["drawdown"]
    inst_cfg = cfg["institution"]
    hc_cfg = cfg["holders_count"]
    unblock_cfg = cfg["unblock"]

    run_date = datetime.date.today()
    # 滞后声明: 距季报截止日交易日数(近似, 不含节假日)
    try:
        rep_end = datetime.date(int(period[:4]), int(period[4:6]), int(period[6:8]))
        n_tdays = len(pd.bdate_range(rep_end, run_date)) - 1
    except Exception:
        n_tdays = "?"

    # ---- 修正C: 触发窗口判定 ----
    trig_ok, trig_msg, avail = should_run_at(cfg, run_date)
    log(f"[触发] {trig_msg}")
    if args.check:
        # 自检模式: 仅核对完整度并退出
        report = data_completeness_check(cfg, period, args.no_fetch)
        log(fmt_completeness(report))
        log(f"[CHECK] 触发窗口={'OK' if trig_ok else 'OUT'} | 完整度={'OK' if report['ok'] else 'WARN'}")
        log("=== CHECK DONE ===")
        return
    if (not trig_ok) and cfg.get("trigger", {}).get("enforce", True) and (not args.force):
        log(f"[中止] 触发窗口判定未通过且 enforce=true。如需绕过请加 --force(仅测试)。")
        log("=== ABORT ===")
        return

    # ---- 修正C: 数据完整度自检(运行前核对, 不阻断但记录) ----
    report = data_completeness_check(cfg, period, args.no_fetch)
    if not report["ok"]:
        log(f"[完整度⚠️] {fmt_completeness(report).strip().splitlines()[-1]}")

    log(f"[配置] report_period={period} hist={hist_periods} run_date={run_date} "
        f"滞后={n_tdays}交易日  no_fetch={args.no_fetch}")

    # --- Layer0: 拉取 ---
    t0 = time.time()
    yjbb = fetch_yjbb(period, args.no_fetch)
    gdhs_cur = fetch_gdhs(period, args.no_fetch)
    log(f"[Layer0] yjbb={len(yjbb)} gdhs={len(gdhs_cur)}  耗时{time.time()-t0:.1f}s")

    # 列解析
    y_code = pick(yjbb, "股票代码"); y_name = pick(yjbb, "股票简称")
    y_yoy = pick(yjbb, "净利润-同比增长"); y_eps = pick(yjbb, "每股收益"); y_ind = pick(yjbb, "所处行业")
    g_code = pick(gdhs_cur, "代码", "股票代码"); g_price = pick(gdhs_cur, "最新价")
    g_avgmv = pick(gdhs_cur, "户均持股市值"); g_avgqty = pick(gdhs_cur, "户均持股数量")
    g_chg = pick(gdhs_cur, "股东户数-增减比例")

    # 统一 code 为字符串并建立字典
    yjbb[y_code] = yjbb[y_code].astype(str)
    gdhs_cur[g_code] = gdhs_cur[g_code].astype(str)

    # --- Layer1① 业绩稳增: 净利同比 > 0 ---
    yjbb[y_yoy] = pd.to_numeric(yjbb[y_yoy], errors="coerce")
    perf = yjbb[yjbb[y_yoy] > 0].copy()
    log(f"[Filter① 业绩] 净利同比>0: {len(perf)} 只")

    # 合并 gdhs(当期)
    perf = perf.merge(gdhs_cur[[g_code, g_price, g_avgmv, g_avgqty, g_chg]],
                      left_on=y_code, right_on=g_code, how="inner")
    perf = perf.rename(columns={g_price: "price", g_avgmv: "holder_avg_mv",
                                g_avgqty: "holder_avg_qty", g_chg: "holder_chg"})
    # 剔除北交所
    before = len(perf)
    perf = perf[~perf[y_code].apply(bse)]
    log(f"[Filter] 剔除北交所: {before}→{len(perf)} 只")

    # --- Layer1② PE 相对低估 ---
    perf["eps"] = pd.to_numeric(perf[y_eps], errors="coerce")
    factor = 4.0 / 3.0
    perf["pe_annualized_3q"] = perf.apply(
        lambda r: (r["price"] / (r["eps"] * factor)) if (pd.notna(r["eps"]) and r["eps"] > 0 and r["price"] > 0) else float("nan"),
        axis=1)
    # 行业中位数(全宇宙可算 PE 的同行业)
    valid_pe = perf.dropna(subset=["pe_annualized_3q"])
    ind_med = valid_pe.groupby(y_ind)["pe_annualized_3q"].median()
    perf["ind_pe_med"] = perf[y_ind].map(ind_med)
    mult = float(pe_cfg["industry_median_mult"])
    pe_pass = perf[(perf["pe_annualized_3q"].notna()) &
                   (perf["pe_annualized_3q"] > 0) &
                   (perf["pe_annualized_3q"] <= perf["ind_pe_med"] * mult)]
    log(f"[Filter② PE] 相对行业中位×{mult} 低估: {len(pe_pass)} 只 (可算PE={len(valid_pe)})")
    perf = pe_pass

    # --- Layer1③ 回撤深度(腾讯K线, 仅当前池) ---
    univ = list(perf[y_code].astype(str))
    if args.limit:
        univ = univ[:args.limit]
    if args.smoke:
        univ = univ[:50]
    dd_rows = []
    for i, code in enumerate(univ, 1):
        res = compute_drawdown(code, window=int(dd_cfg["exclude_new_listing_days"]),
                               suspend_gap=int(dd_cfg["exclude_suspend_days"]))
        dd_rows.append((code, res))
        if i % 50 == 0:
            log(f"  回撤进度 {i}/{len(univ)}")
    dd_df = pd.DataFrame([(c, r["dd"], r["excluded"]) for c, r in dd_rows],
                         columns=["code", "dd", "dd_excluded"])
    perf = perf.merge(dd_df, left_on=y_code, right_on="code", how="left")
    thr = float(dd_cfg["threshold"])
    dd_ok = perf[(perf["dd_excluded"].isna()) & (perf["dd"] >= thr)]
    log(f"[Filter③ 回撤] 回撤≥{thr}: {len(dd_ok)} 只 (已排除新股/停牌)")
    perf = dd_ok

    # --- Layer1④ 机构硬闸(gdfx) ---
    gdfx = fetch_gdfx(period, args.no_fetch)
    g_code_g = pick(gdfx, "股票代码")
    gdfx[g_code_g] = gdfx[g_code_g].astype(str)
    types_set = set(inst_cfg["types"])
    pf_on = (inst_cfg.get("private_fund_match", "heuristic") != "off")
    log(f"[Layer0] gdfx 载入 {len(gdfx)} 行  private_fund_on={pf_on}")
    inst_hits = {}
    for code in perf[y_code].astype(str):
        ok, cats = institution_pass(gdfx, code, types_set, pf_on)
        if ok:
            inst_hits[code] = cats
    perf = perf[perf[y_code].astype(str).isin(inst_hits.keys())]
    perf["inst_hits"] = perf[y_code].astype(str).map(inst_hits)
    log(f"[Filter④ 机构硬闸] 含机构新进/增持: {len(perf)} 只")

    # --- Layer1⑤ + 交叉: 户数出清 + 户均分位 ---
    perf["holder_chg"] = pd.to_numeric(perf["holder_chg"], errors="coerce")
    # 单季环比下降
    if hc_cfg.get("decline_required", True):
        perf = perf[perf["holder_chg"] < 0]
    # 连续两季下降(需历史 gdhs)
    if int(hc_cfg.get("consecutive_quarters", 0)) >= 2 and hist_periods:
        hist_chg = {}
        for hp in hist_periods:
            gd = fetch_gdhs(hp, args.no_fetch)
            gc = pick(gd, "代码", "股票代码"); gch = pick(gd, "股东户数-增减比例")
            gd[gc] = gd[gc].astype(str)
            hist_chg[hp] = dict(zip(gd[gc].astype(str), pd.to_numeric(gd[gch], errors="coerce")))
        # 需 当期<上1 <上2 均<0
        def consec(c):
            cur = perf.loc[perf[y_code].astype(str) == c, "holder_chg"]
            if cur.empty: return False
            c0 = float(cur.iloc[0])
            c1 = hist_chg[hist_periods[0]].get(c, float("nan"))
            c2 = hist_chg[hist_periods[1]].get(c, float("nan"))
            return (c0 < 0) and (c1 < 0) and (c2 < 0)
        keep = [c for c in perf[y_code].astype(str) if consec(c)]
        perf = perf[perf[y_code].astype(str).isin(keep)]
        log(f"[Filter⑤ 连续下降] 连续{hc_cfg['consecutive_quarters']}季下降: {len(perf)} 只")
    # 户均市值分位过滤(修正②)
    perf["holder_avg_mv"] = pd.to_numeric(perf["holder_avg_mv"], errors="coerce")
    cutoff = float(holder_cfg["pctile_cutoff"]); floor = float(holder_cfg["hard_floor"])
    q = perf["holder_avg_mv"].quantile(cutoff)
    perf["holder_mv_pctile_flag"] = (perf["holder_avg_mv"] >= q)
    perf = perf[(perf["holder_avg_mv"] >= q) & (perf["holder_avg_mv"] >= floor)]
    log(f"[Filter⑤ 户均分位] 池内第{cutoff}分位(≥{q:,.0f}) & ≥硬下限{floor}: {len(perf)} 只")

    # --- Layer1/③ 户均持股数量升(质量, 仅标注不硬滤) ---
    perf["holder_avg_qty"] = pd.to_numeric(perf["holder_avg_qty"], errors="coerce")

    # --- Layer3 解禁标注(逐只, 仅候选池) ---
    cand = list(perf[y_code].astype(str))
    if args.smoke:
        cand = cand[:20]
    flags = {c: unblock_flag(c, window_days=int(unblock_cfg["window_days"])) for c in cand}
    perf["unblock_60d_flag"] = perf[y_code].astype(str).map(flags).fillna("")
    perf["reduce_plan_manual"] = ""  # 减持人工列
    log(f"[Layer3] 解禁标黄 {sum(1 for v in flags.values() if v=='YELLOW')}/{len(flags)} 只; 减持列留空待人工")

    # --- 输出 ---
    cols = [y_code, y_name, y_ind, y_yoy, "pe_annualized_3q", "ind_pe_med",
            "dd", "inst_hits", "holder_chg", "holder_avg_mv", "holder_avg_qty",
            "unblock_60d_flag", "reduce_plan_manual"]
    out_df = perf[cols].copy()
    out_df = out_df.sort_values("dd", ascending=False)
    stamp = run_date.strftime("%Y%m%d")
    csv_path = os.path.join(OUT, f"candidates_{stamp}.csv")
    md_path = os.path.join(OUT, f"candidates_{stamp}.md")
    out_df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # 头部声明 + 说明
    header = build_header(cfg, period, run_date, n_tdays, len(out_df),
                          perf["pe_annualized_3q"].median(),
                          trig_ok, trig_msg, report)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(fmt_completeness(report))
        f.write("\n## 候选池\n\n")
        f.write(out_df.to_markdown(index=False) if hasattr(out_df, "to_markdown") else out_df.to_string(index=False))
        f.write("\n\n## 字段口径说明\n")
        f.write("- pe_annualized_3q: 三季报年化近似 = 最新价 ÷ (三季报每股收益 × 4/3)，**非机构 forward PE**。季节性行业(农业/零售等)误差显著，需人工复核。\n")
        f.write("- unblock_60d_flag: YELLOW=未来60日内有解禁批次(数据源 stock_restricted_release_queue_em)。\n")
        f.write("- reduce_plan_manual: 减持预告无干净结构化源，留空待人工至巨潮/交易所核查。\n")
        f.write("- holder_mv_pctile_flag: 户均市值已在池内按第20百分位+2万硬下限过滤。\n")
    log(f"[输出] {csv_path}\n[输出] {md_path}\n候选 {len(out_df)} 只")
    log("=== DONE ===")

def build_header(cfg, period, run_date, n_tdays, n_cand, pe_med,
                trig_ok, trig_msg, report):
    pe_cfg = cfg["pe"]
    trig_line = (f"**触发窗口**: {'✅ 有效' if trig_ok else '⚠️ 越界'} — {trig_msg}" if trig_ok
                 else f"**触发窗口**: ⚠️ 越界 — {trig_msg}（结果基于陈旧/残缺数据，仅供参考）")
    comp_line = "✅ 通过" if report["ok"] else "⚠️ 未完全通过(见下方自检明细)"
    return f"""# 季报逆向筛选候选池 · {run_date}

[季报逆向筛选] run-date={run_date}  报告期={period}
{trig_line}
数据完整度: {comp_line}
**滞后声明**: 回撤基于 run-date 收盘价，距季报截止日已 {n_tdays} 个交易日。从筛选到实际下单之间存在执行间隔，期间价格变动未反映于本次输出。
**PE 口径**: pe_annualized_3q 为三季报年化近似(×4/3)，非机构 forward PE；季节性行业(农业/零售等)需人工复核。行业分组来源={pe_cfg.get('industry_data_source')}({pe_cfg.get('industry_classification')})。
候选数量: {n_cand} 只
"""

if __name__ == "__main__":
    main()
