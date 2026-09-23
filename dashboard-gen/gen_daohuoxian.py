#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 similar.json（盗火线打分结果）转成 vibe-dashboard 用的 daohuoxian_data.json。
每日定时任务在 screen_similar.py 之后运行本脚本，dashboard 页面即可 fetch 到最新数据。

注意：本脚本仅用标准库，可直接在 GitHub Actions 运行（原依赖本机 dashboard-gen 目录，
现已改为 __file__ 相对路径，与 screen_similar.py 同目录即可）。
"""
import json, os, re, csv
from datetime import datetime

WS = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(WS, "similar.json")
SAMPLES = os.path.join(WS, "samples.csv")
OUT = os.path.join(WS, "..", "daohuoxian_data.json")


def sample_codes():
    codes = set()
    if os.path.exists(SAMPLES):
        with open(SAMPLES, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("code"):
                    codes.add(row["code"])
    return codes


def main():
    if not os.path.exists(SIM):
        print("缺少 similar.json，请先运行 screen_similar.py")
        return
    sim = json.load(open(SIM, encoding="utf-8"))
    old = sample_codes()
    out = []
    for r in sim:
        out.append({
            "code": re.sub(r'^(sh|sz|bj)', '', r["code"]),
            "name": r["name"],
            "score": r.get("score"),
            "mcap_yi": r.get("mcap_yi"),
            "price": r.get("price"),
            "pre_ret20": r.get("pre_ret20"),
            "pre_amp": r.get("pre_amp"),
            "vol_ratio": r.get("vol_ratio_l5_f10"),
            "max_dd": r.get("max_dd"),
            "dist_from_high": r.get("dist_from_high"),
            "last3_ret": r.get("last3_ret"),
            "exch_avg": r.get("exch_avg"),
            "detail": r.get("detail", {}),
            "kline": r.get("kline", []),
            "old": r["code"] in old,
        })
    json.dump({"updated": datetime.now().strftime("%Y-%m-%d"),
               "daohuoxian": out},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"daohuoxian_data.json 已更新：{len(out)} 条（曾连板 {sum(1 for x in out if x['old'])}）")


if __name__ == "__main__":
    main()
