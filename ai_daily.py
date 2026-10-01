# -*- coding: utf-8 -*-
"""
ai_daily.py — AI 市场复盘数据的「采集 + 报告生成」一体化脚本

背景：
  ai_analysis.py 只负责把 ai_analysis_data.json(结构数据) + ai_analysis_report.json(报告正文)
  渲染成 ai_analysis.html。但它既不采集数据、也不写报告——这两份 JSON 在 Qclaw 时代由外部环节
  产出，迁移到 GitHub Actions 时一并丢失了生成脚本，导致页面永久停在 08-17。

  本脚本补上这两个缺失环节：
    1) 采集：实时抓指数/板块/涨幅榜，并复用仓库已有产物 strongbuy_data.json(益盟强买)、
       vibe_trend_history.json(慢热板块)，聚合写入 ai_analysis_data.json；
    2) 报告：优先用 GitHub Models（仓库自带 GITHUB_TOKEN + models:read，免费、无需 API key）；
       若其处于退役 brownout 或额度不足，则回退到 Google Gemini 免费档（需 GEMINI_API_KEY）；
       两者都不可用时降级为规则化模板——保证页面永远不空。

  由 workflow 在 ai_analysis.py 之前调用（run_if_exists ai_daily.py），两者串起来即形成完整
  「采集 → 报告 → 渲染」自动化链路。

设计原则（沿用本仓库既有约定）：
  - 交易日判断：非交易日直接 skip，保留上一交易日内容（与 market_review 一致）；
  - 数据源任一失败不影响其它板块（分别 try）；
  - 报告生成是「副产品」：即便 LLM 调用失败，结构化数据照常落地，页面至少有数据卡片。
"""
import sys, os, json, ssl, time, datetime, re, urllib.request, urllib.parse
from pathlib import Path

import paths
sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent
DATA_OUT   = paths.w('ai_analysis_data.json')
REPORT_OUT = paths.w('ai_analysis_report.json')
STRONG     = paths.w('strongbuy_data.json')   # 益盟强买（yimeng_strongbuy 产出）
TREND      = paths.w('vibe_trend_history.json')  # 慢热板块（update_slowrise 产出）
SLOW_STOCKS_OUT = paths.w('slowrise_stocks.json')  # 慢热板块后 Top10 个股推荐
BOARD_OUT  = paths.w('ai_analysis_board_kline.json')  # 板块日K缓存（页面同源弹板块K线）
EM_KLINE   = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
EM_KLINE_BASES = ['https://push2his.eastmoney.com/api/qt/stock/kline/get',
                  'https://92.push2his.eastmoney.com/api/qt/stock/kline/get',
                  'https://48.push2his.eastmoney.com/api/qt/stock/kline/get',
                  'https://21.push2his.eastmoney.com/api/qt/stock/kline/get',
                  'https://33.push2his.eastmoney.com/api/qt/stock/kline/get',
                  'https://56.push2his.eastmoney.com/api/qt/stock/kline/get',
                  'https://71.push2his.eastmoney.com/api/qt/stock/kline/get',
                  'http://push2his.eastmoney.com/api/qt/stock/kline/get']

# 复用 market_review 的抓取助手（已带新浪/腾讯降级、东财 push2delay→push2 降级）
from market_review import http_get, fetch_indices, is_trading_day, CTX, UA

EM_BASES = ['https://push2delay.eastmoney.com/api/qt/clist/get',
            'https://push2.eastmoney.com/api/qt/clist/get']
EM_UT = 'b2884a393a59ad64002292a3e90d46a5'


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def em_clist(fid, fields, fs, pz=20):
    """东财板块/个股列表（push2delay→push2 降级）。fs 控制范围，fields 控制返回字段。"""
    params = {'pn': '1', 'pz': str(pz), 'po': '1', 'np': '1',
              'ut': EM_UT, 'fltt': '2', 'invt': '2', 'fid': fid,
              'fs': fs, 'fields': fields}
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    last = None
    for base in EM_BASES:
        try:
            data = json.loads(http_get(base + '?' + query,
                                       headers={'User-Agent': UA,
                                                'Referer': 'https://quote.eastmoney.com/'},
                                       retries=2))
            diff = (data.get('data') or {}).get('diff') or []
            if diff:
                return diff
        except Exception as e:
            last = e
    if last:
        print(f'[warn] 东财 clist(fid={fid}, fs={fs}) 失败: {type(last).__name__}')
    return []


SINA_NODE = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'

