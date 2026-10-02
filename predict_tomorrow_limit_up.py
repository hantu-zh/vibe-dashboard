# -*- coding: utf-8 -*-
"""
预测明日涨停 - 尾盘30分钟八步选股法
=====================================
核心逻辑：筛选涨幅3-5%的优质标的，预测明日涨停机会

八步法：
1. 排雷 - 过滤ST/退市/北交所/次新股
2. 尾盘选股 - 涨幅3-5%（非涨停，有上升空间）
3. 量能筛选 - 量比>1.5，换手率3-15%
4. 趋势确认 - 价格在5日/10日均线之上
5. 市值筛选 - 流通市值20-200亿
6. 主力资金 - 主力净流入为正
7. 板块共振 - 属于当日热点板块
8. 综合评分 - 多因子加权打分，TOP10输出
"""
# 2026-10-02 CI 移植版（源: D盘备份 2026-07-22 版），三处修正:
#   1. filter_by_change_range 回填 code/name（原链路只带 symbol，名称丢失）
#   2. filter_by_main_capital 改 fail-open（东财接口不可达时不再全灭为 0 只）
#   3. save_results 改存 v2 格式（原写废弃旧格式，前端读不到）
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json, re, time, urllib.request, urllib.parse, ssl
from datetime import datetime

# 读取钉钉配置
def load_dingtalk_config():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.dingtalk')
    webhook = None
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('DINGTALK_WEBHOOK='):
                    webhook = line.split('=', 1)[1].strip()
                    break
    return webhook if webhook else os.environ.get('DINGTALK_WEBHOOK')

def send_dingtalk_markdown(title, content, webhook):
    if not webhook:
        print('[钉钉] 未配置Webhook，跳过推送')
        return False
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': content  # 钉钉markdown必须包含text字段
        }
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={'Content-Type': 'application/json'}
    )
    try:
        ssl_ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=10) as resp:
            result = json.loads(resp.read().decode('utf-8'))
            if result.get('errcode') == 0:
                print(f'[钉钉] 推送成功')
                return True
            else:
                print(f'[钉钉] 推送失败: {result}')
                return False
    except Exception as e:
        print(f'[钉钉] 推送异常: {e}')
        return False

def fetch_with_retry(url, headers=None, timeout=15, retries=3, encoding='utf-8'):
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120',
        'Referer': 'https://finance.sina.com.cn/'
    }
    if headers:
        default_headers.update(headers)
    ssl_ctx = ssl.create_default_context()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=default_headers)
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=timeout) as resp:
                raw = resp.read()
                return raw.decode(encoding, errors='replace')
        except Exception as e:
            print(f'  请求失败(尝试{attempt+1}/{retries}): {e}')
            if attempt < retries - 1:
                time.sleep(2)
    return None

def get_a_stock_list():
    """Step 1: 排雷 - 获取全A股列表，排除ST/退市/北交所"""
    print('Step 1: 排雷 - 获取全A股列表...')
    all_stocks = []
    page = 1
    while True:
        url = f'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page={page}&num=80&sort=changepercent&asc=0&node=hs_a'
        raw = fetch_with_retry(url)
        if not raw:
            break
        try:
            # 处理JSONP格式: var a=[{...}]
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                data = json.loads(raw)
            if not data:
                break
            all_stocks.extend(data)
            page += 1
            if page > 50:
                break
        except Exception as e:
            print(f'  解析失败: {e}')
            break
    
    # 过滤ST/退市/北交所; 使用symbol字段(已有sh/sz/bj前缀)
    filtered = []
    for s in all_stocks:
        symbol = s.get('symbol', '')
        code = s.get('code', '')
        name = s.get('name', '')
        # 跳过ST/退市
        if re.match(r'\*(?:ST|A)$', name):
            continue
        # 跳过北交所(bj前缀)
        if symbol.startswith('bj'):
            continue
        # 跳过B股
        if symbol.startswith('shB') or symbol.startswith('szB'):
            continue
        filtered.append({'symbol': symbol, 'code': code, 'name': name})
    
    print(f'  排雷后剩余 {len(filtered)} 只股票')
    return filtered

