#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
news_update.py - Multi-source news aggregator
Sources: 东方财富 (98dou) | 新浪财经 (mix API) | 雪球 (livenews API)
Intelligent features: cross-source dedup, importance scoring, source badges
"""
import json, sys, os, re
from datetime import datetime
from difflib import SequenceMatcher
import urllib.request
import requests as req_lib

sys.stdout.reconfigure(encoding='utf-8')

NEWS_JSON = r'C:\Users\china\.qclaw\workspace\news_data.json'
NEWS_HTML = r'C:\Users\china\.qclaw\workspace\news.html'

IMPORTANT_KEYWORDS = [
    '央行', '降息', '加息', '降准', '政策', '暴跌', '大涨', '熔断',
    '制裁', '关税', '突破', '崩盘', '危机', '救市', '利好', '利空',
    'GDP', 'CPI', 'PMI', '利率', '定增', '回购', '分红',
    '证监会', '银保监会', '中央', '国务院', '政治局',
    '涨停', '跌停', '退市', 'ST', '立案', '调查'
]

def ua():
    return {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120'}

def parse_time_ms(time_str):
    try:
        return int(datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S').timestamp() * 1000)
    except:
        return 0

# ==================== Source: 东方财富 (via 98dou) ====================
def fetch_eastmoney():
    """Fetch from 98dou API (wraps 东方财富 7x24 + announcements)"""
    items = []
    for type_code, category in [(102, 'fast'), (101, 'fast'), (103, 'ann')]:
        url = f'https://api.98dou.cn/api/hotlist/eastmoney?type={type_code}'
        try:
            req = urllib.request.Request(url, headers=ua())
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode('utf-8'))
            raw_items = data.get('data', [])
            for item in raw_items:
                ts = item.get('time', '')
                items.append({
                    'id': 'em' + str(item.get('id_original', item.get('id', ''))),
                    'time_str': ts,
                    'time': parse_time_ms(ts),
                    'title': item.get('title', ''),
                    'text': item.get('content', item.get('title', '')),
                    'source': '东方财富',
                    'category': category,
                    'stock': '',
                    'url': item.get('url', '') or item.get('mobileUrl', ''),
                    'importance': ''
                })
            print(f'  东方财富(type={type_code}): {len(raw_items)} items')
        except Exception as e:
            print(f'  东方财富(type={type_code}) error: {e}')
    return items

# ==================== Source: 新浪财经 (mix API) ====================
def fetch_sina():
    """Fetch from 新浪财经 roll news API"""
    items = []
    # lid=2509: 滚动新闻, lid=2516: 新浪头条, lid=2517: 7x24快讯
    for lid, category in [('2509', 'fast'), ('2516', 'fast')]:
        url = f'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid={lid}&k=&num=30'
        try:
            req = urllib.request.Request(url, headers=ua())
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode('utf-8'))
            raw_items = data.get('result', {}).get('data', [])
            for item in raw_items:
                title = item.get('title', '')
                intro = item.get('intro', '')
                raw_time = item.get('intime', '') or item.get('ctime', '0')
                try:
                    ts_ms = int(raw_time) * 1000
                except:
                    ts_ms = 0
                items.append({
                    'id': 'sina' + item.get('docid', ''),
                    'time_str': '',
                    'time': ts_ms,
                    'title': title,
                    'text': intro or title,
                    'source': '新浪财经',
                    'category': category,
                    'stock': '',
                    'url': item.get('url', ''),
                    'sub_source': item.get('media_name', ''),
                    'importance': ''
                })
            print(f'  新浪财经(lid={lid}): {len(raw_items)} items')
        except Exception as e:
            print(f'  新浪财经(lid={lid}) error: {e}')
    return items

# ==================== Source: 网易财经 (3g.163.com touch API) ====================
def fetch_netease():
    """Fetch from 网易财经 by scraping money.163.com homepage (API deprecated)"""
    items = []
    try:
        url = 'https://money.163.com/'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'zh-CN,zh;q=0.9'
        })
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
        # 网易 may use UTF-8 or GBK
        html = raw.decode('utf-8', errors='ignore')

        # Extract article blocks: <li> containing <a href="...163.com/dy/article/..."> + timestamp
        blocks = re.split(r'<li[^>]*>', html)
        seen_urls = set()
        for block in blocks:
            link_m = re.search(r'<a[^>]+href="(https?://[^"]*163\.com/(?:dy|money)/article/[^"]+)"[^>]*>([^<]{8,120})</a>', block)
            if not link_m:
                continue
            article_url = link_m.group(1)
            if article_url in seen_urls:
                continue
            title = link_m.group(2).strip()
            if len(title) < 8:
                continue
            seen_urls.add(article_url)

            # Extract timestamp from the same block
            time_m = re.search(r'(\d{4}-\d{2}-\d{2}[\s ]\d{2}:\d{2}:\d{2})', block)
            raw_time = time_m.group(1) if time_m else ''
            ts_ms = 0
            if raw_time:
                try:
                    dt = datetime.strptime(raw_time, '%Y-%m-%d %H:%M:%S')
                    ts_ms = int(dt.timestamp() * 1000)
                except:
                    pass

            items.append({
                'id': '163' + str(abs(hash(article_url)) % 10**10),
                'time_str': raw_time,
                'time': ts_ms,
                'title': title,
                'text': title,
                'source': '网易财经',
                'category': 'fast',
                'stock': '',
                'url': article_url,
                'importance': ''
            })
        print(f'  网易财经: {len(items)} items (网页抓取)')
    except Exception as e:
        print(f'  网易财经 error: {e}')
    return items

# ==================== Source: 雪球 (hot posts API) ====================
def fetch_xueqiu_hotposts():
    """Fetch from 雪球 hot/list.json API (authenticated via saved cookies)"""
    items = []
    cookie_file = r'C:\Users\china\.qclaw\workspace\xueqiu_cookies.txt'
    if not os.path.exists(cookie_file):
        print('  雪球热帖: cookies文件不存在, 跳过')
        return items
    try:
        with open(cookie_file, 'r', encoding='utf-8') as f:
            cookie_str = f.read().strip()
        if not cookie_str:
            print('  雪球热帖: cookies为空, 跳过')
            return items

        url = 'https://xueqiu.com/statuses/hot/list.json?type=within24hours'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120',
            'Cookie': cookie_str,
            'Accept': 'application/json',
            'Referer': 'https://xueqiu.com/'
        })
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode('utf-8'))
        raw_items = data.get('items', [])
        for item in raw_items:
            title = item.get('title', '')
            if not title:
                text = item.get('text', '')
                title = text[:80] + ('...' if len(text) > 80 else '')
            else:
                text = item.get('text', title)
            # 提取stock标签
            stocks = []
            tags = item.get('stockCategory', item.get('tagList', []))
            if isinstance(tags, list):
                for t in tags:
                    if isinstance(t, dict) and 'stockName' in t:
                        stocks.append(t['stockName'])

            created = item.get('created_at', 0)
            # created可能是时间戳或字符串
            try:
                ts_ms = int(created) if created else 0
                if ts_ms > 1e15:  # 微秒转毫秒
                    ts_ms //= 1000
            except:
                ts_ms = 0

            items.append({
                'id': 'xqhot' + str(item.get('id', '')),
                'time_str': '',
                'time': ts_ms,
                'title': title,
                'text': text,
                'source': '雪球热帖',
                'category': 'hot',
                'stock': ','.join(stocks[:3]),
                'url': f'https://xueqiu.com/{item.get("user", {}).get("screenName", "")}/{item.get("id", "")}' if item.get('id') else '',
                'importance': ''
            })
        print(f'  雪球热帖: {len(raw_items)} items')
    except Exception as e:
        print(f'  雪球热帖 error: {e}')
    return items

# ==================== Source: 雪球 (livenews API) ====================
def fetch_xueqiu():
    """Fetch from 雪球 livenews API (uses requests for WAF)"""
    items = []
    try:
        s = req_lib.Session()
        s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120'})

        # Visit /hq first to get past WAF
        s.get('https://xueqiu.com/hq', timeout=10)

        # Fetch livenews
        r = s.get(
            'https://xueqiu.com/statuses/livenews/list.json?type=all&count=30',
            headers={'Referer': 'https://xueqiu.com/hq',
                     'X-Requested-With': 'XMLHttpRequest'},
            timeout=10
        )
        data = r.json()
        raw_items = data.get('items', [])
        for item in raw_items:
            text = item.get('text', '')
            title = text[:80] + ('...' if len(text) > 80 else '')
            # 雪球target已含http开头时直接用,否则补域名
            tgt = item.get('target', '')
            item_url = tgt if tgt.startswith('http') else ('https://xueqiu.com' + tgt) if tgt.startswith('/') else ('https://xueqiu.com/' + tgt)
            items.append({
                'id': 'xq' + str(item.get('id', '')),
                'time_str': '',
                'time': item.get('created_at', 0),
                'title': title,
                'text': text,
                'source': '雪球',
                'category': 'fast',
                'stock': '',
                'url': item_url,
                'importance': ''
            })
        print(f'  雪球: {len(raw_items)} items')
    except Exception as e:
        print(f'  雪球 error: {e}')
    return items

# ==================== Source: 财联社 (cls.cn v3 API with sign) ====================
def fetch_cls():
    """Fetch from 财联社 v3/depth/list/1003 (SHA1→MD5 sign)"""
    import hashlib, gzip, time
    items = []
    try:
        now = int(time.time())
        params = {
            'app': 'CailianpressWeb',
            'id': '1003',
            'last_time': str(now - 7200),  # last 2 hours
            'os': 'web',
            'rn': '30',
            'sv': '8.4.6',
        }
        # Sign: sort params → concat → SHA1 → MD5
        sorted_p = sorted(params.items(), key=lambda x: x[0])
        param_str = '&'.join(f'{k}={v}' for k, v in sorted_p)
        sha1 = hashlib.sha1(param_str.encode('utf-8')).hexdigest()
        sign = hashlib.md5(sha1.encode('utf-8')).hexdigest()
        params['sign'] = sign

        from urllib.parse import urlencode
        url = 'https://www.cls.cn/v3/depth/list/1003?' + urlencode(params)
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120',
            'Referer': 'https://www.cls.cn/telegraph',
            'Accept': 'application/json',
        })
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode('utf-8'))
        raw_items = data.get('data', [])
        for item in raw_items:
            ctime = item.get('ctime', 0)
            level = item.get('level', 'C')
            # level: A=重要, B=一般, C=普通
            cat = 'macro' if level == 'A' else 'fast'
            items.append({
                'id': 'cls' + str(item.get('id', '')),
                'time_str': '',
                'time': ctime * 1000 if ctime else 0,
                'title': item.get('title', ''),
                'text': item.get('brief', '') or item.get('title', ''),
                'source': '财联社',
                'category': cat,
                'stock': '',
                'url': f'https://www.cls.cn/detail/{item.get("id", "")}' if item.get('id') else '',
                'importance': ''
            })
        print(f'  财联社: {len(raw_items)} items')
    except Exception as e:
        print(f'  财联社 error: {e}')
    return items

# ==================== Source: 华尔街见闻 (wallstreetcn lives API) ====================
def fetch_wallstreetcn():
    """Fetch from 华尔街见闻 apiv1/content/lives"""
    items = []
    try:
        url = 'https://api-prod.wallstreetcn.com/apiv1/content/lives?channel=global-channel&client=pc&cursor=0&limit=40'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120',
            'Accept': 'application/json',
            'Referer': 'https://wallstreetcn.com/live/global',
        })
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
        if resp.headers.get('Content-Encoding') == 'gzip':
            import gzip
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode('utf-8'))
        raw_items = data.get('data', {}).get('items', [])
        for item in raw_items:
            title = item.get('title', '') or item.get('content_text', '')[:80]
            text = item.get('content_text', '') or item.get('title', '')
            display_time = item.get('display_time', '') or item.get('created_at', '')
            try:
                ts_ms = int(display_time) * 1000 if isinstance(display_time, (int, float)) else 0
            except:
                ts_ms = 0
            # Extract related stock info
            symbols = item.get('symbols', [])
            stock_names = []
            for sym in (symbols or []):
                if isinstance(sym, dict):
                    stock_names.append(sym.get('name', ''))

            # 华尔街见闻快讯没有独立详情页，直接链接到快讯列表页
            item_url = 'https://wallstreetcn.com/live/global'

            items.append({
                'id': 'wscn' + str(item.get('id', '')),
                'time_str': '',
                'time': ts_ms,
                'title': title,
                'text': text,
                'source': '华尔街见闻',
                'category': 'fast',
                'stock': ','.join(stock_names[:3]),
                'url': item_url,
                'importance': ''
            })
        print(f'  华尔街见闻: {len(raw_items)} items')
    except Exception as e:
        print(f'  华尔街见闻 error: {e}')
    return items

# ==================== Source: 同花顺快讯 (thsgd/realtimenews.js) ====================
def fetch_ths():
    """Fetch from 同花顺 7x24快讯 JS endpoint (GBK encoding)"""
    items = []
    try:
        url = 'http://stock.10jqka.com.cn/thsgd/realtimenews.js'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120',
            'Accept': '*/*',
            'Referer': 'http://stock.10jqka.com.cn/',
        })
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read()
        # 同花顺 JS 文件是 GBK 编码
        text = raw.decode('gbk', errors='ignore')

        # Extract thsRss JSON from JS variable assignment
        m = re.search(r'var\s+thsRss\s*=\s*(\{.+\});?\s*$', text, re.DOTALL)
        if not m:
            print('  同花顺: 未找到 thsRss 数据')
            return items

        ths_data = json.loads(m.group(1))
        raw_items = ths_data.get('item', [])

        for item in raw_items:
            title = item.get('title', '').strip()
            content = item.get('content', '').strip()
            pub_date = item.get('pubDate', '')  # e.g. "2026/06/19 14:51"
            seq = item.get('seq', '')
            item_url = item.get('url', '')

            if not title:
                continue

            ts_ms = 0
            try:
                dt = datetime.strptime(pub_date, '%Y/%m/%d %H:%M')
                ts_ms = int(dt.timestamp() * 1000)
            except:
                pass

            stock_code = item.get('stockCode', '') or ''

            items.append({
                'id': 'ths' + str(seq),
                'time_str': pub_date,
                'time': ts_ms,
                'title': title,
                'text': content or title,
                'source': '同花顺',
                'category': 'fast',
                'stock': stock_code,
                'url': item_url,
                'importance': ''
            })
        print(f'  同花顺: {len(raw_items)} items')
    except Exception as e:
        print(f'  同花顺 error: {e}')
    return items

# ==================== Cross-source detection ====================
def compute_similarity(a, b):
    """Simple title similarity"""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:30], b[:30]).ratio()

def mark_importance(items):
    """Mark items with importance flags: ⭐ keywords, 🔥 multi-source"""
    if not items:
        return items

    # ⭐ Keyword importance
    for item in items:
        text = (item.get('title', '') + ' ' + item.get('text', '')).lower()
        for kw in IMPORTANT_KEYWORDS:
            if kw.lower() in text:
                item['importance'] = 'important'
                break

    # 🔥 Cross-source hot detection (same event from different sources)
    for i in range(len(items)):
        if items[i]['importance'] == 'hot':
            continue
        src_a = items[i]['source']
        for j in range(i + 1, len(items)):
            src_b = items[j]['source']
            if src_a == src_b:
                continue
            sim = compute_similarity(
                items[i].get('title', ''),
                items[j].get('title', '')
            )
            if sim > 0.45:
                items[i]['importance'] = 'hot'
                items[j]['importance'] = 'hot'
                break

    return items

# ==================== Main ====================
def main():
    print('=== 多源新闻聚合 ===')
    print(f'Current time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')

    # Fetch from all sources
    all_items = []

    print('1. 东方财富...')
    all_items.extend(fetch_eastmoney())

    print('2. 新浪财经...')
    all_items.extend(fetch_sina())

    print('3. 网易财经...')
    all_items.extend(fetch_netease())

    print('4. 雪球...')
    all_items.extend(fetch_xueqiu())

    print('5. 雪球热帖(热门博文)...')
    all_items.extend(fetch_xueqiu_hotposts())

    print('6. 财联社...')
    all_items.extend(fetch_cls())

    print('7. 华尔街见闻...')
    all_items.extend(fetch_wallstreetcn())

    # Deduplicate by id
    seen = set()
    unique_items = []
    for item in all_items:
        if item['id'] not in seen:
            seen.add(item['id'])
            unique_items.append(item)

    # Sort by time desc (newest first)
    unique_items.sort(key=lambda x: x.get('time', 0), reverse=True)

    # Mark importance
    unique_items = mark_importance(unique_items)

    # Stats
    source_counts = {}
    for item in unique_items:
        s = item.get('source', '未知')
        source_counts[s] = source_counts.get(s, 0) + 1

    hot_count = sum(1 for x in unique_items if x.get('importance') == 'hot')
    important_count = sum(1 for x in unique_items if x.get('importance') == 'important')

    print(f'\n结果:')
    for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f'  {src}: {cnt}')
    print(f'  总计: {len(unique_items)}')
    print(f'  多源🔥: {hot_count}, 重要⭐: {important_count}')

    # Save JSON
    news_data = {
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(unique_items),
        'source_stats': source_counts,
        'hot_count': hot_count,
        'important_count': important_count,
        'items': unique_items[:200]
    }

    with open(NEWS_JSON, 'w', encoding='utf-8') as f:
        json.dump(news_data, f, ensure_ascii=False, indent=2)
    print(f'\n已保存 {len(unique_items)} 条到 {NEWS_JSON}')

    # Inject into news.html
    inject_into_html(unique_items[:200])

    # Sync to GitHub
    try:
        sync_script = r'C:\Users\china\.qclaw\workspace\sync_vibe_to_github.py'
        if os.path.exists(sync_script):
            import subprocess
            python_exe = os.environ.get('QCLAW_PYTHON_BINARY', sys.executable)
            subprocess.run([python_exe, sync_script], check=False)
    except Exception as e:
        print(f'Sync error: {e}')

def inject_into_html(items):
    """Inject data into news.html for file:// access"""
    try:
        with open(NEWS_HTML, 'r', encoding='utf-8') as f:
            html = f.read()

        # Build compact JSON
        compact_items = []
        for item in items:
            compact_items.append({
                'id': item['id'],
                'time': item.get('time', 0),
                'title': item.get('title', ''),
                'text': item.get('text', ''),
                'source': item.get('source', ''),
                'category': item.get('category', 'fast'),
                'stock': item.get('stock', ''),
                'url': item.get('url', ''),
                'importance': item.get('importance', '')
            })
        embed_json = json.dumps(compact_items, ensure_ascii=False, separators=(',', ':'))

        # Replace _rawData = [...] (any existing data)
        # Match from 'var _rawData = [' to the first '];' that follows '}'
        new_html = re.sub(
            r'var _rawData = \[.+?\}\];',
            lambda m: 'var _rawData = ' + embed_json + ';',
            html,
            flags=re.DOTALL
        )

        # Fallback: if no match (empty array), try matching [];
        if new_html == html:
            new_html = re.sub(
                r'var _rawData = \[\];',
                lambda m: 'var _rawData = ' + embed_json + ';',
                html
            )

        if new_html != html:
            with open(NEWS_HTML, 'w', encoding='utf-8') as f:
                f.write(new_html)
            print(f'已注入新闻数据到 news.html ({len(items)}条)')
            
            # 同步到 vibe-dashboard/news.html
            try:
                vibe_path = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\news.html'
                with open(vibe_path, 'w', encoding='utf-8') as f:
                    f.write(new_html)
                print(f'已同步到 vibe-dashboard/news.html')
            except Exception as e2:
                print(f'同步 vibe-dashboard 失败: {e2}')
        else:
            print('警告: news.html _rawData 未替换')
    except Exception as e:
        print(f'注入 news.html 错误: {e}')

if __name__ == '__main__':
    main()
