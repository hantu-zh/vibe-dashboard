# MEMORY.md - 闀挎湡璁板繂

## 鐢ㄦ埛鍋忓ソ
- 浣跨敤鍦烘櫙锛欰鑲″競鍦哄垎鏋愩€佸畾鏃舵姤鍛婃帹閫?
- 鎺ㄩ€佹笭閬擄細閽夐拤鏈哄櫒浜猴紙2026-04-09璧锋墍鏈変换鍔″彧鍙戦拤閽夛紝涓嶅彂鎺у彴锛?

## 閽夐拤閰嶇疆
- Webhook锛歚https://oapi.dingtalk.com/robot/send?access_token=055ab261c9ba6f087e26f2abbdb3566508c73da140be3bc75511a3933bd430ba`
- 鎺ㄩ€佽剼鏈細`C:\Users\china\.qclaw\workspace\dingtalk.py`
- 鎺ㄩ€佹柟寮忥細Python `urllib.request`锛孶TF-8缂栫爜锛宮arkdown鏍煎紡

## 鎶€鏈瑪璁?
- PowerShell 涓枃缂栫爜闂锛氭敼鐢?Python 鑴氭湰鍙戦€侀拤閽夋秷鎭?
- 涓滄柟璐㈠瘜 push2.eastmoney.com 宸插け鏁堬紝鏀圭敤 push2he.eastmoney.com
- 瀛愪换鍔″唴瀹瑰畨鍏ㄦ嫤鎴棶棰橈細鏀逛负涓昏繘绋嬬洿鎺ユ姄鍙栨暟鎹?鎺ㄩ€?
- 閫夎偂鑴氭湰缂栫爜闂锛歐indows涓嬪己鍒禪TF-8锛屾坊鍔犻敊璇鐞?
- 闆悆WAF缁曡繃锛歅laywright headless 鈫?璁块棶棣栭〉 鈫?娴忚鍣ㄥ唴fetch 鈫?7x24蹇
- 濡欐兂API瀛楁鍚嶇簿纭尮閰嶏紙2026-04-09锛夛細浠ｇ爜/鍚嶇О/鏈€鏂颁环(鍏?/娑ㄨ穼骞?%)/鎹㈡墜鐜?%)/娴侀€氬競鍊?鍏?



## 慢热板块系统 (2026-07-05 重建)

### 核心规范
- **数据源**: 东方财富 push2he（主）→ Sina 行业板块（备）
- **板块数量**: 49 个标准行业板块
- **板块名格式**: 玻璃行业、电力行业、房地产（4字，含"行业"后缀）
- **唯一数据源**: `vibe-dashboard/vibe_trend_history.json`
- **入组条件**: 连续 3 天排名递进
- **保留条件**: 5 天内保持 3 天递进

### 数据质量检查（已实现）
1. 板块数量检查: 必须为 49 个
2. 格式检查: `is_common_format()` 确保是标准行业名
3. 数据重复检测: 如果与前一天完全一样，跳过并告警
4. 板块名一致性检查: 与前一天对比，如果不一致则跳过

### 定时任务
- **时间**: 交易日 15:30
- **脚本**: `update_slowrise.py`
- **检查清单**: `SLOWRISE_CHECKLIST.md`
- **规范文档**: `SLOWRISE_SPEC.md`

### 历史问题（2026-07-05）
- 数据重复: 07-01/02/03 数据完全一样
- 数据源不一致: 前 3 天 30 个板块，后 3 天 49 个板块
- 板块名不一致: 06-29 使用细分板块名，后续使用行业板块名

### 解决方案
- 清理损坏数据，从 07-07 开始重建
- 添加数据质量验证逻辑
- 统一数据源和板块名格式

### Dashboard 客户端实时计算：从 GitHub Pages 读取 vibe_trend_history.json
- 数据流：update_slowrise.py → 直接写 vibe_trend_history.json（绕过 daily_picks.json）
- cron 任务：交易日 15:30 执行 merge + sync
- 历史回填：daily_picks.json 中有 06-27/29 数据（回填到 vibe_trend_history.json）
- min_streak 从 3 降至 2（端午假期打断，06-24~30 只有 5 个交易日但非连续）


