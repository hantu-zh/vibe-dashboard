#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""季报业绩跟踪 · 周更流水线（全市场扫描 + 候选池 + 跟踪池 一次性产出）

背景
----
2026-10-18 那两份数据（q3_scan_candidates.json / q3_earnings.json）是临时手工跑的，
生产脚本没进仓库，于是页面停在三季报预告期的旧快照上。本脚本把 scan_q3 / fetch_q3 /
build_q3 三段合并重建，挂到周更 workflow 上，从此不需要人工重跑。

数据源（东财 datacenter，稳定、免 token）
----------------------------------------
RPT_PUBLIC_OP_NEWPREDICT  业绩预告。同一只票按「预测指标」分多条记录：
                          004=归母净利润  005=扣非净利润  006=营业收入  002=净利润
                          合并后可用 004 对 005 算出「扣非是否跟得上归母」，
                          比原来靠关键词猜「扣非存疑」准得多。
RPT_PUBLIC_BS_APPOIN      预约披露日期 + 是否已披露（页面「N 天」倒计时用）
RPT_LICO_FN_CPD           正式财报（判断「已出正式 / 仅有预告」）
RPT_F10_BASIC_ORGINFO     上市日期（判断上市是否满 1 年，替代原来的 K 线兜底核验）

产出（覆盖写；任一步失败即退出非 0，绝不用残缺数据覆盖旧快照）
------------------------------------------------------------
q3_scan_candidates.json   全市场候选池（≥50% 口径，含机械剔除统计）
q3_earnings.json          跟踪池（核心 top N + 观察池）

