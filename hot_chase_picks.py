# -*- coding: utf-8 -*-
"""
追涨强势股 - 换手率追涨选股
数据源：Sina 换手率排行榜（免费接口）
时段：10:00 / 12:00 / 14:00 各运行一次，写入 daily_picks.json
保存策略key：追涨强势股（合并）/ 追涨强势股_10:00（分时段）
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

import json, time, ssl, urllib.request as ur
import re
import argparse
from datetime import datetime

# ── 统一存储 ──────────────────────────────────────────────
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))  # 仓库根，自动适配 Linux
try:
    from daily_picks_store import save_daily_picks
except Exception as e:
    print(f"[hot_chase] daily_picks_store 加载失败: {e}")
    save_daily_picks = None

# ── SSL ──────────────────────────────────────────────────
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE

# ── 时段判断 ─────────────────────────────────────────────
# 补跑时由 workflow 通过 --period 显式指定时段（例如 17:00 补跑 14:00 的任务），
# 否则会按当前钟点算出「15:30」写错 key，页面上 14:00 那一档依旧是空的。
PERIOD_LABEL = {'10:00': '早盘', '12:00': '午盘', '14:00': '午盘', '15:30': '收盘'}

# 每个时段的「允许写入窗口」：超过这个窗口再跑，抓到的已是收盘后冻结快照，
# 写进去只会让不同时间段显示一模一样的假数据（2026-09-09 三次补跑全落在收盘后，
# 10:00/12:00/14:00 抓到同一份快照，页面三档完全相同）。离谱超时直接跳过，
# 宁可留空档，也不写假数据。
PERIOD_WINDOWS = {
    '10:00': ('10:00', '12:00'),
    '12:00': ('12:00', '14:00'),
    '14:00': ('14:00', '15:30'),
    '15:30': ('15:30', '16:30'),
}

def shanghai_now():
    """返回上海时区当前时间（优先 zoneinfo，退化则信任 TZ 环境变量）。"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Asia/Shanghai'))
    except Exception:
        return datetime.now()

def _hm(s):
    h, m = s.split(':')
    return int(h) * 60 + int(m)

def in_period_window(period_str):
    if period_str not in PERIOD_WINDOWS:
        return True
    start, end = PERIOD_WINDOWS[period_str]
    now = _hm(shanghai_now().strftime('%H:%M'))
    return _hm(start) <= now <= _hm(end)



def get_period():
    now = datetime.now()
    h, m = now.hour, now.minute
    if h < 11:
        return "10:00", "早盘"
    elif h < 13:
        return "12:00", "午盘"
    elif h < 15:
        return "14:00", "午盘"
    else:
        return "15:30", "收盘"

# ── HTTP ─────────────────────────────────────────────────
def _fetch_raw(url, encoding="utf-8"):
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

def float_or(s, default=0.0):
    try:
        return float(s)
    except:
        return default

# ── 获取换手率排行榜 Top N ────────────────────────────────
def fetch_turnover_top(n=120):
    """获取沪深A股换手率排行（使用 Sina 免费接口）"""
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeData?page=1&num={n * 2}&sort=turnoverratio&asc=0&node=hs_a"
    )
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
            # 排除：B股(90/92/200)、新三板(83/87)、ST(4开头)、退市(43开头)
            if not code or len(code) != 6:
                continue
            if code.startswith(("8", "4", "83", "87", "43", "90", "92", "200")):
                continue
            name = item.get("name", "")
            if not name or "ST" in name or "st" in name or "\u9000" in name:
                continue
            result.append({
                "code": code,
                "name": name,
                "price": float_or(item.get("trade"), 0),
                "prev_close": float_or(item.get("settlement"), 0),
                "open": float_or(item.get("open"), 0),
                "high": float_or(item.get("high"), 0),
                "low": float_or(item.get("low"), 0),
                "change_pct": float_or(item.get("changepercent"), 0),
                "turnover": float_or(item.get("turnoverratio"), 0),  # 换手率%
                "volume": float_or(item.get("volume"), 0),            # 成交量(股)
                "amount": float_or(item.get("amount"), 0),            # 成交额(元)
            })
            if len(result) >= n:
                break
        return result
    except Exception as e:
        print(f"    Sina 换手率榜解析失败: {e}")
        return []

