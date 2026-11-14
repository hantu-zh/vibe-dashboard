#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 资金观测台 —— 数据采集

数据来源（全部免费、无需 Key）：
  1. 东方财富数据中心  RPT_FUND_ETF_SHARECHANGE   逐日 ETF 总份额（权益变动口径）
  2. 新浪财经 K 线     日频收盘价 / 成交量（成交额 = 成交量 × 均价估算）
  3. 腾讯行情 qt.gtimg.cn  当日精确成交额、换手率、涨跌幅、份额快照
  4. 腾讯分时 ifzq.gtimg.cn 当日分钟成交额（累计额差分）

输出：web/data/snapshot.json
用法：python scripts/collect.py [--days 260] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 允许外部指定配置 / 输出 / 缓存位置：vibe-dashboard 仓库里本脚本放在 etf_flow/，
# 产物要写到 etf/data/snapshot.json，缓存落在仓库外，避免污染提交。
CONFIG = Path(os.environ.get("ETF_CONFIG", ROOT / "config" / "universe.json"))
OUT = Path(os.environ.get("ETF_OUT", ROOT / "web" / "data" / "snapshot.json"))
CACHE = Path(os.environ.get("ETF_CACHE_DIR", ROOT / ".cache"))

CST = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

PACING = float(os.environ.get("PACING", "0.12"))
TTL = float(os.environ.get("CACHE_TTL", "21600"))  # 缓存有效期（秒），默认 6 小时


def cached(name: str, producer):
    """磁盘缓存，便于反复调试时不必重抓。--refresh 可强制刷新。"""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}.json"
    if not os.environ.get("REFRESH") and path.exists():
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - blob.get("at", 0) < TTL and blob.get("v") == 1:
                print(f"  (cache) {name}")
                return blob["data"]
        except Exception:  # noqa: BLE001
            pass
    data = producer()
    path.write_text(json.dumps({"v": 1, "at": time.time(), "data": data}, ensure_ascii=False),
                    encoding="utf-8")
    return data


# ---------------------------------------------------------------- HTTP

def http_get(url: str, referer: str = "", timeout: int = 25, tries: int = 3) -> str:
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            time.sleep(PACING)
            return data.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.6 * (i + 1))
    raise RuntimeError(f"GET failed: {url[:110]} -> {type(last).__name__}: {last}")


def num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 份额

def fetch_shares(codes: list[str], need_days: int) -> dict[str, dict[str, float]]:
    """逐日总份额（单位：份）。批量 + 分页，每页 500 行。"""
    out: dict[str, dict[str, float]] = {c: {} for c in codes}
    BATCH = 10
    for i in range(0, len(codes), BATCH):
        group = codes[i:i + BATCH]
        flt = '(SECURITY_CODE in ("' + '","'.join(group) + '"))'
        page, got_per_code = 1, {c: 0 for c in group}
        while page <= 60:
            url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
                   "?reportName=RPT_FUND_ETF_SHARECHANGE&columns=ALL"
                   f"&filter={urllib.parse.quote(flt)}"
                   f"&pageSize=500&pageNumber={page}"
                   "&sortColumns=CHANGE_DATE&sortTypes=-1")
            try:
                d = json.loads(http_get(url, "https://data.eastmoney.com/"))
            except Exception as e:  # noqa: BLE001
                print(f"  ! shares page fail {group[0]}..: {e}", file=sys.stderr)
                break
            res = d.get("result")
            if not res or not res.get("data"):
                break
            for row in res["data"]:
                code = row.get("SECURITY_CODE")
                day = (row.get("CHANGE_DATE") or "")[:10]
                share = num(row.get("TOTAL_SHARE"))
                if not code or not day or share is None or share <= 0:
                    continue
                if code in out and day not in out[code]:
                    out[code][day] = share * 10000.0  # 万份 -> 份
                    got_per_code[code] = got_per_code.get(code, 0) + 1
            if all(v >= need_days for v in got_per_code.values()):
                break
            if len(res["data"]) < 500:
                break
            page += 1
        print(f"  shares {i + len(group)}/{len(codes)}")
    return out


# ---------------------------------------------------------------- 日线

