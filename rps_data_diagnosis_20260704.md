# RPS强弱快照数据诊断报告

**诊断时间**: 2026-07-04 15:46 (周六)
**问题**: Dashboard 显示"RPS强弱快照 没有数据"

---

## ✅ 数据完整性检查

### 1. Cron 任务执行状态

| 时间 | 任务 | 状态 | 结果 |
|------|------|------|------|
| 2026-07-03 10:00 | RPS早盘强弱快照 | ✅ ok | 30个板块已保存 |
| 2026-07-03 15:30 | RPS收盘强弱快照 | ✅ ok | 52秒执行完成 |
| 2026-07-04 15:30 | RPS收盘强弱快照 | ⚠️ ok | 周六非交易日，数据可能为空 |

### 2. daily_picks.json 数据检查

```bash
文件路径: vibe-dashboard/daily_picks.json
文件大小: 159,756 bytes
最后修改: 2026-07-03 12:03:31 (昨天中午)

数据结构:
- sector_rankings: ✅ 存在
  - 2026-07-03: ✅ 有数据
  - 2026-07-02: ✅ 有数据
  - 2026-07-01: ✅ 有数据

- 2026-07-03 任务: ✅ 8个任务
  - 市场深度解读
  - 大力水手菠菜涨停战法
  - 杰克船长
  - 季度环比增长
  - 高欣资金净流入
  - 追涨强势股
  - 高欣美股
  - 船长钓鱼战法

- RPS强弱快照: ❌ 不在任务列表中
  (RPS数据保存在 sector_rankings 字段，而非独立任务)
```

### 3. Dashboard 前端日志

```javascript
[VIBE] Looking for date: 2026-07-04
[VIBE] Loaded embedded, top keys: Array(16) | hasToday: false
[VIBE] Normalized: bestDate=2026-07-03, tasks=12
[VIBE] All date keys: Array(16)
```

**分析**:
- Dashboard 正确识别 `bestDate=2026-07-03` ✅
- 今天周六，期望使用昨天的数据 ✅
- 但"RPS强弱快照"任务未显示在任务列表中

---

## 🔍 问题根源

### 关键发现

**RPS 数据的保存位置与 Dashboard 期望不一致**:

| 数据类型 | 保存位置 | Dashboard 期望 |
|---------|---------|---------------|
| 选股任务 | `daily_picks['2026-07-03']['任务名']` | ✅ 正确读取 |
| RPS 板块排名 | `daily_picks['sector_rankings']['2026-07-03']` | ⚠️ 需单独处理 |

### RPS 数据结构

```json
{
  "sector_rankings": {
    "2026-07-03": [
      {"rank": 1, "name": "黄金", "change_pct": 8.21, "rps": 99, ...},
      {"rank": 2, "name": "航运", "change_pct": 5.32, "rps": 96, ...}
    ]
  },
  "2026-07-03": {
    "市场深度解读": {...},
    "追涨强势股": {...}
  }
}
```

---

## 🐛 发现的问题

### 1. RPS 脚本缺少交易日检测 ⚠️

**现象**: 周六 15:30 仍执行脚本，获取空数据

**影响**:
- 可能覆盖 `sector_rankings` 字段
- 浪费 API 调用额度

**建议**: 添加交易日检测逻辑

### 2. daily_picks.json 未更新 ⚠️

**现象**: 最后修改时间 `2026-07-03 12:03:31`（昨天中午）

**分析**:
- 昨天 15:30 的收盘任务可能未成功保存数据
- 或者今天 15:30 的执行未写入文件

### 3. Dashboard 前端可能未正确读取 sector_rankings ⚠️

**现象**: Dashboard 显示"RPS强弱快照 没有数据"

**可能原因**:
- `strong.html` 或 RPS 页面期望 `sector_rankings` 在特定位置
- 前端 JavaScript 未正确解析嵌套结构

---

## 🔧 解决方案

### 方案 1: 为 RPS 脚本添加交易日检测

```python
import chinese_calendar

def is_trading_day():
    """检查是否为交易日（工作日且非节假日）"""
    today = datetime.date.today()
    # 周末
    if today.weekday() >= 5:
        return False
    # 法定节假日
    if not chinese_calendar.is_workday(today):
        return False
    return True

if not is_trading_day():
    print("[INFO] 非交易日，跳过执行")
    sys.exit(0)
```

### 方案 2: 检查 Dashboard 前端代码

需要检查以下文件:
- `vibe-dashboard/index.html` (行 4600-4900)
- `vibe-dashboard/strong.html`

确保正确读取:
```javascript
const rpsData = data.sector_rankings?.[targetDate];
```

### 方案 3: 手动触发 RPS 脚本（测试）

```bash
cd C:\Users\china\.qclaw\workspace
python RPS_thermal_dingtalk.py close
```

---

## 📌 结论

**数据存在，但显示逻辑可能有问题**

- ✅ Cron 任务正常执行
- ✅ RPS 数据已保存到 `sector_rankings`
- ⚠️ Dashboard 可能未正确读取 `sector_rankings` 字段
- ⚠️ RPS 脚本缺少交易日检测（非关键）

**下一步**:
1. 检查 `strong.html` 或 RPS 页面的 JavaScript
2. 确认前端如何读取 `sector_rankings` 数据
3. (可选) 为 RPS 脚本添加交易日检测
