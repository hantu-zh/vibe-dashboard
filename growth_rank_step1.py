# -*- coding: utf-8 -*-
"""
财报连续增长筛选系统 — Step 1
只做财报层筛选，输出 candidates.json 供 Step2 使用
用法: python growth_rank_step1.py
输出: growth_rank_candidates.json
"""

import sys, os, json, time, random
from datetime import datetime

# 禁用tqdm进度条（必须在import akshare之前设置）
os.environ["TQDM_DISABLE"] = "1"
try:
    import tqdm as _tqdm_mod
    _tqdm_mod.tqdm.disable = True
except Exception:
    pass

try:
    import akshare as ak
    import warnings
    warnings.simplefilter("ignore")
except ImportError:
    print("[ERROR] pip install akshare")
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept-Encoding": "gzip, deflate",
}

RANK_HISTORY_FILE = "C:/Users/china/.qclaw/workspace/vibe_rank_history.json"
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "growth_rank_candidates.json")


def gz_fetch(url, timeout=15):
    import urllib.request, gzip
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.info().get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8-sig", errors="replace")
        if text.startswith("\ufeff"):
            text = text[1:]
        return text


def find_col(df, *keywords):
    for col in df.columns:
        s = str(col).strip()
        if all(k in s for k in keywords):
            return col
    return None


def safe_float(v):
    try:
        f = float(v)
        return None if (f != f or f == float("inf")) else f
    except (TypeError, ValueError):
        return None


def fetch_quarterly_reports(dates):
    """获取多季度业绩报表，返回 {code: {name, industry, rev_qoq_list, profit_qoq_list, dates, eps, roe, gross}}"""
    stock_map = {}
    for date_str in dates:
        sys.stdout.write(f"\r  [{datetime.now().strftime('%H:%M:%S')}] {date_str} ({len(stock_map)}已有...)")
        sys.stdout.flush()
        try:
            # 给 akshare 调用加超时保护（10秒）
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(ak.stock_yjbb_em, date=date_str)
                df = future.result(timeout=10)
        except concurrent.futures.TimeoutError:
            print(f" [TIMEOUT]")
            time.sleep(0.5)  # 超时后短暂休息
            continue
        except Exception as e:
            print(f" [FAIL:{e}]")
            time.sleep(0.5)
            continue
        if df is None or df.empty:
            print(" [EMPTY]")
            continue

        c_code     = find_col(df, "股票代码")
        c_name     = find_col(df, "股票简称")
        c_rev_qoq  = find_col(df, "营业总收入", "季度环比增长")
        c_prof_qoq = find_col(df, "净利润", "季度环比增长")
        c_eps      = find_col(df, "每股收益")
        c_roe      = find_col(df, "净资产收益率")
        c_gross    = find_col(df, "销售毛利率")
        c_industry = find_col(df, "所处行业")

        if not c_code:
            print(f" [COL_ERR]")
            continue

        for _, row in df.iterrows():
            code = str(row[c_code]).strip().replace(".0", "")
            if not code or code in ("None", "-"):
                continue
            name = str(row[c_name]).strip() if c_name else ""

            rev_qoq  = safe_float(row[c_rev_qoq])  if c_rev_qoq  else None
            prof_qoq = safe_float(row[c_prof_qoq]) if c_prof_qoq else None

            if code in stock_map:
                stock_map[code]["rev_qoq_list"].insert(0, rev_qoq)
                stock_map[code]["profit_qoq_list"].insert(0, prof_qoq)
                stock_map[code]["dates"].append(date_str)
            else:
                stock_map[code] = {
                    "name":            name,
                    "industry":        str(row[c_industry] or "").strip() if c_industry else "",
                    "eps":             safe_float(row[c_eps]) if c_eps else 0,
                    "roe":             safe_float(row[c_roe]) if c_roe else 0,
                    "gross":           safe_float(row[c_gross]) if c_gross else 0,
                    "rev_qoq_list":    [rev_qoq],
                    "profit_qoq_list": [prof_qoq],
                    "dates":           [date_str],
                }

        print(f" +{len(df)}={len(stock_map)}")
        time.sleep(random.uniform(0.4, 0.9))
    return stock_map


def score_growth(rev_qoq, profit_qoq):
    """增长质量评分"""
    import statistics
    n = min(len(rev_qoq), len(profit_qoq))
    if n == 0:
        return 0, 0, ""

    def count_positive(a, b, max_n):
        cnt = 0
        for i in range(min(len(a), len(b), max_n)):
            if a[i] is not None and b[i] is not None and a[i] > 0 and b[i] > 0:
                cnt += 1
            else:
                break
        return cnt

    consec = count_positive(rev_qoq, profit_qoq, n)
    if consec == 0:
        return 0, 0, ""

    valid_r = [rev_qoq[i] for i in range(consec) if rev_qoq[i] is not None]
    valid_p = [profit_qoq[i] for i in range(consec) if profit_qoq[i] is not None]
    if not valid_r or not valid_p:
        return 0, 0, ""

    avg_r = sum(valid_r) / len(valid_r)
    avg_p = sum(valid_p) / len(valid_p)
    base = 60 if consec >= 3 else (50 if consec == 2 else 30)
    rev_s  = min(25, max(0, avg_r / 5))
    prof_s = min(25, max(0, avg_p / 5))
    stab = 0
    if consec >= 2 and len(valid_r) >= 2:
        cs = (statistics.stdev(valid_r) + statistics.stdev(valid_p)) / 2 if len(valid_r) > 1 else 0
        stab = 10 if cs < 10 else (5 if cs < 20 else 0)
    trend_bonus = 0
    if consec >= 3 and len(valid_r) >= 3:
        if valid_r[0] > valid_r[1] > valid_r[2]:
            trend_bonus += 5
    total = round(base + rev_s + prof_s + stab + trend_bonus, 1)
    return total, consec, f"连续{consec}季双增长"


