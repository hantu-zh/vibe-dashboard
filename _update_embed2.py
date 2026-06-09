#!/usr/bin/env python3
import sys, json
sys.stdout.reconfigure(encoding='utf-8')

LOCAL_HTML = r'C:\Users\china\.qclaw\workspace\vibe-dashboard\index.html'
LOCAL_PICKS = r'C:\Users\china\.qclaw\workspace\daily_picks.json'

with open(LOCAL_PICKS, 'r', encoding='utf-8') as f:
    picks = json.load(f)
with open(LOCAL_HTML, 'r', encoding='utf-8') as f:
    html = f.read()

# 正确识别日期 key (YYYY-MM-DD格式)
SKIP = {'sector_rankings', 'generated_at', 'count', 'candidates'}
import re
date_keys = sorted([k for k in picks.keys() if re.match(r'^\d{4}-\d{2}-\d{2}$', k)])
task_keys = [k for k in picks.keys() if k not in date_keys and k not in SKIP]
print('Date keys:', date_keys)
print('Task keys:', task_keys)

embed_data = {}
for dk in date_keys:
    embed_data[dk] = picks[dk]
for tk in task_keys:
    embed_data[tk] = picks[tk]
if 'sector_rankings' in picks:
    embed_data['sector_rankings'] = picks['sector_rankings']

# Replace daily-picks-embed
START = '<script id="daily-picks-embed" type="application/json">'
s = html.find(START)
e = html.find('</script>', s)
new_embed = '\n' + json.dumps(embed_data, ensure_ascii=False, indent=2) + '\n'
new_html = html[:s+len(START)] + new_embed + html[e:]
with open(LOCAL_HTML, 'w', encoding='utf-8') as f:
    f.write(new_html)

print('Done! Updated.')
