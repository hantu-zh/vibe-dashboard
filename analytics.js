/* vibe-analytics 前端埋点 · 静默上报，不产生任何可见元素
 *
 * 部署：在本仓库根目录放置本文件，并在每个页面的 </body> 前加一行
 *   <script defer src="analytics.js"></script>
 * 如需覆盖端点，可在页面先定义 window.__VIBE_ANALYTICS__={endpoint:'...'}
 *
 * 上报内容：页面路径、来源(referrer)、浏览器匿名访客 ID、国内 IP 地域(省/市)
 * 不采集：表单内容、点击、任何用户输入
 * 后端：https://vibe-analytics.app.workbuddy.host/collect
 */
(function () {
  var cfg = window.__VIBE_ANALYTICS__ || {};
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

  // 国内 IP 地域（省份 / 城市）：通过太平洋 IP 库 JSONP 获取
  // 在浏览器侧（国内）直连，<script charset="gb2312"> 让浏览器自动解码 GBK，无需任何依赖
  function getGeo(cb) {
    var cbName = '__vibeGeo' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    var done = false;
    var timer = setTimeout(function () { if (!done) { done = true; cb(null); } }, 2000);
    window[cbName] = function (d) {
      if (done) return; done = true; clearTimeout(timer);
      try { delete window[cbName]; } catch (e) {}
      cb(d);
    };
    var s = document.createElement('script');
    s.charset = 'gb2312';
    s.src = 'https://whois.pconline.com.cn/ipJson.jsp?callback=' + cbName;
    s.onerror = function () { if (!done) { done = true; clearTimeout(timer); cb(null); } };
    (document.head || document.documentElement).appendChild(s);
  }

  function send(geo) {
    var country = '', region = '', city = '';
    if (geo && !geo.err) {
      country = 'CN';
      region = (geo.pro || '').replace(/(省|市|自治区|特别行政区|壮族|回族|维吾尔)/g, '');
      city = (geo.city || '').replace(/(市|区|县|旗)/g, '');
    } else if (geo && geo.err) {
      country = '海外';
    }
    var payload = JSON.stringify({
      p: location.pathname,
      referrer: document.referrer || '',
      visitorUuid: vid,
      country: country,
      region: region,
      city: city
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
  }

  // 先拿地域，再随主上报一起发出（只发一次；超时 2s 则不带地域）
  getGeo(function (geo) { send(geo); });
})();
