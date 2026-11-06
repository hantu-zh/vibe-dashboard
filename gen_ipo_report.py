# -*- coding: utf-8 -*-
"""gen_ipo_report.py — 每日收盘后由 workflow 调用。

读取 ipo_break.json（gen_ipo_break.py 的产物），对候选（四维共振 + 深度错杀）个股
重拉后复权日 K 线 / 名义 K 线 / 逐日主力净额，机械算交易位，渲染 HTML。
输出稳定文件名 ipo_break_pick.html（覆盖式，无日期），供 /week Tab7 入口链接。

交易位公式（机械、公开、非建议）：
  入场区 = 回踩 MA10/MA20 汇聚区（min(MA10,MA20) 上下 1%）
  止损   = 近期 20 日低点 × 0.98（跌破说明底背离失效 / 破位）
  目标   = 入场中枢 × 1.15（机械修复目标，非估值结论）
ECharts 走 CDN（jsdelivr），报告驻留在 GitHub Pages，在线打开即可。
"""
import json, sys, os, datetime as dt, random

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen_ipo_break as G   # noqa: E402

CN_TZ = dt.timezone(dt.timedelta(hours=8))


def flow_daily_one(code: str, lmt: int = 32):
    """逐日主力净额（近 lmt 天），返回 [(date, 净额元)]；失败返回 None。"""
    secid = ("1." if code[0] in "69" else "0.") + code
    q = "?lmt=%d&klt=101&fields1=f1,f2,f3,f7&fields2=f51,f52&secid=%s" % (lmt, secid)
    try:
        j = G.his_get(q, G.H_HIS, tries=3, path=G.FLOW_PATH)
        kl = (j.get("data") or {}).get("klines") or []
        out = []
        for k in kl:
            p = k.split(",")
            if len(p) >= 6:
                try:
                    out.append((p[0][:10], float(p[1])))
                except ValueError:
                    pass
        return out
    except Exception:                                  # noqa: BLE001
        return None


def _ma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def trade_levels(closes):
    if not closes or len(closes) < 20:
        return None
    ma10, ma20 = _ma(closes, 10), _ma(closes, 20)
    if ma10 is None or ma20 is None:
        return None
    pivot = min(ma10, ma20)
    recent_low = min(closes[-20:])
    return {
        "ma10": round(ma10, 2), "ma20": round(ma20, 2), "pivot": round(pivot, 2),
        "zone": (round(pivot * 0.99, 2), round(ma10 * 1.01, 2)),
        "recent_low": round(recent_low, 2),
        "stop": round(recent_low * 0.98, 2),
        "target": round(pivot * 1.15, 2),
    }


def pick_candidates(items):
    res = [i for i in items if (i.get("resonance") or {}).get("hit")]
    res.sort(key=lambda x: x["adj_pct"])
    pool = list(res)
    if len(pool) < 3:
        extra = [i for i in items if i not in res
                 and (i.get("valuation") or {}).get("low_pe_deep")
                 and not (i.get("valuation") or {}).get("pe_negative")
                 and (i.get("lift") or {}).get("max_ratio", 0) < 0.20]
        extra.sort(key=lambda x: x["adj_pct"])
        pool += extra
    return pool[:5]


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt(v, n=2, suf=""):
    if v is None:
        return "—"
    return ("%." + str(n) + "f") % v + suf


def _flow5_s(ff):
    """主力 5 日净额（元）→ 万元展示；缺失返回 —。"""
    v = (ff or {}).get("prime_inflow_5d")
    if v is None:
        v = (ff or {}).get("prime_inflow_1d")
    if v is None:
        return "—"
    try:
        return _fmt(float(v) / 1e4, 1, "万")
    except (TypeError, ValueError):
        return "—"


