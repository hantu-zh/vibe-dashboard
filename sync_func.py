# -*- coding: utf-8 -*-
import paths
import secrets_conf
from secrets_conf import DINGTALK_WEBHOOK, DINGTALK
VIBE_WS = paths.VIBE_WS
"""
sync_func.py - 供各选股脚本调用的同步函数
每次选股完成后调用 sync_after_pick(task_name, picks_list)
自动更新 daily_picks.json + 同步到 GitHub Pages
"""
import json, base64, os, sys, time, ssl, urllib.request, urllib.error, re
from datetime import datetime
import certifi

sys.stdout.reconfigure(encoding='utf-8')

# 优先从环境变量 GITHUB_TOKEN 读取，缺失时回退到本地 .github_token 文件
def _gh_token():
    import os
    t = os.environ.get('GITHUB_TOKEN')
    if t:
        return t
    try:
        with open(paths.w(r'.github_token'), encoding='utf-8-sig') as _f:
            return _f.read().strip()
    except Exception:
        return None
TOKEN = _gh_token()

REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
LOCAL_HTML = paths.w(r'index.html')
LOCAL_PICKS = paths.w(r'daily_picks.json')
LOCAL_RPS = paths.w(r'rps.html')
LOCAL_NEWS_HTML = paths.w(r'news.html')
LOCAL_NEWS_DATA = paths.w(r'news_data.json')
LOCAL_MARKET_REVIEW = paths.w(r'market_review.json')
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

def get_remote_text(path):
    """取远程 branch 上该文件的当前文本，返回 (text, sha)；失败返回 (None, None)。

    为什么要以「远程最新版」而不是 workflow 本地副本为基础：
    定时任务 checkout 之后，可能有人手动提交过前端改动（改 CSS / 改渲染函数）。
    若直接用 workflow 本地那份旧副本注入数据再推送，就会把那些改动整体覆盖回退——
    push_file 的 SHA 校验只能防并发写冲突，防不住这种内容回退。
    """
    info = api_get(path)
    if not info or not info.get('content'):
        return None, None
    try:
        return base64.b64decode(info['content']).decode('utf-8'), info.get('sha')
    except Exception as e:
        print(f'[sync] 解码远程 {path} 失败: {e}')
        return None, None


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

def _safe_embed_json(obj):
    """生成可安全内嵌 <script> 的 JSON。

    历史 bug：embed 数据内部若含 '</script>' 字面量，html.find('</script>')
    会匹配到数据内部的标签，导致替换错位、文件被截断。
    这里把 '<' 统一转义为 \\u003c，保证数据块内永不会出现 '</script>'。
    """
    return json.dumps(obj, ensure_ascii=False, indent=2).replace('<', '\\u003c')


def update_html_embed(html, picks_dict):
    """将 picks_dict 注入 HTML 的 daily-picks-embed 标签。

    性能优化（vibe-dashboard 性能专项）：线上页面通过
    fetch('daily_picks.json', {cache:'no-store'}) 拉取全量数据，把整份
    daily_picks.json（≈1MB）内联进 HTML 对线上用户是纯冗余死重，严重拖慢首屏。
    因此 picks_dict 为 None/空时只保留空标签，作为 file:// 离线兜底的占位，
    不再内联全量数据。START_TAG 用正则匹配以兼容标签属性顺序差异。
    """
    m = re.search(r'<script\b[^>]*\bid="daily-picks-embed"[^>]*>', html)
    if not m:
        print('[sync] [WARN] daily-picks-embed tag not found')
        return html
    start_idx = m.end()
    end_idx = html.find('</script>', start_idx)
    if end_idx < 0:
        print('[sync] [WARN] daily-picks-embed closing tag not found')
        return html
    if picks_dict:
        embed_json = '\n' + _safe_embed_json(picks_dict) + '\n'
        tag = f'{len(picks_dict)} tasks'
    else:
        embed_json = ''
        tag = 'empty (online uses fetch)'
    result = html[:start_idx] + embed_json + html[end_idx:]
    print(f'[sync] Embed updated: {tag}')
    return result


