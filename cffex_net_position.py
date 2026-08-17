# -*- coding: utf-8 -*-
"""
股指期货净多空数据采集脚本（适配 cffex.html 新结构）
- 数据源: akshare get_cffex_rank_table(date=YYYYMMDD, vars_list=[...])
  返回 dict[合约代码] -> DataFrame，每行 = 某会员在某合约的持仓
- 关键字段: long_open_interest / short_open_interest / long_party_name /
            short_party_name / long_open_interest_chg / short_open_interest_chg / variety
- 输出: vibe-dashboard/cffex_net_position.json
  结构(与 cffex.html 期望一致):
  {
    "2026-08-17": {
      "IF": {"long":..,"short":..,"net":..,"net_chg":..,"net_ratio":..},
      "IH": {...}, "IC": {...}, "IM": {...},
      "seats": [{"name":..,"net":..,"net_chg":..}, ...],
      "summary": "<b>...</b>..."
    }, ...
  }
- 同步: 写入本地 JSON + 注入 cffex.html 内联 + API 推送 (sync_func.push_file)
"""
import sys, os, json, re
from datetime import datetime, timedelta
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')

WS_DIR = r'C:\Users\china\.qclaw\workspace'
DASH_DIR = os.path.join(WS_DIR, 'vibe-dashboard')
JSON_FILE = os.path.join(DASH_DIR, 'cffex_net_position.json')
HTML_FILE = os.path.join(DASH_DIR, 'cffex.html')
sys.path.insert(0, DASH_DIR)

MAJOR_BROKERS = ['中信期货', '国泰君安', '海通期货', '华泰期货', '银河期货',
                 '光大期货', '南华期货', '招商期货', '广发期货', '申银万国',
                 '永安期货', '国信期货', '中信建投', '东证期货', '浙商期货']

VARIETIES = ['IF', 'IH', 'IC', 'IM']


def get_trade_dates(n=35):
    dates = []
    d = datetime.now()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    return [dt.strftime('%Y%m%d') for dt in dates]


def strip_name(n):
    return str(n).replace('(代客)', '').replace('(非期货公司)', '').strip()


def is_real_broker(name):
    """排除空值及非真实券商的聚合类别"""
    if not name:
        return False
    generic = {'None', 'nan', 'NaN', '非期货公司', '非期货公司席位', '非期货 Company'}
    return name not in generic


def aggregate_variety(df):
    """聚合单品种净持仓。返回 {variety: {long, short, net, net_ratio, seats:[{name,net}]}}"""
    result = {}
    for variety in VARIETIES:
        vdf = df[df['variety'] == variety]
        if vdf.empty:
            continue
        # 总数用原始列求和（包含全部席位，最稳健）
        total_long = float(vdf['long_open_interest'].sum())
        total_short = float(vdf['short_open_interest'].sum())
        net = total_long - total_short
        total_pos = total_long + total_short
        net_ratio = round(net / total_pos * 100, 2) if total_pos > 0 else 0
        long_by_member, short_by_member = {}, {}
        for _, row in vdf.iterrows():
            m = strip_name(row.get('long_party_name', ''))
            if is_real_broker(m):
                long_by_member[m] = long_by_member.get(m, 0) + float(row.get('long_open_interest', 0) or 0)
            m2 = strip_name(row.get('short_party_name', ''))
            if is_real_broker(m2):
                short_by_member[m2] = short_by_member.get(m2, 0) + float(row.get('short_open_interest', 0) or 0)
        all_members = set(long_by_member) | set(short_by_member)
        net_by_member = {m: long_by_member.get(m, 0) - short_by_member.get(m, 0) for m in all_members}
        top_seats = sorted(net_by_member.items(), key=lambda x: abs(x[1]), reverse=True)[:20]
        result[variety] = {
            'long': round(total_long, 2),
            'short': round(total_short, 2),
            'net': round(net, 2),
            'net_ratio': round(net_ratio, 2),
            'seats': [{'name': m, 'net': round(n, 2)} for m, n in top_seats],
        }
    return result


def fetch_cffex_for_date(date_str):
    import akshare as ak
    import warnings
    warnings.filterwarnings('ignore')
    try:
        raw = ak.get_cffex_rank_table(date=date_str, vars_list=VARIETIES)
    except Exception as e:
        print(f'    get_cffex_rank_table({date_str}) failed: {e}')
        return None
    if not raw or not isinstance(raw, dict) or not raw:
        print(f'    No data for {date_str}')
        return None
    df = pd.concat(raw.values(), ignore_index=True)
    return aggregate_variety(df)


def compute_changes(result, date_display):
    """计算相对前一交易日的净持仓变化，写入 net_chg"""
    dates = sorted([d for d in result if d.startswith('20') and d < date_display])
    if not dates:
        return
    prev = dates[-1]
    for v in VARIETIES:
        cur_net = result[date_display].get(v, {}).get('net', 0)
        prev_net = result.get(prev, {}).get(v, {}).get('net', 0)
        result[date_display][v]['net_chg'] = round(cur_net - prev_net, 2)
    # 顶层 seats 的 net_chg（主要券商跨品种合计）
    cur_seats = {s['name']: s['net'] for s in result[date_display].get('seats', [])}
    prev_seats = {s['name']: s['net'] for s in result.get(prev, {}).get('seats', [])}
    for s in result[date_display].get('seats', []):
        s['net_chg'] = round(s['net'] - prev_seats.get(s['name'], 0), 2)


