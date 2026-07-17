# -*- coding: utf-8 -*-
"""
sync_func.py - 供各选股脚本调用的同步函数
每次选股完成后调用 sync_after_pick(task_name, picks_list)
自动更新 daily_picks.json + 同步到 GitHub Pages
"""
import json, base64, os, sys, time, ssl, urllib.request, urllib.error
from datetime import datetime
import certifi

sys.stdout.reconfigure(encoding='utf-8')

# 优先从环境变量读取，fallback 到当前有效 token（2026-07-13 更新）
FALLBACK_TOKEN = '[REDACTED_PAT]'
TOKEN = os.environ.get('GITHUB_TOKEN') or FALLBACK_TOKEN
REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
LOCAL_HTML = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\index.html'
LOCAL_PICKS = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\daily_picks.json'
LOCAL_NEWS_HTML = r'C:\Users\china\.qclaw\workspace\news.html'
LOCAL_NEWS_DATA = r'C:\Users\china\.qclaw\workspace\news_data.json'
API = 'https://api.github.com'
ctx = ssl.create_default_context(cafile=certifi.where())
ctx.check_hostname = True
ctx.verify_mode = ssl.CERT_REQUIRED

headers = {
    'Authorization': f'token {TOKEN}',
    'Accept': 'application/vnd.github.v3+json',
    'User-Agent': 'QClaw/1.0'
}

def api_get(path):
    url = f'{API}/repos/{REPO}/contents/{path}?ref={BRANCH}'
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            print(f'[sync] GET {path} failed: {e}')
            return None

def api_put(path, content_str, sha, msg):
    url = f'{API}/repos/{REPO}/contents/{path}'
    payload = {
        'message': msg,
        'content': base64.b64encode(content_str.encode('utf-8')).decode('ascii'),
        'branch': BRANCH,
    }
    if sha:
        payload['sha'] = sha
    body = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=body,
                                 headers={**headers, 'Content-Type': 'application/json'},
                                 method='PUT')
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            resp = e.read().decode('utf-8', errors='replace')
            print(f'[sync] HTTP {e.code} (attempt {attempt+1}/{max_retries}): {resp[:200]}')
            if e.code == 409 and attempt < max_retries - 1:
                time.sleep(3)
                sha_new = api_get(path)
                if sha_new: payload['sha'] = sha_new['sha']
                body = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(url, data=body,
                                             headers={**headers, 'Content-Type': 'application/json'},
                                             method='PUT')
                continue
            if e.code >= 500 and attempt < max_retries - 1:
                time.sleep(5 ** attempt)
                continue
            return None
        except Exception as e:
            print(f'[sync] Error (attempt {attempt+1}/{max_retries}): {e}')
            if attempt < max_retries - 1:
                time.sleep(5 ** attempt)
                continue
            return None
    return None

def push_file(path, content_str, msg):
    info = api_get(path)
    sha = info['sha'] if info else None
    print(f'[sync] Pushing {path} (SHA: {sha[:8] if sha else "new"}...)')
    result = api_put(path, content_str, sha, msg)
    if result:
        print(f'[sync] ✅ {path} pushed')
        return True
    else:
        print(f'[sync] ❌ {path} push failed')
        return False

def update_html_embed(html, picks_dict):
    """将 picks_dict（任务名→股票列表）注入 HTML 的 daily-picks-embed 标签"""
    START_TAG = '<script id="daily-picks-embed" type="application/json">'
    END_TAG = '</script>'
    start_idx = html.find(START_TAG)
    if start_idx < 0:
        print('[sync] [WARN] daily-picks-embed tag not found')
        return html
    content_start = start_idx + len(START_TAG)
    end_idx = html.find(END_TAG, content_start)
    if end_idx < 0:
        print('[sync] [WARN] daily-picks-embed closing tag not found')
        return html
    embed_json = json.dumps(picks_dict, ensure_ascii=False, indent=2)
    result = html[:content_start] + '\n' + embed_json + '\n' + html[end_idx:]
    print(f'[sync] Embed updated: {len(picks_dict)} tasks')
    return result

def update_sector_rankings_embed(html, sector_rankings):
    """将 sector_rankings（日期键→板块列表）注入 HTML 的 sector-rankings-embed 标签"""
    if not sector_rankings:
        return html
    START_TAG = '<script type="application/json" id="sector-rankings-embed">'
    END_TAG = '</script>'
    start_idx = html.find(START_TAG)
    if start_idx < 0:
        print('[sync] [WARN] sector-rankings-embed tag not found')
        return html
    content_start = start_idx + len(START_TAG)
    end_idx = html.find(END_TAG, content_start)
    if end_idx < 0:
        print('[sync] [WARN] sector-rankings-embed closing tag not found')
        return html
    embed_json = json.dumps(sector_rankings, ensure_ascii=False, indent=2)
    result = html[:content_start] + '\n' + embed_json + '\n' + html[end_idx:]
    dates = sorted(sector_rankings.keys())
    print(f'[sync] sector-rankings-embed updated: {len(dates)} dates (latest: {dates[-1] if dates else "N/A"})')
    return result