def save_rank_history(today_sectors):
    """保存今日板块排名快照（供 SlowRise 计算）"""
    hist = {}
    try:
        if os.path.exists(RANK_HISTORY_FILE):
            with open(RANK_HISTORY_FILE, 'r', encoding='utf-8') as f:
                hist = json.load(f)
    except Exception:
        pass
    today = datetime.now().strftime('%Y-%m-%d')
    hist[today] = today_sectors
    dates = sorted(hist.keys())[-10:]
    hist = {d: hist[d] for d in dates}
    try:
        with open(RANK_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def run():
    ts0 = time.time()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Step1 财报+技术双滤 — 财报层筛选")
    print("=" * 60)

    # 动态计算最近4个有数据的财报季度
    now = datetime.now()
    y, m = now.year, now.month
    # 当前月份对应的上一季末月份: 3,6,9,12
    last_q_month = ((m - 1) // 3) * 3  # 3,6,9,0
    if last_q_month == 0:
        last_q_month = 12
        y -= 1
    quarters = []
    qy, qm = y, last_q_month
    for _ in range(4):
        # 季末日期: 3->31, 6->30, 9->30, 12->31
        day = 31 if qm in (3, 12) else 30
        quarters.append(f"{qy}{qm:02d}{day}")
        qm -= 3
        if qm <= 0:
            qm = 12
            qy -= 1
    print(f"  季度列表: {quarters}")

    # Step 1: 财报层
    print("\n[Step 1/2] 财报连续增长筛选...")
    smap = fetch_quarterly_reports(quarters)
    if len(smap) < 50:
        print("[ERROR] 数据不足!")
        return
    print(f"\n  -> 合计 {len(smap)} 只股票")

    # Step 2: 财报增长评分
    print("\n[Step 2/2] 财报增长评分...")
    cand = []
    for code, info in smap.items():
        if len(info["rev_qoq_list"]) < 2:
            continue
        name = info["name"]
        if "ST" in name or "*ST" in name:
            continue
        # 过滤新三板/北交所（87/83/430/830开头）
        if code.startswith("87") or code.startswith("83") or code.startswith("430") or code.startswith("830"):
            continue
        if code.startswith("90") or code.startswith("91") or code.startswith("20") or code.startswith("21"):  # 排除B股
            continue
        rq = info["rev_qoq_list"]
        pq = info["profit_qoq_list"]
        sc, consec, kind = score_growth(rq, pq)
        if sc == 0:
            continue
        cand.append({
            "code":      code,
            "name":      name,
            "industry":  info["industry"],
            "consec":    consec,
            "kind":      kind,
            "score":     sc,
            "rq":        [x for x in rq[:consec] if x is not None],
            "pq":        [x for x in pq[:consec] if x is not None],
            "eps":       info["eps"] or 0,
            "roe":       info["roe"] or 0,
            "gross":     info["gross"] or 0,
            "dates":     info["dates"][:consec],
        })

    print(f"  -> 财报候选 {len(cand)} 只")
    if cand:
        dist = {k: sum(1 for c in cand if c["consec"] == k) for k in [1, 2, 3]}
        print(f"  -> 连续季数: {dist}")

    # 保存板块排名快照（供 SlowRise 计算）
    try:
        sector_url = (
            "https://push2he.eastmoney.com/api/qt/clist/get"
            "?pn=1&pz=50&po=1&np=1&fltt=2&invt=2&fid=f3"
            "&fs=m:90+t:2+f:!50&fields=f2,f3,f4,f12,f14"
        )
        raw = gz_fetch(sector_url)
        if not raw or raw.strip().startswith('<'):
            print(f"  -> 板块快照跳过: API返回非JSON(可能WAF拦截)")
        else:
            sec_data = json.loads(raw)
            today_sectors = []
            if sec_data and sec_data.get("data") and sec_data["data"].get("diff"):
                rank = 1
                for item in sec_data["data"]["diff"]:
                    today_sectors.append({
                        "name": item.get("f14", ""),
                        "change": item.get("f3", 0),
                        "rank": rank
                    })
                    rank += 1
            save_rank_history(today_sectors)
            print(f"  -> 板块快照已写入历史（{len(today_sectors)} 板块）")
    except Exception as e:
        print(f"  -> 板块快照写入失败: {e}")

    # 输出 candidates.json
    output = {
        "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "count": len(cand),
        "candidates": cand
    }
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] 候选名单已保存: {OUTPUT_FILE} ({len(cand)} 只，耗时 {time.time()-ts0:.0f}s)")


if __name__ == "__main__":
    run()
