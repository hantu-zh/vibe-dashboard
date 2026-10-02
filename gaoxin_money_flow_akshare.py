# -*- coding: utf-8 -*-
"""
资金净流入选股策略 (Akshare版 v2 - 使用 stock_individual_fund_flow)
- 使用 akshare.stock_individual_fund_flow 获取个股资金流历史数据
- 基于最近N日主力净流入综合打分排序，选出最优top10
- 结果保存到 daily_picks.json（v2 格式，key=高欣资金净流入）
- 推送到钉钉

2026-10-02 CI 移植版（源: D盘备份 2026-07-22 版）:
- 去掉 Windows 硬编码路径（日志文件 / C:\\... 双写），改走 paths.py + daily_picks_store
- 钉钉 webhook 改读环境变量（原硬编码 token 已泄露作废）
- 存储 key 由「资金净流入」改回「高欣资金净流入」——前端卡片按此名精确匹配，
  07-20 改名导致卡片 07-13 起匹配不到数据的冤案一并修正
"""
import sys
import os
import json
import time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)


def _open_log():
    """日志文件为可选项：任何环境下失败都不影响主流程"""
    try:
        return open(os.path.join(_SCRIPT_DIR, 'gaoxin_mf_log.txt'), 'w', encoding='utf-8')
    except Exception:
        return None


f_log = _open_log()


def log(msg):
    print(msg, flush=True)
    if f_log:
        try:
            f_log.write(msg + '\n')
            f_log.flush()
        except Exception:
            pass


# 尝试导入 akshare（CI 的 01 工作流已预装 akshare==1.18.94，此处为本地兜底）
try:
    import akshare as ak
except ImportError:
    log("[WARN] akshare 未安装，尝试自动安装...")
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'akshare', '-q'], check=True)
    import akshare as ak

from daily_picks_store import save_daily_picks


def _load_dingtalk_webhook():
    """钉钉 webhook：优先环境变量，其次 .env.dingtalk（不再硬编码）"""
    url = (os.environ.get('DINGTALK_WEBHOOK') or '').strip()
    if url:
        return url
    env_file = os.path.join(_SCRIPT_DIR, '.env.dingtalk')
    try:
        if os.path.exists(env_file):
            for line in open(env_file, encoding='utf-8'):
                if line.strip().startswith('DINGTALK_WEBHOOK='):
                    return line.strip().split('=', 1)[1].strip()
    except Exception:
        pass
    return ''


DINGTALK_URL = _load_dingtalk_webhook()

