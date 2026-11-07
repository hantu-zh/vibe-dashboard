#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
daily_run.py —— GitHub Actions 每日自动更新引擎（零第三方依赖）

用途：由 .github/workflows/auto_trader_daily.yml 每天收盘后触发一次。
    读取仓库根目录的 4 个 vibe-dashboard 信号 JSON + trader_state.json（持久化状态），
    跑一轮「盯市 → 止盈止损 → 按信号建仓」，输出 trader_snapshot.json（展示页读取）
    并写回 trader_state.json（延续持仓，保证是连续账户而非每天重置）。

输入文件（均在仓库根）：
    strong_stocks.json  strongbuy_data.json  daily_picks.json  cffex_net_position.json
    trader_state.json   （不存在则按初始资金新建）

输出文件（均在仓库根，由 workflow 自动 commit）：
    trader_snapshot.json    展示页用的账户快照
    trader_state.json       下一轮的状态（持仓/现金/流水/历史）

本地试跑：  python daily_run.py --root <仓库根目录> [--dry-run]
"""

import json
import os
import statistics
import sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

# ---------------- 引擎参数 ----------------
START_CAPITAL_TOTAL = 150000.0          # 总委托资金（两个云端模拟账户合计）
ACCOUNTS = [
    # id / 委托金 / 启用的策略及其占该账户权益的比例
    {"id": "cloud-main-A", "mandate": 100000.0,
     "strategies": {"strong": 0.35, "strongbuy": 0.25, "daily_picks": 0.20}},
    {"id": "cloud-sat-B", "mandate": 50000.0,
     "strategies": {"strong": 0.30, "strongbuy": 0.20}},
]
TAKE_PROFIT_PCT = 8.0        # 止盈（%）
STOP_LOSS_PCT = -5.0         # 止损（%）
MAX_HOLD_DAYS = 10           # 持有超期且盈利则轮出（单位：交易日，与回测 T+10 对齐；旧版按自然日）
MAX_SINGLE_PCT = 0.18        # 单票市值不超过账户权益的比例
MAX_EXPOSURE_PCT = 0.90      # 总仓位上限
DRAWDOWN_HALT_RATIO = 0.85   # 权益跌破委托金该比例 → 暂停新建仓（回撤保护）
BUY_SLIPPAGE = 1.002         # 买入滑点
SELL_SLIPPAGE = 0.998        # 卖出滑点
COMMISSION = 0.00025         # 佣金（单边）
MIN_COMMISSION = 5.0         # 券商佣金最低 5 元/笔（旧版漏掉，小额成交成本被严重低估）
TRANSFER_FEE = 0.00001       # 过户费 0.001%（沪深双边）
STAMP_TAX = 0.0005           # 印花税（仅卖出）
FRESH_DAYS = 3               # 信号源新鲜度容忍天数，超过则视为停更并跳过
CLOSE_STALE_DAYS = 5          # 真实收盘价缓存(kline_cache)最大可容忍滞后天数，超过则回退信号价
MAX_TRADES_KEEP = 300
MAX_NEW_BUYS_PER_DAY = 6
PER_STRATEGY_TOP = 12        # 每路信号进入全局候选池的条数上限（配合分位归一化消除量纲差异）
LIMIT_UP_MAIN = 9.8          # 主板涨停阈值（%）
LIMIT_UP_GEM = 19.8          # 创业板 / 科创板涨停阈值（%）

# —— 自适应层阈值（单位统一为「百分点」）——
# cffex_net_position.json 的 net_ratio 是小数（如 -0.0519 表示 -5.19%），
# 旧版直接拿它和 ±1.0 / 0.05 / 1.2 这些百分数阈值比较，量纲差 100 倍，
# 导致 RISK_ON / RISK_OFF 命中 0 天、情绪趋势恒 FLAT、波动折扣永不触发。
# 现在 cffex_daily_means() 内统一 ×100，以下阈值全部按百分点标定。
# 另：股指期货机构套保使净持仓长期结构性净空（实测 101 日均值 -8.46% ~ -5.62%），
# 用绝对零点判定会变成「天天 RISK_OFF 停买」，因此改用相对 60 日中位数的偏离。
REGIME_BASELINE_DAYS = 60    # 相对基线窗口（交易日）
REGIME_DEV_THRESHOLD = 0.5   # 偏离基线 > ±0.5pp 判定 RISK_ON / RISK_OFF
MOOD_SLOPE_THRESHOLD = 0.05  # 情绪斜率阈值（百分点 / 交易日）
MOOD_VOL_HIGH = 0.60         # 情绪波动阈值（10 日净持仓标准差，百分点）

SIGNAL_FILES = ["strong_stocks.json", "strongbuy_data.json",
                "daily_picks.json", "cffex_net_position.json"]


# ---------------- 工具函数 ----------------
def now_cst():
    return datetime.now(CST)


def today_str():
    return now_cst().strftime("%Y-%m-%d")


# 2026 年 A 股休市安排（沪深北交易所官方公告，2025-12-22 发布）。
# 已含节假日闭市区间；周末本就休市，这里一并列出以便完整对照。
# 注：若遇调休补班（极少），需按当年最新公告刷新此集合。
HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-01-03",                 # 元旦
    "2026-02-14", "2026-02-15", "2026-02-16", "2026-02-17",
    "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-21",
    "2026-02-22", "2026-02-23", "2026-02-28",                 # 春节
    "2026-04-04", "2026-04-05", "2026-04-06",                 # 清明
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04",
    "2026-05-05", "2026-05-09",                                # 劳动节
    "2026-06-19", "2026-06-20", "2026-06-21",                 # 端午
    "2026-09-20", "2026-09-25", "2026-09-26", "2026-09-27",   # 中秋
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
    "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-10",   # 国庆
}


def is_trading_day(d):
    """A股交易日判定：周一到周五且不在法定休市日才为交易日。"""
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in HOLIDAYS_2026


def parse_date(s):
    """容错解析 2026-09-14 / 2026-09-14 12:00 / '2026-09-14'（带引号）等格式。"""
    if not s:
        return None
    s = str(s).strip().strip('"').strip("'")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:len(fmt) + 2], fmt).date()
        except Exception:
            continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def num(v):
    """把可能是字符串/带引号/带%的值转 float，失败返回 None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().strip('"').strip("'").replace(",", "").replace("%", "")
    if s in ("", "-", "--", "None", "null"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def is_valid_code(code):
    c = str(code or "").strip()
    return len(c) == 6 and c.isdigit()


def find_root(explicit=None):
    """定位仓库根目录（信号 JSON 所在目录）。"""
    cands = []
    if explicit:
        cands.append(explicit)
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.dirname(here), here, os.getcwd()]
    for c in cands:
        if os.path.exists(os.path.join(c, "daily_picks.json")):
            return c
    return cands[0]


