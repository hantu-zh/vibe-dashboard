'use strict';
/* similar.js — 近似走势查找 前端逻辑（纯前端，无后端） */

const KING = 'https://hantu-zh.github.io/vibe-dashboard/king/?c=';
const SELF_KEY = 'vibe_selfselect';

let ALL = [];           // 全部股票
let BYCODE = {};        // code -> stock
let SELF = loadSelf();  // localStorage 个人自选

const $ = (id) => document.getElementById(id);
const qInput = $('qName'), dimSel = $('dim'), scopeSel = $('scope'),
      winSel = $('win'), algoSel = $('algo'), topkInput = $('topk'),
      statusEl = $('status'), resultsEl = $('results'), qbox = $('qbox');

// ───────── 数据加载 ─────────
async function boot() {
  try {
    const r = await fetch('trend_features.json?t=' + Date.now());
    const data = await r.json();
    ALL = data.stocks || [];
    BYCODE = {}; ALL.forEach(s => BYCODE[s.code] = s);
    // datalist
    const dl = $('stocklist');
    dl.innerHTML = ALL.map(s => `<option value="${s.code}">${s.name} ${s.code}</option>`).join('');
    statusEl.textContent = `特征库就绪：${ALL.length} 只`;
  } catch (e) {
    statusEl.textContent = '特征库加载失败（需经 http 访问，勿用 file://）：' + e.message;
    return;
  }
  // URL 预填 ?c=CODE&dim=q
  const p = new URLSearchParams(location.search);
  if (p.get('c') && BYCODE[p.get('c')]) qInput.value = p.get('c');
  if (p.get('dim')) dimSel.value = p.get('dim');
  $('run').addEventListener('click', search);
  qInput.addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
  if (qInput.value) search();
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
  return D[n][m] / Math.max(n, m); // 归一化平均距离
}

// ───────── 自选 ─────────
function loadSelf() { try { return new Set(JSON.parse(localStorage.getItem(SELF_KEY) || '[]')); } catch (e) { return new Set(); } }
function saveSelf() { localStorage.setItem(SELF_KEY, JSON.stringify([...SELF])); }
function selfCodes() {
  const set = new Set(SELF);
  // 合并 strongbuy_data.json（益盟强买池）
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

// ───────── 检索 ─────────
function resolveQuery() {
  const v = qInput.value.trim();
  if (BYCODE[v]) return v;
  const hit = ALL.find(s => s.name === v || s.code === v);
  return hit ? hit.code : null;
}
function search() {
  const qc = resolveQuery();
  if (!qc) { statusEl.textContent = '未找到该股票，请从列表选择'; return; }
  const q = BYCODE[qc];
  const dim = dimSel.value, W = +winSel.value, algo = algoSel.value;
  const topk = Math.max(1, Math.min(200, +topkInput.value || 20));
  const qf = norm(q[dim], W);

  let pool = ALL.filter(s => s.code !== qc);
  if (scopeSel.value === 'board') pool = pool.filter(s => s.board === q.board);
  else if (scopeSel.value === 'self') { const set = selfCodes(); pool = pool.filter(s => set.has(s.code)); }

  const t0 = performance.now();
  let scored;
  if (algo === 'dtw') {
    // 两段式：皮尔逊粗筛 Top200 → DTW 精排
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
    <span class="meta"><span class="badge">${q.board}</span><span class="badge">${q.industry}</span> 查询模板 · ${q.date}</span></div>
    ${cardSVG(qf, qf)}`;
}
function renderResults(scored, qf) {
  resultsEl.innerHTML = scored.map(({ s, sim }) => {
    const on = SELF.has(s.code);
    const pct = Math.round(sim * 100);
    const col = pct >= 80 ? '#00e676' : pct >= 60 ? '#ffd400' : '#8a93b0';
    return `<div class="card ${on ? 'selfed' : ''}" data-code="${s.code}">
      <div class="chd"><span><span class="nm">${s.name}</span><span class="cd">${s.code}</span></span>
        <span class="score" style="color:${col}">${pct}%</span></div>
      <div class="meta"><span class="badge">${s.board}</span><span class="badge">${s.industry}</span></div>
      ${cardSVG(s[dimSel.value], qf)}
      <div class="legend"><span><i class="dot" style="background:#4ea1ff"></i>结果</span><span><i class="dot" style="background:#fff"></i>查询模板</span>
        <button class="selfbtn ${on ? 'on' : ''}" data-self="${s.code}">${on ? '★已加自选' : '＋自选'}</button></div>
    </div>`;
  }).join('');
  resultsEl.querySelectorAll('.card').forEach(c => c.addEventListener('click', e => {
    if (e.target.dataset.self) return;
    window.open(KING + c.dataset.code, '_blank');
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
