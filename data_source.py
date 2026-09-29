# -*- coding: utf-8 -*-
"""数据源模块 - Sina + 东方财富（+ 腾讯行情兜底）"""
import json
import os
import re
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

    # 兜底：Sina 在 CI / 海外 runner 上常被拦截（整批超时返回空），
    # 改从仓库内已提交的 dashboard-gen/a_shares.json 全市场宇宙回退，
    # 保证 captain_fishing / popeye 等依赖「全A股列表」的脚本不空跑。
    if len(stocks) < 1000:
        print(f"[data_source] Sina 股票列表仅 {len(stocks)} 只（疑似被拦截），改用仓库内置宇宙 a_shares.json")
        stocks = _load_universe_from_json()

    return stocks


def _load_universe_from_json():
    """从仓库内置 dashboard-gen/a_shares.json 读取全A股宇宙（含 code/name），
    并按 get_a_stock_codes 同样的规则过滤 ST / 退市 / 非沪深A股。"""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "dashboard-gen", "a_shares.json"),
        os.path.join(here, "a_shares.json"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("stocks") if isinstance(data, dict) else data
            out, seen = [], set()
            for it in raw:
                code = str(it.get("code", "")).strip()
                if not code or len(code) != 6:
                    continue
                if not code.startswith(("60", "68", "00", "30")):
                    continue
                name = (it.get("name", "") or "").replace(" ", "")
                if "ST" in name.upper() or "退" in name:
                    continue
                if code in seen:
                    continue
                seen.add(code)
                out.append({"code": code, "name": name})
            if out:
                print(f"[data_source] 从 {os.path.basename(path)} 载入 {len(out)} 只")
                return out
        except Exception as e:
            print(f"[data_source] 读取 {path} 失败: {e}")
    return []

def fetch_sina_batch(codes, batch_size=800):
    """批量获取Sina行情"""
    result = {}

    # 测试 / 受限网络下可设 DS_SKIP_SINA=1 强制走 EM/腾讯兜底，避免 Sina 脏数据
    if os.environ.get("DS_SKIP_SINA"):
        return {}

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
    
    # Sina 被海外 runner 拦截时，用东方财富批量行情兜底（仅当 Sina 全空触发）
    if not result:
        print("[data_source] Sina 批量行情为空，改用东方财富备用源")
        result = fetch_em_batch_quotes(codes)
    # 东方财富也不可达时，腾讯行情作最后兜底（沙箱 / 受限网络下保证不空跑）
    if not result:
        print("[data_source] 东方财富行情也为空，改用腾讯行情兜底")
        result = fetch_tencent_quotes(codes)
    return result

def fetch_em_single_flow(code):
    """获取东方财富单只股票资金流（腾讯兜底）"""
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

    # 东财不可达 -> 腾讯兜底（流通市值/PE/PB）
    tb = fetch_tencent_flow_for_codes([code])
    if code in tb:
        d = tb[code]
        return {
            "code": code,
            "pe": d.get("pe", 0),
            "pb": d.get("pb", 0),
            "free_cap_yi": d.get("free_cap_yi", 0),
        }
    return {}

def fetch_tencent_flow_for_codes(codes):
    """腾讯 qt.gtimg.cn 兜底：补足流通市值 / PE / PB。

    说明：腾讯基础行情不含「主力净流入」，net_main 置 0；captain_fishing
    的评分逻辑对该字段有降级处理（net_main<=0 仅少加 0~10 分），不影响选股。
    字段索引（已用 贵州茅台/中国石油 验证）：
        39 = 市盈率(TTM)   44 = 流通市值(亿)
        45 = 总市值(亿)    46 = 市净率
    """
    if not codes:
        return {}
    # 探活：腾讯不可达时整批跳过，避免每个批次都等到超时（N×timeout 把 job 拖爆）
    try:
        probe = urllib.request.Request(
            "https://qt.gtimg.cn/q=sh600519",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        with urllib.request.urlopen(probe, timeout=6, context=ctx) as r:
            _txt = r.read().decode("gbk", errors="replace")
        if "sh600519" not in _txt:
            raise ValueError("tencent probe empty")
    except Exception as e:
        print(f"[data_source] 腾讯行情探活失败，跳过兜底: {e}")
        return {}

    result = {}
    prefix = lambda c: "sh" if c.startswith("6") else "sz"

    def chunk(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    for batch in chunk(codes, 150):
        symbols = ",".join(prefix(c) + c for c in batch)
        url = f"https://qt.gtimg.cn/q={symbols}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/"
            })
            with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
                text = r.read().decode("gbk", errors="replace")

            for line in text.strip().split("\n"):
                m = re.match(r'v_(sh|sz)(\d+)="', line)
                if not m:
                    continue
                code = m.group(2)
                try:
                    f = line.split('="')[1].rstrip('";').split("~")
                    if len(f) < 47:
                        continue
                    result[code] = {
                        "pe": safe_float(f[39]),
                        "pb": safe_float(f[46]),
                        "free_cap_yi": safe_float(f[44]),
                        "net_main_yi": 0.0,
                        "net_main_pct": 0.0,
                        "_src": "tencent",
                    }
                except Exception:
                    continue
        except Exception as e:
            print(f"腾讯资金流兜底批次失败: {e}")
            continue
    return result

def fetch_tencent_quotes(codes):
    """腾讯 qt.gtimg.cn 行情兜底（Sina + 东方财富均不可用时）。

    返回与 fetch_sina_batch 同结构的 dict（price / open / prev_close / high /
    low / volume / turnover / change_pct / name）。腾讯基础行情不含 high/low，
    用当前价填充，不影响 captain_fishing 的初筛（只用 price/change_pct/turnover）。
    """
    if not codes:
        return {}
    try:
        probe = urllib.request.Request(
            "https://qt.gtimg.cn/q=sh600519",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
        with urllib.request.urlopen(probe, timeout=6, context=ctx) as r:
            _txt = r.read().decode("gbk", errors="replace")
        if "sh600519" not in _txt:
            raise ValueError("tencent quote probe empty")
    except Exception as e:
        print(f"[data_source] 腾讯行情探活失败，跳过引号兜底: {e}")
        return {}

    result = {}
    prefix = lambda c: "sh" if c.startswith("6") else "sz"

    def chunk(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    for batch in chunk(codes, 150):
        symbols = ",".join(prefix(c) + c for c in batch)
        url = f"https://qt.gtimg.cn/q={symbols}"
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/"
            })
            with urllib.request.urlopen(req, timeout=6, context=ctx) as r:
                text = r.read().decode("gbk", errors="replace")
            for line in text.strip().split("\n"):
                m = re.match(r'v_(sh|sz)(\d+)="', line)
                if not m:
                    continue
                code = m.group(2)
                try:
                    f = line.split('="')[1].rstrip('";').split("~")
                    if len(f) < 35:
                        continue
                    price = safe_float(f[3])
                    result[code] = {
                        "name": f[1],
                        "price": price,
                        "open": safe_float(f[5]),
                        "prev_close": safe_float(f[4]),
                        "high": safe_float(f[33]),
                        "low": safe_float(f[34]),
                        "volume": safe_float(f[6]),
                        "turnover": safe_float(f[38]),   # 换手率%
                        "change_pct": safe_float(f[32]),  # 涨跌%
                        "_src": "tencent",
                    }
                except Exception:
                    continue
        except Exception as e:
            print(f"腾讯行情兜底批次失败: {e}")
            continue
    return result


def _em_reachable():
    """快速探活：东财 push2 是否可达。不可达时整批跳过东财循环，直接走腾讯兜底，
    避免被限流时每只股票都等到 5s 超时（N×5s 会把 30 分钟 job 拖爆）。"""
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=f116"
        req = urllib.request.Request(url, headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0"
        })
        with urllib.request.urlopen(req, timeout=4, context=ctx) as r:
            d = json.loads(r.read().decode("utf-8", errors="replace"))
        return bool(d and d.get("data"))
    except Exception:
        return False


