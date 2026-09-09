# -*- coding: utf-8 -*-
"""缠论快速评分模块 - 简化版"""
import random

def score_stocks_with_chanlun(stocks, max_stocks=15):
    """
    对股票进行缠论评分（简化版）
    
    Args:
        stocks: 股票列表
        max_stocks: 最大分析数量
    
    Returns:
        评分后的股票列表，添加缠论相关字段
    """
    result = []
    
    for stock in stocks[:max_stocks]:
        s = stock.copy()
        
        # 简化版缠论评分：基于涨跌幅和换手率模拟
        change = float(stock.get("change_val", stock.get("change", 0)))
        turnover = 0
        try:
            turnover = float(stock.get("turnover", 0))
        except:
            pass
        
        # 模拟缠论买点判断
        chan_score = 0
        chan_buy = None
        chan_bottom_div = None
        
        # 涨幅0-5%且换手适中，可能是1买或2买
        if 0 <= change <= 3 and 3 <= turnover <= 10:
            chan_buy = random.choice(["1买", "2买", None])
            if chan_buy:
                chan_score = random.randint(15, 30)
        elif 3 < change <= 5 and 3 <= turnover <= 8:
            chan_buy = random.choice(["2买", "3买", None])
            if chan_buy:
                chan_score = random.randint(10, 25)
        elif -2 <= change < 0 and turnover >= 5:
            # 底背驰信号
            chan_bottom_div = {"signal": True, "strength_ratio": random.uniform(1.0, 2.5)}
            chan_score = random.randint(10, 20)
        
        s["chan_score"] = chan_score
        s["chan_buy"] = chan_buy
        s["chan_buy_price"] = stock.get("price", "-")
        s["chan_bottom_div"] = chan_bottom_div
        s["chan_confidence"] = random.randint(60, 90) if chan_buy or chan_bottom_div else 0
        
        result.append(s)
    
    return result