def _cmp_rows(items):
    rows = []
    for it in items:
        st = it.get("industry_stat") or {}
        sec = it.get("sector") or {}
        rel = sec.get("relative")
        rel_s = ("%+0.2f%%" % rel) if isinstance(rel, (int, float)) else "—"
        rel_cls = ("r-pos" if isinstance(rel, (int, float)) and rel > 0
                   else "r-neg" if isinstance(rel, (int, float)) and rel < 0 else "")
        rows.append(
        '<tr><td><b>%s</b><br><span class="mut">%s</span></td>'
        '<td class="num">%s</td><td>%s</td>'
        '<td class="num">PR %s/%s · %s</td>'
        '<td class="num %s">%s</td>'
        '<td>%s</td><td>%s</td></tr>' % (
                _esc(it["name"]), it["code"],
                _fmt(it["adj_pct"] * 100, 2, "%"), it.get("depth_tier"),
                (st.get("peer_pr") if st.get("peer_pr") is not None else "—"),
                (st.get("peer_count") or "—"),
                (st.get("peer_env") or "—"),
                rel_cls, rel_s,
                (it.get("catalyst") or {}).get("finance_tag") or "—",
                "%d/4" % ((it.get("resonance") or {}).get("score", 0))))
    return "".join(rows)


def _card(b):
    it = b["it"]
    lv = b["levels"] or {}
    mc = it.get("market_cap") or {}
    ff = it.get("fundflow") or {}
    cat = it.get("catalyst") or {}
    tags = " ".join('<span class="tag %s">%s</span>' % (t["cls"], _esc(t["text"]))
                    for t in (cat.get("tags") or []))
    flags = " ".join('<span class="tag risk">%s</span>' % _esc(f)
                       for f in (it.get("risk_flags") or []))
    fs = ff.get("flow_split") or {}
    raid = fs.get("raid")
    raid_s = ("尾盘偷袭疑似" if raid else ("分时数据缺失" if fs == {} or ff.get("flow_split") is None
                                          else "分时正常"))
    raid_cls = ("warn" if raid else ("muted" if ff.get("flow_split") is None else "ok"))
    lf = it.get("lift") or {}
    holder = lf.get("holder_type") or "—"
    holder_cls = ("risk" if "创投" in holder else "ok" if "国资" in holder else "muted")
    conf = lf.get("holder_confidence") or ""
    conf = "" if conf in ("none", "None", "未知") else conf
    sector = it.get("sector") or {}
    sec_s = ("%s %+0.2f%%" % (sector.get("name") or "—", sector["chg"])
             if isinstance(sector.get("chg"), (int, float)) else "—")
    sec_rel = ("相对板块 %+0.2f%%" % sector["relative"]
               if isinstance(sector.get("relative"), (int, float)) else "—")
    lv_html = (
        '<div class="lv"><div><span>入场区</span><b>%s ~ %s</b></div>'
        '<div><span>止损</span><b>%s</b></div>'
        '<div><span>目标(机械)</span><b>%s</b></div>'
        '<div><span>MA10/MA20</span><b>%s/%s</b></div>'
        '<div><span>近期低点</span><b>%s</b></div></div>' % (
            _fmt(lv.get("zone", (None, None))[0]), _fmt(lv.get("zone", (None, None))[1]),
            _fmt(lv.get("stop")), _fmt(lv.get("target")),
            _fmt(lv.get("ma10")), _fmt(lv.get("ma20")), _fmt(lv.get("recent_low")))
    ) if lv else '<div class="lv mut">K 线数据缺失，交易位无法计算</div>'
    chart_id = "k" + it["code"]
    flow_id = "f" + it["code"]
    return (
        '<div class="pcard"><div class="ph"><b>%s</b> <span class="mut">%s</span> '
        '<span class="badge">%s</span></div>'
        '<div class="pmeta">行业 %s · 流通市值 %s 亿 · 破发 %s%%</div>'
        '<div class="ptags">%s%s</div>'
        '<div class="prow">'
        '<div class="pchart"><div id="%s" class="chart"></div>'
        '<div id="%s" class="chart sm"></div></div>'
        '<div class="pinfo"><div class="pstat">'
        '<div><span>板块联动</span><b>%s</b> <i class="mut">%s</i></div>'
        '<div><span>解禁股东</span><b class="%s">%s</b> <i class="mut">%s</i></div>'
        '<div><span>尾盘</span><b class="%s">%s</b></div>'
        '<div><span>主力(5日)</span><b>%s</b></div>'
        '</div>%s'
        '<div class="pdisc">交易位为固定公式机械测算（回踩 MA10/MA20 作入场、近期低点×0.98 作止损、'
        '中枢×1.15 作目标），非投资建议；股东类型=关键词推测需人工确认。</div>'
        '</div></div></div>' % (
            _esc(it["name"]), it["code"],
            ("共振%d/4" % it["resonance"]["score"]) if it.get("resonance") else "观察",
            it.get("industry"), _fmt((mc.get("float_mv_yi")), 1),
            _fmt(it["adj_pct"] * 100, 2),
            tags, flags,
            chart_id, flow_id,
            _esc(sec_s), _esc(sec_rel),
            holder_cls, _esc(holder), _esc(conf),
            raid_cls, _esc(raid_s),
            _flow5_s(ff),
            lv_html))


