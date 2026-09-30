# -*- coding: utf-8 -*-
"""推送 index.html 前的完整性守卫。

背景：auto_trader_nav 工作流会用 checkout 到的副本改 index.html 再推。若副本比远端旧
（job 排队期间 sync bot 已推新内容），就会把新内容整篇覆盖回去 —— 历史上多次让页面
关键模块「凭空消失」。本脚本在 push 前拿「远端最新 index.html」和「待推送内容」对账，
发现关键模块丢失或体积异常缩水就中止，宁可不补 nav 也不覆盖用户数据。

用法：
    python auto_trader/guard_index.py                    # 基线取 origin/main:index.html
    python auto_trader/guard_index.py --base HEAD~1:index.html
退出码：0 = 通过；1 = 中止（不要推送）
"""
import subprocess
import sys

# 必须始终存在的关键模块标记（页面骨架 / 各功能面板）
MUST_MODULES = [
    'top-nav',            # 顶部导航
    'sector-panel',       # 板块热度追踪
    'MODULE: sector',     # 板块模块注释
    'etf-root',           # ETF 雷达
    'slowrise',           # 慢热板块
]
MIN_RATIO = 0.9  # 体积不得低于基线的 90%


def _git_show(spec):
    r = subprocess.run(['git', 'show', spec], capture_output=True, text=True)
    if r.returncode != 0:
        return ''
    return r.stdout


def main():
    base_spec = 'origin/main:index.html'
    argv = sys.argv[1:]
    if '--base' in argv:
        i = argv.index('--base')
        if i + 1 < len(argv):
            base_spec = argv[i + 1]

    try:
        cur = open('index.html', encoding='utf-8').read()
    except FileNotFoundError:
        print('[guard] 找不到 index.html'); return 1

    base = _git_show(base_spec)
    if not base:
        print('[guard] 取不到基线 %s，跳过体积校验（仍校验模块存在性）' % base_spec)

    miss = [k for k in MUST_MODULES if k in base and k not in cur]
    if miss:
        print('[guard] ABORT: 待推送的 index.html 丢失关键模块 -> %s' % miss)
        return 1

    if base and len(cur) < len(base) * MIN_RATIO:
        print('[guard] ABORT: 体积异常缩水 %d -> %d (低于基线 %.0f%%)'
              % (len(base), len(cur), MIN_RATIO * 100))
        return 1

    print('[guard] OK: %d -> %d chars，关键模块齐全（基线 %s）'
          % (len(base), len(cur), base_spec))
    return 0


if __name__ == '__main__':
    sys.exit(main())
