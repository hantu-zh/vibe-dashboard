# -*- coding: utf-8 -*-
"""earnings_funnel.html 生成器
================================
把三份数据合成「财报 × 资金信号 共振面板」：

    q3_earnings.json      —— 业绩预告/正式（名称 + 财报核心字段）
    sector_funnel.json    —— 板块→个股信号漏斗（威震天/益盟/盗火线 命中板块）
    sector_rankings.json  —— 全市场板块 RPS 快照（最新日期键），用于「板块强度」

输出 earnings_funnel.html（自包含，数据内联，双击即可看；无需本地服务器）：
    · 资金信号列 = sector_funnel 命中（个股/孤军/行业）
    · 板块强度列 = 按 industry_dir 主题词匹配最新板块快照 → RPS60/涨跌幅/强度
    · 研判 = 高增(≥50%) × (漏斗命中 或 板块强) → 重点 / 待验证 / 观察

披露期标签取自 q3_earnings.json 的 meta.window，换季只需重新生成，页面零改动。
用法：python _gen_earnings_funnel.py
"""
import json, os, datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
def L(p):
    with open(os.path.join(ROOT, p), encoding='utf-8') as f:
        return json.load(f)

earn = L('q3_earnings.json')
fun = L('sector_funnel.json')
sr = L('sector_rankings.json')

META = earn.get('meta', {})
PERIOD = META.get('window') or '业绩披露窗口'
EARN_ITEMS = earn['items']
BOARDS = fun.get('boards', [])
ORPHANS = fun.get('orphans', [])

# ---------- 板块强度：最新日期键快照 ----------
dated = sorted([k for k in sr if k[0].isdigit()])
LATEST = dated[-1] if dated else None
SECTORS = sr.get(LATEST, []) if LATEST else []

# 行业方向 → 板块名关键词（用于匹配最新板块快照）
THEME_KW = {
    'AI算力/光通信': ['光模块','通信','CPO','算力','数据中心','光通信','PCB'],
    '半导体设备': ['半导体','芯片','集成电路','电子化学品'],
    '稀土·资源': ['稀土','小金属','有色金属','贵金属'],
    '化工涨价': ['化学','化工','塑料','橡胶','化纤','化肥','农药'],
    'PCB·电子': ['PCB','印制电路板','消费电子','电子制造','元件'],
    '医药': ['医药','制药','生物','医疗','中药'],
}

def sector_strength(industry_dir):
    """返回 (sector_name, rps60, score, change_pct, strength) 或 None"""
    if not SECTORS:
        return None
    kws = THEME_KW.get(industry_dir or '', [])
    if not kws:
        return None
    cands = [s for s in SECTORS if any(k in s.get('name','') for k in kws)]
    if not cands:
        return None
    cands.sort(key=lambda s: -(s.get('rps60', 0) or 0))
    b = cands[0]
    return (b.get('name'), b.get('rps60'), b.get('score'),
            b.get('change_pct'), b.get('strength'))

# ---------- 漏斗信号匹配 ----------
def funnel_match(it):
    code = it['code']
    for b in BOARDS:
        for s in b.get('stocks', []):
            if s['code'] == code:
                return ('board', b)
    for o in ORPHANS:
        if o['code'] == code:
            return ('orphan', o)
    sec = it.get('industry_dir') or it.get('sector_guess') or ''
    for b in BOARDS:
        if sec and (sec in b['name'] or b['name'] in sec):
            return ('board', b)
    return None

def src_of(board, code, mode):
    if not board:
        return []
    set_ = set()
    for s in board.get('stocks', []):
        for x in s.get('src', []):
            set_.add(x)
    if mode == '个股':
        st = [s for s in board.get('stocks', []) if s['code'] == code]
        if st:
            for x in st[0].get('src', []):
                set_.add(x)
    return sorted(set_)

