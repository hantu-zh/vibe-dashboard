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
            # 404 = 远程还没有这个文件（新文件首次推送），属正常路径，不打印成错误
            if not (isinstance(e, urllib.error.HTTPError) and e.code == 404):
                print(f'[sync] GET {path} failed: {e}')
            return None

def _git_blob_text(blob_sha):
    """用 Git Data API 读 blob 原文（Accept: raw，无 1MB 限制、无 CDN 缓存）。

    背景（2026-10-13 定位）：Contents API 对 >1MB 的文件【不返回 content】
    （实测 daily_picks.json 2.10MB -> content 为空、encoding=none），
    于是 get_remote_text 对大文件必然失败 -> merge_picks_3way 读到 remote=None
    直接 `return local, 0` -> 三方合并对大文件完全失效 ->
    sync 每个周期都把本地旧副本整文件推上去，
    「任何人手动推的 daily_picks.json 改动都会在 15 分钟内被冲掉」。
    实例：2026-10-13 19:21 的多周期 RPS 回填 commit 2fef11ff51，
    被 19:25 的 sync commit 813be2b04a 整段回退（-6549 行）。
    git/blobs 走 git 层、无缓存、支持到 100MB，是唯一可靠的大文件读取路径。
    """
    url = f'{API}/repos/{REPO}/git/blobs/{blob_sha}'
    req = urllib.request.Request(url, headers={**headers, 'Accept': 'application/vnd.github.v3.raw'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return r.read().decode('utf-8')
        except Exception as e:  # noqa
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            print(f'[sync] blob {str(blob_sha)[:8]} fetch failed: {e}')
            return None


def get_remote_text(path):
    """取远程 branch 上该文件的当前文本，返回 (text, sha)；失败返回 (None, None)。

    为什么要以「远程最新版」而不是 workflow 本地副本为基础：
    定时任务 checkout 之后，可能有人手动提交过前端改动（改 CSS / 改渲染函数）。
    若直接用 workflow 本地那份旧副本注入数据再推送，就会把那些改动整体覆盖回退——
    push_file 的 SHA 校验只能防并发写冲突，防不住这种内容回退。
    """
    info = api_get(path)
    if not info:
        return None, None
    if not info.get('content'):
        # 大文件（>1MB）Contents API 不返回 content -> 回退 git blobs（无缓存、无体积限制）
        blob_sha = info.get('sha')
        if not blob_sha:
            return None, None
        txt = _git_blob_text(blob_sha)
        return (txt, blob_sha) if txt is not None else (None, None)
    try:
        return base64.b64decode(info['content']).decode('utf-8'), info.get('sha')
    except Exception as e:
        print(f'[sync] 解码远程 {path} 失败: {e}')
        return None, None


def api_put(path, content_str, sha, msg, retry_on_409=True):
    """写回文件。retry_on_409=True 时，遇 409（并发写冲突）自动拿新 sha 重试同一内容；
    =False 时 409 直接返回 None，交给上层（如 push_index_html_merged）重读远程最新版重注入——避免用旧内容覆盖他人改动。"""
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
            if e.code == 409:
                if retry_on_409 and attempt < max_retries - 1:
                    time.sleep(3)
                    sha_new = api_get(path)
                    if sha_new: payload['sha'] = sha_new['sha']
                    body = json.dumps(payload).encode('utf-8')
                    req = urllib.request.Request(url, data=body,
                                                 headers={**headers, 'Content-Type': 'application/json'},
                                                 method='PUT')
                    continue
                else:
                    print(f'[sync] HTTP 409（冲突，交上层重读远程最新版重注入）: {resp[:120]}')
                    return None
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

# ============================================================================
# 单提交批量推送（2026-11-01）
# ----------------------------------------------------------------------------
# 背景：原先每个文件各发一次 Contents API PUT，一轮 sync 就产生 4~6 个提交。
# 15 分钟一轮 → 高峰期一小时几十个提交 → 每个提交都触发一次 Pages 构建，
# 2026-11-01 实测一天 52 次部署、10 次失败全部由「in_progress 部署锁」引发，
# 整站（不只某个页面）会因此静默冻结。改为整轮只落 1 个提交后，构建次数
# 降到 1/5 以下。
#
# 机制：运行中所有写操作只入队（stage），结束时用 Git Data API 一次成型
#   POST /git/blobs  (每个文件)
#   POST /git/trees  (base_tree = 最新提交的 tree，只覆盖本次文件)
#   POST /git/commits(parents = [最新提交])
#   PATCH /git/refs/heads/main {force:false}
# force:false 是并发保护：若期间有人推了新提交，ref 更新会 422 → 重读最新
# ref/tree 重建提交（blob 内容寻址可复用），绝不覆盖他人改动。
# index.html 仍「以远程最新版为基础注入」，且支持多个注入器顺序叠加
# （sync_to_github 注主 embed，sync_us_to_github 注 us-picks-embed）。
# ============================================================================
_BATCH_ON = False
_BATCH_FILES = {}     # path -> 文本（同一文件多次写，后者覆盖前者）
_BATCH_INJECTS = []   # [(inject_fn, now)]，按登记顺序叠加到远程 index.html 上
_BATCH_DEFERRED = {}  # path -> resolver()：内容依赖「提交那一刻的远端」，flush 时才求值


def batch_begin():
    """开启批量模式：此后 push_file / push_index_html_merged 只入队，不立即提交。"""
    global _BATCH_ON
    _BATCH_ON = True
    _BATCH_FILES.clear()
    _BATCH_INJECTS.clear()
    _BATCH_DEFERRED.clear()


def _stage(path, content_str, quiet=False):
    if path in _BATCH_FILES and _BATCH_FILES[path] != content_str and not quiet:
        print(f'[sync] {path} 本轮被写多次，以最后一次内容为准')
    _BATCH_FILES[path] = content_str


def stage_deferred(path, resolver):
    """登记「提交时才求值」的内容。

    典型用途：daily_picks.json 的三方合并。单提交模式下一轮要跑 1~2 分钟，
    若在开头合并、结尾才提交，这 1~2 分钟里别人推的新键会被整文件覆盖
    （2026-10-13 事故正是这个形态）。把 merge 挪到 flush 里，窗口≈0。
    """
    _BATCH_DEFERRED[path] = resolver
    print(f'[sync] + {path} 延迟到提交时刻求值（三方合并）')


def _derive_from_picks(dp, now):
    """从（合并后的）daily_picks 派生日历文件：daily_picks_recent + sector_rankings。"""
    try:
        def _is_date(k):
            return (isinstance(k, str) and len(k) == 10 and k[4] == '-'
                    and k[7] == '-' and k[:4].isdigit())
        _dk = sorted([k for k in dp if _is_date(k)], reverse=True)
        _recent = {k: dp[k] for k in _dk[:60]}
        for k in dp:
            if not _is_date(k) and k != 'sector_rankings':
                _recent[k] = dp[k]
        _stage('daily_picks_recent.json',
               json.dumps(_recent, ensure_ascii=False, separators=(',', ':')), quiet=True)
        if 'sector_rankings' in dp:
            # 顶层 date 是旧生产者的遗留字段，长期停在 2026-10-01，会被巡检/人工误判成
            # 「板块 RPS 停更」。这里派生出独立文件时强制对齐到最新日期键，保证自洽。
            _sr = dict(dp['sector_rankings'])
            _sr_days = sorted([k for k in _sr if _is_date(k)])
            if _sr_days:
                _sr['date'] = _sr_days[-1]
            _stage('sector_rankings.json',
                   json.dumps(_sr, ensure_ascii=False, separators=(',', ':')), quiet=True)
    except Exception as e:
        print(f'[sync] [WARN] daily_picks_recent/sector_rankings 派生失败: {e}')


def _merge_picks_and_derive(picks, now):
    """flush 时执行：三方合并 picks → 重新派生依赖文件 → 返回最终文本。"""
    merged = picks
    try:
        merged, n = merge_picks_3way(picks)
        if n:
            print(f'[sync] picks 三方合并（提交时刻）：保留 {n} 个键的远端/本地差异')
        elif not isinstance(merged, dict):
            merged = json.loads(merged)
    except Exception as e:
        print(f'[sync] [WARN] picks 三方合并失败，改用本地内容: {e}')
        merged = picks
    text = json.dumps(merged, ensure_ascii=False, indent=2)
    _stage('daily_picks.json', text, quiet=True)
    _derive_from_picks(merged, now)
    return text


def _api_json(method, path, payload=None, timeout=150, tries=3):
    """调 GitHub REST，返回 (status, obj)。4xx 直接返回交上层判断，5xx/网络错误重试。"""
    url = f'{API}{path}'
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    hdrs = dict(headers)
    if data is not None:
        hdrs['Content-Type'] = 'application/json'
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', errors='replace')
            if e.code < 500:
                return e.code, {'message': body[:300]}
            print(f'[sync] {method} {path} HTTP {e.code} (attempt {attempt+1}/{tries})')
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
        except Exception as e:
            print(f'[sync] {method} {path} 异常: {e} (attempt {attempt+1}/{tries})')
            if attempt < tries - 1:
                time.sleep(3 * (attempt + 1))
            else:
                return 0, {'message': str(e)[:300]}
    return 0, {'message': 'retries exhausted'}


def _create_blob(text):
    payload = {'content': base64.b64encode(text.encode('utf-8')).decode('ascii'),
               'encoding': 'base64'}
    st, obj = _api_json('POST', f'/repos/{REPO}/git/blobs', payload, timeout=240)
    if st == 201 and obj.get('sha'):
        return obj['sha']
    print(f'[sync] ❌ 创建 blob 失败 HTTP {st}: {str(obj.get("message"))[:160]}')
    return None


def _batch_message(now, files):
    head = f'sync: 批量更新 {len(files)} 个文件 ({now})'
    body = '\n'.join(f'- {p}' for p in files[:40])
    if len(files) > 40:
        body += f'\n- …（共 {len(files)} 个文件）'
    return f'{head}\n\n{body}\n'


def _legacy_push_all(message=None):
    """兜底：批量推送整体失败时，退回逐文件 Contents API 推送（宁可多几个提交，也不能丢这一轮数据）。"""
    ok = True
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print('[sync] ⚠️ 回退逐文件推送（保证本轮数据不丢）')
    for _p, _fn in list(_BATCH_DEFERRED.items()):
        try:
            _fn()
        except Exception as e:
            print(f'[sync] ⚠️ {_p} 延迟求值失败: {e}')
    for path, text in _BATCH_FILES.items():
        info = api_get(path)
        sha = info['sha'] if info else None
        if api_put(path, text, sha, f'sync: update {path} ({now})'):
            print(f'[sync] ✅ {path} pushed (legacy)')
        else:
            ok = False
            print(f'[sync] ❌ {path} push failed (legacy)')
    for fn, _n in _BATCH_INJECTS:
        try:
            push_index_html_merged_legacy(fn, now)
        except Exception as e:
            ok = False
            print(f'[sync] ❌ index.html legacy 注入失败: {e}')
    return ok


def batch_flush(now=None, max_tries=4):
    """把队列里所有写操作合并成 1 个提交推上去。返回 True 表示远端已是队列内容。"""
    now = now or datetime.now().strftime('%Y-%m-%d %H:%M')
    injects = list(_BATCH_INJECTS)
    if not _BATCH_FILES and not injects and not _BATCH_DEFERRED:
        print('[sync] 批量推送：无待推内容')
        return True

    for attempt in range(max_tries):
        st, ref = _api_json('GET', f'/repos/{REPO}/git/refs/heads/{BRANCH}')
        base_sha = ((ref.get('object') or {}).get('sha')) if st == 200 else None
        if not base_sha:
            print(f'[sync] 读取 ref 失败 HTTP {st}: {str(ref.get("message"))[:120]}，重试')
            time.sleep(3)
            continue
        st, cm = _api_json('GET', f'/repos/{REPO}/git/commits/{base_sha}')
        base_tree = ((cm.get('tree') or {}).get('sha')) if st == 200 else None
        if not base_tree:
            print(f'[sync] 读取 base tree 失败 HTTP {st}，重试')
            time.sleep(3)
            continue

        # 延迟内容在每次尝试前重新求值：越靠近 PATCH 越新，覆盖窗口趋近于 0
        for _p, _fn in list(_BATCH_DEFERRED.items()):
            try:
                _fn()
            except Exception as e:
                print(f'[sync] ⚠️ {_p} 延迟求值失败，沿用已入队内容: {e}')

        changes = dict(_BATCH_FILES)
        precompared = set()      # 已与远程比对过的路径，避免重复 GET

        # index.html：读远程最新版 → 顺序叠加所有注入器（保留合并保护语义）
        if injects:
            html, _sha = get_remote_text('index.html')
            if html is None:
                print('[sync] ⚠️ index.html 远程读取失败，本轮跳过 embed 注入（不回退本地副本）')
            else:
                new_html = html
                for fn, _n in injects:
                    try:
                        new_html = fn(new_html)
                    except Exception as e:
                        print(f'[sync] ⚠️ embed 注入异常，本轮跳过 index.html: {e}')
                        new_html = None
                        break
                if new_html is None:
                    pass
                elif new_html == html:
                    print('[sync] index.html 内容无变化，跳过')
                else:
                    changes['index.html'] = new_html
                precompared.add('index.html')

        # 剔除与远端完全一致的文件：不制造空提交，也不触发无谓的 Pages 构建
        to_push = {}
        for path, text in changes.items():
            if path not in precompared:
                remote, _s = get_remote_text(path)
                if remote == text:
                    print(f'[sync] {path}: 与远端一致，跳过')
                    continue
            to_push[path] = text

        if not to_push:
            print('[sync] 批量推送：本轮无实际变更，不产生提交')
            return True

        tree, bad = [], None
        for path, text in to_push.items():
            blob = _create_blob(text)
            if not blob:
                bad = path
                break
            tree.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob})
        if bad:
            print(f'[sync] ❌ {bad} 建 blob 失败，本轮放弃并回退逐文件推送')
            return _legacy_push_all()

        st, nt = _api_json('POST', f'/repos/{REPO}/git/trees',
                           {'base_tree': base_tree, 'tree': tree})
        if st != 201 or not nt.get('sha'):
            print(f'[sync] ❌ 建 tree 失败 HTTP {st}: {str(nt.get("message"))[:160]}')
            time.sleep(3)
            continue

        st, cmt = _api_json('POST', f'/repos/{REPO}/git/commits',
                            {'message': _batch_message(now, sorted(to_push)),
                             'tree': nt['sha'], 'parents': [base_sha]})
        if st != 201 or not cmt.get('sha'):
            print(f'[sync] ❌ 建 commit 失败 HTTP {st}: {str(cmt.get("message"))[:160]}')
            time.sleep(3)
            continue

        st, up = _api_json('PATCH', f'/repos/{REPO}/git/refs/heads/{BRANCH}',
                           {'sha': cmt['sha'], 'force': False})
        if st == 200:
            print(f'[sync] ✅ 单提交推送完成 {cmt["sha"][:8]}（{len(to_push)} 个文件）')
            for p in sorted(to_push):
                print(f'        · {p}')
            return True
        if st == 422:
            print(f'[sync] ref 已被推进（非快进），重读最新版重建提交'
                  f'（第 {attempt+1}/{max_tries} 次）')
            time.sleep(3)
            continue
        print(f'[sync] ❌ 更新 ref 失败 HTTP {st}: {str(up.get("message"))[:160]}')
        time.sleep(3)

    print('[sync] ❌ 批量推送达到最大重试次数，回退逐文件推送')
    return _legacy_push_all()