def fetch_daily(code: str, market: str, days: int) -> list[dict]:
    """新浪日线：收盘价 + 成交量（股）。成交额用 (O+H+L+C)/4 估算。"""
    sym = ("sh" if market == "SH" else "sz") + code
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale=240&datalen={max(days, 60)}")
    for _ in range(2):
        try:
            raw = http_get(url, "https://finance.sina.com.cn/")
            arr = json.loads(raw)
            rows = []
            for r in arr:
                o, h, l, c = num(r.get("open")), num(r.get("high")), num(r.get("low")), num(r.get("close"))
                v = num(r.get("volume"))
                if None in (o, h, l, c, v):
                    continue
                rows.append({
                    "d": r["day"][:10],
                    "close": c,
                    "amount": v * ((o + h + l + c) / 4.0),
                    "volume": v,
                })
            if rows:
                return rows
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    # 兜底：腾讯日线（最多 640 根，只有成交量）
    try:
        url = ("https://ifzq.gtimg.cn/appstock/app/fqkline/get"
               f"?param={sym},day,,,{min(max(days, 60), 640)},qfq")
        d = json.loads(http_get(url, "https://gu.qq.com/"))
        node = d["data"][sym]
        arr = node.get("qfqday") or node.get("day") or []
        rows = []
        for r in arr:
            o, c, h, l, v = num(r[1]), num(r[2]), num(r[3]), num(r[4]), num(r[5])
            if None in (o, h, l, c, v):
                continue
            rows.append({"d": r[0][:10], "close": c,
                         "amount": v * 100 * ((o + h + l + c) / 4.0), "volume": v * 100})
        return rows
    except Exception:  # noqa: BLE001
        return []


def fetch_index(days: int) -> dict[str, float]:
    try:
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol=sh000001&scale=240&datalen={max(days, 60)}")
        arr = json.loads(http_get(url, "https://finance.sina.com.cn/"))
        return {r["day"][:10]: num(r["close"]) for r in arr if num(r.get("close"))}
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 报价

QUOTE_FIELDS = {"price": 3, "prev": 4, "volume_hand": 6, "date": 30,
                "change": 31, "pct": 32, "high": 33, "low": 34,
                "amount_wan": 37, "turnover": 38, "share": 72}


