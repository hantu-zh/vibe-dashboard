
# ─── 路径兼容（自动识别 Windows / GitHub Actions） ───
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 确保能找到 paths.py
sys.path.insert(0, 'vibe-dashboard')  # vibe-dashboard 根（本地从 workspace 运行时）
from paths import WS, VIBE_DIR
# -*- coding: utf-8 -*-
"""
高欣-季度环比增长选股 v1.0
数据源: 妙想API（财务筛选）+ 通达信日线数据（技术确认）
策略: 季度净利润环比增长 + 营收增长 + 技术形态支撑
"""
import sys, os, json, ssl, struct, datetime, urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

WORKSPACE = WS

# ── 密钥：全部从环境变量 / .env 读取，不再硬编码 ──
def _load_dingtalk_webhook():
    """优先读环境变量 DINGTALK_WEBHOOK，其次读 .env.dingtalk 文件（多位置兜底）"""
    env_val = os.environ.get("DINGTALK_WEBHOOK")
    if env_val:
        return env_val.strip()
    candidates = [
        os.path.join(VIBE_DIR, ".env.dingtalk"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.dingtalk"),
        os.path.join(WS, ".env.dingtalk"),
    ]
    for p in candidates:
        try:
            if os.path.exists(p):
                for line in open(p, encoding="utf-8"):
                    line = line.strip()
                    if line.startswith("DINGTALK_WEBHOOK="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
    return ""

DINGTALK_WEBHOOK = _load_dingtalk_webhook()

# 妙想API（密钥仅从环境变量读取，缺失则提示，不内置默认值）
API_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/stock-screen"
API_KEY = os.environ.get("MX_APIKEY")
if not API_KEY:
    print("[WARN] 未设置环境变量 MX_APIKEY，妙想API将无法调用（本地建 .env 或在 CI 配置 secret）")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

# 通达信路径
TDX_PATHS = [
    r"E:\开心果【专业版+MPV】\vipdoc",
    r"D:\new_tdx\vipdoc",
    r"C:\new_tdx\vipdoc",
]

def find_tdx_path():
    for p in TDX_PATHS:
        if os.path.exists(p):
            return p
    return None

def read_tdx_day(code, market="sh"):
    """读取通达信日线数据"""
    tdx = find_tdx_path()
    if not tdx:
        return []
    suffix = ".day"
    path = os.path.join(tdx, market, "lday", f"{market}{code}{suffix}")
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'rb') as f:
            data = f.read()
        records = []
        for i in range(0, len(data), 32):
            rec = data[i:i+32]
            date_i, o, h, l, c, amt, vol, res = struct.unpack('<IIIIIffi', rec)
            yr, mo, dy = date_i // 10000, (date_i % 10000) // 100, date_i % 100
            records.append({
                'date': f"{yr}-{mo:02d}-{dy:02d}",
                'open': o / 100, 'high': h / 100, 'low': l / 100,
                'close': c / 100, 'amount': amt, 'volume': vol
            })
        return records[-60:]  # 最近60个交易日
    except Exception:
        return []

def read_prices_network(code, market="sh"):
    """云端兜底：从新浪财经 K 线接口拉取最近 ~60 个交易日日线（无需本地通达信）"""
    try:
        sym = f"{market}{code}"
        url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen=60")
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        )
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            raw = resp.read().decode("gbk", "replace")
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        out = []
        for d in data:
            try:
                out.append({
                    "date": d.get("day", ""),
                    "open": float(d.get("open", 0)),
                    "high": float(d.get("high", 0)),
                    "low": float(d.get("low", 0)),
                    "close": float(d.get("close", 0)),
                })
            except (TypeError, ValueError):
                continue
        return out[-60:]
    except Exception as e:
        print(f"  [WARN] 新浪行情获取失败 {code}: {e}")
        return []

def get_recent_days(code, market="sh"):
    """统一入口：优先本地通达信，云端/无通达信时回退到网络行情"""
    days = read_tdx_day(code, market)
    if days:
        return days, "local_tdx"
    net = read_prices_network(code, market)
    if net:
        return net, "network"
    return [], "none"

