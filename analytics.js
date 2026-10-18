/* vibe-analytics 前端埋点 · 静默上报，不产生任何可见元素
 *
 * 部署：在本仓库根目录放置本文件，并在每个页面的 </body> 前加一行
 *   <script defer src="analytics.js"></script>
 *
 * 上报内容：页面路径、来源(referrer)、浏览器匿名访客 ID（localStorage 持久化）
 * 不采集：表单内容、点击、任何用户输入
 *
 * 双发：
 *   1) https://vibe-analytics.app.workbuddy.host/collect   —— 旧看板，全站口径（保持不动）
 *   2) https://dz-stock-tracker.app.workbuddy.host/hit     —— pv-server，带 site=gh 分站点统计
 *   第二路失败完全不影响第一路；两路都静默，不阻塞页面。
 */
(function () {
  var cfg = window.__VIBE_ANALYTICS__ || {};

  // 旧服务：全站口径，旧看板继续用
  var ENDPOINT_OLD = cfg.endpoint || 'https://vibe-analytics.app.workbuddy.host/collect';
  // 新计数服务：分站点统计（GH 主站固定 site=gh）
  var ENDPOINT_SITE = cfg.siteEndpoint === null
    ? ''
    : (cfg.siteEndpoint || 'https://dz-stock-tracker.app.workbuddy.host/hit');

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

  var path = location.pathname;
  var ref = document.referrer || '';

  function send(url, payload) {
    if (!url) return;
    try {
      if (navigator.sendBeacon) {
        var blob = new Blob([payload], { type: 'text/plain' });
        if (navigator.sendBeacon(url, blob)) return;
      }
    } catch (e) {}
    try {
      fetch(url, {
        method: 'POST', body: payload, keepalive: true,
        headers: { 'Content-Type': 'text/plain' }, mode: 'cors',
      }).catch(function () {});
    } catch (e) {}
  }

  // 1) 旧服务（字段名保持原样，别动，否则旧看板取值会断）
  send(ENDPOINT_OLD, JSON.stringify({
    p: path,
    referrer: ref,
    visitorUuid: vid,
  }));

  // 2) 新计数服务：显式声明 site=gh，不再依赖 path 前缀去猜
  send(ENDPOINT_SITE, JSON.stringify({
    site: 'gh',
    p: path,
    referrer: ref,
    vid: vid,
  }));
})();
