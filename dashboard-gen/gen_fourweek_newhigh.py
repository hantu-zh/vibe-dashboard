#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""四周新高（近52周高 + 温和突破 + 不远离25日线）选股生成器。

产出（参照 LST 的「每日冻结快照」结构，便于观察信号连续性与回测）：
  - fourweek_newhigh_history.json  （每日冻结快照，供 strong.html 的「四周新高」tab 渲染）
        { generated, window_days, formula, dates:[...], data:{ "YYYY-MM-DD": [pick,...] } }
  - fourweek_newhigh.json          （最新一日快照，便于核对/其它入口）
  - fourweek_newhigh.csv           （最新一日明细，透明核对）

选股逻辑（日线，收盘对收盘）—— 单日核心信号：
  A. 相对强度：收盘 ≥ 近 250 日(≈52周)最高价 ×(1−NEAR_52W_PCT)（默认 0.95）→ 一年高位。
  B. 温和突破：当日收盘 > 前20日(4周)最高收盘，且突破幅度(收盘/前20日高−1) ≤ GENTLE_MAX
     （默认 3.5%）→ 新高但不透支。
  D. 贴近25日线（用户要求）：收盘/MA25 − 1 ∈ [DEV25_MIN, DEV25_MAX]（默认 [−8%, +12%]）
     → 不远离中线、不过度超买。
  F. 流动性：近5日日均成交额 ≥ AMOUNT_5D_MIN_YI（默认 1 亿）。
  E. 趋势(MA25>MA60)：仅作展示/辅助排序，不再做硬过滤（避免把样本饿死）。

「连续五天」如何实现（参照 LST）：
  不在单日 K 线里硬卡「连续5日」，而是把「当日核心信号」作为每日快照冻结存档，
  跨交易日累计同一标的被连续选出的天数 → consec_days（连创天数）。CI 每日运行后，
  consec_days 自然增长；达到 5 即标记 🔥。首日部署 consec_days 均为 1，随后逐日累积。