def _chart_data(b):
    it = b["it"]
    dates = b.get("dates") or []
    hfq = b.get("closes") or []
    nom = b.get("nominal") or []
    amt = b.get("amounts") or []
    flow = b.get("flow") or []
    return {
        "code": it["code"], "name": it["name"],
        "issue": it.get("issue_price"),
        "dates": dates[-60:], "hfq": [round(c, 2) for c in hfq[-60:]],
        "nom": [round(c, 2) for c in nom[-60:]],
        "amt": [round(a / 1e8, 3) for a in amt[-60:]],
        "flow_dates": [d for d, _ in flow], "flow_val": [round(v / 1e4, 0) for _, v in flow],
    }


def _index_chg():
    """最近两个交易日上证收盘 → 当日涨跌%；fail-safe 返回 None。"""
    q = "?secid=1.000001&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=0&lmt=2"
    try:
        j = G.his_get(q, G.H_HIS, tries=3)
        kl = (j.get("data") or {}).get("klines") or []
        cl = []
        for k in kl:
            try:
                cl.append(float(k.split(",")[1]))
            except (IndexError, ValueError):
                pass
        if len(cl) >= 2 and cl[-2]:
            return cl[-1] / cl[-2] - 1
    except Exception:                                  # noqa: BLE001
        pass
    return None


def _cand_warnings(b):
    """候选个体的数据类反向信号，全部机械推导，无主观判断。"""
    it = b["it"]
    w = []
    lf = it.get("lift") or {}
    if "创投" in (lf.get("holder_type") or ""):
        w.append("解禁股东疑似创投/机构（真雷，关键词推测需人工确认）")
    if (lf.get("max_ratio") or 0) >= 0.20:
        w.append("未来 3 月解禁占比 ≥20%")
    micro = it.get("micro") or {}
    if micro.get("zombie"):
        w.append("僵尸股特征（长期缩量无人问津）")
    fs = (it.get("fundflow") or {}).get("flow_split") or {}
    if fs.get("raid"):
        w.append("尾盘偷袭疑似（尾盘主力净额占全天超五成且当日上涨）")
    if "复权存疑" in (it.get("risk_flags") or []):
        w.append("东财/腾讯复权口径偏差超 2%，价格口径存疑")
    if not b.get("levels"):
        w.append("K 线缺失，交易位无法机械测算")
    for st in (micro.get("div_state"), micro.get("mild_state")):
        if st and "过期" in st:
            w.append("进攻信号已过保质期（%s）" % st)
            break
    return w