def batch_get_quotes(stocks):
    """批量获取实时行情（使用symbol字段，分批请求避免431错误）"""
    print(f'Step 2: 尾盘选股 - 获取 {len(stocks)} 只股票行情...')
    if not stocks:
        return {}
    
    all_quotes = {}
    batch_size = 60
    stock_symbols = [s['symbol'] for s in stocks]  # symbol已经包含sh/sz前缀
    
    for i in range(0, len(stock_symbols), batch_size):
        batch = stock_symbols[i:i+batch_size]
        codes_str = ','.join(batch)
        url = f'https://hq.sinajs.cn/list={codes_str}'
        raw = fetch_with_retry(url, encoding='gbk', timeout=20)
        
        if not raw:
            continue
        
        for line in raw.split('\n'):
            m = re.search(r'hq_str_(\w+)=["\'](.*?)["\']', line)
            if not m:
                continue
            symbol = m.group(1)
            fields = m.group(2).split(',')
            if len(fields) < 10:
                continue
            try:
                price = float(fields[3])
                prev_close = float(fields[2])
                change_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                all_quotes[symbol] = {
                    'symbol': symbol,
                    'price': price,
                    'prev_close': prev_close,
                    'change_pct': change_pct,
                    'is_limit_up': abs(change_pct) >= 9.8,
                    'amplitude': float(fields[4]) if len(fields) > 4 else 0,
                    'volume': float(fields[8]) if len(fields) > 8 else 0,
                    'amount': float(fields[9]) if len(fields) > 9 else 0,
                }
            except:
                continue
        
        time.sleep(0.5)
        if (i + batch_size) % 300 == 0:
            print(f'  已处理 {min(i+batch_size, len(stock_symbols))}/{len(stock_symbols)}...')
    
    print(f'  获取到 {len(all_quotes)} 只股票行情')
    return all_quotes

def filter_by_change_range(quotes, stocks):
    """Step 2: 筛选涨幅3-5%区间"""
    print('  第二步：筛选涨幅3-5%（主板）/ 3-8%（创业板/科创板）的股票')
    # 2026-10-02: 行情 dict 只带 symbol，丢失 code/name；此处从股票列表回填，
    # 否则保存时只能存到 sh600000 这种 symbol，卡片/钉钉都显示不出名称
    meta = {st.get('symbol'): st for st in (stocks or [])}
    result = {}
    for symbol, q in quotes.items():
        if q['is_limit_up']:
            continue
        change = q['change_pct']
        # 主板3-5%，创业板/科创板3-8%
        if 3.0 <= change <= 5.0:
            result[symbol] = q
        elif (symbol.startswith('sz300') or symbol.startswith('sh300') or 
              symbol.startswith('sh688') or symbol.startswith('sz301')):
            if 3.0 <= change <= 8.0:
                result[symbol] = q
    for symbol, q in result.items():
        m = meta.get(symbol) or {}
        if not q.get('code'):
            q['code'] = m.get('code') or re.sub(r'^(sh|sz|bj)', '', symbol)
        if not q.get('name'):
            q['name'] = m.get('name') or symbol
    print(f'  涨幅区间筛选: {len(result)} 只股票符合条件')
    return result

