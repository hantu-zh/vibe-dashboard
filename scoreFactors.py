"""T0 草案：配置驱动的六因子纯函数（Py 端）。

约束：函数体内不得出现任何业务阈值字面量（7 / 40 / 25 / 60 / 65 / 4 / 100 ...）。
所有阈值、比较符、等级、满分、百分比与归一化基数均从 config 读取。
仅剩的 0 / 0.5 属结构性空值判断与舍入实现，不是业务阈值。
签名：scoreFactors(raw, config) -> {factors, score, coverage, coverage_total,
                                    level, displayable, data_quality, notes}
"""
import math

_OPS = {
    'ge': lambda v, t: v >= t,
    'gt': lambda v, t: v > t,
    'le': lambda v, t: v <= t,
    'lt': lambda v, t: v < t,
    'eq': lambda v, t: v == t,
}


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _match(cond, ctx):
    op = cond.get('op')
    if op == 'default':
        return True
    if op == 'and':
        return all(_match(c, ctx) for c in cond['conds'])
    v = ctx.get(cond['field'])
    return _num(v) and _OPS[op](v, cond['value'])


def _derive(raw, cfg):
    ctx = dict(raw)
    scale = cfg['percent_scale']
    up, down, flat = raw.get('up'), raw.get('down'), raw.get('flat')
    zt, zb = raw.get('zt'), raw.get('zb')

    total = up + down + flat if all(_num(v) for v in (up, down, flat)) else None
    ctx['up_ratio'] = up / total * scale if total else None
    denom = zt + zb if _num(zt) and _num(zb) else None
    ctx['zt_plus_zb'] = denom
    ctx['bomb_rate'] = zb / denom * scale if denom else None

    for f in cfg['factors']:
        if 'ratio_expr' not in f:
            continue
        n = ctx.get(f['ratio_expr']['numerator'])
        d = ctx.get(f['ratio_expr']['denominator'])
        if d == 0:
            flag = f.get('edge_case')
            ctx[f['value_field']] = float('inf') if cfg['edge_case_flags'].get(flag) and n else None
        else:
            ctx[f['value_field']] = n / d if _num(n) and _num(d) else None
    return ctx


def _bands(bands, value, default_op):
    if not _num(value):
        return None
    for b in bands:
        if b.get('min') is None and b.get('max') is None:
            return b['score']
        op = b.get('op', default_op)
        bound = b['max'] if b.get('max') is not None else b['min']
        if _OPS[op](value, bound):
            return b['score']
    return None


def scoreFactors(raw, cfg):
    ctx = _derive(raw, cfg)
    dq, notes, forced = 'ok', [], set()
    for pr in cfg['partial_rules']:
        if not cfg['edge_case_flags'].get(pr.get('enabled_by')):
            continue
        if _match(pr['when'], ctx):
            dq = pr['set_data_quality']
            forced.update(pr.get('set_unavailable', []))
            if pr.get('note'):
                notes.append(pr['note'])

    factors = {}
    for f in cfg['factors']:
        k = f['key']
        if k in forced or any(not _num(ctx.get(d)) for d in f['depends_on']):
            factors[k] = None
            continue
        if any(_match(u, ctx) for u in f.get('unavailable_when', [])):
            factors[k] = None
            continue
        if f['type'] == 'rules':
            factors[k] = next((r['score'] for r in f['rules'] if _match(r, ctx)), None)
        else:
            factors[k] = _bands(
                f['bands'], ctx.get(f.get('value_field', k)),
                cfg['serialization']['default_band_op'],
            )

    usable = [f for f in cfg['factors'] if factors[f['key']] is not None]
    coverage = len(usable)
    denom = sum(f['max'] for f in usable)
    score = cfg['normalization_base'] * sum(factors[f['key']] for f in usable) / denom if denom else None
    if score is not None and cfg.get('rounding') == 'round_half_up':
        score = math.floor(score + 0.5)
    lt = cfg['level_thresholds']
    level = None if score is None else (
        'green' if score >= lt['green'] else ('yellow' if score >= lt['yellow'] else 'red')
    )
    return {
        'factors': factors,
        'score': score,
        'coverage': coverage,
        'coverage_total': cfg['coverage_total'],
        'level': level,
        'displayable': coverage >= cfg['min_coverage_to_display'],
        'data_quality': dq,
        'notes': notes,
    }
