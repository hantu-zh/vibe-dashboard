#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 output/candidates_<date>.csv + summary_<date>.json 转成 vibe-dashboard 用的 quarterly_screen.json

2026-11-01 修复「静默错标」：
  此前 report_period="20250930"、lag_days=273、funnel={3182,1919,980,...} 全是写死的历史值，
  滚动报告期后即使 pipeline 产出新数据，前端仍会显示旧报告期与旧漏斗 → 页面说谎。
  现在 metadata 一律取自 pipeline 本次运行写下的 summary_<stamp>.json；
  找不到对应 summary 时直接报错退出，宁可不导出，也不拿旧 CSV 冒充新报告期。
"""
import os, csv, json, ast, re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output")
CONFIG = os.path.join(HERE, "config.yaml")

DEFAULT_PE_FIELD = "pe_annualized_3q"


def latest_pair():
    """返回最近一次「CSV + 同名 summary」的 (stamp, csv_path, summary_path)；缺 summary 视为运行未完成。"""
    files = sorted(f for f in os.listdir(OUT) if re.match(r"candidates_\d{8}\.csv$", f))
    if not files:
        raise SystemExit("no candidates_*.csv found")
    for f in reversed(files):
        stamp = re.search(r"(\d{8})", f).group(1)
        sp = os.path.join(OUT, f"summary_{stamp}.json")
        if os.path.exists(sp):
            return stamp, os.path.join(OUT, f), sp
        print(f"[跳过] candidates_{stamp}.csv 没有配套 summary_{stamp}.json（旧版本产物或运行中断）")
    raise SystemExit("找不到任何带 summary 的候选 CSV —— 拒绝导出，避免用旧数据冒充当前报告期")


def read_report_period():
    """从 config.yaml 读 report_period（正则解析，不强依赖 pyyaml）。"""
    try:
        txt = open(CONFIG, encoding="utf-8").read()
        m = re.search(r'^report_period:\s*["\']?(\d{8})["\']?', txt, re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def num(s):
    try:
        return float(s)
    except Exception:
        return None


def main():
    stamp, csv_path, summary_path = latest_pair()
    summary = json.load(open(summary_path, encoding="utf-8"))
    pe_field = summary.get("pe_field") or DEFAULT_PE_FIELD
    period = summary.get("report_period") or read_report_period()
    if not period:
        raise SystemExit("无法确定 report_period（summary 与 config.yaml 都没有）")

    cfg_period = read_report_period()
    if cfg_period and str(period) != str(cfg_period):
        raise SystemExit(f"report_period 不一致：summary={period} config={cfg_period}"
                         f"（配置已滚到新报告期但产物是旧的，请重跑 pipeline.py）")

    label = summary.get("pe_period_label") or "报告期"
    factor = summary.get("pe_annualize_factor")
    lag_days = summary.get("lag_days")
    funnel = summary.get("funnel") or {}

    run_date = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"

    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            inst = []
            raw = (r.get("inst_hits") or "").strip()
            if raw:
                try:
                    inst = ast.literal_eval(raw)
                    if not isinstance(inst, list):
                        inst = [str(inst)]
                except Exception:
                    inst = [x for x in re.split(r"[,'\"\[\]]+", raw) if x.strip()]
            rows.append({
                "code": r["股票代码"].strip(),
                "name": r["股票简称"].strip(),
                "ind": (r.get("所处行业") or "").strip(),
                "yoy": num(r.get("净利润-同比增长")),
                "pe": num(r.get(pe_field)),
                "ind_pe_med": num(r.get("ind_pe_med")),
                "dd": num(r.get("dd")),
                "inst": inst,
                "holder_chg": num(r.get("holder_chg")),
                "avg_mv": num(r.get("holder_avg_mv")),
                "avg_qty": num(r.get("holder_avg_qty")),
                "unblock": (r.get("unblock_60d_flag") or "").strip(),
            })
    rows.sort(key=lambda x: (x["dd"] if x["dd"] is not None else -1), reverse=True)

    factor_txt = f"×{factor:g}" if isinstance(factor, (int, float)) else "×?"
    data = {
        "run_date": run_date,
        "generated_at": summary.get("generated_at"),
        "report_period": str(period),
        "lag_days": lag_days,
        "funnel": {k: funnel.get(k) for k in
                   ("perf_gt0", "after_bse", "pe_pass", "dd_pass",
                    "inst_pass", "consec_pass", "final")},
        "declarations": {
            "pe": f"{pe_field} = {label}每股收益 {factor_txt} 年化近似，非机构 forward PE；季节性行业需人工复核",
            "lag": f"回撤基于 run-date 收盘价，距季报截止日已 {lag_days} 个交易日，执行间隔内价格变动未反映",
            "holder": "户均市值按池内第20百分位 + 2万硬下限过滤",
            "reduce": "reduce_plan 列为人工核查项（无结构化源）",
        },
        "candidates": rows,
    }
    dst = os.path.join(OUT, "quarterly_screen.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print("WROTE", dst, "report_period:", period, "run_date:", run_date,
          "lag_days:", lag_days, "funnel:", funnel, "candidates:", len(rows))


if __name__ == "__main__":
    main()