def _strip_nocache_meta(html):
    """移除文档级「禁用所有缓存」meta，让 GitHub Pages/CDN 可缓存 HTML 外壳。

    数据新鲜度由 fetch 的 cache:'no-store' 保证，无需在文档级禁用缓存；
    去掉后重复访问的 HTML 外壳可由 CDN 直接命中，首屏大幅提速。
    """
    for p in [
        r'<meta http-equiv="Cache-Control"[^>]*>\n?',
        r'<meta http-equiv="Pragma"[^>]*>\n?',
        r'<meta http-equiv="Expires"[^>]*>\n?',
        r'<meta http-equiv="Surrogate-Control"[^>]*>\n?',
        r'<!-- 强制禁用所有缓存 -->\n?',
    ]:
        html = re.sub(p, '', html)
    return html

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
    embed_json = _safe_embed_json(sector_rankings)
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

    # 2. 读取 index.html（以远程最新版为基准，避免覆盖 checkout 后的手动前端改动）
    #    与 cffex.html 的 get_remote_text 防护一致：定时任务 checkout 之后若有人手动提交过
    #    前端改动（nav/CSS/渲染函数），直接用本地旧副本注入数据再推送会把那些改动整体回退。
    try:
        html, _sha = get_remote_text('index.html')
        if html is None:
            with open(LOCAL_HTML, 'r', encoding='utf-8') as f:
                html = f.read()
            print('[sync] index.html 远程读取失败，回退本地 checkout 副本')
        else:
            print(f'[sync] index.html 以远程最新版为基准注入 (sha {(_sha or "")[:8]})')
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

    # 4. 更新 HTML embed（性能优化：置空内联，线上走 fetch）
    html_new = update_html_embed(html, None)

    # 4.1 同步更新 sector-rankings-embed（RPS 优先读取此标签）
    if 'sector_rankings' in picks:
        html_new = update_sector_rankings_embed(html_new, picks['sector_rankings'])

    # 4.2 性能优化：剥离文档级禁用缓存 meta，允许 CDN 缓存 HTML 外壳
    html_new = _strip_nocache_meta(html_new)

    # 5. 推送
    success1 = push_file('index.html', html_new, f'sync: update embed ({now})')
    success2 = push_file('daily_picks.json',
                         json.dumps(picks, ensure_ascii=False, indent=2),
                         f'sync: update picks ({now})')

    # 5.1 推送 rps.html（静态页面，运行时 fetch daily_picks.json）
    try:
        with open(LOCAL_RPS, 'r', encoding='utf-8') as f:
            rps_html = _strip_nocache_meta(f.read())
        push_file('rps.html', rps_html, f'sync: update rps.html ({now})')
    except Exception as e:
        print(f'[sync] rps.html push skipped: {e}')

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
    strong_data_path = paths.w(r'strongbuy_data.json')
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
    strong_path = paths.w(r'strong.html')
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
    path = paths.w(r'vibe_trend_history.json')
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
    """同步 us_picks.json 到 GitHub（美股选股数据），并更新 index.html 中的 us-picks-embed"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 us_picks.json 到 GitHub [{now}] =====')
    
    # 1. 读取 us_picks.json
    us_picks_path = paths.w(r'us_picks.json')
    try:
        with open(us_picks_path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        print(f'[sync] us_picks.json: {len(content):,} bytes, date={data.get("date","N/A")}')
    except Exception as e:
        print(f'[sync] ❌ 读取 us_picks.json 失败: {e}')
        return False
    
    # 2. 推送 us_picks.json
    ok1 = push_file('us_picks.json', content, f'sync: update us_picks ({now})')
    
    # 3. 更新 index.html 中的 us-picks-embed
    #    同样以「远程最新版」为基准注入，避免 checkout 后手动前端改动被回退
    #    （get_remote_text 防护，与 sync_to_github / cffex 一致）。
    try:
        html, _sha = get_remote_text('index.html')
        if html is None:
            with open(LOCAL_HTML, 'r', encoding='utf-8') as f:
                html = f.read()
            print('[sync] index.html 远程读取失败，回退本地 checkout 副本')
        else:
            print(f'[sync] index.html 以远程最新版为基准注入 us-picks-embed (sha {(_sha or "")[:8]})')

        START_TAG = '<script type="application/json" id="us-picks-embed">'
        END_TAG = '</script>'
        start_idx = html.find(START_TAG)
        if start_idx >= 0:
            content_start = start_idx + len(START_TAG)
            end_idx = html.find(END_TAG, content_start)
            if end_idx >= 0:
                embed_json = _safe_embed_json(data)
                html_new = html[:content_start] + '\n' + embed_json + '\n' + html[end_idx:]
                print(f'[sync] us-picks-embed updated to date={data.get("date","N/A")}')
                
                # 同步回本地
                with open(LOCAL_HTML, 'w', encoding='utf-8') as f:
                    f.write(html_new)
                print(f'[sync] index.html 本地文件已更新')
                
                # 推送到 GitHub
                ok2 = push_file('index.html', html_new, f'sync: update us-picks-embed ({now})')
                print(f'[sync] ===== us_picks.json + embed 同步: {"✅" if ok1 and ok2 else "❌"} =====\n')
                return ok1 and ok2
            else:
                print('[sync] [WARN] us-picks-embed closing tag not found')
        else:
            print('[sync] [WARN] us-picks-embed tag not found in index.html')
    except Exception as e:
        print(f'[sync] ❌ 更新 us-picks-embed 失败: {e}')
    
    print(f'[sync] ===== us_picks.json 同步: {"✅" if ok1 else "❌"} =====\n')
    return ok1

def sync_research_to_github():
    """同步 research_data.json 到 GitHub（研报/事件追踪数据）"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 research_data.json [{now}] =====')
    # 仓库根目录为唯一权威位置（GitHub Pages 从 main 根提供）
    # 注意：局部变量名不可再叫 paths，否则会遮蔽 paths 模块导致 UnboundLocalError
    candidates = [
        paths.w(r'research_data.json'),
    ]
    content = None
    for p in candidates:
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
    """同步动态生成的 research.html 到 GitHub

    research.html 由 generate_research_html.py 整体生成（无内嵌数据槽位），
    与 cffex.html/index.html 的「注入远程基准」思路不同，这里用 get_remote_text 防护
    的等价做法：先把本地生成版与远程版本比对——若两者一致（说明最近一次生成已推送、
    或本轮没有新数据），则跳过推送，避免每 15 分钟的 routine 同步把远程疑似手动改动
    整体覆盖回退；若不一致（生成脚本确实产出了新内容），再以本地生成版覆盖远端。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 research.html [{now}] =====')
    path = paths.w(r'research.html')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        print(f'[sync] research.html loaded: {len(content):,} bytes')

        # get_remote_text 防护：远程与本地一致则跳过，保留远程版本（含任何手动改动）
        remote_content, _sha = get_remote_text('research.html')
        if remote_content is not None and remote_content == content:
            print(f'[sync] research.html 本地与远程一致 (sha {(_sha or "")[:8]})，跳过（已保护远程版本）')
            print(f'[sync] ===== research.html 同步: ✅ (no-op) =====\n')
            return True

        ok = push_file('research.html', content, f'sync: update research.html ({now})')
        print(f'[sync] ===== research.html 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] research.html 同步失败: {e}')
        return False


def sync_cffex_to_github():
    """同步 cffex_net_position.json 并注入 cffex.html 的内联数据"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 cffex (独立页面) [{now}] =====')
    path = paths.w(r'cffex_net_position.json')
    cffex_html = paths.w(r'cffex.html')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        days = len(data)
        latest = sorted(data.keys())[-1] if days else 'N/A'
        print(f'[sync] cffex_net_position.json: {len(content):,} bytes, {days} days (latest {latest})')
    except Exception as e:
        print(f'[sync] ❌ cffex_net_position.json 失败: {e}')
        return False

    # 注入 cffex.html 的内联数据 (var _cffex = {...})
    try:
        # 以「远程最新版」为基础注入，而不是 workflow 自己 checkout 的本地副本，
        # 否则会把 checkout 之后手动提交的前端改动（CSS / 渲染函数）整体覆盖回退。
        html, _sha = get_remote_text('cffex.html')
        if html is None:
            with open(cffex_html, 'r', encoding='utf-8') as f:
                html = f.read()
            print('[sync] cffex.html 远程读取失败，回退本地副本')
        else:
            print(f'[sync] cffex.html 以远程最新版为基础注入 (sha {(_sha or "")[:8]})')
        html_new = update_cffex_inline(html, data)
        ok1 = push_file('cffex.html', html_new, f'sync: update cffex.html data ({now})')
        if ok1:
            with open(cffex_html, 'w', encoding='utf-8') as f:
                f.write(html_new)
            print('[sync] Local cffex.html inline data updated')
    except Exception as e:
        print(f'[sync] ❌ cffex.html 注入失败: {e}')
        ok1 = False

    ok2 = push_file('cffex_net_position.json', content, f'sync: update cffex data ({now})')
    print(f'[sync] ===== cffex 同步: {"✅" if (ok1 and ok2) else "❌"} =====\n')
    return ok1 and ok2


