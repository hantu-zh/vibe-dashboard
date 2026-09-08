# -*- coding: utf-8 -*-
"""
speedrank.py - 生成 speedrank_history.json（升速排行快照），并刷新 speedrank.html 内嵌 _embed

背景（为什么要新建这个脚本）：
  speedrank.html 是纯前端页：内嵌 _embed（最新快照）+ 运行时拉 speedrank_history.json。
  原 Qclaw 时代由 speedrank_update.py 每 5 分钟产出，迁移到 GitHub Actions 时该脚本
  没有一起带来，于是 speedrank_history.json 永远停在 2026-08-20，页面显示死数据。
  本脚本补上缺失的生成环节，让升速榜每个交易日自动刷新。

数据源（带降级）：
  - 涨速 Top20：东财 push2delay（主） -> push2（备），按 f11（实时涨速%）排序
  - 筛选式 fs=m:0+t:6+f:!2,m:1+t:2+f:!2 精确取「沪深A股」（沪主板+深主板+创业板+科创板），
    排除北交所(8xxxxx)/新三板(4xxxxx)/期权(购/沽)/ST，避免噪音淹没榜单

输出：
  - speedrank_history.json：{日期: {snapshots: [{time, items}]}}，每日追加快照（同分钟不重复）
  - speedrank.html：将最新快照写入 var _embed = {...}（页面 fetch 失败时的兜底）

安全策略：
  - 仅在交易日 09:30-15:05 抓取；非交易时段/非交易日直接退出，不写空快照（避免污染页面）
  - 抓取不足 10 条时不追加（视为数据源异常），保留上一交易日数据
  - 每个交易日快照上限 30，历史保留最近 40 个交易日（控制文件体积）
"""
import sys
import os
import json
import ssl
import time
import urllib.request
import urllib.parse
import re
from datetime import datetime, date

import paths

sys.stdout.reconfigure(encoding='utf-8')

HISTORY_FILE = paths.w('speedrank_history.json')
HTML_FILE = paths.w('speedrank.html')

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 沪深A股筛选式：沪主板+深主板+创业板+科创板，排除北交所/新三板/期权
FS = 'm:0+t:6+f:!2,m:1+t:2+f:!2'
# 需要取的字段：f12代码 f14名称 f2现价 f3涨跌幅 f11涨速 f8换手率 f10量比 f6成交额(元)
FIELDS = 'f12,f14,f2,f3,f11,f8,f10,f6'
PZ = 30          # 多抓一些，过滤 ST 后取前 20
TOP_N = 20
MAX_SNAP_PER_DAY = 30
MAX_HISTORY_DAYS = 40


def http_get(url, timeout=20, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA,
                                                        'Referer': 'https://quote.eastmoney.com/'})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode('utf-8', errors='ignore')
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


def is_trading_day(d):
    """是否 A 股交易日：优先 akshare 交易日历，失败回退周末判断"""
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = {str(x) for x in df['trade_date'].tolist()}
        return d.strftime('%Y-%m-%d') in days
    except Exception:
        return d.weekday() < 5


