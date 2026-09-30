#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refresh_slowrise.py — 慢热板块数据刷新（接回每日管线）
========================================================
数据源 : 东方财富行业板块实时榜  push2.eastmoney.com/api/qt/clist/get  (fs=m:90+t:2)
         -> 返回固定的 ~100 个行业板块（日间宇宙一致，修复旧数据"每日样本不一致"根因）
产出   :
  1) vibe_trend_history.json        {date: {板块名: {rank, chg}}}   慢热 tab 主数据源
  2) daily_picks.json.sector_rankings[date]   RPS tab 复用（与 RPS_thermal_dingtalk.py 同格式）
  3) index.html 内 <script id="vibe-trend-embed">  近5日，供 file:// 兜底
规则   :
  - 按当日涨跌幅从高到低排名(1=最强)
  - 仅保留最近 30 天，自动清掉 8 月那批"细分子行业"旧宇宙脏数据
  - 可重复运行；配合 cron / GitHub Actions 每日执行即自动累积"排名递进"序列
用法   : python refresh_slowrise.py [--date YYYY-MM-DD] [--no-embed]
         python refresh_slowrise.py --backfill[=YYYY-MM-DD] [--force]
         python refresh_slowrise.py --backfill-all [--days N] [--force]
成分股 : 首选东财数据中心报表 RPT_F10_CORETHEME_BOARDTYPE（轻接口，CI 可用）
         + 腾讯行情（当日涨幅 qt.gtimg.cn / 历史涨幅 web.ifzq.gtimg.cn）；
         两者都失败才回退老的 push2 clist（该接口在云 IP 上常被限流/封禁）
