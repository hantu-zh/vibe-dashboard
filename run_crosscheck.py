"""T0 对拍：Py 端与 JS 端 scoreFactors 对同一份配置 + 6 个 fixture 逐字段比对。

用法：python run_crosscheck.py   （需 node 在 PATH 中）
退出码 0 = 全部通过；1 = 有差异。
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as f:
        return json.load(f)


cfg = load('breadth_factors.json')
fxs = load('breadth_fixtures.json')
TOL = cfg['float_tolerance']          # T1 去重：唯一真源在 factors 顶层，fixtures 不再重复定义

# 版本一致性：配置改了但 fixture/快照版本没跟上时，直接判失败而不是静默用旧靶子对拍
VER = cfg['version']
if fxs['spec_version'] != VER or fxs['factors_version'] != VER:
    print('VERSION MISMATCH: factors=%s  fixtures.spec_version=%s  fixtures.factors_version=%s' % (
        VER, fxs['spec_version'], fxs['factors_version']))
    sys.exit(1)

spec = importlib.util.spec_from_file_location('sf_py', os.path.join(HERE, 'scoreFactors.py'))
sf_py = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sf_py)

JS_DRIVER = r"""
const fs = require('fs');
const { scoreFactors } = require('./scoreFactors.js');
const cfg = JSON.parse(fs.readFileSync('breadth_factors.json', 'utf8'));
const fxs = JSON.parse(fs.readFileSync('breadth_fixtures.json', 'utf8'));
process.stdout.write(JSON.stringify(fxs.fixtures.map(f => ({ id: f.id, got: scoreFactors(f.raw, cfg) }))));
"""
js_out = subprocess.check_output(['node', '-e', JS_DRIVER], cwd=HERE, text=True)
js_res = {r['id']: r['got'] for r in json.loads(js_out)}


def close(a, b, tol=TOL):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(close(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b))
    return a == b


def diff(got, exp, prefix=''):
    """返回 (path, got, expect) 差异列表；exp 中出现的键才比对。"""
    out = []
    for k, v in exp.items():
        if k in ('score_formula', 'desc', 'raw', 'id', 'notes_contains'):
            continue
        g = got.get(k)
        if not close(g, v):
            out.append((prefix + k, g, v))
    return out


ok = True
print('config=%s  fixtures=%d  tolerance=%g\n' % (cfg['version'], len(fxs['fixtures']), TOL))
for fx in fxs['fixtures']:
    fid, raw, exp = fx['id'], fx['raw'], fx['expect']
    py = sf_py.scoreFactors(raw, cfg)
    js = js_res[fid]

    e_py = diff(py, exp)
    e_js = diff(js, exp)
    e_cross = diff(py, js)          # Py 作 got、JS 作 expect 基准
    notes_ok = all(any(n in x for x in py['notes']) and any(n in x for x in js['notes'])
                   for n in exp.get('notes_contains', []))
    if not notes_ok:
        e_py.append(('notes_contains', py['notes'], exp.get('notes_contains')))
        e_js.append(('notes_contains', js['notes'], exp.get('notes_contains')))

    passed = not (e_py or e_js or e_cross) and notes_ok
    ok &= passed
    print('%s %-38s py_score=%-20s js_score=%-20s coverage=%s displayable=%s' % (
        'PASS' if passed else 'FAIL', fid, py['score'], js['score'],
        py['coverage'], py['displayable']))
    for label, errs in (('PY vs EXPECT', e_py), ('JS vs EXPECT', e_js), ('PY vs JS', e_cross)):
        for path, g, e in errs:
            print('      [%s] %s: got=%r expect=%r' % (label, path, g, e))

print('\n%s' % ('ALL PASS - T0 cross-check green' if ok else 'HAS FAILURES'))
sys.exit(0 if ok else 1)
