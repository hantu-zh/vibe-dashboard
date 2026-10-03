# -*- coding: utf-8 -*-
"""
vibe-dashboard 选股策略回测引擎 v1（修正版）
===========================================
对比 daily_picks.json 中各策略在真实历史行情下的表现。

相比旧版回溯（jack_learning / popeye_training / jack_weekend_trainer）修正的缺陷：
 1) 固定退出窗口 + 风控：主配置最长持有 T+10 交易日，触达 +8% 止盈 / -5% 止损即退出
    （宽退出带，避免被窄带"削平"真实盈亏分布；对照仍保留 T+5/+5%/-3% 窄带）；
    不再用"今天随便某天的实时价"当退出（旧版持有期混乱）。
 2) 收盘对收盘：入口价优先用推荐价，缺失则用信号日收盘；不再混用盘中实时价。
 3) 扣除交易成本：双边约 0.22%（佣金+印花税+滑点），短周期下差异显著。
 4) 缺失行情 / 无代码的记录单独计入 skipped，不计入分母（旧版会把抓不到价的票
    静默算成"亏损"，系统性压低胜率）。
 5) 输出胜率、平均盈/亏、盈亏比(profit factor)、期望值、最大回撤、按月拆分，
    并标注样本量（旧版无显著性保护）。

注：威震天 / 盗火线 / 益盟强买 的历史日期信号不在 daily_picks.json（仅当前快照），
本引擎聚焦该文件内"有历史推荐"的策略；新闻类策略(xueqiu_7x24/市场深度解读)已过滤。
"""
import json, os, ssl, urllib.request, time
from collections import defaultdict

# BUGFIX(2026-10-03): 原先写死本地 Windows 路径，CI runner 上必然 FileNotFoundError（backtest 每周定时跑挂）。
# 改为「脚本所在目录 = 仓库根」，本地与 runner 通用；需要指到别处时用 BACKTEST_REPO 环境变量覆盖。
REPO = os.environ.get("BACKTEST_REPO") or os.path.dirname(os.path.abspath(__file__))
OUT  = REPO   # 报告/K线缓存生成在仓库根，Linux runner 也能 git add 到
PICKS = os.path.join(REPO, "daily_picks.json")
CACHE = os.path.join(OUT, "kline_cache_backtest.json")
REPORT_JSON = os.path.join(OUT, "backtest_report.json")
REPORT_HTML = os.path.join(OUT, "backtest_report.html")

ROUND_TRIP_COST = 0.0022   # 双边成本（佣金~0.03%*2 + 卖出印花税0.1% + 滑点~0.03%*2）
# 主配置（放宽退出带）：止盈 +8% / 止损 -5% / 最长持有 T+10，避免被窄带"削平"真实盈亏分布
TARGET = 0.08
STOP   = -0.05
MAX_HOLD = 10
# 对照配置（旧窄带）：止盈 +5% / 止损 -3% / T+5
NARROW = (0.05, -0.03, 5)
OFFLINE = os.environ.get('OFFLINE', 'False') == 'True'   # 默认联网补齐；OFFLINE=True 仅用缓存离线出报告
FETCH_DISABLED = False     # 熔断：连续抓取失败过多时自动停止继续请求
FETCH_GAP = 0.5             # 每次成功抓取后的礼貌间隔(秒)，避免触发突发限流

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def log(*a):
    print(*a, flush=True)


# ------------------------- K线获取（带缓存） -------------------------
def sym_of(code):
    c = str(code).strip()
    digits = ''.join(ch for ch in c if ch.isdigit())
    if len(digits) < 6:
        return None
    c = digits[-6:]
    if c[0] in '69':    # 沪市（含科创板9）
        return 'sh' + c
    if c[0] in '5':     # 沪市基金/指数
        return 'sh' + c
    if c[0] in '48':    # 北交所
        return 'bj' + c
    if c[0] in '023':   # 深市（主板0/2、创业板3）
        return 'sz' + c
    return 'sh' + c


