# -*- coding: utf-8 -*-
r"""
gen_board_members.py — 板块成分股名单快照（CI / 本地均可跑）

背景（为什么需要这个文件）
──────────────────────────
index.html「市场异动」卡片点击 → 弹窗展示该板块成分股热力图（market_heat.js
的 mountBoard）。这条链路原来只有一条数据路：浏览器 JSONP 直连东财
`push2.eastmoney.com/api/qt/clist/get?fs=b:BKxxxx`。

但用户所在宽带把 **push2 整段断连**（curl / 浏览器 JSONP 全部 RemoteDisconnected），
于是弹窗只能退到「新浪板块按名称匹配」——而新浪只有 49 个一级行业 + 175 个概念，
与东财的 100 个细分行业（会展服务 / 其他数字媒体 / 烘焙食品 …）名字几乎不重合
（实测精确重合 3/100），所以点击后只会看到「数据源不可达」。

解决办法：把「板块 → 成分股代码」这份**变动很慢的名单**在服务端抓好落成静态 JSON，
前端 fetch 同源文件拿名单，再向 **腾讯行情 qt.gtimg.cn** 要实时涨幅与流通市值
（腾讯在用户网络下是通的，站点其它面板本来就在用）。名单慢、行情快，分开取。

数据源（两路，逐板择优）
────────────────────────
1) push2 clist `fs=b:BKxxxx`（首选，全量、带当日行情）——但用户出口 / 部分
   runner 会被服务端直接断连（RemoteDisconnected），失败极快，不拖慢整体。
2) 东财数据中心 `datacenter-web.eastmoney.com/api/data/v1/get`
   reportName=RPT_BOARD_CONSTITUENT（板块成分关系表，9.9 万行）。
   datacenter-web 与挂掉的 push2 不是同一段，实测本机稳定 200，作为兜底。
   概念板块在 sector_snapshot 里是腾讯代码（pt02xxx），只能按名称查。

输出 board_members.json
───────────────────────
{
  "updated": "2026-10-27 15:30",     # 抓取时间（Asia/Shanghai）
  "ts": 1793000000,
  "source": "eastmoney-dc",
  "count": {"industry": 100, "concept": 80},
  "boards": {                        # key：板块代码小写（bk1283 / pt02gn2170）
    "bk1283": {"n": "银行", "t": "industry", "c": ["600016", "600926", ...]},
    ...
  },
  "nameIndex": {"银行": "bk1283", ...}   # 代码对不上时按名称兜底
}

保护策略
────────
· 任一路抓取失败不影响单板：该板跳过，不放假数据；
· 有效板块数 < 20 → 退出码 1 且**不写文件**（保留上一份快照）；
· 单板成分数上限 MAX_MEMBERS，超出按代码序截断（热力图不需要全量）。
"""

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import paths

OUT_FILE = paths.w('board_members.json')
SNAP_FILE = paths.w('sector_snapshot.json')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
DC_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
DC_REPORT = 'RPT_BOARD_CONSTITUENT'
DC_COLS = 'BOARD_CODE,BOARD_NAME,SECURITY_CODE'
DC_HEADERS = {'User-Agent': UA, 'Referer': 'https://data.eastmoney.com/'}

DC_TIMEOUT = 15
DC_RETRIES = 3
MAX_MEMBERS = 300          # 单板块最多保留成分数
MIN_VALID = 20             # 有效板块数下限，低于视为整体失败
CONCEPT_TOP = 40           # 概念板块只做「涨幅前 40 + 跌幅前 40」，805 个全抓太慢

PUSH2_HOSTS = [
    'https://push2.eastmoney.com/api/qt/clist/get',
    'https://push2delay.eastmoney.com/api/qt/clist/get',
    'https://43.push2.eastmoney.com/api/qt/clist/get',
]
PUSH2_TIMEOUT = 6
EM_UT = 'fa5fd1943c7b386f172d6893dbfba10b'

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def _get_json(url, timeout=DC_TIMEOUT, retries=DC_RETRIES):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers=DC_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return json.loads(r.read().decode('utf-8-sig', errors='ignore'))
        except Exception as e:      # noqa: BLE001 — 网络层异常统一退避重试
            last = e
            time.sleep(0.8)
    print(f'  ! 请求失败: {last}')
    return None


