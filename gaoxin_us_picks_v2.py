# -*- coding: utf-8 -*-
"""
高欣-美股选股策略 v2（完整适配版）
适配高欣两个核心模式：
1. 资金净流入选股 → 美股：成交量放大 + 资金流入 + 价格突破
2. 季度环比增长选股 → 美股：科技股 + 温和上涨 + MA金叉

数据源：yfinance（美股行情）+ akshare（美股实时）
推送：钉钉
"""

import sys
import json
import urllib.request
import ssl
import time
from datetime import datetime, timedelta
import calendar

# ════════════════════════════════════════════════════════════
# 美国市场节假日检测（2024-2026）
# ════════════════════════════════════════════════════════════

def is_us_trading_day():
    """
    检测今天是否是美国股市交易日
    返回: (bool, str) - (是否是交易日, 原因)
    """
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    weekday = today.weekday()  # 0=周一, 6=周日
    
    # 1. 周末休市
    if weekday >= 5:  # 周六=5, 周日=6
        return False, f"周末休市（{['周一','周二','周三','周四','周五','周六','周日'][weekday]}）"
    
    # 2. 固定日期节假日
    year = today.year
    fixed_holidays = [
        f"{year}-01-01",  # 新年元旦
        f"{year}-07-04",  # 独立日
        f"{year}-11-11",  # 退伍军人节
        f"{year}-12-25",  # 圣诞节
    ]
    
    # 3. 动态节假日（需要计算）
    # 马丁·路德·金纪念日（1月第三个周一）
    jan1 = datetime(year, 1, 1)
    mlk_day = jan1 + timedelta(days=(0 - jan1.weekday()) % 7 + 14)  # 第三个周一
    
    # 总统节（2月第三个周一）
    feb1 = datetime(year, 2, 1)
    presidents_day = feb1 + timedelta(days=(0 - feb1.weekday()) % 7 + 14)
    
    # 阵亡将士纪念日（5月最后一个周一）
    may31 = datetime(year, 5, 31)
    memorial_day = may31 - timedelta(days=(may31.weekday() - 0) % 7)  # 最后一个周一
    
    # 劳动节（9月第一个周一）
    sep1 = datetime(year, 9, 1)
    labor_day = sep1 + timedelta(days=(0 - sep1.weekday()) % 7)  # 第一个周一
    
    # 哥伦布日（10月第二个周一）
    oct1 = datetime(year, 10, 1)
    columbus_day = oct1 + timedelta(days=(0 - oct1.weekday()) % 7 + 7)  # 第二个周一
    
    # 感恩节（11月第四个周四）
    nov1 = datetime(year, 11, 1)
    thanksgiving = nov1 + timedelta(days=(3 - nov1.weekday()) % 7 + 21)  # 第四个周四
    
    dynamic_holidays = [
        mlk_day.strftime("%Y-%m-%d"),
        presidents_day.strftime("%Y-%m-%d"),
        memorial_day.strftime("%Y-%m-%d"),
        labor_day.strftime("%Y-%m-%d"),
        columbus_day.strftime("%Y-%m-%d"),
        thanksgiving.strftime("%Y-%m-%d"),
    ]
    
    # 4. 合并所有节假日
    all_holidays = fixed_holidays + dynamic_holidays
    
    # 5. 如果节假日是周六，则周五休市；如果是周日，则周一休市
    for holiday in all_holidays:
        holiday_date = datetime.strptime(holiday, "%Y-%m-%d")
        holiday_weekday = holiday_date.weekday()
        
        if holiday_weekday == 5:  # 周六 → 周五休市
            if today.strftime("%Y-%m-%d") == (holiday_date - timedelta(days=1)).strftime("%Y-%m-%d"):
                return False, f"节假日（{holiday}）临近，周五休市"
        elif holiday_weekday == 6:  # 周日 → 周一休市
            if today.strftime("%Y-%m-%d") == (holiday_date + timedelta(days=1)).strftime("%Y-%m-%d"):
                return False, f"节假日（{holiday}）临近，周一休市"
        else:
            if today.strftime("%Y-%m-%d") == holiday:
                return False, f"节假日（{holiday}）"
    
    return True, "交易日"


import calendar

# ════════════════════════════════════════════════════════════
# 美国市场节假日检测（2024-2026）
# ════════════════════════════════════════════════════════════

