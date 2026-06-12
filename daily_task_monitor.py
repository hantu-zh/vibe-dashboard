# -*- coding: utf-8 -*-
"""
每日选股任务监测报告
检查三大维度：
1. 定时任务是否按时执行（cron状态）
2. 钉钉+网页是否都推送成功
3. 钉钉与网页数据是否一致

每日 17:30 自动运行（所有交易时段任务结束后）
"""
import sys, json, re, os, time, urllib.request, ssl
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"
WS = r"C:\Users\china\.qclaw\workspace"
DP_FILE = os.path.join(WS, "daily_picks.json")
US_FILE = os.path.join(WS, "us_picks.json")
HTML_FILE = os.path.join(WS, "vibe-dashboard", "index.html")
CRON_STATE_FILE = os.path.join(WS, "cron_monitor_state.json")

# 选股类任务定义（名称 → 预期时段 → 对应策略key）
STOCK_TASKS = [
    {"name": "高欣-资金净流入",     "time": "08:05", "key": "高欣资金净流入",   "type": "A股"},
    {"name": "高欣-季度环比增长",   "time": "08:10", "key": "高欣季度环比增长", "type": "A股"},
    {"name": "杰克船长选股信号",     "time": "08:45", "key": "杰克船长",         "type": "A股"},
    {"name": "大力水手Step1",        "time": "09:00", "key": "财报+技术双滤排名", "type": "A股"},
    {"name": "大力水手Step2",        "time": "09:10", "key": "财报+技术双滤排名", "type": "A股"},
    {"name": "大力水手训练",         "time": "09:05", "key": "大力水手训练",     "type": "A股"},
    {"name": "追涨强势股-10:00",    "time": "10:00", "key": "追涨强势股",       "type": "A股"},
    {"name": "美股选股推荐",         "time": "10:00", "key": "美股",             "type": "美股"},
    {"name": "RPS早盘快照",          "time": "10:00", "key": "RPS热力图",        "type": "报告"},
    {"name": "船长钓鱼战法",         "time": "10:30", "key": "船长钓鱼战法",     "type": "A股"},
    {"name": "追涨强势股-12:00",    "time": "12:00", "key": "追涨强势股",       "type": "A股"},
    {"name": "A股午间收盘点评",      "time": "12:00", "key": "午间点评",         "type": "报告"},
    {"name": "高欣-唯科科技形态",   "time": "14:00", "key": "高欣唯科科技形态", "type": "A股"},
    {"name": "追涨强势股-14:00",    "time": "14:00", "key": "追涨强势股",       "type": "A股"},
    {"name": "陈小群战法",           "time": "14:30", "key": "陈小群战法四步选股","type": "A股"},
    {"name": "预测明日涨停",         "time": "14:30", "key": "预测明日涨停",     "type": "A股"},
    {"name": "大力水手下午筛选",     "time": "14:50", "key": "大力水手下午",     "type": "A股"},
    {"name": "RPS收盘快照",          "time": "15:30", "key": "RPS热力图",        "type": "报告"},
    {"name": "强势股精选",           "time": "15:30", "key": "强势股精选",       "type": "A股"},
    {"name": "八大维度研报",         "time": "15:45", "key": "八大维度研报",     "type": "报告"},
    {"name": "慢热板块Dashboard",    "time": "16:00", "key": "慢热板块",         "type": "报告"},
    {"name": "市场深度解读",         "time": "16:00", "key": "市场深度解读",     "type": "报告"},
]


def send_dingtalk(title, content):
    payload = {
        "msgtype": "markdown",
        "markdown": {"title": title, "text": content}
    }
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        DINGTALK_WEBHOOK, data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as r:
            result = json.loads(r.read().decode('utf-8'))
            ok = result.get('errcode') == 0
            print(f"  钉钉推送: {'OK' if ok else 'FAIL'} {result.get('errmsg','')}")
            return ok
    except Exception as e:
        print(f"  钉钉推送失败: {e}")
        return False


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}


def extract_embed_data(html_path):
    """从 index.html 提取 daily-picks-embed 和 us-picks-embed"""
    daily_embed = None
    us_embed = None
    if not os.path.exists(html_path):
        return daily_embed, us_embed
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # A股
    m = re.search(r'id="daily-picks-embed"[^>]*>\s*(\{.+?)\s*</script>', content, re.DOTALL)
    if m:
        try:
            daily_embed = json.loads(m.group(1))
        except:
            pass
    # 美股
    m = re.search(r'id="us-picks-embed">\s*(\{.*?\})\s*</script>', content, re.DOTALL)
    if m:
        try:
            us_embed = json.loads(m.group(1))
        except:
            pass
    return daily_embed, us_embed


