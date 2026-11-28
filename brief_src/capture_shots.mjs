// 面板截图：ETF 信号区 + 跨资产主图 -> brief_site/img/
// 只截核心区域，避开页面底部的数据来源小字；失败时降级为无图，不阻断简报。
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

let pkg;
try {
  pkg = (await import('file:///C:/Users/china/.workbuddy/binaries/node/workspace/node_modules/playwright-core/index.js')).default;
} catch {
  pkg = (await import('playwright-core')).default;
}
const { chromium } = pkg;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, 'brief_site', 'img');
const EXE = process.env.CHROME_PATH || chromium.executablePath();
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36';

// 只取局部：ETF 近期信号段、跨资产主图近期段、沪深300 近期段
const TARGETS = [
  { name: 'etf', url: 'https://hantu-zh.github.io/vibe-dashboard/etf/', sel: '#signalSection', dx: 760, dy: 40, w: 528, h: 370 },
  { name: 'regime', url: 'https://hantu-zh.github.io/vibe-dashboard/cross_asset_regime.html', sel: '#chart1', dx: 540, dy: 20, w: 554, h: 290 },
  { name: 'hs300', url: 'https://hantu-zh.github.io/vibe-dashboard/cross_asset_regime.html', sel: '#chart2', dx: 520, dy: 20, w: 574, h: 250 },
  { name: 'dist20', url: 'https://hantu-zh.github.io/vibe-dashboard/etf/', sel: '#distB20', dx: 0, dy: 0, w: 561, h: 158 },
  { name: 'dist120', url: 'https://hantu-zh.github.io/vibe-dashboard/etf/', sel: '#distB120', dx: 0, dy: 0, w: 561, h: 158 },
  // 双确认图：从标题上方 39px 起，含图例、柱图、横轴与底部琥珀条/绿三角，止于说明小字之前
  { name: 'veto', url: 'https://hantu-zh.github.io/vibe-dashboard/etf_veto.html', sel: '#dcChart', dx: 0, dy: -39, w: 1080, h: 422 },
  // 市场宽度/情绪：情绪分近20交易日曲线（含图例/分档线，容器本身干净无来源小字）
  { name: 'breadth', url: 'https://hantu-zh.github.io/vibe-dashboard/breadth.html', sel: '#chart', dx: 0, dy: 0, w: 1146, h: 236 },
  // 国家队托底：①持仓代理双Y轴（占比 vs 沪深300）。裁掉底部缩略时间轴滚动条（容器470px，取前445px）。
  { name: 'gjd', url: 'https://hantu-zh.github.io/vibe-dashboard/gjd_holdings.html', sel: '#chartA', dx: 0, dy: 0, w: 1162, h: 445 },
];

async function main() {
  fs.mkdirSync(OUT, { recursive: true });
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: EXE,
      headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
  } catch (e) {
    console.log('WARN 截图跳过（浏览器不可用）:', e.message.slice(0, 160));
    return;
  }
  for (const t of TARGETS) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, userAgent: UA });
    try {
      await page.goto(t.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForTimeout(9000);
      const el = await page.$(t.sel);
      if (!el) throw new Error('selector missing: ' + t.sel);
      await el.evaluate(e => e.scrollIntoView({ block: 'center' }));
      await page.waitForTimeout(400);
      const box = await el.boundingBox();
      if (!box) throw new Error('no bounding box: ' + t.sel);
      const clip = {
        x: box.x + t.dx,
        y: box.y + t.dy,
        width: Math.min(t.w, box.width - t.dx),
        height: Math.min(t.h, box.height - t.dy),
      };
      if (clip.width <= 0 || clip.height <= 0) throw new Error('bad clip: ' + t.name);
      const dest = path.join(OUT, t.name + '.png');
      await page.screenshot({ path: dest, clip });
      console.log('OK ->', dest, `${Math.round(clip.width)}x${Math.round(clip.height)}`);
    } catch (e) {
      console.log('WARN 截图跳过', t.name + ':', e.message.slice(0, 160));
    } finally {
      await page.close();
    }
  }
  await browser.close();
}

main().catch(e => console.log('WARN 截图跳过:', e.message.slice(0, 200)));
