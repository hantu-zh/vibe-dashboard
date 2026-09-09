# -*- coding: utf-8 -*-
"""
speedrank.py - 生成 speedrank_history.json（升速排行快照），并刷新 speedrank.html 内嵌 _embed

背景（为什么要新建这个脚本）：
  speedrank.html 是纯前端页：内嵌 _embed（最新快照）+ 运行时拉 speedrank_history.json。
  原 Qclaw 时代由 speedrank_update.py 每 5 分钟产出，迁移到 GitHub Actions 时该脚本
  没有一起带来，于是 speedrank_history.json 永远停在 2026-08-20，页面显示死数据。

数据源（多级降级，针对 GitHub Actions 云 IP 常被东财限流的问题做了加固）：
  - 主源：东财 push2 系列（push2delay / push2 / push2his），按 f11（实时涨速%）排序取 Top
  - 降级源：腾讯行情 qt.gtimg.cn —— 全市场沪深A股实时价，用 (现价-今开)/今开 近似盘中升速
            （腾讯对云 IP 限流远少于东财，作为保底确保"无论如何都有数据更新"）
  - 筛选式 fs=m:0+t:6+f:!2,m:1+t:2+f:!2 精确取「沪深A股」，排除北交所/新三板/期权/ST

输出：
  - speedrank_history.json：{日期: {snapshots: [{time, items}]}}，每日追加快照（同分钟不重复）
  - speedrank.html：将最新快照写入 var _embed = {...}（页面 fetch 失败时的兜底）

安全策略：
  - 仅在交易日 09:30-15:05 抓取；非交易时段/非交易日直接退出，不写空快照（避免污染页面）
  - 抓取不足 10 条时不追加（视为数据源异常），保留上一交易日数据
  - 每个交易日快照上限 30，历史保留最近 40 个交易日（控制文件体积）
  - 东财单节点超时 10s × 重试 1 次，避免云 IP 被封时长时间挂起拖垮 20 分钟 job
"""
import sys
import os
import json
import ssl
import time
import urllib.request
import urllib.parse
import re
from datetime import datetime, date

import paths

sys.stdout.reconfigure(encoding='utf-8')

HISTORY_FILE = paths.w('speedrank_history.json')
HTML_FILE = paths.w('speedrank.html')

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

EM_UT = 'b2884a393a59ad64002292a3e90d46a5'
# 沪深A股筛选式：沪主板+深主板+创业板+科创板，排除北交所/新三板/期权
FS = 'm:0+t:6+f:!2,m:1+t:2+f:!2'
# 字段：f12代码 f14名称 f2现价 f3涨跌幅 f11涨速 f8换手率 f10量比 f6成交额(元)
FIELDS = 'f12,f14,f2,f3,f11,f8,f10,f6'
PZ = 30          # 多抓一些，过滤 ST 后取前 20
TOP_N = 20
MAX_SNAP_PER_DAY = 30
MAX_HISTORY_DAYS = 40
# 东财云 IP 常被限流：单节点超时从 20s 降到 10s、重试 1 次，避免挂起拖垮 job
EM_TIMEOUT = 10
EM_RETRIES = 1

# 近端法定节假日（轻量兜底；即便不全，错判日也会因休市返回空而被跳过，不会污染数据）
HOLIDAYS_2026 = {
    '2026-01-01',
    '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20',
    '2026-02-23', '2026-02-24',
    '2026-04-04', '2026-04-05', '2026-04-06',
    '2026-05-01', '2026-05-02', '2026-05-03', '2026-05-04', '2026-05-05',
    '2026-06-19', '2026-06-20', '2026-06-21',
    '2026-09-25', '2026-09-26',
    '2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05',
    '2026-10-06', '2026-10-07',
    '2026-12-25', '2026-12-31',
}


def http_get(url, timeout=EM_TIMEOUT, retries=EM_RETRIES):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA,
                'Referer': 'https://quote.eastmoney.com/',
                'Cookie': 'em_hq_fls=js; fltt=2; int_log_num_=1;',
                'Accept': '*/*',
            })
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode('utf-8', errors='ignore')
        except Exception as e:
            last = e
            time.sleep(1.5)
    raise last


def _fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def is_trading_day(d):
    """A 股交易日：排除周末 + 已知法定节假日（轻量，不依赖外部网络/akshare）"""
    if d.weekday() >= 5:
        return False
    if d.strftime('%Y-%m-%d') in HOLIDAYS_2026:
        return False
    return True


def in_trading_hours(now=None):
    """上海时间 09:30-15:05 视为交易时段"""
    now = now or datetime.now()
    total = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= total <= 15 * 60 + 5


