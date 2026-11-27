#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日短评卡 · 数据提取器

从 vibe-dashboard 线上数据源抓取 ETF 资金观测台 + 跨资产共振的最新读数，
计算关键派生量，输出口径化的 digest.json 与人可读的 digest.md。
文案生成端只读 digest，不直接解析原始大 JSON，避免口径读错。

用法: python fetch_brief_data.py
输出: 同目录 digest.json / digest.md
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://hantu-zh.github.io/vibe-dashboard/"
SOURCES = {
    "etf_snapshot": "etf/data/snapshot.json",
    "etf_signal": "etf/data/signal.json",
    "regime": "regime_data.json",
    "ai": "ai_analysis_data.json",
}
HERE = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def yi(v):
    """元 -> 亿元，保留 1 位"""
    return round((v or 0) / 1e8, 1)


def pct(v):
    return None if v is None else round(v * 100, 2)


def trailing_count(arr, predicate):
    n = 0
    for v in reversed(arr):
        if v is not None and predicate(v):
            n += 1
        else:
            break
    return n


def last_cross(arr, dates, hi=2.5):
    """最近一次上穿 hi 档的日期与指数位置"""
    for i in range(len(arr) - 1, 0, -1):
        if arr[i] is not None and arr[i - 1] is not None and arr[i] >= hi and arr[i - 1] < hi:
            return {"date": dates[i], "index": i, "bars_ago": len(arr) - 1 - i}
    return None


