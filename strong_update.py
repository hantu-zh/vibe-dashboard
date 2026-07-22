"""
强势股雷达 - 盘后自动更新脚本
功能:从 Sina/efinance 获取强势股数据,更新 strong.html 的 embedded stocks 数组
运行:python strong_update.py
依赖:efinance, requests
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import json, os, re, time, ssl
import urllib.request as ur
from datetime import date
from pathlib import Path

# ── 路径配置 ─────────────────────────────────────────────
DASH_DIR = Path(r"C:\Users\china\.qclaw\workspace\vibe-dashboard")
HTML_PATH = DASH_DIR / "strong.html"
STRONG_JSON = DASH_DIR / "strongbuy_data.json"   # yimeng 数据(已有)
# vibe-dashboard repo 的 origin remote token (环境变量优先, 否则回退本地 .github_token 文件)
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN') or os.environ.get('VIBE_GITHUB_TOKEN')
if not GITHUB_TOKEN:
    try:
        GITHUB_TOKEN = open(r'C:\Users\china\.qclaw\workspace\.github_token', encoding='utf-8-sig').read().strip()
    except Exception:
        GITHUB_TOKEN = None
GITHUB_REPO = "hantu-zh/vibe-dashboard"

# ── SSL context ────────────────────────────────────────────
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# ── 从 Sina 获取涨幅榜 Top N ─────────────────────────────
def fetch_sina_top(n=60):
    """获取沪深A股今日涨幅排行(使用正确的 Sina API)"""
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page=1&num={}&sort=changepercent&asc=0&node=hs_a"
    ).format(n * 2)
    raw = _fetch_raw(url)
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
            result.append({
                "code": code, "name": name,
                "price": item.get("trade", 0),
                "change_pct": item.get("changepercent", 0),
                "turnover": item.get("turnoverratio", 0),
                "volume": item.get("volume", 0),
                "amount": item.get("amount", 0),
            })
            if len(result) >= n:
                break
        return result
    except Exception as e:
        print(f"    Sina 涨幅榜解析失败: {e}")
        return []


def fetch_sina_turnover_top(n=80):
    """获取换手率排行(使用正确的 Sina API)"""
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page=1&num={}&sort=turnoverratio&asc=0&node=hs_a"
    ).format(n * 2)
    raw = _fetch_raw(url, encoding="gbk")
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
            result.append({
                "code": code, "name": name,
                "price": item.get("trade", 0),
                "change_pct": item.get("changepercent", 0),
                "turnover": item.get("turnoverratio", 0),
                "volume": item.get("volume", 0),
                "amount": item.get("amount", 0),
            })
            if len(result) >= n:
                break
        return result
    except Exception as e:
        print(f"    Sina 换手率榜解析失败: {e}")
        return []


def _fetch_raw(url, encoding="utf-8"):
    """通用 HTTP 请求"""
    req = ur.Request(url, headers={
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        with ur.urlopen(req, timeout=15, context=_ctx) as r:
            return r.read().decode(encoding, errors="replace")
    except Exception as e:
        print(f"    HTTP 请求失败: {e}")
        return None

# ── 获取单只股票实时行情 ──────────────────────────────────
def fetch_quote_sina(code):
    """获取单只股票实时数据"""
    # 转换代码前缀
    if code.startswith("6"):
        sym = f"sh{code}"
    else:
        sym = f"sz{code}"
    url = f"https://hq.sinajs.cn/list={sym}"
    raw = get(url, headers={"Referer": "https://finance.sina.com.cn"})
    if not raw:
        return None
    try:
        m = re.search(r'"([^"]+)"', raw)
        if not m:
            return None
        fields = m.group(1).split(",")
        # fields: 名称, 今开, 昨收, 当前价, 最高, 最低, ...(9)买入价,(10)卖出价,(11)成交量(手),(12)成交额(万),...
        if len(fields) < 15:
            return None
        name = fields[0]
        open_p = float_or(fields[1], 0)
        prev_close = float_or(fields[2], 0)
        price = float_or(fields[3], 0)
        high = float_or(fields[4], 0)
        low = float_or(fields[5], 0)
        vol = int_or(fields[8], 0)   # 成交量(手)
        amount = float_or(fields[9], 0)  # 成交额(元)
        return {"name": name, "price": price, "open": open_p, "prev_close": prev_close,
                "high": high, "low": low, "vol": vol, "amount": amount}
    except:
        return None

def float_or(s, default=0.0):
    try:
        return float(s)
    except:
        return default

def int_or(s, default=0):
    try:
        return int(s)
    except:
        return default

# ── 批量获取行情 ──────────────────────────────────────────
def batch_quotes_sina(codes, batch=50, delay=0.1):
    """批量获取股票实时行情(每批50只)"""
    results = {}
    code_list = list(codes)
    for i in range(0, len(code_list), batch):
        batch_codes = code_list[i:i+batch]
        syms = "|".join(
            f"sh{c}" if c.startswith("6") else f"sz{c}"
            for c in batch_codes
        )
        url = f"https://hq.sinajs.cn/list={syms}"
        raw = get(url, headers={"Referer": "https://finance.sina.com.cn"})
        if not raw:
            time.sleep(delay)
            continue
        # 解析每个股票
        parts = raw.split(";")
        for part in parts:
            m = re.search(r'"([^"]+)"', part)
            if not m:
                continue
            try:
                fields = m.group(1).split(",")
                if len(fields) < 15:
                    continue
                # 从 hq.sinajs.cn/r/list=sh600001 获取代码
                cm = re.search(r'(?:sh|sz)(\d{6})', part)
                if not cm:
                    continue
                code = cm.group(1)
                name = fields[0]
                price = float_or(fields[3], 0)
                prev_close = float_or(fields[2], 0)
                high = float_or(fields[4], 0)
                low = float_or(fields[5], 0)
                vol = int_or(fields[8], 0)
                amount = float_or(fields[9], 0)
                if price <= 0:
                    continue
                chg_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                chg_5d = 0.0  # Sina 实时数据无5日涨跌
                turnover = vol / 100 / (1e8) if price > 0 else 0  # 估算换手率
                amount_yi = amount / 1e8  # 亿元
                results[code] = {
                    "code": code, "name": name, "price": round(price, 2),
                    "change_pct": round(chg_pct, 2),
                    "turnover": round(turnover, 1),
                    "change_5d": round(chg_5d, 1),
                    "volume": round(amount_yi, 3)
                }
            except:
                continue
        time.sleep(delay)
    return results

# ── 分类函数 ──────────────────────────────────────────────
def classify_stock(s, all_quotes):
    """根据行情数据分类股票"""
    code = s["code"]
    chg = s.get("change_pct", 0)
    vol_yi = s.get("volume", 0)
    turnover = s.get("turnover", 0)

    # 计算5日涨跌(如果有历史数据,这里用估算)
    change_5d = s.get("change_5d", 0)

    if chg >= 9.5:  # 接近涨停
        return "newhigh", 5, f"涨幅{chg:.1f}%,逼近涨停"
    elif chg >= 5:
        return "breakout", 5, f"强势突破{chg:.1f}%,换手{turnover:.1f}%,成交{vol_yi:.1f}亿"
    elif chg >= 3:
        if turnover >= 5:
            return "volume", 4, f"量价齐升,涨幅{chg:.1f}%,换手{turnover:.1f}%"
        else:
            return "breakout", 4, f"稳健上行{chg:.1f}%"
    elif chg >= 1:
        return "resilient", 3, f"温和上涨{chg:.1f}%"
    else:
        return "resilient", 2, f"小幅波动{chg:.1f}%"

# ── 主更新函数 ────────────────────────────────────────────
def update_strong_stocks():
    today = date.today().isoformat()
    print(f"[strong_update] 今日日期: {today}")

    all_stocks = {}  # code -> stock_dict

    # 1. 从涨幅榜获取数据
    print("[strong_update] 获取涨幅榜数据...")
    gainers = fetch_sina_top(80)
    print(f"  涨幅榜原始记录数: {len(gainers)}")

    for idx, row in enumerate(gainers):
        try:
            code = str(row.get("code", "")).strip()
            if not code or len(code) != 6:
                continue
            chg = float_or(row.get("change_pct", 0))
            price = float_or(row.get("price", 0))
            vol_yi = float_or(row.get("amount", 0)) / 1e8
            turnover = float_or(row.get("turnover", 0))
            name = row.get("name", "")

            if not name or price <= 0:
                continue

            all_stocks[code] = {
                "code": code,
                "name": name,
                "price": round(price, 2),
                "change_pct": round(chg, 2),
                "turnover": round(turnover, 1),
                "change_5d": round(chg * 0.6, 1),  # 粗估
                "volume": round(vol_yi, 3),
                "tag": "newhigh",
                "score": 5 if chg >= 9 else 4 if chg >= 5 else 3,
                "reason": f"涨幅榜前列(第{idx+1}名),飙升{chg:.1f}%"
            }
        except Exception as e:
            continue

    # 2. 从换手率榜补充(未收录的)
    print("[strong_update] 获取换手率榜数据...")
    turnover_list = fetch_sina_turnover_top(80)
    print(f"  换手率榜原始记录数: {len(turnover_list)}")

    for row in turnover_list:
        try:
            code = str(row.get("code", "")).strip()
            if not code or len(code) != 6:
                continue
            if code in all_stocks:
                continue  # 已收录

            chg = float_or(row.get("change_pct", 0))
            price = float_or(row.get("price", 0))
            vol_yi = float_or(row.get("amount", 0)) / 1e8
            turnover = float_or(row.get("turnover", 0))
            name = row.get("name", "")

            if not name or price <= 0:
                continue

            if turnover >= 8 and chg >= 3:
                tag, score, reason = "volume", 5, f"量价齐升,换手{turnover:.1f}%,成交{vol_yi:.1f}亿"
            elif chg >= 5:
                tag, score, reason = "breakout", 5, f"强势突破{chg:.1f}%,换手{turnover:.1f}%"
            elif chg >= 0:
                tag, score, reason = "resilient", 4, f"高换手{turnover:.1f}%,成交{vol_yi:.1f}亿"
            else:
                tag, score, reason = "resilient", 3, f"换手{turnover:.1f}%"

            all_stocks[code] = {
                "code": code, "name": name, "price": round(price, 2),
                "change_pct": round(chg, 2), "turnover": round(turnover, 1),
                "change_5d": round(chg * 0.6, 1), "volume": round(vol_yi, 3),
                "tag": tag, "score": score, "reason": reason
            }
        except:
            continue

    # 3. 分类整理
    tag_order = ["newhigh", "breakout", "volume", "resilient"]
    stocks = list(all_stocks.values())

    # 按分类聚合,每类最多3只
    result = []
    for tag in tag_order:
        tag_stocks = [s for s in stocks if s["tag"] == tag]
        # 按涨幅排序
        tag_stocks.sort(key=lambda x: x["change_pct"], reverse=True)
        for s in tag_stocks[:3]:
            result.append(s)

    print(f"[strong_update] 最终选中 {len(result)} 只强势股")
    for s in result:
        print(f"  [{s['tag']}] {s['name']}({s['code']}) {s['change_pct']:+.2f}% 换手{s['turnover']:.1f}%")

    return today, result

# ── 更新 strong.html ──────────────────────────────────────
def update_html(today, stocks):
    print(f"[strong_update] 更新 strong.html...")

    if not HTML_PATH.exists():
        print("[strong_update] ERROR: strong.html not found!")
        return False

    with open(HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # 生成 stocks JSON 字符串
    stocks_json = json.dumps(stocks, ensure_ascii=False)

    # ── 读 yimeng 数据（优先从独立文件读，备选从 HTML 读）────────────────
    yimeng_file = DASH_DIR / "yimeng_data.json"
    yimeng_data = "[]"
    if yimeng_file.exists():
        try:
            yimeng_raw = json.loads(yimeng_file.read_text(encoding='utf-8'))
            yimeng_list = yimeng_raw.get('stocks', [])
            if yimeng_list:
                yimeng_data = json.dumps(yimeng_list, ensure_ascii=False)
                print(f"[update_html] 从 yimeng_data.json 读取 {len(yimeng_list)} 只益盟强买")
        except Exception as e:
            print(f"[update_html] yimeng_data.json 读取失败: {e}")

    if yimeng_data == "[]":
        # 备选：从旧 HTML 读（此时旧块仍在 html 中）
        ym = re.search(r'"yimeng"\s*:\s*(\[[^\]]+\])', html, re.DOTALL)
        if ym:
            yimeng_data = ym.group(1)
            print(f"[update_html] 从 HTML 读取 yimeng 数据（{len(json.loads(yimeng_data))} 只）")

    # 构建新的 _data 块
    new_data_block = (
        f'var _data = {{\n'
        f'    "updated": "{today}",\n'
        f'    "stocks": {stocks_json},\n'
        f'    "yimeng": {yimeng_data}\n'
        f'  }};'
    )

    # 一次性替换整个 var _data = { ... }; 块
    # re.DOTALL + 惰性 .*? 匹配到首个 };，无论数据内是否含 ; / { / } 都能完整吃掉旧块；
    # 模式包含 var 前缀，避免旧的 [^;]+ + find("_data = {") 方案把 var 前缀漏掉，
    # 导致生成 "var var _data" 且旧数据残留被追加
    new_html, n = re.subn(r'(?:var\s+)?_data\s*=\s*\{.*?\}\s*;', new_data_block, html, count=1, flags=re.DOTALL)
    if n == 0:
        print("[strong_update] ERROR: _data block not found!")
        return False

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)

    print(f"[strong_update] strong.html 已更新 ({len(stocks)} 只)")
    return True

# ── GitHub 推送 ───────────────────────────────────────────
def push_github():
    """推送更新到 GitHub(使用 origin remote 的 token)"""
    import subprocess, base64

    files_to_push = {
        "strong.html": str(HTML_PATH),
        "strongbuy_data.json": str(STRONG_JSON)
    }

    for remote_path, local_path in files_to_push.items():
        if not os.path.exists(local_path):
            print(f"  [GitHub] {remote_path} 本地文件不存在,跳过")
            continue

        sha_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{remote_path}"

        # 获取 SHA(如果文件已存在)
        sha = None
        try:
            req = ur.Request(sha_url, headers={
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            })
            with ur.urlopen(req, timeout=10, context=_ctx) as r:
                sha_data = json.loads(r.read().decode())
                sha = sha_data.get("sha")
        except Exception:
            pass  # 文件不存在,正常

        # 读取文件内容
        with open(local_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode()

        # 构建 payload
        payload = {
            "message": f"Update {remote_path} ({date.today().isoformat()})",
            "content": content_b64,
        }
        if sha:
            payload["sha"] = sha

        # 使用 requests(更可靠)
        try:
            import requests
            session = requests.Session()
            session.headers.update({
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "strong_update.py"
            })
            resp = session.put(sha_url, json=payload, timeout=20)
            if resp.status_code in (200, 201):
                d = resp.json()
                print(f"  [GitHub] {remote_path} -> {d.get('commit',{}).get('sha','')[:8]}")
            else:
                print(f"  [GitHub] {remote_path} 失败({resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            print(f"  [GitHub] {remote_path} 请求异常: {e}")

# ── main ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("="*50)
    print("强势股雷达 - 盘后自动更新")
    print("="*50)

    try:
        today, stocks = update_strong_stocks()
        if stocks:
            ok = update_html(today, stocks)
            if ok:
                push_github()
                print(f"\n✅ 强势股数据已更新({len(stocks)} 只)")
            else:
                print("\n⚠️ HTML 更新失败")
        else:
            print("\n⚠️ 未获取到任何强势股数据")
    except Exception as e:
        import traceback
        print(f"\n❌ 脚本异常: {e}")
        traceback.print_exc()