用法
----
python q3_track.py                    # 自动判定报告期（最近一个已过季末）
python q3_track.py --date 2026-11-06  # 手工指定基准日（补跑/测试）
python q3_track.py --dry-run          # 只打印统计，不写文件
"""

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_CAND = os.path.join(ROOT, "q3_scan_candidates.json")
OUT_TRACK = os.path.join(ROOT, "q3_earnings.json")

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

MIN_YOY = 50.0          # 入池门槛：预告净利同比下限 ≥ 50%
CORE_TOP_N = 36         # 核心池取增速前 N（与原快照口径一致）
WATCH_TOP_N = 20        # 观察池上限（扭亏类）
MIN_ROWS = 30           # 某报告期的预告条数低于此值 → 视为窗口未开，回退上一期
DEDUCT_GAP_PP = 30.0    # 归母增速 - 扣非增速 ≥ 30 个百分点 → 扣非存疑

QEND = ((3, 31), (6, 30), (9, 30), (12, 31))

# 扣非存疑的关键词兜底（仅当没有 005 扣非记录时使用）
KCFJ_WORDS = ("政府补助", "资产处置", "非经常性损益", "投资收益", "公允价值变动",
              "债务重组", "重组收益", "出售资产", "拆迁补偿", "诉讼赔偿")

# 行业方向关键词（名称粗判，只作筛选辅助，不追求准确）
INDUSTRY_RULES = (
    ("AI算力/光通信", ("光模块", "光通信", "光纤", "光缆", "算力", "数据中心", "服务器",
                       "cpo", "铜连接", "铜缆", "液冷", "交换机", "网络设备")),
    ("半导体设备", ("半导体", "芯片", "集成电路", "晶圆", "封测", "刻蚀", "光刻",
                    "存储", "ic", "功率器件", "第三代半导体", "碳化硅", "mcu")),
    ("PCB/消费电子", ("pcb", "线路板", "电路板", "消费电子", "电子元件", "连接器",
                      "面板", "显示", "光学", "镜头", "模组", "精密制造", "结构件")),
    ("稀土/资源", ("稀土", "钨", "锂", "钴", "镍", "铜", "铝", "黄金", "煤", "矿",
                   "有色", "磷", "钛", "锑", "钼", "资源")),
    ("化工涨价", ("化工", "化学", "树脂", "mdi", "纯碱", "农药", "化肥", "氟", "硅料",
                  "涂料", "染料", "新材料", "橡塑", "聚氨酯")),
    ("医药", ("医药", "生物", "制药", "医疗", "疫苗", "器械", "原料药", "cxo",
              "中药", "诊断", "基因")),
)

QNAME = {"03": "一季报", "06": "中报", "09": "三季报", "12": "年报"}
WIN = {"03": (4, 1, 4, 30), "06": (7, 1, 8, 31), "09": (10, 1, 10, 31), "12": (1, 1, 4, 30)}


# ---------------------------------------------------------------- 基础工具
def log(*a):
    print(*a, flush=True)


def _get(params, tries=4):
    """带重试的 datacenter 单次请求。"""
    p = dict(params)
    p.setdefault("source", "WEB")
    p.setdefault("client", "WEB")
    url = API + "?" + urllib.parse.urlencode(p)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://data.eastmoney.com/",
                "Accept": "*/*",
            })
            raw = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
            d = json.loads(raw)
            if d.get("success") is False:
                raise RuntimeError("api said: %s" % d.get("message"))
            return d.get("result") or {}
        except Exception as e:                                  # noqa: BLE001
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
    raise RuntimeError("接口失败 report=%s err=%s" % (params.get("reportName"), last))


def fetch_all(report, columns="ALL", filter_="", sort="", order="-1",
              page_size=200, max_pages=40):
    """分页拉全量。"""
    out, page = [], 1
    while page <= max_pages:
        res = _get({
            "reportName": report, "columns": columns,
            "pageNumber": page, "pageSize": page_size,
            "sortColumns": sort, "sortTypes": order, "filter": filter_,
        })
        data = res.get("data") or []
        out += data
        total_pages = res.get("pages") or 1
        if not data or page >= total_pages or len(data) < page_size:
            break
        page += 1
    return out


def fetch_in(report, columns, codes, date_field=None, report_date=None, chunk=60):
    """按 SECURITY_CODE 批量 in 查询（避免逐只请求）。"""
    rows = []
    codes = list(codes)
    for i in range(0, len(codes), chunk):
        part = codes[i:i + chunk]
        f = "(%s='%s')" % (date_field, report_date) if date_field else ""
        f += '(SECURITY_CODE in (%s))' % ",".join('"%s"' % c for c in part)
        res = _get({"reportName": report, "columns": columns,
                    "pageNumber": 1, "pageSize": 500, "filter": f})
        rows += res.get("data") or []
    return rows


def r2(v):
    if v is None or v == "":
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def dstr(v):
    """'2026-10-23 00:00:00' -> '2026-10-23'"""
    if not v:
        return None
    s = str(v)[:10]
    return s if len(s) == 10 and s[4] == "-" else None


def to_date(v):
    s = dstr(v)
    if not s:
        return None
    try:
        return dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        return None


# ---------------------------------------------------------------- 报告期判定
def quarter_ends_desc(today):
    out = []
    for y in (today.year, today.year - 1):
        for m, d in QEND:
            q = dt.date(y, m, d)
            if q <= today:
                out.append(q)
    return sorted(set(out), reverse=True)


def window_text(rd):
    mm = "%02d" % rd.month
    qn, (sm, sd, em, ed) = QNAME[mm], WIN[mm]
    y = rd.year + 1 if mm == "12" else rd.year
    return "%d %s披露窗口(%d/%d–%d/%d)" % (y, qn, sm, sd, em, ed)


# ---------------------------------------------------------------- 分类口径
def industry_of(name):
    n = (name or "").lower()
    for label, words in INDUSTRY_RULES:
        for w in words:
            if w in n:
                return label
    return "其他"


def board_of(code, market):
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("8", "4", "9")):
        return "北交所"
    if "风险警示" in (market or ""):
        return "风险警示"
    return "主板"


def growth_bucket(yoy, turnaround, ptype):
    if turnaround:
        return "扭亏"
    if "减亏" in (ptype or ""):
        return "减亏"
    if yoy is None:
        return "未知"
    if yoy >= 100:
        return "超高增(≥100%)"
    if yoy >= 50:
        return "高增(50%~100%)"
    if yoy >= 0:
        return "稳增(0~50%)"
    return "同比转差"


# ---------------------------------------------------------------- 主流程
def collect(rd, today, no_listing=False):
    """拉齐一个报告期的全部原料，合并成按股票代码索引的记录。"""
    f_predict = "(ADD_AMP_LOWER>=%g)(REPORT_DATE='%s')" % (MIN_YOY, rd)
    rows = fetch_all("RPT_PUBLIC_OP_NEWPREDICT", filter_=f_predict, sort="NOTICE_DATE")
    log("[fetch] 预告原始记录 %d 条（%s）" % (len(rows), rd))

    # 同一只票按预测指标拆成多条（004 归母 / 005 扣非 / 006 营收 / 002 净利），合并
    by_code = {}
    for r in rows:
        code = r.get("SECURITY_CODE")
        if not code:
            continue
        slot = by_code.setdefault(code, {"base": None, "alt": None, "deduct": None,
                                         "revenue": None, "raw": [], "notice": ""})
        fcode = r.get("PREDICT_FINANCE_CODE")
        slot["raw"].append(r)
        slot["notice"] = max(slot["notice"], dstr(r.get("NOTICE_DATE")) or "")
        rec = {
            "yoy": (r2(r.get("ADD_AMP_LOWER")), r2(r.get("ADD_AMP_UPPER"))),
            "amt": (r2((r.get("PREDICT_AMT_LOWER") or 0) / 1e4) if r.get("PREDICT_AMT_LOWER") is not None else None,
                    r2((r.get("PREDICT_AMT_UPPER") or 0) / 1e4) if r.get("PREDICT_AMT_UPPER") is not None else None),
            "type": r.get("PREDICT_TYPE"),
            "text": (r.get("PREDICT_CONTENT") or "") + (r.get("CHANGE_REASON_EXPLAIN") or ""),
        }
        if fcode == "004":
            slot["base"] = rec
        elif fcode == "005":
            slot["deduct"] = rec
        elif fcode == "006":
            slot["revenue"] = rec
        elif fcode == "002":
            slot["alt"] = rec
        # 其它指标（如扣非前/别的口径）不入池，仅留 raw 备查
        slot["meta"] = r

    codes = list(by_code.keys())
    log("[fetch] 去重后 %d 只" % len(codes))

    # 预约披露 / 正式财报 / 上市日期：批量 in 查，三次请求搞定
    appoin, formal, listing = {}, {}, {}
    try:
        for r in fetch_in("RPT_PUBLIC_BS_APPOIN",
                          "SECURITY_CODE,APPOINT_PUBLISH_DATE,IS_PUBLISH,ACTUAL_PUBLISH_DATE",
                          codes, date_field="REPORT_DATE", report_date=rd):
            appoin[r["SECURITY_CODE"]] = {
                "appoint": dstr(r.get("APPOINT_PUBLISH_DATE")),
                "actual": dstr(r.get("ACTUAL_PUBLISH_DATE")),
                "published": str(r.get("IS_PUBLISH") or "") == "1",
            }
        log("[fetch] 预约披露 %d 只" % len(appoin))
    except Exception as e:                                       # noqa: BLE001
        log("[warn] 预约披露拉取失败，降级为空：%s" % e)

    try:
        for r in fetch_in("RPT_LICO_FN_CPD",
                          "SECURITY_CODE,PARENT_NETPROFIT,SJLTZ,NOTICE_DATE",
                          codes, date_field="REPORTDATE", report_date=rd):
            formal[r["SECURITY_CODE"]] = {
                "yoy": r2(r.get("SJLTZ")),
                "amt_wan": r2((r.get("PARENT_NETPROFIT") or 0) / 1e4)
                if r.get("PARENT_NETPROFIT") is not None else None,
            }
        log("[fetch] 已出正式财报 %d 只" % len(formal))
    except Exception as e:                                       # noqa: BLE001
        log("[warn] 正式财报状态拉取失败，全部按「仅有预告」标注：%s" % e)

    if not no_listing:
        for i in range(0, len(codes), 60):
            part = codes[i:i + 60]
            try:
                res = _get({
                    "reportName": "RPT_F10_BASIC_ORGINFO",
                    "columns": "SECURITY_CODE,LISTING_DATE",
                    "pageNumber": 1, "pageSize": 500,
                    "filter": '(SECURITY_CODE in (%s))' % ",".join('"%s"' % c for c in part),
                })
                for r in res.get("data") or []:
                    listing[r["SECURITY_CODE"]] = dstr(r.get("LISTING_DATE"))
            except Exception as e:                               # noqa: BLE001
                log("[warn] 上市日期批次失败（%d 只）：%s" % (len(part), e))
        log("[fetch] 上市日期 %d 只" % len(listing))

    return by_code, appoin, formal, listing, len(rows)


def build(rd, today, by_code, appoin, formal, listing, raw_rows):
    cands, stats = [], {
        "excluded_bse": 0, "excluded_st": 0, "excluded_new_listing": 0,
        "observation_turnaround": 0, "kcfj_suspect": 0,
    }

    for code, slot in by_code.items():
        base = slot["base"] or slot["alt"]
        if not base:
            continue
        m = slot.get("meta") or {}
        name = m.get("SECURITY_NAME_ABBR") or ""
        market = m.get("TRADE_MARKET") or ""
        ptype = base["type"] or ""

        bd = board_of(code, market)
        if bd == "北交所":
            stats["excluded_bse"] += 1
            continue
        if bd == "风险警示" or "ST" in name.upper():
            stats["excluded_st"] += 1
            continue

        # 上市不满 1 年（以报告期为基准）剔除 —— 次新股业绩预告噪音大
        ipo = listing.get(code)
        if ipo:
            d = to_date(ipo)
            if d and (rd - d).days < 365:
                stats["excluded_new_listing"] += 1
                continue
            new_listing = "满1年(自动核)"
        else:
            new_listing = "需F10核对"

        np_yoy = base["yoy"]
        deduct = slot["deduct"]
        dyoy = deduct["yoy"] if deduct else (None, None)

        suspect = False
        if dyoy[0] is not None and np_yoy[0] is not None:
            suspect = (np_yoy[0] - dyoy[0]) >= DEDUCT_GAP_PP
        else:
            txt = base["text"]
            suspect = any(w in txt for w in KCFJ_WORDS)

        turnaround = "扭亏" in ptype
        if turnaround:
            stats["observation_turnaround"] += 1
        if suspect:
            stats["kcfj_suspect"] += 1

        cands.append({
            "code": code,
            "name": name,
            "market": market,
            "predict_type": ptype,
            "np_yoy": [np_yoy[0], np_yoy[1]],
            "np_amt_wan": [base["amt"][0], base["amt"][1]],
            "deduct_yoy": [dyoy[0], dyoy[1]] if deduct else None,
            "revenue_yoy": [slot["revenue"]["yoy"][0], slot["revenue"]["yoy"][1]] if slot["revenue"] else None,
            "notice_date": slot["notice"] or dstr(m.get("NOTICE_DATE")),
            "report_date": dstr(m.get("REPORT_DATE")) or str(rd),
            "turnaround": turnaround,
            "kcfj_suspect": bool(suspect),
            "new_listing": new_listing,
            "pool": "观察" if turnaround else "核心",
            "ipo_date": ipo,
            "actual_np_yoy": (formal.get(code) or {}).get("yoy"),
            "actual_np_amt_wan": (formal.get(code) or {}).get("amt_wan"),
        })

    # 增速优先排序，让页面默认顺序稳定可读
    cands.sort(key=lambda c: (-(c["np_yoy"][0] if c["np_yoy"][0] is not None else -999), c["code"]))
    return cands, stats


def build_track(rd, today, cands, appoin, formal):
    items = []
    for c in cands:
        code = c["code"]
        core = c["pool"] == "核心"
        ap = appoin.get(code) or {}
        apt = ap.get("appoint")
        is_formal = code in formal
        days = (to_date(apt) - today).days if to_date(apt) else None

        dyoy = c.get("deduct_yoy")
        tags = []
        if not core:
            tags.append("扭亏·观察池")
        if c["kcfj_suspect"]:
            tags.append("非经损益·待核")
        if days is not None and 0 <= days <= 5:
            tags.append("临近披露")
        if not tags:
            tags.append("正常跟踪")

        ipo = c.get("ipo_date")
        age = None
        d = to_date(ipo) if ipo else None
        if d:
            age = round((rd - d).days / 365.25, 1)

        bits = [c["predict_type"] or "—"]
        lo, hi = (c["np_amt_wan"] + [None, None])[:2]
        if lo is not None:
            bits.append("预告净利约 %s~%s 万" % (("%g" % lo), ("%g" % hi if hi is not None else "—")))
        if dyoy and dyoy[0] is not None:
            bits.append("扣非同比 %+.2f%%~%+.2f%%" % (dyoy[0], dyoy[1] if dyoy[1] is not None else dyoy[0]))
        elif c["kcfj_suspect"]:
            bits.append("扣非存疑(含非经损益)")
        if is_formal:
            ay = (formal.get(code) or {}).get("yoy")
            bits.append("已出正式财报" + ("，正式净利同比 %+.2f%%" % ay if ay is not None else ""))
        bits.append("new_listing=%s" % c["new_listing"])
        bits.append("scan_pool=%s" % c["pool"])

        items.append({
            "code": code,
            "name": c["name"],
            "board": board_of(code, c["market"]),
            "industry_dir": industry_of(c["name"]),
            "ipo_date": ipo,
            "listing_age_years": age,
            "pass_hard_filter": True,
            "exclude_reason": None,
            "report_type": "正式" if is_formal else "预告",
            "disclosure_status": "已出正式" if is_formal else "仅有预告",
            "appointment_date": apt,
            "days_to_appointment": days,
            "forecast_np_yoy": c["np_yoy"],
            "forecast_deduct_yoy": dyoy,
            "actual_np_yoy": c.get("actual_np_yoy"),
            "actual_np_amt_wan": c.get("actual_np_amt_wan"),
            "growth_bucket": growth_bucket(c["np_yoy"][0], c["turnaround"], c["predict_type"]),
            "is_turnaround": c["turnaround"],
            "nonrecurring_risk": "mid" if c["kcfj_suspect"] else "low",
            "core": core,
            "focus_tags": tags,
            "notes": "；".join(bits),
            "np_amt_wan": c["np_amt_wan"],
            "predict_type": c["predict_type"],
            "kcfj_suspect": c["kcfj_suspect"],
            "new_listing": c["new_listing"],
            "sector_guess": industry_of(c["name"]),
            "scan_pool": c["pool"],
        })

    core_items = [i for i in items if i["core"]][:CORE_TOP_N]
    watch_items = [i for i in items if not i["core"]][:WATCH_TOP_N]
    kept = core_items + watch_items
    kept.sort(key=lambda i: (not i["core"], -(i["forecast_np_yoy"][0] if i["forecast_np_yoy"][0] is not None else -999)))
    return kept, len(core_items), len(watch_items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="基准日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件")
    ap.add_argument("--no-listing", action="store_true", help="跳过上市日期查询（离线调试用）")
    args = ap.parse_args()

    today = to_date(args.date) if args.date else dt.date.today()
    if not today:
        log("[fatal] --date 格式应为 YYYY-MM-DD")
        return 2

    # 逐个报告期试：最近季末的预告条数太少说明窗口没开，回退上一期
    chosen = None
    for rd in quarter_ends_desc(today)[:3]:
        f = "(ADD_AMP_LOWER>=%g)(REPORT_DATE='%s')" % (MIN_YOY, rd)
        try:
            res = _get({"reportName": "RPT_PUBLIC_OP_NEWPREDICT", "columns": "SECURITY_CODE",
                        "pageNumber": 1, "pageSize": 1, "filter": f})
            n = int(res.get("count") or 0)
        except Exception as e:                                   # noqa: BLE001
            log("[warn] 探测 %s 失败：%s" % (rd, e))
            continue
        log("[period] %s 预告条数 %d" % (rd, n))
        if n >= MIN_ROWS:
            chosen = rd
            break
        if chosen is None:
            chosen = rd            # 兜底：即使少也用最近的，总比空池强
    if not chosen:
        log("[fatal] 无法确定报告期")
        return 1

    rd = chosen
    by_code, appoin, formal, listing, raw_rows = collect(rd, today, args.no_listing)
    cands, stats = build(rd, today, by_code, appoin, formal, listing, raw_rows)
    if not cands:
        log("[fatal] 候选池为空，拒绝覆盖旧快照")
        return 1

    items, core_n, watch_n = build_track(rd, today, cands, appoin, formal)
    core_total = sum(1 for c in cands if c["pool"] == "核心")
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rd_s = str(rd)
    qn = QNAME["%02d" % rd.month]

    cand_doc = {
        "meta": {
            "source": "eastmoney datacenter RPT_PUBLIC_OP_NEWPREDICT",
            "filter": "(ADD_AMP_LOWER>=%g)(REPORT_DATE='%s')" % (MIN_YOY, rd_s),
            "report_date": rd_s,
            "scan_time": now,
            "generator": "q3_track.py",
            "raw_rows": raw_rows,
            "dedup": len(by_code),
            "excluded_bse": stats["excluded_bse"],
            "excluded_st": stats["excluded_st"],
            "observation_turnaround": stats["observation_turnaround"],
            "kcfj_suspect": stats["kcfj_suspect"],
            "excluded_new_listing": stats["excluded_new_listing"],
            "candidate_total": len(cands),
            "core_total": core_total,
            "legend": {
                "pool=核心": "非扭亏、通过机械剔除，建议人工按行业景气/扣非复核后纳入",
                "pool=观察": "扭亏类型（一次性扭亏，非持续绩优），仅观察",
                "kcfj_suspect=1": "归母增速比扣非高 30 个百分点以上（无扣非预告时按非经损益关键词判定），扣非增速可能远低于归母",
                "new_listing=满1年(自动核)": "东财上市日期距报告期已满 1 年，自动确认",
                "new_listing=需F10核对": "上市日期接口未返回，退回人工 F10 核对",
            },
        },
        "candidates": cands,
    }

    track_doc = {
        "meta": {
            "generated": now,
            "built": now,
            "report_date": rd_s,
            "seed": False,
            "auto_suggested": True,
            "source": "eastmoney datacenter RPT_PUBLIC_OP_NEWPREDICT（全市场扫描 ≥%g%% 派生）" % MIN_YOY,
            "rules_version": "v3",
            "generator": "q3_track.py（周更 workflow 自动生成，无需人工重跑）",
            "note": ("由 weekly workflow 自动重算：候选池=全市场预告 ≥%g%% 经四层剔除；"
                     "跟踪池=核心池按预告增速取前 %d + 扭亏观察池。"
                     "扣非增速直接取预告 005 口径，缺失时按非经损益关键词标注存疑。"
                     % (MIN_YOY, CORE_TOP_N)),
            "window": window_text(rd),
            "market_filter": "已剔除北交所/ST/上市不满1年；扭亏→观察池",
            "legend": {
                "industry_dir": "AI算力/光通信 · 半导体设备 · 稀土/资源 · 化工涨价 · PCB/消费电子 · 医药 · 其他（名称关键词粗判，辅助筛选）",
                "report_type": "预告=仅有业绩预告；正式=已出正式财报",
                "nonrecurring_risk": "low=扣非与归母基本匹配；mid=归母显著高于扣非（或含非经损益关键词），需复核",
                "new_listing": "满1年(自动核)=按东财上市日期确认；需F10核对=接口无数据",
                "core": "true=核心池(非扭亏、通过硬性排除)；false=观察池(扭亏)",
            },
            "auto_suggest": {
                "top_n_core": CORE_TOP_N,
                "core_picked": core_n,
                "watch_picked": watch_n,
                "core_total": core_total,
            },
            "today": str(today),
        },
        "summary": {
            "total": len(items),
            "core_formal": sum(1 for i in items if i["core"] and i["disclosure_status"] == "已出正式"),
            "core_forecast": sum(1 for i in items if i["core"] and i["disclosure_status"] != "已出正式"),
            "watch": sum(1 for i in items if not i["core"]),
            "excluded": stats["excluded_bse"] + stats["excluded_st"] + stats["excluded_new_listing"],
        },
        "items": items,
    }

    log("[result] 报告期=%s(%s) 原始=%d 去重=%d → 候选=%d(核心 %d) · 跟踪=%d(核心 %d / 观察 %d)"
        % (rd_s, qn, raw_rows, len(by_code), len(cands), core_total, len(items), core_n, watch_n))
    log("[result] 剔除：北交所 %d · ST %d · 上市不满1年 %d · 扭亏观察 %d · 扣非存疑 %d"
        % (stats["excluded_bse"], stats["excluded_st"], stats["excluded_new_listing"],
           stats["observation_turnaround"], stats["kcfj_suspect"]))

    if args.dry_run:
        log("[dry-run] 不写文件")
        return 0

    # 原子写：两个文件都成功生成后才替换，避免半新半旧
    tmp_c, tmp_t = OUT_CAND + ".tmp", OUT_TRACK + ".tmp"
    with open(tmp_c, "w", encoding="utf-8") as f:
        json.dump(cand_doc, f, ensure_ascii=False, separators=(",", ":"))
    with open(tmp_t, "w", encoding="utf-8") as f:
        json.dump(track_doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_c, OUT_CAND)
    os.replace(tmp_t, OUT_TRACK)
    log("[write] %s / %s" % (os.path.basename(OUT_CAND), os.path.basename(OUT_TRACK)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
