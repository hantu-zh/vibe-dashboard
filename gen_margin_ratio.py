# -*- coding: utf-8 -*-
"""
gen_margin_ratio.py - 生成「融资余额占流通市值比」时间序列 (margin_ratio.json)

每个交易日由 GitHub Actions (margin_ratio_sync.yml) 运行：
  1. 拉取东财 RPT_MARGIN_TOTALDATA（融资余额 / 流通市值比 FIN_BALANCE_RATIO）
  2. 与线上已有 margin_ratio.json 合并（按日期累积历史，去重）
  3. 写本地 margin_ratio.json 并自推回仓库（触发 GitHub Pages 更新）

设计要点：
  - 仅用标准库（urllib/ssl/json/base64），GitHub Actions 无需 pip 安装依赖
  - ratio 以「小数」存储（如 0.02625 = 2.625%），前端再 ×100 显示百分比
  - 自推走 GitHub Contents API（与 etf_sync.py / patrol_cffex.py 同机制），带 409 重试
  - 本地调试可用环境变量 MARGIN_RATIO_NOPUSH=1 仅生成不推送
"""
import os, sys, json, time, ssl, base64, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API = 'https://api.github.com'
REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
LOCAL = os.path.join(BASE_DIR, 'margin_ratio.json')

# 东财数据中心（datacenter-web 稳定 200，避开 push2 间歇掐断）
EM_API = ('https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_MARGIN_TOTALDATA'
          '&columns=TRADE_DATE,MARKET_NAME,FIN_BALANCE,FIN_BALANCE_RATIO'
          '&pageSize=400&sortColumns=TRADE_DATE&sortTypes=-1&source=WEB&client=WEB')

# 不校验证书（沙箱/CI 环境证书链偶尔异常，东财为公开行情，降级处理不影响数据完整性）
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
    """拉取东财两融汇总，返回 {日期: ratio小数}（仅取「全部」市场行）。"""
    req = urllib.request.Request(EM_API, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                j = json.loads(r.read())
            rows = (j.get('result') or {}).get('data') or []
            # 每个交易日有多市场行（沪/深/京/全部）；取「全部」；缺失则用融资余额最大者近似
            by_date = {}
            maxbal = {}
            for o in rows:
                d = (o.get('TRADE_DATE') or '')[:10]
                rt = o.get('FIN_BALANCE_RATIO')
                if not d or rt is None:
                    continue
                try:
                    rt = float(rt)
                except Exception:
                    continue
                mn = o.get('MARKET_NAME')
                if mn in ('全部', '市场汇总', '总计'):
                    by_date[d] = round(rt, 6)
                mb = float(o.get('FIN_BALANCE') or 0)
                if d not in maxbal or mb > maxbal[d][0]:
                    maxbal[d] = (mb, round(rt, 6))
            out = {}
            for d in by_date:
                out[d] = by_date[d]
            for d, (_, rt) in maxbal.items():
                out.setdefault(d, rt)
            return out
        except Exception as e:
            if attempt < 2:
                time.sleep(3 ** attempt)
                continue
            print('[gen_margin_ratio] 东财拉取失败:', e)
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
            print('[gen_margin_ratio] GET 失败:', e)
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
                print(f'[gen_margin_ratio] 409 冲突，重取 SHA 重试 ({attempt+1})')
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
            print(f'[gen_margin_ratio] HTTP {e.code}: {resp[:200]}')
            return None
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(5 ** attempt)
                continue
            print(f'[gen_margin_ratio] Error: {e}')
            return None
    return None


def main():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f'\n[gen_margin_ratio] ===== 开始 [{now}] =====')

    fresh = em_fetch()
    if not fresh:
        print('[gen_margin_ratio] ❌ 东财无数据，退出')
        return False
    print(f'[gen_margin_ratio] 东财拉到 {len(fresh)} 个交易日')

    # 合并线上已有历史（按日期累积，防止文件被清后丢历史）
    existing = {}
    info = api_get('margin_ratio.json')
    if info and info.get('content'):
        try:
            remote = json.loads(base64.b64decode(info['content']).decode('utf-8'))
            # 兼容两种形态：历史纯数组 或 新包裹结构 {updated_at, data:[...]}
            remote_list = remote['data'] if isinstance(remote, dict) else remote
            if isinstance(remote_list, list):
                for it in remote_list:
                    if it.get('date') and it.get('ratio') is not None:
                        existing[it['date']] = it['ratio']
        except Exception as e:
            print('[gen_margin_ratio] 解析线上历史失败(以东财重建):', e)
    existing.update(fresh)

    merged = [{'date': d, 'ratio': existing[d]} for d in sorted(existing.keys())]
    # 仅保留最近 800 点，控制体积
    if len(merged) > 800:
        merged = merged[-800:]
    # 包裹结构：附生成时间戳（北京时间），供前端展示「更新时间戳」
    updated_at = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M:%S+08:00')
    out = {'updated_at': updated_at, 'data': merged}
    out_str = json.dumps(out, ensure_ascii=False, separators=(',', ':'))

    with open(LOCAL, 'w', encoding='utf-8') as f:
        f.write(out_str)
    print(f'[gen_margin_ratio] 合并后 {len(merged)} 点，本地已写 margin_ratio.json')

    if os.environ.get('MARGIN_RATIO_NOPUSH'):
        print('[gen_margin_ratio] MARGIN_RATIO_NOPUSH 设置，跳过推送')
        return True
    if not TOKEN:
        print('[gen_margin_ratio] 无 GITHUB_TOKEN，跳过推送')
        return True

    sha = info['sha'] if info else None
    res = api_put('margin_ratio.json', out_str, sha, f'margin ratio sync: {now}')
    if res:
        print('[gen_margin_ratio] ✅ 已推送到仓库')
        return True
    print('[gen_margin_ratio] ❌ 推送失败')
    return False


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
