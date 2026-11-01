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
import sys, os, json, re, html
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


def _is_junk_stock(name):
    """ST/退市/风险警示股不进自选池（实战噪音）"""
    n = (name or '').upper()
    return 'ST' in n or n.startswith('退') or '退市' in n


def build_dynamic_watchlist(event_stocks, event_detail, daily_picks_stocks, max_stocks=15):
    """
    合并生成动态股票池
    优先级: 事件告警股 > 事件提及股 > 选股系统推荐
    """
    seen = {}  # code -> {stock_info, priority}

    # P0: 告警股（来自 research 事件，权重最高）
    for code, count in event_stocks:
        if _is_junk_stock(event_detail.get(code, {}).get('name')):
            continue
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
        if _is_junk_stock(event_detail.get(code, {}).get('name')):
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
        if _is_junk_stock(s.get('name')):
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


# ── RPS60 主线雷达 ──────────────────────────────────────────────────────
RPS_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
RPS_TONES = [
    (90, '#ff2d2d', 'rgba(255,45,45,0.20)'),
    (80, '#ff6b35', 'rgba(255,107,53,0.16)'),
    (70, '#ffb333', 'rgba(255,179,51,0.13)'),
    (50, '#39ff14', 'rgba(57,255,20,0.10)'),
    (40, '#22bbaa', 'rgba(34,187,170,0.10)'),
]


def _rps_tone(v):
    for th, c, bg in RPS_TONES:
        if v is not None and v >= th:
            return c, bg
    return '#4488cc', 'rgba(68,136,204,0.10)'


def build_rps_board():
    """读 daily_picks.json 的 sector_rankings（多周期新格式），聚合主线雷达数据。

    返回 {'asof','universe','main','fresh','fading'}；旧格式（无 rps60 字段）返回 None。
    """
    if not DAILY_PICKS_JSON.exists():
        return None
    try:
        with open(DAILY_PICKS_JSON, encoding='utf-8') as f:
            d = json.load(f)
        sr = d.get('sector_rankings') or {}
        dates = sorted(k for k in sr if RPS_DATE_RE.match(str(k)))
        if not dates:
            return None
        asof = dates[-1]
        rows = sr[asof] or []
        if not rows or not isinstance(rows[0], dict) or rows[0].get('rps60') is None:
            return None                      # 旧格式单周期，面板隐藏
        enriched = []
        for r in rows:
            f = r.get('fresh')
            if f is None:
                r20, r120 = r.get('rps20'), r.get('rps120')
                if r20 is None or r120 is None:
                    continue
                f = r20 - r120
            enriched.append({
                'name': r.get('name', ''), 'code': str(r.get('code', '')).lower(),
                'rps60': r.get('rps60'), 'rps20': r.get('rps20'), 'rps120': r.get('rps120'),
                'chg60': r.get('change60'), 'fresh': f,
                'score': r.get('score') or r.get('rps') or 0,
            })
        if not enriched:
            return None
        # 近6日 RPS60 走势（Sparkline 用；缺日为 None）—— 按 bk 码跨日期对齐
        hist_dates = dates[-6:]
        hist_map = {}
        for dt in hist_dates:
            for r2 in (sr.get(dt) or []):
                c2 = str(r2.get('code', '')).lower()
                if c2:
                    hist_map.setdefault(c2, {})[dt] = r2.get('rps60')
        for x in enriched:
            x['hist'] = [hist_map.get(x['code'], {}).get(dt) for dt in hist_dates]
        main = sorted(enriched, key=lambda x: (-x['score'], -(x['rps60'] or 0)))[:15]
        fresh = sorted(enriched, key=lambda x: -x['fresh'])[:8]
        fading = sorted(enriched, key=lambda x: x['fresh'])[:8]
        fresh = [x for x in fresh if x['fresh'] >= 40]
        fading = [x for x in fading if x['fresh'] <= -40]
        return {'asof': asof, 'universe': len(enriched), 'main': main,
                'fresh': fresh, 'fading': fading, 'hist_dates': hist_dates}
    except Exception as e:
        print(f"  [warn] RPS 主线雷达失败: {type(e).__name__}: {e}")
        return None


def _spark_svg(hist):
    """近6日 RPS60 迷你走势 SVG（上升红 / 下降绿，A股配色）。少于2个有效点返回空。"""
    vals = [v for v in (hist or []) if isinstance(v, (int, float))]
    if len(vals) < 2:
        return ''
    W, H = 52, 14
    mn, mx = min(vals), max(vals)
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = 2 + (W - 4) * i / (n - 1)
        y = H - 2 - (H - 4) * (((v - mn) / (mx - mn)) if mx > mn else 0.5)
        pts.append('%.1f,%.1f' % (x, y))
    col = '#ff2d6a' if vals[-1] >= vals[0] else '#00e0c0'
    return ('<svg class="rps-spark" width="%d" height="%d" viewBox="0 0 %d %d">'
            '<polyline points="%s" fill="none" stroke="%s" stroke-width="1.5"/></svg>'
            % (W, H, W, H, ' '.join(pts), col))