数据：东财优先 → 腾讯兜底 → 新浪三级兜底。沙箱仅新浪可达时加 `sina`。
参数集中在顶部，便于调参。
"""
import json, os, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

WS = os.path.dirname(os.path.abspath(__file__))
UNIV = os.path.join(WS, "a_shares.json")
OUT_JSON = os.path.join(WS, "..", "fourweek_newhigh.json")
OUT_HISTORY = os.path.join(WS, "..", "fourweek_newhigh_history.json")
OUT_CSV = os.path.join(WS, "..", "fourweek_newhigh.csv")

# ───────── 可调参数（顶部集中） ─────────
LOOKBACK_52W = 250      # 交易日：近一年(48~53周)最高价窗口
NEAR_52W_PCT = 0.05     # A：收盘距52周高 ≤ 5% 视为「近一年高位」
WIN_4W = 20             # 交易日：4周新高窗口
GENTLE_MAX = 0.035      # B：突破幅度 ≤ 3.5% 视为温和（>0 自带，因须创20日新高）
DEV25_MIN = -0.08       # D：偏离25日线下限（不深跌）
DEV25_MAX = 0.12        # D：偏离25日线上限（不过度超买）
AMOUNT_5D_MIN_YI = 1.0  # F：近5日日均成交额下限（亿）
MIN_BARS = 260          # 最少历史交易日（覆盖 250 日窗口 + 缓冲）
FETCH_N = 420           # 单次拉取日线根数（≈250 交易日 + 节假日缓冲）
WINDOW_DAYS = 30        # 冻结快照窗口「缺省值」；已有 history 文件时以文件里的 window_days 为准
                        # （strong.html 会把它渲染成标题，线上现为 8，故不覆盖已有值）

# ───────── 并发与时间预算（2026-10-13 修复） ─────────
# 本脚本随 sync.yml 每交易日 15:45 运行，而 run_task 的硬超时只有 420s；
# 全宇宙 5570 只若串行拉日线必然超时被 kill —— 被杀就无产物、下轮又从头再来，
# 永远产不出数据。故：① 改线程池并发；② 追加 DEADLINE_SEC 软预算，
# 到点提前收尾但仍把已扫结果落盘（宁可少扫一部分，也不要"零产物"死循环）。
WORKERS = int(os.environ.get("WORKERS", "16"))             # 并发线程数
DEADLINE_SEC = int(os.environ.get("DEADLINE_SEC", "330"))  # 扫描软预算（秒），必须 < 420
MIN_COVERAGE = 0.6      # 行情覆盖率下限：低于此值判定数据源限流，宁可不写也不要假数据

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://quote.eastmoney.com/"}

FOURWEEK_FORMULA = """{================ 四周新高（近52周高 + 温和突破 + 不远离25日线）================}
N52:=250; N4W:=20;
HH52:=HHV(H,N52);                         {近52周最高价}
NEAR52:=C>=HH52*0.95;                     {收盘在近一年高位(距高≤5%)}
BRKREF:=HHV(REF(C,1),N4W);                {前20日(4周)最高收盘}
BRK:=C>BRKREF;                           {今日创4周新高}
GENTLE:=C/BRKREF<=1.035;                 {突破幅度≤3.5%(温和，不透支)}
MA25V:=MA(C,25);
IND25:=C/MA25V>0.92 AND C/MA25V<1.12;    {不远离25日线(±区间)}
AMT5:=MA(VOL,5)*C*100/1e8;               {近5日均额(亿)}
LIQ:=AMT5>=1;                            {流动性过滤}
XG:=NEAR52 AND BRK AND GENTLE AND IND25 AND LIQ;
{说明：本公式为日线形态(单日信号)，与 gen_fourweek_newhigh.py 口径一致；}
{「连续天数(consec_days)」由每日快照跨交易日累计，非本公式内计算；北交所流动性多数不达标自然剔除。}"""


def _get_json(url, tries=2, base_sleep=1.2):
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = repr(e)
            time.sleep(base_sleep * (i + 1))
    return {"__err__": last}


def _em_secid(code):
    return ("1." if code[0] == "6" else "0.") + code


def _tx_symbol(code):
    return ("sh" if code[0] == "6" else "sz") + code


def fetch_em(code):
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode({
        "secid": _em_secid(code), "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "klt": "101", "fqt": "0", "lmt": str(FETCH_N)})
    d = _get_json(url)
    if "__err__" in d:
        return None, []
    dd = d.get("data") or {}
    out = []
    for s in dd.get("klines") or []:
        p = s.split(",")
        if len(p) < 6:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[2]), float(p[4]), float(p[3]), int(p[5])])
        except Exception:
            continue
    return dd.get("name") or "", out


def fetch_tx(code):
    sym = _tx_symbol(code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           + urllib.parse.urlencode({"param": f"{sym},day,,,{FETCH_N},"}))
    d = _get_json(url, tries=1, base_sleep=0.5)
    if "__err__" in d:
        return "", []
    node = (d.get("data") or {}).get(sym) or {}
    k = node.get("day") or node.get("qfqday") or []
    out = []
    for p in k:
        if len(p) < 6:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[2]), float(p[4]), float(p[3]), int(p[5])])
        except Exception:
            continue
    return "", out


def fetch_sina(code):
    c = code[0]
    if c == "6":
        sym = "sh" + code
    elif c in ("8", "9", "4"):
        sym = "bj" + code
    else:
        sym = "sz" + code
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
           + urllib.parse.urlencode({"symbol": sym, "scale": "240", "ma": "no",
                                     "datalen": str(FETCH_N)}))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            txt = r.read().decode("utf-8").strip()
        if not txt.startswith("["):
            return "", []
        d = json.loads(txt)
    except Exception:
        return "", []
    out = []
    for it in d or []:
        try:
            out.append([it["day"][:10], float(it["open"]), float(it["close"]),
                        float(it["low"]), float(it["high"]),
                        int(float(it["volume"]) / 100)])  # 新浪 volume 为股 → 手
        except Exception:
            continue
    return "", out


def fetch_daily(code, src_order):
    for s in src_order:
        try:
            name, rows = _fetch_by(s, code)
            if rows:
                return name, rows, s
        except Exception:
            continue
    return "", [], "none"


PROBE_CODE = "600036"   # 招商银行：主板、长期上市，用于探测各数据源可用性


def _fetch_by(src, code):
    if src == "em":
        return fetch_em(code)
    if src == "tx":
        return fetch_tx(code)
    return fetch_sina(code)


def probe_sources(src_order):
    """启动前用一只基准股票探测各数据源，剔除不可达的源。

    为什么必须探测（2026-10-13 修复）：单个源失败一次要付出重试 + 退避的代价
    （em 是 tries=2 且退避 1.2s/2.4s，单只约 3.6s 白等）。全宇宙 5570 只若逐个去撞
    一个已经挂掉的源，光退避就是上万秒，必然被 run_task 的 420s 硬超时 kill，
    结果是「零产物 -> 下轮重来」的死循环。探测 3 个请求即可把最坏情况从 30min 级
    压回 2~4min。
    """
    ok = []
    for s in src_order:
        try:
            _n, rows = _fetch_by(s, PROBE_CODE)
        except Exception as e:
            print(f"  [probe] {s} 异常({e!r})，剔除")
            continue
        if rows:
            ok.append(s)
            print(f"  [probe] {s} 可用（{len(rows)} 根日线）")
        else:
            print(f"  [probe] {s} 不可用，剔除")
    return ok


def is_excluded(name):
    if not name:
        return False
    u = name.upper()
    return ("ST" in u) or u.startswith("*") or ("退" in u)


def qualifies(closes, highs, vols):
    """返回单日核心信号指标 dict；不满足返回 None。趋势(MA25>MA60)仅展示，不硬过滤。"""
    n = len(closes)
    if n < MIN_BARS:
        return None
    high52 = max(highs[max(0, n - LOOKBACK_52W):n])
    if high52 <= 0:
        return None
    c0 = closes[n - 1]
    near_thr = high52 * (1 - NEAR_52W_PCT)
    if c0 < near_thr:
        return None

    # B. 温和 4 周突破
    ref20 = max(closes[max(0, n - 1 - WIN_4W):n - 1])
    if ref20 <= 0:
        return None
    break_today = c0 / ref20 - 1
    if break_today <= 0 or break_today > GENTLE_MAX:
        return None

    # D. 贴近 25 日线（用户要求）
    ma25 = sum(closes[n - 25:n]) / 25.0
    dev25 = c0 / ma25 - 1
    if dev25 < DEV25_MIN or dev25 > DEV25_MAX:
        return None

    # F. 流动性
    amt5 = sum(vols[n - 5:n]) * 100.0 * c0 / 1e8
    if amt5 < AMOUNT_5D_MIN_YI:
        return None

    # E. 趋势（展示 + 辅助排序，不硬过滤）
    ma60 = sum(closes[n - 60:n]) / 60.0 if n >= 60 else None
    trend = (ma60 is not None) and (ma25 > ma60)
    dist_52w = c0 / high52 - 1

    return {
        "dist_52w_pct": dist_52w * 100,
        "break_pct": break_today * 100,
        "dev25_pct": dev25 * 100,
        "ma25": round(ma25, 2),
        "ma60": round(ma60, 2) if ma60 is not None else None,
        "trend": bool(trend),
        "amount_5d_yi": round(amt5, 2),
        "change_pct": (closes[n - 1] / closes[n - 2] - 1) * 100 if n >= 2 else 0.0,
        "change_5d": (closes[n - 1] / closes[n - 6] - 1) * 100 if n >= 6 else 0.0,
    }


def build_pick(code, name, q, consec_days):
    c0 = None  # price 由调用方在扫描后统一补；此处先占位
    rs = max(0.0, min(10.0, (q["dist_52w_pct"] / 100.0 + NEAR_52W_PCT) / NEAR_52W_PCT * 10))
    rs = round(rs, 1)
    reason = (f"近52周高{q['dist_52w_pct']:+.1f}% | "
              f"4周新高+{q['break_pct']:.1f}% | "
              f"偏离25日线{q['dev25_pct']:+.1f}% | "
              f"连续{consec_days}日 | 5日均额{q['amount_5d_yi']:.1f}亿")
    return {
        "code": code, "name": name,
        "price": None,
        "change_pct": round(q["change_pct"], 2),
        "change_5d": round(q["change_5d"], 2),
        "dist_52w_pct": round(q["dist_52w_pct"], 2),
        "break_pct": round(q["break_pct"], 2),
        "dev25_pct": round(q["dev25_pct"], 2),
        "ma25": q["ma25"], "ma60": q["ma60"], "trend": q["trend"],
        "amount_5d_yi": q["amount_5d_yi"],
        "consec_days": consec_days,
        "score": rs,
        "tag": "fourweek",
        "reason": reason,
    }


def consec_for(code, today, prior_dates, hist_data):
    """同一标的在 prior_dates（均 < today，升序）中，从 today 往前连续被选中的天数 +1。"""
    cnt = 0
    for d in reversed(prior_dates):
        if any(p.get("code") == code for p in hist_data.get(d, [])):
            cnt += 1
        else:
            break
    return cnt + 1


def scan_one(item, src_order, asof=None):
    """单只股票的工作单元（线程池里跑，只做纯计算，不碰共享状态）。

    asof：只保留该日期（含）之前的日线，用于「补历史快照」（见 --asof 参数）。

    返回：
      None                              → 无代码 / 无行情，跳过
      {"code":.., "close":..}           → 取到行情但未命中（close 供历史快照补算前向收益）
      {"code":.., "close":.., "name":, "q":{...}}  → 命中
    """
    code = item.get("code")
    if not code:
        return None
    uname = item.get("name", "")
    name, rows, _src = fetch_daily(code, src_order)
    if not rows:
        return None
    if asof:
        rows = [r for r in rows if r[0] <= asof]
    if not rows:
        return None
    closes = [r[2] for r in rows]
    highs = [r[4] for r in rows]
    vols = [r[5] for r in rows]
    hit = {"code": code, "close": closes[-1]}
    if is_excluded(uname) or is_excluded(name):
        return hit
    q = qualifies(closes, highs, vols)
    if not q:
        return hit
    hit["name"] = uname or name
    hit["q"] = q
    return hit


def main():
    src_order = ["em", "tx", "sina"]
    limit = None
    asof = None
    for a in sys.argv[1:]:
        if a == "sina":
            src_order = ["sina"]
        elif a.startswith("--limit="):
            limit = int(a.split("=")[1])
        elif a.startswith("--asof="):
            # 补历史快照：以指定交易日为「今天」重算一次单日信号。
            # 用途：某天因流水线故障漏跑时，事后补齐该交易日的冻结快照（consec_days 才能正确累计）。
            asof = a.split("=")[1].strip()

    if not os.path.exists(UNIV):
        print("缺少 a_shares.json（宇宙文件）")
        return
    univ = json.load(open(UNIV, encoding="utf-8")).get("stocks", [])
    print(f"宇宙 {len(univ)} 只，候选数据源 {src_order}")
    # 探测各源可用性后再开扫（剔除不可达源，避免全宇宙逐个撞墙白等退避）
    src_order = probe_sources(src_order)
    if not src_order:
        print("所有数据源均不可用，放弃本次（不写文件，交由后续 run 自动补跑）")
        return
    print(f"实际使用数据源顺序 {src_order}")

    today = asof or time.strftime("%Y-%m-%d")
    if asof:
        # 校验 asof 确实是交易日（基准股在该日应有日线），否则快照会张错日期
        _n, _rows = _fetch_by(src_order[0], PROBE_CODE)
        _rows = [r for r in _rows if r[0] <= asof]
        last_bar = _rows[-1][0] if _rows else "无"
        if not _rows or last_bar != asof:
            print(f"--asof={asof} 不是交易日（基准股 {PROBE_CODE} 该日最后日线为 {last_bar}），放弃")
            return
        print(f"补历史快照模式：以 {asof} 为基准日")
    else:
        # 基准日守卫：快照日期用 today，所以必须先确认数据源里「今天」的日线已经就绪，
        # 否则会把上一交易日的信号张成今天的日期（静默错一天）。未就绪就放弃不写，
        # 交给后续 run 补跑（task_state 未标记 → 15 分钟后重试）。
        _n, _rows = _fetch_by(src_order[0], PROBE_CODE)
        last_bar = _rows[-1][0] if _rows else "无"
        if last_bar != today:
            print(f"基准股 {PROBE_CODE} 最新日线为 {last_bar}，尚未到 {today}（当日日线未就绪），"
                  f"本次不写文件，等下一轮 run 补跑")
            return
        print(f"当日日线已就绪（基准股最新日线 {last_bar}）")
    # 载入历史快照（用于累计 consec_days 与补算「选出后涨跌幅 / 累计天数」）
    history = {"generated": today, "window_days": WINDOW_DAYS,
               "formula": FOURWEEK_FORMULA, "dates": [], "data": {}}
    if os.path.exists(OUT_HISTORY):
        try:
            history = json.load(open(OUT_HISTORY, encoding="utf-8"))
            history.setdefault("window_days", WINDOW_DAYS)
            history.setdefault("formula", FOURWEEK_FORMULA)
        except Exception:
            pass
    prior_dates = sorted(d for d in history.get("dates", []) if d < today)

    results = []          # 今日命中（不含 price，稍后补）
    latest_close = {}     # code -> 今日收盘（用于补算历史快照的前向收益）
    scanned = 0
    t0 = time.time()
    todo = [it for it in univ if it.get("code")]
    if limit:
        todo = todo[:limit]
    n_total = len(todo)
    timed_out = False
    print(f"并发 {WORKERS} 线程，软预算 {DEADLINE_SEC}s")

    with ThreadPoolExecutor(max_workers=max(4, WORKERS)) as ex:
        idx = 0
        while idx < n_total:
            if time.time() - t0 > DEADLINE_SEC:
                timed_out = True
                print(f"  ⏰ 已达软预算 {DEADLINE_SEC}s，提前收尾：已扫 {scanned}/{n_total} 只")
                break
            chunk = todo[idx:idx + WORKERS * 4]
            idx += len(chunk)
            for res in ex.map(lambda it: scan_one(it, src_order, asof), chunk):
                scanned += 1
                if not res:
                    continue
                latest_close[res["code"]] = res["close"]
                if "q" in res:
                    cd = consec_for(res["code"], today, prior_dates, history.get("data", {}))
                    pk = build_pick(res["code"], res["name"], res["q"], cd)
                    pk["price"] = round(res["close"], 2)
                    results.append(pk)
                if scanned % 500 == 0:
                    print(f"  [{scanned}/{n_total}] 今日命中 {len(results)} 只, {time.time()-t0:.0f}s")
            # 熔断：开扫一段后仍「一根日线都没取到」，说明所选源在跑到一半时挂了。
            # 此时立即放弃（不写文件），让下一轮 run 重新探测数据源，避免空跑满预算。
            if scanned >= 80 and not latest_close:
                print(f"  ✖ 熔断：已扫 {scanned} 只但无任何有效行情（数据源疑似中途失效），"
                      f"本次放弃、不写文件，等下一轮补跑")
                return

    if timed_out:
        print(f"  ⚠️ 本次为部分扫描（{scanned}/{n_total}），仍按当前结果落盘；"
              f"下一交易日会覆盖式重扫，不影响最终收敛。")

    # 行情覆盖率闸门：数据源被限流时会「静默返回空」，落盘出去就是一份假数据。
    # 覆盖率按「已扫」为分母，因此与是否提前收尾无关。
    n_ok = len(latest_close)
    coverage = n_ok / scanned if scanned else 0.0
    print(f"行情覆盖 {n_ok}/{scanned} ({coverage:.1%})，命中 {len(results)} 只")
    if coverage < MIN_COVERAGE:
        print(f"  ✖ 覆盖率低于 {MIN_COVERAGE:.0%}，判定数据源限流/异常，本次不写文件，等下一轮补跑")
        return

    # 补算历史快照的「选出后涨跌幅 / 累计天数」（用今日收盘）
    all_dates = sorted(set(history.get("dates", [])) | {today})
    for dt in all_dates:
        if dt >= today:
            continue
        for p in history["data"].get(dt, []):
            lc = latest_close.get(p.get("code"))
            p["days_since"] = sum(1 for d in all_dates if dt < d <= today)
            p["post_change_pct"] = round((lc - p["price"]) / p["price"] * 100, 2) if (lc and p.get("price")) else None

    results.sort(key=lambda x: (x["consec_days"], x["score"]), reverse=True)

    # 写入今日快照（覆盖同日）
    history["data"][today] = results
    history["dates"] = sorted(set(history["dates"]) | {today})
    # 裁剪窗口：以 history 里已落地的 window_days 为准（strong.html 直接把它渲染成
    # 「每日冻结快照（最近 N 个交易日）」的标题，线上是 8）。若此处用代码常量 30 裁剪，
    # 就会出现「标题写 8、实际塞了 30 天」的对不上。
    try:
        win = int(history.get("window_days") or WINDOW_DAYS)
    except (TypeError, ValueError):
        win = WINDOW_DAYS
    keep = sorted(history["dates"])[-max(1, win):]
    history["dates"] = keep
    history["data"] = {d: history["data"][d] for d in keep}
    history["generated"] = today
    json.dump(history, open(OUT_HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"date": today, "picks": results}, open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # CSV 透明核对（最新一日）
    cols = ["code", "name", "price", "change_pct", "change_5d", "dist_52w_pct",
            "break_pct", "dev25_pct", "ma25", "ma60", "trend", "amount_5d_yi",
            "consec_days", "score", "tag", "reason"]
    import csv
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k) for k in cols})
    print(f"完成：扫描 {scanned} 只，今日命中 {len(results)} 只 → {OUT_HISTORY}")


if __name__ == "__main__":
    main()
