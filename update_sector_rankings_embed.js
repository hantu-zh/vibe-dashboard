// update_sector_rankings_embed.js
const fs = require('fs');

// 读取 daily_picks.json
const data = JSON.parse(fs.readFileSync('daily_picks.json', 'utf8'));
const srData = data.sector_rankings || {};

if (Object.keys(srData).length === 0) {
  console.log('[ERROR] sector_rankings not found');
  process.exit(1);
}

console.log(`[INFO] sector_rankings: ${Object.keys(srData).length} dates`);
console.log(`[INFO] Latest date: ${Object.keys(srData).sort().reverse()[0]}`);

// 读取 index.html
let html = fs.readFileSync('index.html', 'utf8');

// 查找第一个 sector-rankings-embed
const pattern = /<script type="application\/json" id="sector-rankings-embed">[\s\S]*?<\/script>/;
const match = html.match(pattern);

if (!match) {
  console.log('[ERROR] No sector-rankings-embed found');
  process.exit(1);
}

console.log(`[INFO] Found sector-rankings-embed at position ${match.index}`);

// 生成新的 embed 内容
const srJson = JSON.stringify(srData, null, 2);
const newEmbed = `<script type="application/json" id="sector-rankings-embed">\n${srJson}\n  </script>`;

// 替换
html = html.replace(pattern, newEmbed);

// 保存
fs.writeFileSync('index.html', html, 'utf8');

console.log('[OK] Updated sector-rankings-embed');