def push_file(path, content_str, msg):
    if _BATCH_ON:
        _stage(path, content_str)
        print(f'[sync] + {path} 入队（{len(content_str):,} 字符，随本轮单提交一起推）')
        return True
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

def _inject_index_embed(html, picks):
    """在 index.html 上注入 embed / sector-rankings-embed 并剥离 nocache meta（幂等）。"""
    html = update_html_embed(html, None)
    if 'sector_rankings' in picks:
        html = update_sector_rankings_embed(html, picks['sector_rankings'])
    html = _strip_nocache_meta(html)
    return html


def _inject_us_picks_embed(html, data):
    """在 index.html 上注入 us-picks-embed（幂等）。找不到标签则原样返回。"""
    START_TAG = '<script type="application/json" id="us-picks-embed">'
    END_TAG = '</script>'
    start_idx = html.find(START_TAG)
    if start_idx < 0:
        print('[sync] [WARN] us-picks-embed tag not found in index.html')
        return html
    content_start = start_idx + len(START_TAG)
    end_idx = html.find(END_TAG, content_start)
    if end_idx < 0:
        print('[sync] [WARN] us-picks-embed closing tag not found')
        return html
    embed_json = _safe_embed_json(data)
    print(f'[sync] us-picks-embed updated to date={data.get("date", "N/A")}')
    return html[:content_start] + '\n' + embed_json + '\n' + html[end_idx:]


