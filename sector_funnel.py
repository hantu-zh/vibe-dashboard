# -*- coding: utf-8 -*-
"""
sector_funnel.py — 板块 → 个股信号漏斗
========================================
把「RPS60 中期主线板块」和「三信号源个股」（威震天 / 益盟强买 / 盗火线）关联起来：

    信号个股 → 查所属板块（东财 BK####）→ 关联板块 RPS60 → 聚合输出

输出 sector_funnel.json：
    boards   —— 有信号股的板块，按板块综合分降序；每板块含信号股清单
    orphans  —— 所属板块偏弱（综合分 < ORPHAN_LINE）的信号股（"孤军"）

用法（仓库根目录）:
    python sector_funnel.py              # 全量
环境变量:
    FUNNEL_SLEEP   逐只查询间隔秒数，默认 0.5
    FUNNEL_SKIP_NET=1  跳过网络查询（调试页面用）
"""
import json, os, re, sys, time, pathlib, subprocess

BASE = pathlib.Path(__file__).parent
OUT_JSON = BASE / 'sector_funnel.json'
RPS_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
ORPHAN_LINE = 60          # 所属板块综合分低于此值 → 孤儿信号
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
REF = 'https://quote.eastmoney.com/center/boardlist.html'

SIGNAL_FILES = [
    ('weizhentian.json',    '威震天',   'picks'),
    ('strongbuy_data.json', '益盟强买', 'yimeng'),
    ('strongbuy_data.json', '益盟涨幅', 'stocks'),
    ('daohuoxian_data.json', '盗火线',  'daohuoxian'),
]


def curl(url, to=20):
    r = subprocess.run(['curl', '-sS', '--max-time', str(to), '-A', UA, '-e', REF, url],
                       capture_output=True, text=True)
    return r.stdout


def norm6(c):
    m = re.search(r'(\d{6})', str(c))
    return m.group(1) if m else None


def secid_of(code):
    return ('1.' if code[0] in '69' else '0.') + code


def collect_signals():
    """收集三信号源个股（去重合并来源）"""
    out = {}
    for fname, tag, key in SIGNAL_FILES:
        p = BASE / fname
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text('utf-8'))
        except Exception as e:
            print('[warn] %s 解析失败: %s' % (fname, e))
            continue
        items = d.get(key) or []
        for s in items:
            code = norm6(s.get('code', ''))
            if not code:
                continue
            it = out.setdefault(code, {'name': s.get('name', ''), 'src': []})
            it['name'] = it['name'] or s.get('name', '')
            if tag not in it['src']:
                it['src'].append(tag)
    return out


def boards_push2(code, retry=2):
    url = ('https://push2.eastmoney.com/api/qt/slist/get?spt=3&fltt=2&invt=2'
           '&fields=f12,f13,f14&secid=%s&pn=1&pz=60&po=1' % secid_of(code))
    for _ in range(retry):
        t = curl(url)
        if t.startswith('{'):
            try:
                diff = (json.loads(t).get('data') or {}).get('diff') or {}
                items = list(diff.values()) if isinstance(diff, dict) else diff
                out = [(x['f12'], x.get('f14', '')) for x in items
                       if str(x.get('f12', '')).upper().startswith('BK')]
                if out:
                    return out
            except Exception:
                pass
        time.sleep(0.8)
    return None


def boards_datacenter(code, retry=2):
    url = ('https://datacenter-web.eastmoney.com/api/data/v1/get'
           '?reportName=RPT_F10_CORETHEME_BOARDTYPE'
           '&columns=SECUCODE,SECURITY_CODE,BOARD_CODE,BOARD_NAME'
           '&pageNumber=1&pageSize=60&source=HSF10&client=PC'
           '&filter=(SECURITY_CODE%%3D%%22%s%%22)' % code)
    for _ in range(retry):
        t = curl(url)
        if t.startswith('{'):
            try:
                data = (json.loads(t).get('result') or {}).get('data') or []
                if data:
                    return [('BK' + str(x['BOARD_CODE']).zfill(4), x.get('BOARD_NAME', ''))
                            for x in data]
            except Exception:
                pass
        time.sleep(0.8)
    return None


