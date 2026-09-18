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
MAX_HOLD_DAYS = 12           # 持有超期且盈利则轮出
MAX_SINGLE_PCT = 0.18        # 单票市值不超过账户权益的比例
MAX_EXPOSURE_PCT = 0.90      # 总仓位上限
DRAWDOWN_HALT_RATIO = 0.85   # 权益跌破委托金该比例 → 暂停新建仓（回撤保护）
BUY_SLIPPAGE = 1.002         # 买入滑点
SELL_SLIPPAGE = 0.998        # 卖出滑点
COMMISSION = 0.00025         # 佣金（单边）
STAMP_TAX = 0.0005           # 印花税（仅卖出）
FRESH_DAYS = 3               # 信号源新鲜度容忍天数，超过则视为停更并跳过
CLOSE_STALE_DAYS = 5          # 真实收盘价缓存(kline_cache)最大可容忍滞后天数，超过则回退信号价
MAX_TRADES_KEEP = 300
MAX_NEW_BUYS_PER_DAY = 6

SIGNAL_FILES = ["strong_stocks.json", "strongbuy_data.json",
                "daily_picks.json", "cffex_net_position.json"]


# ---------------- 工具函数 ----------------
def now_cst():
    return datetime.now(CST)


def today_str():
    return now_cst().strftime("%Y-%m-%d")


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

    cands, seen = [], {}

    def add(code, name, price, chg, score, strategy):
        if not is_valid_code(code) or price is None or price <= 0:
            return
        code = str(code).strip()
        key = code
        if key in seen:                      # 同票多源，保留评分最高的
            if score <= seen[key]["score"]:
                return
        seen[key] = {"code": code, "name": str(name or code).strip(),
                     "price": round(price, 3), "chg": chg or 0.0,
                     "score": score, "strategy": strategy}

    if ok_strong:
        for r in (strong.get("stocks") or []):
            add(r.get("code"), r.get("name"), num(r.get("price")), num(r.get("change_pct")),
                (num(r.get("score")) or 0) * 10 + (num(r.get("change_pct")) or 0), "strong")
        for r in (strong.get("yimeng") or []):
            add(r.get("code"), r.get("name"), num(r.get("price")), num(r.get("change_pct")),
                (num(r.get("score")) or 0) * 10 + (num(r.get("change_pct")) or 0), "strong")

    if ok_sb:
        for key in ("yimeng", "stocks"):
            for r in (strongbuy.get(key) or []):
                add(r.get("code"), r.get("name"), num(r.get("price")), num(r.get("change_pct")),
                    (num(r.get("net_inflow_pct")) or 0) * 0.5 + (num(r.get("change_pct")) or 0),
                    "strongbuy")

    if dk:
        leaves = []
        collect_leaf_stocks(daily.get(dk), leaves)
        scored = []
        for r in leaves:
            sc = num(r.get("final_score")) or num(r.get("heat_score")) or num(r.get("base_score")) or 0
            scored.append((sc, r))
        scored.sort(key=lambda x: -x[0])
        for sc, r in scored[:25]:
            add(r.get("code"), r.get("name"), num(r.get("price")),
                num(r.get("chg_pct")) or num(r.get("change_pct")), sc, "daily_picks")

    cands = sorted(seen.values(), key=lambda x: -x["score"])
    return cands, fresh


def cffex_daily_means(cffex):
    """返回按日期排序的 [(date, 当日净持仓均值%)]，用于趋势/波动分析。"""
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
            out.append((dt, sum(ratios) / len(ratios)))
    out.sort(key=lambda x: x[0])
    return out


def compute_regime(cffex, today):
    """用股指期货净多空判定市场氛围。"""
    means = cffex_daily_means(cffex)
    if not means:
        return "NEUTRAL", "无股指期货数据"
    d, avg = means[-1]
    parts = []
    latest = cffex.get(d.strftime("%Y-%m-%d")) or {}
    for k, v in latest.items():
        if isinstance(v, dict) and num(v.get("net_ratio")) is not None:
            parts.append("%s %.1f%%" % (k, num(v.get("net_ratio"))))
    detail = "%s 净持仓均值 %.2f%%（%s）" % (d, avg, "，".join(parts))
    if avg > 1.0:
        return "RISK_ON", detail
    if avg < -1.0:
        return "RISK_OFF", detail
    return "NEUTRAL", detail


