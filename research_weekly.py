#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
投研信息筛选系统 - 周频版
================================
周六 09:00 → 采集模式（抓数据+过滤+存档，不推送）
周日 09:00 → 报告模式（生成周报+推钉钉+更新 dashboard）

日常轻量嗅探 → research_scanner.py（工作日 12:00/20:00）

用法:
  python research_weekly.py --mode policy    # 周六采集
  python research_weekly.py --mode report    # 周日报告
"""

import sys
import os
import json
import re
import time
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, date, timedelta
from pathlib import Path

# 多源新闻抓取（复用 news.html 已验证链路：东财98dou/财联社/同花顺/新浪/雪球）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_sources
from research_sources import fetch_all

def _fmt_ms(ms):
    """毫秒时间戳转显示字符串"""
    if not ms:
        return ''
    try:
        return datetime.fromtimestamp(ms / 1000).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return ''

# ── 路径配置 ──
BASE_DIR = Path(__file__).parent
WATCHLIST = BASE_DIR / 'watchlist.yaml'
EVENTS_DIR = BASE_DIR / 'memory' / 'events'
STOCKS_DIR = BASE_DIR / 'memory' / 'stocks'
RESEARCH_DATA = BASE_DIR / 'research_data.json'
DINGTALK_PY = BASE_DIR / 'dingtalk.py'
SYNC_SCRIPT = BASE_DIR / 'sync_research.py'

# ── 关键词配置 ──
HIGH_TRIGGER = [
    '出口管制', '断供', '制裁', '禁令', '国产替代',
    '技术突破', '扩产', '产能紧缺', '停产', '供应紧张'
]

MEDIUM_TRIGGER = [
    '产业基金', '政策支持', '订单增长', '原材料涨价',
    '进口依赖', '自主可控', '半导体', '芯片'
]

FILTER_WORDS = [
    '短期检修', '小幅波动', '机构调研', '业绩说明会',
    '临时停牌', '例行检查', '日常维护', '现金管理'
]

# 📌 新闻扫描宇宙 = 纯数据驱动（选股系统推荐股，由 load_recommendation_stocks 注入）
#    已移除硬编码 WATCHLIST_STOCKS / KEYWORD_MAP —— 自选股票池不再写死任何股票清单，
#    只命中 daily_picks.json 中的选股推荐股，做到零硬编码。

# ── 数据源 URL ──
EASTMONEY_NEWS = "https://push2he.eastmoney.com/api/qt/ulist.np/get"
SNOWBALL_HOT = "https://xueqiu.com/statuses/hot/listV2.json"
PPI_INDEX = "https://www.100ppi.com/mall/list.html"


# ══════════════════════════════════════════
#  第一部分：数据采集
# ══════════════════════════════════════════

def web_fetch(url, timeout=15):
    """通用 web_fetch 封装，返回文本或 None"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/json,*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  [web_fetch] {url[:60]}... ❌ {e}")
        return None


def fetch_eastmoney_news(keyword, days=7):
    """东方财富新闻搜索（按关键词）"""
    print(f"  [东财新闻] 搜索: {keyword}")
    # 用 push2he 的新闻接口
    since = (date.today() - timedelta(days=days)).strftime('%Y-%m-%d')
    params = {
        'secids': '',
        'fields': '',
        'type': '7',
        'page': '1',
        'pagesize': '20',
        'keyword': keyword,
        'sr': '-1',
    }
    url = EASTMONEY_NEWS + '?' + urllib.parse.urlencode(params)
    text = web_fetch(url)
    if not text:
        return []
    try:
        data = json.loads(text)
        items = data.get('data', {}).get('list', []) if isinstance(data, dict) else []
        results = []
        for item in items:
            results.append({
                'title': item.get('title', ''),
                'content': item.get('content', ''),
                'time': item.get('time', ''),
                'source': '东方财富',
                'url': item.get('url', ''),
            })
        print(f"    → 获取 {len(results)} 条")
        return results
    except json.JSONDecodeError:
        print(f"    → 解析失败")
        return []


def fetch_sina_news(keyword, days=7):
    """新浪财经新闻搜索"""
    print(f"  [新浪财经] 搜索: {keyword}")
    url = f"https://search.finance.sina.com.cn/finance/search?q={urllib.parse.quote(keyword)}&range=all&c=news&sort=time"
    text = web_fetch(url)
    if not text:
        return []
    # 简单提取标题
    titles = re.findall(r'<h3[^>]*>([^<]+)</h3>', text)
    results = []
    for t in titles[:10]:
        clean = re.sub(r'<[^>]+>', '', t).strip()
        if clean and len(clean) > 5:
            results.append({'title': clean, 'content': '', 'source': '新浪财经'})
    print(f"    → 获取 {len(results)} 条")
    return results