def sync_to_github():
    """
    主同步函数：
    1. 读取本地 daily_picks.json
    2. 读取本地 index.html
    3. 将最新日期的选股数据注入 HTML embed
    4. 推送 index.html 和 daily_picks.json 到 GitHub
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 开始同步到 GitHub [{now}] =====')

    # 1. 读取 daily_picks.json
    try:
        with open(LOCAL_PICKS, 'r', encoding='utf-8') as f:
            picks = json.load(f)
        print(f'[sync] daily_picks.json loaded: {len(picks)} keys')
    except Exception as e:
        print(f'[sync] ❌ 读取 daily_picks.json 失败: {e}')
        return False

    # 2. 读取 index.html
    try:
        with open(LOCAL_HTML, 'r', encoding='utf-8') as f:
            html = f.read()
        print(f'[sync] index.html loaded: {len(html):,} bytes')
    except Exception as e:
        print(f'[sync] ❌ 读取 index.html 失败: {e}')
        return False

    # 3. 构建 embed 数据
    # daily_picks.json 是混合结构：日期键 + 顶层任务键 + sector_rankings
    # embed 需要包含所有数据，让 JS IIFE 归一化处理
    embed_data = {}
    
    _skip_keys = {'sector_rankings'}
    
    # 日期键 → 直接放入（保持 {"2026-05-20": {任务名: 数据}} 结构）
    date_keys = sorted([k for k in picks.keys() if len(k) == 10 and k[4] == '-' and k[7] == '-'])
    for dk in date_keys:
        embed_data[dk] = picks[dk]
    
    if date_keys:
        latest_date = date_keys[-1]
        print(f'[sync] Latest date: {latest_date}, tasks: {list(picks[latest_date].keys())}')
    
    # 顶层任务键（非日期、非特殊键）也放入 embed
    _task_keys = [k for k in picks.keys() if k not in date_keys and k not in _skip_keys]
    for tk in _task_keys:
        embed_data[tk] = picks[tk]
    if _task_keys:
        print(f'[sync] Top-level task keys in embed: {_task_keys}')

    # sector_rankings 放入顶层
    if 'sector_rankings' in picks:
        embed_data['sector_rankings'] = picks['sector_rankings']
        print(f'[sync] sector_rankings dates: {sorted(picks["sector_rankings"].keys(), reverse=True)[:3]}')

    # 4. 更新 HTML embed
    html_new = update_html_embed(html, embed_data)

    # 4.1 同步更新 sector-rankings-embed（RPS 优先读取此标签）
    if 'sector_rankings' in picks:
        html_new = update_sector_rankings_embed(html_new, picks['sector_rankings'])

    # 5. 推送
    success1 = push_file('index.html', html_new, f'sync: update embed ({now})')
    success2 = push_file('daily_picks.json',
                         json.dumps(picks, ensure_ascii=False, indent=2),
                         f'sync: update picks ({now})')

    # 6. 保存本地 HTML
    if success1:
        with open(LOCAL_HTML, 'w', encoding='utf-8') as f:
            f.write(html_new)
        print('[sync] Local index.html updated')

    print(f'[sync] ===== 同步完成: {"✅" if success1 and success2 else "❌"} =====\n')
    return success1 and success2

def sync_news_to_github():
    """
    同步新闻相关文件到 GitHub：
    1. news.html（已修复，禁用动态加载）
    2. news_data.json（最新新闻数据）
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 开始同步新闻文件到 GitHub [{now}] =====')

    # 1. 读取 news.html
    try:
        with open(LOCAL_NEWS_HTML, 'r', encoding='utf-8') as f:
            news_html = f.read()
        print(f'[sync] news.html loaded: {len(news_html):,} bytes')
    except Exception as e:
        print(f'[sync] ❌ 读取 news.html 失败: {e}')
        return False

    # 2. 读取 news_data.json
    try:
        with open(LOCAL_NEWS_DATA, 'r', encoding='utf-8') as f:
            news_data = f.read()
        print(f'[sync] news_data.json loaded: {len(news_data):,} bytes')
    except Exception as e:
        print(f'[sync] ⚠️ 读取 news_data.json 失败: {e}')
        news_data = None

    # 3. 推送 news.html
    success1 = push_file('news.html', news_html, f'sync: update news.html ({now})')

    # 4. 推送 news_data.json
    success2 = False
    if news_data:
        success2 = push_file('news_data.json', news_data, f'sync: update news_data ({now})')
    else:
        print('[sync] ⚠️ 跳过 news_data.json（读取失败）')

    print(f'[sync] ===== 新闻同步完成: {"✅" if success1 else "❌"} =====\n')
    return success1

