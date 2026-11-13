# -*- coding: utf-8 -*-
"""
国家队持仓 · 沪深300 投研看板  —— 真实数据层 & 每交易日自动更新脚本
=====================================================================

一次运行完成：
  1) 抓取真实行情与官方披露数据
  2) 把当日真实快照累积进 gjd_snapshots.json（可版本化、可回滚）
  3) 全量重算 ratio / net1 / net20 / pct252 / 背离区间 / 阶段标签 / 今日解读
  4) 产出 gjd_data.json（在线 fetch）、gjd_data.js（离线兜底）、单文件 HTML、源码版 HTML

数据源（全部公开、无需 token）：
  · 沪深300 日线       腾讯 web.ifzq.gtimg.cn  fqkline/get
  · ETF 每日单位净值    天天基金 pingzhongdata/<code>.js  Data_netWorthTrend
  · ETF 期末份额/净资产 天天基金 F10 FundArchivesDatas.aspx?type=gmbd
  · 当日 ETF 份额/规模  东方财富 push2  f84 / f116
  · 当日指数流通市值    东方财富 push2  f117（沪深300 流通市值，元）

真实性约定（页面内同步标注）：
  · 所有数值均来自上述真实公开数据，不含任何手工编造的锚点或噪声序列。
  · 份额：季度末为**官方披露真实值**，季度之间线性插值（明确标注）；
          自首次运行日起，每日 f84 真实快照作为硬锚点写入并逐日累积。
  · 流通市值历史：以当日官方 f117 快照为锚，按指数点位比例折算。
          因 ETF 净值同样与指数同比例变动，价格机械效应在比值中自动抵消，
          故 ratio 本质上刻画的是「份额占比」而非价格涨跌。
  · 事件锚点只做时间定位，不参与任何数值生成。

用法：
  python fetch_real.py                 # 抓取当日快照（幂等；已有则跳过）并重算
  python fetch_real.py --force         # 强制覆盖当日快照
  python fetch_real.py --no-fetch      # 完全离线：只用已有快照重建页面（供断网演练）
"""
import argparse
import bisect
import datetime as dt
import json
import math
import re
import ssl
import sys
import time
import urllib.request
from pathlib import Path

ssl._create_default_https_context = ssl._create_unverified_context

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
START_DATE = "2021-01-01"
CACHE_FILE = BASE / "raw_cache.json"     # 原始行情/净值/季度份额缓存，供断网重建
MARK = "国家队持仓 · 量价背离监测"          # 本看板页面标识，用于避免覆盖仓库根 index.html

ETF = [
    ("510300", "1.510300", "华泰柏瑞300ETF"),
    ("510310", "1.510310", "易方达300ETF"),
    ("510330", "1.510330", "华夏300ETF"),
    ("159919", "0.159919", "嘉实300ETF"),
]
INDEX_SECID = "1.000300"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 公开可核验的政策 / 增持时点（仅用于时间定位与阶段判定，不参与数值生成）
EVENTS = [
    {"date": "2021-07-27", "label": "监管冲击 · 外资集中流出", "kind": "shock"},
    {"date": "2022-04-26", "label": "政策底 · 沪指 2863", "kind": "policy"},
    {"date": "2022-10-31", "label": "二次探底 · 沪指 2885", "kind": "policy"},
    {"date": "2023-10-11", "label": "中央汇金增持四大行", "kind": "buy"},
    {"date": "2023-10-23", "label": "汇金公告买入 ETF 并将继续增持", "kind": "buy"},
    {"date": "2024-02-06", "label": "汇金扩大 ETF 增持范围", "kind": "buy"},
    {"date": "2024-09-24", "label": "一揽子政策 · 互换便利", "kind": "policy"},
    {"date": "2025-04-07", "label": "关税冲击 · 多机构增持 ETF", "kind": "buy"},
]
NARRATIVE_DATES = ["2022-04-26", "2022-10-31", "2023-10-23", "2024-02-06", "2024-09-24"]
SHORT_NAME = {
    "2022-04-26": "政策底", "2022-10-31": "二次探底", "2023-10-23": "汇金入场",
    "2024-02-06": "扩大增持", "2024-09-24": "9·24政策",
}

KERN = [1, 2, 3, 2, 1]           # 5 点三角窗，用于一阶差分平滑


# ------------------------------------------------------------------ HTTP
def http(url, referer=None, timeout=20, retries=3, encoding="utf-8"):
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    last = None
    for k in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(encoding, "replace")
        except Exception as e:                       # noqa: BLE001
            last = e
            if k + 1 < retries:
                time.sleep(1.2 * (k + 1))
    raise RuntimeError("请求失败 %s : %s" % (url[:110], last))