def filter_by_volume(stocks_dict, all_quotes):
    """Step 3: 量能筛选 - 量比>1.5，换手率3-15%，成交额>1亿
    使用腾讯股票接口:
    - [38] = 换手率(%)  实际小数形式，如0.51表示0.51%
    - [43] = 量比        实际小数形式，如1.94表示1.94x
    - [37] = 成交额(万元)
    - [44] = 流通市值(亿元)
    """
    print('Step 3: 量能筛选 - 量比/换手率/成交额...')
    if not stocks_dict:
        return {}
    
    result = {}
    symbols_list = list(stocks_dict.keys())
    batch_size = 50
    
    for i in range(0, len(symbols_list), batch_size):
        batch = symbols_list[i:i+batch_size]
        qt_url = ','.join(batch)
        url = f'https://qt.gtimg.cn/q={qt_url}'
        raw = fetch_with_retry(url, encoding='gbk', timeout=15)
        
        if not raw:
            time.sleep(1)
            continue
        
        for line in raw.split('\n'):
            idx_quote = line.find('="')
            if idx_quote == -1:
                continue
            try:
                sym_start = line.find('_') + 1
                sym_end = line.find('=', sym_start)
                symbol = line[sym_start:sym_end]
                
                content_start = idx_quote + 2
                content_end = line.rfind('"')
                if content_end <= content_start:
                    continue
                content = line[content_start:content_end]
                fields = content.split('~')
                
                if len(fields) < 44 or symbol not in stocks_dict:
                    continue
                
                # 字段映射 (腾讯接口)
                vol_ratio_raw = fields[43].strip()      # 量比
                turnover_raw = fields[38].strip()       # 换手率 (%)
                amount_raw = fields[37].strip()         # 成交额(万元)
                mktcap_raw = fields[44].strip()         # 流通市值(亿元)
                
                volume_ratio = float(vol_ratio_raw) if vol_ratio_raw else 0
                turnover_rate = float(turnover_raw) if turnover_raw else 0
                turnover_yi = (float(amount_raw) / 10000) if amount_raw else 0  # 万->亿
                market_cap_yi = float(mktcap_raw) if mktcap_raw else 0
                
                if (volume_ratio >= 1.5 and 
                    3.0 <= turnover_rate <= 15.0 and 
                    turnover_yi >= 1.0):
                    q = stocks_dict[symbol]
                    q['turnover_rate'] = turnover_rate
                    q['volume_ratio'] = volume_ratio
                    q['turnover_yi'] = turnover_yi
                    q['market_cap_yi'] = market_cap_yi  # 提前获取，市值筛选复用
                    result[symbol] = q
            except Exception as e:
                continue
        
        time.sleep(0.5)
    
    print(f'  量能筛选后: {len(result)} 只')
    return result

def filter_by_trend(stocks_dict):
    """Step 4: 趋势确认 - 价格在5日/10日均线之上
    使用腾讯 ifzq K线接口
    格式: param=SYMBOL,day,,,5,qfq (5个交易日数据)
    返回: [[日期,开,收,高,低,量,...],...]
    """
    print('Step 4: 趋势确认 - 价格在5日/10日均线之上...')
    if not stocks_dict:
        return {}
    
    result = {}
    symbols_list = list(stocks_dict.keys())
    batch_size = 30
    
    for i in range(0, len(symbols_list), batch_size):
        batch = symbols_list[i:i+batch_size]
        qt_url = ','.join(batch)
        url = f'https://qt.gtimg.cn/q={qt_url}'
        raw = fetch_with_retry(url, encoding='gbk', timeout=15)
        
        if not raw:
            time.sleep(1)
            continue
        
        for line in raw.split('\n'):
            idx_quote = line.find('="')
            if idx_quote == -1:
                continue
            try:
                sym_start = line.find('_') + 1
                sym_end = line.find('=', sym_start)
                symbol = line[sym_start:sym_end]
                
                content_start = idx_quote + 2
                content_end = line.rfind('"')
                if content_end <= content_start:
                    continue
                content = line[content_start:content_end]
                fields = content.split('~')
                
                if len(fields) < 33 or symbol not in stocks_dict:
                    continue
                
                # 从分时数据获取当前价格
                current_price = float(fields[3]) if fields[3] else 0
                prev_close = float(fields[4]) if fields[4] else 0
                
                # 从5日/10日均线字段 (Sina接口已有这些字段)
                ma5_field = fields[33].strip()  # 5日均线
                ma10_field = fields[34].strip() if len(fields) > 34 else ''  # 10日均线
                
                ma5 = float(ma5_field) if ma5_field and ma5_field != '-' else 0
                ma10 = float(ma10_field) if ma10_field and ma10_field != '-' else ma5
                
                if ma5 > 0 and current_price >= ma5 * 0.95:  # 允许小幅回调
                    stocks_dict[symbol]['ma5'] = ma5
                    stocks_dict[symbol]['ma10'] = ma10
                    stocks_dict[symbol]['current_price'] = current_price
                    result[symbol] = stocks_dict[symbol]
            except Exception as e:
                continue
        
        time.sleep(0.5)
    
    print(f'  趋势确认后: {len(result)} 只')
    return result

