# paths.py - 路径解析层
# 作用：去掉原 Qclaw 云端 Windows 硬编码路径（C:\Users\china\.qclaw\workspace...），
#       改为基于环境变量 VIBE_WS 的相对路径，使其在 Linux / GitHub Actions 上可运行。
import os

# 工作区根目录：
#   - GitHub Actions 下默认即仓库根目录（本文件所在目录）
#   - 本地调试时可 export VIBE_WS=/your/path 覆盖
VIBE_WS = os.environ.get("VIBE_WS", os.path.dirname(os.path.abspath(__file__)))
# 仪表盘子目录（原 C:\Users\china\.qclaw\workspace\vibe-dashboard）
VIBE_DASH = os.path.join(VIBE_WS, "vibe-dashboard")


def w(rel):
    """把“相对 workspace 的 Windows 路径”转为当前系统的绝对路径。

    入参示例（即原脚本里 r'...' 中的相对部分）：
      r'vibe-dashboard\\index.html'        -> <WS>/vibe-dashboard/index.html
      r'xueqiu_cookies.txt'                -> <WS>/xueqiu_cookies.txt
      r'skills\\mx-skills\\mx-select-stock'-> <WS>/skills/mx-skills/mx-select-stock
    """
    parts = rel.replace("\\", "/").split("/")
    if parts and parts[0] == "vibe-dashboard":
        base, sub = VIBE_DASH, parts[1:]
    else:
        base, sub = VIBE_WS, parts
    return os.path.join(base, *sub) if sub else base
