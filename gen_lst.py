# -*- coding: utf-8 -*-
"""
LST · 周线 LST 刚突破 —— 每日冻结快照生成器

选股公式（通达信条件选股，周线口径）：
  N     := 55
  周HH   := HHV(H#WEEK, N)
  周LL   := LLV(L#WEEK, N)
  周LST  := (周HH + 周LL) / 2
  周突破 := C#WEEK > 周LST AND REF(C#WEEK,1) <= REF(周LST,1)
  周溢价 := C#WEEK <= 周LST * 1.03
  股价条件 := C < 20
  去ST   := NOT(NAMELIKE('ST') OR NAMELIKE('*ST') OR NAMELIKE('退'))
  去停牌 := DYNAINFO(4) > 0
  XG     := 周突破 AND 周溢价 AND 股价条件 AND 去ST AND 去停牌 AND C < 26

白话：取最近 55 周的高低中点 LST 作为「长期成本中枢」，当本周周收盘首次站上 LST
（上周还在下方）、且溢价不超过 3%、现价低于 20 元时触发。属于「长期横盘后的周线突破」形态。

数据口径说明：
  - H#WEEK / L#WEEK / C#WEEK 由日K按「周一开始、周五结束」聚合成周K得到；
    当前（未完成）周为部分周，其 C#WEEK = 截至该交易日的最新收盘，
    与通达信在盘中/收盘后运行该公式的口径一致。
  - 数据源（沙箱/CI 均可达；东财 push2his 与 腾讯 web.ifzq 已被拦，故不用）：
      · 全A股列表 + 现价：新浪 hs_a
      · 个股日K：新浪 getKLineData（主，datalen=340）→ 同花顺 d.10jqka.com.cn（兜底，140 根）
        注：同花顺兜底仅 140 根（≈28 周 < 55 周窗口），不足 55 周历史的个股将被跳过，
        不影响主源（新浪）覆盖的标的。

输出：
  - lst_history.json   近 5 个交易日冻结快照（页面消费）
  - lst.json           最新一日选股（兼容旧格式）

用法：
  python gen_lst.py
  LIMIT=300 python gen_lst.py            # 调试
  TARGET_DATE=20260916 python gen_lst.py # 单日回归校验
"""
import os
import re
import json
import time
import urllib.request
from datetime import datetime, timedelta
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
N_WEEK = 55                       # LST 窗口（周）
DAILY_LEN = 340                   # 日K长度：≈68 周，足以支撑 55 周窗口
LIMIT = int(os.environ.get("LIMIT", "0"))
TARGET_DATE = os.environ.get("TARGET_DATE", "").strip()
WORKERS = int(os.environ.get("WORKERS", "12"))
MIN_COVERAGE = float(os.environ.get("MIN_COVERAGE", "0.70"))  # 覆盖率低于此值则认为数据不可靠
OUT_HISTORY = "lst_history.json"
OUT_LATEST = "lst.json"
KLINES_EMBED = 40                 # 内嵌到每个 pick 的日K根数（供 K 线弹窗）


def http_get(url, headers, enc="utf-8", tries=5, timeout=15):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers)
            return urllib.request.urlopen(req, timeout=timeout).read().decode(enc, "replace")
        except Exception:
            time.sleep(0.3 + 0.35 * k)
    return None


# ---------- 数据源 ----------
def _name_is_st(name):
    return bool(re.search(r"(^|\s)(ST|\*ST|退)", name or ""))


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
            continue  # 北交所 30% 涨跌幅，本公式按沪深口径，跳过
        try:
            trade = float(s.get("trade") or 0)
        except Exception:
            trade = 0.0
        name = (s.get("name") or "").strip()
        # 预筛：LST 要求现价 C<26 且非 ST/退市/停牌
        if trade <= 0:
            continue                # 停牌（无现价）
        if trade >= 26:
            continue                # 不满足 C<26
        if _name_is_st(name):
            continue
        out.append({"symbol": sym, "code": code6, "name": name})
    return out