def build_market_context(cffex, run_date):
    """市场环境自适应层：读股指期货净多空时序 + 运行日历，
    输出当日自适应交易参数（仍跑模拟盘，不改变账户连续性）。

    逻辑：
      1. regime：期货净持仓均值 > +1% 偏多(RISK_ON)，< -1% 偏空(RISK_OFF)，否则中性。
      2. 情绪趋势 mood_trend：近 10 日净持仓斜率（UP/DOWN/FLAT）。
      3. 情绪波动 mood_vol：近 10 日净持仓标准差。
      4. 年底(11/12月)/季末(3/6/9月)降杠杆。
    以上共同决定 仓位上限/单票上限/止盈/止损/持有上限/回撤熔断/新买上限/RISK_OFF减仓比例。
    无数据时全部回退到写死常量，保证引擎永不崩溃。
    """
    ctx = {"regime": "NEUTRAL", "regime_detail": "无股指期货数据", "mood_trend": "FLAT",
           "mood_slope": 0.0, "mood_vol": 0.0, "year_end": False, "quarter_end": False,
           "cffex_days": 0, "params": {}, "rationale": []}
    means = cffex_daily_means(cffex)
    ctx["cffex_days"] = len(means)
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
        ctx["mood_trend"] = "UP" if slope > 0.05 else ("DOWN" if slope < -0.05 else "FLAT")

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
        r.append("期货净空偏重(RISK_OFF)：仓位降至50%、单票≤10%、停买、盈利持仓减仓50%、止损收紧至-3.5%")
    elif regime == "NEUTRAL":
        p.update(exposure_cap=0.72, single_cap=0.15, take_profit=7.0, stop_loss=-4.5, max_hold_days=10)
        r.append("市场中性(NEUTRAL)：仓位72%、单票≤15%、止盈7%/止损-4.5%")
    else:
        p.update(exposure_cap=0.90, single_cap=0.18, take_profit=9.0, stop_loss=-5.5, max_hold_days=14)
        r.append("风险偏好(RISK_ON)：仓位90%、单票≤18%、止盈9%/止损-5.5%")

    if ctx["mood_trend"] == "DOWN":
        p["exposure_cap"] = max(0.40, p["exposure_cap"] - 0.05)
        p["stop_loss"] = round(p["stop_loss"] - 1.0, 2)
        r.append("情绪下行：仓位再降5%、止损收紧")
    elif ctx["mood_trend"] == "UP":
        p["take_profit"] = max(p["take_profit"], 9.0)
        r.append("情绪上行：放宽止盈让利润奔跑")

    if ctx["mood_vol"] > 1.2:
        p["exposure_cap"] = max(0.40, p["exposure_cap"] - 0.10)
        p["single_cap"] = max(0.08, round(p["single_cap"] * 0.85, 3))
        p["stop_loss"] = round(p["stop_loss"] - 1.0, 2)
        r.append("波动偏高(σ=%.2f%%)：仓位再降10%、单票收紧、止损加严" % ctx["mood_vol"])

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
    与 build_market_context 同源计算，避免写死数字日后漂移。"""
    def synth(vals, base="2026-09-"):
        cffex = {}
        for i, v in enumerate(vals):
            cffex[base + "%02d" % (10 + i)] = {"IF": {"net_ratio": v}, "IC": {"net_ratio": v}, "IH": {"net_ratio": v}}
        return cffex
    def d(s):
        return datetime.strptime(s, "%Y-%m-%d").date()
    scen = [
        ("RISK_ON 偏多（季末）", synth([1.0,1.5,2.0,2.2,2.5,2.8,3.0,3.2,3.5,3.8]), d("2026-09-19"),
         "让利润奔跑：放宽止盈、仓位90%（季末再×0.95）"),
        ("NEUTRAL 中性（季末）", synth([0.2,0.4,0.0,-0.3,0.1,0.3,0.0,-0.2,0.1,0.2]), d("2026-09-19"),
         "基准仓位72%、中性止盈7%/止损-4.5%（季末再×0.95）"),
        ("RISK_OFF 净空", synth([-0.5,-1.0,-1.5,-2.0,-2.5,-3.0,-3.2,-3.5,-3.8,-4.0]), d("2026-09-19"),
         "停买 + 盈利持仓减仓50%、止损收紧至-3.5%"),
        ("年底 RISK_ON（11/12月）", synth([1.0,1.5,2.0,2.2,2.5,2.8,3.0,3.2,3.5,3.8]), d("2026-11-20"),
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

    signals = {f: load_json(root, f) for f in SIGNAL_FILES}
    cands, fresh = build_candidates(signals, today)

    # 真实收盘价源（日K缓存）：用于盯市结算，覆盖不到或缓存过期则回退信号价
    kline_cache = load_json(root, "kline_cache.json")
    close_map = build_close_map(kline_cache, today)
    if isinstance(kline_cache, dict):
        kc_stocks = kline_cache.get("stocks") or {}
        held_hit = sum(1 for a in st["accounts"] for p in a["positions"]
                       if str(p["code"]).strip() in close_map)
        print("  真实收盘价源 kline_cache.json: updated=%s 覆盖%d只 命中当前持仓%d只"
              % (kline_cache.get("updated"), len(kc_stocks), held_hit))
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
    # 同一天重复运行时只盯市、不再重复补仓（幂等），需要强制重跑加 --force
    already = st.get("last_run_date") == today.strftime("%Y-%m-%d")
    skip_new = already and not force
    if skip_new:
        print("  · 今天(%s)已跑过一轮 → 本轮只盯市/结算，不新建仓" % today)

    trades_today = []
    sold_today = set()
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
            hold_days = (today - opened).days
            reason = None
            if pnl_pct >= ctx["params"]["take_profit"]:
                reason = "止盈 +%.1f%%" % pnl_pct
            elif pnl_pct <= ctx["params"]["stop_loss"]:
                reason = "止损 %.1f%%" % pnl_pct
            elif hold_days >= ctx["params"]["max_hold_days"] and pnl_pct > 0:
                reason = "持有%d天轮出" % hold_days
            if reason:
                px = round(last * SELL_SLIPPAGE, 3)
                gross = px * p["qty"]
                fee = gross * COMMISSION + gross * STAMP_TAX
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
                        fee = gross * COMMISSION + gross * STAMP_TAX
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
            for c in cands:
                if bought >= ctx["params"]["max_new_buys"]:
                    break
                if c["code"] in sold_today:
                    continue
                if c["code"] in {p["code"] for p in acc["positions"]}:
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
                fee = gross * COMMISSION
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
            "status": "回撤暂停" if equity < acc["mandate"] * ctx["params"]["drawdown_halt"] else (
                "盈利追加" if pnl > 0 else ("亏损缩减" if pnl < 0 else "持平")),
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
        "accounts": snap_accounts,
        "positions": snap_positions,
        "trades": st["trades"][-200:],
        "history": st["history"],
        "allocations": allocations,
    }

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
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from sync_demo_data import sync_trader_html
        sync_trader_html(root, st, snapshot)
    except Exception as e:
        print("  ! 同步 trader.html 演示块失败(不影响快照): %s" % e, file=sys.stderr)
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