def em_secid(code):
    c = str(code).strip()
    digits = ''.join(ch for ch in c if ch.isdigit())
    if len(digits) < 6:
        return None
    c = digits[-6:]
    if c[0] in '69':    # 沪市
        return '1.' + c
    if c[0] in '5':     # 沪市基金/指数
        return '1.' + c
    if c[0] in '48':    # 北交所走深通道(可能无数据)
        return '0.' + c
    return '0.' + c     # 深市


def fetch_kline_tencent(sym):
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,2026-01-01,2026-12-31,600,qfq" % sym
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.qq.com", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            j = json.loads(r.read().decode('utf-8-sig'))
        node = j.get('data', {}).get(sym)
        if not node:
            return None
        key = 'qfqday' if 'qfqday' in node else ('day' if 'day' in node else None)
        if not key:
            return None
        out = []
        for rw in node[key]:
            try:
                # 腾讯日K顺序: [日期, 开, 收, 高, 低, 量]
                out.append([rw[0], float(rw[1]), float(rw[2]), float(rw[3]), float(rw[4])])
            except Exception:
                pass
        return out if out else None
    except Exception:
        return None


def fetch_kline_em(secid):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&fields1=f1&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1"
           "&beg=20260101&end=20261231") % secid
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://quote.eastmoney.com/", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
            j = json.loads(r.read().decode('utf-8-sig'))
        kl = j.get('data', {}).get('klines')
        if not kl:
            return None
        out = []
        for s in kl:
            p = s.split(',')
            try:
                # 东财顺序: date,open,close,high,low,volume
                out.append([p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4])])
            except Exception:
                pass
        return out if out else None
    except Exception:
        return None


def fetch_kline_sina(sym):
    # 新浪历史日线（未复权，与推荐价市价口径一致）；不同IP段，腾讯/东财被封时可用
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=%s&scale=240&ma=no&datalen=220" % sym
    try:
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            d = json.loads(r.read().decode('utf-8-sig'))
        if not isinstance(d, list) or not d:
            return None
        out = []
        for it in d:
            try:
                # 新浪字段: day,open,high,low,close,volume（均为字符串）
                out.append([it['day'], float(it['open']), float(it['close']),
                            float(it['high']), float(it['low'])])
            except Exception:
                pass
        return out if out else None
    except Exception:
        return None


def fetch_kline(sym):
    # 腾讯(前复权) → 东财(前复权) → 新浪(未复权)；各源独立局部熔断
    if _src_ok('tencent'):
        k = fetch_kline_tencent(sym)
        if k:
            _src_hit('tencent'); return k
        _src_miss('tencent')
    if _src_ok('em'):
        code = sym[2:] if len(sym) > 2 else sym
        sec = em_secid(code)
        if sec:
            k = fetch_kline_em(sec)
            if k:
                _src_hit('em'); return k
            _src_miss('em')
    if _src_ok('sina'):
        k = fetch_kline_sina(sym)
        if k:
            _src_hit('sina'); return k
        _src_miss('sina')
    return None


def load_cache():
    if os.path.exists(CACHE):
        try:
            c = json.load(open(CACHE, encoding='utf-8'))
            # 只保留有效(非空)K线，坏条目(空列表)丢弃以便重新拉取
            return {k: v for k, v in c.items() if isinstance(v, list) and len(v) > 0}
        except Exception:
            pass
    return {}


def _atomic_write(path, data, is_json=False):
    # 先写临时文件再 os.replace，避免进程被中断(如 TaskStop)时主文件被截断损坏
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            if is_json:
                json.dump(data, f, ensure_ascii=False, indent=2)
            else:
                f.write(data)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def save_cache(c):
    # 只持久化有数据的K线；失败的(None/空)不写入，便于IP解封后重跑补齐
    _atomic_write(CACHE, {k: v for k, v in c.items() if v}, is_json=True)


KL = load_cache()
_CONSEC_FAIL = 0

