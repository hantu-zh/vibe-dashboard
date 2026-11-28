# -*- coding: utf-8 -*-
"""_verify_data.py — 自动巡检的「数据闸门」

配合 sync.yml 的 run_task / force_run 使用：某个选股脚本跑完（退出码 0）后，
用本脚本确认它**真的把数据写出来了**，再决定是否标记任务完成。

为什么需要它：脚本可能因中途异常被吞掉而“退出 0 却没写数据”，
此时若直接 mark 完成，任务就永远不再补跑，页面卡在“超时未选出股票”。
加一道闸门后：
  - 数据确实存在（daily_picks.json 的 key，或结果文件）→ 标记完成，不再补跑
    （即便是 0 只，也算“真的没选出来”，符合“除非真的没选出来”）
  - 数据缺失 → 不标记 → 交给后续每 15 分钟的 run 自动补跑

用法：
  python _verify_data.py <信号>
    <信号> 以 .json 结尾  → 视为结果文件，检查其存在且 date 为今天，且非异常空壳
    其他字符串            → 视为 daily_picks.json 里“今天”的 key，检查该 key 存在

退出码：0 = 数据已产出（可标记）；非 0 = 未产出（应继续补跑）

2026-11-28 增补「统一时效标记 _meta」：
  - 写入侧 signal_meta.py 在信号落盘时追加 _meta.{generated_at, asof, source}
  - 本脚本结果文件模式优先读 _meta.generated_at 判闸门（只放宽、不收紧）：
      generated_at 落在今天/昨天 → 放行；asof 仅打印诊断，绝不参与拦截
    （避免 asof 天然滞后的周频/融资类信号被误判过期，触发每 15 分钟补跑饿死其他任务）
  - 没有 _meta 的文件继续走原有 date / 日期键逻辑，行为逐字不变
"""
import sys, os, json, datetime, re

SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))
TODAY = datetime.datetime.now(SHANGHAI).strftime("%Y-%m-%d")


def main():
    if len(sys.argv) < 2:
        # 没给信号：等价于“不校验”，调用方应自行承担风险；这里返回 0（放行标记）
        return 0
    sig = sys.argv[1]

    if sig.endswith(".json"):
        # 结果文件模式
        if not os.path.exists(sig):
            print(f"[verify] 结果文件缺失: {sig}")
            return 1
        try:
            d = json.load(open(sig, encoding="utf-8"))
        except Exception as e:
            print(f"[verify] 结果文件解析失败: {sig} ({e})")
            return 1

        # ── _meta 优先（只放宽不收紧）────────────────────────────────
        # 判定只看 generated_at 是否落在 [昨天, 今天]：脚本真的跑过就放行。
        # asof 是数据真实截止日，天然可能滞后（周频/融资 T+1），只打印诊断不拦截。
        meta = d.get("_meta") if isinstance(d, dict) else None
        if isinstance(meta, dict) and str(meta.get("generated_at", "")).strip():
            ga = str(meta["generated_at"]).strip()
            try:
                gday = datetime.datetime.strptime(ga[:10], "%Y-%m-%d").date()
            except ValueError:
                gday = None
            asof = meta.get("asof")
            asof_hint = f" asof={asof}" if asof else ""
            if gday is not None:
                today_d = datetime.datetime.now(SHANGHAI).date()
                lag = (today_d - gday).days
                if 0 <= lag <= 1:
                    stale = ""
                    if asof:
                        try:
                            aday = datetime.datetime.strptime(str(asof)[:10], "%Y-%m-%d").date()
                            a_lag = (today_d - aday).days
                            if a_lag > 1:
                                stale = f" （数据滞后{a_lag}天，仅提示不拦截）"
                        except ValueError:
                            pass
                    print(f"[verify] _meta.generated_at={ga}{asof_hint} → 有效{stale}: {sig}")
                    return 0
                print(f"[verify] _meta.generated_at={ga}{asof_hint} 距今{lag}天，已过期: {sig}")
                return 1
            # generated_at 格式异常 → 落到下方旧逻辑兜底

        if "date" in d:
            # 普通结果文件：要求顶层 date == 今天
            if d.get("date") != TODAY:
                print(f"[verify] 结果文件日期非今天({d.get('date')} != {TODAY}): {sig}")
                return 1
            # 0 只也算成功（真的没选出来）；只要 date 对、文件有效即放行
            print(f"[verify] 结果文件有效(date={d.get('date')}): {sig}")
            return 0
        # 累加型文件（如 cffex_net_position.json：{日期: 数据...}，无顶层 date）：
        # 中金所数据天然 T+1，校验“最新键为今天或昨天”即视为正常，
        # 既不会因 T+1 导致每天误判失败而无限补跑，也能在连续多日静默停滞时被捕获。
        dated_keys = [k for k in d.keys() if isinstance(k, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", k)]
        if not dated_keys:
            print(f"[verify] 累加文件无日期键: {sig}")
            return 1
        latest = max(dated_keys)
        cutoff = (datetime.datetime.now(SHANGHAI) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if latest >= cutoff:
            print(f"[verify] 累加文件最新键={latest}（24h 内），有效: {sig}")
            return 0
        print(f"[verify] 累加文件最新键={latest}，已超 24h 未更新: {sig}")
        return 1

    # daily_picks.json key 模式
    if not os.path.exists("daily_picks.json"):
        print("[verify] daily_picks.json 不存在")
        return 1
    try:
        dp = json.load(open("daily_picks.json", encoding="utf-8"))
    except Exception as e:
        print(f"[verify] daily_picks.json 解析失败: {e}")
        return 1
    node = dp.get(TODAY, {}).get(sig)
    if not node:
        print(f"[verify] 今日数据缺失 key={sig}")
        return 1
    print(f"[verify] 今日数据存在 key={sig}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
