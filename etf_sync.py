# -*- coding: utf-8 -*-
"""
etf_sync.py - ETF实时数据同步脚本
1. 调用 etf_data.py 抓取ETF数据
2. 注入 index.html 的 etf-embed 标签
3. 推送到 GitHub
"""
import sys, os, json, base64, ssl, urllib.request, urllib.error, time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

# etf_data 与本脚本同目录，延迟导入（缺模块时优雅跳过）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# === 配置 ===
def _gh_token():
    import os
    t = os.environ.get('GITHUB_TOKEN')
    if t:
        return t
    try:
        with open(os.path.join(BASE_DIR, '.github_token'), encoding='utf-8-sig') as _f:
            return _f.read().strip()
    except Exception:
        return None
TOKEN = _gh_token()

REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
LOCAL_HTML = os.path.join(BASE_DIR, 'index.html')
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
            print(f'[etf_sync] GET {path} failed: {e}')
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
            if e.code == 409 and attempt < 2:
                time.sleep(2)
                sha_new = api_get(path)
                if sha_new:
                    payload['sha'] = sha_new['sha']
                    body = json.dumps(payload).encode('utf-8')
                    req = urllib.request.Request(url, data=body,
                                                 headers={**headers, 'Content-Type': 'application/json'},
                                                 method='PUT')
                continue
            if e.code >= 500 and attempt < 2:
                time.sleep(3 ** attempt)
                continue
            resp = e.read().decode('utf-8', errors='replace')
            print(f'[etf_sync] HTTP {e.code}: {resp[:200]}')
            return None
        except Exception as e:
            if attempt < 2:
                time.sleep(3 ** attempt)
                continue
            print(f'[etf_sync] Error: {e}')
            return None
    return None

def push_file(path, content_str, msg):
    info = api_get(path)
    sha = info['sha'] if info else None
    print(f'[etf_sync] Pushing {path} (SHA: {sha[:8] if sha else "new"}...)')
    result = api_put(path, content_str, sha, msg)
    if result:
        print(f'[etf_sync] ✅ {path} pushed')
        return True
    else:
        print(f'[etf_sync] ❌ {path} push failed')
        return False

def _replace_embed(html, tag_id, data):
    """替换指定embed标签的内容"""
    START_TAG = f'<script id="{tag_id}" type="application/json">'
    END_TAG = '</script>'
    start_idx = html.find(START_TAG)
    if start_idx < 0:
        print(f'[etf_sync] ⚠️ {tag_id} tag not found in HTML')
        return None
    content_start = start_idx + len(START_TAG)
    end_idx = html.find(END_TAG, content_start)
    if end_idx < 0:
        print(f'[etf_sync] ⚠️ {tag_id} closing tag not found')
        return None
    embed_json = json.dumps(data, ensure_ascii=False, indent=2)
    result = html[:content_start] + '\n' + embed_json + '\n' + html[end_idx:]
    print(f'[etf_sync] {tag_id} updated')
    return result

def main():
    try:
        import etf_data
    except Exception as e:
        print(f'[etf_sync] ⚠️ 依赖模块 etf_data 未找到，跳过 ETF 同步: {e}')
        return False
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[etf_sync] ===== ETF同步开始 [{now}] =====')

    # 1. 抓取ETF数据
    etf_list = etf_data.fetch_etf_data()
    if not etf_list:
        print('[etf_sync] ❌ 无ETF数据，退出')
        return False

    etf_payload = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(etf_list),
        'etfs': etf_list,
    }

    # 2. 保存本地JSON（到 vibe-dashboard 目录，Dashboard 直接读取这个文件）
    local_json = os.path.join(BASE_DIR, 'etf_data.json')
    etf_data.save_etf_data(etf_list, local_json)

    # 3. 读取index.html
    try:
        with open(LOCAL_HTML, 'r', encoding='utf-8') as f:
            html = f.read()
        print(f'[etf_sync] index.html loaded: {len(html):,} bytes')
    except Exception as e:
        print(f'[etf_sync] ❌ 读取index.html失败: {e}')
        return False

    # 4. 替换etf-embed标签
    html_new = _replace_embed(html, 'etf-embed', etf_payload)
    if html_new is None:
        print('[etf_sync] ⚠️ etf-embed标签不存在，跳过HTML注入（仅推送JSON）')
        html_new = html
    
    # 5. 推送到GitHub
    success1 = push_file('index.html', html_new, f'etf sync: {now}')
    
    # 6. 更新本地HTML
    if success1 and html_new != html:
        with open(LOCAL_HTML, 'w', encoding='utf-8') as f:
            f.write(html_new)
        print('[etf_sync] Local index.html updated')

    print(f'[etf_sync] ===== 同步完成: {"✅" if success1 else "❌"} =====\n')
    return success1

if __name__ == '__main__':
    main()