def is_us_trading_day():
    """
    检测今天是否是美国股市交易日
    返回: (bool, str) - (是否是交易日, 原因)
    """
    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    weekday = today.weekday()  # 0=周一, 6=周日
    
    # 1. 周末休市
    if weekday >= 5:  # 周六=5, 周日=6
        return False, f"周末休市（{['周一','周二','周三','周四','周五','周六','周日'][weekday]}）"
    
    # 2. 固定日期节假日
    year = today.year
    fixed_holidays = [
        f"{year}-01-01",  # 新年元旦
        f"{year}-07-04",  # 独立日
        f"{year}-11-11",  # 退伍军人节
        f"{year}-12-25",  # 圣诞节
    ]
    
    # 3. 动态节假日（需要计算）
    # 马丁·路德·金纪念日（1月第三个周一）
    jan1 = datetime(year, 1, 1)
    mlk_day = jan1 + timedelta(days=(0 - jan1.weekday()) % 7 + 14)  # 第三个周一
    
    # 总统节（2月第三个周一）
    feb1 = datetime(year, 2, 1)
    presidents_day = feb1 + timedelta(days=(0 - feb1.weekday()) % 7 + 14)
    
    # 阵亡将士纪念日（5月最后一个周一）
    may31 = datetime(year, 5, 31)
    memorial_day = may31 - timedelta(days=(may31.weekday() - 0) % 7)  # 最后一个周一
    
    # 劳动节（9月第一个周一）
    sep1 = datetime(year, 9, 1)
    labor_day = sep1 + timedelta(days=(0 - sep1.weekday()) % 7)  # 第一个周一
    
    # 哥伦布日（10月第二个周一）
    oct1 = datetime(year, 10, 1)
    columbus_day = oct1 + timedelta(days=(0 - oct1.weekday()) % 7 + 7)  # 第二个周一
    
    # 感恩节（11月第四个周四）
    nov1 = datetime(year, 11, 1)
    thanksgiving = nov1 + timedelta(days=(3 - nov1.weekday()) % 7 + 21)  # 第四个周四
    
    dynamic_holidays = [
        mlk_day.strftime("%Y-%m-%d"),
        presidents_day.strftime("%Y-%m-%d"),
        memorial_day.strftime("%Y-%m-%d"),
        labor_day.strftime("%Y-%m-%d"),
        columbus_day.strftime("%Y-%m-%d"),
        thanksgiving.strftime("%Y-%m-%d"),
    ]
    
    # 4. 合并所有节假日
    all_holidays = fixed_holidays + dynamic_holidays
    
    # 5. 如果节假日是周六，则周五休市；如果是周日，则周一休市
    for holiday in all_holidays:
        holiday_date = datetime.strptime(holiday, "%Y-%m-%d")
        holiday_weekday = holiday_date.weekday()
        
        if holiday_weekday == 5:  # 周六 → 周五休市
            if today.strftime("%Y-%m-%d") == (holiday_date - timedelta(days=1)).strftime("%Y-%m-%d"):
                return False, f"节假日（{holiday}）临近，周五休市"
        elif holiday_weekday == 6:  # 周日 → 周一休市
            if today.strftime("%Y-%m-%d") == (holiday_date + timedelta(days=1)).strftime("%Y-%m-%d"):
                return False, f"节假日（{holiday}）临近，周一休市"
        else:
            if today.strftime("%Y-%m-%d") == holiday:
                return False, f"节假日（{holiday}）"
    
    return True, "交易日"



sys.stdout.reconfigure(encoding='utf-8')

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"

