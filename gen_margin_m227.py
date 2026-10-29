# -*- coding: utf-8 -*-
"""
gen_margin_m227.py - 生成「全市场平均担保比例 + 融资余额」时间序列 (margin_m227.json)

每个交易日由 GitHub Actions (margin_ratio_sync.yml) 运行：
  1. 拉取东财账户统计 RPTA_WEB_MARGIN_DAILYTRADE
     - AVG_GUARANTEE_RATIO : 全市场平均担保比例（市值加权：Σ担保物市值÷Σ融资负债）
                              由第三方基于两融数据估算，交易所不直接发布
     - FIN_BALANCE         : 融资余额（亿元）
  2. 与线上已有 margin_m227.json 合并（按日期累积历史，去重）
  3. 写本地 margin_m227.json 并自推回仓库（触发 GitHub Pages 更新）

设计要点：
  - 仅用标准库（urllib/ssl/json/base64），GitHub Actions 无需 pip 安装依赖
  - 自推走 GitHub Contents API（与 etf_sync.py / gen_margin_ratio.py 同机制），带 409 重试
  - 本地调试可用环境变量 MARGIN_M227_NOPUSH=1 仅生成不推送
"""
import os, sys, json, time, ssl, base64, urllib.request, urllib.error
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API = 'https://api.github.com'
REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
LOCAL = os.path.join(BASE_DIR, 'margin_m227.json')

# 东财数据中心（datacenter-web 稳定 200，避开 push2 间歇掐断）
# reportName 是 RPTA_WEB_MARGIN_DAILYTRADE（注意前缀 RPTA，非 RPT）
EM_API = ('https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_WEB_MARGIN_DAILYTRADE'
          '&columns=STATISTICS_DATE,FIN_BALANCE,AVG_GUARANTEE_RATIO'
          '&pageSize=260&sortColumns=STATISTICS_DATE&sortTypes=-1&source=WEB&client=WEB')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def _gh_token():
    t = os.environ.get('GITHUB_TOKEN')
    if t:
        return t
    try:
        with open(os.path.join(BASE_DIR, '.github_token'), encoding='utf-8-sig') as _f:
            return _f.read().strip()
    except Exception:
        return None


TOKEN = _gh_token()
if TOKEN:
    HEADERS = {'Authorization': f'token {TOKEN}',
               'Accept': 'application/vnd.github.v3+json',
               'User-Agent': 'QClaw/1.0'}
else:
    HEADERS = {'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'QClaw/1.0'}


def em_fetch():
    """拉取东财账户统计，返回 {日期: (fin_balance, guarantee_ratio)}。"""
    req = urllib.request.Request(EM_API, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                j = json.loads(r.read())
            rows = (j.get('result') or {}).get('data') or []
            out = {}
            for o in rows:
                d = (o.get('STATISTICS_DATE') or '')[:10]
                if not d:
                    continue
                fb = o.get('FIN_BALANCE')
                gr = o.get('AVG_GUARANTEE_RATIO')
                try:
                    fb = float(fb) if fb is not None else None
                    gr = float(gr) if gr is not None else None
                except Exception:
                    continue
                out[d] = (round(fb, 2) if fb is not None else None,
                          round(gr, 2) if gr is not None else None)
            return out
        except Exception as e:
            if attempt < 2:
                time.sleep(3 ** attempt)
                continue
            print('[gen_margin_m227] 东财拉取失败:', e)
            return None
    return None


def api_get(path):
    url = f'{API}/repos/{REPO}/contents/{path}?ref={BRANCH}'
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return json.loads(r.read())
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            print('[gen_margin_m227] GET 失败:', e)
            return None
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
                                 headers={**HEADERS, 'Content-Type': 'application/json'},
                                 method='PUT')
    max_retries = 5
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            resp = e.read().decode('utf-8', errors='replace')
            if e.code == 409 and attempt < max_retries - 1:
                print(f'[gen_margin_m227] 409 冲突，重取 SHA 重试 ({attempt+1})')
                time.sleep(3)
                info = api_get(path)
                if info:
                    payload['sha'] = info['sha']
                    body = json.dumps(payload).encode('utf-8')
                    req = urllib.request.Request(url, data=body,
                                                 headers={**HEADERS, 'Content-Type': 'application/json'},
                                                 method='PUT')
                    continue
                return None
            if e.code >= 500 and attempt < max_retries - 1:
                time.sleep(5 ** attempt)
                continue
            print(f'[gen_margin_m227] HTTP {e.code}: {resp[:200]}')
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(5 ** attempt)
                continue
            print(f'[gen_margin_m227] Error: {e}')
            return None
    return None


def main():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[gen_margin_m227] ===== 开始 [{now}] =====')

    fresh = em_fetch()
    if not fresh:
        print('[gen_margin_m227] ❌ 东财无数据，退出')
        return False
    print(f'[gen_margin_m227] 东财拉到 {len(fresh)} 个交易日')

    # 合并线上已有历史（按日期累积，防止文件被清后丢历史）
    existing = {}
    info = api_get('margin_m227.json')
    if info and info.get('content'):
        try:
            remote = json.loads(base64.b64decode(info['content']).decode('utf-8'))
            if isinstance(remote, list):
                for it in remote:
                    if it.get('date') and it.get('guarantee_ratio') is not None:
                        existing[it['date']] = (it.get('fin_balance'), it.get('guarantee_ratio'))
        except Exception as e:
            print('[gen_margin_m227] 解析线上历史失败(以东财重建):', e)
    for d, (fb, gr) in fresh.items():
        existing[d] = (fb, gr)

    merged = [{'date': d, 'fin_balance': existing[d][0], 'guarantee_ratio': existing[d][1]}
              for d in sorted(existing.keys())]
    # 仅保留最近 800 点，控制体积
    if len(merged) > 800:
        merged = merged[-800:]
    out_str = json.dumps(merged, ensure_ascii=False, separators=(',', ':'))

    with open(LOCAL, 'w', encoding='utf-8') as f:
        f.write(out_str)
    print(f'[gen_margin_m227] 合并后 {len(merged)} 点，本地已写 margin_m227.json')

    if os.environ.get('MARGIN_M227_NOPUSH'):
        print('[gen_margin_m227] MARGIN_M227_NOPUSH 设置，跳过推送')
        return True
    if not TOKEN:
        print('[gen_margin_m227] 无 GITHUB_TOKEN，跳过推送')
        return True

    sha = info['sha'] if info else None
    res = api_put('margin_m227.json', out_str, sha, f'margin m227 sync: {now}')
    if res:
        print('[gen_margin_m227] ✅ 已推送到仓库')
        return True
    print('[gen_margin_m227] ❌ 推送失败')
    return False


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
