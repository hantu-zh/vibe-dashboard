# -*- coding: utf-8 -*-
"""
patrol_reconcile.py — 每日全链路对账（自巡检）

背景（2026-10-13 上线）：
此前的巡检只看「单个文件自己的最新日期」，抓不到下面四类事故，导致问题长期靠用户截图发现：
  ① 生成器从未被任何 workflow 调度 —— fourweek_newhigh / sector_snapshot 冻住数周
  ② 产物没有推送路径 —— 只活在临时 runner，run 结束即销毁
  ③ 源数据都新鲜、但页面展示的是派生旧值 —— strong.html 三战法面板停在 10-09
  ④ 策略连续多日 0 结果却无人发现 —— 大力水手下午自 10-10 起每 15 分钟补跑却始终为空

本脚本把检查固定下来，每个交易日收盘后跑一次，异常时钉钉告警 + Actions 标红。

检查项：
  A 生成器→调度覆盖：写 JSON 的脚本必须被某个 workflow 调用（或被已调度的脚本 import）
  B 数据→推送覆盖：被页面消费的 JSON 必须有人推送（sync_func 清单 或 workflow 的 git add）
  C 时效基线：带明确交付时点的关键文件，过了时点仍是旧日期 → 告警
  D 派生视图：daily_picks 的策略键（页面会渲染的那些）距最近一次有料超过阈值 → 告警
  E 幽灵键：页面 fallback 会渲染、但已无生成器写入的历史键 → 告警

退出码：发现 HARD 问题 → 1（Actions 标红），否则 0。SOFT 问题只告警不标红。
"""
import json
import os
import re
import sys
import glob
import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(ROOT, '.github', 'workflows')

try:
    from chinese_calendar import is_workday as _is_workday
except Exception:
    _is_workday = None


def read(p):
    try:
        return open(p, encoding='utf-8', errors='replace').read()
    except Exception:
        return ''


def load_json(p):
    try:
        return json.loads(read(p))
    except Exception:
        return None


def is_trading_day(d):
    if _is_workday is not None:
        try:
            return _is_workday(d)
        except Exception:
            pass
    return d.weekday() < 5


def trading_days_since(d0, d1):
    """d1 与 d0 之间的交易日数（不含 d0，含 d1）"""
    n = 0
    c = d0 + datetime.timedelta(days=1)
    while c <= d1:
        if is_trading_day(c):
            n += 1
        c += datetime.timedelta(days=1)
    return n


DATE_RE = re.compile(r'20\d\d-\d\d-\d\d')


def latest_date(obj):
    """递归找对象里最大的 YYYY-MM-DD 字符串"""
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v)
        elif isinstance(o, list):
            for v in o[:80]:
                walk(v)
        elif isinstance(o, str):
            m = DATE_RE.search(o)
            if m:
                found.append(m.group(0))

    walk(obj)
    return max(found) if found else None


# ─── 输入 ──────────────────────────────────────────────────────────────
wf_texts = {}
for p in sorted(glob.glob(os.path.join(WF_DIR, '*.yml'))):
    wf_texts[os.path.basename(p)] = read(p)
WF_ALL = '\n'.join(wf_texts.values())

py_files = {}
for p in glob.glob(os.path.join(ROOT, '**', '*.py'), recursive=True):
    if '__pycache__' in p:
        continue
    py_files[os.path.basename(p)] = read(p)

sync_func_src = read(os.path.join(ROOT, 'sync_func.py'))
html_files = {}
for p in glob.glob(os.path.join(ROOT, '*.html')) + glob.glob(os.path.join(ROOT, '*', '*.html')):
    html_files[os.path.relpath(p, ROOT)] = read(p)

# 人工确认过：库 / 助手 / 手动工具 / 已知遗留。新增项必须写明理由。
WHITELIST = {
    # 库与助手（被其它已调度脚本 import）
    'paths.py', 'secrets_conf.py', 'dingtalk.py', 'dingtalk_style.py', 'data_source.py',
    'em_api_helper.py', 'kline_src.py', 'turnover_utils.py', 'daily_picks_store.py',
    'chanlun_quick.py', 'board_rps.py', 'etf_data.py', 'analyze.py', 'research_sources.py',
    'mx_select_stock.py', 'gen_lst.py', 'sync_demo_data.py', 'pick_tracker.py', 'ai_analysis.py',
    'market_review.py', 'research_weekly.py', 'generate_research_html.py', 'sync_research.py',
    # 手动执行的一次性/部署工具
    'deploy_analytics.py', 'deploy_multipush.py', 'embed_sector_rankings.py', 'enrich_lst.py',
    # 已知遗留：活路在别处（sector_funnel→research_scanner 内联；update_slowrise→refresh_slowrise）
    'sector_funnel.py', 'update_slowrise.py',
    # 巡检/调度基础设施自身
    'patrol_reconcile.py', 'daily_task_monitor.py', '_verify_data.py', 'task_state.py', 'trading_day.py',
}

