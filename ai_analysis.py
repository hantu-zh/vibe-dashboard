# -*- coding: utf-8 -*-
"""
ai_analysis.py — 重新生成 AI 市场复盘页面
========================================
读取:
  ai_analysis_data.json  (结构化数据: 指数/强势股/板块/慢热)
  ai_analysis_report.json (AI 撰写的 Markdown 复盘报告)
生成:
  ai_analysis.html

用法:
  python ai_analysis.py --serve
"""
import sys, os, json, re, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_JSON = BASE_DIR / 'ai_analysis_data.json'
REPORT_JSON = BASE_DIR / 'ai_analysis_report.json'
SLOW_JSON = BASE_DIR / 'slowrise_stocks.json'
TREND_JSON = BASE_DIR / 'vibe_trend_history.json'
BOARD_KLINE_JSON = BASE_DIR / 'ai_analysis_board_kline.json'   # 板块日K缓存（ai_daily.py 采集）
OUTPUT_HTML = BASE_DIR / 'ai_analysis.html'


def md_to_html(md):
    """极简 Markdown -> HTML（标题/加粗/换行）"""
    if not md:
        return ''
    lines = md.split('\n')
    out = []
    for ln in lines:
        ln = ln.rstrip()
        if ln.startswith('## '):
            out.append(f'<h3>{ln[3:]}</h3>')
        elif ln.startswith('# '):
            out.append(f'<h2>{ln[2:]}</h2>')
        else:
            ln = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', ln)
            out.append(ln if ln == '' else ln)
    text = '<br>'.join(out)
    # 空行分段
    text = re.sub(r'(<br>){2,}', '</p><p>', text)
    return '<p>' + text + '</p>'


def idx_class(pct):
    if pct > 0:
        return 'up'
    if pct < 0:
        return 'down'
    return 'flat'


def arrow(pct):
    if pct > 0:
        return '▲'
    if pct < 0:
        return '▼'
    return '-'


BOARD_CODES = {
    '家具制造业': 'BK0440',
    '木材加工业': 'BK0476',
    '通信线缆及配套': 'BK1592',
    '玻纤制造': 'BK1462',
    '地面兵装Ⅲ': 'BK1382',
    '地面兵装Ⅱ': 'BK1229',
    '其他数字媒体': 'BK1295',
    '印制电路板': 'BK1340',
    '文字媒体': 'BK1298',
    '玻璃玻纤': 'BK0546',
    '元件': 'BK0459',
    '彩电': 'BK1453',
    '被动元件': 'BK1339',
    '玻璃制造': 'BK1461',
    '光伏发电': 'BK1375',
    '水力发电': 'BK1380',
    '其他通信设备': 'BK1590',
    '橡胶助剂': 'BK1443',
    '影视动漫制作': 'BK1299',
    '电信运营商': 'BK1587',
    '纺织鞋类制造': 'BK1347',
    '影视院线': 'BK1222',
    '通信设备': 'BK0448',
    '国有大型银行Ⅲ': 'BK1611',
    '空调': 'BK1450',
    '风电整机': 'BK1314',
    '胶黏剂及胶带': 'BK1425',
    '视频媒体': 'BK1296',
    '风电设备': 'BK1032',
    '国防军工': 'BK1204',
    '风电零部件': 'BK1313',
    '通信网络设备及器件': 'BK1591',
    '军工电子Ⅲ': 'BK1386',
    '军工电子Ⅱ': 'BK1233',
    '综合乘用车': 'BK1520',
    '通信': 'BK1215',
    '黑色家电': 'BK1241',
    '体育Ⅲ': 'BK1564',
    '体育Ⅱ': 'BK1273',
    '数字媒体': 'BK1221',
    '核力发电': 'BK1376',
    '航空装备Ⅲ': 'BK1384',
    '航空装备Ⅱ': 'BK1231',
    '膜材料': 'BK1439',
    '电视广播Ⅲ': 'BK1291',
    '电视广播Ⅱ': 'BK1219',
    '电力': 'BK0428',
    '乘用车': 'BK1262',
    '教育出版': 'BK1290',
    '线缆部件及其他': 'BK1312',
    '风力发电': 'BK1374',
    '股份制银行Ⅲ': 'BK1610',
    '出版': 'BK1218',
    '快递': 'BK1489',
    '传媒': 'BK0486',
    '广告媒体': 'BK1292',
    '大众出版': 'BK1289',
    '清洁小家电': 'BK1459',
    '航天装备Ⅲ': 'BK1385',
    '航天装备Ⅱ': 'BK1232',
    '纺织服装设备': 'BK1401',
    '跨境电商': 'BK1547',
    '教育运营及其他': 'BK1556',
    '白色家电': 'BK1239',
    '医美服务': 'BK1499',
    '集成电路制造': 'BK1329',
    '粮食种植': 'BK1515',
    '其他电子Ⅲ': 'BK1336',
    '其他电子Ⅱ': 'BK1223',
    '公用事业': 'BK0427',
    '国际工程': 'BK1475',
    '火力发电': 'BK1377',
    '热力服务': 'BK1379',
    '广告营销': 'BK1220',
    '营销代理': 'BK1293',
    '院线': 'BK1300',
    '银行': 'BK1283',
    '银行Ⅱ': 'BK0475',
    '建筑材料': 'BK1208',
    '电子': 'BK1201',
    '厨房小家电': 'BK1457',
    '厨房电器': 'BK1451',
    '特钢Ⅲ': 'BK1370',
    '特钢Ⅱ': 'BK1227',
    '硅料硅片': 'BK1319',
    '高速公路': 'BK1483',
    '小家电': 'BK1244',
    '化学工程': 'BK1476',
    '火电设备': 'BK1321',
    '配电设备': 'BK1310',
    '学历教育': 'BK1558',
    '城商行Ⅲ': 'BK1609',
    '熟食': 'BK1584',
    '电动乘用车': 'BK1519',
    '油品石化贸易': 'BK1571',
    '通信服务': 'BK0736',
    '通信应用增值服务': 'BK1589',
    '其他饰品': 'BK1356',
    '其他金属新材料': 'BK1619',
    '电能综合服务': 'BK1373',
    '门户网站': 'BK1294',
    '教育': 'BK0740',
    # ── 新浪行业板块兜底（ZCxx 节点）→ 东财行业板块 BK 映射 ──
    # 新浪行业名与东财行业板块口径不完全一致，这里选成分最接近的代表性板块，
    # 仅用于「行业板块涨跌」行的板块K线弹窗（data-kline-sym）。
    # 经东财 searchapi suggest 逐一验证（2026-10-01）；查不到的行业不留条目，
    # 该行 data-kline-sym 为空、点击不弹K线，绝不拿模糊猜测凑数。
    '农副食品加工业': 'BK1280',   # 食品加工
    '食品制造业': 'BK1282',       # 饮料乳品
    '酒饮料茶制造业': 'BK1277',   # 白酒Ⅱ
    '纺织业': 'BK1224',           # 纺织制造
    '纺织服装服饰业': 'BK1225',   # 服装家纺
    '皮革毛皮制鞋业': 'BK1354',   # 鞋帽及其他
    '造纸纸制品业': 'BK1267',     # 造纸
    '印刷媒介复制业': 'BK1265',   # 包装印刷
    '石油炼焦加工业': 'BK1274',   # 炼化及贸易
    '化学原料制品业': 'BK1019',   # 化学原料
    '医药制造业': 'BK0465',       # 化学制药
    '化学纤维制造业': 'BK0471',   # 化学纤维
    '橡胶塑料制品业': 'BK0454',   # 塑料
    '非金属矿物制品业': 'BK0424', # 水泥
    '黑色金属冶炼业': 'BK0479',   # 钢铁
    '有色金属冶炼业': 'BK0478',   # 有色金属
    '金属制品业': 'BK1396',       # 金属制品
    '通用设备制造业': 'BK0545',   # 通用设备
    '专用设备制造业': 'BK0910',   # 专用设备
    '汽车制造业': 'BK1211',       # 汽车
    '铁路船舶航天业': 'BK0480',   # 航天航空
    '电气机械器材业': 'BK0457',   # 电网设备
    '计算机电子设备业': 'BK0459', # 元件
    '仪器仪表制造业': 'BK0458',   # 仪器仪表
    '废弃资源利用业': 'BK0728',   # 环保
}


