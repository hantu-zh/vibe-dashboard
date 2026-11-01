# -*- coding: utf-8 -*-
import paths
import secrets_conf
from secrets_conf import DINGTALK_WEBHOOK, DINGTALK
VIBE_WS = paths.VIBE_WS
"""
船长钓鱼战法选股 - 动量突破 (Sina+EM版)
基于日内动量（收于日高附近）、活跃换手、中小盘偏好、主力资金流的综合选股策略

依赖: data_source.py, dingtalk_style.py, daily_picks_store.py
"""
import sys
import os
import time
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, VIBE_WS)

from data_source import (
    get_a_stock_codes, fetch_sina_batch, fetch_em_single_flow,
    fetch_em_flow_for_codes, fetch_em_indices, safe_float
)
from dingtalk_style import (
    header, footer, stock_table, highlight_card, send, index_block
)
from daily_picks_store import save_daily_picks

# ── 时间预算 ───────────────────────────────────────────────────────────
# 背景：workflow 里单任务硬超时 420s，本脚本曾因「东财 push2 探活通过但逐只请求
# 间歇性掐断」在 runner 上打满 420s，导致当天选股直接丢失。现在脚本自带预算：
#   - 软预算 CAPTAIN_FISHING_BUDGET（默认 300s）：到点停止逐只抓取，改批量兜底；
#   - 硬预算 = 软预算 + 40s：看门狗强制退出，绝不把 420s 打满。
BUDGET = float(os.environ.get('CAPTAIN_FISHING_BUDGET', '300'))


def install_watchdog(hard_deadline):
    """到硬截止时间强制退出（exit 3），保证不会占满 workflow 的 420s。
    仅在支持 SIGALRM 的平台生效（Linux runner 有；Windows 本地自动跳过）。"""
    try:
        import signal as _sig
        if not hasattr(_sig, 'SIGALRM'):
            return False

        def _kill(signum, frame):
            print(f'[watchdog] 触及硬预算 {datetime.now().strftime("%H:%M:%S")}，强制退出：本轮不写 picks，标记失败待下轮补跑',
                  flush=True)
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(3)

        _sig.signal(_sig.SIGALRM, _kill)
        _sig.setitimer(_sig.ITIMER_REAL, max(5.0, hard_deadline - time.time()))
        return True
    except Exception as e:
        print(f'[watchdog] 安装失败（不影响主流程）: {e}')
        return False


class CaptainFishingStrategy:
    """船长钓鱼战法"""

    def __init__(self, deadline=None):
        self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.results = []
        self.deadline = deadline

    def select_stocks(self):
        """执行选股：Sina实时行情初筛 + EM资金流精筛"""
        print("获取全A股列表...")
        # 列表阶段最多占用预算的 1/3，避免新浪被拦时拖死后续
        list_deadline = None
        if self.deadline:
            list_deadline = min(self.deadline - BUDGET * 0.66, self.deadline)
        stock_list = get_a_stock_codes(deadline=list_deadline)
        if not stock_list:
            print("[ABORT] 获取股票列表失败（Sina 与内置宇宙都不可用）：本轮不写 picks，exit 2")
            sys.exit(2)

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
                # 只保留沪深A股（白名单）：60沪主板/68科创板/00深主板/30创业板
                # 一并排除北交所(43/83/87/88/920)、B股(90/20)、新三板(4xx/8xx)
                if not code.startswith(("60", "68", "00", "30")):
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
        flow_dict = fetch_em_flow_for_codes(candidate_codes, delay=0.03, deadline=self.deadline)

        # 覆盖率闸门：资金流覆盖不足说明数据源在抖，此时算出来的 0 结果不是「真没票」
        # 而是「没数据」。绝不能把这种空结果写进 daily_picks（会被当成"执行了但0只"），
        # 直接以退出码 2 失败，留给下一轮补跑。
        if candidates:
            cov = len(flow_dict) / len(candidates)
            print(f"资金流覆盖 {len(flow_dict)}/{len(candidates)} = {cov:.0%}")
            if cov < 0.6:
                print("[ABORT] 资金流覆盖率过低，判为数据源故障：本轮不写 picks，exit 2")
                sys.exit(2)

        qualified = []
        for c in candidates:
            flow = flow_dict.get(c['code'])
            if not flow:
                continue

            # 获取EM资金流数据时，处理市值字段
            market_cap = flow.get('free_cap_yi', 0)  # 流通市值(亿)

            # 市值数据不可得时直接跳过该股——不伪造默认市值参与筛选打分
            if market_cap <= 0:
                continue

            net_main = flow.get('net_main_yi', 0)
            net_main_pct = flow.get('net_main_pct', 0)

            # 市值筛选 5-500亿（放宽范围）
            if market_cap < 5 or market_cap > 500:
                continue

            # 真实日内突破特征：收盘价收在当日最高价97%以上（逼近/创日内新高）
            high = c.get('high', 0)
            c['near_high'] = bool(high > 0 and c['price'] >= high * 0.97)

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
        print(f"选股条件: 动量突破(收于日高附近) + 活跃换手 + 中小盘 + 主力资金流")

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
            save_daily_picks("船长钓鱼战法", [], task_time=datetime.now().strftime('%H:%M'))
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
                'rules': [f"涨幅{s['change']:.1f}%", f"换手{s['turnover']:.1f}%", f"市值{s['market_cap_yi']:.0f}亿"] + (["收于日高附近"] if s.get('near_high') else []),
                'action': '动量+放量，关注回调试买',
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
        save_daily_picks("船长钓鱼战法", picks, task_time=datetime.now().strftime('%H:%M'))

        return top_stocks


def main():
    t0 = time.time()
    deadline = t0 + BUDGET
    install_watchdog(deadline + 40)
    print(f"[budget] 软预算 {BUDGET:.0f}s / 硬看门狗 {BUDGET + 40:.0f}s")
    strategy = CaptainFishingStrategy(deadline=deadline)
    strategy.generate_report()


if __name__ == "__main__":
    main()
