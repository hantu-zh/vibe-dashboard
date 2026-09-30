# -*- coding: utf-8 -*-
r"""
gen_sector_snapshot.py — 全市场板块涨跌幅快照生成（CI 侧）

背景
────
前端 index.html 的「市场板块温度计」需要在浏览器里拿到**全市场**板块数据。
原先只有两条路：
  1) 浏览器端 JSONP 直连东财 push2 —— 部分运营商/网络环境会把 push2.eastmoney.com
     直接断连（curl 也无法连通），此时整块面板降级；
  2) 降级到腾讯行情的 33 个申万一级行业「代表股」均值 —— 样本太少，
     用户看到的是「行业 33 个」，不是真实板块统计。

本脚本在 GitHub Actions（云 IP，无运营商拦截）里抓东财全市场板块，
落一份 sector_snapshot.json 提交回仓库，前端 fetch 同源静态 JSON 即可拿到
行业 ~86 / 概念 ~430 个板块。东财直连可用时前端仍优先用实时数据。

输出 sector_snapshot.json：
{
  "updated": "2026-09-30 15:05",       # 抓取时间（Asia/Shanghai）
  "ts": 1759218300,                    # 抓取时间戳（前端判断新鲜度）
  "source": "eastmoney",               # eastmoney / tencent
  "count": {"industry": 86, "concept": 431},
  "industry": [{"code":"BK0475","name":"银行","change":1.23}, ...],
  "concept":  [{"code":"BK0800","name":"人工智能","change":2.31}, ...]
}

两路数据源（自动择优，前端只认结果）
────────────────────────────────────
1) 东财 push2（首选）：行业 ~86 / 概念 ~430。与站点其它模块（慢热板块、RPS、
   sector_rankings）的板块宇宙一致，口径统一。
2) 腾讯 proxy.finance.qq.com 板块榜（兜底）：
     board_type=hy2 → 124 个申万二级行业（比东财更细）
     board_type=gn  → 804 个概念板块（分页取全）
   注意：腾讯这个接口**没有 CORS/JSONP**，浏览器直连不了，所以只能落在快照里。
   实测部分家宽能访问腾讯但访问不到东财（运营商拦截 push2），此时兜底源即为生效源。

保护策略
────────
· 多节点重试（push2 / push2delay / 43.push2 / push2his）+ 东财云 IP 限流退避；
· 任一路数据源抓到 20 条以上即视为有效；两路都拿不到 → 退出码 1 且**不写文件**，
  保留上一份快照（旧快照好过没有，前端会标注快照时间与来源）。
"""

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

import paths

OUT_FILE = paths.w('sector_snapshot.json')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
EM_UT = 'fa5fd1943c7b386f172d6893dbfba10b'

# m:90+t:2=行业板块  m:90+t:3=概念板块（地域板块 t:1 不纳入）
SCOPES = [('industry', 'm:90+t:2'), ('concept', 'm:90+t:3')]

# 节点顺序对齐 refresh_slowrise.py（该脚本在 CI 每日稳定跑通同一接口）
BASES = [
    'https://push2.eastmoney.com/api/qt/clist/get',
    'https://push2delay.eastmoney.com/api/qt/clist/get',
    'https://43.push2.eastmoney.com/api/qt/clist/get',
    'https://push2his.eastmoney.com/api/qt/clist/get',
]

EM_TIMEOUT = 15
EM_RETRIES = 2
MIN_VALID = 20          # 单类板块数低于此值视为抓取失败
PZ = 900                # 概念板块约 430 个，900 足够且不会被分页截断

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


# ─── 兜底：腾讯板块榜（hy2=申万二级 124 个 / gn=概念 804 个） ───
# 该接口无 CORS/JSONP，浏览器直连不可行，只能由本脚本落快照给前端 fetch。
TQ_URL = 'https://proxy.finance.qq.com/cgi/cgi-bin/rank/pt/getRank'
TQ_SCOPES = {'industry': 'hy2', 'concept': 'gn'}
TQ_PAGE = 200           # count 上限：>=300 返回 ARG_ERROR
TQ_MAX_PAGES = 8        # 概念约 804 个 → 5 页足够，留冗余


def _http_get(url, timeout, retries, referer):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA,
                'Referer': referer,
                'Accept': '*/*',
            })
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode('utf-8-sig', errors='ignore')
        except Exception as e:      # noqa: BLE001 — 网络层任何异常都退避重试
            last = e
            time.sleep(1.5)
    raise last


def http_get(url, timeout=EM_TIMEOUT, retries=EM_RETRIES):
    return _http_get(url, timeout, retries, 'https://quote.eastmoney.com/')


