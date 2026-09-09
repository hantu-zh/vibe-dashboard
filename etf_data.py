# -*- coding: utf-8 -*-
"""ETF 雷达数据源模块

供 etf_sync.py 调用：
    etf_list = etf_data.fetch_etf_data()          # -> list[dict] 或 []
    etf_data.save_etf_data(etf_list, local_json)  # -> bool

设计原则（迁移到 GitHub Actions 后必须遵守）：
  * 去掉原 Qclaw 云端 Windows 硬编码路径
  * 数据源只用海外 runner 可达的：腾讯 qt.gtimg.cn（主）/ 新浪 hq.sinajs.cn（备）
    东方财富 push2 在海外稳定 RemoteDisconnected，已弃用
  * 抓不到任何真实行情时返回 []，由调用方跳过写入 —— 绝不写 mock/假数据
"""
import json
import os
import ssl
import time
import urllib.request
from datetime import datetime

# ──────────────────────────────────────────────────────────────
# ETF 池（沿用原有 47 只清单：代码 / 全称 / 名称 / 分类 / 星级）
# ──────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────
# ETF 池（沿用原有 47 只清单：代码 / 全称 / 名称 / 大类 / 二级行业 / 星级）
#   大类 = 宽基 / 行业 / 主题（页面一级 Tab）
#   二级行业 = 科技 / 金融 / 医药 / 消费 / 新能源 / 军工 / 周期 / 其他（行业、主题下的子 Tab）
#   宽基不做二级细分，sub 置空
# ──────────────────────────────────────────────────────────────
ETF_POOL = [
    ("000001", "sh000001", "上证指数",        "宽基", "",     4),
    ("510300", "sh510300", "沪深300ETF",     "宽基", "",     4),
    ("510500", "sh510500", "中证500ETF",     "宽基", "",     4),
    ("510050", "sh510050", "上证50ETF",      "宽基", "",     4),
    ("512100", "sh512100", "中证1000ETF",    "宽基", "",     4),
    ("159919", "sz159919", "沪深300ETF(深)", "宽基", "",     4),
    ("159915", "sz159915", "创业板ETF",      "宽基", "",     4),
    ("159901", "sz159901", "深证100ETF",     "宽基", "",     4),
    ("510880", "sh510880", "红利ETF",        "宽基", "",     4),
    ("588000", "sh588000", "科创50ETF",      "宽基", "",     4),
    ("562500", "sh562500", "中证1000ETF(沪)","宽基", "",     4),
    ("159949", "sz159949", "创业板50ETF",    "宽基", "",     4),
    ("512480", "sh512480", "半导体ETF",      "行业", "科技",   4),
    ("512760", "sh512760", "芯片ETF",        "行业", "科技",   4),
    ("512660", "sh512660", "军工ETF",        "行业", "军工",   4),
    ("512800", "sh512800", "银行ETF",        "行业", "金融",   4),
    ("512070", "sh512070", "非银ETF",        "行业", "金融",   4),
    ("512200", "sh512200", "房地产ETF",      "行业", "周期",   4),
    ("512010", "sh512010", "医药ETF",        "行业", "医药",   3),
    ("512170", "sh512170", "医疗ETF",        "行业", "医药",   4),
    ("512290", "sh512290", "生物医药ETF",    "行业", "医药",   3),
    ("512690", "sh512690", "白酒ETF",        "行业", "消费",   4),
    ("515170", "sh515170", "食品饮料ETF",    "行业", "消费",   4),
    ("512360", "sh512360", "计算机ETF",      "行业", "科技",   1),
    ("515030", "sh515030", "新能源车ETF",    "行业", "新能源", 4),
    ("516160", "sh516160", "新能源ETF",      "行业", "新能源", 4),
    ("515790", "sh515790", "光伏ETF",        "行业", "新能源", 4),
    ("515050", "sh515050", "5GETF",          "行业", "科技",   4),
    ("512400", "sh512400", "有色金属ETF",    "行业", "周期",   4),
    ("512980", "sh512980", "传媒ETF",        "行业", "其他",   4),
    ("516950", "sh516950", "基建ETF",        "行业", "周期",   3),
    ("515880", "sh515880", "通信ETF",        "行业", "科技",   4),
    ("516150", "sh516150", "稀土ETF",        "行业", "周期",   4),
    ("515710", "sh515710", "光伏产业ETF",    "行业", "新能源", 3),
    ("159903", "sz159903", "深证成指ETF",    "主题", "其他",   4),
    ("512510", "sh512510", "中证高铁ETF",    "主题", "周期",   3),
    ("515060", "sh515060", "国证芯片ETF",    "主题", "科技",   3),
    ("159992", "sz159992", "创新药ETF",      "主题", "医药",   3),
    ("516110", "sh516110", "汽车ETF",        "主题", "消费",   4),
    ("515950", "sh515950", "半导体材料ETF",  "主题", "科技",   2),
    ("159995", "sz159995", "芯片ETF(深)",    "主题", "科技",   4),
    ("512970", "sh512970", "证券ETF",        "主题", "金融",   1),
    ("515080", "sh515080", "新能车产业ETF",  "主题", "新能源", 4),
    ("516390", "sh516390", "储能ETF",        "主题", "新能源", 1),
    ("515220", "sh515220", "煤炭ETF",        "主题", "周期",   4),
    ("512560", "sh512560", "军工龙头ETF",    "主题", "军工",   4),
    ("159825", "sz159825", "农业ETF",        "主题", "消费",   4),
]

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://gu.qq.com/',
    'Accept': '*/*',
}


