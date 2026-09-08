# trading_day.py - 交易日判断（供 GitHub Actions 调用）
# 返回 0 = 今天需要跑选股/同步任务；返回 1 = 非交易日，跳过
import sys, datetime

try:
    # chinese_calendar 含 A股法定节假日 + 周末
    from chinese_calendar import is_workday
    ok = is_workday(datetime.date.today())
except Exception:
    # 兜底：仅排除周六周日
    ok = datetime.date.today().weekday() < 5

sys.exit(0 if ok else 1)
