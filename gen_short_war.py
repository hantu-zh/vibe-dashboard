#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_short_war.py —— 徐翔式 1~5 日短线「作战记录」选股脚本
=========================================================
把 hantu-zh/vibe-dashboard 现有的短线信号源（威震天回踩 / 益盟强买 / 强势榜 / 盗火线）
作为候选池，叠加腾讯日K线做技术特征（均线多头、近期涨停、反包/N字、量比），
按「题材情绪 / 量价筹码 / 资金面 / 技术形态 / 市场地位 / 风控」六维打分，
输出 short_week.json（连续 5 个交易日 × 每天 开盘/午间/尾盘 三段推荐）。

用法：
  python gen_short_war.py                       # 按当前时间推断时段，写入今天
  python gen_short_war.py --window open         # 指定时段 open|midday|close|all
  python gen_short_war.py --date 20261014       # 指定日期（回补历史用）
  python gen_short_war.py --signals /path/to   # 信号源目录（默认：脚本同目录）
  python gen_short_war.py --out short_week.json # 输出文件
  python gen_short_war.py --no-seed             # 不回填过去 4 个交易日（首跑默认回填）

定时：建议 GitHub Actions 在每个交易日 09:35 / 11:35 / 15:05 各跑一次 --window 对应值，
       脚本自动按日期合并、保留最近 5 天。