def push_index_html_merged(inject_fn, now, max_tries=6):
    """index.html 注入推送入口。

    批量模式（sync_func.py 主流程）下只登记注入器，由 batch_flush 统一
    「读远程最新版 → 叠加全部注入器 → 与其他文件同一个提交」推送；
    多个注入器可叠加（主 embed + us-picks-embed），保持原有合并保护语义。
    非批量模式（外部脚本单独调用）沿用下面的逐次推送实现。
    """
    if _BATCH_ON:
        _BATCH_INJECTS.append((inject_fn, now))
        print(f'[sync] + index.html 注入器入队（共 {len(_BATCH_INJECTS)} 个，随本轮单提交一起推）')
        return True
    return push_index_html_merged_legacy(inject_fn, now, max_tries)


def push_index_html_merged_legacy(inject_fn, now, max_tries=6):
    """通用化：推送 index.html 并做合并保护，根除「整段回退手动前端改动」的历史问题。

    机制（对齐 daily_picks 的 merge_picks_3way 思路；index.html 是整文件注入，无法键级合并）：
    每次循环都读「远程最新版」→ 在其上注入（inject_fn，幂等）→ 用该版 sha 推送。
    若与手动推送/并发写入冲突(409)：重读远程最新版→重注入→重推，保证最终上线版本
    一定包含「推送那一刻远程已有的全部前端代码 + 最新 embed 数据」，绝不回退他人改动。

    get_remote_text 失败（网络/限流/大文件）时跳过本次推送——宁可少推一次 embed，
    也绝不回退 workflow checkout 的旧本地副本（旧逻辑正是此处的元凶）。
    """
    last_sha = None
    for attempt in range(max_tries):
        html, sha = get_remote_text('index.html')
        if html is None:
            print('[sync] ⚠️ index.html 远程读取失败，跳过本次推送（不回退本地副本，留待下次 sync）')
            return False
        if sha == last_sha:
            print('[sync] index.html 远程版本未变，无需重推')
            return True
        html_new = inject_fn(html)
        print(f'[sync] index.html 以远程最新版注入 (sha {(sha or "")[:8]}, 尝试 {attempt+1}/{max_tries})')
        result = api_put('index.html', html_new, sha, f'sync: update embed ({now})', retry_on_409=False)
        if result:
            return True
        # 409：远程又被他人/手动推送改了 → 记录 sha 重新循环重读重注入
        print('[sync] index.html 推送冲突(409)，重读最新版重注入...')
        last_sha = sha
        time.sleep(3)
    print('[sync] ❌ index.html 合并推送达到最大重试次数')
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

