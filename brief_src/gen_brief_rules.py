#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则文案生成器：读 digest.json -> 按状态规则生成 brief.json。

云端 GitHub Actions 没有大模型，用规则模板把当天数据填进方瑟风格的句子里。
覆盖 ETF 信号各状态与跨资产各档位，严守 风格模板与口径.md 的铁律。

用法: python gen_brief_rules.py
输出: 覆盖 brief.json
"""
import json
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
D = json.loads((HERE / "digest.json").read_text(encoding="utf-8"))
E, S, R = D["etf"], D["etf"]["signal"], D["regime"]
F = lambda v, n=1: f"{v:.{n}f}"


def sgn(v):
    if v is None:
        return "—"
    return ("+" if v >= 0 else "−") + f"{abs(v):.1f}%"


# ---------- ETF 段 ----------
def etf_section():
    state = S.get("state")
    ratio20 = E["ratio20"]
    flow20, pr, pidx = S["flow20_yi"], S["pr"], S["pidx"]
    sb = D["etf"]["stats_bottom"]
    lines = ["先看背景,ETF资金观测台。"]

    if state == "bottom":
        lines.append(f"20天净申购{flow20}亿,流温度{F(S['ratio'])}%、历史{pr}分位,指数却在120日的{pidx}分位。")
        if ratio20 < 0.9:
            lines.append(f"钱在跌里进场,但成交只有20日均量的{F(ratio20,2)}倍——有人接盘,没人抢筹。")
        elif ratio20 > 1.15:
            lines.append(f"钱在跌里进场,成交放大到20日均量的{F(ratio20,2)}倍,量能跟上了。")
        else:
            lines.append(f"钱在跌里进场,成交在20日均量的{F(ratio20,2)}倍附近,量能不温不火。")
        lines.append(f"历史{sb['n']}段同类信号,20日中位{sgn(sb['t20m'])}、胜率{sb['t20w']}%。")
        tail = "资金底,不是买点。"
        if ratio20 < 0.9:
            tail += "确认看放量上行,失效看跌破近月低点且赎回放大。"
        else:
            tail += "盯住后续量价能不能接力。"
        lines.append(tail)
        headline = "资金底亮灯，但不是买点"
        metrics = [
            {"k": "20 日净申购", "v": f"{flow20} 亿", "c": "up"},
            {"k": "历史分位", "v": f"{pr}%", "c": "up"},
            {"k": "核心池量比", "v": f"{F(ratio20,2)}×", "c": "down" if ratio20 < 1 else "up"},
        ]
    elif state == "top":
        sp = D["etf"]["stats_top"]
        lines.append(f"宽基资金持续净赎回,20日累计{flow20}亿,却处在历史{pr}分位的高位。")
        lines.append("钱在涨里撤退,这是顶部出逃区。")
        lines.append(f"历史上{sp['n']}段同类信号,60日胜率只有{sp['t60w']}%,三个周期中位全为负。")
        lines.append("资金边拉边撤,注意控制仓位,别在高位追。")
        headline = "资金边拉边撤，注意高位风险"
        metrics = [
            {"k": "20 日净申赎", "v": f"{flow20} 亿", "c": "down"},
            {"k": "历史分位", "v": f"{pr}%", "c": "down"},
            {"k": "指数分位", "v": f"{pidx}%", "c": "up"},
        ]
    elif state == "warm":
        lines.append(f"资金在进,历史{pr}分位,但指数不在{('低' if pidx < 50 else '相对低')}位。")
        lines.append("这类组合历史上不构成底部信号,只说明承接较强。")
        lines.append("跟随意义有限,先观察,别急着下结论。")
        headline = "流入偏多，但还不是底部信号"
        metrics = [
            {"k": "历史分位", "v": f"{pr}%", "c": "up"},
            {"k": "指数分位", "v": f"{pidx}%", "c": "flat"},
            {"k": "核心池量比", "v": f"{F(ratio20,2)}×", "c": "flat"},
        ]
    elif state == "cool":
        lines.append(f"资金在退,历史{pr}分位,而价格并不低。")
        lines.append("多出现在上涨后的整理期,观察是否升级为顶部出逃。")
        lines.append("先减一分急躁,盯住资金有没有继续流出。")
        headline = "流出偏空，观察是否升级"
        metrics = [
            {"k": "历史分位", "v": f"{pr}%", "c": "down"},
            {"k": "指数分位", "v": f"{pidx}%", "c": "up"},
            {"k": "核心池量比", "v": f"{F(ratio20,2)}×", "c": "flat"},
        ]
    else:  # neutral / unknown
        lines.append(f"资金流温度{F(S['ratio'])}%,处在历史{pr}分位,指数在120日{pidx}分位。")
        lines.append("既没有极端承接,也没有明显出逃,中性区不下结论。")
        lines.append("继续观察,等信号走到极端再动。")
        headline = "资金中性，等信号明确"
        metrics = [
            {"k": "流温度", "v": f"{F(S['ratio'])}%", "c": "flat"},
            {"k": "历史分位", "v": f"{pr}%", "c": "flat"},
            {"k": "核心池量比", "v": f"{F(ratio20,2)}×", "c": "flat"},
        ]
    return {"id": "etf", "label": "ETF 资金观测台", "headline": headline,
            "metrics": metrics, "lines": lines}


# ---------- 跨资产段 ----------
def regime_section():
    rg, up, down = R["regime"], R["up"], R["down"]
    lvl = (R.get("level") or "").replace("⚠️", "").strip()
    n = R["event_count"]
    lines = ["再看背景,跨资产共振。"]

    cross = R.get("last_cross_up_2_5") or {}
    cd = cross.get("date")
    close = R.get("hs300_latest", {}).get("close")
    cont = R.get("consecutive_ge_2_5_days", 0)

    if rg >= 2.5:
        cstr = f"{int(cd[5:7])}月{int(cd[8:10])}号" if cd else ""
        lines.append(f"海外{up}升{down}降,共振{rg}档" + (f",{cstr}上穿、连续{cont}天" if cstr else "") +
                     (f",沪深300{F(close,0)}点" if close else "") + f";状态是{lvl}。")
        lines.append("只做识别,不预测涨跌,也不是减仓指令。")
        headline = "海外波动齐升，先别急着抄底"
    elif rg >= 1.5:
        lines.append(f"共振处在{rg}档,方向有分歧,还没到齐升的风险确认。")
        lines.append(f"面板状态是{lvl}。仍以状态识别为主,不预测涨跌。")
        lines.append("关注档位会不会继续抬升,再决定要不要更谨慎。")
        headline = "海外波动分歧，关注档位变化"
    else:
        lines.append(f"共振强度只有{rg}档,海外波动整体平缓。")
        lines.append(f"面板状态是{lvl},风险信号不强。")
        lines.append("按状态管理,不必过度反应。")
        headline = "海外波动平缓，风险信号不强"

    metrics = [
        {"k": "共振强度", "v": f"{rg} / 3 档", "c": "up" if rg >= 2.5 else "flat"},
        {"k": "方向一致性", "v": f"{up} 升 {down} 降", "c": "up" if up > down else "down"},
        {"k": "连续", "v": f"{cont} 日", "c": "flat"},
    ]
    return {"id": "regime", "label": "跨资产共振", "headline": headline,
            "metrics": metrics, "lines": lines}


# ---------- 双确认段（周度，单独口径） ----------
def veto_section():
    V = D.get("veto") or {}
    if not V:
        return None
    last = V.get("last_week") or {}
    streak, n = V.get("fin_down_streak"), V.get("n_threshold") or 4
    hist = V.get("hist_veto_count")
    run = V.get("longest_both_run") or {}
    lines = ["重点看这张双确认图。蓝柱是融资周环比，绿柱是指数周环比；两根柱子同一周朝下，才算一次双确认。"]
    if hist is not None:
        tail = f"历史上{hist}次。"
    else:
        tail = ""
    if run.get("weeks"):
        tail += f"最近{run['start'][5:7]}月{run['start'][8:10]}号到{run['end'][5:7]}月{run['end'][8:10]}号连着{run['weeks']}周朝下，亮了{len(run.get('veto_weeks') or [])}次三角。"
    lines.append(f"连着{n}周双确认，否决才触发，{tail}")
    fin, idx = last.get("fin"), last.get("idx")
    if fin is not None and idx is not None:
        d = last.get("date", "")
        lines.append(f"到{d[8:10]}号这周，融资负{abs(fin):.2f}%、指数负{abs(idx):.2f}%，两柱又同向朝下。")
    if streak is not None:
        lines.append(f"可连降才{streak}周，离{n}周还差{n - streak}周，否决{'触发' if V.get('veto_triggered') else '没触发'}，防御档不动。"
                     + ("末周融资T+1未出，尾部不完整，等补齐再看。" if V.get("t1_missing") else ""))
    lines.append("只做纪律检查，不是买卖信号。")
    headline = "两柱一起朝下，但还差三周" if streak is not None and streak < n else "双确认达标，按纪律处理"
    metrics = [
        {"k": "融资周环比", "v": f"{fin}%" if fin is not None else "—", "c": "down" if (fin or 0) < 0 else "up"},
        {"k": "沪深300周环比", "v": f"{idx}%" if idx is not None else "—", "c": "down" if (idx or 0) < 0 else "up"},
        {"k": "连降计数", "v": f"{streak} / {n} 周", "c": "flat"},
    ]
    return {"id": "veto", "label": "融资 × 指数双确认", "headline": headline,
            "data_date": f"{V.get('last_fin')} / {V.get('last_idx')}",
            "metrics": metrics, "lines": lines}


def main():
    date = datetime.now().strftime("%Y-%m-%d")
    data_date = E["trade_date"]
    etf, rg = etf_section(), regime_section()
    vt = veto_section()
    brief = {
        "title": "每日市场语音简报",
        "date": date,
        "data_date": data_date,
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%",
        "sections": [
            {"id": "opening", "label": "开场", "headline": "今天讲两件事", "metrics": [],
             "lines": [
                 "欢迎收看今天的每日市场语音简报。",
                 f"数据截至{data_date[5:7]}月{data_date[8:10]}日收盘。今天重点讲双确认图,另两件事做背景。",
             ]},
            etf,
            *([vt] if vt else []),
            rg,
            {"id": "closing", "label": "结尾", "headline": "风险提示", "metrics": [],
             "lines": [
                 "以上就是今天的简报。",
                 "内容仅为状态识别与风险揭示,不构成投资建议。市场有风险,投资需谨慎。我们下期见。",
             ]},
        ],
    }
    n = sum(len(s["lines"]) for s in brief["sections"])
    chars = sum(len(l) for s in brief["sections"] for l in s["lines"])
    (HERE / "brief.json").write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK -> brief.json  ({n} 句 / {chars} 字)")
    print("ETF:", etf["headline"])
    print("跨资产:", rg["headline"])


if __name__ == "__main__":
    main()