# ---------------------------------------------------------------------------
# 主源：东财 push2 系列（按 f11 实时涨速排序）
# ---------------------------------------------------------------------------
def _parse_em(rows):
    items = []
    for r in rows:
        code = str(r.get('f12') or '')
        name = r.get('f14') or ''
        if not code or not name:
            continue
        if 'ST' in name or '*ST' in name:
            continue
        if any(k in name for k in ('购', '沽', 'ETF', '债', '基金')):
            continue
        chg = _fnum(r.get('f3'))
        items.append({
            'code': code,
            'name': name,
            'price': round(_fnum(r.get('f2')), 2),
            'chg_pct': round(chg, 2),
            'turnover': round(_fnum(r.get('f8')), 2),
            'vol_ratio': round(_fnum(r.get('f10')), 2),
            'amount_yi': round(_fnum(r.get('f6')) / 1e8, 2),
            'speed': round(_fnum(r.get('f11')), 2),  # 东财 f11 = 实时涨速(%)
        })
    return items


def fetch_eastmoney():
    bases = [
        'https://push2delay.eastmoney.com/api/qt/clist/get',
        'https://push2.eastmoney.com/api/qt/clist/get',
        'https://push2his.eastmoney.com/api/qt/clist/get',
    ]
    for base in bases:
        host = base.split('/')[2]
        try:
            url = (base + '?pn=1&pz=' + str(PZ) + '&po=1&np=1'
                   '&ut=' + EM_UT + '&fltt=2&invt=2'
                   '&fid=f11&fs=' + urllib.parse.quote(FS) + '&fields=' + FIELDS)
            data = json.loads(http_get(url))
            rows = (data.get('data') or {}).get('diff') or []
            if not rows:
                print(f'[speedrank] 东财空数据（{host}）')
                continue
            items = _parse_em(rows)
            if len(items) >= TOP_N:
                print(f'[speedrank] 东财获取 {len(items)} 条（{host}）')
                return {'time': datetime.now().strftime('%H:%M'),
                        'items': items[:TOP_N]}
            print(f'[speedrank] 东财过滤后仅 {len(items)} 条（{host}），放弃该节点')
        except Exception as e:
            print(f'[speedrank] 东财接口异常（{host}）: {type(e).__name__}: {e}')
    return None