def make_rps_board_html(rps):
    if not rps:
        return ''
    def chip(x):
        c, bg = _rps_tone(x['rps60'])
        return ('<span class="rps-chip" onclick="openKline(\'%s\',\'%s\')" style="color:%s;background:%s">%s<b>%s</b></span>'
                % (x['code'], x['name'].replace("'", ''), c, bg, x['name'],
                   x['rps60'] if x['rps60'] is not None else '–'))
    def row(x, fresh_color):
        c, _ = _rps_tone(x['rps60'])
        chg = x.get('chg60')
        chg_s = ('%+.1f%%' % chg) if isinstance(chg, (int, float)) else '–'
        chg_c = '#ff2d6a' if (isinstance(chg, (int, float)) and chg >= 0) else '#00fff2'
        return ('<div class="rps-row" onclick="openKline(\'%s\',\'%s\')">'
                '<span class="rps-nm" style="color:%s">%s</span>'
                '%s'
                '<span class="rps-r60">RPS60 <b>%s</b></span>'
                '<span class="rps-chg" style="color:%s">60日 %s</span>'
                '<span class="rps-fresh" style="color:%s">%+d</span>'
                '<span class="rps-link" title="联动右侧事件流（再次点击取消）" '
                'onclick="event.stopPropagation();researchBoardLink(\'%s\')">⚡</span></div>'
                % (x['code'], x['name'].replace("'", ''), c, x['name'],
                   _spark_svg(x.get('hist')),
                   x['rps60'] if x['rps60'] is not None else '–', chg_c, chg_s,
                   fresh_color, x['fresh'],
                   x['name'].replace("'", '')))
    main_chips = ''.join(chip(x) for x in rps['main']) or '<span class="empty">—</span>'
    fresh_rows = ''.join(row(x, '#ff9800') for x in rps['fresh']) or '<div class="empty">暂无新晋板块</div>'
    fading_rows = ''.join(row(x, '#4da6ff') for x in rps['fading']) or '<div class="empty">暂无退潮板块</div>'
    return (
        '        <div class="panel">\n'
        '          <div class="panel-title">🎯 RPS60 主线雷达</div>\n'
        f"          <div class='rps-meta'>固定板块池 {rps['universe']} 个 · 基准日 {rps['asof']} · "
        "fresh = RPS20 − RPS120（动能差）· 折线=近6日RPS60走势 · 点板块名看K线 · ⚡联动事件流</div>\n"
        f'          <div class="rps-main">{main_chips}</div>\n'
        '          <div class="rps-cols">\n'
        f"            <div><div class='rps-col-hd' style='color:#ff9800'>🚀 新晋 Top{len(rps['fresh'])}</div>{fresh_rows}</div>\n"
        f"            <div><div class='rps-col-hd' style='color:#4da6ff'>⚠️ 退潮 Top{len(rps['fading'])}</div>{fading_rows}</div>\n"
        '          </div>\n'
        '        </div>\n'
    )