IDX_SYM = {
    '上证指数': 'sh000001', '上证综指': 'sh000001',
    '深证成指': 'sz399001',
    '创业板指': 'sz399006',
    '沪深300': 'sh000300',
    '科创50': 'sh000688',
    '上证50': 'sh000016',
    '中证500': 'sh000905',
    '中证1000': 'sz399852',
}


def slow_picks_html(slow_picks, slow_meta):
    """慢热板块潜力股表格（板块标签下方）。行带 data-kline-code 以接入 K 线弹窗。"""
    if not slow_picks:
        return ''
    rows = []
    for s in slow_picks:
        chg = s.get('chg')
        if isinstance(chg, (int, float)) and chg != 0:
            cls = 'up' if chg > 0 else 'down'
            chg_td = f'<td class="{cls}">{chg:+.2f}%</td>'
        else:
            chg_td = '<td class="flat">-</td>'
        rows.append(
            f'<tr data-kline-code="{s.get("code", "")}"><td class="stock-name">{s.get("name", "")}</td>'
            f'<td class="sym stock-code">{s.get("code", "")}</td>{chg_td}'
            f'<td class="sym">{s.get("board", "")}</td>'
            f'<td class="sym">{s.get("reason", "")}</td></tr>')
    return (
        f'<div class="slowrise-date" style="margin-top:12px">🎯 潜力个股（{slow_meta}）</div>'
        f'<table><thead><tr><th>名称</th><th>代码</th><th>涨跌幅</th><th>所属板块</th><th>入选理由</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>')


# 慢热板块跟踪的板块名 -> 东财 BK 代码（来自 vibe_trend_history.json 最新日期）。
# 该文件由 update_slowrise / refresh_slowrise 维护，每个热度板块都带 BK code，
# 让慢热板块标签也能直接弹 K 线（同源缓存 ai_analysis_board_kline.json 优先）。
_SLOWRISE_CODE_CACHE = None


def _slowrise_code_map():
    global _SLOWRISE_CODE_CACHE
    if _SLOWRISE_CODE_CACHE is not None:
        return _SLOWRISE_CODE_CACHE
    m = {}
    try:
        if TREND_JSON.exists():
            t = json.loads(TREND_JSON.read_text('utf-8'))
            days = [k for k in t
                     if isinstance(k, str) and len(k) == 10 and k[4] == '-' and k[7] == '-'
                     and k[:4].isdigit() and k[5:7].isdigit() and k[8:10].isdigit()]
            if days:
                day = t[max(days)]
                for n, v in day.items():
                    if isinstance(v, dict):
                        c = v.get('code') or ''
                        if str(c).upper().startswith('BK'):
                            m[n] = str(c).lower()
    except Exception:
        pass
    _SLOWRISE_CODE_CACHE = m
    return m


