/* ETF FLOW LAB — 前端渲染 */
'use strict';

const C = {
  pink: '#ff2d6a', cyan: '#00e0d4', gold: '#ffb020',
  up: '#ff3b52', down: '#1dd6a0',
  text: '#eaeaf3', muted: '#7c7c93', line: 'rgba(255,255,255,.07)',
  cat: ['#ff2d6a', '#00e0d4', '#ffb020', '#7c6cff', '#34d399', '#f97316',
        '#38bdf8', '#e879f9', '#a3e635', '#fda4af', '#94a3b8'],
};

const $ = id => document.getElementById(id);
const charts = {};

/* ---------- 格式化 ---------- */
const isNum = v => typeof v === 'number' && isFinite(v);
const yi = v => isNum(v) ? v / 1e8 : null;
function money(v, digits = 1) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  if (a >= 1e8) return (v / 1e8).toFixed(digits) + ' 亿';
  if (a >= 1e4) return (v / 1e4).toFixed(0) + ' 万';
  return v.toFixed(0);
}
function signed(v, digits = 1) {
  if (!isNum(v)) return '—';
  return (v > 0 ? '+' : v < 0 ? '−' : '') + money(Math.abs(v), digits);
}
function pct(v, digits = 2) {
  if (!isNum(v)) return '—';
  return (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(digits) + '%';
}
const cls = v => !isNum(v) ? '' : v > 0 ? 'up' : v < 0 ? 'down' : '';
/* 榜单专用：符号与数字分离排版，带淡色底，红进绿出一眼区分 */
function flowPill(v, digits = 2) {
  if (!isNum(v)) return '<span class="flow-pill flat">—</span>';
  const up = v > 0;
  return `<span class="flow-pill ${up ? 'up' : 'down'}">`
    + `<b class="sign">${up ? '+' : '−'}</b>${money(Math.abs(v), digits)}</span>`;
}

/* ---------- 主题 ---------- */
function axisCommon() {
  return {
    axisLine: { lineStyle: { color: 'rgba(255,255,255,.12)' } },
    axisTick: { show: false },
    axisLabel: { color: C.muted, fontSize: 11 },
    splitLine: { lineStyle: { color: 'rgba(255,255,255,.05)' } },
  };
}
function baseOption() {
  return {
    backgroundColor: 'transparent',
    textStyle: { color: C.text, fontFamily: 'Inter, PingFang SC, Microsoft YaHei, sans-serif' },
    animationDuration: 600,
    tooltip: {
      backgroundColor: 'rgba(14,14,22,.95)',
      borderColor: 'rgba(255,255,255,.12)',
      borderWidth: 1,
      textStyle: { color: C.text, fontSize: 12 },
      axisPointer: { lineStyle: { color: 'rgba(255,255,255,.18)' } },
    },
  };
}
function mount(id, option) {
  const el = $(id);
  if (!el) return;
  const c = charts[id] || (charts[id] = echarts.init(el, null, { renderer: 'canvas' }));
  c.setOption({ ...baseOption(), ...option }, true);
  return c;
}

/* ---------- 结论 ---------- */
function verdict(d) {
  const m = d.market || {};
  const radar = d.radar || [];
  const hot = radar.filter(r => isNum(r.ratio20) && r.ratio20 > 1.3).length;
  const cold = radar.filter(r => isNum(r.ratio20) && r.ratio20 < 0.7).length;
  const r20 = m.ratio20;

  let volWord = '量能平稳', volCls = '';
  if (isNum(r20)) {
    if (r20 > 1.35) { volWord = '明显放量'; volCls = 'hot'; }
    else if (r20 > 1.1) { volWord = '温和放量'; volCls = ''; }
    else if (r20 < 0.75) { volWord = '明显缩量'; volCls = 'cold'; }
    else if (r20 < 0.92) { volWord = '小幅缩量'; }
  }

  const flow = m.total_flow;
  const best = (d.categories || [])
    .filter(c => isNum(c.flow))
    .sort((a, b) => b.flow - a.flow)[0];
  const worst = (d.categories || [])
    .filter(c => isNum(c.flow))
    .sort((a, b) => a.flow - b.flow)[0];

  const flowTxt = !isNum(flow) ? '净申赎数据待补齐'
    : flow >= 0 ? `<i>净申购 ${money(flow)}</i>`
      : `<i>净赎回 ${money(Math.abs(flow))}</i>`;

  let lead = '';
  if (best && worst && best !== worst) {
    if (isNum(flow) && flow > 0 && isNum(best.flow) && best.flow > 0)
      lead = `，<em>${best.name}</em> <span class="win">吸金最多</span>（${signed(best.flow)}）`;
    else if (isNum(worst.flow) && worst.flow < 0)
      lead = `，<em>${worst.name}</em> <span class="lose">流出最多</span>（${signed(worst.flow)}）`;
  }

  const ratioTxt = isNum(r20) ? `，为 20 日均量的 <b>${r20.toFixed(2)} 倍</b>` : '';
  $('verdict').innerHTML =
    `核心池成交 <b>${money(m.core_amount)}</b>${ratioTxt}，${volWord}；全池 ${flowTxt}${lead}。`;

  const bits = [];
  if (isNum(m.up_count)) bits.push(`核心池上涨 ${m.up_count}/${radar.length} 只`);
  if (hot) bits.push(`放量 ${hot} 只`);
  if (cold) bits.push(`缩量 ${cold} 只`);
  if (isNum(r20) && isNum(m.ratio60)) bits.push(`相对 60 日均量 ${m.ratio60.toFixed(2)} 倍`);
  $('verdictSub').textContent = bits.join(' · ');

  /* 两个市场对照：成交量（二级换手） vs 净申赎（一级份额）——最容易被混读的一对 */
  const es = $('heroSplit');
  if (es) {
    es.innerHTML = `
      <div class="hs-cell">
        <div class="hs-k">二级市场 · 成交额 <span class="hs-tag">换手</span></div>
        <div class="hs-v">${money(m.core_amount)}</div>
        <div class="hs-x">钱在<b>投资者之间</b>倒手，基金份额不变。只说明今天有多热闹。</div>
      </div>
      <div class="hs-cell accent">
        <div class="hs-k">一级市场 · 净申赎 <span class="hs-tag">份额增减</span></div>
        <div class="hs-v ${cls(flow)}">${signed(flow)}</div>
        <div class="hs-x">钱<b>真的进出基金</b>，份额跟着增减。这才是「资金流向」。</div>
      </div>`;
  }

  /* 四格细节：量能 / 申赎结构 / 个基结构 / 怎么用 */
  const cats = (d.categories || []).filter(c => isNum(c.flow));
  const inflowCats = cats.filter(c => c.flow > 0).length;
  const grossOut = cats.filter(c => c.flow < 0).reduce((s, c) => s + Math.abs(c.flow), 0);
  const sorted = radar.filter(r => isNum(r.ratio20)).sort((a, b) => b.ratio20 - a.ratio20);
  const hottest = sorted[0], coldest = sorted[sorted.length - 1];

  let peakTxt = '';
  if (isNum(m.core_amount) && d.history && d.history.dates && d.history.core_amount) {
    const hd = d.history.dates, ha = d.history.core_amount;
    let pi = -1;
    for (let i = Math.max(0, hd.length - 20); i < hd.length; i++)
      if (isNum(ha[i]) && (pi < 0 || ha[i] > ha[pi])) pi = i;
    if (pi >= 0) peakTxt = `近 20 日峰值 ${money(ha[pi])}（${hd[pi].slice(5)}）`;
  }

  const volTagCls = isNum(r20) ? (r20 > 1.3 ? 'up' : r20 < 0.8 ? 'down' : '') : '';
  const facts = [
    {
      k: '量能水位',
      tag: volWord, tagCls: volTagCls,
      v: `${money(m.core_amount)}${isNum(r20) ? `<span class="fx"> · ${r20.toFixed(2)}×20日均</span>` : ''}`,
      x: `20 日均量 ${money(m.ma20)}，60 日均量 ${money(m.ma60)}${isNum(m.ratio60) ? `（${m.ratio60.toFixed(2)}×）` : ''}。${peakTxt}`,
      why: '成交额属于二级市场：只反映多空交手规模，不代表有资金进出基金。',
    },
    {
      k: '一级申赎',
      tag: isNum(flow) ? (flow > 0 ? '净流入' : '净流出') : '', tagCls: cls(flow),
      v: `<span class="${cls(flow)}">${signed(flow)}</span>`,
      x: cats.length
        ? `${cats.length} 类中 ${inflowCats} 类净流入、${cats.length - inflowCats} 类净流出`
          + (worst && isNum(worst.flow) && worst.flow < 0 && grossOut > 0
            ? `；最大流出 <b>${worst.name} ${signed(worst.flow)}</b>，占总流出 ${(Math.abs(worst.flow) / grossOut * 100).toFixed(0)}%`
            : '')
        : '分类数据待补齐',
      why: '净申赎＝Δ份额 × 价格，份额真的增减，代表钱进出了基金。',
    },
    {
      k: '个基结构',
      tag: hot ? `放量 ${hot} 只` : '', tagCls: '',
      v: `${isNum(m.up_count) ? m.up_count : '—'}<span class="fx"> / ${radar.length} 上涨</span>`,
      x: hottest && coldest
        ? `最热 <b>${hottest.name}</b> ${hottest.ratio20.toFixed(2)}×（${pct(hottest.pct)}）；最冷 <b>${coldest.name}</b> ${coldest.ratio20.toFixed(2)}×（${pct(coldest.pct)}）`
        : '—',
      why: '量比高而价格跌＝分歧大（有人抢着走）；量比高且价格涨＝有承接。',
    },
    (() => {
      let tag = '观望', txt = '量能与资金都平淡，属观望区。方向要等一根放量柱或一次大额申赎来定。';
      if (isNum(r20) && isNum(flow)) {
        if (r20 < 0.92 && flow < 0) { tag = '退潮消化'; txt = `缩量 + 净赎回＝退潮消化，不是恐慌。等成交额回到 20 日均量（约 ${money(m.ma20)}／日）之上再谈方向。`; }
        else if (r20 > 1.3 && flow > 0) { tag = '放量吸筹'; txt = '放量 + 净申购＝有资金借道建仓。指数同步走强是承接确认，指数仍跌则是左侧抄底。'; }
        else if (r20 > 1.3 && flow < 0) { tag = '放量出逃'; txt = '放量 + 净赎回＝边打边撤，分歧最大。次日若不能缩量止跌，容易延续。'; }
        else if (r20 < 0.75) { tag = '无量僵持'; txt = '明显缩量＝多空都懒得动。极致缩量常见于阶段低点附近，但必须放量确认。'; }
      }
      return { k: '怎么用', tag, tagCls: '', v: `<span class="fx-lead">${tag}</span>`, x: txt, why: '单日读数只能定「状态」，方向要靠连续 2–3 日的量价＋申赎组合确认。' };
    })(),
  ];
  const ef = $('heroFacts');
  if (ef) {
    ef.innerHTML = facts.map(f => `
      <div class="hf">
        <div class="hf-k">${f.k}${f.tag ? `<span class="hf-tag ${f.tagCls || ''}">${f.tag}</span>` : ''}</div>
        <div class="hf-v">${f.v}</div>
        <div class="hf-x">${f.x}</div>
        <div class="hf-why">${f.why}</div>
      </div>`).join('');
  }
}

/* ---------- KPI ---------- */
function kpis(d) {
  const m = d.market || {};
  const radar = d.radar || [];
  const hot = radar.filter(r => isNum(r.ratio20) && r.ratio20 > 1.3).length;
  const cards = [
    {
      label: '核心池成交额', value: money(m.core_amount, 1),
      sub: isNum(m.ma20) ? `20 日均量 ${money(m.ma20)}` : '—',
      cls: '',
    },
    {
      label: '量比（20 日）', value: isNum(m.ratio20) ? m.ratio20.toFixed(2) : '—',
      sub: isNum(m.ratio60) ? `60 日 ${m.ratio60.toFixed(2)} 倍` : '—',
      cls: isNum(m.ratio20) && m.ratio20 > 1.3 ? 'hot' : isNum(m.ratio20) && m.ratio20 < 0.75 ? 'cool' : '',
    },
    {
      label: '全池净申赎', value: signed(m.total_flow, 1),
      sub: `${d.fund_count || 0} 只 ETF 合计`,
      cls: isNum(m.total_flow) && m.total_flow > 0 ? 'hot' : 'cool',
      colorValue: true,
    },
    {
      label: '放量标的', value: `${hot} <small>/ ${radar.length}</small>`,
      sub: `上涨 ${m.up_count ?? '—'} 只`,
      cls: '',
      raw: true,
    },
  ];
  $('kpis').innerHTML = cards.map(c => `
    <div class="kpi ${c.cls}">
      <div class="label">${c.label}</div>
      <div class="value" ${c.colorValue ? `style="color:${isNum(m.total_flow) && m.total_flow > 0 ? 'var(--up)' : 'var(--down)'}"` : ''}>${c.raw ? c.value : c.value}</div>
      <div class="sub">${c.sub}</div>
    </div>`).join('');
}

/* ---------- 雷达 ---------- */
function radarView(d) {
  const rows = (d.radar || []).slice();
  const max = Math.max(2, ...rows.map(r => r.ratio20 || 0));
  $('radar').innerHTML = rows.map(r => {
    const ratio = isNum(r.ratio20) ? r.ratio20 : null;
    const kind = ratio === null ? 'cold' : ratio > 1.3 ? '' : ratio < 0.7 ? 'cold' : 'normal';
    const w = ratio === null ? 0 : Math.min(100, (ratio / max) * 100);
    return `<div class="rrow">
      <div class="rname">${r.name}<span>${r.code}</span></div>
      <div class="rbar"><i class="${kind}" style="width:${w.toFixed(1)}%"></i></div>
      <div class="rratio">${ratio === null ? '—' : ratio.toFixed(2)}</div>
      <div class="rpct ${cls(r.pct)}">${pct(r.pct)}</div>
      <div class="rflow ${cls(r.flow)}">${signed(r.flow)}</div>
    </div>`;
  }).join('');
}

/* ---------- 分类资金流（堆叠柱） ---------- */
function flowChart(d) {
  const hist = d.history || {};
  const dates = (hist.dates || []).slice(-60);
  const cats = d.categories || [];
  const series = cats.map((c, i) => {
    const s = (c.series || []).slice(-60);
    const pad = dates.length - s.length;
    const data = Array(Math.max(0, pad)).fill(null).concat(s).map(v => isNum(v) ? +(v / 1e8).toFixed(2) : null);
    return {
      name: c.name, type: 'bar', stack: 'flow', data,
      itemStyle: { color: C.cat[i % C.cat.length] },
      barMaxWidth: 14,
      emphasis: { focus: 'series' },
    };
  });

  mount('chartFlow', {
    grid: { left: 52, right: 16, top: 34, bottom: 46 },
    legend: {
      type: 'scroll', top: 0, textStyle: { color: C.muted, fontSize: 10.5 }, itemWidth: 9, itemHeight: 9, itemGap: 10,
    },
    tooltip: {
      trigger: 'axis',
      valueFormatter: v => (isNum(v) ? v.toFixed(1) + ' 亿' : '—'),
    },
    xAxis: {
      type: 'category', data: dates.map(x => x.slice(5)), ...axisCommon(),
      axisLabel: { ...axisCommon().axisLabel, interval: Math.max(0, Math.floor(dates.length / 8)) },
    },
    yAxis: { type: 'value', ...axisCommon(), axisLabel: { ...axisCommon().axisLabel, formatter: v => v + '亿' } },
    dataZoom: [{ type: 'inside' }],
    series,
  });
}

/* ---------- 累计净流入排行 ---------- */
function cumChart(d) {
  const rows = (d.categories || [])
    .filter(c => isNum(c.cum60))
    .map(c => ({ name: c.name, v: +(c.cum60 / 1e8).toFixed(1) }))
    .sort((a, b) => a.v - b.v);
  mount('chartCum', {
    grid: { left: 62, right: 58, top: 10, bottom: 24 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: v => v.toFixed(1) + ' 亿' },
    xAxis: { type: 'value', ...axisCommon(), axisLabel: { show: false }, splitLine: { show: false } },
    yAxis: {
      type: 'category', data: rows.map(r => r.name), ...axisCommon(),
      axisLabel: { color: C.text, fontSize: 11.5 }, splitLine: { show: false },
    },
    series: [{
      type: 'bar', data: rows.map(r => ({
        value: r.v,
        itemStyle: { color: r.v >= 0 ? C.up : C.down, borderRadius: r.v >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] },
      })),
      barMaxWidth: 13,
      label: {
        show: true, position: 'right', color: C.muted, fontSize: 10.5,
        formatter: p => (p.value > 0 ? '+' : '') + p.value,
      },
    }],
  });
}

