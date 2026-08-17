# -*- coding: utf-8 -*-
"""
generate_research_html.py
=========================
从 research_data.json + daily_picks.json 动态生成 research.html

数据源优先级（生成动态股票池）：
  1. research_events — 本周新闻事件中高频提及的股票（权重最高）
  2. daily_picks     — 当日选股系统推荐（gaoxin / jack_captain / popeye）
  3. sector_leaders  — RPS 热榜前三板块的代表性股票

每周六 policy 扫描后执行，周内有新事件时也更新。

用法:
  python generate_research_html.py
"""
import sys, os, json, re
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import Counter

def fetch_qt_prices(codes):
    """从腾讯行情获取收盘价、涨跌幅、换手率. 返回 dict: {code: {price, close, chg_pct, turnover}}"""
    if not codes:
        return {}
    import urllib.request
    # sh6xxxxx, sz0xxxxx, sz3xxxxx, bjxxxxxx
    em_codes = []
    for c in codes:
        if c.startswith('6'):
            em_codes.append('sh' + c)
        elif c.startswith(('0', '3', '2')):
            em_codes.append('sz' + c)
        else:
            em_codes.append('sh' + c)
    url = 'https://qt.gtimg.cn/q=' + ','.join(em_codes)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        r = urllib.request.urlopen(req, timeout=8)
        data = r.read().decode('gbk', errors='ignore')
    except Exception as e:
        print(f'  [fetch_qt_prices] FAIL: {e}')
        return {}
    result = {}
    for line in data.strip().split('\n'):
        if '=' not in line:
            continue
        m = re.match(r'v_([a-z]{2}\d+)="([^"]*)"', line)
        if not m:
            continue
        em, payload = m.group(1), m.group(2)
        if not payload:
            continue
        f = payload.split('~')
        if len(f) > 3 and f[3]:
            code6 = em[2:]  # sh603986 -> 603986
            try:
                price = float(f[3])
                close = float(f[4]) if len(f) > 4 and f[4] else 0.0
                chg_pct = float(f[32]) if len(f) > 32 and f[32] else 0.0
            except ValueError:
                continue
            turnover = float(f[38]) if len(f) > 38 and f[38] else 0.0
            result[code6] = {'price': f[3], 'close': close, 'chg_pct': chg_pct, 'turnover': turnover}
    print(f'  [fetch_qt_prices] got {len(result)}/{len(codes)} prices')
    return result



sys.stdout.reconfigure(encoding='utf-8')
BASE_DIR = Path(__file__).parent

RESEARCH_DATA_JSON = BASE_DIR / 'research_data.json'
DAILY_PICKS_JSON  = BASE_DIR / 'daily_picks.json'
SECTOR_RANK_JSON  = BASE_DIR / 'daily_picks.json'   # 复用
OUTPUT_HTML       = BASE_DIR / 'research.html'

# ── 选股系统关键字映射（从 daily_picks 中识别）──
# 注: 底仓(CORE_STOCKS)已移除 —— 自选股票池不再硬编码任何股票，纯数据驱动(新闻事件 + 选股推荐)，见 build_dynamic_watchlist
PICK_SYSTEMS = {
    '高欣-季度环比增长': {'tag': '高欣', 'tag_color': '#00e5ff'},
    '杰克船长':          {'tag': '杰克',  'tag_color': '#ff6d00'},
    '大力水手菠菜涨停战法': {'tag': '菠菜',  'tag_color': '#69f0ae'},
    '市场深度解读':        {'tag': '深度',  'tag_color': '#ea80fc'},
}

# tag -> color 反查表（source 存的是 tag 如'杰克'/'菠菜'）
TAG_COLORS = {v['tag']: v['tag_color'] for v in PICK_SYSTEMS.values()}



