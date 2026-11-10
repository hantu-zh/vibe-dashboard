#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场情绪 · 赚钱效应 —— 通道 2 本地生成器（仓库内版本，由 breadth.yml 每交易日 15:30 调用）。

职责：抓 4 个真实行情源 → 构造 raw → 调 T0 定稿的 scoreFactors(raw, config)
      → 写 breadth_snapshot.json（当日兜底）+ upsert breadth_history.json（每日一条）。

硬约束（对应方案 §4.2 / §5.2）：
  * 日期一律 ZoneInfo("Asia/Shanghai")，禁止 datetime.utcnow() / toISOString()。
    T-1 实测：传未来日 date 时东财会静默回落「当日」数据，算错一天不会报错。
  * 任一源失败 → 对应 raw 字段为 null、sources[].ok=false，由纯函数按 coverage 归一化，
    绝不填 0 / 50% / 随机占位值。
  * 评分逻辑只来自仓库根目录的 scoreFactors.py + breadth_factors.json（单一真源）。
  * 同名交易日重跑为 upsert 覆盖，保留最后一次 as_of；history 上限 120 条。

用法：python gen_breadth.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# 生产（GitHub Actions Ubuntu，有系统 tzdata）强制走 ZoneInfo。
# 本机 Windows Python 3.13 无 IANA tzdata 且未装 tzdata 包，实测会抛
# ZoneInfoNotFoundError('No time zone found with key Asia/Shanghai')。
# 中国大陆自 1991 年起不实行夏令时，Asia/Shanghai 恒为 UTC+8，
# 故本地原型允许固定偏移 fallback，并在运行时输出 TZ_MODE 明示，绝不静默降级。
try:
    from zoneinfo import ZoneInfo
    SH = ZoneInfo('Asia/Shanghai')
    TZ_MODE = 'zoneinfo:Asia/Shanghai'
except Exception:
    SH = timezone(timedelta(hours=8), 'Asia/Shanghai')
    TZ_MODE = 'fixed UTC+8 (fallback: no tzdata)'
HERE = os.path.dirname(os.path.abspath(__file__))
T0 = HERE                      # 仓库内版本：配置与纯函数同目录
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
EM_UT = '7eea3edcaed734bea9cbfc24409ed989'
CLIST_UT = 'c92c50e6b0fab2c17cd5e276e9a79c42'
CLIST_FS = 'm:1+t:2,m:0+t:6,m:1+t:23,m:0+t:80'
HISTORY_LIMIT = 120
TIMEOUT = 20

# T0 定稿的纯函数与配置：不复制、不改写，直接引用
with open(os.path.join(T0, 'breadth_factors.json'), encoding='utf-8') as f:
    CFG = json.load(f)
_spec = importlib.util.spec_from_file_location('sf_py', os.path.join(T0, 'scoreFactors.py'))
_sf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sf)
scoreFactors = _sf.scoreFactors


def now_sh():
    return datetime.now(SH)


def market_status(now):
    """时段态。注意：仅按周末判非交易日，法定节假日需接 trading_day.py（见方案 §4.2）。"""
    if now.weekday() >= 5:
        return 'non_trading'
    hm = now.hour * 60 + now.minute
    if hm < 9 * 60 + 30:
        return 'preopen'
    if hm < 15 * 60:
        return 'intraday'
    if hm < 15 * 60 + 30:
        return 'closing'
    return 'close'


def curl_json(url):
    t0 = time.monotonic()
    r = subprocess.run(
        ['curl', '-s', '--max-time', str(TIMEOUT), '-A', UA, url],
        capture_output=True, text=True,
    )
    ms = int((time.monotonic() - t0) * 1000)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError('curl rc=%s %s' % (r.returncode, r.stderr[:80]))
    return json.loads(r.stdout), ms


def pool_url(endpoint, sort, day_str):
    return ('https://push2ex.eastmoney.com/%s?ut=%s&dpt=wz.ztzt'
            '&Pageindex=0&Pagesize=500&sort=%s&date=%s'
            % (endpoint, EM_UT, sort, day_str))


def clist_url():
    return ('https://push2dycalc.eastmoney.com/api/qt/clist/get?fs=%s'
            '&fltt=2&invt=2&fields=f3&fid=f3&po=1&pn=1&pz=6000&np=1&ut=%s'
            % (CLIST_FS, CLIST_UT))


def fetch_clist():
    j, _ = curl_json(clist_url())
    if j.get('rc') != 0 or not (j.get('data') or {}).get('diff'):
        raise RuntimeError('clist rc=%r / diff 为空' % j.get('rc'))
    diff = j['data']['diff']
    up = down = flat = skipped = 0
    for x in diff:
        v = x.get('f3')
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            skipped += 1          # 停牌 / 无报价：不计入分母
            continue
        if v > 0:
            up += 1
        elif v < 0:
            down += 1
        else:
            flat += 1
    return {'up': up, 'down': down, 'flat': flat,
            'total': up + down + flat, 'skipped': skipped}


def fetch_pool(endpoint, sort, day_str, kind):
    j, _ = curl_json(pool_url(endpoint, sort, day_str))
    data = j.get('data')
    if data is None:
        raise RuntimeError('%s data=null (date 必传)' % kind)
    pool = data.get('pool') or []
    tc = data.get('tc')
    count = int(tc) if isinstance(tc, (int, float)) else len(pool)
    if kind != 'zt':
        return {'count': count}
    lbcs = [int(x.get('lbc') or 0) for x in pool]
    return {'count': count,
            'max_lb': max(lbcs) if lbcs else 0,
            'lb_cnt': sum(1 for v in lbcs if v >= 2)}