# 时效基线（刻意保守：只列调度时点明确的文件）。时点 = 计划时点 + 补跑余量。
# (文件名, 期望已交付时点 HH:MM, 是否 HARD)
DEADLINES = [
    ('chen_xiaoqun_result.json', '15:30', True),   # run_task 14:30
    ('sector_snapshot.json',     '16:30', True),   # run_task 15:35
    ('fourweek_newhigh.json',    '16:30', True),   # run_task 15:45
    ('slowrise_stocks.json',     '16:30', True),   # run_task 15:30
    ('market_review.json',       '17:00', True),   # run_task 16:00
    ('strategy_tabs.json',       '21:00', True),   # run_task 20:05
    ('sector_funnel.json',       '13:30', False),  # research_scanner 12:00 场次（软）
]

issues_hard = []
issues_soft = []


def hard(msg):
    issues_hard.append(msg)


def soft(msg):
    issues_soft.append(msg)


# ─── A 生成器 → 调度覆盖 ────────────────────────────────────────────────
PRODUCER_RE = re.compile(r'(json\.dump\(|\.to_json\(|\.json[\'"]\s*,\s*[\'"][wa])')


def referenced(basename):
    return re.search(r'(^|[/\s\'"])' + re.escape(basename) + r'(\b|$)', WF_ALL) is not None


def imported_by_scheduled(basename):
    stem = basename[:-3]
    for name, src in py_files.items():
        if name == basename:
            continue
        if re.search(r'(import|from)\s+' + re.escape(stem) + r'\b', src) and referenced(name):
            return name
    return None


for name in sorted(py_files):
    if name in WHITELIST:
        continue
    src = py_files[name]
    if not PRODUCER_RE.search(src):
        continue                      # 不产出数据 → 不要求被调度
    if referenced(name):
        continue
    who = imported_by_scheduled(name)
    if who:
        continue                      # 间接调度（如 sync_demo_data ← daily_run）
    hard(f'[A] 生成器从未被调度: {name}（写 JSON 但 23 个 workflow 里无人调用）')

# ─── B 数据 → 推送覆盖（只查被页面消费的）────────────────────────────────
# 人工维护静态 JSON 白名单：手工编辑后直接 commit 即交付，无生成器、无需 CI 推送。
# 新增必须写明理由；某文件接上真实生成器后应立即从此处移除。
STATIC_JSON_OK = {
    # 2026-10-15 定增跟踪种子数据（证券时报·增发破发页+公开公告人工整理，week.html 消费）。
    # 东财 datacenter RPTA_WEB_DZJG 实时接口接通、改由生成器产出后移除本条。
    'dz_stock_seed.json',
    # 2026-10-20 三季报业绩超预期跟踪（q3.html/week.html 消费）。由仓库外 scan_q3.py
    # （东财 RPT_PUBLIC_OP_NEWPREDICT 全市场扫描）本地生成后手工 commit，季报季结束即归档。
    # 接上真实 CI 生成器后应立即移除本条。
    'q3_earnings.json',
    'q3_scan_candidates.json',
}

# 已知遗留别名（daily_picks.json 历史数据里残留的旧策略名）。这些策略已改名、
# 新名已进 index.html 调度（sched_names），并非「无生成器写入的幽灵键」，只是被
# 改名替代——故从 [E] 幽灵键检查跳过，消除误报。
# 待 ② 清理 daily_picks.json 死键时，本条应随死键一并移除。
LEGACY_ALIAS_OK = {
    '菠菜涨停战法',        # → 大力水手菠菜涨停战法（D:\bt_inspect\live_predict_tomorrow_limit_up.py）
    '资金净流入',          # → 高欣资金净流入（gaoxin_money_flow_em.py / akshare.py）
    '陈小群战法四步选股',  # → 陈小群战法（D:\20260703\vibe-dashboard\jack_captain.py）
}


def has_push_path(fname):
    if fname in sync_func_src:
        return True
    for t in wf_texts.values():
        for m in re.finditer(r'git add ([^\n|;&]+)', t):
            if fname in m.group(1):
                return True
    return False


