#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 K 线数据源：东方财富优先，腾讯行情兜底，新浪三级兜底。

背景：2026-09-11 的定时任务中，东财 push2his / push2.clist 对本机 IP 实施限流
（RemoteDisconnected，150 只仅 1 只成功），导致初筛池为空、看板无数据。
本模块保持东财为首选（与原实现等价），仅在东财连续失败时自动切到腾讯
web.ifzq.gtimg.cn，再失败切新浪，使每日筛选在东财限流期间仍能跑出完整结果。

仅涉及数据获取与字段归一化，不包含任何打分逻辑、权重或画像阈值。
"""
import json, time, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      "Referer": "https://quote.eastmoney.com/"}


def _get_json(url, tries=3, base_sleep=1.5):
    last = ""
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = repr(e)
            time.sleep(base_sleep * (i + 1))
    return {"__err__": last}


def _em_secid(code):
    return ("1." if code[0] == "6" else "0.") + code


def _tx_symbol(code):
    # 6 开头=沪(sh)，其余=深(sz)
    return ("sh" if code[0] == "6" else "sz") + code


def _from_em(code, n):
    """东财：返回 (name, rows)；rows = [date, open, close, low, high, volume]"""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get?" + urllib.parse.urlencode({
        "secid": _em_secid(code), "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "klt": "101", "fqt": "0", "beg": "0", "end": "20500101"})
    d = _get_json(url)
    if "__err__" in d:
        return None, [], d["__err__"]
    dd = d.get("data") or {}
    out = []
    for s in dd.get("klines") or []:
        p = s.split(",")
        if len(p) < 6:
            continue
        try:
            out.append([p[0], float(p[1]), float(p[2]), float(p[4]), float(p[3]), int(p[5])])
        except Exception:
            continue
    return dd.get("name") or "", (out[-n:] if n else out), ""


def _from_tx(code, n):
    """腾讯兜底：rows = [date, open, close, low, high, volume]（未复权，对齐东财 fqt=0）"""
    sym = _tx_symbol(code)
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           + urllib.parse.urlencode({"param": f"{sym},day,,,{max(n or 60, 60)},"}))
    d = _get_json(url, tries=2, base_sleep=1.0)
    if "__err__" in d:
        return "", [], d["__err__"]
    node = (d.get("data") or {}).get(sym) or {}
    k = node.get("day") or node.get("qfqday") or []
    out = []
    for p in k:
        if len(p) < 6:
            continue
        try:
            # 腾讯顺序: date, open, close, high, low, volume
            out.append([p[0], float(p[1]), float(p[2]), float(p[4]), float(p[3]), int(p[5])])
        except Exception:
            continue
    return "", (out[-n:] if n else out), ""


# 东财整体限流时的自适应开关：连续 EM_FAIL_MAX 次失败后，
# 本进程内直接走腾讯，避免每只股票白等 9 秒重试。
EM_FAIL_MAX = 3
_state = {"em_fail": 0, "em_down": False}


def _from_sina(code, n):
    """新浪兜底（第三级）。返回 rows = [date, open, close, low, high, volume]
    注意：新浪 volume 单位为「股」，东财/腾讯为「手」，此处统一 /100。"""
    c = code[0]
    if c == "6":
        sym = "sh" + code
    elif c in ("8", "9", "4"):   # 北交所
        sym = "bj" + code
    else:
        sym = "sz" + code
    url = ("https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?"
           + urllib.parse.urlencode({"symbol": sym, "scale": "240", "ma": "no",
                                     "datalen": str(max(n or 60, 60))}))
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            txt = r.read().decode("utf-8").strip()
        if not txt.startswith("["):
            return "", [], "bad payload"
        d = json.loads(txt)
    except Exception as e:
        return "", [], repr(e)
    out = []
    for it in d or []:
        try:
            out.append([it["day"][:10], float(it["open"]), float(it["close"]),
                        float(it["low"]), float(it["high"]),
                        int(float(it["volume"]) / 100)])
        except Exception:
            continue
    return "", (out[-n:] if n else out), ""


def get_rows(code, n=None, verbose=False):
    """统一入口，返回 (name, rows, src)。东财失败自动切腾讯。"""
    name, rows, err = ("", [], "em down (auto-skip)")
    if not _state["em_down"]:
        name, rows, err = _from_em(code, n)
    if rows:
        _state["em_fail"] = 0
        _state["em_down"] = False
        return name, rows, "em"
    # 东财未取到数据：累计失败次数，超限后本进程内停用东财
    _state["em_fail"] += 1
    if _state["em_fail"] >= EM_FAIL_MAX and not _state["em_down"]:
        _state["em_down"] = True
        print(f"    [kline_src] 东财连续 {_state['em_fail']} 次失败，判定为限流，"
              f"本轮后续股票直接走腾讯兜底", flush=True)
    if verbose:
        print(f"    [fallback] {code} 东财失败({err[:60]})，切腾讯")
    name2, rows2, err2 = _from_tx(code, n)
    if rows2:
        return name2, rows2, "tx"
    # 腾讯也被限流时，再退到新浪
    name3, rows3, err3 = _from_sina(code, n)
    if rows3:
        return name3, rows3, "sina"
    if verbose:
        print(f"    [fail] {code} 腾讯({err2[:40]}) / 新浪({err3[:40]}) 均失败")
    return "", [], "none"


def get_bars(code, verbose=False):
    """analyze.get_kline 兼容格式：升序 dict 列表 {date,open,last,high,low,volume}"""
    _, rows, _ = get_rows(code, None, verbose)
    return [{"date": r[0], "open": r[1], "last": r[2],
             "low": r[3], "high": r[4], "volume": r[5]} for r in rows]


if __name__ == "__main__":
    import sys
    for c in (sys.argv[1:] or ["000759", "600712"]):
        nm, rows, src = get_rows(c, 30, verbose=True)
        print(f"{c} src={src} name={nm} rows={len(rows)} last={rows[-1] if rows else None}")
