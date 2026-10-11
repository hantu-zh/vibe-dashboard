# -*- coding: utf-8 -*-
r"""
board_rps.py — 基于板块日K的多周期 RPS（相对强度）计算

为什么重做
──────────
原实现（RPS_thermal_dingtalk.calculate_rps）：
    RPS = (1 - rank/N)*100,  rank 由「**当日**涨跌幅」排序,  N = 当日抓到的板块数
三个致命问题：
  1. 只取到「当日涨幅榜前 100」—— 东财 clist 单页硬上限 100，
     pz=900 也被截断，于是下跌板块全部缺席，样本本身就是赢家池；
  2. 分母每天在 49/100 之间漂移，跨日不可比（实测相邻日名单 Jaccard 平均 0.14）；
  3. 单日涨跌幅噪声极大，无法反映中期趋势。

本模块改为经典 RPS 口径：
    RPS_N = (1 - rank_N / N) * 100
    rank_N = 按「过去 N 个交易日累计涨幅」在**固定板块池**内的横截面排序
    N(分母) = 固定板块池大小，恒定不变 → 跨日可比

信号字段
────────
rps5 / rps20 / rps60 / rps120 : 各周期 RPS（主用于 rps60）
trend      : 真趋势 = RPS60(t) - RPS60(t-5)，五档 ↑↑ ↑ → ↓ ↓↓
trend_delta: 上述差值
fresh      : 新晋 = RPS20 - RPS120 的差值（短期强于长期，正在启动）
"""

import json
import re

# ── 口径参数 ────────────────────────────────────────────────────────────
PERIODS = (5, 20, 60, 120)
MAIN_PERIOD = 60            # 主 RPS 周期
TREND_LOOKBACK = 5          # trend = RPS60 的 5 日变化
FRESH_THRESHOLD = 60        # RPS20 - RPS120 >= 此值 → 标记「新晋」
FADING_THRESHOLD = -60      # <= 此值 → 标记「退潮」

# 综合分权重：(周期, 权重)。用于榜单排序，避免「RPS60 高但正在崩」的板块霸榜。
SCORE_WEIGHTS = ((60, 0.5), (20, 0.3), (120, 0.2))

_LEVEL_RE = re.compile(r'[ⅠⅡⅢⅣⅤⅥⅦ]+$')


def display_name(name):
    """去掉东财层级后缀用于展示：地面兵装Ⅲ -> 地面兵装"""
    return _LEVEL_RE.sub('', (name or '').strip()).strip() or name


# ── 数据装载 ────────────────────────────────────────────────────────────
def load_board_kline(path):
    """读 ai_analysis_board_kline.json -> {code: {'name':..., 'kline': [[d,o,c,l,h,v], ...]}}"""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('stocks') or {}


def build_series(stocks):
    """构建 {code: {date: close}} 与 {code: name}；跳过无效行"""
    close_map, name_map = {}, {}
    for code, v in stocks.items():
        name = (v.get('name') or code).strip()
        d = {}
        for row in (v.get('kline') or []):
            if len(row) < 3:
                continue
            try:
                d[str(row[0])] = float(row[2])      # [d, o, c, l, h, v] -> 索引 2 = 收盘
            except (TypeError, ValueError):
                continue
        if len(d) >= 2:
            close_map[code] = d
            name_map[code] = name
    return close_map, name_map


def all_dates(close_map):
    s = set()
    for d in close_map.values():
        s.update(d)
    return sorted(s)


# ── 层级去重 ────────────────────────────────────────────────────────────
def dedup_pool(name_map):
    """东财行业板块层级重复（地面兵装Ⅱ / 地面兵装Ⅲ 等）。
    按「去掉罗马数字后缀」分组，每组保留名称最短者（通常是无后缀的那个）。
    返回保留的 code 列表（顺序稳定）。"""
    groups = {}
    for code in sorted(name_map):
        key = _LEVEL_RE.sub('', name_map[code]).strip()
        groups.setdefault(key, []).append(code)
    keep = []
    for key in groups:
        members = groups[key]
        keep.append(sorted(members, key=lambda c: (len(name_map[c]), c))[0])
    return keep


