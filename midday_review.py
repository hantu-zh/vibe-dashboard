# -*- coding: utf-8 -*-
"""
midday_review.py - 交易日 11:30 午间收盘后生成「午间点评」

复用 market_review.py 的全部数据抓取与点评生成逻辑（指数/板块/涨停/涨跌家数/要闻），
仅在以下方面做午间适配：
  - period 固定为 '午间'（页面/钉钉标题显示为【午间点评】）
  - 额外把点评推送到钉钉（收盘版 market_review.py 不发钉钉）
  - 输出文件为 midday_review.json（与收盘版 market_review.json 互不覆盖）
  - 盘中快讯从 news_update.py 产出的 news_data.json 取（即用户要求的「news 数据源」）

cffex.html 已新增【午间点评】模块，运行时 fetch('midday_review.json') 渲染；
本脚本只负责产出 midday_review.json + 推钉钉，sync_func 负责把 json 推到 GitHub Pages。

数据源（全部带降级，任一失败不影响其它板块）：
  - 八大指数行情：新浪 hq.sinajs.cn（主） -> 腾讯 qt.gtimg.cn（备）
  - 行业板块涨幅 / 主力净流入：东财 push2delay（主） -> push2（备） -> 新浪（末）
  - 涨停 / 跌停：akshare stock_zt_pool_em / stock_zt_pool_dtgc_em
  - 全市场涨跌家数：东财 clist 的 f104/f105/f106（上证+深证求和）
  - 盘中快讯：复用 news_update.py 产出的 news_data.json（缺失则跳过）
"""
import sys
import os
import json
import urllib.request
from datetime import datetime

import paths
import secrets_conf
from secrets_conf import DINGTALK_WEBHOOK

# 复用收盘点评脚本的成熟抓取与生成逻辑（该模块导入无副作用）
import market_review as mr

sys.stdout.reconfigure(encoding='utf-8')

# 输出路径：仓库根目录（GitHub Pages 根发布，勿写 vibe-dashboard/ 子目录）
OUT_FILE = paths.w('midday_review.json')
WEEKDAY_CN = mr.WEEKDAY_CN


def send_dingtalk(text, title):
    """推送 Markdown 消息到钉钉。未配置 token 时静默跳过。"""
    if not DINGTALK_WEBHOOK:
        print('[dingtalk] 未配置 DINGTALK_TOKEN，跳过推送')
        return
    payload = {
        'msgtype': 'markdown',
        'markdown': {'title': title, 'text': text},
    }
    req = urllib.request.Request(
        DINGTALK_WEBHOOK,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode('utf-8'))
        if resp.get('errcode') == 0:
            print('[dingtalk] ✅ 午间点评推送成功')
        else:
            print(f'[dingtalk] ⚠️ 推送返回异常: {resp}')
    except Exception as e:
        print(f'[dingtalk] ❌ 推送失败: {e}')


def main():
    now = datetime.now()
    print(f'\n[midday_review] ===== {now:%Y-%m-%d %H:%M:%S} =====')

    if not mr.is_trading_day(now):
        print('[skip] 非交易日，不更新午间点评（保留上一交易日内容）')
        return 0

    idx = mr.fetch_indices()
    if len(idx) < 5:
        print('[abort] 指数行情获取失败，为保护现有数据，本次不写入')
        return 1

    date_str = now.strftime('%Y-%m-%d')
    date_compact = now.strftime('%Y%m%d')
    date_cn = f'{now.year}年{now.month:02d}月{now.day:02d}日 {WEEKDAY_CN[now.weekday()]}'
    time_str = now.strftime('%H:%M')

    gain, inflow, outflow, total_main = mr.fetch_sectors()
    zt, dt, names = mr.fetch_limit_pool(date_compact)
    breadth = mr.fetch_breadth()
    news = mr.fetch_news(limit=8)  # 盘中快讯：news 数据源，取前 8 条

    period = '午间'
    text = mr.build_text(date_cn, period, time_str, idx, gain, inflow,
                         outflow, total_main, zt, dt, names, breadth, news)
    # 文案午间化
    text = text.replace('# 📊 A股收盘数据研报', '# 📊 A股午间收盘点评', 1)
    text = text.replace('## 5️⃣ 政策要闻', '## 5️⃣ 盘中快讯', 1)
    text = text.replace(
        '数据来源：东方财富 / 新浪财经 / 腾讯证券',
        '数据来源：东方财富 / 新浪财经 / 腾讯证券 / 财联社 / 华尔街见闻', 1)

    sh_chg = idx.get('上证指数', {}).get('chg', 0)
    if sh_chg > 0.3:
        rec = '上证%+.2f%%，市场情绪偏暖，结构性机会活跃，可关注当日强势方向。' % sh_chg
    elif sh_chg < -0.3:
        rec = '上证%+.2f%%，市场情绪偏弱，控制仓位为主，等待企稳信号。' % sh_chg
    else:
        rec = '上证%+.2f%%，市场窄幅震荡，多看少动，等待方向明朗。' % sh_chg

    data = {
        # ── 页面 renderMidday() 读取的顶层字段（与 renderReview 一致）──
        'date': date_str,
        'time': time_str,
        'period': period,
        'indices': {n: {'price': d['price'], 'chg': d['chg']} for n, d in idx.items()},
        'sectors': [{'name': g['name'], 'chg': g['chg']} for g in gain],
        'money': {'total_main_yi': total_main},
        'limit_count': zt,
        'outlook': {'recommendation': rec},
        # ── 兼容结构 ──
        'text': text,
        'dims': {
            'macro': {'indices': [
                {'name': n, 'price': d['price'], 'chg': d['chg'],
                 'amount_yi': d.get('amount_yi'), 'pe': None, 'pb': None}
                for n, d in idx.items()
            ]},
            'sectors': {'top_gain': gain},
            'money': {
                'inflow': inflow, 'outflow': outflow,
                'breadth': breadth,
                'total_main_yi': total_main,
                'limit_up': zt, 'limit_down': dt,
                'up': breadth.get('up', 0), 'down': breadth.get('down', 0),
                'flat': breadth.get('flat', 0),
                'top_up_names': names, 'top_dn_names': [],
            },
            'limit': {'zt': zt, 'dt': dt, 'names': names},
            'policy': {'items': news},
            'strategy': {
                'mood': rec,
                'main_line': [g['name'] for g in gain[:3]],
                'strategy': rec,
                'sh_chg': sh_chg,
                'sh_price': idx.get('上证指数', {}).get('price'),
            },
        },
        'updated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
    }

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f'[ok] 已写入 {OUT_FILE}')
    print(f'     【{period}点评】{date_str} {time_str}｜涨停{zt} 跌停{dt}｜'
          f'板块{len(gain)} 主力{total_main:+.1f}亿｜快讯{len(news)}条')

    # 推送钉钉（核心需求：午评发到钉钉）
    send_dingtalk(text, f'A股午间收盘点评 {date_str} {time_str}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