def build():
    raw = {k: fetch(p) for k, p in SOURCES.items()}
    snap, sig, reg, ai = (raw[k] for k in ("etf_snapshot", "etf_signal", "regime", "ai"))

    m = snap.get("market", {})
    now = sig.get("now", {})
    rn = reg.get("now", {})
    meta = reg.get("meta", {})
    sb = (sig.get("stats") or {}).get("bottom", {})
    sp = (sig.get("stats") or {}).get("top", {})
    dates, rr = reg.get("dates", []), reg.get("regime", [])
    hs = reg.get("hs300", [])
    cont = trailing_count(rr, lambda v: v >= 2.5)

    today = datetime.now(CST).date()
    etf_date = datetime.strptime(snap["trade_date"], "%Y-%m-%d").date()
    sig_date = datetime.strptime(now["date"], "%Y-%m-%d").date()
    reg_date = datetime.strptime(meta["latest"], "%Y-%m-%d").date()
    # 简单新鲜度判断：周末/节假日顺延属正常，超过 4 自然日标记 stale
    stale = {}
    for k, d in (("etf_snapshot", etf_date), ("etf_signal", sig_date), ("regime", reg_date)):
        stale[k] = (today - d).days > 4

    etf = {
        "trade_date": snap.get("trade_date"),
        "generated_at": snap.get("generated_at"),
        "core_count": snap.get("core_count"),
        "fund_count": snap.get("fund_count"),
        "core_amount_yi": yi(m.get("core_amount")),
        "ma20_yi": yi(m.get("ma20")),
        "ma60_yi": yi(m.get("ma60")),
        "ratio20": round(m.get("ratio20") or 0, 3),
        "ratio60": round(m.get("ratio60") or 0, 3),
        "total_flow_yi": yi(m.get("total_flow")),
        "up_count": m.get("up_count"),
        "inflow_cats": m.get("inflow_cats"),
        "signal": {
            "date": now.get("date"),
            "generated_at": sig.get("generated_at"),
            "def": sig.get("def"),
            "caliber": sig.get("caliber"),
            "ratio": now.get("ratio"),
            "pr": now.get("pr"),
            "pidx": now.get("pidx"),
            "flow20_yi": now.get("flow20"),
            "state": now.get("state"),
            "light": now.get("light"),
            "zone": now.get("zone"),
            "invalid": now.get("invalid"),
        },
        "levels": sig.get("levels"),
        "stats_bottom": {k: sb.get(k) for k in ("n", "ndays", "t20m", "t20w", "t60m", "t60w", "t120m", "t120w", "md60m")},
        "stats_top": {k: sp.get(k) for k in ("n", "ndays", "t20m", "t20w", "t60m", "t60w", "t120m", "t120w", "md60m")},
        "top_inflow_funds": [
            {"name": x.get("name"), "flow_yi": yi(x.get("flow")), "pct": x.get("pct")}
            for x in sorted((snap.get("radar") or []), key=lambda x: -(x.get("flow") or 0))[:3]
        ],
    }
    regime = {
        "latest": meta.get("latest"),
        "generated": meta.get("generated"),
        "regime": rn.get("regime"),
        "up": rn.get("up"),
        "down": rn.get("down"),
        "level": rn.get("level"),
        "hint": rn.get("hint"),
        "consecutive_ge_2_5_days": cont,
        "consecutive_start": dates[len(dates) - cont] if cont and cont <= len(dates) else None,
        "last_cross_up_2_5": last_cross(rr, dates),
        "hs300_latest": {"date": dates[-1] if dates else None, "close": hs[-1] if hs else None},
        "event_count": len(reg.get("events", [])),
        "events_last3": reg.get("events", [])[-3:],
        "stale_meta": meta.get("stale"),
    }
    sent = ai.get("sentiment", {})
    ai_brief = {
        "timestamp": ai.get("timestamp"),
        "sentiment_value": sent.get("value"),
        "sentiment_date": sent.get("date"),
        "sentiment_complete": sent.get("complete"),
        "note": sent.get("note"),
    }

    digest = {
        "fetched_at": datetime.now(CST).isoformat(timespec="seconds"),
        "stale_flags": stale,
        "etf": etf,
        "regime": regime,
        "ai": ai_brief,
        "source_urls": SOURCES,
    }
    (HERE / "digest.json").write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 短评数据 digest · {digest['fetched_at']}",
        "",
        f"新鲜度: {stale}  (任一 true 则不得当作当日数据出稿)",
        "",
        "## ETF 资金观测台",
        f"- 交易日 {etf['trade_date']} | 核心池成交 {etf['core_amount_yi']} 亿 / 20日均量 {etf['ma20_yi']} 亿 = {etf['ratio20']}× / 60日 {etf['ratio60']}×",
        f"- 全池净申购 {etf['total_flow_yi']} 亿 | 核心池上涨 {etf['up_count']}/{etf['core_count']} | 流入类别 {etf['inflow_cats']}",
        f"- 信号 {sig_date}: state={etf['signal']['state']} light={etf['signal']['light']} | 流温度 {etf['signal']['ratio']}% (历史 {etf['signal']['pr']} 分位) | 指数120日 {etf['signal']['pidx']} 分位",
        f"- 20日累计净申赎 {etf['signal']['flow20_yi']} 亿 | 连续区段 {etf['signal']['zone']} | 失效锚 {etf['signal']['invalid']}",
        f"- 底部样本 {sb.get('n')} 段: 20日中位 {sb.get('t20m')}% 胜率 {sb.get('t20w')}% | 60日中位 {sb.get('t60m')}% | 120日中位 {sb.get('t120m')}%",
        f"- 主力吸金: {[(x['name'], x['flow_yi']) for x in etf['top_inflow_funds']]}",
        "",
        "## 跨资产共振",
        f"- 最新 {regime['latest']} | 共振 {regime['regime']} 档 | up={regime['up']} down={regime['down']} | {regime['level']}",
        f"- 连续 >=2.5 档 {regime['consecutive_ge_2_5_days']} 日起于 {regime['consecutive_start']} | 最近上穿 {regime['last_cross_up_2_5']}",
        f"- 沪深300 {regime['hs300_latest']} | 事件总数 {regime['event_count']}",
        f"- 情绪 {ai_brief['sentiment_value']} ({ai_brief['sentiment_date']}, complete={ai_brief['sentiment_complete']})",
        "",
        "## 口径提醒（写文案前必读）",
        "- ETF 底部承接 = 资金底信号，不是买点；60日中位为负说明「跌不动」≠「要涨」。",
        "- ETF 确认信号 = 缩量后重新放量上行；失效 = 跌破近月低点且单日净赎回放大。",
        "- 成交额(倒手) ≠ 净申购(真进钱)，两者背离是题眼。",
        "- 跨资产只做状态识别、不预测涨跌；风险释放 ≠ 减仓指令。",
        "- sentiment.complete=false 时不得把情绪值当正式收盘值引用。",
        "- 红涨绿跌；画面/文字零网址零来源名；保底免责声明。",
    ]
    (HERE / "digest.md").write_text("\n".join(lines), encoding="utf-8")
    print("OK ->", HERE / "digest.json")
    print("OK ->", HERE / "digest.md")
    print("\n".join(lines))


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print(f"FETCH FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