def fetch_push2_members(code):
    """push2 clist 全量成分（首选）。失败返回 []（连接被断时约 0.1s 返回）。"""
    if not code:
        return []
    c = str(code).upper()
    if not c.startswith('BK'):
        return []
    q = urllib.parse.urlencode({
        'pn': '1', 'pz': str(MAX_MEMBERS), 'po': '1', 'np': '1',
        'fltt': '2', 'invt': '2', 'fid': 'f3', 'fs': 'b:' + c,
        'fields': 'f12,f14', 'ut': EM_UT,
    })
    for host in PUSH2_HOSTS:
        try:
            req = urllib.request.Request(host + '?' + q, headers={
                'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/', 'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=PUSH2_TIMEOUT, context=CTX) as r:
                d = json.loads(r.read().decode('utf-8-sig', errors='ignore'))
            diff = ((d or {}).get('data') or {}).get('diff') or []
            out, seen = [], set()
            for it in diff:
                sc = str(it.get('f12') or '').strip()
                if sc and sc not in seen:
                    seen.add(sc)
                    out.append(sc)
            if out:
                return out[:MAX_MEMBERS]
        except Exception:      # noqa: BLE001 — 该节点不通就换下一个
            continue
    return []


def fetch_members(code='', name=''):
    """板块成分股代码列表：push2 全量优先，数据中心兜底。"""
    p2 = fetch_push2_members(code)
    if p2:
        return p2, 'p2'
    filters = []
    if code:
        c = str(code).upper()
        num = c[2:] if c.startswith('BK') else c
        filters.append(f'(BOARD_CODE="{num}")')
        if num.isdigit() and len(num) < 4:      # 0475 / 475 两种写法都试
            filters.append(f'(BOARD_CODE="{num.zfill(4)}")')
    if name:
        filters.append(f'(BOARD_NAME="{name}")')
    for f in filters:
        url = DC_URL + '?' + urllib.parse.urlencode({
            'reportName': DC_REPORT, 'columns': DC_COLS, 'filter': f,
            'pageSize': str(MAX_MEMBERS), 'pageNumber': '1',
        })
        d = _get_json(url)
        rows = ((d or {}).get('result') or {}).get('data') or []
        out, seen = [], set()
        for it in rows:
            sc = (it.get('SECURITY_CODE') or '').strip()
            if sc and sc not in seen:
                seen.add(sc)
                out.append(sc)
        if out:
            return out[:MAX_MEMBERS], 'dc'
    return [], ''


def load_boards():
    """从 sector_snapshot.json 取板块宇宙：行业全量 + 概念涨跌两端。"""
    with open(SNAP_FILE, 'r', encoding='utf-8') as f:
        snap = json.load(f)
    industry = [b for b in (snap.get('industry') or []) if b.get('name')]
    concept = [b for b in (snap.get('concept') or []) if b.get('name')]
    concept.sort(key=lambda b: b.get('change') or 0)
    picked = concept[:CONCEPT_TOP] + concept[-CONCEPT_TOP:] if len(concept) > CONCEPT_TOP * 2 else concept
    seen, uniq = set(), []
    for b in picked:
        if b['name'] not in seen:
            seen.add(b['name'])
            uniq.append(b)
    return industry, uniq


def main():
    if not os.path.exists(SNAP_FILE):
        print(f'✗ 缺少 {SNAP_FILE}，无法确定板块宇宙')
        return 1
    industry, concept = load_boards()
    print(f'>> 板块宇宙: 行业 {len(industry)} / 概念 {len(concept)}')

    boards, name_index = {}, {}
    stats = {'industry': 0, 'concept': 0}
    src_stats = {'p2': 0, 'dc': 0}

    for scope, items in (('industry', industry), ('concept', concept)):
        for i, b in enumerate(items, 1):
            code = (b.get('code') or '').strip()
            name = (b.get('name') or '').strip()
            members, msrc = fetch_members(code=code, name=name)
            if not members:
                print(f'  - {name}({code}) 无成分数据，跳过')
                continue
            key = (code or name).lower()
            boards[key] = {'n': name, 't': scope, 'c': members, 's': msrc}
            name_index[name.replace(' ', '')] = key
            stats[scope] += 1
            src_stats[msrc] = src_stats.get(msrc, 0) + 1
            if i % 20 == 0:
                print(f'  .. {scope} {i}/{len(items)}')

    total = stats['industry'] + stats['concept']
    print(f'>> 抓取完成: 行业 {stats["industry"]} / 概念 {stats["concept"]}'
          f'（共 {total}；push2 {src_stats.get("p2", 0)} / 数据中心 {src_stats.get("dc", 0)}）')
    if total < MIN_VALID:
        print('✗ 有效板块过少，放弃写入（保留旧快照）')
        return 1

    cn = timezone(timedelta(hours=8))
    now = datetime.now(cn)
    payload = {
        'updated': now.strftime('%Y-%m-%d %H:%M'),
        'ts': int(now.timestamp()),
        'source': 'eastmoney-dc',
        'count': stats,
        'boards': boards,
        'nameIndex': name_index,
    }
    tmp = OUT_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, OUT_FILE)
    print(f'✓ 已写出 {OUT_FILE}（{os.path.getsize(OUT_FILE) / 1024:.0f} KB）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