# 候选股票池（沪深A股，按成交额/换手率预筛选的活跃股）
CANDIDATE_STOCKS = [
    # 沪深300成分股中成交活跃的代表性股票
    ('000001', '平安银行'), ('000002', '万科A'), ('000063', '中兴通讯'), ('000066', '中国长城'),
    ('000100', 'TCL科技'), ('000333', '美的集团'), ('000425', '徐工机械'), ('000538', '云南白药'),
    ('000651', '格力电器'), ('000661', '长春高新'), ('000708', '中信特钢'), ('000725', '京东方A'),
    ('000768', '中航西飞'), ('000858', '五粮液'), ('000876', '新希望'), ('000895', '双汇发展'),
    ('000938', '紫光股份'), ('000961', '中南建设'), ('000963', '华东医药'), ('002001', '新和成'),
    ('002027', '分众传媒'), ('002049', '紫光国微'), ('002050', '三花智控'), ('002142', '宁波银行'),
    ('002230', '科大讯飞'), ('002236', '大华股份'), ('002241', '歌尔股份'), ('002252', '上海莱士'),
    ('002304', '洋河股份'), ('002311', '海大集团'), ('002352', '顺丰控股'), ('002371', '北方华创'),
    ('002415', '海康威视'), ('002460', '赣锋锂业'), ('002475', '立讯精密'), ('002493', '荣盛石化'),
    ('002594', '比亚迪'), ('002601', '龙佰集团'), ('002607', '亚玛顿'), ('002624', '完美世界'),
    ('002714', '牧原股份'), ('002736', '国信证券'), ('002812', '恩捷股份'), ('002841', '视源股份'),
    ('002916', '中晶科技'), ('300001', '特锐德'), ('300003', '乐普医疗'), ('300015', '爱尔眼科'),
    ('300033', '同花顺'), ('300059', '东方财富'), ('300122', '智飞生物'), ('300124', '汇川技术'),
    ('300142', '沃森生物'), ('300223', '北京君正'), ('300274', '阳光电源'), ('300285', '国瓷材料'),
    ('300308', '中际旭创'), ('300316', '晶盛机电'), ('300347', '泰格医药'), ('300408', '三环集团'),
    ('300450', '先导智能'), ('300496', '中科创达'), ('300529', '健帆生物'), ('300750', '宁德时代'),
    ('300896', '爱美客'), ('600009', '上海机场'), ('600016', '民生银行'), ('600019', '宝钢股份'),
    ('600028', '中国石化'), ('600030', '中信证券'), ('600036', '招商银行'), ('600048', '保利发展'),
    ('600050', '中国联通'), ('600104', '上汽集团'), ('600111', '北方稀土'), ('600150', '中国船舶'),
    ('600183', '生益科技'), ('600276', '恒瑞医药'), ('600309', '万华化学'), ('600406', '国电南瑞'),
    ('600436', '片仔癀'), ('600519', '贵州茅台'), ('600585', '海螺水泥'), ('600588', '用友网络'),
    ('600690', '海尔智家'), ('600703', '三安光电'), ('600745', '闻泰科技'), ('600809', '山西汾酒'),
    ('600887', '伊利股份'), ('600900', '长江电力'), ('600905', '三峡能源'), ('600918', '中泰证券'),
    ('600926', '杭州银行'), ('600941', '中国移动'), ('601006', '大秦铁路'), ('601012', '隆基绿能'),
    ('601021', '春秋航空'), ('601066', '中信建投'), ('601088', '中国神华'), ('601118', '海南橡胶'),
    ('601138', '工业富联'), ('601166', '兴业银行'), ('601169', '北京银行'), ('601186', '中国铁建'),
    ('601225', '陕西煤业'), ('601236', '红塔证券'), ('601288', '农业银行'), ('601318', '中国平安'),
    ('601328', '交通银行'), ('601336', '新华保险'), ('601390', '中国中铁'), ('601398', '工商银行'),
    ('601601', '中国太保'), ('601628', '中国人寿'), ('601658', '邮储银行'), ('601668', '中国建筑'),
    ('601688', '华泰证券'), ('601698', '中国卫通'), ('601728', '中国电信'), ('601766', '中国中车'),
    ('601800', '中国交建'), ('601818', '光大银行'), ('601857', '中国石油'), ('601888', '中国中免'),
    ('601899', '紫金矿业'), ('601919', '中远海控'), ('601939', '建设银行'), ('601985', '中国核电'),
    ('601988', '中国银行'), ('601989', '中国重工'), ('601995', '中金公司'), ('603259', '药明康德'),
    ('603288', '海天味业'), ('603501', '韦尔股份'), ('603799', '华友钴业'), ('603986', '兆易创新'),
    ('688041', '芯海科技'), ('688111', '金山办公'), ('688126', '沪硅产业'), ('688981', '中芯国际'),
]

ALL_STOCKS = CANDIDATE_STOCKS  # 统一使用一套股票池


def get_fund_flow_for_stock(code):
    """获取单只股票的资金流数据（最近20个交易日）"""
    try:
        df = ak.stock_individual_fund_flow(stock=code)
        if df is None or len(df) == 0:
            return None
        # 取最近20个交易日
        df = df.head(20)
        return df
    except Exception:
        return None