# ── 核心：横截面 RPS ────────────────────────────────────────────────────
def _price_at(series, dates, idx, max_stale=0):
    """取 dates[idx] 的收盘价。max_stale>0 时允许向前回看（板块K抓取缺口兜底）。"""
    if idx < 0:
        return None
    for k in range(max_stale + 1):
        j = idx - k
        if j < 0:
            break
        v = series.get(dates[j])
        if v is not None:
            return v
    return None


def _rps_rows(close_map, name_map, dates, asof, period, pool, max_stale=0):
    """计算某周期在某基准日的横截面 RPS。返回 (rows, base_date)；样本不足返回 (None, None)。"""
    i = dates.index(asof)
    if i - period < 0:
        return None, None
    base = dates[i - period]
    rows = []
    for code in pool:
        d = close_map.get(code)
        if not d:
            continue
        c1 = _price_at(d, dates, i, max_stale)
        c0 = _price_at(d, dates, i - period, max_stale)
        if c1 is None or c0 is None or c0 <= 0:
            continue
        rows.append({'code': code, 'name': name_map[code], 'ret': c1 / c0 - 1})
    n = len(rows)
    if n == 0:
        return None, None
    rows.sort(key=lambda x: x['ret'], reverse=True)
    for rk, r in enumerate(rows, 1):
        r['rank'] = rk
        r['rps'] = int(round((1 - rk / n) * 100))
    return rows, base


def _strength(rps):
    if rps >= 90:
        return '极强'
    if rps >= 80:
        return '强势'
    if rps >= 70:
        return '偏强'
    if rps >= 50:
        return '中等'
    if rps >= 40:
        return '偏弱'
    return '弱势'


def _trend_label(delta):
    if delta >= 10:
        return '↑↑'
    if delta >= 3:
        return '↑'
    if delta > -3:
        return '→'
    if delta > -10:
        return '↓'
    return '↓↓'


def _rps_map(close_map, name_map, dates, asof, period, pool, max_stale=0):
    rows, _ = _rps_rows(close_map, name_map, dates, asof, period, pool, max_stale)
    return {r['code']: r['rps'] for r in rows} if rows else {}