# ── research 页增强：中军👑 / 板块⚡联动 / 自选穿透 / 日期筛选 / 主线模式 ──
RESEARCH_EXTRA_JS = '''<script>
(function () {
  function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}
  function symOf(c){c=String(c).replace(/^(sh|sz|bj)/i,'');return (/^[469]/.test(c)?'sh':'sz')+c;}
  // ===== 1) 板块 ⚡ 联动事件流 + 雷达闪烁 =====
  var cur=null, bar=null;
  function ensureBar(){
    var panel=document.getElementById('eventsPanel');
    if(!panel) return null;
    if(!bar){
      bar=document.createElement('div');
      bar.id='evFilterBar';
      bar.style.display='none';
      panel.insertBefore(bar, panel.firstChild);
    }
    return bar;
  }
  function flashRadar(name){
    // 左侧雷达 + 漏斗板块闪烁提示（点击右侧事件板块 chips 时）
    var sel='.rps-row, .rps-chip, .fnl-hd';
    Array.prototype.forEach.call(document.querySelectorAll(sel),function(el){
      el.classList.remove('rps-flash');
      if(el.textContent && el.textContent.indexOf(name)>=0){
        void el.offsetWidth;  // 重启动画
        el.classList.add('rps-flash');
        setTimeout(function(){ el.classList.remove('rps-flash'); }, 2600);
      }
    });
  }
  window.researchBoardLink=function(name, noFlash){
    cur=(cur===name)?null:name;
    var b=ensureBar();
    var items=document.querySelectorAll('.event-item');
    var hit=0;
    Array.prototype.forEach.call(items,function(el){
      var bds=(el.getAttribute('data-bds')||'').split(',');
      var isHit=cur && bds.indexOf(cur)>=0;
      el.classList.remove('ev-dim','ev-hit');
      if(cur){ el.classList.add(isHit?'ev-hit':'ev-dim'); if(isHit)hit++; }
    });
    if(b){
      if(cur){ b.innerHTML='🔗 事件流联动: <b>'+esc(cur)+'</b>（命中 '+hit+' 条） <span style="cursor:pointer;text-decoration:underline" onclick="researchBoardLink(\\''+name.replace(/'/g,'')+'\\')">取消</span>'; b.style.display='block'; }
      else { b.style.display='none'; }
    }
    if(cur && !noFlash) flashRadar(cur);
    var ev=document.getElementById('eventsPanel');
    if(cur && ev && ev.scrollIntoView) ev.scrollIntoView({behavior:'smooth',block:'start'});
  };
  function addLinks(){
    // 漏斗板块头 ⚡
    Array.prototype.forEach.call(document.querySelectorAll('.fnl-hd'),function(hd){
      if(hd.querySelector('.rps-link')) return;
      var nm=hd.querySelector('.fnl-name'); if(!nm) return;
      var s=document.createElement('span');
      s.className='rps-link'; s.textContent='⚡'; s.title='联动右侧事件流';
      s.onclick=function(e){ e.stopPropagation(); researchBoardLink(nm.textContent); };
      hd.appendChild(s);
    });
  }
  // ===== 2) 自选 ↔ 事件 双向穿透 =====
  // hover 左侧自选股 → 右侧相关新闻高亮、无关变暗
  function wlHover(){
    Array.prototype.forEach.call(document.querySelectorAll('.stock-item'),function(it){
      var nmEl=it.querySelector('.stock-name'); if(!nmEl) return;
      var nm=nmEl.textContent.replace(/[+\\-]?[0-9.]+%$/,'').trim();
      if(!nm) return;
      it.addEventListener('mouseenter',function(){
        Array.prototype.forEach.call(document.querySelectorAll('.event-item'),function(ev){
          var wl=(ev.getAttribute('data-wlnames')||'').split(',');
          var hit=wl.indexOf(nm)>=0;
          if(!hit){
            var tx=ev.textContent||'';
            hit=tx.indexOf(nm)>=0;
          }
          ev.classList.remove('ev-dim','ev-hit');
          ev.classList.add(hit?'ev-hit':'ev-dim');
        });
      });
      it.addEventListener('mouseleave',function(){
        Array.prototype.forEach.call(document.querySelectorAll('.event-item'),function(ev){
          ev.classList.remove('ev-dim','ev-hit');
        });
      });
    });
  }
  // ===== 3) 事件流控制条：⭐只看自选相关 + 📅 日期筛选 =====
  function evControls(){
    var panel=document.getElementById('eventsPanel'); if(!panel) return;
    var hd=panel.querySelector('.panel-title'); if(!hd) return;
    var ctl=document.createElement('span'); ctl.className='ev-ctl';
    var btn=document.createElement('button'); btn.id='wlOnlyBtn'; btn.textContent='⭐ 只看自选相关';
    var sel=document.createElement('select'); sel.id='evDateSel';
    sel.innerHTML='<option value="">📅 全部日期</option>';
    Array.prototype.forEach.call(panel.querySelectorAll('.ev-date-hd'),function(h){
      var d=h.getAttribute('data-evdate'); if(d) sel.innerHTML+='<option value="'+d+'">'+d+'</option>';
    });
    ctl.appendChild(btn); ctl.appendChild(sel); hd.appendChild(ctl);
    var wlOnly=false;
    function apply(){
      Array.prototype.forEach.call(panel.querySelectorAll('.event-item'),function(ev){
        var okWl=!wlOnly || ev.getAttribute('data-wl')==='1';
        ev.style.display=okWl?'':'none';
      });
      // 日期分组头跟随显隐
      var lastHd=null, hdHas=false;
      Array.prototype.forEach.call(panel.querySelectorAll('.event-item, .ev-date-hd'),function(el){
        if(el.classList.contains('ev-date-hd')){
          if(lastHd) lastHd.style.display=hdHas?'':'none';
          lastHd=el; hdHas=false;
        } else if(el.style.display!=='none'){
          hdHas=true;
        }
      });
      if(lastHd) lastHd.style.display=hdHas?'':'none';
      btn.classList.toggle('on', wlOnly);
      btn.textContent=wlOnly?'⭐ 自选中（再点取消）':'⭐ 只看自选相关';
    }
    // 事件卡回填所属日期（按 DOM 分组头位置）
    (function backfill(){
      var d='';
      Array.prototype.forEach.call(panel.querySelectorAll('.event-item, .ev-date-hd'),function(el){
        if(el.classList.contains('ev-date-hd')) d=el.getAttribute('data-evdate')||'';
        else el.setAttribute('data-date', d);
      });
    })();
    btn.onclick=function(){ wlOnly=!wlOnly; apply(); };
    sel.onchange=function(){
      var d=sel.value;
      Array.prototype.forEach.call(panel.querySelectorAll('.event-item'),function(ev){
        if(d && ev.getAttribute('data-date')!==d) ev.style.display='none';
        else if(!wlOnly) ev.style.display='';
        else ev.style.display=(ev.getAttribute('data-wl')==='1')?'':'none';
      });
      Array.prototype.forEach.call(panel.querySelectorAll('.ev-date-hd'),function(h){
        h.style.display=(!d||h.getAttribute('data-evdate')===d)?'':'none';
      });
    };
  }
  // ===== 4) 主线模式：剔除杂毛题材（默认开，可勾选关闭）=====
  var MAINLINE_KW=['算力','AI','人工智能','芯片','半导体','光模块','CPO','PCB','印制电路板','低空','无人驾驶','智能驾驶','机器人','军工','固态电池','商业航天','量子','信创','数据要素','消费电子','创新药','稀土','黄金','有色','白酒','食品饮料','新能源','锂电','光伏','医药','传媒','游戏'];
  // 说明：银行/保险/证券/电力等大市值防守板块默认被主线模式隐藏（勾选框可关）
  function isMainline(txt){
    for(var i=0;i<MAINLINE_KW.length;i++){ if(txt.indexOf(MAINLINE_KW[i])>=0) return true; }
    return false;
  }
  function mlMode(){
    var panel=document.querySelector('.rps-main');
    if(!panel || !panel.parentElement) return;
    var wrap=panel.parentElement;  // RPS 面板
    var meta=wrap.querySelector('.rps-meta'); if(!meta) return;
    var lab=document.createElement('label'); lab.className='ml-toggle';
    lab.innerHTML='<input type="checkbox" id="mlChk" checked>🎯 主线模式（隐藏杂毛题材）';
    meta.appendChild(lab);
    function apply(){
      var on=document.getElementById('mlChk').checked;
      Array.prototype.forEach.call(wrap.querySelectorAll('.rps-chip,.rps-row'),function(el){
        var nm=el.querySelector('.rps-nm')||el;
        el.style.display=(on&&!isMainline(nm.textContent))?'none':'';
      });
    }
    document.getElementById('mlChk').onchange=apply;
    apply();
  }
  // ===== 5) 中军👑：板块内成交额最大股（腾讯行情 p[38]=成交额万元）=====
  function zjRun(){
    var boards=document.querySelectorAll('.fnl-board');
    if(!boards.length) return;
    var codes=[];
    Array.prototype.forEach.call(boards,function(bd){
      Array.prototype.forEach.call(bd.querySelectorAll('.fnl-stock'),function(ch){
        var c=ch.getAttribute('data-kc')||'';
        if(/^\\d{6}$/.test(c)) codes.push(c);
      });
    });
    if(!codes.length) return;
    codes=codes.filter(function(v,i,a){return a.indexOf(v)===i;});
    var old=document.getElementById('zjQuotes'); if(old) old.remove();
    var s=document.createElement('script'); s.id='zjQuotes';
    s.src='https://qt.gtimg.cn/q='+codes.map(symOf).join(',')+'&_t='+Date.now();
    s.onload=function(){
      Array.prototype.forEach.call(boards,function(bd){
        var best=null,bestAmt=-1;
        Array.prototype.forEach.call(bd.querySelectorAll('.fnl-stock'),function(ch){
          var c=ch.getAttribute('data-kc')||'';
          if(!/^\\d{6}$/.test(c)) return;
          var v=window['v_'+symOf(c)]; if(!v) return;
          var p=String(v).split('~');
          var amt=parseFloat(p[38]);   // p[38]=成交额(万元)，p[37]是成交量(手)
          if(isNaN(amt)) return;
          if(amt>bestAmt){bestAmt=amt;best=ch;}
          var zj=ch.querySelector('.zj'); if(zj) zj.remove();
        });
        if(best && !best.querySelector('.zj')){
          var tag=document.createElement('span');
          tag.className='zj'; tag.textContent='👑中军';
          tag.title='板块内成交额最大（盘中实时，延时约15分钟）';
          best.appendChild(tag);
        }
      });
      s.remove();
    };
    s.onerror=function(){ s.remove(); };
    document.head.appendChild(s);
  }
  // 漏斗面板为异步渲染，挂 MutationObserver 等内容就绪后执行
  var fp=document.getElementById('funnelPanel');
  function boot(){ addLinks(); zjRun(); }
  function boot2(){ wlHover(); evControls(); mlMode(); }
  if(fp){
    var mo=new MutationObserver(function(){ if(document.querySelector('.fnl-board')){ mo.disconnect(); boot(); } });
    mo.observe(fp,{childList:true,subtree:true});
  }
  setTimeout(boot, 2500);   // 兜底
  setTimeout(boot2, 300);   // 静态面板控制条（自选/日期/主线模式）
})();
</script>'''

