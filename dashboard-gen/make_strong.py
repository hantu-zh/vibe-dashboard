#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「盗火线」选股结果页 strong.html。
数据源：similar.json（Top25 打分结果） + samples.csv（判断曾连板/新面孔）
列顺序（用户指定）：代码 名称 相似度 市值(亿) 前20日收益 价格 振幅 量比
                   20日回撤 距区间高 最后3日 均换手% 备注

注意：本脚本仅用标准库，可直接在 GitHub Actions 运行（原依赖本机 dashboard-gen 目录，
现已改为 __file__ 相对路径）。
"""
import json, os, csv

WS = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(WS, "similar.json")
SAMPLES = os.path.join(WS, "samples.csv")
OUT = os.path.join(WS, "..", "strong.html")


def load_sample_codes():
    codes = set()
    if not os.path.exists(SAMPLES):
        return codes
    with open(SAMPLES, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("code"):
                codes.add(row["code"])
    return codes


def pct(v, nd=1):
    return f"{v*100:.{nd}f}%"


def num(v, nd=2):
    return f"{v:.{nd}f}"


def cls(v):
    """A股红涨绿跌"""
    if v > 0:
        return "up"
    if v < 0:
        return "dn"
    return ""


def main():
    sim = json.load(open(SIM, encoding="utf-8"))
    sample_codes = load_sample_codes()
    rows = []
    for r in sim:
        g = lambda k: r.get(k)
        old = r["code"] in sample_codes
        rows.append(f"""<tr>
<td class="mono">{r['code']}</td>
<td class="nm">{r['name']}</td>
<td class="score">{num(g('score'),1)}</td>
<td>{num(g('mcap_yi'),1)}</td>
<td class="{cls(g('pre_ret20'))}">{pct(g('pre_ret20'))}</td>
<td class="mono">{num(g('price'))}</td>
<td>{num(g('pre_amp'),3)}</td>
<td>{num(g('vol_ratio_l5_f10'),2)}</td>
<td>{pct(g('max_dd'))}</td>
<td class="{cls(g('dist_from_high'))}">{pct(g('dist_from_high'))}</td>
<td class="{cls(g('last3_ret'))}">{pct(g('last3_ret'))}</td>
<td>{num(g('exch_avg'),2)}</td>
<td class="{'tag-old' if old else 'tag-new'}">{'曾连板' if old else '新面孔'}</td>
</tr>""")
    body = "\n".join(rows)
    n_new = sum(1 for r in sim if r["code"] not in sample_codes)
    n_old = len(sim) - n_new
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>盗火线 · 连板潜力股筛选</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     color:#1f2330;background:#f5f6f8}}
.wrap{{max-width:1180px;margin:0 auto;padding:22px 16px 56px}}
h1{{font-size:26px;margin:0 0 4px;letter-spacing:2px}}
.sub{{color:#8a90a0;font-size:13px;margin-bottom:16px}}
.rule{{background:#fff;border:1px solid #e6e8ee;border-left:4px solid #e23b3b;
      border-radius:8px;padding:12px 16px;font-size:13px;line-height:1.9;margin-bottom:14px}}
.rule b{{color:#e23b3b}}
.stat{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}}
.stat div{{background:#fff;border:1px solid #e6e8ee;border-radius:8px;padding:8px 14px;font-size:13px}}
.stat b{{font-size:16px;color:#e23b3b}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#fff;
      border:1px solid #e6e8ee;border-radius:8px;overflow:hidden}}
th,td{{border-bottom:1px solid #eef0f4;padding:8px 6px;text-align:center;white-space:nowrap}}
th{{background:#f0f2f6;font-weight:600;font-size:12.5px;color:#4a5160}}
tbody tr:hover{{background:#fafbfc}}
.mono{{font-family:ui-monospace,Consolas,monospace;color:#5a6270}}
.nm{{font-weight:600;text-align:left;padding-left:10px}}
.score{{font-weight:700;color:#e23b3b}}
.up{{color:#e23b3b}}
.dn{{color:#1f9d55}}
.tag-old{{color:#c2410c;background:#fff1e6;font-weight:600}}
.tag-new{{color:#15803d;background:#eafaf0;font-weight:600}}
.note{{font-size:12.5px;color:#8a90a0;line-height:1.8;margin-top:10px}}
.disc{{background:#fff8f0;border:1px solid #f3d9a8;border-radius:8px;padding:12px 16px;
      font-size:12.5px;color:#7a5a1a;line-height:1.8;margin-top:16px}}
@media(max-width:900px){{.wrap{{padding:14px 8px}}table{{font-size:11.5px}}th,td{{padding:6px 3px}}}}
</style></head><body><div class="wrap">
<h1>盗 火 线</h1>
<div class="sub">连板潜力股筛选 · 基于「启动前20个交易日」实测形态画像 · 生成于 {today}</div>

<div class="rule">
<b>筛选口径：</b>小市值 20~120亿 · <b>剔除 ST / *ST / 退市股</b> · <b>剔除股价 &gt; 30 元</b> ·
剔除当日已涨停（视为已启动）· 换手率 &gt; 0.8%<br>
<b>打分维度（来自 48 段 ≥5 连板样本实测）：</b>小市值 + 前期弱势未拉升 + 处于区间中低位 +
量能温和回升 + 振幅相当 + 已有回撤 + 波动温和。<br>
<b>画像要点：</b>真实形态是「小市值 + 超跌/弱势 + 量能先行回暖」的<b>低位反转</b>，
不是「缩量窄幅横盘后突破」。
</div>

<div class="stat">
  <div>候选总数 <b>{len(sim)}</b></div>
  <div>新面孔 <b>{n_new}</b></div>
  <div>曾连板 <b>{n_old}</b></div>
</div>

<table>
<thead><tr>
<th>代码</th><th>名称</th><th>相似度</th><th>市值(亿)</th><th>前20日收益</th><th>价格</th>
<th>振幅</th><th>量比</th><th>20日回撤</th><th>距区间高</th><th>最后3日</th><th>均换手%</th><th>备注</th>
</tr></thead>
<tbody>
{body}
</tbody></table>

<div class="note">
<b>列含义：</b>相似度=七维加权得分(0~100)；前20日收益=最近20个交易日累计涨跌；
振幅=(区间最高-最低)/均价，衡量横盘紧密度；量比=后5日均量/前10日均量(&gt;1为量能回升)；
20日回撤=窗口内最大回撤；距区间高=当前价相对20日最高价的位置；最后3日=启动前最后3日涨跌；均换手=窗口内日均换手率。<br>
<b>备注：</b>「曾连板」= 样本窗口内已出现过 ≥5 板，属二次蓄势、已完成一轮情绪释放；
「新面孔」= 窗口内未出现过 ≥5 板，形态首次接近画像。<br>
<b>数据限制：</b>画像基于 2026-05-04 起约 4.5 个月的样本（连板历史接口更早数据不可得），
不含 1–4 月；市场风格漂移会削弱画像有效性，建议每月重算画像。
</div>

<div class="disc">
<b>免责声明：</b>以上内容基于公开数据和量化分析，仅供参考，不构成投资建议。市场有风险，投资需谨慎。
任何投资决策应结合个人风险承受能力、资金状况和投资目标独立判断，必要时咨询持牌专业机构。过往表现不预示未来收益。
</div>
</div></body></html>'''
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"strong.html 已生成｜候选 {len(sim)} 条（新面孔 {n_new} / 曾连板 {n_old}）")


if __name__ == "__main__":
    main()
