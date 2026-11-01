#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
briefing_push.py — A股简报（快讯 + 投研）定时推送到钉钉
=====================================================
由 .github/workflows/briefing_push.yml 在每天 09:00 / 12:00 / 16:00（北京时间，
周一至周五，跳过法定假日）调度：

  1. 读取已同步的 news_data.json（news_update.py 每15分钟刷新）与 research_data.json
     （research_scanner.py 12:00/20:00 嗅探 + generate_research_html.py 渲染）；
  2. 把两者浓缩成「一份合并简报」：📰 快讯精选 + 🔬 投研动态 + 页面链接；
  3. 经仓库已有的 dingtalk.send_dingtalk 推送到钉钉群。

设计取舍（与用户确认）：
  - 渠道：钉钉（复用 DINGTALK_WEBHOOK secret + send_dingtalk，零新增配置）；
  - 形式：每个时点只发 1 条合并消息，不刷屏；
  - 时效：用「已同步的最新快照」直接生成，不重复抓取、不触发额外推送；
  - 推送日：仅交易日（周末 + 法定假日跳过；FORCE=1 可绕过，便于手动测试）。

用法:
  python briefing_push.py                 # 自动按当前北京时间判定时段(早盘/午间/收盘)
  python briefing_push.py --slot morning   # 指定时段
  python briefing_push.py --dry-run       # 只打印简报，不推送（本地调试 / 验证文案）