def _rows_from_sina(sym):
    raw = http_get(f"{SINA_KLINE}?symbol={sym}&scale=240&ma=no&datalen={DAILY_LEN}", SINA_H)
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
    """新浪(主) → 同花顺(兜底)，返回 (rows, src)"""
    rows = _rows_from_sina(stock["symbol"])
    if rows:
        return rows, "sina"
    rows = _rows_from_ths(stock["code"])
    if rows:
        return rows, "ths"
    return None, None


def sina_symbol(code):
    """6 开头 -> sh；0/3 开头 -> sz（北交所已预筛跳过）。
    兼容已带 sh/sz 前缀的输入（快照里的 code 即为带前缀符号）。"""
    c = code
    if c.startswith(("sh", "sz", "bj")):
        c = c[2:]
    if c.startswith("6"):
        return "sh" + c
    if c.startswith(("0", "3")):
        return "sz" + c
    return "sh" + c


def fetch_current_close(symbol):
    """取个股最新收盘价（datalen=2，仅取末根 close），用于回填选出后涨跌幅。"""
    raw = http_get(f"{SINA_KLINE}?symbol={symbol}&scale=240&ma=no&datalen=2", SINA_H)
    if not raw:
        return None
    try:
        arr = json.loads(raw)
    except Exception:
        return None
    if not arr:
        return None
    try:
        return float(arr[-1]["close"])
    except Exception:
        return None


def enrich_history(hist, run_date):
    """回填每个 pick：累计天数 days_since、最新价 cur_close、选出后涨跌幅 post_change_pct。
    hist: {日期: [pick,...]}；run_date: 'YYYYMMDD'（通常为运行当日）。"""
    codes = set()
    for picks in hist.values():
        for p in picks:
            codes.add(p["code"])
    sym = {c: sina_symbol(c) for c in codes}
    close_map = {}
    def _get(c):
        return c, fetch_current_close(sym[c])
    with ThreadPoolExecutor(max_workers=max(4, WORKERS)) as ex:
        for c, cl in ex.map(_get, list(codes)):
            if cl is not None:
                close_map[c] = cl
    rd = datetime.strptime(run_date, "%Y%m%d")
    for dt, picks in hist.items():
        try:
            dd = datetime.strptime(dt, "%Y%m%d")
        except Exception:
            dd = rd
        days = (rd - dd).days
        for p in picks:
            cur = close_map.get(p["code"])
            p["days_since"] = days
            if cur is not None and p.get("daily_close"):
                p["cur_close"] = round(cur, 2)
                p["post_change_pct"] = round((cur - p["daily_close"]) / p["daily_close"] * 100.0, 2)
            else:
                p["cur_close"] = None
                p["post_change_pct"] = None
    return hist


# ---------- 日K -> 周K 聚合（周一起、周五止） ----------
def _monday(d):
    dt = datetime.strptime(d, "%Y%m%d")
    return (dt - timedelta(days=dt.weekday())).strftime("%Y%m%d")


def aggregate_weekly(rows):
    """按周一分组聚合成周K，返回按时间升序的周列表。
    每根周K: {date(该周最后交易日), open, high, low, close, vol}"""
    weeks = {}
    for r in rows:
        key = _monday(r["date"])
        w = weeks.get(key)
        if w is None:
            weeks[key] = {"mon": key, "first": r["date"], "last": r["date"],
                          "open": r["open"], "high": r["high"], "low": r["low"],
                          "close": r["close"], "vol": r["vol"]}
        else:
            w["last"] = r["date"]
            w["high"] = max(w["high"], r["high"])
            w["low"] = min(w["low"], r["low"])
            w["close"] = r["close"]
            w["vol"] += r["vol"]
    out = sorted(weeks.values(), key=lambda x: x["mon"])
    return out