# 源状态：BAD=本轮暂时回避；DEAD=会话级硬失效(501/连接关闭，整轮不再恢复)
SRC_BAD = {'tencent': False, 'em': False, 'sina': False}
SRC_DEAD = {'tencent': False, 'em': False, 'sina': False}
SRC_STREAK = {'tencent': 0, 'em': 0, 'sina': 0}
SRC_THRESHOLD = 8
RETRY_COOLDOWN = 8      # 新浪被限流后的全局冷却秒数（限流多为短时窗口）
MAX_COOLDOWNS = 4       # 三源全死冷却循环上限(<=4次≈32s即放弃本轮联网，剩余按「跳过」)；避免限流时长时间空转
_COOLDOWN_CYCLES = 0

def _src_ok(tag):
    return (not SRC_BAD[tag]) and (not SRC_DEAD[tag])

def _src_hit(tag):
    SRC_STREAK[tag] = 0
    SRC_BAD[tag] = False

def _src_miss(tag):
    SRC_STREAK[tag] += 1
    if SRC_STREAK[tag] >= SRC_THRESHOLD:
        SRC_BAD[tag] = True
        SRC_DEAD[tag] = True   # 501/连接关闭通常是会话级IP封禁，整轮不再恢复


def _on_success():
    global _CONSEC_FAIL
    _CONSEC_FAIL = 0


def _on_fail():
    global _CONSEC_FAIL, FETCH_DISABLED
    _CONSEC_FAIL += 1
    if _CONSEC_FAIL >= 60:
        FETCH_DISABLED = True
        log("!! 连续抓取失败 >=60(疑似整轮全源不可用)，停止后续联网，剩余按「跳过」")


def get_kline(code):
    global _COOLDOWN_CYCLES, FETCH_DISABLED
    sym = sym_of(code)
    if not sym:
        return None
    if sym in KL:            # 已在本轮决定过（成功列表 或 失败 None）
        return KL[sym]
    if OFFLINE or FETCH_DISABLED:
        return None
    # 腾讯/东财为会话级硬封；若新浪也限流(三源全死)，全局冷却后重置新浪续跑
    if SRC_DEAD['tencent'] and SRC_DEAD['em'] and SRC_DEAD['sina']:
        _COOLDOWN_CYCLES += 1
        if _COOLDOWN_CYCLES > MAX_COOLDOWNS:
            FETCH_DISABLED = True
            log("!! 三源全死冷却循环超限(%d)，判定本IP整轮不可用，停止联网，剩余信号按「跳过」" % MAX_COOLDOWNS)
            return None
        log("  三源均受限，冷却 %ds 后重置新浪续跑 (%d/%d)" % (RETRY_COOLDOWN, _COOLDOWN_CYCLES, MAX_COOLDOWNS))
        time.sleep(RETRY_COOLDOWN)
        for t in ('tencent', 'em', 'sina'):
            SRC_BAD[t] = False
            SRC_DEAD[t] = False
            SRC_STREAK[t] = 0
    k = fetch_kline(sym)
    if k is not None:
        KL[sym] = k
        save_cache(KL)       # 仅落盘成功K线；失败不持久化→下次运行可重试
        _on_success()
        time.sleep(FETCH_GAP)   # 礼貌限速，规避突发限流
    else:
        KL[sym] = None       # 本轮内存标记失败，避免同轮重复请求
        _on_fail()
    return k


# ------------------------- 解析历史信号 -------------------------
NEWS_LIKE = ('xueqiu', '7x24', '市场深度', '解读', 'news', '快讯')


def is_news_strategy(name):
    nl = name.lower()
    return any(k in nl for k in NEWS_LIKE)


