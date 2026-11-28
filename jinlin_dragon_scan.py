# -*- coding: utf-8 -*-
"""金鳞化龙 · 平台突破每日全A扫描

模型（严格版，2026-11-28 全A 4年回测验证）：
  平台（t-1 及之前）：60日收盘振幅<=45% / MA250走平(20日变化<=4%) /
    股价距MA250 ±18% / 量能沉寂(5日均量<=60日均量*1.5)
  突破日：涨幅>=3% 且 量>=5日均量*2 且（创60日收盘新高 或 上穿"上边线"）
  上边线 = 最近一次30日KDJ D<10 当日高点（因果版，无未来函数）
  精品 = 突破日因果CYQ获利比例 >= 95%（上方无套牢盘）

输出 jinlin_dragon.json：最近 5 个交易日信号，按日分组（premium 精品 / watch 候选）。
全部判定只用当日及之前数据，无前视偏差。
"""
import sys, os, json, ssl, time, datetime, urllib.request, random
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from signal_meta import stamp  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_JSON = os.path.join(HERE, 'jinlin_dragon.json')

# ── 模型参数（与回测严格版一致，勿随意改动）─────────────────────────
PLAT_DAYS = 60
QUIET = 1.5      # 量沉寂上限（回测验证 1.5 可抓到燕达技术型温和放量）
AMP = 0.45
BAND = 0.18
MA_SLOPE = 0.04
BRK_PCT = 0.03
BRK_VR = 2.0
PREMIUM_WIN = 95.0   # 获利比例 >= 95% 为精品
WINDOW_DAYS = 5      # 保留最近 5 个交易日
MCAP_MAX = 150e8     # 流通市值上限（回测验证：排除大盘蓝筹的慢牛假信号）

KLINE_BEG = '20221101'
REQ_HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def http_json(url, tries=4, timeout=15):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=REQ_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return json.loads(r.read().decode('utf-8-sig'))
        except Exception as e:
            last = e
            time.sleep(0.5 * (i + 1) + random.random())
    raise RuntimeError('http fail %s: %s' % (url[:80], last))


def fetch_stock_list():
    """全A列表（沪深主板/创业板/科创板，已排除北交所），剔除ST/退市、市值过滤。"""
    stocks = []
    pn = 1
    while True:
        url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=100&po=1&np=1'
               '&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'
               '&fields=f12,f14,f21' % pn)
        j = http_json(url)
        d = j.get('data') or {}
        diff = d.get('diff') or []
        if not diff:
            break
        for it in diff:
            code = str(it.get('f12', ''))
            name = str(it.get('f14', ''))
            mcap = it.get('f21', '-')
            if not code or len(code) != 6:
                continue
            if 'ST' in name.upper() or '退' in name:
                continue
            if mcap in ('-', None) or not isinstance(mcap, (int, float)):
                continue
            if mcap > MCAP_MAX:
                continue
            mkt = '1' if code.startswith('6') else '0'
            stocks.append({'code': code, 'name': name, 'mcap': mcap, 'mkt': mkt})
        if len(diff) < 100 or pn * 100 >= (d.get('total') or 0):
            break
        pn += 1
    return stocks


def fetch_kline(mkt, code):
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s.%s'
           '&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'
           '&klt=101&fqt=1&beg=%s&end=20500101' % (mkt, code, KLINE_BEG))
    j = http_json(url, tries=3)
    kl = (j.get('data') or {}).get('klines') or []
    rows = []
    for line in kl:
        p = line.split(',')
        # date, open, close, high, low, volume, turnover%(f61)
        try:
            rows.append((p[0], float(p[1]), float(p[2]), float(p[3]),
                         float(p[4]), float(p[5]), float(p[10])))
        except (ValueError, IndexError):
            continue
    return rows


# ── 因果引擎（与回测 platform_engine.py 逐算法一致）────────────────────

def sma(vals, w):
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= w:
            s -= vals[i - w]
        if i >= w - 1:
            out[i] = s / w
    return out


def kdj_d(rows, n=30):
    N = len(rows)
    D = [None] * N
    k_prev = d_prev = None
    for i in range(N):
        lo = min(r[4] for r in rows[max(0, i - n + 1):i + 1])
        hi = max(r[3] for r in rows[max(0, i - n + 1):i + 1])
        rsv = (rows[i][2] - lo) / (hi - lo) * 100 if hi > lo else 50.0
        k = (1 * rsv + 2 * k_prev) / 3 if k_prev is not None else rsv
        d = (1 * k + 2 * d_prev) / 3 if d_prev is not None else k
        D[i] = d
        k_prev, d_prev = k, d
    return D


def upper_line(rows, t, kd, n=30, oversold=10.0):
    for j in range(t - 1, -1, -1):
        if kd[j] is not None and kd[j] < oversold:
            return rows[j][3]
    return None


def cyq_winner(rows, t, lookback=250, bins=150):
    """因果CYQ获利比例（当前价以下筹码占比）。"""
    start = max(0, t - lookback + 1)
    seg = rows[start:t + 1]
    lo = min(r[4] for r in seg)
    hi = max(r[3] for r in seg)
    if hi <= lo:
        return None
    step = (hi - lo) / bins
    grid = [0.0] * (bins + 1)

    def bin_of(p):
        if p < lo or p >= hi:
            return None
        return min(bins - 1, int((p - lo) / step))

    for r in seg:
        rt = max(0.001, min(0.99, r[6] / 100.0))
        for i in range(bins + 1):
            grid[i] *= (1 - rt)
        avg_p = (r[3] + r[4] + 2 * r[2]) / 4.0
        b = bin_of(avg_p)
        if b is not None:
            grid[b] += rt
    total = sum(grid)
    if total <= 0:
        return None
    cb = bin_of(seg[-1][2])
    below = sum(grid[:cb + 1]) if cb is not None else 0.0
    return below / total * 100.0