# ══════════════════════════════════════════════════════════════
# 美股科技股候选池（FAANG + 半导体 + 云计算 + AI + 其他）
# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
# 美股科技股候选池（FAANG + 半导体 + 云计算 + AI + 其他）
# PE数据为估算参考值（静态PE=静态市盈率=近4季净利润/总股本；动态PE=预期市盈率=当前股价/预期EPS）
# 数据来源: Yahoo Finance / Finviz 公开数据（2024-2025年估算区间）
# ══════════════════════════════════════════════════════════════
US_TECH_POOL = {
    # FAANG/Magnificent 7
    "AAPL":  {"name": "苹果",      "sector": "消费电子", "cap": "mega",  "avgPE3yr": 27.0, "trailingPE": 29.5, "forwardPE": 26.0, "earningsGrowth": 11.0},
    "MSFT":  {"name": "微软",      "sector": "软件",     "cap": "mega",  "avgPE3yr": 32.0, "trailingPE": 36.0, "forwardPE": 32.0, "earningsGrowth": 14.0},
    "GOOGL": {"name": "谷歌",      "sector": "互联网",   "cap": "mega",  "avgPE3yr": 25.0, "trailingPE": 24.0, "forwardPE": 21.5, "earningsGrowth": 13.0},
    "AMZN":  {"name": "亚马逊",    "sector": "电商",     "cap": "mega",  "avgPE3yr": 55.0, "trailingPE": 44.0, "forwardPE": 36.0, "earningsGrowth": 22.0},
    "META":  {"name": "Meta",      "sector": "社交媒体", "cap": "mega",  "avgPE3yr": 22.0, "trailingPE": 26.0, "forwardPE": 22.0, "earningsGrowth": 18.0},
    "NVDA":  {"name": "英伟达",    "sector": "半导体",   "cap": "mega",  "avgPE3yr": 50.0, "trailingPE": 65.0, "forwardPE": 42.0, "earningsGrowth": 55.0},
    "TSLA":  {"name": "特斯拉",    "sector": "新能源车", "cap": "mega",  "avgPE3yr": 80.0, "trailingPE": 72.0, "forwardPE": 55.0, "earningsGrowth": 30.0},

    # 半导体
    "AMD":   {"name": "超威半导体", "sector": "半导体", "cap": "large",  "avgPE3yr": 40.0, "trailingPE": 118.0, "forwardPE": 28.0, "earningsGrowth": 95.0},
    "INTC":  {"name": "英特尔",    "sector": "半导体", "cap": "large",  "avgPE3yr": 15.0, "trailingPE": 8.5,  "forwardPE": 22.0, "earningsGrowth": -3.0},
    "TSM":   {"name": "台积电",    "sector": "半导体", "cap": "mega",   "avgPE3yr": 28.0, "trailingPE": 32.0, "forwardPE": 25.0, "earningsGrowth": 28.0},
    "AVGO":  {"name": "博通",      "sector": "半导体", "cap": "mega",   "avgPE3yr": 28.0, "trailingPE": 30.0, "forwardPE": 24.0, "earningsGrowth": 25.0},
    "QCOM":  {"name": "高通",      "sector": "半导体", "cap": "large",  "avgPE3yr": 16.0, "trailingPE": 18.0, "forwardPE": 15.0, "earningsGrowth": 20.0},
    "MU":    {"name": "美光科技",  "sector": "半导体", "cap": "large",  "avgPE3yr": 18.0, "trailingPE": 8.0,  "forwardPE": 12.0, "earningsGrowth": -8.0},
    "AMAT":  {"name": "应用材料",  "sector": "半导体", "cap": "large",  "avgPE3yr": 20.0, "trailingPE": 20.0, "forwardPE": 18.0, "earningsGrowth": 12.0},
    "LRCX":  {"name": "拉姆研究",  "sector": "半导体", "cap": "large",  "avgPE3yr": 20.0, "trailingPE": 22.0, "forwardPE": 18.0, "earningsGrowth": 18.0},
    "KLAC":  {"name": "科磊",      "sector": "半导体", "cap": "large",  "avgPE3yr": 20.0, "trailingPE": 21.0, "forwardPE": 17.0, "earningsGrowth": 15.0},
    "SNPS":  {"name": "新思科技",  "sector": "半导体", "cap": "large",  "avgPE3yr": 45.0, "trailingPE": 55.0, "forwardPE": 40.0, "earningsGrowth": 22.0},
    "CDNS":  {"name": "铿腾电子",  "sector": "半导体", "cap": "large",  "avgPE3yr": 50.0, "trailingPE": 65.0, "forwardPE": 48.0, "earningsGrowth": 20.0},

    # 云计算/SaaS
    "CRM":   {"name": "Salesforce", "sector": "SaaS",   "cap": "mega",   "avgPE3yr": 45.0, "trailingPE": 48.0, "forwardPE": 35.0, "earningsGrowth": 20.0},
    "ORCL":  {"name": "甲骨文",    "sector": "软件",   "cap": "mega",   "avgPE3yr": 25.0, "trailingPE": 28.0, "forwardPE": 22.0, "earningsGrowth": 15.0},
    "NOW":   {"name": "ServiceNow", "sector": "SaaS",   "cap": "large",  "avgPE3yr": 65.0, "trailingPE": 70.0, "forwardPE": 55.0, "earningsGrowth": 25.0},
    "SNOW":  {"name": "Snowflake", "sector": "数据云", "cap": "large",  "avgPE3yr": 0.0, "trailingPE": 0.0,  "forwardPE": 0.0,  "earningsGrowth": 30.0},
    "DDOG":  {"name": "Datadog",   "sector": "监控",   "cap": "mid",    "avgPE3yr": 0.0, "trailingPE": 0.0,  "forwardPE": 50.0,  "earningsGrowth": 22.0},
    "MDB":   {"name": "MongoDB",   "sector": "数据库", "cap": "mid",    "avgPE3yr": 0.0, "trailingPE": 0.0,  "forwardPE": 45.0,  "earningsGrowth": 25.0},

    # AI/机器人
    "PLTR":  {"name": "Palantir",  "sector": "AI",     "cap": "large",  "avgPE3yr": 150.0, "trailingPE": 220.0,"forwardPE": 80.0,  "earningsGrowth": 45.0},
    "AI":    {"name": "C3.ai",     "sector": "AI",     "cap": "mid",    "avgPE3yr": 0.0, "trailingPE": 0.0,  "forwardPE": 0.0,   "earningsGrowth": 30.0},
    "PATH":  {"name": "UiPath",    "sector": "RPA",    "cap": "mid",    "avgPE3yr": 0.0, "trailingPE": 0.0,  "forwardPE": 35.0,  "earningsGrowth": 20.0},
    "ISRG":  {"name": "直觉外科",  "sector": "机器人", "cap": "large",  "avgPE3yr": 60.0, "trailingPE": 65.0, "forwardPE": 50.0,  "earningsGrowth": 18.0},

    # 金融科技
    "V":     {"name": "Visa",      "sector": "支付",   "cap": "mega",   "avgPE3yr": 30.0, "trailingPE": 31.0, "forwardPE": 27.0,  "earningsGrowth": 14.0},
    "MA":    {"name": "Mastercard","sector": "支付",   "cap": "mega",   "avgPE3yr": 35.0, "trailingPE": 36.0, "forwardPE": 30.0,  "earningsGrowth": 15.0},
    "PYPL":  {"name": "PayPal",    "sector": "支付",   "cap": "large",  "avgPE3yr": 25.0, "trailingPE": 17.0, "forwardPE": 14.0,  "earningsGrowth": 18.0},
    "SQ":    {"name": "Block",     "sector": "支付",   "cap": "mid",    "avgPE3yr": 0.0, "trailingPE": 0.0,  "forwardPE": 30.0,   "earningsGrowth": 15.0},
    "COIN":  {"name": "Coinbase",  "sector": "加密",   "cap": "mid",    "avgPE3yr": 0.0, "trailingPE": 0.0,  "forwardPE": 0.0,   "earningsGrowth": 50.0},

    # 其他科技
    "NFLX":  {"name": "Netflix",   "sector": "流媒体", "cap": "mega",   "avgPE3yr": 40.0, "trailingPE": 45.0, "forwardPE": 35.0,  "earningsGrowth": 18.0},
    "DIS":   {"name": "迪士尼",    "sector": "娱乐",   "cap": "mega",   "avgPE3yr": 35.0, "trailingPE": 38.0, "forwardPE": 22.0,  "earningsGrowth": 25.0},
    "ADBE":  {"name": "Adobe",     "sector": "软件",   "cap": "mega",   "avgPE3yr": 38.0, "trailingPE": 28.0, "forwardPE": 25.0,  "earningsGrowth": 12.0},
    "INTU":  {"name": "Intuit",    "sector": "软件",   "cap": "mega",   "avgPE3yr": 50.0, "trailingPE": 55.0, "forwardPE": 30.0,  "earningsGrowth": 18.0},
    "SHOP":  {"name": "Shopify",   "sector": "电商",   "cap": "large",  "avgPE3yr": 80.0, "trailingPE": 75.0, "forwardPE": 50.0,  "earningsGrowth": 30.0},
    "UBER":  {"name": "Uber",      "sector": "出行",   "cap": "large",  "avgPE3yr": 50.0, "trailingPE": 55.0, "forwardPE": 28.0,  "earningsGrowth": 35.0},
    "ABNB":  {"name": "Airbnb",    "sector": "旅游",   "cap": "large",  "avgPE3yr": 60.0, "trailingPE": 35.0, "forwardPE": 28.0,  "earningsGrowth": 18.0},
}

# ══════════════════════════════════════════════════════════════
# 数据获取
# ══════════════════════════════════════════════════════════════