def _http_get(url, headers, timeout=15, encoding='utf-8'):
    req = urllib.request.Request(url, headers=headers)
    resp = urllib.request.urlopen(req, timeout=timeout, context=_CTX)
    raw = resp.read()
    if resp.headers.get('Content-Encoding') == 'gzip':
        import gzip
        raw = gzip.decompress(raw)
    return raw.decode(encoding, errors='ignore')


def _f(x, default=0.0):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return default


# ──────────────────────────────────────────────────────────────
# 主源：腾讯 qt.gtimg.cn（支持一次批量查多个代码）
# ──────────────────────────────────────────────────────────────
def _fetch_tencent(full_codes):
    """返回 {full_code: {price, change_pct, volume, amount}}"""
    out = {}
    batch = 20
    for i in range(0, len(full_codes), batch):
        chunk = full_codes[i:i + batch]
        url = 'https://qt.gtimg.cn/q=' + ','.join(chunk)
        try:
            text = _http_get(url, _HEADERS, timeout=15, encoding='gbk')
        except Exception as e:
            print(f'  [etf_data] 腾讯源批次请求失败: {type(e).__name__}: {e}')
            continue
        for line in text.strip().split('\n'):
            if '=' not in line:
                continue
            key, val = line.split('=', 1)
            fc = key.replace('v_', '').strip()
            v = val.strip().rstrip(';').strip('"')
            p = v.split('~')
            if len(p) < 38:
                continue
            price = _f(p[3])
            prev = _f(p[4])
            if price <= 0 or prev <= 0:
                continue
            change_pct = _f(p[32])
            # p[35] = "价格/成交量(手)/成交额(元)"
            amount_yuan = 0.0
            if '/' in p[35]:
                amount_yuan = _f(p[35].split('/')[2])
            amount_wan = _f(p[37])  # 成交额（万元），与历史数据口径一致
            if amount_wan == 0 and amount_yuan:
                amount_wan = amount_yuan / 1e4
            out[fc] = {
                'price': round(price, 4),
                'change_pct': round(change_pct, 2),
                'volume': _f(p[6]),        # 成交量（手）
                'amount': round(amount_wan, 1),  # 成交额（万元）
            }
        time.sleep(0.3)
    return out