/* ---------- 成交额趋势 ---------- */
/* 三套配色（右上角按钮切换，localStorage 记忆）：
   pro     专业机构风 —— 砖红柱 + 冰蓝指数线，克制稳重；
   minimal 科技极简风 —— 珊瑚红柱 + 薄荷绿指数线，现代高对比；
   morandi 温柔暗夜风 —— 莫兰迪低饱和，护眼。
   通用规则：网格线弱化到 rgba(255,255,255,.05)；图层从下到上 = 灰柱/红柱 → 60日线 → 20日线 → 指数线（最顶层）；
   图例文字颜色与实际 series 颜色一一对应。 */
const TREND_PALETTES = {
  pro:     { hot: '#d32f2f', cold: '#4a4a4a', ma20: '#ffc107', ma60: '#9575cd', idx: '#5b9bd5' },
  minimal: { hot: '#ff5252', cold: '#424242', ma20: '#ffab40', ma60: '#616161', idx: '#00e676' },
  morandi: { hot: '#ad1457', cold: '#37474f', ma20: '#d4c4a8', ma60: '#546e7a', idx: '#78909c' },
};
const hexA = (hex, a) => {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
};
function currentPalette() {
  try { return TREND_PALETTES[localStorage.getItem('etfflow_palette')] || TREND_PALETTES.pro; }
  catch (_) { return TREND_PALETTES.pro; }
}
function initPaletteSwitch() {
  const box = $('paletteSwitch');
  if (!box) return;
  const cur = (localStorage.getItem('etfflow_palette') || 'pro');
  box.querySelectorAll('button').forEach(b => {
    b.classList.toggle('on', b.dataset.pal === cur);
    b.addEventListener('click', () => {
      localStorage.setItem('etfflow_palette', b.dataset.pal);
      box.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      if (window.__snapshot) trendChart(window.__snapshot);
    });
  });
}