def score_stock_from_fund_flow(df, code, name):
    """基于资金流数据计算综合得分"""
    if df is None or len(df) == 0:
        return None

    try:
        # 取最新5日数据计算趋势
        recent = df.head(5)

        # 主力净流入-净额 (元)
        net_amount_col = '主力净流入-净额'
        net_ratio_col = '主力净流入-净占比'

        if net_amount_col not in df.columns or net_ratio_col not in df.columns:
            return None

        # 最近5日主力净流入之和（万元）
        total_net = 0
        total_ratio = 0
        positive_days = 0

        for _, row in recent.iterrows():
            try:
                net = float(row[net_amount_col] or 0)
                ratio = float(row[net_ratio_col] or 0)
                total_net += net
                total_ratio += ratio
                if net > 0:
                    positive_days += 1
            except Exception:
                continue

        total_net_wan = total_net / 10000  # 转为万元

        # 最近1日数据
        latest = df.iloc[0]
        latest_change = float(latest.get('涨跌幅', 0) or 0)
        latest_price = float(latest.get('收盘价', 0) or 0)

        # 得分计算
        # 1. 5日累计净流入得分（0-100）
        amount_score = min(100, max(0, total_net_wan / 50000 * 100)) if total_net_wan > 0 else max(0, 100 + total_net_wan / 5000 * 100)
        # 2. 净流入天数得分（0-100）
        day_score = positive_days / 5 * 100
        # 3. 5日平均净流入占比（0-100）
        avg_ratio = total_ratio / 5 if len(recent) > 0 else 0
        ratio_score = min(100, max(0, avg_ratio * 3)) if avg_ratio > 0 else 0
        # 4. 今日涨跌幅（0-100，涨跌都有价值，正收益加分）
        change_score = min(100, max(0, latest_change * 5)) if latest_change >= 0 else min(100, max(0, -latest_change * 2))

        # 综合得分
        total_score = amount_score * 0.4 + day_score * 0.25 + ratio_score * 0.2 + change_score * 0.15

        return {
            'code': code,
            'name': name,
            'net_amount_5d': round(total_net_wan, 0),
            'net_ratio_avg': round(avg_ratio, 2),
            'positive_days': positive_days,
            'change_pct': round(latest_change, 2),
            'price': round(latest_price, 2),
            'score': round(total_score, 2),
        }
    except Exception:
        return None


def get_money_flow_top_stocks(top_n=10, max_stocks=100):
    """批量获取资金流数据并打分排序"""
    log(f"[1/4] 获取资金流数据 (最多 {max_stocks} 只候选股)...")

    results = []
    stocks_to_check = ALL_STOCKS[:max_stocks]

    for i, (code, name) in enumerate(stocks_to_check):
        # 进度
        if (i + 1) % 10 == 0:
            log(f"      进度: {i+1}/{len(stocks_to_check)}...")

        try:
            df = get_fund_flow_for_stock(code)
            if df is None:
                continue

            scored = score_stock_from_fund_flow(df, code, name)
            if scored and scored['score'] > 0:
                results.append(scored)
        except Exception:
            continue

        # 避免请求过快
        time.sleep(0.3)

    log(f"      成功获取 {len(results)} 只股票资金流数据")

    # 按得分降序排序
    results.sort(key=lambda x: x['score'], reverse=True)

    return results[:top_n]


def save_results(stocks):
    """保存结果到 daily_picks.json（v2 格式，前端卡片按「高欣资金净流入」精确匹配）"""
    log("[2/4] 保存选股结果...")
    now = datetime.now().strftime("%H:%M")
    save_daily_picks('高欣资金净流入', stocks, task_time=now)
    log(f"      已保存 {len(stocks)} 只 → key=高欣资金净流入 time={now}")


