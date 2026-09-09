# -*- coding: utf-8 -*-
"""换手率评分工具"""

def turnover_score(turnover, market_cap=None, max_score=15):
    """
    根据换手率和市值计算换手评分
    
    Args:
        turnover: 换手率（%）
        market_cap: 流通市值（元），可选
        max_score: 最高分数
    
    Returns:
        换手评分 (0-max_score)
    """
    try:
        t = float(turnover)
    except (TypeError, ValueError):
        return 0
    
    if t <= 0:
        return 0
    
    # 基于换手率的基础评分
    if 3 <= t <= 7:
        # 温和换手，最理想
        base_score = max_score
    elif 7 < t <= 10:
        # 适中换手
        base_score = max_score * 0.8
    elif 2 <= t < 3:
        # 偏低
        base_score = max_score * 0.5
    elif 10 < t <= 15:
        # 偏高
        base_score = max_score * 0.6
    elif 15 < t <= 20:
        # 高换手
        base_score = max_score * 0.4
    else:
        # 极端换手
        base_score = max_score * 0.2
    
    # 如果有市值信息，可以进一步调整
    if market_cap:
        try:
            cap = float(market_cap)
            # 小盘股换手率高一些也合理
            if cap < 50e8:  # 50亿以下
                base_score = min(base_score * 1.2, max_score)
            # 大盘股换手率低一些也合理
            elif cap > 200e8:  # 200亿以上
                base_score = min(base_score * 0.9, max_score)
        except:
            pass
    
    return round(base_score, 1)