def derive(raw, scale):
    """事实层展示用的派生值（四则运算，无业务阈值；阈值全在配置里）。"""
    up, down, flat = raw.get('up'), raw.get('down'), raw.get('flat')
    zt, zb = raw.get('zt'), raw.get('zb')
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    tot = up + down + flat if all(num(v) for v in (up, down, flat)) else None
    den = zt + zb if num(zt) and num(zb) else None
    bomb = zb / den * scale if den else None
    return {
        'up_ratio': up / tot * scale if tot else None,
        'up_down_ratio': (up / down if down else None) if num(up) and num(down) else None,
        'zt_plus_zb': den,
        'bomb_rate': bomb,
        'seal_rate': scale - bomb if bomb is not None else None,
    }


def main():
    now = now_sh()
    day = now.strftime('%Y-%m-%d')
    day_compact = now.strftime('%Y%m%d')
    status = market_status(now)

    jobs = [
        ('clist', lambda: fetch_clist()),
        ('zt_pool', lambda: fetch_pool('getTopicZTPool', 'fbt%3Aasc', day_compact, 'zt')),
        ('dt_pool', lambda: fetch_pool('getTopicDTPool', 'fund%3Aasc', day_compact, 'dt')),
        ('zb_pool', lambda: fetch_pool('getTopicZBPool', 'fbt%3Aasc', day_compact, 'zb')),
    ]
    sources, results = [], {}
    for name, fn in jobs:
        t0 = time.monotonic()
        rec = {'name': name, 'ok': False, 'fetched_at': now_sh().isoformat(),
               'latency_ms': None, 'error': None}
        try:
            results[name] = fn()
            rec['ok'] = True
        except Exception as e:                       # 显式失败，绝不补假数据
            results[name] = None
            rec['error'] = '%s: %s' % (type(e).__name__, str(e)[:160])
        rec['latency_ms'] = int((time.monotonic() - t0) * 1000)
        sources.append(rec)

    cl = results['clist'] or {}
    zt = results['zt_pool'] or {}
    dt = results['dt_pool'] or {}
    zb = results['zb_pool'] or {}
    raw = {
        'up': cl.get('up'), 'down': cl.get('down'), 'flat': cl.get('flat'),
        'total': cl.get('total'), 'skipped': cl.get('skipped'),
        'zt': zt.get('count'), 'max_lb': zt.get('max_lb'), 'lb_cnt': zt.get('lb_cnt'),
        'dt': dt.get('count'), 'zb': zb.get('count'),
    }
    res = scoreFactors(raw, CFG)
    derived = derive(raw, CFG['percent_scale'])

    snap = {
        'schema_version': '1.0',
        'factors_spec_version': CFG['version'],
        'trading_day': day,
        'as_of': now.isoformat(),
        'status': status,
        'displayable': res['displayable'],
        'score': res['score'],
        'level': res['level'],
        'coverage': res['coverage'],
        'coverage_total': res['coverage_total'],
        'data_quality': res['data_quality'],
        'raw': raw,
        'derived': derived,
        'factors': res['factors'],
        'notes': res['notes'],
        'sources': sources,
    }
    snap_path = os.path.join(HERE, 'breadth_snapshot.json')
    # newline='' 必须保留：Windows 文本模式会把 \n 转 \r\n，导致 Actions(Ubuntu/LF)
    # 每次重跑产生整文件 diff，且与 git 层核验的字节数不一致。
    with open(snap_path, 'w', encoding='utf-8', newline='') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    hist_path = os.path.join(HERE, 'breadth_history.json')
    days = []
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding='utf-8') as f:
                days = json.load(f).get('days') or []
        except Exception:
            days = []
    days = [d for d in days if d.get('trading_day') != day]   # 同日覆盖
    days.append({
        'trading_day': day, 'as_of': now.isoformat(), 'status': status,
        'score': res['score'], 'level': res['level'],
        'coverage': res['coverage'], 'displayable': res['displayable'],
        'data_quality': res['data_quality'],
    })
    days = sorted(days, key=lambda d: d['trading_day'])[-HISTORY_LIMIT:]
    with open(hist_path, 'w', encoding='utf-8', newline='') as f:
        json.dump({'schema_version': '1.0', 'days': days}, f, ensure_ascii=False, indent=2)

    ok = sum(1 for s in sources if s['ok'])
    print('TZ_MODE=%s' % TZ_MODE)
    print('trading_day=%s status=%s  sources_ok=%d/4  coverage=%d/%d  score=%s  level=%s  displayable=%s' % (
        day, status, ok, res['coverage'], res['coverage_total'],
        res['score'], res['level'], res['displayable']))
    print('raw=%s' % json.dumps(raw, ensure_ascii=False))
    print('derived=%s' % json.dumps(derived, ensure_ascii=False))
    for s in sources:
        print('  %-9s ok=%-5s %4sms %s' % (s['name'], s['ok'], s['latency_ms'], s['error'] or ''))
    print('wrote %s / %s (history %d/%d days)' % (
        os.path.basename(snap_path), os.path.basename(hist_path), len(days), HISTORY_LIMIT))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
