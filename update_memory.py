with open(r'C:\Users\china\.qclaw\workspace\MEMORY.md', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find start
start = next(i for i, l in enumerate(lines) if 'TradingAgents-CN' in l and '\u91cf\u5316\u9009\u80a1' in l)

# Find end (next top-level heading)
end = len(lines)
for i in range(start + 1, len(lines)):
    if lines[i].startswith('# ') and '\u552e\u79d1\u79d1\u6280' in lines[i]:
        end = i
        break

new_section = """# TradingAgents-CN 量化选股系统 (2026-04-04 创建，v3 最新)

基于周线CCI从-100突破/5周内穿0轴 + 股价站上20/30周均线的八维评分方案

### v3 最终策略（2026-04-04 晚确认）
- 周线CCI核心买点A：CCI从-100下方向上突破 -> 30分
- 周线CCI核心买点B：**5周内穿越0轴**（由负转正），当前在-20~+50震荡 -> 28分
- 周线CCI穿轴走强：5周内穿轴后当前在50~100 -> 20分
- 日线均线：MA5上穿MA20+MA10双金叉 -> 25分

### 八维评分体系（满分110分）
| 维度 | 满分 | 核心信号 |
|------|------|---------|
| ⑧ 周线趋势过滤 | 10+过滤 | WMA20/30走平/上行+股价<=WMA20x1.05（一票否决） |
| ① CCI周线状态 | 30 | 从-100突破（30）或5周内穿0轴（28） |
| ② 均线金叉 | 25 | MA5上穿MA20+MA10双金叉 |
| ③ 量价配合 | 20 | 量价齐升最佳 |
| ④ 趋势结构 | 15 | 创20/60日新高 |
| ⑤ 基本面加分 | 10 | 净利润/ROE/负债率 |
| ⑥ 相对强度 | 10 | 大盘对比 |
| ⑦ 买入时机 | 10 | 回踩均线支撑 |

### 目录
`TradingAgents-CN/`（已Git提交 v3）
- `score_cci_ma_crossover.py` - 主评分脚本
- `STRATEGY.md` - 完整策略文档
- `run_scan.bat` - Windows执行脚本

### 评级等级
- 85-100分 S级 重仓（需同时通过周线过滤）
- 70-84分  A级 积极买入
- 55-69分  B级 轻仓试探
- 40-54分  C级 观望

### 实测效果（2026-04-04）
- 50只扫描：31只被周线过滤淘汰，7只通过
- S级标的：海王生物(000078)、盐田港(000088)
- 周线过滤：股价>WMA20或>WMA30 + 两线走平/上行 + 乖离<=5%

"""

new_lines = lines[:start] + [new_section] + lines[end:]
with open(r'C:\Users\china\.qclaw\workspace\MEMORY.md', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print('OK')
