#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_bond_stock_gap.py — 生成 bond_stock_gap.json
供 cffex.html 的「股债收益差」标签页读取，演示 A 股极端底部信号：
  把「沪深300全收益指数」与「二级债基指数」放在同一坐标，
  当沪深300全收益跌到与二级债基指数相近/击穿时，历史上往往对应 A 股重要底部区域。

数据源：东方财富 K 线接口 push2his.eastmoney.com（GitHub Actions 环境可达；
        本机沙箱若被服务端掐断则自动降级，保留上次成功的数据，不会写出空文件）。

仅用标准库（urllib），无第三方依赖，可在 GitHub Actions 沙箱运行。

⚠️ secid 候选说明（需在生产环境验证）：
  - 沪深300全收益指数（中证 H00300）在东方财富免费接口未必有独立 secid，
    故先试全收益候选，失败则降级为沪深300价格指数(1.000300)，kind 标注为 price_index。
  - 二级债基指数多为 Wind(885007.WI)/Choice(809010.EI) 专有，免费接口常取不到，
    故先试这两个候选，失败则降级为可得的债券成本线（中证全债 H11001）并明确标注；
    若全部失败，则保留上次成功的 bond2（或种子建模值），并在 JSON 标注 fetch_status。
"""
import json, sys, os, time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
REF = 'https://quote.eastmoney.com/'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bond_stock_gap.json')

# (secid, name, kind) —— kind 用于页面标注数据质量
HS300_CANDIDATES = [
    ('1.H00300', '沪深300全收益指数', 'total_return'),   # 中证全收益 H00300（待验证）
    ('1.000300', '沪深300价格指数', 'price_index'),      # 降级：价格指数（腾讯同源口径）
]
BOND2_CANDIDATES = [
    ('1.885007', '二级债基指数', 'real'),               # Wind 二级债基（待验证，免费接口常无）
    ('1.809010', '混合债券型二级指数', 'real'),          # Choice 809010.EI（待验证）
    ('1.H11001', '中证全债指数', 'real'),               # 降级：债券成本线近似
]
KLINE_HOSTS = [
    'https://push2his.eastmoney.com/api/qt/stock/kline/get',
    'https://push2.eastmoney.com/api/qt/stock/kline/get',
]
LMT = 4000  # 抓取足够多的日线（覆盖 2008 以来的底部）


def http_get(url, timeout=40, tries=3):
    headers = {'User-Agent': UA, 'Referer': REF, 'Accept': '*/*'}
    last = None
    for i in range(tries):
        if i > 0:
            time.sleep(2.5)
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            last = e
            continue
    return None


def parse_em_kline(text):
    """东方财富 kline 返回 -> [[date, close], ...]（只取收盘，按日期升序）。"""
    try:
        j = json.loads(text)
    except Exception:
        return None
    klines = j.get('data', {}).get('klines')
    if not isinstance(klines, list) or not klines:
        return None
    out = []
    for line in klines:
        p = str(line).split(',')
        if len(p) < 6:
            continue
        try:
            c = float(p[2])  # [date, open, close, high, low, vol, ...]
        except Exception:
            continue
        if c <= 0:
            continue
        out.append([str(p[0]), round(c, 2)])
    return out if len(out) >= 2 else None


def fetch_series(candidates):
    """依次尝试候选 secid，返回 (data, meta) 或 None。"""
    for secid, name, kind in candidates:
        for host in KLINE_HOSTS:
            url = ('%s?secid=%s&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56'
                   '&klt=101&fqt=0&end=20500101&lmt=%d') % (host, secid, LMT)
            try:
                txt = http_get(url)
            except Exception:
                txt = None
            if not txt:
                continue
            data = parse_em_kline(txt)
            if data:
                return data, {'name': name, 'code': secid, 'kind': kind,
                              'src': '东方财富K线(%s)' % secid}
            time.sleep(0.4)
    return None


def load_existing():
    try:
        with open(OUT, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def main():
    print('== gen_bond_stock_gap ==')
    existing = load_existing()

    # ---- 沪深300 ----
    hs = fetch_series(HS300_CANDIDATES)
    if hs:
        hs_data, hs_meta = hs
        print('  沪深300: %s  数据 %d 根 (kind=%s)' % (hs_meta['code'], len(hs_data), hs_meta['kind']))
    else:
        print('  ⚠ 沪深300 抓取失败，尝试保留已有数据')
        if existing and existing.get('hs300', {}).get('data'):
            hs_data = existing['hs300']['data']
            hs_meta = {k: existing['hs300'].get(k) for k in ('name', 'code', 'kind', 'src')}
            hs_meta['fetch_status'] = 'unavailable_now_kept_last'
            print('    保留上次 %d 根' % len(hs_data))
        else:
            print('    无可用沪深300数据，跳过'); return

    # ---- 二级债基 ----
    bd = fetch_series(BOND2_CANDIDATES)
    if bd:
        bd_data, bd_meta = bd
        print('  二级债基: %s  数据 %d 根 (kind=%s)' % (bd_meta['code'], len(bd_data), bd_meta['kind']))
    else:
        print('  ⚠ 二级债基（Wind/Choice）免费接口未取到，尝试保留已有数据')
        if existing and existing.get('bond2', {}).get('data'):
            bd_data = existing['bond2']['data']
            bd_meta = {k: existing['bond2'].get(k) for k in ('name', 'code', 'kind', 'src')}
            bd_meta['fetch_status'] = 'unavailable_free_api_kept_last'
            print('    保留上次 %d 根' % len(bd_data))
        else:
            print('    无可用二级债基数据，跳过'); return

    # 对齐：取两者日期交集（按日期）
    hs_map = {d: v for d, v in hs_data}
    bd_map = {d: v for d, v in bd_data}
    common = sorted(set(hs_map) & set(bd_map))
    if len(common) < 2:
        print('  ⚠ 两序列无足够重合日期，退化为各自最近对齐'); 
        # 退路：用较短者长度对齐两端
        m = min(len(hs_data), len(bd_data))
        hs_al = hs_data[-m:]; bd_al = bd_data[-m:]
    else:
        hs_al = [[d, hs_map[d]] for d in common]
        bd_al = [[d, bd_map[d]] for d in common]

    out = {
        'updated': hs_al[-1][0],
        'data_kind': 'live' if all('fetch_status' not in m for m in (hs_meta, bd_meta)) else 'seed_fallback',
        'hs300': {
            'name': hs_meta.get('name', '沪深300'),
            'code': hs_meta.get('code', ''),
            'kind': hs_meta.get('kind', 'unknown'),
            'src': hs_meta.get('src', ''),
            'fetch_status': hs_meta.get('fetch_status', 'ok'),
            'data': hs_al,
        },
        'bond2': {
            'name': bd_meta.get('name', '二级债基指数'),
            'code': bd_meta.get('code', ''),
            'kind': bd_meta.get('kind', 'unknown'),
            'src': bd_meta.get('src', ''),
            'fetch_status': bd_meta.get('fetch_status', 'ok'),
            'data': bd_al,
        },
        'notes': '由 gen_bond_stock_gap.py 自动生成。沪深300全收益与二级债基指数在免费接口可能取不到，'
                  '此时会降级（价格指数 / 债券成本线 / 保留上次数据）并在 fetch_status 标注，页面据此提示数据质量。',
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('写入 %s  沪深300 %d 根 / 二级债基 %d 根  末日 %s' % (OUT, len(hs_al), len(bd_al), out['updated']))


if __name__ == '__main__':
    main()
