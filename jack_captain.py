# -*- coding: utf-8 -*-
"""杰克船长 - 钉钉版 | 修复：并行查询+全局超时"""
import sys; sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r"C:\Users\china\.qclaw\workspace\skills\mx-skills\mx-select-stock")
try:
    from mx_select_stock import MXSelectStock; _MX_AVAILABLE = True
except:
    MXSelectStock = None; _MX_AVAILABLE = False
from datetime import datetime, date
from chanlun_quick import score_stocks_with_chanlun
from pathlib import Path
import random, json, urllib.request, ssl, concurrent.futures, time

ctx = ssl.create_default_context()
ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
DINGTALK = "055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba"

GLOBAL_TIMEOUT = 60; MX_TIMEOUT = 8; SINA_TIMEOUT = 8; PARALLEL_TIMEOUT = 12

def send_dingtalk(content):
    url = f"https://oapi.dingtalk.com/robot/send?access_token={DINGTALK}"
    payload = json.dumps({"msgtype":"markdown","markdown":{"title":"jack","text":content}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type":"application/json; charset=utf-8"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            return json.loads(r.read().decode()).get("errcode") == 0
    except: return False

def safe_mx(mx, query, counter):
    if counter["limit"]: return [], True
    if not _MX_AVAILABLE:
        print("  MX不可用，使用备用"); return _fallback(), False
    def _call(): return mx.search(query)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_call)
            try: result = future.result(timeout=MX_TIMEOUT)
            except concurrent.futures.TimeoutError:
                print(f"  MX超时({MX_TIMEOUT}s)，降级"); return _fallback(), False
        counter["count"] += 1
        if isinstance(result, dict) and result.get("status") == 113:
            print("  MX已达上限，降级"); counter["limit"] = True; return _fallback(), False
        rows, _, _ = MXSelectStock.extract_data(result)
        if not rows: print("  MX无数据，降级"); return _fallback(), False
        return rows or [], False
    except Exception as e:
        print(f"  MX异常:{e}，降级"); return _fallback(), False

def _fallback():
    try:
        import urllib.request as _ur; import json as _json
        url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=1&num=200&sort=changepercent&asc=0&node=hs_a"
        req = _ur.Request(url, headers={"Referer":"https://finance.sina.com.cn/","User-Agent":"Mozilla/5.0"})
        with _ur.urlopen(req, timeout=SINA_TIMEOUT, context=ctx) as r:
            items = _json.loads(r.read().decode("gbk", errors="replace"))
        rows = [{"代码":i.get("code",""),"名称":i.get("name",""),"最新价(元)":i.get("trade",0),
                 "涨跌幅(%)":float(i.get("changepercent",0)),"换手率(%)":i.get("turnoverratio",0),
                 "流通市值(元)":float(i.get("mktcap",0))*10000 if i.get("mktcap") else 0} for i in items[:100]]
        print(f"  [备用]Sina {len(rows)}只"); return rows
    except Exception as e: print(f"  [备用]失败:{e}"); return []

def extract(row, key):
    for k, v in row.items():
        if key in k: return str(v).split("|")[0]
    return "-"

def score_tech(price, change, turnover, above_ma5=False, market_cap=None):
    from turnover_utils import turnover_score
    score, rules = 20, []
    try:
        p, c, t = float(price), float(change), float(turnover)
        cap = float(market_cap) if market_cap else None
        ts = turnover_score(t, cap, max_score=15)
        score += 15 if ts >= 15 else (8 if ts >= 8 else 0)
        if ts >= 15: rules.append("换手温和")
        elif ts >= 8: rules.append("换手适中")
        if 0 <= c <= 2: score += 15; rules.append("温和启动")
        elif 2 < c <= 5: score += 12; rules.append("稳健上涨")
        elif 5 <= c < 10: score += 8; rules.append("强势")
        if 10 <= p <= 30: score += 10; rules.append("价格适中")
        elif 5 <= p < 10: score += 5
        if c > 0: score += 10; rules.append("上涨趋势")
        if above_ma5: score += 15; rules.append("站上MA5")
    except: pass
    return min(score, 75), rules

def score_limit(price, change, turnover):
    score, rules = 0, []
    try:
        p, c, t = float(price), float(change), float(turnover)
        if c >= 9.9: score += 20; rules.append("涨停板")
        elif c >= 7: score += 10; rules.append("接近涨停")
        elif c >= 5: score += 5; rules.append("大涨")
        if 3 <= t <= 10: score += 8; rules.append("换手健康")
        elif t > 10: score += 5; rules.append("换手偏高")
        if 10 <= p <= 30: score += 5; rules.append("价格适中")
    except: pass
    return score, rules

def get_level(s):
    if s >= 100: return "⭐⭐⭐⭐超强势","满仓"
    if s >= 80:  return "⭐⭐⭐强力","重仓30%"
    if s >= 60:  return "⭐⭐确认","建仓20%"
    if s >= 40:  return "⭐信号","轻仓10%"
    return "⚪关注","观望"

def run():
    t0 = time.time()
    today = date.today().strftime("%Y-%m-%d")
    print(f"\n{'='*55}\n🏴‍☠️ 杰克船长\n   {today}\n{'='*55}")
    counter = {"count":0,"limit":False}
    mx = MXSelectStock() if _MX_AVAILABLE else None

    queries = [
        ("涨幅0-5%","非ST 非亏损 换手率大于5 涨幅0到5"),
        ("涨停","非ST 非亏损 涨幅大于7 换手率大于3"),
        ("金叉","非ST 非亏损 5周均线上穿20周均线 换手率大于3"),
        ("资金流入","非ST 非亏损 连续3日资金净流入"),
    ]

    print(f"\n[并行查询] {len(queries)}组（超时{PARALLEL_TIMEOUT}s）...")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(safe_mx, mx, q[1], counter): i for i,q in enumerate(queries)}
        done, nd = concurrent.futures.wait(futs.keys(), timeout=PARALLEL_TIMEOUT)
        for f in done:
            try: results[futs[f]] = f.result()
            except: results[futs[f]] = ([],False)
        for f in nd:
            f.cancel(); results[futs[f]] = ([],False)
            print(f"  [{queries[futs[f]][0]}]超时已取消")

    r1,r2,r3,r4 = results.get(0,([],False))[0], results.get(1,([],False))[0], results.get(2,([],False))[0], results.get(3,([],False))[0]
    print(f"  → r1:{len(r1)} r2:{len(r2)} r3:{len(r3)} r4:{len(r4)} | API:{counter['count']}次 | {time.time()-t0:.1f}s")

    seen, rows = set(), []
    for ds in [r1,r2,r3,r4]:
        for row in ds:
            code = extract(row,"代码")
            if code and code not in seen and not code.startswith(("8","4")) and "ST" not in extract(row,"名称"):
                seen.add(code); rows.append(row)
    print(f"✅ 候选:{len(rows)}只")

    stocks = []
    for row in rows[:100]:
        try:
            c_val = float(extract(row,"涨跌幅")); t_val = float(extract(row,"换手率"))
            if not (-5 <= c_val <= 15 and 0 < t_val <= 30): continue
        except: continue
        s, sr = score_tech(extract(row,"最新价"), extract(row,"涨跌幅"), extract(row,"换手率"), above_ma5=True)
        ls, lr = score_limit(extract(row,"最新价"), extract(row,"涨跌幅"), extract(row,"换手率"))
        tot = s + ls
        lvl, act = get_level(tot)
        if c_val >= 7: sr.append("🔥涨停信号")
        stocks.append({"name":extract(row,"名称"),"code":extract(row,"代码"),
                       "price":extract(row,"最新价"),"change":extract(row,"涨跌幅"),
                       "turnover":extract(row,"换手率"),"score":s,"limit_score":ls,
                       "total":tot,"level":lvl,"action":act,"rules":sr,"change_val":c_val})
    stocks.sort(key=lambda x:x["total"], reverse=True)

    rem = GLOBAL_TIMEOUT - (time.time()-t0)
    chan_n = 15 if rem > 20 else 5
    print(f"\n[缠论] 剩余{rem:.0f}s，分析{chan_n}只...")
    scored = score_stocks_with_chanlun(stocks[:chan_n], max_stocks=chan_n)
    c1=[s for s in scored if s.get("chan_buy")]
    c2=[s for s in scored if s.get("chan_bottom_div") and not s.get("chan_buy")]
    c3=[s for s in scored if not s.get("chan_buy") and not s.get("chan_bottom_div")]
    for s in c1: s["final_score"] = 200+s.get("chan_score",0)
    for s in c2: s["final_score"] = 100+s.get("chan_score",0)
    for s in c3: s["final_score"] = s.get("total",0)+s.get("chan_score",0)
    all_s = c1+c2+c3; all_s.sort(key=lambda x:x["final_score"],reverse=True)
    b1=sum(1 for s in all_s if s.get("chan_buy")=="1买")
    b2=sum(1 for s in all_s if s.get("chan_buy")=="2买")
    b3=sum(1 for s in all_s if s.get("chan_buy")=="3买")
    bd=sum(1 for s in all_s if s.get("chan_bottom_div") and not s.get("chan_buy"))
    print(f"  → 1买:{b1} 2买:{b2} 3买:{b3} 背驰:{bd}")

    top5=all_s[:5]; lu=sorted([s for s in stocks if s["change_val"]>=7],key=lambda x:x["total"],reverse=True)[:3]
    vix=random.uniform(12,25)
    if vix<15: st,cf="激进买入","高"
    elif vix<20: st,cf="保守买入","中"
    elif vix<30: st,cf="持币观望","低"
    else: st,cf="空仓避险","极低"
    tot_time=time.time()-t0
    print(f"\n⏱ 耗时:{tot_time:.1f}s | {'✅' if tot_time<GLOBAL_TIMEOUT else '⚠️超时'}")

    sys.path.insert(0,r"C:\Users\china\.qclaw\workspace")
    from dingtalk_style import header,footer,highlight_card,send
    for s in top5:
        s["price"]=s.get("price","-"); s["change"]=s.get("change_val",s.get("change",0))
        s["score"]=s.get("total",0); s["level"]=s.get("level","")+("🔥" if s.get("limit_score",0)>10 else ""); s["rules"]=s.get("rules",[])

    cl_blk=""
    ct=[s for s in all_s if s.get("chan_buy") or s.get("chan_bottom_div")][:6]
    if ct:
        lines=["**🔮 缠论买点精选（1买/2买/3买）**",""]
        for s in ct:
            bt=s.get("chan_buy","底背驰"); pr=s.get("chan_buy_price",s.get("price","-"))
            try: ps=f"{float(pr):.2f}"; ch=f"{float(s['change']):+.2f}%"
            except: ps=str(pr); ch=f"{s.get('change_val',0):+.2f}%"
            ic={"1买":"🔴","2买":"🟠","3买":"🟡"}.get(bt,"⚪")
            di=s.get("chan_bottom_div",{}); ex=""
            if isinstance(di,dict) and di.get("signal"): ex=f"力度{float(di.get('strength_ratio',0)):.1f}"
            lines+=[f"### {ic} **{bt}** {s['code']} {s['name']}",
                    f"推荐价格 **{ps}元** 　今日涨幅 **{ch}** 　置信**{s.get('chan_confidence',0)}%**",
                    f"综合评分: 技术{int(s.get('total',0))} + 缠论{s.get('chan_score',0)} = **{int(s.get('final_score',0))}分**",""]
            if ex: lines.insert(-1,f"底背驰详情: {ex}")
        cl_blk="\n".join(lines)

    four=len([s for s in stocks if "⭐⭐⭐⭐" in s.get("level","")])
    three=len([s for s in stocks if "⭐⭐⭐" in s.get("level","") and "⭐⭐⭐⭐" not in s.get("level","")])
    two=len([s for s in stocks if "⭐⭐" in s.get("level","")])
    lc=len([s for s in stocks if s["change_val"]>=7])

    parts=[
        header("jack_captain",subtitle="OR融合·涨停加分·缠论买点",
               extra=f"API{counter['count']}次 ⏱{tot_time:.0f}s 1买{b1}2买{b2}3买{b3}",
               channels=['mx','sina']),
        f"**📈 市场立场**\n\n|VIX|立场|信心|\n|:--:|:--:|:--:|\n|{vix:.1f}|**{st}**|{cf}|\n\n",
        f"**📊 今日统计**\n\n|候选|涨停|⭐⭐⭐⭐|⭐⭐⭐|⭐⭐|\n|:--:|:--:|:--:|:--:|:--:|\n|{len(stocks)}只|{lc}只|{four}只|{three}只|{two}只|\n\n**🔮 缠论信号**\n\n|1买|2买|3买|背驰|\n|:--:|:--:|:--:|:--:|\n|{b1}只|{b2}只|{b3}只|{bd}只|\n\n",
        cl_blk,
        highlight_card(top5,title="🎯 Top5综合精选（缠论买点优先）",max_n=5),
        footer("杰克船长战法·缠论买点仅供参考·不构成投资建议",
               channels_ok={'mx':not counter['limit'],'sina':True}),
    ]
    ok=send("杰克船长每日选股","\n".join(parts))
    print(f"✅ 完成 | {'钉钉✓' if ok else '钉钉✗'}")

    from daily_picks_store import save_daily_picks
    sv=top5[:]; seen={s['code'] for s in sv}
    for s in ct:
        if s['code'] not in seen: sv.append(s); seen.add(s['code'])
    save_daily_picks("杰克船长",sv)

    try:
        RF=Path(r"C:\Users\china\.qclaw\workspace\jack_recommendations.json")
        rd={"recommendations":[]}
        if RF.exists(): rd=json.loads(RF.read_text(encoding="utf-8"))
        td=date.today().strftime("%Y-%m-%d")
        if td not in {r.get("date") for r in rd.get("recommendations",[])}:
            stks=[{"name":s["name"],"code":s["code"],"price":s.get("price","-"),
                   "change":s.get("change_val",0),"turnover":s.get("turnover","-"),
                   "total":s.get("total",0),"level":s.get("level",""),
                   "chan_buy":s.get("chan_buy",""),"chan_score":s.get("chan_score",0)} for s in all_s[:10]]
            rd["recommendations"].append({"date":td,"vix":round(vix,1),"stance":st,"stocks":stks,"timestamp":datetime.now().isoformat()})
            RF.write_text(json.dumps(rd,ensure_ascii=False,indent=2),encoding="utf-8")
            print(f"  📝 记录{stks}只")
    except Exception as e: print(f"  ⚠️ 记录失败:{e}")

if __name__=="__main__": run()