def update_cffex_inline(html, cffex_data):
    """将 cffex_data（日期→品种指标）注入 cffex.html 内联变量 var _cffex = {...}"""
    marker = 'var _cffex = '
    idx = html.find(marker)
    if idx < 0:
        print('[sync] [WARN] cffex.html 找不到 var _cffex 占位')
        return html
    semi = html.find(';', idx)
    if semi < 0:
        print('[sync] [WARN] cffex.html var _cffex 缺少分号')
        return html
    embed_json = json.dumps(cffex_data, ensure_ascii=False, separators=(',', ':'))
    result = html[:idx] + marker + embed_json + ';' + html[semi+1:]
    print(f'[sync] cffex.html inline updated: {len(cffex_data)} days')
    return result


def sync_market_review_to_github():
    """同步 market_review.json（大盘点评）到 GitHub"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 market_review.json [{now}] =====')
    path = LOCAL_MARKET_REVIEW
    if not os.path.exists(path):
        print(f'[sync] market_review.json 不存在，跳过')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        print(f'[sync] market_review.json: {len(content):,} bytes, {data.get("date","N/A")} {data.get("period","")}')
        ok = push_file('market_review.json', content, f'sync: update market_review ({now})')
        print(f'[sync] ===== market_review.json 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ market_review.json 失败: {e}')
        return False


def sync_midday_to_github():
    """同步 midday_review.json（午间点评）到 GitHub（cffex.html【午间点评】模块数据源）"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 midday_review.json [{now}] =====')
    path = paths.w(r'midday_review.json')
    if not os.path.exists(path):
        print(f'[sync] midday_review.json 不存在，跳过')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        print(f'[sync] midday_review.json: {len(content):,} bytes, {data.get("date","N/A")} {data.get("period","")}')
        ok = push_file('midday_review.json', content, f'sync: update midday_review ({now})')
        print(f'[sync] ===== midday_review.json 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ midday_review.json 失败: {e}')
        return False


