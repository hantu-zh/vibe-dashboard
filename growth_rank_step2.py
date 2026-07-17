# -*- coding: utf-8 -*-
"""
大力水手菠菜涨停战法 Step2 — 技术过滤 + 热度加权 + 输出
数据源：
  - 新浪涨幅榜 → 获取候选股票列表
  - 腾讯K线API → 获取80日历史数据计算 MA60/量比/近高
  - 新浪实时 → 补充涨跌幅/换手率
输出：growth_rank_filtered.csv + daily_picks.json + 钉钉推送
"""
import sys, os, json, time, ssl, urllib.request, warnings, signal
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 导入统一存储模块
from daily_picks_store import save_daily_picks

sys.stdout.reconfigure(encoding='utf-8')
os.environ["TQDM_DISABLE"] = "1"
warnings.simplefilter("ignore")

WORKSPACE = r"C:\Users\china\.qclaw\workspace"
CANDIDATES_FILE = os.path.join(WORKSPACE, "growth_rank_candidates.json")
OUTPUT_CSV = os.path.join(WORKSPACE, "growth_rank_filtered.csv")
OUTPUT_JSON = os.path.join(WORKSPACE, "daily_picks.json")

DINGTALK_TOKEN = "055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"

THRESHOLD_VOLUME_RATIO = 1.5
MAX_PULLBACK_PCT = 9
MA_GAP_MAX = 20
TOP_DISPLAY = 20
HIST_DAYS = 80

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


# ═══════════════════════════════════════════════════════════════
# 钉钉推送
# ═══════════════════════════════════════════════════════════════
def send_dingtalk(title: str, content: str) -> bool:
    url = f"https://oapi.dingtalk.com/robot/send?access_token={DINGTALK_TOKEN}"
    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"title": title, "text": content}
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return json.loads(r.read().decode()).get("errcode") == 0
    except Exception as e:
        print(f"[钉钉推送失败] {e}")
        return False


# ═══════════════════════════════════════════════════════════════
# 新浪涨幅榜候选
# ═══════════════════════════════════════════════════════════════
def fetch_top_gainers(pages: int = 5, per_page: int = 100) -> list:
    all_stocks = []
    url_tpl = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData?page={page}&num={num}&sort=changepercent"
               "&asc=0&node=hs_a")
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    for page in range(1, pages + 1):
        url = url_tpl.format(page=page, num=per_page)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                raw = r.read().decode("gbk", errors="replace")
            data = json.loads(raw)
            if not data:
                break
            all_stocks.extend(data)
            time.sleep(0.1)
        except Exception as e:
            print(f"[涨幅榜第{page}页失败] {e}")
            break
    return all_stocks


# ═══════════════════════════════════════════════════════════════
# 腾讯K线API — 获取80日前复权日线
# ═══════════════════════════════════════════════════════════════
def fetch_tencent_kline(code6: str, count: int = 80) -> list:
    """返回 [[日期, 开盘, 收盘, 最高, 最低, 成交量], ...] 或 None"""
    if code6.startswith(("6",)):
        sym = f"sh{code6}"
    elif code6.startswith(("0", "3")):
        sym = f"sz{code6}"
    else:
        return None
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?_var=kline_dayhfq&param={sym},day,,,{count},qfq")
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.qq.com",
        "User-Agent": "Mozilla/5.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            raw = r.read().decode("utf-8", errors="replace")
        prefix = "kline_dayhfq="
        if raw.startswith(prefix):
            json_str = raw[len(prefix):]
        else:
            return None
        obj = json.loads(json_str)
        stock_data = obj.get("data", {}).get(sym, {})
        for key in ["qfqday", "day"]:
            if key in stock_data:
                return stock_data[key]
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# 新浪实时单只/批量行情
# ═══════════════════════════════════════════════════════════════
def fetch_sina_realtime(code6: str) -> dict:
    """获取单只新浪实时数据"""
    if code6.startswith(("6",)):
        sym = f"sh{code6}"
    elif code6.startswith(("0", "3")):
        sym = f"sz{code6}"
    else:
        return {}
    url = f"https://hq.sinajs.cn/list={sym}"
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            raw = r.read().decode("gbk", errors="replace")
        if '=""' in raw or '="";' in raw:
            return {}
        parts = raw.split('="')[1].split('"')[0].split(",")
        if len(parts) < 10:
            return {}
        return {
            "name": parts[0],
            "price": float(parts[3]) if parts[3] else 0,
            "chg_pct": float(parts[32]) if len(parts) > 32 and parts[32] else 0,
            "vol_ratio": float(parts[33]) if len(parts) > 33 and parts[33] else 0,
            "turnover": float(parts[38]) if len(parts) > 38 and parts[38] else 0,
            "high": float(parts[4]) if parts[4] else 0,
            "low": float(parts[5]) if parts[5] else 0,
        }
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════
# 名字兜底查询
_NAME_CACHE = {}


