#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_bond_stock_gap.py — 生成 bond_stock_gap.json（供 cffex.html「股债收益差」标签页读取）

策略（v2，2026-11-01 重构）：
  1) 先用东方财富探测「沪深300全收益 H00300」「二级债基」等 secid 是否真有日线；
     两条都拿到 → 直接用真实序列（data_kind=live）。
  2) 任一取不到 → 用腾讯行情取「沪深300价格指数」（该链路本机与 Actions 均稳定），
     以 JSON 最后一天为链头，按 (价格日收益 × 分红因子) 递推全收益建模值；
     二级债基按年化漂移递推。历史段不动、只向前延伸，不重锚定。
  3) 任何环节失败都不崩：保留原文件、退出码 0，由页面按 kind/fetch_status 提示。

页面按**索引**对齐两条线，因此本脚本保证输出两序列日期完全一一对应（取交集）。

仅用标准库，可在 GitHub Actions 沙箱运行。
"""
import json, sys, os, time
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bond_stock_gap.json')

DIV_YIELD = 0.023      # 沪深300 年化股息率估测（与种子口径一致）
BOND_DRIFT = 0.055     # 二级债基年化漂移估测
TRADING_DAYS = 252.0

EM_HS_CANDIDATES = ['1.H00300', '0.H00300', '2.H00300']
EM_BOND_CANDIDATES = ['1.885007', '1.809010', '1.H11001', '1.000012', '1.000832', '1.H11009', '1.930897']
EM_HOSTS = [
    'https://push2his.eastmoney.com/api/qt/stock/kline/get',
    'https://push2.eastmoney.com/api/qt/stock/kline/get',
]


def http_get(url, timeout=40, tries=3):
    headers = {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/', 'Accept': '*/*'}
    for i in range(tries):
        if i > 0:
            time.sleep(2.0)
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception:
            continue
    return None


def parse_em_kline(text):
    """东财 kline -> [[date, close]]；data 为 null/空 时返回 None（v1 在此崩过）。"""
    if not text:
        return None
    try:
        j = json.loads(text)
    except Exception:
        return None
    d = j.get('data')
    if not isinstance(d, dict):
        return None
    klines = d.get('klines')
    if not isinstance(klines, list) or not klines:
        return None
    out = []
    for line in klines:
        p = str(line).split(',')
        if len(p) < 3:
            continue
        try:
            c = float(p[2])
        except Exception:
            continue
        if c > 0:
            out.append([str(p[0]), round(c, 2)])
    return out if len(out) >= 2 else None


def em_series(secid, lmt=4000):
    info = []
    for host in EM_HOSTS:
        tag = host.split('//')[1].split('.')[0]
        url = ('%s?secid=%s&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56'
               '&klt=101&fqt=0&end=20500101&lmt=%d') % (host, secid, lmt)
        txt = http_get(url, timeout=30, tries=1)
        if not txt:
            info.append(tag + ':noresp')
            continue
        data = parse_em_kline(txt)
        if data:
            info.append('%s:%dbar' % (tag, len(data)))
            return data, ','.join(info)
        info.append(tag + ':null')
    return None, ','.join(info)


def tencent_series(symbol='sh000300', lmt=2000):
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq' % (symbol, lmt)
    txt = http_get(url, timeout=40, tries=2)
    if not txt:
        return None
    try:
        j = json.loads(txt)
    except Exception:
        return None
    nd = (j.get('data') or {}).get(symbol) or {}
    arr = nd.get('day') or nd.get('qfqday') or []
    out = []
    for r in arr:
        try:
            out.append([str(r[0]), round(float(r[2]), 2)])
        except Exception:
            continue
    return out if len(out) >= 2 else None


def load_existing():
    try:
        with open(OUT, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def align_by_date(hs, bd):
    """按日期取交集，保证两序列索引一一对应（页面按索引画图）。"""
    bmap = {d: v for d, v in bd}
    hs2, bd2 = [], []
    for d, v in hs:
        if d in bmap:
            hs2.append([d, v])
            bd2.append([d, bmap[d]])
    return hs2, bd2


def extend_modeled(existing, price_series):
    """以现有最后一天为链头，用真实价格日收益 + 分红/漂移因子向前递推（不重写历史）。"""
    hs = [list(x) for x in existing['hs300']['data']]
    bd = [list(x) for x in existing['bond2']['data']]
    hs, bd = align_by_date(hs, bd)
    if not hs:
        return 0, 'empty after align'

    last_date = hs[-1][0]
    tr, bond = hs[-1][1], bd[-1][1]

    price_map = {d: v for d, v in price_series}
    if last_date in price_map:
        base_price = price_map[last_date]
    else:
        prior = [d for d in price_map if d <= last_date]
        if not prior:
            return 0, 'anchor price missing'
        base_price = price_map[max(prior)]

    new_dates = sorted(d for d in price_map if d > last_date)
    if not new_dates:
        existing['hs300']['data'] = hs
        existing['bond2']['data'] = bd
        return 0, 'no new bars'

    d_div = (1 + DIV_YIELD) ** (1.0 / TRADING_DAYS)
    d_bond = (1 + BOND_DRIFT) ** (1.0 / TRADING_DAYS)
    prev = base_price
    added = 0
    for d in new_dates:
        p = price_map[d]
        if prev <= 0:
            break
        tr = tr * (p / prev) * d_div
        bond = bond * d_bond
        hs.append([d, round(tr, 2)])
        bd.append([d, round(bond, 2)])
        prev = p
        added += 1
    existing['hs300']['data'] = hs
    existing['bond2']['data'] = bd
    return added, 'appended'


def main():
    print('== gen_bond_stock_gap v2 ==')
    existing = load_existing()
    if not existing or not existing.get('hs300', {}).get('data'):
        print('  ⚠ 无既有数据文件，退出（保留现状，不写空文件）')
        return

    print('  [probe] 东方财富 secid：')
    hs_real = bd_real = None
    for sid in EM_HS_CANDIDATES:
        data, info = em_series(sid)
        print('    HS %-12s %-30s %s' % (sid, info, ('OK %d bars' % len(data)) if data else '-'))
        if data and hs_real is None:
            hs_real = (data, sid)
    for sid in EM_BOND_CANDIDATES:
        data, info = em_series(sid)
        print('    BD %-12s %-30s %s' % (sid, info, ('OK %d bars' % len(data)) if data else '-'))
        if data and bd_real is None:
            bd_real = (data, sid)

    changed = False

    if hs_real and bd_real:
        (hdata, hsid), (bdata, bsid) = hs_real, bd_real
        hs, bd = align_by_date(hdata, bdata)
        if len(hs) >= 60:
            print('  ✓ 真实序列：HS=%s %d 根 / BD=%s %d 根 → 交集 %d 根'
                  % (hsid, len(hdata), bsid, len(bdata), len(hs)))
            existing['hs300'] = {'name': '沪深300全收益指数', 'code': hsid, 'kind': 'total_return',
                                 'src': '东方财富K线(%s)' % hsid, 'fetch_status': 'ok', 'data': hs}
            existing['bond2'] = {'name': '债券指数(%s)' % bsid, 'code': bsid, 'kind': 'real',
                                 'src': '东方财富K线(%s)' % bsid, 'fetch_status': 'ok', 'data': bd}
            existing['data_kind'] = 'live'
            changed = True
        else:
            print('  ⚠ 交集过短(%d)，放弃真实序列，改走递推' % len(hs))
    else:
        print('  ⚠ 真实全收益/二级债基未能同时取得（免费接口限制），改走建模递推')

    if not changed:
        price = tencent_series('sh000300')
        if not price:
            print('  ⚠ 腾讯价格指数也取不到，保留既有文件不动')
            return
        print('  腾讯 sh000300：%d 根 (%s..%s)' % (len(price), price[0][0], price[-1][0]))
        added, why = extend_modeled(existing, price)
        print('  建模递推：新增 %d 个交易日（%s）' % (added, why))
        if added:
            existing['data_kind'] = 'modeled_rolling'
            existing['hs300']['src'] = str(existing['hs300'].get('src', '')) + ' +腾讯价格日收益递推'
            existing['bond2']['src'] = str(existing['bond2'].get('src', '')) + ' +年化漂移递推'
            changed = True

    if changed:
        existing['updated'] = existing['hs300']['data'][-1][0]
        existing['notes'] = (
            '由 gen_bond_stock_gap.py 自动生成。免费接口取不到沪深300全收益(H00300)与二级债基(885007.WI)时，'
            '以沪深300价格指数 + 估测股息率 %s%% 递推全收益建模值、以年化 %s%% 漂移递推债基线；'
            '历史段不重锚定、只向前延伸。页面按 kind / data_kind 提示数据质量。'
            % (round(DIV_YIELD * 100, 1), round(BOND_DRIFT * 100, 1))
        )
        with open(OUT, 'w', encoding='utf-8', newline='') as f:
            json.dump(existing, f, ensure_ascii=False, separators=(',', ':'))
        print('  写入 | data_kind=%s | HS %d 根 / BD %d 根 | 末日 %s'
              % (existing.get('data_kind'), len(existing['hs300']['data']),
                 len(existing['bond2']['data']), existing['updated']))
    else:
        print('  无变更，未写文件')


if __name__ == '__main__':
    main()
