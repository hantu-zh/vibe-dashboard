# -*- coding: utf-8 -*-
"""
威震天 · 涨停后中继缩量回踩 —— 每日冻结快照生成器

规则（源自 strong.html 内的通达信条件选股公式）：
  ZT  := (C/REF(C,1) >= 1.095 AND C = H) OR (C/REF(C,1) >= 1.195 AND C = H)   # 涨停(兼容10%/20%)
  N   := BARSLAST(ZT)                     # 距最近一次涨停的天数
  LTP := REF(C, N)                        # 涨停价
  COND_DIST  := N >= 3 AND N <= 8
  COND_PRICE := C > MA(C,10) AND C < LTP
  COND_MA    := MA10>REF(MA10,1) AND MA25>REF(MA25,1) AND MA25>=REF(MA25,5) AND MA10>=MA25
  COND_UNDER := C < MA(C,25) * 1.08
  COND_TURN(等价量能比口径) := V/VZT in [0.45,0.80] AND 1.5<HS<12 AND V < MA(V,20)*1.5

数据源（沙箱/CI 均可达；东财 push2his 与 腾讯 web.ifzq 已被拦，故不用）：
  - 全A股列表 + 流通市值：新浪 hs_a
  - 个股日K：新浪 getKLineData（主）→ 同花顺 d.10jqka.com.cn（兜底）

输出：
  - weizhentian_history.json  近 5 个交易日冻结快照（页面消费）
  - weizhentian.json          最新一日选股（兼容旧格式）

用法：
  python gen_weizhentian.py
  LIMIT=300 python gen_weizhentian.py            # 调试
  TARGET_DATE=20260911 python gen_weizhentian.py # 单日回归校验
"""
import os
import re
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

SINA_LIST = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
             "Market_Center.getHQNodeData")
SINA_KLINE = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
              "CN_MarketData.getKLineData")
THS_KLINE = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"

SINA_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
          "Referer": "https://finance.sina.com.cn/"}
THS_H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
         "Referer": "https://stockpage.10jqka.com.cn/"}

WINDOW_DAYS = 5
LIMIT = int(os.environ.get("LIMIT", "0"))
TARGET_DATE = os.environ.get("TARGET_DATE", "").strip()
WORKERS = int(os.environ.get("WORKERS", "12"))
MIN_COVERAGE = float(os.environ.get("MIN_COVERAGE", "0.70"))  # 覆盖率低于此值则认为数据不可靠
OUT_HISTORY = "weizhentian_history.json"
OUT_LATEST = "weizhentian.json"


def http_get(url, headers, enc="utf-8", tries=5, timeout=15):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            return urllib.request.urlopen(req, timeout=timeout).read().decode(enc, "replace")
        except Exception:
            time.sleep(0.3 + 0.35 * k)
    return None


# ---------- 数据源 ----------
def fetch_universe():
    stocks = []
    page = 1
    while True:
        url = (f"{SINA_LIST}?page={page}&num=100&sort=symbol&asc=1&node=hs_a"
               f"&symbol=&_s_r_a=page")
        raw = http_get(url, SINA_H, tries=6)
        if not raw:
            break
        try:
            arr = json.loads(raw)
        except Exception:
            break
        if not arr:
            break
        stocks.extend(arr)
        if len(arr) < 100:
            break
        page += 1
    out = []
    for s in stocks:
        sym = (s.get("symbol") or "").strip()
        code6 = (s.get("code") or "").strip()
        if not sym or not code6 or sym.startswith("bj"):
            continue  # 北交所 30% 涨跌幅不适用本公式
        try:
            trade = float(s.get("trade") or 0)
            nmc = float(s.get("nmc") or 0)
        except Exception:
            trade, nmc = 0.0, 0.0
        out.append({
            "symbol": sym, "code": code6,
            "name": (s.get("name") or "").strip(),
            "float_shares": (nmc * 10000.0 / trade) if trade > 0 else 0.0,
        })
    return out


def _rows_from_sina(sym):
    raw = http_get(f"{SINA_KLINE}?symbol={sym}&scale=240&ma=no&datalen=140", SINA_H)
    if not raw:
        return None
    try:
        arr = json.loads(raw)
    except Exception:
        return None
    rows = []
    for r in arr:
        try:
            rows.append({"date": str(r["day"]).replace("-", ""),
                         "open": float(r["open"]), "high": float(r["high"]),
                         "low": float(r["low"]), "close": float(r["close"]),
                         "vol": float(r["volume"])})
        except Exception:
            continue
    return rows or None


def _rows_from_ths(code6):
    raw = http_get(THS_KLINE.format(code=code6), THS_H)
    if not raw:
        return None
    m = re.search(r"last\((.*)\)\s*;?\s*$", raw.strip(), re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1))
        data = obj.get("data") or ""
    except Exception:
        return None
    rows = []
    for line in data.split(";"):
        if not line:
            continue
        p = line.split(",")
        if len(p) < 6:
            continue
        try:
            # 同花顺列序：[date, open, HIGH, low, close, volume, ...]
            rows.append({"date": p[0], "open": float(p[1]), "high": float(p[2]),
                         "low": float(p[3]), "close": float(p[4]), "vol": float(p[5])})
        except Exception:
            continue
    return rows or None


def fetch_kline(stock):
    """同花顺(不复权01) 主 → 新浪兜底，返回 (rows, src)"""
    rows = _rows_from_ths(stock["code"])
    if rows:
        return rows, "ths"
    rows = _rows_from_sina(stock["symbol"])
    if rows:
        return rows, "sina"
    return None, None


