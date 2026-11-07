#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""daily_run.py 根因回归单测（零第三方依赖）。

覆盖 2026-11-07 修复的四类回归：
  1) 股指期货净持仓单位（小数 → 百分点）与「相对基线」regime 判定；
  2) 三路信号评分量纲归一化；
  3) 交易成本（最低佣金/印花税/过户费）与交易日口径持有期；
  4) 建仓合规过滤与当日收盘数据闸。

运行：  python -m unittest auto_trader/test_daily_run.py -v
     或 python auto_trader/test_daily_run.py
"""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_run as D  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = datetime.date(2026, 11, 6)


def d(y, m, day):
    return datetime.date(y, m, day)


def cffex_of(vals):
    """vals 为百分点；还原成源 JSON 的小数口径 net_ratio。"""
    return {
        "2026-09-%02d" % (10 + i): {
            "IF": {"net_ratio": v / 100.0},
            "IC": {"net_ratio": v / 100.0},
            "IH": {"net_ratio": v / 100.0},
        }
        for i, v in enumerate(vals)
    }


class TestUnitsAndRegime(unittest.TestCase):
    def test_ratio_converted_to_percentage_points(self):
        means = D.cffex_daily_means({"2026-09-10": {"IF": {"net_ratio": -0.0519}}})
        self.assertAlmostEqual(means[0][1], -5.19, places=6)
        self.assertEqual(D.regime_deviation([]), 0.0)

    def test_regime_uses_relative_baseline(self):
        base = [-7.0] * 9
        self.assertEqual(D.compute_regime(cffex_of(base + [-5.8]), TODAY)[0], "RISK_ON")
        self.assertEqual(D.compute_regime(cffex_of(base + [-7.0]), TODAY)[0], "NEUTRAL")
        self.assertEqual(D.compute_regime(cffex_of(base + [-8.2]), TODAY)[0], "RISK_OFF")

    def test_structural_net_short_is_not_risk_off(self):
        # 机构套保使净持仓常年 -7%，绝对零点判定会「天天停买」
        self.assertEqual(D.compute_regime(cffex_of([-7.0] * 10), TODAY)[0], "NEUTRAL")
        self.assertIn("百分点", D.cffex_daily_means.__doc__)

    def test_scenarios_cover_all_regimes(self):
        regimes = [s["regime"] for s in D.scenario_reference()]
        self.assertIn("RISK_ON", regimes)
        self.assertIn("NEUTRAL", regimes)
        self.assertIn("RISK_OFF", regimes)
        self.assertEqual(len(regimes), 4)

    def test_mood_trend_and_vol_are_triggerable(self):
        up = D.build_market_context(cffex_of([-8.0 + 0.3 * i for i in range(10)]), d(2026, 9, 19))
        self.assertEqual(up["mood_trend"], "UP")
        swing = D.build_market_context(
            cffex_of([-7.0 + (1.5 if i % 2 else -1.5) for i in range(10)]), d(2026, 9, 19))
        self.assertGreater(swing["mood_vol"], D.MOOD_VOL_HIGH)
        self.assertTrue(any("波动偏高" in t for t in swing["rationale"]))

    def test_real_data_regime_is_not_constant(self):
        path = os.path.join(ROOT, "cffex_net_position.json")
        if not os.path.exists(path):
            self.skipTest("no cffex_net_position.json")
        with open(path, encoding="utf-8") as f:
            cffex = json.load(f)
        means = D.cffex_daily_means(cffex)
        seen = set()
        for i in range(4, len(means) + 1):
            sub = {dt.strftime("%Y-%m-%d"): cffex[dt.strftime("%Y-%m-%d")]
                   for dt, _ in means[:i]}
            seen.add(D.compute_regime(sub, means[i - 1][0])[0])
        self.assertGreaterEqual(len(seen), 2, "自适应层不能恒为同一档")
        self.assertNotEqual(seen, {"RISK_OFF"}, "不能天天停买")


class TestCostsAndCalendar(unittest.TestCase):
    def test_min_commission_applies(self):
        # 1000 元成交：佣金 0.25 元 → 按最低 5 元收，另加过户费
        self.assertAlmostEqual(D.buy_fee(1000), 5.0 + 1000 * D.TRANSFER_FEE, places=4)
        self.assertAlmostEqual(D.sell_fee(1000),
                               5.0 + 1000 * (D.STAMP_TAX + D.TRANSFER_FEE), places=4)

    def test_large_order_uses_rate(self):
        self.assertAlmostEqual(D.buy_fee(100000),
                               100000 * (D.COMMISSION + D.TRANSFER_FEE), places=4)

    def test_hold_days_count_trading_days(self):
        # 2026-09-14 ~ 09-28 共 14 个自然日，其中周末 4 天 + 中秋休市 9/25 → 9 个交易日
        self.assertEqual(D.trading_days_between(d(2026, 9, 14), d(2026, 9, 28)), 9)
        self.assertEqual(D.trading_days_between(TODAY, TODAY), 0)


class TestFiltersAndCloseGate(unittest.TestCase):
    def setUp(self):
        self.cm = {"600000": (10.0, TODAY)}

    def chk(self, code, name, chg, cm=None):
        return D.tradable({"code": code, "name": name, "chg": chg},
                          self.cm if cm is None else cm, TODAY)

    def test_st_and_delisting(self):
        self.assertFalse(self.chk("600000", "ST浦发", 1.0)[0])
        self.assertFalse(self.chk("600000", "某某退", 1.0)[0])

    def test_new_listing_prefix(self):
        self.assertFalse(self.chk("300001", "N 特新", 0.5)[0])

    def test_limit_up_and_board(self):
        self.assertFalse(self.chk("600000", "浦发银行", 9.9)[0])
        self.assertFalse(self.chk("300001", "创业板股", 19.9)[0])
        self.assertFalse(self.chk("830001", "北交所股", 1.0)[0])
        self.assertTrue(self.chk("300001", "创业板股", 19.0)[0])

    def test_suspended_price(self):
        stale = {"600000": (10.0, d(2026, 10, 20))}
        self.assertFalse(self.chk("600000", "浦发银行", 1.0, stale)[0])

    def test_normal_passes(self):
        self.assertEqual(self.chk("600519", "贵州茅台", 2.0), (True, ""))

    def test_close_gate(self):
        self.assertTrue(D.close_data_fresh(
            {"updated": "2026-11-06", "stocks": {"600000": {}}}, TODAY)[0])
        self.assertFalse(D.close_data_fresh(
            {"updated": "2026-11-05", "stocks": {"600000": {}}}, TODAY)[0])
        self.assertFalse(D.close_data_fresh({}, TODAY)[0])


class TestCandidateNormalization(unittest.TestCase):
    def test_three_sources_interleave(self):
        sig = {
            "strong_stocks.json": {"updated": "2026-11-06", "stocks": [
                {"code": "60000%d" % i, "name": "A%d" % i, "price": 10 + i,
                 "change_pct": 1.0, "score": 100 + i} for i in range(1, 10)]},
            "strongbuy_data.json": {"updated": "2026-11-06", "stocks": [
                {"code": "00000%d" % i, "name": "B%d" % i, "price": 10 + i,
                 "change_pct": 1.0, "net_inflow_pct": 2.0 + i} for i in range(1, 9)]},
            "daily_picks.json": {"2026-11-06": {"picks": [
                {"code": "30000%d" % i, "name": "C%d" % i, "price": 10 + i,
                 "change_pct": 1.0, "final_score": 50 + i} for i in range(1, 9)]}},
            "cffex_net_position.json": {},
        }
        cands, fresh = D.build_candidates(sig, TODAY)
        self.assertTrue(all(0 <= c["score"] <= 100 for c in cands))
        top = [c["strategy"] for c in cands[:6]]
        self.assertEqual(set(top), {"strong", "strongbuy", "daily_picks"},
                         "旧版 strong 分量纲霸榜，归一化后三路应交织")
        self.assertEqual(len(cands), len({c["code"] for c in cands}), "同票必须去重")
        self.assertTrue(fresh["daily_picks"]["used"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
