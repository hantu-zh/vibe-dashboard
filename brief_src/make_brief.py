#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日语音简报生成器：brief.json -> 逐句语音 + 时间戳 -> 单页网页

产物：
  brief_site/index.html      可直接打开的网页（看文字 + 听语音 + 逐句高亮）
  brief_site/audio/seg_*.mp3 逐句音频
  brief_site/audio/narration.mp3 拼接整轨
  brief_site/timeline.json   句子级时间戳（网页读取对齐高亮）

约束：口播总时长 <= 300 秒（5 分钟）；超时告警并停止出页。
用法: python make_brief.py
"""
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import edge_tts
import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
HERE = Path(__file__).resolve().parent
SITE = HERE / "brief_site"
AUD = SITE / "audio"
MAX_SEC = 300.0

B = json.loads((HERE / "brief.json").read_text(encoding="utf-8"))
VOICE, RATE = B.get("voice", "zh-CN-XiaoxiaoNeural"), B.get("rate", "+0%")


def flatten_lines():
    """展平成 [(section_id, line_text), ...]"""
    out = []
    for s in B["sections"]:
        for t in s["lines"]:
            out.append((s["id"], t))
    return out


async def synth_one(i, text, path):
    c = edge_tts.Communicate(text, VOICE, rate=RATE)
    with open(path, "wb") as f:
        async for ch in c.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])


def dur_of(p):
    r = subprocess.run([FF, "-i", str(p)], capture_output=True, text=True, errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise RuntimeError("no duration: " + str(p))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


async def main():
    AUD.mkdir(parents=True, exist_ok=True)
    lines = flatten_lines()
    total_chars = sum(len(t) for _, t in lines)
    print(f"口播 {len(lines)} 句 / {total_chars} 字，开始合成…")

    for i, (_, text) in enumerate(lines):
        await synth_one(i, text, AUD / f"seg_{i:03d}.mp3")
        print(f"  [{i:02d}] {dur_of(AUD / f'seg_{i:03d}.mp3'):5.2f}s  {text[:22]}", flush=True)

    lst = AUD / "concat.txt"
    lst.write_text(
        "\n".join("file '" + str(AUD / f"seg_{i:03d}.mp3").replace("\\", "/") + "'" for i in range(len(lines))),
        encoding="utf-8",
    )
    narration = AUD / "narration.mp3"
    subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(narration)],
                   capture_output=True)

    # 时间戳：每句 start 为前面所有句时长累加
    acc = 0.0
    tl = []
    for i, (sid, text) in enumerate(lines):
        d = round(dur_of(AUD / f"seg_{i:03d}.mp3"), 3)
        tl.append({"i": i, "sec": sid, "text": text, "start": round(acc, 3), "dur": d})
        acc += d
    total = round(acc, 2)

    print(f"\n总时长 {total}s / {MAX_SEC}s 上限")
    if total > MAX_SEC:
        print("ERROR: 超过 5 分钟上限，停止出页。请精简 brief.json 文案。", file=sys.stderr)
        sys.exit(2)

    (SITE / "timeline.json").write_text(
        json.dumps({"total": total, "date": B["date"], "data_date": B["data_date"],
                    "title": B["title"], "lines": tl}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("OK ->", SITE / "timeline.json")
    print("OK ->", narration)


if __name__ == "__main__":
    asyncio.run(main())
