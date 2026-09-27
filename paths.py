# -*- coding: utf-8 -*-
"""
paths.py — 跨平台路径适配层

在 Windows 本地运行时，自动使用：
  WS      = C:/Users/china/.qclaw/workspace
  VIBE_DIR = C:/Users/china/.qclaw/workspace/vibe-dashboard

在 GitHub Actions 上运行时，自动使用：
  VIBE_ROOT = /home/runner/work/vibe-dashboard/vibe-dashboard  (仓库根)
  WS        = VIBE_ROOT
  VIBE_DIR  = VIBE_ROOT  (因为仓库本身就是 vibe-dashboard)

可通过环境变量覆盖：
  VIBE_ROOT=/custom/path
"""
import os

# 默认指向仓库根（GitHub Actions 场景）
# 或本地 workspace 根（Windows 场景）
# 候选本地工作区根（按存在性自动选择，兼容旧机与新机 D:\Qclaw）
_CANDIDATE_WS_WIN = [
    r"D:\Qclaw\workspace",
    r"C:\Users\china\.qclaw\workspace",
]
_DEFAULT_WS_WIN = next((p for p in _CANDIDATE_WS_WIN if os.path.isdir(p)), _CANDIDATE_WS_WIN[-1])


def _detect_vibe_root():
    """智能识别工作目录"""
    env_root = os.environ.get('VIBE_ROOT')
    if env_root:
        return env_root
    # GitHub Actions 标准路径
    ga_root = os.environ.get('GITHUB_WORKSPACE')
    if ga_root:
        return ga_root
    # 本地兜底
    if os.path.exists(_DEFAULT_WS_WIN):
        return _DEFAULT_WS_WIN
    # Linux/Mac 无 VIBE_ROOT 时，使用当前脚本所在目录
    return os.path.dirname(os.path.abspath(__file__))


VIBE_ROOT = _detect_vibe_root()


def _vibe_dir():
    """vibe-dashboard 目录"""
    # 如果 VIBE_ROOT 下面有 vibe-dashboard 子目录（如本地布局），用它
    sub = os.path.join(VIBE_ROOT, 'vibe-dashboard')
    if os.path.isdir(sub) and os.path.isfile(os.path.join(sub, 'index.html')):
        return sub
    # 否则 VIBE_ROOT 本身就是 vibe-dashboard（GitHub 布局）
    return VIBE_ROOT


WS = VIBE_ROOT
VIBE_DIR = _vibe_dir()

# ─── 常用路径常量 ─────────────────────────────────────────
DAILY_PICKS       = os.path.join(VIBE_DIR, 'daily_picks.json')
NEWS_DATA         = os.path.join(VIBE_DIR, 'news_data.json')
US_PICKS          = os.path.join(VIBE_DIR, 'us_picks.json')
INDEX_HTML        = os.path.join(VIBE_DIR, 'index.html')
AI_ANALYSIS_HTML  = os.path.join(VIBE_DIR, 'ai_analysis.html')
AI_ANALYSIS_DATA  = os.path.join(VIBE_DIR, 'ai_analysis_data.json')
AI_ANALYSIS_REPORT = os.path.join(VIBE_DIR, 'ai_analysis_report.json')
DINGTALK_ENV      = os.path.join(VIBE_DIR, '.env.dingtalk')
GITHUB_TOKEN_FILE = os.path.join(VIBE_DIR, '.github_token')

# ─── 工具函数 ─────────────────────────────────────────────
def normalize_path(p: str) -> str:
    """把 Windows 反斜杠转换为正斜杠，便于跨平台"""
    return p.replace('\\', '/')



# ─── 兼容层：恢复 54d4c20d（2026-09-25）误删的旧 API ───
# 20+ 脚本（sync_func / pick_tracker / speedrank / strong_update ...）依赖
# paths.w() 与 paths.VIBE_WS，删除后 AttributeError 导致 9/25 12:04 起数据断更。
VIBE_WS = VIBE_ROOT            # workspace 根语义：Actions=仓库根；本地=workspace 根
VIBE_DASH = VIBE_DIR           # dashboard 目录语义

def w(rel):
    """把“相对 workspace 的路径”转为当前系统的绝对路径（旧 API，2026-09-08 引入）。

    示例：
      w('vibe-dashboard/index.html') -> <VIBE_DASH>/index.html
      w('daily_picks.json')          -> <VIBE_WS>/daily_picks.json
    """
    parts = rel.replace("\\", "/").split("/")
    if parts and parts[0] == "vibe-dashboard":
        base, sub = VIBE_DASH, parts[1:]
    else:
        base, sub = VIBE_WS, parts
    return os.path.join(base, *sub) if sub else base


if __name__ == '__main__':
    print(f'[paths] VIBE_ROOT = {VIBE_ROOT}')
    print(f'[paths] VIBE_DIR  = {VIBE_DIR}')
    print(f'[paths] WS        = {WS}')
    print(f'[paths] daily_picks.json exists: {os.path.exists(DAILY_PICKS)}')
    print(f'[paths] index.html exists: {os.path.exists(INDEX_HTML)}')
