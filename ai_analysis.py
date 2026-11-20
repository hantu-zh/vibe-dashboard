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
VALUATION_JSON = BASE_DIR / 'ai_analysis_valuation.json'       # 板块估值热度（collect_valuation.py 采集）
OUTPUT_HTML = BASE_DIR / 'ai_analysis.html'


def md_to_html(md):
    """Markdown -> 折叠卡片式 HTML（## 标题 = 可展开卡片；风险提示单独红框）。"""
    if not md:
        return ''
    sections = []          # [(标题, 正文行列表)]
    cur_title, cur_lines = None, []
    risk_lines = []
    for ln in md.split('\n'):
        ln = ln.rstrip()
        if ln.startswith('## '):
            if cur_title is not None:
                sections.append((cur_title, cur_lines))
            cur_title, cur_lines = ln[3:].strip(), []
        elif ln.startswith('# '):
            if cur_title is not None:
                sections.append((cur_title, cur_lines))
            cur_title, cur_lines = ln[2:].strip(), []
        else:
            ln = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', ln)
            if ln:
                cur_lines.append(ln)
    if cur_title is not None:
        sections.append((cur_title, cur_lines))

    out = []
    for title, lines in sections:
        body = '<br>'.join(lines)
        if '风险' in title:
            risk_lines.append((title, body))
            continue
        open_attr = ' open' if ('综述' in title or '展望' in title) else ''
        out.append(
            f'<details class="rcard"{open_attr}><summary>{title}</summary>'
            f'<div class="rcard-body">{body}</div></details>'
        )
    html = ''.join(out)
    for title, body in risk_lines:
        html += f'<div class="risk-box"><b>⚠ {title}</b><br>{body}</div>'
    return html


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
    """完整 5 因子恐贪（ai_daily 产出 data['sentiment']，含历史分位）优先；
    缺失时回退旧版 3 因子简化合成（指数+板块广度）。"""
    try:
        s = data.get('sentiment') if isinstance(data, dict) else None
        if isinstance(s, dict) and s.get('factors'):
            return s
    except Exception:
        pass
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
                'note': '简化合成（指数+板块广度）；完整5因子见 ai_daily.collect_sentiment'}
    except Exception:
        return None


VALUATION_SCRIPT = r'''<script>
(function () {
  var DATA = window.__VALUATION__ || [];
  var body = document.getElementById('valBody');
  if (!body || !DATA.length) return;
  var sortKey = 'pe_pct', sortDir = -1, filter = 'all';

  function bar(pct, color) {
    // 固定 0~100 轨道：左右端严格对齐，填充按比例，数值标在轨道右端
    if (pct == null) return '<div style="color:#6f7787">—</div>';
    return '<div class="val-bar"><i style="width:' + pct + '%;background:' + color + '"></i><span class="mid"></span></div>';
  }
  // PB 弱化：纯文字 + 小色块，不占长进度条空间
  function pbText(v, pct, color) {
    if (v == null && pct == null) return '<span class="val-pb"><span class="val-pb-dot" style="background:#5a6270"></span>PB —</span>';
    var dot = '<span class="val-pb-dot" style="background:' + (color || '#5a6270') + '"></span>';
    return '<span class="val-pb">' + dot + 'PB ' + (v != null ? v : '—')
      + (pct != null ? ' <span style="color:#8b93a3">(' + pct.toFixed(0) + '%分位)</span>' : '') + '</span>';
  }
  // 颜色即刻度：>80红 / 60-80橙 / 40-60灰蓝(中性) / 20-40浅绿 / <20深绿
  function barColor(pct) {
if (pct == null) return '#5a6270';
if (pct > 80) return '#d46868';
if (pct > 60) return '#d19a52';
if (pct > 40) return '#7f8ba0';
if (pct > 20) return '#6fb894';
return '#3fa878';
  }
  function render() {
var rows = DATA.slice();
if (filter === 'cheap') rows = rows.filter(function (x) { return x.pe_pct != null && x.pe_pct < 20; });
if (filter === 'rich') rows = rows.filter(function (x) { return x.pe_pct != null && x.pe_pct >= 80; });
rows.sort(function (a, b) {
  var av = a[sortKey], bv = b[sortKey];
  if (typeof av === 'string') return sortDir * av.localeCompare(bv, 'zh');
  if (av == null) return 1;
  if (bv == null) return -1;
  return sortDir * (av - bv);
});
body.innerHTML = rows.map(function (x) {
  var pc = barColor(x.pe_pct), bc = barColor(x.pb_pct);
  var pctTxt = x.pe_pct != null ? '<span class="val-pct" style="color:' + pc + '">' + x.pe_pct.toFixed(0) + '%</span>' : '';
  return '<tr data-val-name="' + x.name + '" title="PE ' + x.pe + ' · 近5年 ' + (x.pe_min != null ? x.pe_min + '~' + x.pe_max : '—') + ' · 样本 ' + x.samples + '个交易日">'
    + '<td class="val-name">' + x.name + '</td>'
    + '<td class="num">' + (x.pe != null ? x.pe : '—') + '</td>'
    + '<td><div class="val-barcell">' + bar(x.pe_pct, pc) + pctTxt + '</div></td>'
    + '<td><div class="val-judge"><span class="val-pill" style="color:' + pc + ';border-color:' + pc + '66;background:' + pc + '1a">' + x.label + '</span>'
    + pbText(x.pb, x.pb_pct, bc) + '</div></td></tr>';
}).join('');
document.getElementById('valCount').textContent = rows.length + ' 个板块';
  }
  document.querySelectorAll('.val-chip').forEach(function (btn) {
btn.addEventListener('click', function () {
  document.querySelectorAll('.val-chip').forEach(function (b) { b.classList.remove('on'); });
  btn.classList.add('on'); filter = btn.dataset.f; render();
});
  });
  document.querySelectorAll('.val-table th[data-s]').forEach(function (th) {
th.addEventListener('click', function () {
  var k = th.dataset.s;
  if (sortKey === k) sortDir = -sortDir;
  else { sortKey = k; sortDir = (k === 'name') ? 1 : -1; }
  render();
});
  });
  render();
  // 联动：热力图点板块 → 高亮估值表对应行（空 name = 取消高亮）
  window.addEventListener('vibe:focusBoard', function (e) {
    var name = ((e.detail && e.detail.name) || '').trim();
    var trs = body.querySelectorAll('tr[data-val-name]');
    var hit = null;
    if (name) {
      trs.forEach(function (tr) {
        var mn = tr.dataset.valName || '';
        if (mn === name || mn.indexOf(name) >= 0 || name.indexOf(mn) >= 0) hit = tr;
      });
    }
    trs.forEach(function (tr) { tr.classList.toggle('val-dim', !!hit && tr !== hit); });
    if (hit) hit.scrollIntoView({ block: 'center', behavior: 'smooth' });
  });
  // 反向联动：hover 估值行 → 广播高亮名，供热力图色块 / 慢热列表项呼应（不改视图钻取状态）
  function emitHover(name) {
    try { window.dispatchEvent(new CustomEvent('vibe:hoverBoard', { detail: { name: name || '' } })); } catch (e) {}
  }
  body.addEventListener('mouseover', function (e) {
    var tr = e.target.closest ? e.target.closest('tr[data-val-name]') : null;
    if (tr) emitHover(tr.dataset.valName);
  });
  body.addEventListener('mouseleave', function () { emitHover(''); });
})();
</script>'''