function trendChart(d) {
  const P = currentPalette();
  const h = d.history || {};
  const dates = (h.dates || []).map(x => x.slice(5));
  const toYi = a => (a || []).map(v => isNum(v) ? +(v / 1e8).toFixed(1) : null);

  const amount = toYi(h.core_amount);
  const ma20 = toYi(h.ma20);
  const ma60 = toYi(h.ma60);
  const index = h.index || [];

  // 放量日（成交额高于 20 日均量）用主色亮柱，缩量日用弱化的灰柱衬托
  const bars = amount.map((v, i) => {
    const base = ma20[i];
    const hot = isNum(v) && isNum(base) && v > base;
    return {
      value: v,
      itemStyle: {
        borderRadius: [3, 3, 0, 0],
        color: hot
          ? new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: hexA(P.hot, .92) },
            { offset: 1, color: hexA(P.hot, .28) }])
          : new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: hexA(P.cold, .72) },
            { offset: 1, color: hexA(P.cold, .16) }]),
      },
    };
  });

  mount('chartTrend', {
    grid: { left: 62, right: 66, top: 44, bottom: 40 },
    legend: {
      top: 0, itemWidth: 14, itemHeight: 8, itemGap: 18,
      textStyle: { fontSize: 11 },
      data: [
        { name: '核心池成交额', textStyle: { color: P.hot } },
        { name: '20 日均量', textStyle: { color: P.ma20 } },
        { name: '60 日均量', textStyle: { color: hexA(P.ma60, .9) } },
        { name: '上证指数', textStyle: { color: P.idx } },
      ],
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,255,255,.04)' } },
      formatter: params => {
        const i = params[0]?.dataIndex ?? 0;
        const v = amount[i], m20 = ma20[i];
        const ratio = (isNum(v) && isNum(m20) && m20) ? (v / m20) : null;
        const idx = index[i];
        const prev = i > 0 ? index[i - 1] : null;
        const idxPct = (isNum(idx) && isNum(prev) && prev) ? ((idx / prev - 1) * 100) : null;
        const lines = [
          `<div style="color:${C.muted};font-size:11px;margin-bottom:6px">${h.dates?.[i] || ''}</div>`,
          `<div><span style="color:${P.hot}">●</span> 成交额 <b>${isNum(v) ? v.toFixed(1) : '—'}</b> 亿`
          + (ratio !== null ? ` <span style="color:${ratio > 1 ? C.up : C.down}">(${ratio.toFixed(2)}×20日均)</span>` : '')
          + `</div>`,
          `<div><span style="color:${P.ma20}">●</span> 20 日均量 ${isNum(m20) ? m20.toFixed(1) : '—'} 亿</div>`,
          `<div><span style="color:${P.idx}">●</span> 上证指数 ${isNum(idx) ? idx.toFixed(1) : '—'}`
          + (idxPct !== null ? ` <span style="color:${idxPct >= 0 ? C.up : C.down}">${idxPct >= 0 ? '+' : ''}${idxPct.toFixed(2)}%</span>` : '')
          + `</div>`,
        ];
        return lines.join('');
      },
    },
    xAxis: {
      type: 'category', data: dates, ...axisCommon(),
      axisLabel: { ...axisCommon().axisLabel, interval: Math.floor(dates.length / 9) },
    },
    yAxis: [
      {
        type: 'value', name: '成交额 · 亿', nameTextStyle: { color: hexA(P.hot, .85), fontSize: 10, padding: [0, 0, 4, 0] },
        ...axisCommon(),
        axisLabel: { ...axisCommon().axisLabel, color: hexA(P.hot, .62) },
        splitLine: { lineStyle: { color: 'rgba(255,255,255,.05)' } },
      },
      {
        type: 'value', name: '上证指数', nameTextStyle: { color: hexA(P.idx, .85), fontSize: 10, padding: [0, 0, 4, 0] },
        scale: true, ...axisCommon(),
        axisLabel: { ...axisCommon().axisLabel, color: hexA(P.idx, .7) },
        splitLine: { show: false },
      },
    ],
    dataZoom: [{ type: 'inside' }],
    series: [
      // 图层从下到上：灰/红柱(z1) → 60日线(z2) → 20日线(z3) → 指数线(z5，最顶层)
      { name: '核心池成交额', type: 'bar', data: bars, barMaxWidth: 9, z: 1 },
      {
        name: '60 日均量', type: 'line', data: ma60, z: 2,
        smooth: true, symbol: 'none', lineStyle: { color: hexA(P.ma60, .9), width: 1.3, type: [5, 4] },
      },
      {
        name: '20 日均量', type: 'line', data: ma20, z: 3,
        smooth: true, symbol: 'none', lineStyle: { color: P.ma20, width: 1.7 },
      },
      {
        name: '上证指数', type: 'line', yAxisIndex: 1, data: index, z: 5,
        smooth: 0.2, symbol: 'none',
        // 光晕压到极轻，让折线呈现出配色本身的颜色（与图例文字同色）
        lineStyle: { color: P.idx, width: 2.1, shadowColor: hexA(P.idx, .16), shadowBlur: 5 },
        // 指数线下方淡淡的同色渐变，增强立体感但不抢柱子的戏
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: hexA(P.idx, .1) },
            { offset: 1, color: hexA(P.idx, 0) }]),
        },
      },
    ],
  });
}