def parse_signals():
    d = json.load(open(PICKS, encoding='utf-8'))
    dates = sorted([k for k in d if k[0:1].isdigit() and len(k) >= 8])
    sigs = defaultdict(list)
    strat_total = defaultdict(int)
    strat_with_code = defaultdict(int)
    for dt in dates:
        node = d[dt]
        if not isinstance(node, dict):
            continue
        for sname, sval in node.items():
            if sname == 'sector_rankings':
                continue
            picks = sval.get('picks', []) if isinstance(sval, dict) else (sval if isinstance(sval, list) else [])
            for p in picks:
                strat_total[sname] += 1
                code = p.get('code') or p.get('证券代码') or ''
                if not code or len(str(code)) < 6 or not any(ch.isdigit() for ch in str(code)):
                    continue
                code = str(code).strip()
                strat_with_code[sname] += 1
                price = p.get('price') or p.get('recommend_price') or p.get('recommendPrice')
                try:
                    price = float(price) if price not in (None, '-', '') else None
                except Exception:
                    price = None
                sigs[sname].append((dt, code, p.get('name', ''), price))
    stock_sigs = {}
    for sname, lst in sigs.items():
        if is_news_strategy(sname):
            continue
        if strat_total[sname] > 0 and strat_with_code[sname] / strat_total[sname] < 0.5:
            continue
        seen = set()
        ded = []
        for dt, code, name, price in lst:
            kk = (dt, code)
            if kk in seen:
                continue
            seen.add(kk)
            ded.append((dt, code, name, price))
        stock_sigs[sname] = ded
    return stock_sigs, strat_total, strat_with_code


# ------------------------- 单笔回测 -------------------------
def backtest_signal(kline, sdate, entry_price, target=None, stop=None, maxhold=None):
    target = TARGET if target is None else target
    stop = STOP if stop is None else stop
    maxhold = MAX_HOLD if maxhold is None else maxhold
    idx = None
    for i, r in enumerate(kline):
        if r[0] >= sdate:
            idx = i
            break
    if idx is None:
        return None
    if entry_price and entry_price > 0:
        entry = entry_price
    else:
        entry = None
        for r in kline[idx:]:
            if r[0] == sdate:
                entry = r[2]
                break
        if entry is None:
            entry = kline[idx][2]
    fwd = kline[idx + 1: idx + 1 + maxhold + 6]
    if len(fwd) < 1:
        return None
    res = {'entry': entry}
    for H in (1, 3, 5):
        res['t%d_gross' % H] = (fwd[H - 1][2] / entry - 1) if len(fwd) >= H else None
    exit_price = None
    exit_day = None
    reason = None
    for di, r in enumerate(fwd, start=1):
        hi, lo = r[3], r[4]
        if hi >= entry * (1 + target):
            exit_price = entry * (1 + target); exit_day = di; reason = 'target'; break
        if lo <= entry * (1 + stop):
            exit_price = entry * (1 + stop); exit_day = di; reason = 'stop'; break
        if di >= maxhold:
            exit_price = r[2]; exit_day = di; reason = 'hold'; break
    if exit_price is None:
        exit_price = fwd[-1][2]; exit_day = len(fwd); reason = 'hold'
    res['managed_gross'] = exit_price / entry - 1
    res['managed_net'] = exit_price / entry - 1 - ROUND_TRIP_COST
    res['managed_exit_day'] = exit_day
    res['managed_reason'] = reason
    return res


# ------------------------- 聚合 -------------------------
def aggregate(stock_sigs, target=None, stop=None, maxhold=None):
    report = {}
    for sname, lst in stock_sigs.items():
        wins = []
        losses = []
        nets = []
        skipped = 0
        horizon = {'t1': [], 't3': [], 't5': []}
        monthly = defaultdict(lambda: {'n': 0, 'w': 0})
        equity = 1.0
        peak = 1.0
        mdd = 0.0
        for dt, code, name, price in lst:
            kline = get_kline(code)
            if not kline:
                skipped += 1
                continue
            r = backtest_signal(kline, dt, price, target, stop, maxhold)
            if not r:
                skipped += 1
                continue
            net = r['managed_net']
            nets.append(net)
            if net > 0:
                wins.append(net)
            else:
                losses.append(net)
            m = dt[:7]
            monthly[m]['n'] += 1
            if net > 0:
                monthly[m]['w'] += 1
            equity *= (1 + net)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > mdd:
                mdd = dd
            for H, key in ((1, 't1'), (3, 't3'), (5, 't5')):
                g = r.get('t%d_gross' % H)
                if g is not None:
                    horizon[key].append(g)
        n = len(nets)
        win_n = len(wins)
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
        report[sname] = {
            'n': n,
            'skipped': skipped,
            'win_rate': round(win_n / n * 100, 1) if n else 0,
            'win_n': win_n,
            'loss_n': n - win_n,
            'avg_win': round(sum(wins) / win_n * 100, 2) if wins else 0,
            'avg_loss': round(sum(losses) / len(losses) * 100, 2) if losses else 0,
            'profit_factor': round(pf, 2),
            'expectancy': round(sum(nets) / n * 100, 2) if n else 0,
            'max_drawdown': round(mdd * 100, 2),
            't1_wr': round(sum(1 for x in horizon['t1'] if x > 0) / len(horizon['t1']) * 100, 1) if horizon['t1'] else None,
            't3_wr': round(sum(1 for x in horizon['t3'] if x > 0) / len(horizon['t3']) * 100, 1) if horizon['t3'] else None,
            't5_wr': round(sum(1 for x in horizon['t5'] if x > 0) / len(horizon['t5']) * 100, 1) if horizon['t5'] else None,
            'monthly': {m: {'n': v['n'], 'wr': round(v['w'] / v['n'] * 100, 1)} for m, v in sorted(monthly.items())},
        }
    return report