# ---------- 指标 / 选股 ----------
def evaluate(rows, dt, name):
    """在交易日 dt 收盘后评估 LST 公式。rows 为截至 dt 的日K（升序）。"""
    if not rows:
        return None
    # 截止 dt 的日K
    sub = [r for r in rows if r["date"] <= dt]
    if len(sub) < 5:
        return None
    weekly = aggregate_weekly(sub)
    W = len(weekly)
    if W < N_WEEK + 1:        # 需要至少 56 根周K（索引 0..55）
        return None
    highs = [w["high"] for w in weekly]
    lows = [w["low"] for w in weekly]
    closes = [w["close"] for w in weekly]
    vols = [w["vol"] for w in weekly]

    # 每根周K 的 LST（55 周窗口，含自身）
    lst = [None] * W
    for k in range(N_WEEK - 1, W):
        lo = k - (N_WEEK - 1)
        lst[k] = (max(highs[lo:k + 1]) + min(lows[lo:k + 1])) / 2.0

    w = W - 1                  # 本周（部分周）
    cw = closes[w]
    cw1 = closes[w - 1]
    lst_w = lst[w]
    lst_w1 = lst[w - 1]
    if lst_w is None or lst_w1 is None:
        return None

    # 周突破：本周收盘站上 LST，上周在下方
    周突破 = (cw > lst_w) and (cw1 <= lst_w1)
    # 周溢价：不超 3%
    周溢价 = cw <= lst_w * 1.03
    if not (周突破 and 周溢价):
        return None

    daily_close = cw           # 本周收盘 = dt 当日收盘（部分周）
    if daily_close >= 26:
        return None
    if daily_close >= 20:
        return None
    if vols[w] <= 0:           # 停牌
        return None

    premium_pct = (cw / lst_w - 1.0) * 100.0
    # 内嵌日K（供弹窗），取截止 dt 的最近 KLINES_EMBED 根
    emb = sub[-KLINES_EMBED:]
    kline = [{"d": r["date"][:4] + "-" + r["date"][4:6] + "-" + r["date"][6:],
              "o": round(r["open"], 2), "h": round(r["high"], 2),
              "l": round(r["low"], 2), "c": round(r["close"], 2),
              "v": int(r["vol"])} for r in emb]

    return {"week_close": round(cw, 2), "lst": round(lst_w, 2),
            "premium_pct": round(premium_pct, 2),
            "daily_close": round(daily_close, 2),
            "kline": kline}


def main():
    print("[1/4] 拉取全A股列表（预筛 ST/停牌/现价>=26）...", flush=True)
    uni = fetch_universe()
    if LIMIT:
        uni = uni[:LIMIT]
    print(f"      候选股票数: {len(uni)}", flush=True)
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
                # 预筛历史长度：至少 56 周需 ≈ N_WEEK*5 根日K
                data[s["symbol"]] = {"rows": rows, "name": s["name"]}
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

    # 交易日历（按日K出现频次）
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

    print("[3/4] 计算 LST 周线突破 ...", flush=True)
    hist = {}
    for dt in window:
        picks = []
        for sym, v in data.items():
            p = evaluate(v["rows"], dt, v["name"])
            if p:
                picks.append({"code": sym, "name": v["name"], **p})
        picks.sort(key=lambda x: (x["premium_pct"], x["daily_close"]))
        hist[dt] = picks
        print(f"      {dt}: {len(picks)} 只 -> " + ", ".join(p["code"] for p in picks[:15]), flush=True)

    # 回填：选出后的涨跌幅 / 累计天数（相对运行当日）
    print("[3.5/4] 回填选出后涨跌幅 / 累计天数 ...", flush=True)
    enrich_history(hist, datetime.now().strftime("%Y%m%d"))

    out_hist = {"generated": window[-1], "window_days": len(window),
                "formula": "LST(N=55): 周LST=(HHV(H#WEEK,55)+LLV(L#WEEK,55))/2; 周突破=C#WEEK>周LST AND REF(C#WEEK,1)<=REF(周LST,1); 周溢价=C#WEEK<=周LST*1.03; XG=周突破 AND 周溢价 AND C<20 AND 去ST AND 去停牌 AND C<26",
                "dates": window, "data": hist}
    with open(OUT_HISTORY, "w", encoding="utf-8") as f:
        json.dump(out_hist, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_LATEST, "w", encoding="utf-8") as f:
        json.dump({"date": window[-1], "picks": hist.get(window[-1], [])},
                  f, ensure_ascii=False, indent=1)
    print(f"[4/4] 写出 {OUT_HISTORY} / {OUT_LATEST}  (generated={window[-1]})", flush=True)


if __name__ == "__main__":
    main()
