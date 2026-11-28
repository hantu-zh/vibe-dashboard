# -*- coding: utf-8 -*-
"""dz_refresh.py — 定增跟踪表每周自动刷新

数据源：
  1. 东财 datacenter RPT_SEO_DETAIL（增发明细）：code/name/发行价/上市日/认购对象
     — 字段口径已用种子校准：种子的 issue_date == API 的 ISSUE_LISTING_DATE
       （江波龙 301308: 560.00 / 2026-08-17 完全吻合）。
  2. 腾讯 qt.gtimg.cn 批量行情：现价 → 用于 ① 判断新标的是否破发 ② 刷新存量 seed_close。

更新逻辑（幂等，可重复跑）：
  - 新增标的 = 窗口内(近18个月上市) A股(60/00/30/68) 竞价定增(SEO_TYPE=1) 且 当前破发(现价<发行价)，
    按 (code, issue_date) 去重；自动标注字段带 auto_est 标记，绝不编造：
      term_type='1Y'（现行竞价定增 6 个月锁定）/ unlock_date=上市日+6个月(unlock_est=true)
      purpose_type='待标注' / capital='—' / discount_rate=null
      subscriber 由 ISSUE_OBJECT 关键词分类（实控人/战略投资者/机构/混合），判不出→'待标注'
  - 存量标的：仅刷新 seed_close（前端现价兜底价），其余人工标注一律不动。
  - 落盘前 stamp _meta（generated_at/asof/source），保留原 meta 说明段。

用法：
    python dz_refresh.py --seed dz_stock_seed.json            # 原地更新
    python dz_refresh.py --seed a.json --out b.json --dry-run # 只看不写
"""
import argparse
import datetime
import json
import re
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request

_SH = datetime.timezone(datetime.timedelta(hours=8))
_WINDOW_MONTHS = 18
_MARKETS = ('60', '00', '30', '68')   # 沪深主板/创业板/科创板，排除北交所(920/8/4)

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
      'Referer': 'https://data.eastmoney.com/'}


def today():
    return datetime.datetime.now(_SH).date()


def window_start():
    d = today()
    m = d.month - _WINDOW_MONTHS
    y = d.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return datetime.date(y, m, 1)


# ---------------- 东财增发明细 ----------------

def _em_call(params, timeout=30):
    p = {'reportName': 'RPT_SEO_DETAIL', 'columns': 'ALL', 'pageSize': 500,
         'pageNumber': 1, 'source': 'WEB', 'client': 'WEB',
         'filter': "(ISSUE_LISTING_DATE>='%s')" % window_start().isoformat(),
         'sortColumns': 'ISSUE_LISTING_DATE', 'sortTypes': '-1'}
    p.update(params)
    url = 'https://datacenter-web.eastmoney.com/api/data/v1/get?' + urllib.parse.urlencode(p)
    for attempt in range(4):
        if attempt:
            import time
            time.sleep(2 * attempt)
        try:
            req = urllib.request.Request(url, headers=UA)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                j = json.loads(r.read().decode('utf-8', 'ignore'))
            res = j.get('result')
            if res and res.get('data') is not None:
                return res
        except Exception as e:
            if attempt == 3:
                raise RuntimeError('东财 RPT_SEO_DETAIL 连续 4 次失败: %r' % e)
    return None


def fetch_seo_rows():
    res = _em_call({})
    rows, page, pages = list(res['data']), 1, res['pages']
    while page < pages:
        page += 1
        res = _em_call({'pageNumber': page})
        rows += res['data']
    return rows


def classify_subscriber(obj_text):
    t = obj_text or ''
    has_holder = ('实际控制人' in t) or ('控股股东' in t)
    has_strategy = '战略投资者' in t
    n_parts = len([p for p in t.split(',') if p.strip()])
    if has_holder and (has_strategy or n_parts > 1):
        return '混合'
    if has_holder:
        return '实控人'
    if has_strategy:
        return '战略投资者'
    if t:
        return '机构'
    return '待标注'


def add_months(d, n):
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return datetime.date(y, m, day)


def build_new_item(d, close):
    code = d['SECURITY_CODE']
    lst = (d.get('ISSUE_LISTING_DATE') or '')[:10]
    ld = datetime.date(*map(int, lst.split('-')))
    obj_raw = d.get('ISSUE_OBJECT') or ''
    return {
        'code': code,
        'name': d.get('SECURITY_NAME_ABBR') or code,
        'term_type': '1Y',
        'purpose_type': '待标注',
        'capital': '—',
        'subscriber': classify_subscriber(obj_raw),
        'issue_price': float(d['ISSUE_PRICE']),
        'issue_date': lst,
        'lock_period_months': 6,
        'unlock_date': add_months(ld, 6).isoformat(),
        'unlock_est': True,
        'discount_rate': None,
        'discount_est': True,
        'seed_close': close,
        'was_below_minus2_20d': None,
        'was_below_minus5_20d': None,
        'auto_added': True,
        'auto_est': 'term/unlock/subscriber 为关键词自动分类(上市日+6个月锁定)，purpose 待人工标注',
        'subscribers_raw': obj_raw[:80],
        'issue_way': d.get('ISSUE_WAY') or '',
    }