def call_miaoxiang(keyword, page=1, page_size=100):
    """调用妙想API"""
    payload = json.dumps({"keyword": keyword, "pageNo": page, "pageSize": page_size}).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=payload,
        headers={"Content-Type": "application/json", "apikey": API_KEY, "User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [WARN] 妙想API错误 {keyword}: {e}")
        return None

def extract_mx_rows(result):
    """从妙想API结果提取数据"""
    try:
        inner = result.get("data", {}).get("data", {})
        ar = inner.get("allResults", {})
        res = ar.get("result", {})
        cols = res.get("columns", [])
        data_list = res.get("dataList", [])
        title_to_key = {c.get("title", ""): c.get("key", "") for c in cols}

        def find_key(*subs):
            for t, k in title_to_key.items():
                if any(s in t for s in subs):
                    return k
            return None

        name_k = find_key("名称", "名称", "股票名称")
        code_k = find_key("代码", "股票代码")
        profit_k = find_key("净利润增长", "净利润增速", "净利润")
        rev_k = find_key("营收增长", "营业收入增长", "营收增长")
        roe_k = find_key("ROE", "净资产收益率")
        gross_k = find_key("毛利率", "毛利润")
        qoq_k = find_key("环比", "季度环比")

        rows = []
        for raw in data_list:
            def getv(k, default=""):
                if not k: return default
                v = raw.get(k, default)
                if v and "|" in str(v):
                    v = str(v).split("|")[0].strip()
                return v

            rows.append({
                "name": getv(name_k),
                "code": getv(code_k),
                "profit_growth": _parse_num(getv(profit_k, "0")),
                "revenue_growth": _parse_num(getv(rev_k, "0")),
                "roe": _parse_num(getv(roe_k, "0")),
                "gross_margin": _parse_num(getv(gross_k, "0")),
                "qoq": _parse_num(getv(qoq_k, "0")),
            })
        return rows
    except Exception as e:
        print(f"  [WARN] 提取数据失败: {e}")
        return []

def _parse_num(v):
    """解析带%或其他符号的数值"""
    if not v or v in ("-", "", "nan"):
        return 0.0
    s = str(v).replace("%", "").replace(",", "").replace(" ", "").strip()
    try:
        return float(s)
    except:
        return 0.0

def get_market(code):
    return "sh" if code.startswith(("6", "9")) else "sz"

def analyze_tech(days=20):
    """简单技术分析: 趋势强度、均线多头"""
    if len(days) < 5:
        return 0
    closes = [d['close'] for d in days]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else ma5
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else ma10
    last_close = closes[-1]
    # 均线多头 + 价格站稳均线
    score = 0
    if last_close > ma5 > ma10 > ma20:
        score += 30
    elif last_close > ma5 > ma10:
        score += 20
    elif last_close > ma5:
        score += 10
    # 趋势强度 (近20日涨幅)
    if len(closes) >= 20:
        trend = (closes[-1] - closes[-20]) / closes[-20] * 100
        if trend > 10:
            score += 20
        elif trend > 5:
            score += 15
        elif trend > 0:
            score += 10
    return score

def score_stock(row):
    """综合打分"""
    s = 0
    # 净利润增速 (最高30分)
    pg = row.get('profit_growth', 0)
    if pg >= 100: s += 30
    elif pg >= 50: s += 25
    elif pg >= 30: s += 20
    elif pg >= 10: s += 12
    elif pg > 0: s += 5
    # 营收增长 (最高20分)
    rg = row.get('revenue_growth', 0)
    if rg >= 50: s += 20
    elif rg >= 30: s += 15
    elif rg >= 15: s += 10
    elif rg > 0: s += 5
    # 季度环比 (最高15分)
    qoq = row.get('qoq', 0)
    if qoq >= 50: s += 15
    elif qoq >= 20: s += 10
    elif qoq > 0: s += 5
    # ROE (最高15分)
    roe = row.get('roe', 0)
    if roe >= 20: s += 15
    elif roe >= 10: s += 10
    elif roe >= 5: s += 6
    elif roe > 0: s += 3
    # 毛利率 (最高10分)
    gm = row.get('gross_margin', 0)
    if gm >= 50: s += 10
    elif gm >= 30: s += 7
    elif gm >= 15: s += 4
    elif gm > 0: s += 2
    # 技术分 (最高10分)
    s += row.get('tech_score', 0)
    return s