_IS_DATE_KEY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

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
    dates = sorted(k for k in sector_rankings if _IS_DATE_KEY.match(str(k)))
    print(f'[sync] sector-rankings-embed updated: {len(dates)} dates (latest: {dates[-1] if dates else "N/A"})')
    return result

def update_rps_embed(html: str, sector_rankings: dict, keep: int = 2) -> str:
    """
    将最新的 sector_rankings 注入 rps.html 的 window.__RPS_EMBED__（兜底快照），
    避免兜底数据长期冻结。仅保留最近 keep 天，控制体积。
    """
    marker = 'window.__RPS_EMBED__'
    i = html.find(marker)
    if i == -1:
        print('[sync] [WARN] rps __RPS_EMBED__ marker not found')
        return html
    j = html.find('{', i)
    if j == -1:
        return html
    depth = 0
    k = j
    while k < len(html):
        if html[k] == '{':
            depth += 1
        elif html[k] == '}':
            depth -= 1
            if depth == 0:
                break
        k += 1
    keep_dates = sorted((k for k in sector_rankings if _IS_DATE_KEY.match(str(k))), reverse=True)[:keep]
    embed = {d: sector_rankings[d] for d in keep_dates}
    embed_json = json.dumps(embed, ensure_ascii=False, separators=(',', ':'))
    print(f'[sync] rps __RPS_EMBED__ updated: {len(keep_dates)} dates (latest: {keep_dates[0] if keep_dates else "N/A"})')
    return html[:j] + embed_json + html[k + 1:]