/* ---------- 分时 ---------- */
/* ---------- 资金流温度 · 历史分位预警 ---------- */
const SIG_LABEL = {
  bottom: '底部承接区', top: '顶部出逃区',
  warm: '流入偏多（暖）', cool: '流出偏空（冷）', neutral: '中性区',
};
const sg = v => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1) + '%';

function signalPanel(s) {
  const st = s.now || {};
  const light = $('sigLight');
  light.className = 'light ' + (st.light === 'buy' ? 'buy' : st.light === 'sell' ? 'sell' : 'idle');
  $('sigState').textContent = SIG_LABEL[st.state] || '—';
  $('sigMeta').textContent = isNum(st.ratio)
    ? ` · 流温度 ${st.ratio.toFixed(1)}%（历史 ${st.pr} 分位）· 指数 120 日 ${st.pidx} 分位`
    : '';

  const dates = (s.dates || []).map(x => x.slice(0, 7));
  const ratio = s.ratio || [];
  const pidx = s.pidx || [];
  const pos = {};
  s.dates.forEach((x, i) => { pos[x] = i; });

  // ECharts category 轴上稀疏散点必须用 [x索引, y值] 形式，否则会整体挤到轴开头
  const mk = list => list.map(d => {
    const i = pos[d];
    return i == null ? null : [i, ratio[i]];
  }).filter(Boolean);

  const L = s.levels || {};
  // 反向后 25/50/75 三条线挤在 -3~6% 窄带里，标签只留 90/50/10，避免叠成一团
  const lines = [['p90', '90%分位·承接'], ['p75', ''], ['p50', '50%'], ['p25', ''], ['p10', '10%分位·出逃']]
    .filter(([k]) => isNum(L[k]))
    .map(([k, label]) => ({
      yAxis: L[k],
      lineStyle: { color: k === 'p50' ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.10)', type: [5, 5], width: 1 },
      label: label
        ? { formatter: label, color: 'rgba(255,255,255,.4)', fontSize: 10, position: 'insideStartTop' }
        : { show: false },
    }));

  mount('chartSignal', {
    grid: { left: 56, right: 20, top: 30, bottom: 40 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: 'rgba(255,255,255,.18)' } },
      formatter: ps => {
        const i = ps[0]?.dataIndex ?? 0;
        const r = ratio[i], p = pidx[i], d = s.dates[i];
        return `<div style="color:${C.muted};font-size:11px;margin-bottom:6px">${d}</div>`
          + `<div>流温度 <b>${isNum(r) ? r.toFixed(1) : '—'}%</b>${isNum(s.ratio?.[i]) ? '' : ''}</div>`
          + `<div style="color:${C.muted};font-size:11px">指数 120 日分位 ${p == null ? '—' : p + '%'}</div>`;
      },
    },
    xAxis: {
      type: 'category', data: dates, ...axisCommon(),
      axisLabel: { ...axisCommon().axisLabel, interval: Math.floor(dates.length / 10) },
    },
    yAxis: {
      // 纵轴反向：上＝净赎回（顶部出逃侧），下＝净申购（底部承接侧），贴合「上顶下底」的看图习惯
      type: 'value', inverse: true,
      name: '净申赎/市值 %　上＝出逃 · 下＝承接',
      nameLocation: 'start', nameGap: 14,
      nameTextStyle: { color: C.muted, fontSize: 10, align: 'left' },
      ...axisCommon(),
      axisLabel: { ...axisCommon().axisLabel, formatter: '{value}%' },
    },
    dataZoom: [{ type: 'inside' }],
    series: [
      {
        name: '流温度', type: 'line', data: ratio, z: 2,
        symbol: 'none', smooth: 0.15,
        lineStyle: { color: '#7c6cff', width: 1.5 },
        markLine: { silent: true, symbol: 'none', animation: false, data: lines },
      },
      {
        name: '底部承接', type: 'scatter', data: mk(s.marks?.bottom || []), z: 4,
        symbolSize: 6, itemStyle: { color: C.up, opacity: .85 },
      },
      {
        name: '顶部出逃', type: 'scatter', data: mk(s.marks?.top || []), z: 4,
        symbolSize: 6, itemStyle: { color: C.down, opacity: .8 },
      },    ],
  });

  // 结论条
  const sb = s.stats?.bottom, sp = s.stats?.top;
  const box = $('sigVerdict');
  box.hidden = false;
  if (st.state === 'bottom' && sb) {
    box.className = 'sig-verdict buy';
    box.innerHTML = `<b>底部承接区</b>——宽基资金历史级别净流入（20 日累计 ${st.flow20} 亿，占市值比处历史 ${st.pr}% 分位），`
      + `而指数只处在 120 日 ${st.pidx}% 分位。历史上同类状态 <b>${sb.n}</b> 例：之后 20 日平均 ${sg(sb.t20)}（胜率 ${sb.t20w}%）、`
      + `120 日平均 ${sg(sb.t120)}（胜率 ${sb.t120w}%），60 日内平均最多再跌 ${Math.abs(sb.md60).toFixed(1)}%。`
      + `<span class="caveat">这是资金底信号、不是点位：护盘 ≠ 市场底，且本次规模远超历史单次（无先例可套）。确认信号 = 缩量后重新放量上行；失效信号 = 跌破近月低点且单日净赎回放大。</span>`;
  } else if (st.state === 'top' && sp) {
    box.className = 'sig-verdict sell';
    box.innerHTML = `<b>顶部出逃区</b>——宽基资金持续净赎回（20 日累计 ${st.flow20} 亿，历史 ${st.pr}% 分位）而指数处于 120 日 ${st.pidx}% 分位高位。`
      + `历史上同类状态 <b>${sp.n}</b> 例：之后 20 日平均 ${sg(sp.t20)}（胜率 ${sp.t20w}%）、120 日平均 ${sg(sp.t120)}（胜率仅 ${sp.t120w}%）——`
      + `<span class="caveat">资金边拉边撤的特征形态，历史上升级为回调的概率约八成，注意控制仓位。</span>`;
  } else if (st.state === 'warm') {
    box.className = 'sig-verdict buy soft';
    box.innerHTML = `<b>流入偏多</b>——资金在进（历史 ${st.pr}% 分位）但价格不在低位。这类组合历史上不构成底部信号，只说明承接较强，跟随意义有限。`;
  } else if (st.state === 'cool') {
    box.className = 'sig-verdict sell soft';
    box.innerHTML = `<b>流出偏空</b>——资金在退（历史 ${st.pr}% 分位）且价格不低。多出现在上涨后的整理期，观察是否升级为顶部出逃区。`;
  } else {
    box.className = 'sig-verdict';
    box.innerHTML = `<b>中性区</b>——资金流与价格位置均未到历史极端，本面板不下结论，结合量价趋势与雷达的日常变化判断。`;
  }
}