def sync_strong_to_github():
    """同步 strong.html 和 strongbuy_data.json 到 GitHub"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 开始同步 strong 文件到 GitHub [{now}] =====')
    success = True

    # 0. 先读取 strongbuy_data.json 获取最新数据
    strong_data_path = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\strongbuy_data.json'
    strong_data_content = None
    try:
        with open(strong_data_path, 'r', encoding='utf-8') as f:
            strong_data_content = f.read()
        print(f'[sync] strongbuy_data.json loaded: {len(strong_data_content):,} bytes (from vibe-dashboard)')
        if not push_file('strongbuy_data.json', strong_data_content, f'sync: update strongbuy_data ({now})'):
            success = False
    except Exception as e:
        print(f'[sync] ⚠️ 读取 strongbuy_data.json 失败: {e} (可能不需要同步)')

    # 1. 读取 strong.html 并更新嵌入的 _data（避免页面加载时显示旧数据）
    strong_path = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\strong.html'
    try:
        with open(strong_path, 'r', encoding='utf-8') as f:
            strong_html = f.read()
        print(f'[sync] strong.html loaded: {len(strong_html):,} bytes')

        # 从 strongbuy_data.json 提取数据更新嵌入的 _data
        if strong_data_content:
            import re
            data_obj = json.loads(strong_data_content)
            # 重建嵌入的 _data：保留 stocks（新高数据），用 strongbuy_data 的 updated/yimeng
            embed_data = {
                "updated": data_obj.get("updated", ""),
                "stocks": data_obj.get("stocks", []),
                "yimeng": data_obj.get("yimeng", [])
            }
            embed_json = json.dumps(embed_data, ensure_ascii=False, separators=(',', ':'))
            # 替换 var _data={...}; 那一行（无空格/有空格均可）
            # JSON 内含逗号，所以用 non-greedy .*? 匹配到第一个 };
            new_html = re.sub(
                r'var _data\s*=\s*\{.*?\};',
                f'var _data={embed_json};',
                strong_html,
                flags=re.DOTALL
            )
            if new_html != strong_html:
                print(f'[sync] strong.html embedded _data updated to: {embed_data["updated"]}')
                strong_html = new_html
                # 同步回本地文件，避免下次启动又是旧的
                with open(strong_path, 'w', encoding='utf-8') as f:
                    f.write(strong_html)
                print(f'[sync] strong.html 本地文件已更新')
            else:
                print(f'[sync] strong.html embedded _data 未变化')

        if not push_file('strong.html', strong_html, f'sync: update strong.html ({now})'):
            success = False
    except Exception as e:
        print(f'[sync] ❌ 同步 strong.html 失败: {e}')
        success = False

    print(f'[sync] ===== strong 文件同步完成: {"✅" if success else "❌"} =====\n')
    return success


def sync_trend_history_to_github():
    """同步 vibe_trend_history.json 到 GitHub（慢热板块历史数据）"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 vibe_trend_history.json [{now}] =====')
    path = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\vibe_trend_history.json'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        dates = list(json.loads(content).keys())
        print(f'[sync] vibe_trend_history.json: {len(content):,} bytes, {len(dates)} days ({dates})')
        ok = push_file('vibe_trend_history.json', content, f'sync: update trend history ({now})')
        print(f'[sync] ===== trend history 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ vibe_trend_history.json 失败: {e}')
        return False

def sync_us_to_github():
    """同步 us_picks.json 到 GitHub（美股选股数据）"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 us_picks.json 到 GitHub [{now}] =====')
    path = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\us_picks.json'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        print(f'[sync] us_picks.json: {len(content):,} bytes, date={data.get("date","N/A")}')
        ok = push_file('us_picks.json', content, f'sync: update us_picks ({now})')
        print(f'[sync] ===== us_picks.json 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ us_picks.json 失败: {e}')
        return False

def sync_research_to_github():
    """同步 research_data.json 到 GitHub（研报/事件追踪数据）"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 research_data.json [{now}] =====')
    # 优先从 vibe-dashboard 读，兜底从 workspace 根目录读
    paths = [
        r'C:\Users\china\.qclaw\workspace\vibe-dashboard\research_data.json',
        r'C:\Users\china\.qclaw\workspace\research_data.json',
    ]
    content = None
    for p in paths:
        try:
            with open(p, 'r', encoding='utf-8') as f:
                content = f.read()
            print(f'[sync] research_data.json loaded from {p}')
            break
        except Exception:
            continue
    if content is None:
        print('[sync] ⚠️ research_data.json 未找到，跳过')
        return False
    data = json.loads(content)
    stats = data.get('stats', {})
    print(f'[sync] research_data.json: {len(content):,} bytes, date={data.get("date","N/A")}, processed={stats.get("processed","?")}, alerts={stats.get("alerts","?")}, filtered={stats.get("filtered","?")}')
    ok = push_file('research_data.json', content, f'sync: update research_data.json ({now})')
    print(f'[sync] ===== research_data.json 同步: {"✅" if ok else "❌"} =====\n')
    return ok


def sync_research_html_to_github():
    """同步动态生成的 research.html 到 GitHub"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 research.html [{now}] =====')
    path = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\research.html'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f'[sync] research.html loaded: {len(content):,} bytes')
        ok = push_file('research.html', content, f'sync: update research.html ({now})')
        print(f'[sync] ===== research.html 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] research.html 同步失败: {e}')
        return False


# 供外部直接调用
if __name__ == '__main__':
    sync_to_github()
    sync_news_to_github()
    sync_strong_to_github()
    sync_trend_history_to_github()
    sync_us_to_github()