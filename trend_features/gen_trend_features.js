#!/usr/bin/env node
/**
 * gen_trend_features.js — 近似走势查找 · 特征库生成器
 *
 * 输入：全市场股票池（universe.json，或自动从东财 datacenter-web 拉全 A）
 * 输出：
 *   trend_features.json      完整特征库（含 c/q 序列），供前端检索
 *   trend_features_index.json 轻量索引（code/name/board/industry），供前端列表/候选
 *
 * 特征说明（与锁定方案一致）：
 *   - K线维度：窗口内收盘价（原始，min-max 归一化在查询端按所选窗口做；线性变换不改变 Pearson）
 *   - 四合一维度：强弱线 QX = EMA( posK(C,30), 3 )，posK = 100*(C-LLV(L,30))/(HHV(H,30)-LLV(L,30))
 *
 * 数据源：腾讯 web.ifzq.gtimg.cn fqkline（稳定，避 push2）；名称/板块从东财 datacenter-web 取。
 */
'use strict';
const fs = require('fs');
const path = require('path');

// ───────── 纯函数指标（四合一·强弱线 QX）─────────
function nul(n) { const a = new Array(n); for (let i = 0; i < n; i++) a[i] = null; return a; }
function MA(a, n) { const o = nul(a.length); let s = 0; for (let i = 0; i < a.length; i++) { s += (a[i] == null ? 0 : a[i]); if (i >= n) s -= (a[i - n] == null ? 0 : a[i - n]); o[i] = i >= n - 1 ? s / n : null; } return o; }
function EMA(a, n) { const o = nul(a.length); const k = 2 / (n + 1); let p = null; for (let i = 0; i < a.length; i++) { const v = a[i] == null ? 0 : a[i]; p = (i === 0 || p == null) ? v : v * k + p * (1 - k); o[i] = p; } return o; }
function LLV(a, n) { const o = nul(a.length); for (let i = 0; i < a.length; i++) { let lo = Infinity; for (let j = Math.max(0, i - n + 1); j <= i; j++) if (a[j] != null && a[j] < lo) lo = a[j]; o[i] = isFinite(lo) ? lo : null; } return o; }
function HHV(a, n) { const o = nul(a.length); for (let i = 0; i < a.length; i++) { let hi = -Infinity; for (let j = Math.max(0, i - n + 1); j <= i; j++) if (a[j] != null && a[j] > hi) hi = a[j]; o[i] = isFinite(hi) ? hi : null; } return o; }
function posK(C, L, H, per) {
  const lo = LLV(L, per), hi = HHV(H, per), o = nul(C.length);
  for (let i = 0; i < C.length; i++) o[i] = (hi[i] != null && lo[i] != null && hi[i] > lo[i]) ? 100 * (C[i] - lo[i]) / (hi[i] - lo[i]) : 0;
  return o;
}
function qxSeries(O, H, L, C) {
  const pk = posK(C, L, H, 30);
  return EMA(pk, 3);
}

// ───────── 工具 ─────────
// 6 开头沪市(含688科创/60主板)；8 开头北交所(bj)；其余深市(000/002/300)
const PREFIX = (code) => (/^6/.test(code) ? 'sh' : /^8/.test(code) ? 'bj' : 'sz');
const BOARD = (code) => {
  if (/^688/.test(code)) return '科创';
  if (/^30[01]/.test(code)) return '创业';
  if (/^8/.test(code)) return '北交';
  if (/^60/.test(code)) return '沪主';
  if (/^00/.test(code) || /^002/.test(code)) return '深主';
  return '其他';
};
const isST = (name) => /ST|退/.test(name || '');
// 仅排除老三板(4/92 开头，基本无日线流动性)；北交所(8)纳入全市场
const isExcluded = (code) => /^(4|92)/.test(code);

function parseKline(json, code) {
  const d = json && json.data && json.data[code];
  const arr = d && (d.qfqday || d.day);
  if (!arr || !arr.length) return null;
  const bars = arr.map(r => ({ d: r[0], o: +r[1], c: +r[2], h: +r[3], l: +r[4], v: +r[5] }));
  return bars;
}

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';
async function fetchKline(code, attempt = 0, timeout = 8000) {
  const url = `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=${code},day,,,320,qfq`;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    const r = await fetch(url, { signal: ctrl.signal, headers: { 'User-Agent': UA, 'Referer': 'https://gu.qq.com/' } });
    const txt = await r.text();
    if (txt.trim().startsWith('<!')) throw new Error('blocked-html'); // 风控拦截页，重试
    return parseKline(JSON.parse(txt), code);
  } catch (e) {
    if (attempt < 3) { await sleep(400 * Math.pow(2, attempt)); return fetchKline(code, attempt + 1); }
    return null;
  } finally { clearTimeout(t); }
}