def build_valuation_panel():
    """板块估值热度面板：PE/PB 近5年分位。数据来自 collect_valuation.py。
    返回 (panel_html, script_html)；无数据时返回 ('', '')。"""
    if not VALUATION_JSON.exists():
        return '', ''
    try:
        vd = json.loads(VALUATION_JSON.read_text('utf-8'))
    except Exception as e:
        print(f'[warn] 读取估值数据失败: {e}')
        return '', ''
    items = vd.get('items') or []
    if not items:
        return '', ''
    data_date = vd.get('date', '')
    # Hero：动态最贵/最便宜
    priced = [x for x in items if x.get('pe_pct') is not None]
    if not priced:
        return '', ''
    dearest = priced[0]          # 已按 pe_pct 降序
    cheapest = priced[-1]
    # 内联数据给前端（排序/筛选/联动）
    payload = json.dumps(items, ensure_ascii=False, separators=(',', ':'))
    panel = f'''    <div class="panel full" id="valuationPanel">
      <div class="panel-title">💎 板块估值热度 <span style="font-size:.72em;font-weight:400;color:#8b95a5">PE/PB 近5年历史分位 · 数据 {data_date} · 东方财富</span></div>
      <div class="val-hero">
        <div class="vh-lead">💡 <b>资金看热度，估值看安全垫。</b> 这里的 PE 分位 =「现在的 PE 在过去 5 年里排第几」。</div>
        <div class="vh-pills">
          <span class="vh-pill vh-hot">🚨 80% 以上 = 比历史绝大多数时候都贵</span>
          <span class="vh-pill vh-cold">🛡️ 20% 以下 = 比历史绝大多数时候都便宜</span>
        </div>
        <div class="vh-concl">目前最贵的是 <b class="hot">{dearest['name']}（{dearest['pe_pct']:.0f}%）</b>，最便宜的是 <b class="cold">{cheapest['name']}（{cheapest['pe_pct']:.0f}%）</b>。分位低 ≠ 马上涨，分位高 ≠ 马上跌——估值给的是历史视角的"赔率"参考，不是买卖信号。</div>
      </div>
      <div class="val-tools">
        <span class="vt-label">筛选：</span>
        <button class="val-chip on" data-f="all">全部</button>
        <button class="val-chip" data-f="cheap">只看低估</button>
        <button class="val-chip" data-f="rich">只看高估</button>
        <span class="vt-label" style="margin-left:auto" id="valCount"></span>
      </div>
      <div class="val-wrap">
        <table class="val-table">
          <thead><tr>
            <th data-s="name">板块<span class="arr">↕</span></th>
            <th data-s="pe">PE(TTM)<span class="arr">↕</span></th>
            <th data-s="pe_pct">PE历史分位<span class="arr">↓</span></th>
            <th data-s="label">估值判断 · PB<span class="arr">↕</span></th>
          </tr></thead>
          <tbody id="valBody"></tbody>
        </table>
      </div>
      <div class="val-note">📖 <b>怎么看？</b> 分位在 80% 以上 = 比历史上绝大多数时候都贵（回调风险大）；20% 以下 = 比历史上绝大多数时候都便宜（有安全垫）。<b>口径</b>：东方财富申万行业 PE(TTM)/PB(MRQ)，近 5 年每个交易日算一次分位，当前值取最新交易日；负 PE（板块整体亏损）不计入分位。估值只看"贵不贵"，资金流向和涨跌趋势请配合上方板块来看。</div>
    </div>'''
    script = (
        '<script>window.__VALUATION__ = ' + payload + ';</script>\n'
        + VALUATION_SCRIPT
    )
    return panel, script


