#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深度破发错杀观察板块 · 数据生成器
输出仓库根目录 ipo_break.json，供 week.html 第 7 个 tab 消费。

口径（2026-11-06 定稿）：
  1. 基础池：近 3 年上市的沪深 A 股（60/68/00/30），剔北交所/B股/ST/退市/风险警示。
  2. 破发判定：含权息口径 = 后复权最新收盘价 / IPO 发行价 - 1。
     两家后复权（东财 push2his fqt=2、腾讯 hfqday）均以上市首日为基准，首日复权因子=1，
     故发行价自身无需复权。名义破发% 另列展示，不作为入池判据。
     数学上 后复权价 >= 未复权现价，故「真实破发」必然「名义破发」，只需对名义破发池复权。
  3. L2：最近两个完整会计年度，归母净利与扣非净利孰低者连续为负 -> 一票否决。
  4. L3：36 个月内财务造假类行政处罚 / 12 个月内立案未结案 / 最近年度否定或无法表示意见
     的审计意见 -> 一票否决；警示函、问询函、会计差错等仅标记观察，不剔。
     公告必须按「公告日期 >= 上市日」过滤，避免 IPO 审核问询函误杀。
  5. 深度档位：含权息 <= -10% 为「深度」、<= -30% 为「极端」，两列口径均输出，
     同时提供深度筛选，入池门槛不收严（仍为 < 0）。
  6. v2 增强字段：破发持续期（含权息/名义双口径）、行业新股破发率（系统性杀跌代理）、
     当前 PE 与发行 PE 压缩比、未来 3 个月解禁占比。均为展示与筛选维度，不改变入池规则。
  7. v3 微观与择时字段（D+E，全部为展示/筛选维度，绝不改变入池规则）：
     K 线扩 f56/f57/f61 取成交额、成交量、换手率 -> 20 日均额、破发段换手、距最高点回撤、
     量比、MACD 底背离、温和放量；腾讯批量报价取流通市值 -> 散点图气泡；
     解禁按「距上市月数」分首发一年期/三年期/小额配售；TTM 转亏预警。
     资金流双源：RPT_DMSK_TS_STOCKNEW 单日快照保底 + push2his fflow 逐股近 5 日净额，
     逐股失败自动降级到单日数据，失败不影响入池、不触发 fail-safe。

仅依赖 Python 标准库。数据源走 datacenter-web / push2his 分片域名池 / ifzq.gtimg.cn /
qt.gtimg.cn；push2his 主域名会整体被掐断（HTTP 000），K 线一律走分片域名池轮换。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(ROOT, "ipo_break.json")
BLACKLIST_PATH = os.path.join(ROOT, "ipo_break_blacklist.json")

CN_TZ = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(CN_TZ).date()
WINDOW_YEARS = 3
DEEP_THRESHOLD = -0.10
EXTREME_THRESHOLD = -0.30
RULES_VERSION = "v3"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
H_EM = {"User-Agent": UA, "Referer": "https://data.eastmoney.com/"}
H_HIS = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
H_TQ = {"User-Agent": UA, "Referer": "https://gu.qq.com/"}

DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# 主域名 push2his.eastmoney.com 会整体被掐断（HTTP 000），分片域名池实测 7/7 可用。
HIS_HOSTS = ["1.push2his", "7.push2his", "23.push2his", "33.push2his",
             "48.push2his", "63.push2his", "99.push2his"]
HIS_PATH = "/api/qt/stock/kline/get"
FLOW_PATH = "/api/qt/stock/fflow/daykline/get"
TQ_HFQ_HOSTS = ["ifzq.gtimg.cn", "web.ifzq.gtimg.cn"]
TQ_HFQ_PATH = "/appstock/app/fqkline/get"
QT_URL = "https://qt.gtimg.cn/q="
ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"

UNIVERSE_REPORT = "RPTA_APP_IPOAPPLY"
FIN_REPORT = "RPT_LICO_FN_CPD"
FORECAST_REPORT = "RPT_PUBLIC_OP_NEWPREDICT"
VALUATION_REPORT = "RPT_VALUE_ANALYSIS"
LIFT_REPORT = "RPT_LIFT_STOCK"
FLOW_SNAPSHOT_REPORT = "RPT_DMSK_TS_STOCKNEW"

LOW_LIQUIDITY_YI = 0.5          # 20 日均成交额门槛，仅作可切换筛选用
ZOMBIE_TURNOVER = 1.5          # 破发段日均换手率低于此值标「僵尸股」
LIFT_NEAR_DAYS = 30            # 解禁 30 天内在散点图描警示色

RE_PUNISH = re.compile(r"行政处罚决定书|行政处罚|虚假记载|财务造假|虚增|欺诈发行|重大违法")
RE_INVEST = re.compile(r"立案告知书|立案调查|被立案|涉嫌信息披露违法")
RE_CLOSED = re.compile(r"结案|不予立案|撤销立案|终止调查|调查终结")
RE_AUDIT = re.compile(r"否定意见|无法表示意见")
RE_OBSERVE = re.compile(
    r"警示函|责令改正|纪律处分|通报批评|问询函|关注函|会计差错|前期差错|"
    r"保留意见|非标准审计意见|业绩预告更正|监管措施"
)
RE_ST = re.compile(r"ST|退|PT", re.I)


# ----------------------------------------------------------------------------
# 通用请求
# ----------------------------------------------------------------------------
def _get_json(url: str, headers: dict, tries: int = 3, timeout: int = 20,
              encoding: str = "utf-8-sig"):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode(encoding, errors="ignore"))
        except Exception as exc:                       # noqa: BLE001
            last = exc
            if i < tries - 1:
                time.sleep(0.6 * (i + 1) + random.uniform(0, 0.4))
    raise last


def his_get(query: str, headers: dict, tries: int = 7, path: str | None = None):
    """分片域名池：域名会被逐个掐断，需试完全部候选再判失败。
    path 可切换到 fflow/daykline 等同一域名池下的其它接口。"""
    hosts = random.sample(HIS_HOSTS, len(HIS_HOSTS))
    p = path or HIS_PATH
    last = None
    for i in range(min(tries, len(hosts))):
        url = "https://" + hosts[i] + ".eastmoney.com" + p + query
        try:
            return _get_json(url, headers, tries=1)
        except Exception as exc:                       # noqa: BLE001
            last = exc
            time.sleep(0.12 + random.uniform(0, 0.15))
    raise last