## 缂犺寮曟搸 (2026-04-09 鏂板)
- `chanlun_engine.py` - 瀹屾暣缂犺寮曟搸锛圸igZag鎶樼偣+绗?涓灑+MACD鑳岄┌+涔扮偣璇嗗埆锛?
- `chanlun_quick.py` - 杞婚噺绾х紶璁鸿瘎鍒嗭紙0.1绉?鑲★紝瀵筎op30鍊欓€夋壒閲忓鐞嗭級
- 闆嗘垚鍒帮細`jack_captain.py`銆乣popeye.py`
- 鏁版嵁婧愶細閫氳揪淇℃湰鍦版棩绾匡紙浼樺厛锛?+ akshare澶囩敤
- 涔扮偣浣撶郴锛?
- **1涔帮紙+45鍒嗭級**锛氬簳鑳岄┌纭锛屼笅璺屽垱鏂颁綆浣哅ACD鍔涘害鍑忓急锛屽姏搴︽瘮<0.5涓哄己鑳岄┌
- **2涔帮紙+35鍒嗭級**锛?涔板悗鍥炶皟涓嶇牬鎴栬繘鍏ヤ腑鏋紝瀹夊叏鎬ф渶楂?
- **3涔帮紙+30鍒嗭級**锛氱寮€涓灑鍚庡洖璋冧綆鐐逛笉杩涘叆涓灑
- **搴曡儗椹帮紙+20~25鍒嗭級**锛氬姏搴︽瘮<0.7鐤戜技锛?0.5寮鸿儗椹?
- 鎺掑簭閫昏緫锛氱紶璁轰拱鐐硅偂绁ㄦ帓鍦ㄦ渶鍓嶏紝缁煎悎璇勫垎=鎶€鏈垎+缂犺鍒?

## 鏉板厠鑸归暱璁粌绯荤粺 (2026-04-09 鏂板)
- `jack_weekend_trainer.py` - 鍛ㄦ湯璁粌鑴氭湰锛堝懆鍏?0:00鑷姩鎵ц锛?
- `jack_recommendations.json` - 姣忔棩鎺ㄨ崘璁板綍锛堜緵鍛ㄦ湯璁粌鐢級
- `jack_weekend_learning.json` - 璁粌瀛︿範璁板綍
- 娴佺▼锛氳幏鍙栦笂鍛ㄦ帹鑽愮湡瀹炶鎯?鈫?璁＄畻3%/5%鑳滅巼 鈫?甯傚満婕斿寲鍒嗘瀽 鈫?绛栫暐璋冩暣 鈫?閽夐拤鎺ㄩ€?
- 瀹氭椂浠诲姟ID: `0ddec1ca-c95f-4acd-8b4f-766824c9f300`
- 閫夎偂鏍稿績锛歄R铻嶅悎閫夎偂锛堟崲鎵嬬巼/娑ㄥ箙/浠锋牸/瓒嬪娍/MA5锛?+ 娑ㄥ仠鍔犲垎锛堟定鍋?20/鎺ヨ繎娑ㄥ仠+10锛?

# TradingAgents-CN 閲忓寲閫夎偂绯荤粺 (2026-04-04 鍒涘缓锛寁3 鏈€鏂?

鍩轰簬鍛ㄧ嚎CCI浠?100绐佺牬/5鍛ㄥ唴绌?杞?+ 鑲′环绔欎笂20/30鍛ㄥ潎绾跨殑鍏淮璇勫垎鏂规

### v3 鏈€缁堢瓥鐣ワ紙2026-04-04 鏅氱‘璁わ級
- 鍛ㄧ嚎CCI鏍稿績涔扮偣A锛欳CI浠?100涓嬫柟鍚戜笂绐佺牬 -> 30鍒?
- 鍛ㄧ嚎CCI鏍稿績涔扮偣B锛?*5鍛ㄥ唴绌胯秺0杞?*锛堢敱璐熻浆姝ｏ級锛屽綋鍓嶅湪-20~+50闇囪崱 -> 28鍒?
- 鍛ㄧ嚎CCI绌胯酱璧板己锛?鍛ㄥ唴绌胯酱鍚庡綋鍓嶅湪50~100 -> 20鍒?
- 鏃ョ嚎鍧囩嚎锛歁A5涓婄┛MA20+MA10鍙岄噾鍙?-> 25鍒?

### 鍏淮璇勫垎浣撶郴锛堟弧鍒?10鍒嗭級
| 缁村害 | 婊″垎 | 鏍稿績淇″彿 |
|------|------|---------|
| 鈶?鍛ㄧ嚎瓒嬪娍杩囨护 | 10+杩囨护 | WMA20/30璧板钩/涓婅+鑲′环<=WMA20x1.05锛堜竴绁ㄥ惁鍐筹級 |
| 鈶?CCI鍛ㄧ嚎鐘舵€?| 30 | 浠?100绐佺牬锛?0锛夋垨5鍛ㄥ唴绌?杞达紙28锛?|
| 鈶?鍧囩嚎閲戝弶 | 25 | MA5涓婄┛MA20+MA10鍙岄噾鍙?|
| 鈶?閲忎环閰嶅悎 | 20 | 閲忎环榻愬崌鏈€浣?|
| 鈶?瓒嬪娍缁撴瀯 | 15 | 鍒?0/60鏃ユ柊楂?|
| 鈶?鍩烘湰闈㈠姞鍒?| 10 | 鍑€鍒╂鼎/ROE/璐熷€虹巼 |
| 鈶?鐩稿寮哄害 | 10 | 澶х洏瀵规瘮 |
| 鈶?涔板叆鏃舵満 | 10 | 鍥炶俯鍧囩嚎鏀拺 |

### 鐩綍
`TradingAgents-CN/`锛堝凡Git鎻愪氦 v3锛?
- `score_cci_ma_crossover.py` - 涓昏瘎鍒嗚剼鏈?
- `STRATEGY.md` - 瀹屾暣绛栫暐鏂囨。
- `run_scan.bat` - Windows鎵ц鑴氭湰

### 璇勭骇绛夌骇
- 85-100鍒?S绾?閲嶄粨锛堥渶鍚屾椂閫氳繃鍛ㄧ嚎杩囨护锛?
- 70-84鍒? A绾?绉瀬涔板叆
- 55-69鍒? B绾?杞讳粨璇曟帰
- 40-54鍒? C绾?瑙傛湜

### 瀹炴祴鏁堟灉锛?026-04-04锛?
- 50鍙壂鎻忥細31鍙鍛ㄧ嚎杩囨护娣樻卑锛?鍙€氳繃
- S绾ф爣鐨勶細娴风帇鐢熺墿(000078)銆佺洂鐢版腐(000088)
- 鍛ㄧ嚎杩囨护锛氳偂浠?WMA20鎴?WMA30 + 涓ょ嚎璧板钩/涓婅 + 涔栫<=5%

## 鎶€鏈鑼冨亸濂?

- 鍧囩嚎閲戝弶鏈€浣冲弬鏁帮細涓€绌夸竴涓?鏃ョ┛30鏃ュ潎绾匡紙鎴愬姛鐜?2%锛夛紝涓€绌夸簩涓?鏃ョ┛20鏃?30鏃ュ潎绾匡紙鎴愬姛鐜?5%锛夛紱閫夎偂鎶€鏈寚鏍囷細鏃ユ垚浜ら噺鏀惧ぇ鍒拌繃鍘?0鏃ュ潎閲忕殑1.6鍊嶄互涓娿€佽偂浠峰洖璋冧笉瓒呰繃12%銆佺珯涓?0鏃ュ潎绾匡紱鍙傛暟闃堝€硷細閲忔瘮鈮?.0x銆丮A60鍋忕鈮?%
- RPS鐑姏鍥炬帹閫佹柟妗堬細鍥惧簥鏂规涓嶅彲闈狅紙imgbb/catbox/SM.MS鍧囧け璐ワ級锛屾敼涓虹函Markdown琛ㄦ牸鎺ㄩ€侊紝emoji鏍囪寮哄害涓庤秼鍔匡紝鎵ц鏃堕棿浠?2绉掗檷鑷?2绉?

## 缁忛獙涓庡喅绛?

- 澶у姏姘存墜绯荤粺绱浜ゆ槗140绗旓紝骞冲潎+3.94%锛岄粍閲戝尯闂?-6%
- 鑸归暱閽撻奔鎴樻硶鍦ㄥ競鍦洪渿鑽℃湡鍙兘鏃犵鍚堟爣鐨勶紝鏉′欢缁勫悎锛堜环鏍?-80鍏冦€佹定骞?-10%銆佹崲鎵嬬巼>3%銆佸競鍊?0-300浜匡級鍙兘杩囦弗
- 绯荤粺瀹夊叏璇勫垎82%锛?/11椤归€氳繃锛夛紝涓昏椋庨櫓锛歐indows Defender瀹炴椂闃叉姢鏈惎鐢ㄣ€佺鐩樻湭鍔犲瘑锛圔itLocker锛?

## 鏁版嵁婧愬浠芥柟妗?(2026-04-24 鏂板)

### 鏍稿績鏂囦欢
- `data_source_backup.yaml` - 鏁版嵁婧愬浠介厤缃紙涓绘簮+澶囩敤婧愶級
- `data_source_fallback.py` - 鏁版嵁婧愬垏鎹㈠伐鍏凤紙鑷姩鍒囨崲+鍋ュ悍妫€鏌ワ級
- `popeye_fast.py` - 澶у姏姘存墜杞婚噺鐗堬紙璺宠繃缂犺锛屼富鑴氭湰瓒呮椂鏃朵娇鐢級
- `gaoxin_money_flow_akshare.py` - 楂樻璧勯噾鍑€娴佸叆澶囩敤鐗堬紙akshare鏇夸唬濡欐兂API锛?

### 澶囩敤绛栫暐
褰撲富鏁版嵁婧愯秴鏃舵垨澶辫触鏃讹紝鑷姩鍒囨崲鍒板鐢ㄦ簮锛?

| 鏁版嵁绫诲瀷 | 涓绘暟鎹簮 | 澶囩敤婧? | 澶囩敤婧? | 澶囩敤婧? |
|---------|---------|---------|---------|---------|
| 琛屾儏鏁版嵁 | 涓滄柟璐㈠瘜 | 鑵捐璇佸埜 | 鏂版氮璐㈢粡 | Yahoo Finance |
| 璐㈠姟鏁版嵁 | 濡欐兂API | Tushare | AKShare | 宸ㄦ疆璧勮 |
| 鏂伴椈鑸嗘儏 | 璐㈣仈绀?| 涓滆储璧勮 | 鏂版氮璐㈢粡 | i闂储 |
| 闆悆蹇 | 闆悆7x24 | 涓滆储蹇 | 閲戝崄鏁版嵁 | - |
| 璧勯噾娴佸悜 | 涓滄柟璐㈠瘜 | AKShare | 鍚岃姳椤?| - |
| 鐮旀姤鏁版嵁 | 鎱у崥鎶曠爺 | 涓滆储鐮旀姤 | 鍚岃姳椤?| - |
| 浼板€兼暟鎹?| 鐞嗘潖浠?| AKShare | 涓滆储浼板€?| - |
| 瀹忚鏁版嵁 | 鍥藉缁熻灞€ | AKShare | 涓滆储瀹忚 | - |

### 鍒囨崲瑙﹀彂鏉′欢
- 瓒呮椂 > 60绉?
- 杩炵画5娆″け璐?
- HTTP鐘舵€佺爜 429/503

### 鍋ュ悍妫€鏌?
姣?鍒嗛挓鑷姩妫€鏌ユ墍鏈変富鏁版嵁婧愮姸鎬?

### 閫氱煡鏈哄埗
鍒囨崲鏃惰嚜鍔ㄥ彂閫侀拤閽夐€氱煡锛歚鈿狅笍 鏁版嵁婧愬垏鎹細{source} -> {backup}`