# ------------------------- 报告 HTML -------------------------
def family_of(name):
    if name.startswith('大力水手') or '菠菜' in name:
        return '大力水手/菠菜涨停'
    if name.startswith('追涨强势股'):
        return '追涨强势股'
    if name.startswith('高欣'):
        return '高欣系列'
    if name in ('杰克船长', '船长钓鱼战法'):
        return '杰克系列'
    if '陈小群' in name:
        return '陈小群'
    if '财报' in name:
        return '财报+技术'
    return '其他'


def build_html(report_wide, report_narrow, meta):
    report = report_wide
    rows = sorted(report.items(), key=lambda kv: (kv[1]['expectancy'], kv[1]['win_rate']), reverse=True)
    families = defaultdict(list)
    for sname, st in report.items():
        families[family_of(sname)].append((sname, st))

    def color_wr(v):
        if v is None:
            return '#8b949e'
        if v >= 55:
            return '#3fb950'   # 暗底亮绿
        if v >= 50:
            return '#d29922'   # 暗底琥珀
        return '#f85149'       # 暗底亮红

    def color_exp(v):
        if v > 0:
            return '#3fb950'
        return '#f85149'

    body = []
    body.append('<h2>策略回测总览（按期望值排序）</h2>')
    body.append('<table class="tbl"><thead><tr>'
                '<th>策略</th><th>样本</th><th>跳过</th><th>胜率%</th>'
                '<th>均盈%</th><th>均亏%</th><th>盈亏比</th><th>期望值%</th>'
                '<th>最大回撤%</th><th>T+1胜率</th><th>T+3胜率</th><th>T+5胜率</th>'
                '</tr></thead><tbody>')
    for sname, st in rows:
        body.append('<tr>'
                    '<td class="sname">%s</td>'
                    '<td>%d</td><td>%d</td>'
                    '<td style="color:%s;font-weight:700">%s</td>'
                    '<td>%s</td><td>%s</td><td>%s</td>'
                    '<td style="color:%s;font-weight:700">%s</td>'
                    '<td>%s</td>'
                    '<td>%s</td><td>%s</td><td>%s</td>'
                    '</tr>' % (
                        sname, st['n'], st['skipped'],
                        color_wr(st['win_rate']), st['win_rate'],
                        st['avg_win'], st['avg_loss'], st['profit_factor'],
                        color_exp(st['expectancy']), st['expectancy'],
                        st['max_drawdown'],
                        st['t1_wr'] if st['t1_wr'] is not None else '-',
                        st['t3_wr'] if st['t3_wr'] is not None else '-',
                        st['t5_wr'] if st['t5_wr'] is not None else '-'))
    body.append('</tbody></table>')

    # 宽带 vs 窄带 对照（同一批K线，仅退出规则不同）
    body.append('<h2>宽退出带 vs 窄退出带（同一批信号，仅退出规则不同）</h2>')
    body.append('<p class="sub">宽：T+%d / 止盈+%d%% / 止损%d%%　窄：T+%d / 止盈+%d%% / 止损%d%%（看放宽止损止盈带后真实盈亏分布变化）</p>' % (
        MAX_HOLD, int(TARGET*100), int(STOP*100),
        NARROW[2], int(NARROW[0]*100), int(NARROW[1]*100)))
    cmp_rows = [r for r in rows if r[0] in report_narrow]
    cmp_rows.sort(key=lambda kv: kv[1]['expectancy'], reverse=True)
    body.append('<table class="tbl"><thead><tr>'
                '<th>策略</th><th>样本</th>'
                '<th>宽·胜率</th><th>窄·胜率</th>'
                '<th>宽·期望%</th><th>窄·期望%</th>'
                '<th>宽·盈亏比</th><th>窄·盈亏比</th>'
                '</tr></thead><tbody>')
    for sname, st in cmp_rows:
        nr = report_narrow[sname]
        body.append('<tr>'
                    '<td class="sname">%s</td><td>%d</td>'
                    '<td style="color:%s;font-weight:700">%s</td><td style="color:%s;font-weight:700">%s</td>'
                    '<td style="color:%s;font-weight:700">%s</td><td style="color:%s;font-weight:700">%s</td>'
                    '<td>%s</td><td>%s</td>'
                    '</tr>' % (
                        sname, st['n'],
                        color_wr(st['win_rate']), st['win_rate'], color_wr(nr['win_rate']), nr['win_rate'],
                        color_exp(st['expectancy']), st['expectancy'], color_exp(nr['expectancy']), nr['expectancy'],
                        st['profit_factor'], nr['profit_factor']))
    body.append('</tbody></table>')

    # 按月拆分
    body.append('<h2>按月胜率拆分（看市场状态敏感度）</h2>')
    all_months = sorted({m for st in report.values() for m in st['monthly']})
    body.append('<table class="tbl"><thead><tr><th>策略</th>')
    for m in all_months:
        body.append('<th>%s</th>' % m)
    body.append('</tr></thead><tbody>')
    for sname, st in rows:
        body.append('<tr><td class="sname">%s</td>' % sname)
        for m in all_months:
            cell = st['monthly'].get(m)
            if cell:
                body.append('<td style="color:%s">%s%%<br><span class="sub">n=%d</span></td>' % (
                    color_wr(cell['wr']), cell['wr'], cell['n']))
            else:
                body.append('<td>-</td>')
        body.append('</tr>')
    body.append('</tbody></table>')

    # 方法论 & bug 修正
    body.append('<h2>方法论与相对旧版修正点</h2>')
    body.append('<ul class="notes">')
    for note in meta['notes']:
        body.append('<li>%s</li>' % note)
    body.append('</ul>')
    body.append('<p class="foot">信号源：daily_picks.json（%s ~ %s）｜主配置(宽退出带)：最长T+%d，止盈+%d%%/止损%d%%｜双边成本%.2f%%｜入口价优先推荐价否则信号日收盘｜对照窄带见上文对照表</p>' % (
        meta['start'], meta['end'], MAX_HOLD, int(TARGET*100), int(STOP*100), ROUND_TRIP_COST*100))

    html = '''<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>vibe-dashboard 策略回测报告</title>
<style>
body{font-family:-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif;background:#0b0e14;color:#c9d1d9;margin:0;padding:24px;}
h1{font-size:20px;margin:0 0 4px;color:#e6edf3;} .sub{font-size:11px;color:#8b949e;}
h2{font-size:16px;margin:28px 0 10px;color:#e6edf3;border-left:4px solid #58a6ff;padding-left:8px;}
.tbl{border-collapse:collapse;width:100%%;background:#11151c;box-shadow:0 1px 3px rgba(0,0,0,.5);border:1px solid #21262d;font-size:13px;}
.tbl th{background:#161b22;color:#8b949e;padding:8px 6px;text-align:center;font-weight:600;border-bottom:2px solid #30363d;}
.tbl td{padding:7px 6px;text-align:center;border-bottom:1px solid #21262d;color:#c9d1d9;}
.tbl td.sname{text-align:left;font-weight:600;color:#e6edf3;white-space:nowrap;}
.tbl tr:hover td{background:#1c2230;}
.notes{background:#11151c;border:1px solid #21262d;color:#c9d1d9;padding:14px 18px;border-radius:8px;line-height:1.9;font-size:13px;}
.foot{color:#8b949e;font-size:12px;margin-top:18px;}
</style></head><body>
<h1>vibe-dashboard 选股策略回测报告（修正版）</h1>
<p class="sub">生成时间：%s</p>
%s
</body></html>''' % (meta['gen'], '\n'.join(body))

    return html


