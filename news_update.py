#!/usr/bin/env python3
"""news_update.py - Fetch news data from eastmoney via 98dou API and save as news_data.json"""
import json, sys, urllib.request, time, os

sys.stdout.reconfigure(encoding='utf-8')

NEWS_JSON = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\news_data.json'

def fetch_news(type_code, category):
    """Fetch news from 98dou API (wraps eastmoney data)"""
    url = f'https://api.98dou.cn/api/hotlist/eastmoney?type={type_code}'
    items = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode('utf-8'))
        raw_items = data.get('data', [])
        for item in raw_items:
            items.append({
                'id': str(item.get('id_original', item.get('id', ''))),
                'time_str': item.get('time', ''),
                'title': item.get('title', ''),
                'text': item.get('content', item.get('title', '')),
                'source': '东方财富',
                'category': category,
                'stock': '',
                'url': item.get('url', '') or item.get('mobileUrl', '')
            })
    except Exception as e:
        print(f'{category} fetch error: {e}')
    return items

def main():
    print('Fetching news data...')
    
    # type 102 = 7x24全球直播, type 101 = 红字焦点快讯, type 103 = 上市公司快讯
    fast_items = fetch_news(102, 'fast')   # 7x24快讯
    focus_items = fetch_news(101, 'fast')  # 红字焦点
    stock_items = fetch_news(103, 'ann')   # 上市公司快讯(含公告)
    
    # Merge and deduplicate by id
    all_items = fast_items + focus_items + stock_items
    seen = set()
    unique_items = []
    for item in all_items:
        if item['id'] not in seen:
            seen.add(item['id'])
            unique_items.append(item)
    
    # Sort by time_str desc
    unique_items.sort(key=lambda x: x.get('time_str', ''), reverse=True)
    
    fast_count = sum(1 for x in unique_items if x['category'] == 'fast')
    ann_count = sum(1 for x in unique_items if x['category'] == 'ann')
    
    print(f'Fast: {fast_count}, Ann: {ann_count}, Total: {len(unique_items)}')
    
    news_data = {
        'update_time': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(unique_items),
        'fast_count': fast_count,
        'ann_count': ann_count,
        'items': unique_items[:150]  # cap at 150 items
    }
    
    with open(NEWS_JSON, 'w', encoding='utf-8') as f:
        json.dump(news_data, f, ensure_ascii=False, indent=2)
    
    print(f'Saved {len(unique_items)} items to {NEWS_JSON}')
    
    # Sync to GitHub
    try:
        sync_script = r'C:\Users\china\.qclaw\workspace\sync_vibe_to_github.py'
        if os.path.exists(sync_script):
            os.system(f'python {sync_script}')
    except Exception as e:
        print(f'Sync error: {e}')

if __name__ == '__main__':
    main()