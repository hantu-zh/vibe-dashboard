# -*- coding: utf-8 -*-
"""
大力水手训练系统
每日选10只 → 模拟买卖 → 次日复盘 → 持续学习
目标：找出特色操盘手法
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import json, os, ssl, urllib.request
from datetime import datetime, date, timedelta
import random
from daily_picks_store import save_daily_picks

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
TRAINING_DIR = os.path.join(WORKSPACE, "popeye_data")
try:
    os.makedirs(TRAINING_DIR, exist_ok=True)
except:
    TRAINING_DIR = WORKSPACE

# 钉钉Webhook
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"

def load_json(fname):
    path = os.path.join(TRAINING_DIR, fname)
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except:
            pass
    return {}

def save_json(fname, data):
    path = os.path.join(TRAINING_DIR, fname)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"  ⚠️ 保存{fname}失败: {e}")

def send_dingtalk(content, title="大力水手训练系统"):
    """发送钉钉消息"""
    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": content
        }
    }
    try:
        req = urllib.request.Request(
            DINGTALK_WEBHOOK,
            data=json.dumps(data, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            result = json.loads(r.read().decode('utf-8-sig'))
            if result.get("errcode") == 0:
                print("  ✅ 钉钉推送成功")
                return True
            else:
                print(f"  ⚠️ 钉钉推送失败: {result}")
                return False
    except Exception as e:
        print(f"  ⚠️ 钉钉推送异常: {e}")
        return False

def fetch(url, timeout=10):
    h = {"User-Agent": "Mozilla/5.0 Chrome/120.0"}
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8-sig", errors="replace"))
    except:
        return None

def get_realtime_price(code):
    """获取实时价格（EM API 不可用时降级到 Sina）"""
    # 先尝试 EM API
    secid = "1." + code if code.startswith("6") else "0." + code
    url = f"https://push2he.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f2,f3,f4,f5"
    data = fetch(url)
    if data and data.get("data"):
        d = data["data"]
        return {
            "price": d.get("f2", 0),
            "change": d.get("f3", 0),
            "volume": d.get("f5", 0),
        }
    # 降级到 Sina
    try:
        prefix = "sh" if code.startswith("6") else "sz"
        from em_api_helper import sina_get_quotes
        quotes = sina_get_quotes([f"{prefix}{code}"])
        if quotes:
            q = quotes[0]
            try:
                price = float(q.get('price', 0))
                close_yd = float(q.get('close_yesterday', 0))
                change = round((price - close_yd) / close_yd * 100, 2) if close_yd > 0 else 0
            except:
                price, change = 0, 0
            return {"price": price, "change": change, "volume": q.get('volume', 0)}
    except Exception as e:
        print(f"  Sina备用也失败: {e}")
    return None

def select_10_stocks():
    """选10只股票（妙想API优先，降级Sina）"""
    sys.path.insert(0, os.path.join(WORKSPACE, "skills", "mx-skills", "mx-select-stock"))
    try:
        from mx_select_stock import MXSelectStock
        mx = MXSelectStock()
        
        # 查询涨幅0-5%
        result = mx.search("非ST 非亏损 涨幅0到5 换手率大于3")
        rows, _, _ = MXSelectStock.extract_data(result)
        
        if not rows:
            print("  MX无数据，降级Sina")
            raise Exception("MX无数据")
        
        candidates = []
        for row in (rows or []):
            code = "-"
            name = "-"
            price = 0
            change = 0
            turnover = 0
            for k, v in row.items():
                # 精确匹配股票代码字段（排除"市场代码"等）
                if k == "代码" or k == "证券代码":
                    code = str(v).split("|")[0]
                elif k == "名称" or k == "证券名称":
                    name = str(v).split("|")[0]
                elif "最新价" in k and "均价" not in k:
                    try: price = float(str(v).split("|")[0])
                    except: pass
                elif "涨跌幅" in k:
                    try: change = float(str(v).split("|")[0])
                    except: pass
                elif k == "换手率" or "换手率" in k and "累计" not in k:
                    try: turnover = float(str(v).split("|")[0])
                    except: pass
            
            if not code or code.startswith(("8", "4")) or "ST" in name:
                continue
            if price <= 0 or price > 50:
                continue
            
            # 黄金区间优先
            if 3 <= change < 6:
                score = 100 + random.randint(0, 20)
            elif 1 <= change < 3:
                score = 80 + random.randint(0, 15)
            elif 0 <= change < 1:
                score = 60 + random.randint(0, 10)
            else:
                score = 40 + random.randint(0, 10)
            
            candidates.append({
                "code": code,
                "name": name,
                "price": price,
                "change": change,
                "turnover": turnover,
                "score": score,
            })
        
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:10]
    except Exception as e:
        print(f"  妙想API错误: {e}，降级Sina")
        return _fallback_select_stocks()

def _fallback_select_stocks():
    """Sina备用选股"""
    try:
        import urllib.request as _urllib
        import json as _json
        url = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=200&sort=changepercent&asc=0&node=hs_a'
        req = _urllib.Request(url, headers={'Referer': 'https://finance.sina.com.cn/', 'User-Agent': 'Mozilla/5.0'})
        with _urllib.urlopen(req, timeout=15, context=ctx) as r:
            items = _json.loads(r.read().decode('gbk', errors='replace'))
        candidates = []
        for item in items:
            try:
                pct = float(item.get('changepercent', 0))
                price = float(item.get('trade', 0))
                turnover = float(item.get('turnoverratio', 0))
            except:
                continue
            name = item.get('name', '')
            code = item.get('code', '')
            if 'ST' in name or code.startswith(('8','4')):
                continue
            if price <= 0 or price > 50 or turnover < 3:
                continue
            if 3 <= pct < 6: score = 100 + random.randint(0,20)
            elif 1 <= pct < 3: score = 80 + random.randint(0,15)
            else: score = 60 + random.randint(0,10)
            candidates.append({"code": code, "name": name, "price": price, "change": pct, "turnover": turnover, "score": score})
        candidates.sort(key=lambda x: x["score"], reverse=True)
        print(f"  [备用] Sina选 {len(candidates[:10])} 只")
        return candidates[:10]
    except Exception as e:
        print(f"  [备用] Sina也失败: {e}")
        return []

def simulate_buy(stocks, capital=100000):
    """模拟买入"""
    today_str = date.today().strftime("%Y-%m-%d")
    portfolio = load_json("portfolio.json")

    # 如果已有持仓，先检查是否需要卖出
    if portfolio.get("positions"):
        print("\n📊 检查昨日持仓...")
        for pos in portfolio["positions"]:
            code = pos["code"]
            rt = get_realtime_price(code)
            if rt:
                current_price = rt["price"]
                buy_price = pos["buy_price"]
                pnl_pct = (current_price - buy_price) / buy_price * 100
                pos["current_price"] = current_price
                pos["pnl_pct"] = pnl_pct
                print(f"  {pos['name']}({code}): 买入{buy_price:.2f} → 现价{current_price:.2f} = {pnl_pct:+.2f}%")

    # 新建持仓
    positions = []
    per_stock = capital / len(stocks)

    print(f"\n💰 模拟买入（总资金{capital}元，每只{per_stock:.0f}元）")
    for s in stocks:
        shares = int(per_stock / s["price"] / 100) * 100  # 整手
        if shares <= 0:
            continue
        cost = shares * s["price"]
        positions.append({
            "code": s["code"],
            "name": s["name"],
            "buy_price": s["price"],
            "buy_date": today_str,
            "shares": shares,
            "cost": cost,
            "reason": "黄金区间3-6%" if s["change"] >= 3 else "温和启动",
            "change_at_buy": s["change"],
        })
        print(f"  ✅ {s['code']} {s['name']} {s['price']:.2f}元 × {shares}股 = {cost:.0f}元")

    portfolio = {
        "date": today_str,
        "capital": capital,
        "positions": positions,
        "total_cost": sum(p["cost"] for p in positions),
    }
    save_json("portfolio.json", portfolio)
    return portfolio

def review_yesterday():
    """复盘昨日操作"""
    history = load_json("history.json")
    today_str = date.today().strftime("%Y-%m-%d")
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    if not history.get(yesterday):
        print("\n📭 昨日无交易记录")
        return

    print(f"\n📋 复盘 {yesterday}")
    print("="*50)

    day_data = history[yesterday]
    results = []

    for pos in day_data.get("positions", []):
        code = pos["code"]
        rt = get_realtime_price(code)
        if rt:
            current_price = rt["price"]
            buy_price = pos["buy_price"]
            pnl_pct = (current_price - buy_price) / buy_price * 100
            results.append({
                "name": pos["name"],
                "code": code,
                "buy_price": buy_price,
                "current_price": current_price,
                "pnl_pct": pnl_pct,
                "reason": pos.get("reason", ""),
            })

    # 统计
    wins = [r for r in results if r["pnl_pct"] > 0]
    losses = [r for r in results if r["pnl_pct"] <= 0]
    avg_win = sum(r["pnl_pct"] for r in wins) / len(wins) if wins else 0
    avg_loss = sum(r["pnl_pct"] for r in losses) / len(losses) if losses else 0

    print(f"\n📊 结果统计")
    print(f"  盈利: {len(wins)}只，平均+{avg_win:.2f}%")
    print(f"  亏损: {len(losses)}只，平均{avg_loss:.2f}%")

    print(f"\n✅ 盈利股票")
    for r in sorted(wins, key=lambda x: x["pnl_pct"], reverse=True):
        print(f"  {r['code']} {r['name']} {r['buy_price']:.2f}→{r['current_price']:.2f} = +{r['pnl_pct']:.2f}% [{r['reason']}]")

    print(f"\n❌ 亏损股票")
    for r in sorted(losses, key=lambda x: x["pnl_pct"]):
        print(f"  {r['code']} {r['name']} {r['buy_price']:.2f}→{r['current_price']:.2f} = {r['pnl_pct']:.2f}% [{r['reason']}]")

    # 学习总结
    print(f"\n🧠 学习总结")
    if len(wins) > len(losses):
        print("  ✅ 胜率>50%，策略有效，继续优化")
    else:
        print("  ⚠️ 胜率<50%，需要调整选股条件")

    # 分析盈利股票的共同特征
    if wins:
        print("\n📈 盈利股票特征分析:")
        avg_change_at_buy = sum(pos.get("change_at_buy", 0) for pos in day_data["positions"] if any(r["code"] == pos["code"] for r in wins)) / len(wins)
        print(f"  - 买入时平均涨幅: {avg_change_at_buy:.2f}%")

    return results

def update_history(portfolio):
    """更新历史记录"""
    history = load_json("history.json")
    today_str = portfolio["date"]
    history[today_str] = portfolio
    save_json("history.json", history)

def learn_patterns():
    """学习模式识别"""
    history = load_json("history.json")
    stats = load_json("stats.json")

    all_trades = []
    for day_str, day_data in history.items():
        for pos in day_data.get("positions", []):
            all_trades.append({
                "date": day_str,
                **pos
            })

    if len(all_trades) < 10:
        print("\n📊 交易样本不足10笔，继续积累数据...")
        return

    print(f"\n🧠 模式学习（共{len(all_trades)}笔交易）")
    print("="*50)

    # 分析买入涨幅分布
    changes = [t.get("change_at_buy", 0) for t in all_trades]
    avg_change = sum(changes) / len(changes)
    print(f"\n📊 买入涨幅分布")
    print(f"  平均买入涨幅: {avg_change:.2f}%")
    print(f"  建议: 黄金区间3-6% {'✅ 符合' if 3 <= avg_change <= 6 else '⚠️ 需调整'}")

    # 统计学习
    stats["total_trades"] = len(all_trades)
    stats["avg_buy_change"] = avg_change
    stats["last_update"] = date.today().strftime("%Y-%m-%d")
    save_json("stats.json", stats)

def run():
    today_str = date.today().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%H:%M")

    print(f"\n{'='*55}")
    print(f"🧑‍✈️ 大力水手训练系统")
    print(f"   {today_str} {now_str}")
    print(f"{'='*55}")

    # 1. 复盘昨日
    review_results = review_yesterday()

    # 2. 选10只股票
    print(f"\n🎯 今日选股（黄金区间3-6%）")
    print("="*50)
    stocks = select_10_stocks()

    if not stocks:
        print("❌ 未找到符合条件的股票")
        # 发送钉钉消息告知无结果
        content = f"## 🧑‍✈️ 大力水手训练系统\n\n> {today_str} {now_str}\n\n❌ 今日未找到符合条件的股票，请检查妙想API或调整筛选条件。"
        send_dingtalk(content)
        return

    for i, s in enumerate(stocks, 1):
        print(f"  {i}. {s['code']} {s['name']} {s['price']:.2f}元 +{s['change']:.2f}% 分数{s['score']}")

    # 同步到 Dashboard 每日选股推荐
    save_daily_picks('大力水手训练', stocks)

    # 3. 模拟买入
    portfolio = simulate_buy(stocks)

    # 4. 更新历史
    update_history(portfolio)

    # 5. 学习模式
    learn_patterns()

    # 6. 发送钉钉消息
    history = load_json("history.json")
    stats = load_json("stats.json")
    total_trades = stats.get("total_trades", len(history.keys()))
    
    # 构建钉钉消息
    lines = [
        f"## 🧑‍✈️ 大力水手训练系统",
        f"\n> {today_str} {now_str}",
        f"\n### 📋 复盘昨日",
    ]
    
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    yesterday_data = history.get(yesterday)
    if yesterday_data:
        wins = 0
        losses = 0
        for pos in yesterday_data.get("positions", []):
            code = pos["code"]
            rt = get_realtime_price(code)
            if rt:
                pnl = (rt["price"] - pos["buy_price"]) / pos["buy_price"] * 100
                if pnl > 0:
                    wins += 1
                else:
                    losses += 1
        if wins > losses:
            lines.append(f"✅ 胜率>50%（盈利{wins}只，亏损{losses}只）")
        else:
            lines.append(f"⚠️ 胜率<50%（盈利{wins}只，亏损{losses}只），需调整")
    else:
        lines.append("📭 昨日无交易记录")
    
    lines.append(f"\n### 🎯 今日建仓（10只，黄金区间3-6%）")
    lines.append(f"\n| 序号 | 代码 | 名称 | 价格 | 涨幅 |")
    lines.append(f"\n|------|------|------|------|------|")
    for i, s in enumerate(stocks, 1):
        lines.append(f"\n| {i} | {s['code']} | {s['name']} | {s['price']:.2f} | +{s['change']:.2f}% |")
    
    lines.append(f"\n\n💰 **合计投入**: ¥{portfolio['total_cost']:.0f}")
    avg_change = sum(s['change'] for s in stocks) / len(stocks)
    lines.append(f"\n📊 **平均涨幅**: +{avg_change:.2f}% {'✅ 符合黄金区间' if 3 <= avg_change <= 6 else '⚠️ 需调整'}")
    
    lines.append(f"\n\n### 📈 学习进度")
    lines.append(f"\n- 累计交易: {total_trades}笔")
    lines.append(f"\n- 系统持续学习中...")
    
    lines.append(f"\n\n---\n📌 持仓待明日复盘验证效果")
    
    content = "".join(lines)
    send_dingtalk(content)

    print(f"\n{'='*55}")
    print("📌 训练完成，明日继续复盘")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    run()