def build_board_rps():
    """各板块的多周期 RPS，内联给「市场热力图」按 RPS60 上色。

    算法在 board_rps.py（固定板块池 + 横截面分位，分母恒定）。
    基准日取「覆盖率足够高的最近交易日」——板块K线是增量抓取，最新一天常常只有
    一半板块有数据，直接用会让分母腰斩、RPS 失真。
    """
    try:
        if not BOARD_KLINE_JSON.exists():
            print('[warn] ai_analysis_board_kline.json 不存在，跳过 RPS 上色')
            return None
        import os as _os
        import sys as _sys
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import board_rps as br

        raw = json.loads(BOARD_KLINE_JSON.read_text('utf-8'))
        stocks = raw.get('stocks') or {}
        if not stocks:
            return None

        close_map, name_map = br.build_series(stocks)
        pool = br.dedup_pool(name_map)
        if not pool:
            return None

        dates = br.all_dates(close_map)
        asof = dates[-1]
        for d in reversed(dates[-15:]):
            cov = sum(1 for c in pool if d in close_map.get(c, {}))
            if cov >= 0.7 * len(pool):
                asof = d
                break

        rows, info = br.compute(close_map, name_map, asof=asof, pool=pool)
        boards = {}
        for r in rows:
            boards[str(r['code']).lower()] = {
                'n': r['disp_name'],
                'r5': r['rps5'], 'r20': r['rps20'], 'r60': r['rps60'], 'r120': r['rps120'],
                'f': r['fresh'],
            }
        print(f'[ai_analysis] 板块 RPS 内联: 基准 {info["asof"]} · 固定池 {info["universe"]} 个板块'
              f'（当日真实覆盖 {info["coverage"]:.0%}）')
        return {'asof': info['asof'], 'universe': info['universe'], 'boards': boards}
    except Exception as e:
        print(f'[warn] 生成板块 RPS 失败: {type(e).__name__}: {e}')
        return None


def build_rotation(win_days=30, keep=12):
    """从板块日K缓存裁剪出「板块轮动矩阵」数据（内联进 HTML，前端零额外请求）。

    纵轴 = 按近 win_days 个交易日累计涨幅取最强 keep 个 + 最弱 keep 个板块；
    横轴 = 最近 win_days 个交易日；单元格 = 当日涨跌幅(%)。
    缓存行格式 [date, open, close, low, high, volume]，故涨跌幅 = close/前收-1。
    """
    try:
        if not BOARD_KLINE_JSON.exists():
            print('[warn] ai_analysis_board_kline.json 不存在，跳过板块轮动矩阵')
            return None
        raw = json.loads(BOARD_KLINE_JSON.read_text('utf-8'))
        stocks = raw.get('stocks') or {}

        series = {}          # lower_code -> {'name':.., 'pct':{date:pct}, 'close':{date:close}}
        for code, v in stocks.items():
            kl = (v or {}).get('kline') or []
            if len(kl) < 3:
                continue
            bars = []
            for bar in kl:
                if len(bar) >= 3:
                    try:
                        bars.append((str(bar[0]), float(bar[2])))
                    except (TypeError, ValueError):
                        pass
            if len(bars) < 3:
                continue
            pct, close = {}, {}
            for i in range(1, len(bars)):
                d, c = bars[i]
                pc = bars[i - 1][1]
                close[d] = c
                if pc:
                    pct[d] = round((c / pc - 1) * 100, 2)
            if pct:
                series[str(code).lower()] = {'name': (v.get('name') or str(code)), 'pct': pct, 'close': close}

        if not series:
            return None

        all_dates = sorted({d for s in series.values() for d in s['pct']})[-win_days:]
        if len(all_dates) < 5:
            return None

        rows = []
        for code, s in series.items():
            vals = [s['close'][d] for d in all_dates if d in s['close']]
            if len(vals) < 3 or not vals[0]:
                continue
            rows.append({
                'code': code,
                'name': s['name'],
                'pct': [s['pct'].get(d) for d in all_dates],   # 与 all_dates 对齐，缺失为 None
                'cum': round((vals[-1] / vals[0] - 1) * 100, 2),
            })

        if len(rows) < 4:
            return None

        rows.sort(key=lambda r: r['cum'], reverse=True)
        sel = rows[:keep] + rows[-keep:]          # 上强下弱

        print(f'[ai_analysis] 板块轮动矩阵: {len(sel)}/{len(rows)} 个板块 × {len(all_dates)} 个交易日 '
              f'({all_dates[0]} → {all_dates[-1]}), 最强 {sel[0]["name"]} {sel[0]["cum"]:+.2f}%, '
              f'最弱 {sel[-1]["name"]} {sel[-1]["cum"]:+.2f}%')
        return {
            'updated': all_dates[-1],
            'from': all_dates[0],
            'dates': all_dates,
            'rows': sel,
            'total': len(rows),
        }
    except Exception as e:
        print(f'[warn] 生成板块轮动矩阵失败: {type(e).__name__}: {e}')
        return None