def sina_gainers(pz=15):
    """新浪涨幅榜兜底（东财 clist 在云服务器 IP 常被封禁时启用）。
    返回 [{name,code,price,change_pct,turnover}]，仅保留沪深 A 股。"""
    import urllib.parse
    q = urllib.parse.urlencode({'page': 1, 'num': str(pz), 'sort': 'changepercent',
                                'asc': 0, 'node': 'hs_a', 'symbol': ''})
    try:
        txt = http_get(SINA_NODE + '?' + q,
                       headers={'User-Agent': UA, 'Referer': 'https://finance.sina.com.cn/'},
                       retries=2)
        arr = json.loads(txt)
    except Exception as e:
        print(f'[warn] 新浪涨幅榜失败: {type(e).__name__}')
        return []
    out = []
    _A_PREFIX = ('60', '68', '00', '30', '20', '90')
    for it in arr:
        code = str(it.get('code', ''))
        name = it.get('name')
        if not code or not name or not code.startswith(_A_PREFIX):
            continue
        out.append({'name': name, 'code': code,
                    'price': round(fnum(it.get('trade')), 2),
                    'change_pct': round(fnum(it.get('changepercent')), 2),
                    'turnover': round(fnum(it.get('turnoverratio')), 2)})
    return out[:pz]


# ─────────────── 行业板块（新浪兜底，CI 友好） ───────────────
# 东财 m:90+t:2 板块列表在云服务器 IP 被封；新浪行业板块(制造业细分)节点稳定可达，
# 抓每个板块全部成分股，资本加权算出板块涨跌幅 + 板块成交额。映射来自新浪行业页静态分类。
SINA_HY = {
    'ZC13': '农副食品加工业', 'ZC14': '食品制造业', 'ZC15': '酒饮料茶制造业',
    'ZC16': '烟草制品业', 'ZC17': '纺织业', 'ZC18': '纺织服装服饰业',
    'ZC19': '皮革毛皮制鞋业', 'ZC20': '木材加工业', 'ZC21': '家具制造业',
    'ZC22': '造纸纸制品业', 'ZC23': '印刷媒介复制业', 'ZC24': '文教体育用品业',
    'ZC25': '石油炼焦加工业', 'ZC26': '化学原料制品业', 'ZC27': '医药制造业',
    'ZC28': '化学纤维制造业', 'ZC29': '橡胶塑料制品业', 'ZC30': '非金属矿物制品业',
    'ZC31': '黑色金属冶炼业', 'ZC32': '有色金属冶炼业', 'ZC33': '金属制品业',
    'ZC34': '通用设备制造业', 'ZC35': '专用设备制造业', 'ZC36': '汽车制造业',
    'ZC37': '铁路船舶航天业', 'ZC38': '电气机械器材业', 'ZC39': '计算机电子设备业',
    'ZC40': '仪器仪表制造业', 'ZC41': '其他制造业', 'ZC42': '废弃资源利用业',
    'ZC43': '金属制品修理业',
}


def _sina_sector_one(node_id):
    """抓单个新浪行业板块全部成分股，返回 (资本加权涨跌幅%, 成交额亿)。失败返回 None。"""
    node = 'hangye_' + node_id
    total_w, total_chg, amount = 0.0, 0.0, 0.0
    page = 1
    while page <= 5:
        q = urllib.parse.urlencode({'page': page, 'num': '100', 'sort': 'symbol',
                                    'asc': 1, 'node': node, 'symbol': '', '_s_r_a': 'init'})
        try:
            txt = http_get(SINA_NODE + '?' + q,
                           headers={'User-Agent': UA,
                                    'Referer': 'https://vip.stock.finance.sina.com.cn/mkt/'},
                           retries=2)
            arr = json.loads(txt)
        except Exception as e:
            print(f'[warn] 新浪板块 {node_id} 第{page}页失败: {type(e).__name__}')
            break
        if not isinstance(arr, list) or not arr:
            break
        for it in arr:
            chg = fnum(it.get('changepercent'))
            cap = fnum(it.get('mktcap'))        # 万元
            amount += fnum(it.get('amount'))    # 元
            if cap > 0:
                total_w += cap
                total_chg += chg * cap
        if len(arr) < 100:
            break
        page += 1
    if total_w <= 0:
        return None
    return round(total_chg / total_w, 2), round(amount / 1e8, 1)