# ── 资金流向板块（东财细分行业）→ 估值板块（申万二级）近似映射 ────────────
# 两套口径并非一一对应：资金流向来自东财 BK 细分行业，估值表来自申万 6 位代码二级行业。
# 匹配顺序：精确名 → 去罗马数字后缀归一化 → 显式别名表。匹配不到就不标（宁缺毋滥），
# 绝不拿模糊猜测凑数；徽章 title 里注明"近似映射·仅供参考"。
_ROMAN_SUFFIX = 'ⅡⅢⅣⅤⅠ'
_FLOW_TO_VAL_ALIAS = {
    '其他数字媒体': '数字媒体', '视频媒体': '数字媒体', '文字媒体': '数字媒体',
    '广告媒体': '广告营销', '营销代理': '广告营销', '广告营销': '广告营销',
    '国有大型银行Ⅲ': '银行Ⅱ', '国有大型银行Ⅱ': '银行Ⅱ', '股份制银行Ⅲ': '银行Ⅱ',
    '城商行Ⅲ': '银行Ⅱ', '银行': '银行Ⅱ', '保险Ⅲ': '保险Ⅱ', '保险': '保险Ⅱ',
    '核力发电': '电力', '水力发电': '电力', '风力发电': '电力', '火力发电': '电力',
    '光伏发电': '电力', '电能综合服务': '电力', '热力服务': '电力',
    '电信运营商': '通信服务', '通信': '通信服务', '通信应用增值服务': '通信服务',
    '其他通信设备': '通信设备', '通信网络设备及器件': '通信设备',
    '通信线缆及配套': '通信设备', '线缆部件及其他': '通信设备',
    '水产养殖': '渔业', '生猪养殖': '养殖业', '饲料': '饲料',
    '种子': '种植业', '粮食种植': '种植业',
    '影视动漫制作': '影视院线', '院线': '影视院线',
    '被动元件': '元件', '印制电路板': '元件', '元件': '元件',
    '玻璃制造': '玻璃玻纤', '玻纤制造': '玻璃玻纤',
    '空调': '白色家电', '厨房小家电': '小家电', '清洁小家电': '小家电',
    '厨房电器': '厨卫电器', '集成电路制造': '半导体',
    '军工电子Ⅲ': '军工电子Ⅱ', '地面兵装Ⅲ': '地面兵装Ⅱ',
    '航空装备Ⅲ': '航空装备Ⅱ', '航天装备Ⅲ': '航天装备Ⅱ',
    '白酒': '白酒Ⅱ', '特钢Ⅲ': '特钢Ⅱ', '普钢': '普钢',
}


def _norm_board(name):
    s = (name or '').strip()
    for r in _ROMAN_SUFFIX:
        s = s.replace(r, '')
    return s.strip()


_FLOW_VAL_CACHE = None


def _flow_val_lookup():
    """估值分位查找表：归一化板块名 -> (申万原名, pe_pct)，供资金流向徽章复用。"""
    global _FLOW_VAL_CACHE
    if _FLOW_VAL_CACHE is not None:
        return _FLOW_VAL_CACHE
    by_norm = {}
    try:
        if VALUATION_JSON.exists():
            vd = json.loads(VALUATION_JSON.read_text('utf-8'))
            for x in vd.get('items', []):
                pp = x.get('pe_pct')
                if pp is None:
                    continue
                by_norm[_norm_board(x.get('name'))] = (x.get('name', ''), pp)
    except Exception:
        pass
    _FLOW_VAL_CACHE = by_norm
    return by_norm


def flow_badge(sector):
    """资金流向板块的估值提示徽章：仅当净流入为正、且估值分位极低/偏贵时才标。"""
    name = sector.get('name', '')
    inflow = sector.get('net_inflow_yi')
    if inflow is None or inflow <= 0:
        return ''
    table = _flow_val_lookup()
    hit = table.get(_norm_board(name))
    if not hit:
        alias = _FLOW_TO_VAL_ALIAS.get(name)
        if alias:
            hit = table.get(_norm_board(alias))
    if not hit:
        return ''
    src = name                      # 来源始终是资金流向里的原始板块名
    val_name, pe_pct = hit
    if pe_pct < 20:
        cls, icon, txt = 'cheap', '👑', '低估潜力'
    elif pe_pct >= 80:
        cls, icon, txt = 'rich', '⚠️', '高位风险'
    else:
        return ''
    tip = (f'主力净流入 +{inflow:.1f}亿 · 近似映射申万「{val_name}」PE历史分位 {pe_pct:.0f}%'
           f'（「{src}」→「{val_name}」为近似口径，仅供参考）').replace('"', '')
    return f' <span class="flow-sig {cls}" title="{tip}">{icon}{txt}</span>'


