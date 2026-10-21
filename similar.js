'use strict';
/* similar.js — 近似走势查找 前端逻辑（纯前端，无后端） */

const KING = 'https://hantu-zh.github.io/vibe-dashboard/king/?c=';
const SELF_KEY = 'vibe_selfselect';

let ALL = [];            // 全部股票（索引：code/name/board/industry，检索时被补全 c/q）
let BYCODE = {};         // code -> stock（含索引信息）
let FULL = null;         // 完整特征库（含 c/q），首次检索时按需加载
let SELF = loadSelf();   // localStorage 个人自选

const $ = (id) => document.getElementById(id);
const qInput = $('qName'), dimSel = $('dim'), scopeSel = $('scope'),
      winSel = $('win'), algoSel = $('algo'), topkInput = $('topk'), aboveYear = $('aboveYear'),
      statusEl = $('status'), resultsEl = $('results'), qbox = $('qbox'),
      libFilter = $('libFilter'), libList = $('libList'), libMeta = $('libMeta'), libChips = $('libChips');

// ───────── 数据加载 ─────────
// 先拉轻量索引（供列表/候选），完整特征库在首次检索时按需加载
async function boot() {
  try {
    const r = await fetch('trend_features_index.json?t=' + Date.now());
    const data = await r.json();
    ALL = data.stocks || [];
    BYCODE = {}; ALL.forEach(s => BYCODE[s.code] = s);
    buildLib();
    statusEl.textContent = `特征库就绪：${ALL.length} 只（点击「查找」时加载序列）`;
  } catch (e) {
    statusEl.textContent = '特征库加载失败（需经 http 访问，勿用 file://）：' + e.message;
    return;
  }
  const p = new URLSearchParams(location.search);
  if (p.get('c') && BYCODE[p.get('c')]) qInput.value = p.get('c');
  if (p.get('dim')) dimSel.value = p.get('dim');
  $('run').addEventListener('click', search);
  qInput.addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
  libFilter.addEventListener('input', renderLib);
  if (qInput.value) search();
}

// 首次检索时加载完整特征库（含 c/q 序列）
async function ensureFull() {
  if (FULL) return FULL;
  statusEl.textContent = '首次检索：加载完整特征库…';
  const r = await fetch('trend_features.json?t=' + Date.now());
  const data = await r.json();
  FULL = data.stocks || [];
  const fb = {};
  FULL.forEach(s => {
    const t = BYCODE[s.code];
    if (t) { t.c = s.c; t.q = s.q; t.date = s.date; }
    fb[s.code] = s;
  });
  // 把完整序列写回 ALL（索引里有的票在全集里缺失则补上）
  ALL.forEach(s => { if (!s.c && fb[s.code]) { s.c = fb[s.code].c; s.q = fb[s.code].q; s.date = fb[s.code].date; } });
  return FULL;
}

// ───────── 指标/相似度 ─────────
function norm(series, W) {
  const s = series.slice(-W);
  let lo = Infinity, hi = -Infinity;
  for (const v of s) { if (v < lo) lo = v; if (v > hi) hi = v; }
  if (hi === lo) return s.map(() => 0.5);
  return s.map(v => (v - lo) / (hi - lo));
}
function pearson(a, b) {
  const n = a.length; let sa = 0, sb = 0;
  for (let i = 0; i < n; i++) { sa += a[i]; sb += b[i]; }
  const ma = sa / n, mb = sb / n; let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) { const xa = a[i] - ma, xb = b[i] - mb; num += xa * xb; da += xa * xa; db += xb * xb; }
  if (da === 0 || db === 0) return 0;
  return num / Math.sqrt(da * db);
}
function dtw(a, b, band) {
  const n = a.length, m = b.length, INF = Infinity;
  const w = band || Math.max(10, Math.floor(Math.max(n, m) * 0.2));
  const D = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(INF));
  D[0][0] = 0;
  for (let i = 1; i <= n; i++) {
    const jl = Math.max(1, i - w), jh = Math.min(m, i + w);
    for (let j = jl; j <= jh; j++) {
      const cost = Math.abs(a[i - 1] - b[j - 1]);
      D[i][j] = cost + Math.min(D[i - 1][j], D[i][j - 1], D[i - 1][j - 1]);
    }
  }
  return D[n][m] / Math.max(n, m);
}