"""
import os, sys, json, re, time, datetime, subprocess

# ---------------------------------------------------------------------------
# 网络：优先 curl（沙箱/CI 经系统代理稳定），失败回退 urllib
# ---------------------------------------------------------------------------
def fetch_url(url, timeout=25):
    try:
        r = subprocess.run(['curl', '-sS', '--http1.1', '--max-time', str(timeout), url],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', 'replace')
    except Exception:
        return ''

def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def norm_code(c):
    c = str(c or '').strip().lower()
    return re.sub(r'^[a-z]+', '', c)

def is_st(name):
    return bool(re.search(r'(s{1,2}t|st|退)', str(name or ''), re.I)) or 'ST' in str(name or '').upper()

def board_of(code):
    if code.startswith('688'): return '科创板'
    if code.startswith(('300', '301')): return '创业板'
    if code.startswith(('83', '87', '92', '4')): return '北交所'
    if code.startswith('60'): return '沪市'
    if code.startswith(('00', '002', '003')): return '深市'
    return '其他'

def limit_rate(code):
    return 0.20 if code.startswith(('688', '300', '301')) else 0.10

def dh_kline_to_list(kl):
    """盗火线自带 kline：[{d,o,c,h,l,v}] -> [[date,open,close,high,low,volume]]"""
    try:
        return [[x.get('d', ''), float(x['o']), float(x['c']), float(x['h']), float(x['l']), float(x['v'])]
                for x in kl if x.get('c') is not None]
    except Exception:
        return []

# ---------------------------------------------------------------------------
# 候选池：复用现有信号源
# ---------------------------------------------------------------------------
def gather_candidates(sigdir):
    cands = {}
    def add(code, name, src, blob):
        code = norm_code(code)
        if not re.fullmatch(r'\d{6}', code):
            return
        c = cands.setdefault(code, {'code': code, 'name': str(name or code), 'src': [],
                                    'yimeng': None, 'stock': None, 'wz': None, 'dh': None})
        c['src'].append(src)
        c[src_key(src)] = blob

    wz = load_json(os.path.join(sigdir, 'weizhentian.json'))
    if wz:
        for p in wz.get('picks', []):
            add(p.get('code'), p.get('name'), '威震天回踩', p)

    sb = load_json(os.path.join(sigdir, 'strongbuy_data.json'))
    if sb:
        for p in sb.get('yimeng', []):
            add(p.get('code'), p.get('name'), '益盟强买', p)
        for p in sb.get('stocks', []):
            add(p.get('code'), p.get('name'), '强势榜', p)

    dh = load_json(os.path.join(sigdir, 'daohuoxian_data.json'))
    if dh:
        for p in (dh.get('daohuoxian') or []):
            add(p.get('code'), p.get('name'), '盗火线', p)

    return cands

def src_key(s):
    return {'威震天回踩': 'wz', '益盟强买': 'yimeng', '强势榜': 'stock', '盗火线': 'dh'}[s]

# ---------------------------------------------------------------------------
# 腾讯日K线 + 技术特征
# ---------------------------------------------------------------------------
KL_CACHE = {}

def fetch_kline(code, bars=60):
    if code in KL_CACHE:
        return KL_CACHE[code]
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq' % (code, bars)
    raw = fetch_url(url)
    kl = None
    try:
        d = json.loads(raw)
        node = d['data'].get(code) or (list(d['data'].values())[0] if d.get('data') else None)
        kl = node.get('qfqday') or node.get('day') if node else None
    except Exception:
        kl = None
    KL_CACHE[code] = kl
    return kl

def tech_features(code, kl):
    if not kl or len(kl) < 20:
        return {}
    closes = [float(x[2]) for x in kl]
    vols = [float(x[5]) for x in kl]
    n = len(closes)
    def ma(k):
        return sum(closes[-k:]) / k
    ma5, ma10, ma20 = ma(5), ma(10), ma(20)
    prev, cur = closes[-2], closes[-1]
    lr = limit_rate(code)
    is_limit = cur >= prev * (1 + lr) * 0.995
    lim_cnt = 0
    for i in range(1, min(11, n)):
        if closes[-i] >= closes[-i - 1] * (1 + lr) * 0.995:
            lim_cnt += 1
    v5 = sum(vols[-6:-1]) / 5 if vols[-6:-1] else 0
    vol_ratio = (vols[-1] / v5) if v5 else 0
    ma_bull = (ma5 > ma10 > ma20)
    maxr = max(closes)
    near_high = cur >= maxr * 0.97
    reversal = False
    if n >= 3:
        yd, yyd = closes[-2], closes[-3]
        if yd < yyd and cur > yd * 1.03:
            reversal = True
    return {'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma_bull': ma_bull,
            'is_limit': is_limit, 'lim_cnt': lim_cnt, 'vol_ratio': vol_ratio,
            'near_high': near_high, 'reversal': reversal, 'close': cur, 'prev': prev}

# ---------------------------------------------------------------------------
# 徐翔式六维打分
# ---------------------------------------------------------------------------
def score_pick(c):
    code, name = c['code'], c['name']
    reasons = []
    sc = 0
    ym = c.get('yimeng') or {}
    st = c.get('stock') or {}
    wz = c.get('wz') or {}
    dh = c.get('dh')
    # 主策略来源（用于页面分组多样性）
    srcs = c['src']
    primary = ('威震天回踩' if '威震天回踩' in srcs else
               '益盟强买' if ym else
               '强势榜' if st else '盗火线')

    # ---- 资金面（益盟强买带的真实主力净占比/净流入）----
    mr = ym.get('main_ratio')
    if mr is None:
        mr = ym.get('net_inflow_pct')
    if mr is not None:
        try:
            mr = float(mr)
            if mr >= 8:
                sc += 30; reasons.append('主力净占比+%.0f%%（大资金介入）' % mr)
            elif mr >= 0:
                sc += 18; reasons.append('主力净流入+%.1f%%' % mr)
            else:
                sc += 4; reasons.append('主力小幅流出%.1f%%' % mr)
        except Exception:
            mr = None

    # ---- 题材/市场地位（连板身位、涨停、强势榜标签）----
    cd = ym.get('consec_days')
    try:
        cd = int(cd) if cd is not None else 0
    except Exception:
        cd = 0
    if cd >= 2:
        sc += 16; reasons.append('连板身位%d天（辨识度高）' % cd)
    tag = (st.get('tag') or '')
    if tag == 'newhigh':
        sc += 22; reasons.append('涨幅榜/新高（当日最强）')
    elif tag == 'volume':
        sc += 16; reasons.append('量价齐升')
    elif tag == 'resilient':
        sc += 10; reasons.append('高换手抗跌')
    if '威震天回踩' in c['src']:
        n = wz.get('n')
        try:
            n = int(n)
        except Exception:
            n = None
        if n is not None and 3 <= n <= 8:
            sc += 24; reasons.append('涨停后回踩%d天未破10日线' % n)
        else:
            sc += 14; reasons.append('涨停回踩形态（威震天）')

    # ---- 量价/活跃度（换手率；优先源数据，盗火线用嵌入kline推算）----
    to = None
    for src in (ym, st, wz):
        if src.get('turnover') is not None:
            try:
                to = float(src['turnover']); break
            except Exception:
                pass
    if to is None and dh:
        try:
            last_v = dh['kline'][-1]['v']                 # 手
            mcap = float(dh['mcap_yi']); pr = float(dh['price'])
            if mcap > 0 and pr > 0:
                to = last_v * 100 / (mcap * 1e8 / pr) * 100   # 换手率%
        except Exception:
            pass
    if to is not None:
        if 4 <= to <= 35:
            sc += 12; reasons.append('换手%.1f%%（活跃且未失控）' % to)
        elif to > 35:
            sc += 4; reasons.append('换手%.1f%%（过热）' % to)
        elif to >= 2:
            sc += 6; reasons.append('换手%.1f%%（偏冷）' % to)

    # ---- 盗火线（自带 kline/vol_ratio/score，不再额外联网）----
    dh = c.get('dh')
    if dh:
        dscore = dh.get('score')
        try:
            dscore = float(dscore)
            if dscore >= 70:
                sc += 18; reasons.append('盗火线高分%.0f' % dscore)
            elif dscore >= 40:
                sc += 10; reasons.append('盗火线信号%.0f' % dscore)
        except Exception:
            pass
        dvr = dh.get('vol_ratio')
        try:
            dvr = float(dvr)
            if dvr > 1.3:
                sc += 8; reasons.append('放量(量比%.1f)' % dvr)
        except Exception:
            pass
        dfh = dh.get('dist_from_high')
        try:
            if float(dfh) > -0.05:
                sc += 8; reasons.append('贴近阶段新高')
        except Exception:
            pass
        l3 = dh.get('last3_ret')
        try:
            if float(l3) > 0.1:
                sc += 6; reasons.append('近3日强势')
        except Exception:
            pass

    # ---- 技术形态（优先用盗火线自带 kline，否则拉腾讯日K线）----
    kl = dh_kline_to_list(dh['kline']) if (dh and dh.get('kline')) else fetch_kline(code)
    tf = tech_features(code, kl)
    if tf:
        if tf['ma_bull']:
            sc += 15; reasons.append('均线多头(5>10>20)')
        if tf['lim_cnt'] >= 1:
            sc += 14; reasons.append('近10日有涨停（有人气）')
        if tf['vol_ratio'] and tf['vol_ratio'] > 1.3:
            sc += 10; reasons.append('放量(量比%.1f)' % tf['vol_ratio'])
        elif tf['vol_ratio'] and tf['vol_ratio'] < 0.7 and '威震天回踩' in c['src']:
            sc += 6; reasons.append('缩量回踩(量比%.2f)' % tf['vol_ratio'])
        if tf['near_high']:
            sc += 8; reasons.append('贴近阶段新高')
        if tf['reversal']:
            sc += 8; reasons.append('反包（昨弱今强）')

    reasons = list(dict.fromkeys(reasons))   # 去重
    sc = max(0, min(100, sc))
    stars = max(1, min(5, int(round(sc / 20.0))))

    # ---- 信号标签（优先级）----
    sig = '形态关注'
    if tf and tf['is_limit']:
        sig = '打板/回封'
    elif tf and tf['reversal']:
        sig = '反包弱转强'
    elif '威震天回踩' in c['src']:
        sig = '回踩低吸'
    elif tag == 'newhigh':
        sig = '龙头新高'
    elif tag == 'volume':
        sig = '量价齐升'
    elif cd >= 2:
        sig = '连板接力'
    elif tag == 'resilient':
        sig = '高换手强势'
    elif dh:
        sig = '盗火线蓄势'

    # ---- 介入/目标/止损/仓位（技术位启发式）----
    price = None
    for src in (ym, st, wz):
        if src.get('price') is not None:
            try:
                price = float(src['price']); break
            except Exception:
                pass
    if price is None and tf:
        price = tf['close']
    if price is None:
        price = 0.0

    if '威震天回踩' in c['src'] and tf:
        entry = tf['ma10']
    else:
        entry = price
    tp = {5: 1.10, 4: 1.08, 3: 1.06, 2: 1.05, 1: 1.04}[stars]
    target = entry * tp
    stop = entry * (0.95 if '威震天回踩' in c['src'] else 0.96)
    pos = {5: '15%~20%', 4: '12%~15%', 3: '8%~10%', 2: '5%~8%', 1: '3%~5%'}[stars]

    # 涨跌幅：优先源数据，否则K线推算
    chg = None
    if ym.get('change_pct') is not None:
        try: chg = float(ym['change_pct'])
        except Exception: chg = None
    if chg is None and st.get('change_pct') is not None:
        try: chg = float(st['change_pct'])
        except Exception: chg = None
    if chg is None and tf and tf['prev']:
        chg = (tf['close'] - tf['prev']) / tf['prev'] * 100

    turnover_str = ('%.2f%%' % to) if to is not None else '—'
    main_str = ('%+d%%' % int(round(mr))) if mr is not None else '—'

    return {
        'name': name, 'code': code, 'board': board_of(code), 'margin': False,
        'signal': sig, 'score': round(sc, 1), 'stars': stars,
        'price': ('%.2f' % price) if price else '—',
        'change_pct': round(chg, 2) if chg is not None else 0.0,
        'entry': '%.2f' % entry, 'target': '%.2f' % target, 'stop': '%.2f' % stop,
        'position': pos, 'turnover': turnover_str, 'main_ratio': main_str,
        'reason': '；'.join(reasons) if reasons else '综合短线评分入选',
        '_src': primary,
    }

# ---------------------------------------------------------------------------
# 方法与风控（写入 JSON，页面直接消费）
# ---------------------------------------------------------------------------
METHOD = [
    {'t': '题材与情绪', 'items': ['主线是否清晰、昨日涨停溢价率', '连板高度/空间板、炸板率', '赚钱效应与亏钱效应']},
    {'t': '盘面节奏择时', 'items': ['开盘看竞价超预期/弱转强', '午盘看分歧转一致、回封', '尾盘看抢筹/龙回头/埋伏']},
    {'t': '量价与筹码', 'items': ['放量突破 vs 缩量整理', '换手率、量比、成交额', '流动性过滤避开小票操纵']},
    {'t': '资金面', 'items': ['主力净流入占比', '龙虎榜游资席位', '北向/机构动向']},
    {'t': '技术位置形态', 'items': ['突破关键压力位', '均线多头/首板二板反包', 'N字/空中加油/贴近新高']},
    {'t': '市场地位辨识度', 'items': ['只做龙头或唯一性', '身位优先于跟风', '空间板/最先上板']},
    {'t': '大盘环境', 'items': ['指数趋势与量能', '涨跌家数、冰点/高潮', '决定仓位上限与出手频率']},
    {'t': '风控纪律', 'items': ['破位即走、单票仓位', '不补仓摊薄', '情绪退潮立刻收手']},
]
RISK = {'title': '短线纪律（1–5 日必守）', 'rules': [
    '破位即走：跌破买入价 3%~5% 或破 5 日线，无条件止损，不补仓摊薄。',
    '单票仓位 ≤20%，总仓随大盘情绪调节（高潮 80%、冰点 ≤30%）。',
    '只做计划内：开盘前写好预案，盘中不临时起意追杂毛。',
    '次日不及预期（竞价弱/板块退潮）直接走，持股不超过 5 天。',
    '情绪退潮（炸板率飙升、空间板断板）立刻收手，空仓也是操作。',
    '融资标的（融）波动更大，仓位减半、止损更紧。',
]}

# ---------------------------------------------------------------------------
# 交易日推算 & 主流程
# ---------------------------------------------------------------------------
def prev_trading_days(from_date, k):
    out = []
    d = datetime.date(int(from_date[:4]), int(from_date[4:6]), int(from_date[6:8]))
    while len(out) < k:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5:  # 跳过周末
            out.append(d.strftime('%Y%m%d'))
    return out

def infer_window():
    hm = datetime.datetime.now().hour * 100 + datetime.datetime.now().minute
    if hm < 1130:
        return 'open'
    if hm < 1500:
        return 'midday'
    return 'close'

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--signals', default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'short_week.json'))
    ap.add_argument('--window', default='auto', choices=['auto', 'open', 'midday', 'close', 'all'])
    ap.add_argument('--date', default=datetime.date.today().strftime('%Y%m%d'))
    ap.add_argument('--topn', type=int, default=12)
    ap.add_argument('--seed', dest='seed', action='store_true', default=None)
    ap.add_argument('--no-seed', dest='seed', action='store_false')
    args = ap.parse_args()
    if args.seed is None:
        args.seed = not os.path.exists(args.out)
    if args.window == 'auto':
        args.window = infer_window()

    print('[gen_short_war] signals=%s out=%s window=%s date=%s' % (args.signals, args.out, args.window, args.date))
    cands = gather_candidates(args.signals)
    print('[gen_short_war] candidates=%d' % len(cands))

    all_picks = []
    for code, c in cands.items():
        if is_st(c['name']):
            continue
        try:
            p = score_pick(c)
        except Exception as e:
            print('  skip %s: %s' % (code, e))
            continue
        if p:
            all_picks.append(p)
        time.sleep(0.05)  # 礼貌限速

    # 按策略来源各取前几名，保证多样性（避免单一信号源霸榜）
    groups = {'威震天回踩': [], '益盟强买': [], '强势榜': [], '盗火线': []}
    for p in all_picks:
        gname = p.get('_src')
        groups.setdefault(gname, []).append(p)
    picks = []
    for gname, items in groups.items():
        items.sort(key=lambda x: -x['score'])
        picks += items[:4]
    picks.sort(key=lambda x: -x['score'])
    picks = picks[:max(args.topn, 16)]
    for p in picks:
        p.pop('_src', None)
    print('[gen_short_war] candidates_scored=%d diversified=%d' % (len(all_picks), len(picks)))

    existing = load_json(args.out) or {'days': []}
    days_map = {d['date']: d for d in existing.get('days', [])}
    day = days_map.get(args.date, {'date': args.date, 'sample': False, 'windows': {}})
    day.setdefault('windows', {})
    if args.window == 'all':
        for w in ('open', 'midday', 'close'):
            day['windows'][w] = {'picks': picks}
    else:
        day['windows'][args.window] = {'picks': picks}
    day['sample'] = False
    days_map[args.date] = day

    if args.seed and not existing.get('days'):
        for pd in prev_trading_days(args.date, 4):
            if pd not in days_map:
                days_map[pd] = {'date': pd, 'sample': True,
                                'windows': {w: {'picks': picks} for w in ('open', 'midday', 'close')}}

    dates = sorted(days_map.keys(), reverse=True)[:5]
    days = [days_map[d] for d in dates]
    for i, d in enumerate(days):
        d['label'] = '第1天 · 最新' if i == 0 else ('第5天 · 最旧' if i == len(days) - 1 else '第%d天' % (i + 1))
    synth = any(d.get('sample') for d in days)
    out = {'meta': {'sample': synth, 'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')},
           'method': METHOD, 'days': days, 'risk': RISK}
    with open(args.out, 'w', encoding='utf-8', newline='') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('[gen_short_war] wrote %s (days=%d sample=%s)' % (args.out, len(days), synth))
    print('[gen_short_war] top3:', ' / '.join('%s(%s)%.0f★' % (p['name'], p['code'], p['stars']) for p in picks[:3]))

if __name__ == '__main__':
    main()