def load_json(root, name):
    p = os.path.join(root, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("  ! 读取失败 %s: %s" % (name, e), file=sys.stderr)
        return None


def freshness(updated_str, today):
    """返回 (是否新鲜, 停更天数)。"""
    d = parse_date(updated_str)
    if not d:
        return False, None
    days = (today - d).days
    return days <= FRESH_DAYS, days


def build_close_map(kline_cache, today):
    """从 kline_cache.json 取每只股票『最新且 <= 运行日』的真实收盘价(日K收盘)。

    kline_cache 结构: {"updated":..., "count":..., "stocks":{code:{"name":...,
        "kline":[[日期,开,高,低,收,量], ...]}}}
    返回 {code: (收盘价:float, 收盘日:date)}；bar 收盘价位于每根第 5 项(index 4)。
    找不到合法数据或日期晚于 today 的票不进入结果（调用方据此回退信号价）。
    """
    out = {}
    if not isinstance(kline_cache, dict):
        return out
    stocks = kline_cache.get("stocks") or {}
    for code, rec in stocks.items():
        if not isinstance(rec, dict):
            continue
        kl = rec.get("kline")
        if not isinstance(kl, list) or not kl:
            continue
        best_date = None
        best_close = None
        for bar in kl:
            if not isinstance(bar, list) or len(bar) < 5:
                continue
            d = parse_date(bar[0])
            if not d or d > today:
                continue
            c = num(bar[4])
            if c is None or c <= 0:
                continue
            if best_date is None or d > best_date:
                best_date = d
                best_close = c
        if best_date is not None and best_close is not None:
            out[str(code).strip()] = (best_close, best_date)
    return out


# ---------------- 交易成本 / 可执行性 / 日历 ----------------
def buy_fee(gross):
    """买入费用：佣金（含券商最低 5 元）+ 过户费。"""
    return max(gross * COMMISSION, MIN_COMMISSION) + gross * TRANSFER_FEE


def sell_fee(gross):
    """卖出费用：佣金（含券商最低 5 元）+ 印花税 + 过户费。"""
    return (max(gross * COMMISSION, MIN_COMMISSION)
            + gross * STAMP_TAX + gross * TRANSFER_FEE)


def trading_days_between(d0, d1):
    """两个日期之间的交易日数（不含起始日、含结束日），用于持有期计算。"""
    if not d0 or d1 <= d0:
        return 0
    n, cur = 0, d0
    while cur < d1:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            n += 1
    return n


def tradable(c, close_map, today):
    """建仓前的可执行性 / 合规过滤，返回 (是否放行, 原因)。

    旧版无任何过滤，涨停票、ST、次新、北交所都可能按信号价「成交」，
    实际盘中根本买不到，模拟收益因此系统性虚高。
    注意：不能用 kline_cache 的 K 线根数判断次新股 —— 该缓存按固定窗口裁剪
    （实测 497 只中 210 只仅 1 根、其余多为 30 根），会误杀全部候选。
    """
    name = str(c.get("name") or "").upper()
    code = str(c.get("code") or "").strip()
    if any(k in name for k in ("ST", "退", "PT")):
        return False, "ST/退市"
    if code.startswith(("4", "8")):
        return False, "北交所/老三板"
    # A 股上市首日 / 次新 5 日内简称前缀为 N / C（后接空格或中文）
    if len(name) >= 2 and name[0] in ("N", "C") and not name[1].isalpha():
        return False, "次新股"
    chg = num(c.get("chg")) or 0.0
    lim = LIMIT_UP_GEM if code.startswith(("300", "301", "688")) else LIMIT_UP_MAIN
    if abs(chg) >= lim:
        return False, "涨跌停不可成交"
    cm = close_map.get(code)
    if cm and cm[1] is not None and (today - cm[1]).days > CLOSE_STALE_DAYS:
        return False, "疑似停牌/行情陈旧"
    return True, ""


def close_data_fresh(kline_cache, today):
    """当日收盘价数据是否就绪，返回 (bool, 说明)。

    旧版引擎约 15:12 跑、kline_cache 16:35 才生成，28 只持仓只有一半用真实
    收盘价盯市，另一半回退信号价却照样建仓。这里作为硬闸：非当日收盘数据
    只允许盯市结算，不允许新建仓（--force 可显式绕过，仅供本地调试）。
    """
    if not isinstance(kline_cache, dict) or not kline_cache.get("stocks"):
        return False, "kline_cache.json 缺失或为空"
    d = parse_date(kline_cache.get("updated"))
    if not d:
        return False, "kline_cache.updated 无法解析"
    if d != today:
        return False, "kline_cache.updated=%s 非当日(%s)" % (d, today)
    return True, ""


# ---------------- 信号采集 ----------------
def collect_leaf_stocks(obj, out):
    """daily_picks 结构不固定，递归扫描所有含 code+name 的记录。"""
    if isinstance(obj, dict):
        if "code" in obj and "name" in obj:
            out.append(obj)
        else:
            for v in obj.values():
                collect_leaf_stocks(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_leaf_stocks(v, out)


def pick_latest_date(daily, today):
    """daily_picks 顶层是 {日期: 内容}，选最新且不太旧的日期键。"""
    if not isinstance(daily, dict):
        return None
    best = None
    for k in daily.keys():
        d = parse_date(k)
        if not d:
            continue
        if (today - d).days > FRESH_DAYS:
            continue
        if best is None or d > best[0]:
            best = (d, k)
    return best[1] if best else None


def build_candidates(signals, today):
    """返回 (候选买入列表, 各源新鲜度 dict)。"""
    strong = signals.get("strong_stocks.json") or {}
    strongbuy = signals.get("strongbuy_data.json") or {}
    daily = signals.get("daily_picks.json") or {}

    fresh = {}
    ok_strong, days = freshness(strong.get("updated"), today)
    fresh["strong_stocks"] = {"updated": str(strong.get("updated")), "used": ok_strong,
                              "stale_days": days}
    ok_sb, days_sb = freshness(strongbuy.get("updated") or strongbuy.get("updated_at"), today)
    fresh["strongbuy_data"] = {"updated": str(strongbuy.get("updated") or strongbuy.get("updated_at")),
                               "used": ok_sb, "stale_days": days_sb}
    dk = pick_latest_date(daily, today)
    fresh["daily_picks"] = {"updated": dk, "used": bool(dk), "stale_days": None}

    buckets = {"strong": [], "strongbuy": [], "daily_picks": []}

    def put(strategy, code, name, price, chg, raw):
        if not is_valid_code(code) or price is None or price <= 0:
            return
        buckets[strategy].append({"code": str(code).strip(),
                                  "name": str(name or code).strip(),
                                  "price": round(price, 3), "chg": chg or 0.0,
                                  "raw": raw, "score": 0.0, "strategy": strategy})

    if ok_strong:
        for r in (strong.get("stocks") or []) + (strong.get("yimeng") or []):
            put("strong", r.get("code"), r.get("name"), num(r.get("price")),
                num(r.get("change_pct")),
                (num(r.get("score")) or 0) * 10 + (num(r.get("change_pct")) or 0))

    if ok_sb:
        for key in ("yimeng", "stocks"):
            for r in (strongbuy.get(key) or []):
                put("strongbuy", r.get("code"), r.get("name"), num(r.get("price")),
                    num(r.get("change_pct")),
                    (num(r.get("net_inflow_pct")) or 0) * 0.5 + (num(r.get("change_pct")) or 0))

    if dk:
        leaves = []
        collect_leaf_stocks(daily.get(dk), leaves)
        scored = []
        for r in leaves:
            sc = num(r.get("final_score")) or num(r.get("heat_score")) or num(r.get("base_score")) or 0
            scored.append((sc, r))
        scored.sort(key=lambda x: -x[0])
        for sc, r in scored[:25]:
            put("daily_picks", r.get("code"), r.get("name"), num(r.get("price")),
                num(r.get("chg_pct")) or num(r.get("change_pct")), sc)

    # 分位归一化：三路原始分不在同一量纲（strong≈1000、strongbuy≈0~120、daily_picks≈0~103），
    # 旧版直接全局排序让 strong 永远霸榜，另两路的资金配额形同优先级、几乎捡不到票。
    # 每路先内部转成 0~100 分位分、各取 TopN，再同票去重 + 全局排序，三路才公平竞争。
    seen = {}
    for strat, rows in buckets.items():
        rows.sort(key=lambda x: -x["raw"])
        n = len(rows)
        for i, r in enumerate(rows[:PER_STRATEGY_TOP]):
            r["score"] = round(100.0 * (n - i) / n, 2) if n else 0.0
            key = r["code"]
            if key in seen and r["score"] <= seen[key]["score"]:
                continue
            seen[key] = r
    cands = sorted(seen.values(), key=lambda x: -x["score"])
    return cands, fresh


def cffex_daily_means(cffex):
    """返回按日期排序的 [(date, 当日净持仓均值百分点)]。

    单位换算都收敛在这里：源 JSON 的 net_ratio 是小数（-0.0519 表示 -5.19%），
    输出统一 ×100 成百分点，供后面所有阈值/展示共用，杜绝小数与百分数混用。
    """
    out = []
    if not isinstance(cffex, dict):
        return out
    for d, contracts in cffex.items():
        dt = parse_date(d)
        if not dt or not isinstance(contracts, dict):
            continue
        ratios = [num(v.get("net_ratio")) for v in contracts.values()
                  if isinstance(v, dict) and num(v.get("net_ratio")) is not None]
        if ratios:
            out.append((dt, sum(ratios) / len(ratios) * 100.0))
    out.sort(key=lambda x: x[0])
    return out


def regime_deviation(means):
    """最新一日净持仓相对前 60 日中位数的偏离（百分点）。样本不足时视为 0。"""
    if len(means) < 4:
        return 0.0
    hist = [m for _, m in means[:-1]][-REGIME_BASELINE_DAYS:]
    if len(hist) < 3:
        return 0.0
    return means[-1][1] - statistics.median(hist)


def compute_regime(cffex, today):
    """用股指期货净多空相对基线的偏离判定市场氛围。

    绝对净持仓长期为负是机构套保常态，不能当作绝对风险偏好；
    只看「较近 60 日基线转多 / 转空」才有择时含义。
    """
    means = cffex_daily_means(cffex)
    if not means:
        return "NEUTRAL", "无股指期货数据"
    d, avg = means[-1]
    dev = regime_deviation(means)
    hist = [m for _, m in means[:-1]][-REGIME_BASELINE_DAYS:]
    base = statistics.median(hist) if len(hist) >= 3 else avg
    parts = []
    latest = cffex.get(d.strftime("%Y-%m-%d")) or {}
    for k, v in latest.items():
        if isinstance(v, dict) and num(v.get("net_ratio")) is not None:
            parts.append("%s %.2f%%" % (k, num(v.get("net_ratio")) * 100.0))
    detail = ("%s 净持仓均值 %.2f%%（60日基线 %.2f%%，偏离 %+.2fpp；%s）"
              % (d, avg, base, dev, "，".join(parts)))
    if dev > REGIME_DEV_THRESHOLD:
        return "RISK_ON", detail
    if dev < -REGIME_DEV_THRESHOLD:
        return "RISK_OFF", detail
    return "NEUTRAL", detail


def build_market_context(cffex, run_date):
    """市场环境自适应层：读股指期货净多空时序 + 运行日历，
    输出当日自适应交易参数（仍跑模拟盘，不改变账户连续性）。

    逻辑（净持仓序列统一为百分点）：
      1. regime：最新净持仓相对 60 日基线偏离 > +0.5pp 偏多(RISK_ON)，< -0.5pp 偏空(RISK_OFF)。
         绝对净空是套保常态，不能用绝对零点判定，否则天天 RISK_OFF 停买。
      2. 情绪趋势 mood_trend：近 10 日净持仓斜率（UP/DOWN/FLAT，阈值 0.05pp/交易日）。
      3. 情绪波动 mood_vol：近 10 日净持仓标准差（高波动阈值 0.60pp）。
      4. 年底(11/12月)/季末(3/6/9月)降杠杆。
    以上共同决定 仓位上限/单票上限/止盈/止损/持有上限/回撤熔断/新买上限/RISK_OFF减仓比例。
    无数据时全部回退到写死常量，保证引擎永不崩溃。
    """
    ctx = {"regime": "NEUTRAL", "regime_detail": "无股指期货数据", "mood_trend": "FLAT",
           "mood_slope": 0.0, "mood_vol": 0.0, "regime_dev": 0.0,
           "year_end": False, "quarter_end": False,
           "cffex_days": 0, "params": {}, "rationale": []}
    means = cffex_daily_means(cffex)
    ctx["cffex_days"] = len(means)
    ctx["regime_dev"] = round(regime_deviation(means), 4)
    regime, detail = compute_regime(cffex, run_date)
    ctx["regime"] = regime
    ctx["regime_detail"] = detail

    if len(means) >= 3:
        n = min(10, len(means))
        win = means[-n:]
        vals = [m for _, m in win]
        x = list(range(len(vals)))
        mx = sum(x) / len(x); my = sum(vals) / len(vals)
        denom = sum((xi - mx) ** 2 for xi in x)
        slope = (sum((x[i] - mx) * (vals[i] - my) for i in range(len(vals))) / denom) if denom else 0.0
        vol = (sum((v - my) ** 2 for v in vals) / len(vals)) ** 0.5
        ctx["mood_slope"] = round(slope, 4)
        ctx["mood_vol"] = round(vol, 4)
        ctx["mood_trend"] = ("UP" if slope > MOOD_SLOPE_THRESHOLD
                             else ("DOWN" if slope < -MOOD_SLOPE_THRESHOLD else "FLAT"))

    m = (run_date or now_cst().date()).month
    if m in (11, 12):
        ctx["year_end"] = True
    elif m in (3, 6, 9):
        ctx["quarter_end"] = True

    p = {"exposure_cap": MAX_EXPOSURE_PCT, "single_cap": MAX_SINGLE_PCT,
         "take_profit": TAKE_PROFIT_PCT, "stop_loss": STOP_LOSS_PCT,
         "max_hold_days": MAX_HOLD_DAYS, "drawdown_halt": DRAWDOWN_HALT_RATIO,
         "max_new_buys": MAX_NEW_BUYS_PER_DAY, "risk_off_trim": 0.0}
    r = []

    if regime == "RISK_OFF":
        p.update(exposure_cap=0.50, single_cap=0.10, take_profit=6.0, stop_loss=-3.5,
                 max_hold_days=8, drawdown_halt=0.90, max_new_buys=0, risk_off_trim=0.5)
        r.append("净持仓较60日基线明显转空(RISK_OFF, %+.2fpp)：仓位降至50%%、单票≤10%%、停买、盈利持仓减仓50%%、止损收紧至-3.5%%" % ctx["regime_dev"])
    elif regime == "NEUTRAL":
        p.update(exposure_cap=0.72, single_cap=0.15, take_profit=7.0, stop_loss=-4.5, max_hold_days=10)
        r.append("市场中性(NEUTRAL)：仓位72%、单票≤15%、止盈7%/止损-4.5%")
    else:
        p.update(exposure_cap=0.90, single_cap=0.18, take_profit=9.0, stop_loss=-5.5, max_hold_days=14)
        r.append("净持仓较60日基线转多(RISK_ON, %+.2fpp)：仓位90%%、单票≤18%%、止盈9%%/止损-5.5%%" % ctx["regime_dev"])

    if ctx["mood_trend"] == "DOWN":
        p["exposure_cap"] = max(0.40, p["exposure_cap"] - 0.05)
        p["stop_loss"] = round(p["stop_loss"] - 1.0, 2)
        r.append("情绪下行：仓位再降5%、止损收紧")
    elif ctx["mood_trend"] == "UP":
        p["take_profit"] = max(p["take_profit"], 9.0)
        r.append("情绪上行：放宽止盈让利润奔跑")

    if ctx["mood_vol"] > MOOD_VOL_HIGH:
        p["exposure_cap"] = max(0.40, p["exposure_cap"] - 0.10)
        p["single_cap"] = max(0.08, round(p["single_cap"] * 0.85, 3))
        p["stop_loss"] = round(p["stop_loss"] - 1.0, 2)
        r.append("波动偏高(σ=%.2f%%)：仓位再降10%%、单票收紧、止损加严" % ctx["mood_vol"])

    if ctx["year_end"]:
        p["exposure_cap"] = max(0.40, round(p["exposure_cap"] * 0.85, 3))
        p["single_cap"] = max(0.08, round(p["single_cap"] * 0.9, 3))
        p["max_new_buys"] = min(p["max_new_buys"], 4)
        r.append("年底降杠杆(11/12月)：仓位×0.85、单票×0.9、日新买≤4")
    elif ctx["quarter_end"]:
        p["exposure_cap"] = max(0.40, round(p["exposure_cap"] * 0.95, 3))
        r.append("季末微降杠杆：仓位×0.95")

    ctx["params"] = {k: round(v, 4) for k, v in p.items()}
    ctx["rationale"] = r
    return ctx


def scenario_reference():
    """生成 4 个代表性市场场景的自适应参数，用于页面「大盘调整交易策略」说明。
    与 build_market_context 同源计算，避免写死数字日后漂移。

    旧版合成数据用百分数（1.0~3.8）却与小数口径的真实数据混用，才出现
    「场景表能算出 RISK_OFF、真实数据永远算不出」的假象。这里统一按
    真实水平（-7% 附近）+ 相对基线渐变构造，写入时还原成源 JSON 的小数口径。
    """
    def synth(vals, base="2026-09-"):
        cffex = {}
        for i, v in enumerate(vals):          # vals 单位：百分点 → /100 还原成小数
            cffex[base + "%02d" % (10 + i)] = {
                "IF": {"net_ratio": v / 100.0}, "IC": {"net_ratio": v / 100.0},
                "IH": {"net_ratio": v / 100.0}}
        return cffex
    def d(s):
        return datetime.strptime(s, "%Y-%m-%d").date()
    on_vals = [-7.4, -7.3, -7.2, -7.1, -7.0, -6.9, -6.8, -6.6, -6.3, -5.8]
    mid_vals = [-7.1, -7.0, -7.05, -6.95, -7.0, -6.98, -7.02, -6.97, -7.0, -6.99]
    off_vals = [-6.6, -6.7, -6.8, -6.9, -7.0, -7.1, -7.3, -7.5, -7.8, -8.2]
    scen = [
        ("RISK_ON 偏多（季末）", synth(on_vals), d("2026-09-19"),
         "净持仓较60日基线转多 + 情绪上行：仓位90%、放宽止盈（季末再×0.95）"),
        ("NEUTRAL 中性（季末）", synth(mid_vals), d("2026-09-19"),
         "净持仓贴合基线：基准仓位72%、止盈7%/止损-4.5%（季末再×0.95）"),
        ("RISK_OFF 净空", synth(off_vals), d("2026-09-19"),
         "净持仓转空 + 情绪下行：停买、盈利持仓减仓50%、仓位与止损再收紧"),
        ("年底 RISK_ON（11/12月）", synth(on_vals), d("2026-11-20"),
         "11/12月降杠杆：仓位×0.85、单票×0.9、日新买≤4"),
    ]
    out = []
    for name, cffex, rd, note in scen:
        ctx = build_market_context(cffex, rd)
        out.append({"name": name, "regime": ctx["regime"], "params": ctx["params"], "note": note})
    return out


# ---------------- 状态持久化 ----------------
def new_state():
    return {
        "created_at": today_str(),
        "start_capital": START_CAPITAL_TOTAL,
        "accounts": [
            {"id": a["id"], "mandate": a["mandate"], "strategies": a["strategies"],
             "cash": a["mandate"], "positions": [], "realized": 0.0}
            for a in ACCOUNTS
        ],
        "history": [{"ts": now_cst().strftime("%Y-%m-%d %H:%M"), "equity": START_CAPITAL_TOTAL}],
        "trades": [],
        "last_run_date": "",
    }


def load_state(root):
    st = load_json(root, "trader_state.json")
    if not st or "accounts" not in st:
        print("  · 无历史状态，按初始资金新建")
        return new_state()
    # 账户配置可能变更：以代码里的 ACCOUNTS 为准补齐
    by_id = {a["id"]: a for a in st.get("accounts", [])}
    merged = []
    for cfg in ACCOUNTS:
        old = by_id.get(cfg["id"])
        merged.append({
            "id": cfg["id"], "mandate": cfg["mandate"], "strategies": cfg["strategies"],
            "cash": (old or {}).get("cash", cfg["mandate"]),
            "positions": (old or {}).get("positions", []),
            "realized": (old or {}).get("realized", 0.0),
        })
    st["accounts"] = merged
    st.setdefault("history", [])
    st.setdefault("trades", [])
    st.setdefault("start_capital", START_CAPITAL_TOTAL)
    return st


# ---------------- 主流程 ----------------
def run_one_day(root, dry_run=False, force=False):
    today = now_cst().date()
    print("[daily_run] 根目录: %s   日期: %s" % (root, today))
    if not force and not is_trading_day(today):
        print("[daily_run] %s 为非交易日（周末/法定节假日），跳过本轮。如需强制运行请加 --force" % today)
        return None

    signals = {f: load_json(root, f) for f in SIGNAL_FILES}
    cands, fresh = build_candidates(signals, today)

    # 真实收盘价源（日K缓存）：用于盯市结算，覆盖不到或缓存过期则回退信号价
    kline_cache = load_json(root, "kline_cache.json")
    close_map = build_close_map(kline_cache, today)
    close_ok, close_why = close_data_fresh(kline_cache, today)
    regime, regime_detail = compute_regime(signals.get("cffex_net_position.json"), today)
    print("  候选信号: %d 条 | 氛围: %s" % (len(cands), regime))
    ctx = build_market_context(signals.get("cffex_net_position.json"), today)
    print("  自适应: regime=%s trend=%s vol=%.2f%% 年底=%s 净持仓样本=%d天"
          % (ctx["regime"], ctx["mood_trend"], ctx["mood_vol"], ctx["year_end"], ctx["cffex_days"]))
    for line in ctx["rationale"]:
        print("     · " + line)
    for k, v in fresh.items():
        print("    源 %-16s updated=%-12s 使用=%s %s" % (
            k, v["updated"], v["used"],
            ("停更%s天" % v["stale_days"]) if (v["stale_days"] or 0) > FRESH_DAYS else ""))

    # 当日价格表（用于盯市）
    price_map = {c["code"]: c["price"] for c in cands}

    st = load_state(root)
    # kline_cache 诊断（必须在 load_state 之后，st 才有值）
    if isinstance(kline_cache, dict):
        kc_stocks = kline_cache.get("stocks") or {}
        held_hit = sum(1 for a in st["accounts"] for p in a["positions"]
                       if str(p["code"]).strip() in close_map)
        print("  真实收盘价源 kline_cache.json: updated=%s 覆盖%d只 命中当前持仓%d只"
              % (kline_cache.get("updated"), len(kc_stocks), held_hit))
    # 同一天重复运行时只盯市、不再重复补仓（幂等），需要强制重跑加 --force
    already = st.get("last_run_date") == today.strftime("%Y-%m-%d")
    skip_new = (already or not close_ok) and not force
    if already and not force:
        print("  · 今天(%s)已跑过一轮 → 本轮只盯市/结算，不新建仓" % today)
    if not close_ok and not force:
        print("  ! 当日收盘数据未就绪（%s）→ 只盯市/结算，不新建仓" % close_why)

    trades_today = []
    sold_today = set()
    filtered = {}          # 建仓过滤计数，供日志与快照审计
    total_cash = total_exposure = 0.0
    snap_accounts, snap_positions = [], []

    for acc in st["accounts"]:
        held_codes = {p["code"] for p in acc["positions"]}
        # ---------- 1) 盯市 + 止盈止损 ----------
        kept = []
        for p in acc["positions"]:
            # 盯市优先用 kline_cache 真实收盘价；覆盖不到或缓存过期(>CLOSE_STALE_DAYS)回退信号价
            cm = close_map.get(p["code"])
            if cm and cm[1] is not None and (today - cm[1]).days <= CLOSE_STALE_DAYS:
                last = cm[0]
            else:
                last = price_map.get(p["code"], p.get("last"))
            if last is None:
                last = p.get("avg")
            p["last"] = last
            avg = float(p.get("avg") or 0)
            pnl_pct = ((last - avg) / avg * 100) if avg else 0.0
            opened = parse_date(p.get("open_date")) or today
            # 持有期按交易日计（旧版用自然日，12 天实际只有约 8 个交易日）
            hold_days = trading_days_between(opened, today)
            reason = None
            if pnl_pct >= ctx["params"]["take_profit"]:
                reason = "止盈 +%.1f%%" % pnl_pct
            elif pnl_pct <= ctx["params"]["stop_loss"]:
                reason = "止损 %.1f%%" % pnl_pct
            elif hold_days >= ctx["params"]["max_hold_days"] and pnl_pct > 0:
                reason = "持有%d个交易日轮出" % hold_days
            if reason:
                px = round(last * SELL_SLIPPAGE, 3)
                gross = px * p["qty"]
                fee = sell_fee(gross)
                acc["cash"] += gross - fee
                realized = gross - fee - float(p.get("avg")) * p["qty"]
                acc["realized"] += realized
                sold_today.add(p["code"])          # 当天卖出的票不再买回
                trades_today.append({
                    "date": today.strftime("%Y-%m-%d"),
                    "ts": now_cst().strftime("%Y-%m-%d %H:%M"),
                    "code": p["code"], "name": p["name"], "side": "SELL",
                    "qty": p["qty"], "price": px, "reason": reason, "account": acc["id"],
                })
            else:
                kept.append(p)
        acc["positions"] = kept

        # ---------- 1.5) RISK_OFF 盈利持仓减仓 ----------
        if not skip_new and ctx["params"]["risk_off_trim"] > 0:
            trim_factor = ctx["params"]["risk_off_trim"]
            after_trim = []
            for p in acc["positions"]:
                last = p.get("last") or p.get("avg") or 0
                avg = float(p.get("avg") or 0)
                pnl_pct = ((last - avg) / avg * 100) if avg else 0.0
                if pnl_pct > 0 and p["qty"] > 100:
                    sell_qty = int(p["qty"] * trim_factor / 100) * 100
                    if sell_qty >= 100:
                        px = round(last * SELL_SLIPPAGE, 3)
                        gross = px * sell_qty
                        fee = sell_fee(gross)
                        acc["cash"] += gross - fee
                        acc["realized"] += (gross - fee) - avg * sell_qty
                        p["qty"] -= sell_qty
                        trades_today.append({
                            "date": today.strftime("%Y-%m-%d"),
                            "ts": now_cst().strftime("%Y-%m-%d %H:%M"),
                            "code": p["code"], "name": p["name"], "side": "SELL",
                            "qty": sell_qty, "price": px,
                            "reason": "RISK_OFF 减仓 %.0f%%" % (trim_factor * 100),
                            "account": acc["id"],
                        })
                if p["qty"] >= 100:
                    after_trim.append(p)
            acc["positions"] = after_trim

        exposure = sum(p["qty"] * p["last"] for p in acc["positions"])
        equity = acc["cash"] + exposure
        pnl = equity - acc["mandate"]
        halted = equity < acc["mandate"] * ctx["params"]["drawdown_halt"]

        # ---------- 2) 按信号建仓 ----------
        bought = 0
        if not halted and not skip_new and regime != "RISK_OFF":
            # 跨账户同票去重：旧版每个账户各自判重，同一只票可在 A/B 两个账户各买满
            # 单票上限，实际敞口翻倍。这里按全账本持仓并集判重。
            held_anywhere = {p["code"] for a in st["accounts"] for p in a["positions"]}
            for c in cands:
                if bought >= ctx["params"]["max_new_buys"]:
                    break
                if c["code"] in sold_today:
                    continue
                if c["code"] in held_anywhere:
                    continue
                ok, why = tradable(c, close_map, today)
                if not ok:
                    filtered[why] = filtered.get(why, 0) + 1
                    continue
                if c["strategy"] not in acc["strategies"]:
                    continue
                # 该策略已部署市值
                deployed = sum(p["qty"] * p["last"] for p in acc["positions"]
                               if p.get("strategy") == c["strategy"])
                cap = acc["mandate"] * acc["strategies"][c["strategy"]]
                room_strategy = cap - deployed
                room_single = equity * ctx["params"]["single_cap"]
                room_exposure = equity * ctx["params"]["exposure_cap"] - exposure
                avail = min(room_strategy, room_single, room_exposure, acc["cash"] * 0.95)
                px = round(c["price"] * BUY_SLIPPAGE, 3)
                qty = int(avail / (px * 100)) * 100
                if qty < 100:
                    continue
                gross = px * qty
                fee = buy_fee(gross)
                if gross + fee > acc["cash"]:
                    continue
                acc["cash"] -= gross + fee
                acc["positions"].append({
                    "code": c["code"], "name": c["name"], "qty": qty,
                    "avg": round((gross + fee) / qty, 3), "last": c["price"],
                    "strategy": c["strategy"], "open_date": today.strftime("%Y-%m-%d"),
                })
                trades_today.append({
                    "date": today.strftime("%Y-%m-%d"),
                    "ts": now_cst().strftime("%Y-%m-%d %H:%M"),
                    "code": c["code"], "name": c["name"], "side": "BUY",
                    "qty": qty, "price": px, "reason": "%s 信号买入" % c["strategy"],
                    "account": acc["id"],
                })
                exposure = sum(p["qty"] * p["last"] for p in acc["positions"])
                held_anywhere.add(c["code"])
                bought += 1

        exposure = sum(p["qty"] * p["last"] for p in acc["positions"])
        equity = acc["cash"] + exposure
        pnl = equity - acc["mandate"]
        pnl_pct = (pnl / acc["mandate"] * 100) if acc["mandate"] else 0.0
        total_cash += acc["cash"]
        total_exposure += exposure

        snap_accounts.append({
            "id": acc["id"], "mandate": acc["mandate"], "equity": round(equity, 2),
            "cash": round(acc["cash"], 2), "exposure": round(exposure, 2),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
            # 旧版「盈利追加 / 亏损缩减」只是文案，mandate 从不调整，会误导读者以为
            # 系统在动态加减委托规模。改为如实标注盈亏状态（页面同步改口）。
            "status": "回撤暂停" if equity < acc["mandate"] * ctx["params"]["drawdown_halt"] else (
                "盈利运行" if pnl > 0 else ("亏损运行" if pnl < 0 else "持平")),
            "strategies": ",".join(acc["strategies"].keys()),
        })
        for p in acc["positions"]:
            avg = float(p.get("avg") or 0)
            last = float(p.get("last") or avg)
            ppnl = (last - avg) * p["qty"]
            snap_positions.append({
                "code": p["code"], "name": p["name"], "qty": p["qty"],
                "avg": round(avg, 3), "last": round(last, 3),
                "value": round(last * p["qty"], 2),
                "pnl": round(ppnl, 2),
                "pnl_pct": round(((last - avg) / avg * 100) if avg else 0.0, 2),
                "strategy": p.get("strategy", ""), "account": acc["id"],
            })

    st["trades"] = (st["trades"] + trades_today)[-MAX_TRADES_KEEP:]
    st["last_run_date"] = today.strftime("%Y-%m-%d")

    total_equity = total_cash + total_exposure
    start_capital = float(st.get("start_capital") or START_CAPITAL_TOTAL)
    ts = now_cst().strftime("%Y-%m-%d %H:%M")
    # 同一天重复运行则覆盖当天最后一个点，避免曲线出现重复日期
    if st["history"] and str(st["history"][-1].get("ts", "")).startswith(today.strftime("%Y-%m-%d")):
        st["history"][-1] = {"ts": ts, "equity": round(total_equity, 2)}
    else:
        st["history"].append({"ts": ts, "equity": round(total_equity, 2)})
    st["history"] = st["history"][-400:]

    # 分策略资金分配（全局）
    allocations = {}
    for cfg in ACCOUNTS:
        acc = next((a for a in st["accounts"] if a["id"] == cfg["id"]), None)
        if not acc:
            continue
        for strat, pct in cfg["strategies"].items():
            slot = allocations.setdefault(
                strat, {"alloc_total": round(sum(a["mandate"] * (a["strategies"].get(strat) or 0)
                                                 for a in st["accounts"]), 2),
                        "assigned": 0.0, "deployed": 0.0})
            slot["assigned"] += acc["mandate"] * pct
            slot["deployed"] += sum(p["qty"] * p["last"] for p in acc["positions"]
                                    if p.get("strategy") == strat)
    for k in allocations:
        allocations[k]["assigned"] = round(allocations[k]["assigned"], 2)
        allocations[k]["deployed"] = round(allocations[k]["deployed"], 2)

    snapshot = {
        "mode": "cloud-daily", "live": True,
        "start_capital": start_capital, "equity": round(total_equity, 2),
        "cash": round(total_cash, 2), "exposure": round(total_exposure, 2),
        "ret_pct": round((total_equity - start_capital) / start_capital * 100, 2),
        "regime": regime, "regime_detail": regime_detail,
        "adaptive": {**ctx, "scenarios": scenario_reference()},
        "updated": now_cst().strftime("%Y-%m-%d %H:%M"),
        "run_date": today.strftime("%Y-%m-%d"),
        "source_freshness": fresh,
        "data_quality": {"close_fresh": close_ok, "close_note": close_why,
                         "close_updated": str((kline_cache or {}).get("updated") or ""),
                         "trading_allowed": not skip_new,
                         "filtered_candidates": filtered},
        "accounts": snap_accounts,
        "positions": snap_positions,
        "trades": st["trades"][-200:],
        "history": st["history"],
        "allocations": allocations,
    }

    print("  数据闸: 当日收盘数据%s | 本轮%s新建仓%s"
          % ("就绪" if close_ok else "未就绪（%s）" % close_why,
             "允许" if not skip_new else "禁止",
             "（--force 显式绕过）" if (force and not close_ok) else ""))
    if filtered:
        print("  建仓过滤: %s" % "，".join("%s %d" % (k, v) for k, v in sorted(filtered.items())))
    print("  成交 %d 笔 | 权益 %.2f (%+.2f%%) | 现金 %.2f | 持仓市值 %.2f"
          % (len(trades_today), total_equity, snapshot["ret_pct"], total_cash, total_exposure))
    for a in snap_accounts:
        print("    %-14s 委托%.0f → 权益%.2f (%+.2f%%) %s"
              % (a["id"], a["mandate"], a["equity"], a["pnl_pct"], a["status"]))

    if dry_run:
        print("  [dry-run] 未写文件")
        return snapshot

    def dump(name, obj):
        with open(os.path.join(root, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    dump("trader_snapshot.json", snapshot)
    dump("trader_state.json", st)
    print("  已写入 trader_snapshot.json / trader_state.json")
    # 同步 trader.html 内嵌演示块 = 真实账本回放（登录前/登录后数据一致）
    # 故意不吞错：演示块锚点漂移会让 demo 态权益与 live 态永久偏离，必须 fail-fast
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sync_demo_data import sync_trader_html, verify_demo_equity
    sync_trader_html(root, st, snapshot)
    # 三态一致性断言：trader.html 末日权益必须与实时快照权益一致，否则非零退出
    verify_demo_equity(root, snapshot["equity"])
    print("  · 三态一致性校验通过 (demo 末日权益 == 快照权益 %.2f)" % snapshot["equity"])
    return snapshot


def main():
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    root = None
    if "--root" in sys.argv:
        i = sys.argv.index("--root")
        if i + 1 < len(sys.argv):
            root = sys.argv[i + 1]
    root = find_root(root)
    if not any(os.path.exists(os.path.join(root, f)) for f in SIGNAL_FILES):
        print("[daily_run] 未在 %s 找到任何信号 JSON，退出。" % root, file=sys.stderr)
        sys.exit(1)
    run_one_day(root, dry_run=dry, force=force)


if __name__ == "__main__":
    main()