## GitHub vibe-dashboard (2026-07-12 鏇存柊)
- PAT: `[REDACTED_PAT]` (hantu-zh, 2026-07-12)
- 宸插拰寉: `[REDACTED_PAT]` (澶辨晥)銆亄ndex.html/sync_func.py `vibe-dashboard/`涓婄殑鏍硅妭鐢↖n workspace鐨勬槸`GITHUB_TOKEN`鐜舰
- 鎺ㄩ€佹柟寮? GitHub REST API (urllib)锛屾枃浠跺湪 repo 鏍硅妛 `daily_picks.json`锛岄泦鎴栧瓨 `vibe-dashboard/` 瀛愮洰
- 娉ㄦ満: 蹇呴』浣跨敤 PAT锛孏it push 涓嶅彲


## 数据保存路径规范 (2026-06-30 新增)
- 所有选股脚本必须保存到 `vibe-dashboard/daily_picks.json`（正确路径）
- 禁止写入 `workspace/daily_picks.json`（旧路径，Dashboard 不会读取）
- 统一使用 `daily_picks_store.save_daily_picks()` 接口
- 修复过的脚本：
- hot_chase_picks.py → vibe-dashboard/daily_picks.json（已修复）
- gaoxin_money_flow_akshare.py → daily_picks_store（已修复）
- update_slowrise.py → vibe-dashboard/daily_picks.json（已修复）
- _gx_quarter_rank.py → vibe-dashboard/daily_picks.json（已修复）
- gaoxin_us_picks_v2.py → vibe-dashboard/us_picks.json（已修复）
- jack_captain.py → daily_picks_store（已修复）
- RPS_thermal_dingtalk.py → 写 vibe-dashboard/daily_picks.json（正确）
- emoji 打印会触发 Windows GBK UnicodeEncodeError，需移除或替换为 ASCII 字符

