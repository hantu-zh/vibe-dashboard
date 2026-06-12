# -*- coding: utf-8 -*-
"""
sync_func.py - 供各选股脚本调用的同步函数
每次选股完成后调用 sync_after_pick(task_name, picks_list)
自动更新 daily_picks.json + 同步到 GitHub Pages
"""
import json, base64, os, sys, time, ssl, urllib.request, urllib.error
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

TOKEN = 'YOUR_GITHUB_TOKEN'
REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
LOCAL_HTML = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\index.html'
LOCAL_PICKS = r'C:\Users\china\.qclaw\workspace\daily_picks.json'
API = 'https://api.github.com'
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

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
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            resp = e.read().decode('utf-8', errors='replace')
            print(f'[sync] HTTP {e.code} (attempt {attempt+1}/3): {resp[:200]}')
            if e.code == 409 and attempt < 2:
                time.sleep(2)
                sha_new = api_get(path)
                if sha_new: payload['sha'] = sha_new['sha']
                body = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(url, data=body,
                                             headers={**headers, 'Content-Type': 'application/json'},
                                             method='PUT')
                continue
            if e.code >= 500 and attempt < 2:
                time.sleep(3 ** attempt)
                continue
            return None
        except Exception as e:
            print(f'[sync] Error (attempt {attempt+1}/3): {e}')
            if attempt < 2:
                time.sleep(3 ** attempt)
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
        print('[sync] ⚠️  daily-picks-embed tag not found')
        return html
    content_start = start_idx + len(START_TAG)
    end_idx = html.find(END_TAG, content_start)
    if end_idx < 0:
        print('[sync] ⚠️  daily-picks-embed closing tag not found')
        return html
    embed_json = json.dumps(picks_dict, ensure_ascii=False, indent=2)
    result = html[:content_start] + '\n' + embed_json + '\n' + html[end_idx:]
    print(f'[sync] Embed updated: {len(picks_dict)} tasks')
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

# 供外部直接调用
if __name__ == '__main__':
    sync_to_github()