# ── 批量获取行情（补全数据）────────────────────────────────
def batch_quotes_sina(codes, batch=50, delay=0.1):
    """批量获取股票实时行情（每批50只）"""
    results = {}
    code_list = list(codes)
    for i in range(0, len(code_list), batch):
        batch_codes = code_list[i:i+batch]
        syms = "|".join(
            f"sh{c}" if c.startswith("6") else f"sz{c}"
            for c in batch_codes
        )
        url = f"https://hq.sinajs.cn/list={syms}"
        raw = _fetch_raw(url)
        if not raw:
            time.sleep(delay)
            continue
        parts = raw.split(";")
        for part in parts:
            m = re.search(r'"([^"]+)"', part)
            if not m:
                continue
            try:
                fields = m.group(1).split(",")
                if len(fields) < 15:
                    continue
                cm = re.search(r'(?:sh|sz)(\d{6})', part)
                if not cm:
                    continue
                code = cm.group(1)
                name = fields[0]
                price = float_or(fields[3], 0)
                prev_close = float_or(fields[2], 0)
                open_p = float_or(fields[1], 0)
                high = float_or(fields[4], 0)
                low = float_or(fields[5], 0)
                vol = float_or(fields[8], 0)   # 成交量(手)
                amount_yi = float_or(fields[9], 0) / 1e8  # 成交额(万元->亿元)
                if price <= 0:
                    continue
                chg_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                results[code] = {
                    "name": name,
                    "price": round(price, 2),
                    "open": round(open_p, 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "prev_close": round(prev_close, 2),
                    "change_pct": round(chg_pct, 2),
                    "volume": round(amount_yi, 2),  # 亿元
                }
            except Exception:
                continue
        time.sleep(delay)
    return results

# ── 打分筛选 ─────────────────────────────────────────────
def score_and_filter(stocks, period_label):
    """
    追涨选股打分逻辑：
    - 换手率 >5% → +40分（活跃）
    - 涨幅 3-10% → +30分（强势但不至涨停）
    - 涨幅 >10% → +10分（过高，涨停风险）
    - 成交额 >1亿 → +20分
    - 价格 2-80元 → +10分（排除低价垃圾股）
    最终取 Top 15，按综合分降序
    """
    scored = []
    for s in stocks:
        code = s["code"]
        turnover = s.get("turnover", 0)
        change = s.get("change_pct", 0)
        amount_yi = s.get("amount", 0) / 1e8 if s.get("amount", 0) else 0
        price = s.get("price", 0)

        score = 0
        reasons = []

        # 换手率打分
        if turnover >= 15:
            score += 40
            reasons.append(f"换手{turnover:.1f}%")
        elif turnover >= 10:
            score += 30
            reasons.append(f"换手{turnover:.1f}%")
        elif turnover >= 5:
            score += 20
            reasons.append(f"换手{turnover:.1f}%")
        elif turnover >= 3:
            score += 10

        # 涨幅打分
        if 3 <= change <= 10:
            score += 30
            reasons.append(f"涨幅{change:+.1f}%")
        elif change > 10:
            score += 10
            reasons.append(f"涨幅{change:+.1f}%[偏高]")
        elif 1 <= change < 3:
            score += 15
            reasons.append(f"涨幅{change:+.1f}%")

        # 成交额
        if amount_yi >= 3:
            score += 20
        elif amount_yi >= 1:
            score += 10

        # 价格范围
        if 2 <= price <= 80:
            score += 10
        elif price < 2:
            continue  # 排除仙股

        if score >= 30:
            scored.append({
                "code": code,
                "name": s["name"],
                "price": s.get("price", 0),
                "change_pct": change,
                "turnover": turnover,
                "volume_yi": round(amount_yi, 2),
                "score": score,
                "reason": " | ".join(reasons),
            })

    # 按分数降序，取前15只
    scored.sort(key=lambda x: (x["score"], x["turnover"]), reverse=True)
    return scored[:15]

# ── 钉钉推送 ──────────────────────────────────────────────
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"

def send_dingtalk(stocks, period_str, period_label):
    if not stocks:
        print(f"[hot_chase] {period_str} {period_label} 无数据，跳过钉钉推送")
        return

    title = f"追涨强势股 {period_label} {period_str}"
    lines = [f"## 追涨强势股 {period_label}", "", f"**时段：{period_str}**", f"**共{len(stocks)}只**", ""]
    for i, s in enumerate(stocks, 1):
        tag = "[强]" if s["score"] >= 60 else "[中]"
        lines.append(f"{i}. {tag} {s['name']}({s['code']}) 现价{s['price']:.2f} {s['change_pct']:+.2f}% 换手{s['turnover']:.1f}% 成交{s['volume_yi']:.1f}亿")
    lines.append("")
    lines.append(f"> 综合打分，追踪活跃强势股")

    content = "\n".join(lines)
    payload = json.dumps({
        "msgtype": "markdown",
        "markdown": {"title": title, "text": content}
    }, ensure_ascii=False).encode("utf-8")
    req = ur.Request(DINGTALK_WEBHOOK, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with ur.urlopen(req, timeout=15, context=_ctx) as r:
            result = json.loads(r.read().decode("utf-8"))
            ok = result.get("errcode") == 0
            print(f"[hot_chase] 钉钉推送: {'OK' if ok else 'FAIL'} {result.get('errmsg','')}")
            return ok
    except Exception as e:
        print(f"[hot_chase] 钉钉推送失败: {e}")
        return False

# ── 主逻辑 ───────────────────────────────────────────────
def run(data_date=None, period=None, period_label=None):
    from datetime import datetime as _dt
    actual_date = data_date or _dt.now().strftime('%Y-%m-%d')
    if period:
        period_str = period
        period_label = period_label or PERIOD_LABEL.get(period, '盘中')
    else:
        period_str, period_label = get_period()
    # 防假数据：显式指定时段但当前已远超该时段窗口（典型场景：调度链断裂后收盘才补跑），
    # 此时抓到的是收盘冻结快照，写进去会污染页面，直接跳过且不标记完成，等下次正常时段再跑。
    if period and not in_period_window(period_str):
        print(f"[hot_chase][跳过] 当前上海时间 {shanghai_now().strftime('%H:%M')} 已超出 {period_str} 时段窗口 "
              f"{PERIOD_WINDOWS.get(period_str)}，跳过写入以避免假数据（将于正常时段重跑）")
        sys.exit(2)
    print(f"[hot_chase] 追涨强势股 {period_label} {period_str} 开始执行... (数据日期: {actual_date})")


    # 1. 获取换手率榜
    raw_stocks = fetch_turnover_top(120)
    print(f"[hot_chase] 换手率榜获取 {len(raw_stocks)} 条")

    if not raw_stocks:
        print("[hot_chase] 获取换手率榜失败，尝试备用方案...")
        # 备选：直接批量获取沪、深两市实时行情再排序
        # 用已知高换手股票池
        pass

    # 2. 补全行情数据（用于更精确的涨跌幅）
    codes = [s["code"] for s in raw_stocks[:60]]
    quotes = batch_quotes_sina(codes)
    for s in raw_stocks:
        code = s["code"]
        if code in quotes:
            q = quotes[code]
            s["price"] = q.get("price", s["price"])
            s["change_pct"] = q.get("change_pct", s["change_pct"])
            s["high"] = q.get("high", s.get("high", 0))
            s["low"] = q.get("low", s.get("low", 0))

    # 3. 打分筛选
    stocks = score_and_filter(raw_stocks, period_label)
    print(f"[hot_chase] 筛选后 {len(stocks)} 只强势股")
    for s in stocks[:5]:
        print(f"  [{s['score']}] {s['name']}({s['code']}) {s['change_pct']:+.2f}% 换手{s['turnover']:.1f}% 成交{s['volume_yi']:.1f}亿")

    # 4. 保存到 daily_picks.json（按时段 key）
    if save_daily_picks:
        save_daily_picks("追涨强势股", stocks, task_time=period_str, data_date=actual_date)
    else:
        print("[hot_chase] WARNING: save_daily_picks 不可用，仅打印结果")

    # 5. 钉钉推送
    send_dingtalk(stocks, period_str, period_label)

    return stocks

# ── 入口 ─────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='追涨强势股')
    parser.add_argument('--data-date', dest='data_date', default=None,
                        help='数据日期 (YYYY-MM-DD)，默认为今天')
    parser.add_argument('--period', dest='period', default=None,
                        help='强制指定时段 10:00/12:00/14:00/15:30（补跑时用，避免按当前钟点算错）')
    parser.add_argument('--label', dest='label', default=None,
                        help='时段标签（早盘/午盘/收盘），配合 --period 使用')
    args = parser.parse_args()
    run(data_date=args.data_date, period=args.period, period_label=args.label)