def fetch_7x24_alerts(days=7):
    """雪球7x24快讯（轻量嗅探）"""
    print("  [雪球7x24] 扫描一周快讯...")
    # 雪球7x24 bypass
    url = "https://xueqiu.com/v4/statuses/public_timeline_by_category.json?since_id=-1&max_id=-1&count=50&category=12"
    text = web_fetch(url)
    if not text:
        return []
    try:
        data = json.loads(text)
        items = data.get('list', [])
        results = []
        for item in items:
            title = item.get('text', '') or item.get('title', '')
            if title:
                # 清理HTML标签
                title = re.sub(r'<[^>]+>', '', title).strip()
                results.append({
                    'title': title[:120],
                    'content': title,
                    'source': '雪球7x24',
                    'time': item.get('created_at', ''),
                })
        print(f"    → 获取 {len(results)} 条")
        return results
    except (json.JSONDecodeError, KeyError) as e:
        print(f"    → 解析失败: {e}")
        return []


# ══════════════════════════════════════════
#  第二部分：关键词过滤 + 匹配
# ══════════════════════════════════════════

def is_noise(title, content):
    """判断是否为噪音"""
    text = (title + ' ' + content).lower()
    for w in FILTER_WORDS:
        if w in text:
            return True
    return False


def load_recommendation_stocks(path='vibe-dashboard/daily_picks.json', lookback_days=3):
    """从 daily_picks.json 读取近期选股系统推荐股票，作为新闻扫描扩展池（增强：P2 推荐股也参与新闻命中）"""
    p = BASE_DIR / path
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text('utf-8'))
    except Exception:
        return []
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    seen = set()
    out = []
    for date_key, tasks in data.items():
        if not isinstance(date_key, str) or date_key < cutoff:
            continue
        if not isinstance(tasks, dict):
            continue
        for task_name, tv in tasks.items():
            picks = tv.get('picks', []) if isinstance(tv, dict) else []
            for s in picks:
                c = s.get('code')
                n = s.get('name')
                if c and n and c not in seen:
                    seen.add(c)
                    out.append({'code': c, 'name': n, 'task_name': task_name})
    return out


def match_stocks(title, content, extra_stocks=None):
    """匹配自选股，返回匹配结果列表。
    extra_stocks: 额外股票池(如选股系统推荐)，用于扩大新闻命中范围（增强）"""
    text = title + ' ' + content
    matches = []
    if not extra_stocks:
        return matches
    hit_map = {}  # 去重: stock_name -> hit_reason
    # 纯数据驱动：只扫描选股系统推荐股（extra_stocks），无硬编码清单、无关键词表
    for s in extra_stocks:
        if s['name'] in text or s['code'] in text:
            if s['name'] not in hit_map:
                hit_map[s['name']] = f"直接提及[{s['name']}]（选股推荐）"
    for sn, reason in hit_map.items():
        stock_info = next((s for s in extra_stocks if s['name'] == sn), None)
        if stock_info:
            matches.append({
                'name': sn,
                'code': stock_info['code'],
                'match_reason': reason,
            })
    return matches


# 注: check_memory 已移除（依赖已删除的 WATCHLIST_STOCKS 记忆文件）


# ══════════════════════════════════════════
#  第三部分：事件归档
# ══════════════════════════════════════════