def _counter(built, items, meta):
    """反向声音：机械生成的反向证据清单（方法论/大盘 beta/池子动量/样本量/个体警示）。
    每条由数据推导；数据缺失时降级为缺失声明，绝不静默跳过。"""
    bullets = [
        "本池从未做过持有期 / 卖出规则的胜率回测，所有标签均为统计描述，不是买卖信号。"]
    chg = _index_chg()
    if chg is not None:
        if chg > 0.01:
            bullets.append("最近交易日上证 %+.2f%%：破发股普涨时「错杀」可能是大盘 beta 而非个股 alpha，反弹会绑架观望者、追高者会站岗。" % (chg * 100))
        elif chg < -0.01:
            bullets.append("最近交易日上证 %+.2f%%：系统性杀跌绑架一切反弹假设，回踩入场位可能继续失效。" % (chg * 100))
        else:
            bullets.append("最近交易日上证 %+.2f%%，无极端环境；样本信号未被大盘噪音放大。" % (chg * 100))
    else:
        bullets.append("大盘行情缺失（源临时不可达），当日 beta 环境无法评估——反弹日慎追、杀跌日慎接。")
    chgs = sorted((i.get("micro") or {}).get("day_chg")
                  for i in items
                  if isinstance((i.get("micro") or {}).get("day_chg"), (int, float)))
    if chgs:
        med = chgs[len(chgs) // 2]
        bullets.append("池内 %d 只当日中位涨跌 %+.2f%%：普涨日的「温和放量 / 主力回流」可能是整体 beta 的投影，不是个股资金行为。" % (len(chgs), med))
    n_res = sum(1 for i in items if (i.get("resonance") or {}).get("hit"))
    bullets.append("全市场近 3 年破发池今日入池 %d 只、四维共振仅 %d 只——样本极小，任何「胜率」结论都不可外推，2~3 只候选同涨同跌属正常。" % (len(items), n_res))
    flat = [(b["it"]["name"], w) for b in built for w in _cand_warnings(b)]
    if flat:
        bullets.append("候选个体反向信号：" + "；".join("%s—%s" % (k, w) for k, w in flat[:6])
                       + ("；……" if len(flat) > 6 else ""))
    else:
        bullets.append("候选个体暂无数据类反向信号；尾盘 / 板块源不可达时这些维度是盲区，盲区不等于安全。")
    return "".join("<li>%s</li>" % _esc(x) for x in bullets)


def render(built, meta, all_items=None):
    gen_at = dt.datetime.now(CN_TZ).isoformat(timespec="seconds")
    as_of = meta.get("as_of")
    cmp = _cmp_rows([b["it"] for b in built])
    cards = "".join(_card(b) for b in built)
    data = [_chart_data(b) for b in built]
    counter = _counter(built, all_items or [b["it"] for b in built], meta)
    title = "深度破发 · 错杀观察 — 每日候选研报"
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>
* { box-sizing: border-box; } body { margin:0; background:#0f1320; color:#e6e9f0;
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif; line-height:1.5; }
.wrap { max-width:1180px; margin:0 auto; padding:18px 16px 60px; }
h1 { font-size:21px; margin:4px 0 2px; }
.sub { color:#8a90a6; font-size:12px; margin-bottom:14px; }
table { width:100%%; border-collapse:collapse; background:#171c2e; border-radius:10px;
  overflow:hidden; margin-bottom:18px; font-size:13px; }
th,td { padding:8px 10px; text-align:left; border-bottom:1px solid #232a40; }
th { background:#1d2540; color:#aab2cc; font-weight:600; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
.r-pos { color:#ff6b6b; } .r-neg { color:#3fbf6f; }
.mut { color:#8a90a6; font-size:11px; }
.pcard { background:#171c2e; border:1px solid #232a40; border-radius:12px;
  padding:14px; margin-bottom:16px; }
.ph { font-size:16px; } .ph .badge { background:#3a2a55; color:#c9a3ff; font-size:11px;
  padding:2px 8px; border-radius:6px; margin-left:6px; }
.pmeta { color:#8a90a6; font-size:12px; margin:2px 0 8px; }
.ptags .tag { display:inline-block; font-size:11px; padding:2px 7px; border-radius:6px;
  margin:2px 4px 2px 0; }
.tag.good { background:#16331f; color:#5fe08a; } .tag.warn { background:#332a12; color:#ffcf6b; }
.tag.risk { background:#33181a; color:#ff8a8a; } .tag.muted { background:#222838; color:#9aa0b6; }
.tag.ok { background:#16331f; color:#5fe08a; }
.prow { display:flex; gap:14px; flex-wrap:wrap; }
.pchart { flex:1 1 460px; min-width:320px; }
.chart { width:100%%; height:300px; } .chart.sm { height:150px; }
.pinfo { flex:1 1 320px; min-width:280px; }
.pstat div { display:flex; justify-content:space-between; padding:5px 0;
  border-bottom:1px solid #232a40; font-size:13px; }
.pstat span { color:#8a90a6; } .pstat b { font-variant-numeric:tabular-nums; }
.pstat .mut { font-style:normal; font-size:11px; }
.lv { display:grid; grid-template-columns:1fr 1fr; gap:6px 14px; margin-top:10px;
  background:#131829; border-radius:10px; padding:10px; }
.lv div { font-size:12px; } .lv span { color:#8a90a6; display:block; font-size:11px; }
.lv b { font-size:14px; font-variant-numeric:tabular-nums; }
.lv.mut { color:#8a90a6; }
.pdisc { color:#8a90a6; font-size:11px; margin-top:10px; line-height:1.6; }
.note { background:#1a1f33; border-left:3px solid #ffb74d; padding:10px 12px;
  border-radius:8px; color:#c9cde0; font-size:12px; margin-bottom:18px; }
.counter { background:#241619; border-left:3px solid #ff6b6b; padding:10px 12px;
  border-radius:8px; color:#e0c9cd; font-size:12px; margin-bottom:18px; }
.counter ul { margin:6px 0 0; padding:0; }
.counter li { margin:4px 0 4px 16px; line-height:1.6; }
footer { color:#6b7186; font-size:11px; margin-top:24px; line-height:1.7; }
</style></head><body><div class="wrap">
<h1>%s</h1>
<div class="sub">生成时间 %s · 数据截至 %s · 规则版本 %s · 全市场近 3 年 IPO 破发池</div>
<div class="note">本研报由每日收盘后 workflow 用固定公式机械重算，<b>不是买卖建议</b>。
交易位（入场/止损/目标）为机械测算；「解禁股东类型」为关键词推测、需人工确认；
板块联动、尾盘拆分均为统计标签。破发≠低估，注册制下高发行 PE 破发可能是估值回归。
市场有风险，决策需独立判断。</div>
<div class="counter"><b>反向声音（机械生成的反向证据，防止只看多信号吃暗亏）</b><ul>%s</ul></div>
<table><thead><tr><th>标的</th><th>含权息破发</th><th>深度</th><th>同行相对</th>
<th>板块联动</th><th>业绩催化</th><th>共振</th></tr></thead><tbody>%s</tbody></table>
<div class="cards">%s</div>
<footer>数据来源：东财 push2his 后复权 K 线 / fflow 资金流、datacenter-web 财报与解禁、
腾讯 qt.gtimg 板块归属、东财板块行情。所有标签均为统计描述，不构成投资建议。
日内分时数据缺失时尾盘偷袭标记降级；K 线数据缺失时交易位不计算。</footer>
</div>
<script>
var DATA = %s;
function candlestick(el, d){
  var up='#e24b4a', down='#3fbf6f';
  var ohlc = d.hfq.map(function(c,i){ return [i, c, c, c, c]; }); // 仅有收盘，用收盘占位
  var chart = echarts.init(el);
  chart.setOption({
    backgroundColor:'transparent', grid:{left:48,right:12,top:18,bottom:46},
    tooltip:{trigger:'axis'}, xAxis:{type:'category',data:d.dates,axisLabel:{color:'#8a90a6',fontSize:9}},
    yAxis:{scale:true,axisLabel:{color:'#8a90a6'},splitLine:{lineStyle:{color:'#232a40'}}},
    dataZoom:[{type:'inside'}],
    series:[{type:'line',data:d.hfq,smooth:true,symbol:'none',
      lineStyle:{color:'#7aa2ff',width:1.6},
      areaStyle:{color:'rgba(122,162,255,0.12)'},
      markLine:{silent:true,symbol:'none',lineStyle:{color:'#ffb74d',type:'dashed'},
        data:[{yAxis:(d.nom && d.nom.length) ? d.nom[d.nom.length-1] : d.issue,
          label:{formatter:'发行价',color:'#ffb74d',fontSize:10}}]}},
      {type:'bar',data:d.amt.map(function(v){return {value:v,itemStyle:{color:'#2a3354'}};}),
        xAxisIndex:0,yAxisIndex:0,barWidth:'60%%'}]
  });
  return chart;
}
DATA.forEach(function(d){
  var el = document.getElementById('k'+d.code);
  if (el && d.hfq.length >= 2) { candlestick(el, d); }
  else if (el) { el.innerHTML = '<div style="height:100%%;display:flex;align-items:center;'
    + 'justify-content:center;color:#8a90a6;font-size:13px;">K 线数据缺失（数据源临时不可达）</div>'; }
  var fe = document.getElementById('f'+d.code);
  if(fe && d.flow_val.length >= 2){
    var c = echarts.init(fe);
    c.setOption({backgroundColor:'transparent',grid:{left:48,right:12,top:10,bottom:30},
      tooltip:{trigger:'axis'},xAxis:{type:'category',data:d.flow_dates,axisLabel:{color:'#8a90a6',fontSize:9}},
      yAxis:{axisLabel:{color:'#8a90a6'},splitLine:{lineStyle:{color:'#232a40'}}},
      series:[{type:'bar',data:d.flow_val.map(function(v){return {value:v,
        itemStyle:{color: v>=0? '#3fbf6f':'#e24b4a'}};}),barWidth:'60%%'}]});
  } else if (fe) {
    fe.innerHTML = '<div style="height:100%%;display:flex;align-items:center;'
      + 'justify-content:center;color:#8a90a6;font-size:12px;">逐日资金流缺失（数据源临时不可达）</div>';
  }
});
</script></body></html>""" % (
        _esc(title), title, gen_at, _esc(as_of or ""),
        _esc(meta.get("rules_version")), counter, cmp, cards,
        json.dumps(data, ensure_ascii=False))
    return html


def main():
    src = os.path.join(HERE, "ipo_break.json")
    d = json.load(open(src, encoding="utf-8"))
    items = d["items"]
    cands = pick_candidates(items)
    # v5.1：优先用主生成器落盘的 sidecar 缓存（零网络），东财限流也不空窗；
    # sidecar 缺失（如本地单跑）才回退到网络抓取（东财→腾讯兜底）。
    side = {}
    try:
        side = json.load(open(os.path.join(HERE, "ipo_break_kline.json"),
                              encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        side = {}
    built = []
    for it in cands:
        k = side.get(it["code"]) or {}
        if k.get("closes") and len(k["closes"]) >= 2:
            closes = k["closes"]
            dates, amounts = k.get("dates") or [], k.get("amounts") or []
            nominal = k.get("nominal") or []
            flow = [(p[0], p[1]) for p in (k.get("flow") or [])
                    if len(p) >= 2] or None
        else:
            row = {"code": it["code"], "issue_price": it.get("issue_price"),
                   "listing_date": it.get("listing_date", "")}
            em = None
            try:
                em = G.hfq_em(row)
            except Exception:                              # noqa: BLE001
                em = None
            if not em:
                try:
                    tx = G.hfq_tencent(row)
                except Exception:                          # noqa: BLE001
                    tx = None
                if tx and len(tx[1]) >= 2:
                    em = {"dates": tx[0], "closes": tx[1],
                          "amounts": [], "nominal_closes": []}
            closes = (em or {}).get("closes") or []
            dates = (em or {}).get("dates") or []
            amounts = (em or {}).get("amounts") or []
            nominal = (em or {}).get("nominal_closes") or []
            flow = flow_daily_one(it["code"], 32)
        built.append({
            "it": it,
            "closes": closes, "nominal": nominal,
            "amounts": amounts, "dates": dates,
            "flow": flow or [], "levels": trade_levels(closes),
        })
    html = render(built, d["meta"], items)
    out = os.path.join(HERE, "ipo_break_pick.html")
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    print("[ipo_report] wrote %s, candidates=%d, with_kline=%d, sidecar=%d"
          % (out, len(built), sum(1 for b in built if b["closes"]),
             sum(1 for b in built if (side.get(b["it"]["code"]) or {}).get("closes"))))


if __name__ == "__main__":
    main()
