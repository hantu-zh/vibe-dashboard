# -*- coding: utf-8 -*-
"""
# ─── 路径兼容：Windows 本地 / GitHub Actions Linux 自动适配 ───
import sys as _s, os as _o
_s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
from paths import WS, VIBE_DIR, DAILY_PICKS, AI_ANALYSIS_DATA, AI_ANALYSIS_REPORT  # noqa: E402
高欣-唯科科技形态选股 v2.0 稳定版
- 从 TDX 本地数据读取日线
- 筛选：箱体突破 + 均线多头 + 20日新高
- 输出 TOP10 到 breakout_stocks.txt 并写入 daily_picks.json
"""
import os, struct, sys, json, datetime, time, re, ssl, urllib.request
from datetime import datetime as dt

# ── 配置（路径自动适配：BASE_DIR=脚本所在目录，本地/CI 通用）─────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
TDX_PATH    = r'E:\开心果【专业版+MPV】\vipdoc\sh\lday'   # 仅本地 Windows 用；CI 上不存在则走网络行情
RESULT_FILE = os.path.join(BASE_DIR, 'breakout_stocks.txt')
LOG_FILE    = os.path.join(BASE_DIR, 'logs', 'scan_breakout.log')
MAX_STOCKS  = 5000
NAMES_FILE  = os.path.join(BASE_DIR, 'stock_names.json')
# ────────────────────────────────────────────────────────────────────────────────

def log(msg, level='INFO'):
    ts = dt.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [{level}] {msg}'
    try:
        print(line, flush=True)
    except:
        pass
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except:
        pass

def load_stock_names():
    if not os.path.exists(NAMES_FILE):
        return {}
    try:
        with open(NAMES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # stock_names.json 结构: {"_meta":..., "names":{"000001":"平安银行",...}}
        if 'names' in data and isinstance(data['names'], dict):
            return data['names']
        return data
    except:
        return {}

def is_valid_stock(code):
    """
    验证是否为有效的A股股票代码（排除基金/指数/债券）
    沪市股票：600000-605999
    深市股票：000000-002999, 300000-301999
    """
    if not code or len(code) != 6:
        return False
    
    # 沪市股票（60开头）
    if code.startswith('60'):
        return True
    
    # 深市主板（000/001/002/003开头）
    if code.startswith(('000', '001', '002', '003')):
        return True
    
    # 深市创业板（300/301开头）
    if code.startswith(('300', '301')):
        return True
    
    # 排除所有其他代码（基金/指数/债券等）
    return False

def read_tdx_day_file(filepath, max_days=60):
    """读取 TDX .day 二进制日线文件，返回 list of (date, open, high, low, close, volume)
    TDX日线格式：每32字节一条记录
    - 日期：uint (YYYYMMDD)
    - 开高低收：int (实际价格 * 100)
    - 成交量：uint (手)
    - 成交额：uint 或 float
    """
    results = []
    try:
        with open(filepath, 'rb') as f:
            buf = f.read()
        rec_size = 32
        n = len(buf) // rec_size
        if n == 0:
            return results
        for i in range(max(0, n - max_days), n):
            chunk = buf[i*rec_size:(i+1)*rec_size]
            date_int = struct.unpack('I', chunk[0:4])[0]
            date_str = f'{date_int//10000:04d}-{(date_int//100)%100:02d}-{date_int%100:02d}'
            # TDX价格字段是 int 类型，实际价格 = 值 / 100
            open_p  = struct.unpack('i', chunk[4:8])[0] / 100.0
            high_p  = struct.unpack('i', chunk[8:12])[0] / 100.0
            low_p   = struct.unpack('i', chunk[12:16])[0] / 100.0
            close_p = struct.unpack('i', chunk[16:20])[0] / 100.0
            volume  = struct.unpack('I', chunk[20:24])[0]  # 成交量（手）
            results.append((date_str, open_p, high_p, low_p, close_p, volume))
    except Exception as e:
        pass
    return results

def check_breakout(data, box_days=10):
    """
    箱体突破判断：
    - 最近 box_days 天在一个窄幅箱体震荡
    - 最新一天收盘价突破箱体上沿（最高价）
    - 放量（今日量 > 昨日量 * 1.2）
    返回 (是否突破, 评分, 特征列表)
    """
    if len(data) < box_days + 5:
        return False, 0, []
    recent = data[-box_days-1:]
    highs = [d[2] for d in recent[:-1]]
    lows  = [d[3] for d in recent[:-1]]
    box_high = max(highs)
    box_low  = min(lows)
    box_range = (box_high - box_low) / box_low if box_low > 0 else 999
    last = recent[-1]
    prev = recent[-2]
    features = []
    score = 0
    # 1. 突破箱体
    if last[4] > box_high * 1.00:
        features.append('突破箱体')
        score += 30
    else:
        return False, 0, []
    # 2. 涨幅 > 3%
    pct = (last[4] - prev[4]) / prev[4] * 100 if prev[4] > 0 else 0
    features.append(f'涨幅{pct:.1f}%')
    if pct > 3:
        score += 20
    # 3. 均线多头（简单判断：收盘价 > 5日均价）
    if len(data) >= 5:
        ma5 = sum(d[4] for d in data[-5:]) / 5
        if last[4] > ma5:
            features.append('均线多头')
            score += 20
    # 4. 20日新高
    if len(data) >= 20:
        max20 = max(d[4] for d in data[-20:])
        if last[4] >= max20 * 0.998:
            features.append('20日新高')
            score += 20
    # 5. 放量
    if last[5] > prev[5] * 1.2:
        features.append('放量')
        score += 10
    return True, score, features

# ═══════════════════════════════════════════════════════════════
# 云端兜底：网络行情（无本地通达信时由 GitHub Actions 使用）
# ═══════════════════════════════════════════════════════════════
def _net_ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def read_network_kline(code6, market, max_days=60):
    """云端兜底：新浪日K线，返回与 TDX 同构的 [(date, open, high, low, close, volume)] 列表"""
    try:
        sym = f"{market}{code6}"
        url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={max_days}")
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/"
        })
        with urllib.request.urlopen(req, context=_net_ctx(), timeout=20) as r:
            raw = r.read().decode("gbk", "replace")
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        out = []
        for d in data:
            try:
                out.append((
                    d.get("day", ""),
                    float(d.get("open", 0)),
                    float(d.get("high", 0)),
                    float(d.get("low", 0)),
                    float(d.get("close", 0)),
                    float(d.get("volume", 0)),
                ))
            except (TypeError, ValueError):
                continue
        return out[-max_days:]
    except Exception as e:
        log(f"网络K线获取失败 {code6}: {e}")
        return []


