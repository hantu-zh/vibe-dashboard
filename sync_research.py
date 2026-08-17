# -*- coding: utf-8 -*-
"""
sync_research.py
================
灏?research_data.json 鍜?research.html 鍚屾鎺ㄩ€佸埌 GitHub Pages
浠撳簱: hantu-zh/vibe-dashboard (main 鍒嗘敮锛岄儴缃蹭簬 vibe-dashboard/ 鐩綍)
"""
import base64, json, sys, os
sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path

import requests

# Fallback token 2026-07-13锛堢幆澧冨彉閲忎紭鍏堬級
def _gh_token():
    import os
    t = os.environ.get('GITHUB_TOKEN')
    if t:
        return t
    try:
        with open(r'C:\Users\china\.qclaw\workspace\.github_token', encoding='utf-8-sig') as _f:
            return _f.read().strip()
    except Exception:
        return None
TOKEN = _gh_token()
REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
API = 'https://api.github.com'
HEADERS = {'Authorization': f'token {TOKEN}', 'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'QClaw/1.0'}

session = requests.Session()
session.headers.update(HEADERS)

BASE_DIR = Path(__file__).parent
VIBE_DIR = BASE_DIR / 'vibe-dashboard'


def do_sha(path):
    """鑾峰彇鏂囦欢褰撳墠 SHA (鑻ヤ笉瀛樺湪杩斿洖 None)"""
    r = session.get(f'{API}/repos/{REPO}/contents/{path}', params={'ref': BRANCH}, timeout=20)
    if r.status_code == 200:
        return r.json()['sha']
    if r.status_code == 404:
        return None
    print(f'  sha lookup {path}: HTTP {r.status_code} {r.text[:100]}')
    return None


def do_put(path, content, sha, msg):
    """PUT 鏂囦欢鍒?GitHub"""
    payload = {
        'message': msg,
        'content': base64.b64encode(content.encode('utf-8')).decode('ascii'),
        'branch': BRANCH,
    }
    if sha:
        payload['sha'] = sha
    r = session.put(f'{API}/repos/{REPO}/contents/{path}', json=payload, timeout=30)
    if r.status_code in (200, 201):
        resp = r.json()
        sha8 = resp['commit']['sha'][:8]
        print(f'  OK: {path} -> {sha8}')
        return True
    else:
        print(f'  FAIL: {path} HTTP {r.status_code}: {r.text[:300]}')
        return False


def sync_file(local_rel_path, github_path, commit_msg):
    """鍚屾鍗曚釜鏂囦欢"""
    local_path = VIBE_DIR / local_rel_path
    if not local_path.exists():
        print(f'  SKIP: {local_rel_path} not found locally')
        return False
    content = local_path.read_text(encoding='utf-8')
    sha = do_sha(github_path)
    return do_put(github_path, content, sha, commit_msg)


def main():
    from datetime import datetime
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'馃攧 sync_research.py  ({ts})')
    print(f'   REPO: {REPO} / {BRANCH}')

    results = []

    # 1. research_data.json
    results.append(sync_file(
        'research_data.json',
        'research_data.json',
        f'chore: sync research_data.json ({ts})'
    ))

    # 2. research.html
    results.append(sync_file(
        'research.html',
        'research.html',
        f'chore: sync research.html ({ts})'
    ))

    # 3. 鍚屾鍛ㄦ姤 (memory/events/weekly_report_W*.md)
    events_dir = BASE_DIR / 'memory' / 'events'
    if events_dir.exists():
        for wf in sorted(events_dir.glob('weekly_report_W*.md'), key=lambda p: p.name, reverse=True):
            week_file = wf.name
            github_path = f'markdown/weekly_reports/{week_file}'
            if sync_file(f'memory/events/{week_file}', github_path, f'chore: sync {week_file} ({ts})'):
                break  # 鍙帹閫佹渶鏂颁竴浠?
    ok = sum(1 for r in results if r)
    print(f'\n鉁?GitHub Pages 鍚屾瀹屾垚 ({ok}/{len(results)} 鏂囦欢鎺ㄩ€佹垚鍔?')
    return 0


if __name__ == '__main__':
    sys.exit(main())
