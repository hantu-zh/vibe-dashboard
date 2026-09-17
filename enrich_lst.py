# -*- coding: utf-8 -*-
"""
一次性回填：给 lst_history.json / lst.json 里每个 pick 补上
  - days_since       累计天数（相对运行当日）
  - cur_close        最新价
  - post_change_pct  选出后涨跌幅 (%)  = (最新价 - 选出日收盘) / 选出日收盘
仅补拉最新价（约百余只），不重扫全市场；运行 gen_lst.py 时也会自动包含这些字段。
"""
import json
from datetime import datetime
import gen_lst as G

HIST = "lst_history.json"
LATEST = "lst.json"

def main():
    h = json.load(open(HIST, encoding="utf-8"))
    run_date = datetime.now().strftime("%Y%m%d")
    G.enrich_history(h["data"], run_date)
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, separators=(",", ":"))
    latest = h["dates"][-1]
    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump({"date": latest, "picks": h["data"].get(latest, [])},
                  f, ensure_ascii=False, indent=1)
    total = sum(len(v) for v in h["data"].values())
    ok = sum(1 for v in h["data"].values() for p in v if p.get("post_change_pct") is not None)
    print(f"回填完成: {len(h['dates'])} 个交易日, {total} 只, 成功拿到最新价 {ok} 只; run_date={run_date}")

if __name__ == "__main__":
    main()
