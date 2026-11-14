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