"""
import sys, os, json, ssl, time, base64, datetime, urllib.request, urllib.parse, urllib.error

# ─── 路径（自带，避免 import paths 在 Python3.12+ 因 docstring 内 \U 转义而崩） ───
def _detect_vibe_dir():
    env = os.environ.get('VIBE_ROOT')
    if env and os.path.isdir(env):
        return env
    ga = os.environ.get('GITHUB_WORKSPACE')
    if ga and os.path.isdir(ga):
        return ga
    win = r'C:\Users\china\.qclaw\workspace'
    sub = os.path.join(win, 'vibe-dashboard')
    if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, 'index.html')):
        return sub
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(here, 'index.html')):
        return here
    return sub

VIBE_DIR = _detect_vibe_dir()
WS = os.path.dirname(VIBE_DIR) if os.path.basename(VIBE_DIR) == 'vibe-dashboard' else VIBE_DIR
DAILY_PICKS = os.path.join(VIBE_DIR, 'daily_picks.json')

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


def _gh_token():
    t = os.environ.get('GITHUB_TOKEN')
    if t:
        return t
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.github_token'),
                  encoding='utf-8-sig') as _f:
            return _f.read().strip()
    except Exception:
        return None


def get_remote_html(repo='hantu-zh/vibe-dashboard', branch='main', path='index.html'):
    """以 GitHub 远端最新版为基准读取 index.html（避免自续调度 job 的陈旧本地副本覆盖前端改动）。
    <=1MB 走 contents API 取 base64；超大队列（contents 不返回 content）则回退本地。"""
    tok = _gh_token()
    if not tok:
        return None
    url = f'https://api.github.com/repos/{repo}/contents/{path}?ref={branch}'
    req = urllib.request.Request(url, headers={
        'Authorization': f'token {tok}',
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            o = json.loads(r.read().decode('utf-8'))
        if 'content' not in o:
            return None
        return base64.b64decode(o['content']).decode('utf-8')
    except Exception as e:  # noqa
        print(f'[refresh] 远端拉取失败，回退本地: {e}')
        return None

EM_URL = 'https://push2.eastmoney.com/api/qt/clist/get'
# 成分股接口多 host 回退（主接口在定时任务时段偶发不可达时切换，提升成功率）
EM_HOSTS = [
    'https://push2.eastmoney.com/api/qt/clist/get',
    'https://push2delay.eastmoney.com/api/qt/clist/get',
    'https://43.push2.eastmoney.com/api/qt/clist/get',
]
# ─── 成分股新数据源（不依赖被限流的 push2 clist） ────────────────
# 2026-09-30 实测：push2 的 clist 接口对云 IP / 本机网络整段不可达（连接被 reset 或挂起），
# 导致所有 CI 生成的日期 stocks 全空。改用：
#   1) 东财数据中心报表 RPT_F10_CORETHEME_BOARDTYPE —— 轻量 JSON，按板块名/代码取成分股
#   2) 腾讯行情 —— 当日涨幅 qt.gtimg.cn 批量；历史某日涨幅 web.ifzq.gtimg.cn 日K
DC_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
DC_REPORT = 'RPT_F10_CORETHEME_BOARDTYPE'
DC_COLS = 'SECURITY_CODE,SECURITY_NAME_ABBR,BOARD_CODE,BOARD_NAME,NEW_BOARD_CODE,BOARD_TYPE,BOARD_RANK'
TQ_QUOTE = 'https://qt.gtimg.cn/q='
TQ_KLINE = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
TQ_HEADERS = {'User-Agent': UA, 'Referer': 'https://gu.qq.com/'}
DC_HEADERS = {'User-Agent': UA, 'Referer': 'https://data.eastmoney.com/'}


def _get_text(url, headers, timeout=20, enc='utf-8', retry=3, tag='http'):
    """带重试的 GET；返回 str 或 None。空响应也会重试（东财偶发空体）。"""
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                raw = r.read()
            if raw:
                return raw.decode(enc, 'replace')
        except Exception as e:  # noqa
            print(f'[{tag}] 失败{i + 1}/{retry}: {type(e).__name__}: {e}')
        time.sleep(1.2 * (i + 1))
    return None


# 磁盘缓存的「板块名 -> 成分股」映射（供回填复跑复用，避免重复打接口）
MEM_CACHE_PATH = os.path.join(VIBE_DIR, 'slowrise_members_cache.json')
# 日K 涨幅缓存（接口会限流，断点续跑必须落盘）
KLINE_CACHE_PATH = os.path.join(VIBE_DIR, 'slowrise_kline_cache.json')


def dc_members(code: str = '', name: str = '', page_size: int = 500, max_pages: int = 4):
    """东财数据中心报表取板块成分股。
    返回 (members, board_code)；members = [{'code','name','rank'}, ...]（rank=BOARD_RANK 相关性）"""
    conds = []
    if code:
        conds.append(f'(NEW_BOARD_CODE="{code}")')
    else:
        conds.append(f'(BOARD_NAME="{name}")')
    filt = ''.join(conds)
    out, board_code, page = [], code, 1
    while page <= max_pages:
        url = DC_URL + '?' + urllib.parse.urlencode({
            'reportName': DC_REPORT, 'columns': DC_COLS, 'filter': filt,
            'pageSize': str(page_size), 'pageNumber': str(page),
            'sortColumns': 'SECURITY_CODE', 'sortTypes': '1',
        })
        txt = _get_text(url, DC_HEADERS, timeout=15, retry=2, tag='dc')
        if not txt:
            break
        try:
            res = (json.loads(txt) or {}).get('result')
        except Exception:
            res = None
        if not res or not res.get('data'):
            break
        for it in res['data']:
            c = (it.get('SECURITY_CODE') or '').strip()
            nm = (it.get('SECURITY_NAME_ABBR') or '').strip()
            if not board_code:
                board_code = (it.get('NEW_BOARD_CODE') or '').strip()
            if c and nm:
                out.append({'code': c, 'name': nm, 'rank': it.get('BOARD_RANK') or 99})
        if page * page_size >= (res.get('count') or 0):
            break
        page += 1
    return out, board_code


def _tq_secid(c: str) -> str:
    """6xxxxx->sh，0/3xxxxx->sz，其余(4/8/9 北交所)->bj"""
    c = (c or '').strip()
    if not c:
        return ''
    if c[0] == '6':
        return 'sh' + c
    if c[0] in '03':
        return 'sz' + c
    return 'bj' + c


def tq_batch_quotes(codes):
    """腾讯批量行情：{code: 当日涨幅%}，每 60 只一个请求"""
    out = {}
    codes = [c for c in dict.fromkeys(codes) if c]
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        url = TQ_QUOTE + ','.join(_tq_secid(c) for c in chunk)
        txt = _get_text(url, TQ_HEADERS, timeout=12, enc='gbk', retry=2, tag='tq')
        if not txt:
            continue
        for line in txt.split(';'):
            if '="' not in line:
                continue
            body = line.split('="', 1)[1].rstrip('"\n ')
            f = body.split('~')
            if len(f) > 33 and f[2].strip():
                try:
                    out[f[2].strip()] = round(float(f[33]), 2)
                except (TypeError, ValueError):
                    pass
    return out


def sina_daily_chg(code: str, datalen: int = 40):
    """新浪日K：{date: 当日涨幅%}（相邻收盘价计算）。
    腾讯 ifzq 日K 在批量抓取时会被限流（HTTP 501），所以历史回填以新浪为主。"""
    sid = _tq_secid(code)
    if not sid:
        return {}
    url = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol={sid}&scale=240&ma=no&datalen={datalen}')
    txt = _get_text(url, {'User-Agent': UA, 'Referer': 'https://finance.sina.com.cn/'},
                    timeout=15, retry=2, tag='sinak')
    if not txt:
        return {}
    try:
        arr = json.loads(txt)
    except Exception:
        return {}
    if not isinstance(arr, list):
        return {}
    closes = []
    for r in arr:
        try:
            closes.append((r['day'][:10], float(r['close'])))
        except Exception:
            continue
    out = {}
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1][1], closes[i][1]
        if prev:
            out[closes[i][0]] = round((cur / prev - 1) * 100, 2)
    return out


def tq_daily_chg(code: str, start: str, end: str, count: int = 60):
    """腾讯日K：{date: 当日涨幅%}（区间内，用相邻收盘价计算）"""
    sid = _tq_secid(code)
    if not sid:
        return {}
    url = f'{TQ_KLINE}?param={sid},day,{start},{end},{count},qfq'
    txt = _get_text(url, TQ_HEADERS, timeout=15, retry=2, tag='tqk')
    if not txt:
        return {}
    try:
        d = json.loads(txt).get('data', {}).get(sid) or {}
    except Exception:
        return {}
    arr = d.get('qfqday') or d.get('day') or []
    closes = []
    for r in arr:
        try:
            closes.append((r[0], float(r[2])))
        except (IndexError, TypeError, ValueError):
            continue
    out = {}
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1][1], closes[i][1]
        if prev:
            out[closes[i][0]] = round((cur / prev - 1) * 100, 2)
    return out


def _load_mem_cache():
    try:
        with open(MEM_CACHE_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f) or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_mem_cache(cache):
    try:
        with open(MEM_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))
    except Exception as e:  # noqa
        print(f'[refresh] 写成分股缓存失败: {e}')


def _load_json_cache(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f) or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_json_cache(path, cache):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))
    except Exception as e:  # noqa
        print(f'[refresh] 写缓存 {os.path.basename(path)} 失败: {e}')


TREND_PATH = os.path.join(VIBE_DIR, 'vibe_trend_history.json')
KEEP_DAYS = 30          # 保留最近 N 天
EMBED_DAYS = 5          # embed 内保留天数


# ─── 交易日判定 ────────────────────────────────────────────────
def is_trading_day(dt: datetime.date) -> bool:
    if dt.weekday() >= 5:
        return False
    try:
        import chinese_calendar
        return chinese_calendar.is_workday(dt)
    except Exception:
        return True  # 装不上库就只按周末判断


# ─── 取数 ─────────────────────────────────────────────────────
def fetch_boards(retry: int = 3) -> list:
    """返回 [{name, change_pct, code}, ...] 当日全部行业板块"""
    params = {
        'pn': '1', 'pz': '200', 'po': '1', 'np': '1',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'm:90+t:2',
        'fields': 'f1,f2,f3,f4,f5,f6,f7,f12,f14',
    }
    q = '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items())
    last_err = None
    for host in EM_HOSTS:
        url = host + '?' + q
        for attempt in range(retry):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': UA,
                    'Referer': 'https://quote.eastmoney.com/',
                    'Accept': 'application/json',
                })
                with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                    raw = r.read()
                data = json.loads(raw.decode('utf-8-sig'))
                items = data.get('data', {}).get('diff', []) or []
                out = []
                for it in items:
                    name = (it.get('f14') or '').strip()
                    try:
                        chg = float(it.get('f3') or 0)
                    except (TypeError, ValueError):
                        chg = 0.0
                    code = (it.get('f12') or '').strip()
                    if name:
                        out.append({'name': name, 'change_pct': chg, 'code': code})
                if out:
                    print(f'[refresh] 取到 {len(out)} 个行业板块 (host={host.split("/")[2]}, 尝试{attempt+1})')
                    return out
            except Exception as e:  # noqa
                last_err = e
                print(f'[refresh] EM 尝试{attempt+1}失败: {type(e).__name__}: {e}')
                time.sleep(2)
    if last_err:
        print(f'[refresh] EM 全部失败: {last_err}')
    # clist 被限流/封禁时的兜底：用 push2 的 ulist.np/get 批量取「最近一天的板块宇宙」行情
    # （只在已知代码上取涨幅，板块集合与前一天一致，不会打乱 rank 序列）
    alt = _fetch_boards_via_ulist()
    if alt:
        print(f'[refresh] ulist 兜底取到 {len(alt)} 个板块（沿用最近一天的宇宙）')
        return alt
    return []


def _recent_board_codes(max_days: int = 20):
    """从 trend 历史里取最近一天代码齐全的板块列表 [(name, code), ...]"""
    try:
        with open(TREND_PATH, 'r', encoding='utf-8') as f:
            trend = json.load(f)
    except Exception:
        return []
    dates = sorted([d for d in trend if d[0:1].isdigit() and len(d) >= 8], reverse=True)
    for d in dates[:max_days]:
        day = trend.get(d)
        if not isinstance(day, dict):
            continue
        pairs = [(n, v.get('code')) for n, v in day.items()
                 if isinstance(v, dict) and str(v.get('code') or '').startswith('BK')]
        if len(pairs) >= 50:
            return pairs
    return []


def _fetch_boards_via_ulist(codes=None):
    """用 push2 ulist.np/get 批量取板块行情（clist 被封时的兜底，实测云 IP 可用）"""
    pairs = _recent_board_codes()
    names = {c: n for n, c in pairs}
    if codes is None:
        codes = [c for _, c in pairs]
    codes = [c for c in dict.fromkeys(codes) if c]
    out = []
    for i in range(0, len(codes), 80):
        chunk = codes[i:i + 80]
        url = ('https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&ut='
               'b2884a393a59ad64002292a3e90d46a5&fields=f12,f14,f3&secids='
               + ','.join('90.' + c for c in chunk))
        txt = _get_text(url, {'User-Agent': UA, 'Referer': 'https://quote.eastmoney.com/'},
                        timeout=15, retry=2, tag='ulist')
        if not txt:
            continue
        try:
            items = (json.loads(txt).get('data') or {}).get('diff') or []
        except Exception:
            continue
        for it in items:
            code = (it.get('f12') or '').strip()
            name = (it.get('f14') or '').strip() or names.get(code, '')
            try:
                chg = float(it.get('f3') or 0)
            except (TypeError, ValueError):
                chg = 0.0
            if code and name:
                out.append({'name': name, 'change_pct': chg, 'code': code})
    return out


# ─── 排名 ─────────────────────────────────────────────────────
def rank_boards(boards: list) -> list:
    n = len(boards)
    ordered = sorted(boards, key=lambda x: x['change_pct'], reverse=True)
    res = []
    for i, b in enumerate(ordered, 1):
        rps = round((1 - i / n) * 100) if n else 0
        res.append({
            'rank': i,
            'name': b['name'],
            'code': b.get('code', ''),
            'change_pct': round(b['change_pct'], 2),
            'rps': rps,
            'trend': '↑' if b['change_pct'] >= 0 else '↓',
            'strength': ('极强' if rps >= 90 else '强势' if rps >= 80 else
                         '偏强' if rps >= 70 else '中等' if rps >= 50 else
                         '偏弱' if rps >= 40 else '弱势'),
        })
    return res


# ─── 成分股（股票列在板块下面） ──────────────────────────────
CONS_TOP = 25      # 仅给当日排名前 N 的板块抓成分股（控制请求量）
CONS_N = 8         # 每个板块展示的成分股数量
CONS_PZ = 40       # 单次拉取数量，再取涨幅前 CONS_N


def fetch_constituents(code: str, top_n: int = CONS_N, retry: int = 3,
                       name: str = '', live: bool = True) -> list:
    """取某板块(BKxxxx)的成分股，返回 [{code, name, chg}, ...]（按涨跌幅降序）。
    主路径：东财数据中心报表取成分 + 腾讯批量行情取当日涨幅（云 IP 可用）；
    两条都拿不到才回退老的 push2 clist。"""
    members, bcode = dc_members(code=code, name='' if code else name)
    if members:
        chg = tq_batch_quotes([m['code'] for m in members]) if live else {}
        if len(chg) >= max(1, len(members) * 0.5):
            rows = [{'code': m['code'], 'name': m['name'], 'chg': chg.get(m['code'], 0.0)}
                    for m in members]
            rows.sort(key=lambda x: x['chg'], reverse=True)
            out = rows[:top_n]
            print(f'[refresh]   {code or name} 成分股 {len(out)} 只(数据中心)')
            return out
        # 行情拿不到就别写一堆 chg=0.0 的假数据，回退老路径
        print(f'[refresh]   {code or name} 腾讯行情覆盖不足({len(chg)}/{len(members)})，回退 push2')
    return _push2_constituents(code, top_n, retry)


def _push2_constituents(code: str, top_n: int = CONS_N, retry: int = 3) -> list:
    """老的 push2 clist 路径（云 IP 常被限流，仅作兜底）。"""
    if not code:
        return []
    params = {
        'pn': '1', 'pz': str(CONS_PZ), 'po': '1', 'np': '1',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'b:' + code,
        'fields': 'f12,f14,f3',
    }
    q = '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items())
    last_err = None
    for host in EM_HOSTS:
        url = host + '?' + q
        for attempt in range(retry):
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': UA,
                    'Referer': 'https://quote.eastmoney.com/',
                    'Accept': 'application/json',
                })
                with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
                    raw = r.read()
                data = json.loads(raw.decode('utf-8-sig'))
                items = data.get('data', {}).get('diff', []) or []
                out = []
                for it in items:
                    name = (it.get('f14') or '').strip()
                    try:
                        chg = float(it.get('f3') or 0)
                    except (TypeError, ValueError):
                        chg = 0.0
                    c = (it.get('f12') or '').strip()
                    if name and c:
                        out.append({'code': c, 'name': name, 'chg': round(chg, 2)})
                if out:
                    out.sort(key=lambda x: x['chg'], reverse=True)
                    return out[:top_n]
                last_err = '空结果'
            except Exception as e:  # noqa
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        # 该 host 全部失败，换下一个 host 重试
    if last_err:
        print(f'[refresh] 成分股 {code} 抓取失败: {last_err}')
    return []


# ─── 慢热排序（复刻前端 computeSlowRise，仅用 rank 序列取 topN 板块名） ───
def compute_slow_rise_names(trend, top_n=10):
    dates = sorted([d for d in trend if d[0:1].isdigit() and len(d) >= 8])
    if not dates:
        return []
    recent = dates[-6:]
    rec = {}
    for d in recent:
        day = trend.get(d)
        if not isinstance(day, dict):
            continue
        for name, v in day.items():
            if not isinstance(v, dict):
                continue
            rank = v.get('rank')
            if not isinstance(rank, (int, float)):
                continue
            rec.setdefault(name, []).append({'date': d, 'rank': rank})
    result = []
    for name, arr in rec.items():
        arr.sort(key=lambda x: x['date'])
        first, last = arr[0]['rank'], arr[-1]['rank']
        streak, in_top = 0, False
        for i in range(len(arr) - 1, 0, -1):
            if arr[i]['rank'] < arr[i - 1]['rank']:
                if in_top:
                    streak += 1
                else:
                    in_top, streak = True, 1
            else:
                break
        result.append({'name': name, 'streak': streak,
                       'improve': first - last, 'today_rank': last})
    result.sort(key=lambda x: (-x['streak'], -x['improve'], x['today_rank']))
    return [x['name'] for x in result[:top_n]]


# ─── 写入 vibe_trend_history.json ─────────────────────────────
def write_trend(today: str, ranked: list):
    trend = {}
    if os.path.exists(TREND_PATH):
        try:
            with open(TREND_PATH, 'r', encoding='utf-8') as f:
                trend = json.load(f)
        except Exception as e:  # noqa
            print(f'[refresh] 读取旧 trend 失败: {e}')

    # 清掉 30 天前的脏数据（旧宇宙 other养殖等一并清除）
    cutoff = (datetime.datetime.strptime(today, '%Y-%m-%d') -
              datetime.timedelta(days=KEEP_DAYS)).strftime('%Y-%m-%d')
    for d in list(trend.keys()):
        if d < cutoff:
            del trend[d]

    trend[today] = {b['name']: {'rank': b['rank'], 'chg': b['change_pct']} for b in ranked}

    # 给当日排名前 CONS_TOP 的板块补成分股（股票列在板块下面）
    top_codes = [b for b in ranked if b['rank'] <= CONS_TOP and b.get('code')]
    for b in top_codes:
        stocks = fetch_constituents(b['code'], name=b['name'])
        trend[today][b['name']]['code'] = b['code']
        trend[today][b['name']]['stocks'] = stocks
        if stocks:
            print(f'[refresh]   {b["name"]} 成分股 {len(stocks)} 只')

    # 额外给「慢热 top10」板块补成分股：它们常不在涨跌幅前25，但慢热 tab 需要挂个股
    slow_names = set(compute_slow_rise_names(trend))
    for b in ranked:
        nm = b['name']
        if nm in slow_names and b.get('code') and not trend[today].get(nm, {}).get('stocks'):
            stocks = fetch_constituents(b['code'], name=nm)
            if stocks:
                trend[today][nm]['code'] = b['code']
                trend[today][nm]['stocks'] = stocks
                print(f'[refresh]   慢热 {nm} 成分股 {len(stocks)} 只')

    # 仅保留最近 KEEP_DAYS 天
    for d in sorted(trend.keys()):
        if d < cutoff:
            trend.pop(d, None)

    with open(TREND_PATH, 'w', encoding='utf-8') as f:
        json.dump(trend, f, ensure_ascii=False, indent=2)
    print(f'[refresh] 写入 {TREND_PATH} -> {len(ranked)} 板块(含 {len(top_codes)} 板块成分股), 共 {len(trend)} 天')
    return trend


# ─── 写入 daily_picks.json.sector_rankings ───────────────────
def write_sector_rankings(today: str, ranked: list):
    for PICKS in [DAILY_PICKS, os.path.join(VIBE_DIR, 'daily_picks.json')]:
        if not os.path.exists(PICKS):
            continue
        try:
            with open(PICKS, 'r', encoding='utf-8') as f:
                d = json.load(f)
        except Exception as e:  # noqa
            print(f'[refresh] 读 {PICKS} 失败: {e}')
            continue
        d.setdefault('sector_rankings', {})[today] = ranked
        try:
            with open(PICKS, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            print(f'[refresh] 更新 sector_rankings[{today}] -> {PICKS}')
        except Exception as e:  # noqa
            print(f'[refresh] 写 {PICKS} 失败: {e}')


# ─── 重建 index.html 的 vibe-trend-embed ─────────────────────
def rebuild_embed(trend: dict, write: bool = True):
    if not write:
        return
    html_path = os.path.join(VIBE_DIR, 'index.html')
    if not os.path.exists(html_path):
        return
    dates = sorted(trend.keys())[-EMBED_DAYS:]
    embed = {d: trend[d] for d in dates}
    # 优先以【远端最新版】为基准（自续调度 job 的本地工作副本可能陈旧，会整篇覆盖手动前端改动）
    html = get_remote_html()
    if html is None:
        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html = f.read()
        except Exception as e:  # noqa
            print(f'[refresh] 读 index.html 失败: {e}')
            return
    start_tag = '<script type="application/json" id="vibe-trend-embed">'
    s = html.find(start_tag)
    if s == -1:
        print('[refresh] 未找到 vibe-trend-embed，跳过')
        return
    s_end = html.find('>', s) + 1
    e = html.find('</script>', s_end)
    if e == -1:
        print('[refresh] vibe-trend-embed 结束标签缺失，跳过')
        return
    new_html = html[:s_end] + json.dumps(embed, ensure_ascii=False) + html[e:]
    try:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(new_html)
        print(f'[refresh] 重建 vibe-trend-embed ({len(dates)} 天)')
    except Exception as e:  # noqa
        print(f'[refresh] 写 index.html 失败: {e}')


# ─── 补数：为已有日期回填成分股（不改动 rank/chg，不新增日期） ─────────
def _trend_dates(trend):
    return sorted([d for d in trend if d[0:1].isdigit() and len(d) >= 8])


def _needs_fill(day):
    """该交易日是否还有板块缺成分股（部分缺也算，便于断点续跑收敛）"""
    if not isinstance(day, dict):
        return False
    for v in day.values():
        if isinstance(v, dict) and not v.get('stocks'):
            return True
    return False


def _latest_empty_date():
    """返回最近一个『还有板块缺成分股』的交易日；全都齐了就返回最新日期"""
    if not os.path.exists(TREND_PATH):
        return None
    try:
        with open(TREND_PATH, 'r', encoding='utf-8') as f:
            trend = json.load(f)
    except Exception:
        return None
    dates = _trend_dates(trend)
    for d in reversed(dates):
        if _needs_fill(trend.get(d)):
            return d
    return dates[-1] if dates else None


def _empty_dates(trend, force=False, days=None):
    """需要补数的日期（旧->新）。force=True 表示全部日期都重抓。"""
    dates = _trend_dates(trend)
    if force:
        return dates[-days:] if days else dates
    out = [d for d in dates if _needs_fill(trend.get(d))]
    return out[-days:] if days else out


def _board_members(name, code, mem_cache, stat, legacy=None):
    """板块名/代码 -> (成员[{code,name,rank}], 板块代码)，进程内+磁盘缓存。

    旧版快照里有一批板块只有 rank/chg、没有板块代码（如 2026-09-21 的 40 个），
    名字是东财已下线的老行业名（酿酒行业/煤炭行业…）。数据中心报表
    RPT_F10_CORETHEME_BOARDTYPE 只收录概念/主题板块，按老行业名查必然为空，
    所以只做一次「XX行业 -> XX概念」的精确兜底，查不到就记为 legacy 跳过，
    绝不拿模糊匹配凑数往里写成分股。"""
    key = name or code
    e = mem_cache.get(key)
    if isinstance(e, dict):
        if e.get('stocks'):
            return e['stocks'], (e.get('code') or code)
        if e.get('legacy'):
            if legacy is not None:
                legacy.append(name)
            return [], ''
    members, bcode = dc_members(code=code, name='' if code else name)
    stat['dc'] += 1
    if not members and not code and name.endswith('行业'):
        members, bcode = dc_members(name=name[:-2] + '概念')
        stat['dc'] += 1
    if members:
        mem_cache[key] = {'code': bcode or code, 'stocks': members}
    else:
        if not code:                      # 负缓存：老行业名下次不用再请求
            mem_cache[key] = {'code': '', 'stocks': [], 'legacy': True}
        if legacy is not None and not code:
            legacy.append(name)
    return members, (bcode or code)


def backfill_stocks(target_date=None, force=False, days=None, all_days=False,
                    top_n=CONS_N, max_boards=None, save_cache=True):
    """回填成分股：数据中心报表取成分 + 腾讯取涨幅（当天用批量行情，历史日用日K）。
    只更新该日期的 stocks/code 字段，绝不覆盖 rank/chg，也不新增日期。"""
    t0 = time.time()
    if not os.path.exists(TREND_PATH):
        print('[backfill] 无 vibe_trend_history.json，退出')
        return 0
    with open(TREND_PATH, 'r', encoding='utf-8') as f:
        trend = json.load(f)

    if target_date:
        targets = [target_date] if target_date in trend else []
    elif all_days:
        targets = _empty_dates(trend, force=force, days=days)
    else:
        d = _latest_empty_date()
        targets = [d] if d else []
    if not targets:
        print('[backfill] 没有需要补数的日期，退出')
        return 0
    print(f'[backfill] 目标 {len(targets)} 天: {", ".join(targets)}')

    mem_cache = _load_mem_cache() if save_cache else {}
    stat = {'dc': 0}
    legacy = []
    need = {}                                    # (name, code) -> (members, board_code)
    for d in targets:
        day = trend.get(d)
        if not isinstance(day, dict):
            continue
        n = 0
        for name, v in day.items():
            if not isinstance(v, dict) or (v.get('stocks') and not force):
                continue
            if max_boards and n >= max_boards:
                break
            n += 1
            key = (name, v.get('code') or '')
            if key not in need:
                need[key] = _board_members(name, v.get('code') or '', mem_cache, stat, legacy)
        if not max_boards:
            continue
    hit = sum(1 for ms, _ in need.values() if ms)
    print(f'[backfill] 板块 {len(need)} 个，取到成分股 {hit} 个（数据中心请求 {stat["dc"]} 次，缓存 {len(mem_cache)} 条）')
    if legacy:
        print(f'[backfill] 其中 {len(set(legacy))} 个是旧版行业板块（快照里没记板块代码、用的是已下线的老行业名，'
              f'数据中心报表只有概念板块查不到）：{"、".join(sorted(set(legacy))[:8])}{"…" if len(set(legacy)) > 8 else ""}')
    if not hit:
        print('[backfill] 数据中心接口无数据，未写文件（避免把已有数据写坏）')
        return 0

    today_str = datetime.date.today().strftime('%Y-%m-%d')
    hist = [d for d in targets if d != today_str]
    all_codes = sorted({m['code'] for ms, _ in need.values() for m in ms})
    print(f'[backfill] 涉及个股 {len(all_codes)} 只，历史日期 {len(hist)} 天')
    kcache, live = {}, {}
    if hist:
        start = (datetime.datetime.strptime(min(hist), '%Y-%m-%d') -
                 datetime.timedelta(days=10)).strftime('%Y-%m-%d')
        count = min(120, len(hist) * 3 + 10)
        datalen = min(200, len(hist) * 2 + 15)
        # 日K 会被限流（腾讯 ~600 只后 501、新浪 ~1400 只后开始拒），落盘缓存让多次运行能收敛
        kcache = _load_json_cache(KLINE_CACHE_PATH) if save_cache else {}
        t0k, fails, dead = time.time(), 0, False
        src_stat = {'sina': 0, 'tq': 0, 'cache': 0}
        todo = [c for c in all_codes if not any(d in kcache.get(c, {}) for d in hist)]
        print(f'[backfill] 日K 需要抓 {len(todo)}/{len(all_codes)} 只（其余命中缓存）')
        for i, c in enumerate(todo, 1):
            if dead:
                break
            m = sina_daily_chg(c, datalen)
            if m:
                src_stat['sina'] += 1
            else:
                time.sleep(1.0)
                m = tq_daily_chg(c, start, max(hist), count)
                if m:
                    src_stat['tq'] += 1
            if m:
                kcache.setdefault(c, {}).update(m)
            fails = 0 if m else fails + 1
            if fails >= 40:                      # 熔断：连着几十只都拿不到，说明被限流/断网，收工
                print('[backfill] 日K连续失败 40 次，熔断（剩余留待下次运行续跑）')
                dead = True
            time.sleep(0.12)                     # 限速，别把接口打急
            if i % 300 == 0 or i == len(todo):
                print(f'[backfill]   日K {i}/{len(todo)}  ({time.time() - t0k:.0f}s, '
                      f'连续失败{fails}, 新浪{src_stat["sina"]}/腾讯{src_stat["tq"]})')
                if save_cache:
                    _save_json_cache(KLINE_CACHE_PATH, kcache)
        if save_cache:
            _save_json_cache(KLINE_CACHE_PATH, kcache)
    if today_str in targets:
        live = tq_batch_quotes(all_codes)
        print(f'[backfill] 当日行情覆盖 {len(live)}/{len(all_codes)}')

    filled_total = 0
    for d in targets:
        day = trend.get(d)
        if not isinstance(day, dict):
            continue
        filled, skipped = 0, 0
        for name, v in day.items():
            if not isinstance(v, dict) or (v.get('stocks') and not force):
                continue
            members, bcode = need.get((name, v.get('code') or ''), ([], ''))
            if not members:
                continue
            if bcode:
                v['code'] = bcode
            rows = []
            for mm in members:
                c = live.get(mm['code']) if d == today_str else kcache.get(mm['code'], {}).get(d)
                rows.append({'code': mm['code'], 'name': mm['name'],
                             'chg': c, 'rank': mm.get('rank', 99)})
            have = [r for r in rows if r['chg'] is not None]
            if have and len(have) >= max(1, int(len(rows) * 0.3)):
                have.sort(key=lambda x: x['chg'], reverse=True)
                v['stocks'] = [{'code': r['code'], 'name': r['name'], 'chg': r['chg']} for r in have[:top_n]]
                filled += 1
            else:
                # 涨幅一个都没拿到：宁可不写，也不要写一堆 chg=0.0 的假数据
                skipped += 1
        print(f'[backfill] {d}: 补 {filled} 个板块' + (f'，{skipped} 个板块因无涨幅数据跳过' if skipped else ''))
        filled_total += filled

    with open(TREND_PATH, 'w', encoding='utf-8') as f:
        json.dump(trend, f, ensure_ascii=False, indent=2)
    if save_cache:
        _save_mem_cache(mem_cache)
    print(f'[backfill] 完成：{len(targets)} 天 / {filled_total} 板块，用时 {time.time() - t0:.0f}s')
    return filled_total


# ─── main ────────────────────────────────────────────────────
def main():
    date_arg = None
    write_embed = True
    backfill = None
    force = False
    all_days = False
    days = None
    max_boards = None
    save_cache = True
    for a in sys.argv[1:]:
        if a.startswith('--date'):
            date_arg = a.split('=', 1)[1] if '=' in a else None
        elif a == '--no-embed':
            write_embed = False
        elif a.startswith('--backfill-all'):
            all_days = True
        elif a.startswith('--backfill'):
            backfill = a.split('=', 1)[1] if '=' in a else ''
        elif a.startswith('--days'):
            days = int(a.split('=', 1)[1]) if '=' in a else None
        elif a.startswith('--boards'):
            max_boards = int(a.split('=', 1)[1]) if '=' in a else None
        elif a == '--no-cache':
            save_cache = False
        elif a == '--force':
            force = True
        elif a[:1].isdigit():          # 位置参数直接写日期，工作流里最省事
            backfill = a

    if all_days or backfill is not None:
        backfill_stocks(backfill or None, force, days=days, all_days=all_days,
                        max_boards=max_boards, save_cache=save_cache)
        return

    today = date_arg or datetime.date.today().strftime('%Y-%m-%d')
    dt = datetime.datetime.strptime(today, '%Y-%m-%d').date()
    if not is_trading_day(dt):
        print(f'[refresh] {today} 非交易日，仅输出不推送（仍写文件）')

    print(f'=== 慢热板块刷新 {today} ===')
    boards = fetch_boards()
    if not boards:
        print('[refresh] 取数失败，退出')
        sys.exit(1)
    ranked = rank_boards(boards)
    print(f'[refresh] 排名完成，最强: {ranked[0]["name"]} +{ranked[0]["change_pct"]}%')

    trend = write_trend(today, ranked)
    write_sector_rankings(today, ranked)
    rebuild_embed(trend, write_embed)

    print('[refresh] 完成。Top5:')
    for b in ranked[:5]:
        print(f'   #{b["rank"]} {b["name"]} {b["change_pct"]:+.2f}%  RPS{b["rps"]}')


if __name__ == '__main__':
    main()