def sync_ai_analysis_to_github():
    """同步 AI 复盘三件套：ai_analysis_data.json / ai_analysis_report.json / ai_analysis.html

    为什么必须有这一段：ai_daily.py（采集 + 生成报告）和 ai_analysis.py（渲染页面）
    在 workflow 里每次都会跑，但产物只落在 runner 本地。原 sync_func.py 的推送清单里
    没有任何 ai_analysis 相关文件，这三个文件从未回推仓库，于是 GitHub Pages 上的
    /ai_analysis.html 永远停在最后一次本地手动推送的快照——表现为「AI 复盘没有自动运行」。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 ai_analysis [{now}] =====')
    success = True

    for rel in ('ai_analysis_data.json', 'ai_analysis_report.json',
                'ai_analysis_board_kline.json', 'ai_analysis.html'):
        path = paths.w(rel)
        if not os.path.exists(path):
            print(f'[sync] {rel} 不存在，跳过（本次 ai_daily/ai_analysis 可能未执行成功）')
            success = False
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if not push_file(rel, content, f'sync: update {rel} ({now})'):
                success = False
        except Exception as e:
            print(f'[sync] ❌ {rel} 失败: {e}')
            success = False

    print(f'[sync] ===== ai_analysis 同步: {"✅" if success else "❌"} =====\n')
    return success


def sync_task_state_to_github():
    """同步 daily_task_state.json（时点任务补跑状态）到 GitHub

    task_state.py 在本地记录「今天哪些时点任务已经跑过」，必须回推仓库，
    下一次 workflow checkout 才能拿到当天此前的完成情况，否则补跑判断永远从零开始。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    path = paths.w('daily_task_state.json')
    if not os.path.exists(path):
        print('[sync] daily_task_state.json 不存在，跳过')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        ok = push_file('daily_task_state.json', content, f'sync: update task state ({now})')
        print(f'[sync] ===== task_state 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ daily_task_state.json 失败: {e}')
        return False


