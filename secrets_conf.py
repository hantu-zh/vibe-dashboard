# secrets_conf.py - 敏感凭证集中管理
# 原脚本把钉钉 access_token 硬编码在代码里（且仓库是公开的，已泄露）。
# 迁移后统一从环境变量读取，永不写死在代码 / 提交到仓库。
import os

# 钉钉机器人 token（GitHub Actions 里由 secrets.DINGTALK_TOKEN 注入）
DINGTALK_TOKEN = os.environ.get("DINGTALK_TOKEN", "")
# 完整 webhook 地址
DINGTALK_WEBHOOK = (
    f"https://oapi.dingtalk.com/robot/send?access_token={DINGTALK_TOKEN}"
    if DINGTALK_TOKEN
    else ""
)
# 兼容部分脚本用裸 DINGTALK 变量拼 URL 的写法
DINGTALK = DINGTALK_TOKEN

# GitHub 写回仓库用的 token（Actions 自带 secrets.GITHUB_TOKEN，无需 PAT）
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
