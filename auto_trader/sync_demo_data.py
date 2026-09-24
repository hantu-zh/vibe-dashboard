# -*- coding: utf-8 -*-
"""用真实引擎账本(trader_state.json + trader_snapshot.json)重写 trader.html 内嵌 DATA 演示块。
幂等：重复运行结果一致。可独立运行，也可被 daily_run.py import 复用。
"""
import json, os, re

def build_data(state, snap):
    """从账本状态+快照生成 trader.html 的 DATA 结构（真实回放）。"""
    start = float(state.get("start_capital") or 150000.0)
    hist = state.get("history") or []
    # 按日期分组（同日取最后一个点）
    by_day = {}
    for h in hist:
        d = str(h.get("ts", ""))[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            by_day[d] = float(h.get("equity") or 0)
    dates = sorted(by_day)
    trades = state.get("trades") or []
    regime = snap.get("regime") or "NEUTRAL"
    detail = snap.get("regime_detail") or ""
    days = []
    prev = start
    for d in dates:
        eq = round(by_day[d], 2)
        day_trades = [t for t in trades if str(t.get("date")) == d]
        is_last = (d == dates[-1])
        days.append({
            "date": d, "equity": eq,
            "cash": round(float(snap.get("cash") or 0), 2) if is_last else None,
            "pos_value": round(float(snap.get("exposure") or 0), 2) if is_last else None,
            "daily_pnl": round(eq - prev, 2),
            "regime": regime, "regime_detail": detail,
            "trades": day_trades,
            "positions": (snap.get("positions") or []) if is_last else [],
        })
        prev = eq
    return {
        "start_capital": start,
        "days": days,
        "allocations": snap.get("allocations") or {},
        "accounts": snap.get("accounts") or [],
        "adaptive": snap.get("adaptive") or {},
    }

def sync_trader_html(root, state, snap):
    """重写 trader.html 的 const DATA = {...}; 块。返回是否修改。"""
    p = os.path.join(root, "trader.html")
    if not os.path.exists(p):
        print("  · trader.html 不存在，跳过演示块同步")
        return False
    with open(p, "r", encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"const DATA = (\{.*?\});\nconst LIVE", html, re.S)
    if not m:
        print("  ! trader.html 未找到 DATA 块锚点，跳过")
        return False
    new_block = "const DATA = " + json.dumps(build_data(state, snap),
                                            ensure_ascii=False, separators=(",", ":")) + ";\nconst LIVE"
    html2 = html[:m.start()] + new_block + html[m.end():]
    if html2 == html:
        return False
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(html2)
    print("  · 已用真实账本重写 trader.html 演示块")
    return True

def verify_demo_equity(root, expected_equity, tol=0.5):
    """三态一致性断言：trader.html 内嵌 DATA 末日权益必须等于实时快照权益。
    不一致说明演示块锚点漂移 / 同步失败，必须报错让 daily_run 非零退出。"""
    p = os.path.join(root, "trader.html")
    if not os.path.exists(p):
        return  # 不存在则跳过校验（sync_trader_html 已打印提示）
    with open(p, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"const DATA = (\{.*?\});\nconst LIVE", html, re.S)
    if not m:
        raise AssertionError("trader.html 未找到 DATA 锚点，无法校验三态一致性")
    data = json.loads(m.group(1))
    days = data.get("days") or []
    if not days:
        raise AssertionError("trader.html DATA.days 为空，无法校验三态一致性")
    last_eq = float(days[-1].get("equity") or 0)
    if abs(last_eq - float(expected_equity)) > tol:
        raise AssertionError(
            "三态一致性破缺: trader.html 末日权益 %.2f != 快照权益 %.2f (差 %.2f)"
            % (last_eq, float(expected_equity), last_eq - float(expected_equity)))
    return True

if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    st = json.load(open(os.path.join(root, "trader_state.json"), encoding="utf-8"))
    sp = json.load(open(os.path.join(root, "trader_snapshot.json"), encoding="utf-8"))
    sync_trader_html(root, st, sp)
