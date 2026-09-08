# vibe-dashboard 去 Qclaw 化 · 部署指南

把原来跑在腾讯 Qclaw 云端 Windows 上的定时数据流水线，整体迁移到 **GitHub Actions** 免费定时运行。
迁移后：脚本自己抓行情 → 生成 JSON → 推送回本仓库 → 自动触发 GitHub Pages 更新。Qclaw 不再参与。

---

## 一、这次改造做了什么（已自动完成）

| 改造项 | 说明 |
|------|------|
| **去 Windows 路径** | 脚本里 `C:\Users\china\.qclaw\workspace\...` 全部改为 `paths.w(...)`，由 `paths.py` 按环境变量 `VIBE_WS` 解析（Actions 下即仓库根） |
| **去钉钉硬编码** | 钉钉 `access_token` 不再写死在代码里，改由 `secrets_conf.py` 从环境变量读取（避免公开仓库泄露） |
| **GitHub 写回** | `sync_func.py` 通过仓库自带 `GITHUB_TOKEN` 用 REST API 写回，无需 PAT |
| **定时调度** | `cron.yml` 按原 Qclaw 时点（上海时间）触发，含"仅交易日执行"判断（`trading_day.py`） |
| **容错** | 每个任务"文件存在才跑，缺失则跳过并提示"，不会因个别脚本缺失中断整体 |

> 文件清单：新增 `paths.py` `secrets_conf.py` `trading_day.py` `requirements.txt` `.github/workflows/sync.yml`，并对 12 个含硬编码路径的脚本做了改写。所有业务脚本语法已校验通过。

---

## 二、你需要做的 4 步（约 10 分钟）

### 步骤 1：把改造后的代码放进你的仓库
两种做法任选：

- **做法 A（我代劳）**：如果你给我一个对 `hantu-zh/vibe-dashboard` 有写权限的 GitHub PAT（或在此环境登录 gh），我直接帮你提交并启用。
- **做法 B（自己来）**：
  1. 本地克隆：`git clone https://github.com/hantu-zh/vibe-dashboard.git`
  2. 把我给你的 `vibe-dashboard-actions/` 目录里**所有文件**复制覆盖到克隆仓库的根目录（新文件直接加进来，原有的 `index.html`/`*.json` 保留不动）
  3. `git add -A && git commit -m "migrate: replace Qclaw with GitHub Actions" && git push`

### 步骤 2：补回 Qclaw workspace 里"没提交"的文件（关键！）

> **✅ 2026-09-08 更新：workflow 会调用的脚本已全部补齐，自动同步链路已打通。**
> 以下 5 个生成脚本 + 依赖模块已提交入库，并在 GitHub Actions 上实测跑通
> （美股 / 追涨 / 益盟强买 / ETF / 周末训练均已产出真实数据）：
> `gaoxin_us_picks_v2.py`、`hot_chase_picks.py`、`yimeng_strongbuy.py`、
> `etf_sync.py`、`jack_weekend_trainer.py`、`daily_picks_store.py`
> （`etf_data.py` 是其依赖的数据源模块，已按海外可达源重写：腾讯 `qt.gtimg.cn` 主 / 新浪备用）。
> `research_sources.py` 本来就在仓库内。

**仍缺（但不影响现有自动同步）：**

下列模块只被 `captain_fishing.py` / `jack_captain.py` 等**当前未被 workflow 调用**的脚本引用，
缺失不会让定时任务报错，仅在你日后把它们接进 workflow 时才需要补：

- `chanlun_quick.py`、`chanlun_engine.py`
- `turnover_utils.py`、`em_api_helper.py`
- `dingtalk_style.py` / `dingtalk.py`
- `data_source.py` / `data_source_fallback.py` / `data_source_backup.yaml`
- `mx_select_stock/`（在 `skills\mx-skills\mx-select-stock`）
- `sync_vibe_to_github.py`（`news_update.py` 内部可能调用；缺失时由 `sync_func.py` 兜底推送，不阻塞）