def push_dingtalk(stocks, date_str, time_str):
    """推送钉钉"""
    if not stocks:
        content = f"## 📊 季度环比增长选股\n\n**{date_str} {time_str}**\n\n今日暂无符合条件股票"
    else:
        lines = [f"## 📊 季度环比增长选股 Top10\n\n**{date_str} {time_str}**\n"]
        lines.append("> 数据源: 妙想API财务筛选 + 通达信技术确认")
        lines.append("> 评分: 净利润增速(30) + 营收增长(20) + 季度环比(15) + ROE(15) + 毛利率(10) + 技术(10)\n")
        lines.append("---\n")
        for i, s in enumerate(stocks, 1):
            trend_icon = "🟢" if s.get('trend_pct', 0) > 5 else ("🟡" if s.get('trend_pct', 0) > 0 else "🔴")
            lines.append(
                f"**{i}. {s['name']} ({s['code']})** {trend_icon}\n"
                f"   净利润增速 {s.get('profit_growth', 0):+.1f}% | "
                f"营收增长 {s.get('revenue_growth', 0):+.1f}% | "
                f"季度环比 {s.get('qoq', 0):+.1f}%\n"
                f"   ROE {s.get('roe', 0):.1f}% | "
                f"毛利率 {s.get('gross_margin', 0):.1f}% | "
                f"技术分 {s.get('tech_score', 0)}/10\n"
                f"   综合评分: **{s['score']}**\n"
            )
    text = "\n".join(lines)
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": "📊 季度环比增长选股 Top10", "text": text}
    }
    try:
        req = urllib.request.Request(
            DINGTALK_WEBHOOK,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, context=ctx, timeout=15) as r:
            res = json.loads(r.read().decode('utf-8-sig'))
            if res.get("errcode") == 0:
                print("  [OK] 钉钉推送成功")
                return True
            else:
                print(f"  [WARN] 钉钉推送失败: {res}")
                return False
    except Exception as e:
        print(f"  [WARN] 钉钉推送异常: {e}")
        return False

