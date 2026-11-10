/* T0 草案：配置驱动的六因子纯函数（JS 端）。
 * 约束：函数体内不得出现任何业务阈值字面量（7 / 40 / 25 / 60 / 65 / 4 / 100 ...）。
 * 所有阈值、比较符、等级、满分、百分比与归一化基数均从 config 读取。
 * 仅剩的 0 / 0.5 属结构性空值判断与舍入实现，不是业务阈值。
 * 签名：scoreFactors(raw, config) -> {factors, score, coverage, coverage_total,
 *                                     level, displayable, data_quality, notes}
 */
'use strict';

const OPS = {
  ge: (v, t) => v >= t,
  gt: (v, t) => v > t,
  le: (v, t) => v <= t,
  lt: (v, t) => v < t,
  eq: (v, t) => v === t,
};
const isNum = v => typeof v === 'number' && !Number.isNaN(v);

function match(c, ctx) {
  if (c.op === 'default') return true;
  if (c.op === 'and') return c.conds.every(k => match(k, ctx));
  const v = ctx[c.field];
  return isNum(v) && OPS[c.op](v, c.value);
}

function derive(raw, cfg) {
  const ctx = { ...raw };
  const scale = cfg.percent_scale;
  const up = raw.up, down = raw.down, flat = raw.flat, zt = raw.zt, zb = raw.zb;

  const total = [up, down, flat].every(isNum) ? up + down + flat : null;
  ctx.up_ratio = total ? up / total * scale : null;
  const denom = isNum(zt) && isNum(zb) ? zt + zb : null;
  ctx.zt_plus_zb = denom;
  ctx.bomb_rate = denom ? zb / denom * scale : null;

  for (const f of cfg.factors) {
    if (!f.ratio_expr) continue;
    const n = ctx[f.ratio_expr.numerator], d = ctx[f.ratio_expr.denominator];
    if (d === 0) {
      ctx[f.value_field] = (cfg.edge_case_flags[f.edge_case] && n) ? Infinity : null;
    } else {
      ctx[f.value_field] = (isNum(n) && isNum(d)) ? n / d : null;
    }
  }
  return ctx;
}

function bands(bs, value, defaultOp) {
  if (!isNum(value)) return null;
  for (const b of bs) {
    if (b.min == null && b.max == null) return b.score;
    const op = b.op || defaultOp;
    const bound = b.max != null ? b.max : b.min;
    if (OPS[op](value, bound)) return b.score;
  }
  return null;
}

function scoreFactors(raw, cfg) {
  const ctx = derive(raw, cfg);
  let dq = 'ok';
  const notes = [], forced = new Set();
  for (const pr of cfg.partial_rules) {
    if (!cfg.edge_case_flags[pr.enabled_by]) continue;
    if (match(pr.when, ctx)) {
      dq = pr.set_data_quality;
      (pr.set_unavailable || []).forEach(k => forced.add(k));
      if (pr.note) notes.push(pr.note);
    }
  }

  const factors = {};
  for (const f of cfg.factors) {
    const k = f.key;
    if (forced.has(k) || f.depends_on.some(d => !isNum(ctx[d]))) { factors[k] = null; continue; }
    if ((f.unavailable_when || []).some(u => match(u, ctx))) { factors[k] = null; continue; }
    if (f.type === 'rules') {
      const r = f.rules.find(rule => match(rule, ctx));
      factors[k] = r ? r.score : null;
    } else {
      factors[k] = bands(f.bands, ctx[f.value_field || k], cfg.serialization.default_band_op);
    }
  }

  const usable = cfg.factors.filter(f => factors[f.key] !== null);
  const coverage = usable.length;
  const denom = usable.reduce((s, f) => s + f.max, 0);
  let score = denom ? cfg.normalization_base * usable.reduce((s, f) => s + factors[f.key], 0) / denom : null;
  if (score !== null && cfg.rounding === 'round_half_up') score = Math.floor(score + 0.5);
  const lt = cfg.level_thresholds;
  const level = score === null ? null : (score >= lt.green ? 'green' : (score >= lt.yellow ? 'yellow' : 'red'));
  return {
    factors, score, coverage, coverage_total: cfg.coverage_total, level,
    displayable: coverage >= cfg.min_coverage_to_display,
    data_quality: dq, notes,
  };
}

// 双端兼容：Node 对拍用 module.exports，浏览器 <script> 用 window.scoreFactors。
// 求值逻辑完全不变，改此处后必须复跑 run_crosscheck.py 保持 6/6 PASS。
if (typeof module !== 'undefined' && module.exports) { module.exports = { scoreFactors }; }
if (typeof window !== 'undefined') { window.scoreFactors = scoreFactors; }