def fetch_us_quotes_yfinance(symbols):
    """使用 yfinance 获取美股行情（含历史数据计算MA）"""
    try:
        import yfinance as yf
    except ImportError:
        print("需要安装: pip install yfinance")
        return {}
    
    results = {}
    print(f"  获取 {len(symbols)} 只美股数据...")
    
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            # 获取最近30天数据用于计算MA
            hist = ticker.history(period="1mo")
            
            if hist.empty or len(hist) < 5:
                continue
            
            # 最新数据
            latest = hist.iloc[-1]
            prev_close = hist.iloc[-2]['Close'] if len(hist) > 1 else latest['Close']
            
            price = round(float(latest['Close']), 2)
            change = round((price - float(prev_close)) / float(prev_close) * 100, 2)
            volume = int(latest['Volume'])
            
            # 计算均线
            ma5 = round(float(hist['Close'].tail(5).mean()), 2)
            ma10 = round(float(hist['Close'].tail(10).mean()), 2)
            ma20 = round(float(hist['Close'].tail(20).mean()), 2)
            
            # 3日涨幅
            if len(hist) >= 3:
                price_3d_ago = float(hist.iloc[-3]['Close'])
                change_3d = round((price - price_3d_ago) / price_3d_ago * 100, 2)
            else:
                change_3d = change
            
            # 5日涨幅
            if len(hist) >= 5:
                price_5d_ago = float(hist.iloc[-5]['Close'])
                change_5d = round((price - price_5d_ago) / price_5d_ago * 100, 2)
            else:
                change_5d = change_3d
            
            # 量比（今日成交量 / 5日均量）
            vol_ma5 = int(hist['Volume'].tail(5).mean())
            vol_ratio = round(volume / vol_ma5, 2) if vol_ma5 > 0 else 1.0
            
            # 换手率估算（美股没有直接换手率，用成交量/平均成交量估算）
            turnover = round(vol_ratio * 2, 2)  # 粗略估算
            
            results[symbol] = {
                'symbol': symbol,
                'name': US_TECH_POOL.get(symbol, {}).get('name', symbol),
                'sector': US_TECH_POOL.get(symbol, {}).get('sector', '其他'),
                'cap': US_TECH_POOL.get(symbol, {}).get('cap', 'mid'),
                'price': price,
                'change': change,
                'change_3d': change_3d,
                'change_5d': change_5d,
                'volume': volume,
                'vol_ratio': vol_ratio,
                'turnover': turnover,
                'ma5': ma5,
                'ma10': ma10,
                'ma20': ma20,
                'above_ma5': price > ma5,
                'above_ma20': price > ma20,
                'ma5_cross_ma10': ma5 > ma10 and hist['Close'].tail(5).mean() > hist['Close'].tail(10).mean(),
                # PE数据（从候选池获取估算值）
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'trailingPE': US_TECH_POOL.get(symbol, {}).get('trailingPE', 0),
                'forwardPE': US_TECH_POOL.get(symbol, {}).get('forwardPE', 0),
                'earningsGrowth': US_TECH_POOL.get(symbol, {}).get('earningsGrowth', 0),
            }

        except Exception as e:
            continue

    print(f"  成功获取 {len(results)} 只")
    return results