// 并发池
async function pool(items, worker, size = 8) {
  const out = []; let i = 0;
  async function next() {
    while (i < items.length) {
      const idx = i++;
      try { out[idx] = await worker(items[idx], idx); }
      catch (e) { out[idx] = null; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, next));
  return out;
}

// ───────── 全市场股票池（东财 datacenter-web，稳定）─────────
async function fetchUniverseFromEM() {
  const API = 'https://datacenter-web.eastmoney.com/api/data/v1/get';
  const cols = 'SECURITY_CODE,SECURITY_NAME_ABBR,SECUCODE,TRADE_MARKET_CODE,LISTING_STATE';
  const first = await fetch(`${API}?reportName=RPT_DMSK_TS_STOCKNEW&pageSize=500&pageNumber=1&columns=${cols}`, { headers: { 'User-Agent': UA } });
  const fj = await first.json();
  const total = fj.result.count;
  const pages = fj.result.pages;
  let rows = fj.result.data.slice();
  for (let p = 2; p <= pages; p++) {
    const r = await fetch(`${API}?reportName=RPT_DMSK_TS_STOCKNEW&pageSize=500&pageNumber=${p}&columns=${cols}`, { headers: { 'User-Agent': UA } });
    const j = await r.json();
    if (j.result && j.result.data) rows = rows.concat(j.result.data);
    await sleep(120);
  }
  console.log(`[uni] 拉到 ${rows.length}/${total} 只`);
  const out = [];
  for (const x of rows) {
    const code = (x.SECURITY_CODE || '').trim();
    const name = (x.SECURITY_NAME_ABBR || '').trim();
    if (!code || !name) continue;
    if (x.LISTING_STATE && String(x.LISTING_STATE) !== '0') continue; // 剔除退市/未上市
    if (isST(name)) continue;
    if (isExcluded(code)) continue;
    // 板块后缀纠正（SECUCODE 后缀：.SH/.SZ/.BJ）
    let board = BOARD(code);
    const suf = (x.SECUCODE || '').split('.').pop();
    if (suf === 'BJ') board = '北交';
    out.push({ code, name, board, industry: '—' });
  }
  return out;
}

function loadUniverse() {
  const p = path.join(__dirname, 'universe.json');
  if (fs.existsSync(p)) {
    const u = JSON.parse(fs.readFileSync(p, 'utf8'));
    return u.stocks || u;
  }
  return null; // 标记需要在线拉
}

// ───────── 主流程 ─────────
async function main() {
  let uni = loadUniverse();
  if (!uni) {
    console.log('[gen] 无本地 universe.json，从东财拉全市场…');
    uni = await fetchUniverseFromEM();
  } else {
    console.log(`[gen] 本地 universe：${uni.length} 只`);
  }
  uni = uni.filter(s => !isExcluded(s.code)).filter(s => !isST(s.name));

  const results = await pool(uni, async (s) => {
    const code = s.code;
    const bars = await fetchKline(PREFIX(code) + code);
    if (!bars || bars.length < 30) return null;
    const O = bars.map(b => b.o), H = bars.map(b => b.h), L = bars.map(b => b.l), C = bars.map(b => b.c);
    const qx = qxSeries(O, H, L, C);
    const keep = 120;
    const cSlice = C.slice(-keep);
    const qSlice = qx.slice(-keep).map(v => v == null ? 0 : +v.toFixed(4));
    // 年线：EMA(收盘, 250)；用于「年线上方 C≥EMA250×0.95」过滤
    const ema250arr = EMA(C, 250);
    const ema250 = ema250arr[ema250arr.length - 1];
    const lastClose = cSlice[cSlice.length - 1];
    return {
      code,
      name: s.name || code,
      board: s.board || BOARD(code),
      industry: s.industry || '—',
      date: bars[bars.length - 1].d,
      c: cSlice.map(v => +v.toFixed(3)),
      q: qSlice,
      ema250: isFinite(ema250) ? +ema250.toFixed(3) : null,
      close: +lastClose.toFixed(3)
    };
  }, 8);

  const stocks = results.filter(Boolean);
  const out = {
    meta: {
      generatedAt: new Date().toISOString(),
      count: stocks.length,
      windows: [20, 60, 120],
      maxLen: 120,
      source: 'tencent-fqkline-qfq',
      universe: 'eastmoney-RPT_DMSK_TS_STOCKNEW',
      feature: 'c=close(120) | q=四合一强弱线QX=EMA(posK(C,30),3)'
    },
    stocks
  };
  const f = path.join(__dirname, '..', 'trend_features.json');
  fs.writeFileSync(f, JSON.stringify(out));
  console.log(`[gen] 写出 ${stocks.length} 只 -> ${f} (${ (fs.statSync(f).size / 1024).toFixed(1) } KB)`);

  // 轻量索引（供列表 UI，不含序列）
  const idx = {
    meta: { generatedAt: out.meta.generatedAt, count: stocks.length },
    stocks: stocks.map(s => ({ code: s.code, name: s.name, board: s.board, industry: s.industry, ema250: s.ema250, close: s.close }))
  };
  const fi = path.join(__dirname, '..', 'trend_features_index.json');
  fs.writeFileSync(fi, JSON.stringify(idx));
  console.log(`[gen] 写出索引 ${stocks.length} 只 -> ${fi} (${ (fs.statSync(fi).size / 1024).toFixed(1) } KB)`);
}
main().catch(e => { console.error('FATAL', e); process.exit(1); });
