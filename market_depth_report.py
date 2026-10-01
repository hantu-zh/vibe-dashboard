# -*- coding: utf-8 -*-
"""
A股市场深度解读报告（新版）v1.0
生成内容：大盘指数 + 板块热度 + 主力资金动向 + 快讯分类 + 策略建议
结果保存到 daily_picks.json，同步到 GitHub Pages，发送到钉钉
"""
import sys, os, json, time, ssl, urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_source import fetch_em_indices, safe_float, get_a_stock_codes, fetch_sina_batch, fetch_em_flow_for_codes
from dingtalk_style import send

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

DP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "daily_picks.json")
TODAY = datetime.now().strftime("%Y-%m-%d")
NOW = datetime.now().strftime("%Y-%m-%d %H:%M")


# ============================================================
# 数据获取函数
# ============================================================

def fetch_top_sectors():
    """获取板块涨跌排行（东方财富板块数据）"""
    sectors = []
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=20&po=1&np=1&ut=&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f4,f8"
        req = urllib.request.Request(url, headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            data = json.loads(r.read())
        if data and "data" in data and data["data"]:
            diff = data["data"].get("diff", [])
            for item in diff[:20]:
                sectors.append({
                    "name": item.get("f14", ""),
                    "change": safe_float(item.get("f3", 0)),
                    "volume": safe_float(item.get("f8", 0)) / 10000,
                })
    except Exception as e:
        print(f"获取板块排行失败: {e}")
    return sectors


def fetch_bottom_sectors():
    """获取跌幅前10板块"""
    sectors = []
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=10&po=0&np=1&ut=&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f4,f8"
        req = urllib.request.Request(url, headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            data = json.loads(r.read())
        if data and "data" in data and data["data"]:
            diff = data["data"].get("diff", [])
            for item in diff[:10]:
                sectors.append({
                    "name": item.get("f14", ""),
                    "change": safe_float(item.get("f3", 0)),
                })
    except Exception as e:
        print(f"获取跌幅板块失败: {e}")
    return sectors


def fetch_main_flow():
    """获取大盘主力资金净流入情况（push2delay 替代被封的 push2his）"""
    try:
        indices_codes = [
            ("sh000001", "上证指数"),
            ("sz399001", "深证成指"),
            ("sz399006", "创业板指"),
        ]
        results = []
        for code, name in indices_codes:
            market = "1" if code.startswith("sh") else "0"
            secid = f"{market}.{code[2:]}"
            url = f"https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=5&klt=101&secid={secid}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
            req = urllib.request.Request(url, headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                data = json.loads(r.read())
            if data and "data" in data and data["data"]:
                klines = data["data"].get("klines", [])
                if klines:
                    last = klines[-1].split(",")
                    if len(last) >= 4:
                        results.append({
                            "name": name,
                            "main_flow": safe_float(last[1]),
                            "date": last[0],
                        })
        return results
    except Exception as e:
        print(f"获取主力资金失败: {e}")
        return []


def fetch_sector_flow():
    """获取板块资金流向排行"""
    sectors = []
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get?cb=&pn=1&pz=10&po=1&np=1&ut=&fltt=2&invt=2&fid=f62&fs=m:90+t:2+f:!50&fields=f12,f14,f62,f3"
        req = urllib.request.Request(url, headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            data = json.loads(r.read())
        if data and "data" in data and data["data"]:
            diff = data["data"].get("diff", [])
            for item in diff[:10]:
                main_flow = safe_float(item.get("f62", 0))
                sectors.append({
                    "name": item.get("f14", ""),
                    "main_flow_yi": main_flow / 100000000,  # f62 单位：元 -> 亿
                    "change_pct": safe_float(item.get("f3", 0)),
                })
    except Exception as e:
        print(f"获取板块资金流失败: {e}")
    return sectors


def fetch_limit_up_stocks():
    """统计涨停股"""
    limit_up = []
    try:
        url = "https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=30&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:13,m:1+t:2,m:1+t:23&fields=f12,f14,f3,f5,f8"
        req = urllib.request.Request(url, headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            data = json.loads(r.read())
        if data and "data" in data and data["data"]:
            diff = data["data"].get("diff", [])
            for item in diff:
                name = item.get("f14", "")
                change = safe_float(item.get("f3", 0))
                if "ST" in name or "退" in name or change < 9.5:
                    continue
                limit_up.append({
                    "name": name,
                    "code": item.get("f12", ""),
                    "change": change,
                    "volume_yi": safe_float(item.get("f8", 0)) / 10000,
                })
                if len(limit_up) >= 15:
                    break
    except Exception as e:
        print(f"获取涨停股失败: {e}")
    return limit_up


def fetch_a_stock_count():
    """获取全市场涨跌统计（akshare Sina 实时行情，避开被封的 eastmoney）"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        col = "涨跌幅" if "涨跌幅" in df.columns else ("changepercent" if "changepercent" in df.columns else None)
        if col is None:
            return {"up": 0, "down": 0, "flat": 0}
        chg = df[col].astype(float)
        return {
            "up": int((chg > 0).sum()),
            "down": int((chg < 0).sum()),
            "flat": int((chg == 0).sum()),
        }
    except Exception as e:
        print(f"获取涨跌统计失败: {e}")
        return {"up": 0, "down": 0, "flat": 0}


def fetch_news_flash():
    """获取快讯（新浪财经7x24）"""
    news_list = []
    try:
        url = "https://finance.sina.com.cn/7x24/"
        req = urllib.request.Request(url, headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            html = r.read().decode("utf-8", errors="replace")
        
        # 提取最新快讯：新浪 7x24 结构为 <div>HH:MM:SS</div><a ...>内容</a>
        import re
        items = re.findall(r'<div>(\d{1,2}:\d{2}:\d{2})</div>\s*<a[^>]*>(.*?)</a>', html, re.DOTALL)
        for t, content in items[:25]:
            content = re.sub(r'<[^>]+>', '', content).strip()
            if content and len(content) > 10:
                news_list.append({"time": t[:5], "content": content[:200]})
    except Exception as e:
        print(f"获取快讯失败: {e}")
    return news_list


def classify_news(news_list):
    """将快讯分类"""
    categories = {
        "政策": ["政策", "监管", "央行", "国务院", "发改委", "证监会", "银保监", "财政部", "法规", "制度"],
        "宏观": ["GDP", "CPI", "PMI", "通胀", "利率", "就业", "社融", "M2", "外汇", "汇率", "进出口"],
        "行业": ["新能源", "芯片", "半导体", "AI", "人工智能", "锂电", "光伏", "储能", "汽车", "医药", "消费", "地产", "银行", "保险", "券商", "白酒", "军工"],
        "市场": ["A股", "港股", "美股", "指数", "北向", "南向", "融资", "两融", "成交", "成交量"],
    }
    
    classified = {k: [] for k in categories}
    classified["其他"] = []
    
    for news in news_list:
        content = news["content"]
        time_str = news["time"]
        matched = False
        for cat, keywords in categories.items():
            if any(kw in content for kw in keywords):
                classified[cat].append({"time": time_str, "content": content})
                matched = True
                break
        if not matched:
            classified["其他"].append({"time": time_str, "content": content})
    
    # 每类最多保留3条
    for k in classified:
        classified[k] = classified[k][:3]
    
    return classified


# ============================================================
# 策略生成
# ============================================================

def generate_strategy(indices, sectors, main_flow, market_stats, sector_flow):
    """基于市场数据生成策略建议"""
    sh_change = 0
    sz_change = 0
    cyb_change = 0
    for name, data in indices.items():
        if "上证" in name:
            sh_change = data.get("change_pct", 0)
        elif "深证" in name:
            sz_change = data.get("change_pct", 0)
        elif "创业板" in name:
            cyb_change = data.get("change_pct", 0)

    avg_change = (sh_change + sz_change + cyb_change) / 3

    # 主力资金方向
    total_flow = sum(mf.get("main_flow", 0) for mf in main_flow)
    
    # 涨跌比
    up = int(market_stats.get("up", 0))
    down = int(market_stats.get("down", 0))
    total = up + down
    up_ratio = up / total if total > 0 else 0.5

    # 热门板块
    hot_sectors = [s["name"] for s in sectors[:5] if s.get("change", 0) > 1.0]
    cold_sectors = [s["name"] for s in (sector_flow or [])[:3] if s.get("main_flow_yi", 0) < -1.0]

    # 策略生成逻辑
    if avg_change > 1.0 and up_ratio > 0.6:
        strategy = "强势做多"
        position = "7-8成"
        advice = [
            f"市场整体强势，上涨家数占比{up_ratio:.0%}，做多氛围浓厚",
            f"主线方向: {'、'.join(hot_sectors[:3]) if hot_sectors else '普涨格局'}",
            "操作建议: 逢回调加仓主线龙头，持股为主",
            "风险提示: 追高需谨慎，关注量能是否持续",
        ]
    elif avg_change > 0.3 and up_ratio > 0.5:
        strategy = "积极布局"
        position = "5-6成"
        advice = [
            f"市场温和偏强，上涨家数占比{up_ratio:.0%}，可适度参与",
            f"关注板块: {'、'.join(hot_sectors[:3]) if hot_sectors else '均衡配置'}",
            "操作建议: 低吸为主，不追高，关注低位补涨品种",
            "风险提示: 量能不足时注意控制仓位",
        ]
    elif avg_change > -0.3 and up_ratio > 0.4:
        strategy = "观望为主"
        position = "3-4成"
        advice = [
            f"市场震荡整理，上涨家数占比{up_ratio:.0%}，方向不明",
            f"等待方向选择，关注是否有新的主线板块出现",
            "操作建议: 轻仓观望，已持仓的可做T+0降成本",
            "风险提示: 震荡市不宜重仓，耐心等待信号",
        ]
    elif avg_change > -1.0:
        strategy = "防御为主"
        position = "2-3成"
        advice = [
            f"市场偏弱，上涨家数占比仅{up_ratio:.0%}，风险加大",
            "操作建议: 降低仓位，回避高位股和弱势板块",
            "关注防御性品种: 银行、公用事业、黄金等",
            "风险提示: 跌破关键支撑位需果断减仓",
        ]
    else:
        strategy = "空仓等待"
        position = "0-1成"
        advice = [
            f"市场大幅调整，上涨家数占比仅{up_ratio:.0%}，系统性风险加大",
            "操作建议: 以空仓或极轻仓为主，不抄底",
            "等待企稳信号: 缩量企稳、底部放量、政策利好",
            "风险提示: 弱市不抄底是铁律",
        ]

    # 资金面补充
    if total_flow > 50000:
        advice.append(f"💰 主力资金今日大幅净流入{total_flow/10000:.1f}亿，资金面积极")
    elif total_flow < -50000:
        advice.append(f"⚠️ 主力资金今日大幅净流出{abs(total_flow)/100000000:.1f}亿，注意风险")

    if cold_sectors:
        advice.append(f"🚫 回避板块: {'、'.join(cold_sectors[:3])}")

    return {
        "strategy": strategy,
        "position": position,
        "advice": advice,
        "hot_sectors": hot_sectors,
        "sentiment": strategy,
    }


# ============================================================
# 保存 & 推送
# ============================================================

def save_to_daily_picks(report_data):
    """保存到 daily_picks.json"""
    data = {}
    if os.path.exists(DP_FILE):
        try:
            with open(DP_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except:
            data = {}

    if "picks" not in data:
        data["picks"] = []

    existing = None
    for p in data["picks"]:
        if p.get("date") == TODAY and p.get("strategy") == "市场深度解读":
            existing = p
            break

    record = {
        "date": TODAY,
        "strategy": "市场深度解读",
        "stocks": report_data.get("limit_up", []),
        "count": report_data.get("limit_up_count", 0),
        "timestamp": NOW,
        "data": {
            "indices": report_data.get("indices", {}),
            "sectors_top": report_data.get("sectors_top", []),
            "sectors_bottom": report_data.get("sectors_bottom", []),
            "main_flow": report_data.get("main_flow", []),
            "sector_flow": report_data.get("sector_flow", []),
            "market_stats": report_data.get("market_stats", {}),
            "news_classified": report_data.get("news_classified", {}),
            "strategy": report_data.get("strategy", {}),
        }
    }

    if existing:
        existing.update(record)
    else:
        data["picks"].append(record)

    data["picks"].sort(key=lambda x: x.get("date", ""), reverse=True)
    data["picks"] = data["picks"][:30]

    # 保存板块排名
    top_sectors = report_data.get("sectors_top", [])
    if top_sectors:
        data["sector_rankings"] = {
            "date": TODAY,
            "rankings": top_sectors,
            "timestamp": NOW
        }

    with open(DP_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, ensure_ascii=False, indent=2, fp=f)
    print(f"[深度解读] 已保存到 daily_picks.json")


def send_dingtalk_report(report_data):
    """发送钉钉市场深度解读报告"""
    indices = report_data.get("indices", {})
    sectors_top = report_data.get("sectors_top", [])
    sectors_bottom = report_data.get("sectors_bottom", [])
    limit_up = report_data.get("limit_up", [])
    main_flow = report_data.get("main_flow", [])
    sector_flow = report_data.get("sector_flow", [])
    market_stats = report_data.get("market_stats", {})
    news_classified = report_data.get("news_classified", {})
    strategy = report_data.get("strategy", {})
    limit_up_count = report_data.get("limit_up_count", 0)

    # === 1. 指数 ===
    idx_lines = ["**📊 大盘指数**", ""]
    for name, data in indices.items():
        price = data.get("price", 0)
        change_pct = data.get("change_pct", 0)
        emoji = "🔴" if change_pct < 0 else "🟢"
        try:
            idx_lines.append(f"{emoji} **{name}**: {float(price):.2f} ({float(change_pct):+.2f}%)")
        except:
            idx_lines.append(f"{emoji} **{name}**: {price} ({change_pct}%)")

    # === 2. 市场统计 ===
    ms = market_stats or {}
    up_count = int(ms.get("up", 0))
    down_count = int(ms.get("down", 0))
    flat_count = int(ms.get("flat", 0))
    total = up_count + down_count + flat_count
    up_ratio = f"{up_count/total*100:.0f}%" if total > 0 else "N/A"
    stats_lines = [
        f"上涨 {up_count} / 下跌 {down_count} / 平盘 {flat_count} | 涨跌比 {up_ratio}",
        ""
    ]

    # === 3. 板块热度 ===
    sector_lines = ["**🔥 板块涨幅 TOP10**", ""]
    if sectors_top:
        for i, s in enumerate(sectors_top[:10], 1):
            name = s.get("name", "-")
            change = s.get("change", 0)
            emoji = "🟢"
            try:
                sector_lines.append(f"{i}. {emoji} {name} {float(change):+.2f}%")
            except:
                sector_lines.append(f"{i}. {name} {change}%")
    else:
        sector_lines.append("暂无数据")

    # 跌幅榜
    bottom_lines = ["\n**❄️ 板块跌幅 TOP5**", ""]
    if sectors_bottom:
        for i, s in enumerate(sectors_bottom[:5], 1):
            name = s.get("name", "-")
            change = s.get("change", 0)
            try:
                bottom_lines.append(f"{i}. 🔴 {name} {float(change):+.2f}%")
            except:
                bottom_lines.append(f"{i}. {name} {change}%")

    # === 4. 主力资金 ===
    flow_lines = ["**💰 主力资金动向**", ""]
    if main_flow:
        for mf in main_flow:
            name = mf.get("name", "")
            flow = mf.get("main_flow", 0)
            date = mf.get("date", "")
            sign = "流入" if flow > 0 else "流出"
            emoji = "🟢" if flow > 0 else "🔴"
            try:
                flow_lines.append(f"{emoji} {name}: {float(flow)/100000000:+.2f}亿 {sign}")  # flow 单位：元 -> 亿
            except:
                flow_lines.append(f"- {name}: {flow}万 {sign}")
    
    # 板块资金流
    if sector_flow:
        flow_lines.append("\n板块资金净流入 TOP5:")
        for sf in sector_flow[:5]:
            name = sf.get("name", "")
            flow = sf.get("main_flow_yi", 0)
            emoji = "🟢" if flow > 0 else "🔴"
            try:
                flow_lines.append(f"  {emoji} {name}: {float(flow):+.2f}亿")
            except:
                flow_lines.append(f"  {name}: {flow}亿")

    # === 5. 涨停统计 ===
    limit_lines = ["\n**🚀 涨停板**", ""]
    limit_lines.append(f"今日涨停: **{limit_up_count}** 只")
    if limit_up:
        for i, s in enumerate(limit_up[:8], 1):
            name = s.get("name", "-")
            code = s.get("code", "")
            change = s.get("change", 0)
            try:
                limit_lines.append(f"{i}. **{name}** ({code}) {float(change):+.1f}%")
            except:
                limit_lines.append(f"{i}. {name} ({code}) {change}%")

    # === 6. 快讯分类 ===
    news_lines = ["\n**📰 快讯精选**", ""]
    cat_emoji = {"政策": "🏛️", "宏观": "🌍", "行业": "🏭", "市场": "📈"}
    for cat, items in news_classified.items():
        if items:
            emoji = cat_emoji.get(cat, "📌")
            news_lines.append(f"{emoji} **{cat}**")
            for n in items[:2]:
                news_lines.append(f"  • [{n['time']}] {n['content'][:80]}")
            news_lines.append("")

    # === 7. 策略建议 ===
    strategy_name = strategy.get("strategy", "观望")
    position = strategy.get("position", "3-4成")
    advice = strategy.get("advice", [])
    hot = strategy.get("hot_sectors", [])

    strategy_lines = [
        "\n**🎯 操作策略**",
        f"策略: **{strategy_name}** | 建议仓位: **{position}**",
        ""
    ]
    for a in advice:
        strategy_lines.append(f"• {a}")
    strategy_lines.append("")

    # === 组装 ===
    parts = [
        f"### 📊 A股市场深度解读\n\n📅 {NOW}",
        "",
        "\n".join(idx_lines),
        "",
        "\n".join(stats_lines),
        "",
        "\n".join(sector_lines),
        "\n".join(bottom_lines),
        "",
        "\n".join(flow_lines),
        "",
        "\n".join(limit_lines),
        "",
        "\n".join(news_lines),
        "\n".join(strategy_lines),
        "",
        "*市场深度解读 · 仅供参考 · 不构成投资建议*"
    ]

    content = "\n".join(parts)
    ok = send("📊 A股市场深度解读(⏰计划16:00)", content)
    print(f"[深度解读] 钉钉推送: {'✅ 成功' if ok else '❌ 失败'}")
    return ok


# ============================================================
# 主流程
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("📊 A股市场深度解读报告（新版）v1.0")
    print("=" * 60)
    print(f"时间: {NOW}")

    # 1. 大盘指数
    print("\n[1/8] 获取大盘指数...")
    indices = fetch_em_indices()
    print(f"  获取到 {len(indices)} 个指数")

    # 2. 市场统计
    print("[2/8] 获取市场统计...")
    market_stats = fetch_a_stock_count()
    print(f"  上涨: {market_stats.get('up',0)} 下跌: {market_stats.get('down',0)}")

    # 3. 板块涨幅
    print("[3/8] 获取板块排行...")
    sectors_top = fetch_top_sectors()
    sectors_bottom = fetch_bottom_sectors()
    print(f"  涨幅TOP: {len(sectors_top)} 跌幅TOP: {len(sectors_bottom)}")

    # 4. 涨停统计
    print("[4/8] 获取涨停统计...")
    limit_up = fetch_limit_up_stocks()
    print(f"  涨停 {len(limit_up)} 只")

    # 5. 主力资金
    print("[5/8] 获取主力资金...")
    main_flow = fetch_main_flow()
    sector_flow = fetch_sector_flow()
    print(f"  大盘资金流: {len(main_flow)} 条 板块资金流: {len(sector_flow)} 条")

    # 6. 快讯
    print("[6/8] 获取快讯...")
    news = fetch_news_flash()
    news_classified = classify_news(news)
    total_news = sum(len(v) for v in news_classified.values())
    print(f"  快讯 {len(news)} 条，分类后 {total_news} 条")

    # 7. 生成策略
    print("[7/8] 生成策略...")
    strategy = generate_strategy(indices, sectors_top, main_flow, market_stats, sector_flow)
    print(f"  策略: {strategy.get('strategy')} 仓位: {strategy.get('position')}")

    # 8. 保存并发送
    print("[8/8] 保存并发送...")
    report_data = {
        "indices": {k: {"price": v.get("price", 0), "change_pct": v.get("change_pct", 0)} for k, v in indices.items()},
        "sectors_top": sectors_top[:20],
        "sectors_bottom": sectors_bottom[:10],
        "limit_up": [{"name": s["name"], "code": s["code"], "change": s["change"]} for s in limit_up],
        "limit_up_count": len(limit_up),
        "main_flow": main_flow,
        "sector_flow": sector_flow[:10],
        "market_stats": market_stats,
        "news_classified": news_classified,
        "strategy": strategy,
    }

    save_to_daily_picks(report_data)
    send_dingtalk_report(report_data)

    print(f"\n{'='*60}")
    print(f"市场深度解读完成 | {NOW}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