def main():
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    print("=" * 60)
    print(f"高欣-季度环比增长选股 v1.0  {date_str} {time_str}")
    print("=" * 60)

    # ── Step 1: 妙想API 多关键词财务筛选 ───────────────────────────
    keywords = [
        "季度净利润环比增长大于10%",
        "营收增长大于20%",
        "ROE大于5% 净利润正增长",
        "毛利率大于20% 环比增长",
        "净利润增速大于30%",
    ]

    all_rows = []
    seen = set()
    for kw in keywords:
        print(f"\n[查询] {kw}")
        result = call_miaoxiang(kw)
        if result:
            rows = extract_mx_rows(result)
            print(f"  获取 {len(rows)} 条")
            for r in rows:
                code = r.get('code', '')
                if code and code not in seen:
                    seen.add(code)
                    all_rows.append(r)
        else:
            print("  无数据")

    print(f"\n[汇总] 共 {len(all_rows)} 只候选股")

    if not all_rows:
        print("[WARN] 妙想API无数据，使用备用策略")
        # 备用: 用东方财富API获取财务数据
        all_rows = _fallback_ef()

    # ── Step 2: 技术分析 (通达信日线) ───────────────────────────────
    scored = []
    for row in all_rows:
        code = row.get('code', '')
        if not code:
            continue
        # 过滤
        if code.startswith(('8', '4', '83', '87', '43')):
            continue
        name = row.get('name', '')
        if 'ST' in name or 'st' in name or '退' in name:
            continue
        # 质量过滤: 环比增长 10% ~ 5000%（剔除基数异常导致的天文数字）
        qoq_v = row.get('qoq', 0) or row.get('profit_growth', 0)
        if qoq_v < 10 or qoq_v > 5000:
            continue

        market = get_market(code)
        days, price_src = get_recent_days(code, market)
        tech_score = analyze_tech(days)
        trend_pct = 0
        if len(days) >= 20:
            c0, c1 = days[-20]['close'], days[-1]['close']
            trend_pct = (c1 - c0) / c0 * 100 if c0 > 0 else 0

        row['price_src'] = price_src
        row['tech_score'] = min(tech_score, 10)  # 封顶10分
        row['trend_pct'] = round(trend_pct, 2)
        row['market'] = market
        row['score'] = score_stock(row)
        scored.append(row)

    # ── Step 3: 排序取Top10 ─────────────────────────────────────────
    scored.sort(key=lambda x: x['score'], reverse=True)
    top10 = scored[:10]

    print(f"\n[结果] Top10 选股:")
    for i, s in enumerate(top10, 1):
        print(f"  {i}. {s['name']} ({s['code']}) "
              f"净利润{s.get('profit_growth',0):+.1f}% "
              f"营收{s.get('revenue_growth',0):+.1f}% "
              f"环比{s.get('qoq',0):+.1f}% "
              f"ROE{s.get('roe',0):.1f}% "
              f"技术{s.get('tech_score',0)}/10 "
              f"总分={s['score']}")

    # ── Step 4: 保存 daily_picks.json ────────────────────────────────
    try:
        sys.path.insert(0, WORKSPACE)
        from daily_picks_store import save_daily_picks
        save_daily_picks('高欣季度环比增长', top10, task_time=time_str)
    except Exception as e:
        print(f"  [WARN] daily_picks_store失败: {e}")
    # 同时写 workspace/daily_picks.json（日期条目 + 季度环比增长_latest）
    _save_picks_legacy(top10, date_str, time_str)

    # ── Step 5: 同步 GitHub ──────────────────────────────────────────
    # 说明：云端由 CI 工作流（git add -A && commit && push）统一负责同步，
    # 本脚本仅在本地存在 sync_vibe_to_github.py 时才尝试自同步，否则安全跳过。
    try:
        sync_helper = os.path.join(WORKSPACE, "sync_vibe_to_github.py")
        if os.path.exists(sync_helper):
            print("\n[同步] 调用 sync_vibe_to_github.py ...")
            import subprocess
            r = subprocess.run(
                [sys.executable, sync_helper],
                capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                print("  [OK] GitHub同步成功")
            else:
                print(f"  [WARN] GitHub同步失败: {r.stderr[:200]}")
        else:
            print("\n[同步] 跳过：本机无 sync_vibe_to_github.py（云端由 CI 工作流统一推送）")
    except Exception as e:
        print(f"  [WARN] GitHub同步异常: {e}")

    # ── Step 6: 钉钉推送 ─────────────────────────────────────────────
    print("\n[推送] 钉钉...")
    push_dingtalk(top10, date_str, time_str)

    print("\n[DONE]")
    return top10


def _fallback_ef():
    """东方财富备用财务筛选"""
    print("[INFO] 使用东方财富财务数据作为备用...")
    try:
        import urllib.parse
        # 东方财富财务筛选API
        url = (
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page=1&num=200&sort=changepercent&asc=0&node=hs_a&symbol=&_s_r_a=page"
        )
        req = urllib.request.Request(url, headers={
            'Referer': 'https://finance.sina.com.cn/',
            'User-Agent': 'Mozilla/5.0'
        })
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            data = json.loads(r.read().decode('gbk', 'replace'))
        rows = []
        for item in data:
            code = item.get('code', '')
            if code.startswith(('8', '4', '83', '87', '43')):
                continue
            rows.append({
                'name': item.get('name', ''),
                'code': code,
                'profit_growth': 0,
                'revenue_growth': 0,
                'roe': 0,
                'gross_margin': 0,
                'qoq': 0,
            })
        return rows
    except Exception as e:
        print(f"  [WARN] 东方财富备用也失败: {e}")
        return []


def _save_picks_legacy(stocks, date_str, time_str):
    """备用保存逻辑 (直接写文件)"""
    picks_file = Path(WORKSPACE) / "daily_picks.json"
    data = {}
    if picks_file.exists():
        try:
            data = json.loads(picks_file.read_text(encoding='utf-8'))
        except:
            pass
    if date_str not in data:
        data[date_str] = {}
    data[date_str]['高欣季度环比增长'] = stocks
    data['季度环比增长_latest'] = {
        "time": time_str,
        "strategy": "季度环比增长",
        "count": len(stocks),
        "picks": stocks
    }
    picks_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"  [保存] workspace/daily_picks.json → 高欣季度环比增长 ({len(stocks)}只)")


if __name__ == '__main__':
    main()