/* ---------- 信号后收益分布（直方图） ---------- */
const HBIN = 2; // 每档 2 个百分点

function histBins(vals, w) {
  const lo = Math.floor(Math.min.apply(null, vals) / w) * w;
  const hi = Math.ceil(Math.max.apply(null, vals) / w) * w;
  const n = Math.max(1, Math.round((hi - lo) / w));
  const bins = [];
  for (let i = 0; i < n; i++) bins.push({ a: lo + i * w, b: lo + (i + 1) * w, c: 0 });
  vals.forEach(v => {
    let k = Math.floor((v - lo) / w);
    k = Math.max(0, Math.min(n - 1, k));
    bins[k].c += 1;
  });
  return bins;
}

function distChart(id, d) {
  const el = $(id);
  if (!d || !d.vals || !d.vals.length) { if (el) el.closest('.dist-cell')?.setAttribute('hidden', ''); return; }
  const bins = histBins(d.vals, HBIN);
  const lo = bins[0].a;
  const total = d.vals.length;
  const cats = bins.map(b => b.a);

  // 红涨绿跌：与全站口径一致，不随买卖方向反转
  const data = bins.map(b => {
    const mid = (b.a + b.b) / 2;
    return {
      value: b.c,
      itemStyle: {
        color: mid > 0 ? C.up : mid < 0 ? C.down : 'rgba(255,255,255,.3)',
        opacity: .78,
        borderRadius: [2, 2, 0, 0],
      },
    };
  });

  const binOf = v => Math.max(0, Math.min(bins.length - 1, Math.floor((v - lo) / HBIN)));
  // 0% 线先画（垫底），中位线后画（覆盖在上），中位恰落 0 档时不会被灰线盖住
  const marks = [
    {
      xAxis: binOf(0),
      lineStyle: { color: 'rgba(255,255,255,.16)', width: 1 },
      label: { show: false },
    },
    {
      xAxis: binOf(d.med),
      lineStyle: { color: 'rgba(255,255,255,.55)', type: [4, 3], width: 1 },
      label: { formatter: '中位 ' + sg(d.med), color: 'rgba(255,255,255,.72)', fontSize: 10, position: 'insideEndTop' },
    },
  ];

  mount(id, {
    grid: { left: 34, right: 14, top: 22, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(255,255,255,.05)' } },
      formatter: ps => {
        const i = ps[0].dataIndex, b = bins[i];
        return `<div style="color:${C.muted};font-size:11px;margin-bottom:4px">收益 ${sg(b.a)} ~ ${sg(b.b)}</div>`
          + `<div><b>${b.c}</b> 次 · 占 ${(b.c / total * 100).toFixed(0)}%</div>`;
      },
    },
    xAxis: {
      type: 'category', data: cats, ...axisCommon(),
      axisLabel: {
        ...axisCommon().axisLabel, fontSize: 10,
        formatter: v => (Number(v) > 0 ? '+' : '') + v + '%',
        interval: Math.max(0, Math.floor(cats.length / 8) - 1),
      },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value', name: '次数', nameTextStyle: { color: C.muted, fontSize: 9 },
      ...axisCommon(), splitNumber: 3,
      axisLabel: { ...axisCommon().axisLabel, fontSize: 9.5 },
    },
    series: [{
      type: 'bar', data, barWidth: '66%', z: 2,
      markLine: { silent: true, symbol: 'none', animation: false, data: marks },
    }],
  });
}

