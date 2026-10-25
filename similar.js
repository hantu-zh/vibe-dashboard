'use strict';
/* similar.js — 近似走势查找 前端逻辑（纯前端，无后端） */

const KING = 'https://hantu-zh.github.io/vibe-dashboard/king/?c=';
const SELF_KEY = 'vibe_selfselect';

let ALL = [];            // 全部股票（索引：code/name/board/industry，检索时被补全 c/q）
let BYCODE = {};         // code -> stock（含索引信息）
let FULL = null;         // 主特征库（含 c 序列），首次检索时按需加载
let QFULL = null;        // 四合一(q)序列库，仅 dim=q 时按需加载
let KC = null;           // kline_cache（带逐根日期，供历史区间源段切片）
let SELF = loadSelf();   // localStorage 个人自选
let QLABEL = null;       // 历史区间模式下的查询标签（区间+天数）
let RENDIM = 'c';        // 结果卡片迷你线所用的维度（范围模式强制 c）
let rangeQc = null;      // 当前已填充日期下拉的源票（避免重复填充/覆盖用户选择）

const $ = (id) => document.getElementById(id);
const qInput = $('qName'), dimSel = $('dim'), scopeSel = $('scope'),
      winSel = $('win'), algoSel = $('algo'), topkInput = $('topk'), aboveYear = $('aboveYear'),
      modeSel = $('mode'), rStartEl = $('rStart'), rEndEl = $('rEnd'),
      statusEl = $('status'), resultsEl = $('results'), qbox = $('qbox'),
      libFilter = $('libFilter'), libList = $('libList'), libMeta = $('libMeta'), libChips = $('libChips');

// ───────── 数据加载 ─────────
// 先拉轻量索引（供列表/候选），完整特征库在首次检索时按需加载
async function boot() {
  try {
    const r = await fetch('trend_features_index.json');   // 每日收盘后才更新；去 ?t= 复用浏览器 10min 缓存（github.io max-age=600）
    const data = await r.json();
    ALL = data.stocks || [];
    BYCODE = {}; ALL.forEach(s => BYCODE[s.code] = s);
  buildLib();                                  // 内部列表渲染延后到 idle，首屏不被 600 行 DOM 阻塞
  statusEl.textContent = `特征库就绪：${ALL.length} 只（点击「查找」时加载序列）`;
  idle(() => ensureKlinePopup());               // 空闲时才加载 94KB 弹窗框架，不占首屏
  } catch (e) {
    statusEl.textContent = '特征库加载失败（需经 http 访问，勿用 file://）：' + e.message;
    return;
  }
  const p = new URLSearchParams(location.search);
  if (p.get('c') && BYCODE[p.get('c')]) qInput.value = p.get('c');
  if (p.get('dim')) dimSel.value = p.get('dim');
  $('run').addEventListener('click', search);
  qInput.addEventListener('keydown', e => { if (e.key === 'Enter') search(); });
  // 模式切换：历史区间 ↔ 最近N日（旧版缓存 HTML 无「模式」控件时跳过，保持原有行为不崩）
  if (modeSel) {
    // 历史区间模式下：源票变化（输入框 change / 切模式）即重填日期下拉
    const repopIfRange = () => {
      if (modeSel.value !== 'range') return;
      const c = resolveQuery(qInput.value);
      if (c) populateRangeDates(c);
      else statusEl.textContent = '历史区间模式：请先填「查询股票」（源票须在看板自选池内）';
    };
    modeSel.addEventListener('change', async () => {
      const isRange = modeSel.value === 'range';
      $('rangeBox').style.display = isRange ? 'inline-flex' : 'none';
      winSel.disabled = isRange;       // 历史区间模式下 W 由日期范围决定
      if (isRange) repopIfRange();
    });
    qInput.addEventListener('change', repopIfRange);
  }
  $('qClear').addEventListener('click', e => {
    e.preventDefault();
    qInput.value = '';
    qInput.focus();
  });
  libFilter.addEventListener('input', renderLib);
  qbox.addEventListener('click', async () => {
    if (!qbox.dataset.code) return;
    // 近似走势页要求「全量副图」（四合一/缠论/寒梅傲雪/钱龙风格线/MACD/大观顾蓝）→ 传 tables:'all'
    await ensureKlinePopup();
    if (window.openKline) window.openKline(qbox.dataset.code, qbox.dataset.name, { tables: 'all' });
    else window.open(KING + qbox.dataset.code, '_blank');
  });
  // 不再进入页面即自动比对：那会立刻拉取 8.7MB 特征库导致首屏卡顿/看似无反应。
  // 仅预填查询标的，由用户点「查找相似」时再按需加载（ensureFull 已做去重）。
  if (qInput.value) statusEl.textContent = `特征库就绪：${ALL.length} 只 · 已预填「${qInput.value}」，点击「查找相似」开始比对`;
}

