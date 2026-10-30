# -*- coding: utf-8 -*-
"""
gen_margin_hs300.py - 生成「沪深300 日收盘 + 涨跌幅」时间序列 (margin_hs300.json)

用途：cffex.html Tab2 新增「融资余额增速 × 沪深300 背离」图的角度三数据源。
融资余额增速由前端用已有 margin_m227.json 的 fin_balance 现算，本文件只管沪深300。

每个交易日由 GitHub Actions (margin_ratio_sync.yml) 运行：
  1. 拉取腾讯日K web.ifzq.gtimg.cn/appstock/app/fqkline/get（sh000300, 前复权, 320 根）
  2. 计算每日涨跌幅 pct（%）；北京时间 15:30 前剔除当日未收盘 K
  3. 与线上已有 margin_hs300.json 合并（按日期累积历史，去重），仅保留最近 800 点
  4. 写本地 margin_hs300.json 并自推回仓库（触发 GitHub Pages 更新）

设计要点：
  - 仅用标准库（urllib/ssl/json/base64），GitHub Actions 无需 pip 安装依赖
  - 自推走 GitHub Contents API（与 gen_margin_m227.py 同机制），带 409 重试
  - 本地调试可用环境变量 HS300_NOPUSH=1 仅生成不推送
  - 自包含实现，不与 gen_margin_m227.py 共享代码（禁止跨公式抽取共享 helper）
"""
import os, sys, json, time, ssl, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API = 'https://api.github.com'
REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
LOCAL = os.path.join(BASE_DIR, 'margin_hs300.json')

# 腾讯日K：前复权 320 根（覆盖 >1 年）；_var 不加，纯 JSON
TX_API = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
          '?param=sh000300,day,,,320,qfq')

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


def tx_fetch():
    """拉取腾讯沪深300日K，返回 {日期: (close, pct)}。15:30 前剔除当日未收盘K。"""
    req = urllib.request.Request(TX_API, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                j = json.loads(r.read())
            d = (j.get('data') or {}).get('sh000300') or {}
            rows = d.get('qfqday') or d.get('day') or []
            if not rows:
                print('[gen_margin_hs300] 腾讯返回空数据')
                return None
            # 当日未收盘 K 剔除：北京时间 15:30 前且最后一根是今天 → 丢弃
            now_bj = datetime.now(timezone(timedelta(hours=8)))
            last_d = str(rows[-1][0])[:10]
            if last_d == now_bj.strftime('%Y-%m-%d') and now_bj.time() < datetime.strptime('15:30', '%H:%M').time():
                rows = rows[:-1]
            out = {}
            prev_c = None
            for row in rows:
                dt = str(row[0])[:10]
                try:
                    c = float(row[2])  # [date, open, close, high, low, ...]
                except Exception:
                    prev_c = None
                    continue
                pct = None
                if prev_c is not None and prev_c > 0:
                    pct = round((c - prev_c) / prev_c * 100, 2)
                out[dt] = (round(c, 2), pct)
                prev_c = c
            return out
        except Exception as e:
            if attempt < 2:
                time.sleep(3 ** attempt)
                continue
            print('[gen_margin_hs300] 腾讯拉取失败:', e)
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
            print('[gen_margin_hs300] GET 失败:', e)
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
                print(f'[gen_margin_hs300] 409 冲突，重取 SHA 重试 ({attempt+1})')
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
            print(f'[gen_margin_hs300] HTTP {e.code}: {resp[:200]}')
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(5 ** attempt)
                continue
            print(f'[gen_margin_hs300] Error: {e}')
            return None
    return None


def main():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[gen_margin_hs300] ===== 开始 [{now}] =====')

    fresh = tx_fetch()
    if not fresh:
        print('[gen_margin_hs300] ❌ 腾讯无数据，退出')
        return False
    print(f'[gen_margin_hs300] 腾讯拉到 {len(fresh)} 个交易日')

    # 合并线上已有历史（按日期累积，防止抓取窗口缩短丢历史）
    existing = {}
    info = api_get('margin_hs300.json')
    if info and info.get('content'):
        try:
            remote = json.loads(base64.b64decode(info['content']).decode('utf-8'))
            remote_list = remote['data'] if isinstance(remote, dict) else remote
            if isinstance(remote_list, list):
                for it in remote_list:
                    if it.get('date'):
                        existing[it['date']] = (it.get('close'), it.get('pct'))
        except Exception as e:
            print('[gen_margin_hs300] 解析线上历史失败(以腾讯重建):', e)
    for dt, (c, p) in fresh.items():
        existing[dt] = (c, p)

    merged = [{'date': dt, 'close': existing[dt][0], 'pct': existing[dt][1]}
              for dt in sorted(existing.keys())]
    # 仅保留最近 800 点，控制体积
    if len(merged) > 800:
        merged = merged[-800:]
    updated_at = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M:%S+08:00')
    out = {'updated_at': updated_at, 'data': merged}
    out_str = json.dumps(out, ensure_ascii=False, separators=(',', ':'))

    with open(LOCAL, 'w', encoding='utf-8') as f:
        f.write(out_str)
    print(f'[gen_margin_hs300] 合并后 {len(merged)} 点，本地已写 margin_hs300.json')

    if os.environ.get('HS300_NOPUSH'):
        print('[gen_margin_hs300] HS300_NOPUSH 设置，跳过推送')
        return True
    if not TOKEN:
        print('[gen_margin_hs300] 无 GITHUB_TOKEN，跳过推送')
        return True

    sha = info['sha'] if info else None
    res = api_put('margin_hs300.json', out_str, sha, f'margin hs300 sync: {now}')
    if res:
        print('[gen_margin_hs300] ✅ 已推送到仓库')
        return True
    print('[gen_margin_hs300] ❌ 推送失败')
    return False


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