FUNNEL_PANEL = '''        <div class="panel" id="funnelPanel" style="display:none">
          <div class="panel-title">🧭 板块 → 个股信号漏斗</div>
          <div class="rps-meta" id="funnelMeta"></div>
          <div id="funnelBody"></div>
        </div>
        <script>
        (function () {
          var SRC_COLOR = { '威震天': '#ff2d6a', '益盟强买': '#ff9800', '益盟涨幅': '#ffb333', '盗火线': '#00fff2' };
          function esc(s) {
            return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
          }
          function srcTag(s) {
            var c = SRC_COLOR[s] || '#aaa';
            return '<span class="fnl-src" style="color:' + c + ';background:rgba(255,255,255,0.05)">' + esc(s) + '</span>';
          }
          function stockChip(s, extra) {
            return '<span class="fnl-stock" data-kc="' + esc(s.code) + '" data-kn="' + esc(s.name) + '">' +
              '<b>' + esc(s.name) + '</b>' + (extra || '') + (s.src || []).map(srcTag).join('') + '</span>';
          }
          fetch('sector_funnel.json?cb=' + Date.now()).then(function (r) { return r.ok ? r.json() : null; }).then(function (d) {
            if (!d || !d.boards || !d.boards.length) return;
            var meta = document.getElementById('funnelMeta');
            var body = document.getElementById('funnelBody');
            var panel = document.getElementById('funnelPanel');
            meta.textContent = '更新 ' + (d.updated || '') + ' · RPS 基准 ' + (d.asof || '') +
              ' · 信号源覆盖 ' + (d.total_signals || 0) + ' 只 · 上榜板块 ' + d.boards.length + ' 个' +
              (d.orphans && d.orphans.length ? ' · 孤儿信号 ' + d.orphans.length + ' 只' : '');
            var html = '';
            d.boards.forEach(function (bd) {
              html += '<div class="fnl-board"><div class="fnl-hd">' +
                '<span class="fnl-name" data-kc="' + esc(bd.code) + '" data-kn="' + esc(bd.name) + '">' + esc(bd.name) + '</span>' +
                '<span class="fnl-badge" style="color:#ff2d2d;background:rgba(255,45,45,0.15)">RPS60 ' + (bd.rps60 != null ? bd.rps60 : '–') + '</span>' +
                (bd.fresh != null ? '<span class="fnl-badge" style="color:' + (bd.fresh >= 0 ? '#ff9800' : '#4da6ff') + ';background:rgba(255,255,255,0.06)">动能 ' + (bd.fresh > 0 ? '+' : '') + bd.fresh + '</span>' : '') +
                '<span style="font-size:.72em;color:#666">' + bd.stocks.length + ' 只信号</span></div>' +
                '<div class="fnl-stocks">' + bd.stocks.map(function (s) { return stockChip(s); }).join('') + '</div></div>';
            });
            if (d.orphans && d.orphans.length) {
              html += '<div class="fnl-board" style="border:1px dashed rgba(77,166,255,0.25)"><div class="fnl-hd">' +
                '<span class="fnl-name" style="color:#4da6ff">🕊️ 孤儿信号（所属板块 RPS60 弱）</span></div>' +
                '<div class="fnl-stocks">' + d.orphans.map(function (s) {
                  return stockChip(s, '<span style="color:#666;font-size:.85em">' + esc(s.board) + '</span>' +
                    '<span class="fnl-badge" style="color:#4da6ff;background:rgba(68,136,204,0.12)">RPS60 ' + (s.rps60 != null ? s.rps60 : '–') + '</span>');
                }).join('') + '</div></div>';
            }
            body.innerHTML = html;
            Array.prototype.forEach.call(body.querySelectorAll('[data-kc]'), function (el) {
              el.onclick = function () { if (window.openKline) openKline(el.getAttribute('data-kc'), el.getAttribute('data-kn')); };
            });
            panel.style.display = '';
          }).catch(function () { /* sector_funnel.json 不存在则保持隐藏 */ });
        })();
        </script>
        '''




# ── 舆情情绪量化（正则，A 股红涨绿跌）──────────────────────────────────
SENTI_STRONG_POS = re.compile(r'(中标|大增|暴涨|增持|回购|重大突破|超预期|扭亏为盈|历史新高|预增|翻倍|上调评级|重大合同|涨停)')
SENTI_WEAK_POS   = re.compile(r'(利好|增长|获批|合作|受益|签约|扩张|加码|扩产|回暖|新高)')
SENTI_STRONG_NEG = re.compile(r'(退市|暴雷|处罚|立案|亏损扩大|减持|暂停上市|风险警示|造假|违规|暴跌|跌停|黑天鹅|责令改正|警示函)')
SENTI_WEAK_NEG   = re.compile(r'(下滑|下降|承压|下调|不及预期|缩水|回落|利空|预警|回撤)')

def event_sentiment(title, content):
    """返回 (label, color, score)；score: 强多+2 弱多+1 强空-2 弱空-1 中性0"""
    t = (title or '') + ' ' + (content or '')
    sp, wp = bool(SENTI_STRONG_POS.search(t)), bool(SENTI_WEAK_POS.search(t))
    sn, wn = bool(SENTI_STRONG_NEG.search(t)), bool(SENTI_WEAK_NEG.search(t))
    if sp and not sn:
        return ('强利好', '#ff5a5a', 2)
    if sn and not sp:
        return ('强利空', '#2ecc71', -2)
    if wp and not wn:
        return ('利好', '#e57474', 1)
    if wn and not wp:
        return ('利空', '#6ec79a', -1)
    return ('中性', '#888', 0)