// ───────── 自选 ─────────
function loadSelf() { try { return new Set(JSON.parse(localStorage.getItem(SELF_KEY) || '[]')); } catch (e) { return new Set(); } }
function saveSelf() { localStorage.setItem(SELF_KEY, JSON.stringify([...SELF])); }
function selfCodes() {
  const set = new Set(SELF);
  try {
    const xhr = new XMLHttpRequest(); xhr.open('GET', 'strongbuy_data.json', false); xhr.send();
    if (xhr.status === 200) {
      const d = JSON.parse(xhr.responseText);
      (d.stocks || []).forEach(s => set.add(String(s.code)));
    }
  } catch (e) {}
  return set;
}
function toggleSelf(code) {
  if (SELF.has(code)) SELF.delete(code); else SELF.add(code);
  saveSelf();
}

// ───────── 特征库列表 UI ─────────
let libBoard = '全部';
const BOARDS = ['全部', '沪主', '深主', '创业', '科创', '北交', '其他'];
function buildLib() {
  libChips.innerHTML = BOARDS.map(b => `<span class="chip ${b === '全部' ? 'on' : ''}" data-b="${b}">${b}</span>`).join('');
  libChips.querySelectorAll('.chip').forEach(c => c.addEventListener('click', () => {
    libBoard = c.dataset.b;
    libChips.querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x === c));
    renderLib();
  }));
  renderLib();
}
function renderLib() {
  const q = libFilter.value.trim().toLowerCase();
  let arr = ALL;
  if (libBoard !== '全部') arr = arr.filter(s => s.board === libBoard);
  if (q) arr = arr.filter(s => s.code.indexOf(q) >= 0 || (s.name || '').toLowerCase().indexOf(q) >= 0);
  const total = arr.length;
  arr = arr.slice(0, 600); // 限制渲染条数，避免超长 DOM
  libMeta.textContent = `特征库共 ${ALL.length} 只 · 当前筛选 ${total} 只${total > arr.length ? `（显示前 ${arr.length}）` : ''} · 点击任一行作为查询标的`;
  libList.innerHTML = arr.map(s => {
    const yn = (s.ema250 != null && s.close >= s.ema250 * 0.95) ? '<span class="yn" title="年线上方 C≥EMA250×0.95">年</span>' : '';
    return `<div class="row" data-code="${s.code}"><span class="nm">${s.name}</span><span class="cd">${s.code}</span><span class="bd">${s.board}</span>${yn}</div>`;
  }).join('');
  libList.querySelectorAll('.row').forEach(r => r.addEventListener('click', () => {
    qInput.value = r.dataset.code;
    search();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }));
}

// ───────── 检索 ─────────
function resolveQuery() {
  const v = qInput.value.trim();
  if (BYCODE[v]) return v;
  const hit = ALL.find(s => s.name === v || s.code === v);
  return hit ? hit.code : null;
}
async function search() {
  const qc = resolveQuery();
  if (!qc) { statusEl.textContent = '未找到该股票，请从列表选择'; return; }
  await ensureFull();
  const q = BYCODE[qc];
  if (!q || !q.c) { statusEl.textContent = `「${qc}」在特征库中无日线序列，无法比对`; return; }
  const dim = dimSel.value, W = +winSel.value, algo = algoSel.value;
  const topk = Math.max(1, Math.min(200, +topkInput.value || 20));
  const qf = norm(q[dim], W);

  let pool = ALL.filter(s => s.code !== qc && s.c);
  if (scopeSel.value === 'board') pool = pool.filter(s => s.board === q.board);
  else if (scopeSel.value === 'self') { const set = selfCodes(); pool = pool.filter(s => set.has(s.code)); }
  // 年线上方过滤：收盘价 ≥ EMA(收盘,250) × 0.95
  if (aboveYear.checked) pool = pool.filter(s => s.ema250 != null && s.close >= s.ema250 * 0.95);

  const t0 = performance.now();
  let scored;
  if (algo === 'dtw') {
    const coarse = pool.map(s => ({ s, r: Math.abs(pearson(qf, norm(s[dim], W))) }))
                       .sort((a, b) => b.r - a.r).slice(0, 200);
    scored = coarse.map(o => ({ s: o.s, sim: Math.exp(-dtw(qf, norm(o.s[dim], W))) }))
                   .sort((a, b) => b.sim - a.sim).slice(0, topk);
  } else {
    scored = pool.map(s => ({ s, sim: Math.abs(pearson(qf, norm(s[dim], W))) }))
                 .sort((a, b) => b.sim - a.sim).slice(0, topk);
  }
  const dt = performance.now() - t0;

  renderQuery(q, qf);
  renderResults(scored, qf);
  statusEl.textContent = `范围 ${pool.length} 只 · 命中 ${scored.length} · ${algo === 'dtw' ? 'DTW两段式' : '皮尔逊'} 用时 ${dt.toFixed(1)}ms`;
}

