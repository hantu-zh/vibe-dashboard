# -*- coding: utf-8 -*-
"""
cffex 巡视脚本（云端 GitHub Actions 运行，不依赖本地机器）

逻辑：
  1. 算出「今日交易日」（含今天；若今天非交易日则取最近一个交易日）。
  2. 读取线上 cffex_net_position.json 的最新日期。
  3. 若已是最新 -> 结束。
  4. 否则循环运行 cffex_net_position.py 拉取并推送，直到拉到目标日期或达到最大尝试次数。
     - 每次 cffex_net_position.py 带 300s 超时（该脚本历史上会卡死 20 分钟，必须兜底）。
     - 拉到后退出；拉不到则退避后重试。

环境变量（由 workflow 注入）：
  GITHUB_TOKEN            推送用 token（Actions 自带 secrets.GITHUB_TOKEN）
  CFFEX_PATROL_ATTEMPTS  最大尝试次数，默认 6
  CFFEX_PATROL_SLEEP      每次尝试之间的退避秒数，默认 180
"""
import os
import sys
import json
import time
import ssl
import certifi
import urllib.request
import subprocess
import datetime

API = 'https://api.github.com'
REPO = 'hantu-zh/vibe-dashboard'
BRANCH = 'main'
TOKEN = os.environ.get('GITHUB_TOKEN', '')
MAX_ATTEMPTS = int(os.environ.get('CFFEX_PATROL_ATTEMPTS', '6'))
SLEEP_SEC = int(os.environ.get('CFFEX_PATROL_SLEEP', '180'))
FETCH_TIMEOUT = 300  # cffex_net_position.py 卡死兜底


def latest_trading_day():
    """返回最近一个交易日（含今天），最多回看 15 天。"""
    try:
        from chinese_calendar import is_workday
    except Exception:
        is_workday = None
    d = datetime.date.today()
    for _ in range(15):
        if is_workday is None or is_workday(d):
            return d
        d -= datetime.timedelta(days=1)
    return datetime.date.today()


def repo_latest_date():
    """读取线上 cffex_net_position.json 的最新日期；失败返回 None。"""
    url = f'{API}/repos/{REPO}/contents/cffex_net_position.json?ref={BRANCH}'
    headers = {'Accept': 'application/vnd.github.v3+json'}
    if TOKEN:
        headers['Authorization'] = f'token {TOKEN}'
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            info = json.loads(r.read())
        import base64
        content = base64.b64decode(info['content']).decode('utf-8')
        data = json.loads(content)
        dates = sorted(k for k in data if k.startswith('20'))
        return dates[-1] if dates else None
    except Exception as e:
        print(f'[patrol] 读取线上数据失败: {e}')
        return None


def main():
    target = latest_trading_day()
    target_s = target.strftime('%Y-%m-%d')
    print(f'[patrol] 目标交易日: {target_s}')

    for attempt in range(1, MAX_ATTEMPTS + 1):
        cur = repo_latest_date()
        print(f'[patrol] 当前线上最新: {cur}  (attempt {attempt}/{MAX_ATTEMPTS})')
        if cur == target_s:
            print('[patrol] ✅ 已是最新，巡视完成')
            return 0
        print(f'[patrol] 缺失 {target_s}，运行 cffex_net_position.py ...')
        try:
            rc = subprocess.call(
                [sys.executable, 'cffex_net_position.py'],
                timeout=FETCH_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            print(f'[patrol] cffex_net_position.py 超过 {FETCH_TIMEOUT}s 超时，杀掉重试')
            rc = 124
        print(f'[patrol] cffex_net_position.py 退出码: {rc}')
        # 给推送 + Pages 传播留一点时间
        time.sleep(10 if rc == 0 else 15)
        if attempt < MAX_ATTEMPTS:
            print(f'[patrol] 退避 {SLEEP_SEC}s 后重试 ...')
            time.sleep(SLEEP_SEC)

    final = repo_latest_date()
    if final == target_s:
        print('[patrol] ✅ 最终拉到数据')
        return 0
    print('[patrol] ⚠️ 达到最大尝试次数仍未拉到，等待下一轮 cron 继续')
    # 返回 0 避免刷 failure 通知；下一轮 cron 会继续巡视
    return 0


if __name__ == '__main__':
    sys.exit(main())