# ---------- 合成行 ----------
rows = []
for it in EARN_ITEMS:
    fm = funnel_match(it)
    yoy = it.get('forecast_np_yoy') or []
    ymin = yoy[0] if yoy else None
    high = (ymin is not None and ymin >= 50)
    ss = sector_strength(it.get('industry_dir') or it.get('sector_guess'))

    # 漏斗
    funnel_kind = None; funnel_name = None; funnel_score = None; funnel_rps = None; funnel_fresh = None; srcs = []
    if fm and fm[0] == 'board':
        b = fm[1]; funnel_kind = 'board'; funnel_name = b['name']
        funnel_score = b.get('score'); funnel_rps = b.get('rps60'); funnel_fresh = b.get('fresh')
        srcs = src_of(b, it['code'], fm[0])
    elif fm and fm[0] == 'orphan':
        o = fm[1]; funnel_kind = 'orphan'; funnel_name = '孤军·' + (o.get('board') or '')
        funnel_rps = o.get('rps60')

    # 板块强度
    sec_name = ss[0] if ss else None
    sec_rps = ss[1] if ss else None
    sec_score = ss[2] if ss else None
    sec_chg = ss[3] if ss else None
    sec_strength = ss[4] if ss else None

    # 研判
    if high:
        if funnel_kind == 'board' or (sec_rps is not None and sec_rps >= 70):
            tag = '重点·高增+资金确认'
        elif sec_rps is not None and sec_rps >= 50:
            tag = '高增·板块偏强'
        else:
            tag = '待验证·资金未确认'
    else:
        tag = '共振·温和' if funnel_kind == 'board' else ('弱板块·观察' if funnel_kind == 'orphan' else '观察')

    rank = {'重点·高增+资金确认':0,'高增·板块偏强':1,'共振·温和':2,
            '弱板块·观察':3,'待验证·资金未确认':4,'观察':5}[tag]

    rows.append({
        'code': it['code'], 'name': it['name'], 'board': it.get('board',''),
        'industry': it.get('industry_dir') or it.get('sector_guess') or '',
        'yoy': yoy, 'amt': it.get('np_amt_wan') or [],
        'status': it.get('disclosure_status') or it.get('report_type') or '',
        'appr': it.get('appointment_date'),
        'core': it.get('core'),
        'high': high, 'kcfj': bool(it.get('kcfj_suspect')),
        'focus': it.get('focus_tags') or [],
        'funnel_kind': funnel_kind, 'funnel_name': funnel_name,
        'funnel_score': funnel_score, 'funnel_rps': funnel_rps, 'funnel_fresh': funnel_fresh,
        'srcs': srcs,
        'sec_name': sec_name, 'sec_rps': sec_rps, 'sec_score': sec_score,
        'sec_chg': sec_chg, 'sec_strength': sec_strength,
        'tag': tag, 'rank': rank,
    })

rows.sort(key=lambda r: (r['rank'], -((r['yoy'][0]) if (r['yoy'] and r['yoy'][0] is not None) else -1e9)))

EF_META = {
    'period': PERIOD,
    'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
    'latest_snapshot': LATEST,
    'total': len(rows),
    'high': sum(1 for r in rows if r['high']),
    'funnel': sum(1 for r in rows if r['funnel_kind'] == 'board'),
    'key': sum(1 for r in rows if r['tag'] == '重点·高增+资金确认'),
    'wait': sum(1 for r in rows if r['tag'] == '待验证·资金未确认'),
}

# ================= 输出 HTML 模板 =================
TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <link rel="preconnect" href="https://qt.gtimg.cn" crossorigin>
  <link rel="preconnect" href="https://web.ifzq.gtimg.cn" crossorigin>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>财报 × 资金信号 共振面板 | vibe-dashboard</title>