// 首次检索时加载主特征库（c 序列，约 1.2MB）。
// 去重：并发点击只发一次请求（避免每次点击都重新拉库）；失败清掉 _fullReq 允许下次重试。
// 缓存：trend_features.json 每日收盘后（约18:40）才更新，去掉 ?t= 让浏览器复用 10 分钟缓存。
let _fullReq = null;
function ensureFull() {
  if (FULL) return Promise.resolve(FULL);
  if (_fullReq) return _fullReq;
  statusEl.textContent = '首次检索：加载特征库…（约 1.2MB，请稍候）';
  _fullReq = fetch('trend_features.json')
    .then(r => r.json())
    .then(data => {
      FULL = data.stocks || [];
      const fb = {};
      FULL.forEach(s => {
        const t = BYCODE[s.code];
        if (t) { t.c = s.c; t.date = s.date; }
        fb[s.code] = s;
      });
      // 把完整序列写回 ALL（索引里有的票在全集里缺失则补上）
      ALL.forEach(s => { if (!s.c && fb[s.code]) { s.c = fb[s.code].c; s.date = fb[s.code].date; } });
      return FULL;
    })
    .catch(e => { _fullReq = null; throw e; });   // 失败清掉，允许下次点击重试
  return _fullReq;
}

// 四合一(q)序列：仅当用户选「四合一(强弱线QX)」维度时按需加载（默认 c 维度不拉，省一半以上下载）。
// 与 ensureFull 同一套去重/失败重试 pattern。
let _qReq = null;
function ensureQ() {
  if (QFULL) return Promise.resolve(QFULL);
  if (_qReq) return _qReq;
  statusEl.textContent = '加载四合一(q)序列…（约 1.3MB，请稍候）';
  _qReq = fetch('trend_features_q.json')
    .then(r => r.json())
    .then(data => {
      QFULL = data.stocks || [];
      const qb = {};
      QFULL.forEach(s => {
        const t = BYCODE[s.code];
        if (t) t.q = s.q;
        qb[s.code] = s;
      });
      ALL.forEach(s => { if (!s.q && qb[s.code]) s.q = qb[s.code].q; });
      return QFULL;
    })
    .catch(e => { _qReq = null; throw e; });
  return _qReq;
}

// 历史区间模式需要「带逐根日期」的源段 → 懒加载 kline_cache（371 只看板自选池）
async function ensureKlineCache() {
  if (KC) return KC;
  const r = await fetch('kline_cache.json?t=' + Date.now());
  const data = await r.json();
  KC = data.stocks || {};
  return KC;
}