def make_events_html(events, board_members=None, wl_names=None):
    """生成事件列表 HTML（情绪标签 + data-bds 板块命中 + 日期分组 + 自选命中标记）。

    返回 (html, 舆情得分, 利好数, 利空数)。
    board_members: {板块名: set(codes)}，用于事件↔板块命中计算。
    wl_names: 自选股名单（set），事件标题/正文命中时 data-wl="1" 且名称高亮。
    """
    if not events:
        return '<div class="event-list"><p class="empty">暂无事件</p></div>', 0, 0, 0

    board_members = board_members or {}
    wl_names = set(wl_names or [])
    lines = ['<div class="event-list">']
    total_score = pos_n = neg_n = 0
    last_date = None
    for e in events[:20]:
        is_alert = e.get('type') == 'alert'
        cls = 'event-item event-alert' if is_alert else 'event-item'
        t = e.get('time', '')[:16]
        edate, etime = (t[:10], t[11:]) if len(t) >= 16 else (t[:10], '')
        title = e.get('title', '')
        content = e.get('content', '')[:100]
        source = e.get('source', '')
        url = e.get('source_url', '')
        stocks = e.get('stocks', [])
        badge = '<span class="event-badge alert">重大预警</span>' if is_alert else '<span class="event-badge">存档</span>'

        # 日期分组头（复盘系统按交易日对齐）
        if edate and edate != last_date:
            try:
                wd = '一二三四五六日'[datetime.strptime(edate, '%Y-%m-%d').weekday()]
            except Exception:
                wd = ''
            lines.append('<div class="ev-date-hd" data-evdate="%s">📅 %s（周%s）</div>' % (edate, edate, wd))
            last_date = edate

        # 情绪标签 + 得分
        slabel, scol, sscore = event_sentiment(title, e.get('content', ''))
        total_score += sscore
        if sscore > 0:
            pos_n += 1
        elif sscore < 0:
            neg_n += 1
        senti_html = ('<span class="senti" style="color:%s;border-color:%s40">[%s]</span>'
                      % (scol, scol, slabel))

        # 板块命中（供 ⚡联动过滤）：事件个股 ∈ 板块成员，或板块名出现在标题/正文
        ev_codes = set(str(s.get('code', '')) for s in stocks if s.get('code'))
        text_all = (title or '') + ' ' + (e.get('content', '') or '')
        hits = [bn for bn, codes in board_members.items()
                if (ev_codes & codes) or (bn in text_all)]
        bds_attr = ' data-bds="%s"' % html.escape(','.join(hits)) if hits else ''
        # 可点击板块 chips（点击 → 联动过滤 + 雷达闪烁）
        bds_chips = ''.join(
            '<span class="bds-chip" title="点击联动：过滤该板块事件 + 雷达闪烁" '
            'onclick="event.stopPropagation();researchBoardLink(\'%s\')">%s</span>'
            % (bn.replace("'", ''), bn) for bn in sorted(hits)[:4])

        # 自选命中：data-wl="1"（供「只看自选相关」过滤）+ 名称高亮
        hit_wl = []
        for wln in wl_names:
            if wln and (wln in title or wln in (e.get('content', '') or '')
                        or any(wln == s.get('name') for s in stocks)):
                hit_wl.append(wln)
        wl_attr = ' data-wl="1" data-wlnames="%s"' % html.escape(','.join(hit_wl)) if hit_wl else ' data-wl="0"'

        # 自选股名高亮（标题 + 正文截断段）
        def _hl(txt):
            for wln in hit_wl:
                txt = txt.replace(wln, '<b class="wl-hit">%s</b>' % wln)
            return txt
        title_hl, content_hl = _hl(title), _hl(content)

        stock_tags = ''.join(
            f'<span class="stock-tag{" wl-hit" if s.get("name") in hit_wl else ""}">{s.get("name","?")}</span>'
            for s in stocks[:5]
        )

        if url:
            title_html = f'<a href="{url}" target="_blank" class="event-title">{title_hl}</a>'
        else:
            title_html = f'<span class="event-title">{title_hl}</span>'

        lines.append(f'''<div class="{cls}"{bds_attr}{wl_attr}>
              <div class="event-header">
                {badge}
                <span class="event-time" title="{edate}">{etime or edate}</span>
                {senti_html}
                {bds_chips}
                <span class="event-source">{source}</span>
              </div>
              <div class="event-body">
                {title_html}
                <p class="event-content">{content_hl}...</p>
                {stock_tags}
              </div>
            </div>''')
    lines.append('</div>')
    return '\n'.join(lines), total_score, pos_n, neg_n


def make_hotspot_html(events, board_members):
    """消息面热点板块 Top8（事件命中统计条形图；诚实标注：非涨停原因词典）"""
    cnt = Counter()
    for e in events[:20]:
        ev_codes = set(str(s.get('code', '')) for s in e.get('stocks', []) if s.get('code'))
        text_all = (e.get('title', '') or '') + ' ' + (e.get('content', '') or '')
        for bn, codes in board_members.items():
            if (ev_codes & codes) or (bn in text_all):
                cnt[bn] += 1
    if not cnt:
        return ''
    top = cnt.most_common(8)
    nmax = top[0][1]
    items = []
    for bn, n in top:
        w = max(8, int(100 * n / nmax))
        items.append(
            '<div class="heat-row" onclick="researchBoardLink(\'%s\')" style="cursor:pointer">'
            '<span class="heat-nm">%s</span>'
            '<span class="heat-bar"><i style="width:%d%%;background:linear-gradient(90deg,#00e0c0,#b829ff)"></i></span>'
            '<span class="heat-ct">%d条</span></div>'
            % (bn.replace("'", ''), bn, w, n))
    return ('        <div class="panel">\n'
            '          <div class="panel-title">📈 消息面热点板块 Top%d</div>\n'
            '          <div class="rps-meta">事件板块命中统计（非涨停原因词典，需接入盘口逻辑数据源后升级）· 点击条目联动事件流</div>\n'
            % len(top)) + '\n'.join(items) + '\n        </div>\n'


