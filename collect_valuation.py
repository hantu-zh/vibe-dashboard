#!/usr/bin/env python3
"""
板块估值热度采集：从东方财富 datacenter-web 拉近 5 年行业板块 PE_TTM/PB_MRQ 历史，
按板块计算当前估值在近 5 年的历史分位，输出 ai_analysis_valuation.json。

数据源：https://datacenter-web.eastmoney.com/api/data/v1/get  (reportName=RPT_VALUEINDUSTRY_DET)
用法：python collect_valuation.py
"""
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUT_JSON = BASE_DIR / 'ai_analysis_valuation.json'
API = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
LOOKBACK_YEARS = 5
PAGE_SIZE = 5000
MAX_PAGES = 60          # 安全上限：5 年全行业约 31 页
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'


def fetch_page(page, start_date):
    params = {
        'reportName': 'RPT_VALUEINDUSTRY_DET',
        'columns': 'TRADE_DATE,BOARD_CODE,BOARD_NAME,PE_TTM,PB_MRQ',
        'filter': f"(TRADE_DATE>='{start_date}')",
        'pageNumber': page,
        'pageSize': PAGE_SIZE,
        'sortColumns': 'TRADE_DATE',
        'sortTypes': -1,
        'source': 'WEB',
    }
    url = API + '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://data.eastmoney.com/'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                obj = json.loads(resp.read().decode('utf-8'))
            result = obj.get('result')
            if not result:
                raise RuntimeError(f'result empty: {obj.get("message")}')
            return result
        except Exception as e:
            if attempt == 2:
                raise
            print(f'  [retry {attempt+1}] page {page}: {e}')
            time.sleep(2)


def collect_history():
    start = (datetime.now() - timedelta(days=LOOKBACK_YEARS * 365 + 5)).strftime('%Y-%m-%d')
    print(f'[valuation] 采集区间 {start} 至今')
    by_code = defaultdict(list)      # code -> [(date, name, pe, pb)]
    name_of = {}
    total = 0
    pages = None
    for page in range(1, MAX_PAGES + 1):
        result = fetch_page(page, start)
        if pages is None:
            pages = result.get('pages', 1)
            print(f'[valuation] 共 {pages} 页 / 约 {pages*PAGE_SIZE:,} 行')
        rows = result.get('data') or []
        if not rows:
            break
        for r in rows:
            code = r.get('BOARD_CODE')
            if not code:
                continue
            d = str(r.get('TRADE_DATE') or '')[:10]
            pe = r.get('PE_TTM')
            pb = r.get('PB_MRQ')
            name_of[code] = r.get('BOARD_NAME') or name_of.get(code, '')
            # 负值 PE 表示板块整体亏损，分位无意义，丢弃该点
            if pe is not None and pe < 0:
                pe = None
            by_code[code].append((d, pe, pb))
        total += len(rows)
        if page % 10 == 0 or page == pages:
            print(f'  page {page}/{pages} 累计 {total:,} 行')
        if page >= pages:
            break
        time.sleep(0.3)
    return by_code, name_of, total


def pct_rank(series, value):
    """当前值在历史序列中的百分位（0-100）：小于当前值的样本占比。"""
    valid = [x for x in series if x is not None]
    if not valid or value is None:
        return None
    n = len(valid)
    below = sum(1 for x in valid if x < value)
    equal = sum(1 for x in valid if x == value)
    return round((below + 0.5 * equal) / n * 100, 1)


def judge(pct):
    if pct is None:
        return 'N/A', '#8b93a3'
    if pct >= 80:
        return '偏贵', '#e57373'
    if pct >= 60:
        return '略高', '#e0a458'
    if pct >= 40:
        return '中性', '#c9cdd6'
    if pct >= 20:
        return '偏低', '#6fb98f'
    return '便宜', '#4dbb8a'


def build_output(by_code, name_of):
    items = []
    for code, hist in by_code.items():
        hist.sort(key=lambda x: x[0])          # 按日期升序
        # 只保留最近 5 年窗口（采集已限定，这里再兜底去重/排序）
        pe_series = [h[1] for h in hist]
        pb_series = [h[2] for h in hist]
        latest = hist[-1]
        cur_pe, cur_pb = latest[1], latest[2]
        pe_pct = pct_rank(pe_series, cur_pe)
        pb_pct = pct_rank(pb_series, cur_pb)
        label, color = judge(pe_pct)
        valid_pe = [x for x in pe_series if x is not None]
        valid_pb = [x for x in pb_series if x is not None]
        items.append({
            'code': code,
            'name': name_of.get(code, ''),
            'pe': round(cur_pe, 2) if cur_pe is not None else None,
            'pb': round(cur_pb, 2) if cur_pb is not None else None,
            'pe_pct': pe_pct,
            'pb_pct': pb_pct,
            'label': label,
            'color': color,
            'pe_min': round(min(valid_pe), 2) if valid_pe else None,
            'pe_max': round(max(valid_pe), 2) if valid_pe else None,
            'pe_med': round(sorted(valid_pe)[len(valid_pe)//2], 2) if valid_pe else None,
            'samples': len(valid_pe),
        })
    # 按 PE 分位降序（最贵在前），N/A 沉底
    items.sort(key=lambda x: (-1 if x['pe_pct'] is None else -x['pe_pct'], x['name']))
    return items


def main():
    by_code, name_of, total = collect_history()
    items = build_output(by_code, name_of)
    if not items:
        raise SystemExit('[valuation] 无有效数据，不写文件')
    data_date = max(h[-1][0] for h in by_code.values())
    out = {
        'source': '东方财富 datacenter-web · RPT_VALUEINDUSTRY_DET',
        'date': data_date,
        'lookback_years': LOOKBACK_YEARS,
        'count': len(items),
        'total_rows': total,
        'items': items,
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8', newline='\n')
    print(f'[valuation] 写入 {OUT_JSON} ({OUT_JSON.stat().st_size:,} bytes), 板块 {len(items)}, 数据日期 {data_date}')
    print('[valuation] 最贵:', [(x['name'], x['pe_pct']) for x in items[:3]])
    print('[valuation] 最便宜:', [(x['name'], x['pe_pct']) for x in items[-3:]])


if __name__ == '__main__':
    main()