def build_top_seats(result, date_display):
    """聚合主要券商跨品种净持仓，取 top 12 作为顶层 seats"""
    agg = {}
    for v in VARIETIES:
        for s in result[date_display].get(v, {}).get('seats', []):
            agg[s['name']] = agg.get(s['name'], 0) + s['net']
    # 优先主要券商，其余按 |net| 排序补齐
    ordered = [b for b in MAJOR_BROKERS if b in agg]
    others = sorted([m for m in agg if m not in MAJOR_BROKERS],
                    key=lambda x: abs(agg[x]), reverse=True)
    ordered += others
    top = [n for n in ordered[:12] if n in agg and n]
    result[date_display]['seats'] = [
        {'name': n, 'net': round(agg[n], 2)} for n in top if n in agg
    ]


def generate_summary(result, date_display):
    d = result[date_display]
    nets = {s: d.get(s, {}).get('net', 0) for s in VARIETIES}
    net_count = sum(1 for n in nets.values() if n > 0)
    if net_count == 4:
        signal = '全线净多'
    elif net_count == 0:
        signal = '全线净空'
    elif net_count >= 3:
        signal = '净多偏强'
    else:
        signal = '净空偏弱'
    heaviest = min(VARIETIES, key=lambda s: nets[s])
    most_crowded = min(VARIETIES, key=lambda s: d.get(s, {}).get('net_ratio', 0))
    parts = [f'<b>四大期指{signal}</b>。']
    parts.append(f'净空最重的是 <b>{heaviest}（{ {"IF":"沪深300","IH":"上证50","IC":"中证500","IM":"中证1000"}[heaviest] }）</b> '
                 f'<span class="neg">净空 {abs(nets[heaviest])/10000:.2f}万手</span>（净空比 {d.get(heaviest,{}).get("net_ratio",0):.1f}%）。')
    parts.append(f'若看净空比，<b>{most_crowded}（{ {"IF":"沪深300","IH":"上证50","IC":"中证500","IM":"中证1000"}[most_crowded] }）</b>相对最拥挤（{d.get(most_crowded,{}).get("net_ratio",0):.1f}%）。')
    # 变化
    prev_dates = sorted([x for x in result if x.startswith('20') and x < date_display])
    if prev_dates:
        prev = prev_dates[-1]
        chg_parts = []
        for s in VARIETIES:
            c = d.get(s, {}).get('net_chg', 0)
            if c:
                sign = '加空' if c < 0 else '减空'
                chg_parts.append(f'{s} <span class="{"neg" if c<0 else "pos"}">{sign}{abs(c)/10000:.2f}万手</span>')
        if chg_parts:
            parts.append(f'较前一交易日（{prev}）：' + '；'.join(chg_parts))
    result[date_display]['summary'] = ''.join(parts)


def main():
    print('[cffex_net_position] 启动...')
    if os.path.exists(JSON_FILE):
        with open(JSON_FILE, 'r', encoding='utf-8') as f:
            result = json.load(f)
        print(f'  读取已有数据，{len([k for k in result if k.startswith("20")])} 个日期')
    else:
        result = {}

    trade_dates = get_trade_dates(35)
    print(f'  目标交易日: {len(trade_dates)} 天')

    new_count = 0
    for td in trade_dates:
        date_display = f'{td[:4]}-{td[4:6]}-{td[6:]}'
        if date_display in result:
            continue
        print(f'  采集 {date_display}...')
        data = fetch_cffex_for_date(td)
        if data:
            result[date_display] = data
            build_top_seats(result, date_display)
            compute_changes(result, date_display)
            generate_summary(result, date_display)
            new_count += 1
            print(f'    OK: IF净={data.get("IF",{}).get("net",0):.0f}, IM净={data.get("IM",{}).get("net",0):.0f}')
        else:
            print(f'    无数据（可能非交易日）')
        if new_count >= 5:
            break

    # 清理非日期键
    for k in list(result.keys()):
        if not k.startswith('20'):
            del result[k]

    with open(JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'  保存 {JSON_FILE}，{len([k for k in result if k.startswith("20")])} 个日期，新增 {new_count} 天')

    # 注入 cffex.html 内联 + 推送
    try:
        import sync_func
        html = open(HTML_FILE, 'r', encoding='utf-8').read()
        html_new = sync_func.update_cffex_inline(html, result)
        with open(HTML_FILE, 'w', encoding='utf-8') as f:
            f.write(html_new)
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        ok1 = sync_func.push_file('cffex.html', html_new, f'sync: update cffex.html ({now})')
        ok2 = sync_func.push_file('cffex_net_position.json',
                                  json.dumps(result, ensure_ascii=False, indent=2),
                                  f'sync: update cffex data ({now})')
        print(f'  GitHub 同步: cffex.html={"✅" if ok1 else "❌"}, json={"✅" if ok2 else "❌"}')
    except Exception as e:
        print(f'  GitHub 同步失败: {e}')

    print('[cffex_net_position] 完成')


if __name__ == '__main__':
    main()