## 慢热板块追踪 (2026-06-30 完整)
- Dashboard 客户端实时计算：从 GitHub Pages 读取 vibe_trend_history.json
- 数据流：update_slowrise.py → 直接写 vibe_trend_history.json（绕过 daily_picks.json）
- cron 任务：交易日 15:30 执行 merge + sync
- 历史回填：daily_picks.json 中有 06-27/29 数据（回填到 vibe_trend_history.json）
- min_streak 从 3 降至 2（端午假期打断，06-24~30 只有 5 个交易日但非连续）

## 美股/ETF cron
- 美股：gaoxin_us_picks_v2.py 保存到 vibe-dashboard/us_picks.json
- **cron 调度**：交易日早上 9:00 (Asia/Shanghai, cron ID: `27b940ca-3f47-45e9-882c-a07153a2b90c`)
- **日期映射规则**：美股交易时间晚于北京时间，所以 9:00 AM 北京时间 = 获取美股前一交易日数据
- 例如：北京时间 7-10 09:00 = 美股 7-09 收盘数据
- 节假日顺延（脚本内自动判断美国节假日）
- ETF：etf_sync.py 交易日 09:30 和 14:00 各跑一次
- sync_vibe_to_github.py 会同步 us_picks.json + etf_data.json
- 旧 cron ID `fe62822f-c425-4450-98a8-42b6fc722185`（10:00，禁用）已废弃
## 强势股雷达修复 (2026-07-01)