def build_html(data, report, slow_picks=None, slow_meta=''):
    CSS_EXTRA = r'''
  /* —— quick-win ①：表格数值列右对齐 + 等宽数字 —— */
  .num { text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
  /* —— quick-win ④：AI 报告涨跌幅红绿 + 关键词胶囊 —— */
  .kw { display: inline-block; padding: 0 5px; margin: 0 1px; border-radius: 6px; background: rgba(184,41,255,0.22); color: #e9c9ff; font-weight: 600; }
  .rpt-up { color: #ef5350; font-weight: 600; }
  .rpt-down { color: #26c281; font-weight: 600; }
  /* —— quick-win ⑤：A股情绪恐贪 Gauge（真实广度合成） —— */
  .sg-wrap { display: flex; gap: 32px; align-items: flex-start; flex-wrap: wrap; }
  .sg-gauge { flex: 0 0 320px; max-width: 100%; }
  .sg-gauge-img { position: relative; width: 100%; aspect-ratio: 2 / 1; background: url('gauge_bg.png') no-repeat center bottom / contain; }
  .sg-needle { position: absolute; left: 50%; bottom: 1%; width: 4px; height: 66%; background: linear-gradient(180deg, rgba(255,255,255,.95), rgba(255,255,255,.30)); border-radius: 2px; transform-origin: 50% 100%; box-shadow: 0 0 6px rgba(0,0,0,.6); }
  .sg-readout { text-align: center; margin-top: 4px; }
  .sg-zl { font-size: 1em; font-weight: 600; margin-left: 0; }
  .sg-arcnum { position: absolute; left: 50%; top: 58%; transform: translate(-50%, -50%); z-index: 2; pointer-events: none; font-size: 2.1em; font-weight: 600; color: #fff; font-family: ui-monospace, Menlo, Consolas, monospace; line-height: 1; text-shadow: 0 1px 4px rgba(0,0,0,.85), 0 0 14px rgba(0,0,0,.55); }
  .sg-factors { flex: 1 1 340px; min-width: 300px; }
  .sg-title { border-bottom: none; padding-bottom: 0; border-left: 3px solid #8b7fa8; padding-left: 12px; }
  .sg-cap { font-size: .78em; color: #8b93a3; margin: 8px 0 16px; padding-left: 13px; line-height: 1.6; }
  .sg-card { background: var(--surface-2); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; margin-bottom: 10px; display: flex; align-items: center; gap: 14px; }
  .sg-card:last-of-type { margin-bottom: 0; }
  .sg-l { flex: 0 0 130px; }
  .sg-nm { font-size: .98em; font-weight: 600; color: #eceff5; }
  .sg-sub { font-size: .74em; color: #8b93a3; margin-top: 3px; }
  .sg-bar2 { position: relative; flex: 1; height: 10px; border-radius: 6px; background: linear-gradient(90deg, rgba(230,110,105,.32) 0%, rgba(212,151,82,.28) 35%, rgba(180,183,150,.22) 60%, rgba(74,156,121,.30) 100%); }
  .sg-dot { position: absolute; top: 50%; width: 12px; height: 12px; border-radius: 50%; background: #fff; border: 2px solid rgba(0,0,0,.3); transform: translate(-50%, -50%); box-shadow: 0 0 5px rgba(0,0,0,.7); }
  .sg-val { flex: 0 0 92px; display: flex; align-items: center; justify-content: flex-end; gap: 8px; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
  .sg-num { font-size: 1.05em; font-weight: 600; }
  .sg-badge { font-size: .74em; font-weight: 700; padding: 3px 9px; border-radius: 999px; color: #fff; white-space: nowrap; text-shadow: 0 1px 2px rgba(0,0,0,.45); box-shadow: inset 0 0 0 1px rgba(255,255,255,.14); }
  .sg-note { font-size: .78em; color: #8b93a3; margin-top: 12px; line-height: 1.6; }
  /* —— 仓位刻度：简化为醒目百分比 + 进度条（趋势优先逻辑不变） —— */
  .sg-tiers { width: 100%; margin-top: 10px; }
  .sg-tiers-hd { font-size: .9em; color: #c9d1e0; margin: 4px 0 14px; padding-left: 13px; line-height: 1.7; }
  .sg-tiers-hd b { color: #e8d5f0; }
  .sg-tiers-hd b.pos-title { color: #f0cc52; font-weight: 700; }
  .pos-line { display: flex; align-items: center; gap: 18px; padding: 16px 18px; background: var(--surface-2); border: 1px solid var(--line); border-radius: 10px; }
  .pos-big { font-size: 2.6em; font-weight: 600; font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; line-height: 1; min-width: 120px; }
  .pos-big small { display: block; font-size: .32em; font-weight: 400; color: #8b93a3; margin-top: 7px; font-family: inherit; }
  .pos-track { flex: 1; }
  .pos-track-hd { display: flex; justify-content: space-between; font-size: .84em; color: #9aa3b2; margin-bottom: 9px; }
  .pos-bar { position: relative; height: 12px; border-radius: 6px; background: var(--surface-3); overflow: hidden; }
  .pos-bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 6px; transition: width .35s ease; }
  .pos-ticks { display: flex; justify-content: space-between; font-size: .72em; color: #6f7787; margin-top: 7px; }
  .sg-tiers-note { font-size: .78em; color: #8b93a3; margin-top: 12px; padding-left: 13px; line-height: 1.65; }
  /* —— 板块估值热度（PE/PB 近5年分位） —— */
  .val-hero { background: var(--surface-2); border: 1px solid var(--line); border-left: 3px solid #c9a83a; border-radius: 10px; padding: 15px 18px; margin-bottom: 18px; font-size: .9em; line-height: 1.7; color: #c9d1e0; }
  .val-hero b { color: #e8d5f0; }
  .val-hero .hot { color: #e08a8a; }
  .val-hero .cold { color: #6cc79b; }
  .vh-lead { margin-bottom: 12px; }
  .vh-pills { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .vh-pill { display: inline-block; font-size: .84em; font-weight: 600; padding: 5px 13px; border-radius: 999px; }
  .vh-pill.vh-hot { background: rgba(212,104,104,.15); color: #e39a9a; border: 1px solid rgba(212,104,104,.38); }
  .vh-pill.vh-cold { background: rgba(63,168,120,.15); color: #83d1ac; border: 1px solid rgba(63,168,120,.38); }
  .vh-concl { font-size: .96em; color: #dfe4ee; }
  .val-tools { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
  .val-tools .vt-label { font-size: .8em; color: #8b93a3; }
  .val-chip { background: var(--surface-2); border: 1px solid var(--line); color: #aab2c0; border-radius: 999px; padding: 5px 15px; font-size: .8em; cursor: pointer; transition: .15s; }
  .val-chip:hover { background: var(--surface-3); color: #e8d5f0; }
  .val-chip.on { background: #c9a83a; color: #16161d; border-color: #c9a83a; font-weight: 600; }
  .val-wrap { max-height: 620px; overflow-y: auto; }
  .val-wrap::-webkit-scrollbar { width: 8px; }
  .val-wrap::-webkit-scrollbar-thumb { background: var(--surface-3); border-radius: 4px; }
  table.val-table { font-size: .92em; }   /* 与个股 Top10 表格数值字号一致 */
  .val-table th { position: sticky; top: 0; background: var(--card-bg); z-index: 2; cursor: pointer; user-select: none; white-space: nowrap; }
  .val-table th:hover { color: #e8d5f0; }
  .val-table th .arr { font-size: .8em; color: #6f7787; margin-left: 3px; }
  .val-table td { white-space: nowrap; }
  .val-table tr.val-dim { opacity: .32; }
  .val-table tr { cursor: pointer; }
  .val-name { font-weight: 600; color: #eceff5; }
  /* 胶囊标签：沿用报告 kw 标签的圆角 + 半透明背景，全局 UI 语言统一 */
  .val-pill { display: inline-block; font-size: .82em; font-weight: 600; padding: 3px 11px; border-radius: 999px; background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.10); }
  /* 进度条：固定 0~100 轨道宽度，左右端严格对齐；PB 不再占用长条 */
  .val-barcell { display: flex; align-items: center; gap: 10px; }
  .val-bar { position: relative; flex: 0 0 150px; width: 150px; height: 11px; border-radius: 5px; background: var(--surface-3); overflow: hidden; }
  .val-bar i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 5px; }
  .val-bar .mid { position: absolute; top: 0; bottom: 0; left: 50%; width: 1px; background: rgba(255,255,255,.16); }
  .val-pct { font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; font-weight: 600; font-size: .92em; min-width: 42px; }
  .val-judge { display: flex; flex-direction: column; align-items: flex-start; gap: 5px; }
  .val-pb { display: inline-flex; align-items: center; gap: 6px; font-size: .82em; color: #b9c0cd; font-variant-numeric: tabular-nums; }
  .val-pb-dot { display: inline-block; width: 8px; height: 8px; border-radius: 2px; flex: 0 0 8px; }
  .val-note { font-size: .78em; color: #8b93a3; margin-top: 14px; line-height: 1.7; border-top: 1px solid var(--line); padding-top: 12px; }
  .val-note b { color: #c9d1e0; }
  /* 资金流向 × 估值 交叉提示徽章 */
  .flow-sig { display: inline-block; font-size: .72em; font-weight: 600; padding: 2px 8px; margin-left: 6px; border-radius: 999px; cursor: help; white-space: nowrap; vertical-align: 1px; }
  .flow-sig.rich { background: rgba(196,112,112,.15); color: #e09393; border: 1px solid rgba(196,112,112,.35); }
  .flow-sig.cheap { background: rgba(74,156,121,.15); color: #7fd0a9; border: 1px solid rgba(74,156,121,.35); }
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
  // 核心关键词提气泡标签（只处理卡片正文，不动 summary 标题）
  var bubbleWords = ['抱团', '高低切', '避险', '缩量', '普跌', '退潮', '企稳', '抄底', '控仓'];
  var used = {};
  bubbleWords.forEach(function (k) {
    if (html.indexOf(k) >= 0 && !used[k]) {
      used[k] = 1;
      html = '<span class="tag-bubble">' + k + '</span>' + html;
    }
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
  // 半圆 Gauge：gauge_bg.png 经典四段底图（极度恐惧绿/恐惧黄/贪婪橙/极度贪婪红），
  // 叠加动态指针（0→-90°，100→+90°），gzone 阈值 25/50/75 与底图分段一致；
  // 数值放表盘弧心（指针上方正中），档位文字留在表盘下方
  var z = gzone(d.value);
  var ang = (d.value * 1.8 - 90).toFixed(1);
  var gauge = '<div class="sg-gauge-img">'
    + '<div class="sg-needle" style="transform:translateX(-50%) rotate(' + ang + 'deg)"></div>'
    + '<div class="sg-arcnum">' + d.value + '</div>'
    + '</div>'
    + '<div class="sg-readout"><span class="sg-zl" style="color:' + z.c + '">' + z.t + '</span></div>';
  var cards = '';
  (d.factors || []).forEach(function (f) {
    var fz = zone(f.score);
    cards += '<div class="sg-card"><div class="sg-l"><div class="sg-nm">' + f.name + '</div><div class="sg-sub">' + (f.note || '') + '</div></div>'
      + '<div class="sg-bar2"><span class="sg-dot" style="left:' + f.score + '%"></span></div>'
      + '<div class="sg-val"><span class="sg-num" style="color:' + fz.c + '">' + f.score + '</span>'
      + '<span class="sg-badge" style="background:' + fz.c + '">' + fz.t + '</span></div></div>';
  });
  box.innerHTML = '<div class="sg-gauge">' + gauge + '</div><div class="sg-factors">' + cards + '<div class="sg-note">' + (d.note || '') + '</div></div>';

  // —— 六档仓位刻度（趋势优先，恐贪微调）——
  // 趋势分位（沪深300 60日收益滚动分位）定仓位上限；恐贪只在上限内微调/降档，
  // 避免「极端恐惧=便宜」的逆向误判在下跌趋势里变成接飞刀。
  var tbox = document.getElementById('sentimentTiers');
  if (!tbox) return;
  var tiers = [
    { nm: '满仓', pct: '100%', val: 100, rg: '趋势≥50 · 恐贪≤10', c: '#e05a5a', tip: '趋势转强，情绪极冷' },
    { nm: '八成', pct: '80%', val: 80, rg: '趋势≥50 · 恐贪10~30', c: '#e07a4a', tip: '趋势转强，可分批加' },
    { nm: '四成', pct: '40%', val: 40, rg: '趋势30~50 或 恐贪30~50', c: '#c9a83a', tip: '趋势修复，控制仓位' },
    { nm: '一成', pct: '10%', val: 10, rg: '趋势10~30 或 恐贪50~70', c: '#7fa8c9', tip: '趋势偏弱，轻仓防守' },
    { nm: '半成', pct: '5%', val: 5, rg: '趋势0~10 或 恐贪70~90', c: '#5ec9a0', tip: '趋势极弱，防守为主' },
    { nm: '清仓', pct: '0%', val: 0, rg: '恐贪>90 或 弱趋势+贪婪', c: '#4a9d7a', tip: '离场观望，等趋势转强' }
  ];
  var v = d.value;
  // 只认名字含「趋势」的因子（完整5因子口径），避免把当日涨跌误当趋势
  var trendScore = null;
  (d.factors || []).forEach(function (f) {
    if (trendScore === null && f.name && f.name.indexOf('趋势') >= 0) trendScore = f.score;
  });
  var trendTxt;
  if (trendScore === null) trendTxt = '数据缺失';
  else if (trendScore < 10) trendTxt = '极弱';
  else if (trendScore < 30) trendTxt = '偏弱';
  else if (trendScore < 50) trendTxt = '修复中';
  else trendTxt = '转强';
  var cur;
  if (trendScore === null || trendScore < 10) cur = 4;       // 极弱/缺失 → 半成
  else if (trendScore < 30) cur = 3;                          // 偏弱 → 一成
  else if (trendScore < 50) cur = 2;                          // 修复中 → 四成
  else if (v <= 10) cur = 0;                                  // 趋势转强后，恐贪才决定重仓
  else if (v <= 30) cur = 1;
  else if (v <= 50) cur = 2;
  else if (v <= 70) cur = 3;
  else if (v <= 90) cur = 4;
  else cur = 5;
  // 恐贪微调：趋势偏弱且情绪已偏贪婪(>=70) → 再降一档
  if (trendScore !== null && trendScore < 50 && v >= 70 && cur < 5) cur += 1;
  var t = tiers[cur];
  var trendHead = (trendScore === null ? '趋势数据缺失' : '趋势分位 <b>' + trendScore + '</b>（' + trendTxt + '）');
  tbox.innerHTML = '<div class="sg-tiers-hd"><b class="pos-title">仓位建议（趋势优先 · 恐贪微调）</b> · ' + trendHead + ' · 恐贪 <b>' + v + '</b></div>'
    + '<div class="pos-line">'
    + '<div class="pos-big" style="color:' + t.c + '">' + t.pct + '<small>建议仓位上限</small></div>'
    + '<div class="pos-track">'
    + '<div class="pos-track-hd"><span>当前档位：<b style="color:' + t.c + '">' + t.nm + '</b> · ' + t.tip + '</span><span>' + t.rg + '</span></div>'
    + '<div class="pos-bar"><i style="width:' + t.val + '%;background:' + t.c + '"></i></div>'
    + '<div class="pos-ticks"><span>0% 清仓</span><span>25%</span><span>50%</span><span>75%</span><span>100% 满仓</span></div>'
    + '</div></div>'
    + '<div class="sg-tiers-note">规则：①「沪深300趋势」(60日收益滚动分位) 定上限：0~10→半成、10~30→一成、30~50→四成、≥50→才按恐贪原档位；②趋势偏弱且恐贪≥70 再降一档。趋势向下时极端恐惧≠便宜，不触发重仓。进度条仅示意，非投资建议。</div>';
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
                f'<td class="num">{s.get("price","")}</td>'
                f'<td class="{"up" if chg>0 else "down" if chg<0 else "flat"} num">{chg:+.2f}%</td>'
                f'<td class="num">{s.get("turnover",0)}%</td></tr>'
            )
        return '\n'.join(rows) if rows else '<tr><td colspan="5" class="sym">暂无数据</td></tr>'

    def _sector_sym(s):
        """行业板块行的K线符号：优先按板块名查 BK 映射（新浪兜底数据的 code 是
        ZCxx 新浪节点，弹窗数据源不认识），非 BK 代码一律丢弃。"""
        code = BOARD_CODES.get(s.get('name', '')) or (s.get('code') or '')
        return code.lower() if code.upper().startswith('BK') else ''

    def _spark(pct):
        """迷你条形图：宽度=涨跌绝对值(封顶5%)，涨红跌绿。"""
        w = min(abs(float(pct or 0)) / 5 * 100, 100)
        col = 'var(--up)' if (pct or 0) > 0 else 'var(--down)'
        return f'<span class="spark"><i style="width:{w:.0f}%;background:{col}"></i></span>'

    # 按主力净流入排序（钱往哪跑优先），净流入缺失的排末尾
    sectors_sorted = sorted(
        sectors,
        key=lambda s: (s.get('net_inflow_yi') is not None, s.get('net_inflow_yi') or 0),
        reverse=True,
    )
    # 净流入列的浅色底 bar（按绝对量级归一化；红=净流入 绿=净流出）
    _max_in = max((abs(s.get('net_inflow_yi') or 0) for s in sectors_sorted), default=0) or 1

    def _inflow_bg(val):
        if val is None:
            return ''
        w = abs(val) / _max_in * 100
        rgb = '239,83,80' if val > 0 else '38,194,129'
        return (f'background:linear-gradient(90deg,rgba({rgb},.17) {w:.0f}%,transparent {w:.0f}%)')

    sector_rows = ''.join(
        f'<tr data-kline-sym="{_sector_sym(s)}"><td class="stock-name">{s.get("name","")}{flow_badge(s)}</td>'
        f'<td class="{"up" if s.get("pct",0)>0 else "down" if s.get("pct",0)<0 else "flat"} num">{s.get("pct",0):+.2f}%{_spark(s.get("pct",0))}</td>'
        f'<td class="num">{s.get("amount_yi",0)}亿</td>'
        f'<td class="num inflow-cell {"money-in" if (s.get("net_inflow_yi") or 0)>0 else "money-out"}" '
        f'style="{_inflow_bg(s.get("net_inflow_yi"))}">'
        f'{("%+.2f" % s["net_inflow_yi"]) if s.get("net_inflow_yi") is not None else "—"}</td></tr>'
        for s in sectors_sorted
    ) or '<tr><td colspan="4" class="sym">暂无数据</td></tr>'

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

    # ── 板块估值热度（PE/PB 近5年分位）─────────────────────────────
    val_panel, val_script = build_valuation_panel()

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
  :root {{ --neon-purple: #b829ff; --neon-cyan: #00fff2; --dark-bg: #0b0b10; --card-bg: #16161d; --surface-2: #1e1e26; --surface-3: #26262f; --line: rgba(255,255,255,0.055); --up: #ef5350; --down: #26c281; }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans SC', sans-serif; background: var(--dark-bg); color: #e6e7eb; min-height: 100vh; padding: 24px; }}
  .container {{ max-width: 1400px; margin: 0 auto; }}
  header {{ text-align: center; margin-bottom: 30px; padding: 22px; background: var(--card-bg); border-radius: 12px; border: 1px solid var(--line); }}
  h1 {{ font-size: 2em; font-weight: 600; letter-spacing: .02em; color: #f2e9f7; }}
  .meta {{ color: #8b93a3; font-size: 0.9em; margin-top: 10px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; align-items: stretch; }}
  @media(max-width:900px){{ .grid{{grid-template-columns:1fr}} }}
  /* 底部两栏等高卡片：内部滚动，避免页面无限拉长 */
  .panel-scroll {{ display: flex; flex-direction: column; min-height: 0; max-height: 560px; }}
  .panel-scroll .panel-title {{ flex: 0 0 auto; }}
  .panel-scroll-body {{ flex: 1 1 auto; min-height: 0; overflow-y: auto; padding-right: 6px; }}
  .panel-scroll-body::-webkit-scrollbar {{ width: 7px; }}
  .panel-scroll-body::-webkit-scrollbar-thumb {{ background: var(--surface-3); border-radius: 4px; }}
  .panel {{ background: var(--card-bg); border-radius: 12px; padding: 24px 26px; border: 1px solid var(--line); }}
  .panel-title {{ font-size: 1.15em; font-weight: 600; margin-bottom: 20px; color: #e8d5f0; border-bottom: 1px solid var(--line); padding-bottom: 12px; letter-spacing: .01em; }}
  .report {{ line-height: 2.05; font-size: 1.04em; background: rgba(0,0,0,0.22); padding: 18px 20px; border-radius: 10px; }}
  .report h2 {{ font-size: 1.25em; font-weight: 600; color: #d8c4e8; margin: 18px 0 10px; }}
  .report h3 {{ font-size: 1.1em; font-weight: 600; color: #cfe0e8; margin: 16px 0 8px; }}
  .report p {{ margin: 0 0 10px; }}
  .idx-row {{ display: flex; gap: 14px; flex-wrap: wrap; }}
  .idx-card {{ background: var(--surface-2); border-radius: 10px; padding: 14px 18px; text-align: center; min-width: 108px; border: 1px solid var(--line); }}
  .idx-name {{ font-size: 0.85em; color: #8b93a3; }}
  .idx-price {{ font-size: 1.3em; font-weight: 600; }}
  .idx-chg {{ font-size: 0.9em; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.92em; }}
  th,td {{ padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--line); }}
  th {{ color: #8b93a3; font-weight: 400; }}
  .up {{ color: var(--up); background: rgba(239,83,80,0.07); }}
  .down {{ color: var(--down); background: rgba(38,194,129,0.07); }}
  .flat {{ color: #8b93a3; }}
  .sym {{ color: #8b93a3; font-size: 0.88em; }}
  .slowrise {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  .slowrise-entry {{ background: var(--surface-2); border-radius: 10px; padding: 10px 14px; border: 1px solid var(--line); }}
  .slowrise-date {{ font-size: 0.8em; color: #8b93a3; margin-bottom: 6px; }}
  .board-tag {{ display: inline-block; background: var(--surface-3); color: #cfc7d8; border-radius: 8px; padding: 4px 10px; margin: 3px; font-size: 0.86em; border: 1px solid var(--line); }}
  .board-tag[data-kline-sym] {{ cursor: pointer; transition: background .15s; }}
  .board-tag[data-kline-sym]:hover {{ background: rgba(184,41,255,0.28); color: #fff; }}
  .board-tag.hl-board {{ background: rgba(255,211,92,.22); border-color: #ffd35c; color: #ffe08a; box-shadow: 0 0 10px rgba(255,211,92,.4); }}
  .full {{ grid-column: 1 / -1; }}
  .data-time {{ font-size: 0.8em; color: #737b8b; text-align: right; }}
  .idx-card.kl-idx {{ cursor: pointer; transition: background .15s; }}
  .idx-card.kl-idx:hover {{ background: var(--surface-3); }}
  tr[data-kline-sym] {{ cursor: pointer; }}
  tr[data-kline-sym]:hover td {{ background: var(--surface-3); }}
  /* —— 视觉做减法：折叠卡片 + 气泡标签 + 风险提示独立 —— */
  details.rcard {{ background: var(--surface-2); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 12px; overflow: hidden; }}
  details.rcard > summary {{ cursor: pointer; padding: 13px 18px; font-weight: 600; font-size: 1.05em; color: #e8d5f0; list-style: none; display: flex; align-items: center; gap: 9px; }}
  details.rcard > summary::-webkit-details-marker {{ display: none; }}
  details.rcard > summary::before {{ content: '▸'; color: #a678c4; font-size: .9em; transition: transform .15s; display: inline-block; }}
  details.rcard[open] > summary::before {{ transform: rotate(90deg); }}
  details.rcard .rcard-body {{ padding: 2px 18px 16px; color: #ccd0da; }}
  .tag-bubble {{ display: inline-block; padding: 2px 11px; margin: 0 5px 6px 0; border-radius: 999px; font-size: .82em; background: rgba(0,255,242,0.10); color: #7fe8e0; border: 1px solid rgba(0,255,242,0.18); }}
  .risk-box {{ margin-top: 16px; padding: 14px 18px; border-radius: 10px; border: 1px solid rgba(239,83,80,0.4); background: rgba(239,83,80,0.07); color: #f0b8b6; font-size: .9em; line-height: 1.75; }}
  .risk-box b {{ color: #ff8a86; }}
  /* 迷你条形图 sparkline（行业板块涨跌幅） */
  .spark {{ display: inline-block; width: 56px; height: 9px; margin-left: 8px; vertical-align: middle; border-radius: 5px; background: var(--surface-3); position: relative; overflow: hidden; }}
  .spark i {{ position: absolute; top: 0; bottom: 0; left: 0; border-radius: 5px; }}
  .money-in {{ color: var(--up); }} .money-out {{ color: var(--down); }}
  .inflow-cell {{ background-repeat: no-repeat; }}
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

{GAVAGE_PANEL}
    <div class="panel full">
      <div class="panel-title">🧠 AI 复盘报告 <span style="font-size:.72em;font-weight:400;color:#8b95a5">先看总结陈词，再用下方图表印证</span></div>
      <div class="report">{report_html}</div>
    </div>

    <div class="panel full">
      <div class="panel-title">🔥 市场热力图 <span style="font-size:.72em;font-weight:400;color:#8b95a5">方块大小=市值 / 颜色可切 当日涨跌幅 ⇄ RPS60 强弱</span></div>
      <div id="marketHeat"></div>
    </div>

{rot_panel}
{val_panel}
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

    <div class="panel panel-scroll">
      <div class="panel-title">📊 行业板块 · 资金流向</div>
      <div class="panel-scroll-body">
      <table><thead><tr><th>板块</th><th>涨跌幅</th><th>成交额(亿)</th><th>主力净流入(亿)</th></tr></thead>
      <tbody>{sector_rows}</tbody></table>
      <div class="sg-note" style="margin-top:12px">按主力净流入排序 · <span class="money-in">红=净流入</span> / <span class="money-out">绿=净流出</span>（A股口径：红涨绿跌）<br>👑低估潜力 / ⚠️高位风险 = 资金方向与估值分位的交叉提示（东财细分板块近似映射申万行业，仅供参考）；<b>历史估值仅代表过去，不代表未来收益。</b></div>
      </div>
    </div>

    <div class="panel panel-scroll">
      <div class="panel-title">🌡️ 慢热板块跟踪</div>
      <div class="panel-scroll-body">
      <div class="slowrise">{slow_tags}</div>
      {picks_html}
      </div>
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
// 估值表 hover 板块 → 慢热列表项高亮呼应（纯视觉）
(function () {{
  var tags = document.querySelectorAll('.slowrise .board-tag');
  window.addEventListener('vibe:hoverBoard', function (e) {{
    var name = ((e.detail && e.detail.name) || '').trim();
    for (var i = 0; i < tags.length; i++) {{
      var t = tags[i].textContent.trim();
      var hit = !!name && (t === name || t.indexOf(name) >= 0 || name.indexOf(t) >= 0);
      tags[i].classList.toggle('hl-board', hit);
    }}
  }});
}})();
</script>
<!-- 板块 RPS60 强弱（内联）：市场热力图「颜色=RPS60」口径的数据源 -->
{rps_script}<!-- 市场热力 treemap（板块/个股）：东财→新浪→腾讯 三源自动兜底，点击个股弹K线 -->
<script src="market_heat.js?v=20261107a" defer></script>
<!-- 板块轮动矩阵（板块 × 交易日）：数据内联，无额外网络请求；点击板块名弹K线 -->
{rot_script}<script src="market_rotation.js?v=20261016a" defer></script>
<!-- 通用K线弹窗：点击股票代码/名称/指数卡片查看K线（指数用完整符号） -->
{sent_script}{NUMALIGN_SCRIPT}{REPORT_SCRIPT}{GAUGE_SCRIPT}{val_script}<script src="kline_popup.js?v=20261025b" defer></script>
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
