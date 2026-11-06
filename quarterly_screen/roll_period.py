#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""报告期自动滚动（quantitative, 无人工）

按「法定披露截止日」判定当期可用报告期，并把 config.yaml 里四个联动字段一次性滚好：
  report_period / pe.annualize_factor / pe.period_label / holders_count.history_periods

为什么要用披露截止日、而不是"最新自然季度"：
  A 股定期报告法定披露截止日 ——
    一季报(0331) → 当年 4/30
    中报  (0630) → 当年 8/31
    三季报(0930) → 当年 10/31
    年报  (1231) → 次年 4/30
  早于截止日时全市场报表未披露完，强行跑会得到残缺样本（例如"仅预告"占比高、
  户均市值缺失），所以只滚到「截止日已过」的最新一期。

用法：
  python roll_period.py                # 打印判定结果，不改文件（dry-run 默认）
  python roll_period.py --apply        # 真正写回 config.yaml
  python roll_period.py --date 2026-11-05 --apply   # 模拟某一天（测试用）
  python roll_period.py --apply --github-output     # 顺带写 GitHub Step Output

退出码：0 正常（含无需改动）；2 参数/文件异常
输出标记（便于 workflow 判断）：
  PERIOD=<yyyymmdd>    本次判定应使用的报告期
  CHANGED=1|0          config.yaml 是否被改动
"""
import argparse
import datetime as dt
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.yaml")

# (月, 日) -> (截止月, 截止日, 截止是否跨年)
PERIOD_DEADLINE = {
    "0331": (4, 30, False),
    "0630": (8, 31, False),
    "0930": (10, 31, False),
    "1231": (4, 30, True),
}
PERIOD_LABEL = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}
# 累计期 → 全年年化系数（与 pipeline.parse_annualize_factor 约定一致）
PERIOD_FACTOR = {"0331": "4", "0630": "2", "0930": "4/3", "1231": "1"}


def deadline_of(period):
    """report_period(YYYYMMDD) -> 法定披露截止日"""
    y, md = int(period[:4]), period[4:]
    mm, dd, cross = PERIOD_DEADLINE[md]
    return dt.date(y + (1 if cross else 0), mm, dd)


def candidates(today):
    """返回近期可能已披露的报告期（升序）"""
    out = []
    for y in (today.year - 1, today.year, today.year + 1):
        for md in PERIOD_DEADLINE:
            out.append("%04d%s" % (y, md))
    return sorted(out)


def latest_disclosed(today):
    """最新一期「截止日已过」的报告期"""
    ok = [p for p in candidates(today) if deadline_of(p) <= today]
    return ok[-1] if ok else None


def prev_periods(period, n=2):
    """相对 report_period 的上 1 / 上 2 报告期"""
    y, md = int(period[:4]), period[4:]
    seq = ["0331", "0630", "0930", "1231"]
    i = seq.index(md)
    out = []
    for k in range(1, n + 1):
        j = i - k
        yy = y
        while j < 0:
            j += 4
            yy -= 1
        out.append("%04d%s" % (yy, seq[j]))
    return out


def read_text(path=CONFIG):
    with open(path, encoding="utf-8") as f:
        return f.read()


def current_period(text):
    m = re.search(r'^report_period:\s*["\']?(\d{8})["\']?', text, re.M)
    return m.group(1) if m else None


def rewrite(text, period, offset_months):
    """把四个联动字段一次改到位，保留原有注释结构"""
    label = PERIOD_LABEL[period[4:]]
    factor = PERIOD_FACTOR[period[4:]]
    hist = prev_periods(period, 2)
    rep = dt.date(int(period[:4]), int(period[4:6]), int(period[6:8]))
    mm = rep.month - 1 + offset_months
    ay, am = rep.year + mm // 12, mm % 12 + 1
    # 3/31 + 1 月 = 4/31 不存在 → 收敛到当月末（4/30）
    ad = min(rep.day, (dt.date(ay + (am // 12), am % 12 + 1, 1) - dt.timedelta(days=1)).day)
    avail = dt.date(ay, am, ad)

    new, n = text, 0

    def sub1(pat, repl, s):
        s2, k = re.subn(pat, repl, s, count=1, flags=re.M)
        return s2, k

    new, k = sub1(r'^report_period:.*$',
                  'report_period: "%s"      # 报告期（%s %s）。可用日 = 报告期 + %d 月 = %s'
                  % (period, period[:4], label, int(offset_months), avail.isoformat()),
                  new)
    n += k
    new, k = sub1(r'^(\s*annualize_factor:\s*)["\']?[^"\'#\s]+["\']?',
                  r'\g<1>"%s"' % factor, new)
    n += k
    new, k = sub1(r'^(\s*period_label:\s*)["\']?[^"\'#\s]+["\']?',
                  r'\g<1>"%s"' % label, new)
    n += k
    new, k = sub1(r'^(\s*history_periods:\s*)\[[^\]]*\]',
                  r'\g<1>["%s", "%s"]' % (hist[0], hist[1]), new)
    n += k
    return new, n, label, factor, hist


def read_offset_months(text):
    m = re.search(r'^\s*data_available_offset_months:\s*(\d+)', text, re.M)
    return int(m.group(1)) if m else 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="模拟今天（YYYY-MM-DD），测试用")
    ap.add_argument("--apply", action="store_true", help="写回 config.yaml")
    ap.add_argument("--github-output", action="store_true", help="写 GITHUB_OUTPUT")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    target = latest_disclosed(today)
    if not target:
        print("ERROR: 无法判定报告期", file=sys.stderr)
        return 2

    text = read_text()
    cur = current_period(text)
    offset = read_offset_months(text)
    print("[roll] today=%s 判定报告期=%s(%s) 当前=%s"
          % (today, target, PERIOD_LABEL[target[4:]], cur))

    changed = 0
    if cur and target <= cur:
        print("[roll] 无需改动：当前报告期 %s 已是最新可用（或更新）" % cur)
    else:
        new, n, label, factor, hist = rewrite(text, target, offset)
        print("[roll] 目标字段：report_period=%s label=%s annualize=%s history=%s（命中 %d 处）"
              % (target, label, factor, hist, n))
        if n < 4:
            print("WARN: 期望改写 4 处，实际命中 %d 处，请人工核对 config.yaml" % n, file=sys.stderr)
        if args.apply:
            with open(CONFIG, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            changed = 1
            print("[roll] 已写回 %s" % CONFIG)
        else:
            print("[roll] dry-run：未写文件（加 --apply 生效）")

    print("PERIOD=%s" % target)
    print("CHANGED=%d" % changed)
    # 面板上已有本期结果 且 报告期没变 → 不必再花 40 分钟重算
    json_period, skip = "", 0
    jp = os.path.join(HERE, os.pardir, "quarterly_screen.json")
    try:
        import json as _json
        with open(os.path.abspath(jp), encoding="utf-8") as f:
            json_period = str(_json.load(f).get("report_period") or "")
    except Exception:
        json_period = ""
    if (not changed) and json_period == target:
        skip = 1
        print("[roll] 面板已是本期结果（%s），本次跳过全量重算" % json_period)
    print("DONE=%d" % (1 if json_period == target else 0))
    print("SKIP=%d" % skip)
    if args.github_output:
        p = os.environ.get("GITHUB_OUTPUT")
        if p:
            with open(p, "a", encoding="utf-8") as f:
                f.write("period=%s\nchanged=%d\ndone=%d\nskip=%d\n"
                        % (target, changed, 1 if json_period == target else 0, skip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
