# secrets_conf.py - 敏感凭证集中管理
# 原脚本把钉钉 access_token 硬编码在代码里（且仓库是公开的，已泄露）。
# 迁移后统一从环境变量读取，永不写死在代码 / 提交到仓库。
import os

# 钉钉 webhook：仓库实际配置的 secret 名是 DINGTALK_WEBHOOK（完整 URL）。
# 修复(2026-10-02): 此前只读 DINGTALK_TOKEN，而仓库从未配置该 secret → 所有钉钉推送静默跳过。
_RAW_WEBHOOK = (os.environ.get("DINGTALK_WEBHOOK") or "").strip()
_RAW_TOKEN = (os.environ.get("DINGTALK_TOKEN") or "").strip()
if _RAW_WEBHOOK:
    DINGTALK_WEBHOOK = _RAW_WEBHOOK if _RAW_WEBHOOK.startswith("http") else (
        "https://oapi.dingtalk.com/robot/send?access_token=" + _RAW_WEBHOOK)
elif _RAW_TOKEN:
    DINGTALK_WEBHOOK = f"https://oapi.dingtalk.com/robot/send?access_token={_RAW_TOKEN}"
else:
    DINGTALK_WEBHOOK = ""
# 裸 token（部分脚本用 f"...access_token={DINGTALK}" 拼 URL）：从完整 URL 反解
if _RAW_TOKEN:
    DINGTALK_TOKEN = _RAW_TOKEN
elif DINGTALK_WEBHOOK:
    DINGTALK_TOKEN = DINGTALK_WEBHOOK.split("access_token=", 1)[-1].split("&")[0]
else:
    DINGTALK_TOKEN = ""
DINGTALK = DINGTALK_TOKEN

# GitHub 写回仓库用的 token（Actions 自带 secrets.GITHUB_TOKEN，无需 PAT）
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
