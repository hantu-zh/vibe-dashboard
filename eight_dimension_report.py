# -*- coding: utf-8 -*-
"""
eight_dimension_report.py - A股八大维度每日收盘研报
维度：宏观 / 政策 / 资金 / 板块 / 大宗 / 日历 / 外围 / 策略
数据来源：东方财富实时行情 + 本地 news_data.json / daily_picks.json
工作日 15:45 运行，生成 Markdown 推送钉钉，同步到 daily_picks.json
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import os
import json
import time
import ssl
import datetime
import urllib.request
import urllib.parse

# ─── 路径配置 ─────────────────────────────────────────────────────────────
REPO_DIR = os.path.dirname(os.path.abspath(__file__))

DINGTALK_ENV = os.path.join(REPO_DIR, '.env.dingtalk')
DAILY_PICKS = os.path.join(REPO_DIR, 'daily_picks.json')
NEWS_DATA = os.path.join(REPO_DIR, 'news_data.json')
SECTOR_RANKINGS = os.path.join(REPO_DIR, 'sector_rankings')

# ─── SSL / 网络 ───────────────────────────────────────────────────────────
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UT = 'b2884a393a59ad64002292a3e90d46a5'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# ─── 指数列表 ───────────────────────────────────────────────────────────────
INDICES = [
    ('1.000001', '上证指数'),
    ('0.399001', '深证成指'),
    ('0.399006', '创业板指'),
    ('1.000300', '沪深300'),
    ('1.000688', '科创50'),
    ('1.000016', '上证50'),
    ('0.399905', '中证500'),
    ('0.399673', '中证1000'),
]

# ─── 大宗商品（国内期货+现货）──────────────────────────────
COMMODITIES = [
    ('CF', '棉花'),
    ('CU', '沪铜'),
    ('AL', '沪铝'),
    ('ZN', '沪锌'),
    ('NI', '沪镍'),
    ('AU', '沪金'),
    ('AG', '沪银'),
    ('RU', '沪橡胶'),
    ('RB', '螺纹钢'),
    ('HC', '热卷'),
    ('J',  '焦炭'),
    ('JM', '焦煤'),
    ('I',  '铁矿石'),
    ('PTA', 'PTA'),
    ('MA', '甲醇'),
    ('Y',  '豆油'),
    ('M',  '豆粕'),
]

# ─── 海外指数 ──────────────────────────────────────────────────────────────
OVERSEAS = [
    ('.INX', '标普500'),
    ('.IXIC', '纳斯达克'),
    ('.DJI', '道琼斯'),
    ('^HSCE', '恒生国企'),
    ('^N225', '日经225'),
]


# ══════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════════════

def is_trading_day():
    today = datetime.date.today()
    if today.weekday() >= 5:
        return False
    try:
        import chinese_calendar
        return chinese_calendar.is_workday(today)
    except ImportError:
        return True


def em_fetch(url, retries=2):
    """东方财富 JSON GET，失败回退 base"""
    last_err = None
    for attempt in range(retries):
        for base in ('https://push2delay.eastmoney.com',
                     'https://push2.eastmoney.com'):
            full = url.replace('__BASE__', base) if '__BASE__' in url else base + url
            try:
                req = urllib.request.Request(
                    full,
                    headers={'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/',
                             'Accept': 'application/json'})
                with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                    raw = r.read()
                enc = 'utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'utf-8'
                return json.loads(raw.decode(enc))
            except Exception as e:
                last_err = e
                time.sleep(1.2)
                continue
    print(f'[WARN] em_fetch 失败: {last_err!r}')
    return None


def fetch_news_keywords(keywords, n=20):
    """从 news_data.json 过滤含关键词的新闻"""
    try:
        with open(NEWS_DATA, 'r', encoding='utf-8') as f:
            data = json.load(f)
        items = data.get('items', [])
        matched = []
        for it in items:
            text = (it.get('title') or '') + (it.get('text') or '')
            hits = [kw for kw in keywords if kw in text]
            if hits:
                matched.append({
                    'title': it.get('title') or it.get('text', '')[:60],
                    'text': (it.get('text') or '')[:150],
                    'source': it.get('source', ''),
                    'time_str': it.get('time_str', ''),
                    'keywords': hits[:3],
                })
        matched.sort(key=lambda x: len(x['keywords']), reverse=True)
        return matched[:n]
    except Exception as e:
        print(f'[WARN] 读取新闻失败: {e!r}')
        return []


# ══════════════════════════════════════════════════════════════════════════
# 维度 1 — 宏观
# ══════════════════════════════════════════════════════════════════════════

def dim_macro():
    """宏观：大盘指数收盘数据"""
    rows = []
    for secid, name in INDICES:
        url = (f'__BASE__/api/qt/stock/get?ut={UT}&fltt=2&invt=2'
               f'&fields=f43,f44,f45,f46,f60,f170,f169&secid={secid}')
        d = em_fetch(url)
        if not d or 'data' not in d or not d['data']:
            continue
        it = d['data']
        try:
            price = float(it.get('f43') or 0)
            chg   = float(it.get('f170') or 0)
            amt   = float(it.get('f46') or 0)
            pe    = float(it.get('f162') or 0) if it.get('f162') not in (None, '') else None
            pb    = float(it.get('f167') or 0) if it.get('f167') not in (None, '') else None
        except (TypeError, ValueError):
            continue
        rows.append({'name': name, 'price': price, 'chg': chg,
                     'amount_yi': round(amt, 0), 'pe': pe, 'pb': pb})
    print(f'[OK] 宏观/指数 {len(rows)} 条')
    return rows


# ══════════════════════════════════════════════════════════════════════════
# 维度 2 — 政策
# ══════════════════════════════════════════════════════════════════════════

POLICY_KW = ['政策', '监管', '央行', '财政部', '发改委', '证监会', '银保监',
             '降准', '加息', 'LPR', 'MLF', '逆回购', '财政', '货币',
             '刺激', '救市', '救经济', '稳增长', '扩内需', '外资',
             '北向', 'QFII', 'RQFII', '注册制', '退市', 'IPO暂停']

def dim_policy():
    """政策：相关快讯摘要"""
    items = fetch_news_keywords(POLICY_KW, n=15)
    print(f'[OK] 政策快讯 {len(items)} 条')
    return items


# ══════════════════════════════════════════════════════════════════════════
# 维度 3 — 资金
# ══════════════════════════════════════════════════════════════════════════

def dim_money():
    """资金：行业板块净流入 + 全市场涨跌停统计"""
    # 行业板块（主力净流入 top/bottom）
    url = (f'__BASE__/api/qt/clist/get?pn=1&pz=100&po=1&np=1&ut={UT}'
           f'&fltt=2&invt=2&fid=f62&fs=m:90+t:2&fields=f12,f14,f3,f62,f184')
    d = em_fetch(url)
    sectors = []
    if d and d.get('data'):
        for it in d['data'].get('diff', []):
            try:
                chg = float(it.get('f3') or 0)
                net = float(it.get('f62') or 0) / 1e8
            except:
                continue
            sectors.append({'name': it.get('f14', ''), 'chg': chg, 'net_yi': round(net, 1)})
    sectors.sort(key=lambda x: x['net_yi'], reverse=True)

    # 全市场涨跌停（样本）
    fs = 'm:0+t:2,m:1+t:2,m:0+t:3,m:1+t:3'
    first = em_fetch(f'__BASE__/api/qt/clist/get?pn=1&pz=100&po=1&np=1&ut={UT}'
                     f'&fltt=2&invt=2&fid=f3&fs={fs}&fields=f12,f14,f3,f62')
    total = (first or {}).get('data', {}).get('total', 0) or 0
    pages = min(max(1, (total + 99) // 100), 30)
    up10 = up20 = dn10 = dn20 = 0
    pos = neg = flat = 0
    net_sum = 0.0
    top_up = []; top_dn = []

    for pn in range(1, pages + 1):
        dd = em_fetch(f'__BASE__/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&ut={UT}'
                      f'&fltt=2&invt=2&fid=f3&fs={fs}&fields=f12,f14,f3,f62')
        if not dd or not dd.get('data'):
            break
        diff = dd['data'].get('diff', [])
        if not diff:
            break
        for it in diff:
            f3 = it.get('f3')
            f62 = it.get('f62')
            try:
                f3 = float(f3) if f3 is not None else None
            except:
                f3 = None
            try:
                net_sum += float(f62 or 0) / 1e8
            except:
                pass
            if f3 is None:
                continue
            if 9.8 <= f3 <= 11:
                up10 += 1
                if len(top_up) < 8: top_up.append(it.get('f14', ''))
            elif 19.8 <= f3 <= 21:
                up20 += 1
                if len(top_up) < 8: top_up.append(it.get('f14', ''))
            elif -11 <= f3 <= -9.8:
                dn10 += 1
                if len(top_dn) < 5: top_dn.append(it.get('f14', ''))
            elif -21 <= f3 <= -19.8:
                dn20 += 1
                if len(top_dn) < 5: top_dn.append(it.get('f14', ''))
            if f3 > 0: pos += 1
            elif f3 < 0: neg += 1
            else: flat += 1

    result = {
        'sectors': sectors,
        'total_main_yi': round(net_sum, 1),
        'limit_up': up10 + up20, 'limit_down': dn10 + dn20,
        'up': pos, 'down': neg, 'flat': flat,
        'top_up_names': top_up, 'top_dn_names': top_dn,
    }
    print(f'[OK] 资金 净流入{result["total_main_yi"]}亿 涨停{result["limit_up"]} 跌停{result["limit_down"]}')
    return result


# ══════════════════════════════════════════════════════════════════════════
# 维度 4 — 板块
# ══════════════════════════════════════════════════════════════════════════

def dim_sectors():
    """板块：行业涨跌幅排行"""
    url = (f'__BASE__/api/qt/clist/get?pn=1&pz=100&po=1&np=1&ut={UT}'
           f'&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f62,f184')
    d = em_fetch(url)
    rows = []
    if d and d.get('data'):
        for it in d['data'].get('diff', []):
            try:
                chg = float(it.get('f3') or 0)
                net = float(it.get('f62') or 0) / 1e8
            except:
                continue
            rows.append({'name': it.get('f14', ''), 'chg': chg, 'net_yi': round(net, 1)})
    rows.sort(key=lambda x: x['chg'], reverse=True)
    print(f'[OK] 板块 {len(rows)} 条')
    return rows


# ══════════════════════════════════════════════════════════════════════════
# 维度 5 — 大宗
# ══════════════════════════════════════════════════════════════════════════

COMMODITY_KW = ['原油', '黄金', '白银', '铜', '铝', '锌', '镍', '钢铁',
                '煤炭', '铁矿石', '螺纹钢', '大豆', '豆粕', '玉米', '小麦',
                '棉花', '白糖', '橡胶', 'PTA', '甲醇', '原油期货', '布伦特',
                'WTI', 'COMEX']

def dim_commodity():
    """大宗：相关新闻快讯"""
    items = fetch_news_keywords(COMMODITY_KW, n=10)
    # 尝试抓取东方财富大宗商品行情
    futures = []
    for sym, name in COMMODITIES[:8]:
        url = (f'https://hq.sinajs.cn/list=nf_{sym}')
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA,
                             'Referer': 'https://finance.sina.com.cn/'})
            with urllib.request.urlopen(req, timeout=8, context=CTX) as r:
                raw = r.read().decode('gbk', 'replace')
            parts = raw.split('"')[1].split(',') if '"' in raw else []
            if len(parts) >= 4:
                price = parts[0]
                chg_pct = parts[3]
                futures.append({'name': name, 'symbol': sym, 'price': price, 'chg_pct': chg_pct})
        except Exception:
            pass
    print(f'[OK] 大宗商品 期货{futures.__len__()} 条 新闻{len(items)} 条')
    return {'futures': futures, 'news': items}


# ══════════════════════════════════════════════════════════════════════════
# 维度 6 — 日历
# ══════════════════════════════════════════════════════════════════════════

CALENDAR_KW = ['业绩预告', '业绩快报', '年报', '季报', '半年报', '分红',
               '高送转', 'IPO', '申购', '上市', '解禁', '减持', '增持',
               '回购', '定增', '配股', '股权激励', '要约收购']

def dim_calendar():
    """日历：近期重要事件快讯"""
    items = fetch_news_keywords(CALENDAR_KW, n=10)
    today = datetime.date.today()
    # 构造近7天日历占位
    week_dates = [(today + datetime.timedelta(days=i)).strftime('%m/%d %a') for i in range(7)]
    print(f'[OK] 日历快讯 {len(items)} 条')
    return {'week_dates': week_dates, 'events': items}


# ══════════════════════════════════════════════════════════════════════════
# 维度 7 — 外围
# ══════════════════════════════════════════════════════════════════════════

OVERSEAS_KW = ['美股', '纳斯达克', '标普', '道琼斯', '美联储', '港股',
               '恒生', '日经', '欧股', '亚太', '美债', '美元', '人民币',
               '汇率', '美联储加息', '美联储降息', '非农', 'CPI', 'PPI',
               'Fed', 'FOMC', 'ECB', '欧央行']

def dim_overseas():
    """外围：海外市场相关新闻"""
    items = fetch_news_keywords(OVERSEAS_KW, n=15)
    print(f'[OK] 外围快讯 {len(items)} 条')
    return items


# ══════════════════════════════════════════════════════════════════════════
# 策略综合研判
# ══════════════════════════════════════════════════════════════════════════

def build_strategy(indices, sectors, money, commodity, calendar, overseas):
    """综合研判：输出策略建议"""
    sh = next((x for x in indices if x['name'] == '上证指数'), None)
    sh_chg = sh['chg'] if sh else 0.0
    sh_price = sh['price'] if sh else 0.0

    # 情绪判断
    lu = money['limit_up']; ld = money['limit_down']
    net = money['total_main_yi']
    pos = money['up']; neg = money['down']

    if lu >= 100 and sh_chg >= 1.5 and net > 0:
        mood = '市场情绪极度亢奋，量价齐升，做多动能充沛'
    elif lu >= 50 and sh_chg >= 0.5 and net > 0:
        mood = '市场情绪偏暖，结构性机会活跃，赚钱效应良好'
    elif lu >= 20 and sh_chg > 0:
        mood = '市场震荡偏强，局部热点活跃，整体仍需谨慎'
    elif ld >= 20 or sh_chg <= -1.0:
        mood = '市场情绪偏弱，抛压较重，防御为主'
    elif sh_chg < 0:
        mood = '市场承压回调，观望情绪浓厚，等待企稳信号'
    else:
        mood = '市场窄幅震荡，方向不明，谨慎操作'

    # 主线板块
    top3 = [s['name'] for s in sectors[:5] if s['chg'] > 0][:3]
    if not top3:
        top3 = ['暂无明确主线']
    # 资金共振板块
    money共振 = [s['name'] for s in sectors if s['chg'] > 0.5 and s['net_yi'] > 0][:3]
    if not money共振:
        money共振 = [s['name'] for s in sectors if s['chg'] > 0][:2] or ['暂无']

    # 策略
    if sh_chg >= 1.0 and net > 50:
        strat = '量价配合良好，主力积极入场，可适当提高仓位参与主线，注意及时止盈。'
    elif sh_chg >= 0.5 and net > 0:
        strat = '震荡偏强，控制仓位，逢低布局景气方向，忌追高位跟风股。'
    elif sh_chg < -0.5 or ld >= 15:
        strat = '承压明显，降低仓位，观望为主，等待指数企稳再行布局。'
    elif net < -100:
        strat = '主力大幅净流出，资金面偏谨慎，轻仓或空仓应对，等待底部信号。'
    else:
        strat = '方向不明，轻仓观望，等待午后信号明确后再行操作。'

    # 风险提示
    risk_items = [s['name'] for s in sectors if s['chg'] < -2][:2]
    risk = f'注意 {risk_items[0]} 等板块回调风险。' if risk_items else '注意高位股补跌风险。'

    return {
        'mood': mood,
        'main_line': top3,
        'money共振': money共振,
        'strategy': strat,
        'risk': risk,
        'sh_chg': round(sh_chg, 2),
        'sh_price': round(sh_price, 2),
    }


# ══════════════════════════════════════════════════════════════════════════
# Markdown 报告构建
# ══════════════════════════════════════════════════════════════════════════

WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

def fmt_yi(v):
    try:
        v = float(v)
    except:
        return '0亿'
    if abs(v) >= 10:
        return f'{v:.0f}亿'
    return f'{v:.1f}亿'

def build_markdown(indices, sectors, money, commodity, calendar, overseas, strategy, today):
    dt = today.strftime('%Y年%m月%d日')
    wk = WEEKDAY_CN[today.weekday()]
    dt_full = f'{dt} {wk}'

    lines = []
    lines.append(f'# 📊 A股八大维度收盘研报')
    lines.append(f'**{dt_full} 收盘 {today.strftime("%H:%M")}**')
    lines.append('')

    # ── 维度 1：宏观大盘 ──
    lines.append('## 1️⃣ 宏观大盘')
    for ix in indices:
        arrow = '🔺' if ix['chg'] > 0 else ('🔻' if ix['chg'] < 0 else '➖')
        pe_str = f' | PE={ix["pe"]:.1f}' if ix.get('pe') else ''
        pb_str = f' PB={ix["pb"]:.2f}' if ix.get('pb') else ''
        amt_str = f' | 成交{ix["amount_yi"]:.0f}亿' if ix.get('amount_yi') else ''
        lines.append(f"- {ix['name']} **{ix['price']:.2f}** {arrow}{ix['chg']:+.2f}%{amt_str}{pe_str}{pb_str}")
    lines.append('')

    # ── 维度 2：政策 ──
    lines.append('## 2️⃣ 政策要闻')
    if overseas_policy := [x for x in calendar.get('events', []) if any(k in x.get('title', '') for k in ['监管', '政策', '央行', '财政', '救市'])]:
        for i, it in enumerate(overseas_policy[:6], 1):
            src = f"【{it['source']}】" if it.get('source') else ''
            lines.append(f"{i}. {src}{it['title']}")
    else:
        # 从外围快讯中提取政策相关
        pol_items = overseas[:5] if overseas else []
        if not pol_items:
            lines.append('- 今日暂无重大政策快讯')
        else:
            for i, it in enumerate(pol_items, 1):
                src = f"【{it.get('source', '')}】"
                lines.append(f"{i}. {src}{it['title'][:60]}")
    lines.append('')

    # ── 维度 3：资金 ──
    lines.append('## 3️⃣ 资金动向')
    net_in = [s for s in sectors if s['net_yi'] > 0][:5]
    net_out = sorted([s for s in sectors if s['net_yi'] < 0], key=lambda x: x['net_yi'])[:3]
    if net_in:
        ins = '、'.join(f"{s['name']}+{fmt_yi(s['net_yi'])}" for s in net_in)
        lines.append(f"- 主力净流入居前：{ins}")
    if net_out:
        outs = '、'.join(f"{s['name']}{fmt_yi(s['net_yi'])}" for s in net_out)
        lines.append(f"- 主力净流出居前：{outs}")
    sign = '+' if money['total_main_yi'] >= 0 else ''
    lines.append(f"- 全市场主力净流入：**{sign}{money['total_main_yi']:.0f}亿**")
    lines.append(f"- 涨停 **{money['limit_up']}只** ｜ 跌停 {money['limit_down']}只")
    lines.append(f"- 上涨 {money['up']} ｜ 下跌 {money['down']} ｜ 平 {money['flat']}")
    if money['top_up_names']:
        lines.append(f"- 代表涨停：{'、'.join(money['top_up_names'][:6])}")
    lines.append('')

    # ── 维度 4：板块 ──
    lines.append('## 4️⃣ 板块动向')
    if sectors:
        top5 = sectors[:5]
        lines.append('**涨幅前5：**')
        for i, s in enumerate(top5, 1):
            arrow = '🔺' if s['chg'] > 0 else '🔻'
            net_str = f'(净{fmt_yi(s["net_yi"])})' if s.get('net_yi', 0) != 0 else ''
            lines.append(f"{i}. {s['name']} **{arrow}{s['chg']:+.2f}%** {net_str}")
        if len(sectors) > 5:
            bot3 = sectors[-3:]
            lines.append('> 领跌：' + ' / '.join(f"{s['name']} {s['chg']:+.2f}%" for s in bot3 if s['chg'] < 0))
    else:
        lines.append('- 暂无板块数据')
    lines.append('')

    # ── 维度 5：大宗商品 ──
    lines.append('## 5️⃣ 大宗商品')
    futures = commodity.get('futures', [])
    if futures:
        for f in futures[:8]:
            arrow = '🔺' if f.get('chg_pct', '0').startswith('+') else ('🔻' if f.get('chg_pct') and f['chg_pct'] not in ('0', '0.00', '') else '➖')
            lines.append(f"- {f['name']} {f.get('price','--')} {arrow}{f.get('chg_pct','--')}%")
    else:
        lines.append('- 期货行情暂无数据')
    if commodity.get('news'):
        lines.append('')
        lines.append('**相关快讯：**')
        for it in commodity['news'][:4]:
            src = f"【{it['source']}】" if it.get('source') else ''
            lines.append(f"- {src}{it['title'][:50]}")
    lines.append('')

    # ── 维度 6：日历 ──
    lines.append('## 6️⃣ 重要日历')
    events = calendar.get('events', [])
    if events:
        for i, it in enumerate(events[:8], 1):
            src = f"【{it.get('source', '')}】"
            lines.append(f"{i}. {src}{it['title'][:55]}")
    else:
        lines.append('- 今日暂无重要日历事件')
    lines.append('')

    # ── 维度 7：外围市场 ──
    lines.append('## 7️⃣ 外围市场')
    if overseas:
        for i, it in enumerate(overseas[:8], 1):
            src = f"【{it.get('source', '')}】"
            lines.append(f"{i}. {src}{it['title'][:55]}")
    else:
        lines.append('- 今日暂无外围重要资讯')
    lines.append('')

    # ── 维度 8：策略 ──
    lines.append('## 8️⃣ 综合策略')
    sh = next((x for x in indices if x['name'] == '上证指数'), None)
    sh_price = sh['price'] if sh else 0
    lines.append(f"- **上证指数** {sh_price:.2f} ({strategy['sh_chg']:+.2f}%)")
    lines.append(f"- **市场情绪**：{strategy['mood']}")
    lines.append(f"- **关注方向**：{'、'.join(strategy['main_line'])}")
    lines.append(f"- **资金共振**：{'、'.join(strategy['money共振'])}")
    lines.append(f"- **操作策略**：{strategy['strategy']}")
    lines.append(f"- **风险提示**：{strategy['risk']}")
    lines.append('')
    lines.append('> 数据来源：东方财富实时行情 | 本研报仅供参考，不构成投资建议。')

    return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 钉钉推送 & 数据保存
# ══════════════════════════════════════════════════════════════════════════

def send_dingtalk(webhook, title, text):
    payload = json.dumps({'msgtype': 'markdown',
                          'markdown': {'title': title, 'text': text}},
                         ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        webhook, data=payload,
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            resp = json.loads(r.read().decode('utf-8', 'replace'))
        if resp.get('errcode') == 0:
            print('[OK] 钉钉推送成功')
            print('DINGTALK_RESULT=OK')
            return True
        print(f'[ERR] 钉钉返回: {resp}')
        print('DINGTALK_RESULT=FAIL')
        return False
    except Exception as e:
        print(f'[ERR] 钉钉推送异常: {e!r}')
        print('DINGTALK_RESULT=FAIL')
        return False


def load_dingtalk_webhook():
    env = os.environ.get('DINGTALK_WEBHOOK') or os.environ.get('DINGTALK_TOKEN')
    if env:
        return env
    try:
        with open(DINGTALK_ENV, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('DINGTALK_WEBHOOK='):
                    return line.strip().split('=', 1)[1].strip()
    except Exception as e:
        print(f'[ERR] 读取钉钉配置失败: {e!r}')
    return None


def save_report(report_obj, today):
    date_str = today.strftime('%Y-%m-%d')
    # 备份
    try:
        import shutil
        bak = DAILY_PICKS + '.bak_' + today.strftime('%Y%m%d_%H%M%S')
        shutil.copyfile(DAILY_PICKS, bak)
    except Exception as e:
        print(f'[WARN] 备份失败: {e!r}')

    try:
        with open(DAILY_PICKS, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f'[ERR] 读取 daily_picks.json 失败: {e!r}')
        data = {}

    # 八大维度研报
    data['eight_dimension_report'] = report_obj
    data.setdefault(date_str, {})['八大维度研报'] = report_obj
    data['market_review'] = report_obj  # 同步更新 market_review

    try:
        with open(DAILY_PICKS, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'[OK] 已写入 daily_picks.json (eight_dimension_report + {date_str}/八大维度研报)')
        return True
    except Exception as e:
        print(f'[ERR] 写入 daily_picks.json 失败: {e!r}')
        return False


# ══════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════

def main():
    dry = '--dry-run' in sys.argv
    today = datetime.datetime.now()
    print(f'\n{"="*60}')
    print(f'八大维度收盘研报 [{today.strftime("%Y-%m-%d %H:%M")}]')
    print(f'{"="*60}')

    if not is_trading_day():
        print('[INFO] 非交易日，退出')
        return

    # ── 各维度数据采集 ──
    print('\n[STEP 1] 宏观大盘...')
    indices = dim_macro()

    print('[STEP 2] 政策要闻...')
    policy = dim_policy()

    print('[STEP 3] 资金动向...')
    money = dim_money()

    print('[STEP 4] 板块动向...')
    sectors = dim_sectors()

    print('[STEP 5] 大宗商品...')
    commodity = dim_commodity()

    print('[STEP 6] 重要日历...')
    calendar = dim_calendar()

    print('[STEP 7] 外围市场...')
    overseas = dim_overseas()

    if not indices:
        print('[ERR] 指数数据为空，终止')
        return

    # ── 策略研判 ──
    print('[STEP 8] 综合策略研判...')
    strategy = build_strategy(indices, sectors, money, commodity, calendar, overseas)

    # ── 构建报告 ──
    print('[STEP 9] 构建 Markdown 报告...')
    markdown = build_markdown(indices, sectors, money, commodity, calendar, overseas, strategy, today)

    # ── 构建结构化对象 ──
    report_obj = {
        'date': today.strftime('%Y-%m-%d'),
        'time': today.strftime('%H:%M'),
        'period': '收盘',
        'text': markdown,
        'dims': {
            'macro':    {'indices': indices},
            'policy':   {'items': policy},
            'money':    money,
            'sectors':  sectors,
            'commodity': commodity,
            'calendar': calendar,
            'overseas': overseas,
            'strategy': strategy,
        }
    }

    print('\n' + '-' * 40)
    print(markdown)
    print('-' * 40)

    # ── 保存 ──
    print('\n[STEP 10] 保存数据...')
    saved = save_report(report_obj, today)
    if not saved:
        print('[WARN] 本地保存失败，仍尝试推送')

    # ── 钉钉推送 ──
    if dry:
        print('\n[DRY-RUN] 跳过钉钉推送')
        return

    webhook = load_dingtalk_webhook()
    if not webhook:
        print('[ERR] 未找到钉钉 Webhook，跳过推送')
        return

    title = f'📊 八大维度收盘研报 {today.strftime("%Y-%m-%d")}'
    send_dingtalk(webhook, title, markdown)

    print(f'\n{"="*60}')
    print(f'八大维度研报生成完毕 [{today.strftime("%Y-%m-%d %H:%M")}]')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