# ---------------------------------------------------------------------------
# 降级源：腾讯行情 qt.gtimg.cn（云 IP 限流少，确保有数据更新）
# ---------------------------------------------------------------------------
def _get_hs_a_codes():
    """新浪全A股代码（沪深主板/创业板/科创板，去北交所/ST），用于腾讯降级取数"""
    base = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page={page}&num=100&sort=code&asc=1&node=hs_a")
    codes, seen = [], set()
    for page in range(1, 60):
        try:
            req = urllib.request.Request(base.format(page=page), headers={
                'Referer': 'https://finance.sina.com.cn/', 'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
                items = json.loads(r.read().decode('gbk', errors='replace'))
        except Exception as e:
            print(f'[speedrank] 新浪代码页失败(p{page}): {e}')
            break
        if not items:
            break
        for it in items:
            c = it.get('code', '')
            n = (it.get('name', '') or '').replace(' ', '')
            if not c or not c.startswith(('60', '68', '00', '30')):
                continue
            if 'ST' in n.upper() or '退' in n:
                continue
            if c in seen:
                continue
            seen.add(c)
            codes.append(c)
        if len(items) < 100:
            break
        time.sleep(0.05)
    return codes


def _fetch_tencent_quotes(codes, batch=80):
    """批量 qt.gtimg.cn 取实时行情 -> {code: {name,price,open,prev_close}}"""
    out = {}
    for i in range(0, len(codes), batch):
        syms = ','.join([('sh' + c if c.startswith('6') else 'sz' + c)
                         for c in codes[i:i + batch]])
        url = 'https://qt.gtimg.cn/q=' + syms
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Referer': 'https://gu.qq.com/'})
            with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
                text = r.read().decode('gbk', errors='replace')
        except Exception as e:
            print(f'[speedrank] 腾讯行情批失败: {e}')
            continue
        for line in text.strip().split(';'):
            line = line.strip()
            if '=' not in line:
                continue
            _, val = line.split('=', 1)
            val = val.strip().strip('"')
            if not val:
                continue
            p = val.split('~')
            if len(p) < 6:
                continue
            name, code = p[1], p[2]
            try:
                price = float(p[3]); prev_close = float(p[4]); openp = float(p[5])
            except ValueError:
                continue
            out[code] = {'name': name, 'price': price,
                         'open': openp, 'prev_close': prev_close}
        time.sleep(0.05)
    return out


def fetch_tencent():
    print('[speedrank] 东财不可用，启用腾讯行情降级...')
    codes = _get_hs_a_codes()
    if not codes:
        print('[speedrank] 腾讯降级：代码列表为空，放弃')
        return None
    print(f'[speedrank] 腾讯降级：取到 {len(codes)} 只沪深A股，拉取实时行情中...')
    quotes = _fetch_tencent_quotes(codes)
    if not quotes:
        print('[speedrank] 腾讯降级：行情为空，放弃')
        return None
    items = []
    for code, q in quotes.items():
        if not q['open'] or q['open'] <= 0 or not q['price']:
            continue
        if 'ST' in q['name'].upper() or '退' in q['name']:
            continue
        if any(k in q['name'] for k in ('ETF', '债', '基金', '购', '沽')):
            continue
        # 盘中升速近似：(现价-今开)/今开，衡量开盘后拉升幅度
        speed = (q['price'] - q['open']) / q['open'] * 100
        chg = ((q['price'] - q['prev_close']) / q['prev_close'] * 100
               if q['prev_close'] else 0.0)
        items.append({
            'code': code, 'name': q['name'],
            'price': round(q['price'], 2),
            'chg_pct': round(chg, 2),
            'turnover': 0.0, 'vol_ratio': 0.0, 'amount_yi': 0.0,
            'speed': round(speed, 2),
        })
    items.sort(key=lambda x: x['speed'], reverse=True)
    if len(items) >= TOP_N:
        print(f'[speedrank] 腾讯降级获取 {len(items)} 条，'
              f'Top1={items[0]["name"]}({items[0]["speed"]}%)')
        return {'time': datetime.now().strftime('%H:%M'), 'items': items[:TOP_N]}
    print(f'[speedrank] 腾讯降级过滤后仅 {len(items)} 条，放弃')
    return None


def fetch_speedrank():
    """主抓取：东财优先，失败降级腾讯。返回 {time, items} 或 None"""
    emb = fetch_eastmoney()
    if emb:
        return emb
    return fetch_tencent()


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            return json.load(open(HISTORY_FILE, 'r', encoding='utf-8'))
        except Exception as e:
            print(f'[speedrank] 读取历史失败: {e}')
    return {}


def save_history(history):
    # 控制体积：每个交易日快照上限，历史保留最近 N 天
    for day in history:
        snaps = history[day].get('snapshots', [])
        if len(snaps) > MAX_SNAP_PER_DAY:
            history[day]['snapshots'] = snaps[-MAX_SNAP_PER_DAY:]
    days = sorted(history.keys())
    if len(days) > MAX_HISTORY_DAYS:
        for old in days[:len(days) - MAX_HISTORY_DAYS]:
            del history[old]
    json.dump(history, open(HISTORY_FILE, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)


def update_html_embed(embed):
    if not os.path.exists(HTML_FILE):
        print('[speedrank] HTML 文件不存在，跳过 _embed 更新')
        return
    html = open(HTML_FILE, 'r', encoding='utf-8').read()
    embed_str = json.dumps(embed, ensure_ascii=False)
    new_html = re.sub(
        r'var _embed\s*=\s*\{.*?\};',
        'var _embed = ' + embed_str + ';',
        html, count=1, flags=re.DOTALL)
    if new_html != html:
        open(HTML_FILE, 'w', encoding='utf-8').write(new_html)
        print('[speedrank] speedrank.html _embed 已更新')
    else:
        print('[speedrank] [WARN] _embed 替换未命中，检查 speedrank.html 结构')


def main():
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')
    print(f'\n[speedrank] ===== 升速快照 {today} {now.strftime("%H:%M")} =====')

    if not is_trading_day(now.date()):
        print('[speedrank] 非交易日，跳过')
        return
    if not in_trading_hours(now):
        print('[speedrank] 非交易时段（仅 09:30-15:05 抓取），跳过')
        return

    embed = fetch_speedrank()
    if not embed or len(embed['items']) < 10:
        print('[speedrank] 抓取不足 10 条，视为数据源异常，保留既有数据，本次不追加')
        return

    print(f'[speedrank] 抓取完成: {len(embed["items"])} 只，'
          f'涨速Top1={embed["items"][0]["name"]}({embed["items"][0]["speed"]}%)')

    history = load_history()
    if today not in history:
        history[today] = {'snapshots': []}
    existing = {s['time'] for s in history[today].get('snapshots', [])}
    if embed['time'] not in existing:
        history[today]['snapshots'].append(embed)
        print(f'[speedrank] 快照追加 {today} {embed["time"]}，今日共 '
              f'{len(history[today]["snapshots"])} 个')
    else:
        print(f'[speedrank] 同分钟快照已存在，跳过 {embed["time"]}')

    save_history(history)
    update_html_embed(embed)

    top3 = [f'{i["name"]}({i["speed"]}%)' for i in embed['items'][:3]]
    print(f'[speedrank] 完成 | Top3: {top3}')
    print(f'[speedrank] ===== END =====\n')


if __name__ == '__main__':
    main()