def load_research_events():
    """读取 research_data.json 中的事件，提取股票出现频率"""
    if not RESEARCH_DATA_JSON.exists():
        return [], {}, {}

    with open(RESEARCH_DATA_JSON, encoding='utf-8') as f:
        data = json.load(f)

    events = data.get('events', [])
    meta = {
        'date': data.get('date', ''),
        'updated_at': data.get('updated_at', ''),
        'stats': data.get('stats', {}),
    }
    # 每只股票首次入选时间（跨运行持久化），用于「首次白色 / 重复蓝色」标记
    stock_meta = data.get('stock_meta', {})

    # 统计每只股票的提及次数和权重
    stock_counter = Counter()
    stock_detail = {}  # code -> {name, reason, alert_count, last_seen}

    for evt in events:
        is_alert = evt.get('type') == 'alert'
        t = evt.get('time', '')[:10]  # date only
        for s in evt.get('stocks', []):
            key = s.get('code', '')
            name = s.get('name', '')
            reason = s.get('match_reason', '')
            if not key:
                continue
            # 权重: alert 事件 ×2，mention ×1
            weight = 3 if is_alert else 1
            stock_counter[key] += weight
            if key not in stock_detail:
                stock_detail[key] = {'name': name, 'alerts': 0, 'reasons': [], 'last_seen': ''}
            stock_detail[key]['name'] = name
            stock_detail[key]['last_seen'] = t
            if is_alert:
                stock_detail[key]['alerts'] += 1
            if reason and reason not in stock_detail[key]['reasons']:
                stock_detail[key]['reasons'].append(reason)

    return events, meta, stock_meta


def load_daily_picks(lookback_days=3):
    """从 daily_picks.json 读取最近 N 天的推荐股票"""
    if not DAILY_PICKS_JSON.exists():
        return []

    with open(DAILY_PICKS_JSON, encoding='utf-8') as f:
        picks = json.load(f)

    stocks = []
    today = date.today()
    seen_codes = set()

    for i in range(lookback_days):
        d = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        if d not in picks:
            continue
        day_data = picks[d]
        for task_name, task_data in day_data.items():
            if not isinstance(task_data, dict):
                continue
            picks_list = task_data.get('picks', [])
            sys_info = PICK_SYSTEMS.get(task_name, {'tag': task_name[:3], 'tag_color': '#aaa'})
            for s in picks_list:
                code = s.get('code', '')
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)
                # 构造关键词
                tags = []
                if s.get('limit_score', 0) > 0:
                    tags.append('涨停')
                if s.get('chan_buy'):
                    tags.append('缠论买点')
                if s.get('vol_ratio', 0) > 3:
                    tags.append('放量')
                if s.get('pullback_pct', 0) > 5:
                    tags.append('回踩')
                kw_str = ' · '.join(tags) if tags else sys_info['tag']
                stocks.append({
                    'code': code,
                    'name': s.get('name', ''),
                    'keywords': kw_str,
                    'reason': (
                        '%s推荐 | <span class="chg-val" data-chg="%.2f">涨幅%.2f%%</span> | '
                        '<span class="vol-val" data-vol="%.1f">量比%.1f</span> | <span class="turnover-val">换手率%.2f%%</span>'
                    ) % (
                        sys_info['tag'],
                        float(s.get('chg_pct', s.get('change_val', 0)) or 0),
                        float(s.get('chg_pct', s.get('change_val', 0)) or 0),
                        float(s.get('vol_ratio', 0) or 0),
                        float(s.get('vol_ratio', 0) or 0),
                        float(s.get('turnover', 0) or 0),
                    ),
                    'chg_pct': float(s.get('chg_pct', s.get('change_val', 0)) or 0),
                    'vol_ratio': float(s.get('vol_ratio', 0) or 0),
                    'turnover': float(s.get('turnover', 0) or 0),
                    'source': sys_info['tag'],
                    'tag_color': sys_info['tag_color'],
                    'score': s.get('final_score', s.get('total', 0)),
                    'date': d,
                })

    # 按 score 排序，取前 20
    stocks.sort(key=lambda x: x['score'], reverse=True)
    return stocks[:20]


def build_dynamic_watchlist(event_stocks, event_detail, daily_picks_stocks, max_stocks=15):
    """
    合并生成动态股票池
    优先级: 事件告警股 > 事件提及股 > 选股系统推荐
    """
    seen = {}  # code -> {stock_info, priority}

    # P0: 告警股（来自 research 事件，权重最高）
    for code, count in event_stocks:
        detail = event_detail.get(code, {})
        if detail.get('alerts', 0) > 0:
            seen[code] = {
                **detail,
                'code': code,
                'keywords': ' · '.join(detail.get('reasons', [])[:3]),
                'reason': '投研告警 | %d次提及' % count,
                'priority': 0,
                'source': 'event_alert',
            }

    # P1: 事件提及股（排除已在 P0 的）
    for code, count in event_stocks:
        if code in seen:
            continue
        detail = event_detail.get(code, {})
        seen[code] = {
            **detail,
            'code': code,
            'keywords': ' · '.join(detail.get('reasons', [])[:3]) if detail.get('reasons') else '产业动态',
            'reason': '新闻提及 | %d次' % count,
            'priority': 1,
            'source': 'event',
        }

    # P2: 选股系统推荐（取 score 最高的，排除已在 P0/P1 的）
    for s in daily_picks_stocks:
        if s['code'] in seen:
            continue
        seen[s['code']] = {
            'code': s['code'],
            'name': s['name'],
            'keywords': s['keywords'],
            'reason': s['reason'],
            'chg_pct': s.get('chg_pct', 0),
            'vol_ratio': s.get('vol_ratio', 0),
            'turnover': s.get('turnover', 0),
            'priority': 2,
            'source': s['source'],
            'tag_color': s.get('tag_color') or TAG_COLORS.get(s['source'], '#e0e0e0'),
        }

    # P2 选股系统推荐已并入上述逻辑（见上）；底仓(CORE_STOCKS)已移除，不再强制写入

    # 按 priority + 排序，取前 max_stocks
    result = sorted(seen.values(), key=lambda x: x['priority'])
    return result[:max_stocks]