def check_dimension1_cron_status():
    """维度1: 检查cron任务是否按时执行"""
    results = []
    cron_state = load_json(CRON_STATE_FILE)
    # 从cron_state获取每个任务的最新运行状态
    # 这个由外部agentTurn填入（因为Python无法直接调OpenClaw API）
    for task in STOCK_TASKS:
        name = task["name"]
        state = cron_state.get(name, {})
        status = state.get("lastRunStatus", "unknown")
        duration = state.get("lastDurationMs", 0)
        error = state.get("lastError", "")
        consecutiveErrors = state.get("consecutiveErrors", 0)

        if consecutiveErrors > 0:
            results.append(("❌", name, f"连续{consecutiveErrors}次失败: {error}"))
        elif status == "ok":
            dur_s = duration / 1000
            results.append(("✅", name, f"成功({dur_s:.0f}s)"))
        elif status == "error":
            results.append(("❌", name, f"失败: {error}"))
        else:
            results.append(("❓", name, "状态未知"))

    return results


def find_data(data_dict, key):
    """查找策略数据：先精确匹配，再前缀匹配（如追涨强势股 -> 追涨强势股-10:00）"""
    if key in data_dict:
        return data_dict[key]
    # 前缀匹配
    prefix = key + "-"
    for k, v in data_dict.items():
        if k.startswith(prefix):
            return v
    return {}


def check_dimension2_delivery(today, dp_data, daily_embed, us_data, us_embed):
    """维度2: 钉钉+网页是否都推送成功 - v2: 区分0只正常和未执行"""
    results = []
    now_time = datetime.now().strftime("%H:%M")

    # 检查A股策略 - daily_picks.json有key=脚本已跑，count=0是正常空仓
    local_today = dp_data.get(today, {})
    web_today = daily_embed.get(today, {}) if daily_embed else {}

    for task in STOCK_TASKS:
        if task["type"] == "报告":
            continue
        key = task["key"]
        name = task["name"]
        task_time = task["time"]

        # 跳过未到执行时间的任务
        if task_time > now_time:
            continue

        # 本地数据（=钉钉推送的数据源）
        lt = find_data(local_today, key)
        l_exists = isinstance(lt, dict) and bool(lt)  # key存在且有数据（即使是空picks=[]也是脚本跑过的）
        l_count = lt.get('count', len(lt.get('picks', []))) if isinstance(lt, dict) else 0

        # 网页数据
        wt = find_data(web_today, key)
        w_count = wt.get('count', len(wt.get('picks', []))) if isinstance(wt, dict) else 0

        if not l_exists:
            # key完全缺失 = 脚本根本没跑或写入失败
            results.append(("❌", name, f"未执行({task_time}计划, daily_picks无数据)"))
        elif l_count == 0:
            # key存在但0只 = 脚本正常执行，今日无符合条件的股票
            web_ok = w_count == 0  # 网页也为0是正常的
            web_label = "网页✓" if web_ok else f"网页异常({w_count}只)"
            results.append(("✅", name, f"0只(正常) {web_label}"))
        elif l_count > 0 and w_count > 0:
            results.append(("✅", name, f"钉钉✓ 网页✓ ({l_count}只)"))
        elif l_count > 0 and w_count == 0:
            results.append(("⚠️", name, f"钉钉✓({l_count}只) 网页✗(0只)"))
        else:
            results.append(("⚠️", name, f"钉钉✗ 网页✓ (本地0只,网页{w_count}只)"))

    # 美股
    us_date = us_data.get('date', '') if us_data else ''
    us_picks_count = 0
    if us_data:
        for m in ['method1', 'method2']:
            us_picks_count += us_data.get(m, {}).get('count', 0)

    us_web_count = 0
    if us_embed:
        for m in ['method1', 'method2']:
            us_web_count += len(us_embed.get(m, {}).get('picks', []))

    if us_date == today:
        if us_picks_count > 0 and us_web_count > 0:
            results.append(("✅", "美股选股", f"钉钉✓ 网页✓ ({us_picks_count}只)"))
        elif us_picks_count > 0:
            results.append(("⚠️", "美股选股", f"钉钉✓ 网页✗"))
        else:
            results.append(("❌", "美股选股", "钉钉✗ 网页✗"))
    else:
        results.append(("❓", "美股选股", f"数据日期={us_date}"))

    return results


