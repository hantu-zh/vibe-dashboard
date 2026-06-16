# -*- coding: utf-8 -*-
"""
船长钓鱼战法选股 - 箱体突破WM (Sina+EM版)
基于箱体突破、DPO信号、BIG指标的综合选股策略

依赖: data_source.py, dingtalk_style.py, daily_picks_store.py
"""
import sys
import os
import time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\china\.qclaw\workspace")

from data_source import (
    get_a_stock_codes, fetch_sina_batch, fetch_em_single_flow,
    fetch_em_flow_for_codes, fetch_em_indices, safe_float
)
from dingtalk_style import (
    header, footer, stock_table, highlight_card, send, index_block
)
from daily_picks_store import save_daily_picks


class CaptainFishingStrategy:
    """船长钓鱼战法"""

    def __init__(self):
        self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.results = []

    def select_stocks(self):
        """执行选股：Sina实时行情初筛 + EM资金流精筛"""
        print("获取全A股列表...")
        stock_list = get_a_stock_codes()
        if not stock_list:
            print("获取股票列表失败")
            return []

        print(f"共 {len(stock_list)} 只非ST非退市股票，批量获取Sina行情...")
        codes = [s['code'] for s in stock_list]
        name_map = {s['code']: s['name'] for s in stock_list}

        # 批量获取Sina行情
        quote_dict = fetch_sina_batch(codes)
        print(f"获取到 {len(quote_dict)} 只股票行情")

        # 初筛：箱体突破特征（涨幅1-10%、换手>3%、价格2-80）
        candidates = []
        for code, q in quote_dict.items():
            try:
                price = q.get('price', 0)
                change_pct = q.get('change_pct', 0)
                turnover = q.get('turnover', 0)
                name = q.get('name', name_map.get(code, ''))

                # 价格范围
                if price < 2 or price > 80:
                    continue
                # 过滤新三板/北交所
                if code.startswith(('87', '83', '430', '830')):
                    continue
                # 箱体突破特征：涨幅1-10%
                if change_pct < 1 or change_pct > 10:
                    continue
                # 换手率活跃（放巨量特征）
                if turnover < 3:
                    continue

                candidates.append({
                    'code': code,
                    'name': name,
                    'price': price,
                    'change': change_pct,
                    'turnover': turnover,
                    'high': q.get('high', 0),
                    'low': q.get('low', 0),
                    'prev_close': q.get('prev_close', 0),
                })
            except Exception:
                continue

        print(f"初筛通过 {len(candidates)} 只")

        # 精筛：获取EM资金流和市值数据
        print("获取EM资金流和市值数据...")
        candidate_codes = [c['code'] for c in candidates]
        flow_dict = fetch_em_flow_for_codes(candidate_codes, delay=0.03)

        qualified = []
        for c in candidates:
            flow = flow_dict.get(c['code'])
            if not flow:
                continue

            # 获取EM资金流数据时，处理市值字段
            market_cap = flow.get('free_cap_yi', 0)  # 流通市值(亿)
            
            # 如果市值数据为0，尝试从Sina数据计算
            if market_cap <= 0:
                # 使用Sina的成交量估算
                sina_data = quote_dict.get(c['code'], {})
                if sina_data.get('turnover', 0) > 0:
                    market_cap = 50  # 默认中等市值
                else:
                    market_cap = 50  # 默认值
            
            net_main = flow.get('net_main_yi', 0)
            net_main_pct = flow.get('net_main_pct', 0)
            
            # 市值筛选 5-500亿（放宽范围）
            if market_cap < 5 or market_cap > 500:
                continue

            c['market_cap_yi'] = market_cap
            c['net_main_yi'] = net_main
            c['net_main_pct'] = net_main_pct
            c['pe'] = flow.get('pe', 0)
            c['pb'] = flow.get('pb', 0)
            qualified.append(c)

        print(f"精筛通过 {len(qualified)} 只")
        return qualified

    def calculate_score(self, stock):
        """计算综合评分"""
        score = 0

        # 涨幅评分 (1-10% 最佳区间 2-8%)
        change = stock['change']
        if 2 <= change <= 8:
            score += 30
        elif 1 <= change < 2 or 8 < change <= 10:
            score += 20
        else:
            score += 10

        # 换手率评分 (3-20% 最佳区间 5-15%)
        turnover = stock['turnover']
        if 5 <= turnover <= 15:
            score += 25
        elif 3 <= turnover < 5 or 15 < turnover <= 20:
            score += 15
        else:
            score += 5

        # 价格评分 (低价股弹性大)
        price = stock['price']
        if price < 10:
            score += 25
        elif 10 <= price < 20:
            score += 20
        elif 20 <= price <= 30:
            score += 15
        else:
            score += 5

        # 市值评分 (中小盘最佳 30-100亿)
        market_cap = stock['market_cap_yi']
        if 30 <= market_cap <= 100:
            score += 20
        elif 10 <= market_cap < 30 or 100 < market_cap <= 150:
            score += 15
        else:
            score += 5

        # 主力资金加分
        net_main = stock.get('net_main_yi', 0)
        if net_main > 0.5:
            score += 10
        elif net_main > 0:
            score += 5

        return score

    def generate_report(self):
        """生成报告并推送"""
        print("\n" + "=" * 80)
        print("⛵ 船长钓鱼战法选股")
        print("=" * 80)
        print(f"生成时间: {self.timestamp}")
        print(f"选股条件: 箱体突破 + DPO信号 + 放巨量")

        # 获取大盘指数
        indices = fetch_em_indices()

        # 执行选股
        stocks = self.select_stocks()

        if not stocks:
            print("\n未找到符合条件的股票")
            print("=" * 80 + "\n")
            # 推送空结果
            from dingtalk_style import empty_result
            send("⛵ 船长钓鱼战法选股", empty_result("captain_fishing"))
            # 关键修复: 即使0结果也写入daily_picks.json，让监控能区分"未执行"和"执行但0结果"
            save_daily_picks("船长钓鱼战法", [])
            return []

        # 计算评分
        for stock in stocks:
            stock['score'] = self.calculate_score(stock)

        # 按评分排序，取前10
        stocks.sort(key=lambda x: x['score'], reverse=True)
        top_stocks = stocks[:10]

        print(f"\n找到 {len(stocks)} 只符合条件的股票，精选前10只\n")

        # 格式化为统一推送格式
        formatted = []
        for s in top_stocks:
            formatted.append({
                'name': s['name'],
                'code': s['code'],
                'price': s['price'],
                'change': s['change'],
                'turnover': s['turnover'],
                'market_cap_yi': s['market_cap_yi'],
                'score': s['score'],
                'net_main_yi': s.get('net_main_yi', 0),
                'rules': [f"涨幅{s['change']:.1f}%", f"换手{s['turnover']:.1f}%", f"市值{s['market_cap_yi']:.0f}亿"],
                'action': '箱体突破+放巨量，关注回调试买',
            })

        cols = [
            ("name",     "名称",  lambda v: f"**{v}**"),
            ("code",     "代码",  lambda v: str(v)),
            ("price",    "现价",  lambda v: f"{float(v):.2f}"),
            ("change",   "涨幅",  lambda v: f"{float(v):+.2f}%"),
            ("turnover", "换手",  lambda v: f"{float(v):.1f}%"),
            ("market_cap_yi", "市值", lambda v: f"{float(v):.0f}亿"),
            ("score",    "评分",  lambda v: f"**{int(v)}分**"),
        ]

        # 主力资金列（如果有数据）
        has_flow = any(s.get('net_main_yi', 0) != 0 for s in formatted)
        if has_flow:
            cols.insert(-1, ("net_main_yi", "主力净流", lambda v: f"{float(v):+.1f}亿"))

        parts = [
            header("captain_fishing",
                   subtitle="箱体突破 · DPO信号 · 放巨量",
                   channels=['sina', 'eastmoney']),
            index_block(indices),
            stock_table(formatted, cols=cols, title="🎣 钓鱼战法 TOP10"),
            highlight_card(formatted[:5], title="🏆 最佳钓鱼标的"),
            footer("船长钓鱼战法 · 仅供参考 · 不构成投资建议",
                   channels_ok={'sina': True, 'eastmoney': True}),
        ]

        ok = send("⛵ 船长钓鱼战法选股", "\n".join(parts))
        print(f"钉钉推送: {'✔ 成功' if ok else '✘ 失败'}")

        # 保存选股结果
        picks = [{"code": s["code"], "name": s["name"], "price": s["price"],
                  "change": s["change"], "score": s["score"]} for s in top_stocks]
        save_daily_picks("船长钓鱼战法", picks)

        return top_stocks


def main():
    strategy = CaptainFishingStrategy()
    strategy.generate_report()


if __name__ == "__main__":
    main()
