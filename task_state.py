# -*- coding: utf-8 -*-
"""task_state.py — 时点任务的「补跑守护」状态机

背景
----
GitHub Actions 的 schedule 在高负载时会跳档（2026-09-09 实测：*/15 的 cron 一天只触发
7 次，最长空档 4.5 小时）。原 workflow 用「当前时间落在某个小时窗口内」来决定跑哪个任务，
一旦那个小时恰好没有 run，任务就永久丢失——表现就是：
  - 美股选股（07:00）停在 09-07 不刷新
  - 追涨强势股只有 10:00 / 12:00，14:00 和 15:30 的收盘任务是空的

改为以「今天这个时点是否已经产出过」为准，而不是「现在恰好是几点」：
只要已经过了计划时点、且今天还没成功跑过，那么当天任意一次 run 都会补跑它。

用法
----
  python task_state.py should-run <任务名> <时点HH:MM>   # exit 0 = 该跑，1 = 跳过
  python task_state.py mark <任务名>                     # 标记今天该任务已成功完成

状态落在 daily_task_state.json（按日期分组，只留最近 7 天），由 sync_func.py 推送回仓库
持久化，这样每次 workflow checkout 都能拿到当天此前各时点的完成情况。
"""
import sys, json, datetime
from pathlib import Path

BASE = Path(__file__).parent
STATE = BASE / 'daily_task_state.json'
TZ = datetime.timezone(datetime.timedelta(hours=8))   # 上海时间（不依赖 runner 的 TZ 设置）


def _today():
    return datetime.datetime.now(TZ).strftime('%Y-%m-%d')


def _now_minutes():
    n = datetime.datetime.now(TZ)
    return n.hour * 60 + n.minute


def load():
    try:
        return json.loads(STATE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def save(d):
    keep = sorted(d.keys())[-7:]          # 只保留最近 7 天，避免文件无限膨胀
    STATE.write_text(json.dumps({k: d[k] for k in keep}, ensure_ascii=False, indent=2),
                     encoding='utf-8')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    d = load()
    today = _today()

    if cmd == 'should-run':
        name, at = sys.argv[2], sys.argv[3]
        hh, mm = (int(x) for x in at.split(':'))
        done_at = (d.get(today) or {}).get(name)
        if done_at:
            print(f'[task_state] skip {name}：今日 {done_at} 已完成')
            return 1
        if _now_minutes() < hh * 60 + mm:
            print(f'[task_state] skip {name}：未到计划时点 {at}')
            return 1
        print(f'[task_state] run {name}：已过 {at} 且今日未产出 -> 补跑')
        return 0

    if cmd == 'mark':
        name = sys.argv[2]
        d.setdefault(today, {})[name] = datetime.datetime.now(TZ).strftime('%H:%M')
        save(d)
        print(f'[task_state] {name} 已标记完成')
        return 0

    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
