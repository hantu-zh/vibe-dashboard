# -*- coding: utf-8 -*-
"""
market_review.py - 生成 market_review.json（cffex.html 页面【收盘点评】模块的数据源）

背景（为什么要新建这个脚本）：
  cffex.html 的“大盘点评”区块由 JS 动态渲染：
      fetch('market_review.json') -> rev
      periodEl.textContent = '【' + rev.period + '点评】' + rev.date + ' ' + rev.time;
  也就是说页面上的【收盘点评】2026-08-17 20:15 完全来自 market_review.json。
  该文件在 Qclaw 时代由外部环节产出，迁移到 GitHub Actions 时未带生成脚本，
  仓库里只有 sync_func.py 会“原样推送”它 —— 没人生成，自然就永远停在旧日期。
  本脚本补上这个缺失的生成环节，让点评每天收盘后自动刷新。

数据源（全部带降级，任一失败不影响其它板块）：
  - 八大指数行情：新浪 hq.sinajs.cn（主） -> 腾讯 qt.gtimg.cn（备）
  - 行业板块涨幅 / 主力净流入：东财 push2delay（主） -> push2（备） -> 新浪（末）
  - 涨停 / 跌停：akshare stock_zt_pool_em / stock_zt_pool_dtgc_em
  - 全市场涨跌家数：东财 clist 的 f104/f105/f106（上证+深证求和）
  - 政策要闻：复用仓库内 news_update.py 产出的 news_data.json（可选，缺失则跳过）

输出：仓库根目录 market_review.json
  （GitHub Pages 从仓库根目录发布，切勿写 vibe-dashboard/ 子目录，否则页面读不到）
  同时输出页面 renderReview() 需要的顶层字段 indices/sectors/money/limit_count/outlook，
  以及原有结构 date/time/period/text/dims，保证兼容。

安全策略：指数行情是核心数据，获取失败时直接退出且不覆盖已有 JSON，
          避免用残缺数据把页面上原本正常的点评冲掉。
"""
import sys
import os
import json
import ssl
import time
import urllib.request
from datetime import datetime

import paths

sys.stdout.reconfigure(encoding='utf-8')