def make_watchlist_html(stocks, prices=None):
    """生成自选股票池 HTML 片段"""
    if not stocks:
        return '<div class="stock-list"><p class="empty">暂无数据</p></div>'

    prices = prices or {}
    lines = ['<div class="stock-list">']
    for s in stocks:
        code = s['code']
        name = s.get('name', '?')
        price = prices.get(code, '--')
        keywords = s.get('keywords', '')
        reason = s.get('reason', '')
        tag = s.get('source', '')
        tag_color = s.get('tag_color') or TAG_COLORS.get(tag, '#e0e0e0')
        # 选出时间戳: 首次白色 / 重复蓝色
        selected_at = s.get('selected_at', '')
        first_seen = s.get('first_seen', '')
        is_new = s.get('is_new', False)
        time_cls = 'new' if is_new else 'repeat'
        time_short = selected_at[5:] if len(selected_at) >= 16 else selected_at
        repeat_icon = '' if is_new else '↻ '
        time_html = f'<span class="stock-time {time_cls}" title="首次入选 {first_seen}">{repeat_icon}🕒 {time_short}</span>'
        # 价格染色: 用 chg_pct 判断 (红涨绿跌白平)
        p_info = prices.get(code) or {}
        price = p_info.get('price', '--') if p_info else '--'
        chg_pct = p_info.get('chg_pct') if p_info else None
        # 用 qt 实时数据覆盖 daily_picks 里的空值
        _qt_turnover = p_info.get('turnover')
        if _qt_turnover is None:
            _qt_turnover = s.get('turnover', 0)
        s['turnover'] = _qt_turnover
        if chg_pct is None or chg_pct == 0:
            price_color = '#e0e0e0'
        elif chg_pct > 0:
            price_color = '#ef5350'
        else:
            price_color = '#4caf50'
        # 涨跌标签（红绿白，用于股票名旁）
        if chg_pct is None or chg_pct == 0:
            chg_tag_color = '#e0e0e0'
            tag_text = '平'
        elif chg_pct > 0:
            chg_tag_color = '#ef5350'
            tag_text = f'+{chg_pct:.2f}%'
        else:
            chg_tag_color = '#4caf50'
            tag_text = f'{chg_pct:.2f}%'
        chg_tag = f'<span class="chg-tag" style="color:{chg_tag_color};font-weight:700;font-size:0.75em;margin-left:4px">{tag_text}</span>'

        # 先删掉 daily_picks 里自带的涨幅 span
        reason = re.sub(r'\s*\|\s*<span class="chg-val"[^>]*>[^<]*</span>', '', reason)

        # reason: daily_picks 阶段已注入 HTML span (chg-val / vol-val)
        # event 来源: 没有 chg_pct, 用纯文本 (无需染色)
        # 染色: 根据 chg_pct 给 .chg-val 加内联色
        chg_val = s.get('chg_pct', 0) or 0
        try:
            chg_val = float(chg_val)
        except (TypeError, ValueError):
            chg_val = 0.0
        if chg_val > 0:
            chg_color = '#ef5350'
        elif chg_val < 0:
            chg_color = '#4caf50'
        else:
            chg_color = '#e0e0e0'
        vol_val = s.get('vol_ratio', 0) or 0
        try:
            vol_val = float(vol_val)
        except (TypeError, ValueError):
            vol_val = 0.0
        # 量比颜色: >2 荧光紫 (放量), 0.5-2 白, <0.5 灰
        if vol_val >= 2:
            vol_color = '#b829ff'
        elif vol_val < 0.5:
            vol_color = '#888'
        else:
            vol_color = '#e0e0e0'
        # 量比: 替换整个 span
        reason = re.sub(
            r'<span class="vol-val"[^>]*>[^<]*</span>',
            f'<span class="vol-val" style="color:{vol_color};font-weight:600" data-vol="{vol_val:.1f}">量比{vol_val:.1f}</span>',
            reason
        )
        # 换手率: 更新 span 内容 + 灰色
        turnover_val = s.get('turnover', 0) or 0
        try:
            turnover_val = float(turnover_val)
        except (TypeError, ValueError):
            turnover_val = 0.0
        reason = re.sub(
            r'<span class="turnover-val"[^>]*>换手率[^<]*</span>',
            f'<span class="turnover-val" style="color:#ffd700;font-weight:600">换手率{turnover_val:.2f}%</span>',
            reason
        )
        # 东方财富链接
        market = 'sh' if code.startswith('6') else 'sz'
        link = f'https://quote.eastmoney.com/{market}{code}.html'

        badge = ''
        if tag == 'event_alert':
            badge = '<span class="badge badge-red">告警</span>'
        elif tag == 'event':
            badge = '<span class="badge badge-orange">提及</span>'
        elif tag:
            badge = f'<span class="badge" style="background:{tag_color}20;color:{tag_color}">{tag}</span>'

        lines.append(f'''          <div class="stock-item">
            <div class="stock-info">
              <a href="{link}" target="_blank" class="stock-name">{name}{chg_tag}</a>
              <a href="{link}" target="_blank" class="stock-code">{code}</a>
              {badge}
              <span class="stock-price-tag" style="margin-left:auto;font-weight:600;color:{price_color}">{price}</span>
            </div>
            <div class="stock-meta">
              {time_html}
              <span class="stock-keywords">{keywords}</span>
              <span class="stock-reason">{reason}</span>
            </div>
          </div>''')
    lines.append('        </div>')
    return '\n'.join(lines)


