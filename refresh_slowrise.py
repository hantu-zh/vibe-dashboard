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
"""
import sys, os, json, ssl, time, datetime, urllib.request, urllib.parse

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
    return []


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


def fetch_constituents(code: str, top_n: int = CONS_N, retry: int = 3) -> list:
    """取某板块(BKxxxx)的成分股，返回 [{code, name, chg}, ...]（按涨跌幅降序）。
    多 host 回退 + 重试：定时任务时段东财主接口偶发不可达，切换 host/重试可显著提升成功率。"""
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
        stocks = fetch_constituents(b['code'])
        trend[today][b['name']]['code'] = b['code']
        trend[today][b['name']]['stocks'] = stocks
        if stocks:
            print(f'[refresh]   {b["name"]} 成分股 {len(stocks)} 只')

    # 额外给「慢热 top10」板块补成分股：它们常不在涨跌幅前25，但慢热 tab 需要挂个股
    slow_names = set(compute_slow_rise_names(trend))
    for b in ranked:
        nm = b['name']
        if nm in slow_names and b.get('code') and not trend[today].get(nm, {}).get('stocks'):
            stocks = fetch_constituents(b['code'])
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
def _latest_empty_date():
    """返回最近一个『全部板块 stocks 均为空』的交易日；无可补则返回最新日期"""
    if not os.path.exists(TREND_PATH):
        return None
    try:
        with open(TREND_PATH, 'r', encoding='utf-8') as f:
            trend = json.load(f)
    except Exception:
        return None
    dates = sorted([d for d in trend if d[0:1].isdigit()])
    for d in reversed(dates):
        day = trend.get(d)
        if not isinstance(day, dict):
            continue
        if any(isinstance(v, dict) and v.get('stocks') for v in day.values()):
            continue  # 已有成分股，跳过
        return d
    return dates[-1] if dates else None


def backfill_stocks(target_date: str = None, force: bool = False):
    """为指定日期（或最近一个成分股全空的日期）回填 stocks/code。
    只更新该日期的 stocks/code 字段，绝不覆盖 rank/chg，也不新增日期。"""
    if not os.path.exists(TREND_PATH):
        print('[backfill] 无 vibe_trend_history.json，退出')
        return
    with open(TREND_PATH, 'r', encoding='utf-8') as f:
        trend = json.load(f)
    if not target_date:
        target_date = _latest_empty_date()
    if not target_date or target_date not in trend:
        print(f'[backfill] 目标日期 {target_date} 不存在，退出')
        return
    day = trend[target_date]
    if not isinstance(day, dict):
        print(f'[backfill] {target_date} 非 dict 结构，退出')
        return
    # name->code 映射：优先用当日已有 code；否则用实时板块列表补（板块 code 长期稳定）
    boards = fetch_boards()
    name2code = {b['name']: b['code'] for b in boards}
    if not boards:
        print('[backfill] 实时板块列表抓取失败，仅用已有 code 补数')
    done = 0
    skipped = 0
    for name, v in day.items():
        if not isinstance(v, dict):
            continue
        code = v.get('code') or name2code.get(name)
        if not code:
            skipped += 1
            continue
        if v.get('stocks') and not force:
            done += 1
            continue
        stocks = fetch_constituents(code)
        v['code'] = code
        v['stocks'] = stocks
        if stocks:
            done += 1
            print(f'[backfill]   {name}: {len(stocks)} 只')
        else:
            print(f'[backfill]   {name}: 抓取仍为空（接口失败）')
    with open(TREND_PATH, 'w', encoding='utf-8') as f:
        json.dump(trend, f, ensure_ascii=False, indent=2)
    print(f'[backfill] {target_date} 完成：处理 {done} 板块，跳过(无code) {skipped} 板块')


# ─── main ────────────────────────────────────────────────────
def main():
    date_arg = None
    write_embed = True
    backfill = None
    force = False
    for a in sys.argv[1:]:
        if a.startswith('--date'):
            date_arg = a.split('=', 1)[1] if '=' in a else None
        elif a == '--no-embed':
            write_embed = False
        elif a.startswith('--backfill'):
            backfill = a.split('=', 1)[1] if '=' in a else ''
        elif a == '--force':
            force = True

    if backfill is not None:
        backfill_stocks(backfill or None, force)
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