def _build_sentiment(data):
    """简化版恐贪指数：仅用当前已抓取的 指数 + 板块 数据做广度代理。
    完整 5 因子（沪深300 60日趋势/成交额分位/沪市涨跌家数/涨停家数/连板高度）
    需接入历史与广度源，见 sentiment_compute.py。"""
    try:
        d = data.get('data', {}) if isinstance(data, dict) else {}
        indices = d.get('indices', []) or []
        sectors = d.get('sectors', []) or []
        if not indices:
            return None
        idx_pcts = []
        for i in indices:
            try:
                idx_pcts.append(float(i.get('pct', 0) or 0))
            except Exception:
                pass
        avg_idx = (sum(idx_pcts) / len(idx_pcts)) if idx_pcts else 0.0
        hs = None
        for i in indices:
            if i.get('name') == '沪深300':
                try:
                    hs = float(i.get('pct', 0) or 0)
                except Exception:
                    hs = None
                break
        if hs is None:
            hs = avg_idx
        idx_score = max(0.0, min(100.0, 50 + avg_idx * 9))
        up = sum(1 for s in sectors if float(s.get('pct', 0) or 0) > 0)
        breadth = (up / len(sectors) * 100.0) if sectors else 50.0
        hs_score = max(0.0, min(100.0, 50 + hs * 9))
        value = round(0.5 * idx_score + 0.3 * breadth + 0.2 * hs_score)
        factors = [
            {'name': '指数整体强度', 'score': round(idx_score), 'note': '今日指数均值'},
            {'name': '板块上涨占比', 'score': round(breadth), 'note': '上涨板块/全部板块'},
            {'name': '沪深300当日', 'score': round(hs_score), 'note': '沪深300当日涨跌幅'},
        ]
        return {'value': value, 'factors': factors,
                'note': '基于今日指数+板块广度合成（简化版）；完整5因子历史/广度源待接入'}
    except Exception:
        return None