def sina_sectors(pz=12):
    """新浪行业板块兜底：资本加权涨跌幅 + 成交额。
    返回 [{name, code, pct, amount_yi, net_inflow_yi}]，按涨跌幅降序取 Top。"""
    import concurrent.futures
    out = []
    def _one(nid):
        return nid, _sina_sector_one(nid)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for nid, res in ex.map(_one, SINA_HY.keys()):
            if res:
                avg, amt = res
                out.append({'name': SINA_HY[nid], 'code': nid,
                            'pct': avg, 'amount_yi': amt, 'net_inflow_yi': 0})
    out.sort(key=lambda x: x['pct'], reverse=True)
    return out[:pz]


def pick_slowrise_stocks(boards, limit=20, per_board=2, top_boards=10):
    """按板块热度选潜力股（数据驱动，不依赖 LLM）：
    遍历热度前 top_boards 个板块（rank 升序），每块取当日涨幅前 per_board 的成分股，
    跨板块去重后最多 limit 只。板块越热越靠前，块内强者优先。
    boards 为 [(板块名, {rank, chg, code, stocks:[{code,name,chg}]}), ...]，已按 rank 升序。"""
    picks, seen = [], set()

    def _emit(window, name, v):
        rank = v.get('rank')
        rank_s = int(rank) if isinstance(rank, (int, float)) else '-'
        for s in window:
            code = str(s.get('code') or '').strip()
            if not code or code in seen:
                continue
            seen.add(code)
            picks.append({'code': code, 'name': s.get('name', ''),
                          'chg': fnum(s.get('chg')), 'board': name,
                          'reason': f'热度第{rank_s}的「{name}」内领涨'})
            if len(picks) >= limit:
                return True
        return False

    # 两轮挑选：第一轮每块涨幅前 per_board 名；不足 limit 时第二轮每块再补 1 名。
    # 防御：A股单日涨跌停上限约 30%（北交所），超出必是坏数据（如曾把腾讯报价的
    # 最高价 f[33] 错当涨幅写入快照），直接剔除，宁缺毋滥。
    for lo in (0, per_board):
        for name, v in boards[:top_boards]:
            stocks = [s for s in (v.get('stocks') or [])
                      if isinstance(s, dict) and s.get('code') and s.get('name')]
            stocks = [s for s in stocks if abs(fnum(s.get('chg'))) <= 30.5]
            stocks.sort(key=lambda s: fnum(s.get('chg')), reverse=True)
            if _emit(stocks[lo:lo + 1] if lo else stocks[:per_board], name, v):
                return picks
    return picks


def slowrise_from_trend(th):
    """从 vibe_trend_history.json 提取最新一天：慢热板块列表 + 热度潜力股。
    返回 (slowrise, picks, latest_date)。"""
    # ⚠️ 文件里除日期键外还有 "_updated" 等元数据键，而 '_'(0x5F) 的 ASCII
    #    码大于数字，直接 sorted(th.keys())[-1] 会取到 "_updated"，
    #    于是 th[latest] 是字符串、取 .keys() 抛 AttributeError，
    #    被 except 吞掉 → 页面「慢热板块跟踪」永远「暂无数据」。
    #    必须先按日期格式过滤，再取最大日期。
    dates = [k for k in th
             if isinstance(k, str) and len(k) == 10 and k[4] == '-' and k[7] == '-'
             and k[:4].isdigit() and k[5:7].isdigit() and k[8:10].isdigit()]
    latest = max(dates) if dates else None
    day = th.get(latest) if latest else None
    if not isinstance(day, dict):
        return [], [], None
    # 按 rank 升序取前 15（原始插入顺序不可靠），过滤掉非 dict 的元数据
    boards = [(n, v) for n, v in day.items() if isinstance(v, dict)]
    boards.sort(key=lambda kv: kv[1].get('rank')
                if isinstance(kv[1].get('rank'), (int, float)) else 10 ** 9)
    slowrise = [{'name': n, 'chg': v.get('chg')} for n, v in boards[:15]]
    picks = pick_slowrise_stocks(boards)
    return slowrise, picks, latest


