# -*- coding: utf-8 -*-
"""
益盟强买选股 v2 (efinance版 - HTTP直连)
数据源: Sina换手率Top100 + 东方财富HTTP主力资金流历史
流程: 候选股筛选 → 主力净流入评分 → Top10 → 钉钉推送 → 保存strongbuy_data.json → GitHub同步
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import json, os, time, ssl, base64, urllib.request, urllib.error
from datetime import date
from pathlib import Path

import requests

# ── 路径配置 ─────────────────────────────────────────────
WORKSPACE = Path(__file__).resolve().parent  # 仓库根目录，自动适配 Linux / GitHub Actions
VIBE_DIR  = WORKSPACE / "vibe-dashboard"
JSON_OUT  = WORKSPACE / "strongbuy_data.json"
VIBE_JSON = WORKSPACE / "strongbuy_data.json"  # 根目录为唯一权威位置，避免双份副本

# ── GitHub 配置 ───────────────────────────────────────────
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN') or os.environ.get('VIBE_GITHUB_TOKEN')
if not GITHUB_TOKEN:
    try:
        GITHUB_TOKEN = open(WORKSPACE / ".github_token", encoding='utf-8-sig').read().strip()
    except Exception:
        GITHUB_TOKEN = None
GITHUB_REPO = "hantu-zh/vibe-dashboard"

# ── 钉钉配置 ─────────────────────────────────────────────
# 优先读环境变量（GitHub Actions Secrets 注入），其次本地 .env.dingtalk，最后才用内置值
# ⚠️ 内置 token 已失效且随公开仓库暴露，建议尽快在钉钉后台轮换并改用 Secrets
DINGTALK_TOKEN = os.environ.get('DINGTALK_TOKEN')
if not DINGTALK_TOKEN:
    try:
        DINGTALK_TOKEN = open(WORKSPACE / ".env.dingtalk", encoding='utf-8').read().strip()
    except Exception:
        DINGTALK_TOKEN = "055ab261c9ba6f087e26f2abbd3566508c73da140be3bc75511393bd430ba"
DINGTALK_URL   = f"https://oapi.dingtalk.com/robot/send?access_token={DINGTALK_TOKEN}"

# ── SSL ───────────────────────────────────────────────────
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# ── HTTP 通用 ─────────────────────────────────────────────
def http_get(url, params=None, headers=None, encoding='utf-8', timeout=15):
    h = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as r:
            return r.read().decode(encoding, errors='replace')
    except Exception as e:
        print(f"  HTTP GET 失败 [{url[:50]}...]: {e}")
        return None

def requests_get(url, params=None, headers=None, timeout=15):
    h = {
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h, timeout=timeout)
        return r.json()
    except Exception as e:
        print(f"  requests GET 失败 [{url[:50]}...]: {e}")
        return None

# ── Step1: 获取 Sina 换手率 Top100 ───────────────────────
def fetch_sina_turnover_top(n=120):
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeData?page=1&num={n}&sort=turnoverratio&asc=0&node=hs_a"
    )
    raw = http_get(url, encoding='gbk')
    if not raw:
        return []
    try:
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            code = item.get("code", "")
            if not code or code.startswith(("8", "4", "83", "87", "43")):
                continue
            name = item.get("name", "")
            if "ST" in name or "st" in name or "\u9000" in name:
                continue
            chg   = float(item.get("changepercent", 0) or 0)
            price = float(item.get("trade", 0) or 0)
            if price <= 0:
                continue
            result.append({
                "code": code,
                "name": name,
                "price": round(price, 2),
                "change_pct": round(chg, 2),
                "turnover": round(float(item.get("turnoverratio", 0) or 0), 2),
                "amount_yi": round(float(item.get("amount", 0) or 0) / 1e8, 3),
            })
        return result
    except Exception as e:
        print(f"  Sina 换手率榜解析失败: {e}")
        return []

# ── Step2: 过滤候选股 ─────────────────────────────────────
def filter_candidates(stocks):
    """保留换手率≥5% 且 涨幅 1%~9.9% 的股票(排除涨停/跌停/僵尸)"""
    result = []
    for s in stocks:
        trn = s.get("turnover", 0)
        chg = s.get("change_pct", 0)
        if trn >= 5.0 and 1.0 <= chg <= 9.9:
            result.append(s)
    print(f"  候选股过滤后: {len(stocks)} → {len(result)}")
    return result

# ── Step3: 获取东方财富主力资金流 (HTTP直连) ─────────────
# 2026-09-12: 东财 fflow/daykline 自 09-10 起从 GitHub Runner 拉空（益盟强买连续两日
# 未更新的根因）。修复：加公共 ut 令牌、改 https，并按序尝试多个域名兜底。
EMONEY_FLOW_URLS = [
    "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get",
    "http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
]
EMONEY_FLOW_UT = "b2884a393a59ad64002292a3e90d46a5"
EMONEY_FLOW_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
# 字段顺序: f51=日期,f52=主力净流入,f53=小单净流入,f54=中单净流入,
#          f55=大单净流入,f56=超大单净流入,f57=主力净流入占比,f58=小单净占比,
#          f59=中单净占比,f60=大单净占比,f61=超大单净占比,f62=收盘价,f63=涨跌幅

def _secid(code):
    """股票代码转东方财富 secid: 1=沪, 0=深"""
    return f"1.{code}" if code.startswith("6") or code.startswith("9") else f"0.{code}"

def fetch_main_money_flow(code, days=5):
    """
    HTTP直连东方财富资金流历史接口
    返回: {'net_inflow_sum': 总净流入(元), 'days': N, 'latest_pct': 最新主力净流入占比(%)}
    kline字段: 日期,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入,
               主力净流入占比,小单净占比,中单净占比,大单净占比,超大单净占比,收盘价,涨跌幅
    """
    params = {
        "lmt": str(days),
        "klt": "101",
        "secid": _secid(code),
        "fields1": "f1,f2,f3,f7",
        "fields2": EMONEY_FLOW_FIELDS2,
    }
    params["ut"] = EMONEY_FLOW_UT
    d = None
    last_err = ""
    for u in EMONEY_FLOW_URLS:
        try:
            r = requests.get(u, params=params, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/"
            }, timeout=10)
            dj = r.json()
            if isinstance(dj, dict) and dj.get("data") and dj["data"].get("klines"):
                d = dj
                break
            last_err = "empty:%s" % str(dj)[:60]
        except Exception as e:
            last_err = repr(e)[:60]
            continue
    if d is None:
        print(f"  [fflow] {code} 全部源失败: {last_err}")
        return None

    data = d.get("data") if isinstance(d, dict) else None
    if not data:
        return None
    klines = data.get("klines", []) if isinstance(data, dict) else []
    if not klines:
        return None

    net_sum = 0.0
    latest_pct = 0.0
    for kline in klines:
        parts = kline.split(",")
        if len(parts) >= 7:
            try:
                net_sum += float(parts[1])  # 主力净流入(元)
            except (ValueError, IndexError):
                continue
    # 最新一天的主力净流入占比
    try:
        latest_parts = klines[0].split(",")
        if len(latest_parts) >= 7:
            latest_pct = float(latest_parts[6])  # f57=主力净流入占比
    except (ValueError, IndexError):
        latest_pct = 0.0

    # 连飘: 从最新一天起，连续「主力净流入 > 0」的天数 (klines[0]=最新)
    consec_days = 0
    try:
        for kline in klines:
            parts = kline.split(",")
            if len(parts) < 2:
                break
            v = float(parts[1])  # 主力净流入(元)
            if v > 0:
                consec_days += 1
            else:
                break
    except (ValueError, IndexError):
        consec_days = 0

    return {
        'net_inflow_sum': net_sum,
        'days': len(klines),
        'latest_pct': round(latest_pct, 2),
        'consec_days': consec_days,
    }

# ── Step4: 主力净流入评分 ─────────────────────────────────
def score_stocks(stocks, max_days=5):
    """
    评分规则:
      基础分 = max(主力净流入亿元 × 10, 0)
      加分   = 主力净流入占比(%) × 5 + 换手率(%) × 2
      总分   = 基础分 + 加分
    """
    scored = []
    for s in stocks:
        code = s['code']
        mflow = fetch_main_money_flow(code, days=max_days)
        time.sleep(0.08)  # 东方财富HTTP接口限速

        if mflow is None:
            continue

        net_yi   = mflow['net_inflow_sum'] / 1e8
        net_pct  = mflow['latest_pct']
        turnover = s.get('turnover', 0)
        chg      = s.get('change_pct', 0)

        base_score = max(net_yi * 10, 0)
        bonus      = net_pct * 5 + turnover * 2
        score      = round(base_score + bonus, 1)

        tags = []
        if net_yi >= 1:
            tags.append("大资金")
        if turnover >= 15:
            tags.append("高换手")
        if 3 <= chg <= 7:
            tags.append("涨幅适中")
        elif chg > 7:
            tags.append("高弹性")

        entry = {
            **s,
            'net_inflow_yi': round(net_yi, 3),
            'net_inflow_pct': net_pct,
            'consec_days': mflow.get('consec_days', 0),   # 连飘: 连续主力净流入天数
            'main_ratio': net_pct,                        # 净占比: 主力净流入占总成交比(%)
            'score': score,
            'tags': tags,
        }
        scored.append(entry)
        print(f"  {s['name']}({code}) 净流入{net_yi:.2f}亿 占比{net_pct:.2f}% 换手{turnover:.1f}% → 评分{score}")

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored

# ── Step5: 选 Top10 ───────────────────────────────────────
def select_top(stocks, n=10):
    return stocks[:n]

# ── Step6: 钉钉推送 ───────────────────────────────────────
def dingtalk_push(stocks):
    if not stocks:
        return False

    today = date.today().strftime('%Y-%m-%d')

    lines = [f"## 💪 益盟强买选股 {today}（Top10）\n"]
    lines.append("| 排名 | 股票 | 代码 | 现价 | 涨幅 | 换手率 | 成交额(亿) | 主力净流入 | 评分 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(stocks, 1):
        net_str = f"{s['net_inflow_yi']:.2f}亿" if s.get('net_inflow_yi') else "-"
        lines.append(
            f"| {i} | **{s['name']}** | {s['code']} | {s['price']} | "
            f"{s['change_pct']:+.2f}% | {s['turnover']:.1f}% | "
            f"{s['amount_yi']:.2f} | {net_str} | {s['score']} |"
        )
    lines.append(f"\n_评分: 净流入×10 + 净占比×5 + 换手率×2，数据: Sina + 东方财富HTTP_")

    content = "\n".join(lines)

    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': f'益盟强买 {today}',
            'text': content,
        }
    }
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    try:
        resp = requests.post(DINGTALK_URL, json=payload, headers=headers, timeout=20)
        result = resp.json()
        if result.get('errcode') == 0:
            print(f"  钉钉推送成功")
            return True
        else:
            print(f"  钉钉推送失败: {result}")
            return False
    except Exception as e:
        print(f"  钉钉推送异常: {e}")
        return False

# ── Step7: 保存 strongbuy_data.json ───────────────────────
def save_json(top_stocks):
    today = date.today().isoformat()
    # 读取现有的 strongbuy_data.json，保留 stocks 字段
    existing = {"updated": today, "yimeng": [], "stocks": []}
    if JSON_OUT.exists():
        try:
            existing = json.loads(JSON_OUT.read_text(encoding='utf-8'))
        except Exception:
            pass
    
    # 只更新 yimeng 字段，保留 stocks（由 strong_update.py 维护）
    data = {
        "updated": today,
        "yimeng": top_stocks,  # 益盟强买 Top10
        "stocks": existing.get("stocks", []),  # 保留涨幅榜强势股
    }
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  已保存 → {JSON_OUT}")

    if VIBE_JSON.parent.exists():
        with open(VIBE_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  已保存 → {VIBE_JSON}")
    return data

# ── Step8: GitHub 同步 ────────────────────────────────────
def push_github():
    if not GITHUB_TOKEN:
        print("  GitHub token 未配置,跳过推送")
        return

    files = {"strongbuy_data.json": str(JSON_OUT)}
    for remote_path, local_path in files.items():
        if not os.path.exists(local_path):
            print(f"  [GitHub] {remote_path} 本地不存在,跳过")
            continue

        sha_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{remote_path}"
        sha = None
        try:
            req = urllib.request.Request(sha_url, headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            })
            with urllib.request.urlopen(req, timeout=10, context=_ctx) as r:
                sha_data = json.loads(r.read().decode())
                sha = sha_data.get("sha")
        except Exception:
            pass

        with open(local_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode()

        payload = {"message": f"Update {remote_path} ({date.today().isoformat()})", "content": content_b64}
        if sha:
            payload["sha"] = sha

        try:
            resp = requests.put(sha_url, json=payload, headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "yimeng_strongbuy.py"
            }, timeout=20)
            if resp.status_code in (200, 201):
                d = resp.json()
                print(f"  [GitHub] {remote_path} → {d.get('commit',{}).get('sha','')[:8]}")
            else:
                print(f"  [GitHub] {remote_path} 失败({resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            print(f"  [GitHub] {remote_path} 异常: {e}")

# ── main ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("益盟强买选股 v2  (Sina换手率Top100 + 东方财富HTTP资金流)")
    print("=" * 55)

    # Step1
    print("\n[1/7] 获取 Sina 换手率 Top100...")
    stocks = fetch_sina_turnover_top(120)
    print(f"  获取到 {len(stocks)} 只股票")

    # Step2
    print("\n[2/7] 候选股筛选(换手率≥5%, 涨幅1%~9.9%)...")
    candidates = filter_candidates(stocks)
    if not candidates:
        print("  ⚠️ 无候选股,退出")
        sys.exit(0)

    # Step3+4
    print(f"\n[3/7] 获取资金流并评分(共{len(candidates)}只)...")
    scored = score_stocks(candidates, max_days=5)
    print(f"  评分完成,有效股票: {len(scored)}")

    if not scored:
        print("  ⚠️ 资金流数据获取失败,退出")
        sys.exit(0)

    # Step5
    print("\n[4/7] 选 Top10...")
    top10 = select_top(scored, 10)
    for i, s in enumerate(top10, 1):
        print(f"  #{i} {s['name']}({s['code']}) 评分:{s['score']} "
              f"净流入:{s['net_inflow_yi']:.2f}亿 换手:{s['turnover']:.1f}%")

    # Step6
    print("\n[5/7] 钉钉推送...")
    dingtalk_push(top10)

    # Step7
    print("\n[6/7] 保存 strongbuy_data.json...")
    save_json(top10)

    # Step8
    print("\n[7/7] GitHub 同步...")
    push_github()

    # 报告
    top1 = top10[0] if top10 else None
    if top1:
        print(f"\n✅ 益盟强买已完成，Top1: {top1['name']}")
    else:
        print("\n✅ 益盟强买已完成（无有效结果）")
