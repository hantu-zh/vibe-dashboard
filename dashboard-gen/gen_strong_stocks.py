#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由今日 similar.json（连板潜力股画像 / screen_similar.py 产出，25 条）派生
strong_stocks.json —— trader 引擎 (auto_trader/daily_run.py) 的首要信号源。

引擎期望结构（见 daily_run.build_candidates）：
    { "updated": "...", "stocks": [{code,name,price,change_pct,score}, ...], "yimeng": [...] }
引擎对 strong 源评分 = (score or 0)*10 + (change_pct or 0)，strategy="strong"。

注意：
- 引擎 is_valid_code 要求 6 位纯数字 code；similar.json 的 code 带 sz/sh/bj 前缀，这里去前缀。
- similar.json 每条自带近30日 kline（{d,o,h,l,c,v}），取最后两日 close 算当日 change_pct，
  无需重新抓行情，保证与 screen_similar 同源、当日新鲜。
"""
import json, os
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
WS = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(WS, "similar.json")
OUT = os.path.join(WS, "..", "strong_stocks.json")


def to_code(raw):
    c = str(raw or "").strip().lower()
    if c[:2] in ("sz", "sh", "bj"):
        c = c[2:]
    return c


def chg_pct_from_kline(kline):
    if not isinstance(kline, list) or len(kline) < 2:
        return 0.0
    try:
        c1 = float(kline[-1].get("c"))
        c0 = float(kline[-2].get("c"))
        if c0:
            return round((c1 - c0) / c0 * 100, 2)
    except Exception:
        return 0.0
    return 0.0


def main():
    sim = json.load(open(SIM, encoding="utf-8"))
    stocks, skipped = [], 0
    for r in sim:
        code = to_code(r.get("code"))
        if len(code) != 6 or not code.isdigit():
            skipped += 1
            continue
        price = r.get("price")
        try:
            price = float(price)
        except Exception:
            price = 0.0
        if price <= 0:
            skipped += 1
            continue
        stocks.append({
            "code": code,
            "name": str(r.get("name") or code).strip(),
            "price": round(price, 3),
            "change_pct": chg_pct_from_kline(r.get("kline")),
            "score": round(float(r.get("score") or 0), 1),
        })
    out = {
        "updated": datetime.now(CST).strftime("%Y-%m-%d %H:%M"),
        "source": "similar.json (连板潜力股画像, screen_similar.py)",
        "stocks": stocks,
        "yimeng": [],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("strong_stocks.json 已生成｜候选 %d 条（跳过 %d）" % (len(stocks), skipped))


if __name__ == "__main__":
    main()