def collect():
    """聚合结构化数据，返回 ai_analysis_data.json 的 dict。"""
    now = datetime.datetime.now()
    data = {'indices': [], 'top_gainers': [], 'sectors': [],
            'us_stocks': [], 'us_indices': [], 'strong_stocks': [],
            'yimeng_stocks': [], 'slowrise': [], 'slowrise_picks': []}

    # 1) 八大指数
    idx_raw = fetch_indices()
    for name, d in idx_raw.items():
        data['indices'].append({'name': name, 'current': d['price'], 'pct': d['chg']})
    print(f'[ok] 指数 {len(data["indices"])} 个')

    # 2) 行业板块（涨跌幅 + 成交额）；东财 clist 在云服务器 IP 常被封 → 回退新浪行业板块
    for it in em_clist('f3', 'f12,f14,f3,f6,f62', 'm:90+t:2', pz=30)[:12]:
        name = it.get('f14')
        if not name:
            continue
        data['sectors'].append({'name': name,
                                'code': it.get('f12'),
                                'pct': round(fnum(it.get('f3')), 2),
                                'amount_yi': round(fnum(it.get('f6')) / 1e8, 1),
                                'net_inflow_yi': round(fnum(it.get('f62')) / 1e8, 1)})
    if not data['sectors']:
        print('[info] 东财板块为空，回退新浪行业板块')
        data['sectors'] = sina_sectors(12)
    print(f'[ok] 板块 {len(data["sectors"])} 个')

    # 3) 涨幅榜强势股（只保留沪深 A 股：主板/科创板/创业板/中小板；过滤新三板/期权/ETF 等噪声）
    #    东财 clist 在云服务器 IP 常被封 → 回退新浪涨幅榜，保证 CI 上也有数据。
    _A_PREFIX = ('60', '68', '00', '30', '20', '90')
    for it in em_clist('f3', 'f12,f14,f2,f3,f8', 'm:0+t:6', pz=25):
        name, code = it.get('f14'), it.get('f12')
        if not name or not code:
            continue
        if not code.startswith(_A_PREFIX):
            continue
        data['strong_stocks'].append({'name': name, 'code': code,
                                      'price': round(fnum(it.get('f2')), 2),
                                      'change_pct': round(fnum(it.get('f3')), 2),
                                      'turnover': round(fnum(it.get('f8')), 2)})
    if not data['strong_stocks']:
        print('[info] 东财涨幅榜为空，回退新浪涨幅榜')
        data['strong_stocks'] = sina_gainers(15)
    data['strong_stocks'] = data['strong_stocks'][:15]
    data['top_gainers'] = data['strong_stocks'][:]
    print(f'[ok] 涨幅榜强势股 {len(data["strong_stocks"])} 只')

    # 4) 益盟强买（复用 strongbuy_data.json）
    try:
        sb = json.loads(open(STRONG, encoding='utf-8').read()) if os.path.exists(STRONG) else {}
        for s in sb.get('yimeng', []) or []:
            data['yimeng_stocks'].append({
                'name': s.get('name'), 'code': s.get('code'),
                'price': round(fnum(s.get('price')), 2),
                'change_pct': round(fnum(s.get('change_pct')), 2),
                'turnover': round(fnum(s.get('turnover')), 2),
            })
        print(f'[ok] 益盟强买 {len(data["yimeng_stocks"])} 只 (来自 strongbuy_data.json)')
    except Exception as e:
        print(f'[warn] 读取 strongbuy_data.json 失败: {type(e).__name__}')

    # 5) 慢热板块（复用 vibe_trend_history.json 最新日期）
    try:
        if os.path.exists(TREND):
            th = json.loads(open(TREND, encoding='utf-8').read())
            if th:
                slowrise, picks, latest = slowrise_from_trend(th)
                data['slowrise'] = slowrise
                data['slowrise_picks'] = picks
                if slowrise:
                    print(f'[ok] 慢热板块 {len(slowrise)} 个、热度潜力股 {len(picks)} 只 (日期 {latest})')
                else:
                    print(f'[warn] vibe_trend_history.json 无可用日期键: {sorted(map(str, th.keys()))[:6]}')
    except Exception as e:
        print(f'[warn] 读取 vibe_trend_history.json 失败: {type(e).__name__}: {e}')

    return {'data': data, 'timestamp': now.strftime('%Y-%m-%dT%H:%M:%S')}


# ───────────────────────── 报告生成：GitHub Models ──────────────────────────
SYS_PROMPT = (
    "你是经验丰富的 A 股收盘复盘分析师。基于给定的结构化市场数据，输出一份简洁、"
    "数据驱动的 Markdown 复盘报告。\n"
    "严格要求：\n"
    "1. 只能基于提供的数据陈述，禁止编造任何未给出的个股、数值或消息；\n"
    "2. 使用以下固定小节标题：## 大盘综述 / ## 板块解读 / ## 资金与情绪 / ## 后市展望 / ## 风险提示；\n"
    "3. 语言精炼、口语化但专业，全篇不超过 600 字；\n"
    "4. 风险提示必须存在且客观，提示市场有风险、内容不构成投资建议。"
)