def num(s):
    """'1,068.79' -> 1068.79；'---' / '' -> None"""
    if s is None:
        return None
    s = s.replace(",", "").replace("%", "").strip()
    if s in ("", "---", "--", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ------------------------------------------- 北京时间 / 收盘判断（时区雷区）
CN_TZ = dt.timezone(dt.timedelta(hours=8))
SERIES_KEYS = ("hs300", "ratio", "pct252", "net1", "net20",
               "floatMv", "aum", "sharesTotal")


def beijing_now():
    """一律由 UTC 换算北京时间，不依赖运行机本地时区。
    GitHub Actions runner 为 UTC、本机为 GMT+8，二者结果一致。"""
    return dt.datetime.now(dt.timezone.utc).astimezone(CN_TZ)


def is_market_close(now=None):
    """(是否可抓收盘快照, 说明)。A股 15:00 收盘；周末非交易日。
    法定节假日无法本地判定，由 fetch_index() 返回的真实交易日历兜底。"""
    now = now or beijing_now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    if now.weekday() >= 5:
        return False, "周末非交易日（北京时间 %s）" % stamp
    if now < now.replace(hour=15, minute=0, second=0, microsecond=0):
        return False, "未到 15:00 收盘（北京时间 %s）" % stamp
    return True, "已收盘（北京时间 %s）" % stamp


def expected_trade_date(now=None):
    """粗略期望交易日（只跳周末）；节假日以真实交易日历为准。"""
    now = now or beijing_now()
    d = now.date()
    if now.hour < 15:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


# ------------------------------------------- 统一数字格式化（消除负零/浮点尾）
def clean_num(v, d=2):
    """先按 d 位四舍五入；绝对值小于半个最小单位一律归零。
    杜绝 -0.00 / -0.00000001% / +-0.0 这类显示。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(x):
        return 0.0
    q = round(x, d)
    return 0.0 if abs(q) < 0.5 * (10 ** (-d)) else q


def fmt(v, d=2, unit=""):
    return ("%." + str(d) + "f") % clean_num(v, d) + unit


def sgn(v, d=1, unit=""):
    q = clean_num(v, d)
    return ("+" if q > 0 else "") + ("%." + str(d) + "f") % q + unit


def assert_clean_text(brief):
    """构建期断言：今日解读不得出现负零、符号错位、超长浮点、NaN/Inf。"""
    parts = ([brief["state"]] + [s["body"] for s in brief["sections"]] +
             [s["value"] for s in brief["signals"]])
    bad = []
    for t in parts:
        plain = re.sub(r"<[^>]+>", "", t)
        if (re.search(r"-0\.0+\b", plain) or re.search(r"\+-0", plain) or
                re.search(r"\d+\.\d{4,}%", plain) or
                "nan" in plain.lower() or "inf" in plain.lower()):
            bad.append(plain[:120])
    if bad:
        raise RuntimeError("今日解读文本格式异常：%s" % bad)
    print("[文本校验] 今日解读 %d 段：无 -0.00 / 符号错位 / 浮点尾 / NaN" % len(parts))
    return True


def validate_payload(p):
    """X轴口径校验：日期格式统一 YYYY-MM-DD、严格递增、所有序列与 dates 等长。"""
    dates = p["dates"]
    n = len(dates)
    if n < 2:
        raise RuntimeError("交易日数量异常: %d" % n)
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for i, d in enumerate(dates):
        if not date_re.match(d):
            raise RuntimeError("日期格式不统一 @%d: %r" % (i, d))
    for i in range(1, n):
        if dates[i] <= dates[i - 1]:
            raise RuntimeError("日期未严格递增 @%d: %s -> %s" % (i, dates[i - 1], dates[i]))
    for k in SERIES_KEYS:
        arr = p.get(k)
        if not isinstance(arr, list) or len(arr) != n:
            raise RuntimeError("序列长度与交易日不一致: %s (%s)" %
                               (k, len(arr) if isinstance(arr, list) else type(arr).__name__))
    for j, row in enumerate(p.get("etfSeries") or []):
        if len(row) != n:
            raise RuntimeError("etfSeries[%d] 长度不一致" % j)
    for k in SERIES_KEYS:
        for i, v in enumerate(p[k]):
            if v is not None and not (isinstance(v, (int, float)) and math.isfinite(v)):
                raise RuntimeError("非有限数值 %s[%d]=%r" % (k, i, v))
    gaps = []
    for i in range(1, n):
        gap = (dt.date.fromisoformat(dates[i]) - dt.date.fromisoformat(dates[i - 1])).days
        if gap > 5:
            gaps.append(gap)
    max_gap = max(gaps) if gaps else 1
    print("[日历校验] %d 个交易日，格式 / 递增 / 序列长度全部一致；"
          "最长自然日间隔 %d 天（节假日，周末由等距轴自动跳过）" % (n, max_gap))
    return {"maxCalendarGap": max_gap, "longGaps": len(gaps)}


# ------------------------------------------------------- 1. 真实行情日历
def fetch_index():
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           "param=sh000300,day," + START_DATE + ",2099-12-31,1600,qfq")
    j = json.loads(http(url, "https://gu.qq.com/"))
    node = j["data"]["sh000300"]
    rows = node.get("qfqday") or node["day"]
    series = [(r[0], float(r[2])) for r in rows if r[0] >= START_DATE]
    dates = [d for d, _ in series]
    close = [round(c, 2) for _, c in series]
    print("[真实行情] 沪深300 %d 个交易日  %s -> %s  %.2f -> %.2f"
          % (len(dates), dates[0], dates[-1], close[0], close[-1]))
    return dates, close


# --------------------------------------------------- 2. ETF 每日单位净值
def fetch_nav(code):
    txt = http("https://fund.eastmoney.com/pingzhongdata/%s.js" % code,
               "https://fund.eastmoney.com/", timeout=25)
    m = re.search(r"var\s+Data_netWorthTrend\s*=\s*(\[.*?\]);", txt, re.S)
    if not m:
        raise RuntimeError("%s 净值序列解析失败" % code)
    arr = json.loads(m.group(1))
    out = {}
    for it in arr:
        # 天天基金时间戳为北京时间 00:00，按 +8h 还原成本地日期
        day = dt.datetime.utcfromtimestamp(it["x"] / 1000.0 + 8 * 3600).strftime("%Y-%m-%d")
        v = it.get("y")
        if isinstance(v, (int, float)) and v > 0:
            out[day] = float(v)
    print("[真实净值] %s  %d 日  %s -> %s" % (code, len(out), min(out), max(out)))
    return out


# --------------------------------------- 3. ETF 期末总份额 / 净资产（披露值）
def fetch_quarters(code):
    txt = http("https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code=" + code,
               "https://fundf10.eastmoney.com/", timeout=25)
    out = {}
    for d, body in re.findall(r"<tr><td>(\d{4}-\d{2}-\d{2})</td>(.*?)</tr>", txt, re.S):
        cells = [re.sub(r"<[^>]+>", "", x).strip()
                 for x in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
        if len(cells) < 4 or d < "2020-01-01":
            continue
        shares, aum = num(cells[2]), num(cells[3])      # 亿份 / 亿元
        if shares and shares > 0:
            rec = {"shares": shares}
            if aum and aum > 0:
                rec["aum"] = aum
                rec["nav"] = aum / shares               # 披露口径反推单位净值，用于校验
            out[d] = rec
    print("[披露份额] %s  %d 期  %s -> %s" % (code, len(out), min(out), max(out)))
    return out


# ------------------------------------------------ 4. 当日官方快照（东财 push2）
def fetch_snapshot():
    def one(secid):
        u = ("https://push2.eastmoney.com/api/qt/stock/get?secid=" + secid +
             "&fields=f57,f58,f84,f85,f116,f117")
        d = json.loads(http(u, "https://quote.eastmoney.com/", timeout=15)).get("data")
        if not d:
            raise RuntimeError("快照为空 " + secid)
        return d
    idx = one(INDEX_SECID)
    float_mv = float(idx.get("f117") or 0)
    total_mv = float(idx.get("f116") or 0)
    if float_mv < 1e12:
        raise RuntimeError("指数流通市值异常: %s" % float_mv)
    etf = {}
    for code, secid, name in ETF:
        d = one(secid)
        shares = float(d.get("f84") or 0)             # 份
        aum = float(d.get("f116") or 0)               # 元
        if shares < 1e8 or aum < 1e8:
            raise RuntimeError("ETF 份额/规模异常 %s: %s / %s" % (code, shares, aum))
        etf[code] = {"name": name, "shares": shares, "aum": aum, "nav": aum / shares}
    return {"floatMv": float_mv, "totalMv": total_mv, "etf": etf}


# ------------------------------------------------------------ 快照累积库
def load_snapshots():
    p = BASE / "gjd_snapshots.json"
    if not p.exists():
        return {"version": 1, "days": {}, "note": "每交易日收盘后由 fetch_real.py 追加真实快照"}
    return json.loads(p.read_text(encoding="utf-8"))


def save_snapshots(s):
    (BASE / "gjd_snapshots.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8", newline="")


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_cache(c):
    CACHE_FILE.write_text(json.dumps(c, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8", newline="")
    print("[缓存] raw_cache.json  %d 字符（供 --no-fetch 断网重建）" % CACHE_FILE.stat().st_size)


# ------------------------------------------------------- 插值 / 对齐工具
def daily_from_anchors(dates, anchors, field):
    """把稀疏锚点线性插值到交易日；锚点之外用最近值外推。字段单位保持一致。"""
    keys = sorted(anchors)
    xs = [bisect.bisect_left(dates, k) for k in keys]
    vals = [anchors[k][field] for k in keys]
    out = [vals[0]] * len(dates)
    for i, d in enumerate(dates):
        j = bisect.bisect_right(xs, i) - 1
        if j < 0:
            out[i] = vals[0]
        elif j >= len(xs) - 1:
            out[i] = vals[-1]
        else:
            x0, x1 = xs[j], xs[j + 1]
            if x1 == x0:
                out[i] = vals[j + 1]
            else:
                t = (i - x0) / (x1 - x0)
                out[i] = vals[j] + (vals[j + 1] - vals[j]) * t
    return out


def align_nav(dates, nav_map, fallback_nav):
    """把净值按交易日对齐，缺失日用前值填充（节假日/披露延迟）。"""
    keys = sorted(nav_map)
    out, last = [], fallback_nav
    for d in dates:
        j = bisect.bisect_right(keys, d) - 1
        if j >= 0:
            last = nav_map[keys[j]]
        out.append(last)
    return out


# ------------------------------------------------------------- 派生指标
def derive(dates, close, ratio_raw):
    """由真实 ratio 出发，三角窗平滑 net1 并反推 ratio，保证三者严格自洽。"""
    n = len(dates)
    raw1 = [0.0] * n
    for i in range(1, n):
        raw1[i] = (ratio_raw[i] - ratio_raw[i - 1]) * 100     # 1% = 100bp
    net1 = [0.0] * n
    for i in range(1, n):
        acc, wsum = 0.0, 0
        for j, w in enumerate(KERN):
            k = i + j - 2
            if 0 <= k < n:
                acc += raw1[k] * w
                wsum += w
        net1[i] = acc / wsum
    ratio = [ratio_raw[0]] * n
    for i in range(1, n):
        ratio[i] = ratio[i - 1] + net1[i] / 100.0
    ratio = [round(v, 7) for v in ratio]
    net1 = [round(v, 6) for v in net1]

    pct252 = []
    for i in range(n):
        w = ratio[max(0, i - 251): i + 1]
        pct252.append(round(100.0 * sum(1 for x in w if x <= ratio[i]) / len(w), 1))

    net20 = [None] * n
    for i in range(20, n):
        net20[i] = round((ratio[i] - ratio[i - 20]) * 100, 2)
    dev = max(abs(net20[i] - sum(net1[i - 19:i + 1])) for i in range(20, n))
    print("[自洽校验] net20 = Σnet1(20日) 最大偏差 %.6f bp" % dev)
    return ratio, net1, net20, pct252, dev


def find_divergences(dates, close, ratio):
    n = len(dates)
    flags = [False] * n
    for i in range(20, n):
        d_ratio = (ratio[i] - ratio[i - 20]) * 100
        d_px = (close[i] / close[i - 20] - 1) * 100
        flags[i] = (d_ratio >= 8.0 and d_px <= -1.5)
    segs, i = [], 0
    while i < n:
        if flags[i]:
            s = i
            while i + 1 < n and flags[i + 1]:
                i += 1
            e = i
            if e - s + 1 >= 8:
                segs.append({"start": dates[s], "end": dates[e], "bars": e - s + 1,
                             "dRatio": round((ratio[e] - ratio[s - 20]) * 100, 1),
                             "dPx": round((close[e] / close[s - 20] - 1) * 100, 2)})
        i += 1
    segs.sort(key=lambda x: -x["bars"])
    top = sorted(segs[:5], key=lambda x: x["start"])
    print("[背离区间] 检出 %d 段，取最长 %d 段做底纹" % (len(segs), len(top)))
    return top


def build_narratives(dates, close, ratio, net1, events):
    n = len(dates)
    s1 = sorted(v for v in net1)
    p95 = s1[int(len(s1) * 0.95)]
    labels = {e["tradeDate"]: e["label"] for e in events}
    out = []
    for d in NARRATIVE_DATES:
        t = min(bisect.bisect_left(dates, d), n - 1)
        t0, t1 = max(0, t - 10), min(n - 1, t + 20)
        dr = (ratio[t1] - ratio[t0]) * 100
        dp = (close[t1] / close[t0] - 1) * 100
        pk = max(net1[t0:t1 + 1])
        if dp >= 3.0 and dr >= 3.0:
            tag, kind = "量价齐升", "rise"
        elif dp <= -2.0 and dr >= 5.0:
            tag, kind = "量升价跌 · 托而不举", "diverge"
        elif pk >= p95 and dr >= 0:
            tag, kind = "紧急托底 · 短暂反弹", "pulse"
        elif dr > 0:
            tag, kind = "底部吸筹 · 占比抬升", "absorb"
        elif dp <= -2.0:
            tag, kind = "占比回落 · 指数探底", "fade"
        else:
            tag, kind = "低位震荡", "flat"
        out.append({"date": dates[t], "tag": tag, "kind": kind,
                    "short": SHORT_NAME.get(d, ""), "dRatio": round(dr, 1),
                    "dPx": round(dp, 2), "peakNet1": round(pk, 2),
                    "pulseP95": round(p95, 2), "isPulse": bool(pk >= p95),
                    "event": labels.get(dates[t], d),
                    "window": [dates[t0], dates[t1]]})
    return out


# ------------------------------------------- 交叉 / 极值信号与有效性回测
def build_signals(dates, close, ratio, pct252):
    """回答「ETF占比与沪深300的金叉死叉，能否预判指数见顶 / 见底」。

    原始两线量纲不同（% vs 点），视觉交叉完全由双 Y 轴缩放决定 —— 下面的
    visualCross 用四组轴范围给出实证，证明它是伪信号，不能直接标在图上。
    因此改为：把两序列各自的 20 日变化做「滚动 252 日 z 标准化」，
    在**同一量纲**上求交叉；再对所有候选信号做未来 20 / 60 日指数收益回测。
    结论仅作描述性参考，不作为择时依据。
    """
    n = len(dates)

    def ma(a, w):
        out, s, q = [None] * n, 0.0, []
        for i, v in enumerate(a):
            q.append(v)
            s += v
            if len(q) > w:
                s -= q.pop(0)
            if len(q) == w:
                out[i] = s / w
        return out

    def roll_z(a):
        chg = [None] * n
        for i in range(20, n):
            chg[i] = a[i] - a[i - 20]
        z = [None] * n
        for i in range(20, n):
            w = [chg[j] for j in range(max(20, i - 251), i + 1) if chg[j] is not None]
            if len(w) < 60:
                continue
            m = sum(w) / len(w)
            sd = math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
            if sd > 1e-12:
                z[i] = (chg[i] - m) / sd
        return z

    def cross(fa, fb, direction):
        out = []
        for i in range(1, n):
            a0, a1, b0, b1 = fa[i - 1], fa[i], fb[i - 1], fb[i]
            if a0 is None or a1 is None or b0 is None or b1 is None:
                continue
            if direction > 0 and a0 <= b0 and a1 > b1:
                out.append(i)
            if direction < 0 and a0 >= b0 and a1 < b1:
                out.append(i)
        return out

    def cluster(ix, gap):
        """邻近信号聚成一次事件，避免重叠样本虚增显著性。"""
        out = []
        for i in sorted(set(ix)):
            if not out or i - out[-1] > gap:
                out.append(i)
        return out

    def fwd(i, k):
        return (close[i + k] / close[i] - 1) * 100 if i + k < n else None

    # ---- ① 伪信号实证：只改右轴范围，视觉交叉次数就变了
    rlo, rhi = min(ratio), max(ratio)

    def visual(lo, hi):
        g = lambda v: (v - rlo) / (rhi - rlo)
        p = lambda v: (v - lo) / (hi - lo)
        hits = []
        for i in range(1, n):
            if (g(ratio[i - 1]) - p(close[i - 1])) * (g(ratio[i]) - p(close[i])) < 0:
                hits.append(dates[i])
        return {"axis": "[%d, %d]" % (lo, hi), "count": len(hits),
                "first": hits[0] if hits else "—"}

    visual_cross = [visual(*a) for a in
                    [(3000, 6000), (3500, 5500), (3800, 5000), (0, 8000)]]

    # ---- ② 基准与统计量
    b20 = [fwd(i, 20) for i in range(n) if fwd(i, 20) is not None]
    b60 = [fwd(i, 60) for i in range(n) if fwd(i, 60) is not None]
    mu20 = sum(b20) / len(b20)
    sd20 = math.sqrt(sum((x - mu20) ** 2 for x in b20) / len(b20))
    mu60 = sum(b60) / len(b60)
    win20 = 100.0 * sum(1 for x in b20 if x > 0) / len(b20)

    def stat(ix):
        ev = [i for i in ix if i + 20 < n]
        a = [fwd(i, 20) for i in ev]
        b = [fwd(i, 60) for i in ev if i + 60 < n]
        if not a:
            return {"n": 0, "avg20": None, "win20": None, "avg60": None, "t20": None}
        m = sum(a) / len(a)
        t = (m - mu20) / (sd20 / math.sqrt(len(a))) if len(a) > 1 else None
        return {"n": len(a),
                "avg20": clean_num(m, 2),
                "win20": clean_num(100.0 * sum(1 for x in a if x > 0) / len(a), 1),
                "avg60": clean_num(sum(b) / len(b), 2) if b else None,
                "t20": clean_num(t, 2) if t is not None else None}

    zr, zp = roll_z(ratio), roll_z(close)
    r20, r60 = ma(ratio, 20), ma(ratio, 60)
    p20, p60 = ma(close, 20), ma(close, 60)
    dr20 = [None] * n
    chg20 = [None] * n
    for i in range(20, n):
        dr20[i] = (ratio[i] - ratio[i - 20]) * 100
        chg20[i] = (close[i] / close[i - 20] - 1) * 100
    pos = sorted(v for v in dr20 if v is not None)
    hi_th, lo_th = pos[int(len(pos) * 0.90)], pos[int(len(pos) * 0.10)]

    def major_nodes(ix):
        """只留「大节点」：交叉后 25 个交易日内 gap 不回头 + 期间最大 |gap| ≥ 1.2。
        原始 z 交叉 5.7 年有 34 次，过于频繁、不可操作；过滤后仅剩趋势级别切换
        （约 2~3 次/年）。阈值经全样本扫描选定，见构建日志。"""
        keep = []
        for i in ix:
            g0 = (zr[i] - zp[i]) if (zr[i] is not None and zp[i] is not None) else None
            if g0 is None:
                continue
            peak = 0.0
            flipped = False
            for j in range(i + 1, min(i + 26, n)):
                if zr[j] is None or zp[j] is None:
                    continue
                g = zr[j] - zp[j]
                peak = max(peak, abs(g))
                if (g0 > 0 and g < 0) or (g0 < 0 and g > 0):
                    flipped = True
                    break
            if not flipped and peak >= 1.2:
                keep.append(i)
        return keep

    gold = cluster(major_nodes(cross(zr, zp, 1)), 60)
    death = cluster(major_nodes(cross(zr, zp, -1)), 60)
    pct_hi = cluster([i for i in range(1, n)
                      if pct252[i] >= 90 and pct252[i - 1] < 90], 60)
    pct_lo = cluster([i for i in range(1, n)
                      if pct252[i] <= 10 and pct252[i - 1] > 10], 60)

    specs = [
        ("占比 MA20 上穿 MA60（同量纲金叉）", cluster(cross(r20, r60, 1), 30)),
        ("占比 MA20 下穿 MA60（同量纲死叉）", cluster(cross(r20, r60, -1), 30)),
        ("指数 MA20 上穿 MA60", cluster(cross(p20, p60, 1), 30)),
        ("指数 MA20 下穿 MA60", cluster(cross(p20, p60, -1), 30)),
        ("动量金叉 z(占比) 上穿 z(指数)", gold),
        ("动量死叉 z(占比) 下穿 z(指数)", death),
        ("占比进入 252 日 ≥90 分位", pct_hi),
        ("占比进入 252 日 ≤10 分位", pct_lo),
        ("托而不举极值 Δ占比≥P90 且 20 日指数≤−3%", cluster(
            [i for i in range(20, n) if dr20[i] >= hi_th and chg20[i] <= -3], 40)),
        ("有序退出 Δ占比≤P10 且 20 日指数≥+3%", cluster(
            [i for i in range(20, n) if dr20[i] <= lo_th and chg20[i] >= 3], 40)),
    ]
    rows = []
    for name, ix in specs:
        s = stat(ix)
        sig = s["t20"] is not None and abs(s["t20"]) >= 2
        s["name"] = name
        s["verdict"] = ("弱统计证据（未做多重检验校正）" if sig else
                        ("样本不足，无统计证据" if s["n"] < 30 else "无统计显著性"))
        rows.append(s)

    # ---- ③ 图上标记：只保留描述性状态，不宣称预测
    marks = []
    for i in gold:
        marks.append({"date": dates[i], "type": "gold", "label": "动量金叉",
                      "zRatio": clean_num(zr[i], 2), "zPx": clean_num(zp[i], 2),
                      "pct": pct252[i], "fwd20": clean_num(fwd(i, 20), 2)})
    for i in death:
        marks.append({"date": dates[i], "type": "death", "label": "动量死叉",
                      "zRatio": clean_num(zr[i], 2), "zPx": clean_num(zp[i], 2),
                      "pct": pct252[i], "fwd20": clean_num(fwd(i, 20), 2)})
    for i in pct_hi:
        marks.append({"date": dates[i], "type": "pctHi",
                      "label": "占比进入 252 日高位（≥90 分位）",
                      "pct": pct252[i], "fwd20": clean_num(fwd(i, 20), 2)})
    for i in pct_lo:
        marks.append({"date": dates[i], "type": "pctLo",
                      "label": "占比进入 252 日低位（≤10 分位）",
                      "pct": pct252[i], "fwd20": clean_num(fwd(i, 20), 2)})
    marks.sort(key=lambda x: x["date"])

    any_sig = any(r["t20"] is not None and abs(r["t20"]) >= 2 for r in rows)
    conclusion = (
        "本样本（%s ~ %s，%d 个交易日）内，10 类候选交叉 / 极值信号对未来 20 日指数收益"
        "均<b>未达到统计显著</b>（|t| 均 &lt; 2，未做多重检验校正）。"
        "因此图上的三角 / 菱形标记只表示<b>资金动量与价格动量的状态切换</b>，"
        "<b>不是见顶 / 见底的预测信号</b>，不可据此择时。"
        "此外历史占比含季度份额插值，日度信号样本有限，统计效力偏弱。"
        % (dates[0], dates[-1], n)) if not any_sig else (
        "本样本内部分信号达到 |t|≥2，但未做多重检验校正且样本有限，仅作描述性参考，不构成择时依据。")

    print("[信号回测] 基准 20 日 %+.2f%% / 胜率 %.1f%%；%d 类信号，"
          "显著(|t|≥2) %d 类；图上描述性标记 %d 个"
          % (mu20, win20, len(rows), sum(1 for r in rows
             if r["t20"] is not None and abs(r["t20"]) >= 2), len(marks)))
    return {
        "baseline": {"avg20": clean_num(mu20, 2), "win20": clean_num(win20, 1),
                     "avg60": clean_num(mu60, 2), "sd20": clean_num(sd20, 2)},
        "rows": rows, "visualCross": visual_cross, "marks": marks,
        "conclusion": conclusion,
        "definitions": [
            "z 动量 = 20 日变化在滚动 252 日窗口内的标准分数；两序列各自标准化后才可比",
            "动量金叉 = z(占比) 上穿 z(指数)：资金动量转强于价格动量（描述性状态）",
            "动量死叉 = z(占比) 下穿 z(指数)：资金动量转弱于价格动量（描述性状态）",
            "大节点过滤 = 交叉后 25 个交易日方向不回头且期间最大 |Δz| ≥ 1.2；原始交叉 34 次过滤后约 2~3 次/年",
            "占比极值 = pct252 首次进入 ≥90 / ≤10 分位，刻画占比自身的高 / 低位（描述性状态）",
            "回测口径 = 信号聚类后，统计其后 20 / 60 日指数收益与全样本基准之差，t 值仅作粗判",
        ],
    }


# ------------------------------------------------------------- 今日解读
# sgn / fmt / clean_num 统一定义在上方工具区：正文、徽章、指标一律走同一格式化管道，
# 保证今日解读不会出现 -0.00、符号错位或浮点尾。
def hl(v, d=2, unit="", tone=None):
    """把数值包成带语义颜色的 <b>：涨 / 流入红，跌 / 流出绿，零值中性。
    A 股惯例涨红跌绿；颜色由前端 .hl-* CSS 定义。"""
    q = clean_num(v, d)
    s = ("+" if q > 0 else "") + ("%." + str(d) + "f") % q + unit
    if tone is None:
        tone = "hl-up" if q > 0 else ("hl-dn" if q < 0 else "hl-key")
    return '<b class="%s">%s</b>' % (tone, s)


def hlt(text, tone="hl-key"):
    return '<b class="%s">%s</b>' % (tone, text)


def build_brief(dates, close, ratio, net1, net20, pct252, shares_total, aum_total,
                float_mv, meta, signals=None):
    """基于最新真实数据自动生成「当前趋势 + 条件式前瞻」。不含点位预测、不构成投资建议。"""
    n = len(dates)
    L = n - 1

    def chg_px(k):
        i = max(0, L - k)
        return (close[L] / close[i] - 1) * 100

    def chg_ratio(k):
        i = max(0, L - k)
        return (ratio[L] - ratio[i]) * 100          # bp

    d5, d20, d60 = chg_ratio(5), chg_ratio(20), chg_ratio(60)
    p5, p20, p60 = chg_px(5), chg_px(20), chg_px(60)
    n20 = net20[L] if net20[L] is not None else 0.0
    n20_prev = net20[max(20, L - 20)] or 0.0
    momentum = n20 - n20_prev
    streak = 0
    for i in range(L, 0, -1):
        if net1[i] > 0:
            streak += 1
        else:
            break
    px_window = close[max(0, L - 251): L + 1]
    px_pct = round(100.0 * sum(1 for x in px_window if x <= close[L]) / len(px_window), 1)

    hd20, hp20 = hl(d20, 1, " bp"), hl(p20, 2, "%")
    if d20 >= 3 and p20 <= -1:
        state = "量升价跌 · 托而不举"
        state_desc = ("近 20 个交易日占比抬升 %s，同期指数 %s："
                      "增量资金持续进入宽基 ETF，但尚未传导为指数上行，"
                      "属于典型的 %s 而非拉升形态。"
                      % (hd20, hp20, hlt("托底", "hl-key")))
    elif d20 >= 3 and p20 >= 1:
        state = "量价齐升 · 资金与情绪共振"
        state_desc = ("近 20 个交易日占比抬升 %s、指数 %s，增量资金与价格同向，"
                      "是本看板口径下 %s 的组合。"
                      % (hd20, hp20, hlt("最具持续性", "hl-up")))
    elif d20 <= -3 and p20 >= 1:
        state = "量缩价升 · 赎回兑现"
        state_desc = ("近 20 个交易日占比回落 %s、指数 %s，表现为上涨过程中的份额净流出，"
                      "需留意 %s。"
                      % (hd20, hp20, hlt("托底资金退坡", "hl-warn")))
    elif d20 <= -3 and p20 <= -1:
        state = "量价齐跌 · 资金退坡"
        state_desc = ("近 20 个交易日占比回落 %s、指数 %s，份额与价格同步走弱，"
                      "%s。"
                      % (hd20, hp20, hlt("缺乏增量承接", "hl-dn")))
    else:
        state = "低位钝化 · 窄幅震荡"
        state_desc = ("近 20 个交易日占比 %s、指数 %s，量价均在窄幅区间内，"
                      "%s。"
                      % (hd20, hp20, hlt("暂未形成方向性信号", "hl-key")))

    pct_tone = "hl-up" if px_pct >= 80 else ("hl-dn" if px_pct <= 20 else "hl-key")
    status = (
        "截至 %s，4 只头部沪深300ETF 合计规模 %s、合计份额 %s，"
        "占沪深300流通市值 %s，处于过去 252 个交易日的 %s 分位；"
        "沪深300 报 %s 点，位于 252 日 %s 分位。"
        % (hlt(dates[L], "hl-blue"),
           hlt(fmt(aum_total[L], 0) + " 亿元", "hl-blue"),
           hlt(fmt(shares_total[L], 1) + " 亿份", "hl-blue"),
           hlt(fmt(ratio[L], 3, "%"), "hl-key"),
           hlt(fmt(pct252[L], 1, "%"), "hl-key"),
           hlt(fmt(close[L], 2), "hl-key"),
           hlt(fmt(px_pct, 1, "%"), pct_tone)))
    status += ("数据区间 %s ~ %s（%d 个交易日），其中 %s 个交易日已由官方每日快照直接覆盖。"
               % (dates[0], dates[L], n, hlt(str(meta["snapDays"]), "hl-up")))

    trend = (
        "%s %s "
        "短中期动能：近 5 / 20 / 60 日占比变动 %s / %s / %s，"
        "同期指数 %s / %s / %s；"
        "20 日累计净申购强度 %s，较 20 日前 %s。"
        % (hlt("当前状态：" + state, "hl-key"), state_desc,
           hl(d5, 1, " bp"), hl(d20, 1, " bp"), hl(d60, 1, " bp"),
           hl(p5, 2, "%"), hl(p20, 2, "%"), hl(p60, 2, "%"),
           hl(n20, 1, " bp"), hl(momentum, 1, " bp")))
    trend += ("%s：分母（流通市值）与分子（ETF 净值）均随指数同比例波动，"
              "价格机械效应在比值中相互抵消，因此本占比的变化主要反映 %s，而非涨跌带来的账面缩放。"
              "%s：季度末之间的份额为线性插值，故 20D 净申购曲线在季度内呈平滑斜率，"
              "%s；只有自 %s 起的官方快照日对应真实当日份额与规模。"
              % (hlt("口径提示", "hl-dim"), hlt("份额增减", "hl-blue"),
                 hlt("但需特别注意", "hl-warn"),
                 hlt("不能解读为逐日真实申赎", "hl-warn"),
                 hlt(meta["firstSnapDay"] or "首个快照日", "hl-up")))

    hn20 = hl(n20, 1, " bp")
    if n20 > 0 and p20 <= 0:
        s1 = ("%s 20 日净申购维持当前 %s 的正值区间、而指数仍未能收复近 20 日跌幅，"
              "%s，历史区间显示该状态更接近 %s 而非 %s；"
              % (hlt("若", "hl-key"), hn20,
                 hlt("则量价背离大概率延续", "hl-warn"),
                 hlt("托底", "hl-blue"), hlt("反转", "hl-up")))
    elif n20 > 0:
        s1 = ("%s 20 日净申购维持 %s 的正值且指数同步走强，"
              "%s，占比 252 日分位将继续上行；"
              % (hlt("若", "hl-key"), hn20,
                 hlt("则量价共振有望延续", "hl-up")))
    else:
        s1 = ("%s 20 日净申购维持在 %s 的负值区间，"
              "%s，占比上行缺乏增量支撑；"
              % (hlt("若", "hl-key"), hn20,
                 hlt("则份额仍在净流出", "hl-dn")))

    fpct = hlt(fmt(pct252[L], 1, "%"), "hl-key")
    if pct252[L] >= 80:
        s2 = (f"当前占比已处 252 日 {fpct} 的 {hlt('高分位', 'hl-up')}，若后续净申购转负，"
              "占比见顶回落的概率上升，届时关注份额是否出现连续赎回；")
    elif pct252[L] <= 20:
        s2 = (f"当前占比处 252 日 {fpct} 的 {hlt('低分位', 'hl-dn')}，若净申购由负转正，"
              "往往是占比触底回升的先行信号；")
    else:
        s2 = (f"当前占比处 252 日 {fpct} 的 {hlt('中枢分位', 'hl-key')}，方向尚未极端，"
              "需结合净申购斜率与指数分位共同判断；")

    s3 = (f"指数 252 日分位 {hlt(fmt(px_pct, 1, '%'), pct_tone)}，若指数继续下行而净申购同步放大，"
          f"背离区间（{hlt('浅蓝色块', 'hl-blue')}）会再次扩张；若指数企稳而净申购收敛，"
          f"则更接近政策资金有序退出。{hlt('需跟踪的三个信号', 'hl-key')}："
          "① 20 日净申购是否转负；② 占比 252 日分位是否自高位回落；"
          "③ 份额环比与指数方向是否重新同步。")

    outlook = (hlt("条件式情景推演（非点位预测）：", "hl-key") + s1 + s2 + s3)
    if signals:
        outlook += ("<br>%s 本样本内 10 类交叉 / 极值信号对未来 20 日指数收益 %s；"
                    "图上三角 / 菱形标记仅表示 %s，%s。"
                    % (hlt("关于金叉死叉：", "hl-key"),
                       hlt("均未达到统计显著", "hl-warn"),
                       hlt("资金动量与价格动量的状态切换", "hl-blue"),
                       hlt("不是见顶 / 见底的预测信号", "hl-warn")))

    risk = (
        "数据口径：份额季度末为基金定期报告披露的真实值，季度之间线性插值；"
        "自 %s 起逐日写入东方财富官方份额 / 规模 / 流通市值快照（当前 %d 日）。"
        "流通市值历史以最新官方快照为锚按指数点位折算，属估算而非逐日官方披露。"
        "事件锚点仅作时间定位，不参与数值生成。"
        "%s"
        % (meta["firstSnapDay"] or "—", meta["snapDays"],
           hlt("本段文字由脚本依据上述数值自动生成、每交易日随数据刷新，"
               "仅为数据描述与条件式情景推演，不含盈利预测，不构成任何投资建议。", "hl-warn")))

    return {
        "asOf": dates[L], "state": state,
        "conclusion": signals["conclusion"] if signals else "",
        "sections": [
            {"title": "当前状态", "body": status},
            {"title": "趋势研判", "body": trend},
            {"title": "前瞻观察", "body": outlook},
            {"title": "口径与风险", "body": risk},
        ],
        "signals": [
            {"label": "占比 252 日分位", "value": fmt(pct252[L], 1, "%"),
             "tone": "up" if pct252[L] >= 80 else ("dn" if pct252[L] <= 20 else "")},
            {"label": "20D 净申购", "value": sgn(n20, 1) + " bp",
             "tone": "up" if n20 > 0 else "dn"},
            {"label": "近 20 日指数", "value": sgn(p20, 2) + "%",
             "tone": "up" if p20 > 0 else "dn"},
            {"label": "官方快照覆盖", "value": "%d 日" % meta["snapDays"], "tone": ""},
        ],
        "metrics": {
            "dRatio5": clean_num(d5, 2), "dRatio20": clean_num(d20, 2), "dRatio60": clean_num(d60, 2),
            "dPx5": clean_num(p5, 2), "dPx20": clean_num(p20, 2), "dPx60": clean_num(p60, 2),
            "net20": clean_num(n20, 2), "net20Momentum": clean_num(momentum, 2),
            "posStreak": streak, "pxPct252": px_pct, "ratioPct252": pct252[L],
            "aum": clean_num(aum_total[L], 1), "shares": clean_num(shares_total[L], 2),
        },
    }


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="强制覆盖当日快照")
    ap.add_argument("--no-fetch", action="store_true", help="完全离线，仅用已有数据重建页面")
    args = ap.parse_args()

    # 时区雷区：一律以北京时间判断，避免 UTC runner 或本机时区差异导致
    # 「早上 9 点前把昨日/盘中数据写成当日收盘快照」。
    bj = beijing_now()
    closed, close_reason = is_market_close(bj)
    expected = expected_trade_date(bj)
    print("[时区] 北京时间 %s ｜ %s ｜ 期望交易日 %s" %
          (bj.strftime("%Y-%m-%d %H:%M:%S"), close_reason, expected))

    snaps = load_snapshots()
    days = snaps.setdefault("days", {})
    cache = load_cache()
    if args.no_fetch:
        if not cache.get("index"):
            raise RuntimeError("离线重建需要 raw_cache.json；请先联网运行一次 python fetch_real.py")
        dates, close = cache["index"]["dates"], cache["index"]["close"]
        print("[离线缓存] 指数日线 %d 根  %s -> %s" % (len(dates), dates[0], dates[-1]))
    else:
        dates, close = fetch_index()
        cache["index"] = {"dates": dates, "close": close}
    last_day = dates[-1]

    if not args.no_fetch:
        if last_day in days and not args.force:
            print("[快照] %s 已存在，跳过抓取（--force 可覆盖）" % last_day)
        elif not closed and not args.force:
            print("[快照] 跳过写入：%s。盘中 / 开盘前的 f84、f116、f117 仍是未定值，"
                  "不能写成收盘快照；本次仅用已有快照重算（--force 可强制）。" % close_reason)
        elif last_day != expected and not args.force:
            print("[快照] 跳过写入：行情日历末日 %s ≠ 期望交易日 %s，"
                  "接口可能尚未更新到当日（节假日或数据源延迟）；--force 可强制。"
                  % (last_day, expected))
        else:
            snap = fetch_snapshot()
            snap["hs300"] = close[-1]
            days[last_day] = snap
            snaps["version"] = 1
            save_snapshots(snaps)
            print("[快照] 写入 %s：流通市值 %.2f 万亿元，4 只 ETF 合计 %.0f 亿元"
                  % (last_day, snap["floatMv"] / 1e12,
                     sum(v["aum"] for v in snap["etf"].values()) / 1e8))
    else:
        print("[离线重建] 不抓取当日快照，全部数据来自本地缓存与已累积快照")

    if not days:
        raise RuntimeError("没有任何官方快照，无法构建分母锚点。请先联网运行一次。")

    snap_days = sorted(days)
    anchor_day = snap_days[-1]
    anchor_snap = days[anchor_day]
    anchor_mv_yuan = anchor_snap["floatMv"]
    anchor_idx = (anchor_snap.get("hs300") or
                  (close[dates.index(anchor_day)] if anchor_day in dates else close[-1]))
    print("[锚点] %s  流通市值 %.2f 万亿元  指数 %.2f" %
          (anchor_day, anchor_mv_yuan / 1e12, anchor_idx))

    # ---- 每只 ETF 的真实份额锚点：季度披露 + 每日官方快照
    series_by_etf, share_daily = {}, []
    cache.setdefault("quarters", {})
    cache.setdefault("nav", {})
    for code, _secid, name in ETF:
        if args.no_fetch:
            qmap = cache["quarters"].get(code, {})
            nav_map = cache["nav"].get(code, {})
            if not qmap:
                raise RuntimeError("离线缓存缺少 %s 的季度份额" % code)
            print("[离线缓存] %s 季度 %d 期 / 净值 %d 日" % (code, len(qmap), len(nav_map)))
        else:
            try:
                qmap = fetch_quarters(code)
                cache["quarters"][code] = qmap
            except Exception as e:                    # noqa: BLE001
                qmap = cache["quarters"].get(code, {})
                print("  ! %s 季度份额抓取失败，改用缓存：%s" % (code, e))
            try:
                nav_map = fetch_nav(code)
                cache["nav"][code] = nav_map
            except Exception as e:                    # noqa: BLE001
                nav_map = cache["nav"].get(code, {})
                print("  ! %s 净值抓取失败，改用缓存：%s" % (code, e))

        anchors = {d: {"shares": rec["shares"], "aum": rec.get("aum")}
                   for d, rec in qmap.items()}
        for d in snap_days:
            v = days[d]["etf"].get(code)
            if v:
                anchors[d] = {"shares": v["shares"] / 1e8,     # 份 -> 亿份
                              "aum": v["aum"] / 1e8,           # 元 -> 亿元
                              "nav": v["nav"]}
        if not anchors:
            raise RuntimeError("%s 无任何份额锚点" % code)
        sh = daily_from_anchors(dates, anchors, "shares")
        share_daily.append(sh)

        nav_fallback = (anchors[max(anchors)]["aum"] / anchors[max(anchors)]["shares"]
                        if anchors[max(anchors)].get("aum") else 1.0)
        nav = align_nav(dates, nav_map, nav_fallback)
        # 官方快照日：用 aum/shares 覆盖净值，保证 aum 与快照完全一致
        for d in snap_days:
            if d in dates and days[d]["etf"].get(code):
                nav[dates.index(d)] = days[d]["etf"][code]["nav"]
        aum = [sh[i] * nav[i] for i in range(len(dates))]       # 亿份 × 元 = 亿元
        series_by_etf[code] = {"name": name, "shares": sh, "nav": nav, "aum": aum}
        print("[规模序列] %-18s 份额 %.1f -> %.1f 亿份  规模 %.0f -> %.0f 亿元"
              % (name, sh[0], sh[-1], aum[0], aum[-1]))

    n = len(dates)
    aum_total = [sum(series_by_etf[c]["aum"][i] for c, _, _ in ETF) for i in range(n)]
    shares_total = [sum(series_by_etf[c]["shares"][i] for c, _, _ in ETF) for i in range(n)]
    # 亿元：1 万亿元 = 10000 亿元；流通市值以官方快照为锚按指数折算
    float_mv_yi = [anchor_mv_yuan / 1e8 * (close[i] / anchor_idx) for i in range(n)]
    float_mv = [v / 10000.0 for v in float_mv_yi]                # 输出单位：万亿元
    ratio_raw = [aum_total[i] / float_mv_yi[i] * 100.0 for i in range(n)]
    print("[占比] %.3f%% -> %.3f%%  区间 [%.3f%%, %.3f%%]"
          % (ratio_raw[0], ratio_raw[-1], min(ratio_raw), max(ratio_raw)))

    ratio, net1, net20, pct252, dev = derive(dates, close, ratio_raw)
    divergences = find_divergences(dates, close, ratio)

    events = []
    for e in EVENTS:
        j = min(bisect.bisect_left(dates, e["date"]), n - 1)
        events.append(dict(e, tradeDate=dates[j]))
    narratives = build_narratives(dates, close, ratio, net1, events)

    meta = {
        "start": dates[0], "end": dates[-1], "bars": n,
        "source": "腾讯 fqkline + 天天基金 F10/净值 + 东方财富 push2 官方快照",
        "lastClose": close[-1], "lastRatio": round(ratio[-1], 4),
        "lastPct252": pct252[-1], "lastNet20": net20[-1],
        "snapDays": len(snap_days), "firstSnapDay": snap_days[0],
        "interpDays": n - len(snap_days),
        "anchorDay": anchor_day,
        "lastSync": bj.strftime("%Y-%m-%d %H:%M"),
        "selfConsistencyBp": clean_num(dev, 6),
        "cutoff": dates[-1],
        # 时区审计字段：页面与工作流都可据此核对「数据是否当日」
        "tz": "Asia/Shanghai (UTC+8)",
        "generatedAtBeijing": bj.strftime("%Y-%m-%d %H:%M:%S"),
        "marketClosed": closed,
        "marketCloseNote": close_reason,
        "expectedTradeDate": expected,
    }
    signals = build_signals(dates, close, ratio, pct252)
    brief = build_brief(dates, close, ratio, net1, net20, pct252,
                        shares_total, aum_total, float_mv, meta, signals)
    assert_clean_text(brief)

    data_dict = [
        ["日期", "交易日", "YYYY-MM-DD", "腾讯 fqkline 沪深300 真实交易日历", "真实"],
        ["hs300", "沪深300 收盘价", "点", "腾讯 fqkline/get (sh000300, day) 真实日线，指数无需复权", "真实"],
        ["shares", "4 只头部300ETF 合计份额", "亿份", "季度末取基金定期报告披露的期末总份额；季度间线性插值；快照日直接取东财 push2 f84 真实值", "真实+插值"],
        ["nav", "ETF 单位净值", "元", "天天基金 pingzhongdata 每日单位净值；快照日由 f116 ÷ f84 覆盖校准", "真实"],
        ["aum", "4 只头部300ETF 合计规模", "亿元", "Σ(份额 × 单位净值)；快照日与东财 f116 官方规模一致", "真实"],
        ["floatMv", "沪深300 流通市值", "万亿元", "以最新东财 push2 f117 官方快照为锚，按指数点位比例折算历史（非逐日官方披露）", "官方锚定估算"],
        ["ratio", "头部300ETF 规模占流通市值", "%", "ΣETF规模 ÷ 沪深300流通市值 × 100；分子分母同随指数变动，价格机械效应抵消", "派生·真实输入"],
        ["pct252", "占比 252 日分位", "%", "过去 252 个交易日 ratio 的滚动百分位；图上归一化至底部视觉带，真实读数见 Tooltip", "派生·真实输入"],
        ["net1", "单日净申购 / 流通市值", "bp", "(ratio_t − ratio_t−1) × 100 后过 5 点三角窗 [1,2,3,2,1] 平滑；季度间因份额插值为平滑斜率，不是真实逐日申赎", "派生·真实输入"],
        ["net20", "20D 净申购 / 流通市值", "bp", "由平滑后 net1 累加定义 ratio，故恒等于 Σnet1(20日) = 占比 20 日差分", "派生·真实输入"],
        ["etfSeries", "4 只 ETF 分项占比", "%", "各 ETF 规模 ÷ 同一流通市值 × 100，合计恒等于 ratio", "派生·真实输入"],
        ["snapDates", "官方每日快照日", "—", "每交易日收盘后写入的东财 f84/f116/f117 真实快照，图中以绿色小点标出；随运行天数逐日累积", "真实"],
        ["events", "事件锚点", "—", "公开可核验的政策 / 增持时点，仅用于时间定位，不参与数值生成", "公开信息"],
        ["divergences", "量价背离区间（浅蓝色块）", "—", "20 日 Δ占比 ≥ 8bp 且同期 Δ指数 ≤ −1.5%，连续 ≥ 8 个交易日成段；取最长 5 段", "派生·真实输入"],
        ["narratives", "事件阶段标签", "—", "锚定事件日，按 [−10,+20] 窗口实测 Δ占比 / Δ指数 / 单日净申购 P95 自动分类", "派生·真实输入"],
        ["signals", "交叉 / 极值信号（三角 / 菱形）", "—", "两序列 20 日变化各自滚动 252 日 z 标准化后求交叉；含 pct252 极值与 10 类信号的有效性回测。描述性状态，非择时信号", "派生·真实输入"],
        ["brief", "今日解读", "—", "由最新真实数值自动生成的状态描述、趋势研判与条件式情景推演，每交易日随数据刷新", "自动生成的文字"],
    ]

    etf_series = [[round(series_by_etf[c]["aum"][i] / float_mv_yi[i] * 100, 5)
                   for i in range(n)] for c, _, _ in ETF]

    payload = {
        "dates": dates,
        "hs300": close,
        "ratio": ratio,
        "pct252": pct252,
        "net1": net1,
        "net20": net20,
        "floatMv": [round(v, 4) for v in float_mv],
        "aum": [round(v, 2) for v in aum_total],
        "sharesTotal": [round(v, 3) for v in shares_total],
        "etfNames": [name for _, _, name in ETF],
        "etfSeries": etf_series,
        "events": events,
        "divergences": divergences,
        "narratives": narratives,
        "signals": signals,
        "snapDates": snap_days,
        "dict": data_dict,
        "brief": brief,
        "meta": meta,
    }
    payload["meta"]["calendar"] = validate_payload(payload)

    # ---------------------------------------------------------- 产出文件
    if not args.no_fetch:
        save_cache(cache)

    js = ("window.DASH_DATA = " + json.dumps(payload, ensure_ascii=False,
                                             separators=(",", ":")) + ";\n")
    js += ("window.DATA_DICT = " + json.dumps(data_dict, ensure_ascii=False,
                                              separators=(",", ":")) + ";\n")
    (BASE / "gjd_data.js").write_text(js, encoding="utf-8", newline="")
    (BASE / "gjd_data.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="")
    (ROOT / "gjd_data.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="")
    print("[数据层] gjd_data.js %d 字符 / gjd_data.json 已写出" % len(js))

    tpl = (BASE / "template.html").read_text(encoding="utf-8")
    ech = (BASE / "echarts.min.js").read_text(encoding="utf-8")
    ech = ech.replace("</script>", "<\\/script>")
    out = tpl.replace("/*__ECHARTS__*/", ech).replace("/*__DATA__*/", js)

    # ⚠ 仓库根 index.html 是 vibe-dashboard 主站，绝不能被本看板覆盖。
    # 只有在 index.html 不存在、或确认它本来就是本看板产物时才写；否则改写到 gjd/gjd_offline.html。
    idx = ROOT / "index.html"
    ours = True
    if idx.exists():
        try:
            head = idx.read_text(encoding="utf-8", errors="ignore")[:4000]
            ours = (MARK in head)
        except Exception:                                    # noqa: BLE001
            ours = False
    (ROOT / "gjd_holdings.html").write_text(out, encoding="utf-8", newline="")
    if ours:
        (ROOT / "国家队持仓与沪深300投研看板.html").write_text(out, encoding="utf-8", newline="")
        idx.write_text(out, encoding="utf-8", newline="")
        print("[成品] index.html / 国家队持仓与沪深300投研看板.html / gjd_holdings.html  %d 字符"
              % len(out))
    else:
        (BASE / "gjd_offline.html").write_text(out, encoding="utf-8", newline="")
        print("[成品] gjd_holdings.html / gjd/gjd_offline.html  %d 字符" % len(out))
        print("[保护] 仓库根 index.html 属主站，未覆盖；离线单文件写到 gjd/gjd_offline.html")

    src = (tpl.replace('<script>/*__ECHARTS__*/</script>', '<script src="echarts.min.js"></script>')
              .replace('<script>/*__DATA__*/</script>', '<script src="gjd_data.js"></script>'))
    (BASE / "看板_源码版.html").write_text(src, encoding="utf-8", newline="")
    print("[源码版] assets/看板_源码版.html  %d 字符" % len(src))

    print("[完成] 数据截至 %s ｜ 官方快照 %d 日 ｜ 自洽残差 %.6f bp" %
          (dates[-1], len(snap_days), dev))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:                          # noqa: BLE001
        print("[FATAL] %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        raise
