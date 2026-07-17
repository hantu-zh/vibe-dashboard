# -*- coding: utf-8 -*-
"""
RPS行业板块强弱排名 - 钉钉推送脚本（真实数据版）
- 使用东方财富/新浪数据获取板块真实涨跌幅
- 计算RPS相对强度并生成热力图
- 直接推送到钉钉
"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
import json, urllib.request, urllib.error, time, datetime, os, ssl
import numpy as np

# ─── SSL配置 ──────────────────────────────────────────────────────────────
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# ─── 交易日检测 ──────────────────────────────────────────────────────────
def is_trading_day():
    """
    检查是否为交易日（工作日且非节假日）
    返回: True/False
    """
    today = datetime.date.today()
    weekday = today.weekday()  # 0=周一, 6=周日
    
    # 周末直接返回 False
    if weekday >= 5:  # 周六、周日
        print(f'[INFO] 今天是{["周一","周二","周三","周四","周五","周六","周日"][weekday]}，非交易日')
        return False
    
    # 尝试使用 chinese_calendar 检测法定节假日
    try:
        import chinese_calendar
        is_workday = chinese_calendar.is_workday(today)
        if not is_workday:
            print(f'[INFO] 今天是法定节假日，非交易日')
            return False
    except ImportError:
        # chinese_calendar 未安装，跳过节假日检测
        print('[WARN] chinese_calendar 未安装，跳过节假日检测')
    
    return True

# 在脚本开始时检测交易日
if not is_trading_day():
    print('[INFO] 非交易日，脚本退出')
    sys.exit(0)

# ─── 读取钉钉 Webhook ──────────────────────────────────────────────────────
ENV_FILE = r'C:\Users\china\.qclaw\workspace\.env.dingtalk'
WEBHOOK_URL = None
with open(ENV_FILE, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('DINGTALK_WEBHOOK='):
            WEBHOOK_URL = line.strip().split('=', 1)[1]
            break
if not WEBHOOK_URL:
    print('[ERROR] DingTalk webhook not found')
    sys.exit(1)

# ─── 板块数据定义 ──────────────────────────────────────────────────────────
# 东方财富行业板块代码（主要板块）
EM_SECTORS = {
    '半导体': 'bk0897',
    '电子化学品': 'bk0898',
    '元件': 'bk0899',
    '光学光电子': 'bk0900',
    '消费电子': 'bk0901',
    '其他电子': 'bk0902',
    '计算机设备': 'bk0903',
    'IT服务': 'bk0904',
    '软件开发': 'bk0905',
    '通信服务': 'bk0906',
    '通信设备': 'bk0907',
    '医疗器械': 'bk0908',
    '医疗服务': 'bk0909',
    '生物制品': 'bk0910',
    '化学制药': 'bk0911',
    '中药': 'bk0912',
    '医药商业': 'bk0913',
    '电力': 'bk0914',
    '光伏设备': 'bk0915',
    '风电设备': 'bk0916',
    '电池': 'bk0917',
    '电网设备': 'bk0918',
    '汽车整车': 'bk0919',
    '汽车零部件': 'bk0920',
    '汽车服务': 'bk0921',
    '电机': 'bk0922',
    '钢铁': 'bk0923',
    '小金属': 'bk0924',
    '工业金属': 'bk0925',
    '贵金属': 'bk0926',
    '煤炭开采': 'bk0927',
    '石油加工': 'bk0928',
    '油气开采': 'bk0929',
    '银行': 'bk0930',
    '证券': 'bk0931',
    '保险': 'bk0932',
    '房地产开发': 'bk0933',
    '房地产服务': 'bk0934',
    '建筑装饰': 'bk0935',
    '建材': 'bk0936',
    '化学纤维': 'bk0937',
    '塑料': 'bk0938',
    '橡胶': 'bk0939',
    '化学原料': 'bk0940',
    '食品加工': 'bk0941',
    '饮料乳品': 'bk0942',
    '调味品': 'bk0943',
    '家电': 'bk0944',
    '家居用品': 'bk0945',
    '服装家纺': 'bk0946',
    '商贸零售': 'bk0947',
    '旅游酒店': 'bk0948',
    '教育': 'bk0949',
    '传媒': 'bk0950',
    '港口航运': 'bk0951',
    '物流': 'bk0952',
    '机场航空': 'bk0953',
    '公路铁路': 'bk0954',
    '国防军工': 'bk0955',
    '航天航空': 'bk0956',
}

# ─── 数据获取函数 ──────────────────────────────────────────────────────────

def fetch_sector_realtime() -> list:
    """
    从东方财富获取行业板块实时涨跌幅（带重试）
    返回: [{name, change_pct, stock_count}, ...]
    """
    import time
    # EM API URLs - 尝试多个备用地址
    em_urls = [
        'https://push2.eastmoney.com/api/qt/clist/get',
        'https://push2delay.eastmoney.com/api/qt/clist/get',
    ]
    ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

    for attempt in range(3):
        for base_url in em_urls:
            try:
                params = {
                    'pn': '1',
                    'pz': '100',
                    'po': '1',
                    'np': '1',
                    'ut': 'b2884a393a59ad64002292a3e90d46a5',
                    'fltt': '2',
                    'invt': '2',
                    'fid': 'f3',
                    'fs': 'm:90+t:2',
                    'fields': 'f1,f2,f3,f4,f5,f6,f7,f12,f14,f15,f16,f17,f18'
                }
                url = base_url + '?' + '&'.join([f'{k}={v}' for k, v in params.items()])
                req = urllib.request.Request(url, headers={
                    'User-Agent': ua,
                    'Referer': 'https://quote.eastmoney.com/',
                    'Accept': 'application/json',
                    'Connection': 'keep-alive',
                })
                with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
                    raw = r.read()
                    text = raw.decode('utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'utf-8')
                    data = json.loads(text)

                items = data.get('data', {}).get('diff', [])
                results = []
                for item in items:
                    name = item.get('f14', '')
                    chg = item.get('f3', 0)
                    count_raw = item.get('f5', 0)
                    try:
                        count = int(count_raw) if str(count_raw).strip() not in ('', '-', 'N/A') else 0
                    except (ValueError, TypeError):
                        count = 0
                    if name:
                        results.append({
                            'name': name,
                            'change_pct': float(chg) if chg else 0,
                            'stock_count': count
                        })

                if results:
                    print(f'[OK] EM API成功获取 {len(results)} 个板块 (尝试{attempt+1})')
                    return results
            except Exception as e:
                print(f'[WARN] EM({base_url}) 尝试{attempt+1}失败: {type(e).__name__}: {e}')
                time.sleep(2)
                continue

    print('[ERROR] EM 全部备用地址均失败')
    return []


def fetch_sector_from_sina() -> list:
    """
    从新浪获取行业板块涨跌幅（备用）
    """
    try:
        url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn',
        })
        with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
            raw = r.read()
        # Sina返回GBK编码数据
        text = raw.decode('gbk')
        
        # 提取JSON
        json_start = text.index('=') + 1
        json_str = text[json_start:].strip().rstrip(';').strip()
        data = json.loads(json_str)
        
        results = []
        for code, value in data.items():
            if not value or not isinstance(value, str):
                continue
            parts = value.split(',')
            if len(parts) < 5:
                continue
            try:
                name = parts[1]
                cnt = int(parts[2]) if parts[2] else 0
                # parts[3]=平均指数, parts[4]=涨跌幅%(40.4=40.4%), parts[5]=价格变化额
                chg_pct = round(float(parts[4]), 2) if parts[4] else 0
                if cnt >= 3:
                    results.append({'name': name, 'change_pct': chg_pct, 'stock_count': cnt})
            except:
                continue
        
        results.sort(key=lambda x: x['change_pct'], reverse=True)
        return results  # return all, let calculate_rps handle sorting
    except Exception as e:
        print(f'[WARN] Sina板块失败: {e}')
        return []


def calculate_rps(changes: list, period: int = 60) -> list:
    """
    计算RPS相对强度评分
    RPS = (1 - 排名/N) × 100
    返回: [{name, change_pct, rps}, ...]
    """
    n = len(changes)
    if n == 0:
        return []
    
    # 按涨跌幅排序
    sorted_changes = sorted(changes, key=lambda x: x['change_pct'], reverse=True)
    
    results = []
    for rank, item in enumerate(sorted_changes, 1):
        rps = round((1 - rank / n) * 100)
        results.append({
            'name': item['name'],
            'change_pct': item['change_pct'],
            'stock_count': item.get('stock_count', 0),
            'rps': rps,
            'rank': rank
        })
    
    return results


def get_trend_icon(rps: int) -> str:
    """根据RPS值返回趋势图标"""
    if rps >= 80:
        return '↑'  # 强势
    elif rps >= 50:
        return '→'  # 中等
    else:
        return '↓'  # 弱势


def strength_label(rps: int) -> str:
    """根据RPS值返回强度标签"""
    if rps >= 90:
        return '极强'
    if rps >= 80:
        return '强势'
    if rps >= 70:
        return '偏强'
    if rps >= 50:
        return '中等'
    if rps >= 40:
        return '偏弱'
    return '弱势'


# ─── 构建 Markdown 消息 ────────────────────────────────────────────────────

def build_markdown(data: list, mode: str, now: datetime.datetime) -> str:
    """构建钉钉Markdown消息"""
    
    if mode == 'morning':
        phase = '早盘'
        time_label = '10:00'
    else:
        phase = '收盘'
        time_label = '15:30'
    
    date_str = now.strftime('%Y年%m月%d日')
    
    # 统计
    strong_count = sum(1 for d in data if d['rps'] >= 80)
    weak_count = sum(1 for d in data if d['rps'] < 40)
    
    lines = []
    lines.append(f'### RPS{phase}强弱快照')
    lines.append(f'**{date_str} {time_label}**')
    lines.append('')
    lines.append(f'> 强势板块 {strong_count} 个 | 弱势板块 {weak_count} 个')
    lines.append('')
    
    # 表头
    lines.append('**排名 | 行业 | 涨幅% | RPS | 趋势 | 强度**')
    lines.append('--- | --- | --- | --- | --- | ---')
    
    for d in data[:16]:
        # Emoji颜色
        if d['rps'] >= 90:
            mark = '🔴'
        elif d['rps'] >= 80:
            mark = '🟠'
        elif d['rps'] >= 70:
            mark = '🟡'
        elif d['rps'] >= 50:
            mark = '🟢'
        else:
            mark = '🔵'
        
        trend_icon = {'↑': '🔺', '↓': '🔻', '→': '➡️'}.get(get_trend_icon(d['rps']), '')
        chg_sign = '+' if d['change_pct'] > 0 else ''
        
        # Eastmoney sector link
        sector_url = "https://quote.eastmoney.com/center/boardlist.html#industry_board"
        sector_link = f"[{d['name']}]({sector_url})"
        
        lines.append(
            f"{d['rank']} | {mark}{sector_link} | "
            f"{chg_sign}{d['change_pct']:.2f}% | "
            f"**{d['rps']}** | {trend_icon}{get_trend_icon(d['rps'])} | {strength_label(d['rps'])}"
        )
    
    lines.append('')
    lines.append('---')
    lines.append('*RPS = (1 - 排名/N) × 100，值越高越强*')
    lines.append(f'*数据来源: [东方财富行业板块](https://quote.eastmoney.com/center/boardlist.html#industry_board) | {now.strftime("%Y-%m-%d %H:%M")}*')
    
    return '\n'.join(lines)


# ─── 发送到钉钉 ─────────────────────────────────────────────────────────────

def send_to_dingtalk(title: str, markdown_text: str) -> bool:
    """发送Markdown消息到钉钉"""
    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': markdown_text
        }
    }
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(WEBHOOK_URL, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode('utf-8'))
            if result.get('errcode') == 0:
                print('[OK] DingTalk push succeeded')
                return True
            else:
                print(f'[ERROR] DingTalk push failed: {result}')
                return False
    except Exception as e:
        print(f'[ERROR] Push exception: {e}')
        return False


# ─── 主程序 ──────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    now = datetime.datetime.now()
    
    # 命令行参数: morning / close
    mode = 'close'
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
    if mode not in ('morning', 'close'):
        mode = 'close'
    
    print(f'=== RPS强弱快照 ({mode}) ===')
    
    # 获取板块数据
    print('[1/3] 获取板块数据...')
    sectors = fetch_sector_realtime()
    
    if not sectors:
        print('[INFO] EM接口失败，尝试Sina备用...')
        sectors = fetch_sector_from_sina()
    
    if not sectors:
        print('[ERROR] 无法获取板块数据')
        sys.exit(1)
    
    print(f'[OK] 获取到 {len(sectors)} 个板块')
    
    # 计算RPS
    print('[2/3] 计算RPS评分...')
    data = calculate_rps(sectors)
    
    # 构建消息
    phase = '早盘' if mode == 'morning' else '收盘'
    plan_time = "10:00" if phase == "早盘" else "15:30"
    title = f'RPS{phase}强弱快照(⏰计划{plan_time}) | {now.strftime("%m-%d %H:%M")}'
    md = build_markdown(data, mode, now)
    
    # 推送
    print('[3/3] 推送到钉钉...')
    ok = send_to_dingtalk(title, md)
    
    if ok:
        print(f'[OK] {phase}推送完成')
    else:
        print(f'[ERROR] {phase}推送失败')
    
    # 保存板块数据到 daily_picks.json 的 sector_rankings 字段
    try:
        today_str = now.strftime('%Y-%m-%d')
        
        # 构建板块数据（与钉钉推送格式一致，供dashboard读取）
        sector_data = [
            {
                'rank': s['rank'],
                'name': s['name'],
                'change_pct': round(s.get('change_pct', 0), 2),
                'rps': s['rps'],
                'trend': get_trend_icon(s['rps']),
                'strength': strength_label(s['rps'])
            }
            for s in data
        ]
        
        # 写入两个位置：工作区根目录 + vibe-dashboard 子目录
        for PICKS_FILE in [
            r'C:\Users\china\.qclaw\workspace\daily_picks.json',
            r'C:\Users\china\.qclaw\workspace\vibe-dashboard\daily_picks.json'
        ]:
            try:
                # 读取现有数据
                with open(PICKS_FILE, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                
                # 更新 sector_rankings
                if 'sector_rankings' not in d:
                    d['sector_rankings'] = {}
                d['sector_rankings'][today_str] = sector_data
                
                # 写回文件
                with open(PICKS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                
                print(f'[OK] 保存 {len(sector_data)} 个板块到 {PICKS_FILE} (含rps/rank)')
            except Exception as e2:
                print(f'[WARN] 保存失败 {PICKS_FILE}: {e2}')
    except Exception as e:
        print(f'[WARN] 保存失败: {e}')
    
    
def update_dashboard_embed(date_str: str, sector_data: list) -> bool:
    """更新 vibe-dashboard/index.html 中的 sector-rankings-embed"""
    html_path = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\index.html'
    
    # 读取现有嵌入数据
    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()
    
    # 找到 sector-rankings-embed 标签
    start_tag = '<script id="sector-rankings-embed"'
    start_pos = html_content.find(start_tag)
    
    if start_pos == -1:
        print('[WARN] 未找到 sector-rankings-embed 标签')
        return False
    
    # 找到标签结束位置
    start_tag_end = html_content.find('>', start_pos) + 1
    end_pos = html_content.find('</script>', start_tag_end)
    
    if end_pos == -1:
        print('[WARN] 未找到结束标签')
        return False
    
    # 读取现有的嵌入数据
    try:
        embed_json_str = html_content[start_tag_end:end_pos].strip()
        embed_data = json.loads(embed_json_str)
    except:
        embed_data = {}
    
    # 更新今天的数据
    embed_data[date_str] = sector_data
    
    # 只保留最近2天
    if len(embed_data) > 2:
        sorted_dates = sorted(embed_data.keys(), reverse=True)
        embed_data = {d: embed_data[d] for d in sorted_dates[:2]}
    
    # 替换嵌入数据
    new_embed = json.dumps(embed_data, ensure_ascii=False, indent=2)
    new_html = html_content[:start_tag_end] + '\n' + new_embed + '\n  ' + html_content[end_pos:]
    
    # 保存
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)
    
    print(f'[OK] 已更新 {html_path} 中的嵌入数据 ({date_str})')
    return True
# 更新 index.html 中的 sector-rankings-embed
    print('[4/4] 更新 dashboard 嵌入数据...')
    try:
        update_dashboard_embed(today_str, sector_data)
    except Exception as e:
        print(f'[WARN] Dashboard嵌入更新失败: {e}')
    
    sys.exit(0 if ok else 1)


