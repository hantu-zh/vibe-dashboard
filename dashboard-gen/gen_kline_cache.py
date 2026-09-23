#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 kline_cache.json：全站 K 线弹窗所需的个股近30日K线缓存。

数据源：东方财富 push2his 公开接口（优先）+ 腾讯/新浪兜底（见 kline_src.py），无需鉴权。
代码池来源（全部为仓库内已有的公开信号文件，无本机独占依赖，可纯 GitHub Actions 化）：
  - strongbuy_data.json      强买候选（yimeng + stocks）
  - daohuoxian_data.json     盗火线候选
  - weizhentian_history.json 威震天历史冻结快照（全量，避免被选出后消失导致弹窗无数据）
  - kline_cache.json 自身历史（保留已有代码，避免回退丢数据）
输出：仓库根 kline_cache.json（前端运行时 fetch 此文件，与 patch_kline_all.js 的 arr2obj 对应）。
"""
import json, os, re, time, datetime, urllib.request, urllib.parse

WS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(WS)          # 仓库根（dashboard-gen 的上一级）
EM_HEAD = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}

def secid(code):
    return ("1." if code[0] == "6" else "0.") + code

def em_json(url, tries=4):
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=EM_HEAD)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = repr(e)
            time.sleep(1.5 * (i + 1))
    print("  em fail", url[:70], last)
    return {}

def get_kline(code, n=30):
    # 东财限流时自动兜底腾讯/新浪（见 kline_src.py），保证看板 K 线覆盖
    try:
        import kline_src
        nm, rows, src = kline_src.get_rows(code, n)
        if rows:
            return (nm or ""), rows
    except Exception:
        pass
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode({
        "secid": secid(code), "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56",
        "klt": "101", "fqt": "0", "beg": "0", "end": "20500101"})
    d = em_json(url)
    dd = d.get("data") or {}
    kls = dd.get("klines") or []
    name = dd.get("name") or ""
    out = []
    for s in kls:
        p = s.split(",")
        if len(p) < 6:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[2]), float(p[4]), float(p[3]), int(p[5])])
        except Exception:
            continue
    return name, out[-n:]

# ── 收集去重代码池（仅依赖仓库内公开信号文件） ──
codes = {}
def add(code, name=""):
    code = str(code or "").strip()
    if re.match(r"^\d{6}$", code):
        codes.setdefault(code, name or codes.get(code, ""))

# 强买候选
try:
    sb = json.load(open(os.path.join(REPO, "strongbuy_data.json"), encoding="utf-8"))
    for lst in ("yimeng", "stocks"):
        for it in (sb.get(lst) or []):
            if isinstance(it, dict):
                add(it.get("code"), it.get("name", ""))
except Exception as e:
    print("strongbuy read fail:", e)

# 盗火线候选
djf = os.path.join(REPO, "daohuoxian_data.json")
if os.path.exists(djf):
    try:
        for s in json.load(open(djf, encoding="utf-8")).get("daohuoxian", []):
            add(s.get("code"), s.get("name", ""))
    except Exception as e:
        print("daohuoxian read fail:", e)

# 威震天历史冻结快照（全量日期）
wjf = os.path.join(REPO, "weizhentian_history.json")
if os.path.exists(wjf):
    try:
        wdata = json.load(open(wjf, encoding="utf-8")).get("data", {})
        for dt, picks in wdata.items():
            for p in picks:
                if not isinstance(p, dict):
                    continue
                m = re.search(r"(\d{6})", str(p.get("code", "")))
                if m:
                    add(m.group(1), p.get("name", ""))
    except Exception as e:
        print("weizhentian read fail:", e)

# 自身历史（保留已有代码，避免回退丢数据）
oldf = os.path.join(REPO, "kline_cache.json")
if os.path.exists(oldf):
    try:
        old = json.load(open(oldf, encoding="utf-8")).get("stocks", {}) or {}
        for c, info in old.items():
            nm = (info or {}).get("name", "") if isinstance(info, dict) else ""
            add(c, nm)
    except Exception as e:
        print("old kline_cache read fail:", e)

stock_codes = sorted(codes)
print("unique stock codes:", len(stock_codes))

cache = {}
fails = 0
for i, code in enumerate(stock_codes, 1):
    name, kl = get_kline(code)
    if not kl:
        fails += 1
        if fails <= 20:
            print(f"  [{i}/{len(stock_codes)}] skip(no data) {code}")
        continue
    if not codes.get(code) and name:
        codes[code] = name
    cache[code] = {"name": codes.get(code, name), "kline": kl}
    print(f"  [{i}/{len(stock_codes)}] ok {code} {codes.get(code,'')} {len(kl)}bars")

out = {"updated": datetime.date.today().isoformat(), "count": len(cache), "stocks": cache}
out_path = os.path.join(REPO, "kline_cache.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
print("wrote", out_path, ":", len(cache), "stocks, size %.1fKB" % (os.path.getsize(out_path) / 1024),
      "| fails:", fails)
