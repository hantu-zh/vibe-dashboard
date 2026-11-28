#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读 brief.json + timeline.json，渲染单页网页 index.html"""
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SITE = HERE / "brief_site"
B = json.loads((HERE / "brief.json").read_text(encoding="utf-8"))
T = json.loads((SITE / "timeline.json").read_text(encoding="utf-8"))
D = json.loads((HERE / "digest.json").read_text(encoding="utf-8"))

line_by_sec = {}
for ln in T["lines"]:
    line_by_sec.setdefault(ln["sec"], []).append(ln)
for v in line_by_sec.values():
    v.sort(key=lambda x: x["i"])


def fmt(s):
    m = int(s // 60)
    return f"{m}:{int(s % 60):02d}"


def update_time():
    """页面更新提示：用本期数据抓取时间，和「数据截至」分开标。"""
    raw = D.get("fetched_at") or ""
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return raw[:16].replace("T", " ")


def metrics_html(ms):
    if not ms:
        return ""
    cells = "".join(
        f'<div class="metric"><div class="mk">{m["k"]}</div>'
        f'<div class="mv {m.get("c", "flat")}">{m["v"]}</div></div>'
        for m in ms
    )
    return f'<div class="metrics">{cells}</div>'


def macro_html():
    items = ((D.get("macro") or {}).get("items") or [])
    if not items:
        return ""
    cells = "".join(
        f'<div class="macro-item"><div class="mk">{x["k"]}</div>'
        f'<div class="mv {x["c"]}">{x["v"]}</div>'
        f'<div class="mc {x["c"]}">{x["chg"]}</div></div>'
        for x in items
    )
    return (
        '<section class="card macro-card"><div class="sec-head">'
        '<span class="tag">外围速览</span>'
        '<span class="data-date">截稿时 · 仅作背景，不预测涨跌</span></div>'
        f'<div class="macro-grid">{cells}</div></section>'
    )


SHOTS = {
    "etf": [("etf.png", "资金温度信号"), ("dist20.png", "20日底部样本"), ("dist120.png", "120日底部样本")],
    "veto": [("veto.png", "双确认周度图 · 融资 × 沪深300")],
    "breadth": [("breadth.png", "情绪分近20交易日 · 六因子状态识别")],
    "chan": [],  # 缠论只念结论文字，不截图
    "gjd": [("gjd.png", "头部300ETF占流通市值比 · 存量托底")],
    "regime": [("regime.png", "共振强度"), ("hs300.png", "沪深300")],
    "confirm": [],  # 底部确认是总收束文字，不截图
}


def shot_html(sec_id):
    parts = []
    for name, caption in SHOTS.get(sec_id, []):
        if not (SITE / "img" / name).exists():
            continue
        parts.append(
            f'<figure class="shot"><img src="img/{name}" alt="面板局部图">'
            f'<figcaption>{caption} · 面板局部</figcaption></figure>'
        )
    return "".join(parts)


sections_html = []
for s in B["sections"]:
    lns = line_by_sec.get(s["id"], [])
    if not lns:
        continue
    spans = "".join(
        f'<span class="line" data-i="{l["i"]}" data-start="{l["start"]}" '
        f'data-dur="{l["dur"]}">{l["text"]}</span>'
        for l in lns
    )
    sdate = s.get("data_date", B["data_date"])
    focus = " focus" if s["id"] == "veto" else ""
    sections_html.append(
        f'<section class="card{focus}" data-sec="{s["id"]}">'
        f'<div class="sec-head"><span class="tag">{s["label"]}</span>'
        f'<span class="data-date">数据截至 {sdate}</span></div>'
        f'<h2>{s["headline"]}</h2>'
        f'{metrics_html(s.get("metrics", []))}'
        f'{shot_html(s["id"])}'
        f'<div class="transcript">{spans}</div>'
        f'</section>'
    )

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{B["title"]} · {B["date"]}</title>
<style>
:root{{--bg:#f7f8fa;--card:#ffffff;--ink:#1a1c1e;--sub:#6b7280;--line:#e5e7eb;
--up:#d92b2b;--down:#1a9c62;--hl:#fff4d6;--hlbar:#e8a90b;--accent:#3b5bdb}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
line-height:1.75;padding-bottom:120px}}
.wrap{{max-width:760px;margin:0 auto;padding:28px 20px}}
header{{margin-bottom:22px}}
h1{{font-size:24px;font-weight:600;margin:0 0 6px}}
.meta{{color:var(--sub);font-size:13px}}
.updated{{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;
color:var(--accent);background:#eef2ff;border:1px solid #dbe3ff;border-radius:999px;
padding:3px 11px;margin-bottom:9px}}
.updated .dot{{width:7px;height:7px;border-radius:50%;background:var(--accent)}}
.src{{font-size:12px;color:#9ca3af;margin-top:6px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:20px 22px;margin-bottom:16px;transition:border-color .2s,box-shadow .2s}}
.card.active{{border-color:var(--hlbar);box-shadow:0 0 0 3px rgba(232,169,11,.12)}}
.card.focus{{border-color:#c9d4f5;background:linear-gradient(180deg,#fbfcff,#fff 120px)}}
.card.focus h2{{color:var(--accent)}}
.card.focus .shot img{{border-color:#c9d4f5}}
.sec-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
.tag{{font-size:12px;color:var(--accent);background:#eef2ff;border-radius:6px;padding:2px 10px}}
.data-date{{font-size:12px;color:var(--sub)}}
h2{{font-size:18px;font-weight:600;margin:4px 0 14px}}
.metrics{{display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap}}
.metric{{flex:1;min-width:120px;background:#f9fafb;border-radius:10px;padding:10px 14px}}
.mk{{font-size:12px;color:var(--sub)}}
.mv{{font-size:20px;font-weight:600;margin-top:2px}}
.mv.up{{color:var(--up)}}.mv.down{{color:var(--down)}}.mv.flat{{color:var(--ink)}}
.macro-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}}
.macro-item{{background:#f9fafb;border-radius:10px;padding:10px 12px}}
.mc{{font-size:13px;font-weight:600;margin-top:2px}}
.mc.up{{color:var(--up)}}.mc.down{{color:var(--down)}}
.shot{{margin:14px 0 0;text-align:center}}
.shot img{{width:100%;max-width:100%;border:1px solid var(--line);border-radius:8px;display:block}}
.shot figcaption{{font-size:12px;color:var(--sub);margin-top:4px}}
.transcript{{font-size:15px;color:#374151}}
.line{{display:inline;border-radius:4px;padding:1px 2px;cursor:pointer;transition:background .15s}}
.line:hover{{background:#f3f4f6}}
.line.active{{background:var(--hl);box-shadow:inset 3px 0 0 var(--hlbar);color:#111827}}
.foot{{font-size:12px;color:var(--sub);border-top:1px solid var(--line);
padding-top:12px;margin-top:18px;text-align:center}}
.hist{{margin-top:22px;border:1px solid var(--line);border-radius:12px;background:var(--card);overflow:hidden}}
.hist-h{{font-size:13px;font-weight:600;padding:12px 16px;background:#f3f4f6;color:#374151}}
.hrow{{display:flex;align-items:center;gap:12px;padding:10px 16px;border-top:1px solid var(--line);
text-decoration:none;color:var(--ink);font-size:14px}}
.hrow:hover{{background:#f9fafb}}
.hd{{color:var(--accent);font-variant-numeric:tabular-nums;white-space:nowrap}}
.ht{{flex:1;color:#374151;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.hdur{{font-size:12px;color:var(--sub);white-space:nowrap}}
.player{{position:fixed;left:0;right:0;bottom:0;background:rgba(255,255,255,.97);
border-top:1px solid var(--line);backdrop-filter:blur(8px);z-index:10}}
.pinner{{max-width:760px;margin:0 auto;padding:12px 20px;display:flex;align-items:center;gap:14px}}
button{{cursor:pointer}}
.play{{width:44px;height:44px;flex:0 0 44px;border:none;border-radius:50%;
background:var(--accent);color:#fff;font-size:17px;display:flex;align-items:center;justify-content:center}}
.pmid{{flex:1;min-width:0}}
.bar{{height:5px;background:var(--line);border-radius:3px;overflow:hidden;cursor:pointer}}
.fill{{height:100%;width:0;background:var(--accent);border-radius:3px;transition:width .1s linear}}
.tline{{display:flex;justify-content:space-between;font-size:12px;color:var(--sub);margin-top:5px}}
.rate{{border:1px solid var(--line);background:#fff;border-radius:8px;padding:6px 9px;
font-size:13px;color:var(--ink)}}
@media(max-width:520px){{.wrap{{padding:18px 14px}}h1{{font-size:20px}}.metric{{min-width:100px}}}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{B["title"]}</h1>
    <div class="updated"><span class="dot"></span>本期更新于 {update_time()}（数据抓取时间）</div>
    <div class="meta">{B["date"]} · 全文 {T["total"]:.0f} 秒 · 点句子可跳转播放</div>
    <div class="src">打开页面后点底部播放键开始收听，文字随语音逐句高亮</div>
  </header>
  <div id="cards">{macro_html()}{''.join(sections_html)}</div>
  <div id="history"></div>
  <div class="foot">仅为状态识别与风险揭示，不构成投资建议。市场有风险，投资需谨慎。</div>
</div>
<div class="player">
  <div class="pinner">
    <button class="play" id="play" aria-label="播放">▶</button>
    <div class="pmid">
      <div class="bar" id="bar"><div class="fill" id="fill"></div></div>
      <div class="tline"><span id="cur">0:00</span><span id="dur">{fmt(T["total"])}</span></div>
    </div>
    <select class="rate" id="rate" aria-label="语速">
      <option value="0.75">0.75×</option><option value="1" selected>1×</option>
      <option value="1.25">1.25×</option><option value="1.5">1.5×</option>
    </select>
  </div>
</div>
<audio id="au" src="audio/narration.mp3" preload="metadata"></audio>
<script>
const au=document.getElementById('au');
const lines=[...document.querySelectorAll('.line')];
const cards=[...document.querySelectorAll('.card')];
const play=document.getElementById('play');
const fill=document.getElementById('fill');
const cur=document.getElementById('cur');
const bar=document.getElementById('bar');
const rate=document.getElementById('rate');
const fmt=s=>{{s=Math.max(0,s|0);return (s/60|0)+':'+String(s%60).padStart(2,'0')}};
let active=-1;
function setActive(i){{
  if(i===active)return;active=i;
  lines.forEach(l=>l.classList.toggle('active',+l.dataset.i===i));
  const el=lines.find(l=>+l.dataset.i===i);
  cards.forEach(c=>c.classList.toggle('active',el&&c.dataset.sec===el.closest('.card').dataset.sec));
  if(el)el.scrollIntoView({{block:'center',behavior:'smooth'}});
}}
function tick(){{
  const t=au.currentTime;
  fill.style.width=(t/au.duration*100||0)+'%';
  cur.textContent=fmt(t);
  let hit=-1;
  for(const l of lines){{const s=+l.dataset.start;if(t>=s)hit=+l.dataset.i;else break}}
  setActive(hit);
}}
au.addEventListener('timeupdate',tick);
au.addEventListener('loadedmetadata',tick);
au.addEventListener('ended',()=>{{play.textContent='▶';setActive(-1)}});
play.addEventListener('click',()=>{{
  if(au.paused){{au.play();play.textContent='❚❚'}}else{{au.pause();play.textContent='▶'}}
}});
bar.addEventListener('click',e=>{{
  const r=bar.getBoundingClientRect();
  au.currentTime=(e.clientX-r.left)/r.width*au.duration;
}});
lines.forEach(l=>l.addEventListener('click',()=>{{
  au.currentTime=+l.dataset.start;au.play();play.textContent='❚❚';
}}));
rate.addEventListener('change',()=>au.playbackRate=+rate.value);
</script>
</body>
</html>'''

(SITE / "index.html").write_text(html, encoding="utf-8")
print("OK ->", SITE / "index.html")