def fetch_tencent_scope(key):
    """腾讯板块榜分页抓取，返回 [{code,name,change}]（失败返回 []）"""
    board = TQ_SCOPES[key]
    items, seen = [], set()
    for page in range(TQ_MAX_PAGES):
        offset = page * TQ_PAGE
        url = (TQ_URL + '?board_type=' + board
               + '&sort_type=price&direct=down'
               + '&offset=' + str(offset) + '&count=' + str(TQ_PAGE))
        try:
            data = json.loads(_http_get(url, 15, 2, 'https://gu.qq.com/'))
            rows = (data.get('data') or {}).get('rank_list') or []
            if not rows:
                break
            added = 0
            for r in rows:
                code = str(r.get('code') or '').strip()
                name = str(r.get('name') or '').strip()
                try:
                    chg = float(r.get('zdf'))
                except (TypeError, ValueError):
                    continue
                if not name or not code or code in seen:
                    continue
                seen.add(code)
                items.append({'code': code, 'name': name,
                              'change': round(chg, 2)})
                added += 1
            if added == 0 or len(rows) < TQ_PAGE:
                break
        except Exception as e:      # noqa: BLE001
            print(f'[sector-snapshot] 腾讯 {board} offset={offset} 失败: '
                  f'{type(e).__name__}: {e}')
            break
    if len(items) >= MIN_VALID:
        print(f'[sector-snapshot] 腾讯 {board} -> {len(items)} 个板块')
        return items
    print(f'[sector-snapshot] 腾讯 {board} 仅 {len(items)} 个，视为失败')
    return []


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_scope(fs):
    """抓一类板块（行业/概念），返回 [{code,name,change}]，失败返回 []"""
    for base in BASES:
        host = base.split('/')[2]
        try:
            url = (base + '?pn=1&pz=' + str(PZ) + '&po=1&np=1&fltt=2&invt=2'
                   '&fid=f3&fs=' + urllib.parse.quote(fs)
                   + '&fields=f12,f14,f3&ut=' + EM_UT
                   + '&_=' + str(int(time.time() * 1000)))
            data = json.loads(http_get(url))
            rows = (data.get('data') or {}).get('diff') or []
            items = []
            for r in rows:
                code = str(r.get('f12') or '').strip()
                name = str(r.get('f14') or '').strip()
                chg = _fnum(r.get('f3'))
                if not code or not name or chg is None:
                    continue
                items.append({'code': code, 'name': name,
                              'change': round(chg, 2)})
            if len(items) >= MIN_VALID:
                print(f'[sector-snapshot] {fs} -> {len(items)} 个板块（{host}）')
                return items
            print(f'[sector-snapshot] {fs} 仅 {len(items)} 个（{host}），放弃该节点')
        except Exception as e:      # noqa: BLE001
            print(f'[sector-snapshot] {fs} 失败（{host}）: {type(e).__name__}: {e}')
    return []


def load_prev():
    """读上一份快照，用于「单类成功」时补齐另一类"""
    try:
        with open(OUT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:               # noqa: BLE001
        return {}


def main():
    prev = load_prev()

    result, src_of = {}, {}
    for key, fs in SCOPES:
        items = fetch_scope(fs)                      # ① 东财（口径与站点其它模块一致）
        if items:
            result[key], src_of[key] = items, 'eastmoney'
            continue
        items = fetch_tencent_scope(key)             # ② 腾讯兜底（部分网络只拦东财）
        if items:
            result[key], src_of[key] = items, 'tencent'
            continue
        if isinstance(prev.get(key), list) and len(prev[key]) >= MIN_VALID:
            print(f'[sector-snapshot] {key} 两路均失败，沿用旧快照 {len(prev[key])} 个')
            result[key] = prev[key]
            src_of[key] = prev.get('source') or 'cache'

    if not result:
        print('[sector-snapshot] 全部抓取失败，保留旧快照不动')
        return 1

    srcs = set(src_of.values())
    source = srcs.pop() if len(srcs) == 1 else 'mixed'

    now = datetime.now()
    payload = {
        'updated': now.strftime('%Y-%m-%d %H:%M'),
        'ts': int(now.timestamp()),
        'source': source,
        'count': {k: len(v) for k, v in result.items()},
        'industry': result.get('industry', []),
        'concept': result.get('concept', []),
    }

    # 当天内且板块数据/来源完全没变 → 跳过写入，避免每 15 分钟一次无意义 commit
    if (prev.get('industry') == payload['industry']
            and prev.get('concept') == payload['concept']
            and prev.get('source') == payload['source']
            and str(prev.get('updated', ''))[:10] == payload['updated'][:10]):
        print('[sector-snapshot] 板块数据与今日快照一致，跳过写入')
        return 0

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

    size = os.path.getsize(OUT_FILE)
    print(f'[sector-snapshot] 已写入 {OUT_FILE} '
          f'({size:,} bytes, source={source}, 行业 {len(payload["industry"])} / '
          f'概念 {len(payload["concept"])}) @ {payload["updated"]}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:          # noqa: BLE001
        print(f'[sector-snapshot] 异常退出: {type(e).__name__}: {e}')
        sys.exit(1)