def filter_by_market_cap(stocks_dict):
    """Step 5: 市值筛选 - 流通市值20-200亿
    复用filter_by_volume已获取的market_cap_yi字段(腾讯接口[44])
    """
    print('Step 5: 市值筛选 - 流通市值20-200亿...')
    if not stocks_dict:
        return {}
    
    result = {}
    for symbol, q in stocks_dict.items():
        mc = q.get('market_cap_yi', 0)
        if mc <= 0:
            continue
        if 20.0 <= mc <= 200.0:
            result[symbol] = q
    
    print(f'  市值筛选后: {len(result)} 只')
    return result

def filter_by_main_capital(stocks_dict):
    """Step 6: 检查主力资金净流入
    使用东方财富资金流向接口
    """
    print('Step 6: 检查主力资金净流入...')
    if not stocks_dict:
        return {}
    
    result = {}
    for symbol in list(stocks_dict.keys()):
        try:
            if symbol.startswith('sh'):
                secid = f'1.{symbol[2:]}'
            elif symbol.startswith('sz'):
                secid = f'0.{symbol[2:]}'
            else:
                continue
            
            url = (f'https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid={secid}'
                   f'&klt=101&fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56,f57')
            raw = fetch_with_retry(url, timeout=10)
            if not raw:
                continue
            
            data = json.loads(raw).get('data', {})
            klines = data.get('klines', [])
            if not klines:
                continue
            
            # 取最近1天的净流入
            last_kline = klines[-1].split(',')
            if len(last_kline) >= 5:
                main_inflow = float(last_kline[4]) if last_kline[4] else 0
                if main_inflow > 0:
                    stocks_dict[symbol]['main_inflow'] = main_inflow
                    result[symbol] = stocks_dict[symbol]
        except Exception as e:
            continue
        
        time.sleep(0.3)
    
    if not result and stocks_dict:
        # 2026-10-02: 东财 push2 在 CI 偶发不可达 → 原逻辑全灭返回 0 只，整张卡片空转。
        # 降级为「不启用主力资金过滤」，保留前序 6 步筛出的标的（main_inflow 记 0）。
        print('  [WARN] 主力资金接口无有效返回，降级为跳过该过滤（保留前序筛选结果）')
        for q in stocks_dict.values():
            q.setdefault('main_inflow', 0)
        return stocks_dict
    print(f'  主力资金筛选后: {len(result)} 只')
    return result

def get_hot_sectors():
    """Step 7: 获取热门板块
    使用东方财富行业板块接口
    """
    print('Step 7: 板块共振 - 获取热门板块...')
    url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2'
           '&fid=f3&fs=m:90+t:2&fields=f12,f14,f2,f3,f62')
    raw = fetch_with_retry(url, timeout=15)
    if not raw:
        return []
    
    try:
        data = json.loads(raw)
        items = data.get('data', {}).get('diff', [])
        sectors = []
        for item in items:
            code = item.get('f12', '')
            name = item.get('f14', '')
            change_pct = float(item.get('f3', 0) or 0)
            sectors.append({'code': code, 'name': name, 'change_pct': change_pct})
        print(f'  热门板块 TOP3: {[(s["name"], s["change_pct"]) for s in sectors[:3]]}')
        return sectors[:3]
    except Exception as e:
        print(f'  获取板块失败: {e}')
        return []

def get_stock_sector(symbol):
    """获取股票所属板块
    使用腾讯股票接口
    """
    try:
        url = f'https://qt.gtimg.cn/q={symbol}'
        raw = fetch_with_retry(url, encoding='gbk', timeout=10)
        if not raw:
            return None
        
        m = re.search(r'qt_\w+=["\'](.*?)["\']', raw)
        if not m:
            return None
        fields = m.group(1).split('~')
        if len(fields) < 48:
            return None
        
        # 腾讯接口的板块字段
        sector = fields[47] if fields[47] else ''
        return sector if sector else None
    except:
        return None