def build_user_prompt(d):
    s = d['data']
    lines = []
    lines.append('【指数】')
    for i in s['indices']:
        lines.append(f"- {i['name']} {i['current']} ({i['pct']:+.2f}%)")
    if s['sectors']:
        lines.append('\n【行业板块（涨跌幅/成交额亿）】')
        for x in s['sectors'][:8]:
            lines.append(f"- {x['name']} {x['pct']:+.2f}%  成交额 {x.get('amount_yi', 0):.1f}亿")
    if s['strong_stocks']:
        lines.append('\n【涨幅榜强势股】')
        for x in s['strong_stocks'][:8]:
            lines.append(f"- {x['name']}({x['code']}) {x['change_pct']:+.2f}% 换手 {x['turnover']:.1f}%")
    if s['yimeng_stocks']:
        lines.append('\n【益盟强买 Top】')
        for x in s['yimeng_stocks'][:8]:
            lines.append(f"- {x['name']}({x['code']}) {x['change_pct']:+.2f}% 换手 {x['turnover']:.1f}%")
    if s['slowrise']:
        lines.append('\n【慢热板块】' + '、'.join(x['name'] for x in s['slowrise'][:10]))
    return '\n'.join(lines)


def call_github_models(user_text, system=None):
    """调用 GitHub Models 生成报告。返回 (report_text, ok)。无 token/失败则 ok=False。
    system 可覆盖默认 SYS_PROMPT（用于慢热个股推荐等子任务）。"""
    token = os.environ.get('GITHUB_TOKEN', '')
    if not token:
        print('[info] 未检测到 GITHUB_TOKEN，跳过 LLM，使用规则化报告')
        return None, False
    payload = {
        'model': 'openai/gpt-4o-mini',
        'messages': [
            {'role': 'system', 'content': system or SYS_PROMPT},
            {'role': 'user', 'content': user_text},
        ],
        'temperature': 0.3,
        'max_tokens': 1500,
    }
    req = urllib.request.Request(
        'https://models.github.ai/inference/chat/completions',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': 'Bearer ' + token,
                 'Accept': 'application/json'})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                resp = json.loads(r.read().decode('utf-8'))
            msg = ((resp.get('choices') or [{}])[0].get('message') or {}).get('content', '')
            if msg.strip():
                return msg.strip(), True
            return None, False
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'ignore')[:200]
            print(f'[warn] GitHub Models HTTP {e.code}: {body}')
            if e.code in (401, 403, 410):
                return None, False   # 权限/额度/退役(brownout)，直接降级
            time.sleep(2)
        except Exception as e:
            print(f'[warn] GitHub Models 调用失败: {type(e).__name__}')
            time.sleep(2)
    return None, False


def call_gemini(user_text, system=None):
    """备用 LLM：Google Gemini 免费档（仅需 GEMINI_API_KEY，无需信用卡）。
    仅在 GitHub Models 不可用（如退役 brownout）时启用，作为真正的 AI 报告来源。
    system 可覆盖默认 SYS_PROMPT。"""
    key = os.environ.get('GEMINI_API_KEY', '')
    if not key:
        return None, False
    prompt = (system or SYS_PROMPT) + '\n\n' + user_text
    payload = {
        'contents': [{'parts': [{'text': prompt}]}],
        'generationConfig': {'temperature': 0.3, 'maxOutputTokens': 1500},
    }
    url = ('https://generativelanguage.googleapis.com/v1beta/models/'
           'gemini-2.0-flash:generateContent?key=' + key)
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'),
                                headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
            resp = json.loads(r.read().decode('utf-8'))
        parts = (((resp.get('candidates') or [{}])[0].get('content') or {}).get('parts') or [])
        msg = ''.join(p.get('text', '') for p in parts).strip()
        if msg:
            return msg, True
    except urllib.error.HTTPError as e:
        print(f'[warn] Gemini HTTP {e.code}: {e.read().decode("utf-8","ignore")[:160]}')
    except Exception as e:
        print(f'[warn] Gemini 调用失败: {type(e).__name__}')
    return None, False



