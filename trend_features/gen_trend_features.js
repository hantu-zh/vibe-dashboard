#!/usr/bin/env node
/**
 * gen_trend_features.js — 近似走势查找 · 特征库生成器
 *
 * 输入：A 股股票池（universe.json 或内置 SAMPLE_CODES）
 * 输出：trend_features.json
 *   { meta:{generatedAt,count,windows,maxLen,source}, stocks:[ {code,name,board,industry,date,c:[120],q:[120]} ] }
 *
 * 特征说明（与锁定方案一致）：
 *   - K线维度：窗口内收盘价（原始，min-max 归一化在查询端按所选窗口做；线性变换不改变 Pearson）
 *   - 四合一维度：强弱线 QX = EMA( posK(C,30), 3 )，posK = 100*(C-LLV(L,30))/(HHV(H,30)-LLV(L,30))
 *     文档明定为四合一核心线（kline_indicator.src.js L174-175）。后期可升级为四合一 6 线拼接。
 *
 * 数据源：腾讯 web.ifzq.gtimg.cn fqkline（稳定，避 push2）；名称从 qt.gtimg.cn 批量取。
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
const PREFIX = (code) => (/^6/.test(code) ? 'sh' : 'sz'); // 6开头沪市(含688科创/60主板)，其余深市(000/002/300)
const BOARD = (code) => {
  if (/^688/.test(code)) return '科创';
  if (/^30[01]/.test(code)) return '创业';
  if (/^60/.test(code)) return '沪主';
  if (/^00/.test(code) || /^002/.test(code)) return '深主';
  return '其他';
};
const isST = (name) => /ST|退/.test(name || '');
const isExcluded = (code) => /^(8|4|92)/.test(code); // 北交所/老三板

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

async function fetchNames(codes) {
  // qt.gtimg.cn 支持逗号批量；返回 v_sh600519="1~贵州茅台~600519~..."
  const batch = [];
  for (let i = 0; i < codes.length; i += 60) batch.push(codes.slice(i, i + 60));
  const out = {};
  for (const b of batch) {
    const url = `https://qt.gtimg.cn/q=${b.map(c => PREFIX(c) + c).join(',')}`;
    try {
      const r = await fetch(url, { headers: { 'User-Agent': UA, 'Referer': 'https://gu.qq.com/' } });
      const txt = await r.text();
      const re = /v_(\w+)="([^"]*)"/g; let m;
      while ((m = re.exec(txt))) {
        const f = m[2].split('~'); // 腾讯 qt: 0=1, 1=名称, 2=代码, 3=现价...
        const code = f[2]; if (code) out[code] = f[1] || code;
      }
    } catch (e) { /* 单批失败不影响整体 */ }
  }
  return out;
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

// ───────── 股票池 ─────────
// 生产环境：universe.json（由 Actions 从东财 datacenter-web 拉全A，含 name/industry/board）
// 样本环境：内置 SAMPLE_CODES（仅 code，name 从腾讯批量取，industry 留待生产补全）
const SAMPLE_CODES = [
  '600519','000858','000568','600809','000596','002304', // 白酒
  '300750','002594','601012','300014','002129','300274', // 新能源/电池
  '600276','300760','000661','600196','300347','002821', // 医药
  '601318','600036','601166','600000','601398','600028', // 银行/石化
  '600030','300059','601688','600837','000776','601211', // 券商
  '688981','688041','002415','000725','603501','603986', // 科技
  '600887','000651','603288','000333','600690','000100', // 消费/家电
  '600309','002493','600426','600989','000703','002460', // 化工/有色
  '000768','600760','002179','600893','000738','601989', // 军工
  '600900','601857','601628','600028','601088','600585', // 周期/公用
  '300059','300122','300015','300124','300316','300450', // 创业蓝筹
  '688111','688599','688256','688012','688036','688008', // 科创
  '000001','002230','002475','300033','600570','603259', // 综合
  '601888','600104','601933','000651','600660','601668'  // 其他龙头
];

function loadUniverse() {
  const p = path.join(__dirname, 'universe.json');
  if (fs.existsSync(p)) {
    const u = JSON.parse(fs.readFileSync(p, 'utf8'));
    return u.stocks || u;
  }
  return SAMPLE_CODES.map(code => ({ code }));
}

// ───────── 主流程 ─────────
async function main() {
  const uni = loadUniverse().filter(s => !isExcluded(s.code));
  console.log(`[gen] universe: ${uni.length} 只（已排北交/老三板）`);

  // 名称批量取
  const codes = uni.map(s => s.code);
  const names = await fetchNames(codes);
  console.log(`[gen] 取到名称 ${Object.keys(names).length} 只`);

  const results = await pool(uni, async (s) => {
    const code = s.code;
    const bars = await fetchKline(PREFIX(code) + code);
    if (!bars || bars.length < 30) return null;
    const O = bars.map(b => b.o), H = bars.map(b => b.h), L = bars.map(b => b.l), C = bars.map(b => b.c);
    const qx = qxSeries(O, H, L, C);
    const keep = 120;
    const cSlice = C.slice(-keep);
    const qSlice = qx.slice(-keep).map(v => v == null ? 0 : +v.toFixed(4));
    const name = names[code] || s.name || code;
    if (isST(name)) return null; // 排 ST/*ST
    return {
      code,
      name,
      board: s.board || BOARD(code),
      industry: s.industry || '—',
      date: bars[bars.length - 1].d,
      c: cSlice.map(v => +v.toFixed(3)),
      q: qSlice
    };
  }, 4);

  const stocks = results.filter(Boolean);
  const out = {
    meta: {
      generatedAt: new Date().toISOString(),
      count: stocks.length,
      windows: [20, 60, 120],
      maxLen: 120,
      source: 'tencent-fqkline-qfq',
      feature: 'c=close(120) | q=四合一强弱线QX=EMA(posK(C,30),3)'
    },
    stocks
  };
  const f = path.join(__dirname, '..', 'trend_features.json'); // 与 similar.html 同目录（生产部署于仓库根）
  fs.writeFileSync(f, JSON.stringify(out));
  console.log(`[gen] 写出 ${stocks.length} 只 -> ${f} (${ (fs.statSync(f).size/1024).toFixed(1) } KB)`);
}

main().catch(e => { console.error('FATAL', e); process.exit(1); });
