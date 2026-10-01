#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
强势股精选 v3 (Sina版) — 替代妙想API版
选股条件: 非ST 非亏损(市值>30亿) 今日涨幅>2% 量价齐升 换手率>3%
数据源: Sina实时行情 API
保存: daily_picks.json (dict格式，兼容 sync_func.py)
"""
import sys, json, os, datetime, urllib.request, ssl, time

sys.stdout.reconfigure(encoding='utf-8')

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DINGTALK_ENV = os.path.join(REPO_DIR, '.env.dingtalk')
DAILY_PICKS = os.path.join(REPO_DIR, 'daily_picks.json')

# ─── 钉钉推送 ─────────────────────────────────────────────────────────

def load_dingtalk_webhook():
    env = os.environ.get('DINGTALK_WEBHOOK') or os.environ.get('DINGTALK_TOKEN')
    if env:
        return env
    try:
        with open(DINGTALK_ENV, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('DINGTALK_WEBHOOK='):
                    return line.strip().split('=', 1)[1].strip()
    except Exception:
        pass
    return None

def push_dingtalk(msg: str, webhook: str):
    if not webhook:
        print('[DingTalk] webhook 未配置，跳过推送')
        return
    payload = json.dumps({'msgtype': 'text', 'text': {'content': msg}}).encode('utf-8')
    req = urllib.request.Request(webhook, data=payload, headers={
        'Content-Type': 'application/json'
    })
    try:
        with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
            result = json.loads(r.read().decode('utf-8'))
            if result.get('errcode') == 0:
                print('[DingTalk] 推送成功')
            else:
                print(f'[DingTalk] 推送失败: {result}')
    except Exception as e:
        print(f'[DingTalk] 推送异常: {e}')

# ─── Sina 数据获取 ─────────────────────────────────────────────────────

SINA_RANK_URL = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'

def fetch_sina(num=400):
    all_stocks = []
    for page in range(1, 4):
        url = f'{SINA_RANK_URL}?page={page}&num={num}&sort=turnoverratio&asc=0&node=hs_a'
        req = urllib.request.Request(url, headers={
            'Referer': 'https://finance.sina.com.cn/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        })
        try:
            with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
                raw = json.loads(r.read().decode('gbk', errors='replace'))
                if not raw:
                    break
                all_stocks.extend(raw)
        except Exception as e:
            print(f'  [Sina] 第{page}页失败: {e}')
            break
        time.sleep(0.3)
    return all_stocks

def parse_sina_row(row):
    try:
        code = str(row.get('code', ''))
        name = str(row.get('name', ''))
        if not code or not name:
            return None
        if 'ST' in name or '*' in name or '退' in name:
            return None
        if code.startswith(('87', '83', '430', '830', '8', '4', '90', '91', '92', '99', '20')):
            return None

        chg = float(row.get('changepercent', 0))
        turn = float(row.get('turnoverratio', 0)) if row.get('turnoverratio') else 0
        price = float(row.get('trade', 0))
        high = float(row.get('high', 0))
        low = float(row.get('low', 0))
        amount = float(row.get('amount', 0))
        mktcap_str = row.get('mktcap', 0)

        amplitude = 0
        if low > 0:
            amplitude = (high - low) / low * 100

        return {
            'code': code, 'name': name, 'price': price,
            'chg': chg, 'turn': turn, 'amount': amount,
            'amplitude': amplitude, 'mktcap_str': mktcap_str,
        }
    except Exception:
        return None

def filter_candidates(stocks):
    candidates = []
    for s in stocks:
        if not s:
            continue
        chg = s['chg']
        turn = s['turn']
        price = s['price']
        if price <= 0 or chg < 2.0 or chg >= 9.9 or turn < 3.0 or price < 3.0:
            continue
        try:
            mktcap_val = float(s['mktcap_str']) if s['mktcap_str'] else 0
            if mktcap_val > 0 and mktcap_val < 30:
                continue
        except Exception:
            pass
        score = chg * 0.4 + turn * 0.6
        candidates.append((score, s))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in candidates]

def pick_top_stocks(candidates, limit=15):
    picks = []
    remaining = list(candidates)

    newhigh = [s for s in remaining if s['chg'] >= 7 and s['turn'] >= 8]
    newhigh.sort(key=lambda x: x['chg'] * 0.5 + x['turn'] * 0.5, reverse=True)
    for s in newhigh[:3]:
        picks.append(s); remaining.remove(s)

    vol = [s for s in remaining if s['turn'] >= 10]
    vol.sort(key=lambda x: x['turn'] + x['chg'] * 2, reverse=True)
    for s in vol[:6]:
        picks.append(s); remaining.remove(s)

    brk = [s for s in remaining if 3 <= s['chg'] <= 7 and 5 <= s['turn'] <= 15]
    brk.sort(key=lambda x: x['chg'] + x['turn'], reverse=True)
    for s in brk[:4]:
        picks.append(s); remaining.remove(s)

    for s in remaining[:limit - len(picks)]:
        picks.append(s)
    return picks[:limit]

def build_reason(s):
    parts = []
    if s['chg'] >= 7:
        parts.append('创新高')
    elif s['chg'] >= 4:
        parts.append('强势')
    else:
        parts.append('量价齐升')
    if s['turn'] >= 15:
        parts.append(f'换手{s["turn"]:.1f}%')
    elif s['turn'] >= 10:
        parts.append(f'换手{s["turn"]:.1f}%')
    if s['amplitude'] >= 8:
        parts.append(f'振幅{s["amplitude"]:.1f}%')
    if s['amount'] > 1000000000:
        parts.append(f'成交{s["amount"]/100000000:.0f}亿')
    return parts

# ─── 保存 daily_picks.json (dict格式) ──────────────────────────────────

def save_daily_picks(picks):
    today_str = datetime.datetime.now().strftime('%Y-%m-%d')
    time_str = datetime.datetime.now().strftime('%H:%M')

    existing = {}
    if os.path.exists(DAILY_PICKS):
        try:
            with open(DAILY_PICKS, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    if today_str not in existing:
        existing[today_str] = {}

    task_name = '强势股精选Sina版'
    pick_records = []
    for s in picks:
        reason = build_reason(s)
        score = int(s['chg'] * 10 + s['turn'] * 5)
        pick_records.append({
            'name': s['name'],
            'code': s['code'],
            'price': f"{s['price']:.3f}",
            'change': round(s['chg'], 2),
            'turnover': f"{s['turn']:.5f}",
            'score': score,
            'limit_score': 0,
            'total': score,
            'level': '强势' if s['chg'] >= 5 else '量价齐升',
            'action': '关注' if s['chg'] >= 7 else '观察',
            'rules': reason,
            'change_val': round(s['chg'], 2),
            'chan_score': 0,
            'chan_buy': None,
            'chan_buy_price': f"{s['price']:.3f}",
            'chan_bottom_div': None,
            'chan_confidence': 0,
            'final_score': score,
        })

    existing[today_str][task_name] = {
        'time': time_str,
        'picks': pick_records,
    }

    with open(DAILY_PICKS, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    return pick_records

# ─── 钉钉消息 ─────────────────────────────────────────────────────────

def format_dingtalk(picks, date_str):
    if not picks:
        return None
    lines = [f'强势股精选（下午版）', f'{date_str}', '']
    for s in picks:
        chg = s['chg']
        turn = s['turn']
        icon = '🔴' if chg >= 7 else ('🟠' if chg >= 4 else '🟡')
        reason = ' '.join(build_reason(s))
        lines.append(f'{icon} {s["name"]}({s["code"]}) +{chg:.1f}% 换手{turn:.1f}%')
        if reason:
            lines.append(f'   {reason}')
    lines.extend(['', f'Sina实时行情 | 共{len(picks)}只'])
    return '\n'.join(lines)

# ─── 主流程 ────────────────────────────────────────────────────────────

def main():
    now = datetime.datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M:%S')

    print(f'=== 强势股精选 v3 (Sina版) {date_str} {time_str} ===')

    print('\n[Step 1] 获取行情数据...')
    raw = fetch_sina(300)
    stocks = [parse_sina_row(r) for r in raw if r]
    valid = [s for s in stocks if s]
    print(f'  获取到 {len(valid)} 只有效股票')
    if len(valid) < 30:
        print('  ❌ 数据不足，跳过')
        return False

    print('\n[Step 2] 条件筛选...')
    candidates = filter_candidates(valid)
    print(f'  候选股票: {len(candidates)} 只')
    if len(candidates) < 5:
        print('  ❌ 候选不足，跳过')
        return False

    print('\n[Step 3] 精选 Top15...')
    picks = pick_top_stocks(candidates, limit=15)
    print(f'  精选结果: {len(picks)} 只')
    for i, s in enumerate(picks, 1):
        print(f'    {i:2d}. {s["name"]}({s["code"]}) 涨{s["chg"]:.1f}% 换手{s["turn"]:.1f}%')

    print('\n[Step 4] 保存 daily_picks.json...')
    new_records = save_daily_picks(picks)
    print(f'  ✅ 已保存 {len(new_records)} 条记录')
    print(f'  📁 {DAILY_PICKS}')

    print('\n[Step 5] 钉钉推送...')
    webhook = load_dingtalk_webhook()
    if webhook:
        msg = format_dingtalk(picks, date_str)
        if msg:
            push_dingtalk(msg, webhook)
    else:
        print('  ⚠️ 未配置钉钉 Webhook')

    print(f'\n✅ 完成! {len(picks)} 只强势股已更新')
    return True

if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