// ───────── 渲染 ─────────
function svgLine(arr, color, dash) {
  const W = 100, H = 40;
  const pts = arr.map((v, i) => `${(i / (arr.length - 1) * W).toFixed(2)},${(H - v * 38 - 1).toFixed(2)}`).join(' ');
  return `<polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1" ${dash ? 'stroke-dasharray="2,2"' : ''} opacity="0.95"/>`;
}
function cardSVG(res, qf) {
  return `<svg viewBox="0 0 100 40" preserveAspectRatio="none">${svgLine(qf, '#ffffff', true)}${svgLine(res, '#4ea1ff', false)}</svg>`;
}
function renderQuery(q, qf) {
  qbox.style.display = 'block';
  qbox.innerHTML = `<div class="chd"><span class="nm">${q.name}<span class="cd">${q.code}</span></span>
    <span class="meta"><span class="badge">${q.board}</span><span class="badge">${q.industry}</span> 查询模板 · ${q.date || ''}</span></div>
    ${cardSVG(qf, qf)}`;
}
function renderResults(scored, qf) {
  resultsEl.innerHTML = scored.map(({ s, sim }) => {
    const on = SELF.has(s.code);
    const pct = Math.round(sim * 100);
    const col = pct >= 80 ? '#00e676' : pct >= 60 ? '#ffd400' : '#8a93b0';
    return `<div class="card ${on ? 'selfed' : ''}" data-code="${s.code}" data-name="${s.name}" title="点击查看 A类K线（K线分析弹窗）">
      <div class="chd"><span><span class="nm">${s.name}</span><span class="cd">${s.code}</span></span>
        <span class="score" style="color:${col}">${pct}%</span></div>
      <div class="meta"><span class="badge">${s.board}</span><span class="badge">${s.industry}</span></div>
      ${cardSVG(s[dimSel.value], qf)}
      <div class="legend"><span><i class="dot" style="background:#4ea1ff"></i>结果</span><span><i class="dot" style="background:#fff"></i>查询模板</span>
        <button class="selfbtn ${on ? 'on' : ''}" data-self="${s.code}">${on ? '★已加自选' : '＋自选'}</button>
        <span class="klhint">点击卡片看A类K线 ↗</span></div>
    </div>`;
  }).join('');
  resultsEl.querySelectorAll('.card').forEach(c => c.addEventListener('click', e => {
    if (e.target.dataset.self) return;          // 点「自选」不触发
    if (window.openKline) window.openKline(c.dataset.code, c.dataset.name);   // A类K线弹窗（K线分析同款）
    else window.open(KING + c.dataset.code, '_blank');
  }));
  resultsEl.querySelectorAll('.selfbtn').forEach(b => b.addEventListener('click', e => {
    e.stopPropagation();
    toggleSelf(b.dataset.self);
    const card = b.closest('.card');
    const on = SELF.has(b.dataset.self);
    b.classList.toggle('on', on);
    b.textContent = on ? '★已加自选' : '＋自选';
    card.classList.toggle('selfed', on);
  }));
}

boot();