def build_resonance(mkt, events, watchlist, rps=None):
    """🔥 今日资金与消息面共振结论板（规则引擎自动生成，放在页面最顶部）。

    mkt: market_review.json 字典（含 dims.money.inflow / dims.limit）。
    """
    if not mkt:
        return ''
    dims = mkt.get('dims') or {}
    money = dims.get('money') or {}
    inflow = money.get('inflow') or []
    lim = dims.get('limit') or {}
    zt, dt = lim.get('zt', 0) or 0, lim.get('dt', 0) or 0
    top_up = lim.get('top_up_names') or []
    mdate = mkt.get('date', '')
    if not (inflow or zt):
        return ''

    def _base(bn):
        """板块名去尾缀，方便模糊匹配（白酒Ⅲ → 白酒）"""
        return bn.rstrip('ⅠⅡⅢ0123456789')

    # 1) 资金主攻（流入第一 + 前三名）
    lead, lead_amt = ('', 0)
    if inflow:
        lead, lead_amt = inflow[0].get('name', ''), float(inflow[0].get('yi', 0) or 0)
    inflow_str = '、'.join('%s +%.0f亿' % (x.get('name', '?'), float(x.get('yi', 0) or 0))
                           for x in inflow[:3])

    # 2) 消息面核心逻辑：标题/正文命中资金主攻板块的事件（取情绪最强的一条）
    core_html = ''
    if lead:
        cand = [e for e in events[:20]
                if _base(lead) in ((e.get('title', '') or '') + (e.get('content', '') or ''))]
        if cand:
            best = max(cand, key=lambda e: event_sentiment(e.get('title', ''), e.get('content', ''))[2])
            lbl, col, _ = event_sentiment(best.get('title', ''), best.get('content', ''))
            u = best.get('source_url', '')
            t = best.get('title', '')[:44]
            core_html = ('核心逻辑: ' + (('<a href="%s" target="_blank" style="color:#ce93d8">%s…</a>' % (u, t)) if u
                         else '<span style="color:#ce93d8">%s…</span>' % t)
                         + ' <span class="senti" style="color:%s;border-color:%s40">[%s]</span>'
                         % (col, col, lbl))

    # 3) 自选异动：自选股 ∩ 当日涨停/涨幅榜名单（top_up_names 在 dims.money 下）
    wl_names = {s.get('name', '') for s in watchlist}
    top_up = top_up or money.get('top_up_names') or []
    hit_wl = [n for n in top_up if n in wl_names]

    # 4) RPS 主线确认（主线雷达第一）
    rps_lead = ''
    if rps and rps.get('main'):
        rps_lead = rps['main'][0].get('name', '')

    # 组装（A股红涨绿跌）
    seg = []
    if lead:
        seg.append('<span class="rz-tag rz-red">资金主攻</span><b>%s</b>（%s）' % (lead, inflow_str))
    if zt or dt:
        seg.append('<span class="rz-tag">涨跌停</span><b style="color:#ff5a5a">%d家涨停</b>'
                   ' / <b style="color:#2ecc71">%d家跌停</b>' % (zt, dt))
    if rps_lead:
        seg.append('<span class="rz-tag rz-purple">RPS主线</span><b>%s</b>' % rps_lead)
    if hit_wl:
        seg.append('<span class="rz-tag rz-gold">自选异动</span><b style="color:#ffd700">%s</b>'
                   % '、'.join(hit_wl[:4]) + ' 榜上有名')
    concl = ' · '.join(seg) if seg else ''

    html = ('        <div class="panel" id="resonBox">\n'
            '          <div class="panel-title">🔥 今日资金与消息面共振 <span class="rps-meta">规则自动生成 · 市况基准 %s</span></div>\n'
            % mdate)
    if concl:
        html += '          <div class="rz-line">%s</div>\n' % concl
    if core_html:
        html += '          <div class="rz-line rz-sub">%s</div>\n' % core_html
    if not concl and not core_html:
        html += '          <div class="rz-line" style="color:#555">市况数据尚未生成（非交易日或数据源未更新）</div>\n'
    html += '        </div>\n'
    return html


def make_heat_html(watchlist, mention_counts):
    """自选股热度排行（事件提及次数），返回 HTML 面板或空串"""
    rows = []
    for s in watchlist:
        n = mention_counts.get(s['code'], 0)
        if n > 0:
            rows.append((n, s))
    if not rows:
        return ''
    rows.sort(key=lambda x: -x[0])
    top = rows[:8]
    nmax = top[0][0]
    items = []
    for n, s in top:
        w = max(8, int(100 * n / nmax))
        alert_mark = ' <span style="color:#ff2d6a">⚠</span>' if s.get('alerts', 0) > 0 else ''
        items.append(
            '<div class="heat-row"><span class="heat-nm">%s <span class="heat-code">%s</span>%s</span>'
            '<span class="heat-bar"><i style="width:%d%%"></i></span>'
            '<span class="heat-ct">%d次</span></div>'
            % (s.get('name', '?'), s['code'], alert_mark, w, n))
    return ('        <div class="panel">\n'
            '          <div class="panel-title">🔥 自选股热度（事件提及）</div>\n'
            + '\n'.join(items) + '\n        </div>\n')


RESEARCH_EXTRA_CSS = '''
    .rps-spark{margin:0 2px;opacity:.9;flex:0 0 auto}
    .rps-link{cursor:pointer;font-size:.85em;padding:0 5px;border-radius:4px;color:#b829ff;background:rgba(184,41,255,.12);flex:0 0 auto}
    .rps-link:hover{background:rgba(184,41,255,.3);color:#e0c0ff}
    .senti{font-size:.72em;padding:0 6px;border-radius:8px;border:1px solid;font-weight:700}
    .zj{font-size:.68em;padding:0 6px;border-radius:6px;background:rgba(255,215,0,.15);color:#ffd700;font-weight:700;white-space:nowrap}
    .ev-dim{opacity:.22}
    .ev-hit{border-left-color:#ffd700 !important;background:rgba(255,215,0,.06) !important}
    #evFilterBar{background:rgba(184,41,255,.12);border:1px solid rgba(184,41,255,.35);color:#ce93d8;border-radius:8px;padding:6px 12px;margin-bottom:10px;font-size:.85em}
    #evFilterBar b{color:#ffd98a}
    .heat-row{display:flex;align-items:center;gap:8px;padding:5px 8px;margin-bottom:4px;background:rgba(255,255,255,.03);border-radius:8px;font-size:.82em}
    .heat-nm{font-weight:600;flex:0 0 auto}
    .heat-code{color:#666;font-size:.85em}
    .heat-bar{flex:1;height:8px;background:rgba(255,255,255,.06);border-radius:4px;overflow:hidden}
    .heat-bar i{display:block;height:100%;background:linear-gradient(90deg,#b829ff,#ff2d6a)}
    .heat-ct{color:#ce93d8;font-weight:700;flex:0 0 auto}
    /* ── V6: 共振结论板 / 日期分组 / 自选穿透 / 雷达闪烁 ── */
    #resonBox{border:1px solid rgba(255,215,0,.28);background:linear-gradient(135deg,rgba(255,215,0,.06),rgba(184,41,255,.05))}
    #resonBox .panel-title{color:#ffd700;border-bottom-color:rgba(255,215,0,.15)}
    .rz-line{font-size:.9em;line-height:1.7;margin:4px 0}
    .rz-line b{color:#e0e0e0}
    .rz-sub{font-size:.82em;color:#999}
    .rz-tag{display:inline-block;font-size:.7em;font-weight:700;padding:1px 8px;border-radius:8px;margin-right:4px;background:rgba(255,255,255,.08);color:#aaa;vertical-align:1px}
    .rz-red{background:rgba(255,45,45,.15);color:#ff5a5a}
    .rz-purple{background:rgba(184,41,255,.15);color:#ce93d8}
    .rz-gold{background:rgba(255,215,0,.15);color:#ffd700}
    .ev-date-hd{font-size:.8em;font-weight:700;color:#ce93d8;background:rgba(184,41,255,.08);border:1px solid rgba(184,41,255,.18);border-radius:8px;padding:4px 12px;margin:10px 0 6px}
    .wl-hit{color:#ffd700 !important;background:rgba(255,215,0,.10);border-radius:3px;padding:0 2px}
    .bds-chip{font-size:.7em;padding:1px 8px;border-radius:8px;font-weight:600;cursor:pointer;background:rgba(0,224,192,.12);color:#00e0c0}
    .bds-chip:hover{background:rgba(0,224,192,.28)}
    .rps-flash{animation:rpsFlash 1.2s ease 2}
    @keyframes rpsFlash{0%,100%{background:rgba(255,255,255,.03)}50%{background:rgba(255,215,0,.35);box-shadow:0 0 12px rgba(255,215,0,.5)}}
    .ev-ctl{display:flex;gap:8px;align-items:center;margin-left:auto;font-size:.75em}
    .ev-ctl button,.ev-ctl select{background:rgba(184,41,255,.10);border:1px solid rgba(184,41,255,.3);color:#ce93d8;border-radius:8px;padding:3px 10px;cursor:pointer;font-size:1em}
    .ev-ctl button.on{background:rgba(255,215,0,.15);border-color:rgba(255,215,0,.4);color:#ffd700}
    .ml-toggle{font-size:.78em;color:#ce93d8;cursor:pointer;user-select:none;margin-left:auto}
    .ml-toggle input{vertical-align:-2px;margin-right:4px;accent-color:#b829ff}
'''