# ---------- 指标 / 选股 ----------
def sma(vals, i, n):
    if i + 1 < n or i < 0:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def evaluate(rows, i, float_shares, symbol, name):
    if i < 26:
        return None
    closes = [r["close"] for r in rows]
    highs = [r["high"] for r in rows]
    vols = [r["vol"] for r in rows]

    zt_idx = None
    for j in range(i, max(-1, i - 40), -1):
        if j - 1 < 0:
            continue
        prev = closes[j - 1]
        if prev <= 0:
            continue
        ratio = closes[j] / prev
        near_high = closes[j] >= highs[j] - max(1e-6, highs[j] * 1e-4)
        if (ratio >= 1.095 or ratio >= 1.195) and near_high:
            zt_idx = j
            break
    if zt_idx is None:
        return None
    n = i - zt_idx
    if not (3 <= n <= 8):
        return None

    LTP = closes[zt_idx]
    VZT = vols[zt_idx]
    if VZT <= 0:
        return None
    C = closes[i]
    V = vols[i]
    ma10 = sma(closes, i, 10)
    ma10p = sma(closes, i - 1, 10)
    ma25 = sma(closes, i, 25)
    ma25p = sma(closes, i - 1, 25)
    ma25_5 = sma(closes, i - 5, 25)
    ma20v = sma(vols, i, 20)
    if None in (ma10, ma10p, ma25, ma25p, ma25_5, ma20v):
        return None
    if not (C > ma10 and C < LTP):
        return None
    if not (ma10 > ma10p and ma25 > ma25p and ma25 >= ma25_5 and ma10 >= ma25):
        return None
    if not (C < ma25 * 1.08):
        return None
    vr = V / VZT
    if not (0.45 <= vr <= 0.80):
        return None
    if not (V < ma20v * 1.5):
        return None
    hs = (V / float_shares * 100.0) if float_shares > 0 else None
    if hs is not None and not (1.5 < hs < 12):
        return None
    return {"code": symbol, "name": name, "n": int(n), "close": round(C, 2),
            "zt_price": round(LTP, 2), "ma10": round(ma10, 2), "ma25": round(ma25, 2),
            "turnover": round(hs, 2) if hs is not None else 0.0,
            "turnover_ratio": round(vr, 2), "volume": int(V), "ma20vol": int(ma20v)}


def main():
    print("[1/4] 拉取全A股列表 ...", flush=True)
    uni = fetch_universe()
    if LIMIT:
        uni = uni[:LIMIT]
    print(f"      股票数: {len(uni)}", flush=True)
    if not uni:
        print("::error:: 无法获取股票列表", flush=True)
        raise SystemExit(2)

    print("[2/4] 拉取日K (新浪主/同花顺兜底) ...", flush=True)
    data = {}
    miss = []
    src_cnt = Counter()
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_kline, s): s for s in uni}
        for f in as_completed(futs):
            s = futs[f]
            try:
                rows, src = f.result()
            except Exception:
                rows, src = None, None
            done += 1
            if rows:
                data[s["symbol"]] = {"rows": rows, "name": s["name"], "float": s["float_shares"]}
                src_cnt[src] += 1
            else:
                miss.append(s["symbol"])
            if done % 500 == 0:
                print(f"      {done}/{len(uni)} 有效 {len(data)} (失败 {len(miss)})", flush=True)
    cov = len(data) / max(1, len(uni))
    print(f"      有效个股K线: {len(data)}/{len(uni)}  覆盖率 {cov:.1%}  来源 {dict(src_cnt)}", flush=True)
    if miss[:10]:
        print("      失败样例:", miss[:10], flush=True)
    if cov < MIN_COVERAGE:
        print(f"::error:: 覆盖率 {cov:.1%} < {MIN_COVERAGE:.0%}，数据不可靠，放弃提交", flush=True)
        raise SystemExit(2)

    cnt = Counter()
    for v in data.values():
        for r in v["rows"]:
            cnt[r["date"]] += 1
    thr = max(2, int(len(data) * 0.3))
    cal = sorted([d for d, c in cnt.items() if c >= thr])
    if not cal:
        print("::error:: 无交易日历", flush=True)
        raise SystemExit(2)
    print(f"      交易日历末尾: {cal[-8:]}", flush=True)

    window = [TARGET_DATE] if TARGET_DATE else cal[-WINDOW_DAYS:]

    print("[3/4] 计算威震天 ...", flush=True)
    hist = {}
    for dt in window:
        picks = []
        for sym, v in data.items():
            rows = v["rows"]
            idx = next((k for k, r in enumerate(rows) if r["date"] == dt), None)
            if idx is None:
                continue
            p = evaluate(rows, idx, v["float"], sym, v["name"])
            if p:
                picks.append(p)
        picks.sort(key=lambda x: (x["n"], x["code"]))
        hist[dt] = picks
        print(f"      {dt}: {len(picks)} 只 -> " + ", ".join(p["code"] for p in picks[:15]), flush=True)

    out_hist = {"generated": window[-1], "window_days": len(window),
                "dates": window, "data": hist}
    with open(OUT_HISTORY, "w", encoding="utf-8") as f:
        json.dump(out_hist, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_LATEST, "w", encoding="utf-8") as f:
        json.dump({"date": window[-1], "picks": hist.get(window[-1], [])},
                  f, ensure_ascii=False, indent=1)
    print(f"[4/4] 写出 {OUT_HISTORY} / {OUT_LATEST}  (generated={window[-1]})", flush=True)


if __name__ == "__main__":
    main()