def push_to_dingtalk(stocks):
    """推送到钉钉"""
    log("[3/4] 推送钉钉...")

    if not stocks:
        log("      [WARN] 无股票推送")
        return False

    if not DINGTALK_URL:
        log("      [WARN] 未配置 DINGTALK_WEBHOOK，跳过推送")
        return False

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")

    title = f"💰 高欣资金净流入选股 ({today} {now})"

    lines = [
        f"### 💰 高欣资金净流入选股",
        f"**日期**: {today}",
        f"**时间**: {now}",
        f"**策略**: 5日累计主力净流入打分排序 Top{len(stocks)}",
        "",
        "---",
        ""
    ]

    for i, s in enumerate(stocks, 1):
        emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        net_str = f"+{s['net_amount_5d']:.0f}万" if s['net_amount_5d'] > 0 else f"{s['net_amount_5d']:.0f}万"
        change_str = f"+{s['change_pct']:.2f}%" if s['change_pct'] > 0 else f"{s['change_pct']:.2f}%"

        lines.append(f"{emoji} **{s['name']}** (`{s['code']}`)")
        lines.append(f"   - 5日净流入: **{net_str}** | 均净占比: {s['net_ratio_avg']:.1f}%")
        lines.append(f"   - 涨跌: {change_str} | 正流入天数: {s['positive_days']}/5")
        lines.append(f"   - 综合得分: **{s['score']}**")
        lines.append("")

    lines.append("---")
    lines.append(f"_📊 共选出 {len(stocks)} 只股票_")

    content = "\n".join(lines)

    # 发送钉钉
    import ssl, urllib.request
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE

    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': content
        }
    }

    headers = {'Content-Type': 'application/json; charset=utf-8'}
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    if len(content) > 18000:
        content = content[:17500] + '\n\n--- [内容过长已截断] ---'
        payload['markdown']['text'] = content
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')

    req = urllib.request.Request(DINGTALK_URL, data=data, headers=headers, method='POST')

    try:
        with urllib.request.urlopen(req, timeout=20, context=_ssl_ctx) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('errcode') == 0:
                log('      OK: 钉钉推送成功')
                return True
            else:
                log(f'      FAIL: 钉钉推送失败: {result}')
                return False
    except Exception as e:
        log(f'      ERROR: 推送异常: {e}')
        return False


def print_detail_report(stocks):
    """打印详细报告"""
    log("\n" + "="*70)
    log("📊 高欣资金净流入选股 - 详细报告")
    log("="*70)

    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(f"执行时间: {today}")
    log(f"选股数量: {len(stocks)}")
    log("-"*70)

    if stocks:
        log(f"{'排名':<4} {'代码':<8} {'名称':<8} {'5日净流入(万)':<14} {'均净占比%':<10} {'正天数':<6} {'涨跌%':<8} {'得分':<6}")
        log("-"*70)
        for i, s in enumerate(stocks, 1):
            log(f"{i:<4} {s['code']:<8} {s['name']:<8} {s['net_amount_5d']:>12.0f} {s['net_ratio_avg']:>8.2f} {s['positive_days']:>5}/5 {s['change_pct']:>6.2f}% {s['score']:>6.1f}")
    else:
        log("无符合条件的股票")

    log("="*70 + "\n")


def main():
    """主函数"""
    log("\n" + "="*70)
    log("🚀 高欣资金净流入选股策略 (Akshare版 v2, CI移植)")
    log("="*70 + "\n")

    start_time = time.time()

    # 1. 获取数据并打分
    stocks = get_money_flow_top_stocks(top_n=10, max_stocks=120)

    # 2. 保存结果
    if stocks:
        save_results(stocks)
    else:
        log("[WARN] 无选股结果，不保存")

    # 3. 推送钉钉
    if stocks:
        push_to_dingtalk(stocks)
    else:
        log("[WARN] 无选股结果，不推送")

    # 4. 打印详细报告
    print_detail_report(stocks)

    elapsed = time.time() - start_time
    log(f"⏱️ 总耗时: {elapsed:.1f} 秒\n")

    if f_log:
        try:
            f_log.close()
        except Exception:
            pass

    return len(stocks) > 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