def generate_html(watchlist_stocks, events, meta, rps=None, funnel_html='',
                  heat_html='', board_members=None, mkt=None):
    """生成完整的 research.html"""
    EXTRA_CSS = RESEARCH_EXTRA_CSS
    EXTRA_JS = RESEARCH_EXTRA_JS
    # 6b. 抓取自选股票池的实时价格
    watchlist_codes = [s['code'] for s in watchlist_stocks]
    watchlist_prices = fetch_qt_prices(watchlist_codes)
    watchlist_html = make_watchlist_html(watchlist_stocks, prices=watchlist_prices)
    wl_names = {s.get('name', '') for s in watchlist_stocks if s.get('name')}
    events_list, senti_score, senti_pos, senti_neg = make_events_html(events, board_members, wl_names)
    events_html = events_list
    rps_html = make_rps_board_html(rps)
    reson_html = build_resonance(mkt, events, watchlist_stocks, rps)
    hotspot_html = make_hotspot_html(events, board_members)

    updated = meta.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    # 统一展示为 MM-DD HH:MM（消除年份/秒位歧义）
    try:
        updated_disp = datetime.strptime(updated[:16], '%Y-%m-%d %H:%M').strftime('%m-%d %H:%M')
    except Exception:
        updated_disp = updated
    stats = meta.get('stats', {})
    alert_count = stats.get('alerts', 0)
    filtered_count = stats.get('filtered', 0)
    event_date = meta.get('date', '')
    # 涨停/跌停统计（market_review dims.limit）
    _lim = ((mkt or {}).get('dims') or {}).get('limit') or {}
    _zt, _dt = _lim.get('zt', 0) or 0, _lim.get('dt', 0) or 0
    zt_stat = ('<span class="stat">涨停 <b style="color:#ff5a5a">%d</b> / 跌停 <b style="color:#2ecc71">%d</b></span>'
               % (_zt, _dt)) if (_zt or _dt) else ''

    # 舆情得分展示（A股红多绿少）
    senti = (senti_score, senti_pos, senti_neg)
    if senti[0] or senti[1] or senti[2]:
        senti_stat = ('<span class="stat" style="color:%s">舆情得分: %s%d（利好%d / 利空%d）</span>'
                      % ('#ff5a5a' if senti[0] > 0 else ('#2ecc71' if senti[0] < 0 else '#e0e0e0'),
                         '+' if senti[0] > 0 else '', senti[0], senti[1], senti[2]))
    else:
        senti_stat = ''

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
    .rps-chip {{ display:inline-block; padding:3px 10px; border-radius:8px; font-size:.78em; margin:3px 4px; cursor:pointer; font-weight:600; }}
    .rps-chip b {{ font-weight:700; margin-left:3px; }}
    .rps-meta {{ font-size:.75em; color:#666; margin-bottom:8px; }}
    .rps-main {{ margin-bottom:12px; }}
    .rps-cols {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
    @media (max-width: 700px) {{ .rps-cols {{ grid-template-columns:1fr; }} }}
    .rps-col-hd {{ font-size:.82em; font-weight:700; margin-bottom:6px; }}
    .rps-row {{ display:flex; align-items:center; gap:8px; padding:5px 8px; margin-bottom:4px; background:rgba(255,255,255,0.03); border-radius:8px; cursor:pointer; font-size:.82em; }}
    .rps-row:hover {{ background:rgba(184,41,255,0.08); }}
    .rps-nm {{ font-weight:600; flex:0 0 auto; }}
    .rps-r60 {{ color:#888; flex:1; }}
    .rps-r60 b {{ color:#e0e0e0; }}
    .rps-chg {{ flex:0 0 auto; font-size:.92em; }}
    .rps-fresh {{ font-weight:700; flex:0 0 auto; font-size:.92em; }}
    .fnl-board {{ margin-bottom:10px; background:rgba(255,255,255,0.03); border-radius:10px; padding:10px 12px; }}
    .fnl-hd {{ display:flex; align-items:center; gap:8px; margin-bottom:6px; flex-wrap:wrap; }}
    .fnl-name {{ font-weight:700; font-size:.9em; cursor:pointer; }}
    .fnl-name:hover {{ color:#ce93d8; }}
    .fnl-badge {{ font-size:.7em; padding:2px 8px; border-radius:10px; font-weight:700; }}
    .fnl-stocks {{ display:flex; flex-wrap:wrap; gap:6px; }}
    .fnl-stock {{ display:inline-flex; align-items:center; gap:5px; background:rgba(184,41,255,0.10); border:1px solid rgba(184,41,255,0.18); border-radius:8px; padding:3px 9px; font-size:.78em; cursor:pointer; }}
    .fnl-stock:hover {{ background:rgba(184,41,255,0.25); }}
    .fnl-src {{ font-size:.68em; padding:1px 6px; border-radius:6px; font-weight:600; }}
{EXTRA_CSS}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>投研信息筛选系统</h1>
      <div class="subtitle">动态股票池 · 实时新闻追踪 · 智能预警</div>
      <div class="stats-bar">
        <span class="stat">数据截至 {updated_disp}</span>
        <span class="stat alert">重大预警: {alert_count}</span>
        <span class="stat">存档事件: {filtered_count}</span>
        {zt_stat}
        {senti_stat}
      </div>
    </header>
    {reson_html}
    <div class="layout">
      <aside>
        <div class="panel">
          <div class="panel-title">自选股票池 ({len(watchlist_stocks)})</div>
          <div class="source-info">来源: {sources_str}</div>
{watchlist_html}
        </div>
{heat_html}
      </aside>
      <main>
{rps_html}{funnel_html}{hotspot_html}
        <div class="panel" id="eventsPanel">
          <div class="panel-title">投研事件追踪 ({len(events)})<span style="font-size:.72em;color:#666;font-weight:400">· 金色高亮=自选相关 · 板块标签可点击联动</span></div>
{events_html}
        </div>
      </main>
    </div>
    <footer>
      <p>数据来源: 东方财富 · 财联社 · 新浪财经 | 每交易日晚 20:00 自动更新 | 行情/资金/快讯延时约15分钟</p>
      <p>动态股票池: 投研事件高频股 + 选股系统推荐（纯数据驱动，无硬编码底仓）</p>
    </footer>
  </div>
  <!-- 通用K线弹窗：点击股票名/代码即可查看K线（美股不弹） -->
  <script src="kline_popup.js?v=20261025b" defer></script>
  {EXTRA_JS}
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

    # 4c. RPS60 主线雷达数据（旧格式数据自动跳过）
    rps_board = build_rps_board()
    if rps_board:
        print(f"  RPS 主线雷达: 基准 {rps_board['asof']} · 主线 {len(rps_board['main'])} · "
              f"新晋 {len(rps_board['fresh'])} · 退潮 {len(rps_board['fading'])}")
    else:
        print("  RPS 主线雷达: sector_rankings 为旧格式，跳过")

    funnel_html = FUNNEL_PANEL

    # 4d. 板块成员表（供事件↔板块联动）：漏斗板块带成员码，其余板块按名称关键词命中
    board_members = {}
    try:
        with open(BASE_DIR / 'sector_funnel.json', encoding='utf-8') as f:
            _fd = json.load(f)
        for b in (_fd.get('boards') or []):
            codes = set(str(s.get('code', '')) for s in (b.get('stocks') or []) if s.get('code'))
            if b.get('name'):
                board_members.setdefault(b['name'], set()).update(codes)
        print(f"  板块成员表: 漏斗板块 {len(_fd.get('boards') or [])} 个")
    except Exception as e:
        print(f"  [warn] sector_funnel.json 读取失败（联动仅按板块名匹配）: {e}")
    try:
        with open(DAILY_PICKS_JSON, encoding='utf-8') as f:
            _dp = json.load(f)
        _sr = _dp.get('sector_rankings') or {}
        _dates = sorted(k for k in _sr if RPS_DATE_RE.match(str(k)))
        if _dates:
            for _r in (_sr[_dates[-1]] or []):
                if _r.get('name'):
                    board_members.setdefault(_r['name'], set())
    except Exception:
        pass

    # 4e. 自选股热度排行（事件提及次数）
    heat_html = make_heat_html(watchlist, dict(ranked))
    if heat_html:
        print("  自选股热度面板: 已生成")

    # 4f. 市况快照（共振结论板 + 涨跌停统计）：market_review.json
    mkt = None
    try:
        with open(BASE_DIR / 'market_review.json', encoding='utf-8') as f:
            _mr = json.load(f)
        if _mr.get('date') == meta.get('date') or (_mr.get('dims') or {}).get('limit'):
            mkt = _mr
            _lim = ((_mr.get('dims') or {}).get('limit') or {})
            print(f"  市况快照: {_mr.get('date')} · 涨停{_lim.get('zt', 0)} / 跌停{_lim.get('dt', 0)} · "
                  f"资金流入第一 {((_mr.get('dims') or {}).get('money') or {}).get('inflow', [{}])[0].get('name', '—')}")
    except Exception as e:
        print(f"  [warn] market_review.json 读取失败（共振板跳过）: {e}")

    # 5. 生成 HTML
    html = generate_html(watchlist, events, meta, rps=rps_board, funnel_html=funnel_html,
                         heat_html=heat_html, board_members=board_members, mkt=mkt)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f"\n  -> {OUTPUT_HTML} ({len(html):,} bytes)")

    # 同步镜像到 vibe-dashboard/ 子目录（历史遗留发布位置，部分书签/旧链接仍指向此处）
    try:
        sub_dir = BASE_DIR / 'vibe-dashboard'
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / 'research.html').write_text(html, encoding='utf-8')
        print(f"  -> {sub_dir / 'research.html'} (镜像副本)")
    except Exception as e:
        print(f"  [warn] 子目录 research.html 镜像失败（不影响根目录）: {e}")

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

        # 同步镜像到 vibe-dashboard/ 子目录（历史遗留发布位置）
        try:
            sub_dir = BASE_DIR / 'vibe-dashboard'
            sub_dir.mkdir(parents=True, exist_ok=True)
            with open(sub_dir / 'research_data.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  -> {sub_dir / 'research_data.json'} (镜像副本)")
        except Exception as e:
            print(f"  [warn] 子目录 research_data.json 镜像失败（不影响根目录）: {e}")

    # 7. GitHub 同步已移至每日钉钉推送的 sync_func.py 统一处理
    # （避免 subprocess 挂起问题；本地文件由 news_sync cron 同步到 GitHub）
    print("[generate_research_html] DONE")


if __name__ == '__main__':
    main()