// ─── 历史区间源段：任意 A 股兜底取数（腾讯日线 qfq JSONP，复用 kline_popup 同款策略） ───
const SRC_CACHE = {};                       // code -> [{d, c}] 或 null（失败亦缓存，避免反复请求）
function exOf(code) {
  const c = String(code).replace(/^(?:sh|sz|bj)/i, '');
  const pre = /^6/.test(c) ? 'sh' : /^(?:0|3)/.test(c) ? 'sz' : /^(?:4|8|9)/.test(c) ? 'bj' : '';
  return pre ? pre + c : code;
}
let _jsonpSeq = 0;
function jsonpVar(url, timeoutMs) {
  return new Promise(resolve => {
    const cb = 'klq' + (++_jsonpSeq) + '_' + Math.floor(Math.random() * 1e6);
    const sc = document.createElement('script');
    let done = false;
    const timer = setTimeout(() => finish(null), timeoutMs || 9000);
    function finish(val) {
      if (done) return; done = true; clearTimeout(timer);
      try { delete window[cb]; } catch (e) { window[cb] = undefined; }
      if (sc.parentNode) sc.parentNode.removeChild(sc);
      resolve(val);
    }
    sc.onerror = () => finish(null);
    sc.onload = () => { let v = null; try { v = window[cb]; } catch (e) {} finish(v === undefined ? null : v); };
    sc.src = url.replace('__CB__', cb);
    document.head.appendChild(sc);
  });
}
function parseTencentDay(j, sym) {
  const node = j && j.data && j.data[sym];
  if (!node) return null;
  const arr = node.qfqday || node.day;
  if (!arr || arr.length < 2) return null;
  const bars = [];
  for (const a of arr) {
    if (!a || a.length < 5) continue;
    const c = +a[2];
    if (!c) continue;
    bars.push({ d: String(a[0]), c });
  }
  return bars.length >= 2 ? bars : null;
}
async function fetchSrcDaily(code) {
  if (code in SRC_CACHE) return SRC_CACHE[code];
  const sym = exOf(code), n = 400;
  const urls = [
    'https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?_var=__CB__&param=' + sym + ',day,,,' + n + ',qfq',
    'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?_var=__CB__&param=' + sym + ',day,,,' + n + ',qfq',
    'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?_var=__CB__&param=' + sym + ',day,,,' + n
  ];
  let res = null;
  for (const u of urls) {
    const j = await jsonpVar(u, 9000);
    const bars = parseTencentDay(j, sym);
    if (bars) { res = bars; break; }
  }
  SRC_CACHE[code] = res;
  return res;
}
// 返回源段 [date, close] 数组：优先 371 池缓存（带逐根日期），否则任意 A 股腾讯日K 兜底
async function loadSrcSegment(code) {
  const kc = await ensureKlineCache();
  const src = kc[code];
  if (src && Array.isArray(src.kline) && src.kline.length) {
    return src.kline.map(r => [String(r[0]), +r[4]]);
  }
  const daily = await fetchSrcDaily(code);
  if (!daily) return null;
  return daily.map(b => [b.d, b.c]);
}

// 懒加载 kline_popup.js（首屏不加载这 94KB 弹窗框架，点 K 线时才按需拉取）
let _kp = null;
function ensureKlinePopup() {
  if (window.openKline) return Promise.resolve();
  if (_kp) return _kp;
  _kp = new Promise(resolve => {
    const s = document.createElement('script');
    s.src = 'kline_popup.js?v=20261021o';
    s.onload = () => resolve();
    s.onerror = () => resolve();
    document.head.appendChild(s);
  });
  return _kp;
}
const idle = (fn) => ('requestIdleCallback' in window) ? requestIdleCallback(fn) : setTimeout(fn, 200);