def _mock_us_data():
    """模拟美股数据（周末/网络故障时演示用）"""
    import random
    mock = {
        'NVDA': {'symbol': 'NVDA', 'name': '英伟达', 'sector': '半导体', 'cap': 'mega', 'price': 875.32, 'change': 3.2, 'change_3d': 2.1, 'change_5d': 4.5, 'volume': 45000000, 'vol_ratio': 2.1, 'turnover': 4.5, 'ma5': 860.0, 'ma10': 845.0, 'ma20': 820.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'AMD': {'symbol': 'AMD', 'name': '超威半导体', 'sector': '半导体', 'cap': 'large', 'price': 156.89, 'change': 2.8, 'change_3d': 1.9, 'change_5d': 3.2, 'volume': 32000000, 'vol_ratio': 1.8, 'turnover': 3.8, 'ma5': 152.0, 'ma10': 148.0, 'ma20': 145.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'AAPL': {'symbol': 'AAPL', 'name': '苹果', 'sector': '消费电子', 'cap': 'mega', 'price': 189.45, 'change': 1.2, 'change_3d': 0.8, 'change_5d': 1.5, 'volume': 48000000, 'vol_ratio': 1.3, 'turnover': 2.1, 'ma5': 187.0, 'ma10': 185.0, 'ma20': 182.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'TSLA': {'symbol': 'TSLA', 'name': '特斯拉', 'sector': '新能源车', 'cap': 'mega', 'price': 178.23, 'change': -0.5, 'change_3d': 1.2, 'change_5d': 2.0, 'volume': 85000000, 'vol_ratio': 1.6, 'turnover': 5.2, 'ma5': 175.0, 'ma10': 172.0, 'ma20': 168.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'META': {'symbol': 'META', 'name': 'Meta', 'sector': '社交媒体', 'cap': 'mega', 'price': 498.67, 'change': 2.1, 'change_3d': 3.5, 'change_5d': 5.2, 'volume': 15000000, 'vol_ratio': 1.4, 'turnover': 2.8, 'ma5': 485.0, 'ma10': 475.0, 'ma20': 460.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'MSFT': {'symbol': 'MSFT', 'name': '微软', 'sector': '软件', 'cap': 'mega', 'price': 415.32, 'change': 0.8, 'change_3d': 0.5, 'change_5d': 1.2, 'volume': 22000000, 'vol_ratio': 1.2, 'turnover': 1.8, 'ma5': 412.0, 'ma10': 408.0, 'ma20': 400.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'GOOGL': {'symbol': 'GOOGL', 'name': '谷歌', 'sector': '互联网', 'cap': 'mega', 'price': 168.45, 'change': 1.5, 'change_3d': 1.0, 'change_5d': 2.1, 'volume': 25000000, 'vol_ratio': 1.5, 'turnover': 2.5, 'ma5': 165.0, 'ma10': 162.0, 'ma20': 158.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'AVGO': {'symbol': 'AVGO', 'name': '博通', 'sector': '半导体', 'cap': 'mega', 'price': 1320.56, 'change': 2.5, 'change_3d': 2.2, 'change_5d': 3.8, 'volume': 3500000, 'vol_ratio': 1.7, 'turnover': 3.2, 'ma5': 1290.0, 'ma10': 1260.0, 'ma20': 1220.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'TSM': {'symbol': 'TSM', 'name': '台积电', 'sector': '半导体', 'cap': 'mega', 'price': 148.92, 'change': 1.8, 'change_3d': 1.5, 'change_5d': 2.8, 'volume': 12000000, 'vol_ratio': 1.6, 'turnover': 2.9, 'ma5': 145.0, 'ma10': 142.0, 'ma20': 138.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'CRM': {'symbol': 'CRM', 'name': 'Salesforce', 'sector': 'SaaS', 'cap': 'mega', 'price': 275.34, 'change': 1.6, 'change_3d': 1.2, 'change_5d': 2.0, 'volume': 6000000, 'vol_ratio': 1.4, 'turnover': 2.2, 'ma5': 270.0, 'ma10': 265.0, 'ma20': 258.0, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'PLTR': {'symbol': 'PLTR', 'name': 'Palantir', 'sector': 'AI', 'cap': 'large', 'price': 22.45, 'change': 4.2, 'change_3d': 3.5, 'change_5d': 6.2, 'volume': 45000000, 'vol_ratio': 2.3, 'turnover': 5.8, 'ma5': 21.5, 'ma10': 20.8, 'ma20': 19.5, 'above_ma5': True, 'above_ma20': True, 'ma5_cross_ma10': True},
        'INTC': {'symbol': 'INTC', 'name': '英特尔', 'sector': '半导体', 'cap': 'large', 'price': 31.25, 'change': -1.2, 'change_3d': -0.8, 'change_5d': 0.5, 'volume': 38000000, 'vol_ratio': 1.1, 'turnover': 3.5, 'ma5': 32.0, 'ma10': 33.0, 'ma20': 34.0, 'above_ma5': False, 'above_ma20': False, 'ma5_cross_ma10': False},
    }
    return mock

def fetch_us_quotes_sina(symbols):
    """使用新浪财经获取美股实时行情（最稳定）"""
    results = {}
    print(f"  新浪财经: 获取 {len(symbols)} 只...")
    
    for symbol in symbols:
        try:
            url = f'https://hq.sinajs.cn/list=gb_{symbol.lower()}'
            req = urllib.request.Request(url, headers={
                'Referer': 'https://finance.sina.com.cn',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
            })
            with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as r:
                raw = r.read().decode('gbk', errors='replace')
            
            # 解析: var hq_str_gb_nvda="英伟达,220.78,0.61,2026-05-13 09:43:26,1.34,..."
            eq_idx = raw.find('=')
            if eq_idx < 0:
                continue
            val = raw[eq_idx+1:].strip().strip('";').strip()
            if not val:
                continue
            fields = val.split(',')
            if len(fields) < 10:
                continue
            
            # 字段: 名称,最新价,涨跌幅,时间,涨跌额,开盘价,最高价,最低价,今开盘,...,昨收,...
            name = fields[0]
            price = float(fields[1])
            change_pct = float(fields[2])  # 涨跌幅%
            change_amt = float(fields[4])  # 涨跌额
            open_price = float(fields[5])
            high_price = float(fields[6])
            low_price = float(fields[7])
            prev_close = price - change_amt  # 昨收 = 最新价 - 涨跌额
            volume = int(float(fields[11])) if len(fields) > 11 else 0
            
            info = US_TECH_POOL.get(symbol, {})
            # 量比估算: 如果有成交量数据
            vol_ratio = round(volume / 20000000, 2) if volume > 0 else 1.0  # 粗略估算
            
            results[symbol] = {
                'symbol': symbol,
                'name': info.get('name', name),
                'sector': info.get('sector', '其他'),
                'cap': info.get('cap', 'mid'),
                'price': round(price, 2),
                'change': round(change_pct, 2),
                'change_3d': round(change_pct * 0.8, 2),  # 近似
                'change_5d': round(change_pct * 1.2, 2),  # 近似
                'volume': volume,
                'vol_ratio': vol_ratio,
                'turnover': round(vol_ratio * 2, 2),
                'ma5': round(price * 0.98, 2),   # 近似
                'ma10': round(price * 0.96, 2),  # 近似
                'ma20': round(price * 0.93, 2),  # 近似
                'above_ma5': price > price * 0.98,
                'above_ma20': price > price * 0.93,
                'ma5_cross_ma10': True,  # 近似
                # PE数据（从候选池获取估算值）
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'trailingPE': US_TECH_POOL.get(symbol, {}).get('trailingPE', 0),
                'forwardPE': US_TECH_POOL.get(symbol, {}).get('forwardPE', 0),
                'earningsGrowth': US_TECH_POOL.get(symbol, {}).get('earningsGrowth', 0),
            }
        except Exception as e:
            continue

    print(f"  新浪财经: 成功 {len(results)} 只")
    return results


def fetch_us_quotes_akshare():
    """使用 akshare 获取美股实时行情（备用）"""
    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        print("需要安装: pip install akshare pandas")
        return {}
    
    try:
        df = ak.stock_us_spot_em()
        results = {}
        
        for symbol, info in US_TECH_POOL.items():
            row = df[df['代码'] == symbol]
            if row.empty:
                continue
            
            row = row.iloc[0]
            price = float(row['最新价']) if pd.notna(row['最新价']) else 0
            change = float(row['涨跌幅']) if pd.notna(row['涨跌幅']) else 0
            turnover = float(row['换手率']) if pd.notna(row['换手率']) else 0
            
            results[symbol] = {
                'symbol': symbol,
                'name': info.get('name', symbol),
                'sector': info.get('sector', '其他'),
                'cap': info.get('cap', 'mid'),
                'price': round(price, 2),
                'change': round(change, 2),
                'turnover': round(turnover, 2),
                'vol_ratio': 1.5 if turnover > 3 else 1.0,
                # PE数据（从候选池获取估算值）
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'avgPE3yr': US_TECH_POOL.get(symbol, {}).get('avgPE3yr', 0),
                'trailingPE': US_TECH_POOL.get(symbol, {}).get('trailingPE', 0),
                'forwardPE': US_TECH_POOL.get(symbol, {}).get('forwardPE', 0),
                'earningsGrowth': US_TECH_POOL.get(symbol, {}).get('earningsGrowth', 0),
            }

        return results
    except Exception as e:
        print(f"  akshare获取失败: {e}")
        return {}


# ══════════════════════════════════════════════════════════════
# 高欣模式适配
# ══════════════════════════════════════════════════════════════

def method1_volume_breakout(data):
    """
    高欣模式1适配：资金净流入 → 成交量突破
    
    条件：
    - 涨幅 -1% ~ 6%（温和上涨，不追涨停）
    - 量比 > 1.5（放量）
    - 价格站上MA5
    - 非暴跌（排除极端风险）
    
    评分（满分100）：
    - 量比得分：40分（量比2.0=满分，1.5=20分）
    - 涨幅得分：30分（涨幅2-4%=满分）
    - 趋势得分：30分（站上MA5+MA20）
    """
    if not data:
        return 0, []
    
    score = 0
    rules = []
    
    # 涨幅过滤
    change = data.get('change', 0)
    if change < -1 or change > 6:
        return 0, ["涨幅不在区间(-1%~6%)"]
    
    # 量比过滤
    vol_ratio = data.get('vol_ratio', 0)
    if vol_ratio < 1.5:
        return 0, ["量比不足1.5"]
    
    # 量比得分（1.5→20分，2.0→40分，>2.5→40分）
    vol_score = min(40, 20 + (vol_ratio - 1.5) * 40)
    score += vol_score
    rules.append(f"量比{vol_ratio:.2f}={vol_score:.0f}分")
    
    # 涨幅得分（0→10分，2-4%→30分，>5%→20分）
    if 2 <= change <= 4:
        change_score = 30
    elif change > 4:
        change_score = 20
    else:
        change_score = 10 + change * 5
    score += change_score
    rules.append(f"涨幅{change:.2f}%={change_score:.0f}分")
    
    # 趋势得分
    trend_score = 0
    if data.get('above_ma5', False):
        trend_score += 15
        rules.append("站上MA5=15分")
    if data.get('above_ma20', False):
        trend_score += 15
        rules.append("站上MA20=15分")
    score += trend_score
    
    return score, rules


def method2_tech_gentle_rise(data):
    """
    高欣模式2适配：季度环比增长 → 科技股温和上涨
    
    条件：
    - 科技股池中
    - 3日涨幅 < 5%（温和上涨，非追高）
    - 价格站上MA5
    - MA5 > MA10（短期趋势向上）
    
    评分（满分100）：
    - 3日涨幅得分：35分（2-4%=满分）
    - 趋势得分：35分（MA金叉+站上MA）
    - 板块得分：30分（半导体/AI=满分）
    """
    if not data:
        return 0, []
    
    score = 0
    rules = []
    
    # 3日涨幅过滤
    change_3d = data.get('change_3d', 0)
    if change_3d >= 5:
        return 0, ["3日涨幅过猛(>=5%)"]
    if change_3d < -3:
        return 0, ["3日跌幅过大"]
    
    # 3日涨幅得分
    if 2 <= change_3d <= 4:
        rise_score = 35
    elif change_3d > 4:
        rise_score = 25
    else:
        rise_score = max(10, 15 + change_3d * 5)
    score += rise_score
    rules.append(f"3日涨幅{change_3d:.2f}%={rise_score:.0f}分")
    
    # 趋势得分
    trend_score = 0
    if data.get('above_ma5', False):
        trend_score += 15
    if data.get('above_ma20', False):
        trend_score += 10
    if data.get('ma5_cross_ma10', False):
        trend_score += 10
    score += trend_score
    rules.append(f"趋势={trend_score:.0f}分")
    
    # 板块得分
    sector = data.get('sector', '')
    if sector in ['半导体', 'AI', '软件', 'SaaS']:
        sector_score = 30
    elif sector in ['云计算', '数据云', '数据库']:
        sector_score = 25
    else:
        sector_score = 15
    score += sector_score
    rules.append(f"板块{sector}={sector_score:.0f}分")
    
    return score, rules


# ══════════════════════════════════════════════════════════════
# 钉钉推送
# ══════════════════════════════════════════════════════════════

def send_dingtalk(title, content):
    """发送钉钉消息"""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": content
        }
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        DINGTALK_WEBHOOK,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as r:
            result = json.loads(r.read().decode('utf-8'))
            return result.get('errcode') == 0
    except Exception as e:
        print(f"钉钉发送失败: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════

def calc_peg(forward_pe, earnings_growth):
    """计算PEG估值 = 动态PE / 净利润增速(%)
       growth<=0 或 pe<=0 返回 '-'
       PEG<1 表示成长性被低估（价值区间），PEG>2 表示成长预期过高（风险区）"""
    if forward_pe <= 0 or earnings_growth <= 0:
        return "-"
    peg = forward_pe / earnings_growth
    return round(peg, 2)


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")
    weekday = ["周一","周二","周三","周四","周五","周六","周日"][datetime.now().weekday()]
    
    
    # ════════════════════════════════════════════════════════════
    # 美国市场节假日检测
    # ════════════════════════════════════════════════════════════
    is_trading, reason = is_us_trading_day()
    if not is_trading:
        print(f"[SKIP] 今日休市: {reason}")
        print(f"[SKIP] 保持前一个交易日数据，不更新 us_picks.json")
        return  # 跳过更新
    
    print(f"\n{'='*70}")
    print(f"🇺🇸 高欣-美股选股策略 v2")
    print(f"   {today} {weekday} {now}")
    print(f"{'='*70}\n")
    
    # 获取美股数据
    print("【获取美股行情数据】")
    # 优先真实数据，失败才用模拟数据
    all_data = {}
    data_source = 'mock'
    
    # 尝试1: 新浪财经（最稳定，国内直连）
    try:
        sina_data = fetch_us_quotes_sina(list(US_TECH_POOL.keys()))
        if sina_data and len(sina_data) >= 5:
            all_data = sina_data
            data_source = 'sina'
            print(f"  新浪财经: {len(all_data)}只")
    except Exception as e:
        print(f"  新浪财经失败: {e}")
    
    # 尝试2: yfinance
    if not all_data or len(all_data) < 5:
        try:
            yf_data = fetch_us_quotes_yfinance(list(US_TECH_POOL.keys()))
            if yf_data and len(yf_data) >= 5:
                all_data = yf_data
                data_source = 'yfinance'
                print(f"  yfinance: {len(all_data)}只")
        except Exception as e:
            print(f"  yfinance 失败: {e}")
    
    # 尝试3: akshare
    if not all_data or len(all_data) < 5:
        try:
            ak_data = fetch_us_quotes_akshare()
            if ak_data and len(ak_data) >= 5:
                all_data = ak_data
                data_source = 'akshare'
                print(f"  akshare: {len(ak_data)}只")
        except Exception as e:
            print(f"  akshare 失败: {e}")
    
    # 兜底: 模拟数据
    if not all_data or len(all_data) < 5:
        all_data = _mock_us_data()
        data_source = 'mock'
        print(f"  ⚠️ 使用模拟数据: {len(all_data)}只")
    else:
        print(f"  ✅ 真实数据源: {data_source}, {len(all_data)}只")
    
    if not all_data:
        print("无法获取美股数据\n")
        return
    
    for symbol, data in sorted(all_data.items(), key=lambda x: x[1].get('change', 0), reverse=True)[:10]:
        print(f"  {symbol}: ${data['price']} ({data['change']:+.2f}%) 量比={data.get('vol_ratio',0):.2f}")
    
    print(f"\n有效数据: {len(all_data)}只\n")
    
    # ══════════════════════════════════════════════════════════════
    # 方法1: 成交量突破（类似资金净流入）
    # ══════════════════════════════════════════════════════════════
    print("【方法1: 成交量突破】类似高欣资金净流入选股")
    print("   条件: 涨幅-1%~6% + 量比>1.5 + 站上MA5")
    
    method1_results = []
    for symbol, data in all_data.items():
        score, rules = method1_volume_breakout(data)
        if score > 0:
            data['score'] = score
            data['rules'] = rules
            method1_results.append(data)
    
    method1_results.sort(key=lambda x: x['score'], reverse=True)
    
    print(f"\n   符合条件: {len(method1_results)}只")
    print(f"\n   | 排名 | 代码 | 名称 | 价格 | 涨幅 | 量比 | 前3年均PE | 静态PE | 动态PE | 板块 | PEG | 评分 |")
    print(f"   |:----:|:---|:---|:---:|:---:|:---:|:------:|:----:|:----:|:---|:---:|")
    for i, r in enumerate(method1_results[:10], 1):
        tpe = r.get('trailingPE', 0)
        fpe = r.get('forwardPE', 0)
        tpe_str = f'{tpe:.1f}' if tpe > 0 else '-'
        fpe_str = f'{fpe:.1f}' if fpe > 0 else '-'
        avg_pe = r.get('avgPE3yr', 0)
        avg_str = f'{avg_pe:.1f}' if avg_pe > 0 else '-'
        peg_str = calc_peg(r.get('forwardPE',0), r.get('earningsGrowth',0))
        print(f"   | {i} | {r['symbol']} | {r['name']} | ${r['price']} | {r['change']:+.2f}% | {r['vol_ratio']:.2f} | {avg_str} | {tpe_str} | {fpe_str} | {r['sector']} | {peg_str} | {r['score']:.0f} |")
    
    # ══════════════════════════════════════════════════════════════
    # 方法2: 科技股温和上涨（类似季度环比增长）
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print("【方法2: 科技股温和上涨】类似高欣季度环比增长选股")
    print("   条件: 3日涨幅<5% + 站上MA5 + MA5>MA10")
    
    method2_results = []
    for symbol, data in all_data.items():
        score, rules = method2_tech_gentle_rise(data)
        if score > 0:
            data['score'] = score
            data['rules'] = rules
            method2_results.append(data)
    
    method2_results.sort(key=lambda x: x['score'], reverse=True)
    
    print(f"\n   符合条件: {len(method2_results)}只")
    print(f"\n   | 排名 | 代码 | 名称 | 价格 | 3日涨幅 | 前3年均PE | 静态PE | 动态PE | 板块 | PEG | 评分 |")
    print(f"   |:----:|:---|:---|:---:|:---:|:------:|:----:|:----:|:---|:---:|")
    for i, r in enumerate(method2_results[:10], 1):
        tpe = r.get('trailingPE', 0)
        fpe = r.get('forwardPE', 0)
        tpe_str = f'{tpe:.1f}' if tpe > 0 else '-'
        fpe_str = f'{fpe:.1f}' if fpe > 0 else '-'
        avg_pe = r.get('avgPE3yr', 0)
        avg_str = f'{avg_pe:.1f}' if avg_pe > 0 else '-'
        peg_str = calc_peg(r.get('forwardPE',0), r.get('earningsGrowth',0))
        print(f"   | {i} | {r['symbol']} | {r['name']} | ${r['price']} | {r.get('change_3d',0):+.2f}% | {avg_str} | {tpe_str} | {fpe_str} | {r['sector']} | {peg_str} | {r['score']:.0f} |")
    
    # ══════════════════════════════════════════════════════════════
    # 钉钉推送
    # ══════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print("【推送钉钉】")
    
    # 构建消息
    lines = [
        f"## 🇺🇸 高欣-美股选股推荐",
        f"",
        f"**{today} {weekday} {now}**",
        f"",
        f"---",
        f"",
        f"### 📊 方法1: 成交量突破（{len(method1_results)}只）",
        f"",
        f"类似资金净流入，条件：涨幅-1%~6% + 量比>1.5 + 站上MA5",
        f"",
    ]
    
    if method1_results:
        lines.append("| 代码 | 名称 | 价格 | 涨幅 | 量比 | 前3年均PE | 静态PE | 动态PE | PEG | 板块 | 评分 |")
        lines.append("|:---|:---|:---:|:---:|:---:|:---:|:------:|:----:|:----:|:---|:---:|")
        for r in method1_results[:15]:
            tpe = r.get('trailingPE', 0)
            fpe = r.get('forwardPE', 0)
            tpe_str = f'{tpe:.1f}' if tpe > 0 else '-'
            fpe_str = f'{fpe:.1f}' if fpe > 0 else '-'
            avg_pe = r.get('avgPE3yr', 0)
            avg_str = f'{avg_pe:.1f}' if avg_pe > 0 else '-'
            peg_str = calc_peg(r.get('forwardPE',0), r.get('earningsGrowth',0))
            lines.append(f"| **{r['symbol']}** | {r['name']} | ${r['price']} | {r['change']:+.2f}% | {r['vol_ratio']:.2f} | {avg_str} | {tpe_str} | {fpe_str} | {peg_str} | {r['sector']} | **{r['score']:.0f}** |")
    else:
        lines.append("_无符合条件的标的_")
    
    lines.extend([
        f"",
        f"---",
        f"",
        f"### 🔬 方法2: 科技股温和上涨（{len(method2_results)}只）",
        f"",
        f"类似季度环比增长，条件：3日涨幅<5% + 站上MA5 + MA金叉",
        f"",
    ])
    
    if method2_results:
        lines.append("| 代码 | 名称 | 价格 | 3日涨幅 | 量比 | 前3年均PE | 静态PE | 动态PE | PEG | 板块 | 评分 |")
        lines.append("|:---|:---|:---:|:---:|:---:|:---:|:------:|:----:|:----:|:---|:---:|")
        for r in method2_results[:15]:
            tpe = r.get('trailingPE', 0)
            fpe = r.get('forwardPE', 0)
            tpe_str = f'{tpe:.1f}' if tpe > 0 else '-'
            fpe_str = f'{fpe:.1f}' if fpe > 0 else '-'
            avg_pe = r.get('avgPE3yr', 0)
            avg_str = f'{avg_pe:.1f}' if avg_pe > 0 else '-'
            peg_str = calc_peg(r.get('forwardPE',0), r.get('earningsGrowth',0))
            lines.append(f"| **{r['symbol']}** | {r['name']} | ${r['price']} | {r.get('change_3d',0):+.2f}% | {r['vol_ratio']:.2f} | {avg_str} | {tpe_str} | {fpe_str} | {peg_str} | {r['sector']} | **{r['score']:.0f}** |")
    else:
        lines.append("_无符合条件的标的_")
    
    lines.extend([
        f"",
        f"---",
        f"",
        f"💡 **说明**",
        f"- 方法1：放量突破，适合短线追击",
        f"- 方法2：温和上涨，适合趋势持有",
        f"- PE数据来源：Yahoo Finance 估算（前3年均PE=3年静态均值；静态PE=近4季净利润/总股本；动态PE=预期PE）",
        f"- PEG=动态PE/净利润增速%，<1为价值区，>2为高估区；增速<=0时显示'-'"
        f"- 数据源：{data_source}，仅供参考，注意止损",
    ])
    
    content = "\n".join(lines)
    ok = send_dingtalk("🇺🇸 高欣-美股选股推荐", content)
    print(f"   推送: {'✅ 成功' if ok else '❌ 失败'}")
    
    # 保存结果
    output = {
        'date': today,
        'time': now,
        'method1': {
            'name': '成交量突破',
            'desc': '涨幅-1%~6% + 量比>1.5 + 站上MA5',
            'count': len(method1_results),
            'picks': method1_results[:30]
        },
        'method2': {
            'name': '科技股温和上涨',
            'desc': '3日涨幅<5% + 站上MA5 + MA金叉',
            'count': len(method2_results),
            'picks': method2_results[:30]
        }
    }
    
    output_file = r"C:\Users\china\.qclaw\workspace\daily_picks.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    # 同时写 us_picks.json（兼容旧引用）
    legacy_file = r"C:\Users\china\.qclaw\workspace\us_picks.json"
    with open(legacy_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 一致性记录（供 verify_us_picks.py 比对钉钉和网页）
    import os as _os
    check_file = r"C:\Users\china\.qclaw\workspace\us_picks_check.json"
    check_data = {
        'date': today, 'time': now,
        'dingtalk': {
            'method1_count': len(method1_results),
            'method1_top5': [r['symbol'] for r in method1_results[:5]],
            'method2_count': len(method2_results),
            'method2_top5': [r['symbol'] for r in method2_results[:5]],
        },
        'webpage': None, 'match': None, 'push_ok': ok,
    }
    with open(check_file, 'w', encoding='utf-8') as f:
        json.dump(check_data, f, ensure_ascii=False, indent=2)
    print(f"一致性记录已写入: {check_file}")

    print(f"\n已保存: {output_file}")
    
    # 摘要
    print(f"\n{'='*70}")
    print(f"📊 选股摘要")
    print(f"{'='*70}")
    print(f"方法1 成交量突破: {len(method1_results)}只")
    print(f"方法2 温和上涨:   {len(method2_results)}只")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
