# -*- coding: utf-8 -*-
"""拉取指数 K 线（腾讯接口），落地为 JSON 缓存。"""
import json, os, subprocess, io, time

BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)

TARGETS = [
    ("sh000001", "上证指数"),
    ("sz399006", "创业板指"),
    ("sh000300", "沪深300"),
]


def fetch(sym, period, count):
    url = "%s?param=%s,%s,,,%d,qfq&_var=kx" % (BASE, sym, period, count)
    for attempt in range(4):
        r = subprocess.run(
            ["curl", "-sS", "--http1.1", "--max-time", "90", url,
             "-H", "User-Agent: Mozilla/5.0", "-H", "Referer: https://gu.qq.com/"],
            capture_output=True)
        txt = r.stdout.decode("utf-8", "replace")
        i = txt.find("=")
        if i == -1:
            time.sleep(2)
            continue
        try:
            obj = json.loads(txt[i + 1:].strip().rstrip(";"))
        except Exception:
            time.sleep(2)
            continue
        d = obj.get("data", {}).get(sym, {})
        key = period if period in d else ("qfq" + period if ("qfq" + period) in d else None)
        if key is None:
            # 周线有时放在 data[sym]['week'] 或需要 period 映射
            for k in d:
                if period in k:
                    key = k
                    break
        if key is None:
            print("  无数据键", sym, period, list(d.keys()))
            return []
        rows = d[key]
        return rows
    return []


def norm(rows):
    out = []
    for r in rows:
        out.append({
            "date": r[0],
            "open": float(r[1]), "close": float(r[2]),
            "high": float(r[3]), "low": float(r[4]),
            "volume": float(r[5]) if len(r) > 5 else 0.0,
        })
    return out


def main():
    for sym, name in TARGETS:
        for period, cnt in [("day", 2000), ("week", 800)]:
            fn = os.path.join(OUT, "%s_%s.json" % (sym, period))
            rows = fetch(sym, period, cnt)
            bars = norm(rows)
            if bars:
                # 仅在拉取成功时覆盖，失败则保留旧缓存（CI 单点容错）
                with io.open(fn, "w", encoding="utf-8", newline="") as f:
                    f.write(json.dumps(bars, ensure_ascii=False))
                print("%-8s %-5s %4d 根  %s ~ %s  最新收盘 %.2f" %
                      (name, period, len(bars), bars[0]["date"], bars[-1]["date"], bars[-1]["close"]))
            else:
                if os.path.exists(fn):
                    print("%-8s %-5s 拉取失败，沿用旧缓存 (%d 字节)" % (name, period, os.path.getsize(fn)))
                else:
                    print("%-8s %-5s 拉取失败且无缓存，跳过" % (name, period))


if __name__ == "__main__":
    main()
