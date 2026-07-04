# -*- coding: utf-8 -*-
"""更新 index.html 中的 sector-rankings-embed"""
import json
import re

# 读取 daily_picks.json
with open('daily_picks.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

sr_data = data.get('sector_rankings', {})
if not sr_data:
    print('[ERROR] sector_rankings not found')
    exit(1)

# 生成 JSON 字符串
sr_json = json.dumps(sr_data, ensure_ascii=False, indent=2)
print(f'[INFO] sector_rankings: {len(sr_data)} dates, {len(sr_json)} chars')

# 读取 index.html
with open('index.html', 'r', encoding='utf-8') as f:
    html = f.read()

# 查找第一个 sector-rankings-embed 的位置
pattern = r'<script type="application/json" id="sector-rankings-embed">.*?</script>'
matches = list(re.finditer(pattern, html, re.DOTALL))

if len(matches) == 0:
    print('[ERROR] No sector-rankings-embed found')
    exit(1)

print(f'[INFO] Found {len(matches)} sector-rankings-embed tags')

# 替换第一个 embed
first_match = matches[0]
new_embed = f'<script type="application/json" id="sector-rankings-embed">\n{sr_json}\n  </script>'

html_new = html[:first_match.start()] + new_embed + html[first_match.end():]

# 如果有第二个 embed，删除它
if len(matches) > 1:
    # 第二个 embed 是占位符，保留它但标记为已更新
    print('[WARN] Found second sector-rankings-embed (placeholder), keeping it')

# 保存更新后的 HTML
with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html_new)

print(f'[OK] Updated sector-rankings-embed with latest data')
print(f'[INFO] Latest date: {max(sr_data.keys())}')
