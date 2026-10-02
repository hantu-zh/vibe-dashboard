/* vibe-analytics 前端埋点 · 静默上报，不产生任何可见元素
 *
 * 部署：在本仓库根目录放置本文件，并在每个页面的 </body> 前加一行
 *   <script defer src="analytics.js"></script>
 *
 * 上报内容：页面路径、来源(referrer)、浏览器匿名访客 ID（localStorage 持久化）
 * 不采集：表单内容、点击、任何用户输入
 * 后端：https://vibe-analytics.app.workbuddy.host/collect
 */
(function () {
  var cfg = window.__VIBE_ANALYTICS__ || {};
  // 默认端点写死为生产收集地址；如需覆盖可在页面先定义 window.__VIBE_ANALYTICS__={endpoint:'...'}
  var ENDPOINT = cfg.endpoint || 'https://vibe-analytics.app.workbuddy.host/collect';
  if (!ENDPOINT) return;

  var KEY = 'vibe_vid';
  var vid = '';
  try {
    vid = localStorage.getItem(KEY) || '';
    if (!vid) {
      vid = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : 'v-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
      localStorage.setItem(KEY, vid);
    }
  } catch (e) { vid = ''; }

  var payload = JSON.stringify({
    p: location.pathname,
    referrer: document.referrer || '',
    visitorUuid: vid
  });

  // 首选 sendBeacon：页面关闭也能送达；text/plain 属 CORS 安全类型，不触发预检
  try {
    if (navigator.sendBeacon) {
      var blob = new Blob([payload], { type: 'text/plain' });
      if (navigator.sendBeacon(ENDPOINT, blob)) return;
    }
  } catch (e) {}

  try {
    fetch(ENDPOINT, { method: 'POST', body: payload, headers: { 'Content-Type': 'text/plain' }, keepalive: true, mode: 'cors' });
  } catch (e) {}
})();
