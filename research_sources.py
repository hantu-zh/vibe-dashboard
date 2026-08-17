#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
research_sources.py - 投研信息筛选系统 · 多源新闻抓取（复用 news.html 已验证链路）
================================================================================
数据源（均已在 news_update.py 中跑通）:
  1. 东方财富 7x24/公告/焦点  via 98dou API   (主源, 最稳)
  2. 财联社 快讯             v3/depth/list/1003 (SHA1->MD5 sign)
  3. 同花顺 7x24快讯          thsgd/realtimenews.js (GBK)
  4. 新浪财经 滚动           feed.mix.sina.com.cn (mix API)
  5. 雪球 livenews           cookies + WAF bypass (可选, 需 cookie 文件)

所有函数返回归一化 item 字典:
  {id, time(ms), time_str, title, text, source, url, category, stock}
fetch_all() 汇总去重并按时间倒序返回。
"""
import json
import re
import os
import sys
import urllib.request
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
XUEQIU_COOKIE = os.path.join(BASE_DIR, 'xueqiu_cookies.txt')

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120'}


# ══════════════════════════════════════════
#  源1: 东方财富 (98dou 代理)
# ══════════════════════════════════════════
def fetch_eastmoney_98dou(types=(102, 103, 101)):
    """type: 102=7x24快讯, 103=上市公司快讯/公告, 101=红字焦点"""
    items = []
    for type_code in types:
        url = f'https://api.98dou.cn/api/hotlist/eastmoney?type={type_code}'
        cat = 'ann' if type_code == 103 else 'fast'
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            for it in data.get('data', []):
                ts = it.get('time', '')
                items.append({
                    'id': 'em' + str(it.get('id_original', it.get('id', ''))),
                    'time': _parse_time_ms(ts),
                    'time_str': ts,
                    'title': it.get('title', ''),
                    'text': it.get('content', it.get('title', '')),
                    'source': '东方财富',
                    'url': it.get('url', '') or it.get('mobileUrl', ''),
                    'category': cat,
                    'stock': '',
                })
            print(f'  [东财 type={type_code}] {len(data.get("data", []))} 条')
        except Exception as e:
            print(f'  [东财 type={type_code}] ERR {e}')
    return items


# ══════════════════════════════════════════
#  源2: 财联社 (v3 sign)
# ══════════════════════════════════════════
def fetch_cls(hours=6):
    import hashlib, time
    items = []
    try:
        now = int(time.time())
        params = {
            'app': 'CailianpressWeb', 'id': '1003',
            'last_time': str(now - hours * 3600),
            'os': 'web', 'rn': '40', 'sv': '8.4.6',
        }
        sorted_p = sorted(params.items(), key=lambda x: x[0])
        param_str = '&'.join(f'{k}={v}' for k, v in sorted_p)
        sign = hashlib.md5(hashlib.sha1(param_str.encode('utf-8')).hexdigest().encode('utf-8')).hexdigest()
        params['sign'] = sign
        from urllib.parse import urlencode
        url = 'https://www.cls.cn/v3/depth/list/1003?' + urlencode(params)
        req = urllib.request.Request(url, headers={
            'User-Agent': UA['User-Agent'], 'Referer': 'https://www.cls.cn/telegraph',
            'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if resp.headers.get('Content-Encoding') == 'gzip':
                import gzip
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode('utf-8'))
        for it in data.get('data', []):
            ctime = it.get('ctime', 0)
            level = it.get('level', 'C')
            items.append({
                'id': 'cls' + str(it.get('id', '')),
                'time': ctime * 1000 if ctime else 0,
                'time_str': '',
                'title': it.get('title', ''),
                'text': it.get('brief', '') or it.get('title', ''),
                'source': '财联社',
                'url': f'https://www.cls.cn/detail/{it.get("id", "")}' if it.get('id') else '',
                'category': 'macro' if level == 'A' else 'fast',
                'stock': '',
            })
        print(f'  [财联社] {len(data.get("data", []))} 条')
    except Exception as e:
        print(f'  [财联社] ERR {e}')
    return items


# ══════════════════════════════════════════
#  源3: 同花顺 7x24 (GBK)
# ══════════════════════════════════════════
def fetch_ths():
    items = []
    try:
        url = 'http://stock.10jqka.com.cn/thsgd/realtimenews.js'
        req = urllib.request.Request(url, headers={
            'User-Agent': UA['User-Agent'], 'Accept': '*/*',
            'Referer': 'http://stock.10jqka.com.cn/'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode('gbk', errors='ignore')
        m = re.search(r'var\s+thsRss\s*=\s*(\{.+\});?\s*$', text, re.DOTALL)
        if not m:
            print('  [同花顺] 未找到 thsRss')
            return items
        ths = json.loads(m.group(1))
        for it in ths.get('item', []):
            title = (it.get('title') or '').strip()
            if not title:
                continue
            pub = it.get('pubDate', '')
            ts = 0
            try:
                ts = int(datetime.strptime(pub, '%Y/%m/%d %H:%M').timestamp() * 1000)
            except Exception:
                pass
            items.append({
                'id': 'ths' + str(it.get('seq', '')),
                'time': ts,
                'time_str': pub,
                'title': title,
                'text': it.get('content', '') or title,
                'source': '同花顺',
                'url': it.get('url', ''),
                'category': 'fast',
                'stock': it.get('stockCode', '') or '',
            })
        print(f'  [同花顺] {len(ths.get("item", []))} 条')
    except Exception as e:
        print(f'  [同花顺] ERR {e}')
    return items


# ══════════════════════════════════════════
#  源4: 新浪财经 滚动 (mix API)
# ══════════════════════════════════════════
def fetch_sina_mix():
    items = []
    for lid in ('2509', '2516'):
        url = f'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&k=&num=30'
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            raw = data.get('result', {}).get('data', [])
            for it in raw:
                raw_time = it.get('intime', '') or it.get('ctime', '0')
                try:
                    ts = int(raw_time) * 1000
                except Exception:
                    ts = 0
                items.append({
                    'id': 'sina' + it.get('docid', ''),
                    'time': ts,
                    'time_str': '',
                    'title': it.get('title', ''),
                    'text': it.get('intro', '') or it.get('title', ''),
                    'source': '新浪财经',
                    'url': it.get('url', ''),
                    'category': 'fast',
                    'stock': '',
                })
            print(f'  [新浪 lid={lid}] {len(raw)} 条')
        except Exception as e:
            print(f'  [新浪 lid={lid}] ERR {e}')
    return items


# ══════════════════════════════════════════
#  源5: 雪球 livenews (cookie + WAF)
# ══════════════════════════════════════════
def fetch_xueqiu():
    items = []
    if not os.path.exists(XUEQIU_COOKIE):
        return items
    try:
        import requests as req_lib
        with open(XUEQIU_COOKIE, 'r', encoding='utf-8') as f:
            cookie_str = f.read().strip()
        if not cookie_str:
            return items
        s = req_lib.Session()
        s.headers.update({'User-Agent': UA['User-Agent']})
        s.get('https://xueqiu.com/hq', timeout=10)
        r = s.get('https://xueqiu.com/statuses/livenews/list.json?type=all&count=30',
                  headers={'Referer': 'https://xueqiu.com/hq', 'X-Requested-With': 'XMLHttpRequest'},
                  timeout=10)
        for it in r.json().get('items', []):
            text = it.get('text', '')
            tgt = it.get('target', '')
            item_url = tgt if tgt.startswith('http') else ('https://xueqiu.com' + tgt if tgt.startswith('/') else 'https://xueqiu.com/' + tgt)
            items.append({
                'id': 'xq' + str(it.get('id', '')),
                'time': it.get('created_at', 0),
                'time_str': '',
                'title': text[:80] + ('...' if len(text) > 80 else ''),
                'text': text,
                'source': '雪球',
                'url': item_url,
                'category': 'fast',
                'stock': '',
            })
        print(f'  [雪球] {len(r.json().get("items", []))} 条')
    except Exception as e:
        print(f'  [雪球] ERR {e}')
    return items


# ══════════════════════════════════════════
#  汇总
# ══════════════════════════════════════════
def fetch_all(include_xueqiu=True):
    all_items = []
    print('▶ 抓取多源新闻...')
    all_items += fetch_eastmoney_98dou((102, 103, 101))
    all_items += fetch_cls()
    all_items += fetch_ths()
    all_items += fetch_sina_mix()
    if include_xueqiu:
        all_items += fetch_xueqiu()

    # 去重
    seen = set()
    uniq = []
    for it in all_items:
        if it['id'] and it['id'] not in seen:
            seen.add(it['id'])
            uniq.append(it)
    # 时间倒序
    uniq.sort(key=lambda x: x.get('time', 0), reverse=True)
    print(f'  汇总去重后: {len(uniq)} 条')
    return uniq


def _parse_time_ms(time_str):
    try:
        return int(datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S').timestamp() * 1000)
    except Exception:
        return 0


if __name__ == '__main__':
    items = fetch_all()
    print('\n样例:')
    for it in items[:5]:
        print(f"  [{it['source']}] {it['title'][:40]} ({it['time_str'] or it['time']})")