def fetch_em_flow_for_codes(codes, delay=0.05):
    """批量获取资金流数据（东财优先，不可达时腾讯兜底）"""
    result = {}

    # 东财被限流/不可达时，整批跳过逐只 5s 超时，直接走腾讯兜底
    if _em_reachable():
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
    else:
        print("[data_source] 东财 push2 探活失败，整批改用腾讯兜底")

    # 东财缺失的（被限流 / 网络不可达）改用腾讯行情兜底，避免整批静默丢数据
    missing = [c for c in codes if c not in result]
    if missing:
        print(f"东财资金流缺失 {len(missing)}/{len(codes)} 只，改用腾讯兜底")
        result.update(fetch_tencent_flow_for_codes(missing))

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



def fetch_em_batch_quotes(codes):
    """东方财富批量行情（Sina 被拦截时的备用源），返回与 fetch_sina_batch 同结构的 dict。
    一次性用 clist 拉全市场再按 code 过滤，避免逐只请求 5000+ 次。仅 Sina 为空时触发。"""
    result = {}
    want = set(codes)
    try:
        for page in range(1, 4):
            url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=%d&pz=500&po=1&np=1"
                   "&fltt=2&invt=2&fid=f3"
                   "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
                   "&fields=f12,f14,f2,f3,f5,f6,f8,f15,f16,f17,f18") % page
            req = urllib.request.Request(url, headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                d = json.loads(r.read().decode("utf-8", errors="replace"))
            items = (d.get("data") or {}).get("diff") or []
            if not items:
                break
            for it in items:
                code = str(it.get("f12", ""))
                if not code or len(code) != 6:
                    continue
                result[code] = {
                    "name": it.get("f14") or "",
                    "price": safe_float(it.get("f2")),
                    "open": safe_float(it.get("f17")),
                    "prev_close": safe_float(it.get("f18")),
                    "high": safe_float(it.get("f15")),
                    "low": safe_float(it.get("f16")),
                    "volume": safe_float(it.get("f5")),
                    "turnover": safe_float(it.get("f8")),
                    "change_pct": safe_float(it.get("f3")),
                }
            if len(items) < 500:
                break
    except Exception as e:
        print(f"获取EM行情失败: {e}")
    if want:
        return {k: v for k, v in result.items() if k in want}
    return result