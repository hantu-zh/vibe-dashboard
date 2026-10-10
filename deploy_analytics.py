# 部署 vibe-analytics 埋点：推送 analytics.js + 各页面 +1 行埋点
# 页面内容直接从远程 git 层取最新版本（避免本地 HEAD 落后导致回退），只追加埋点行。
# 子目录页面（如 king/index.html）自动用 ../analytics.js 相对前缀。
import os, json, base64, subprocess, time

TOKEN = os.environ['T1'] + os.environ['T2'] + os.environ['T3'] + os.environ['T4']
REPO = os.environ.get('REPO', 'hantu-zh/vibe-dashboard')
REPO_DIR = os.environ.get('REPO_DIR', 'C:/Users/china/.qclaw/workspace/vibe-dashboard')
BRANCH = os.environ.get('BRANCH', 'main')
COMMIT_MSG = os.environ.get('COMMIT_MSG', 'feat: add vibe-analytics silent tracking beacon')

HTML_FILES = ["ai_analysis.html", "cffex.html", "etf_test.html", "index.html",
              "king/index.html", "ma-strategy.html", "markets.html", "news.html",
              "news_clean.html", "nh_live.html", "research.html", "rps.html",
              "speedrank.html", "strong.html", "trader.html", "trader_setup.html",
              "week.html"]


def api(method, path, data=None):
    url = 'https://api.github.com' + path
    env = dict(os.environ)
    env.pop('TOKEN', None)
    payload = json.dumps(data) if data is not None else None
    last = None
    for attempt in range(4):
        if attempt:
            time.sleep(2 * attempt)
        cmd = ['curl', '-sS', '--http1.1', '--retry', '5', '--retry-delay', '2',
               '--max-time', '180', '-X', method, url,
               '-H', 'Accept: application/vnd.github+json',
               '-H', 'X-GitHub-Api-Version: 2022-11-28',
               '-H', 'User-Agent: vibe-deploy',
               '-H', 'Authorization: Bearer ' + TOKEN,
               '-w', '\n__STATUS__%{http_code}']
        if payload is not None:
            cmd += ['-H', 'Content-Type: application/json', '--data-binary', '@-']
        r = subprocess.run(cmd, input=payload, capture_output=True, text=True, env=env)
        if r.returncode:
            last = 'curl failed %s %s: %s' % (method, path, r.stderr)
            continue
        body, _, status = r.stdout.rpartition('\n__STATUS__')
        status = (status or '').strip()
        if not body.strip():
            last = 'empty %s %s' % (method, path)
            continue
        try:
            obj = json.loads(body)
        except Exception:
            last = 'bad json %s %s' % (method, path)
            continue
        if status and not status.startswith('2'):
            if status in ('429', '500', '502', '503', '504'):
                last = 'HTTP %s %s' % (status, path)
                continue
            raise RuntimeError('HTTP %s %s: %s' % (status, path, body[:400]))
        return obj
    raise RuntimeError(last or 'api failed')


def get_base():
    ref = api('GET', '/repos/%s/git/refs/heads/%s' % (REPO, BRANCH))
    base_sha = ref['object']['sha']
    commit = api('GET', '/repos/%s/git/commits/%s' % (REPO, base_sha))
    tree_sha = commit['tree']['sha']
    tree = api('GET', '/repos/%s/git/trees/%s?recursive=1' % (REPO, tree_sha))
    return base_sha, tree_sha, {e['path']: e for e in tree['tree']}


def tag_for(rel):
    # 根目录页面用 analytics.js；子目录页面（如 king/index.html）用相对前缀 ../analytics.js
    depth = rel.count('/')
    return '<script defer src="%sanalytics.js"></script>' % ('../' * depth)


def insert_tag(raw, rel):
    text = raw.decode('utf-8', errors='replace')
    tag = tag_for(rel)
    if tag in text:
        return None
    idx = text.rfind('</body>')
    if idx < 0:
        raise RuntimeError('目标缺 </body>')
    new = text[:idx] + tag + '\n' + text[idx:]
    nb = new.encode('utf-8')
    if b'\r\n' in nb and b'\r\n' not in raw:
        raise RuntimeError('写入产生 CRLF，中止')
    return nb


def main():
    # analytics.js（新文件，取自磁盘）
    analytics_blob = api('POST', '/repos/%s/git/blobs' % REPO,
                         {'content': base64.b64encode(
                             open(os.path.join(REPO_DIR, 'analytics.js'), 'rb').read()).decode(),
                          'encoding': 'base64'})
    print('blob analytics.js', analytics_blob['sha'][:10])

    for attempt in range(6):
        if attempt:
            time.sleep(2)
        try:
            base_sha, _, entries_map = get_base()
            entries = [{'path': 'analytics.js', 'mode': '100644', 'type': 'blob',
                        'sha': analytics_blob['sha']}]
            for rel in HTML_FILES:
                if rel not in entries_map or entries_map[rel]['type'] != 'blob':
                    raise RuntimeError('远程找不到 %s' % rel)
                remote_sha = entries_map[rel]['sha']
                blob = api('GET', '/repos/%s/git/blobs/%s' % (REPO, remote_sha))
                nb = insert_tag(base64.b64decode(blob['content']), rel)
                if nb is None:
                    print(rel, '已含，跳过')
                    continue
                if base64.b64encode(nb).decode() == blob['content']:
                    print(rel, '内容未变，跳过')
                    continue
                b2 = api('POST', '/repos/%s/git/blobs' % REPO,
                         {'content': base64.b64encode(nb).decode(), 'encoding': 'base64'})
                entries.append({'path': rel, 'mode': '100644', 'type': 'blob', 'sha': b2['sha']})
                print('blob', rel, b2['sha'][:10])
                time.sleep(0.8)

            tree = api('GET', '/repos/%s/git/commits/%s' % (REPO, base_sha))['tree']['sha']
            nt = api('POST', '/repos/%s/git/trees' % REPO, {'base_tree': tree, 'tree': entries})['sha']
            nc = api('POST', '/repos/%s/git/commits' % REPO,
                     {'message': COMMIT_MSG, 'tree': nt, 'parents': [base_sha]})['sha']
            api('PATCH', '/repos/%s/git/refs/heads/%s' % (REPO, BRANCH), {'sha': nc})
            print('PUSHED', nc)
            print('ENTRIES', len(entries))
            return
        except RuntimeError as e:
            if '422' in str(e) and 'fast forward' in str(e):
                print('rebase retry', attempt)
                continue
            raise


if __name__ == '__main__':
    main()
