#!/usr/bin/env node
// 自动刷新 cyq3d 预置股筹码快照到最新交易日。
//
// 设计要点（避免算法分叉）：本文件「不复制」任何筹码计算逻辑，而是直接从
// cyq3d/cyq_3d.html 的 // ===CYQ-ALGO-START=== ... // ===CYQ-ALGO-END=== 之间
// 提取页面运行时使用的 computeCYQ / analyzePeak / analyzeWash（纯函数，无 THREE/DOM 依赖），
// 在 Node 内直接求值。这样页面与 CI 永远使用同一份算法，改页面即改 CI。
//
// 行为：
//  - 对 5 只预置股分别拉腾讯日线(n=320)+周线(n=200)，重建内嵌 JSON（updated=最新交易日）。
//  - 与旧内嵌做逐字节比较：仅当数据变化且全部拉取成功时才纯 LF 重写 cyq3d/cyq_3d.html。
//  - 任一股票拉取失败 → 进程非零退出且不写文件（绝不拿坏数据覆盖历史好数据）。
//  - 非交易日（周末/大陆节假日）拉到的末交易日与已内嵌一致 → 跳过写入 → 无空提交。
//
// 调试开关：--dry-run 只校验不写盘；--force-write 即使逐字节一致也强制重写（仅测试用）。
// 路径覆盖：CYQ_HTML=/path 可重定向目标 html（测试用）。

import fs from 'node:fs';
import path from 'node:path';

const REPO = process.cwd();
const HTML = process.env.CYQ_HTML || path.join(REPO, 'cyq3d', 'cyq_3d.html');
const DRY = process.argv.includes('--dry-run');
const FORCE = process.argv.includes('--force-write');

function extractAlgo(html) {
  const m = html.match(/\/\/ ===CYQ-ALGO-START===[\s\S]*?\/\/ ===CYQ-ALGO-END===/);
  if (!m) throw new Error('在 cyq_3d.html 未找到 CYQ-ALGO 标记，无法提取算法');
  return m[0]
    .replace(/\/\/ ===CYQ-ALGO-START===.*$/m, '')
    .replace(/\/\/ ===CYQ-ALGO-END===.*$/m, '');
}

function loadAlgo(html) {
  const src = extractAlgo(html);
  // 算法为纯函数，无外部依赖，可在 Node 直接求值
  // eslint-disable-next-line no-new-func
  const factory = new Function(src + '\n  return { computeCYQ, analyzePeak, analyzeWash };');
  return factory();
}

function extractEmbedded(html) {
  const m = html.match(/<script id="cyq-data" type="application\/json">([\s\S]*?)<\/script>/);
  if (!m) throw new Error('未找到 cyq-data 内嵌脚本');
  return m;
}

async function fetchTencent(code, ktype, n) {
  const url = `https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=${code},${ktype},,,${n}`;
  let lastErr;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 30000);
      const r = await fetch(url, { signal: ctrl.signal });
      clearTimeout(t);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const node = d && d.data && d.data[code];
      const arr = node && (node[ktype] || node['qfq' + ktype]);
      if (!arr || !arr.length) throw new Error('EMPTY');
      // 与页面 fetchTencent 完全一致的行变换：[date, open, close, high, low, volume]
      return arr.map(x => [x[0], +x[1], +x[2], +x[3], +x[4], +x[5]]);
    } catch (e) {
      lastErr = e;
      if (e.message === 'EMPTY') throw new Error(`${code} ${ktype} 无数据（代码/参数错误）`);
      if (attempt < 2) await new Promise(s => setTimeout(s, 1200 * (attempt + 1)));
    }
  }
  throw new Error(`${code} ${ktype} 拉取失败: ${lastErr && lastErr.message}`);
}

// 比率字段四舍五入到 10 位小数：消除浏览器 V8 与 Node V8 在 Math.exp/浮点加法上的
// 1-ULP 末位微差，使内嵌输出与运行时环境无关、可跨写入者稳定（避免每次都判“有变化”而重写）。
const r10 = x => Math.round(x * 1e10) / 1e10;

async function main() {
  const html = fs.readFileSync(HTML, 'utf8');
  const { computeCYQ, analyzePeak, analyzeWash } = loadAlgo(html);
  const emb = extractEmbedded(html);
  const data = JSON.parse(emb[1]);
  const codes = Object.keys(data.stocks);
  const names = {};
  for (const c of codes) names[c] = data.stocks[c].name;

  const newStocks = {};
  let latest = '';
  for (const code of codes) {
    const day = await fetchTencent(code, 'day', 320);
    const week = await fetchTencent(code, 'week', 200);
    const dm = computeCYQ(day), wm = computeCYQ(week);
    dm.peak = analyzePeak(dm.__last, dm.pmin, dm.pmax, dm.nb, dm.close[dm.close.length - 1]);
    dm.peak.peak_ratio = r10(dm.peak.peak_ratio); dm.peak.second_ratio = r10(dm.peak.second_ratio);
    dm.wash = analyzeWash(day, dm.__sliceOf, 'day');
    wm.peak = analyzePeak(wm.__last, wm.pmin, wm.pmax, wm.nb, wm.close[wm.close.length - 1]);
    wm.peak.peak_ratio = r10(wm.peak.peak_ratio); wm.peak.second_ratio = r10(wm.peak.second_ratio);
    wm.wash = analyzeWash(week, wm.__sliceOf, 'week');
    delete dm.__last; delete dm.__sliceOf; delete wm.__last; delete wm.__sliceOf;
    newStocks[code] = { name: names[code] || '自定义', day: dm, week: wm };
    const last = day[day.length - 1][0];
    if (last > latest) latest = last;
  }

  const newData = { source: data.source, updated: latest, stocks: newStocks };
  const newInner = JSON.stringify(newData);

  if (DRY) {
    const same = newInner === emb[1];
    console.log(`[dry-run] 预置股数=${codes.length} 最新交易日=${latest} 与已内嵌逐字节一致=${same}`);
    if (!same) {
      const oldLast = {}, newLast = {};
      for (const c of codes) { oldLast[c] = data.stocks[c].day.dates.at(-1); newLast[c] = newStocks[c].day.dates.at(-1); }
      console.log('[dry-run] 旧末日', JSON.stringify(oldLast));
      console.log('[dry-run] 新末日', JSON.stringify(newLast));
    }
    return;
  }

  if (!FORCE && newInner === emb[1]) {
    console.log(`无变化（已内嵌至最新交易日 ${latest}），跳过写入与提交。`);
    return;
  }

  // 仅替换内嵌 JSON 内容，保留外层 <script> 标签原文（避免 $ 等特殊字符转义问题）
  const re = /(<script id="cyq-data" type="application\/json">)[\s\S]*?(<\/script>)/;
  const newHtml = html.replace(re, (mm, open, close) => open + newInner + close);
  fs.writeFileSync(HTML, newHtml, 'utf8'); // Node 默认写 LF
  const raw = fs.readFileSync(HTML);
  if (raw.includes(Buffer.from('\r\n'))) throw new Error('检测到 CRLF，中止写入以免污染整文件 diff');
  console.log(`已重写 cyq3d/cyq_3d.html，内嵌数据更新至 ${latest}（git 步骤将按差异提交）。`);
}

main().catch(e => { console.error('FATAL', e.message); process.exit(1); });