def compute(close_map, name_map, asof=None, pool=None, periods=PERIODS, main=MAIN_PERIOD,
            max_stale=5):
    """计算基准日 asof 的全量多周期 RPS。

    分母恒定保证：先锁定「所有周期基期日都能取到收盘价」的板块作为固定池
    （允许 max_stale 个交易日的前值回看，兜底板块K抓取缺口），再用这个池
    去算每个周期的横截面排名 —— 任意周期、任意基准日的分母都相同，
    RPS 跨周期、跨日都可比。

    返回 (rows, info)：
      rows  —— 按综合分降序，每项含
               code/name/disp_name/rank/score/rps/rps5/rps20/rps60/rps120/
               change_pct(当日)/change5/change20/change60/change120/
               trend/trend_delta/strength/fresh
      info  —— {'asof','pool_size','universe','base_dates','main_period','coverage'}
    """
    dates = all_dates(close_map)
    if not dates:
        raise ValueError('无可用数据')
    if asof is None:
        asof = dates[-1]
    if asof not in dates:
        raise ValueError('基准日 %s 不在数据范围内' % asof)
    if pool is None:
        pool = dedup_pool(name_map)

    i = dates.index(asof)
    prev = dates[i - 1] if i >= 1 else None
    trend_asof = dates[i - TREND_LOOKBACK] if i - TREND_LOOKBACK >= 0 else None

    # ── 锁定固定池 ──
    # 标准：板块必须在「基准日 + 各周期基期 + 趋势回看及其基期」都能取到收盘价
    # （允许 max_stale 个交易日的前值回看，用于兜底板块K抓取缺口）。
    # 固定池一旦确定，各周期排名共用同一分母 → RPS 跨周期、跨日可比。
    probe = {i}
    for p in periods:
        if i - p >= 0:
            probe.add(i - p)
    if trend_asof:
        j = dates.index(trend_asof)
        probe.add(j)
        for p in periods:
            if j - p >= 0:
                probe.add(j - p)

    fixed = [c for c in pool
             if c in close_map
             and all(_price_at(close_map[c], dates, k, max_stale) is not None for k in probe)]
    if len(fixed) < 20:                       # 数据过于稀疏时放宽，只要求当期可取值
        fixed = [c for c in pool
                 if c in close_map and _price_at(close_map[c], dates, i, max_stale) is not None]
    if not fixed:
        raise ValueError('基准日 %s 无任何可用板块' % asof)

    # 各周期明细（分母统一 = len(fixed)）
    detail = {}
    base_dates = {}
    for p in periods:
        rows, base = _rps_rows(close_map, name_map, dates, asof, p, fixed, max_stale)
        if rows is None:
            continue
        base_dates[p] = base
        for r in rows:
            detail.setdefault(r['code'], {})[p] = r

    main_codes = [c for c in detail if main in detail[c]]
    if not main_codes:
        raise ValueError('主周期 %d 无有效样本' % main)

    # 趋势：主周期 RPS 的 N 日变化（同一固定池，保证分母一致）
    trend_prev = {}
    if trend_asof:
        trend_prev = _rps_map(close_map, name_map, dates, trend_asof, main, fixed, max_stale)

    rows = []
    for code in main_codes:
        dd = detail[code]
        d = close_map[code]
        rps_main = dd[main]['rps']
        rps20 = dd.get(20, {}).get('rps')
        rps120 = dd.get(120, {}).get('rps')
        delta = (rps_main - trend_prev[code]) if code in trend_prev else 0
        c_now = _price_at(d, dates, i, max_stale)
        c_prev = _price_at(d, dates, i - 1, max_stale) if i >= 1 else None
        chg_pct = round((c_now / c_prev - 1) * 100, 2) if (c_now and c_prev) else 0.0
        rows.append({
            'code': code,
            'name': name_map[code],
            'disp_name': display_name(name_map[code]),
            'rps': rps_main,
            'rps5': dd.get(5, {}).get('rps'),
            'rps20': rps20,
            'rps60': dd.get(60, {}).get('rps'),
            'rps120': rps120,
            'change100': dd[main]['ret'],                       # 主周期区间涨幅(小数)
            'change_pct': chg_pct,
            'change5': _pct(dd.get(5, {}).get('ret')),
            'change20': _pct(dd.get(20, {}).get('ret')),
            'change60': _pct(dd.get(60, {}).get('ret')),
            'change120': _pct(dd.get(120, {}).get('ret')),
            'trend_delta': delta,
            'trend': _trend_label(delta),
            'strength': _strength(rps_main),
            'fresh': (rps20 - rps120) if (rps20 is not None and rps120 is not None) else 0,
        })

    # 综合分排序：多周期共振优先，单一周期极端（如 RPS60 高但 RPS20 崩）自动降位
    for r in rows:
        num = den = 0.0
        for p, w in SCORE_WEIGHTS:
            v = r.get('rps%d' % p)
            if v is None:
                continue
            num += v * w
            den += w
        r['score'] = round(num / den, 2) if den else 0.0
    rows.sort(key=lambda r: (-r['score'], -r['rps']))
    for rk, r in enumerate(rows, 1):
        r['rank'] = rk

    info = {
        'asof': asof,
        'pool_size': len(pool),
        'universe': len(fixed),                 # = 恒定分母
        'base_dates': {str(k): v for k, v in base_dates.items()},
        'main_period': main,
        'trend_asof': trend_asof,
        'max_stale': max_stale,
        # 固定池里基准日当天有真实数据（未走前值回看）的比例
        'coverage': round(sum(1 for c in fixed if asof in close_map[c]) / len(fixed), 3),
    }
    return rows, info


def _pct(v):
    return round(v * 100, 2) if v is not None else None