function distPanel(s) {
  const dd = s && s.dist;
  if (!dd || (!dd.bottom && !dd.top)) return;
  $('distSection').hidden = false;
  const nb = dd.bottom && dd.bottom.t20 ? dd.bottom.t20.n : '—';
  const nt = dd.top && dd.top.t20 ? dd.top.t20.n : '—';
  $('distHint').textContent = `历史上同类状态出现后指数未来收益的分布（不是平均值）· 底部区样本 ${nb} · 顶部区样本 ${nt}`;
  distChart('distB20', dd.bottom && dd.bottom.t20);
  distChart('distB120', dd.bottom && dd.bottom.t120);
  distChart('distT20', dd.top && dd.top.t20);
  distChart('distT120', dd.top && dd.top.t120);
}

function intradayChart(d) {
  const it = d.intraday || {};
  if (!it.cum || !it.cum.length) {
    $('intradaySection').hidden = true;
    return;
  }
  $('intradaySection').hidden = false;
  const n = it.cum.length;
  const labels = Array.from({ length: n }, (_, i) => {
    const t = 9 * 60 + 30 + i + (i > 120 ? 90 : 0); // 午休 11:30–13:00
    const hh = String(Math.floor(t / 60)).padStart(2, '0');
    const mm = String(t % 60).padStart(2, '0');
    return hh + ':' + mm;
  });
  const data = it.cum.map(v => +(v / 1e8).toFixed(2));
  $('intradayHint').textContent = `核心池合计 · ${it.date || ''} · 每分钟差分累积`;

  mount('chartIntraday', {
    grid: { left: 56, right: 20, top: 20, bottom: 36 },
    tooltip: { trigger: 'axis', valueFormatter: v => v.toFixed(2) + ' 亿' },
    xAxis: { type: 'category', data: labels, ...axisCommon(), boundaryGap: false, axisLabel: { ...axisCommon().axisLabel, interval: 29 } },
    yAxis: { type: 'value', ...axisCommon(), axisLabel: { ...axisCommon().axisLabel, formatter: v => v + '亿' } },
    series: [{
      type: 'line', data, smooth: true, symbol: 'none', boundaryGap: false,
      lineStyle: { color: C.pink, width: 2, shadowColor: 'rgba(255,45,106,.55)', shadowBlur: 14 },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: 'rgba(255,45,106,.35)' }, { offset: 1, color: 'rgba(255,45,106,.01)' }],
        },
      },
      markLine: {
        silent: true, symbol: 'none',
        lineStyle: { color: 'rgba(0,224,212,.4)', type: 'dashed' },
        label: { color: C.cyan, fontSize: 10, formatter: '20日均量' },
        data: [{ yAxis: isNum(d.market?.ma20) ? +(d.market.ma20 / 1e8).toFixed(2) : 0 }],
      },
    }],
  });
}