def call_deepseek(user_text, system=None):
    """备用 LLM：DeepSeek（OpenAI 兼容协议，国内可直连，免费档充足）。
    作为 GitHub Models 退役、Gemini 未配 key 时的真实 AI 报告来源。
    需仓库 Secret DEEPSEEK_API_KEY。"""
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not key:
        return None, False
    try:
        from openai import OpenAI
    except Exception:
        return None, False
    try:
        client = OpenAI(api_key=key, base_url='https://api.deepseek.com')
        resp = client.chat.completions.create(
            model='deepseek-chat',
            messages=[{'role': 'system', 'content': system or SYS_PROMPT},
                      {'role': 'user', 'content': user_text}],
            max_tokens=1500, temperature=0.3)
        msg = (resp.choices[0].message.content or '').strip()
        if msg:
            return msg, True
    except Exception as e:
        print(f'[warn] DeepSeek 调用失败: {type(e).__name__}: {e}')
    return None, False


def fallback_report(d):
    """规则化降级报告（保证页面不空，且全部数据驱动）。"""
    s = d['data']
    now = d.get('timestamp', '')[:10]
    L = []
    L.append(f'## 大盘综述\n')
    idx = {i['name']: i for i in s['indices']}
    if idx:
        sh = idx.get('上证指数', {}).get('pct', 0)
        mood = '整体偏暖' if sh > 0.3 else ('整体偏弱' if sh < -0.3 else '窄幅震荡')
        L.append(f'今日 A 股{mood}。' + '；'.join(
            f"{n} {v['current']}（{v['pct']:+.2f}%）" for n, v in list(idx.items())[:4]) + '。')
    if s['sectors']:
        top = s['sectors'][0]
        L.append(f'\n## 板块解读\n\n领涨行业为 **{top["name"]}**（{top["pct"]:+.2f}%），'
                 + '、'.join(x['name'] for x in s['sectors'][1:3]) + ' 等跟随。')
    if s['strong_stocks']:
        L.append(f'\n## 资金与情绪\n\n涨幅榜以 '
                 + '、'.join(x['name'] for x in s['strong_stocks'][:3])
                 + ' 为首，短线情绪活跃。')
    if s['yimeng_stocks']:
        L.append(f'益盟强买方向：' + '、'.join(x['name'] for x in s['yimeng_stocks'][:3]) + '。')
    if s['slowrise']:
        L.append(f'慢热板块值得跟踪：' + '、'.join(x['name'] for x in s['slowrise'][:6]) + '。')
    L.append('\n## 后市展望\n')
    if idx:
        sh = idx.get('上证指数', {}).get('pct', 0)
        if sh > 0.3:
            L.append('指数走强，可积极参与主线，注意避免追高后排跟风股。')
        elif sh < -0.3:
            L.append('指数承压，控制仓位、等待企稳信号，忌盲目抄底。')
        else:
            L.append('多空分歧，控制仓位、逢低布局景气方向，忌追涨杀跌。')
    L.append('\n## 风险提示\n\n以上基于收盘数据的客观汇总，不预示未来走势，'
             '市场有风险，内容不构成任何投资建议。')
    return '\n'.join(L)


def fetch_board_kline(code, lmt=320):
    """东财板块日K。多镜像域名轮询+ut令牌+限速重试；失败返回 None。"""
    params = {'secid': '90.' + code, 'fields1': 'f1,f2,f3,f4,f5,f6',
              'fields2': 'f51,f52,f53,f54,f55,f56,f57', 'klt': '101',
              'fqt': '1', 'end': '20500101', 'lmt': str(lmt), 'ut': EM_UT}
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    last = None
    for base in EM_KLINE_BASES:
        try:
            time.sleep(0.15)
            data = json.loads(http_get(base + '?' + query,
                                       headers={'User-Agent': UA,
                                                'Referer': 'https://quote.eastmoney.com/'},
                                       retries=1, timeout=6))
            kl = (data.get('data') or {}).get('klines') or []
            bars = []
            for line in kl:
                p = line.split(',')
                if len(p) >= 6:
                    bars.append([p[0], p[1], p[2], p[4], p[3], p[5]])   # [d,o,c,l,h,v]
            if bars:
                return bars
        except Exception as e:
            last = e
    print(f'[warn] 板块K线 {code} 失败: {type(last).__name__ if last else "空"}')
    return None