def make_events_html(events):
    """生成事件列表 HTML"""
    if not events:
        return '<div class="event-list"><p class="empty">暂无事件</p></div>'

    lines = ['<div class="event-list">']
    for e in events[:20]:
        is_alert = e.get('type') == 'alert'
        cls = 'event-item event-alert' if is_alert else 'event-item'
        t = e.get('time', '')[:16]
        title = e.get('title', '')
        content = e.get('content', '')[:100]
        source = e.get('source', '')
        url = e.get('source_url', '')
        stocks = e.get('stocks', [])
        badge = '<span class="event-badge alert">重大预警</span>' if is_alert else '<span class="event-badge">存档</span>'

        stock_tags = ''.join(
            f'<span class="stock-tag">{s.get("name","?")}</span>'
            for s in stocks[:5]
        )

        if url:
            title_html = f'<a href="{url}" target="_blank" class="event-title">{title}</a>'
        else:
            title_html = f'<span class="event-title">{title}</span>'

        lines.append(f'''<div class="{cls}">
              <div class="event-header">
                {badge}
                <span class="event-time">{t}</span>
                <span class="event-source">{source}</span>
              </div>
              <div class="event-body">
                {title_html}
                <p class="event-content">{content}...</p>
                {stock_tags}
              </div>
            </div>''')
    lines.append('</div>')
    return '\n'.join(lines)


