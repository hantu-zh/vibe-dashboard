# -*- coding: utf-8 -*-
"""pick_tracker.py — 每日选股回测闭环（T+1 / T+5 胜率）

读取 daily_picks.json 的历史选股记录，逐只拉取腾讯日K（前复权），
以「选股当日收盘价」为买入基准，计算每个策略 T+1 / T+5 的
胜率（收益>0占比）与平均收益率，输出紧凑 JSON：pick_tracker.json，
供 index.html「📊 策略回测胜率」卡片展示。

说明：
- 追涨强势股分时段(_10:00/_12:00/...)每个时段作为独立批次统计；
  合并版 key「追涨强势股」在同时段存在时跳过（避免重复计数）。
- 中间候选池(Step1)、点评/复盘类任务不计入。
- 数据不足 T+5 的批次只计入 T+1。

用法: python pick_tracker.py [--max-age-days 90]
"""
import argparse
import bisect
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime

try:
    import paths
except ImportError:
    _WS = os.environ.get("VIBE_WS", os.path.dirname(os.path.abspath(__file__)))

    class _P:
        @staticmethod
        def w(rel):
            parts = rel.replace("\\", "/").split("/")
            base = os.path.join(_WS, "vibe-dashboard") if parts and parts[0] == "vibe-dashboard" else _WS
            sub = parts[1:] if parts and parts[0] == "vibe-dashboard" else parts
            return os.path.join(base, *sub) if sub else base
    paths = _P()

DATA_FILE = os.path.join(paths.w("daily_picks.json"))
OUT_FILE = os.path.join(paths.w("pick_tracker.json"))
KLINE_CACHE = os.path.join(paths.w("pick_tracker_kline_cache.json"))

# 不参与回测的任务 key（候选池/点评/元数据）
SKIP_TASKS = {
    "大力水手菠菜涨停Step1", "sector_rankings", "market_review",
    "weekend_training", "eight_dimension_report", "xueqiu_7x24",
    "weekend_review", "candidates", "count", "generated_at",
    "季度环比增长_latest", "午间收盘点评",
}

_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}


def _norm_strategy(key):
    """追涨强势股_10:00 -> 追涨强势股"""
    return re.sub(r"_\d{2}:\d{2}$", "", key)


def _norm_code(raw):
    """规范化股票代码: 去掉字母前缀/后缀, 只保留数字, 须为6位"""
    c = re.sub(r"[^0-9]", "", str(raw))
    return c if len(c) == 6 else None


def _market_prefix(code):
    c = str(code)
    if c.startswith("92"):
        return "bj"  # 北交所(腾讯若不支持会返回空, 自动跳过)
    if c.startswith(("6", "9", "5")):
        return "sh"
    if c.startswith(("0", "2", "3", "1")):
        return "sz"
    if c.startswith(("4", "8")):
        return "bj"
    return None


def _fetch_kline(opener, full_code, count=160, retries=5):
    """返回 (dates, closes) 或 None"""
    # 注: 沙箱对 web.ifzq.gtimg.cn 会 501 拦截, 用不带 web. 前缀的同源接口
    url = ("https://ifzq.gtimg.cn/appstock/app/fqkline/get"
           "?param=%s,day,,,%d,qfq" % (full_code, count))
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            j = json.loads(opener.open(req, timeout=20).read().decode("utf-8"))
            node = j.get("data", {}).get(full_code, {})
            rows = node.get("qfqday") or node.get("day")
            if not rows:
                return None
            dates = [r[0] for r in rows]
            closes = [float(r[2]) for r in rows]
            return dates, closes
        except Exception:
            time.sleep(min(1.0 * (2 ** i), 10))  # 1,2,4,8,10s 指数退避
    return None


def collect_batches(data):
    """返回 [(date, strategy, batch_key, codes)]"""
    date_keys = sorted(k for k in data.keys() if re.match(r"^\d{4}-\d{2}-\d{2}$", k))
    batches = []
    for d in date_keys:
        day = data[d]
        if not isinstance(day, dict):
            continue
        keys = [k for k in day.keys() if k not in SKIP_TASKS]
        # 追涨强势股: 存在分时段 key 时跳过合并版（与前端逻辑一致）
        has_slots = any(k.startswith("追涨强势股_") for k in keys)
        for k in keys:
            if k == "追涨强势股" and has_slots:
                continue
            tv = day[k]
            picks = tv.get("picks") if isinstance(tv, dict) else tv
            if not isinstance(picks, list) or not picks:
                continue
            codes = []
            for p in picks:
                if isinstance(p, dict) and p.get("code"):
                    c = _norm_code(p["code"])
                    if c:
                        codes.append(c)
            if codes:
                batches.append((d, _norm_strategy(k), k, codes))
    return batches


