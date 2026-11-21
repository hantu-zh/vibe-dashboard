# -*- coding: utf-8 -*-
"""三层仲裁 - 否决层 VETO 判定引擎（基于真实数据）

数据源优先级: VETO_DATA_DIR 环境变量 > 脚本所在目录（CI 里就是仓库根目录）。
工程改进: flt_pct 命名 / 跨节识别 / MA 兜底 / NaN 与数据滞后防御。

compute() 返回结构化结果供自动点评生成器复用；main() 保留原有命令行输出。
"""
import json
import math
import os
from datetime import datetime
from collections import OrderedDict

DATA_DIR = os.environ.get('VETO_DATA_DIR') or os.path.dirname(os.path.abspath(__file__))
N = 4                       # 连续净降周数门槛（待校准）
GAP_HOLIDAY = 9             # 周末日期间隔>9天视为跨节周
STALE_DAYS = 10             # 数据末日距今天超过该天数 → 数据源疑似停更, 显式报警


def pick(name, base=None):
    base = base or DATA_DIR
    return os.path.join(base, name)


def week_key(d):
    y, w, _ = datetime.strptime(d, "%Y-%m-%d").isocalendar()
    return (y, w)


def _finite(v):
    """NaN/Infinity 一律视为缺失（Python json 默认放行 NaN 字面量, 必须显式挡）"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def weekly_last(rows, date_k, val_k):
    """每个交易周取最后交易日的值; 同时记录该周末日期。
    值缺失(None)/非有限数(NaN/Inf)的行直接跳过, 该周回退到最后一个有效交易日。
    """
    out = OrderedDict()
    for r in rows:
        f = _finite(r.get(val_k))
        if f is None:
            continue
        k = week_key(r[date_k])
        out[k] = (r[date_k], f)
    return out


def ma(closes, n):
    """MA 兜底: 历史不足 n 根返回 None, 防节后首日 NaN"""
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def compute(data_dir=None):
    """跑一遍真实数据判定，返回结构化结果（不打印）。"""
    base = data_dir or DATA_DIR
    fin_raw = json.load(open(pick("margin_m227.json", base), encoding="utf-8"))["data"]
    idx_raw = json.load(open(pick("margin_hs300.json", base), encoding="utf-8"))["data"]
    fin_w = weekly_last(fin_raw, "date", "fin_balance")   # 绝对余额, 非比值
    idx_w = weekly_last(idx_raw, "date", "close")
    weeks = sorted(set(fin_w) & set(idx_w), key=lambda k: (k[0], k[1]))

    records = []
    for i in range(1, len(weeks)):
        w, pw = weeks[i], weeks[i - 1]
        fin_now, fin_prev = fin_w[w][1], fin_w[pw][1]
        idx_now, idx_prev = idx_w[w][1], idx_w[pw][1]

        # 变化率(百分点), 命名 flt_pct / idx_pct, 明确非绝对值
        if fin_prev <= 0 or idx_prev <= 0:
            records.append(dict(week=w, date=fin_w[w][0], fin_pct=None,
                                idx_pct=None, fin_down=False, idx_down=False,
                                cross=True, gap=None, bad=True))
            continue
        fin_pct = (fin_now - fin_prev) / fin_prev * 100
        idx_pct = (idx_now - idx_prev) / idx_prev * 100
        if not (math.isfinite(fin_pct) and math.isfinite(idx_pct)):
            records.append(dict(week=w, date=fin_w[w][0], fin_pct=None,
                                idx_pct=None, fin_down=False, idx_down=False,
                                cross=True, gap=None, bad=True))
            continue

        # 跨节识别: 用实际周末日期差, 不用纯列表 prev
        gap = (datetime.strptime(fin_w[w][0], "%Y-%m-%d")
               - datetime.strptime(fin_w[pw][0], "%Y-%m-%d")).days
        cross_holiday = gap > GAP_HOLIDAY
        records.append(dict(week=w, date=fin_w[w][0], fin_pct=fin_pct,
                            idx_pct=idx_pct, fin_down=fin_pct < 0,
                            idx_down=idx_pct < 0, cross=cross_holiday,
                            gap=gap, bad=False))

    # 连续融资净降周数；跨节周口径不纯，剔除并中断
    fin_streak = 0
    for r in reversed(records):
        if r["cross"]:
            break
        if r["fin_down"]:
            fin_streak += 1
        else:
            break

    # VETO: 连续 N 周融资净降 AND 当周指数下跌（严格同周）
    veto_events = []
    for i in range(len(records)):
        if i < N - 1:
            continue
        window = records[i - N + 1:i + 1]
        fin_n = (all(r["fin_down"] for r in window)
                 and all(not r["cross"] for r in window))
        if fin_n and records[i]["idx_down"]:
            veto_events.append(records[i])

    last = records[-1]
    veto_now = last["fin_down"] and last["idx_down"] and fin_streak >= N
    closes = [r["close"] for r in idx_raw if r.get("close") is not None]
    ma_values = {n: ma(closes, n) for n in (20, 60, 120)}

    today = datetime.now()
    stale = {}
    for name, last_date in (("margin", fin_w[weeks[-1]][0]),
                            ("hs300", idx_w[weeks[-1]][0])):
        stale[name] = {
            "last_date": last_date,
            "lag_days": (today - datetime.strptime(last_date, "%Y-%m-%d")).days,
            "stale": (today - datetime.strptime(last_date, "%Y-%m-%d")).days > STALE_DAYS,
        }

    return dict(fin_raw=fin_raw, idx_raw=idx_raw, fin_w=fin_w, idx_w=idx_w,
                weeks=weeks, records=records, fin_streak=fin_streak,
                veto_events=veto_events, veto_now=veto_now, last=last,
                closes=closes, ma=ma_values, stale=stale,
                fin_last_date=fin_w[weeks[-1]][0],
                idx_last_date=idx_w[weeks[-1]][0],
                bad_weeks=[r["date"] for r in records if r.get("bad")])


def main():
    r = compute()
    weeks, fin_w, idx_w = r["weeks"], r["fin_w"], r["idx_w"]
    last, streak = r["last"], r["fin_streak"]
    print("数据末日: 融资 %s / 指数 %s" % (r["fin_last_date"], r["idx_last_date"]))
    print("-" * 74)
    print("当前周 %sW%02d (%s): 融资%+.2f%% 指数%+.2f%% 连续净降%d周"
          % (weeks[-1][0], weeks[-1][1], last["date"],
             last["fin_pct"], last["idx_pct"], streak))
    print("当前 VETO = %s\n" % ("触发" if r["veto_now"] else "未触发"))

    for name, label in (("margin", "融资"), ("hs300", "指数")):
        s = r["stale"][name]
        if s["stale"]:
            print("[报警] %s 数据滞后 %d 天（末日 %s）→ 本周判定标注为'数据缺失, "
                  "维持上一周状态', 待数据恢复后重跑" % (label, s["lag_days"], s["last_date"]))
        else:
            print("[新鲜度] %s 末日 %s（滞后 %d 天, 正常）"
                  % (label, s["last_date"], s["lag_days"]))
    if r["bad_weeks"]:
        print("[缺失周] %s → 连续计数已被打断, 判定语义=维持上一周状态"
              % ", ".join(r["bad_weeks"]))
    print()

    print("=== 严格VETO历史触发（连续%d周融资降 且 当周指数跌）近期→远期 ===" % N)
    print("周末日      | 当周HS300% | 后4周HS300% | 刹车是否踩对")
    for ev in reversed(r["veto_events"]):
        i = r["records"].index(ev)
        fwd = ((idx_w[weeks[i + 4]][1] / idx_w[weeks[i]][1] - 1) * 100
               if i + 4 < len(weeks) else None)
        verdict = ("未定" if fwd is None else ("对(后续跌)" if fwd < 0 else "错(后续涨)"))
        fwd_s = "%+.2f%%" % fwd if fwd is not None else "  n/a "
        print("%s | %+9.2f%% | %11s | %s" % (ev["date"], ev["idx_pct"], fwd_s, verdict))

    print("\n=== 跨节周（date-7days 方案会取空，需特殊处理）===")
    for rec in r["records"]:
        if rec["cross"]:
            print("  %s 间隔%d天 (融资%+.2f%% 指数%+.2f%%)"
                  % (rec["date"], rec["gap"], rec["fin_pct"], rec["idx_pct"]))

    closes = r["closes"]
    print("\n=== MA背景(兜底保护) ===")
    for n in (20, 60, 120):
        m = r["ma"][n]
        print("  MA%d = %s | 当前收盘 %.2f | %s"
              % (n, ("%.2f" % m) if m else "None(历史不足)", closes[-1],
                 ("下方" if m and closes[-1] < m else "上方")))


if __name__ == "__main__":
    main()