def lookup_stock_name_net(code6):
    """云端按代码查股票名称（新浪实时行情）"""
    try:
        market = 'sh' if code6.startswith('6') else 'sz'
        sym = f"{market}{code6}"
        url = f"https://hq.sinajs.cn/list={sym}"
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, context=_net_ctx(), timeout=8) as r:
            raw = r.read().decode("gbk", "replace")
        if '=""' in raw:
            return ""
        parts = raw.split('="')[1].split('"')[0].split(",")
        return parts[0] if parts else ""
    except Exception:
        return ""


def get_candidate_codes(limit=400):
    """云端候选池：新浪涨幅榜（按涨幅排序）前 N 只有效A股代码"""
    codes = []
    try:
        url_tpl = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                   "Market_Center.getHQNodeData?page={page}&num={num}&sort=changepercent"
                   "&asc=0&node=hs_a")
        headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
        for page in range(1, 5):
            url = url_tpl.format(page=page, num=100)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15, context=_net_ctx()) as r:
                raw = r.read().decode("gbk", "replace")
            data = json.loads(raw)
            if not data:
                break
            for item in data:
                code = str(item.get("code", "")).zfill(6)
                if code and is_valid_stock(code):
                    codes.append(code)
            if len(codes) >= limit:
                break
    except Exception as e:
        log(f"涨幅榜获取失败: {e}")
    return codes[:limit]


def scan_network(names):
    """云端模式：对候选池逐只拉K线做箱体突破判断"""
    log('进入云端扫描模式（无本地TDX，使用网络行情）')
    candidates = get_candidate_codes(limit=400)
    log(f'候选池 {len(candidates)} 只')
    results = []
    scanned = 0
    for code6 in candidates:
        scanned += 1
        if scanned % 100 == 0:
            log(f'云端已扫描 {scanned}/{len(candidates)}...')
        try:
            market = 'sh' if code6.startswith('6') else 'sz'
            data = read_network_kline(code6, market, max_days=60)
            if len(data) < 30:
                continue
            ok, score, feats = check_breakout(data, box_days=10)
            if not ok:
                continue
            # 排除非A股/风险代码
            if code6.startswith(('200', '900', '204', '508', '87', '83', '430', '830')):
                continue
            display_code = ('SH' if code6.startswith('6') else 'SZ') + code6
            name = names.get(code6, names.get(display_code, '')) or lookup_stock_name_net(code6)
            close = data[-1][4]
            prev = data[-2][4] if len(data) >= 2 else 0
            pct = (close - prev) / prev * 100 if prev > 0 else 0
            results.append({
                'code': display_code,
                'name': name,
                'price': round(close, 2),
                'pct': round(pct, 1),
                'score': score,
                'features': feats,
            })
        except Exception:
            pass
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:10]


