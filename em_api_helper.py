# -*- coding: utf-8 -*-
"""东方财富 / Sina 行情辅助模块

说明：
    原 Qclaw 环境下的 em_api_helper.py 未能找回，此处按调用方
    （popeye_training.py -> get_realtime_price()）的契约重新实现，
    底层复用仓库已有的 data_source.fetch_sina_batch()，避免重复造轮子。

调用契约（与历史保持一致）：
    sina_get_quotes(["sh600000", "sz000001"]) -> [
        {"price": 10.2, "close_yesterday": 10.0, "volume": 123456}, ...
    ]
    返回列表，顺序尽量与入参一致；取不到行情的代码会被跳过。
"""
import sys
import os

# 保证同目录模块可导入（GitHub Actions 工作目录即仓库根）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _split_symbol(sym):
    """'sh600000' -> ('sh', '600000')；'600000' -> ('', '600000')"""
    sym = (sym or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if sym.startswith(p) and len(sym) > len(p):
            return p, sym[len(p):]
    return "", sym


def sina_get_quotes(symbols):
    """批量获取 Sina 实时行情（带交易所前缀的合约代码）

    Args:
        symbols: 形如 ["sh600000", "sz000001"] 的代码列表

    Returns:
        list[dict]: 每个元素含 price / close_yesterday / volume 等字段；
                    失败时返回空列表（调用方有 try 兜底）。
    """
    if not symbols:
        return []

    try:
        from data_source import fetch_sina_batch
    except Exception as e:
        print(f"[em_api_helper] data_source 不可用: {e}")
        return []

    # fetch_sina_batch 需要「无前缀」代码，且会自行补 sh/sz
    plain = []
    mapping = {}  # plain_code -> [原始 symbol 顺序索引]
    for idx, sym in enumerate(symbols):
        _, code = _split_symbol(sym)
        if not code:
            continue
        plain.append(code)
        mapping.setdefault(code, []).append(idx)

    if not plain:
        return []

    try:
        raw = fetch_sina_batch(plain) or {}
    except Exception as e:
        print(f"[em_api_helper] 拉取行情失败: {e}")
        return []

    out = []
    for code in plain:
        d = raw.get(code)
        if not d:
            continue
        out.append({
            "code": code,
            "price": d.get("price", 0),
            "close_yesterday": d.get("prev_close", 0),
            "open": d.get("open", 0),
            "high": d.get("high", 0),
            "low": d.get("low", 0),
            "volume": d.get("volume", 0),
            "turnover": d.get("turnover", 0),
            "change_pct": d.get("change_pct", 0),
        })

    return out


if __name__ == "__main__":
    # 简单自测（联网）
    print(sina_get_quotes(["sh600000", "sz000001"]))
