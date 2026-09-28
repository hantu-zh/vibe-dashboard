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

EM_URL = 'https://push2.eastmoney.com/api/qt/clist/get'
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
    url = EM_URL + '?' + '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items())
    last_err = None
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
                print(f'[refresh] 取到 {len(out)} 个行业板块 (尝试{attempt+1})')
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


def fetch_constituents(code: str, top_n: int = CONS_N, retry: int = 2) -> list:
    """取某板块(BKxxxx)的成分股，返回 [{code, name, chg}, ...]（按涨跌幅降序）"""
    if not code:
        return []
    params = {
        'pn': '1', 'pz': str(CONS_PZ), 'po': '1', 'np': '1',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
        'fltt': '2', 'invt': '2', 'fid': 'f3',
        'fs': 'b:' + code,
        'fields': 'f12,f14,f3',
    }
    url = EM_URL + '?' + '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in params.items())
    last_err = None
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
                c = (it.get('f12') or '').strip()
                if name:
                    out.append({'code': c, 'name': name, 'chg': round(chg, 2)})
            # 按涨跌幅降序，取前 top_n
            out.sort(key=lambda x: x['chg'], reverse=True)
            return out[:top_n]
        except Exception as e:  # noqa
            last_err = e
            time.sleep(1.5)
    if last_err:
        print(f'[refresh] 成分股 {code} 抓取失败: {last_err}')
    return []


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


# ─── main ────────────────────────────────────────────────────
def main():
    date_arg = None
    write_embed = True
    for a in sys.argv[1:]:
        if a.startswith('--date'):
            date_arg = a.split('=', 1)[1] if '=' in a else None
        elif a == '--no-embed':
            write_embed = False

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
