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
"""
import sys, os, json, datetime

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
        if d.get("date") != TODAY:
            print(f"[verify] 结果文件日期非今天({d.get('date')} != {TODAY}): {sig}")
            return 1
        # 0 只也算成功（真的没选出来）；只要 date 对、文件有效即放行
        print(f"[verify] 结果文件有效(date={d.get('date')}): {sig}")
        return 0

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
