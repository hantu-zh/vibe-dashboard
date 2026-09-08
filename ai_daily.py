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
    2) 报告：用 GitHub Models（仓库自带 GITHUB_TOKEN + models:read，免费、无需 API key）
       生成 Markdown 复盘，写入 ai_analysis_report.json；失败时降级为规则化模板，保证页面不空。

  由 workflow 在 ai_analysis.py 之前调用（run_if_exists ai_daily.py），两者串起来即形成完整
  「采集 → 报告 → 渲染」自动化链路。

设计原则（沿用本仓库既有约定）：
  - 交易日判断：非交易日直接 skip，保留上一交易日内容（与 market_review 一致）；
  - 数据源任一失败不影响其它板块（分别 try）；
  - 报告生成是「副产品」：即便 LLM 调用失败，结构化数据照常落地，页面至少有数据卡片。
"""
import sys, os, json, ssl, time, datetime, urllib.request
from pathlib import Path

import paths
sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = Path(__file__).parent
DATA_OUT   = paths.w('ai_analysis_data.json')
REPORT_OUT = paths.w('ai_analysis_report.json')
STRONG     = paths.w('strongbuy_data.json')   # 益盟强买（yimeng_strongbuy 产出）
TREND      = paths.w('vibe_trend_history.json')  # 慢热板块（update_slowrise 产出）

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


def collect():
    """聚合结构化数据，返回 ai_analysis_data.json 的 dict。"""
    now = datetime.datetime.now()
    data = {'indices': [], 'top_gainers': [], 'sectors': [],
            'us_stocks': [], 'us_indices': [], 'strong_stocks': [],
            'yimeng_stocks': [], 'slowrise': []}

    # 1) 八大指数
    idx_raw = fetch_indices()
    for name, d in idx_raw.items():
        data['indices'].append({'name': name, 'current': d['price'], 'pct': d['chg']})
    print(f'[ok] 指数 {len(data["indices"])} 个')

    # 2) 行业板块（涨幅 + 主力净流入）
    for it in em_clist('f3', 'f12,f14,f3,f62', 'm:90+t:2', pz=30)[:12]:
        name = it.get('f14')
        if not name:
            continue
        data['sectors'].append({'name': name,
                                'pct': round(fnum(it.get('f3')), 2),
                                'net_inflow_yi': round(fnum(it.get('f62')) / 1e8, 1)})
    print(f'[ok] 板块 {len(data["sectors"])} 个')

    # 3) 涨幅榜强势股（只保留沪深 A 股：主板/科创板/创业板/中小板；过滤新三板/期权/ETF 等噪声）
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
                latest = sorted(th.keys())[-1]
                sectors = [s for s in th[latest].keys()][:15]
                data['slowrise'] = [{'name': s} for s in sectors]
                print(f'[ok] 慢热板块 {len(data["slowrise"])} 个 (日期 {latest})')
    except Exception as e:
        print(f'[warn] 读取 vibe_trend_history.json 失败: {type(e).__name__}')

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
        lines.append('\n【行业板块（涨幅/主力净流入亿）】')
        for x in s['sectors'][:8]:
            lines.append(f"- {x['name']} {x['pct']:+.2f}%  净流入 {x['net_inflow_yi']:+.1f}亿")
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


def call_github_models(user_text):
    """调用 GitHub Models 生成报告。返回 (report_text, ok)。无 token/失败则 ok=False。"""
    token = os.environ.get('GITHUB_TOKEN', '')
    if not token:
        print('[info] 未检测到 GITHUB_TOKEN，跳过 LLM，使用规则化报告')
        return None, False
    payload = {
        'model': 'openai/gpt-4o-mini',
        'messages': [
            {'role': 'system', 'content': SYS_PROMPT},
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
            if e.code in (401, 403):
                return None, False   # 权限/额度问题，直接降级
            time.sleep(2)
        except Exception as e:
            print(f'[warn] GitHub Models 调用失败: {type(e).__name__}')
            time.sleep(2)
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


def main():
    now = datetime.datetime.now()
    print(f'\n[ai_daily] ===== {now:%Y-%m-%d %H:%M:%S} =====')
    if not is_trading_day(now):
        print('[skip] 非交易日，保留上一交易日 AI 复盘')
        return 0

    d = collect()
    with open(DATA_OUT, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f'[ok] 已写入 {DATA_OUT}')

    user_text = build_user_prompt(d)
    report, ok = call_github_models(user_text)
    src = 'GitHub Models'
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