def scan_stocks():
    names = load_stock_names()
    # 本地 TDX 优先（Windows 全市场扫描）
    vipdoc = os.path.dirname(os.path.dirname(TDX_PATH))  # 从 sh/lday 向上两级到 vipdoc
    sh_path = os.path.join(vipdoc, 'sh', 'lday')
    sz_path = os.path.join(vipdoc, 'sz', 'lday')
    if os.path.exists(sh_path) and os.path.exists(sz_path):
        log(f'本地 TDX 可用，全市场扫描: {vipdoc}')
        log(f'沪市日线: {sh_path}')
        log(f'深市日线: {sz_path}')
        files_to_scan = []
        for p in [sh_path, sz_path]:
            all_files = os.listdir(p)
            day_files = [f for f in all_files if f.endswith('.day') and (len(f) == 10 or len(f) == 12)]
            log(f'{p}: {len(day_files)} 个 .day 文件')
            for fn in day_files:
                files_to_scan.append(os.path.join(p, fn))
        log(f'总共 {len(files_to_scan)} 个股票文件待扫描')
        results = []
        scanned = 0
        for fp in files_to_scan[:MAX_STOCKS]:
            scanned += 1
            if scanned % 500 == 0:
                log(f'已扫描 {scanned}/{len(files_to_scan[:MAX_STOCKS])}...')
            try:
                data = read_tdx_day_file(fp, max_days=60)
                if len(data) < 30:
                    continue
                ok, score, feats = check_breakout(data, box_days=10)
                if ok:
                    code = os.path.basename(fp).replace('.day', '')
                    # 处理 sh600000 或 600000 两种格式
                    if code.startswith('sh') or code.startswith('sz'):
                        code = code[2:]  # 去掉 sh/sz 前缀

                    # ✅ 验证是否为有效的A股股票代码
                    if not is_valid_stock(code):
                        continue

                    # 排除B股(200/900开头)和债券(SZ204/SZ508)
                    if code.startswith('200') or code.startswith('900'):
                        continue
                    if code.startswith('204') or code.startswith('508'):
                        continue
                    # 过滤新三板/北交所
                    if code.startswith(('87', '83', '430', '830')):
                        continue
                    # 区分沪市/深市
                    if code.startswith('6'):
                        display_code = 'SH' + code
                    else:
                        display_code = 'SZ' + code
                    name = names.get(code, names.get(display_code, ''))
                    close = data[-1][4]
                    pct = (data[-1][4] - data[-2][4]) / data[-2][4] * 100 if len(data) >= 2 and data[-2][4] > 0 else 0
                    results.append({
                        'code': display_code,
                        'name': name,
                        'price': round(close, 2),
                        'pct': round(pct, 1),
                        'score': score,
                        'features': feats,
                    })
            except Exception as e:
                pass
        log(f'扫描完成，找到 {len(results)} 只突破形态股票')
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:10]
    else:
        # 云端：无本地 TDX，走网络行情候选扫描
        return scan_network(names)

def save_results(breaks):
    os.makedirs(os.path.dirname(RESULT_FILE), exist_ok=True)
    with open(RESULT_FILE, 'w', encoding='utf-8') as f:
        f.write('=' * 60 + '\n')
        f.write('高欣-唯科科技形态选股\n')
        f.write('=' * 60 + '\n')
        f.write(f'扫描时间: {dt.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        f.write(f'共找到: {len(breaks)} 只股票\n\n')
        f.write(f'{"代码":<12}{"名称":<12}{"现价":>8}{"涨幅":>8}  {"评分"}  特征\n')
        f.write('-' * 60 + '\n')
        for s in breaks:
            feat_str = ', '.join(s['features'])
            f.write(f"{s['code']:<12}{s['name']:<12}{s['price']:>8.2f}{s['pct']:>7.1f}%  {s['score']:>3}  {feat_str}\n")
    log(f'结果已保存到: {RESULT_FILE}')

def save_to_daily_picks(breaks):
    """写入仓库根 daily_picks.json（高欣唯科科技形态）—— CI 上由 git add -A 推送上线"""
    picks = []
    for s in breaks:
        picks.append({
            'code': s['code'],
            'name': s['name'],
            'price': s['price'],
            'change': s['pct'],
            'score': s['score'],
            'reason': '箱体突破+' + '+'.join(s['features']),
        })
    today = dt.now().strftime('%Y-%m-%d')
    entry = {
        'count': len(picks),
        'time': dt.now().strftime('%H:%M'),
        'picks': picks,
    }
    dp_path = os.path.join(BASE_DIR, 'daily_picks.json')
    try:
        if os.path.exists(dp_path):
            with open(dp_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {}
        if today not in data:
            data[today] = {}
        data[today]['高欣唯科科技形态'] = entry
        with open(dp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f'已写入 {dp_path} [{today}][高欣唯科科技形态]: {len(picks)} 只')
    except Exception as e:
        log(f'写入 {dp_path} 失败: {e}', 'ERROR')

def main():
    log('===== 高欣唯科科技形态选股开始 =====')
    try:
        breaks = scan_stocks()
        if not breaks:
            log('未找到符合条件的股票')
        else:
            save_results(breaks)
            save_to_daily_picks(breaks)
            log(f'完成！共找到 {len(breaks)} 只')
    except Exception as e:
        log(f'运行出错: {e}', 'ERROR')
        import traceback
        traceback.print_exc()
    log('===== 结束 =====')

# ── 直接执行 ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    main()
