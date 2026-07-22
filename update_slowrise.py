#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块排名数据采集 - 慢热板块基础数据源（v2）
- 数据源：东方财富 push2he（行业板块，通用名）→ Sina备用
- 输出格式：dict {板块名: 排名}（统一通用名，不再用list）
- 只保留最近5个交易日
- 存入 daily_picks.json 的 sector_rankings
"""
import json, sys, os, re, urllib.request, ssl
from datetime import datetime

DAILY_PICKS = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\daily_picks.json'
TODAY = datetime.now().strftime('%Y-%m-%d')
CTX = ssl.create_default_context()

# 2026年中国节假日
HOLIDAYS_2026 = {
    '2026-01-01','2026-01-02','2026-01-03',
    '2026-02-17','2026-02-18','2026-02-19','2026-02-20','2026-02-21','2026-02-22','2026-02-23',
    '2026-04-04','2026-04-05','2026-04-06',
    '2026-05-01','2026-05-02','2026-05-03','2026-05-04','2026-05-05',
    '2026-06-19',
    '2026-10-01','2026-10-02','2026-10-03','2026-10-04','2026-10-05','2026-10-06','2026-10-07','2026-10-08',
}

# 通用板块名称样例（用于判断数据源是否返回正确格式）
COMMON_SECTOR_SAMPLES = ['玻璃行业', '船舶制造', '传媒娱乐', '电力行业', '电子器件', '房地产', '酿酒行业', '银行', '证券', '钢铁', '半导体', '医药']

def is_trading_day():
    dt = datetime.now()
    if dt.weekday() >= 5:
        print(f'[slowrise] SKIP: {TODAY} is weekend')
        return False
    if TODAY in HOLIDAYS_2026:
        print(f'[slowrise] SKIP: {TODAY} is holiday')
        return False
    return True

def fetch_eastmoney(host='push2he.eastmoney.com'):
    """东方财富 push 接口，行业板块排名"""
    url = f'https://{host}/api/qt/clist/get?pn=1&pz=100&po=1&np=1&ut=b2884a393a59ad64002292a3e90d46a5&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f2,f3,f12,f14'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/'
        })
        with urllib.request.urlopen(req, timeout=20, context=CTX) as resp:
            raw = resp.read()
            data = json.loads(raw.decode('utf-8-sig') if raw[:3] == b'\xef\xbb\xbf' else raw.decode('utf-8'))
        items = data.get('data', {}).get('diff', [])
        if not items:
            return None
        rankings = {}
        for i, item in enumerate(items):
            name = item.get('f14', '')
            if name:
                rankings[name] = i + 1
        return rankings
    except Exception as e:
        print(f'[slowrise] {host} failed: {e}')
        return None

def fetch_sina():
    """Sina 行业板块排行备用"""
    url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20, context=CTX) as resp:
            text = resp.read().decode('gb18030', errors='replace')
        # 使用非贪婪匹配，匹配到分号为止
        m = re.search(r'=\s*(\{[^;]+\})', text, re.DOTALL)
        if not m:
            # 备用：匹配整个var赋值语句
            m = re.search(r'var\s+\w+\s*=\s*(\{.*?\})\s*;', text, re.DOTALL)
        if not m:
            print(f'[slowrise] Sina: cannot extract JSON')
            return None
        obj_str = m.group(1)
        try:
            obj = json.loads(obj_str)
        except json.JSONDecodeError as e:
            print(f'[slowrise] Sina JSON parse error: {e}')
            return None
        rankings = {}
        for i, (code, val) in enumerate(obj.items()):
            parts = val.split(',')
            name = parts[1].strip() if len(parts) > 1 else code
            if name and len(name) > 1:
                rankings[name] = i + 1
        return rankings if rankings else None
    except Exception as e:
        print(f'[slowrise] Sina failed: {e}')
        import traceback
        traceback.print_exc()
        return None

def is_common_format(rankings):
    """判断是否返回了通用板块名格式（而非细分名）"""
    if not rankings:
        return False
    keys = list(rankings.keys())
    # 检查是否包含通用板块名样例
    common_count = sum(1 for k in keys if any(s in k for s in COMMON_SECTOR_SAMPLES))
    # 也检查是否包含细分板块名（细分名通常是2-3字的短名称，不含"行业"后缀）
    # 标准行业板块：玻璃行业/电力行业/房地产等（4字，含"行业"）
    # 细分子行业：医药/军工/白酒等（2-3字，无"行业"后缀）
    detail_count = sum(1 for k in keys if len(k) <= 3 and not k.endswith('行业'))
    print(f'[slowrise] Format check: common={common_count}, short_detail={detail_count}, total={len(rankings)}')
    print(f'[slowrise] Sample names: {keys[:5]}')
    # 通用格式：细分短名称占比低于25%（说明是标准行业名）AND 包含至少3个样例
    detail_ratio = detail_count / len(rankings) if rankings else 1.0
    return common_count >= 3 and detail_ratio < 0.25

def main():
    if not is_trading_day():
        sys.exit(0)

    rankings = None
    
    # 主数据源：东方财富
    eastmoney_rankings = fetch_eastmoney('push2he.eastmoney.com')
    
    # 评估东方财富数据源
    if eastmoney_rankings and is_common_format(eastmoney_rankings):
        rankings = eastmoney_rankings
        print(f'[slowrise] Using Eastmoney ({len(rankings)} sectors)')
    else:
        if eastmoney_rankings:
            print(f'[slowrise] Eastmoney format incompatible ({len(eastmoney_rankings)} sectors), trying alternatives...')
        else:
            print(f'[slowrise] Eastmoney failed, trying alternatives...')
        
        # Fallback: Sina行业板块 API（返回标准行业名称，板块名每日一致可追踪）
        # 注：Sina行业板块 70+ 个标准行业名（玻璃行业/电力行业等，4字），是稳定的追踪数据源
        try:
            import re as _re
            sina_url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
            req = urllib.request.Request(sina_url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'})
            with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
                text = r.read().decode('gb18030', errors='replace')
            m = _re.search(r'=\s*(\{[^;]+\})', text, _re.DOTALL)
            if not m:
                m = _re.search(r'var\s+\w+\s*=\s*(\{.*?\})\s*;', text, _re.DOTALL)
            if m:
                obj = json.loads(m.group(1))
                sina_rankings = {}
                for i, (code, val) in enumerate(obj.items()):
                    parts = val.split(',')
                    name = parts[1].strip() if len(parts) > 1 else code
                    if name and len(name) > 1:
                        sina_rankings[name] = i + 1
                if len(sina_rankings) >= 20 and is_common_format(sina_rankings):
                    rankings = sina_rankings
                    print(f'[slowrise] Using Sina行业板块 fallback ({len(rankings)} sectors)')
                else:
                    print(f'[slowrise] Sina行业板块 format check failed, skip slowrise today')
            else:
                print(f'[slowrise] Sina行业板块: cannot parse, skip slowrise today')
        except Exception as e:
            print(f'[slowrise] Sina行业板块 failed: {e}, skip slowrise today')
        
        # 如仍有需要，尝试 Eastmoney板块涨跌（板块名固定，但分类与Sina不同）
        if not rankings:
            try:
                url = ('https://push2.eastmoney.com/api/qt/clist/get'
                       '?pn=1&pz=100&po=1&np=1&fltt=2&invt=2'
                       '&fid=f3&fs=m:90+t:2'
                       '&fields=f12,f14,f3')
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
                with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
                    data = json.loads(r.read().decode('utf-8'))
                items = (data.get('data', {}) or {}).get('diff', [])
                if items:
                    em_rankings = {}
                    for i, item in enumerate(items):
                        name = item.get('f14', '')
                        chg = item.get('f3', 0)
                        if name and chg is not None:
                            em_rankings[name] = i + 1
                    if len(em_rankings) >= 20 and is_common_format(em_rankings):
                        rankings = em_rankings
                        print(f'[slowrise] Using Eastmoney板块涨跌 ({len(rankings)} sectors)')
                    else:
                        print(f'[slowrise] Eastmoney板块涨跌 format failed, skip slowrise today')
                else:
                    print(f'[slowrise] Eastmoney板块涨跌: no data, skip slowrise today')
            except Exception as e:
                print(f'[slowrise] Eastmoney板块涨跌 failed: {e}, skip slowrise today')

    if not rankings:
        print(f'[slowrise] FAILED: all sources failed')
        sys.exit(1)

    print(f'[slowrise] Got {len(rankings)} sectors')
    print(f'[slowrise] Top 5: {sorted(rankings.items(), key=lambda x: x[1])[:5]}')

    # 写入 daily_picks.json（用于Dashboard的快热板块展示）
    picks = {}
    if os.path.exists(DAILY_PICKS):
        try:
            with open(DAILY_PICKS, 'r', encoding='utf-8') as f:
                picks = json.load(f)
        except:
            pass

    if 'sector_rankings' not in picks:
        picks['sector_rankings'] = {}
    picks['sector_rankings'][TODAY] = rankings

    # 只保留最近5个交易日
    date_keys = [k for k in picks['sector_rankings'].keys()
                 if len(k) == 10 and k[4] == '-' and k[7] == '-']
    if len(date_keys) > 5:
        for d in sorted(date_keys)[:-5]:
            del picks['sector_rankings'][d]
        print(f'[slowrise] Trimmed sector_rankings to 5 days, kept: {sorted(date_keys)[-5:]}')

    with open(DAILY_PICKS, 'w', encoding='utf-8') as f:
        json.dump(picks, f, ensure_ascii=False, indent=2)
    print(f'[slowrise] Saved {len(rankings)} rankings for {TODAY} to daily_picks.json')

    # 直接写入 vibe_trend_history.json（慢热板块主数据源，不经过daily_picks.json）
    trend_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vibe_trend_history.json')
    trend = {}
    if os.path.exists(trend_path):
        try:
            with open(trend_path, 'r', encoding='utf-8') as f:
                trend = json.load(f)
        except:
            pass
    trend[TODAY] = rankings
    with open(trend_path, 'w', encoding='utf-8') as f:
        json.dump(trend, f, ensure_ascii=False, indent=2)
    print(f'[slowrise] Saved {len(rankings)} rankings for {TODAY} to vibe_trend_history.json')
    print(f'[slowrise] Total trading days: {len(picks["sector_rankings"])}')

if __name__ == '__main__':
    main()