def check_dimension3_consistency(today, dp_data, daily_embed, us_data, us_embed):
    """维度3: 钉钉与网页数据一致性"""
    results = []
    local_today = dp_data.get(today, {})
    web_today = daily_embed.get(today, {}) if daily_embed else {}

    # A股选股类
    for task in STOCK_TASKS:
        if task["type"] != "A股":
            continue
        key = task["key"]
        name = task["name"]

        lt = find_data(local_today, key)
        wt = find_data(web_today, key)

        l_picks = lt.get('picks', []) if isinstance(lt, dict) else []
        w_picks = wt.get('picks', []) if isinstance(wt, dict) else []

        l_count = len(l_picks)
        w_count = len(w_picks)
        l_codes = [p.get('code', '') for p in l_picks[:5]]
        w_codes = [p.get('code', '') for p in w_picks[:5]]

        if l_count == 0 and w_count == 0:
            continue  # 都没数据，跳过
        if l_count == 0 or w_count == 0:
            continue  # 维度2已经报告了

        issues = []
        if l_count != w_count:
            issues.append(f"数量({l_count} vs {w_count})")
        if set(l_codes) != set(w_codes) and l_codes and w_codes:
            diff = set(l_codes) ^ set(w_codes)
            issues.append(f"Top5差异({diff})")

        if issues:
            results.append(("❌", name, "；".join(issues)))
        else:
            results.append(("✅", name, f"{l_count}只一致"))

    # 美股
    if us_data and us_embed:
        us_date = us_data.get('date', '')
        if us_date == today:
            for method, label in [('method1', '量突破'), ('method2', '温和上涨')]:
                m_local = us_data.get(method, {})
                m_web = us_embed.get(method, {})
                l_syms = [p.get('symbol', '') for p in m_local.get('picks', [])[:5]]
                w_syms = [p.get('symbol', '') for p in m_web.get('picks', [])[:5]]
                l_c = m_local.get('count', len(m_local.get('picks', [])))
                w_c = len(m_web.get('picks', []))

                if l_c == 0 and w_c == 0:
                    continue
                if set(l_syms) != set(w_syms) and l_syms and w_syms:
                    diff = set(l_syms) ^ set(w_syms)
                    results.append(("❌", f"美股{label}", f"Top5差异({diff})"))
                elif l_c != w_c:
                    results.append(("⚠️", f"美股{label}", f"数量({l_c} vs {w_c})"))
                else:
                    results.append(("✅", f"美股{label}", f"{l_c}只一致"))

    return results


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    weekday = datetime.now().weekday()
    weekday_str = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday]
    now = datetime.now().strftime("%H:%M")

    print(f"\n{'='*60}")
    print(f"📊 每日选股任务监测报告")
    print(f"   {today} {weekday_str} {now}")
    print(f"{'='*60}\n")

    # 周末跳过
    if weekday >= 5:
        print("  周末，无交易时段任务，跳过监测")
        return

    # 加载数据
    dp_data = load_json(DP_FILE)
    us_data = load_json(US_FILE)
    daily_embed, us_embed = extract_embed_data(HTML_FILE)

    # 维度1: cron执行状态
    print("--- 维度1: 定时任务执行状态 ---")
    d1 = check_dimension1_cron_status()
    for icon, name, detail in d1:
        print(f"  {icon} {name}: {detail}")

    # 维度2: 钉钉+网页推送
    print("\n--- 维度2: 钉钉+网页推送状态 ---")
    d2 = check_dimension2_delivery(today, dp_data, daily_embed, us_data, us_embed)
    for icon, name, detail in d2:
        print(f"  {icon} {name}: {detail}")

    # 维度3: 数据一致性
    print("\n--- 维度3: 钉钉与网页数据一致性 ---")
    d3 = check_dimension3_consistency(today, dp_data, daily_embed, us_data, us_embed)
    if not d3:
        print("  ⏭️ 无数据可比对")
    for icon, name, detail in d3:
        print(f"  {icon} {name}: {detail}")

    # 统计
    d1_fail = sum(1 for i, _, _ in d1 if i == "❌")
    d2_fail = sum(1 for i, _, _ in d2 if i in ("❌", "⚠️"))
    d3_fail = sum(1 for i, _, _ in d3 if i == "❌")
    total_issues = d1_fail + d2_fail + d3_fail

    # 生成钉钉报告
    lines = [
        f"## 📊 每日选股任务监测",
        f"",
        f"**{today} {weekday_str}**",
        f"",
    ]

    if total_issues == 0:
        lines.append("### ✅ 全部正常")
        lines.append("")
        for _, name, detail in d1:
            lines.append(f"- ✅ {name}: {detail}")
    else:
        if d1_fail > 0:
            lines.append(f"### ❌ 执行失败 ({d1_fail}项)")
            for icon, name, detail in d1:
                if icon == "❌":
                    lines.append(f"- {icon} {name}: {detail}")
            lines.append("")

        if d2_fail > 0:
            lines.append(f"### ⚠️ 推送异常 ({d2_fail}项)")
            for icon, name, detail in d2:
                if icon in ("❌", "⚠️"):
                    lines.append(f"- {icon} {name}: {detail}")
            lines.append("")

        if d3_fail > 0:
            lines.append(f"### ❌ 数据不一致 ({d3_fail}项)")
            for icon, name, detail in d3:
                if icon == "❌":
                    lines.append(f"- {icon} {name}: {detail}")
            lines.append("")

        lines.append("### 正常项")
        for _, name, detail in d1 + d2 + d3:
            if True:  # 简化：只列失败的
                pass

    # 推送钉钉
    if total_issues > 0:
        send_dingtalk(f"⚠️ 选股监测异常({total_issues}项)", "\n".join(lines))
    else:
        send_dingtalk("✅ 选股监测全部正常", "\n".join(lines))

    print(f"\n{'='*60}")
    print(f"监测完成: {'✅ 全部正常' if total_issues == 0 else f'❌ {total_issues}项异常'}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
