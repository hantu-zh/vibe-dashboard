#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块排名数据采集 - 慢热板块基础数据源（v4）
- 数据源：直接读取 RPS_thermal_dingtalk.py 生成的 rps.html
  中的 window.__RPS_EMBED__（包含真实涨跌幅 change_pct）
- 输出格式：dict {板块名: 排名}（统一通用名）
- 智能回填：每次运行自动检测并补充最近5个交易日的缺失数据
- 只写 vibe_trend_history.json
"""
import json, sys, os
from datetime import datetime, timedelta

# ─── 常量 ───────────────────────────────────────────────────────────────────
DASH_DIR   = r'C:\Users\china\.qclaw\workspace\vibe-dashboard'
TREND_PATH = os.path.join(DASH_DIR, 'vibe_trend_history.json')

# 2026年中国节假日
HOLIDAYS_2026 = {
    '2026-01-01','2026-01-02','2026-01-03',
    '2026-02-17','2026-02-18','2026-02-19','2026-02-20','2026-02-21','2026-02-22','2026-02-23',
    '2026-04-04','2026-04-05','2026-04-06',
    '2026-05-01','2026-05-02','2026-05-03','2026-05-04','2026-05-05',
    '2026-06-19',
    '2026-10-01','2026-10-02','2026-10-03','2026-10-04','2026-10-05','2026-10-06','2026-10-07','2026-10-08',
}

# 标准化板块名映射：RPS名称 → 标准名称
SECTOR_NORMALIZE = {
    '玻璃': '玻璃行业',
    '船舶': '船舶制造',
    '传媒': '传媒娱乐',
    '传媒娱乐': '传媒娱乐',
    '电力': '电力行业',
    '电器': '电器行业',
    '电子': '电子器件',
    '电子元件': '元件',
    '电子器件': '电子器件',
    '房地产': '房地产',
    '房产开发': '房地产',
    '纺织': '纺织行业',
    '服装': '服装鞋类',
    '钢铁': '钢铁行业',
    '公路': '公路铁路',
    '公用事业': '公用事业',
    '航海': '航运港口',
    '航空': '航空航天',
    '航天': '航空航天',
    '化工': '化工行业',
    '化纤': '化学纤维',
    '环保': '环保行业',
    '家电': '家电行业',
    '建材': '建材行业',
    '建筑': '建筑装饰',
    '交通': '交通运输',
    '教育': '教育传媒',
    '金融': '多元金融',
    '酒店': '旅游酒店',
    '军工': '军工',
    '开采': '能源开采',
    '科创': '科创板',
    '煤炭': '煤炭行业',
    '美容': '美容护理',
    '木材': '木材家具',
    '农牧': '农牧饲渔',
    '农业': '农牧饲渔',
    '汽车': '汽车行业',
    '轻工': '轻工制造',
    '燃气': '燃气水务',
    '商贸': '商业百货',
    '石化': '石油行业',
    '食品': '食品饮料',
    '输配电气': '输配电气',
    '水产': '农牧饲渔',
    '水泥': '建材行业',
    '水务': '燃气水务',
    '塑料': '塑料橡胶',
    '通信': '通信行业',
    '纺织': '纺织行业',
    '通用': '通用设备',
    '造纸': '造纸印刷',
    '医药': '医药行业',
    '银行': '银行',
    '有色': '有色金属',
    '园林': '环保行业',
    '造纸': '造纸印刷',
    '证券': '证券',
    '造纸印刷': '造纸印刷',
    '中药': '中药',
    '专用': '专用设备',
    '装修': '装修装饰',
    '资源': '资源行业',
    '综合': '综合行业',
    '半导体': '半导体',
    '互联网': '互联网服务',
    '软件': '软件开发',
    'IT': 'IT设备',
    '电子信息': '电子信息',
    '电子信息': '电子信息',
    '仪器仪表': '仪器仪表',
    '保险': '保险',
    '多元金融': '多元金融',
    '光伏': '光伏设备',
    '风电': '风电设备',
    '电池': '电池',
    '电网': '电网设备',
    '电机': '电机',
    '小金属': '小金属',
    '贵金属': '贵金属',
    '工业金属': '工业金属',
    '能源': '能源',
    '石油': '石油行业',
    '天然气': '石油行业',
    '化学制药': '化学制药',
    '生物制品': '生物制品',
    '医疗器械': '医疗器械',
    '医疗服务': '医疗服务',
    '医药商业': '医药商业',
    '中药': '中药',
    '饮料': '饮料乳品',
    '乳品': '饮料乳品',
    '调味品': '调味品',
    '旅游': '旅游酒店',
    '酒店': '旅游酒店',
    '航空机场': '航空机场',
    '港口': '航运港口',
    '航运': '航运港口',
    '高速': '公路铁路',
    '铁路': '公路铁路',
    '高速': '公路铁路',
    '银行': '银行',
    '光学光电子': '光学光电子',
    '光学': '光学光电子',
    '消费电子': '消费电子',
    'LED': 'LED',
    '橡胶': '塑料橡胶',
    '塑料': '塑料橡胶',
    '金属': '有色金属',
    '电脑': '计算机设备',
    '元件': '元件',
    'IT服务': 'IT服务',
    '软件开发': '软件开发',
    '计算机': '计算机设备',
    '通信设备': '通信设备',
    '通信服务': '通信服务',
    '化学原料': '化学原料',
    '化学制品': '化学制品',
    '化学纤维': '化学纤维',
    '石油加工': '石油加工',
    '油气开采': '油气开采',
    '房地产服务': '房地产服务',
    '装修装饰': '装修装饰',
    '医疗器械': '医疗器械',
    '生物制品': '生物制品',
    '化学制药': '化学制药',
    '中药': '中药',
    '医药商业': '医药商业',
    '汽车整车': '汽车整车',
    '汽车零部件': '汽车零部件',
    '汽车服务': '汽车服务',
    '电机': '电机',
    '电源设备': '电源设备',
    '电网设备': '电网设备',
    '风电设备': '风电设备',
    '光伏设备': '光伏设备',
    '电池': '电池',
    '小金属': '小金属',
    '工业金属': '工业金属',
    '贵金属': '贵金属',
    '燃气水务': '燃气水务',
    '造纸印刷': '造纸印刷',
    '轻工制造': '轻工制造',
    '珠宝': '珠宝',
    '珠宝首饰': '珠宝',
    '包装': '包装材料',
    '包装材料': '包装材料',
    '仪器仪表': '仪器仪表',
    '通用设备': '通用设备',
    '专用设备': '专用设备',
    '工程机械': '工程机械',
    '洛阳玻璃': '玻璃行业',
    '北新建材': '建材行业',
    '海螺水泥': '建材行业',
    '华新水泥': '建材行业',
    '塔牌集团': '建材行业',
    '祁连山': '建材行业',
    '宁夏建材': '建材行业',
    '青松建化': '建材行业',
    '金隅集团': '建材行业',
    '万年青': '建材行业',
    '上峰水泥': '建材行业',
    '博闻科技': '建材行业',
    '尖峰集团': '建材行业',
    '福建水泥': '建材行业',
    '浙江尖峰': '建材行业',
}

# 标准49行业板块名（用于过滤）
STANDARD_49 = {
    '玻璃行业', '船舶制造', '传媒娱乐', '电力行业', '电器行业',
    '电子器件', '电子信息', '房地产', '纺织行业', '服装鞋类',
    '钢铁行业', '公路铁路', '公用事业', '航空航天', '环保行业',
    '家电行业', '建材行业', '建筑装饰', '交通运输', '教育传媒',
    '银行', '航空航天', '军工', '酿酒行业', '煤炭行业',
    '美容护理', '木材家具', '农牧饲渔', '汽车行业', '轻工制造',
    '燃气水务', '商业百货', '石油行业', '食品饮料', '输配电气',
    '塑料橡胶', '通信设备', '通信服务', '文艺传媒', '医疗器械',
    '医药行业', '银行', '有色金属', '造纸印刷', '证券',
    '中药', '装修装饰', '综合行业', '互联网服务', '软件开发',
    'IT设备', 'IT服务', '半导体', '光伏设备', '风电设备',
    '电池', '电网设备', '电机', '化学原料', '化学制品',
    '化学纤维', '石油加工', '油气开采', '房地产服务', '光学光电子',
    '消费电子', 'LED', '汽车整车', '汽车零部件', '汽车服务',
    '工程机械', '仪器仪表', '通用设备', '专用设备', '珠宝',
    '包装材料', '生物制品', '化学制药', '医疗服务', '医药商业',
    '饮料乳品', '调味品', '旅游酒店', '航空机场', '航运港口',
    '小金属', '贵金属', '工业金属', '多元金融', '保险',
}


def is_holiday(d):
    return d in HOLIDAYS_2026


def is_trading_day(d):
    dt = datetime.strptime(d, '%Y-%m-%d')
    if dt.weekday() >= 5:
        return False
    if is_holiday(d):
        return False
    return True


def get_recent_trading_days(n=6):
    days = []
    d = datetime.now()
    while len(days) < n:
        ds = d.strftime('%Y-%m-%d')
        if is_trading_day(ds):
            days.append(ds)
        d -= timedelta(days=1)
    return days


def normalize_sector(name: str) -> str:
    """标准化板块名"""
    name = name.strip()
    # 直接命中
    if name in STANDARD_49:
        return name
    # 后缀行业
    if name + '行业' in STANDARD_49:
        return name + '行业'
    # 去除行业后缀
    if name.endswith('行业'):
        base = name[:-2]
        if base in STANDARD_49:
            return base
        # 查映射
        if base in SECTOR_NORMALIZE:
            return SECTOR_NORMALIZE[base]
        if name in SECTOR_NORMALIZE:
            return SECTOR_NORMALIZE[name]
        return name
    # 查映射
    if name in SECTOR_NORMALIZE:
        return SECTOR_NORMALIZE[name]
    return name


def parse_sector_rankings() -> dict:
    """
    从 daily_picks.json 的 sector_rankings 字段读取板块排名数据。
    返回: {日期: [(板块名, 排名, 涨跌幅), ...], ...}
    """
    # 读取两个位置的 daily_picks.json
    # 优先读 vibe-dashboard/daily_picks.json（历史数据更完整）
    candidates = [
        os.path.join(DASH_DIR, 'daily_picks.json'),                   # vibe-dashboard 子目录
        os.path.join(os.path.dirname(DASH_DIR), 'daily_picks.json'),  # workspace 根目录
    ]

    result = {}
    for fpath in candidates:
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                d = json.load(f)
            sector_rankings = d.get('sector_rankings', {})
            if sector_rankings:
                for date_str, entries in sector_rankings.items():
                    if not isinstance(entries, list) or date_str in result:
                        continue
                    # entries: [{rank, name, change_pct, rps, trend, strength}, ...]
                    result[date_str] = [
                        (e['name'], e.get('rank', 99), e.get('change_pct', 0))
                        for e in entries
                    ]
                print(f'[slowrise] Read {len(sector_rankings)} dates from {fpath}')
                break
        except Exception as e:
            print(f'[slowrise] Failed to read {fpath}: {e}')

    print(f'[slowrise] Parsed sector_rankings: {len(result)} dates, dates={sorted(result.keys(), reverse=True)[:8]}')
    return result


def build_rankings(entries) -> dict:
    """将 [(name, rank, change_pct)] 转为 {name: rank}"""
    rankings = {}
    for name, rank, change_pct in entries:
        std_name = normalize_sector(name)
        # 如果同名已存在，取排名更靠前的
        if std_name not in rankings or rank < rankings[std_name]:
            rankings[std_name] = rank
    return rankings


def is_old_format(entry):
    if isinstance(entry, list):
        return True
    if isinstance(entry, dict) and 'generated_at' in entry:
        return True
    return False


def main():
    TODAY = datetime.now().strftime('%Y-%m-%d')

    # ── 读取现有数据 ──
    trend = {}
    if os.path.exists(TREND_PATH):
        try:
            with open(TREND_PATH, 'r', encoding='utf-8') as f:
                trend = json.load(f)
            print(f'[slowrise] Loaded existing trend: {len(trend)} dates')
        except Exception as e:
            print(f'[slowrise] Load failed: {e}')

    # ── 清理旧格式 ──
    for d in list(trend.keys()):
        if is_old_format(trend[d]):
            print(f'[slowrise] Removing old-format: {d}')
            del trend[d]

    # ── 从 daily_picks.json 解析板块数据 ──
    rps_data = parse_sector_rankings()
    if not rps_data:
        print('[slowrise] FAILED: cannot parse rps.html')
        sys.exit(1)

    # ── 确定需要更新的日期 ──
    target_days = get_recent_trading_days(6)
    missing = [d for d in target_days if d not in trend or not trend[d]]

    if not missing:
        # 检查今日是否已有足够数据
        if TODAY in trend and isinstance(trend[TODAY], dict) and len(trend[TODAY]) >= 30:
            print(f'[slowrise] Today ({TODAY}) already has {len(trend[TODAY])} sectors, skip.')
            return
        missing = [TODAY]

    print(f'[slowrise] Target: {target_days}')
    print(f'[slowrise] Missing: {missing}')

    # ── 填充缺失日期 ──
    updated = []
    for d in missing:
        if d not in rps_data:
            print(f'[slowrise] No RPS data for {d}, skip.')
            continue
        entries = rps_data[d]
        rankings = build_rankings(entries)
        trend[d] = rankings
        updated.append(d)
        print(f'[slowrise] {d}: {len(rankings)} sectors, top5={[name for name,rank,_ in entries[:5]]}')

    if not updated:
        print('[slowrise] No dates updated.')
        return

    # ── 保存 ──
    with open(TREND_PATH, 'w', encoding='utf-8') as f:
        json.dump(trend, f, ensure_ascii=False, indent=2)
    print(f'[slowrise] Saved {len(updated)} dates, total dates: {len(trend)}')
    print(f'[slowrise] All dates: {sorted(trend.keys(), reverse=True)[:8]}')


if __name__ == '__main__':
    main()
