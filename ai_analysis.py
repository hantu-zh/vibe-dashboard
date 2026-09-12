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


def build_html(data, report):
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

    sector_rows = ''.join(
        f'<tr data-kline-sym="{(s.get("code") or BOARD_CODES.get(s.get("name",""),"") or "").lower()}"><td class="stock-name">{s.get("name","")}</td><td class="{"up" if s.get("pct",0)>0 else "down"}">{s.get("pct",0):+.2f}%</td>'
        f'<td>{s.get("net_inflow_yi",0)}亿</td></tr>'
        for s in sectors
    ) or '<tr><td colspan="3" class="sym">暂无数据</td></tr>'

    slow_tags = ''.join(f'<span class="board-tag">{s.get("name","")}</span>' for s in slowrise) or '<div style="color:#555">暂无数据</div>'

    report_html = md_to_html(report)

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
  .full {{ grid-column: 1 / -1; }}
  .data-time {{ font-size: 0.75em; color: #555; text-align: right; }}
  .idx-card.kl-idx {{ cursor: pointer; transition: background .15s; }}
  .idx-card.kl-idx:hover {{ background: rgba(0,255,242,0.10); }}
  tr[data-kline-sym] {{ cursor: pointer; }}
  tr[data-kline-sym]:hover td {{ background: rgba(90,130,230,0.09); }}
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
      <table><thead><tr><th>板块</th><th>涨跌幅</th><th>主力净流入</th></tr></thead>
      <tbody>{sector_rows}</tbody></table>
    </div>

    <div class="panel">
      <div class="panel-title">🌡️ 慢热板块跟踪</div>
      <div class="slowrise">{slow_tags}</div>
    </div>
  </div>
</div>
<script>
window.KLINE_CONFIG = {{
  rowSelectors: ['.picks-row', '.stock-item', 'tr[data-kline-code]', 'tr[data-kline-sym]', '[data-kline-row]', '.idx-card'],
  triggerSelectors: ['a.picks-link', '.picks-code', 'a.stock-name', '.stock-name', 'a.stock-code', '.stock-code',
    'a[href*="quote.eastmoney.com"]', 'a[href*="finance.sina.com.cn/realstock"]', '.idx-card']
}};
</script>
<!-- 通用K线弹窗：点击股票代码/名称/指数卡片查看K线（指数用完整符号） -->
<script src="kline_popup.js?v=20260912b" defer></script>
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
    html = build_html(data, report)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'[ai_analysis] 生成 {OUTPUT_HTML} ({len(html):,} bytes), date={data.get("timestamp","")[:10]}')


if __name__ == '__main__':
    main()