data_jsons = [os.path.basename(p) for p in glob.glob(os.path.join(ROOT, '*.json'))]
for j in sorted(data_jsons):
    consumers = [h for h, txt in html_files.items() if j in txt]
    if not consumers:
        continue                      # 无页面消费 → 不是交付物
    if j in STATIC_JSON_OK:
        continue                      # 人工维护静态文件 → commit 即交付
    if not has_push_path(j):
        hard(f'[B] 被页面消费但无推送路径: {j}（页面: {",".join(consumers[:3])}）→ 数据会冻在仓库旧版')

# ─── C 时效基线 ────────────────────────────────────────────────────────
today = datetime.date.today()
now_hm = datetime.datetime.now().strftime('%H:%M')
if is_trading_day(today):
    for fname, deadline, is_hard in DEADLINES:
        p = os.path.join(ROOT, fname)
        if not os.path.exists(p):
            (hard if is_hard else soft)(f'[C] 基线文件不存在: {fname}')
            continue
        if now_hm < deadline:
            continue                  # 还没到交付时点
        d = latest_date(load_json(p))
        if d and d < today.strftime('%Y-%m-%d'):
            msg = f'[C] 过时: {fname} 最新={d}，应>= {today}（时点 {deadline}）'
            (hard if is_hard else soft)(msg)

# ─── D/E daily_picks 派生视图 ──────────────────────────────────────────
dp = load_json(os.path.join(ROOT, 'daily_picks.json'))
index_html = read(os.path.join(ROOT, 'index.html'))
sched_names = set(re.findall(r"\{\s*name:\s*'([^']+)'", index_html))
non_stock = set(re.findall(r"_nonStockTasks\s*=\s*new Set\(\[([^\]]*)\]", index_html))
non_stock = {x.strip().strip('\'" ') for x in (list(non_stock)[0].split(',') if non_stock else [])}

if isinstance(dp, dict):
    data = dp.get('data', dp)
    last_nonempty = {}
    for day, strats in data.items():
        if not DATE_RE.fullmatch(str(day)) or not isinstance(strats, dict):
            continue
        for k, v in strats.items():
            if isinstance(v, dict) and v.get('picks'):
                last_nonempty.setdefault(k, []).append(day)
    for k, days in sorted(last_nonempty.items()):
        if k in non_stock or k in LEGACY_ALIAS_OK:
            continue
        last = max(days)
        d0 = datetime.date(*map(int, last.split('-')))
        gap = trading_days_since(d0, today)
        if k in sched_names:
            if gap >= 5:
                soft(f'[D] 策略多日无产出: {k} 最近有料 {last}（已过 {gap} 个交易日）'
                     f' → 页面仍会渲染该卡片，需确认是「条件严格」还是「已坏」')
        else:
            if gap >= 5:
                soft(f'[E] 幽灵键: {k} 最近有料 {last}（已过 {gap} 个交易日），'
                     f'无生成器写入但 index.html 的 fallback 仍会渲染 → 建议停用或改名对齐')

# ─── 报告 ──────────────────────────────────────────────────────────────
lines = ['## 🔍 每日全链路对账', '', f'**{today} {now_hm}**（{"交易日" if is_trading_day(today) else "非交易日"}）', '']
if not issues_hard and not issues_soft:
    lines.append('### ✅ 全部通过')
else:
    if issues_hard:
        lines.append(f'### ❌ 硬性问题 {len(issues_hard)} 项')
        for m in issues_hard:
            lines.append(f'- {m}')
        lines.append('')
    if issues_soft:
        lines.append(f'### ⚠️ 提示 {len(issues_soft)} 项')
        for m in issues_soft:
            lines.append(f'- {m}')

report = '\n'.join(lines)
print(report)
print('=' * 60)
for m in issues_hard:
    print('HARD', m)
for m in issues_soft:
    print('SOFT', m)
print(f'汇总: HARD={len(issues_hard)} SOFT={len(issues_soft)}')

# 钉钉告警：有 HARD 必推；否则仅在交易日推一条简报（便于确认巡检自身在跑）
try:
    sys.path.insert(0, ROOT)
    from dingtalk import send_dingtalk
    if issues_hard:
        send_dingtalk(f'❌ 对账发现 {len(issues_hard)} 项硬性问题', report)
    elif is_trading_day(today):
        send_dingtalk('✅ 每日对账通过', report)
except Exception as e:
    print(f'[warn] 钉钉推送跳过: {e}')

sys.exit(1 if issues_hard else 0)