def dc_get(report: str, columns: str, filt: str | None = None,
           size: int = 500, page: int = 1, sort: str | None = None,
           sort_type: str = "-1"):
    params = {
        "reportName": report, "columns": columns,
        "pageSize": str(size), "pageNumber": str(page),
        "source": "WEB", "client": "WEB",
    }
    if filt:
        params["filter"] = filt
    if sort:
        params["sortColumns"] = sort
        params["sortTypes"] = sort_type
    return _get_json(DC_URL + "?" + urllib.parse.urlencode(params), H_EM)


def log(msg: str):
    print("[ipo_break] " + msg, flush=True)


# ----------------------------------------------------------------------------
# L1 基础池
# ----------------------------------------------------------------------------
def board_of(code: str) -> str:
    if code.startswith("68"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith("60"):
        return "沪主板"
    if code.startswith("00"):
        return "深主板"
    return "其他"


def is_st(name: str) -> bool:
    return bool(RE_ST.search((name or "").replace(" ", "")))


def fetch_universe():
    start = (TODAY - dt.timedelta(days=365 * WINDOW_YEARS)).isoformat()
    filt = "(LISTING_DATE>='%s')(IS_BEIJING=0)" % start
    columns = ("SECURITY_CODE,SECURITY_NAME_ABBR,ISSUE_PRICE,LISTING_DATE,"
               "LATELY_PRICE,AFTER_ISSUE_PE,INDUSTRY_NAME,IS_RISKWARNING,MARKET_TYPE_NEW")
    raw, page = [], 1
    while True:
        j = dc_get(UNIVERSE_REPORT, columns, filt, 500, page, "LISTING_DATE", "-1")
        result = j.get("result") or {}
        raw.extend(result.get("data") or [])
        if page >= (result.get("pages") or 1) or page >= 10:
            break
        page += 1
    log("东财新股原始记录 %d 条（近 %d 年，已排除北交所）" % (len(raw), WINDOW_YEARS))

    universe, l1_out = [], []
    for r in raw:
        code = str(r.get("SECURITY_CODE") or "")
        name = str(r.get("SECURITY_NAME_ABBR") or "")
        listing = str(r.get("LISTING_DATE") or "")[:10]
        issue = r.get("ISSUE_PRICE")
        price = r.get("LATELY_PRICE")
        if not code.startswith(("60", "68", "00", "30")):
            continue
        if not listing or listing > TODAY.isoformat():
            continue
        if not isinstance(issue, (int, float)) or issue <= 0:
            continue
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        reason = None
        if is_st(name):
            reason = "L1 ST/*ST/退市"
        elif r.get("IS_RISKWARNING") in (1, "1", True):
            reason = "L1 风险警示"
        if reason:
            l1_out.append({"code": code, "name": name, "layer": "L1", "reason": reason})
            continue
        universe.append({
            "code": code, "name": name, "listing_date": listing,
            "issue_price": float(issue), "price": float(price),
            "issue_pe": r.get("AFTER_ISSUE_PE"),
            "industry": r.get("INDUSTRY_NAME") or "",
            "market_type": r.get("MARKET_TYPE_NEW") or "",
            "board": board_of(code),
            "nominal_pct": float(price) / float(issue) - 1,
        })
    return universe, l1_out, start


# ----------------------------------------------------------------------------
# 后复权：东财主源 + 腾讯备源
# ----------------------------------------------------------------------------
def hfq_em(row: dict):
    code = row["code"]
    secid = ("1." if code[0] in "69" else "0.") + code
    # f51 日期 / f53 收盘 / f56 成交量(手) / f57 成交额(元) / f61 换手率(%)
    # 量与额不受复权影响，可与后复权收盘序列共用同一次请求。
    query = ("?secid=" + secid +
             "&fields1=f1,f2&fields2=f51,f53,f56,f57,f61&klt=101&fqt=2" +
             "&beg=" + row["listing_date"].replace("-", "") +
             "&end=" + TODAY.strftime("%Y%m%d") + "&lmt=1000")
    j = his_get(query, H_HIS)
    klines = (j.get("data") or {}).get("klines") or []
    if len(klines) < 2:
        return None

    def col(idx):
        out = []
        for k in klines:
            p = k.split(",")
            try:
                out.append(float(p[idx]))
            except (IndexError, ValueError):
                out.append(0.0)
        return out

    dates = [k.split(",")[0] for k in klines]
    closes, vols, amounts, turns = col(1), col(2), col(3), col(4)
    # 盘中东财会生成当日未完成 K 线；收盘前丢弃它，避免用未收盘价做入池判定。
    intraday = (dt.datetime.now(CN_TZ).strftime("%H%M") < "1505"
                and dates[-1] == TODAY.isoformat())
    if intraday and len(closes) > 2:
        dates = dates[:-1]
        closes, vols, amounts, turns = (arr[:-1] for arr in
                                        (closes, vols, amounts, turns))
    # 名义（不复权）序列用于「盘面破发持续期」对照；失败不影响主口径。
    nominal_closes = None
    try:
        j0 = his_get(query.replace("fqt=2", "fqt=0"), H_HIS, tries=2)
        k0 = (j0.get("data") or {}).get("klines") or []
        if len(k0) >= 2:
            nominal_closes = [float(k.split(",")[1]) for k in k0]
            if intraday and len(nominal_closes) > 2:
                nominal_closes = nominal_closes[:-1]
    except Exception:                                  # noqa: BLE001
        pass
    return {
        "hfq_close": closes[-1],
        "as_of": dates[-1],
        "first_close": closes[0],
        "closes": closes,
        "vols": vols, "amounts": amounts, "turnovers": turns,
        "nominal_closes": nominal_closes,
    }


def hfq_tencent(row: dict):
    """返回 (dates, closes) 完整后复权序列；双域名轮换。
    东财分片被逐个封断时，这份序列可完整接管入池判定与破发持续期计算。"""
    code = row["code"]
    sym = ("sh" if code[0] in "69" else "sz") + code
    query = "?param=" + ",".join(
        [sym, "day", row["listing_date"], TODAY.isoformat(), "1000", "hfq"])
    for host in random.sample(TQ_HFQ_HOSTS, len(TQ_HFQ_HOSTS)):
        try:
            j = _get_json("https://" + host + TQ_HFQ_PATH + query,
                          H_TQ, tries=1, encoding="utf-8")
            arr = ((j.get("data") or {}).get(sym) or {}).get("hfqday") or []
            if len(arr) >= 2:
                dates = [str(a[0]) for a in arr]
                closes = [float(a[2]) for a in arr]
                if (dt.datetime.now(CN_TZ).strftime("%H%M") < "1505"
                        and dates[-1].replace("-", "") == TODAY.strftime("%Y%m%d")
                        and len(closes) > 2):
                    dates, closes = dates[:-1], closes[:-1]
                return dates, closes
        except Exception:                              # noqa: BLE001
            continue
    return None


def duration_stats(closes, issue_price):
    """上市以来逐日「收盘价 < 发行价」统计；closes 为后复权或名义收盘序列。"""
    if not closes or len(closes) < 2 or not issue_price:
        return None
    flags = [c < issue_price for c in closes]
    cur = 0
    for f in reversed(flags):
        if f:
            cur += 1
        else:
            break
    best = run = 0
    first = None
    for i, f in enumerate(flags):
        if f:
            run += 1
            best = max(best, run)
            if first is None:
                first = i + 1
        else:
            run = 0
    broken = sum(flags)
    return {
        "bars": len(flags),
        "break_days": broken,
        "break_ratio": round(broken / len(flags), 4),
        "cur_streak": cur,
        "max_streak": best,
        "first_break_at": first,
        "first_day_break": bool(flags[0]),
    }


def _ema(arr, n):
    k = 2 / (n + 1)
    out = [arr[0]]
    for v in arr[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def micro_stats(em: dict, issue_price: float):
    """D 批微观维度：全部来自 hfq_em 那一次 K 线请求，零新增网络调用。
    收盘序列为后复权；成交量/额/换手率为原始值，不受复权影响。"""
    cl = em["closes"]
    vols, amounts, turns = em["vols"], em["amounts"], em["turnovers"]
    if len(cl) < 2 or not issue_price:
        return None
    n = len(cl)
    a20 = amounts[-20:]
    t20 = turns[-20:]
    v20, v5 = vols[-20:], vols[-5:]
    amt20 = sum(a20) / len(a20) / 1e8 if a20 else 0.0
    turn20 = sum(t20) / len(t20) if t20 else 0.0
    # 当前连续破发段：用于区分「无人问津的僵尸股」与「多空分歧巨大的活跃股」
    s = n - 1
    while s > 0 and cl[s - 1] < issue_price:
        s -= 1
    seg_turn = turns[s:]
    seg_amt = amounts[s:]
    seg_turnover = sum(seg_turn) / len(seg_turn) if seg_turn else 0.0
    seg_amt_yi = sum(seg_amt) / len(seg_amt) / 1e8 if seg_amt else 0.0
    drawdown = cl[-1] / max(cl) - 1
    mean20 = sum(v20) / len(v20) if v20 else 0.0
    v_ratio = (sum(v5) / len(v5)) / mean20 if mean20 > 0 else 0.0
    # MACD 底背离：近窗口价格创新低，但 MACD 柱最低值高于上一窗口
    macd_div = False
    if n >= 70:
        w = min(60, n // 2)
        dif = [a - b for a, b in zip(_ema(cl, 12), _ema(cl, 26))]
        dea = _ema(dif, 9)
        hist = [(a - b) * 2 for a, b in zip(dif, dea)]
        macd_div = (min(cl[-w:]) < min(cl[-2 * w:-w])
                    and min(hist[-w:]) > min(hist[-2 * w:-w]))
    zombie = len(seg_turn) >= 5 and seg_turnover < ZOMBIE_TURNOVER
    return {
        "amt20_yi": round(amt20, 3),
        "turnover20": round(turn20, 2),
        "seg_days": len(seg_turn),
        "seg_turnover": round(seg_turnover, 2),
        "seg_amt_yi": round(seg_amt_yi, 3),
        "drawdown": round(drawdown, 4),
        "volume_ratio": round(v_ratio, 2),
        "macd_divergence": bool(macd_div),
        "mild_volume": bool(1.2 <= v_ratio <= 2.5),
        "zombie": bool(zombie),
        "low_liquidity": bool(amt20 < LOW_LIQUIDITY_YI),
    }


def micro_tencent(row: dict, hfq_closes: list):
    """东财主源整体失败、由腾讯备源接管时，用腾讯日线补微观字段。
    腾讯 day 数组只有 OHLC + 成交量(手)，成交额按 量×100×均价 估算（误差 <1%）；
    换手率无可靠来源，置 None 并标 partial=True，页面显示「—」而非 0。"""
    code = row["code"]
    sym = ("sh" if code[0] in "69" else "sz") + code
    query = "?param=" + ",".join(
        [sym, "day", row["listing_date"], TODAY.isoformat(), "1000"])
    arr = None
    for host in random.sample(TQ_HFQ_HOSTS, len(TQ_HFQ_HOSTS)):
        try:
            j = _get_json("https://" + host + "/appstock/app/kline/kline" + query,
                          H_TQ, tries=1, encoding="utf-8")
            a = ((j.get("data") or {}).get(sym) or {}).get("day") or []
            if len(a) >= 2:
                arr = a
                break
        except Exception:                              # noqa: BLE001
            continue
    if not arr or not hfq_closes:
        return None
    if (dt.datetime.now(CN_TZ).strftime("%H%M") < "1505"
            and str(arr[-1][0]) >= TODAY.isoformat()):
        arr = arr[:-1]
    vols, amounts = [], []
    for a in arr:
        try:
            v = float(a[5])
            avg = (float(a[1]) + float(a[2]) + float(a[3]) + float(a[4])) / 4
            vols.append(v)
            amounts.append(v * 100 * avg)
        except (IndexError, ValueError):
            vols.append(0.0)
            amounts.append(0.0)
    cl = hfq_closes[-len(arr):] if len(hfq_closes) >= len(arr) else hfq_closes
    synthetic = {"closes": cl, "vols": vols[-len(cl):],
                 "amounts": amounts[-len(cl):], "turnovers": []}
    base = micro_stats(synthetic, row["issue_price"])
    if not base:
        return None
    base["turnover20"] = None
    base["seg_turnover"] = None
    base["zombie"] = None
    base["partial"] = True
    base["source"] = "腾讯估算"
    return base


def enrich_with_hfq(rows: list):
    def one(row):
        issue = row["issue_price"]
        try:
            em = hfq_em(row)
        except Exception:                              # noqa: BLE001
            em = None
        if not em:
            # 主源全挂：腾讯完整序列接管（含权息基准同为上市首日，可直接除以发行价）
            try:
                tx = hfq_tencent(row)
            except Exception:                          # noqa: BLE001
                tx = None
            if tx and len(tx[1]) >= 2:
                dates, closes = tx
                row["hfq_close"] = closes[-1]
                row["as_of"] = dates[-1]
                row["first_day_premium"] = closes[0] / issue - 1
                row["adj_pct"] = closes[-1] / issue - 1
                row["duration"] = duration_stats(closes, issue)
                row["duration_nominal"] = None
                row["micro"] = micro_tencent(row, closes)
                if row["micro"]:
                    row["micro"]["seg_days"] = None
                row["hfq_source"] = "腾讯备源"
                return row
            row["_hfq_error"] = "后复权主源与备源均失败"
            return row
        row["hfq_close"] = em["hfq_close"]
        row["as_of"] = em["as_of"]
        row["first_day_premium"] = em["first_close"] / issue - 1
        row["adj_pct"] = em["hfq_close"] / issue - 1
        row["duration"] = duration_stats(em["closes"], issue)
        row["duration_nominal"] = (duration_stats(em["nominal_closes"], issue)
                                   if em["nominal_closes"] else None)
        row["micro"] = micro_stats(em, issue)
        row["hfq_source"] = "东财"
        try:
            tx = hfq_tencent(row)
            if (tx and tx[1]
                    and abs(tx[1][-1] / issue - 1 - row["adj_pct"]) > 0.02):
                row["risk_flags"] = ["复权存疑"]
        except Exception:                              # noqa: BLE001
            pass                                       # 备源不可用不影响主源结果
        return row

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(one, rows))


# ----------------------------------------------------------------------------
# L2 连续亏损
# ----------------------------------------------------------------------------
def annual_years():
    # 当前为 11 月，最近两个完整会计年度是去年与前年；
    # 1-4 月年报未披露时，数据缺失会标「财报资料不足」而非误判亏损。
    return [TODAY.year - 1, TODAY.year - 2]


def fetch_financials(codes: list):
    y1, y2 = annual_years()
    dates = ["%d-12-31" % y1, "%d-12-31" % y2]
    filt = ('(SECURITY_CODE in ("%s"))(REPORTDATE in (\'%s\',\'%s\'))'
            % ('","'.join(codes), dates[0], dates[1]))
    columns = ("SECURITY_CODE,REPORTDATE,PARENT_NETPROFIT,DEDUCT_BASIC_EPS,BASIC_EPS,NOTICE_DATE")
    result = {}
    try:
        j = dc_get(FIN_REPORT, columns, filt, 500, 1)
        for r in (j.get("result") or {}).get("data") or []:
            result.setdefault(str(r["SECURITY_CODE"]), {})[str(r["REPORTDATE"])[:4]] = {
                "netprofit": r.get("PARENT_NETPROFIT"),
                "deduct_eps": r.get("DEDUCT_BASIC_EPS"),
            }
    except Exception as exc:                           # noqa: BLE001
        log("财报接口失败：%s" % exc)
    return result, [str(y1), str(y2)]


def is_loss(cell: dict | None) -> bool:
    if not cell:
        return False
    p, e = cell.get("netprofit"), cell.get("deduct_eps")
    return (isinstance(p, (int, float)) and p < 0) or (isinstance(e, (int, float)) and e < 0)


# ----------------------------------------------------------------------------
# L3 重大违规 / 造假 / 审计意见 + 观察标记
# ----------------------------------------------------------------------------
def fetch_announcements(row: dict):
    code = row["code"]
    url = (ANN_URL + "?sr=-1&page_size=100&page_index=1&ann_type=A"
           "&client_source=web&stock_list=" + code + "&f_node=0&s_node=0")
    try:
        j = _get_json(url, H_EM, tries=2, encoding="utf-8")
    except Exception:                                  # noqa: BLE001
        return []
    listing = row["listing_date"]
    out = []
    for a in (j.get("data") or {}).get("list") or []:
        day = str(a.get("notice_date") or "")[:10]
        title = str(a.get("title") or "")
        if day and day >= listing and title:
            out.append({"date": day, "title": title})
    return out


def within(days_str: str, days: int) -> bool:
    try:
        d = dt.date.fromisoformat(days_str)
    except Exception:                                  # noqa: BLE001
        return False
    return (TODAY - d).days <= days


def judge_l3(anns: list):
    hard, observe = [], []
    punish = [a for a in anns if within(a["date"], 1095) and RE_PUNISH.search(a["title"])]
    invest = [a for a in anns if within(a["date"], 365) and RE_INVEST.search(a["title"])]
    closed = [a for a in anns if within(a["date"], 365) and RE_CLOSED.search(a["title"])]
    audit = [a for a in anns if within(a["date"], 365) and RE_AUDIT.search(a["title"])]
    if punish:
        hard.append(("L3-行政处罚/造假", punish[0]))
    # 立案未结案：立案公告之后无更晚的结案类公告
    for a in invest:
        if not any(c["date"] > a["date"] for c in closed):
            hard.append(("L3-立案待核", a))
            break
    if audit:
        hard.append(("L3-审计意见", audit[0]))
    for a in anns:
        if RE_OBSERVE.search(a["title"]):
            observe.append("%s %s" % (a["date"], a["title"][:48]))
            if len(observe) >= 4:
                break
    return hard, observe


def forecast_precheck(codes: list, loss_codes: set, warnings: list):
    """1-2 月业绩预告季：预告净利上限为负且上年已亏 -> 提前预剔除。"""
    if TODAY.month not in (1, 2) or not codes:
        return []
    filt = ('(SECURITY_CODE in ("%s"))(REPORT_DATE=\'%d-12-31\')(IS_LATEST=\'T\')'
            % ('","'.join(codes), TODAY.year - 1))
    columns = ("SECURITY_CODE,PREDICT_FINANCE,PREDICT_AMT_UPPER,PREDICT_AMT_LOWER,"
               "NOTICE_DATE,IS_LATEST,REPORT_DATE")
    out = []
    try:
        j = dc_get(FORECAST_REPORT, columns, filt, 500, 1)
        for r in (j.get("result") or {}).get("data") or []:
            code = str(r.get("SECURITY_CODE") or "")
            label = str(r.get("PREDICT_FINANCE") or "")
            upper = r.get("PREDICT_AMT_UPPER")
            if (code in loss_codes and "净利" in label
                    and isinstance(upper, (int, float)) and upper < 0):
                out.append(code)
    except Exception as exc:                           # noqa: BLE001
        warnings.append("业绩预告预剔除接口失败，本次跳过：%s" % exc)
    return out


# ----------------------------------------------------------------------------
# 增强字段：行业破发率 / 估值分位 / 解禁压力
# ----------------------------------------------------------------------------
def industry_break_stats(universe: list):
    """同一张新股表算出「行业系统性杀跌」代理指标，零新增请求。
    注意：这是同行破发比例，不是行业景气度（营收/库存无结构化源）。"""
    stat = {}
    for r in universe:
        ind = r.get("industry") or "—"
        s = stat.setdefault(ind, {"total": 0, "broken": 0})
        s["total"] += 1
        if r["nominal_pct"] < 0:
            s["broken"] += 1
    for s in stat.values():
        s["rate"] = round(s["broken"] / s["total"], 4) if s["total"] else 0.0
        s["low_sample"] = s["total"] < 5
    return stat


def enrich_valuation(items: list, warnings: list):
    """RPT_VALUE_ANALYSIS：按报告期的 PE 区间，一次批量取全池。"""
    if not items:
        return
    codes = [i["code"] for i in items]
    filt = '(SECURITY_CODE in ("%s"))' % '","'.join(codes)
    columns = "SECURITY_CODE,REPORT,REPORT_DATE,PETTM,PEAVG,PEMAX,PEMIN,PBMRQ"
    try:
        j = dc_get(VALUATION_REPORT, columns, filt, 500, 1)
    except Exception as exc:                           # noqa: BLE001
        warnings.append("估值接口失败，PE 相关列留空：%s" % exc)
        return
    by = {}
    for r in (j.get("result") or {}).get("data") or []:
        by.setdefault(str(r.get("SECURITY_CODE") or ""), []).append(r)
    for it in items:
        rs = by.get(it["code"]) or []
        if not rs:
            continue
        rs.sort(key=lambda x: str(x.get("REPORT_DATE") or ""), reverse=True)
        newest = rs[0]
        pe_now = newest.get("PETTM")
        # TTM 净利转亏时东财返回负 PE；此时压缩比与分位均无意义，不能显示负数误导。
        pe_valid = isinstance(pe_now, (int, float)) and pe_now > 0
        lows = [r["PEMIN"] for r in rs
                if isinstance(r.get("PEMIN"), (int, float)) and r["PEMIN"] > 0]
        highs = [r["PEMAX"] for r in rs
                 if isinstance(r.get("PEMAX"), (int, float)) and r["PEMAX"] > 0]
        lo, hi = (min(lows) if lows else None), (max(highs) if highs else None)
        pe_pct = None
        if pe_valid and lo is not None and hi is not None and hi > lo:
            pe_pct = round(max(0.0, min(1.0, (pe_now - lo) / (hi - lo))), 4)
        issue_pe = it.get("issue_pe")
        compress = (round(pe_now / issue_pe, 4)
                    if pe_valid and isinstance(issue_pe, (int, float)) and issue_pe > 0
                    else None)
        it["valuation"] = {
            "pe_now": round(pe_now, 2) if isinstance(pe_now, (int, float)) else None,
            "pe_compress": compress,
            "pe_low": lo, "pe_high": hi, "pe_percentile": pe_pct,
            "pe_negative": bool(isinstance(pe_now, (int, float)) and pe_now <= 0),
            # 「低发行PE + 深度破发」还需当前 PE 未恶化，才算真有安全边际：
            # TTM 亏损、或当前 PE 明显高于发行 PE，说明是盈利下滑导致的破发。
            "low_pe_deep": bool(pe_valid and compress is not None
                                and isinstance(issue_pe, (int, float))
                                and issue_pe < 30 and it.get("deep")
                                and compress <= 1.0),
            "pe_deteriorated": bool(pe_valid and compress is not None
                                    and compress > 1.5),
            "report": newest.get("REPORT") or "",
        }


def enrich_lift(items: list, warnings: list):
    """RPT_LIFT_STOCK：未来 3 个月解禁，一次取全市场再按池内代码匹配。"""
    if not items:
        return
    start = TODAY.isoformat()
    end = (TODAY + dt.timedelta(days=90)).isoformat()
    filt = "(FREE_DATE>='%s')(FREE_DATE<='%s')" % (start, end)
    columns = ("SECURITY_CODE,SECURITY_NAME_ABBR,FREE_DATE,"
               "ADD_LISTSHARES_RATIO,ADD_LISTING_CAP")
    try:
        j = dc_get(LIFT_REPORT, columns, filt, 500, 1)
    except Exception as exc:                           # noqa: BLE001
        warnings.append("解禁接口失败，解禁列留空：%s" % exc)
        return
    codes = {i["code"] for i in items}
    by = {}
    for r in (j.get("result") or {}).get("data") or []:
        c = str(r.get("SECURITY_CODE") or "")
        if c in codes:
            by.setdefault(c, []).append(r)
    for it in items:
        rs = by.get(it["code"]) or []
        if not rs:
            it["lift"] = {"count": 0, "max_ratio": 0.0, "total_ratio": 0.0,
                          "next_date": None, "cap_yi": 0.0,
                          "days_to_next": None, "months_since_listing": None,
                          "lift_type": None, "high_risk": False, "near": False}
            continue
        rs.sort(key=lambda x: str(x.get("FREE_DATE") or ""))
        ratios = [(r.get("ADD_LISTSHARES_RATIO") or 0) for r in rs]
        caps = [(r.get("ADD_LISTING_CAP") or 0) for r in rs]
        next_date = str(rs[0].get("FREE_DATE") or "")[:10]
        days_to = None
        months = None
        try:
            d0 = dt.date.fromisoformat(it["listing_date"][:10])
            d1 = dt.date.fromisoformat(next_date)
            days_to = (d1 - TODAY).days
            months = round((d1 - d0).days / 30.44, 1)
        except Exception:                              # noqa: BLE001
            pass
        # 近 3 年新股池的解禁只会落在首发 6 / 12 / 36 个月节点上，
        # 用「距上市月数」区分首发原股东与小额配售，比不存在的类型字段更可靠。
        if months is None:
            lift_type = "未知节点"
        elif 11 <= months <= 13:
            lift_type = "首发一年期"
        elif 35 <= months <= 37:
            lift_type = "首发三年期"
        elif 5 <= months <= 7:
            lift_type = "首发配售半年期"
        else:
            lift_type = "其他节点"
        max_ratio = max(ratios)
        it["lift"] = {
            "count": len(rs),
            "max_ratio": round(max_ratio, 4),
            "total_ratio": round(sum(ratios), 4),
            "next_date": next_date,
            "cap_yi": round(sum(caps) / 10000, 2),
            "days_to_next": days_to,
            "months_since_listing": months,
            "lift_type": lift_type,
            "high_risk": bool(lift_type in ("首发一年期", "首发三年期")
                              and max_ratio >= 0.20),
            "near": bool(days_to is not None and 0 <= days_to <= LIFT_NEAR_DAYS),
        }


# ----------------------------------------------------------------------------
# v3：流通市值 / 资金流双源（均为展示字段，失败不改变入池）
# ----------------------------------------------------------------------------
def enrich_float_mv(items: list, warnings: list):
    """腾讯 qt.gtimg.cn 批量报价：[44] 流通市值(亿) / [45] 总市值(亿) / [38] 换手率%。
    返回 GBK，一次请求覆盖全池；散点图气泡大小用流通市值。"""
    if not items:
        return
    syms = ",".join(("sh" if i["code"][0] in "69" else "sz") + i["code"]
                    for i in items)
    try:
        req = urllib.request.Request(QT_URL + syms, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "replace")
        got = {}
        for line in raw.strip().split("\n"):
            if '"' not in line:
                continue
            a = line.split('"')[1].split("~")
            if len(a) < 46 or not a[2]:
                continue
            try:
                got[a[2]] = {"float_mv_yi": round(float(a[44]), 2),
                             "total_mv_yi": round(float(a[45]), 2),
                             "turnover_rate": round(float(a[38]), 2)}
            except ValueError:
                continue
        for it in items:
            it["market_cap"] = got.get(it["code"])
        miss = len(items) - sum(1 for i in items if i.get("market_cap"))
        if miss:
            warnings.append("腾讯批量报价缺 %d 只流通市值，散点图气泡按默认值显示" % miss)
    except Exception as exc:                           # noqa: BLE001
        for it in items:
            it["market_cap"] = None
        warnings.append("腾讯批量报价失败，流通市值列留空：%s" % exc)


def _flow_snapshot(items: list, as_of: str, warnings: list):
    """保底源：RPT_DMSK_TS_STOCKNEW 是全市场单日资金流快照，一次批量取全池。
    历史日期查询返回空，因此不能用它算近 5 日累计。"""
    snap = {}
    if not items or not as_of:
        return snap
    filt = ("(TRADE_DATE='%s')(SECURITY_CODE in (\"%s\"))"
            % (as_of, '","'.join(i["code"] for i in items)))
    columns = ("SECURITY_CODE,TRADE_DATE,PRIME_INFLOW,PRIME_COST_20DAYS,"
               "TURNOVERRATE,CLOSE_PRICE,TOTALSCORE,RATIO_3DAYS")
    try:
        j = dc_get(FLOW_SNAPSHOT_REPORT, columns, filt, 500, 1)
        for r in (j.get("result") or {}).get("data") or []:
            snap[str(r.get("SECURITY_CODE") or "")] = r
    except Exception as exc:                           # noqa: BLE001
        warnings.append("资金流单日快照失败：%s" % exc)
    return snap


def _flow_5d_one(it: dict):
    """逐股资金流日线：取最近 5 根 f52(主力净额，元) 求和。失败返回 None。"""
    code = it["code"]
    secid = ("1." if code[0] in "69" else "0.") + code
    query = ("?lmt=8&klt=101&fields1=f1,f2,f3,f7&fields2=f51,f52&secid=" + secid)
    try:
        j = his_get(query, H_HIS, tries=3, path=FLOW_PATH)
        klines = (j.get("data") or {}).get("klines") or []
        vals = []
        for k in klines[-5:]:
            p = k.split(",")
            if len(p) >= 2:
                vals.append(float(p[1]))
        return sum(vals) if len(vals) >= 3 else None
    except Exception:                                  # noqa: BLE001
        return None


def enrich_fundflow(items: list, as_of: str, warnings: list):
    """双源：单日快照保底 + 逐股近 5 日净额增强。
    逐股成功率 <60% 时整体回退到「单日」口径；单只失败则该只单独回退。
    任何情况都不抛异常、不改变入池。"""
    if not items:
        return
    snap = _flow_snapshot(items, as_of, warnings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        five = list(pool.map(_flow_5d_one, items))
    ok = sum(1 for v in five if v is not None)
    global_fallback = ok < len(items) * 0.6
    if global_fallback:
        warnings.append("逐股资金流仅 %d/%d 成功，整体降级为单日快照口径"
                        % (ok, len(items)))
    for it, v5 in zip(items, five):
        r = snap.get(it["code"]) or {}
        prime1 = r.get("PRIME_INFLOW")
        cost = r.get("PRIME_COST_20DAYS")
        close = r.get("CLOSE_PRICE")
        cost_gap = None
        if (isinstance(cost, (int, float)) and cost > 0
                and isinstance(close, (int, float))):
            cost_gap = round(close / cost - 1, 4)   # 负 = 现价低于主力 20 日成本
        it["fundflow"] = {
            "source": "5日逐股" if (v5 is not None and not global_fallback) else "单日快照",
            "fallback": bool(global_fallback or v5 is None),
            "prime_inflow_1d": round(prime1, 2) if isinstance(prime1, (int, float)) else None,
            "prime_inflow_5d": round(v5, 2) if (v5 is not None and not global_fallback) else None,
            "prime_cost_20d": round(cost, 2) if isinstance(cost, (int, float)) else None,
            "cost_gap": cost_gap,
            "turnover": r.get("TURNOVERRATE"),
            "score": r.get("TOTALSCORE"),
            "ratio_3d": r.get("RATIO_3DAYS"),
            "as_of": as_of,
        }
    if not snap:
        warnings.append("单日资金流快照无数据，资金流标签全部留空")


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    warnings = []
    universe, l1_out, window_start = fetch_universe()
    ind_stat = industry_break_stats(universe)
    log("L1 后基础池 %d 只（剔除 %d 只 ST/风险警示/非沪深）" % (len(universe), len(l1_out)))

    nominal = [r for r in universe if r["nominal_pct"] < 0]
    log("名义破发 %d 只" % len(nominal))
    nominal = enrich_with_hfq(nominal)
    # fail-safe：限流导致复权大面积失败时，绝不覆盖已有产物（否则线上会得到空池/残缺池）
    hfq_failed = len([r for r in nominal if "_hfq_error" in r])
    if nominal and hfq_failed / len(nominal) > 0.20:
        log("ERROR：后复权失败率 %.0f%%（%d/%d），疑似接口限流；拒绝写入，保留上一版产物"
            % (hfq_failed / len(nominal) * 100, hfq_failed, len(nominal)))
        raise SystemExit(2)

    excluded = []
    real = []
    for r in nominal:
        if "_hfq_error" in r:
            warnings.append("%s %s" % (r["code"], r["_hfq_error"]))
            excluded.append({"code": r["code"], "name": r["name"], "layer": "P0",
                             "reason": "复权数据缺失", "evidence": ""})
            continue
        if r["adj_pct"] >= 0:
            excluded.append({
                "code": r["code"], "name": r["name"], "layer": "P0",
                "reason": "送转/分红造成的假破发（含权息并未跌破发行价）",
                "evidence": "名义 %.1f%% → 含权息 %+.1f%%" %
                            (r["nominal_pct"] * 100, r["adj_pct"] * 100),
            })
            continue
        real.append(r)
    log("含权息真实破发 %d 只（剔除假破发 %d 只）" %
        (len(real), len([e for e in excluded if e["layer"] == "P0"])))

    fin, years = fetch_financials([r["code"] for r in real])
    blacklist = {"codes": []}
    if os.path.exists(BLACKLIST_PATH):
        try:
            with open(BLACKLIST_PATH, "r", encoding="utf-8") as fh:
                blacklist = json.load(fh)
        except Exception as exc:                       # noqa: BLE001
            warnings.append("人工黑名单读取失败：%s" % exc)
    bl_codes = {str(c) for c in (blacklist.get("codes") or [])}
    bl_notes = blacklist.get("notes") or {}

    loss_codes = set()

    def one(r):
        cells = fin.get(r["code"], {})
        r["fin"] = {y: cells.get(y) for y in years}
        if len(cells) < len(years):
            r["observe"] = list(r.get("observe") or []) + ["财报资料不足"]
        if all(is_loss(cells.get(y)) for y in years):
            loss_codes.add(r["code"])
            return r, ("L2", "近两年归母净利/扣非孰低连续为负",
                       "、".join("%s年 归母%s / 扣非EPS%s" %
                                (y, (cells.get(y) or {}).get("netprofit"),
                                 (cells.get(y) or {}).get("deduct_eps")) for y in years))
        anns = fetch_announcements(r)
        hard, observe = judge_l3(anns)
        r["observe"] = list(r.get("observe") or []) + observe
        r["risk_flags"] = list(r.get("risk_flags") or [])
        if r["code"] in bl_codes:
            return r, ("L3-人工黑名单", "人工认定重大风险",
                       str(bl_notes.get(r["code"], "")))
        if hard:
            layer, a = hard[0]
            return r, (layer, "上市后公告命中硬性排除规则",
                       "%s %s" % (a["date"], a["title"][:70]))
        return r, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        judged = list(pool.map(one, real))

    items = []
    for r, hit in judged:
        if hit:
            layer, reason, evidence = hit
            excluded.append({"code": r["code"], "name": r["name"], "layer": layer,
                             "reason": reason, "evidence": evidence})
            continue
        r.pop("_hfq_error", None)
        r["deep"] = r["adj_pct"] <= DEEP_THRESHOLD
        r["depth_tier"] = ("极端" if r["adj_pct"] <= EXTREME_THRESHOLD else
                           "深度" if r["deep"] else "浅度")
        r["risk_flags"] = list(dict.fromkeys(r.get("risk_flags") or []))
        r["observe"] = list(dict.fromkeys(r.get("observe") or []))
        items.append(r)

    pre_out = forecast_precheck([i["code"] for i in items], loss_codes, warnings)
    if pre_out:
        kept = []
        for i in items:
            if i["code"] in pre_out:
                excluded.append({"code": i["code"], "name": i["name"], "layer": "L2-预告",
                                 "reason": "年报预告净利上限为负且上年亏损（预告季预剔除）",
                                 "evidence": ""})
            else:
                kept.append(i)
        items = kept

    # 增强字段：估值分位、解禁压力、行业破发率（各一次批量请求，不逐股打接口）
    enrich_valuation(items, warnings)
    enrich_lift(items, warnings)
    for i in items:
        i["industry_stat"] = ind_stat.get(i.get("industry") or "—")
    # L2 只看年报，抓不到「上市后首个报告期 TTM 转亏」；这里只预警，不硬剔。
    for i in items:
        if (i.get("valuation") or {}).get("pe_negative"):
            flags = list(i.get("risk_flags") or [])
            if "TTM转亏" not in flags:
                flags.append("TTM转亏")
            i["risk_flags"] = flags

    # v3：流通市值（腾讯一次批量）与资金流双源（单日快照保底 + 逐股 5 日降级）
    as_of = max((i.get("as_of") or "") for i in items) if items else TODAY.isoformat()
    enrich_float_mv(items, warnings)
    enrich_fundflow(items, as_of, warnings)

    items.sort(key=lambda x: x["adj_pct"])

    # 与上期快照比较，产出调入/调出
    prev = None
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, "r", encoding="utf-8") as fh:
                prev = json.load(fh)
        except Exception:                              # noqa: BLE001
            prev = None
    prev_codes = {i["code"] for i in (prev or {}).get("items", [])}
    cur_codes = {i["code"] for i in items}
    changes = {
        "prev_as_of": ((prev or {}).get("meta") or {}).get("as_of"),
        "added": sorted(cur_codes - prev_codes),
        "removed": sorted(prev_codes - cur_codes),
    }
    if not prev_codes:
        changes["note"] = "首次生成，无上期快照"

    meta = {
        "generated_at": dt.datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "as_of": as_of,
        "window_start": window_start,
        "window": "近 %d 年上市" % WINDOW_YEARS,
        "rules_version": RULES_VERSION,
        "formula": "含权息破发% = 后复权最新收盘价 / IPO 发行价 - 1；名义破发% = 未复权现价 / 发行价 - 1",
        "sources": [
            "datacenter-web RPTA_APP_IPOAPPLY（发行价/上市日/最新价/行业）",
            "push2his 分片域名池 fqt=2（后复权主源 + 破发持续期 + 量额换手）、fqt=0（名义口径对照）",
            "push2his fflow/daykline（逐股近 5 日主力净额，失败自动降级）",
            "ifzq.gtimg.cn / web.ifzq.gtimg.cn fqkline hfq（后复权备源交叉校验）",
            "qt.gtimg.cn 批量报价（流通市值 / 换手率）",
            "datacenter-web RPT_LICO_FN_CPD（年报归母净利/扣非EPS）",
            "datacenter-web RPT_VALUE_ANALYSIS（按报告期 PE 区间）",
            "datacenter-web RPT_LIFT_STOCK（未来 3 个月解禁明细）",
            "datacenter-web RPT_DMSK_TS_STOCKNEW（全市场单日资金流快照，保底源）",
            "np-anotice-stock 公告标题（L3 风控）",
        ],
        "thresholds": {"deep": DEEP_THRESHOLD, "extreme": EXTREME_THRESHOLD,
                       "entry": 0.0, "low_liquidity_yi": LOW_LIQUIDITY_YI,
                       "zombie_turnover": ZOMBIE_TURNOVER,
                       "lift_near_days": LIFT_NEAR_DAYS},
        "funnel": {
            "universe": len(universe), "l1_removed": len(l1_out),
            "nominal_break": len(nominal), "real_break": len(real),
            "final": len(items),
            "deep": len([i for i in items if i["deep"]]),
            "extreme": len([i for i in items if i["depth_tier"] == "极端"]),
            "long_term": len([i for i in items
                              if (i.get("duration") or {}).get("break_ratio", 0) >= 0.5]),
            "lift_warning": len([i for i in items
                                 if (i.get("lift") or {}).get("max_ratio", 0) >= 0.20]),
            "low_pe_deep": len([i for i in items
                                if (i.get("valuation") or {}).get("low_pe_deep")]),
            "low_liquidity": len([i for i in items
                                  if (i.get("micro") or {}).get("low_liquidity")]),
            "macd_divergence": len([i for i in items
                                    if (i.get("micro") or {}).get("macd_divergence")]),
            "mild_volume": len([i for i in items
                                if (i.get("micro") or {}).get("mild_volume")]),
            "ttm_loss": len([i for i in items
                             if (i.get("valuation") or {}).get("pe_negative")]),
            "lift_high_risk": len([i for i in items
                                   if (i.get("lift") or {}).get("high_risk")]),
            "fundflow_5d": len([i for i in items
                                if (i.get("fundflow") or {}).get("prime_inflow_5d") is not None]),
        },
        "excluded_by_layer": {},
        "warnings": warnings,
        "disclaimer": (
            "风险信号排除不等于对财务造假的法律认定；审计意见与证监会处罚无结构化数据源，"
            "以公告标题+人工黑名单为准，需人工复核。破发≠低估，注册制下高发行PE个股破发"
            "可能是估值回归。「行业破发率」只是同行是否同步杀跌的代理指标，并非行业景气度"
            "（营收增速/库存周期无结构化数据源），样本数 <5 的行业已标注。破发持续期分"
            "含权息与名义两种口径，名义口径会被送转除权放大，主判据以含权息为准。"
            "MACD 底背离、温和放量、主力资金净额、主力成本偏离、僵尸股等均为形态/统计标签，"
            "不是买卖信号；资金流逐股接口失败时会自动降级为单日快照口径并在页面标注。"
            "「20 日均成交额 < 5000 万」只是可切换筛选器，不作为入池硬门槛——冷门错杀股"
            "与高流动性本就互斥，硬过滤会剔掉大半池子。「TTM 转亏」是预警标签，因 L2 只"
            "核对经审计年报，上市后首个报告期转亏需人工复核。"
            "仅供形态研究，不构成投资建议。"
        ),
    }
    for e in excluded:
        meta["excluded_by_layer"][e["layer"]] = meta["excluded_by_layer"].get(e["layer"], 0) + 1

    # fail-safe 第二道：入池数较上期骤降超过 50% 时拒绝写入
    prev_items = (prev or {}).get("items", [])
    if prev_items and len(items) < len(prev_items) * 0.5:
        log("ERROR：本次入池 %d 只，较上期 %d 只骤降超过 50%%；拒绝写入，保留上一版产物"
            % (len(items), len(prev_items)))
        raise SystemExit(2)

    payload = {"meta": meta, "items": items, "excluded": excluded, "changes": changes}
    # 统一 LF：Windows 本地默认写 CRLF，GitHub Actions(Linux) 写 LF，
    # 不固定会在每天 workflow 重算后产生整文件换行 diff。
    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    log("最终入池 %d 只（深度 %d / 极端 %d），剔除 %d 只，as_of=%s" %
        (len(items), meta["funnel"]["deep"], meta["funnel"]["extreme"],
         len(excluded), as_of))
    log("写入 %s" % OUT_PATH)


if __name__ == "__main__":
    main()