### 自选股票池动态更新（2026-07-12）
- `generate_research_html.py` 去掉 subprocess GitHub 同步（避免 GITHUB_TOKEN 空时挂起）
- `research_scanner.py` 每次跑（12:00/20:00）都强制重新生成 research.html（不再要求 `added > 0`）
- 清理了 research_scanner.py 中已无用的 `sync_github()` 函数
- GitHub 同步统一由 news_sync cron 每 5 分钟处理

### 益盟强买（yimeng_strongbuy.py）
- 脚本路径：`C:\Users\china\.qclaw\workspace\yimeng_strongbuy.py`
- 数据源：Sina 换手率 Top100 + efinance 主力资金净流入历史
- 输出文件：`vibe-dashboard/strongbuy_data.json`（格式：`{"updated": "YYYY-MM-DD", "yimeng": [...]}`
- **定时任务**：已创建 cron job "益盟强买每日更新 v1.0"
- ID: `249500d5-2750-45b6-b73a-11a5d7130788`
- 调度：交易日 15:30 (Asia/Shanghai)
- 动作：运行 `yimeng_strongbuy.py` → 同步 GitHub
- 注意：`strongbuy_data.json` 格式与 `auto_update_strong_stocks.py` 不兼容，后者会覆盖，建议禁用后者或改用不同文件名

### 追涨强势股（hot_chase_picks.py）
- 脚本路径：`C:\Users\china\.qclaw\workspace\hot_chase_picks.py`（注意文件名 typo: chas**e**）
- **已修复**（2026-07-01）：
- 去除所有 emoji（避免 Windows GBK 崩溃），替换为 `[超强]` `[强]`
- 保存逻辑改为 `daily_picks_store.save_daily_picks('追涨强势股', stocks, task_time=period)`
- 根据当前时间自动判断时段（10:00/12:00/14:00）
- 添加了 `daily_picks_store` 失败时的 fallback 逻辑
- cron 任务：3 个（10:00/12:00/14:00），注意脚本文件名有 typo

### strong.html 数据加载逻辑
- `strong.html` 动态加载 `strongbuy_data.json`
- 期望格式：`{"updated": "...", "yimeng": [...]}`
- `_data.yimeng` 存储益盟强买数据
- 若加载失败，`_yimengLoaded = false`，tab 显示"暂无数据"
