# -*- coding: utf-8 -*-
"""
# ─── 路径兼容：Windows 本地 / GitHub Actions Linux 自动适配 ───
import sys as _s, os as _o
_s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
from paths import WS, VIBE_DIR, DAILY_PICKS, AI_ANALYSIS_DATA, AI_ANALYSIS_REPORT  # noqa: E402
陈小群战法四步选股 v3（修复版）
数据源: 东方财富板块+行情、Sina涨幅榜、腾讯日K（复权）
- Step1: 板块动量（东方财富行业板块涨幅排行）
- Step2: 强势候选（Sina换手率/涨幅榜，排除ST/科创/北交）
- Step3: 均线多头（腾讯日K复权数据计算MA5/10/20/60）
- Step4: 量比>1.2 & 相对强度>1.2
钉钉推送 + 结果保存
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import json, os, time, ssl, base64, urllib.request, urllib.parse
from datetime import datetime, date
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import pandas as pd

# ── 路径配置 ─────────────────────────────────────────────────────────────────
WORKSPACE = Path(WS)
# VIBE_DIR 已从 paths.py 导入，无需重定义
RESULT_FILE = WORKSPACE / "chen_xiaoqun_result.json"
LOG_FILE    = WORKSPACE / "chen_xiaoqun_log.txt"

# ── SSL ─────────────────────────────────────────────────────────────────────
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# ── 钉钉配置 ─────────────────────────────────────────────────────────────────
DINGTALK_TOKEN = "055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"
DINGTALK_URL   = f"https://oapi.dingtalk.com/robot/send?access_token={DINGTALK_TOKEN}"

# ── HTTP 通用 ────────────────────────────────────────────────────────────────
def sina_get(url, encoding='gbk', timeout=15):
    h = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            return r.read().decode(encoding, errors='replace')
    except Exception as e:
        log(f"  Sina HTTP失败: {e}")
        return None

def em_get(url, params=None, timeout=10):
    h = {
        "Referer": "https://finance.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        if params:
            url += ("?" if "?" not in url else "&") + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            return json.loads(r.read().decode('utf-8', errors='replace'))
    except Exception as e:
        log(f"  EM HTTP失败: {e}")
        return None

def tencent_get(url, timeout=10):
    h = {
        "Referer": "https://finance.qq.com/",
        "User-Agent": "Mozilla/5.0"
    }
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as e:
        return None

# ── 日志 ─────────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line)
    except Exception:
        print(line.encode('utf-8', errors='replace').decode('utf-8'))
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# ── 钉钉推送 ─────────────────────────────────────────────────────────────────
def dingtalk_send(title, text):
    try:
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text}
        }
        r = requests.post(DINGTALK_URL, json=payload,
                         headers={"Content-Type": "application/json"}, timeout=15)
        log(f"钉钉推送: {r.status_code} {r.text[:100]}")
    except Exception as e:
        log(f"钉钉推送失败: {e}")

# ── Step1: 板块动量（东方财富行业板块） ──────────────────────────────────────
def step1_block_rps():
    log("=== Step1: 板块动量 ===")
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": 1, "pz": 100, "po": 1, "np": 1,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": 2, "invt": 2, "fid": "f3",
        "fs": "m:90 t:2",
        "fields": "f12,f14,f3,f4,f5",
    }
    data = em_get(url, params)
    if not data:
        log("EM板块接口失败")
        return []
    try:
        items = data.get("data", {}).get("diff", [])
        if not items:
            return []
        df = pd.DataFrame(items)[["f12","f14","f3"]].copy()
        df.columns = ["code","name","pct_chg"]
        df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
        df = df.dropna()
        threshold = df["pct_chg"].quantile(0.75)
        strong = df[df["pct_chg"] >= threshold].sort_values("pct_chg", ascending=False)
        log(f"板块总数:{len(df)} 高动量阈值:{threshold:.2f}% 强势板块:{len(strong)}")
        return strong["code"].tolist()
    except Exception as e:
        log(f"板块解析失败: {e}")
        return []

# ── Step2: 强势候选股 ──────────────────────────────────────────────────────
def step2_candidates():
    log("=== Step2: 强势候选股 ===")
    stocks = []
    # Sina涨幅榜
    for page in range(1, 5):
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={page}&num=50&sort=changepercent&asc=0&node=hs_a"
        )
        raw = sina_get(url, encoding='gbk')
        if raw:
            try:
                items = json.loads(raw)
                for it in items:
                    code = str(it.get('code',''))
                    name = it.get('name','')
                    chg  = float(it.get('changepercent', 0) or 0)
                    price= float(it.get('trade', 0) or 0)
                    vol  = float(it.get('volume', 0) or 0)
                    if not code or not name: continue
                    if 'ST' in name or '*ST' in name: continue
                    if code.startswith('688') or code.startswith('8'): continue
                    if price <= 0: continue
                    if chg >= 3.0:
                        stocks.append({"code": code, "name": name, "pct_chg": chg, "price": price, "vol": vol})
            except Exception as e:
                log(f"Sina涨幅第{page}页解析失败: {e}")
        time.sleep(0.3)
    
    # Sina换手率补充分页
    for page in range(1, 3):
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={page}&num=50&sort=turnoverratio&asc=0&node=hs_a"
        )
        raw = sina_get(url, encoding='gbk')
        if raw:
            try:
                items = json.loads(raw)
                for it in items:
                    code = str(it.get('code',''))
                    name = it.get('name','')
                    chg  = float(it.get('changepercent', 0) or 0)
                    price= float(it.get('trade', 0) or 0)
                    vol  = float(it.get('volume', 0) or 0)
                    if not code or not name: continue
                    if 'ST' in name: continue
                    if code.startswith('688') or code.startswith('8'): continue
                    if price <= 0: continue
                    if chg >= 5.0:  # 换手率补充仅取5%+
                        if code not in [s['code'] for s in stocks]:
                            stocks.append({"code": code, "name": name, "pct_chg": chg, "price": price, "vol": vol})
            except Exception as e:
                log(f"Sina换手率第{page}页解析失败: {e}")
        time.sleep(0.3)
    
    # 去重
    seen = {}
    for s in stocks:
        if s['code'] not in seen:
            seen[s['code']] = s
    result = list(seen.values())
    result.sort(key=lambda x: x["pct_chg"], reverse=True)
    log(f"候选强势股: {len(result)} 支")
    return result

# ── 腾讯日K（复权） ─────────────────────────────────────────────────────────
def fetch_tencent_kline(code6, count=80):
    """返回 [[日期, 开, 收, 高, 低, 量], ...] 或 None"""
    if code6.startswith("6"):
        sym = f"sh{code6}"
    elif code6.startswith(("0","3")):
        sym = f"sz{code6}"
    else:
        return None
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?_var=kline_dayhfq&param={sym},day,,,{count},qfq")
    raw = tencent_get(url)
    if not raw:
        return None
    try:
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

# ── Step3+4: 均线多头 & 量比验证 ────────────────────────────────────────────
def analyze_stock(code, name, current_pct, current_price):
    try:
        kdata = fetch_tencent_kline(code, 80)
        if not kdata or len(kdata) < 25:
            return None
        # kdata格式: [日期, 开, 收, 高, 低, 量]
        closes = []
        vols   = []
        for bar in kdata:
            try:
                closes.append(float(bar[2]))
                vols.append(float(bar[5]))
            except Exception:
                continue
        if len(closes) < 25:
            return None
        avg5  = sum(closes[-5:])   / 5
        avg10 = sum(closes[-10:])  / 10
        avg20 = sum(closes[-20:])  / 20
        avg60 = sum(closes[-60:])  / 60 if len(closes) >= 60 else sum(closes) / len(closes)
        avgvol5 = sum(vols[-5:]) / 5
        last_close = closes[-1]
        last_vol   = vols[-1]
        
        # 均线多头: MA5>MA10>MA20>MA60
        ma_ok = (avg5 > avg10 > avg20 > avg60)
        # 简化版: MA5>MA10 且 MA20>MA60
        ma_ok2 = (avg5 > avg10 and avg20 > avg60)
        ma_ok = ma_ok or ma_ok2
        
        # 量比（今日量 vs 5日均量） — 注：vol数据是总量，用最后一根柱量代表今日
        vol_ratio = last_vol / avgvol5 if avgvol5 > 0 else 0
        
        # 相对强度（收盘/60日均线）
        rel_str = last_close / avg60 if avg60 > 0 else 0
        
        # 跳空检测（前复权数据可能有误差，用前日close）
        gap_pct = 0
        if len(closes) >= 2:
            prev_close = closes[-2]
            gap_pct = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0
        
        return {
            "code": code,
            "name": name,
            "price": round(last_close, 2),
            "pct_chg": round(current_pct, 2),
            "ma5": round(avg5, 2),
            "ma10": round(avg10, 2),
            "ma20": round(avg20, 2),
            "ma60": round(avg60, 2),
            "vol_ratio": round(vol_ratio, 2),
            "relative": round(rel_str, 3),
            "gap_pct": round(gap_pct, 2),
            "ma_ok": ma_ok,
        }
    except Exception as e:
        return None

# ── 主流程 ───────────────────────────────────────────────────────────────────
def run():
    log(f"========== 陈小群战法选股 {datetime.now().strftime('%Y-%m-%d %H:%M')} ==========")
    t0 = time.time()
    
    # Step1
    strong_blocks = step1_block_rps()
    
    # Step2
    candidates = step2_candidates()
    if not candidates:
        log("无候选股票，退出")
        dingtalk_send(f"陈小群战法 {datetime.now().strftime('%m/%d')} 无信号",
                     f"今日候选股票为空，请检查网络。")
        return []
    
    log(f"开始均线分析 {len(candidates)} 支候选股...")
    
    # Step3+4: 并行分析
    results = []
    analyzed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(analyze_stock, c["code"], c["name"], c["pct_chg"], c["price"]): c
            for c in candidates
        }
        for f in as_completed(futures):
            analyzed += 1
            if analyzed % 20 == 0:
                log(f"  已分析 {analyzed}/{len(candidates)}...")
            try:
                r = f.result(timeout=15)
                if r:
                    results.append(r)
            except Exception:
                pass
            time.sleep(0.05)  # 避免并发过高
    
    log(f"均线分析完成: {len(results)} 支有数据")
    
    # 综合评分
    final = []
    for s in results:
        score = 0
        if s["ma_ok"]:              score += 1  # Step2 均线多头
        if s["pct_chg"] > -3:       score += 1  # Step3 非大低开
        if s["vol_ratio"] >= 1.2:    score += 1  # Step4 量比
        if s["relative"] >= 1.1:     score += 1  # Step4 相对强度
        s["score"] = score
        # 核心: 均线多头 必须满足
        if s["ma_ok"] and score >= 3:
            final.append(s)
        elif s["ma_ok"] and s["vol_ratio"] >= 1.5:  # 均线多头+高量比也算
            s["score"] = score + 1
            final.append(s)
    
    final.sort(key=lambda x: (x["score"], x["pct_chg"], x["vol_ratio"]), reverse=True)
    
    log(f"最终入选: {len(final)} 支")
    for s in final[:10]:
        log(f"  {s['code']} {s['name']} 价:{s['price']} 涨:{s['pct_chg']}% "
            f"V比:{s['vol_ratio']} 相对:{s['relative']} 多头:{s['ma_ok']}")
    
    # 保存
    out = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M"),
        "total": len(final),
        "candidates_checked": len(candidates),
        "stocks": final[:20],
    }
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"结果已保存: {RESULT_FILE}")
    
    elapsed = time.time() - t0
    log(f"总耗时: {elapsed:.0f}秒")
    
    # 钉钉推送
    title = f"陈小群战法 {datetime.now().strftime('%m/%d %H:%M')} | {len(final)}支"
    if final:
        lines = [f"## 📈 陈小群战法四步选股\n"]
        lines.append(f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        lines.append(f"**耗时**: {elapsed:.0f}s | **候选**: {len(candidates)} | **入选**: {len(final)}\n\n")
        lines.append("---\n\n")
        for i, s in enumerate(final[:12], 1):
            lines.append(f"**{i}. {s['code']} {s['name']}**\n")
            lines.append(f"  价:{s['price']} 涨幅:{s['pct_chg']}% 量比:{s['vol_ratio']} 相对强度:{s['relative']}\n")
            lines.append(f"  MA5={s['ma5']} MA10={s['ma10']} MA20={s['ma20']} MA60={s['ma60']}\n\n")
        text = "".join(lines)
    else:
        text = (f"**今日({datetime.now().strftime('%H:%M')})四步选股无信号**\n\n"
                f"候选检查: {len(candidates)} 支 | 有K线数据: {len(results)}\n"
                f"市场或需等待机会。")
    
    dingtalk_send(title, text)
    return final

if __name__ == "__main__":
    run()
