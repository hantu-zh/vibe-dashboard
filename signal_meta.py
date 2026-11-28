# -*- coding: utf-8 -*-
"""signal_meta.py — 信号 JSON 统一时效标记（写入侧）

只做一件事：在信号 dict 落盘前追加一个 _meta 段，让所有信号文件的
"什么时候生成的 / 数据截止到哪天"有唯一、可机读的位置。

    "_meta": {
        "generated_at": "2026-11-28 17:55:00",   # 脚本实际写出时刻（闸门判定用）
        "asof": "2026-11-28",                     # 数据真实截止交易日（诊断用，缺省）
        "source": "eastmoney"                     # 数据源标识（可选）
    }

用法（各信号脚本只需 1 行 import + 1 行调用）：
    from signal_meta import stamp
    out = stamp(out, source="eastmoney", asof=data_date)
    json.dump(out, ...)

设计原则：
  - 零依赖：只用标准库，任何脚本 import 都安全
  - 幂等：重复调用只刷新 generated_at，不破坏已有 _meta 的其他键
  - 不覆盖业务字段：只动 _meta，绝不碰 out 里已有的 date/generated_at 等
  - 不抛异常：任何异常都原样返回 out，绝不因为打标失败让信号脚本崩掉

注意：generated_at 用本机时间（上海时区）；CI 与本机均按北京时间运行。
"""
import datetime

_SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))


def stamp(data, source=None, asof=None):
    """给信号 dict 追加/刷新 _meta，返回同一个 dict（原地修改）。

    data   : 待落盘的信号 dict；非 dict 时原样返回
    source : 可选，数据源标识字符串
    asof   : 可选，数据真实截止交易日 YYYY-MM-DD
    """
    try:
        if not isinstance(data, dict):
            return data
        now = datetime.datetime.now(_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")
        meta = data.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
        meta["generated_at"] = now
        if source is not None:
            meta["source"] = source
        if asof is not None:
            meta["asof"] = asof
        data["_meta"] = meta
    except Exception:
        pass
    return data


if __name__ == "__main__":
    import json
    demo = stamp({"date": "2026-11-28", "items": []}, source="demo", asof="2026-11-27")
    print(json.dumps(demo, ensure_ascii=False, indent=2))
