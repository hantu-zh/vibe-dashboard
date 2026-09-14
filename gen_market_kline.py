#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_market_kline.py — 服务端预生成「全球市场 / 贵金属 / 汇率」K 线缓存
部署为 GitHub Pages 同源 JSON（market_kline.json），页面直接读取，
彻底规避浏览器端跨域被新浪 403 / 东财空响应 / 腾讯被拦截 的问题。

数据源优先级（任一成功即采用，取 >=2 根）：
  1) 腾讯 K 线 web.ifzq.gtimg.cn（usDJI/usIXIC/usINX/hkHSI）—— 最稳
  2) 新浪（带 Referer）—— 全球指数 / 贵金属 / 外汇
  3) 东方财富 K 线（服务器网络可能可达，作兜底层）
  4) Frankfurter(ECB) —— 外汇日线（CORS 开放）
  5) 台湾证交所官方 API —— 台湾加权指数

输出格式 market_kline.json：
{
  "updated": "2026-09-14T20:56:00",
  "bars": {
     "<code>": {"type":"kline"|"line","src":"...","unit":"...",
                "data":[[date,open,high,low,close,vol], ...]   # kline
                     or [[date,value], ...]                     # line
               },
     ...
  }
}
页面由 data 推导行情（价格=收盘/末值，涨跌=末-前；最高/最低=末根/区间）。