def fetch_quotes(codes: list[tuple[str, str]]) -> dict[str, dict]:
    """腾讯报价（GBK）。返回 {code: {...}}，含当日精确成交额与份额快照。"""
    out: dict[str, dict] = {}
    for i in range(0, len(codes), 40):
        group = codes[i:i + 40]
        q = ",".join(("sh" if m == "SH" else "sz") + c for c, m in group)
        try:
            req = urllib.request.Request("https://qt.gtimg.cn/q=" + q, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read().decode("gbk", "replace")
        except Exception as e:  # noqa: BLE001
            print(f"  ! quote fail: {e}", file=sys.stderr)
            continue
        for line in raw.strip().split("\n"):
            if '="' not in line:
                continue
            key = line.split("=")[0].replace("v_", "").strip()
            f = line.split('="', 1)[1].rstrip('";').split("~")
            code = key[2:]
            g = lambda k: num(f[QUOTE_FIELDS[k]]) if len(f) > QUOTE_FIELDS[k] else None  # noqa: E731
            amt = g("amount_wan")
            out[code] = {
                "price": g("price"), "prev": g("prev"),
                "amount": amt * 10000 if amt is not None else None,
                "pct": g("pct"), "turnover": g("turnover"),
                "share": g("share"),
                "date": (str(f[30])[:8] if len(f) > 30 else ""),
                "name": f[1] if len(f) > 1 else "",
            }
        time.sleep(PACING)
    return out


# ---------------------------------------------------------------- 分时

def fetch_minute(code: str, market: str) -> tuple[str, list[float]]:
    """腾讯当日分时：返回 (日期, 每分钟成交额列表)。累计额差分。"""
    sym = ("sh" if market == "SH" else "sz") + code
    try:
        d = json.loads(http_get(f"https://ifzq.gtimg.cn/appstock/app/minute/query?code={sym}",
                                "https://gu.qq.com/"))
        node = d["data"][sym]["data"]
        day = node.get("date", "")
        prev, series = 0.0, []
        for point in (node.get("data") or []):
            parts = str(point).split()
            if len(parts) < 4:
                continue
            cum = num(parts[3])
            if cum is None:
                continue
            series.append(max(0.0, cum - prev))
            prev = cum
        return day, series
    except Exception:  # noqa: BLE001
        return "", []


# ---------------------------------------------------------------- 计算

def rolling_mean(vals: list[float | None], n: int, idx: int) -> float | None:
    """idx 之前 n 个有效值的均值（不含 idx 自身）。"""
    out, j = [], idx - 1
    while j >= 0 and len(out) < n:
        if vals[j] is not None:
            out.append(vals[j])
        j -= 1
    if len(out) < max(5, n // 4):
        return None
    return sum(out) / len(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=260, help="历史交易日数量")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 只（调试）")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，强制重抓")
    args = ap.parse_args()
    if args.refresh:
        os.environ["REFRESH"] = "1"

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    funds = cfg["funds"][: args.limit] if args.limit else cfg["funds"]
    core = {s.split(".")[0] for s in cfg["core"]}
    codes = [f["code"] for f in funds]
    print(f"[1/5] 份额历史：{len(funds)} 只")

    shares = cached("shares", lambda: fetch_shares(codes, args.days // 2))

    print(f"[2/5] 日线：{len(funds)} 只")
    daily: dict[str, list[dict]] = cached(
        "daily",
        lambda: {f["code"]: r for f in funds
                 if (r := fetch_daily(f["code"], f["market"], args.days))},
    )

    print("[3/5] 当日报价")
    quotes = cached("quotes", lambda: fetch_quotes([(f["code"], f["market"]) for f in funds]))

    print("[4/5] 分时（核心池）")
    minute: dict[str, list[float]] = {}
    minute_day = ""
    for f in funds:
        if f["code"] not in core:
            continue
        d, s = fetch_minute(f["code"], f["market"])
        if s:
            minute[f["code"]] = s
            minute_day = minute_day or d
    print(f"  minute codes={len(minute)} day={minute_day}")

    print("[5/5] 计算")
    idx_close = cached("index", lambda: fetch_index(args.days))

    # 统一交易日轴：用上证指数日期（最可靠）
    dates = sorted(idx_close.keys())[-args.days:]
    if not dates:
        print("!! 无法取得交易日轴，终止", file=sys.stderr)
        return 1
    dset = set(dates)

    # 每只基金的日序列（收盘 / 成交额 / 份额 / 净申赎）
    per_fund: dict[str, dict] = {}
    for f in funds:
        code = f["code"]
        rows = {r["d"]: r for r in daily.get(code, []) if r["d"] in dset}
        sh = shares.get(code, {})
        seq = []
        prev_share = None
        for d in dates:
            r = rows.get(d)
            s = sh.get(d)
            close = r["close"] if r else None
            flow = None
            if s is not None and prev_share is not None and close is not None:
                ratio = s / prev_share if prev_share else 1.0
                # 份额拆分/合并等公司行为：份额突变而价格未同步变动，不计入申赎
                if not (ratio > 1.4 or ratio < 0.6):
                    flow = (s - prev_share) * close
            if s is not None:
                prev_share = s
            seq.append({"d": d, "close": close,
                        "amount": r["amount"] if r else None,
                        "share": s, "flow": flow})
        per_fund[code] = {"seq": seq, "bucket": f["bucket"], "name": f["name"],
                          "core": code in core}

    # 分类聚合
    cat_order = cfg["category_order"]
    cat_daily: dict[str, list[float | None]] = {c: [] for c in cat_order}
    cat_members: dict[str, int] = {c: 0 for c in cat_order}
    for c in cat_order:
        cat_members[c] = sum(1 for f in funds if f["bucket"] == c)

    for i, d in enumerate(dates):
        for c in cat_order:
            total, ok = 0.0, False
            for f in funds:
                if f["bucket"] != c:
                    continue
                v = per_fund[f["code"]]["seq"][i]["flow"]
                if v is not None:
                    total += v
                    ok = True
            cat_daily[c].append(total if ok else None)

    # 核心池成交额
    core_amount = []
    for i, d in enumerate(dates):
        total, ok = 0.0, False
        for f in funds:
            if f["code"] not in core:
                continue
            v = per_fund[f["code"]]["seq"][i]["amount"]
            if v is not None:
                total += v
                ok = True
        core_amount.append(total if ok else None)

    ma20 = [rolling_mean(core_amount, 20, i) if core_amount[i] is not None else None
            for i in range(len(dates))]
    ma60 = [rolling_mean(core_amount, 60, i) if core_amount[i] is not None else None
            for i in range(len(dates))]

    # 当日快照（用最新有成交额的交易日）
    live_idx = max((i for i in range(len(dates)) if core_amount[i]), default=len(dates) - 1)
    live_date = dates[live_idx]

    def pick(seq, idx, key):
        return seq[idx][key] if idx < len(seq) else None

    radar = []
    total_flow = 0.0
    for f in funds:
        if f["code"] not in core:
            continue
        seq = per_fund[f["code"]]["seq"]
        amt = pick(seq, live_idx, "amount")
        r20 = rolling_mean([s["amount"] for s in seq], 20, live_idx)
        r60 = rolling_mean([s["amount"] for s in seq], 60, live_idx)
        q = quotes.get(f["code"], {})
        flow = pick(seq, live_idx, "flow")
        radar.append({
            "code": f["code"], "name": f["name"], "bucket": f["bucket"],
            "amount": amt,
            "ratio20": (amt / r20) if (amt and r20) else None,
            "ratio60": (amt / r60) if (amt and r60) else None,
            "pct": q.get("pct"),
            "turnover": q.get("turnover"),
            "share": pick(seq, live_idx, "share"),
            "flow": flow,
        })
    radar.sort(key=lambda x: -(x["ratio20"] or 0))

    # 全池当日明细（Top 榜用）
    every = []
    for f in funds:
        seq = per_fund[f["code"]]["seq"]
        amt = pick(seq, live_idx, "amount")
        flow = pick(seq, live_idx, "flow")
        sh_now = pick(seq, live_idx, "share")
        sh_prev = None
        for j in range(live_idx - 1, -1, -1):
            if seq[j]["share"] is not None:
                sh_prev = seq[j]["share"]
                break
        q = quotes.get(f["code"], {})
        every.append({
            "code": f["code"], "name": f["name"], "bucket": f["bucket"],
            "amount": amt, "flow": flow, "pct": q.get("pct"),
            "share": sh_now,
            "share_chg": (sh_now - sh_prev) if (sh_now and sh_prev) else None,
            "turnover": q.get("turnover"),
        })
        if flow:
            total_flow += flow

    # 分时聚合（核心池）
    slots, intraday = [], []
    if minute:
        n = max(len(s) for s in minute.values())
        slots = list(range(n))
        run = 0.0
        for i in range(n):
            s = 0.0
            for series in minute.values():
                if i < len(series):
                    s += series[i]
            run += s
            intraday.append(run)

    # 校验：分时终值 vs 精确成交额
    audit = {}
    if intraday:
        exact = sum((quotes.get(f["code"], {}).get("amount") or 0) for f in funds if f["code"] in core)
        audit = {"minute_total": intraday[-1], "exact_total": exact,
                 "ratio": (intraday[-1] / exact) if exact else None}

    snapshot = {
        "schema": 1,
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "trade_date": live_date,
        "live_date": minute_day or live_date,
        "fund_count": len(funds),
        "core_count": len(radar),
        "market": {
            "core_amount": core_amount[live_idx],
            "ma20": ma20[live_idx],
            "ma60": ma60[live_idx],
            "ratio20": (core_amount[live_idx] / ma20[live_idx]) if (core_amount[live_idx] and ma20[live_idx]) else None,
            "ratio60": (core_amount[live_idx] / ma60[live_idx]) if (core_amount[live_idx] and ma60[live_idx]) else None,
            "total_flow": total_flow,
            "up_count": sum(1 for r in radar if (r["pct"] or 0) > 0),
            "inflow_cats": sum(1 for c in cat_order if (cat_daily[c][live_idx] or 0) > 0),
        },
        "radar": radar,
        "categories": [
            {"name": c, "members": cat_members[c],
             "flow": cat_daily[c][live_idx],
             "cum60": sum(v for v in cat_daily[c][-60:] if v is not None),
             "series": cat_daily[c][-130:]}
            for c in cat_order
        ],
        "history": {
            "dates": dates[-130:],
            "core_amount": core_amount[-130:],
            "ma20": ma20[-130:],
            "ma60": ma60[-130:],
            "index": [idx_close.get(d) for d in dates[-130:]],
        },
        "funds_today": every,
        "intraday": {"date": minute_day, "cum": intraday, "count": len(slots)},
        "audit": audit,
        "sources": ["东方财富数据中心(份额)", "新浪财经(日线)", "腾讯行情(报价/分时)"],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")

    size = OUT.stat().st_size / 1024
    print(f"\n完成 → {OUT}  ({size:.0f} KB)")
    print(f"  交易日 {live_date}  核心池成交额 {(core_amount[live_idx] or 0)/1e8:.1f} 亿"
          f"  量比20 {snapshot['market']['ratio20'] and round(snapshot['market']['ratio20'],2)}"
          f"  净申赎 {total_flow/1e8:+.1f} 亿")
    if audit:
        print(f"  分时校验 minute/exact = {audit['ratio'] and round(audit['ratio'],3)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