/* ---------- 榜单 ---------- */
function rankTable(el, rows, kind) {
  if (!rows.length) { el.innerHTML = `<tr><td style="color:var(--muted)">暂无数据</td></tr>`; return; }
  el.innerHTML = `<thead><tr><th>标的</th><th>净申赎</th><th>涨跌</th></tr></thead><tbody>` +
    rows.map(r => `<tr>
      <td>${r.name}<span class="code">${r.code}</span></td>
      <td>${flowPill(r.flow)}</td>
      <td class="${cls(r.pct)}">${pct(r.pct)}</td>
    </tr>`).join('') + '</tbody>';
}
function ranks(d) {
  const all = (d.funds_today || []).filter(f => isNum(f.flow));
  $('fundCount').textContent = (d.funds_today || []).length;
  rankTable($('rankIn'), all.slice().sort((a, b) => b.flow - a.flow).slice(0, 10), 'in');
  rankTable($('rankOut'), all.slice().sort((a, b) => a.flow - b.flow).slice(0, 10), 'out');
}

/* ---------- 头部与页脚 ---------- */
function chrome(d) {
  const gen = (d.generated_at || '').replace('T', ' ').slice(0, 16);
  $('meta').innerHTML =
    `交易日 <b>${d.trade_date || '—'}</b> · 更新 <b>${gen}</b> · 覆盖 <b>${d.fund_count || 0}</b> 只 / 核心 <b>${d.core_count || 0}</b> 只`;
  $('sources').innerHTML = (d.sources || []).map(s => `<li>${s}</li>`).join('');
}

/* ---------- 启动 ---------- */
async function boot() {
  try {
    const res = await fetch('data/snapshot.json?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const d = await res.json();
    window.__snapshot = d;
    chrome(d);
    initPaletteSwitch();
    verdict(d);
    kpis(d);
    radarView(d);
    flowChart(d);
    cumChart(d);
    trendChart(d);
    intradayChart(d);
    ranks(d);
    // 资金流温度面板（可选数据，失败不阻塞主面板）
    try {
      const r2 = await fetch('data/signal.json?t=' + Date.now(), { cache: 'no-store' });
      if (r2.ok) {
        const sj = await r2.json();
        signalPanel(sj);
        distPanel(sj);
      } else $('sigState').textContent = '暂无数据';
    } catch (_) {
      $('sigState').textContent = '暂无数据';
    }
    document.body.dataset.ready = '1';
  } catch (e) {
    $('verdict').textContent = '数据载入失败：' + e.message;
    console.error(e);
  }
}

let rt;
window.addEventListener('resize', () => {
  clearTimeout(rt);
  rt = setTimeout(() => Object.values(charts).forEach(c => c && c.resize()), 160);
});

boot();
