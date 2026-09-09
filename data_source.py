# -*- coding: utf-8 -*-
"""数据源模块 - Sina + 东方财富"""
import json
import urllib.request
import ssl
import time
from datetime import datetime

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def safe_float(val, default=0.0):
    """安全转换为浮点数"""
    try:
        return float(val) if val else default
    except:
        return default

def get_a_stock_codes():
    """获取全A股非ST非退市股票列表

    注意：新浪该接口服务端强制单页最多返回 100 条，num 参数设多大都无效
    （实测 num=100/500/1000/5000 均只返回 100 条）。原实现只请求 page=1，
    导致全市场只拿到约 95 只股票，选股结果严重失真。此处改为逐页翻取。
    """
    base = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "Market_Center.getHQNodeData?page={page}&num=100&sort=code&asc=1&node=hs_a")

    stocks = []
    seen = set()
    page = 1
    max_page = 80          # 安全上限：全A股约 5400 只 / 100 ≈ 54 页，留足余量
    empty_pages = 0

    while page <= max_page:
        try:
            req = urllib.request.Request(base.format(page=page), headers={
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                items = json.loads(r.read().decode("gbk", errors="replace"))
        except Exception as e:
            print(f"获取股票列表失败(page={page}): {e}")
            break

        if not items:
            empty_pages += 1
            if empty_pages >= 2:
                break          # 连续两页为空视为取完
            page += 1
            continue
        empty_pages = 0

        for item in items:
            code = item.get("code", "")
            if not code:
                continue
            name = (item.get("name", "") or "").replace(" ", "")

            # 只保留沪深A股：60 沪主板 / 68 科创板 / 00 深主板 / 30 创业板
            # 用白名单而非黑名单，可彻底排除：
            #   北交所 43x/83x/87x/88x/920xxx、B股 90x/20x、新三板 4xx/8xx
            # 且对将来新增的北交所代码段天然免疫
            if not code.startswith(("60", "68", "00", "30")):
                continue

            # 过滤 ST / *ST / S*ST / ST 及退市（含退市整理期「XX退」）
            if "ST" in name.upper() or "退" in name:
                continue

            if code in seen:
                continue
            seen.add(code)
            stocks.append({"code": code, "name": name})

        if len(items) < 100:
            break              # 最后一页
        page += 1
        time.sleep(0.08)       # 轻微限速，避免被封

    return stocks

def fetch_sina_batch(codes, batch_size=800):
    """批量获取Sina行情"""
    result = {}
    
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        symbols = ",".join([f"sh{c}" if c.startswith("6") else f"sz{c}" for c in batch])
        url = f"https://hq.sinajs.cn/list={symbols}"
        
        try:
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                text = r.read().decode("gbk", errors="replace")
            
            for line in text.strip().split("\n"):
                if not line or "=" not in line:
                    continue
                try:
                    code_part = line.split('="')[0].split("hq_str_")[1]
                    code = code_part[2:]  # 去掉sh/sz前缀
                    data = line.split('="')[1].rstrip('";')
                    fields = data.split(",")
                    
                    if len(fields) >= 32:
                        name = fields[0]
                        open_p = safe_float(fields[1])
                        prev_close = safe_float(fields[2])
                        price = safe_float(fields[3])
                        high = safe_float(fields[4])
                        low = safe_float(fields[5])
                        volume = safe_float(fields[8])
                        turnover = safe_float(fields[9]) * 100 / (safe_float(fields[3]) * safe_float(fields[10])) if safe_float(fields[3]) > 0 else 0
                        
                        change_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                        
                        result[code] = {
                            "name": name,
                            "price": price,
                            "open": open_p,
                            "prev_close": prev_close,
                            "high": high,
                            "low": low,
                            "volume": volume,
                            "turnover": turnover,
                            "change_pct": change_pct
                        }
                except:
                    continue
            
            if i + batch_size < len(codes):
                time.sleep(0.1)
                
        except Exception as e:
            print(f"获取Sina行情批次失败: {e}")
            continue
    
    return result

def fetch_em_single_flow(code):
    """获取东方财富单只股票资金流"""
    secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f43,f169,f170,f46,f44,f51,f168,f47,f48,f60,f45,f52,f50,f49,f167,f117,f71,f113,f114,f115,f152"
    
    try:
        req = urllib.request.Request(url, headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
            data = json.loads(r.read().decode())
        
        if data and "data" in data and data["data"]:
            d = data["data"]
            return {
                "code": code,
                "pe": safe_float(d.get("f162")),
                "pb": safe_float(d.get("f167")),
                "free_cap_yi": safe_float(d.get("f116")),  # 流通市值(亿)
            }
    except:
        pass
    
    return {}

def fetch_em_flow_for_codes(codes, delay=0.05):
    """批量获取资金流数据"""
    result = {}
    
    for i, code in enumerate(codes):
        try:
            secid = f"1.{code}" if code.startswith("6") else f"0.{code}"
            url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f162,f167,f116,f66,f69,f72,f78,f84,f87"
            
            req = urllib.request.Request(url, headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0"
            })
            
            with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                data = json.loads(r.read().decode())
            
            if data and "data" in data and data["data"]:
                d = data["data"]
                # f116是流通市值（元），需要转换为亿
                free_cap_yuan = safe_float(d.get("f116"))
                free_cap_yi = free_cap_yuan / 100000000 if free_cap_yuan > 0 else 0
                
                result[code] = {
                    "pe": safe_float(d.get("f162")),
                    "pb": safe_float(d.get("f167")),
                    "free_cap_yi": free_cap_yi,
                    "net_main_yi": safe_float(d.get("f66")) / 100000000,  # 主力净流入(亿)
                    "net_main_pct": safe_float(d.get("f69")),  # 主力净占比
                }
        except:
            pass
        
        if delay > 0 and (i + 1) % 10 == 0:
            time.sleep(delay)
    
    return result

def fetch_em_indices():
    """获取大盘指数"""
    indices = {}
    codes = {
        "sh000001": "上证指数",
        "sz399001": "深证成指", 
        "sz399006": "创业板指"
    }
    
    for code, name in codes.items():
        url = f"https://hq.sinajs.cn/list={code}"
        try:
            req = urllib.request.Request(url, headers={
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
                text = r.read().decode("gbk", errors="replace")
            
            if "=" in text and '"' in text:
                data = text.split('="')[1].rstrip('";')
                fields = data.split(",")
                if len(fields) >= 3:
                    price = safe_float(fields[3])
                    prev_close = safe_float(fields[2])
                    change_pct = (price - prev_close) / prev_close * 100 if prev_close > 0 else 0
                    indices[name] = {
                        "price": price,
                        "change_pct": change_pct
                    }
        except:
            pass
    
    return indices
