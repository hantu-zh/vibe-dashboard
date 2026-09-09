
# ─── 路径兼容（自动识别 Windows / GitHub Actions） ───
import sys, os
sys.path.insert(0, 'vibe-dashboard')  # vibe-dashboard 根
from paths import WS, VIBE_DIR
# -*- coding: utf-8 -*-
"""
大力水手下午筛选 v3
每日 14:50 执行
条件：涨幅3-5% + 量比>1 + 换手5-10% + 量能放大
使用：Sina（股票列表+变化）+ Tencent（量比+换手率）
"""
import sys, json, os, ssl, urllib.request, subprocess, datetime
from datetime import date

sys.stdout.reconfigure(encoding='utf-8')

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(WORKSPACE, "vibe-dashboard")

DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"

# ─── Sina API: 获取股票列表（含变化率和换手率）──────────────────────────────
def fetch_sina_all(pages=10):
    """分页获取 Sina A股涨幅榜（每页100条）"""
    all_stocks = []
    for page in range(1, pages + 1):
        url = (
            f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
            f"/Market_Center.getHQNodeData"
            f"?page={page}&num=100&sort=changepercent&asc=0&node=hs_a"
        )
        req = urllib.request.Request(url, headers={
            'Referer': 'https://finance.sina.com.cn/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        })
        try:
            with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                data = json.loads(r.read().decode('gbk'))
            if not data:
                break
            for row in data:
                try:
                    code = str(row.get('code', ''))
                    name = str(row.get('name', ''))
                    if not code or not name or 'ST' in name or '*' in name:
                        continue
                    if code.startswith(('87', '83', '430', '830', '8', '4')):
                        continue
                    price = float(row.get('trade', 0) or 0)
                    if price <= 0 or price > 50:
                        continue
                    all_stocks.append({
                        'code': code,
                        'name': name,
                        'price': price,
                        'change': float(row.get('changepercent', 0) or 0),
                        'turnover': float(row.get('turnoverratio', 0) or 0),
                        'volume_ratio': 1.0,  # will be filled by Tencent
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"  [Sina] page {page} failed: {e}")
            break
    return all_stocks

# ─── Tencent API: 批量获取量比和换手率 ─────────────────────────────────────
def fetch_tencent_vr_turnover(codes):
    """
    腾讯行情批量接口
    字段 38: 换手率
    字段 43: 量比
    """
    if not codes:
        return {}, {}
    vr_map = {}
    turnover_map = {}
    batch_size = 30
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        symbols = ",".join(("sh" if c.startswith("6") else "sz") + c for c in batch)
        url = f"https://qt.gtimg.cn/q={symbols}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Referer': 'https://gu.qq.com/'
        })
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                text = r.read().decode('gbk')
            for line in text.split(';'):
                if '=' not in line or '"' not in line:
                    continue
                sym = line.split('=')[0].replace('v_', '')
                code = sym[2:] if sym.startswith(('sh', 'sz')) else sym
                content = line.split('"')[1]
                parts = content.split('~')
                if len(parts) < 50:
                    continue
                try:
                    turn_str = parts[38]
                    turn = float(turn_str) if turn_str not in ('', '-', 'None') else 0.0
                    turnover_map[code] = turn
                except Exception:
                    turnover_map[code] = 0.0
                try:
                    vr_str = parts[43]
                    vr = float(vr_str) if vr_str not in ('', '-', 'None') else 1.0
                    vr_map[code] = vr if vr > 0 else 1.0
                except Exception:
                    vr_map[code] = 1.0
        except Exception as e:
            print(f"    [Tencent] batch error: {e}")
    return vr_map, turnover_map

# ─── 筛选逻辑 ───────────────────────────────────────────────────────────────
def screen_afternoon(stocks):
    """
    大力水手下午筛选
    涨幅3-5% + 量比>1 + 换手5-10% + 量能放大
    """
    # 第一步：涨幅3-5%
    s1 = [s for s in stocks if 3.0 <= s['change'] <= 5.0]
    print(f"  涨幅3-5%: {len(s1)} 只")

    # 第二步：量比>1
    s2 = [s for s in s1 if s['volume_ratio'] > 1.0]
    print(f"  量比>1: {len(s2)} 只")

    # 第三步：换手5-10%
    s3 = [s for s in s2 if 5.0 <= s['turnover'] <= 10.0]
    print(f"  换手5-10%: {len(s3)} 只")

    candidates = s3
    print(f"  量能放大候选: {len(candidates)} 只")

    # 评分：量比权重最高，换手次之，涨幅次之
    scored = []
    for s in candidates:
        vr = max(s['volume_ratio'], 1.0)
        score = vr * 10 + s['turnover'] * 2 + s['change'] * 5
        s['_score'] = score
        scored.append(s)

    scored.sort(key=lambda x: x['_score'], reverse=True)
    top15 = scored[:15]
    return top15

# ─── 钉钉推送 ───────────────────────────────────────────────────────────────
def send_dingtalk(content, title="大力水手下午筛选"):
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

# ─── 保存到 daily_picks.json ────────────────────────────────────────────────
def save_picks(stocks):
    today_str = date.today().strftime("%Y-%m-%d")
    picks_file = os.path.join(DASHBOARD_DIR, "daily_picks.json")

    try:
        data = {}
        if os.path.exists(picks_file):
            try:
                with open(picks_file, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                pass

        pick_list = []
        for s in stocks:
            pick_list.append({
                "code": s['code'],
                "name": s['name'],
                "price": s['price'],
                "change": round(s['change'], 2),
                "turnover": round(s['turnover'], 2),
                "volume_ratio": round(s.get('volume_ratio', 1.0), 2),
                "score": round(s.get('_score', s.get('score', 0)), 1),
                "level": "A"
            })

        data[today_str] = {
            "大力水手下午": {
                "time": datetime.datetime.now().strftime("%H:%M"),
                "count": len(pick_list),
                "picks": pick_list
            }
        }

        with open(picks_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  ✅ 已保存 {len(pick_list)} 只到 {picks_file}")
        return True
    except Exception as e:
        print(f"  ⚠️ 保存失败: {e}")
        return False

# ─── 主流程 ─────────────────────────────────────────────────────────────────
def run():
    today_str = date.today().strftime("%Y-%m-%d")
    now_str = datetime.datetime.now().strftime("%H:%M")

    print(f"\n{'='*55}")
    print(f"🧑‍✈️ 大力水手下午筛选")
    print(f"   {today_str} {now_str}")
    print(f"{'='*55}")

    # 1. 获取 Sina 数据（多页）
    print("\n📡 获取行情数据（Sina 分页，10页）...")
    stocks = fetch_sina_all(pages=10)
    print(f"  共获取 {len(stocks)} 只股票")

    if not stocks:
        print("\n❌ 未获取到任何股票数据")
        content = f"## 🧑‍✈️ 大力水手下午筛选\n\n> {today_str} {now_str}\n\n❌ 今日未获取到行情数据，请检查网络。"
        send_dingtalk(content)
        return

    # 2. 从涨幅3-8%候选股票中批量获取量比+换手率
    candidates = [s for s in stocks if 2.0 <= s['change'] <= 8.0]
    print(f"\n📡 批量获取量比+换手率（{len(candidates)} 只候选）...")
    vr_map, turn_map = fetch_tencent_vr_turnover([s['code'] for s in candidates])

    for s in candidates:
        s['volume_ratio'] = vr_map.get(s['code'], 1.0)
        s['turnover'] = turn_map.get(s['code'], s['turnover'])  # prefer Tencent

    # 3. 筛选
    print("\n🔍 执行筛选：涨幅3-5% + 量比>1 + 换手5-10%")
    top15 = screen_afternoon(stocks)

    if not top15:
        print("\n❌ 未找到符合条件的股票")
        content = (
            f"## 🧑‍✈️ 大力水手下午筛选\n\n"
            f"> {today_str} {now_str}\n\n"
            f"❌ 今日未找到符合条件的股票\n"
            f"(涨幅3-5% + 量比>1 + 换手5-10%)\n\n"
            f"获取行情: {len(stocks)} 只\n"
            f"涨幅2-8%: {len(candidates)} 只"
        )
        send_dingtalk(content)
        return

    print(f"\n🏆 精选 TOP10:")
    print(f"{'排名':<4} {'代码':<8} {'名称':<10} {'涨幅':>8} {'换手':>8} {'量比':>8} {'评分':>6}")
    print("-" * 55)
    for i, s in enumerate(top15[:10], 1):
        score = s.get('_score', 0)
        print(f"{i:<4} {s['code']:<8} {s['name']:<10} {s['change']:>+7.2f}% {s['turnover']:>7.2f}% {s.get('volume_ratio', 1.0):>7.2f}x {score:>6.1f}")

    # 4. 保存
    print("\n💾 保存选股结果...")
    save_picks(top15)

    # 5. 钉钉推送
    print("\n📲 发送钉钉通知...")
    top3_lines = []
    for i, s in enumerate(top15[:3], 1):
        top3_lines.append(
            f"{i}. **{s['name']}**({s['code']}) "
            f"+{s['change']:.2f}% | 换手{s['turnover']:.1f}% | "
            f"量比{s.get('volume_ratio', 1.0):.2f}x | 评分{int(s['_score'])}"
        )

    more_count = len(top15) - 3
    top10_table = (
        f"| 排名 | 代码 | 名称 | 涨幅 | 换手 | 量比 | 评分 |\n"
        f"|------|------|------|------|------|------|------|\n"
    )
    for i, s in enumerate(top15[:10], 1):
        top10_table += (
            f"| {i} | {s['code']} | {s['name']} | "
            f"+{s['change']:.2f}% | {s['turnover']:.1f}% | "
            f"{s.get('volume_ratio', 1.0):.2f}x | {int(s['_score'])} |\n"
        )

    content = (
        f"## 🧑‍✈️ 大力水手下午筛选\n\n"
        f"> {today_str} {now_str}\n\n"
        f"**筛选条件**：涨幅3-5% + 量比>1 + 换手5-10% + 量能放大\n\n"
        f"**达标数量**：{len(top15)} 只\n\n"
        f"---\n\n"
        f"### 🏆 重点关注 TOP3\n\n"
        + "\n\n".join(top3_lines) +
        (f"\n\n还有 **{more_count}** 只备选" if more_count > 0 else "") +
        f"\n\n---\n\n"
        f"### 📊 完整 TOP10\n\n"
        f"{top10_table}"
    )
    send_dingtalk(content)

    # 6. GitHub同步
    print("\n🔄 同步到 GitHub...")
    try:
        result = subprocess.run(
            [sys.executable, "sync_vibe_to_github.py"],
            cwd=WORKSPACE,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120
        )
        if result.returncode == 0:
            print("  ✅ GitHub同步成功")
        else:
            print(f"  ⚠️ GitHub同步失败: {result.stderr[:200] if result.stderr else result.stdout[:200]}")
    except Exception as e:
        print(f"  ⚠️ GitHub同步异常: {e}")

    print(f"\n{'='*55}")
    print("✅ 大力水手下午筛选完成")
    print(f"{'='*55}\n")

if __name__ == "__main__":
    run()