# ──────────────────────────────────────────────────────────────
# 备用源：新浪 hq.sinajs.cn
# ──────────────────────────────────────────────────────────────
def _fetch_sina(full_codes):
    out = {}
    headers = dict(_HEADERS)
    headers['Referer'] = 'https://finance.sina.com.cn'
    batch = 20
    for i in range(0, len(full_codes), batch):
        chunk = full_codes[i:i + batch]
        url = 'https://hq.sinajs.cn/list=' + ','.join(chunk)
        try:
            text = _http_get(url, headers, timeout=15, encoding='gbk')
        except Exception as e:
            print(f'  [etf_data] 新浪源批次请求失败: {type(e).__name__}: {e}')
            continue
        for line in text.strip().split('\n'):
            if '="' not in line:
                continue
            key, val = line.split('="', 1)
            fc = key.replace('var hq_str_', '').strip()
            p = val.rstrip('";').split(',')
            if len(p) < 10:
                continue
            # 指数/ETF 通用: [0]名称 [1]今开 [2]昨收 [3]当前价 [4]最高 [5]最低
            #               [8]成交量 [9]成交额
            price = _f(p[3])
            prev = _f(p[2])
            if price <= 0 or prev <= 0:
                continue
            vol = _f(p[8])
            amt = _f(p[9])
            # 新浪 ETF 成交量为股，历史口径为手
            if fc.startswith(('sh51', 'sz15', 'sh58', 'sh56')):
                vol = vol / 100.0
                amt = amt / 1e4  # 元 -> 万元
            out[fc] = {
                'price': round(price, 4),
                'change_pct': round((price - prev) / prev * 100, 2),
                'volume': round(vol, 1),
                'amount': round(amt, 1),
            }
        time.sleep(0.3)
    return out


def fetch_etf_data():
    """抓取全部 ETF 池的实时行情。

    返回 list[dict]；若所有源均不可用则返回 []（调用方应跳过写入，
    避免把假数据推上 GitHub Pages）。
    """
    full_codes = [c[1] for c in ETF_POOL]
    quotes = {}
    for attempt in range(3):
        quotes = _fetch_tencent(full_codes)
        if quotes:
            print(f'  [etf_data] 腾讯源: {len(quotes)}/{len(full_codes)}')
            break
        print(f'  [etf_data] 腾讯源尝试 {attempt + 1}/3 失败，退避重试')
        time.sleep(2 + attempt * 2)
    if not quotes:
        quotes = _fetch_sina(full_codes)
        if quotes:
            print(f'  [etf_data] 新浪备用源: {len(quotes)}/{len(full_codes)}')

    if not quotes:
        print('  [etf_data] ❌ 所有数据源均不可用，返回空（不写文件）')
        return []

    # 覆盖率过低视为异常，同样不写（防止半截数据污染页面）
    coverage = len(quotes) / len(full_codes)
    if coverage < 0.5:
        print(f'  [etf_data] ❌ 覆盖率仅 {coverage:.0%}，判定为异常响应，返回空')
        return []

    result = []
    for code, full_code, name, category, sub_category, stars in ETF_POOL:
        q = quotes.get(full_code)
        if not q:
            continue
        item = {
            'code': code,
            'full_code': full_code,
            'name': name,
            'price': q['price'],
            'change_pct': q['change_pct'],
            'volume': q['volume'],
            'amount': q['amount'],
            'category': category,
        }
        if sub_category:
            item['sub_category'] = sub_category
        item['stars'] = stars
        result.append(item)
    print(f'  [etf_data] ✅ 有效 ETF: {len(result)} 只')
    return result


def save_etf_data(etf_list, path):
    """保存为 {generated_at, count, etfs}，格式与 Dashboard 读取端一致。"""
    if not etf_list:
        print('  [etf_data] 无数据，跳过保存')
        return False
    payload = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'count': len(etf_list),
        'etfs': etf_list,
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f'  [etf_data] 已保存 {path}（{len(etf_list)} 只）')
    return True


if __name__ == '__main__':
    data = fetch_etf_data()
    if data:
        for e in data[:5]:
            print(f"  {e['name']:14s} {e['price']:>9.3f}  {e['change_pct']:+.2f}%  "
                  f"额{e['amount']/1e4:.1f}亿")
