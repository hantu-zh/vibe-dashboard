/* vibe-analytics 兼容层 → UStat 统计（2026-11-18）
 *
 * 旧的 vibe-analytics /collect、dz-stock-tracker /hit、/counter 端点已全部下线
 * （404 / 403），不再请求。本文件保留各页面的引用不变，改为加载 UStat 脚本，
 * 让全站访问计数继续工作。
 *
 * UStat 脚本自动上报 PV / 指纹 / 点击到其 /api/report，跨域友好，
 * 不依赖本页 Origin。原站点里 <script defer src="analytics.js"> 的引用无需改动。
 */
(function () {
  var SRC = 'https://01a161c1-4647-7775-a534-adb8b649f49f.spst2.com/ustat.js';
  var HOST = 'https://01a161c1-4647-7775-a534-adb8b649f49f.spst2.com';

  // 幂等：避免同页多处引用导致重复注入
  if (window.__USTAT_INJECTED__) return;
  window.__USTAT_INJECTED__ = true;

  // 若页面已有静态 UStat 标签（本站多数页面头部已加），则不再动态注入，
  // 避免同一页加载两次、上报重复。只引用 analytics.js 的页面仍由此处注入。
  function hasStaticUstat() {
    var list = document.querySelectorAll('script[src]');
    for (var i = 0; i < list.length; i++) {
      if (list[i].src && list[i].src.indexOf('ustat.js') >= 0) return true;
    }
    return false;
  }

  function inject() {
    if (hasStaticUstat()) return;
    var s = document.createElement('script');
    s.async = true;
    s.src = SRC;
    // 显式指定上报 host，避免脚本解析 currentScript.src 时受子目录相对路径影响
    s.setAttribute('data-host-url', HOST);
    (document.head || document.body).appendChild(s);
  }

  if (document.head || document.body) inject();
  else document.addEventListener('DOMContentLoaded', inject, { once: true });
})();