def in_trading_hours(now=None):
    """上海时间 09:30-15:05 视为交易时段"""
    now = now or datetime.now()
    total = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= total <= 15 * 60 + 5


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def fetch_speedrank():
    """抓取沪深A股涨速 Top20。返回 {time, items:[...]} 或 None（失败时）"""
    bases = ['https://push2delay.eastmoney.com/api/qt/clist/get',
             'https://push2.eastmoney.com/api/qt/clist/get']
    for base in bases:
        try:
            url = (base + '?pn=1&pz=' + str(PZ) + '&po=1&np=1'
                   '&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2'
                   '&fid=f11&fs=' + urllib.parse.quote(FS) + '&fields=' + FIELDS)
            data = json.loads(http_get(url))
            rows = (data.get('data') or {}).get('diff') or []
            if not rows:
                continue
            items = []
            for r in rows:
                code = str(r.get('f12') or '')
                name = r.get('f14') or ''
                if not code or not name:
                    continue
                if 'ST' in name or '*ST' in name:
                    continue
                if any(k in name for k in ('购', '沽', 'ETF', '债', '基金')):
                    continue
                chg = _fnum(r.get('f3'))
                items.append({
                    'code': code,
                    'name': name,
                    'price': round(_fnum(r.get('f2')), 2),
                    'chg_pct': round(chg, 2),
                    'turnover': round(_fnum(r.get('f8')), 2),
                    'vol_ratio': round(_fnum(r.get('f10')), 2),
                    'amount_yi': round(_fnum(r.get('f6')) / 1e8, 2),
                    'speed': round(_fnum(r.get('f11')), 2),  # 东财 f11 = 实时涨速(%)
                })
            if len(items) >= TOP_N:
                print(f'[speedrank] 东财获取 {len(items)} 条（{base.split("/")[-2]}）')
                return {'time': datetime.now().strftime('%H:%M'),
                        'items': items[:TOP_N]}
        except Exception as e:
            print(f'[speedrank] 东财接口异常({base}): {type(e).__name__}')
    return None


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            return json.load(open(HISTORY_FILE, 'r', encoding='utf-8'))
        except Exception as e:
            print(f'[speedrank] 读取历史失败: {e}')
    return {}


def save_history(history):
    # 控制体积：每个交易日快照上限，历史保留最近 N 天
    for day in history:
        snaps = history[day].get('snapshots', [])
        if len(snaps) > MAX_SNAP_PER_DAY:
            history[day]['snapshots'] = snaps[-MAX_SNAP_PER_DAY:]
    days = sorted(history.keys())
    if len(days) > MAX_HISTORY_DAYS:
        for old in days[:len(days) - MAX_HISTORY_DAYS]:
            del history[old]
    json.dump(history, open(HISTORY_FILE, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


def update_html_embed(embed):
    if not os.path.exists(HTML_FILE):
        print('[speedrank] HTML 文件不存在，跳过 _embed 更新')
        return
    html = open(HTML_FILE, 'r', encoding='utf-8').read()
    embed_str = json.dumps(embed, ensure_ascii=False)
    new_html = re.sub(
        r'var _embed\s*=\s*\{.*?\};',
        'var _embed = ' + embed_str + ';',
        html, count=1, flags=re.DOTALL)
    if new_html != html:
        open(HTML_FILE, 'w', encoding='utf-8').write(new_html)
        print('[speedrank] speedrank.html _embed 已更新')
    else:
        print('[speedrank] [WARN] _embed 替换未命中，检查 speedrank.html 结构')


def main():
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    print(f'\n[speedrank] ===== 升速快照 {today} {now.strftime("%H:%M")} =====')

    if not is_trading_day(now.date()):
        print('[speedrank] 非交易日，跳过')
        return
    if not in_trading_hours(now):
        print('[speedrank] 非交易时段（仅 09:30-15:05 抓取），跳过')
        return

    embed = fetch_speedrank()
    if not embed or len(embed['items']) < 10:
        print('[speedrank] 抓取不足 10 条，视为数据源异常，保留既有数据，本次不追加')
        return

    print(f'[speedrank] 抓取完成: {len(embed["items"])} 只，'
          f'涨速Top1={embed["items"][0]["name"]}({embed["items"][0]["speed"]}%)')

    history = load_history()
    if today not in history:
        history[today] = {'snapshots': []}
    existing = {s['time'] for s in history[today].get('snapshots', [])}
    if embed['time'] not in existing:
        history[today]['snapshots'].append(embed)
        print(f'[speedrank] 快照追加 {today} {embed["time"]}，今日共 '
              f'{len(history[today]["snapshots"])} 个')
    else:
        print(f'[speedrank] 同分钟快照已存在，跳过 {embed["time"]}')

    save_history(history)
    update_html_embed(embed)

    top3 = [f'{i["name"]}({i["speed"]}%)' for i in embed['items'][:3]]
    print(f'[speedrank] 完成 | Top3: {top3}')
    print(f'[speedrank] ===== END =====\n')


if __name__ == '__main__':
    main()