# ---------------- 腾讯批量行情 ----------------

def sym_of(code):
    return ('sh' if code.startswith(('6', '9')) else 'sz') + code


def fetch_quotes(codes, batch=60):
    """返回 {code: float_close}；失败/停牌的 code 不在结果里。"""
    out = {}
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        q = ','.join(sym_of(c) for c in chunk)
        url = 'https://qt.gtimg.cn/q=' + q
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20) as r:
                txt = r.read().decode('gbk', 'ignore')
            for line in txt.split(';'):
                m = re.search(r'v_(?:sh|sz)(\d{6})="([^"]*)"', line)
                if not m:
                    continue
                f = m.group(2).split('~')
                if len(f) > 3 and f[3]:
                    try:
                        px = float(f[3])
                        if px > 0:
                            out[m.group(1)] = px
                    except ValueError:
                        pass
        except Exception as e:
            print('WARN quotes batch %d 失败: %r' % (i // batch, e))
        import time
        time.sleep(0.4)
    return out


# ---------------- 主流程 ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', default='dz_stock_seed.json')
    ap.add_argument('--out', default=None, help='默认原地覆盖 --seed')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    out_path = args.out or args.seed

    with open(args.seed, 'r', encoding='utf-8') as f:
        doc = json.load(f)
    items = doc.get('items', [])
    print('种子存量: %d 条' % len(items))

    # 1) 拉增发明细，过滤 A 股竞价定增
    rows = fetch_seo_rows()
    ash = [d for d in rows
           if (d.get('SECURITY_CODE') or '')[:2] in _MARKETS
           and str(d.get('SEO_TYPE')) == '1'
           and d.get('ISSUE_LISTING_DATE') and d.get('ISSUE_PRICE')]
    print('东财窗口内 A股竞价定增: %d 条' % len(ash))

    # 2) 拉行情：存量 + 候选
    all_codes = sorted({it['code'] for it in items} | {d['SECURITY_CODE'] for d in ash})
    quotes = fetch_quotes(all_codes)
    print('行情成功: %d/%d' % (len(quotes), len(all_codes)))

    # 3) 存量：只刷 seed_close
    refreshed = 0
    for it in items:
        px = quotes.get(it['code'])
        if px is not None and it.get('seed_close') != px:
            it['seed_close'] = px
            refreshed += 1

    # 4) 新增：窗口内 + 当前破发 + 去重(code, issue_date)
    existing_keys = {(it['code'], it.get('issue_date')) for it in items}
    existing_codes = {it['code'] for it in items}
    added = []
    for d in ash:
        code = d['SECURITY_CODE']
        lst = d['ISSUE_LISTING_DATE'][:10]
        if (code, lst) in existing_keys:
            continue
        px = quotes.get(code)
        if px is None or px >= float(d['ISSUE_PRICE']):
            continue  # 未破发 / 无行情 → 不入表
        added.append(build_new_item(d, px))
        existing_keys.add((code, lst))
        existing_codes.add(code)

    # 5) 落盘：meta 保留、items 一行一条、_meta 时效标
    doc['items'] = items + added
    asof = today().isoformat()
    try:
        sys.path.insert(0, __file__.rsplit('/', 1)[0] if '/' in __file__ else '.')
        from signal_meta import stamp
        stamp(doc, source='eastmoney RPT_SEO_DETAIL + tencent quotes', asof=asof)
    except Exception:
        doc['_meta'] = {'generated_at': datetime.datetime.now(_SH).strftime('%Y-%m-%d %H:%M:%S'),
                        'source': 'eastmoney RPT_SEO_DETAIL + tencent quotes', 'asof': asof}
    meta = doc.get('meta') or {}
    meta['generated'] = asof
    meta['auto_update'] = ('CI 每周一自动增量更新（dz_refresh.py）：东财增发明细 + 腾讯行情；'
                           '新增仅收录当前破发标的，自动标注字段见 auto_added/auto_est 标记')
    doc['meta'] = meta

    items_text = ',\n    '.join(json.dumps(it, ensure_ascii=False) for it in doc['items'])
    doc_noitems = dict(doc)
    doc_noitems['items'] = []
    text = json.dumps(doc_noitems, ensure_ascii=False, indent=2)
    if items_text:
        text = text.replace('"items": []', '"items": [\n    ' + items_text + '\n  ]')
    assert '\r' not in text

    new_meta = {
        'asof': asof,
        'existing': len(items),
        'seed_close_refreshed': refreshed,
        'added': len(added),
        'added_list': ['%s %s @%.2f (%s)' % (a['code'], a['name'], a['issue_price'], a['issue_date']) for a in added],
    }
    print(json.dumps(new_meta, ensure_ascii=False, indent=2))

    if args.dry_run:
        print('DRY-RUN: 未写盘')
        return
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        f.write(text + '\n')
    print('已写出: %s (%d 字节)' % (out_path, len(text.encode('utf-8'))))


if __name__ == '__main__':
    main()