// 历史区间模式：把开始/结束日期框填成「源票在 K线缓存里的逐根交易日」下拉，
// 默认 结束=最后交易日、开始=前第5个交易日（最近5天）。源票不在缓存时清空。
async function populateRangeDates(code) {
  if (!modeSel || modeSel.value !== 'range' || !code) return;
  if (code === rangeQc && rStartEl.options.length > 1) return;   // 已为该票填充，保留用户当前选择
  statusEl.textContent = `加载 ${code} 日K…`;
  const seg = await loadSrcSegment(code);                   // 371 池 或 任意 A 股 JSONP 兜底
  if (!seg || !seg.length) {
    const ph = '<option value="" disabled selected>取源段失败</option>';
    rStartEl.innerHTML = ph; rEndEl.innerHTML = ph;
    rangeQc = null;
    statusEl.textContent = `「${code}」日K 取数失败（网络或代码无效）`;
    return;
  }
  const dates = seg.map(x => x[0]).sort();                  // 升序
  const opts = dates.map(d => `<option value="${d}">${d}</option>`).join('');
  rStartEl.innerHTML = opts;
  rEndEl.innerHTML = opts;
  const n = dates.length;
  rEndEl.value = dates[n - 1];                             // 结束 = 最后交易日
  rStartEl.value = dates[Math.max(0, n - 5)];              // 开始 = 前第5个交易日
  rangeQc = code;
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

// 日收益率序列（短窗口降噪用）：比较「每日涨跌节奏」而非绝对价格形状
function rets(arr) {
  const r = [];
  for (let i = 1; i < arr.length; i++) r.push((arr[i] - arr[i - 1]) / arr[i - 1]);
  return r;
}

// 目标库某票在窗口 W 内的比对序列：pearson/dtw 用归一化价格形状，returns 用日收益率
function tgtSeries(s, dim, W, algo) {
  const raw = (s[dim] || []).slice(-W).map(Number);
  if (raw.length < (algo === 'returns' ? 2 : W)) return null;
  return algo === 'returns' ? rets(raw) : norm(raw, W);
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
  idle(renderLib);                             // 首屏延后渲染，先完成特征库就绪状态
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
const escH = s => String(s).replace(/[<>&]/g, c => c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&amp;');
function code6(v) { const m = /^(?:sh|sz|bj)?(\d{6})$/i.exec(String(v || '').trim()); return m ? m[1] : null; }

// 东财搜索联想（JSONP，接口无 CORS 头）：代码 / 中文名 / 拼音全拼 / 拼音首字母（ygzm → 阳光照明）。
// 移植自 king 页同款实现；只认 A 股（以 SecurityTypeName 判市场，代码前缀兜底）。
function suggest(raw) {
  return new Promise(resolve => {
    const fn = '_klSuggest' + Date.now();
    const s = document.createElement('script');
    let done = false;
    const cleanup = () => { if (done) return; done = true; clearTimeout(to); try { delete window[fn]; } catch (e) { window[fn] = undefined; } if (s.parentNode) s.parentNode.removeChild(s); };
    const to = setTimeout(() => { cleanup(); resolve([]); }, 6000);
    function classify(it) {
      const c = String(it.Code || '');
      if (!/^\d{6}$/.test(c)) return null;
      const nm = String(it.SecurityTypeName || '');
      let pre = '';
      if (!nm) pre = /^6/.test(c) ? 'sh' : /^(0|3)/.test(c) ? 'sz' : /^(4|8|9)/.test(c) ? 'bj' : '';
      else if (nm === '沪A') pre = 'sh'; else if (nm === '深A') pre = 'sz'; else if (nm === '京A') pre = 'bj';
      return pre ? { code: pre + c, name: String(it.Name || ''), mkt: nm } : null;
    }
    window[fn] = j => {
      cleanup();
      const list = (j && j.QuotationCodeTable && j.QuotationCodeTable.Data) || [];
      const rows = [], seen = {};
      const q = String(raw || '').trim().replace(/\s+/g, '');
      const letters = /^[A-Za-z]+$/.test(q) ? q.toUpperCase() : null;
      for (let i = 0; i < list.length; i++) {
        const row = classify(list[i]);
        if (!row) continue;
        if (letters && String(list[i].PinYin || '').toUpperCase().indexOf(letters) !== 0) continue;
        if (seen[row.code]) continue; seen[row.code] = 1;
        rows.push(row);
      }
      if (!rows.length && letters) {
        for (let k = 0; k < list.length; k++) {
          const row2 = classify(list[k]);
          if (!row2 || seen[row2.code]) continue;
          seen[row2.code] = 1; rows.push(row2);
        }
      }
      resolve(rows);
    };
    s.onerror = () => { cleanup(); resolve([]); };
    s.src = 'https://searchapi.eastmoney.com/api/suggest/get?input=' + encodeURIComponent(raw) +
      '&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=10&cb=' + fn;
    document.head.appendChild(s);
  });
}

function renderCands(query, list) {
  const box = $('qcands');
  box.style.display = 'block';
  box.innerHTML = `「${escH(query)}」匹配到 ${list.length} 只特征库内 A 股，请点选：` +
    list.map(c => `<span class="chip cand" data-c="${escH(c.code)}" title="${escH(c.name)} ${escH(c.code)}"><b>${escH(c.code)}</b>${escH(c.name)}${c.mkt ? `<em> ${escH(c.mkt)}</em>` : ''}</span>`).join('');
  box.querySelectorAll('.cand').forEach(b => b.addEventListener('click', () => {
    box.style.display = 'none'; box.innerHTML = '';
    qInput.value = b.dataset.c;
    search();
  }));
}

function resolveQuery(v) {
  v = (v == null ? qInput.value : v).trim();
  const c6 = code6(v);
  if (c6 && BYCODE[c6]) return c6;
  if (BYCODE[v]) return v;
  const hit = ALL.find(s => s.name === v);
  return hit ? hit.code : null;
}

let searchReq = 0;
async function search() {
  const raw = qInput.value.trim();
  if (!raw) { statusEl.textContent = '请输入 代码 / 名称 / 拼音首字母（如 600000 / 浦发银行 / pfyh）'; return; }
  const my = ++searchReq;
  let qc = resolveQuery(raw);
  if (!qc) {
    statusEl.textContent = `正在搜索「${escH(raw)}」…`;
    const list = await suggest(raw);
    if (my !== searchReq) return;
    const ais = (list || []).map(x => ({ code: code6(x.code), name: x.name, mkt: x.mkt })).filter(x => x.code);
    const inLib = ais.filter(x => BYCODE[x.code]);
    if (!ais.length) { statusEl.innerHTML = `<span style="color:#ff5b5b">未找到</span>「<span style="color:#ffd93d">${escH(raw)}</span>」（支持 代码 / 名称 / 拼音首字母，如 600000 / 浦发银行 / pfyh）`; return; }
    if (!inLib.length) { statusEl.textContent = `「${ais[0].name} ${ais[0].code}」不在特征库（仅全 A 快照内的股票可比对）`; return; }
    if (inLib.length === 1) { qc = inLib[0].code; qInput.value = qc; }
    else { renderCands(raw, inLib); statusEl.textContent = `「${raw}」匹配到 ${inLib.length} 只，请点选上方候选`; return; }
  } else {
    qInput.value = qc;
    const box = $('qcands'); box.style.display = 'none'; box.innerHTML = '';
  }
  if (my !== searchReq) return;
  // 旧版缓存 HTML 无「模式」控件 → 视作「最近N日」，与历史行为一致
  const mode = modeSel ? modeSel.value : 'recent', algo = algoSel.value;
  const topk = Math.max(1, Math.min(200, +topkInput.value || 20));
  QLABEL = null;
  const effDim = mode === 'range' ? 'c' : dimSel.value;   // 提前定维度：q 维度需先按需加载序列

  const runBtn = $('run'), runTxt = runBtn.textContent;
  runBtn.disabled = true; runBtn.textContent = '加载中…';
  let loadWhat = '特征库';
  try {
    await ensureFull();
    if (effDim === 'q') { loadWhat = '四合一(q)序列'; await ensureQ(); }   // 仅 q 维度拉独立文件
  } catch (e) {
    if (my === searchReq) statusEl.textContent = `${loadWhat}加载失败（网络较慢或中断），请重试：` + (e && e.message ? e.message : e);
    return;
  } finally {
    runBtn.disabled = false; runBtn.textContent = runTxt;
  }
  if (my !== searchReq) return;
  const q = BYCODE[qc];
  if (!q || !q[effDim]) { statusEl.textContent = `「${qc}」在特征库中无${effDim === 'q' ? '四合一(q)' : '日线'}序列，无法比对`; return; }

  // 查询模板 + 相关性序列：历史区间模式取指定日期段；最近N日模式取末尾 W 天
  let qf, qSeries, W;
  if (mode === 'range') {
    // 历史区间模式：源段取自 kline_cache(371池) 或 任意 A 股腾讯日K 兜底，仅支持收盘价维度
    await populateRangeDates(qc);            // 确保下拉已填充（源票可能被用户换过）
    const seg = await loadSrcSegment(qc);     // 取源段用于比对
    if (!seg || !seg.length) {
      statusEl.textContent = `「${qc}」日K 取数失败（网络或代码无效）`; return;
    }
    const rs = rStartEl.value, re = rEndEl.value;
    if (!rs || !re) { statusEl.textContent = '请选择 开始日期 与 结束日期'; return; }
    const segC = seg.filter(r => r[0] >= rs && r[0] <= re).map(r => +r[1]);
    W = segC.length;
    if (W < 3) { statusEl.textContent = `区间 ${rs}~${re} 内交易日不足(<3)，无法比对`; return; }
    qf = norm(segC, W);                                           // 价格形状，用于展示
    qSeries = (algo === 'returns') ? rets(segC) : norm(segC, W);  // 相关性序列
    QLABEL = `${rs}~${re}（源段 ${W} 天）`;
  } else {
    W = +winSel.value;
    qf = norm(q[effDim], W);
    qSeries = (algo === 'returns') ? rets(q[effDim].slice(-W).map(Number)) : norm(q[effDim], W);
  }
  RENDIM = effDim;

  let pool = ALL.filter(s => s.code !== qc && s[effDim]);
  if (scopeSel.value === 'board') pool = pool.filter(s => s.board === q.board);
  else if (scopeSel.value === 'self') { const set = selfCodes(); pool = pool.filter(s => set.has(s.code)); }
  // 年线上方过滤：收盘价 ≥ EMA(收盘,250) × 0.95
  if (aboveYear.checked) pool = pool.filter(s => s.ema250 != null && s.close >= s.ema250 * 0.95);

  const t0 = performance.now();
  let scored;
  if (algo === 'dtw') {
    const coarse = pool.map(s => { const t = tgtSeries(s, effDim, W, 'pearson'); return t ? { s, r: Math.abs(pearson(qSeries, t)) } : null; })
                       .filter(Boolean).sort((a, b) => b.r - a.r).slice(0, 200);
    scored = coarse.map(o => ({ s: o.s, sim: Math.exp(-dtw(qSeries, tgtSeries(o.s, effDim, W, 'pearson'))) }))
                   .sort((a, b) => b.sim - a.sim).slice(0, topk);
  } else {
    scored = pool.map(s => { const t = tgtSeries(s, effDim, W, algo); return t ? { s, sim: Math.abs(pearson(qSeries, t)) } : null; })
                 .filter(Boolean).sort((a, b) => b.sim - a.sim).slice(0, topk);
  }
  const dt = performance.now() - t0;

  renderQuery(q, qf);
  renderResults(scored, qf);
  const modeLabel = mode === 'range' ? `历史区间 ${QLABEL}` : (algo === 'dtw' ? 'DTW两段式' : '皮尔逊');
  statusEl.textContent = `范围 ${pool.length} 只 · 命中 ${scored.length} · ${modeLabel} · 窗口 ${W} 天 · 用时 ${dt.toFixed(1)}ms`;
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
  qbox.dataset.code = q.code;
  qbox.dataset.name = q.name;
  qbox.title = '点击查看 A类K线（K线分析弹窗）';
  qbox.innerHTML = `<div class="chd"><span class="nm">${q.name}<span class="cd">${q.code}</span><span class="kl-margin" data-margin-code="${q.code}" hidden title="融资融券标的">融</span></span>
    <span class="meta"><span class="badge">${q.board}</span><span class="badge">${q.industry}</span> 查询模板 · ${QLABEL || q.date || ''} <span class="klhint">点击看A类K线 ↗</span></span></div>
    ${cardSVG(qf, qf)}`;
  if (window.applyMarginBadges) window.applyMarginBadges(qbox);   // 「融」徽章：两融标的自动点亮（kline_popup 同源）
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
      ${cardSVG(s[RENDIM], qf)}
      <div class="legend"><span><i class="dot" style="background:#4ea1ff"></i>结果</span><span><i class="dot" style="background:#fff"></i>查询模板</span>
        <button class="selfbtn ${on ? 'on' : ''}" data-self="${s.code}">${on ? '★已加自选' : '＋自选'}</button>
        <span class="klhint">点击卡片看A类K线 ↗</span></div>
    </div>`;
  }).join('');
  resultsEl.querySelectorAll('.card').forEach(c => c.addEventListener('click', async e => {
    if (e.target.dataset.self) return;          // 点「自选」不触发
    await ensureKlinePopup();
    if (window.openKline) window.openKline(c.dataset.code, c.dataset.name, { tables: 'all' });   // A类K线弹窗（本页=全量副图标签）
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