def _picks_base_from_git(path='daily_picks.json'):
    """本 job 检出时 git HEAD 里的那一份，作为三方合并的基线。"""
    import subprocess as _sp
    try:
        out = _sp.run(['git', 'show', 'HEAD:' + path], capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return json.loads(out.stdout)
    except Exception as e:
        print(f'[sync] 读取 HEAD:{path} 失败: {e}')
    return None


def merge_picks_3way(local, path='daily_picks.json'):
    """三方合并远端/本地/基线，避免 in-flight 运行的整文件直推把别人刚推的键覆盖掉。

    返回 (合并结果, 变更键数)；远端或基线不可用时原样返回本地内容。
    """
    base = _picks_base_from_git(path)
    remote_txt, _sha = get_remote_text(path)
    if base is None or not remote_txt:
        return local, 0
    try:
        remote = json.loads(remote_txt)
    except Exception as e:
        print(f'[sync] 远端 {path} 解析失败，跳过合并: {e}')
        return local, 0
    def merge_level(local_v, base_v, remote_v):
        """同一键下再合一层子键（日期键 → 任务名；任务键 → 日期）。"""
        if not (isinstance(local_v, dict) and isinstance(base_v, dict) and isinstance(remote_v, dict)):
            return local_v, 0
        out = dict(remote_v)
        n = 0
        for sk, sv in local_v.items():
            if sk not in base_v or base_v[sk] != sv:
                if out.get(sk) != sv:
                    out[sk] = sv
                    n += 1
        for sk in list(base_v.keys()):
            if sk not in local_v and sk in out:
                out.pop(sk, None)
                n += 1
        return out, n

    merged = dict(remote)
    changed = 0
    for k, v in local.items():
        if k not in base or base.get(k) != v:
            if k in merged and isinstance(v, dict) and isinstance(base.get(k), dict) and isinstance(merged[k], dict):
                # 本 job 只改了该日期/任务下的部分子键 → 逐子键合并，别把远端新增的子键冲掉
                nv, n = merge_level(v, base[k], merged[k])
                if nv != merged.get(k):
                    merged[k] = nv
                changed += n
            elif merged.get(k) != v:
                merged[k] = v
                changed += 1
    for k in list(base.keys()):
        if k not in local and k in merged:
            merged.pop(k, None)
            changed += 1
    return merged, changed


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

    # 过滤非交易日（周末/法定节假日）的日期键：避免假期误跑产生的选股长期残留
    try:
        from chinese_calendar import is_holiday as _is_holiday
        _dropped = []
        for _k in list(picks.keys()):
            if len(_k) == 10 and _k[4] == '-' and _k[7] == '-':
                try:
                    _d = datetime.strptime(_k, '%Y-%m-%d').date()
                except ValueError:
                    continue
                if _d.weekday() >= 5 or _is_holiday(_d):
                    picks.pop(_k, None); _dropped.append(_k)
        if _dropped:
            print(f'[sync] 已剔除非交易日选股键: {sorted(_dropped)}')
    except Exception as _e:
        print(f'[sync] 非交易日过滤跳过: {_e}')

    # 2. index.html 的读取与注入推送统一交给 push_index_html_merged（见下方第 5 步）：
    #    该函数每次循环读「远程最新版」注入并推送，遇 409 重读重注入，远程读取失败则跳过，
    #    彻底消除旧实现「get_remote_text 失败→回退本地 checkout 副本→整段回退手动改动」的漏洞。

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

    # 5. 推送 index.html（合并保护：读远程最新版注入 + 冲突重读重注入，绝不回退手动前端改动）
    success1 = push_index_html_merged(lambda h: _inject_index_embed(h, picks), now)
    # daily_picks.json 必须先三方合并再推：本 job 跑了十几分钟，期间别人可能推了新键，
    # 直接整文件直推会把那些键覆盖掉（2026-10-02 实测：高欣 09-30 批次被 in-flight sync 覆盖两次）。
    # 批量模式下把这次 merge 推迟到 flush（提交那一刻）执行：单提交一轮要跑 1~2 分钟，
    # 若在开头合并、结尾才提交，这 1~2 分钟里别人推的键仍会被覆盖。派生文件同理。
    success2 = True
    if _BATCH_ON:
        stage_deferred('daily_picks.json', lambda: _merge_picks_and_derive(picks, now))
    else:
        picks_push, _merged_n = merge_picks_3way(picks)
        if _merged_n:
            print(f'[sync] picks 三方合并：保留 {_merged_n} 个键的远端/本地差异')
        success2 = push_file('daily_picks.json',
                             json.dumps(picks_push, ensure_ascii=False, indent=2),
                             f'sync: update picks ({now})')
        # Phase ①: 派生 daily_picks_recent.json(近60天精选) + sector_rankings.json(仅 rps 用)
        if not isinstance(picks_push, dict):
            try:
                picks_push = json.loads(picks_push)
            except Exception:
                picks_push = picks
        _derive_from_picks(picks_push, now)


    # 5.1 推送 rps.html（静态页面，运行时 fetch daily_picks.json），同时刷新兜底快照
    try:
        with open(LOCAL_RPS, 'r', encoding='utf-8') as f:
            rps_html = f.read()
        if 'sector_rankings' in picks:
            rps_html = update_rps_embed(rps_html, picks['sector_rankings'])
        rps_html = _strip_nocache_meta(rps_html)
        push_file('rps.html', rps_html, f'sync: update rps.html ({now})')
    except Exception as e:
        print(f'[sync] rps.html push skipped: {e}')

    # 6. 保存本地 HTML（从远程最新版落盘，确保本地与线上一致，含他人手动改动）
    #    批量模式下此刻尚未提交，远程还是注入前的版本 → 不能拿它覆盖本地副本。
    if success1 and not _BATCH_ON:
        html_local, _ = get_remote_text('index.html')
        if html_local:
            with open(LOCAL_HTML, 'w', encoding='utf-8') as f:
                f.write(html_local)
            print('[sync] Local index.html synced from remote')
        else:
            print('[sync] [WARN] 本地 index.html 未更新（远程读取失败，线上已是最新版）')

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

    # 5. 推送 em_stock_names.json（快讯代码名称缓存，跨 run 持久化；失败不影响主流程）
    local_names = paths.w(r'em_stock_names.json')
    if os.path.exists(local_names):
        try:
            with open(local_names, 'r', encoding='utf-8') as f:
                names_txt = f.read()
            push_file('em_stock_names.json', names_txt, f'sync: update stock names ({now})')
        except Exception as e:
            print(f'[sync] ⚠️ em_stock_names.json 推送失败: {e}')

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
    #    以「远程最新版」为基准注入，避免 checkout 后手动前端改动被回退
    #    （get_remote_text 防护，与 sync_us_to_github 的 index.html 一致）
    strong_path = paths.w(r'strong.html')
    try:
        strong_html, _rsha = get_remote_text('strong.html')
        if strong_html is None:
            with open(strong_path, 'r', encoding='utf-8') as f:
                strong_html = f.read()
            print('[sync] strong.html 远程读取失败，回退本地 checkout 副本')
        else:
            print(f'[sync] strong.html 以远程最新版为基准注入 _data (sha {(_rsha or "")[:8]})')

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

        # 守护：注入 _data 后若丢失三 tab，绝不推送（避免覆盖带 tab 的页面）
        if not all(t in strong_html for t in ('威震天', '益盟强买', '盗火线')):
            print('[sync] \u274c strong.html 注入后缺失 tab，中止推送')
            success = False
        elif not push_file('strong.html', strong_html, f'sync: update strong.html ({now})'):
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
    
    # 3. 更新 index.html 中的 us-picks-embed（合并保护：读远程最新版注入 + 冲突重读重注入，不回退本地副本）
    ok2 = push_index_html_merged(lambda h: _inject_us_picks_embed(h, data), now)
    # 本地副本从远程落盘（保留他人手动改动）；批量模式下尚未提交，跳过以免用旧版本覆盖
    if ok2 and not _BATCH_ON:
        html_local, _ = get_remote_text('index.html')
        if html_local:
            with open(LOCAL_HTML, 'w', encoding='utf-8') as f:
                f.write(html_local)
            print('[sync] index.html 本地文件已同步（含他人改动）')
        else:
            print('[sync] [WARN] 本地 index.html 未更新（远程读取失败）')
    print(f'[sync] ===== us_picks.json + embed 同步: {"✅" if ok1 and ok2 else "❌"} =====\n')
    return ok1 and ok2

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


def sync_sector_funnel_to_github():
    """同步 sector_funnel.json 到 GitHub（板块→个股信号漏斗，research.html 面板数据源）

    由 research_scanner.py（12:00 / 20:00）生成，这里负责推回仓库。
    数据为纯自动生成的快照，无需 get_remote_text 防护，直接覆盖。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 sector_funnel.json [{now}] =====')
    path = paths.w(r'sector_funnel.json')
    if not os.path.exists(path):
        print('[sync] sector_funnel.json 不存在，跳过')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        print(f'[sync] sector_funnel.json: {len(content):,} bytes, asof={data.get("asof", "N/A")}, '
              f'boards={len(data.get("boards") or [])}, orphans={len(data.get("orphans") or [])}')
        ok = push_file('sector_funnel.json', content, f'sync: update sector_funnel.json ({now})')
        print(f'[sync] ===== sector_funnel.json 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] sector_funnel.json 同步失败: {e}')
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
        html_new, _cffex_data_json = update_cffex_inline(html, data)
        ok1 = push_file('cffex.html', html_new, f'sync: update cffex.html data ({now})')
        if ok1:
            with open(cffex_html, 'w', encoding='utf-8') as f:
                f.write(html_new)
            print('[sync] Local cffex.html inline data updated')
    except Exception as e:
        print(f'[sync] ❌ cffex.html 注入失败: {e}')
        ok1 = False

    ok_data = False
    if _cffex_data_json:
        ok_data = push_file('cffex_data.json', _cffex_data_json, f'sync: update cffex_data ({now})')
    ok2 = push_file('cffex_net_position.json', content, f'sync: update cffex data ({now})')
    print(f'[sync] ===== cffex 同步: {"✅" if (ok1 and ok2 and ok_data) else "❌"} =====\n')
    return ok1 and ok2 and ok_data


def update_cffex_inline(html, cffex_data):
    """将 cffex_data（日期→品种指标）注入 cffex.html 内联变量 var _cffex = {...}"""
    marker = 'var _cffex = '
    idx = html.find(marker)
    if idx < 0:
        print('[sync] [WARN] cffex.html 找不到 var _cffex 占位')
        return html, None
    semi = html.find(';', idx)
    if semi < 0:
        print('[sync] [WARN] cffex.html var _cffex 缺少分号')
        return html, None
    # Phase ①: 不再内联大块数据，改为落到 cffex_data.json 供页面运行时 fetch
    embed_json = json.dumps(cffex_data, ensure_ascii=False, separators=(',', ':'))
    result = html[:idx] + marker + '{};' + html[semi+1:]
    print(f'[sync] cffex.html inline blanked (was {len(cffex_data)} days)')
    return result, embed_json


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
    """同步 AI 复盘相关文件到 GitHub（ai_analysis_data/report/html + 慢热个股 + 板块K线）。

    为什么必须有这一段：ai_daily.py（采集 + 生成报告）和 ai_analysis.py（渲染页面）
    在 workflow 里每次都会跑，但产物只落在 runner 本地。原 sync_func.py 的推送清单里
    没有任何 ai_analysis 相关文件，这些文件从未回推仓库，于是 GitHub Pages 上的
    /ai_analysis.html 永远停在最后一次本地手动推送的快照——表现为「AI 复盘没有自动运行」。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 ai_analysis [{now}] =====')
    success = True

    # ai_analysis.html 的推送带「新鲜度闸」：
    #   - sync.yml 在 A 股交易时段（09:30–12:00 / 13:00–15:30）会跑 ai_analysis.py 重渲染页面，
    #     渲染成功则在 GITHUB_ENV 里导出 AI_HTML_FRESH=1；
    #   - 只有该标志为 1 时才推送 HTML。否则磁盘上的 HTML 只是 checkout 里的旧副本，
    #     推上去会覆盖正确版本（2026-09-29 事故：JSON 已填板块、HTML 却停在空面板）。
    # 有了这道闸，页面在交易时段每 15 分钟自动刷新，不再依赖 ai_analysis.yml 那个常被
    # GitHub 调度延迟数小时的每日 cron。
    # 数据文件同样新鲜度闸（2026-11-04 事故）：窗口外本轮没跑 ai_daily 时，
    # checkout 里的 data/report 是旧副本，推上去会把 ai_analysis.yml 刚提交的
    # 新数据盖回旧版（html 早有 AI_HTML_FRESH 闸，data 当时漏了）。
    rels = ['ai_analysis_data.json', 'ai_analysis_report.json',
            'slowrise_stocks.json', 'ai_analysis_board_kline.json']
    if str(os.environ.get('AI_DATA_FRESH', '')).strip() == '1':
        pass
    else:
        print('[sync] ai_analysis 数据文件本轮未重采集（AI_DATA_FRESH≠1），跳过推送（避免旧副本回灌）')
        rels = []
    if str(os.environ.get('AI_HTML_FRESH', '')).strip() == '1':
        rels.append('ai_analysis.html')
    else:
        print('[sync] ai_analysis.html 本轮未重渲染（AI_HTML_FRESH≠1），跳过推送')
    for rel in rels:
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
        # Phase ①: 派生 speedrank_recent.json（近30天），去掉 ?t= 缓存击穿
        try:
            _hist = json.loads(hist_content)
            _hd = [k for k in _hist if isinstance(k, str) and len(k) == 10 and k[4] == '-' and k[7] == '-' and k[:4].isdigit()]
            _hd.sort(reverse=True)
            _recent_hist = {k: _hist[k] for k in _hd[:30]}
            push_file('speedrank_recent.json',
                      json.dumps(_recent_hist, ensure_ascii=False, separators=(',', ':')),
                      f'sync: update speedrank_recent ({now})')
        except Exception as _e:
            print(f'[sync] [WARN] speedrank_recent push skipped: {_e}')
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


def sync_sector_snapshot_to_github():
    """同步 sector_snapshot.json（首页「市场板块温度计」的全市场板块数据源）

    为什么需要：温度计原先是浏览器端 JSONP 直连东财 push2，但部分网络
    （实测部分家宽/运营商）会把 push2.eastmoney.com 直接断连，此时只能降级到
    腾讯行情的 33 个申万一级行业代表股，样本严重不足。改由 CI（云 IP 可正常访问）
    每轮生成全市场板块快照（行业 ~86 / 概念 ~430），前端 fetch 同源静态 JSON 兜底。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 sector_snapshot.json [{now}] =====')
    path = paths.w(r'sector_snapshot.json')
    if not os.path.exists(path):
        print('[sync] sector_snapshot.json 不存在，跳过（首次需 CI 跑一轮 gen_sector_snapshot.py）')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        cnt = data.get('count') or {}
        print(f'[sync] sector_snapshot.json: {len(content):,} bytes, '
              f'updated={data.get("updated", "N/A")}, '
              f'行业 {cnt.get("industry", "?")} / 概念 {cnt.get("concept", "?")}')
        # 守护：板块数太少说明抓取异常，别把坏数据覆盖上去
        if (cnt.get('industry') or 0) < 20 and (cnt.get('concept') or 0) < 20:
            print('[sync] ❌ 快照板块数异常偏低，跳过推送（保留线上旧快照）')
            return False
        ok = push_file('sector_snapshot.json', content,
                       f'sync: update sector_snapshot ({now})')
        print(f'[sync] ===== sector_snapshot.json 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ sector_snapshot.json 失败: {e}')
        return False


def sync_board_members_to_github():
    """同步 board_members.json（首页「市场异动」卡片点击 → 成分股热力图的名单源）

    为什么需要：这条弹窗原先只有一条数据路——浏览器 JSONP 直连东财 push2 取
    `fs=b:BKxxxx` 成分股。用户出口把 push2 整段断连，新浪板块名又和东财细分行业
    对不上（精确重合 3/100），点击后只能看到「数据源不可达」。
    改成「名单慢、行情快」分开取：名单由 gen_board_members.py 在 CI 抓好落快照，
    前端 fetch 同源文件 + 腾讯 qt.gtimg.cn 实时价。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 board_members.json [{now}] =====')
    path = paths.w(r'board_members.json')
    if not os.path.exists(path):
        print('[sync] board_members.json 不存在，跳过（首次需 CI 跑一轮 gen_board_members.py）')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        boards = data.get('boards') or {}
        print(f'[sync] board_members.json: {len(content):,} bytes, '
              f'updated={data.get("updated", "N/A")}, 板块 {len(boards)} 个')
        # 守护：板块数太少说明抓取异常，别把坏数据覆盖上去
        if len(boards) < 20:
            print('[sync] ❌ 名单板块数异常偏低，跳过推送（保留线上旧名单）')
            return False
        ok = push_file('board_members.json', content,
                       f'sync: update board_members ({now})')
        print(f'[sync] ===== board_members.json 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ board_members.json 失败: {e}')
        return False


def sync_market_kline_to_github():
    """同步 market_kline.json（news.html「全球指数 / 贵金属汇率」两个 Tab 的数据源）

    为什么必须有这一段（2026-09-16 事故）：
    sync.yml 每轮都调用 gen_market_kline.py 生成 market_kline.json，
    但本文件的推送清单此前从未包含它 —— 文件只在临时 runner 里生成，
    run 结束即销毁，线上 JSON 永远停在最后一次手工推送的版本
    （实测 updated 冻结在 2026-09-14T21:17:51，页面两个 Tab 停更两天）。
    补上本函数后，每 15 分钟一轮的 CI 会把新生成的缓存提交回仓库。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步 market_kline.json [{now}] =====')
    path = paths.w(r'market_kline.json')
    if not os.path.exists(path):
        print('[sync] market_kline.json 不存在，跳过')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        data = json.loads(content)
        bars = data.get('bars') or {}
        print(f'[sync] market_kline.json: {len(content):,} bytes, '
              f'updated={data.get("updated", "N/A")}, {len(bars)} 个品种')
        ok = push_file('market_kline.json', content,
                       f'sync: update market_kline ({now})')
        print(f'[sync] ===== market_kline.json 同步: {"✅" if ok else "❌"} =====\n')
        return ok
    except Exception as e:
        print(f'[sync] ❌ market_kline.json 失败: {e}')
        return False


def sync_fourweek_to_github():
    """同步 fourweek_newhigh.json / fourweek_newhigh_history.json
    （strong.html「四周新高」tab 的数据源）

    为什么必须有这一段（2026-10-13 事故）：
    「四周新高」tab 的生成器 dashboard-gen/gen_fourweek_newhigh.py 自 2026-10-01
    上线以来从未被任何 workflow 调度过；即便补上调度，本文件的推送清单若仍不含这两个
    文件，产物也只存在于临时 runner 里、run 结束即销毁，线上 JSON 会永远停在最后一次
    手工推送的版本 —— 与 2026-09-16 market_kline.json 是同一类事故。

    与其它 sync_* 的差别：这两个文件是「每日一次」的冻结快照，绝大多数 run 里内容与
    远端完全一致，故先比对远端内容，一致就跳过，避免每 15 分钟制造一个空提交。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步四周新高快照 [{now}] =====')
    ok = True
    for name in ('fourweek_newhigh.json', 'fourweek_newhigh_history.json',
                 'fourweek_newhigh.csv'):
        path = paths.w(name)
        if not os.path.exists(path):
            print(f'[sync] {name} 不存在，跳过')
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if name.endswith('.json'):
                data = json.loads(content)
                stamp = data.get('date') or data.get('generated') or 'N/A'
            else:
                # CSV 明细（透明核对用）没有日期字段，取首个数据行的代码行数即可
                stamp = f'{max(0, len(content.splitlines()) - 1)} 行'
            remote, _sha = get_remote_text(name)
            if remote == content:
                print(f'[sync] {name}: 与远端一致（{stamp}），跳过')
                continue
            print(f'[sync] {name}: {len(content):,} bytes, 日期={stamp}')
            ok = push_file(name, content, f'sync: update {name} ({now})') and ok
        except Exception as e:
            print(f'[sync] ❌ {name} 失败: {e}')
            ok = False
    print(f'[sync] ===== 四周新高快照同步: {"✅" if ok else "❌"} =====\n')
    return ok


def _push_data_file(name, now, stamp=None):
    """内容一致则跳过、否则推送单个数据文件（供下列 sync_* 复用）。

    为什么先比对远端：这些文件在绝大多数 run 里与远端完全一致，直接 PUT 会每
    15 分钟制造一个空提交；比对后一致就跳过（与 sync_fourweek_to_github 同思路）。
    """
    path = paths.w(name)
    if not os.path.exists(path):
        print(f'[sync] {name} 不存在，跳过')
        return True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        remote, _sha = get_remote_text(name)
        tag = stamp or f'{len(content):,} bytes'
        if remote == content:
            print(f'[sync] {name}: 与远端一致（{tag}），跳过')
            return True
        print(f'[sync] {name}: {len(content):,} bytes, 标识={tag}')
        return push_file(name, content, f'sync: update {name} ({now})')
    except Exception as e:
        print(f'[sync] ❌ {name} 失败: {e}')
        return False


def _json_stamp(path, *keys):
    """取 JSON 顶层第一个存在的标识字段（日期类），仅用于日志。"""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in keys:
                if data.get(k):
                    return str(data[k])
            recs = data.get('recommendations')
            if isinstance(recs, list) and recs:
                ds = [str(r.get('date')) for r in recs if isinstance(r, dict) and r.get('date')]
                if ds:
                    return max(ds)
    except Exception:
        pass
    return None


def sync_strategy_tabs_to_github():
    """同步 strategy_tabs.json + strategy_tabs_kline_cache.json
    （strong.html 三战法 5 日跟踪页的数据源）

    为什么必须有这一段（2026-10-13 事故）：
    gen_strategy_tabs.py 原先只由 pick_tracker.yml 的每日 cron（北京 18:30）驱动，
    该 cron 实测可迟飞数小时（10-13 那次 04:08 才跑，落在当天 08:45 选股之前），
    strong.html 三战法 tab 因此停在 10-09。现已改由 sync.yml 的 run_task
    strategy_tabs（20:05，全部选股跑完之后）驱动，本函数负责推回仓库。

    strategy_tabs_kline_cache.json 是 K 线增量缓存，必须一起推——它此前从不在
    仓库里（pick_tracker 只 add 了两个文件），导致每个 runner 都是冷缓存、每次
    全量抓约 60 只票的日 K（分钟级，逼近 run_task 的 420s 硬超时）。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步三战法跟踪页 [{now}] =====')
    ok = True
    for name in ('strategy_tabs.json', 'strategy_tabs_kline_cache.json'):
        path = paths.w(name)
        stamp = _json_stamp(path, 'generated_at', 'data_through') if os.path.exists(path) else None
        ok = _push_data_file(name, now, stamp) and ok
    print(f'[sync] ===== 三战法跟踪页同步: {"✅" if ok else "❌"} =====\n')
    return ok


def sync_jack_reco_to_github():
    """同步 jack_recommendations.json（杰克船长完整推荐存档）

    为什么必须有这一段（2026-10-13 事故）：
    该文件此前只由 01_morning_picks.yml 的 git 推送带回仓库，而那个 cron 经常
    迟飞/跳档；sync.yml 的 run_task jack_captain 每次也会生成它。纳入本清单后，
    完整存档不再依赖 01 的 cron——10-12 的存档正是靠它证明了「当天选出了 10 只、
    是 daily_picks 侧被并发覆盖丢失」，是这次事故定性的关键证据。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步杰克船长存档 [{now}] =====')
    path = paths.w('jack_recommendations.json')
    stamp = _json_stamp(path) if os.path.exists(path) else None
    ok = _push_data_file('jack_recommendations.json', now, stamp)
    print(f'[sync] ===== 杰克船长存档同步: {"✅" if ok else "❌"} =====\n')
    return ok


def sync_chenxiaoqun_to_github():
    """同步 chen_xiaoqun_result.json（陈小群战法选股结果）

    为什么必须有这一段：该文件此前只由 03_afternoon_picks.yml 的 git 推送带回
    仓库，cron 跳档即停更；sync.yml 的 run_task chen_xiaoqun（14:30）每次也会
    生成它，纳入本清单后不再依赖 03 的 cron。
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[sync] ===== 同步陈小群战法结果 [{now}] =====')
    path = paths.w('chen_xiaoqun_result.json')
    stamp = _json_stamp(path, 'date', 'generated_at', 'data_through') if os.path.exists(path) else None
    ok = _push_data_file('chen_xiaoqun_result.json', now, stamp)
    print(f'[sync] ===== 陈小群战法结果同步: {"✅" if ok else "❌"} =====\n')
    return ok



# 供外部直接调用
SYNC_TASKS = [
    ('daily_picks/index', sync_to_github),
    ('news', sync_news_to_github),
    ('market_kline', sync_market_kline_to_github),
    ('strong', sync_strong_to_github),
    ('trend_history', sync_trend_history_to_github),
    ('us_picks', sync_us_to_github),
    ('cffex', sync_cffex_to_github),
    ('market_review', sync_market_review_to_github),
    ('midday_review', sync_midday_to_github),
    ('ai_analysis', sync_ai_analysis_to_github),
    ('task_state', sync_task_state_to_github),
    ('speedrank', sync_speedrank_to_github),
    ('sector_snapshot', sync_sector_snapshot_to_github),
    ('board_members', sync_board_members_to_github),
    ('research', sync_research_to_github),
    ('research_html', sync_research_html_to_github),
    ('sector_funnel', sync_sector_funnel_to_github),
    ('fourweek', sync_fourweek_to_github),
    ('strategy_tabs', sync_strategy_tabs_to_github),
    ('jack_reco', sync_jack_reco_to_github),
    ('chenxiaoqun', sync_chenxiaoqun_to_github),
]

_BATCH_TASK_LIST = SYNC_TASKS


def main():
    """整轮只推 1 个提交（2026-11-01）：所有 sync_* 只把内容入队，最后统一提交。

    单个函数抛异常不再中断整轮——否则「一崩就整轮不提交」，比原来丢得更多。
    返回 0 表示提交成功。
    """
    batch_begin()
    failed = []
    ok = False
    try:
        for name, fn in _BATCH_TASK_LIST:
            try:
                fn()
            except Exception as e:
                failed.append(name)
                print(f'[sync] ❌ {name} 抛异常（继续执行其余任务）: {type(e).__name__}: {e}')
    finally:
        print(f'\n[sync] ===== 批量提交（{len(_BATCH_FILES)} 个文件 + '
              f'{len(_BATCH_INJECTS)} 个 index 注入器）=====')
        try:
            ok = batch_flush()
        except Exception as e:
            print(f'[sync] ❌ 批量提交异常: {type(e).__name__}: {e}')
            try:
                ok = _legacy_push_all()
            except Exception as e2:
                print(f'[sync] ❌ 兜底逐文件推送也失败: {type(e2).__name__}: {e2}')
                ok = False
        if failed:
            print(f'[sync] 本轮异常任务: {failed}')
        print(f'[sync] ===== 结束: {"✅" if ok and not failed else "⚠️ 见上方日志"} =====')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())