def filter_by_sector(stocks_dict, hot_sectors):
    """Step 7: 板块共振筛选"""
    print('Step 7: 板块共振 - 匹配热门板块...')
    hot_names = [s['name'] for s in hot_sectors] if hot_sectors else []
    result = {}
    for symbol in list(stocks_dict.keys()):
        sector = get_stock_sector(symbol)
        if sector and hot_names and any(hn in sector for hn in hot_names):
            stocks_dict[symbol]['sector_name'] = sector
            result[symbol] = stocks_dict[symbol]
        else:
            stocks_dict[symbol]['sector_name'] = sector or '未知'
            result[symbol] = stocks_dict[symbol]
    
    return result

def score_stocks(stocks_dict, hot_sectors):
    """Step 8: 多因子加权评分"""
    print('Step 8: 多因子加权评分...')
    hot_names = [s['name'] for s in hot_sectors] if hot_sectors else []
    scored = []
    for symbol, q in stocks_dict.items():
        change = q.get('change_pct', 0)
        turnover = q.get('turnover_rate', 0)
        vol_ratio = q.get('volume_ratio', 0)
        amount = q.get('turnover_yi', 0)
        mc = q.get('market_cap_yi', 0)
        sector = q.get('sector_name', '')
        is_hot = any(hn in sector for hn in hot_names) if hot_names else False
        
        score = (change * 2.0 +
                 turnover * 1.5 +
                 vol_ratio * 1.0 +
                 min(amount / 10, 1.0) * 3.0 +
                 (50 if is_hot else 0))
        
        # 评分理由
        reasons = []
        if change >= 3.5:
            reasons.append('涨幅适中')
        if vol_ratio >= 2.0:
            reasons.append('量比优秀')
        if turnover >= 5.0:
            reasons.append('换手活跃')
        if mc and 50 <= mc <= 100:
            reasons.append('市值适中')
        if is_hot:
            reasons.append('板块热点')
        if q.get('main_inflow', 0) > 0:
            reasons.append('主力流入')
        
        q['score'] = round(score, 1)
        q['reason'] = '/'.join(reasons[:3]) if reasons else '综合优质'
        scored.append(q)
    
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:10]

def save_results(stocks, hot_sectors):
    """保存结果到 daily_picks.json（v2 格式，key=预测明日涨停）

    2026-10-02: 原写法 `existing[today] = {'stocks': ...}` 是已废弃的旧格式，
    前端 loadDailyPicks 读的是 {date: {任务名: {time, picks}}}，因此即使跑成功卡片也读不到。
    """
    now = datetime.now().strftime('%H:%M')
    picks = []
    for s in (stocks or [])[:10]:
        code = s.get('code') or re.sub(r'^(sh|sz|bj)', '', str(s.get('symbol', '')))
        picks.append({
            'code': code,
            'name': s.get('name') or s.get('symbol', ''),
            'price': round(float(s.get('current_price') or s.get('price') or 0), 2),
            'change': round(float(s.get('change_pct') or 0), 2),
            'turnover': round(float(s.get('turnover_rate') or 0), 2),
            'vol_ratio': round(float(s.get('volume_ratio') or 0), 2),
            'market_cap': round(float(s.get('market_cap_yi') or 0), 1),
            'sector': s.get('sector_name') or '',
            'reason': s.get('reason') or '',
            'score': s.get('score', 0),
        })

    if not picks:
        print('[保存] 本次无标的，跳过写入（保留上一批数据，避免空结果覆盖）')
        return None

    try:
        from daily_picks_store import save_daily_picks
        save_daily_picks('预测明日涨停', picks, task_time=now)
        print(f'[保存] 已写入 {len(picks)} 只 → key=预测明日涨停 time={now}')
    except Exception as e:
        print(f'[保存] 写入失败: {e}')
        return None
    return picks