"""
import os
import sys
import json
import datetime
import argparse

sys.stdout.reconfigure(encoding='utf-8')

# 复用仓库已有的路径 / 钉钉推送链路（脚本与这些模块同在仓库根目录，sys.path[0] 已含）
import paths                                  # noqa: E402
import secrets_conf                          # noqa: E402
from dingtalk import send_dingtalk            # noqa: E402

# 直接以脚本所在目录定位数据文件，避免 paths.VIBE_DIR 在本地/远端解析差异
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NEWS_JSON = os.path.join(BASE_DIR, 'news_data.json')
RESEARCH_JSON = os.path.join(BASE_DIR, 'research_data.json')

PAGE_NEWS = 'https://hantu-zh.github.io/vibe-dashboard/news.html'
PAGE_RESEARCH = 'https://hantu-zh.github.io/vibe-dashboard/research.html'

SLOT_LABELS = {
    'morning': ('早盘', '09:00'),
    'midday':  ('午间', '12:00'),
    'close':   ('收盘', '16:00'),
}


def beijing_now():
    """返回 (UTC+8 当前时间, 当前小时)"""
    utc = datetime.datetime.now(datetime.timezone.utc)
    bj = utc.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
    return bj, bj.hour


def detect_slot():
    _, hour = beijing_now()
    if 8 <= hour <= 10:
        return 'morning'
    if 11 <= hour <= 13:
        return 'midday'
    if 15 <= hour <= 17:
        return 'close'
    # 兜底：其余时段（如手动触发）默认收盘口径
    return 'close'


def is_trading_day(today=None):
    """交易日判定：周末或法定节假日 -> False。FORCE=1 时强制 True。"""
    if os.environ.get('FORCE', '').strip() == '1':
        return True
    if today is None:
        today = datetime.date.today()
    if today.weekday() >= 5:          # 周六/周日
        return False
    try:
        from chinese_calendar import is_holiday
        if is_holiday(today):
            return False
    except Exception:
        # 缺失依赖时退化为「仅看周末」，不阻断推送
        pass
    return True


def fmt_time(ms, time_str=''):
    if time_str:
        s = time_str.strip()
        if len(s) >= 16:
            return s[11:16]
    try:
        if ms:
            dt = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
            dt = dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
            return dt.strftime('%H:%M')
    except Exception:
        pass
    return ''


def build_news_section(items, limit=6):
    if not items:
        return '> （暂无快讯数据）'
    rank = {'hot': 0, 'important': 1}
    def keyf(it):
        return (rank.get(it.get('importance', ''), 2), -(it.get('time', 0) or 0))
    items = sorted(items, key=keyf)[:limit]
    lines = []
    for it in items:
        imp = it.get('importance', '')
        mark = '🔥' if imp == 'hot' else ('⭐' if imp == 'important' else '•')
        title = (it.get('title') or it.get('text') or '').strip().replace('\n', ' ')
        if len(title) > 60:
            title = title[:60] + '…'
        src = it.get('source', '')
        t = fmt_time(it.get('time', 0), it.get('time_str', ''))
        meta = ' · '.join(x for x in [src, t] if x)
        lines.append(f'> {mark} **{title}**')
        if meta:
            lines.append(f'> _{meta}_')
    return '\n'.join(lines)


def build_research_section(data, limit_watch=6, limit_alert=3):
    if not data:
        return '> （暂无投研数据）'
    stats = data.get('stats', {}) or {}
    alerts = stats.get('alerts', 0)
    filtered = stats.get('filtered', 0)
    events = data.get('events', []) or []
    watch = data.get('watchlist', []) or []
    alert_evts = [e for e in events if e.get('type') == 'alert'][:limit_alert]

    lines = []
    lines.append(f'> 🔴 重大预警 **{alerts}** · 存档 **{filtered}**')
    if alert_evts:
        for e in alert_evts:
            t = (e.get('title') or '').strip().replace('\n', ' ')
            if len(t) > 50:
                t = t[:50] + '…'
            names = ','.join(s.get('name', '') for s in (e.get('stocks') or []) if s.get('name'))[:30]
            extra = f' · {names}' if names else ''
            lines.append(f'> 🚨 **{t}**{extra}')
    else:
        lines.append('> （近期无重大预警事件）')
    if watch:
        lines.append('> ')
        lines.append(f'> **动态股票池（Top {min(limit_watch, len(watch))}）**')
        for s in watch[:limit_watch]:
            name = s.get('name', '?')
            code = s.get('code', '')
            kw = (s.get('keywords') or '').strip()
            tag = '🆕' if s.get('is_new') else '🔁'
            line = f'> {tag} {name}({code})'
            if kw:
                line = line + f' · {kw}'
            lines.append(line)
    return '\n'.join(lines)


def build_briefing(slot):
    label, clock = SLOT_LABELS.get(slot, ('收盘', '16:00'))
    now_bj, _ = beijing_now()
    date_cn = now_bj.strftime('%Y-%m-%d %H:%M')
    title = f'📋 A股简报 · {label} {clock}'

    news_items = []
    news_total = 0
    try:
        with open(NEWS_JSON, encoding='utf-8') as f:
            nd = json.load(f)
        news_items = nd.get('items', []) or []
        news_total = nd.get('total', len(news_items))
    except Exception as e:
        print(f'[briefing] 读取 news_data.json 失败: {e}')

    rd = {}
    try:
        with open(RESEARCH_JSON, encoding='utf-8') as f:
            rd = json.load(f)
    except Exception as e:
        print(f'[briefing] 读取 research_data.json 失败: {e}')

    news_section = build_news_section(news_items)
    research_section = build_research_section(rd)

    parts = []
    parts.append(f'### {title}')
    parts.append(f'> 时段：**{label}** · 生成于 {date_cn}（北京时间）')
    parts.append(f'> 快讯池：{news_total} 条 · 来源：已同步最新快照（每15分钟刷新）')
    parts.append('')
    parts.append('**📰 快讯精选**')
    parts.append(news_section)
    parts.append('')
    parts.append('**🔬 投研动态**')
    parts.append(research_section)
    parts.append('')
    parts.append(f'> 📎 完整版：[快讯]({PAGE_NEWS}) · [投研]({PAGE_RESEARCH})')
    parts.append('> _数据来源：东方财富 / 财联社 / 新浪 / 同花顺 / 雪球 / 华尔街见闻_')
    text = '\n'.join(parts)
    if len(text) > 18000:
        text = text[:17500] + '\n\n--- 内容过长已截断 ---'
    return title, text


def main():
    ap = argparse.ArgumentParser(description='A股简报推送（钉钉）')
    ap.add_argument('--slot', choices=['auto', 'morning', 'midday', 'close'], default='auto')
    ap.add_argument('--dry-run', action='store_true', help='只打印，不推送')
    args = ap.parse_args()

    slot = args.slot if args.slot != 'auto' else detect_slot()
    slot = os.environ.get('SLOT') or slot

    today = datetime.date.today()
    if not is_trading_day(today):
        print(f'[briefing] {today} 非交易日，跳过推送')
        return 0

    title, text = build_briefing(slot)
    print('=' * 50)
    print(title)
    print('=' * 50)
    print(text)
    print('=' * 50)

    if args.dry_run:
        print('[briefing] --dry-run：未推送')
        return 0

    ok = send_dingtalk(title, text)
    if ok:
        print('[briefing] ✅ 推送成功')
    else:
        print('[briefing] ⚠️ 推送失败/跳过（未配置 DINGTALK_WEBHOOK 或网络异常）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