def scan_breakouts(rows):
    """返回突破日候选列表 [dict(t, nh, cu)]。因果筛选，与回测一致。"""
    n = len(rows)
    if n < 300:
        return []
    close = [r[2] for r in rows]
    vol = [r[5] for r in rows]
    ma250 = sma(close, 250)
    vma5 = sma(vol, 5)
    vma60 = sma(vol, 60)
    kd = kdj_d(rows)
    out = []
    for t in range(260, n):
        j = t - 1
        if ma250[j] is None or ma250[j - 20] is None:
            continue
        win = close[j - PLAT_DAYS + 1:j + 1]
        mn, mx = min(win), max(win)
        if mn <= 0 or (mx / mn - 1) > AMP:
            continue
        if abs(ma250[j] / ma250[j - 20] - 1) > MA_SLOPE:
            continue
        if not (ma250[j] * (1 - BAND) < close[j] < ma250[j] * (1 + BAND)):
            continue
        if vma5[j] is None or vma60[j] is None or vma5[j] > vma60[j] * QUIET:
            continue
        pct = close[t] / close[t - 1] - 1
        if pct < BRK_PCT:
            continue
        if vma5[t - 1] is None or vol[t] < vma5[t - 1] * BRK_VR:
            continue
        nh = close[t] > max(close[j - PLAT_DAYS + 1:j + 1])
        upper = upper_line(rows, t, kd)
        cu = upper is not None and close[t] > upper and close[j] <= upper
        if nh or cu:
            out.append({'t': t, 'nh': nh, 'cu': cu, 'upper': upper})
    return out


def scan_one(stock):
    """返回 (res, rows)。res 为突破日候选列表；rows 可用于末交易日与筹码计算。"""
    rows = fetch_kline(stock['mkt'], stock['code'])
    res = scan_breakouts(rows)
    out = []
    for s in res:
        t = s['t']
        r = rows[t]
        out.append({'t': t, 'nh': s['nh'], 'cu': s['cu'], 'date': r[0],
                    'price': r[2],
                    'pct': (r[2] / rows[t - 1][2] - 1) * 100.0})
    return out, rows


def main():
    t0 = time.time()
    stocks = fetch_stock_list()
    print('[jinlin] 候选池 %d 只（剔除ST/北交所/市值>150亿）' % len(stocks), flush=True)
    if len(stocks) < 1000:
        raise RuntimeError('候选池异常偏少，疑似数据源故障，拒绝产出')

    all_last_dates = []
    fail = 0
    signals = []   # (date, rec_dict, need_profit bool)
    done = 0

    def work(st):
        return st, scan_one(st)

    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(work, st): st for st in stocks}
        for fut in as_completed(futs):
            done += 1
            try:
                st, r = fut.result()
                res, rows = r
                all_last_dates.append(rows[-1][0])
            except Exception:
                fail += 1
                continue
            if res:
                vma5 = sma([x[5] for x in rows], 5)
                for x in res:
                    t = x['t']
                    vr = rows[t][5] / vma5[t - 1] if vma5[t - 1] else None
                    types = []
                    if x['nh']:
                        types.append('新高')
                    if x['cu']:
                        types.append('上边线')
                    rec = {
                        'code': st['code'], 'name': st['name'],
                        'date': x['date'], 'type': '+'.join(types),
                        'price': round(x['price'], 2),
                        'pct': round(x['pct'], 2),
                        'vol_ratio': round(vr, 2) if vr else None,
                        'mcap_yi': round(st['mcap'] / 1e8, 1),
                    }
                    signals.append((x['date'], rec, t, rows))
            if done % 400 == 0:
                print('[jinlin] 已扫 %d/%d 失败%d' % (done, len(stocks), fail), flush=True)

    if done == 0 or fail > done * 0.4:
        raise RuntimeError('K线拉取失败率过高 (done=%d fail=%d)，拒绝产出' % (done, fail))

    # 交易日历：取出现频次最高的"末交易日"作为 asof（剔除长期停牌股干扰）
    from collections import Counter
    cnt = Counter(all_last_dates)
    asof = cnt.most_common(1)[0][0]
    # 窗口：asof 之前（含）的全部出现日期取最近 5 个
    all_dates = set()
    for d, _, _, _ in signals:
        all_dates.add(d)
    all_dates.add(asof)
    window = sorted(all_dates)[-WINDOW_DAYS:]
    window_set = set(window)

    out_signals = []
    for d, rec, t, rows in signals:
        if d not in window_set:
            continue
        win = cyq_winner(rows, t)
        rec['group'] = 'premium' if (win is not None and win >= PREMIUM_WIN) else 'watch'
        if rec['group'] == 'premium':
            rec['profit_pct'] = round(win, 1)
        out_signals.append(rec)

    days = []
    for d in sorted(window, reverse=True):
        sigs = [r for r in out_signals if r['date'] == d]
        sigs.sort(key=lambda r: (0 if r['group'] == 'premium' else 1, r['code']))
        days.append({'date': d, 'premium': [r for r in sigs if r['group'] == 'premium'],
                     'watch': [r for r in sigs if r['group'] == 'watch']})

    data = stamp({'days': days}, source='eastmoney', asof=asof)
    with open(OUT_JSON, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    n_p = sum(len(d['premium']) for d in days)
    n_w = sum(len(d['watch']) for d in days)
    print('[jinlin] 完成 asof=%s 精品%d 候选%d 用时%.0fs -> %s'
          % (asof, n_p, n_w, time.time() - t0, OUT_JSON), flush=True)


if __name__ == '__main__':
    main()