def build_html(data, report, slow_picks=None, slow_meta=''):
    CSS_EXTRA = r'''
  /* —— quick-win ①：表格数值列右对齐 + 等宽数字 —— */
  .num { text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
  /* —— quick-win ④：AI 报告涨跌幅红绿 + 关键词胶囊 —— */
  .kw { display: inline-block; padding: 0 5px; margin: 0 1px; border-radius: 6px; background: rgba(184,41,255,0.22); color: #e9c9ff; font-weight: 600; }
  .rpt-up { color: #ef5350; font-weight: 600; }
  .rpt-down { color: #26c281; font-weight: 600; }
  /* —— quick-win ⑤：A股情绪恐贪 Gauge（真实广度合成） —— */
  .sg-wrap { display: flex; gap: 28px; align-items: flex-start; flex-wrap: wrap; }
  .sg-gauge { flex: 0 0 300px; max-width: 100%; }
  .sg-factors { flex: 1 1 340px; min-width: 300px; }
  .sg-title { border-bottom: none; padding-bottom: 0; border-left: 3px solid #22d3ee; padding-left: 10px; }
  .sg-cap { font-size: .74em; color: #7a8493; margin: 7px 0 14px; padding-left: 13px; line-height: 1.6; }
  .sg-card { background: rgba(255,255,255,.045); border: 1px solid rgba(255,255,255,.05); border-radius: 12px; padding: 11px 15px; margin-bottom: 10px; display: flex; align-items: center; gap: 14px; }
  .sg-card:last-of-type { margin-bottom: 0; }
  .sg-l { flex: 0 0 122px; }
  .sg-nm { font-size: .96em; font-weight: 700; color: #f0f3f8; }
  .sg-sub { font-size: .72em; color: #7a8493; margin-top: 3px; }
  .sg-bar2 { position: relative; flex: 1; height: 14px; border-radius: 7px; background: linear-gradient(90deg, #22d3ee 0%, #ec4899 100%); }
  .sg-dot { position: absolute; top: 50%; width: 12px; height: 12px; border-radius: 50%; background: #fff; border: 2px solid rgba(0,0,0,.3); transform: translate(-50%, -50%); box-shadow: 0 0 5px rgba(255,255,255,.7); }
  .sg-val { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
  .sg-num { font-size: 1.2em; font-weight: 700; }
  .sg-badge { font-size: .68em; font-weight: 600; padding: 2px 8px; border-radius: 999px; background: #f59e0b; color: #1a1d24; white-space: nowrap; }
  .sg-note { font-size: .74em; color: #7a8493; margin-top: 10px; line-height: 1.6; }
  /* —— 六档仓位刻度（越便宜越重） —— */
  .sg-tiers { width: 100%; margin-top: 6px; }
  .sg-tiers-hd { font-size: .84em; color: #c9d1e0; margin: 12px 0 16px; padding-left: 13px; line-height: 1.7; }
  .sg-tiers-hd b { color: #f59e0b; }
  .sg-tier-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }
  @media (max-width: 860px) { .sg-tier-grid { grid-template-columns: repeat(3, 1fr); } }
  .sg-tier { position: relative; border-radius: 12px; padding: 13px 6px 11px; text-align: center; border: 1px solid; }
  .sg-tier .t-nm { font-size: 1.02em; font-weight: 700; letter-spacing: .06em; }
  .sg-tier .t-pct { font-size: 1.3em; font-weight: 700; margin: 5px 0 4px; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
  .sg-tier .t-rg { font-size: .72em; opacity: .88; line-height: 1.5; }
  .sg-tier.cur { border-color: rgba(255,255,255,.85); box-shadow: 0 0 16px rgba(255,255,255,.16); transform: translateY(-3px); }
  .sg-cur-badge { position: absolute; top: -11px; left: 50%; transform: translateX(-50%); background: #fff; color: #14161c; font-size: .7em; font-weight: 700; padding: 1px 10px; border-radius: 999px; white-space: nowrap; }
  .sg-tiers-note { font-size: .72em; color: #7a8493; margin-top: 12px; padding-left: 13px; line-height: 1.6; }
'''
    GAVAGE_PANEL = r'''    <div class="panel full">
      <div class="panel-title sg-title">🌡️ 市场情绪 · 恐贪指数 <span style="font-size:.72em;font-weight:400;color:#8b95a5">基于今日真实数据合成 · 0=极度恐惧 → 100=极度贪婪</span></div>
      <div id="sentimentGauge" class="sg-wrap"></div>
      <div id="sentimentTiers" class="sg-tiers"></div>
      <div class="sg-cap">基于今日指数+板块广度合成（简化版 · 非第三方加密指数）· <span style="color:#4CAF3E">0=极度恐惧</span> → <span style="color:#E53935">100=极度贪婪</span></div>
    </div>
'''
    NUMALIGN_SCRIPT = r'''<script>
(function () {
  var numCols = ['代码', '现价', '涨跌幅', '换手率', '成交额'];
  document.querySelectorAll('table').forEach(function (tb) {
    var ths = [].slice.call(tb.querySelectorAll('thead th'));
    var idx = [];
    ths.forEach(function (th, i) { var tx = th.textContent.trim(); if (numCols.some(function (n) { return tx.indexOf(n) === 0; })) idx.push(i); });
    if (!idx.length) return;
    tb.querySelectorAll('tr').forEach(function (tr) {
      [].slice.call(tr.children).forEach(function (cell, i) {
        if (idx.indexOf(i) >= 0) cell.classList.add('num');
      });
    });
  });
})();
</script>'''
    REPORT_SCRIPT = r'''<script>
(function () {
  var el = document.querySelector('.report'); if (!el) return;
  var html = el.innerHTML;
  html = html.replace(/(\+?-?\d+(?:\.\d+)?%)/g, function (m) {
    var cls = m.charAt(0) === '-' ? 'rpt-down' : 'rpt-up';
    return '<span class="' + cls + '">' + m + '</span>';
  });
  var kws = ['领涨', '涨停', '强买', '资金', '情绪', '风险', '主线', '连板', '北向', '贪婪', '恐慌'];
  kws.forEach(function (k) {
    var re = new RegExp('(' + k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'g');
    html = html.replace(re, '<span class="kw">$1</span>');
  });
  el.innerHTML = html;
})();
</script>'''
    GAUGE_SCRIPT = r'''<script>
(function () {
  var box = document.getElementById('sentimentGauge');
  if (!box) return;
  var d = window.__SENTIMENT__;
  if (!d) { box.innerHTML = '<div class="sg-note">暂无情绪数据</div>'; return; }
  var cx = 150, cy = 150, R = 120;
  function polar(val) { var t = (180 - val * 1.8) * Math.PI / 180; return { x: cx + R * Math.cos(t), y: cy - R * Math.sin(t) }; }
  function zone(v) {
    if (v < 10) return { t: '极度恐惧', c: '#22d3ee' };
    if (v < 35) return { t: '恐惧', c: '#38bdf8' };
    if (v <= 65) return { t: '中性', c: '#94a3b8' };
    if (v < 90) return { t: '贪婪', c: '#f472b6' };
    return { t: '极度贪婪', c: '#ec4899' };
  }
  // 半圆 Gauge 沿用经典恐贪配色（绿→黄→橙→红，见 gauge_bg.png），与下方卡片的青/品红区分
  function gzone(v) {
    if (v < 25) return { t: '极度恐惧', c: '#4CAF3E' };
    if (v < 50) return { t: '恐惧', c: '#FFB300' };
    if (v < 75) return { t: '贪婪', c: '#FB8C00' };
    return { t: '极度贪婪', c: '#E53935' };
  }
  var z = gzone(d.value), n = polar(d.value);
  var svg = '<svg viewBox="0 0 300 178" width="100%" style="max-width:300px">'
    + '<defs><linearGradient id="sgGrad" x1="0" y1="0" x2="300" y2="0" gradientUnits="userSpaceOnUse">'
    + '<stop offset="0" stop-color="#4CAF3E"/><stop offset="0.34" stop-color="#FFB300"/><stop offset="0.67" stop-color="#FB8C00"/><stop offset="1" stop-color="#E53935"/></linearGradient></defs>'
    + '<path d="M ' + (cx - R) + ' ' + cy + ' A ' + R + ' ' + R + ' 0 0 1 ' + (cx + R) + ' ' + cy + '" fill="none" stroke="url(#sgGrad)" stroke-width="18" stroke-linecap="round"/>'
    + '<line x1="' + cx + '" y1="' + cy + '" x2="' + n.x.toFixed(1) + '" y2="' + n.y.toFixed(1) + '" stroke="' + z.c + '" stroke-width="3.5"/>'
    + '<circle cx="' + cx + '" cy="' + cy + '" r="6" fill="' + z.c + '"/>'
    + '<text x="' + cx + '" y="' + (cy - 36) + '" text-anchor="middle" font-size="34" font-weight="700" fill="' + z.c + '">' + d.value + '</text>'
    + '<text x="' + cx + '" y="' + (cy - 14) + '" text-anchor="middle" font-size="13" fill="#c9d1e0">' + z.t + '</text>'
    + '<text x="' + (cx - R + 6) + '" y="' + (cy + 18) + '" text-anchor="middle" font-size="10" fill="#4CAF3E">极度恐惧</text>'
    + '<text x="' + (cx + R - 6) + '" y="' + (cy + 18) + '" text-anchor="middle" font-size="10" fill="#E53935">极度贪婪</text>'
    + '</svg>';
  var cards = '';
  (d.factors || []).forEach(function (f) {
    var fz = zone(f.score);
    cards += '<div class="sg-card"><div class="sg-l"><div class="sg-nm">' + f.name + '</div><div class="sg-sub">' + (f.note || '') + '</div></div>'
      + '<div class="sg-bar2"><span class="sg-dot" style="left:' + f.score + '%"></span></div>'
      + '<div class="sg-val"><span class="sg-num" style="color:' + fz.c + '">' + f.score + '</span>'
      + '<span class="sg-badge" style="background:' + fz.c + '">' + fz.t + '</span></div></div>';
  });
  box.innerHTML = '<div class="sg-gauge">' + svg + '</div><div class="sg-factors">' + cards + '<div class="sg-note">' + (d.note || '') + '</div></div>';

  // —— 六档仓位刻度（越便宜越重）：恐贪值当作 0-100 分位口径 ——
  var tbox = document.getElementById('sentimentTiers');
  if (!tbox) return;
  var tiers = [
    { nm: '满仓', pct: '100%', rg: '≤10 分位', th: 10, c: '#2e9e5b', bg: 'rgba(46,158,91,.16)', tip: '越跌越买' },
    { nm: '八成', pct: '80%', rg: '10~30 分位', th: 30, c: '#4CAF3E', bg: 'rgba(76,175,62,.16)', tip: '可分批加仓' },
    { nm: '四成', pct: '40%', rg: '30~50 分位', th: 50, c: '#c9b03a', bg: 'rgba(201,176,58,.14)', tip: '持有观察' },
    { nm: '一成', pct: '10%', rg: '50~70 分位', th: 70, c: '#f59e0b', bg: 'rgba(245,158,11,.14)', tip: '轻仓试错' },
    { nm: '半成', pct: '5%', rg: '70~90 分位', th: 90, c: '#d1543a', bg: 'rgba(209,84,58,.16)', tip: '管住手，不追高' },
    { nm: '清仓', pct: '0%', rg: '>90 分位', th: 1e9, c: '#e53935', bg: 'rgba(229,57,53,.16)', tip: '落袋为安，等情绪回落' }
  ];
  var v = d.value, cur = 0;
  for (var i = 0; i < tiers.length; i++) { if (v <= tiers[i].th) { cur = i; break; } }
  var cells = '';
  tiers.forEach(function (t, i2) {
    var isCur = i2 === cur;
    cells += '<div class="sg-tier' + (isCur ? ' cur' : '') + '" style="background:' + t.bg + ';border-color:' + (isCur ? 'rgba(255,255,255,.85)' : t.c) + '">'
      + (isCur ? '<span class="sg-cur-badge">当前</span>' : '')
      + '<div class="t-nm" style="color:' + t.c + '">' + t.nm + '</div>'
      + '<div class="t-pct" style="color:' + t.c + '">' + t.pct + '</div>'
      + '<div class="t-rg" style="color:' + t.c + '">' + t.rg + '<br>' + t.tip + '</div>'
      + '</div>';
  });
  tbox.innerHTML = '<div class="sg-tiers-hd">六档仓位刻度（越便宜越重）· 当前恐贪 <b>' + v + '</b> · 对应档位：<b>' + tiers[cur].nm + '</b>（建议仓位上限 ' + tiers[cur].pct + '，' + tiers[cur].tip + '）</div>'
    + '<div class="sg-tier-grid">' + cells + '</div>'
    + '<div class="sg-tiers-note">六档 = 六个仓位档位（绿→红：越便宜越重）；白色高亮卡 = 当前所处档位。档位按恐贪值（0-100 分位口径）划分：情绪越冷越重仓、越热越轻仓，为示意刻度，非投资建议。</div>';
})();
</script>'''
    ts = data.get('timestamp', '')[:19].replace('T', ' ')
    date_str = ts[:10] if ts else ''
    indices = data.get('data', {}).get('indices', [])
    strong = data.get('data', {}).get('strong_stocks', [])
    yimeng = data.get('data', {}).get('yimeng_stocks', [])
    sectors = data.get('data', {}).get('sectors', [])
    slowrise = data.get('data', {}).get('slowrise', [])

    idx_cards = ''
    for i in indices:
        sym = IDX_SYM.get(i.get('name', ''), '')
        cls = 'idx-card kl-idx' if sym else 'idx-card'
        dsym = f' data-kline-sym="{sym}"' if sym else ''
        ncls = 'idx-name stock-name' if sym else 'idx-name'
        idx_cards += (
            f'<div class="{cls}"{dsym}><div class="{ncls}">{i["name"]}</div>'
            f'<div class="idx-price">{i["current"]}</div>'
            f'<div class="idx-chg {idx_class(i["pct"])}">{arrow(i["pct"])} {i["pct"]:+.2f}%</div></div>'
        )

    def stock_rows(stocks, limit=10):
        rows = []
        for s in stocks[:limit]:
            chg = s.get('change_pct', 0)
            rows.append(
                f'<tr data-kline-code="{s.get("code","")}"><td class="stock-name">{s.get("name","")}</td><td class="sym stock-code">{s.get("code","")}</td>'
                f'<td>{s.get("price","")}</td>'
                f'<td class="{"up" if chg>0 else "down" if chg<0 else "flat"}">{chg:+.2f}%</td>'
                f'<td>{s.get("turnover",0)}%</td></tr>'
            )
        return '\n'.join(rows) if rows else '<tr><td colspan="5" class="sym">暂无数据</td></tr>'

    def _sector_sym(s):
        """行业板块行的K线符号：优先按板块名查 BK 映射（新浪兜底数据的 code 是
        ZCxx 新浪节点，弹窗数据源不认识），非 BK 代码一律丢弃。"""
        code = BOARD_CODES.get(s.get('name', '')) or (s.get('code') or '')
        return code.lower() if code.upper().startswith('BK') else ''

    sector_rows = ''.join(
        f'<tr data-kline-sym="{_sector_sym(s)}"><td class="stock-name">{s.get("name","")}</td><td class="{"up" if s.get("pct",0)>0 else "down"}">{s.get("pct",0):+.2f}%</td>'
        f'<td>{s.get("amount_yi",0)}亿</td></tr>'
        for s in sectors
    ) or '<tr><td colspan="3" class="sym">暂无数据</td></tr>'

    cm = _slowrise_code_map()
    slow_tags = ''.join(
        (f'<span class="board-tag" data-kline-sym="{cm.get(s.get("name",""),"")}"'
         f' data-kline-name="{s.get("name","")}">{s.get("name","")}</span>'
         if cm.get(s.get("name", "")) else
         f'<span class="board-tag">{s.get("name","")}</span>')
        for s in slowrise
    ) or '<div style="color:#555">暂无数据</div>'
    picks_html = slow_picks_html(slow_picks or [], slow_meta)

    report_html = md_to_html(report)

    # ── 板块轮动矩阵（板块 × 交易日）────────────────────────────────
    # 数据从板块日K缓存裁剪后内联，前端零额外请求；无数据则整块面板不输出。
    # ── 板块 RPS（内联给热力图上色：板块方块颜色 = RPS60 分位）──────
    board_rps_data = build_board_rps()
    if board_rps_data:
        rps_script = (
            '<script>window.__BOARD_RPS__ = '
            + json.dumps(board_rps_data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
            + ';</script>\n'
        )
    else:
        rps_script = ''

    rot = build_rotation()
    if rot:
        rot_panel = (
            '    <div class="panel full">\n'
            '      <div class="panel-title">🌀 板块轮动矩阵</div>\n'
            '      <div id="marketRotation"></div>\n'
            '    </div>\n'
        )
        rot_script = (
            '<script>window.__MARKET_ROTATION__ = '
            + json.dumps(rot, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
            + ';</script>\n'
        )
    else:
        rot_panel = ''
        rot_script = ''

    # ── 市场情绪恐贪指数（简化真实合成）────────────────────────────
    sent = _build_sentiment(data)
    if sent:
        sent_script = (
            '<script>window.__SENTIMENT__ = '
            + json.dumps(sent, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
            + ';</script>\n'
        )
    else:
        sent_script = ''

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI市场复盘 | 方瑟</title>
<style>
  :root {{ --neon-purple: #b829ff; --neon-cyan: #00fff2; --dark-bg: #0a0a0f; --card-bg: rgba(15,15,25,0.85); }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif; background: var(--dark-bg); color: #e0e0e0; min-height: 100vh; padding: 20px; }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  header {{ text-align: center; margin-bottom: 24px; padding: 20px; background: linear-gradient(135deg, rgba(183,28,211,0.15), rgba(0,255,242,0.08)); border-radius: 16px; border: 1px solid rgba(183,28,211,0.2); }}
  h1 {{ font-size: 1.8em; background: linear-gradient(135deg, #ce93d8, #00fff2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  .meta {{ color: #888; font-size: 0.85em; margin-top: 8px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
  @media(max-width:900px){{ .grid{{grid-template-columns:1fr}} }}
  .panel {{ background: var(--card-bg); border-radius: 16px; padding: 18px; border: 1px solid rgba(255,255,255,0.06); }}
  .panel-title {{ font-size: 1em; font-weight: 600; margin-bottom: 14px; color: #ce93d8; border-bottom: 1px solid rgba(255,255,255,0.06); padding-bottom: 8px; }}
  .report {{ line-height: 1.9; font-size: 0.95em; background: rgba(0,0,0,0.3); padding: 16px; border-radius: 10px; }}
  .report h2 {{ font-size: 1.2em; color: #00fff2; margin: 14px 0 8px; }}
  .report h3 {{ font-size: 1.05em; color: #ce93d8; margin: 14px 0 6px; }}
  .report p {{ margin: 0 0 8px; }}
  .idx-row {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .idx-card {{ background: rgba(255,255,255,0.04); border-radius: 10px; padding: 12px 16px; text-align: center; min-width: 100px; }}
  .idx-name {{ font-size: 0.8em; color: #888; }}
  .idx-price {{ font-size: 1.2em; font-weight: 600; }}
  .idx-chg {{ font-size: 0.85em; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88em; }}
  th,td {{ padding: 7px 10px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  th {{ color: #888; font-weight: 400; }}
  .up {{ color: #ef5350; }}
  .down {{ color: #26c281; }}
  .flat {{ color: #888; }}
  .sym {{ color: #888; font-size: 0.85em; }}
  .slowrise {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .slowrise-entry {{ background: rgba(0,255,242,0.06); border-radius: 10px; padding: 10px 14px; border: 1px solid rgba(0,255,242,0.15); }}
  .slowrise-date {{ font-size: 0.8em; color: #00fff2; margin-bottom: 6px; }}
  .board-tag {{ display: inline-block; background: rgba(184,41,255,0.2); color: #ce93d8; border-radius: 6px; padding: 3px 8px; margin: 2px; font-size: 0.82em; }}
  .board-tag[data-kline-sym] {{ cursor: pointer; transition: background .15s; }}
  .board-tag[data-kline-sym]:hover {{ background: rgba(184,41,255,0.45); color: #fff; }}
  .full {{ grid-column: 1 / -1; }}
  .data-time {{ font-size: 0.75em; color: #555; text-align: right; }}
  .idx-card.kl-idx {{ cursor: pointer; transition: background .15s; }}
  .idx-card.kl-idx:hover {{ background: rgba(0,255,242,0.10); }}
  tr[data-kline-sym] {{ cursor: pointer; }}
  tr[data-kline-sym]:hover td {{ background: rgba(90,130,230,0.09); }}
{CSS_EXTRA}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>📊 AI市场复盘报告</h1>
    <div class="meta">{date_str} | 方瑟 Dashboard</div>
    <div class="data-time">数据时间: {ts}</div>
  </header>

  <div class="grid">
    <div class="panel full">
      <div class="panel-title">📈 A股指数</div>
      <div class="idx-row">{idx_cards}</div>
    </div>

    <div class="panel full">
{GAVAGE_PANEL}      <div class="panel-title">🔥 市场热力图 <span style="font-size:.72em;font-weight:400;color:#8b95a5">方块大小=市值 / 颜色可切 当日涨跌幅 ⇄ RPS60 强弱</span></div>
      <div id="marketHeat"></div>
    </div>

{rot_panel}
    <div class="panel full">
      <div class="panel-title">🧠 AI 复盘报告</div>
      <div class="report">{report_html}</div>
    </div>

    <div class="panel">
      <div class="panel-title">🔥 益盟强买 Top10</div>
      <table><thead><tr><th>名称</th><th>代码</th><th>现价</th><th>涨跌幅</th><th>换手率</th></tr></thead>
      <tbody>{stock_rows(yimeng)}</tbody></table>
    </div>

    <div class="panel">
      <div class="panel-title">🚀 涨幅榜强势股</div>
      <table><thead><tr><th>名称</th><th>代码</th><th>现价</th><th>涨跌幅</th><th>换手率</th></tr></thead>
      <tbody>{stock_rows(strong)}</tbody></table>
    </div>

    <div class="panel">
      <div class="panel-title">📊 行业板块涨跌</div>
      <table><thead><tr><th>板块</th><th>涨跌幅</th><th>成交额(亿)</th></tr></thead>
      <tbody>{sector_rows}</tbody></table>
    </div>

    <div class="panel">
      <div class="panel-title">🌡️ 慢热板块跟踪</div>
      <div class="slowrise">{slow_tags}</div>
      {picks_html}
    </div>
  </div>
</div>
<script>
window.KLINE_CONFIG = {{
  rowSelectors: ['.picks-row', '.stock-item', 'tr[data-kline-code]', 'tr[data-kline-sym]', '[data-kline-row]', '.idx-card', '[data-kline-sym]'],
  triggerSelectors: ['a.picks-link', '.picks-code', 'a.stock-name', '.stock-name', 'a.stock-code', '.stock-code',
    'a[href*="quote.eastmoney.com"]', 'a[href*="finance.sina.com.cn/realstock"]', '.idx-card',
    '[data-kline-sym]', '[data-kline-sym] *']
}};
</script>
<!-- 板块 RPS60 强弱（内联）：市场热力图「颜色=RPS60」口径的数据源 -->
{rps_script}<!-- 市场热力 treemap（板块/个股）：东财→新浪→腾讯 三源自动兜底，点击个股弹K线 -->
<script src="market_heat.js?v=20261025a" defer></script>
<!-- 板块轮动矩阵（板块 × 交易日）：数据内联，无额外网络请求；点击板块名弹K线 -->
{rot_script}<script src="market_rotation.js?v=20261016a" defer></script>
<!-- 通用K线弹窗：点击股票代码/名称/指数卡片查看K线（指数用完整符号） -->
{sent_script}{NUMALIGN_SCRIPT}{REPORT_SCRIPT}{GAUGE_SCRIPT}<script src="kline_popup.js?v=20261025b" defer></script>
</body>
</html>'''


def main():
    # 注：原脚本以 --serve 作为运行开关，但 workflow 以 `python ai_analysis.py` 调用（不带参数），
    # 导致 main() 直接打印用法并退出、页面永不重生成。改为无条件生成（--serve 仍可被接受，无害）。
    data = json.loads(DATA_JSON.read_text('utf-8')) if DATA_JSON.exists() else {'data': {}, 'timestamp': ''}
    report = ''
    if REPORT_JSON.exists():
        rj = json.loads(REPORT_JSON.read_text('utf-8'))
        report = rj.get('report', '')
    slow_picks, slow_meta = [], ''
    if SLOW_JSON.exists():
        try:
            sj = json.loads(SLOW_JSON.read_text('utf-8'))
            slow_picks = sj.get('stocks') or []
            if slow_picks:
                src = sj.get('source', '')
                src_label = 'LLM 荐股' if src == 'llm' else '按板块热度×成分股涨幅'
                slow_meta = f"{sj.get('updated', '')} · {src_label}"
        except Exception as e:
            print(f'[warn] 读取 slowrise_stocks.json 失败: {type(e).__name__}: {e}')
    html = build_html(data, report, slow_picks, slow_meta)
    # newline='\n'：Windows 下 Path.write_text 默认会把 \n 翻成 \r\n，
    # 而仓库（含 CI 的 ubuntu）一直是 LF。显式固定，避免本地跑一次就污染整文件 diff。
    OUTPUT_HTML.write_text(html, encoding='utf-8', newline='\n')
    print(f'[ai_analysis] 生成 {OUTPUT_HTML} ({len(html):,} bytes), date={data.get("timestamp","")[:10]}, 潜力股 {len(slow_picks)} 只')


if __name__ == '__main__':
    main()