def load_rps():
    """板块 RPS：优先用 daily_picks.json 的 sector_rankings（新格式多周期），
    否则用 board_rps.py 现算。返回 {bkLower: {...}} 与 asof。"""
    dp = BASE / 'daily_picks.json'
    if dp.exists():
        try:
            sr = (json.loads(dp.read_text('utf-8')).get('sector_rankings') or {})
            dates = sorted(k for k in sr if RPS_DATE_RE.match(str(k)))
            if dates:
                rows = sr[dates[-1]] or []
                if rows and isinstance(rows[0], dict) and rows[0].get('rps60') is not None:
                    m = {}
                    for r in rows:
                        m[str(r.get('code', '')).lower()] = {
                            'name': r.get('name', ''),
                            'rps60': r.get('rps60'), 'rps20': r.get('rps20'),
                            'fresh': r.get('fresh'),
                            'score': r.get('score') or r.get('rps') or 0,
                        }
                    return m, dates[-1], 'daily_picks'
        except Exception as e:
            print('[warn] daily_picks.json RPS 读取失败: %s' % e)
    # 回退：用 board_rps.py 现算
    bk = BASE / 'ai_analysis_board_kline.json'
    if bk.exists():
        try:
            sys.path.insert(0, str(BASE))
            import board_rps as br
            st = br.load_board_kline(str(bk))
            cm, nm = br.build_series(st)
            pool = br.dedup_pool(nm)
            rows, info = br.compute(cm, nm, pool=pool)
            m = {str(r['code']).lower(): {
                'name': r['disp_name'], 'rps60': r['rps60'], 'rps20': r['rps20'],
                'fresh': r['fresh'], 'score': r['score']} for r in rows}
            return m, info['asof'], 'board_kline'
        except Exception as e:
            print('[warn] board_rps 现算失败: %s' % e)
    return {}, None, None


def main():
    print('[sector_funnel] start %s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    signals = collect_signals()
    total = len(signals)
    print('  信号个股（去重）: %d 只' % total)

    rps_map, rps_asof, rps_src = load_rps()
    print('  RPS: 基准 %s · 板块 %d · 来源 %s' % (rps_asof, len(rps_map), rps_src))

    skip_net = os.environ.get('FUNNEL_SKIP_NET') == '1'
    sleep = float(os.environ.get('FUNNEL_SLEEP', '0.5'))

    groups = {}       # bkLower -> {'name','stocks':[]}
    orphans = []
    missing = []
    ok = fail = 0
    for i, (code, meta) in enumerate(sorted(signals.items()), 1):
        bl = None
        if not skip_net:
            bl = boards_push2(code) or boards_datacenter(code)
            time.sleep(sleep)
            if (i % 10) == 0:
                print('    ... %d/%d' % (i, total))
        if bl is None:
            fail += 1
            missing.append(code)
            continue
        ok += 1
        # 取「RPS 池内综合分最高」的所属板块作为主归属
        cand = []
        for bc, bn in bl:
            r = rps_map.get(bc.lower())
            if r:
                cand.append((r['score'], bc.lower(), bn, r))
        if not cand:
            missing.append(code)
            continue
        cand.sort(key=lambda x: -x[0])
        score, bkey, bname, r = cand[0]
        stock = {'code': code, 'name': meta['name'], 'src': meta['src']}
        if score >= ORPHAN_LINE:
            g = groups.setdefault(bkey, {'name': r['name'] or bname,
                                         'rps60': r['rps60'], 'fresh': r['fresh'],
                                         'score': score, 'stocks': []})
            g['stocks'].append(stock)
        else:
            orphans.append(dict(stock, board=r['name'] or bname, rps60=r['rps60']))

    boards = sorted(groups.values(), key=lambda g: (-g['score'], -len(g['stocks'])))
    for g in boards:
        g['stocks'].sort(key=lambda s: -len(s['src']))
    out = {
        'updated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'asof': rps_asof,
        'universe': len(rps_map),
        'total_signals': total,
        'boards': boards,
        'orphans': orphans,
        'missing': missing,
        'stats': {'ok': ok, 'fail': fail},
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8', newline='\n')
    print('  查询: 成功 %d / 失败 %d | 上榜板块 %d | 孤儿 %d | 未匹配 %d'
          % (ok, fail, len(boards), len(orphans), len(missing)))
    for g in boards[:8]:
        print('    %-12s RPS60=%-3s %d 只: %s' % (
            g['name'], g['rps60'], len(g['stocks']),
            '、'.join(s['name'] for s in g['stocks'][:6])))
    print('  -> %s (%d bytes)' % (OUT_JSON.name, OUT_JSON.stat().st_size))


if __name__ == '__main__':
    main()