def main():
    ap = argparse.ArgumentParser(description="每日选股回测闭环")
    ap.add_argument("--max-age-days", type=int, default=90,
                    help="只回测最近 N 天的批次（0=全部）")
    args = ap.parse_args()

    if not os.path.exists(DATA_FILE):
        print("[pick_tracker] daily_picks.json 不存在: %s" % DATA_FILE)
        return 1
    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    batches = collect_batches(data)
    if args.max_age_days > 0:
        cutoff = (datetime.now().strftime("%Y-%m-%d"))
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=args.max_age_days)).strftime("%Y-%m-%d")
        batches = [b for b in batches if b[0] >= cutoff]
    if not batches:
        print("[pick_tracker] 没有可回测的批次")
        return 1

    all_codes = set()
    for _, _, _, codes in batches:
        all_codes.update(codes)
    print("[pick_tracker] 批次 %d 个, 唯一股票 %d 只, 开始拉取日K..." % (len(batches), len(all_codes)))

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # 磁盘缓存: 只拉取缺失的代码, 支持断点续跑
    klines = {}
    if os.path.exists(KLINE_CACHE):
        try:
            with open(KLINE_CACHE, encoding="utf-8") as f:
                klines = json.load(f)
            print("[pick_tracker] 缓存命中 %d 只" % len(klines))
        except Exception:
            klines = {}
    todo = [c for c in sorted(all_codes) if c not in klines]
    print("[pick_tracker] 需新拉 %d 只" % len(todo))
    done = 0
    for code in todo:
        mkt = _market_prefix(code)
        if not mkt:
            continue
        kl = _fetch_kline(opener, mkt + code)
        if kl:
            klines[code] = kl
        done += 1
        if done % 100 == 0:
            print("[pick_tracker] 已拉取 %d/%d" % (done, len(todo)))
            with open(KLINE_CACHE, "w", encoding="utf-8") as f:
                json.dump(klines, f, ensure_ascii=False, separators=(",", ":"))
        time.sleep(0.12)
    with open(KLINE_CACHE, "w", encoding="utf-8") as f:
        json.dump(klines, f, ensure_ascii=False, separators=(",", ":"))
    print("[pick_tracker] 日K获取完成: %d/%d" % (len(klines), len(all_codes)))

    # data_through: 全部K线的最大日期
    data_through = max((ds[-1] for ds, _ in klines.values()), default=None)

    # 统计: strategy -> {"T1": [rets], "T5": [rets]}
    stats = {}
    recent = []
    for d, strat, batch_key, codes in batches:
        t1r, t5r = [], []
        for code in codes:
            kl = klines.get(code)
            if not kl:
                continue
            ds, cs = kl
            i = bisect.bisect_left(ds, d)
            if i >= len(ds):
                continue  # 当日无K线（停牌/未上市）
            base = cs[i]
            if i + 1 < len(ds):
                t1r.append((cs[i + 1] / base - 1) * 100)
            if i + 5 < len(ds):
                t5r.append((cs[i + 5] / base - 1) * 100)
        s = stats.setdefault(strat, {"T1": [], "T5": []})
        s["T1"].extend(t1r)
        s["T5"].extend(t5r)
        recent.append({
            "date": d,
            "strategy": strat,
            "batch": batch_key,
            "n": len(codes),
            "t1_win": round(sum(1 for x in t1r if x > 0) / len(t1r) * 100, 1) if t1r else None,
            "t5_win": round(sum(1 for x in t5r if x > 0) / len(t5r) * 100, 1) if t5r else None,
            "t1_avg": round(sum(t1r) / len(t1r), 2) if t1r else None,
            "t5_avg": round(sum(t5r) / len(t5r), 2) if t5r else None,
        })

    def agg(rets):
        if not rets:
            return None
        return {
            "count": len(rets),
            "win_rate": round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1),
            "avg_ret": round(sum(rets) / len(rets), 2),
        }

    out = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_through": data_through,
        "note": "以选股当日收盘价为买入基准; T+1/T+5 为交易日; 胜率=收益>0占比",
        "strategies": {k: {"T1": agg(v["T1"]), "T5": agg(v["T5"])}
                       for k, v in sorted(stats.items())},
        "recent": sorted(recent, key=lambda r: (r["date"], r["batch"]))[-15:],
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print("[OK] pick_tracker.json 已生成: %d 个策略" % len(out["strategies"]))
    for k, v in out["strategies"].items():
        t1, t5 = v.get("T1"), v.get("T5")
        print("  %-12s T+1: %s | T+5: %s" % (
            k,
            ("%d笔 胜率%.1f%% 均%+.2f%%" % (t1["count"], t1["win_rate"], t1["avg_ret"])) if t1 else "样本不足",
            ("%d笔 胜率%.1f%% 均%+.2f%%" % (t5["count"], t5["win_rate"], t5["avg_ret"])) if t5 else "样本不足",
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
