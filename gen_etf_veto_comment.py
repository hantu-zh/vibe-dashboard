# -*- coding: utf-8 -*-
"""生成 etf_veto.html「近期点评」的自动快照。

读取仓库根目录的真实数据（融资/指数/ETF信号/ERP），复用 veto_engine.compute()
的同一套判定口径，输出 etf_veto_comment.json。前端同源 fetch 后覆盖静态兜底点评。

输出顶层 date = 上海时区今天，供 _verify_data.py 数据闸门校验。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veto_engine

SHANGHAI = timezone(timedelta(hours=8))
DATA_DIR = veto_engine.DATA_DIR
OUT = os.path.join(DATA_DIR, 'etf_veto_comment.json')
N = veto_engine.N


def load(name):
    with open(os.path.join(DATA_DIR, name), encoding='utf-8') as f:
        return json.load(f)


def bottom_days(signal, asof):
    """当前 bottom 区连续天数；now.state 才是权威，marks 仅用于计数。"""
    if signal.get('now', {}).get('state') != 'bottom':
        return 0
    marks = set((signal.get('marks') or {}).get('bottom') or [])
    d = datetime.strptime(asof, '%Y-%m-%d')
    n = 0
    while d.strftime('%Y-%m-%d') in marks:
        n += 1
        d -= timedelta(days=1)
    return n


def pct(v, digits=2):
    return '数据缺失' if v is None else ('%+.{}f%%'.format(digits) % v)


def main():
    r = veto_engine.compute(DATA_DIR)
    last = r['last']
    streak = r['fin_streak']
    fin_pct = last['fin_pct']
    idx_pct = last['idx_pct']
    close = r['closes'][-1]
    ma = r['ma']
    below = [n for n in (20, 60, 120) if ma[n] is not None and close < ma[n]]
    ma_text = ('MA20/60/120 全下方' if len(below) == 3 else
               '全上方' if not below else
               '/'.join('MA%d' % n for n in below) + ' 下方')

    signal = load('signal.json')
    bond = load('bond_stock_gap.json')
    sig_now = signal.get('now') or {}
    sig_date = sig_now.get('date') or signal.get('generated_at', '')[:10]
    sig_state = sig_now.get('state', 'unknown')
    sig_days = bottom_days(signal, sig_date)
    fin_positive = fin_pct is not None and fin_pct > 0
    stale = any(r['stale'][k]['stale'] for k in ('margin', 'hs300')) or bool(r['bad_weeks'])
    week = '{}W{:02d}'.format(r['weeks'][-1][0], r['weeks'][-1][1])

    if stale:
        action = ('数据缺失/陈旧 → 维持上一周状态，不用旧数据下新判定'
                  if not r['veto_now'] else '否决已在上一周触发 → 冻结加仓，数据恢复后再复核')
        one_line = '数据没到位，这一周不新下结论——保护不会凭空解除。'
    elif r['veto_now']:
        action = ('否决层触发：冻结加仓，仓位只可持平或下移；'
                  '减不减由你自己的回撤纪律决定')
        one_line = '融资与指数同周向下、且连降已够 N=4 周，系统在踩刹车。'
    elif sig_state == 'bottom' and sig_days >= 4 and not fin_positive:
        action = '第5条 4 周期限触发：退回防御档（左侧信号超期）'
        one_line = '左侧信号已在 bottom 区待满 4 周、融资未转正，时间到了就退回防御。'
    else:
        action = '否决未触发、无其它明确指向 → 默认层：维持当前档位，不动'
        one_line = '信号没给出新指向之前，"不动"本身就是答案。'

    if r['veto_now'] or (sig_state == 'bottom' and sig_days >= 4 and not fin_positive):
        stance = '冻结加仓 / 防御档（30%—40%）'
    else:
        stance = '防御档（30%—40%）维持不动'

    gap_weeks = max(0, N - streak)
    fin_line = (u'融资连降重新累计（当前 <b>{}</b> 周，距 N={} 还差 <b>{}</b> 周）且指数同周走弱 → VETO 才会再次触发'
                .format(streak, N, gap_weeks))
    if fin_positive:
        fin_line = u'融资周环比<b>已转正</b>（{}）→ 关注是否持续，持续转正才进入仲裁层'.format(escape(pct(fin_pct)))
    sig_line = (u'ETF 左侧信号 <b>{}</b>（{}）；{} → 第5条 4 周期限{}'
                .format(escape(sig_state), escape(sig_date),
                        (u'bottom 区已 <b>{}</b> 个交易日'.format(sig_days)
                         if sig_state == 'bottom' else u'未进 bottom 区'),
                        u'计时中' if sig_state == 'bottom' and sig_days < 4 else u'当前不成立'))

    items = [
        u'<b>当前状态：</b>{}（{}）沪深300 收 <b>{}</b>（{}），处 <b>{}</b>；'
        u'融资周末值口径 <b>{}</b>，连续净降 <b>{} 周</b>；'
        u'双确认 {} → VETO <b>{}</b>。'
        .format(escape(week), escape(r['idx_last_date']), escape('%.2f' % close),
                escape(pct(idx_pct)), escape(ma_text), escape(pct(fin_pct)),
                streak, u'同周成立' if (fin_pct is not None and idx_pct is not None
                                      and fin_pct < 0 and idx_pct < 0) else u'未同向',
                u'触发' if r['veto_now'] else u'未触发'),
        u'<b>当前档位：</b>{}——{}。'.format(escape(stance), escape(action)),
        u'<b>接下来盯两件事：</b>① {}；② {}。'.format(fin_line, sig_line),
        u'<b>一句话：</b>{}'.format(escape(one_line)),
    ]

    asof = (u'数据截至：融资 {}（T+1）· 沪深300 {} · ETF信号 {} · ERP {}（建模）。'
            u'点评由生成器自动重写；历史复盘段保留人工撰写。'
            .format(r['fin_last_date'], r['idx_last_date'], sig_date,
                    bond.get('updated', 'N/A')))

    now = datetime.now(SHANGHAI)
    out = {
        'date': now.strftime('%Y-%m-%d'),
        'generated_at': now.strftime('%Y-%m-%d %H:%M'),
        'asof': {
            'margin': r['fin_last_date'],
            'hs300': r['idx_last_date'],
            'signal': sig_date,
            'erp': bond.get('updated', ''),
        },
        'status': {
            'week': week,
            'veto': bool(r['veto_now']),
            'fin_pct': fin_pct,
            'fin_streak': streak,
            'idx_pct': idx_pct,
            'idx_close': close,
            'ma_below': below,
            'signal_state': sig_state,
            'signal_days': sig_days,
            'stale': bool(stale),
        },
        'title': u'近期点评（自动生成于 {} · 全部基于真实序列）'.format(now.strftime('%Y-%m-%d %H:%M')),
        'badge': u'自动生成 · 数据截至 {}'.format(r['idx_last_date']),
        'items': items,
        'source': asof,
    }
    with open(OUT, 'w', encoding='utf-8', newline='') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    print('[etf_veto_comment] %s | VETO=%s | streak=%d | signal=%s/%d'
          % (out['generated_at'], out['status']['veto'], streak, sig_state, sig_days))


if __name__ == '__main__':
    main()