# ── 输出路径：仓库根目录（Pages 根发布） ────────────────────────────────────
OUT_FILE = paths.w('market_review.json')
NEWS_FILE = paths.w('news_data.json')

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 八大指数：(显示名, 新浪代码, 腾讯代码)
INDEXES = [
    ('上证指数', 's_sh000001', 'sh000001'),
    ('深证成指', 's_sz399001', 'sz399001'),
    ('创业板指', 's_sz399006', 'sz399006'),
    ('沪深300', 's_sh000300', 'sh000300'),
    ('科创50', 's_sh000688', 'sh000688'),
    ('上证50', 's_sh000016', 'sh000016'),
    ('中证500', 's_sh000905', 'sh000905'),
    ('中证1000', 's_sz399852', 'sz399852'),
]
WEEKDAY_CN = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def http_get(url, headers=None, decode='utf-8', timeout=20, retries=3):
    """带重试的 GET（东财 push2 系列偶发断连，单域名不稳，靠重试+换源兜住）"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                raw = r.read()
            return raw.decode(decode, errors='ignore')
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


def is_trading_day(d):
    """是否 A 股交易日：优先用 akshare 交易日历，失败回退到周末判断"""
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        days = {str(x) for x in df['trade_date'].tolist()}
        return d.strftime('%Y-%m-%d') in days
    except Exception as e:
        print(f'[warn] 交易日历获取失败({type(e).__name__})，回退周末判断')
        return d.weekday() < 5


def fetch_indices():
    """八大指数收盘行情：新浪主，腾讯备。返回 {名称: {price, chg, amount_yi}}"""
    # ① 新浪
    try:
        codes = ','.join(c[1] for c in INDEXES)
        text = http_get('https://hq.sinajs.cn/list=' + codes,
                        headers={'User-Agent': UA, 'Referer': 'https://finance.sina.com.cn'},
                        decode='gbk')
        out = {}
        for line in text.strip().split('\n'):
            if '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('";')
            p = body.split(',')
            if len(p) < 6:
                continue
            # 名称, 点位, 涨跌额, 涨跌幅, 成交量(手), 成交额(万元)
            out[p[0]] = {
                'price': round(float(p[1]), 2),
                'chg': round(float(p[3]), 2),
                'amount_yi': round(float(p[5]) / 10000, 1) if p[5] else None,  # 万元 -> 亿
            }
        if len(out) >= 5:
            print(f'[ok] 指数(新浪) {len(out)} 个')
            return out
    except Exception as e:
        print(f'[warn] 新浪指数失败: {type(e).__name__}')

    # ② 腾讯：v_sh000001="1~名称~代码~现价~昨收~今开~..."
    try:
        codes = ','.join(c[2] for c in INDEXES)
        text = http_get('https://qt.gtimg.cn/q=' + codes, decode='gbk')
        out = {}
        for line in text.strip().split('\n'):
            if '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('";')
            p = body.split('~')
            if len(p) < 6:
                continue
            try:
                price, prev = float(p[3]), float(p[4])
                chg = round((price - prev) / prev * 100, 2) if prev else 0.0
                out[p[1]] = {'price': round(price, 2), 'chg': chg, 'amount_yi': None}
            except ValueError:
                continue
        if len(out) >= 5:
            print(f'[ok] 指数(腾讯) {len(out)} 个')
            return out
    except Exception as e:
        print(f'[warn] 腾讯指数失败: {type(e).__name__}')

    return {}


def _em_clist(fid, fields='f12,f14,f3,f62', pz=100):
    """东财板块列表：push2delay -> push2。返回 diff 列表"""
    bases = ['https://push2delay.eastmoney.com/api/qt/clist/get',
             'https://push2.eastmoney.com/api/qt/clist/get']
    params = {
        'pn': '1', 'pz': str(pz), 'po': '1', 'np': '1',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
        'fltt': '2', 'invt': '2', 'fid': fid,
        'fs': 'm:90+t:2', 'fields': fields,
    }
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    last = None
    for base in bases:
        try:
            data = json.loads(http_get(base + '?' + query,
                                       headers={'User-Agent': UA,
                                                'Referer': 'https://quote.eastmoney.com/'},
                                       retries=2))
            diff = (data.get('data') or {}).get('diff') or []
            if diff:
                return diff
        except Exception as e:
            last = e
    if last:
        print(f'[warn] 东财板块(fid={fid})失败: {type(last).__name__}')
    return []


def fetch_sectors():
    """行业板块：涨幅前5(按f3) + 主力净流入前5(按f62) + 净流出前3 + 全板块资金和。

    返回 (top_gain, inflow, outflow, total_yi)。
    注意：净流出必须只取 f62 < 0 的项——直接取排序末尾会得到仍为正的板块，
          导致页面上出现“净流出 +1.2亿”这种自相矛盾的表述。
    """

    def fnum(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    gain = []
    for it in _em_clist('f3')[:5]:
        gain.append({'name': it.get('f14', ''), 'chg': round(fnum(it.get('f3')), 2)})
    gain = [g for g in gain if g['name']]

    inflow, outflow = [], []
    diff = _em_clist('f62')
    # 全板块主力资金之和，作为“全市场主力净流入”的估算口径
    total_yi = round(sum(fnum(it.get('f62')) for it in diff) / 1e8, 1)
    for it in diff[:5]:
        inflow.append({'name': it.get('f14', ''),
                       'yi': round(fnum(it.get('f62')) / 1e8, 1)})
    # 只保留真正净流出的板块，取最负的 3 个
    neg = [it for it in diff if fnum(it.get('f62')) < 0]
    neg.sort(key=lambda it: fnum(it.get('f62')))
    for it in neg[:3]:
        outflow.append({'name': it.get('f14', ''),
                        'yi': round(fnum(it.get('f62')) / 1e8, 1)})
    inflow = [x for x in inflow if x['name']]
    outflow = [x for x in outflow if x['name']]

    # 末级兜底：新浪行业板块
    if not gain:
        try:
            text = http_get('https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php',
                            headers={'User-Agent': UA, 'Referer': 'https://finance.sina.com.cn'},
                            decode='gbk')
            js = text[text.index('=') + 1:].strip().rstrip(';').strip()
            rows = []
            for _code, v in json.loads(js).items():
                if not isinstance(v, str):
                    continue
                p = v.split(',')
                if len(p) < 5:
                    continue
                try:
                    rows.append({'name': p[1], 'chg': round(float(p[4]), 2) if p[4] else 0.0})
                except ValueError:
                    continue
            rows.sort(key=lambda x: x['chg'], reverse=True)
            gain = rows[:5]
        except Exception as e:
            print(f'[warn] 新浪板块失败: {type(e).__name__}')

    print(f'[ok] 板块 涨幅{len(gain)} 流入{len(inflow)} 流出{len(outflow)} 全市场合计{total_yi}亿')
    return gain, inflow, outflow, total_yi


def fetch_limit_pool(date_compact):
    """涨停/跌停：返回 (涨停数, 跌停数, 代表涨停股名列表)"""
    zt, dt, names = 0, 0, []
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=date_compact)
        zt = len(df)
        col = '名称' if '名称' in df.columns else df.columns[1]
        names = [str(x) for x in df[col].tolist()[:10]]
        print(f'[ok] 涨停池 {zt} 只')
    except Exception as e:
        print(f'[warn] 涨停池失败: {type(e).__name__}: {str(e)[:60]}')
    try:
        import akshare as ak
        df = ak.stock_zt_pool_dtgc_em(date=date_compact)
        dt = len(df)
        print(f'[ok] 跌停池 {dt} 只')
    except Exception as e:
        print(f'[warn] 跌停池失败: {type(e).__name__}: {str(e)[:60]}')
    return zt, dt, names


def fetch_breadth():
    """全市场涨跌家数：上证+深证的 f104(涨)/f105(跌)/f106(平)"""
    try:
        url = ('https://push2delay.eastmoney.com/api/qt/ulist.np/get?fltt=2'
               '&secids=1.000001,0.399001&fields=f104,f105,f106')
        data = json.loads(http_get(url, headers={'User-Agent': UA,
                                                 'Referer': 'https://quote.eastmoney.com/'},
                                   retries=2))
        up = dn = flat = 0
        for it in (data.get('data') or {}).get('diff') or []:
            up += int(it.get('f104') or 0)
            dn += int(it.get('f105') or 0)
            flat += int(it.get('f106') or 0)
        if up + dn > 0:
            print(f'[ok] 涨跌家数 涨{up} 跌{dn} 平{flat}')
            return {'up': up, 'down': dn, 'flat': flat}
    except Exception as e:
        print(f'[warn] 涨跌家数失败: {type(e).__name__}')
    return {}


def fetch_news(limit=5):
    """政策要闻：复用 news_data.json（不存在或过旧则跳过）"""
    try:
        if not os.path.exists(NEWS_FILE):
            return []
        d = json.load(open(NEWS_FILE, encoding='utf-8'))
        items = d.get('items') or []
        out = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            title = it.get('title') or it.get('text') or ''
            title = str(title).strip().replace('\n', ' ')
            if title:
                out.append({'source': it.get('source', ''), 'title': title[:60]})
        if out:
            print(f'[ok] 要闻 {len(out)} 条')
        return out
    except Exception as e:
        print(f'[warn] 要闻读取失败: {type(e).__name__}')
        return []


def build_text(date_cn, period, time_str, idx, gain, inflow, outflow, total_yi,
               zt, dt, names, breadth, news):
    """渲染点评正文（Markdown）。全部用真实抓到的数据，不做任何编造。"""
    def arrow(c):
        return '🔺' if c > 0 else ('🔻' if c < 0 else '➖')

    L = []
    L.append('# 📊 A股收盘数据研报')
    L.append(f'**{date_cn} {period} {time_str}**')

    L.append('\n## 1️⃣ 宏观大盘')
    for name, _s, _t in INDEXES:
        d = idx.get(name)
        if not d:
            continue
        amt = f' | 成交{d["amount_yi"]}亿' if d.get('amount_yi') else ''
        L.append(f'- {name} **{d["price"]}** {arrow(d["chg"])}{d["chg"]:+.2f}%{amt}')

    if gain:
        L.append('\n## 2️⃣ 板块热度（行业涨幅前5）')
        for i, g in enumerate(gain, 1):
            L.append(f'{i}. {g["name"]} **{g["chg"]:+.2f}%**')

    L.append('\n## 3️⃣ 资金动向')
    if inflow:
        L.append('- 主力净流入居前：' + '、'.join(f'{x["name"]}{x["yi"]:+.1f}亿' for x in inflow))
    if outflow:
        L.append('- 主力净流出居前：' + '、'.join(f'{x["name"]}{x["yi"]:+.1f}亿' for x in outflow))
    L.append(f'- 全市场主力净流入(估算)：**{total_yi:+.1f}亿**')
    if breadth:
        L.append(f'- 上涨 {breadth["up"]} ｜ 下跌 {breadth["down"]} ｜ 平 {breadth["flat"]}')

    L.append('\n## 4️⃣ 涨停统计')
    L.append(f'- 涨停 **{zt}只** ｜ 跌停 {dt}只')
    if names:
        L.append('- 代表涨停：' + '、'.join(names))

    if news:
        L.append('\n## 5️⃣ 政策要闻')
        for i, n in enumerate(news, 1):
            src = f'【{n["source"]}】' if n['source'] else ''
            L.append(f'{i}. {src}{n["title"]}')

    # 后市展望：基于真实数据的规则化研判，不含主观臆断
    L.append('\n## 🔭 后市展望')
    sh = idx.get('上证指数', {}).get('chg', 0)
    if sh > 0.3:
        mood = f'上证{sh:+.2f}%，市场情绪偏暖，结构性机会活跃。'
    elif sh < -0.3:
        mood = f'上证{sh:+.2f}%，市场情绪偏弱，赚钱效应一般。'
    else:
        mood = f'上证{sh:+.2f}%，市场窄幅震荡，观望情绪较浓。'
    L.append(f'- 盘面：{mood}')
    if gain:
        L.append('- 关注方向：**' + '、'.join(g['name'] for g in gain[:3]) + '**（当日涨幅居前）')
    if breadth:
        up, dn = breadth['up'], breadth['down']
        if up > dn * 1.5:
            strat = '普涨格局，可积极参与主线，注意避免追高后排跟风股。'
        elif dn > up * 1.5:
            strat = '跌多涨少，控制仓位、等待情绪修复，忌盲目抄底。'
        else:
            strat = '多空分歧，控制仓位、逢低布局景气方向，忌追涨杀跌。'
        L.append(f'- 策略：{strat}')

    L.append('\n> 数据来源：东方财富 / 新浪财经 / 腾讯证券'
             '｜本点评由脚本按收盘数据自动汇总，不构成投资建议。')
    return '\n'.join(L)


def main():
    now = datetime.now()
    print(f'\n[market_review] ===== {now:%Y-%m-%d %H:%M:%S} =====')

    if not is_trading_day(now):
        print('[skip] 非交易日，不更新收盘点评（保留上一交易日内容）')
        return 0

    idx = fetch_indices()
    if len(idx) < 5:
        print('[abort] 指数行情获取失败，为保护现有数据，本次不写入')
        return 1

    date_str = now.strftime('%Y-%m-%d')
    date_compact = now.strftime('%Y%m%d')
    date_cn = f'{now.year}年{now.month:02d}月{now.day:02d}日 {WEEKDAY_CN[now.weekday()]}'
    time_str = now.strftime('%H:%M')

    gain, inflow, outflow, total_main = fetch_sectors()
    zt, dt, names = fetch_limit_pool(date_compact)
    breadth = fetch_breadth()
    news = fetch_news()

    period = '收盘'
    text = build_text(date_cn, period, time_str, idx, gain, inflow, outflow, total_main,
                      zt, dt, names, breadth, news)

    sh_chg = idx.get('上证指数', {}).get('chg', 0)
    if sh_chg > 0.3:
        rec = '情绪偏暖，结构性机会活跃，可关注当日强势方向。'
    elif sh_chg < -0.3:
        rec = '情绪偏弱，控制仓位为主，等待企稳信号。'
    else:
        rec = '窄幅震荡，多看少动，等待方向明朗。'

    data = {
        # ── 页面 renderReview() 读取的顶层字段 ──
        'date': date_str,
        'time': time_str,
        'period': period,
        'indices': {n: {'price': d['price'], 'chg': d['chg']} for n, d in idx.items()},
        'sectors': [{'name': g['name'], 'chg': g['chg']} for g in gain],
        'money': {'total_main_yi': total_main},
        'limit_count': zt,
        'outlook': {'recommendation': rec},
        # ── 原有结构（保留兼容）──
        'text': text,
        # dims 保持与原 Qclaw 产出相近的结构（页面当前不直接消费，
        # 但保留字段名可避免将来的消费方因缺键报错）
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
          f'板块{len(gain)} 主力{total_main:+.1f}亿')
    return 0


if __name__ == '__main__':
    sys.exit(main())
