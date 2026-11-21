# -*- coding: utf-8 -*-
"""
每日数据更新：抓 3 资产真实日线 → 滞后对齐 → 20日RV → 120日分位 → 连续档
→ 输出 regime_data.json 供前端读取重绘

数据源（均已实测稳定）：
  美股  腾讯 usINX  kline/kline
  原油  新浪 GlobalFutures...DailyKLine?symbol=CL
  黄金  新浪 同上 symbol=GC
  基准  腾讯 sh000300

健壮性：单源失败重试 + 保留上次成功快照（stale 标记），绝不输出半成品。
用法：python daily_update.py
"""
import json, urllib.request, ssl, time, statistics, os, sys, csv, random
from datetime import datetime

ssl._create_default_https_context = ssl._create_unverified_context
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"}

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "regime_data.json")
EVENT_LOG = os.path.join(HERE, "event_log.csv")       # 长期事件库（滚动累积）
MC_STATE = os.path.join(HERE, "mc_state.json")        # 上次MC重跑时的样本数游标
MC_OUT = os.path.join(HERE, "mc_rerun.json")          # 蒙特卡洛重跑结果
TH = 0.60          # 定稿阈值（样本最大、最显著）
RV_WIN = 20
Q_WIN = 120
MC_STEP = 5        # 每积累 5 个新已验证样本，触发一次蒙特卡洛重跑


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            if i == tries - 1:
                raise RuntimeError(f"抓取失败 {url[:70]} -> {e}")
            time.sleep(0.8)


def tencent_kline(code, start="2019-01-01", end="", count=2000):
    # 注意：腾讯 kline 单次 count 上限约 2000，超过会返回 data=[]（空 list 非 dict）
    if not end:
        end = datetime.now().strftime("%Y-%m-%d")
    url = f"https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={code},day,{start},{end},{count}"
    j = json.loads(fetch(url))
    data = j.get("data")
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"腾讯K线返回异常(data={type(data).__name__}), code={code} count={count}")
    key = list(data.keys())[0]
    rows = data[key].get("day", [])
    return {r[0]: float(r[2]) for r in rows if r[2]}


def sina_futures(symbol, start="2019-01-01"):
    url = f"https://stock.finance.sina.com.cn/futures/api/openapi.php/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={symbol}"
    j = json.loads(fetch(url))
    rows = j["result"]["data"]
    return {r["date"]: float(r["close"]) for r in rows
            if r.get("close") and float(r["close"]) > 0 and r["date"] >= start}


def shift_series(series, dates):
    """对每个 A 股交易日 d，取海外资产在 d 之前最近一个交易日的值（滞后1日防偷价）"""
    sd = sorted(series.keys())
    out, idx = {}, 0
    for d in dates:
        # 双指针推进到 < d 的最后一条
        while idx < len(sd) and sd[idx] < d:
            idx += 1
        if idx > 0:
            out[d] = series[sd[idx - 1]]
    return out


def calc_rv(aligned, dates, win=RV_WIN):
    """20日滚动已实现波动率（年化），按 A 股交易日序列"""
    rv = {}
    for i in range(win, len(dates)):
        rets, ok = [], True
        for j in range(i - win + 1, i + 1):
            a, b = aligned.get(dates[j]), aligned.get(dates[j - 1])
            if a is None or b is None or b == 0:
                ok = False
                break
            rets.append(a / b - 1)
        if ok:
            rv[dates[i]] = statistics.pstdev(rets) * (252 ** 0.5)
    return rv


def rolling_pct(rv, win=Q_WIN):
    ds = sorted(rv.keys())
    out = {}
    for i, d in enumerate(ds):
        lo = max(0, i - win + 1)
        seg = [rv[x] for x in ds[lo:i + 1]]
        out[d] = sum(1 for x in seg if x <= rv[d]) / len(seg)
    return out


