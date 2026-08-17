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


def build_html(data, report):
    ts = data.get('timestamp', '')[:19].replace('T', ' ')
    date_str = ts[:10] if ts else ''
    indices = data.get('data', {}).get('indices', [])
    strong = data.get('data', {}).get('strong_stocks', [])
    yimeng = data.get('data', {}).get('yimeng_stocks', [])
    sectors = data.get('data', {}).get('sectors', [])
    slowrise = data.get('data', {}).get('slowrise', [])

    idx_cards = ''.join(
        f'<div class="idx-card"><div class="idx-name">{i["name"]}</div>'
        f'<div class="idx-price">{i["current"]}</div>'
        f'<div class="idx-chg {idx_class(i["pct"])}">{arrow(i["pct"])} {i["pct"]:+.2f}%</div></div>'
        for i in indices
    )

    def stock_rows(stocks, limit=10):
        rows = []
        for s in stocks[:limit]:
            chg = s.get('change_pct', 0)
            rows.append(
                f'<tr><td>{s.get("name","")}</td><td class="sym">{s.get("code","")}</td>'
                f'<td>{s.get("price","")}</td>'
                f'<td class="{"up" if chg>0 else "down" if chg<0 else "flat"}">{chg:+.2f}%</td>'
                f'<td>{s.get("turnover",0)}%</td></tr>'
            )
        return '\n'.join(rows) if rows else '<tr><td colspan="5" class="sym">暂无数据</td></tr>'

    sector_rows = ''.join(
        f'<tr><td>{s.get("name","")}</td><td class="{"up" if s.get("pct",0)>0 else "down"}">{s.get("pct",0):+.2f}%</td>'
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
</body>
</html>'''


def main():
    if '--serve' not in sys.argv:
        print('用法: python ai_analysis.py --serve')
        return
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