def archive_events(events):
    """写入 events 归档文件"""
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = date.today().strftime('%Y-%m-%d')
    evt_file = EVENTS_DIR / f"{today_str}.md"

    if not events:
        # 空存档：写个时间戳就行
        evt_file.write_text(
            f"# 投研事件归档 {today_str}\n\n"
            f"- 巡检时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"- 状态: 无事件\n", encoding='utf-8')
        return

    lines = [f"# 投研事件归档 {today_str}\n"]
    for evt in events:
        lines.append(f"\n## {evt.get('title', '未命名')}")
        lines.append(f"- 时间: {evt.get('time', '')}")
        lines.append(f"- 来源: {evt.get('source', '')}")
        lines.append(f"- 匹配: {', '.join(s['name'] for s in evt.get('stocks', []))}")
        lines.append(f"- 详请: {evt.get('content', '')[:200]}")
    evt_file.write_text('\n'.join(lines), encoding='utf-8')


# ══════════════════════════════════════════
#  第四部分：周报生成
# ══════════════════════════════════════════

def generate_report(events, mode='report'):
    """生成周报 Markdown"""
    today = date.today()
    week_num = today.isocalendar()[1]
    week_range = f"{today - timedelta(days=today.weekday()+1)}~{today}"

    alerts = [e for e in events if e.get('stocks') and e.get('verification', {}).get('memory')]
    archives = [e for e in events if e not in alerts]

    lines = []
    # ── 标题 ──
    lines.append(f"📊 投研周报 {today.year}-W{week_num} ({week_range})")
    lines.append("")

    # ── 一、重点预警 ──
    lines.append("━━━ 一、本周重点预警 ━━━")
    if alerts:
        for evt in alerts:
            lines.append(f"")
            lines.append(f"🔴 **{evt['title']}**")
            lines.append(f"  时间：{evt.get('time', '')}")
            lines.append(f"  来源：{evt.get('source', '')}")
            lines.append(f"  详情：{evt.get('content', '')[:100]}")
            for s in evt.get('stocks', []):
                mem = s.get('memory_match', '')
                tag = ' ✅' if mem else ' ⚠️ 无记忆佐证'
                lines.append(f"  📌 {s['name']}({s['code']}) {tag}")
                if mem:
                    lines.append(f"    记忆匹配：{mem}")
            lines.append(f"  校验状态：{'双重验证通过 ✅' if evt.get('verification', {}).get('memory') else '单次验证 ⚪'}")
    else:
        lines.append("  (无：无重大政策/公告涉及自选股)")
    lines.append("")

    # ── 二、政策动态 ──
    lines.append("━━━ 二、政策动态 ━━━")
    policy_events = [e for e in events if '政策' in e.get('source', '') or '部' in e.get('source', '')]
    if policy_events:
        for evt in policy_events:
            lines.append(f"  • {evt['title']}")
    else:
        lines.append("  (本周无重大政策发布)")
    lines.append("")

    # ── 三、产业跟踪 ──
    lines.append("━━━ 三、产业跟踪 ━━━")
    industry_events = [e for e in events if '产业' in e.get('source', '') or '行业' in e.get('source', '')]
    if industry_events:
        for evt in industry_events:
            lines.append(f"  • {evt['title']}")
    else:
        lines.append("  (无关键原材料价格异动或行业变化)")
    lines.append("")

    # ── 四、持仓/自选相关 ──
    lines.append("━━━ 四、持仓/自选相关 ━━━")
    stock_events = [e for e in events if e.get('stocks')]
    if stock_events:
        seen = set()
        for evt in stock_events:
            for s in evt.get('stocks', []):
                name = s['name']
                if name not in seen:
                    seen.add(name)
                    lines.append(f"  📌 {name}({s['code']}) — {evt['title'][:40]}")
    else:
        lines.append("  (无)")
    lines.append("")

    # ── 五、个股记忆建议更新 ──
    lines.append("━━━ 五、个股记忆建议更新 ━━━")
    if events:
        # 检查是否有新关键词需要记入记忆
        new_keywords = set()
        for evt in events:
            text = evt.get('title', '') + evt.get('content', '')
            for kw in HIGH_TRIGGER + MEDIUM_TRIGGER:
                if kw in text and kw not in new_keywords:
                    new_keywords.add(kw)
        if new_keywords:
            for kw in sorted(new_keywords):
                lines.append(f"  • 新增关注关键词: [{kw}] — 建议更新到个股记忆")
        else:
            lines.append("  (无)")
    else:
        lines.append("  (无)")
    lines.append("")

    lines.append("---")
    lines.append(f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

    return '\n'.join(lines)


# ══════════════════════════════════════════
#  第五部分：数据源采集聚合
# ══════════════════════════════════════════

def collect_week_data():
    """采集一周数据，返回事件列表"""
    print(f"\n{'='*50}")
    print(f"📡 投研数据采集 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    all_events = []

    # 多源抓取（复用 news.html 已验证链路：东财98dou/财联社/同花顺/新浪/雪球）
    print("▶ 多源新闻抓取（东财/财联社/同花顺/新浪/雪球）")
    for item in fetch_all():
        title = item.get('title', '')
        content = item.get('text', '')
        if not title:
            continue
        if is_noise(title, content):
            continue
        stocks = match_stocks(title, content, load_recommendation_stocks(lookback_days=7))
        if stocks:
            tstr = item.get('time_str') or _fmt_ms(item.get('time', 0))
            all_events.append({
                'time': tstr,
                'type': 'alert' if any(s.get('memory_match') for s in stocks) else 'archive',
                'title': title[:80],
                'content': content[:200],
                'source': item.get('source', ''),
                'source_url': item.get('url', ''),
                'stocks': stocks,
                'verification': {
                    'memory': any(s.get('memory_match') for s in stocks),
                    'source': True,
                }
            })

    # 去重
    seen_titles = set()
    deduped = []
    for e in all_events:
        key = e['title'][:40]
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(e)

    print(f"\n{'='*50}")
    print(f"📊 采集完成: {len(deduped)} 个事件 (去重前 {len(all_events)})")
    print(f"   重点预警: {sum(1 for e in deduped if e['type']=='alert')}")
    print(f"   静默存档: {sum(1 for e in deduped if e['type']=='archive')}")
    print(f"{'='*50}")

    return deduped


# ══════════════════════════════════════════
#  第六部分：更新 & 推送
# ══════════════════════════════════════════

def update_dashboard(events, mode):
    """更新 research_data.json"""
    stats = {
        'processed': len(events),
        'alerts': sum(1 for e in events if e['type'] == 'alert'),
        'filtered': sum(1 for e in events if e['type'] == 'archive'),
    }

    data = {
        'date': date.today().strftime('%Y-%m-%d'),
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'stats': stats,
        'events': events[:20],  # 只存最近的20条
        'keywords': {
            'high_priority': HIGH_TRIGGER,
            'medium_priority': MEDIUM_TRIGGER,
            'filter': FILTER_WORDS,
        },
        'mode': mode,
        'week': date.today().isocalendar()[1],
    }

    RESEARCH_DATA.parent.mkdir(parents=True, exist_ok=True)
    with open(RESEARCH_DATA, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n✅ research_data.json 已更新")


def push_dingtalk(report_text):
    """推送到钉钉"""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from dingtalk import send_dingtalk
        send_dingtalk(report_text)
        print("✅ 已推送到钉钉")
    except Exception as e:
        print(f"❌ 钉钉推送失败: {e}")
        # Fallback: 直接调用
        try:
            webhook = "https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"
            payload = json.dumps({
                "msgtype": "markdown",
                "markdown": {"title": "投研周报", "text": report_text}
            }).encode('utf-8')
            req = urllib.request.Request(webhook, data=payload, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=10)
            print("✅ 已推送到钉钉(fallback)")
        except Exception as e2:
            print(f"❌ 钉钉推送失败(fallback): {e2}")


def sync_to_github():
    """同步到 GitHub Pages"""
    print("\n▶ 同步到 GitHub...")
    try:
        import subprocess
        py = sys.executable
        r = subprocess.run([py, str(SYNC_SCRIPT)], capture_output=True, text=True, timeout=120)
        out = (r.stdout or r.stderr).strip()
        print(out)
        print("✅ GitHub 同步完成" if r.returncode == 0 else "⚠️  GitHub 同步可能失败，稍后检查")
    except Exception as e:
        print(f"❌ GitHub 同步异常: {e}")


# ══════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description='投研信息筛选系统 - 周频版')
    parser.add_argument('--mode', choices=['policy', 'report'], default='report',
                       help='policy=仅采集存档, report=采集+生成周报+推送')
    args = parser.parse_args()

    print(f"🔍 投研信息筛选系统 (周频版)")
    print(f"   模式: {args.mode}")
    print(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # 1. 采集一周数据
    events = collect_week_data()

    # 2. 存档到 events 目录
    archive_events(events)

    # 3. 更新 dashboard
    update_dashboard(events, args.mode)

    # 3b. 重新生成 research.html（动态股票池 + 事件列表）
    print("\n▶ 重新生成 research.html（动态股票池）...")
    try:
        gen_script = BASE_DIR / 'generate_research_html.py'
        if gen_script.exists():
            r = subprocess.run([sys.executable, str(gen_script)], capture_output=True, text=True, timeout=180)
            for line in (r.stdout or r.stderr).strip().split('\n'):
                if line.strip():
                    print(f"  {line}")
        else:
            print("  generate_research_html.py not found")
    except Exception as e:
        print(f"  research.html 生成失败: {e}")

    # 4. 如果是 report 模式，生成周报并推送
    if args.mode == 'report':
        print(f"\n{'='*50}")
        print(f"📝 生成周报...")
        print(f"{'='*50}")
        report = generate_report(events, mode='report')

        # 保存周报到文件
        week_num = date.today().isocalendar()[1]
        report_file = EVENTS_DIR / f'weekly_report_W{week_num}.md'
        report_file.write_text(report, encoding='utf-8')
        print(f"✅ 周报已保存: {report_file.name}")

        # 推送到钉钉
        print(f"\n{'='*50}")
        print(f"📨 推送周报...")
        print(f"{'='*50}")
        push_dingtalk(report)

        # 同步到 GitHub
        sync_to_github()

        print(f"\n{'='*50}")
        print(f"✅ 投研周报 W{week_num} 完成")
        print(f"{'='*50}")
    else:
        print(f"\n✅ 政策采集完成（未推送，等待周日 report 模式汇总）")

    return 0


if __name__ == '__main__':
    sys.exit(main())