> 技巧：把 Qclaw workspace 里**整个项目目录**对比一下，凡是仓库里没有的 `.py`/子目录都补进来最省事。

---

### ⚠️ 维护须知：仓库根目录是唯一数据权威位置

GitHub Pages 从 `main` **根目录**提供文件，`vibe-dashboard/` 子目录只是历史残留副本。
所有脚本的读写路径必须指向仓库根目录，写进子目录等于写了个线上永远读不到的孤儿文件。

（2026-09-08 已踩过这个坑：`sync_func.py` 里 12 处路径写着 `vibe-dashboard\index.html`
等根本不存在的文件，导致主页 embed 注入与推送**长期静默失败**，主页一直停在旧快照。）

### 步骤 3：配置两个 Secrets（GitHub 网页操作）
仓库 **Settings → Secrets and variables → Actions → New repository secret**：
- `XUEQIU_COOKIES`：雪球 cookie 文件的**全部文本内容**（新闻模块需要；从 Qclaw 的 `xueqiu_cookies.txt` 复制）
- `DINGTALK_TOKEN`：钉钉机器人 `access_token=` 后面那一串（保留通知用）

> `GITHUB_TOKEN` **不用配**，GitHub Actions 自动提供。

### 步骤 4：启用工作流
仓库 **Actions** 标签页 → 找到 `vibe-dashboard sync` → 若显示 "disabled" 点 Enable。
之后它会按 cron 自动跑；也可点 **Run workflow** 手动触发一次验证。

---

## 三、验证是否跑通
1. Actions 页面看最新一次运行的日志，确认没有 `ModuleNotFoundError` / 路径错误。
2. 等一个交易时段（如 15:30 后），打开 https://hantu-zh.github.io/vibe-dashboard/ 看数据是否更新。
3. 若某任务显示 `>> skip xxx.py（尚未提交到仓库）`，说明步骤 2 漏了文件。

---

## 四、已知限制与风险（务必看）

1. **海外 runner 访问国内数据源可能不稳**（最重要）：GitHub Actions 的机器在美国/欧洲，而你的数据源是东方财富 / 腾讯 / 雪球，这些对**国内或香港 IP** 最友好。海外 IP 可能慢、超时或被限频，导致抓取失败。
   - 若 Actions 实测频繁失败，**更稳的方案是改用一台国内轻量服务器 + Linux crontab**（调度逻辑完全一样，只是把 `sync.yml` 换成 `crontab` 条目）。需要的话我给你出服务器版。
2. **定时精度**：Actions 的 scheduled 触发可能有几分钟延迟（公开仓库更明显），对"精确开盘时点选股"不算完美，但能接受。
3. **不活跃自动停用**：GitHub 对超过 60 天无新 commit 的仓库，会**自动禁用 scheduled workflow**。保持仓库有点动静（或定期手动 Run workflow）即可。
4. **`build.py` 已损坏**：源仓库里的 `build.py` 本身含损坏字节、任何编码都解不开，本次**原样保留未动**。它不是定时任务必需（`sync_func.py` 已负责把数据注入页面）。如需"从 JSON 重新构建 HTML"功能，请单独提供正确版本。
5. **安全**：仓库是公开的，所有 token 已改为读 Secrets，切勿再把 `access_token` 写回代码。

---

## 五、排错速查
| 现象 | 处理 |
|------|------|
| `ModuleNotFoundError: xxx` | 步骤 2 漏了本地模块，补进仓库根 |
| `>> skip xxx.py（尚未提交）` | 步骤 2 漏了生成脚本 |
| 数据一直不更新 | 看 Actions 日志；多半是数据源限流/海外 IP 问题（见限制 1） |
| `401/403 push failed` | `permissions: contents: write` 已设；确认不是用了无写权限的 token |
| 钉钉没收到通知 | 检查 `DINGTALK_TOKEN` Secret 是否正确 |