def write_board_klines(pz=30):
    """抓行业板块涨幅 Top-N 的日K，写 ai_analysis_board_kline.json（格式对齐 kline_cache.stocks）。
    供 ai_analysis.html 同源快速弹出板块K线；历史数据与交易日无关，非交易日也刷新。
    本轮抓取来源：
      1) BOARD_CODES 映射里的全部行业板块（确保页面上每个带 data-kline-sym 的板块都有缓存）；
      2) 叠加 em_clist 当前涨幅 Top-N（补全新板块、更新名称）。"""
    stocks = {}
    # 合并写：保留旧缓存里已成功的板块（本轮抓取失败也不丢，下轮继续补）
    try:
        old = json.load(open(BOARD_OUT, encoding='utf-8'))
        stocks.update(old.get('stocks') or {})
    except Exception:
        pass

    # 以 BOARD_CODES 为基准全集（保证页面映射的板块都有缓存）
    try:
        from ai_analysis import BOARD_CODES
        items = {c.upper(): n for n, c in BOARD_CODES.items() if str(c).startswith('BK')}
    except Exception:
        items = {}

    # 再叠加 clist 热门板块（更新名称、补充新上市/更名板块）
    try:
        for it in em_clist('f3', 'f12,f14', 'm:90+t:2', pz=pz):
            c = it.get('f12')
            n = it.get('f14')
            if c and n:
                items[c.upper()] = n
    except Exception:
        pass

    if not items:
        print('[warn] 板块K线无待抓列表，跳过')
        return 0

    print(f'[info] 板块K线待抓 {len(items)} 个')
    got = 0
    from concurrent.futures import ThreadPoolExecutor

    def _one(args):
        code, name = args
        return code, name, fetch_board_kline(code)

    deadline = time.time() + 240
    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, name, bars in ex.map(_one, sorted(items.items())):
            if time.time() > deadline:
                break
            if bars and len(bars) >= 2:
                stocks[code.lower()] = {'name': name, 'kline': bars}
                got += 1
    print(f'[info] 板块K线本轮新抓/更新 {got}，合并后共 {len(stocks)}')
    if not stocks:
        print('[warn] 板块K线全部失败，跳过写盘')
        return 0
    with open(BOARD_OUT, 'w', encoding='utf-8') as f:
        json.dump({'stocks': stocks,
                   'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M')},
                  f, ensure_ascii=False)
    print(f'[ok] 已写入 {BOARD_OUT} ({len(stocks)} 个板块)')
    return len(stocks)


# ───────────────────── 慢热个股推荐（方案 C） ─────────────────────
SLOW_SYS = (
    "你是 A 股选股助手。基于给定的慢热（持续走强）板块列表，挑选最值得关注的 Top20 A股个股。"
    "每个推荐必须给出 6 位 A 股代码（沪市60开头、深市00/30开头、科创板68开头、北交所8开头）、"
    "股票名称、以及一句话推荐理由（不超过 20 字）。只能从与所列慢热板块相关的个股中选取，禁止编造。\n"
    "输出要求：只输出一个 JSON 代码块，格式例如\n"
    "```json\n{\"slowrise_stocks\":[{\"code\":\"600519\",\"name\":\"贵州茅台\",\"reason\":\"行业龙头，慢热延续\"}]}\n```\n"
    "最多 20 项；若认为无合适标的，输出 {\"slowrise_stocks\":[]}。不要输出 JSON 代码块以外的任何文字。"
)


def _extract_json_block(text):
    """从 LLM 返回中提取第一个含 slowrise_stocks 的 JSON 对象。"""
    if not text:
        return None
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.S)
    if not m:
        m = re.search(r'\{[^{}]*"slowrise_stocks"\s*:\s*\[.*?\]\s*\}', text, re.S)
    if not m:
        return None
    blob = m.group(1) if m.group(1).strip().startswith('{') else m.group(0)
    try:
        return json.loads(blob)
    except Exception:
        return None


