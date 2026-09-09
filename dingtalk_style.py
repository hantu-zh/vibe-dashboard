# -*- coding: utf-8 -*-
"""钉钉推送样式模块"""
import json
import urllib.request
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

DINGTALK_WEBHOOK = "055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"

def send(title, content):
    """发送钉钉消息"""
    url = f"https://oapi.dingtalk.com/robot/send?access_token={DINGTALK_WEBHOOK}"
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": content
        }
    }
    
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json; charset=utf-8"
        }, method="POST")
        
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            resp = json.loads(r.read().decode())
            return resp.get("errcode") == 0
    except Exception as e:
        print(f"钉钉推送失败: {e}")
        return False

def header(strategy_name, subtitle="", extra="", channels=None):
    """生成报告头部"""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    title_map = {
        "captain_fishing": "⛵ 船长钓鱼战法",
        "jack_captain": "🏴‍☠️ 杰克船长",
    }
    
    display_title = title_map.get(strategy_name, strategy_name)
    
    lines = [
        "---",
        f"### {display_title}",
        ""
    ]
    
    if subtitle:
        lines.append(f"**{subtitle}**")
        lines.append("")
    
    lines.append(f"📅 {now}")
    
    if extra:
        lines.append(f"📊 {extra}")
    
    lines.append("---")
    lines.append("")
    
    return "\n".join(lines)

def footer(disclaimer, channels_ok=None):
    """生成报告尾部"""
    lines = [
        "",
        "---",
        f"*{disclaimer}*"
    ]
    return "\n".join(lines)

def stock_table(stocks, cols=None, title="📈 选股结果"):
    """生成股票表格"""
    if not stocks:
        return f"**{title}**\n\n暂无符合条件的股票"
    
    if cols is None:
        cols = [
            ("name", "名称", lambda v: f"**{v}**"),
            ("code", "代码", lambda v: str(v)),
            ("price", "现价", lambda v: f"{float(v):.2f}"),
            ("change", "涨幅", lambda v: f"{float(v):+.2f}%"),
        ]
    
    # 构建表头
    header_parts = ["|"] + [f" {c[1]} |" for c in cols] + [""]
    separator_parts = ["|"] + [":--:|" for _ in cols] + [""]
    
    lines = [f"**{title}**", ""]
    lines.append("".join(header_parts))
    lines.append("".join(separator_parts))
    
    # 构建数据行
    for s in stocks[:10]:
        row_parts = ["|"]
        for col_key, _, formatter in cols:
            val = s.get(col_key, "-")
            try:
                row_parts.append(f" {formatter(val)} |")
            except:
                row_parts.append(f" {val} |")
        lines.append("".join(row_parts))
    
    return "\n".join(lines)

def highlight_card(stocks, title="🏆 精选推荐", max_n=5):
    """生成高亮卡片"""
    if not stocks:
        return f"**{title}**\n\n暂无"
    
    lines = [f"**{title}**", ""]
    
    for i, s in enumerate(stocks[:max_n], 1):
        name = s.get("name", "-")
        code = s.get("code", "-")
        change = s.get("change", 0)
        score = s.get("score", 0)
        action = s.get("action", "关注")
        
        try:
            change_str = f"{float(change):+.2f}%"
            score_str = f"{int(score)}分"
        except:
            change_str = str(change)
            score_str = str(score)
        
        lines.append(f"**{i}. {name}** ({code})")
        lines.append(f"   涨幅: {change_str} | 评分: {score_str}")
        lines.append(f"   操作: {action}")
        lines.append("")
    
    return "\n".join(lines)

def index_block(indices):
    """生成指数行情块"""
    if not indices:
        return ""
    
    lines = ["**📊 大盘指数**", ""]
    
    for name, data in indices.items():
        price = data.get("price", 0)
        change_pct = data.get("change_pct", 0)
        
        emoji = "🔴" if change_pct < 0 else "🟢"
        
        try:
            lines.append(f"{emoji} **{name}**: {float(price):.2f} ({float(change_pct):+.2f}%)")
        except:
            lines.append(f"{emoji} **{name}**: {price} ({change_pct})")
    
    lines.append("")
    return "\n".join(lines)

def empty_result(strategy_name):
    """空结果提示"""
    return "⚠️ 今日未找到符合条件的股票，请明日再试。"