def generate_html(watchlist_stocks, events, meta):
    """生成完整的 research.html"""
    # 6b. 抓取自选股票池的实时价格
    watchlist_codes = [s['code'] for s in watchlist_stocks]
    watchlist_prices = fetch_qt_prices(watchlist_codes)
    watchlist_html = make_watchlist_html(watchlist_stocks, prices=watchlist_prices)
    events_html = make_events_html(events)

    updated = meta.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    stats = meta.get('stats', {})
    alert_count = stats.get('alerts', 0)
    filtered_count = stats.get('filtered', 0)
    event_date = meta.get('date', '')

    # 统计各来源数量
    source_counts = Counter(s.get('source', '') for s in watchlist_stocks)
    sources_str = ' | '.join(f'{k}({v})' for k, v in source_counts.items() if k)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <title>投研信息筛选系统 | 方瑟的openclaw</title>
  <style>
    :root {{
      --neon-purple: #b829ff;
      --neon-pink: #ff2d6a;
      --neon-cyan: #00fff2;
      --dark-bg: #0a0a0f;
      --card-bg: rgba(15, 15, 25, 0.85);
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif; background: var(--dark-bg); color: #e0e0e0; min-height: 100vh; padding: 20px; }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    header {{ text-align: center; margin-bottom: 30px; padding: 20px; background: linear-gradient(135deg, rgba(183,28,211,0.15), rgba(156,39,176,0.1)); border-radius: 16px; border: 1px solid rgba(183,28,211,0.2); }}
    h1 {{ font-size: 2em; margin-bottom: 8px; background: linear-gradient(135deg, #ce93d8, #ab47bc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
    .subtitle {{ color: #888; font-size: 0.9em; margin-top: 8px; }}
    .stats-bar {{ display: flex; justify-content: center; gap: 30px; margin: 15px 0; flex-wrap: wrap; }}
    .stat {{ background: rgba(255,255,255,0.05); padding: 8px 20px; border-radius: 20px; font-size: 0.85em; }}
    .stat.alert {{ background: rgba(255,45,106,0.15); color: #ff2d6a; }}
    .layout {{ display: grid; grid-template-columns: 380px 1fr; gap: 24px; }}
    @media (max-width: 900px) {{ .layout {{ grid-template-columns: 1fr; }} }}
    .panel {{ background: var(--card-bg); border-radius: 16px; padding: 20px; border: 1px solid rgba(255,255,255,0.06); }}
    .panel-title {{ font-size: 1.1em; font-weight: 600; margin-bottom: 16px; display: flex; align-items: center; gap: 8px; color: #ce93d8; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 10px; }}
    .stock-item {{ padding: 12px; margin-bottom: 8px; background: rgba(255,255,255,0.03); border-radius: 10px; transition: background 0.2s; }}
    .stock-item:hover {{ background: rgba(184,41,255,0.08); }}
    .stock-info {{ display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }}
    .stock-name {{ color: #e0e0e0; font-weight: 600; font-size: 1em; text-decoration: none; }}
    .stock-name:hover {{ color: #ce93d8; }}
    .chg-tag {{ }}
    .stock-code {{ color: #666; font-size: 0.85em; text-decoration: none; }}
    .badge {{ font-size: 0.7em; padding: 2px 8px; border-radius: 10px; font-weight: 600; }}
    .badge-red {{ background: rgba(255,45,106,0.2); color: #ff2d6a; }}
    .badge-orange {{ background: rgba(255,152,0,0.2); color: #ff9800; }}
    .badge-purple {{ background: rgba(184,41,255,0.2); color: #b829ff; }}
    .stock-meta {{ display: flex; flex-direction: column; gap: 3px; }}
    .stock-keywords {{ font-size: 0.8em; color: #888; }}
    .stock-reason {{ font-size: 0.78em; color: #555; }}
    .stock-time {{ font-size: 0.75em; font-weight: 600; display: inline-block; margin-bottom: 2px; }}
    .stock-time.new {{ color: #ffffff; }}
    .stock-time.repeat {{ color: #4da6ff; }}
    .event-item {{ padding: 14px; margin-bottom: 10px; background: rgba(255,255,255,0.03); border-radius: 10px; border-left: 3px solid rgba(255,255,255,0.1); }}
    .event-alert {{ border-left-color: #ff2d6a; background: rgba(255,45,106,0.05); }}
    .event-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }}
    .event-badge {{ font-size: 0.7em; padding: 2px 8px; border-radius: 10px; font-weight: 600; background: rgba(255,255,255,0.08); color: #aaa; }}
    .event-badge.alert {{ background: rgba(255,45,106,0.2); color: #ff2d6a; }}
    .event-time {{ font-size: 0.8em; color: #666; }}
    .event-source {{ font-size: 0.8em; color: #555; margin-left: auto; }}
    .event-title {{ color: #e0e0e0; font-weight: 500; display: block; margin-bottom: 4px; text-decoration: none; }}
    .event-title:hover {{ color: #ce93d8; }}
    .event-content {{ font-size: 0.82em; color: #777; margin: 4px 0; line-height: 1.4; }}
    .stock-tag {{ display: inline-block; background: rgba(184,41,255,0.15); color: #b829ff; font-size: 0.75em; padding: 2px 8px; border-radius: 8px; margin: 2px 4px 2px 0; }}
    .source-info {{ font-size: 0.75em; color: #444; text-align: right; margin-top: 10px; }}
    .empty {{ color: #444; text-align: center; padding: 20px; font-size: 0.9em; }}
    footer {{ text-align: center; margin-top: 30px; color: #444; font-size: 0.8em; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>投研信息筛选系统</h1>
      <div class="subtitle">动态股票池 · 实时新闻追踪 · 智能预警</div>
      <div class="stats-bar">
        <span class="stat">更新时间: {updated}</span>
        <span class="stat alert">重大预警: {alert_count}</span>
        <span class="stat">存档事件: {filtered_count}</span>
      </div>
    </header>
    <div class="layout">
      <aside>
        <div class="panel">
          <div class="panel-title">自选股票池 ({len(watchlist_stocks)})</div>
          <div class="source-info">来源: {sources_str}</div>
{watchlist_html}
        </div>
      </aside>
      <main>
        <div class="panel">
          <div class="panel-title">投研事件追踪 ({len(events)})</div>
{events_html}
        </div>
      </main>
    </div>
    <footer>
      <p>数据来源: 东方财富 · 财联社 · 新浪财经 | 每交易日晚 20:00 自动更新</p>
      <p>动态股票池: 投研事件高频股 + 选股系统推荐（纯数据驱动，无硬编码底仓）</p>
    </footer>
  </div>
</body>
</html>'''


def main():
    print(f"[generate_research_html] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. 加载投研事件
    events, meta, stock_meta = load_research_events()
    print(f"  事件: {len(events)} 条")

    # 2. 提取事件中的股票频率
    stock_counter = Counter()
    stock_detail = {}
    for evt in events:
        is_alert = evt.get('type') == 'alert'
        t = evt.get('time', '')[:10]
        for s in evt.get('stocks', []):
            key = s.get('code', '')
            name = s.get('name', '')
            reason = s.get('match_reason', '')
            if not key:
                continue
            weight = 3 if is_alert else 1
            stock_counter[key] += weight
            if key not in stock_detail:
                stock_detail[key] = {'name': name, 'alerts': 0, 'reasons': [], 'last_seen': ''}
            stock_detail[key]['name'] = name
            stock_detail[key]['last_seen'] = t
            if is_alert:
                stock_detail[key]['alerts'] += 1
            if reason and reason not in stock_detail[key]['reasons']:
                stock_detail[key]['reasons'].append(reason)

    ranked = stock_counter.most_common()
    print(f"  事件股票覆盖: {len(ranked)} 只")

    # 3. 加载每日选股推荐
    daily_stocks = load_daily_picks(lookback_days=3)
    print(f"  选股系统推荐: {len(daily_stocks)} 只")

    # 4. 生成动态股票池
    watchlist = build_dynamic_watchlist(ranked, stock_detail, daily_stocks, max_stocks=15)
    print(f"  动态股票池: {len(watchlist)} 只")
    for s in watchlist:
        print(f"    [{s['source']:12}] {s['code']} {s['name']:8} | {s['keywords'][:30]}")

    # 4b. 选出时间戳 + 首次/重复标记（跨运行持久化到 stock_meta）
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    for s in watchlist:
        code = s['code']
        if code in stock_meta:
            s['is_new'] = False
            s['first_seen'] = stock_meta[code]
        else:
            s['is_new'] = True
            stock_meta[code] = now_str
            s['first_seen'] = now_str
        s['selected_at'] = now_str
    print(f"  时间戳: 首次={sum(1 for s in watchlist if s['is_new'])} 重复={sum(1 for s in watchlist if not s['is_new'])}")

    # 5. 生成 HTML
    html = generate_html(watchlist, events, meta)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f"\n  -> {OUTPUT_HTML} ({len(html):,} bytes)")

    # 6. 更新 research_data.json 中的 watchlist 字段（方便其他系统读取）
    if RESEARCH_DATA_JSON.exists():
        with open(RESEARCH_DATA_JSON, encoding='utf-8') as f:
            data = json.load(f)
        data['stock_meta'] = stock_meta
        data['watchlist'] = [{
            'name': s['name'],
            'code': s['code'],
            'keywords': s['keywords'],
            'reason': s['reason'],
            'source': s['source'],
            'selected_at': s.get('selected_at', ''),
            'first_seen': s.get('first_seen', ''),
            'is_new': s.get('is_new', False),
        } for s in watchlist]
        with open(RESEARCH_DATA_JSON, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  -> research_data.json watchlist updated")

    # 7. GitHub 同步已移至每日钉钉推送的 sync_func.py 统一处理
    # （避免 subprocess 挂起问题；本地文件由 news_sync cron 同步到 GitHub）
    print("[generate_research_html] DONE")


if __name__ == '__main__':
    main()