# ── 输出：兼容旧 sector_rankings 结构 ──────────────────────────────────
def to_sector_rankings(rows, info, keep_days=2):
    """转成 daily_picks.json 的 sector_rankings 结构 {date: [ ... ]}。
    每条保留旧字段（rank/name/code/change_pct/rps/trend/strength）
    并追加多周期扩展字段，下游（rps.html / index.html / 钉钉）可渐进使用。"""
    compact = []
    for r in rows:
        compact.append({
            'rank': r['rank'],
            'name': r['disp_name'],
            'code': r['code'],
            'change_pct': r['change_pct'],
            'rps': r['rps'],
            'trend': {'↑↑': '↑', '↑': '↑', '→': '→', '↓': '↓', '↓↓': '↓'}[r['trend']],
            'strength': r['strength'],
            # ── 扩展字段 ──
            'score': r['score'],
            'rps5': r['rps5'], 'rps20': r['rps20'], 'rps60': r['rps60'], 'rps120': r['rps120'],
            'change5': r['change5'], 'change20': r['change20'],
            'change60': r['change60'], 'change120': r['change120'],
            'trend_label': r['trend'],
            'trend_delta': r['trend_delta'],
            'fresh': r['fresh'],
            'source': 'eastmoney',
            'is_full': True,
            'sector_count': info['universe'],
            'rps_period': info['main_period'],
        })
    return {info['asof']: compact}


def build_report(rows, info, topn=20):
    """命令行查看用"""
    out = []
    out.append('基准日 %s | 固定板块池 %d | 有效样本 %d' % (info['asof'], info['pool_size'], info['universe']))
    out.append('各周期基准日: %s' % info['base_dates'])
    out.append('综合分权重: %s' % (SCORE_WEIGHTS,))
    out.append('')
    out.append('%-4s %-18s %5s %5s %5s %5s %7s %6s | %7s %7s | %-3s %5s %5s'
               % ('#', '板块', '5d', '20d', '60d', '120d', '综合分', '当日', '20日涨', '60日涨', '趋', 'Δ60', '新晋'))
    for r in rows[:topn]:
        out.append('%-4d %-18s %5s %5s %5s %5s %7.1f %+5.2f%% | %+6.1f%% %+6.1f%% | %-3s %+5d %5d'
                   % (r['rank'], r['disp_name'], r['rps5'], r['rps20'], r['rps60'], r['rps120'],
                      r['score'], r['change_pct'], r['change20'] or 0, r['change60'] or 0,
                      r['trend'], r['trend_delta'], r['fresh']))
    out.append('')
    out.append('=== 新晋榜（RPS20 - RPS120 >= %d，短期刚启动）===' % FRESH_THRESHOLD)
    fresh = sorted(rows, key=lambda r: r['fresh'], reverse=True)[:10]
    for r in fresh:
        if r['fresh'] < FRESH_THRESHOLD:
            break
        out.append('   %-18s RPS20=%-3s RPS120=%-3s 差=%+3d | 20日 %+.1f%%'
                   % (r['disp_name'], r['rps20'], r['rps120'], r['fresh'], r['change20'] or 0))
    out.append('')
    out.append('=== 退潮榜（<= %d，长期强但近期走弱）===' % FADING_THRESHOLD)
    fade = sorted(rows, key=lambda r: r['fresh'])[:10]
    for r in fade:
        if r['fresh'] > FADING_THRESHOLD:
            break
        out.append('   %-18s RPS20=%-3s RPS120=%-3s 差=%+3d | 20日 %+.1f%%'
                   % (r['disp_name'], r['rps20'], r['rps120'], r['fresh'], r['change20'] or 0))
    return '\n'.join(out)


if __name__ == '__main__':
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else '_board_kline.json'
    asof = sys.argv[2] if len(sys.argv) > 2 else None
    stocks = load_board_kline(src)
    cm, nm = build_series(stocks)
    pool = dedup_pool(nm)
    rows, info = compute(cm, nm, asof=asof, pool=pool)
    print(build_report(rows, info))