def build():
    print("=" * 60)
    print("每日数据更新", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    print("抓取 标普500(usINX)...")
    spx = tencent_kline("usINX")
    print(f"  {len(spx)} 条, 最新 {max(spx) if spx else '-'}")
    print("抓取 沪深300(sh000300)...")
    hs = tencent_kline("sh000300")
    print(f"  {len(hs)} 条, 最新 {max(hs) if hs else '-'}")
    print("抓取 WTI原油(CL)...")
    cl = sina_futures("CL")
    print(f"  {len(cl)} 条, 最新 {max(cl) if cl else '-'}")
    print("抓取 伦敦金(GC)...")
    gc = sina_futures("GC")
    print(f"  {len(gc)} 条, 最新 {max(gc) if gc else '-'}")

    # 完整性校验：单源不足 300 条即视为失败
    for name, s in [("美股", spx), ("原油", cl), ("黄金", gc), ("沪深300", hs)]:
        if len(s) < 300:
            raise RuntimeError(f"{name} 数据不足({len(s)}条)，放弃本次更新")

    dates = sorted(hs.keys())
    src = {"美股": spx, "原油": cl, "黄金": gc}
    rv_all, q_all = {}, {}
    for name, s in src.items():
        aligned = shift_series(s, dates)          # 关键：海外滞后1日
        rv_all[name] = calc_rv(aligned, dates)
        q_all[name] = rolling_pct(rv_all[name])

    common = sorted(set.intersection(*[set(q) for q in q_all.values()]))
    if len(common) < Q_WIN + 30:
        raise RuntimeError(f"共同有效日期不足({len(common)}天)")

    # 连续档 + 等权对照 + 各资产分位
    regime, regimeA, qs = [], [], []
    for d in common:
        qv = [q_all[n][d] for n in src]
        up = sum(1 for q in qv if q >= TH)
        down = sum(1 for q in qv if q <= 1 - TH)
        regime.append(round((up - down + 3) / 2, 1))          # 连续档 0~3，步长0.5
        regimeA.append(min(3, int(sum(qv) / 3 * 3)))
        qs.append([round(x, 3) for x in qv])

    # 事件：连续档上穿 2.5（3资产齐升），间隔>=20日；标注后20日回撤/收益
    hsv = [hs.get(d) for d in common]
    events = []
    for i in range(1, len(common) - 21):
        if regime[i] >= 2.5 and regime[i - 1] < 2.5 and (not events or i - events[-1]["i"] >= 20):
            seg, mdd = [], 0.0
            peak = hsv[i + 1]
            for j in range(i + 1, min(i + 21, len(hsv))):
                if hsv[j]:
                    peak = max(peak, hsv[j])
                    mdd = min(mdd, hsv[j] / peak - 1)
            t20 = (hsv[i + 20] / hsv[i + 1] - 1) if hsv[i + 20] and hsv[i + 1] else None
            events.append({"i": i, "date": common[i],
                           "dd": round(mdd * 100, 1),
                           "t20": round(t20 * 100, 1) if t20 is not None else None})

    # 当前信号
    last = regime[-1]
    up_last = sum(1 for q in qs[-1] if q >= TH)
    down_last = sum(1 for q in qs[-1] if q <= 1 - TH)
    if last >= 2.5:
        level, hint = "⚠️ 风险释放/观望", "海外波动齐升，A股多已承压 → 风险释放观察窗，勿盲目杀跌（非减仓指令）"
    elif last <= 0.5:
        level, hint = "🌱 情绪修复/逢低", "波动回落，风险偏好修复，可适度进攻"
    elif up_last < 2 and down_last < 2:
        level = "分歧观察"
        hint = "分歧状态：当前仅 3 个资产（美股/原油/黄金），波动未形成共振，暂无明确方向信号，建议保持观望/中性。"
    else:
        level, hint = "分歧观察", "状态信号仅供观察，不构成择时建议。"

    payload = {
        "meta": {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "latest": common[-1], "days": len(common),
            "threshold": TH, "rv_win": RV_WIN, "q_win": Q_WIN,
            "stale": False,
            "sources": {"美股": "腾讯 usINX", "原油": "新浪 CL", "黄金": "新浪 GC", "基准": "腾讯 sh000300"},
        },
        "dates": common,
        "rv": {n: [round(rv_all[n].get(d), 2) if rv_all[n].get(d) is not None else None for d in common] for n in src},
        "q": qs,
        "regime": regime,
        "regimeA": regimeA,
        "hs300": [round(x, 2) if x else None for x in hsv],
        "events": events,
        "now": {"regime": last, "up": up_last, "down": down_last, "level": level, "hint": hint},
    }
    return payload


def update_event_log(payload):
    """维护长期事件库 event_log.csv：新增触发去重追加，回填历史事件的 DD/T20。
    返回 (总事件数, 已验证数, 本次新增数)。"""
    events = payload["events"]
    existing = {}
    if os.path.exists(EVENT_LOG):
        with open(EVENT_LOG, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                existing[row["date"]] = row
    added = 0
    hs = payload.get("hs300", [])
    for ev in events:
        d = ev["date"]
        if d not in existing:
            existing[d] = {"date": d, "regime": "2.5", "hs300_close": "",
                           "dd": "", "t20": "", "first_seen": datetime.now().strftime("%Y-%m-%d")}
            added += 1
        # 回填收盘价 + 已到期的 DD、T20（历史回放每次都能算全）
        idx = ev.get("i")
        if idx is not None and 0 <= idx < len(hs) and hs[idx]:
            existing[d]["hs300_close"] = str(hs[idx])
        existing[d]["dd"] = "" if ev["dd"] is None else str(ev["dd"])
        existing[d]["t20"] = "" if ev["t20"] is None else str(ev["t20"])
    fields = ["date", "regime", "hs300_close", "dd", "t20", "first_seen"]
    with open(EVENT_LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for d in sorted(existing):
            w.writerow({k: existing[d].get(k, "") for k in fields})
    verified = sum(1 for r in existing.values() if r["dd"] != "" and r["t20"] != "")
    return len(existing), verified, added


def mc_rerun_if_due(verified_count):
    """已验证样本比上次重跑时新增 >= MC_STEP 个，则重跑轻量蒙特卡洛。
    用事件库真实的 T20 收益 vs 随机抽取同等数量交易日的 20 日持有收益做对照。"""
    last_n = (0, 0)
    if os.path.exists(MC_STATE):
        try:
            st = json.load(open(MC_STATE, encoding="utf-8"))
            last_n = st.get("last_verified", 0), st.get("last_total", 0)
        except Exception:
            pass
    if verified_count - last_n[0] < MC_STEP:
        return False, f"已验证样本 {verified_count}，距下次重跑还差 {MC_STEP - (verified_count - last_n[0])} 个"

    # 读取事件库已验证 T20
    t20s = []
    with open(EVENT_LOG, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["t20"] not in ("", None):
                t20s.append(float(row["t20"]))
    n = len(t20s)
    if n < 3:
        return False, f"已验证样本仅 {n}，不足 3 个，跳过 MC"

    real_mean = statistics.mean(t20s)
    real_win = sum(1 for x in t20s if x > 0) / n

    # 随机基准：从 regime_data.json 的沪深300序列随机抽 n 个起点，算 20 日持有收益，重复 1000 次
    rng = random.Random(20261121)
    hs = json.load(open(OUT, encoding="utf-8"))["hs300"]
    pool = []
    for i in range(len(hs) - 20):
        if hs[i] and hs[i + 20]:
            pool.append((hs[i + 20] / hs[i] - 1) * 100)
    rand_means = []
    for _ in range(1000):
        samp = rng.choices(pool, k=n)
        rand_means.append(statistics.mean(samp))
    pct = sum(1 for x in rand_means if x <= real_mean) / len(rand_means)

    result = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verified": n,
        "real_mean_t20": round(real_mean, 2),
        "real_win_rate": round(real_win, 4),
        "rand_mean_p05": round(sorted(rand_means)[50], 2),
        "rand_mean_p50": round(sorted(rand_means)[500], 2),
        "rand_mean_p95": round(sorted(rand_means)[950], 2),
        "real_mean_percentile": round(pct, 4),
        "note": "real_mean_percentile 越低=真实信号后续收益越差于随机入场，反向印证识别风险有效",
    }
    json.dump(result, open(MC_OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump({"last_verified": verified_count, "last_total": n,
               "updated": result["run_at"]}, open(MC_STATE, "w", encoding="utf-8"), ensure_ascii=False)
    return True, f"重跑完成：{n} 个已验证样本，真实均值 {real_mean:+.2f}%，随机分位 {pct:.1%}"


def main():
    try:
        payload = build()
    except Exception as e:
        print(f"\n[失败] {e}")
        if os.path.exists(OUT):
            old = json.load(open(OUT, encoding="utf-8"))
            old["meta"]["stale"] = True
            old["meta"]["stale_reason"] = str(e)[:120]
            old["meta"]["stale_checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            json.dump(old, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
            print(f"已保留上次快照并标记 stale（数据截至 {old['meta']['latest']}）")
            return 2
        print("无历史快照可用，退出")
        return 1
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n[OK] 已写 {OUT}")
    print(f"  数据区间 {payload['meta']['latest']} | {payload['meta']['days']} 天 | 当前 {payload['now']['regime']} 档 {payload['now']['level']}")
    print(f"  风险确认事件 {len(payload['events'])} 个 | 文件 {os.path.getsize(OUT)//1024} KB")

    # 事件库长期累积 + 到期蒙特卡洛重跑
    try:
        total, verified, added = update_event_log(payload)
        print(f"\n[事件库] {EVENT_LOG}")
        print(f"  累计 {total} 次触发（本次新增 {added}）| 已验证 {verified} 次（DD/T20 齐全）")
        ran, msg = mc_rerun_if_due(verified)
        print(f"  [MC重跑] {'✅ ' if ran else '○ '}{msg}")
    except Exception as e:
        print(f"  [事件库警告] 不影响主数据：{e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
