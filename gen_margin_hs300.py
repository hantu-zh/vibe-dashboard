# -*- coding: utf-8 -*-
"""
gen_margin_hs300.py - 生成「沪深300 日收盘 / 涨跌幅 / 成交量」时间序列 (margin_hs300.json)

用途：
  1. cffex.html Tab2「融资余额增速 × 沪深300 背离」图的数据源；
  2. cffex.html Tab3「市场温度」里 BIAS 乖离率、量能比（地量）等指标的原始序列。

每个交易日由 GitHub Actions (margin_sync.yml) 运行：
  1. 主源：新浪 CN_MarketData.getKLineData（sh000300，日线 2500 根 ≈ 10 年，含 volume）
  2. 备源：腾讯 fqkline（sh000300，前复权 320 根 ≈ 1 年，无成交量）——新浪失败时回退
  3. 计算每日涨跌幅 pct（%）；北京时间 15:30 前剔除当日未收盘 K
  4. 与线上已有 margin_hs300.json 合并（按日期累积历史，去重），仅保留最近 2600 点
  5. 写本地 margin_hs300.json 并自推回仓库（触发 GitHub Pages 更新）

字段：
  date  'YYYY-MM-DD'
  close 收盘价（点，2 位小数）
  pct   日涨跌幅（%，2 位小数；序列首点为 null）
  vol   成交量（亿股，2 位小数；腾讯备源时为 null，字段省略）

为什么改用新浪：BIAS 的 ±σ 带、量能比的地量阈值都需要长历史才能算出有意义的分位。
腾讯接口单次上限 320 根，只有 13 个月，分位统计不可靠。

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

# 新浪日K：2500 根 ≈ 10 年，含 volume（单位：股）
SINA_API = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
            'CN_MarketData.getKLineData?symbol=sh000300&scale=240&ma=no&datalen=2500')

# 腾讯日K：前复权 320 根，备源
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


def _drop_open_bar(rows, key):
    """北京时间 15:30 前且最后一根是今天 → 丢弃未收盘 K。"""
    now_bj = datetime.now(timezone(timedelta(hours=8)))
    last_d = str(rows[-1].get(key))[:10]
    if last_d == now_bj.strftime('%Y-%m-%d') and now_bj.time() < datetime.strptime('15:30', '%H:%M').time():
        return rows[:-1]
    return rows


def sina_fetch():
    """新浪沪深300日K（2500 根）。返回 {日期: (close, pct, vol亿股)}。"""
    req = urllib.request.Request(SINA_API, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
                raw = r.read().decode('utf-8', errors='replace')
            rows = json.loads(raw)
            if not isinstance(rows, list) or not rows:
                print('[gen_margin_hs300] 新浪返回空数据')
                return None
            rows = _drop_open_bar(rows, 'day')
            out = {}
            prev_c = None
            for row in rows:
                dt = str(row.get('day'))[:10]
                try:
                    c = float(row.get('close'))
                except Exception:
                    prev_c = None
                    continue
                vol = None
                try:
                    vol = round(float(row.get('volume')) / 1e8, 2)   # 股 -> 亿股
                except Exception:
                    vol = None
                pct = None
                if prev_c is not None and prev_c > 0:
                    pct = round((c - prev_c) / prev_c * 100, 2)
                out[dt] = (round(c, 2), pct, vol)
                prev_c = c
            return out
        except Exception as e:
            if attempt < 2:
                time.sleep(3 ** attempt)
                continue
            print('[gen_margin_hs300] 新浪拉取失败:', e)
            return None
    return None


def tx_fetch():
    """备源：腾讯沪深300日K（320 根，无成交量）。返回 {日期: (close, pct, None)}。"""
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
            rows = _drop_open_bar([{'day': r[0]} for r in rows], 'day')
            # rows 现在是裁剪后的 dict 列表，需重新对齐原始行
            keep = len(rows)
            raw_rows = (d.get('qfqday') or d.get('day'))[:keep]
            out = {}
            prev_c = None
            for row in raw_rows:
                dt = str(row[0])[:10]
                try:
                    c = float(row[2])      # [date, open, close, high, low, ...]
                except Exception:
                    prev_c = None
                    continue
                pct = None
                if prev_c is not None and prev_c > 0:
                    pct = round((c - prev_c) / prev_c * 100, 2)
                out[dt] = (round(c, 2), pct, None)
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

    fresh = sina_fetch()
    src = '新浪(10年)'
    if not fresh:
        print('[gen_margin_hs300] 新浪失败 → 回退腾讯')
        fresh = tx_fetch()
        src = '腾讯(1年,备源)'
    if not fresh:
        print('[gen_margin_hs300] ❌ 两源均无数据，退出')
        return False
    print(f'[gen_margin_hs300] {src} 拉到 {len(fresh)} 个交易日')

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
                        existing[it['date']] = (it.get('close'), it.get('pct'), it.get('vol'))
        except Exception as e:
            print('[gen_margin_hs300] 解析线上历史失败(以本次重建):', e)
    for dt, tup in fresh.items():
        old = existing.get(dt)
        # 备源无 vol 时，不覆盖已有 vol
        if old and old[2] is not None and tup[2] is None:
            existing[dt] = (tup[0], tup[1], old[2])
        else:
            existing[dt] = tup

    merged = []
    for dt in sorted(existing.keys()):
        c, p, v = existing[dt]
        item = {'date': dt, 'close': c, 'pct': p}
        if v is not None:
            item['vol'] = v
        merged.append(item)
    if len(merged) > 2600:
        merged = merged[-2600:]

    vol_cnt = sum(1 for m in merged if m.get('vol') is not None)
    updated_at = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%dT%H:%M:%S+08:00')
    out = {'updated_at': updated_at, 'data': merged}
    out_str = json.dumps(out, ensure_ascii=False, separators=(',', ':'))

    with open(LOCAL, 'w', encoding='utf-8') as f:
        f.write(out_str)
    print(f'[gen_margin_hs300] 合并后 {len(merged)} 点（含成交量 {vol_cnt} 点），'
          f'{merged[0]["date"]} ~ {merged[-1]["date"]}，本地已写 ({len(out_str)}B)')

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