def _write_slowrise_stocks(uniq, source):
    """落盘 slowrise_stocks.json（始终写入，避免前端 fetch 404）。"""
    out = {
        'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'source': source,
        'stocks': uniq,
    }
    try:
        with open(SLOW_STOCKS_OUT, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[ok] 已写入 {SLOW_STOCKS_OUT} ({len(uniq)} 只, source={source})")
    except Exception as e:
        print(f'[warn] 写入 {SLOW_STOCKS_OUT} 失败: {type(e).__name__}: {e}')


def gen_slowrise_stocks(slowrise_names, fallback=None):
    """慢热潜力股推荐：优先 LLM（更有个股逻辑）；LLM 不可用/无产出时，
    回退到 collect() 产出的数据驱动挑选（fallback：板块热度×成分股涨幅）。
    无论成败都落盘 slowrise_stocks.json（保证前端不崩、不阻塞主流程、周末不 404）。"""
    if not slowrise_names and not fallback:
        print('[slowrise_stocks] 无慢热板块，写空占位')
        _write_slowrise_stocks([], 'empty')
        return []
    stocks = []
    if slowrise_names:
        prompt = '当前慢热（持续走强）板块：\n' + '、'.join(slowrise_names[:10]) + \
                 '\n\n请基于上述板块推荐 Top20 相关 A 股个股。'
        txt, ok = call_github_models(prompt, system=SLOW_SYS)
        if not ok or not txt:
            txt, ok = call_gemini(prompt, system=SLOW_SYS)
        if ok and txt:
            obj = _extract_json_block(txt)
            if obj and isinstance(obj.get('slowrise_stocks'), list):
                for it in obj['slowrise_stocks'][:20]:
                    name = str(it.get('name', '')).strip()
                    if not name:
                        continue
                    code = str(it.get('code', '')).strip()
                    # 轻校验代码：不规范则留空（前端仍可显示名称，弹窗按名称兜底）
                    if code and not re.match(r'^(sh|sz|bj)?\d{6}$', code, re.I):
                        code = ''
                    reason = str(it.get('reason', '')).strip()
                    stocks.append({'code': code, 'name': name, 'reason': reason})
    # 按名称去重
    seen, uniq = set(), []
    for s in stocks:
        if s['name'] in seen:
            continue
        seen.add(s['name'])
        uniq.append(s)
    uniq = uniq[:20]
    if uniq:
        _write_slowrise_stocks(uniq, 'llm')
        return uniq
    if fallback:
        # 数据驱动兜底：板块热度 × 成分股涨幅（来自 vibe_trend_history.json 已回填成分股）
        uniq = [dict(x) for x in fallback][:10]
        _write_slowrise_stocks(uniq, 'components')
        return uniq
    _write_slowrise_stocks([], 'empty')
    return []


def main():
    now = datetime.datetime.now()
    print(f'\n[ai_daily] ===== {now:%Y-%m-%d %H:%M:%S} =====')
    # 板块K线缓存：历史数据，与交易日无关，非交易日也刷新（失败不影响后续流程）
    try:
        write_board_klines()
    except Exception as e:
        print(f'[warn] 板块K线缓存失败: {type(e).__name__}: {e}')
    if not is_trading_day(now):
        print('[skip] 非交易日，保留上一交易日 AI 复盘')
        # 非交易日不覆盖已有的慢热潜力股（节假日页面不至于清空）；
        # 仅当文件缺失或本来就是空占位时才写空，避免前端 fetch 404（sync_func 会回推）
        try:
            keep = False
            if os.path.exists(SLOW_STOCKS_OUT):
                old = json.loads(open(SLOW_STOCKS_OUT, encoding='utf-8').read())
                keep = bool(old.get('stocks'))
            if keep:
                print('[skip] 非交易日，保留已有慢热潜力股')
            else:
                gen_slowrise_stocks([])
        except Exception as e:
            print(f'[warn] 周末慢热占位写入失败: {type(e).__name__}: {e}')
        return 0

    d = collect()
    with open(DATA_OUT, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f'[ok] 已写入 {DATA_OUT}')

    # 慢热个股推荐：优先 LLM 荐股，LLM 不可用时回退到「板块热度×成分股涨幅」
    # 的数据驱动挑选（slowrise_picks，来自 vibe_trend_history.json 已回填成分股）
    try:
        gen_slowrise_stocks([x['name'] for x in d['data']['slowrise']],
                            fallback=d['data'].get('slowrise_picks'))
    except Exception as e:
        print(f'[warn] 慢热个股推荐生成失败: {type(e).__name__}: {e}')

    user_text = build_user_prompt(d)
    report, ok = call_github_models(user_text)
    src = 'GitHub Models'
    if not ok or not report:
        # GitHub Models 退役 brownout 或额度不足时，尝试 Gemini 免费档（需 GEMINI_API_KEY）
        report, ok = call_gemini(user_text)
        src = 'Google Gemini'
    if not ok or not report:
        # Gemini 未配 key 时，尝试 DeepSeek（需 DEEPSEEK_API_KEY，用户此前可用的真实 AI 来源）
        report, ok = call_deepseek(user_text)
        src = 'DeepSeek'
    if not ok or not report:
        report = fallback_report(d)
        src = '规则化模板(降级)'
    with open(REPORT_OUT, 'w', encoding='utf-8') as f:
        json.dump(
            {'date': now.strftime('%Y-%m-%d'),
             'generated_at': now.strftime('%Y-%m-%d %H:%M:%S'),
             'source': src, 'report': report},
            f, ensure_ascii=False, indent=2)
    print(f'[ok] 已写入 {REPORT_OUT} (来源: {src}, {len(report)} 字)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