# ------------------------- 主流程 -------------------------
def main():
    log("== 解析历史信号 ==")
    stock_sigs, strat_total, strat_with_code = parse_signals()
    log("股票类策略数: %d；总信号(去重后)约 %d" % (
        len(stock_sigs), sum(len(v) for v in stock_sigs.values())))
    for s, v in sorted(stock_sigs.items(), key=lambda kv: -len(kv[1])):
        log("  %-28s 信号数=%d" % (s, len(v)))

    log("\n== 运行回测（拉取历史K线，带缓存）==")
    report_wide = aggregate(stock_sigs, TARGET, STOP, MAX_HOLD)
    report_narrow = aggregate(stock_sigs, *NARROW)

    dates = sorted([k for k in json.load(open(PICKS, encoding='utf-8')) if k[0:1].isdigit() and len(k) >= 8])
    valid_kl = sum(1 for v in KL.values() if v)
    meta = {
        'gen': time.strftime('%Y-%m-%d %H:%M'),
        'start': dates[0] if dates else '-',
        'end': dates[-1] if dates else '-',
        'notes': [
            f'主配置（宽退出带）：最长持有 T+{MAX_HOLD} 交易日，触达 +{int(TARGET*100)}% 止盈 / {int(STOP*100)}% 止损即退出（避免被窄带"削平"真实盈亏分布）；对照保留旧窄带 T+5/+5%/-3%。',
            '入口价优先用推荐价，缺失则用信号日收盘价；全程收盘对收盘，不再混用盘中实时价（旧版 jl 用新浪实时 parts[3]）。',
            f'扣除双边交易成本约 {ROUND_TRIP_COST*100:.2f}%（佣金+卖出印花税0.1%+滑点），短周期下显著影响净胜率（旧版零成本）。',
            '抓不到行情 / 无代码的记录单独计入「跳过」，不计入分母（旧版 jack_learning 会把缺失价静默算成亏损，系统性压低胜率）。',
            '输出胜率、平均盈/亏、盈亏比、期望值、最大回撤，并按月拆分以观察市场状态敏感度（旧版无样本量/显著性保护）。',
            '已过滤新闻类策略（xueqiu_7x24 / 市场深度解读）；威震天 / 盗火线 / 益盟强买 历史日期信号不在本文件，需补充 history JSON 才能回测。',
            f'覆盖率说明：本次成功取到期历史K线的股票 {valid_kl} 只；其余信号因数据源(腾讯/东财/新浪)对本沙箱IP限流未能取到行情，已计入各策略「跳过」、不计入分母。限流窗口过后重跑(去掉OFFLINE)即可补全覆盖。',
        ],
    }

    try:
        _atomic_write(REPORT_JSON, {'meta': meta, 'report': report_wide, 'report_narrow': report_narrow}, is_json=True)
        html = build_html(report_wide, report_narrow, meta)
        _atomic_write(REPORT_HTML, html, is_json=False)
    except Exception as e:
        log("写入报告异常(已至少保存JSON):", e)
        _atomic_write(REPORT_JSON, {'meta': meta, 'report': report_wide, 'report_narrow': report_narrow}, is_json=True)
    log("\n== 完成 ==")
    log("报告: %s" % REPORT_HTML)
    log("JSON: %s" % REPORT_JSON)
    log("缓存K线条数: %d" % len(KL))


if __name__ == '__main__':
    main()