<meta name="description" content="业绩预告 × 板块资金信号漏斗共振：高增个股是否有板块资金确认，重点/待验证一目了然。">
<style>
  :root{
    --bg:#070b14; --panel:#0d1424; --panel2:#0a0f1d; --line:#1e2740;
    --txt:#dfe6ff; --dim:#7b85a8; --dim2:#5f6b8f; --accent:#7fd3ff;
    --hot:#ffb74d; --new:#4dd0e1; --up:#ff5a5a; --down:#37d67a;
  }
  *{box-sizing:border-box}
  html,body{margin:0;background:var(--bg);color:var(--txt);
    font-family:"Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;
    -webkit-font-smoothing:antialiased}
  .wrap{max-width:1200px;margin:0 auto;padding:18px 14px 56px}
  h1{margin:0 0 4px;font-size:1.2em;font-weight:700;letter-spacing:.02em}
  h1 .accent{color:var(--accent)}
  .sub{margin:0 0 6px;color:var(--dim);font-size:.78em;line-height:1.6}
  .sub b{color:var(--txt);font-weight:600}
  .badge{display:inline-block;margin-left:8px;padding:2px 10px;border-radius:10px;font-size:.74em;
    background:#14203a;color:var(--accent);border:1px solid var(--line);vertical-align:2px}
  .sumbar{display:flex;gap:14px;flex-wrap:wrap;margin:12px 0 14px;font-size:.82em;color:var(--dim)}
  .sumbar b{color:var(--txt);font-weight:700;font-family:ui-monospace,Consolas,monospace}
  .sumbar .k{color:var(--accent)} .sumbar .h{color:var(--up)} .sumbar .w{color:var(--hot)}
  .bar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
  .search{flex:1 1 220px;display:flex;align-items:center;gap:8px;background:var(--panel);
    border:1px solid var(--line);border-radius:9px;padding:8px 11px}
  .search input{flex:1;border:0;background:transparent;color:var(--txt);font-size:.9em;outline:none;font-family:inherit}
  .chk{display:flex;align-items:center;gap:7px;background:var(--panel);border:1px solid var(--line);
    border-radius:9px;padding:8px 12px;cursor:pointer;font-size:.84em;color:var(--dim);user-select:none}
  .chk input{accent-color:var(--accent);width:15px;height:15px}
  .dz-select{background:var(--panel);border:1px solid var(--line);border-radius:9px;
    padding:8px 10px;color:var(--txt);font-size:.84em;font-family:inherit;outline:none;cursor:pointer}
  .dz-select:hover{border-color:var(--accent)}
  .scroll{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel2)}
  table{border-collapse:collapse;width:100%;min-width:1000px;font-size:12px}
  thead th{position:sticky;top:0;background:#101a30;color:var(--dim);font-weight:600;
    padding:7px 6px;text-align:center;white-space:nowrap;border-bottom:1px solid var(--line);font-size:11px}
  thead th.l{text-align:left}
  tbody td{padding:6px 6px;text-align:center;border-bottom:1px solid #141d33;white-space:nowrap}
  tbody tr{cursor:pointer;transition:background .12s}
  tbody tr:hover{background:#13203a}
  td.idx{color:var(--dim2);font-family:ui-monospace,Consolas,monospace;width:38px}
  td.code{color:var(--accent);font-family:ui-monospace,Consolas,monospace}
  td.nm{text-align:left;color:var(--txt);font-weight:600}
  td.nm a{color:var(--txt);text-decoration:none}
  td.nm a:hover{color:var(--accent)}
  td.dim{color:var(--dim)}
  td.num{font-family:ui-monospace,Consolas,monospace;color:var(--txt);font-weight:600}
  td.price{font-family:ui-monospace,Consolas,monospace;color:var(--txt);font-weight:600}
  td.chg{font-family:ui-monospace,Consolas,monospace;font-weight:600}
  td.chg.up{color:var(--up)} td.chg.down{color:var(--down)}
  .muted{color:var(--dim2)}
  .tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:.82em;font-weight:600;white-space:nowrap}
  .tag.reso{background:rgba(55,214,122,.18);color:#7fe9a0;border:1px solid rgba(55,214,122,.4)}
  .tag.key{background:rgba(255,90,90,.18);color:#ff9b9b;border:1px solid rgba(255,90,90,.45);font-weight:700}
  .tag.mid{background:rgba(255,183,77,.16);color:#ffce8a}
  .tag.wait{background:rgba(255,183,77,.16);color:#ffce8a}
  .tag.orphan{background:rgba(107,116,148,.16);color:#aeb6cf}
  .tag.obs{background:#14203a;color:var(--dim);border:1px solid var(--line)}
  .tag.weak{background:rgba(107,116,148,.16);color:#aeb6cf}
  .score{font-family:ui-monospace,Consolas,monospace;font-weight:700;color:var(--accent)}
  .rps{font-family:ui-monospace,Consolas,monospace;font-size:11px}
  .rps.strong{color:#7fe9a0} .rps.mid{color:#ffce8a} .rps.weak{color:#aeb6cf}
  .src{display:inline-block;margin:1px 2px;padding:1px 6px;border-radius:5px;font-size:.76em;
    background:#14203a;color:var(--dim);white-space:nowrap}
  .mini{display:inline-block;margin:1px 2px;padding:1px 6px;border-radius:5px;font-size:.76em;
    background:rgba(255,107,107,.14);color:#ffb3b3;white-space:nowrap}
  .cd-near{color:#ff6b6b;font-weight:700}
  .secbox{display:inline-block;text-align:left;white-space:nowrap}
  .secbox .sn{color:var(--txt);font-weight:600}
  .bd{color:var(--dim2);font-size:11px;font-weight:400}
  .foot{margin-top:18px;color:var(--dim2);font-size:.76em;line-height:1.7;text-align:center}
  .foot a{color:var(--accent);text-decoration:none}
</style>
</head>
<body>
<div class="wrap">
  <h1><span class="accent">财报 × 资金信号</span> 共振面板 <span class="badge" id="seasonBadge">…</span></h1>
  <p class="sub" id="periodSub"></p>
  <div class="sumbar" id="sumbar"></div>
  <div class="bar">
    <label class="search"><input id="efQ" type="text" placeholder="搜索代码 / 名称 / 行业…" autocomplete="off" spellcheck="false"></label>
    <label class="chk"><input type="checkbox" id="efReso"> 只看漏斗信号</label>
    <label class="chk"><input type="checkbox" id="efHigh"> 只看高增(≥50%)</label>
    <label class="chk"><input type="checkbox" id="efKey"> 只看重点</label>
    <select id="efPool" class="dz-select"><option value="">池·全部</option><option value="core">核心池</option><option value="watch">观察池</option></select>
    <select id="efSector" class="dz-select"><option value="">行业·全部</option></select>
  </div>
  <div class="scroll">
    <table id="efTable">
      <thead><tr>
        <th>序号</th><th>代码</th><th class="l">名称</th><th class="l">行业方向</th>
        <th>归母同比(预告)</th><th>预告净利</th><th>状态</th><th>距披露</th>
        <th>资金信号(漏斗)</th><th>板块强度(估算)</th><th>现价</th><th>涨跌幅</th><th class="l">研判</th>
      </tr></thead>
      <tbody id="efBody"></tbody>
    </table>
  </div>
  <div class="foot">
    数据：<b>q3_earnings.json</b>（业绩预告/正式）× <b>sector_funnel.json</b>（信号股漏斗）× <b>sector_rankings.json</b>（板块 RPS 快照 · __LATEST__）<br>
    披露期标签自动取自数据 <b>meta.window</b>，换季重新生成即可、无需改页面 · 现价/涨跌幅为腾讯行情（60s 刷新）· 点击任意行弹 K 线<br>
    「待验证」= 高增但漏斗与板块强度均未确认。<b>板块强度(估算)</b> 按上游「行业方向」关键词匹配最新板块快照，而该字段属名称关键词猜测、可能偏离实际主业（如华海药业标为半导体设备、实为医药），仅作初筛参考。
  </div>
</div>
<script src="kline_popup.js?v=20261025b" defer></script>
<script>
var EF_ROWS = __EF_ROWS__;
var EF_META = __EF_META__;
(function(){
  'use strict';
  var rows=EF_ROWS, quotes={};
  function el(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];});}
  function symOf(code){return (/^[69]/.test(code)?'sh':'sz')+code;}
  function daysTo(ds){if(!ds)return null;var t=new Date(String(ds).replace(/-/g,'/'));if(isNaN(t))return null;return Math.round((t-new Date())/86400000);}
  function seasonBadge(){var m=new Date().getMonth()+1;if(m>=1&&m<=4)return '当前披露季 · 年报/一季报';if(m>=7&&m<=8)return '当前披露季 · 中报';if(m===10)return '当前披露季 · 三季报';return '业绩空窗 · 跟踪最新预告';}
  function yoyColor(lo){if(lo==null)return '#5f6b8f';if(lo>=100)return '#2ecc71';if(lo>=50)return '#27ae60';if(lo>=0)return '#7bc6a0';return '#e74c3c';}
  function yoyStr(y){if(!y||y[0]==null||y[1]==null)return '<span class="muted">—</span>';return '<span style="color:'+yoyColor(y[0])+'">+'+(+y[0]).toFixed(0)+'%~+'+(+y[1]).toFixed(0)+'%</span>';}
  function amtStr(a){if(!a||a[0]==null)return '<span class="muted">—</span>';return (a[0]>=10000)?((a[0])/10000).toFixed(1)+'亿':(+a[0]).toFixed(0)+'万';}
  function rpsCls(r){if(r==null)return 'weak';if(r>=70)return 'strong';if(r>=50)return 'mid';return 'weak';}
  function funnelHtml(r){
    var tip=(r.srcs&&r.srcs.length)?('信号源: '+r.srcs.join('、')):'';
    if(r.funnel_kind==='board') return '<span class="tag reso"'+(tip?' title="'+esc(tip)+'"':'')+'>✓'+esc(r.funnel_name)+'</span>';
    if(r.funnel_kind==='orphan') return '<span class="tag orphan">'+esc(r.funnel_name)+'</span>';
    return '<span class="muted">— 待验证</span>';
  }
  function secHtml(r){
    if(!r.sec_name) return '<span class="muted">—</span>';
    var cls=rpsCls(r.sec_rps);
    var chg = r.sec_chg==null?'':(r.sec_chg>=0?'+':'')+r.sec_chg.toFixed(2)+'%';
    var tip=r.sec_name+' · RPS60 '+r.sec_rps
      +(r.sec_score!=null?' · 分'+r.sec_score.toFixed(0):'')
      +(chg?' · '+chg:'')+(r.sec_strength?' · '+r.sec_strength:'')
      +'（按行业方向「'+(r.industry||'—')+'」关键词匹配，估算）';
    return '<span class="secbox" title="'+esc(tip)+'"><span class="sn">'+esc(r.sec_name)+'</span> <span class="rps '+cls+'">'+r.sec_rps+'</span>'
      +(chg?' <span class="rps '+(r.sec_chg>=0?'strong':'weak')+'">'+chg+'</span>':'')+'</span>';
  }
  function tagHtml(r){
    var cls = r.tag.indexOf('重点')>=0?'key':(r.tag.indexOf('待验证')>=0?'wait':(r.tag.indexOf('弱')>=0?'orphan':'mid'));
    return '<span class="tag '+cls+'">'+esc(r.tag)+'</span>';
  }
  function build(){
    el('seasonBadge').textContent=seasonBadge();
    el('periodSub').innerHTML='业绩预告 × 板块资金信号漏斗（sector_funnel）× 板块 RPS（sector_rankings）共振 · <b>名称 + 财报 + 资金确认</b> 三栏同屏 · 披露期：<b>'+esc(EF_META.period)+'</b>';
    document.title='财报 × 资金信号 共振面板 · '+EF_META.period+' | vibe-dashboard';
    el('sumbar').innerHTML='共 <b>'+EF_META.total+'</b> 只业绩候选'
      +' · <span class="h">高增(≥50%) '+EF_META.high+'</span>'
      +' · <span class="k">漏斗信号 '+EF_META.funnel+'</span>'
      +' · <span class="h">重点(高增+资金确认) '+EF_META.key+'</span>'
      +' · <span class="w">待验证(高增无信号) '+EF_META.wait+'</span>';
    var sel=el('efSector'),set={};
    rows.forEach(function(r){if(r.industry)set[r.industry]=1;});
    Object.keys(set).sort().forEach(function(s){var o=document.createElement('option');o.value=s;o.textContent=s;sel.appendChild(o);});
    render(); maybeQuotes(); setInterval(maybeQuotes,60000);
  }
  function passes(r){
    var q=(el('efQ').value||'').trim().toLowerCase();
    if(el('efReso').checked && r.funnel_kind!=='board') return false;
    if(el('efHigh').checked && !r.high) return false;
    if(el('efKey').checked && r.tag!=='重点·高增+资金确认') return false;
    var pl=el('efPool').value;
    if(pl==='core'&&r.core===false) return false;
    if(pl==='watch'&&r.core!==false) return false;
    var sec=el('efSector').value;
    if(sec&&r.industry!==sec) return false;
    if(q){var hay=(r.code+' '+r.name+' '+r.industry).toLowerCase();if(hay.indexOf(q)<0)return false;}
    return true;
  }
  function render(){
    var shown=rows.filter(passes);
    var h='';
    shown.forEach(function(r,i){
      var d=daysTo(r.appr);
      var cd=d==null?'<span class="muted">—</span>':(d<0?'<span class="muted">已过</span>':(d<=5?'<b class="cd-near">'+d+'天</b>':d+'天'));
      var stTag = r.status==='已出正式'?'<span class="tag reso">已出正式</span>':(r.status==='仅有预告'?'<span class="tag mid">仅有预告</span>':'<span class="muted">'+(r.status||'—')+'</span>');
      var kcfj=r.kcfj?'<span class="tag wait" title="扣非存疑：业绩含非经常性损益，需复核扣非增速匹配度">扣非存疑</span>':'';
      var minis=(r.focus||[]).map(function(t){return '<span class="mini">'+esc(t)+'</span>';}).join(' ');
      h+='<tr data-code="'+r.code+'" data-name="'+esc(r.name)+'">'
        +'<td class="idx">'+(i+1)+'</td>'
        +'<td class="code">'+r.code+'</td>'
        +'<td class="nm"><a href="https://quote.eastmoney.com/'+symOf(r.code)+'.html" target="_blank" rel="noopener">'+esc(r.name)+'</a>'+(r.board?' <span class="bd">'+esc(r.board)+'</span>':'')+'</td>'
        +'<td class="dim">'+(r.industry||'')+'</td>'
        +'<td class="num">'+yoyStr(r.yoy)+'</td>'
        +'<td class="num">'+amtStr(r.amt)+'</td>'
        +'<td>'+stTag+'</td>'
        +'<td>'+cd+'</td>'
        +'<td>'+funnelHtml(r)+'</td>'
        +'<td>'+secHtml(r)+'</td>'
        +'<td class="price" id="efpx_'+r.code+'">—</td>'
        +'<td class="chg" id="efchg_'+r.code+'">—</td>'
        +'<td class="l">'+tagHtml(r)+(minis?' '+minis:'')+(kcfj?' '+kcfj:'')+'</td>'
        +'</tr>';
    });
    el('efBody').innerHTML=h||'<tr><td colspan="13" class="muted" style="padding:24px">无匹配项</td></tr>';
    applyQuotes();
  }
  function maybeQuotes(){
    var codes=rows.map(function(r){return r.code;});
    for(var i=0;i<codes.length;i+=50){(function(sl){var s=document.createElement('script');s.src='https://qt.gtimg.cn/q='+sl.map(symOf).join(',')+'&_t='+Date.now();s.onload=function(){applyQuotes();s.remove();};s.onerror=function(){s.remove();};document.head.appendChild(s);})(codes.slice(i,i+50));}
  }
  function applyQuotes(){
    rows.forEach(function(r){var v=window['v_'+symOf(r.code)];if(!v)return;var p=String(v).split('~');if(p.length<33)return;var price=parseFloat(p[3]),pct=parseFloat(p[32]);if(isNaN(price))return;var pe=el('efpx_'+r.code),ce=el('efchg_'+r.code);if(pe)pe.textContent=price.toFixed(2);if(ce&&!isNaN(pct)){ce.textContent=(pct>=0?'+':'')+pct.toFixed(2)+'%';ce.className='chg '+(pct>=0?'up':'down');}});
  }
  // 点击行 → 全站统一 B 弹窗（kline_popup.js，默认阉割版副图）；脚本未就绪时降级为无操作
  function openK(code,name){if(typeof window.openKline==='function'){window.openKline(code,name);}}
  if(el('efBody'))el('efBody').addEventListener('click',function(e){var tr=e.target.closest('tr[data-code]');if(!tr)return;openK(tr.getAttribute('data-code'),tr.getAttribute('data-name'));});
  ['efQ','efReso','efHigh','efKey','efPool','efSector'].forEach(function(id){var e=el(id);if(!e)return;e.addEventListener('input',render);if(e.tagName==='SELECT')e.addEventListener('change',render);e.addEventListener('change',render);});
  if(document.readyState!=='loading')build();else document.addEventListener('DOMContentLoaded',build);
})();
</script>
</body>
</html>
"""

html = (TEMPLATE
        .replace('__EF_ROWS__', json.dumps(rows, ensure_ascii=False))
        .replace('__EF_META__', json.dumps(EF_META, ensure_ascii=False))
        .replace('__LATEST__', str(LATEST or '—')))

out = os.path.join(ROOT, 'earnings_funnel.html')
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)

print('生成 earnings_funnel.html')
print('披露期:', PERIOD, '| 板块快照:', LATEST)
print('共 %d 只 | 高增 %d | 漏斗信号 %d | 重点 %d | 待验证 %d'
      % (EF_META['total'], EF_META['high'], EF_META['funnel'], EF_META['key'], EF_META['wait']))
print('重点样本:')
for r in rows:
    if r['tag'] == '重点·高增+资金确认':
        print('  ', r['name'], r['code'], '| 漏斗:', r['funnel_name'], '| 板块:', r['sec_name'], 'RPS60=', r['sec_rps'])
