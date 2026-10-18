#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 output/candidates_<date>.csv 转成 vibe-dashboard 用的 quarterly_screen.json"""
import os, csv, json, ast, datetime, re
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")

def latest_csv():
    files = sorted(f for f in os.listdir(OUT) if re.match(r"candidates_\d{8}\.csv$", f))
    if not files:
        raise SystemExit("no candidates_*.csv found")
    return files[-1]

def num(s):
    try:
        v = float(s)
        return v
    except Exception:
        return None

def main():
    fname = latest_csv()
    date_m = re.search(r"(\d{8})", fname)
    run_date = f"{date_m.group(1)[:4]}-{date_m.group(1)[4:6]}-{date_m.group(1)[6:]}"
    rows = []
    with open(os.path.join(OUT, fname), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            inst = []
            raw = (r.get("inst_hits") or "").strip()
            if raw:
                try:
                    inst = ast.literal_eval(raw)
                    if not isinstance(inst, list): inst = [str(inst)]
                except Exception:
                    inst = [x for x in re.split(r"[,'\"\[\]]+", raw) if x.strip()]
            rows.append({
                "code": r["股票代码"].strip(),
                "name": r["股票简称"].strip(),
                "ind": (r.get("所处行业") or "").strip(),
                "yoy": num(r.get("净利润-同比增长")),
                "pe": num(r.get("pe_annualized_3q")),
                "ind_pe_med": num(r.get("ind_pe_med")),
                "dd": num(r.get("dd")),
                "inst": inst,
                "holder_chg": num(r.get("holder_chg")),
                "avg_mv": num(r.get("holder_avg_mv")),
                "avg_qty": num(r.get("holder_avg_qty")),
                "unblock": (r.get("unblock_60d_flag") or "").strip(),
            })
    rows.sort(key=lambda x: (x["dd"] if x["dd"] is not None else -1), reverse=True)
    data = {
        "run_date": run_date,
        "report_period": "20250930",
        "lag_days": 273,
        "funnel": {"perf_gt0": 3182, "after_bse": 1919, "pe_pass": 980,
                   "dd_pass": 782, "inst_pass": 551, "consec_pass": 57, "final": len(rows)},
        "declarations": {
            "pe": "pe_annualized_3q = 三季报EPS×4/3 年化近似，非机构 forward PE；季节性行业需人工复核",
            "lag": "回撤基于 run-date 收盘价，距季报截止日已 273 个交易日，执行间隔内价格变动未反映",
            "holder": "户均市值按池内第20百分位 + 2万硬下限过滤",
            "reduce": "reduce_plan 列为人工核查项（无结构化源）",
        },
        "candidates": rows,
    }
    dst = os.path.join(OUT, "quarterly_screen.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("WROTE", dst, "candidates:", len(rows))

if __name__ == "__main__":
    main()