def sync_speedrank_to_github():
    """同步 speedrank_history.json（升速快照）与 speedrank.html（内嵌 _embed）到 GitHub

    speedrank.py 在 workflow 中先运行：抓取涨速榜 -> 追加快照到 speedrank_history.json
    -> 刷新 speedrank.html 内嵌 var _embed。本函数把这两个本地文件统一推送，
    与面板其它数据文件走同一套 push_file（SHA 校验）机制。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 speedrank [{now}] =====')
    success = True

    # 1. speedrank_history.json
    hist_path = paths.w(r'speedrank_history.json')
    try:
        with open(hist_path, 'r', encoding='utf-8') as f:
            hist_content = f.read()
        dates = list(json.loads(hist_content).keys())
        today = dates[-1] if dates else 'N/A'
        snaps = json.loads(hist_content).get(today, {}).get('snapshots', [])
        print(f'[sync] speedrank_history.json: {len(hist_content):,} bytes, '
              f'{len(dates)} 天 (最新 {today}, {len(snaps)} 个快照)')
        if not push_file('speedrank_history.json', hist_content, f'sync: update speedrank history ({now})'):
            success = False
    except Exception as e:
        print(f'[sync] ❌ speedrank_history.json 失败: {e}')
        success = False

    # 2. speedrank.html（_embed 已由 speedrank.py 刷新为本地最新快照）
    html_path = paths.w(r'speedrank.html')
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        if not push_file('speedrank.html', html_content, f'sync: update speedrank.html ({now})'):
            success = False
    except Exception as e:
        print(f'[sync] ❌ speedrank.html 失败: {e}')
        success = False

    # 3. 运行时缓存 / 状态文件（跨 run 持久化）
    #    - speedrank_codes_cache.json：腾讯降级用的沪深A股代码列表，持久化后可抗新浪限流
    #    - speedrank_alert_state.json：卡顿告警冷却时间戳，持久化避免限流期间刷屏
    for extra in ('speedrank_codes_cache.json', 'speedrank_alert_state.json'):
        ep = paths.w(extra)
        if os.path.exists(ep):
            try:
                with open(ep, 'r', encoding='utf-8') as f:
                    content = f.read()
                if not push_file(extra, content, f'sync: update {extra} ({now})'):
                    success = False
            except Exception as e:
                print(f'[sync] ❌ {extra} 失败: {e}')
                success = False
        else:
            print(f'[sync] {extra} 不存在，跳过')

    print(f'[sync] ===== speedrank 同步: {"✅" if success else "❌"} =====\n')
    return success


# 供外部直接调用
if __name__ == '__main__':
    sync_to_github()
    sync_news_to_github()
    sync_strong_to_github()
    sync_trend_history_to_github()
    sync_us_to_github()
    sync_cffex_to_github()
    sync_market_review_to_github()
    sync_midday_to_github()
    sync_ai_analysis_to_github()
    sync_task_state_to_github()
    sync_speedrank_to_github()
    sync_research_to_github()
    sync_research_html_to_github()