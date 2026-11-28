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
from signal_meta import stamp

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


def md(d):
    """2026-11-19 -> 11-19，用于页面叙述中的短日期。"""
    return '%d-%d' % (int(d[5:7]), int(d[8:10])) if d else 'N/A'


def week_span(y, w):
    """ISO 周 -> (周一, 周日)，用于展示末周区间。"""
    mon = datetime.strptime('%04d-W%02d-1' % (y, w), '%G-W%V-%u')
    return mon.strftime('%Y-%m-%d'), (mon + timedelta(days=6)).strftime('%Y-%m-%d')


def build_kv(r, signal, bond, sig_date, sig_state, sig_days, week):
    """生成「按 key 覆盖 DOM」的文案表。

    只包含客观数字/日期/状态；历史复盘统计（如 20对9错）与策展性叙述
    口径与引擎不统一，保留页面人工撰写，不在此覆盖。
    前端 fetch 成功后按 [data-kv="key"] 逐项替换 innerHTML，失败则用内联兜底。
    """
    fin_pct = r['last']['fin_pct']
    idx_pct = r['last']['idx_pct']
    streak = r['fin_streak']
    prev = r['records'][-2]
    close = r['closes'][-1]
    below = [n for n in (20, 60, 120) if r['ma'][n] is not None and close < r['ma'][n]]
    ma_text = ('MA20/60/120 全下方' if len(below) == 3 else
               '全上方' if not below else
               '/'.join('MA%d' % n for n in below) + ' 下方')
    fin_last, idx_last = r['fin_last_date'], r['idx_last_date']
    t1_missing = fin_last < idx_last
    wmon, _ = week_span(*r['weeks'][-1])
    wsun = idx_last  # 末周按各自可用末日展示，不用 ISO 周日
    gap = max(0, N - streak)
    same_down = (fin_pct is not None and idx_pct is not None
                 and fin_pct < 0 and idx_pct < 0)
    now = signal.get('now') or {}
    pr, flow20 = now.get('pr'), now.get('flow20')
    erp_hs = (bond.get('hs300') or {}).get('data') or []
    erp_bd = (bond.get('bond2') or {}).get('data') or []
    erp_a = erp_hs[-1] if erp_hs else ['N/A', 'N/A']
    erp_b = erp_bd[-1] if erp_bd else ['N/A', 'N/A']
    erp_up = bond.get('updated', 'N/A')

    kv = {}
    # —— 顶部时间戳对齐 ——
    kv['stamp'] = u'已更新至线上最新（{} 拉取）'.format(
        datetime.now(SHANGHAI).strftime('%Y-%m-%d'))
    kv['asof_fin'] = u'融资余额 <b>截至 {}</b>{}'.format(
        md(fin_last), u'（T+1，{} 未出）'.format(md(idx_last)) if t1_missing else '')
    kv['asof_idx'] = u'沪深300 <b>截至 {}</b>'.format(md(idx_last))
    kv['asof_erp'] = u'ERP <b>截至 {}</b> · 建模值'.format(md(erp_up))
    kv['asof_sig'] = u'ETF净申赎 <b>截至 {}</b>'.format(md(sig_date))
    kv['asof_rule'] = (
        u'融资余额为 T+1 公布：{} 的数据要到下一同步周期才出现，因此末周（{}~{}）的'
        u'融资周末值暂取 {}，<b>该周判定为数据尾部不完整</b>，{} 数据到位后需重跑引擎确认。'
        u'判定按各组数据<b>各自可用末日</b>计算，不做强行对齐。'
        .format(md(idx_last), md(wmon), md(wsun), md(fin_last), md(idx_last))
        if t1_missing else
        u'各组数据末日已对齐（融资 {} / 指数 {}），判定按各自可用末日计算，不做强行对齐。'
        .format(md(fin_last), md(idx_last)))

    # —— 三层仲裁状态徽章 ——
    if r['veto_now']:
        kv['arb_badge'] = u'否决层已触发 → 冻结加仓（只可持平/下移）'
    elif sig_state == 'bottom' and sig_days >= 4 and not (fin_pct is not None and fin_pct > 0):
        kv['arb_badge'] = u'第5条 4 周期限触发 → 退回防御档'
    else:
        kv['arb_badge'] = u'否决未触发 → 默认层（防御档维持不动）'

    # —— 图表徽章 ——
    kv['chart_badge'] = u'真实数据 · 截至 {}/{}'.format(md(fin_last), md(idx_last))

    # —— 否决层质量校验 ——
    kv['veto_badge'] = (u'已触发 · 连降计数 {} 周 ≥ N={}'.format(streak, N)
                        if r['veto_now'] else
                        u'未触发 · 连降计数 {} 周 / N={}'.format(streak, N))
    pp = prev['fin_pct']
    if pp is not None and pp > 0:
        chain = (u'当周融资 <b class="num">{}</b> 微升，<b>连续净降链条断裂</b>（归零重计）'
                 .format(escape(pct(pp))))
    elif pp is None:
        chain = u'上一周融资数据缺失，连降链条无法确认'
    else:
        chain = u'当周融资 <b class="num">{}</b>，连降链条延续'.format(escape(pct(pp)))
    kv['fin_val'] = (
        u'按<b>交易周</b>取周末值：{} {}；末周（{}~{}）融资 <b class="num">{}</b>{}'
        u'→ 连降计数 <b class="num">{} 周</b>，距 N={} 还差 <b>{}</b> 周'
        .format(md(prev['date']), chain, md(wmon), md(wsun), escape(pct(fin_pct)),
                u'（T+1 缺 {}）'.format(md(idx_last)) if t1_missing else '',
                streak, N, gap))
    kv['fin_src'] = (u'margin_m227.json（线上版，{}条）· 用 fin_balance 绝对余额（非比值）'
                     u'· 日频聚合为交易周（每周最后交易日）').format(len(r['fin_raw']))
    kv['fin_badge'] = (u'链断 · 计数={}'.format(streak)
                       if pp is not None and pp > 0 else
                       u'连降延续 · 计数={}'.format(streak))
    kv['idx_badge'] = (u'末周{}但连降不足'.format(u'成立' if idx_pct is not None and idx_pct < 0 else u'不成立')
                       if not r['veto_now'] else u'末周同向 + 连降已达标')
    kv['idx_val'] = (
        u'末周（{}~{}）沪深300周环比 <b class="num">{}</b>——指数{}，'
        u'融资连降计数 <b class="num">{} 周</b>，双确认的 N={} 前提{} → {}'
        .format(md(wmon), md(wsun), escape(pct(idx_pct)),
                u'同周下跌' if (idx_pct is not None and idx_pct < 0) else u'未下跌',
                streak, N, u'成立' if r['veto_now'] else u'不成立',
                u'触发' if r['veto_now'] else u'不触发'))
    kv['idx_src'] = (u'margin_hs300.json（线上版，{}根日K至{}）· 同周口径=当周指数周环比&lt;0，'
                     u'与融资严格对齐到同一交易周').format(len(r['idx_raw']), md(idx_last))
    if r['veto_now']:
        kv['verdict'] = (u'<b>VETO 已触发（连降 {} 周 ≥ N={}，当周指数同向下跌）。</b>'
                         u'双确认两个前提同时成立 → 冻结加仓，仓位只可持平或下移；'
                         u'减不减由你自己的回撤纪律决定。').format(streak, N)
    else:
        kv['verdict'] = (
            u'<b>VETO 未触发（连降计数 {} &lt; N={}）。</b>'
            u'融资连降与当周指数下跌尚未同时满足连续 {} 周 + 同周同向，'
            u'否决层按设计保持钝感——宁钝勿滥。{}'
            .format(streak, N, N,
                    u'末周融资数据到位后重跑引擎确认。' if t1_missing else u''))
    kv['probe_a'] = u'末周 {} → <b style="color:var(--green)">{}</b>（{}）'.format(
        escape(pct(idx_pct)),
        u'成立' if (idx_pct is not None and idx_pct < 0) else u'不成立',
        u'但融资连降不足' if not r['veto_now'] else u'融资连降已达标')
    kv['probe_b'] = u'{:.2f} {} → 成立，仅作背景'.format(close, ma_text)

    # —— 第5条计时器 ——
    bottoms = (signal.get('zones') or {}).get('bottom') or []
    entered = bottoms[-1].get('s') if bottoms else 'N/A'  # 最近一段 bottom 的起始日
    exited = bottoms[-1].get('e') if bottoms else 'N/A'
    kv['timer_badge'] = (u'计时中 · bottom 区 {} 个交易日'.format(sig_days)
                         if sig_state == 'bottom' else
                         u'已失效 · 左侧信号 {} 退出'.format(md(sig_date)))
    kv['timer_enter'] = entered or 'N/A'
    kv['timer_exit'] = (u'{}'.format(sig_date))
    kv['timer_rule'] = (
        u'<b>左侧信号在 bottom 区（已 {} 个交易日）→ 第5条 4 周期限计时中。</b>'
        u'若满 4 周且融资未转正，退回防御档。'.format(sig_days)
        if sig_state == 'bottom' else
        u'<b>左侧信号已退出 → 4 周期限规则无对象，判定失效。</b>'
        u'防御档的维持改由「趋势资金流出 + 指数破位」支撑（见五档表）')
    kv['timer_foot'] = (
        u'注：ETF 净申购分位仍处 <b>pr={}</b> 历史极高位（flow20 ≈ {} 亿），'
        u'但信号引擎判定为 {}——分位高不等于处于 bottom 信号区。'
        .format(escape(str(pr)), escape('%.1f' % flow20) if flow20 is not None else 'N/A',
                escape(sig_state)))

    # —— 五档映射 ——
    kv['map_preview'] = (u'预览开关：模拟 VETO 触发态（仅演示覆盖层效果，'
                         u'<b>不改变真实判定</b>——当前真实 VETO={}）'
                         .format(u'已触发' if r['veto_now'] else u'未触发'))
    kv['map_cur'] = (u'VETO 已触发 · 冻结加仓' if r['veto_now'] else
                     u'当前维持：趋势资金流出 + 指数破位（否决未触发·默认层不动）')
    kv['map_foot'] = (
        u'<b>纪律：</b>① 档位切换需多信号交叉确认，单一信号不触发；② 档位间设缓冲带，'
        u'不跨档跳跃；③ 每月第一个交易日再平衡底仓/进攻仓至目标比。当前结论是'
        u'<b>{}</b>，<b>不是强制减仓</b>——是否减仓由你的回撤纪律决定。'
        .format(u'"冻结加仓"（VETO 触发）' if r['veto_now'] else
                u'"防御档维持不动"（否决未触发、左侧信号已退出、默认层不动）'))

    # —— 伪代码区当前读数 ——
    kv['pseudo_foot'] = (
        u'当前 W={}（数据末日 融资 {} / 指数 {}）：flt_pct={}（连降计数 {} 周），'
        u'idx_pct=<b>{}</b> → {} → <b>VETO={}</b>。ETF 左侧信号 {} {}。'
        u'{}'
        .format(escape(week), md(fin_last), md(idx_last),
                escape(pct(fin_pct)), streak, escape(pct(idx_pct)),
                u'两路同周同向' if same_down else u'两路未同向',
                u'true' if r['veto_now'] else u'false',
                md(sig_date), u'仍在 bottom 区' if sig_state == 'bottom' else u'已退出',
                u'*末周融资 T+1 未出（缺 {}），数据尾部不完整，到位后重跑引擎确认。'.format(md(idx_last))
                if t1_missing else ''))

    # —— 观察项 ERP（宽度卡片源数据不在仓库，保留人工） ——
    kv['obs_erp_val'] = u'沪深300全收益（建模）{} / 二级债基（建模）{} · 截至 {} · 无可靠历史分位'.format(
        escape(str(erp_a[1])), escape(str(erp_b[1])), md(erp_up))
    kv['obs_erp_src'] = u'bond_stock_gap.json · data_kind=modeled_rolling · 方向参考，精确分位不当真，暂不驱动档位'

    # —— 字段核实清单中的客观条数 ——
    kv['field_fin_n'] = u'margin_m227.json 单条含 <span class="mono">fin_balance</span>（如 {}）= 余额绝对值，直接用周度差分，不用比值代理'.format(
        escape(str(r['fin_raw'][-1].get('fin_balance'))))
    kv['field_fin_rows'] = u'{} 条日频（间隔多为1天）；按 isocalendar 聚合、每周取最后交易日值，避免自然周空周'.format(
        len(r['fin_raw']))
    kv['field_idx_n'] = u'有 <b style="color:var(--green)">{} 根日K</b>'.format(len(r['idx_raw']))
    kv['field_idx_src'] = u'margin_hs300.json 的 <span class="mono">data</span> 含 date/close/pct/vol，{}→{}，可算均线/前低/周涨跌'.format(
        r['idx_raw'][0]['date'], r['idx_raw'][-1]['date'])
    kv['field_weeks'] = u'融资周环比与指数周环比取同一 isocalendar 周，两者同号才算，{} 个共同周覆盖'.format(len(r['weeks']))
    kv['nval_count'] = u'{} 次'.format(len(r['veto_events']))

    return kv


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

    # 图表数据：与页面内联 CHART_WEEKS 完全同口径，逐项复刻（已验证 529 周零差异）
    # 结构 [周末日, 融资周环比%, 指数周环比%, 跨节周标记, VETO触发标记]
    veto_weeks = {tuple(e['week']) for e in r['veto_events']}
    chart_weeks = []
    for rec in r['records']:
        fp = None if rec['fin_pct'] is None else round(rec['fin_pct'], 2)
        ip = None if rec['idx_pct'] is None else round(rec['idx_pct'], 2)
        chart_weeks.append([rec['date'], fp, ip,
                            1 if rec['cross'] else 0,
                            1 if rec['week'] in veto_weeks else 0])

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
        'chart': {
            'weeks': chart_weeks,
            'meta': {
                'last_fin': r['fin_last_date'],
                'last_idx': r['idx_last_date'],
                't1_missing': r['fin_last_date'] < r['idx_last_date'],
            },
        },
        'kv': build_kv(r, signal, bond, sig_date, sig_state, sig_days, week),
    }
    out = stamp(out, source="eastmoney+datacenter", asof=r['fin_last_date'])
    with open(OUT, 'w', encoding='utf-8', newline='') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    print('[etf_veto_comment] %s | VETO=%s | streak=%d | signal=%s/%d'
          % (out['generated_at'], out['status']['veto'], streak, sig_state, sig_days))


if __name__ == '__main__':
    main()