def lookup_stock_name(code6: str) -> str:
    """从新浪查询单只股票名称，失败返回空字符串"""
    if code6 in _NAME_CACHE:
        return _NAME_CACHE[code6]
    if code6.startswith(("6",)):
        sym = f"sh{code6}"
    elif code6.startswith(("0", "3")):
        sym = f"sz{code6}"
    else:
        _NAME_CACHE[code6] = ""
        return ""
    url = f"https://hq.sinajs.cn/list={sym}"
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0"
    })
    try:
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            raw = r.read().decode("gbk", errors="replace")
        if '=""' in raw or '="";' in raw:
            _NAME_CACHE[code6] = ""
            return ""
        parts = raw.split('="')[1].split('"')[0].split(",")
        name = parts[0] if parts else ""
        _NAME_CACHE[code6] = name
        return name
    except Exception:
        _NAME_CACHE[code6] = ""
        return ""


# ═══════════════════════════════════════════════════════════════
# 综合技术指标获取（腾讯K线 → MA60/量比/近高）
# ═══════════════════════════════════════════════════════════════
def get_stock_tech(code6: str) -> dict:
    """返回技术指标 dict 或 None"""
    days = fetch_tencent_kline(code6, HIST_DAYS)
    if not days or len(days) < 30:
        return None
    try:
        closes = [float(d[2]) for d in days]
        highs = [float(d[3]) for d in days]
        vols = [float(d[5]) for d in days]

        ma60 = sum(closes[-60:]) / min(len(closes), 60)
        ma5 = sum(closes[-5:]) / 5
        price = closes[-1]

        vol5_avg = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else sum(vols) / max(len(vols), 1)
        vol_ratio = vols[-1] / vol5_avg if vol5_avg > 0 else 0
        vol_ratio_raw = vol_ratio  # 保留原始计算的量比

        recent_high20 = max(highs[-20:])
        ma_gap = abs((price - ma60) / ma60 * 100) if ma60 > 0 else 999
        pullback = (recent_high20 - price) / recent_high20 * 100 if recent_high20 > 0 else 999
        ma5_gap = abs((price - ma5) / ma5 * 100) if ma5 > 0 else 999

        # 尝试获取新浪实时（补充换手率）
        sina_rt = fetch_sina_realtime(code6)
        turnover = sina_rt.get("turnover", 0)
        chg_pct = sina_rt.get("chg_pct", 0)
        # 若新浪无换手率，用历史最后一根
        if not turnover:
            # 从腾讯K线中无法直接获取换手率，设为0
            pass

        return {
            "code": code6,
            "name": sina_rt.get("name", ""),
            "price": price,
            "chg_pct": chg_pct,
            "vol_ratio": vol_ratio_raw,
            "turnover": turnover,
            "ma60": ma60,
            "ma_gap": ma_gap,
            "ma5_gap": ma5_gap,
            "pullback_pct": pullback,
            "recent_high20": recent_high20,
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# 热度评分
# ═══════════════════════════════════════════════════════════════
def calc_heat_score(t: dict) -> float:
    vol_score = min(t["vol_ratio"] / 3.0, 2.0) * 30
    turn_score = min(t["turnover"] / 5.0, 2.0) * 20
    ma_penalty = max(0, t["ma_gap"] - 10) * 2
    pullback_penalty = min(t["pullback_pct"] * 2, 20)
    return round(vol_score + turn_score - ma_penalty - pullback_penalty + 30, 2)


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def main():
    signal.signal(signal.SIGTERM, lambda *_: None)

    t0 = time.time()
    print(f"\n[Step2] 技术过滤开始 — {datetime.now().strftime('%H:%M:%S')}")

    # 1. 读取候选池名称映射
    base_map = {}
    if os.path.exists(CANDIDATES_FILE):
        with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
            cand_data = json.load(f)
        for s in cand_data.get("candidates", []):
            base_map[s["code"]] = s.get("name", "")
        total_in = len(base_map)
        print(f"  候选池 {total_in} 只")
    else:
        total_in = 0
        print("  候选池不存在")

    # 2. 新浪涨幅榜候选
    print("  获取新浪涨幅榜...")
    gainers = fetch_top_gainers(pages=5, per_page=100)
    print(f"  涨幅榜 {len(gainers)} 只")

    # 构建候选列表（优先涨幅榜）
    candidates = []
    seen = set()
    for g in gainers:
        code = str(g.get("code", "")).zfill(6)
        if not code or code in seen or code in ("000000",):
            continue
        seen.add(code)
        name = g.get("name", "") or base_map.get(code, "") or lookup_stock_name(code)
        candidates.append({
            "code": code,
            "name": name,
            "score": 50,
        })

    print(f"  待检查 {len(candidates)} 只（涨幅榜）")

    # 3. 并发获取技术指标（腾讯K线，极快）
    tech_results = {}
    codes = [c["code"] for c in candidates]
    batch_size = 30
    total_batches = (len(codes) + batch_size - 1) // batch_size

    print(f"  并发获取技术指标（{len(codes)} 只，{total_batches} 批次）...")
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        with ThreadPoolExecutor(max_workers=20) as ex:
            futures = {ex.submit(get_stock_tech, c): c for c in batch}
            for future in as_completed(futures):
                result = future.result()
                code = futures[future]
                if result:
                    tech_results[code] = result
        elapsed = time.time() - t0
        done = min(i + batch_size, len(codes))
        print(f"  批次 {(i//batch_size)+1}/{total_batches} 完成 {done}/{len(codes)} 耗时 {elapsed:.0f}s")

    print(f"  技术数据获取完成：{len(tech_results)} 只")

    # 4. 技术过滤 + 评分
    filtered = []
    for s in candidates:
        code = s["code"]
        t = tech_results.get(code)
        if not t:
            continue
        if t["vol_ratio"] < THRESHOLD_VOLUME_RATIO:
            continue
        if t["pullback_pct"] > MAX_PULLBACK_PCT:
            continue
        if t["ma_gap"] > MA_GAP_MAX:
            continue

        heat = calc_heat_score(t)
        name = s.get("name", "") or t.get("name", "") or lookup_stock_name(code)
        filtered.append({
            "code": code,
            "name": name,
            "base_score": s.get("score", 50),
            "heat_score": heat,
            "final_score": round(s.get("score", 50) * 0.4 + heat * 0.6, 2),
            "price": t["price"],
            "chg_pct": t["chg_pct"],
            "vol_ratio": round(t["vol_ratio"], 2),
            "turnover": round(t["turnover"], 2),
            "ma60": round(t["ma60"], 2),
            "ma_gap": round(t["ma_gap"], 2),
            "pullback_pct": round(t["pullback_pct"], 2),
        })

    filtered.sort(key=lambda x: x["final_score"], reverse=True)
    top_stocks = filtered[:TOP_DISPLAY]

    print(f"\n  技术过滤后：{len(filtered)} 只 → 取前 {len(top_stocks)} 只")
    for i, s in enumerate(top_stocks, 1):
        sign = "+" if s["chg_pct"] >= 0 else ""
        print(f"  #{i:02d} {s['code']} {s['name']:<8s} | 现价:{s['price']:.2f} "
              f"涨幅:{sign}{s['chg_pct']:.2f}% | 量比:{s['vol_ratio']:.2f} "
              f"换手:{s['turnover']:.2f}% | MA60乖离:{s['ma_gap']:.2f}% "
              f"回调:{s['pullback_pct']:.2f}% | 综合:{s['final_score']:.1f}")

    # 5. 保存 CSV
    csv_lines = ["代码,名称,现价,涨幅%,量比,换手率%,MA60,MA60乖离%,回调幅度%,综合评分"]
    for s in filtered:
        sign = "+" if s["chg_pct"] >= 0 else ""
        csv_lines.append(f"{s['code']},{s['name']},{s['price']:.2f},{sign}{s['chg_pct']:.2f},"
                         f"{s['vol_ratio']:.2f},{s['turnover']:.2f},{s['ma60']:.2f},"
                         f"{s['ma_gap']:.2f},{s['pullback_pct']:.2f},{s['final_score']:.2f}")
    with open(OUTPUT_CSV, "w", encoding="utf-8") as f:
        f.write("\n".join(csv_lines))
    print(f"\n  CSV: {OUTPUT_CSV}")

    # 6. 使用统一存储模块保存（新格式）
    save_daily_picks("大力水手菠菜涨停战法", top_stocks)
    print(f"  已保存到 daily_picks.json（新格式）")

    # 7. 钉钉推送
    total_time = time.time() - t0
    lines = [
        f"## \U0001f7e2 大力水手菠菜涨停战法 v3.0\n",
        f"**Step2 技术过滤** | {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        f"**候选**: {total_in}+{len(gainers)} → **合格**: {len(filtered)} 只\n",
        f"**过滤**: 量比>{THRESHOLD_VOLUME_RATIO} | 回调≤{MAX_PULLBACK_PCT}% | MA60乖离≤{MA_GAP_MAX}%\n",
        f"**耗时**: {total_time:.0f}秒\n---\n",
    ]
    for i, s in enumerate(top_stocks, 1):
        sign = "+" if s["chg_pct"] >= 0 else ""
        lines.append(
            f"**#{i:02d} {s['name']}({s['code']})**  \n"
            f"> 现价:{s['price']:.2f} 涨幅:{sign}{s['chg_pct']:.2f}%  \n"
            f"> 量比:{s['vol_ratio']:.2f} 换手:{s['turnover']:.2f}%  \n"
            f"> MA60乖离:{s['ma_gap']:.2f}% 回调:{s['pullback_pct']:.2f}%  \n"
            f"> **综合评分:{s['final_score']:.1f}**  \n"
        )

    ok = send_dingtalk("\U0001f7e2菠菜涨停战法 Step2", "\n".join(lines))
    print(f"\n  钉钉推送: {'成功 ✅' if ok else '失败 ❌'}")
    print(f"\n[Step2 完成] 技术过滤后 {len(filtered)} 只，耗时 {total_time:.0f}s")


if __name__ == "__main__":
    main()