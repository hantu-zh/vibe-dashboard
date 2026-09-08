# -*- coding: utf-8 -*-
"""每日选股结果存储模块（新格式 v2）
新格式: { "2026-06-17": { "追涨强势股": {"time": "10:05", "picks": [...]} } }
旧格式: { "picks": [{ "date": "...", "strategy": "...", "stocks": [...] }] }  <-- 废弃
"""
import json
from pathlib import Path
from datetime import datetime
import os
import sys

# 路径解析：去掉原 Qclaw 云端 Windows 硬编码，改为基于仓库根目录
# （与 paths.py / sync_func.py 保持一致，Linux / GitHub Actions 可运行）
try:
    import paths
except ImportError:  # 兜底：paths.py 不在同目录时自行推导
    _WS = os.environ.get("VIBE_WS", os.path.dirname(os.path.abspath(__file__)))
    class _P:
        @staticmethod
        def w(rel):
            parts = rel.replace("\\", "/").split("/")
            base = os.path.join(_WS, "vibe-dashboard") if parts and parts[0] == "vibe-dashboard" else _WS
            sub = parts[1:] if parts and parts[0] == "vibe-dashboard" else parts
            return os.path.join(base, *sub) if sub else base
    paths = _P()

# 路径：daily_picks.json 位于仓库根目录（Dashboard 读取此文件）
DATA_FILE = Path(paths.w(r'daily_picks.json'))
# 旧路径（兼容读取；仓库根目录为唯一权威位置，旧文件不存在时自动跳过）
OLD_DATA_FILE = Path(paths.w(r'daily_picks_legacy.json'))

def _load_data():
    """加载数据，合并两个来源（新格式优先）"""
    data = {}
    # 1. 读取 daily_picks.json（仓库根目录，权威主文件）
    if DATA_FILE.exists():
        try:
            data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
            if any(k.startswith("202") for k in data.keys()):
                pass  # 有日期key，继续
            else:
                data = {}  # 旧格式，重置
        except:
            data = {}
    # 2. 合并 legacy 源（daily_picks_legacy.json）中的额外日期（不存在时跳过）
    if OLD_DATA_FILE.exists():
        try:
            old = json.loads(OLD_DATA_FILE.read_text(encoding="utf-8"))
            if "picks" in old:
                old = _migrate_old_format(old)
            for k, v in old.items():
                if k.startswith("202") and k not in data:
                    data[k] = v
                elif k == "sector_rankings" and "sector_rankings" not in data:
                    data["sector_rankings"] = v
        except:
            pass
    return data

def _migrate_old_format(old_data):
    """将旧格式转换为新格式"""
    new_data = {}
    for rec in old_data.get("picks", []):
        date = rec.get("date", "")
        strategy = rec.get("strategy", "")
        stocks = rec.get("stocks", [])
        if not date or not strategy:
            continue
        if date not in new_data:
            new_data[date] = {}
        new_data[date][strategy] = {
            "time": rec.get("timestamp", "").split(" ")[-1][:5] or "--:--",
            "picks": stocks
        }
    # 保留 sector_rankings
    if "sector_rankings" in old_data:
        new_data["sector_rankings"] = old_data["sector_rankings"]
    return new_data

def save_daily_picks(strategy_name, stocks, task_time=None, data_date=None):
    """保存每日选股结果（新格式）
    Args:
        strategy_name: 策略名称，如 "追涨强势股"
        stocks: 股票列表
        task_time: 可选，执行时间字符串如 "10:05"，默认当前时间
        data_date: 可选，数据日期 YYYY-MM-DD，默认今天
    """
    today = data_date or datetime.now().strftime("%Y-%m-%d")
    """保存每日选股结果（新格式）
    Args:
        strategy_name: 策略名称，如 "追涨强势股"
        stocks: 股票列表
        task_time: 可选，执行时间字符串如 "10:05"，默认当前时间
    """
    today = datetime.now().strftime("%Y-%m-%d")
    data = _load_data()

    if today not in data:
        data[today] = {}
    if task_time is None:
        task_time = datetime.now().strftime("%H:%M")
    
    # 检查是否需要支持多时段（追涨强势股）
    if strategy_name == "追涨强势股":
        # 为每个时段创建独立的key
        key = f"{strategy_name}_{task_time}"
        data[today][key] = {
            "time": task_time,
            "picks": stocks
        }
        # 同时保留一个合并版本（最新时段）
        data[today][strategy_name] = {
            "time": task_time,
            "picks": stocks
        }
    else:
        # 其他策略保持原逻辑（覆盖）
        data[today][strategy_name] = {
            "time": task_time,
            "picks": stocks
        }

    # 保留最近 N 天（原值 30 太激进：历史已有 34 天，新增一天就会把最老的
    # 5 个交易日连同其选股记录一起删掉，造成不可逆的历史数据丢失。放宽到 365 天）
    MAX_KEEP_DAYS = 365
    date_keys = sorted([k for k in data.keys() if k.startswith("202")], reverse=True)
    for old_key in date_keys[MAX_KEEP_DAYS:]:
        del data[old_key]

    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] saved {len(stocks)} stocks to {DATA_FILE}")
    return True

def save_sector_rankings(rankings):
    """保存板块排名"""
    today = datetime.now().strftime("%Y-%m-%d")
    data = _load_data()
    data["sector_rankings"] = {
        "date": today,
        "rankings": rankings,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] saved {len(rankings)} sector rankings")
    return True

def get_daily_picks(date=None, strategy_name=None):
    """读取选股结果（兼容新旧格式）"""
    data = _load_data()
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")
    if date not in data:
        return []
    if strategy_name:
        task = data[date].get(strategy_name)
        return task["picks"] if task else []
    # 返回当天所有策略
    return data[date]

def get_all_dates():
    """获取所有有数据的日期"""
    data = _load_data()
    return sorted([k for k in data.keys() if k.startswith("202")], reverse=True)