仅用标准库（urllib），无第三方依赖，可在 GitHub Actions 沙箱运行。
"""
import json, sys, os, re, io, time
from datetime import datetime, date, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'
SINA_REF = 'https://finance.sina.com.cn/'
MAX_BARS = 30
FETCH_N = 60  # 抓取更多，取最近 MAX_BARS 根

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'market_kline.json')

# ---------------- 品种配置 ----------------
# (code, name, type, unit, fx_sym_or_None, sources)
# sources: list of (kind, *args)
#   ('tencent', code, api)          api: usfqkline/get | fqkline/get
#   ('sina_us', '.DJI')
#   ('sina_gi', 'FTSE')
#   ('sina_futures', 'XAU')
#   ('sina_forex', 'USDCNY')
#   ('em', '100.DJIA')
#   ('frankfurter', 'CNY')
#   ('twse',)
CODES = [
    ('gb_dji', '道琼斯', 'kline', '', None, [('tencent', 'usDJI', 'usfqkline/get'), ('sina_us', '.DJI'), ('em', '100.DJIA')]),
    ('gb_ixic', '纳斯达克', 'kline', '', None, [('tencent', 'usIXIC', 'usfqkline/get'), ('sina_us', '.IXIC'), ('sina_us', '.NDX'), ('em', '100.NDX')]),
    ('gb_inx', '标普500', 'kline', '', None, [('tencent', 'usINX', 'usfqkline/get'), ('sina_us', '.INX'), ('em', '100.SPX')]),
    ('hf_CHA50CFD', '富时中国A50', 'kline', '', None, [('sina_gi', 'FTXIN9'), ('em', '100.XIN9'), ('em', '101.CN00Y')]),
    ('int_ftse', '英国富时100', 'kline', '', None, [('sina_gi', 'FTSE'), ('em', '100.FTSE')]),
    ('b_DAX', '德国DAX', 'kline', '', None, [('sina_gi', 'DAX'), ('em', '100.DAX')]),
    ('b_CAC', '法国CAC40', 'kline', '', None, [('sina_gi', 'CAC'), ('em', '100.CAC')]),
    ('int_nikkei', '日经225', 'kline', '', None, [('sina_gi', 'NKY'), ('em', '100.N225')]),
    ('hkHSI', '恒生指数', 'kline', '', None, [('tencent', 'hkHSI', 'fqkline/get'), ('sina_futures', 'HSI'), ('em', '100.HSI')]),
    ('b_KOSPI', '韩国KOSPI', 'kline', '', None, [('sina_gi', 'KOSPI'), ('em', '100.KS11')]),
    ('b_AS51', '澳洲ASX200', 'kline', '', None, [('sina_gi', 'AS51'), ('em', '100.AS51')]),
    ('b_SENSEX', '印度Sensex', 'kline', '', None, [('sina_gi', 'SENSEX'), ('em', '100.SENSEX')]),
    ('b_TWSE', '台湾台北指数', 'kline', '', None, [('twse',), ('em', '100.TWII')]),
    ('DINIW', '美元指数', 'kline', '', None, [('sina_forex', 'DINIW'), ('em', '100.UDI')]),
    ('hf_XAU', '现货黄金', 'kline', '美元/盎司', None, [('sina_futures', 'XAU'), ('sina_futures', 'GC'), ('em', '122.XAU'), ('em', '101.GC00Y')]),
    ('hf_XAG', '现货白银', 'kline', '美元/盎司', None, [('sina_futures', 'XAG'), ('tencent', 'usSI', 'usfqkline/get'), ('em', '122.XAG'), ('em', '101.SI00Y')]),
    ('hf_XAU_icbc', '工行黄金', 'kline', '美元/盎司', None, [('sina_futures', 'XAU'), ('em', '122.XAU')]),
    ('hf_XAG_ccb', '建行白银', 'kline', '美元/盎司', None, [('sina_futures', 'XAG'), ('em', '122.XAG')]),
    ('fx_susdcny', '美元兑人民币', 'line', '', 'CNY', [('frankfurter', 'CNY'), ('sina_forex', 'USDCNY'), ('em', '119.USDCNY')]),
    ('fx_susdjpy', '美元兑日元', 'line', '', 'JPY', [('frankfurter', 'JPY'), ('sina_forex', 'USDJPY'), ('em', '119.USDJPY')]),
    ('fx_susdeur', '美元兑欧元', 'line', '', 'EUR', [('frankfurter', 'EUR'), ('sina_forex', 'EURUSD'), ('em', '119.USDEUR')]),
    ('fx_susdgbp', '美元兑英镑', 'line', '', 'GBP', [('frankfurter', 'GBP'), ('sina_forex', 'GBPUSD'), ('em', '119.USDGBP')]),
    ('fx_susdaud', '美元兑澳元', 'line', '', 'AUD', [('frankfurter', 'AUD'), ('sina_forex', 'AUDUSD'), ('em', '119.USDAUD')]),
    ('fx_susdnzd', '美元兑纽元', 'line', '', 'NZD', [('frankfurter', 'NZD'), ('sina_forex', 'NZDUSD'), ('em', '119.USDNZD')]),
    ('fx_susdhkd', '美元兑港元', 'line', '', 'HKD', [('frankfurter', 'HKD'), ('sina_forex', 'USDHKD'), ('em', '119.USDHKD')]),
    ('fx_susdchf', '美元兑瑞郎', 'line', '', 'CHF', [('frankfurter', 'CHF'), ('sina_forex', 'USDCHF'), ('em', '119.USDCHF')]),
    ('fx_susdcad', '美元兑加元', 'line', '', 'CAD', [('frankfurter', 'CAD'), ('sina_forex', 'USDCAD'), ('em', '119.USDCAD')]),
    ('fx_susdrub', '美元兑卢布', 'line', '', 'RUB', [('frankfurter', 'RUB'), ('sina_forex', 'USDRUB'), ('em', '119.USDRUB')]),
]


def http_get(url, referer=None, timeout=45, tries=3):
    headers = {'User-Agent': UA, 'Accept': '*/*'}
    if referer:
        headers['Referer'] = referer
    last_err = None
    for i in range(tries):
        if i > 0:
            time.sleep(2.5)  # 退避，规避新浪/东财限流
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            last_err = e
            continue
    return None


def fnum(v):
    try:
        return float(v)
    except Exception:
        return None


# ---------------- 解析器（统一输出 [date, open, high, low, close, vol] 或 line [date, value]） ----------------

def parse_tencent(text, code):
    try:
        j = json.loads(text)
        node = j.get('data', {}).get(code)
        if not node:
            return None
        arr = node.get('day') or node.get('qfqday')
        if not isinstance(arr, list) or len(arr) < 2:
            return None
        out = []
        for r in arr:
            if not r or len(r) < 5:
                continue
            # [date, open, close, high, low, vol]
            o, c, h, l = fnum(r[1]), fnum(r[2]), fnum(r[3]), fnum(r[4])
            if None in (o, c, h, l):
                continue
            out.append([str(r[0]), o, h, l, c, fnum(r[5])])
        return out if len(out) >= 2 else None
    except Exception:
        return None


def _extract_array(text):
    m = re.search(r'\[.*\]', text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


def _extract_object(text):
    m = re.search(r'\{.*\}', text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except Exception:
        return None


def parse_sina_us(text):
    arr = _extract_array(text)
    if not isinstance(arr, list):
        return None
    out = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        o, h, l, c, v = fnum(it.get('o')), fnum(it.get('h')), fnum(it.get('l')), fnum(it.get('c')), fnum(it.get('v'))
        if None in (o, h, l, c):
            continue
        out.append([str(it.get('d')), o, h, l, c, v])
    return out if len(out) >= 2 else None


def parse_sina_gi(text):
    j = _extract_object(text)
    if not j:
        return None
    arr = j.get('result', {}).get('data')
    if not isinstance(arr, list):
        return None
    out = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        o, h, l, c, v = fnum(it.get('o')), fnum(it.get('h')), fnum(it.get('l')), fnum(it.get('c')), fnum(it.get('v'))
        if None in (o, h, l, c):
            continue
        out.append([str(it.get('d')), o, h, l, c, v])
    return out if len(out) >= 2 else None


def parse_sina_futures(text):
    if not text:
        return None
    arr = _extract_array(text)
    if not isinstance(arr, list):
        return None
    out = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        o, h, l, c, v = fnum(it.get('open')), fnum(it.get('high')), fnum(it.get('low')), fnum(it.get('close')), fnum(it.get('volume'))
        if None in (o, h, l, c):
            continue
        out.append([str(it.get('date')), o, h, l, c, v])
    return out if len(out) >= 2 else None


def parse_sina_forex(text):
    m = re.search(r'"([^"]+)"', text)
    if not m:
        return None
    s = m.group(1)
    out = []
    for day in s.split('|'):
        p = day.split(',')
        if len(p) < 5 or not p[0]:
            continue
        o, h, l, c = fnum(p[1]), fnum(p[2]), fnum(p[3]), fnum(p[4])
        if None in (o, h, l, c):
            continue
        out.append([p[0], o, h, l, c, None])
    return out if len(out) >= 2 else None


def parse_em(text):
    try:
        j = json.loads(text)
        klines = j.get('data', {}).get('klines')
        if not isinstance(klines, list) or not klines:
            return None
        out = []
        for line in klines:
            p = str(line).split(',')
            if len(p) < 6:
                continue
            o, c, h, l, v = fnum(p[1]), fnum(p[2]), fnum(p[3]), fnum(p[4]), fnum(p[5])
            if None in (o, c, h, l):
                continue
            out.append([p[0], o, h, l, c, v])
        return out if len(out) >= 2 else None
    except Exception:
        return None


def parse_frankfurter(text, sym):
    try:
        j = json.loads(text)
        rates = j.get('rates')
        if not isinstance(rates, dict):
            return None
        dates = sorted(rates.keys())
        out = []
        for d in dates:
            v = fnum(rates[d].get(sym))
            if v is None:
                continue
            out.append([d, v])
        return out if len(out) >= 2 else None
    except Exception:
        return None


def fetch_twse():
    now = datetime.now()
    out = []
    for mm in (now.month - 1 or 12, now.month):
        yy = now.year if mm <= now.month else now.year - 1
        ym = '%04d%02d01' % (yy, mm)
        url = 'https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date=%s&response=json' % ym
        text = http_get(url)
        if not text:
            continue
        try:
            j = json.loads(text)
        except Exception:
            continue
        if j.get('stat') != 'OK' or not isinstance(j.get('data'), list):
            continue
        for r in j['data']:
            if not r or len(r) < 5:
                continue
            p = str(r[0]).split('/')
            if len(p) != 3:
                continue
            d = '%04d-%s-%s' % (int(p[0]) + 1911, p[1], p[2])
            o, h, l, c = fnum(str(r[1]).replace(',', '')), fnum(str(r[2]).replace(',', '')), fnum(str(r[3]).replace(',', '')), fnum(str(r[4]).replace(',', ''))
            if None in (o, h, l, c):
                continue
            out.append([d, o, h, l, c, None])
    return out if len(out) >= 2 else None


# ---------------- 单源抓取 ----------------

def fetch_source(kind, args):
    if kind == 'tencent':
        code, api = args[0], args[1]
        url = 'https://web.ifzq.gtimg.cn/appstock/app/%s?param=%s,day,,,%d,qfq' % (api, code, FETCH_N)
        return parse_tencent(http_get(url), code)
    if kind == 'sina_us':
        sym = args[0]
        url = 'https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20x=/US_MinKService.getDailyK?symbol=%s&datalen=%d' % (sym, FETCH_N)
        return parse_sina_us(http_get(url, SINA_REF))
    if kind == 'sina_gi':
        sym = args[0]
        url = 'https://gi.finance.sina.com.cn/hq/daily?symbol=%s&num=%d&callback=x' % (sym, FETCH_N)
        return parse_sina_gi(http_get(url, SINA_REF))
    if kind == 'sina_futures':
        sym = args[0]
        url = 'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20x=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=%s&source=web' % sym
        return parse_sina_futures(http_get(url, SINA_REF, timeout=60, tries=3))
    if kind == 'sina_forex':
        sym = args[0]
        url = 'https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var_x=/NewForexService.getDayKLine?symbol=%s' % sym
        return parse_sina_forex(http_get(url, SINA_REF))
    if kind == 'em':
        secid = args[0]
        for host in ('https://push2his.eastmoney.com/api/qt/stock/kline/get',
                     'https://push2.eastmoney.com/api/qt/stock/kline/get'):
            url = '%s?secid=%s&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=0&end=20500101&lmt=%d' % (host, secid, FETCH_N)
            bars = parse_em(http_get(url))
            if bars:
                return bars
        return None
    if kind == 'frankfurter':
        sym = args[0]
        end = date.today()
        start = end - timedelta(days=MAX_BARS + 5)
        url = 'https://api.frankfurter.dev/v1/%s..%s?base=USD&symbols=%s' % (start.isoformat(), end.isoformat(), sym)
        return parse_frankfurter(http_get(url), sym)
    if kind == 'twse':
        return fetch_twse()
    return None


def main():
    bars = {}
    for code, name, typ, unit, fxsym, sources in CODES:
        time.sleep(0.35)  # 限流：每支品种之间留间隔
        chosen = None
        chosen_src = None
        for spec in sources:
            kind = spec[0]
            try:
                res = fetch_source(kind, spec[1:])
            except Exception as e:
                res = None
            if res and len(res) >= 2:
                chosen = res[-MAX_BARS:]
                chosen_src = kind
                break
        if chosen:
            bars[code] = {'type': typ, 'src': chosen_src, 'unit': unit, 'data': chosen}
            print('  OK  %-14s %-12s src=%-12s bars=%d' % (code, name, chosen_src, len(chosen)))
        else:
            print('  --  %-14s %-12s 无数据' % (code, name))

    out = {
        'updated': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'bars': bars,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('\n写入 %s  共 %d/%d 个品种有数据' % (OUT, len(bars), len(CODES)))


if __name__ == '__main__':
    main()