def build_dingtalk_message(stocks, hot_sectors):
    """构建钉钉Markdown消息"""
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    hot_md = '\n'.join([f'- **{s["name"]}** (+{s["change_pct"]:.1f}%)' 
                        for s in hot_sectors[:5]])
    
    stock_lines = []
    for i, s in enumerate(stocks[:10], 1):
        change_pct = s.get('change_pct', 0)
        turnover_r = s.get('turnover_rate', 0)
        vol_r = s.get('volume_ratio', 0)
        amt = s.get('turnover_yi', 0)
        mc = s.get('market_cap_yi', 0)
        score = s.get('score', 0)
        sector = s.get('sector_name', '未知')
        reason = s.get('reason', '')
        name = s.get('name', s.get('symbol', ''))
        code_disp = s.get('symbol', s.get('code', ''))
        
        line = (f'{i}. **{name}**({code_disp})'
                f' 涨幅:+{change_pct:.2f}% | 换手:{turnover_r:.1f}% | '
                f'量比:{vol_r:.1f}x | 成交:{amt:.1f}亿 | 市值:{mc:.0f}亿\n'
                f'   评分:{score} | 板块:{sector} | 理由:{reason}')
        stock_lines.append(line)
    
    content = (f'## 预测明日涨停 - 八步选股法\n'
               f'**日期**: {today}\n\n'
               f'### 热点板块 TOP5\n'
               f'{hot_md}\n\n'
               f'### 精选标的 ({len(stocks)}只)\n'
               + '\n\n'.join(stock_lines) +
               f'\n\n---\n'
               f'*八步法: 排雷→尾盘→量能→趋势→市值→主力→板块→评分*\n'
               f'*策略: 涨幅3-5%优质标的，预测次日涨停机会*\n'
               f'*免责: 仅供参考，不构成投资建议*')
    
    return content

def main():
    print('='*60)
    print('预测明日涨停 - 尾盘30分钟八步选股法')
    print('='*60)
    
    webhook = load_dingtalk_config()
    if not webhook:
        print('[钉钉] 未配置钉钉Webhook，跳过推送')
    else:
        print(f'[钉钉] Webhook已配置')
    
    # Step 1: 获取股票列表
    all_stocks = get_a_stock_list()
    if not all_stocks:
        print('未能获取股票列表，退出')
        return
    
    # Step 2: 获取实时行情并筛选涨幅
    all_quotes = batch_get_quotes(all_stocks)
    filtered = filter_by_change_range(all_quotes, all_stocks)
    if not filtered:
        print('涨幅区间筛选无结果，退出')
        return
    
    # Step 3: 量能筛选 (同时获取市值数据)
    filtered = filter_by_volume(filtered, all_quotes)
    
    # Step 5: 市值筛选 (复用Step3已获取的market_cap_yi)
    filtered = filter_by_market_cap(filtered)
    
    # Step 4: 趋势确认 (放到市值筛选后，减少API调用)
    filtered = filter_by_trend(filtered)
    
    # Step 6: 主力资金
    filtered = filter_by_main_capital(filtered)
    
    # Step 7: 获取热门板块 & 板块共振
    hot_sectors = get_hot_sectors()
    filtered = filter_by_sector(filtered, hot_sectors)
    
    # Step 8: 综合评分
    result_stocks = score_stocks(filtered, hot_sectors)
    
    print(f'\n最终结果: 选出 {len(result_stocks)} 只标的')
    for i, s in enumerate(result_stocks[:5], 1):
        print(f'  {i}. {s.get("name", s.get("symbol", ""))} '
              f'涨幅:{s.get("change_pct",0):.2f}% '
              f'评分:{s.get("score",0)} '
              f'理由:{s.get("reason","")}')
    
    # 保存结果
    save_results(result_stocks, hot_sectors)
    
    # 钉钉推送
    title = f'预测明日涨停 - {datetime.now().strftime("%m/%d")}八步选股'
    content = build_dingtalk_message(result_stocks, hot_sectors)
    if webhook:
        send_dingtalk_markdown(title, content, webhook)
    else:
        print('[钉钉] 跳过推送')

if __name__ == '__main__':
    main()
