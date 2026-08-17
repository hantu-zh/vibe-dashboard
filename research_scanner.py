#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投研信息筛选系统 - 日常轻量嗅探
==================================
工作日 12:00 / 20:00 各跑一次
抓取多源新闻(news.html 同款链路) -> 命中自选股/关键词 -> 推钉钉 + 更新 research_data.json

用法:
  python research_scanner.py         # 单次扫描
"""
import sys
import os
import json
import subprocess
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

import research_sources
from research_sources import fetch_all
from research_weekly import match_stocks, is_noise, load_recommendation_stocks

# 脚本位于 vibe-dashboard 内，直接使用 BASE_DIR
RESEARCH_DATA = BASE_DIR / 'research_data.json'
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"


def _fmt_ms(ms):
    if not ms:
        return ''
    try:
        return datetime.fromtimestamp(ms / 1000).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''


def scan_items(items, extra_stocks=None):
    """扫描命中自选股/关键词的条目（extra_stocks: 选股系统推荐扩展池）"""
    alerts = []
    for item in items:
        title = item.get('title', '')
        content = item.get('text', '')
        if not title:
            continue
        if is_noise(title, content):
            continue
        stocks = match_stocks(title, content, extra_stocks)
        if stocks:
            alerts.append({
                'time': item.get('time_str') or _fmt_ms(item.get('time', 0)),
                'title': title[:120],
                'content': content[:200],
                'source': item.get('source', ''),
                'source_url': item.get('url', ''),
                'stocks': stocks,
            })
    return alerts


def update_dashboard(alerts):
    """合并新事件到 research_data.json（去重, 保留最近20条）"""
    data = {}
    if RESEARCH_DATA.exists():
        try:
            data = json.loads(RESEARCH_DATA.read_text('utf-8'))
        except Exception:
            data = {}
    events = data.get('events', [])
    seen = {e.get('title', '')[:40] for e in events}
    added = 0
    for a in alerts:
        key = a['title'][:40]
        if key in seen:
            continue
        seen.add(key)
        mem = any(s.get('memory_match') for s in a['stocks'])
        events.append({
            'time': a['time'],
            'type': 'alert' if mem else 'archive',
            'title': a['title'][:80],
            'content': a['content'],
            'source': a['source'],
            'source_url': a['source_url'],
            'stocks': a['stocks'],
            'verification': {'memory': mem, 'source': True},
        })
        added += 1
    # 丢弃早于 (今天-5天) 的陈旧事件，避免追踪器长期显示旧闻
    cutoff = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    events = [e for e in events if (e.get('time', '')[:10] or '0000-00-00') >= cutoff]
    # 按时间倒序保留最近 20 条（修复：原 events[:20] 保留的是存储顺序前 20 条，新事件被截断丢失）
    events.sort(key=lambda e: e.get('time', ''), reverse=True)
    events = events[:20]
    stats = data.get('stats', {})
    data.update({
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'stats': {
            'processed': stats.get('processed', 0) + len(alerts),
            'alerts': sum(1 for e in events if e['type'] == 'alert'),
            'filtered': sum(1 for e in events if e['type'] == 'archive'),
        },
        'events': events,
        'mode': 'scan',
        'week': datetime.now().isocalendar()[1],
    })
    RESEARCH_DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return added


def push_dingtalk(text):
    try:
        payload = json.dumps({"msgtype": "markdown", "markdown": {"title": "盘中提醒", "text": text}}).encode('utf-8')
        req = urllib.request.Request(DINGTALK_WEBHOOK, data=payload, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=10)
        print("✅ 已推送钉钉")
    except Exception as e:
        print(f"❌ 推送失败: {e}")


def regenerate_research_html():
    """重新生成 research.html（动态股票池 + 事件列表）"""
    try:
        gen_script = BASE_DIR / 'generate_research_html.py'
        if gen_script.exists():
            r = subprocess.run([sys.executable, str(gen_script)], capture_output=True, encoding='utf-8', errors='replace', timeout=60)
            output = (r.stdout or '').strip() or (r.stderr or '').strip()
            for line in output.split('\n'):
                if line.strip():
                    print(f"  {line}")
        else:
            print("generate_research_html.py not found")
    except Exception as e:
        print(f"research.html 生成失败: {e}")


def main():
    now = datetime.now()
    print(f"🔎 盘中轻量嗅探 {now.strftime('%Y-%m-%d %H:%M')}")

    items = fetch_all(include_xueqiu=False)
    print(f"  扫描 {len(items)} 条快讯...")
    # 增强: 将选股系统推荐股纳入新闻扫描宇宙，使 P2 推荐股也能命中新闻事件
    rec_stocks = load_recommendation_stocks(lookback_days=3)
    print(f"  扩展扫描池(选股推荐): {len(rec_stocks)} 只")
    alerts = scan_items(items, extra_stocks=rec_stocks)

    if alerts:
        lines = [f"📡 盘中快讯扫描 ({now.strftime('%H:%M')})", ""]
        for a in alerts:
            names = ",".join(s['name'] for s in a['stocks'])
            lines.append(f"- ⚠️ 【{names}】{a['title'][:80]}")
            print(f"  [{a['source']}] {names}: {a['title'][:60]}")
        push_dingtalk('\n'.join(lines))
        added = update_dashboard(alerts)
        print(f"  写入 research_data.json 新增 {added} 条")
    else:
        print("  无涉及自选股事件，静默跳过")

    # 每次扫描都重新生成 research.html（即使无新事件，也同步最新选股推荐数据）
    print("  重新生成 research.html（动态股票池）...")
    regenerate_research_html()
    # GitHub 同步由 news_sync cron（每5分钟）统一处理，这里不再单独同步


if __name__ == '__main__':
    main()
