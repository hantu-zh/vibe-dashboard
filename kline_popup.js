/*!

 * kline_popup.js — 全站通用「点击看K线」弹窗（无外部依赖，纯手绘 SVG）

 *

 * 用法：在页面 </body> 前引入

 *   <script src="kline_popup.js?v=1" defer></script>

 *

 * 可选配置（必须在引入本脚本之前定义）：

 *   <script>window.KLINE_CONFIG = { rowSelectors:['#watchBody tr'], triggerSelectors:['#watchBody tr'] };</script>

 *

 * 设计要点：

 *  1) 只认 6 位纯数字代码 → 美股（AAPL 之类）天然被排除，无需逐页判断；

 *  2) 数据四级兜底：kline_cache.json → 腾讯 newfqkline(JSONP) → 腾讯 proxy.finance(JSONP)

 *     → 东财 push2his(JSONP)。**跨域一律走 <script> JSONP**，不用 fetch：

 *     github.io 上腾讯老接口 fqkline 已被 WAF 拦（501 HTML，浏览器报 CORS）；

 *     push2his 在部分网络下 ERR_EMPTY_RESPONSE，所以只当最后兜底。

 *  3) 红涨绿跌（A股习惯）、MA5/MA10/MA20、成交量、十字光标悬浮读数；

 *  4) 支持 日K / 周K / 月K 切换，同一次会话内缓存结果。

 */

(function () {

  'use strict';

  if (window.__KLINE_POPUP_READY__) return;

  window.__KLINE_POPUP_READY__ = true;



  /* 本脚本自身所在目录（用于拼同目录资源，如 kline_indicator.js / kline_cache.json）。
   * 必须用它而不是相对路径：像 /k/ 这种子目录页面若用相对路径，会被解析到 /k/xxx 而 404。 */
  var SELF_BASE = (function () {
    try {
      var d = document.currentScript;
      if (!d || !d.src) {
        var ss = document.getElementsByTagName('script');
        for (var i = ss.length - 1; i >= 0; i--) {
          if (/kline_popup\.js/.test(ss[i].src || '')) { d = ss[i]; break; }
        }
      }
      if (d && d.src) return d.src.replace(/[?#].*$/, '').replace(/[^/]*$/, '');
    } catch (e) { }
    return '';
  })();



  /* ────────────────────────── 配置 ────────────────────────── */



  var CFG = Object.assign({

    // 「一行股票」的容器（点击后据此取代码/名称）

    rowSelectors: ['.picks-row', '.stock-item', 'tr[data-kline-code]', '[data-kline-row]', 'tr[data-kline-sym]'],

    // 行内「可以被点击」的元素；命中后才打开弹窗，避免误伤同页其它表格

    triggerSelectors: [

      'a.picks-link', '.picks-code',

      'a.stock-name', 'a.stock-code', '.stock-code',

      'a[href*="quote.eastmoney.com"]',

      'a[href*="finance.sina.com.cn/realstock"]',

      // 行业板块行 / 指数卡片 / 慢热板块标签等本身带 data-kline-sym 的元素（及其子元素）也可点

      '[data-kline-sym]', '[data-kline-sym] *'

    ],

    // 明确不处理的区域（美股面板等）

    excludeSelectors: [

      '#us-stock-list', '.us-stock-panel', '.us-stock-container',

      '.us-table-wrap', '.us-table', '.us-picks-header', '[data-no-kline]'

    ],

    bars: 120,         // 默认取最近多少个交易日（图表显示根数；下限 80）
    // 指标计算用的历史深度：ZIG/PEAKBARS/TROUGHBARS 是重绘函数，依赖完整历史，
    // 只用 60 根会在窗口左边缘产生假信号（与通达信不一致）。日线按此深度多取一些，
    // 计算后由 TDXIndicator.trim() 裁回 bars 根显示。
    historyBars: 420,

    hint: '点击查看K线'

  }, window.KLINE_CONFIG || {});



  var CACHE_URL = SELF_BASE + 'kline_cache.json';



  /* ────────────────────────── 样式 ────────────────────────── */



  var CSS = [

    '.kl-pop-overlay{display:none;position:fixed;inset:0;z-index:99999;background:rgba(3,6,14,.74);',

    '-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);align-items:center;justify-content:center;padding:14px}',

    '.kl-pop-overlay.show{display:flex}',

    '.kl-pop{background:linear-gradient(165deg,#141b30 0%,#0b1020 100%);border:1px solid #27314f;border-radius:14px;',

    'width:100%;max-width:660px;max-height:90vh;overflow-y:auto;color:#e6e9f2;box-shadow:0 26px 64px rgba(0,0,0,.62);',

    'animation:klIn .16s ease-out}',

    '@keyframes klIn{from{transform:translateY(12px) scale(.985);opacity:0}to{transform:none;opacity:1}}',

    /* 全屏：整块面板撑满视口（弹窗与 /k 内联面板共用） */
    '.kl-pop.kl-fs{max-width:none;max-height:none;width:100vw;height:100vh;border-radius:0;border-left:none;border-right:none}',
    '.kl-pop-overlay.kl-fs{padding:0;align-items:stretch;justify-content:stretch;-webkit-backdrop-filter:none;backdrop-filter:none}',
    '.kl-pop.kl-fs .kl-chart{margin:12px 16px 0}',
    '.kl-pop.kl-fs .kl-sub,.kl-pop.kl-fs .kl-tblbox{margin-left:16px;margin-right:16px}',
    '[data-kl-panel].kl-fs{position:fixed !important;inset:0 !important;z-index:99998 !important;background:#0a0f1d !important;padding:14px 16px !important;margin:0 !important;overflow:auto !important;border-radius:0 !important;max-width:none !important;max-height:none !important;width:auto !important;height:auto !important;border:none !important}',
    '.kl-fsbtn{margin-left:8px;background:#232b49;border:none;color:#cfd6ea;border-radius:8px;min-width:28px;height:28px;padding:0 8px;cursor:pointer;font-size:13px;line-height:1}',

    '.kl-head{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;padding:15px 18px 11px;border-bottom:1px solid #232c47}',

    '.kl-head h3{margin:0;font-size:1.16em;color:#ffb74d;font-weight:700}',

    '.kl-head .kl-code{font-family:ui-monospace,Consolas,monospace;font-size:.86em;color:#7b85a8}',

    '.kl-head .kl-live{font-size:.72em;padding:1px 7px;border-radius:6px;background:#123024;color:#4ade80}',

    '.kl-close{margin-left:auto;background:#232b49;border:none;color:#cfd6ea;border-radius:8px;width:28px;height:28px;',

    'cursor:pointer;font-size:15px;line-height:1}',

    '.kl-close:hover{background:#2e3860}',

    '.kl-stats{display:flex;flex-wrap:wrap;gap:6px;padding:11px 18px 0}',

    '.kl-stat{background:#111829;border:1px solid #222b45;border-radius:8px;padding:5px 10px;min-width:74px}',

    '.kl-stat b{display:block;font-size:.95em;font-weight:700;font-variant-numeric:tabular-nums}',

    '.kl-stat span{font-size:.68em;color:#7b85a8}',

    '.kl-tabs{display:flex;gap:6px;padding:12px 18px 0}',

    '.kl-tab{background:#151d33;border:1px solid #27314f;color:#8f9ab8;border-radius:7px;padding:4px 13px;',

    'font-size:.79em;cursor:pointer}',

    '.kl-tab.on{background:#25325c;border-color:#3d5490;color:#dbe6ff}',

    '.kl-chart{position:relative;margin:10px 12px 0;background:#0a0f1d;border:1px solid #1e2740;border-radius:10px;',

    'padding:4px 2px 1px}',

    '.kl-chart svg{width:100%;height:auto;display:block}',

    '.kl-tip{position:absolute;display:none;pointer-events:none;background:rgba(9,14,28,.96);border:1px solid #33405f;',

    'border-radius:8px;padding:7px 10px;font-size:.73em;line-height:1.65;white-space:nowrap;z-index:3;',

    'box-shadow:0 8px 22px rgba(0,0,0,.5);font-variant-numeric:tabular-nums}',

    '.kl-tip i{font-style:normal;color:#7b85a8;display:inline-block;min-width:26px}',

    '.kl-legend{display:flex;flex-wrap:wrap;gap:12px;padding:6px 18px 0;font-size:.73em;color:#8b95b5}',

    '.kl-legend em{font-style:normal;display:inline-flex;align-items:center;gap:5px}',

    '.kl-legend i{width:14px;height:2px;border-radius:2px;display:inline-block}',

    '.kl-note{padding:9px 18px 0;font-size:.72em;color:#6b7494;line-height:1.6}',

    '.kl-foot{display:flex;align-items:center;gap:10px;padding:13px 18px 16px;border-top:1px solid #232c47;margin-top:13px}',

    '.kl-btn{background:#1a3a5e;color:#7fd3ff;text-decoration:none;border:none;border-radius:8px;padding:7px 15px;',

    'font-size:.83em;cursor:pointer}',

    '.kl-btn.ghost{background:#232b49;color:#cfd6ea}',

    '.kl-btn:hover{filter:brightness(1.15)}',

    '.kl-warn{margin-left:auto;text-align:right;font-size:.71em;color:#6b7494;line-height:1.5}',

    '.kl-loading,.kl-err{padding:52px 20px;text-align:center;color:#7b85a8;font-size:.88em}',

    '.kl-err .kl-errsub{margin-top:7px;font-size:.82em;color:#5f6a8c}',

    '.kl-spin{width:22px;height:22px;margin:0 auto 12px;border:2px solid #2a3358;border-top-color:#7fd3ff;',

    'border-radius:50%;animation:klSpin .8s linear infinite}',

    '@keyframes klSpin{to{transform:rotate(360deg)}}',

    /* 可点击提示 */

    'a.picks-link,a.stock-name,a.stock-code,.picks-code{cursor:pointer}',

    'a.picks-link:hover{filter:brightness(1.4)}',

    '.picks-code:hover,a.stock-name:hover,a.stock-code:hover{color:#ffb74d !important}',

    '.kl-clickable{cursor:pointer}',

    '.kl-clickable:hover{background:rgba(90,130,230,.09)}',

    'tr.kl-clickable:hover td{background:rgba(90,130,230,.09)}',

    '.kl-sub{margin:10px 12px 0;background:#0a0f1d;border:1px solid #1e2740;border-radius:10px;padding:4px 2px 1px}',
    '.kl-sub svg{width:100%;height:auto;display:block}',
    '.kl-sub-label{font-size:.72em;color:#7b85a8;padding:6px 10px 0}',
    '.kl-embed-title{display:flex;align-items:center;gap:8px;font-size:.9em;color:#dfe6ff;padding:2px 2px 6px}',
    '.kl-embed-title span{color:#5f6b8f;font-family:ui-monospace,Consolas,monospace;font-size:.92em}',
    '.kl-embed-title em{font-style:normal;font-size:.8em;color:#8A93B0;border:1px solid #24304d;border-radius:4px;padding:1px 6px}',
    '@media(max-width:560px){.kl-stat{min-width:62px;padding:4px 8px}.kl-tip{font-size:.68em}}',

    /* A/B 通用：表格标签页（1/2/3），纯 CSS 单选切换，随周期重渲染自动复位到 1 */
    '.kl-tbltabs-wrap{margin:6px 12px 0}',
    '.kl-tbltabs-wrap>input{position:absolute;width:0;height:0;opacity:0;pointer-events:none}',
    '.kl-tbltabs-wrap>label{cursor:pointer;display:inline-block;background:#151d33;border:1px solid #27314f;color:#8f9ab8;padding:5px 18px;font-size:.8em;border-radius:7px 7px 0 0;margin-right:4px;user-select:none}',
    '.kl-tbltabs-wrap>input:checked+label{background:#25325c;border-color:#3d5490;color:#dbe6ff}',
    '.kl-tblpanel{display:none}',
    '.kl-tbltabs-wrap>input:nth-of-type(1):checked~.kl-tblpanel[data-tab="1"]{display:block}',
    '.kl-tbltabs-wrap>input:nth-of-type(2):checked~.kl-tblpanel[data-tab="2"]{display:block}',
    '.kl-tbltabs-wrap>input:nth-of-type(3):checked~.kl-tblpanel[data-tab="3"]{display:block}',
    '.kl-tbltabs-wrap>input:nth-of-type(4):checked~.kl-tblpanel[data-tab="4"]{display:block}',
    '.kl-box-ov{display:block}'

  ].join('');



  function injectStyle() {

    if (document.getElementById('kl-pop-style')) return;

    var st = document.createElement('style');

    st.id = 'kl-pop-style';

    st.textContent = CSS;

    (document.head || document.documentElement).appendChild(st);

  }



  /* ────────────────────────── 工具 ────────────────────────── */



  function hex6(v) { return String(v == null ? '' : v).toUpperCase(); }



  // 6 位纯数字 = A股/北交所；美股代码、指数等一律不处理

  function isAShare(code) { return /^\d{6}$/.test(code); }

  // 完整符号（如 sh000300 / sz399006）：用于指数、ETF 等前缀与个股不同的品种

  function isSym(code) { return /^(sh|sz|bj)\d{6}$/.test(code) || /^bk\d{4,6}$/i.test(code); }

  // 把代码拆成 {交易所前缀, 6位代码}：同时支持纯6位与完整符号

  function splitSym(code) {

    var m = /^(sh|sz|bj)(\d{6})$/.exec(code);

    if (m) return { ex: m[1], num: m[2] };

    return { ex: (/^(4|8|92)/.test(code) ? 'bj' : (code.charAt(0) === '6' ? 'sh' : 'sz')), num: code };

  }



  // 交易所前缀（腾讯用）

  function txPrefix(code) { return splitSym(code).ex; }

  // 东方财富 secid

  function emSecid(code) {

    var bk = /^bk(\d{4,6})$/i.exec(code);



    if (bk) return '90.BK' + bk[1];



    var s = splitSym(code);

    if (s.ex === 'bj') return '0.' + s.num;

    return (s.ex === 'sh' ? '1.' : '0.') + s.num;

  }

  function emUrl(code) {

    var bk = /^bk(\d{4,6})$/i.exec(code);



    // 板块用东财 unify 行情页（如 90.BK1277），旧的 /bk/BKxxxx.html 会 404
    if (bk) return 'https://quote.eastmoney.com/unify/cr/90.BK' + bk[1];



    var s = splitSym(code);

    if (s.ex === 'bj') return 'https://quote.eastmoney.com/bj/' + s.num + '.html';

    return 'https://quote.eastmoney.com/' + s.ex + s.num + '.html';

  }

  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : 0; }

  function cls(v) { return v > 0 ? 'kl-up' : (v < 0 ? 'kl-down' : ''); }

  function pct(v, nd) {

    if (!isFinite(v)) return '—';

    return (v > 0 ? '+' : '') + v.toFixed(nd == null ? 2 : nd) + '%';

  }



  /* ── 换手率支持 ──
   * 流通股本来自腾讯行情 qt.gtimg.cn（<script> 方式绕 CORS，返回值挂到全局 v_<sym>）：
   *   parts[3]=现价  parts[6]=当日成交量  parts[38]=当日换手率%  parts[44]=流通市值(亿)
   * 流通股本(股) = 流通市值 * 1e8 / 现价。
   * K线量的单位随数据源不同（腾讯=股，东财兜底=手），用官方换手率 × 当日K线量自校准 factor(1或100)，
   * 换手率%(某日) = 该日K线量 * factor / 流通股本 * 100。
   */
  var toCache = {};   // code -> {shares:流通股本(股), factor:量单位系数, to:当日换手率%}

  function parseQtQuote(raw) {
    try {
      var p = String(raw).split('~');
      var price = parseFloat(p[3]);
      var mcap = parseFloat(p[44]);
      if (!(price > 0) || !(mcap > 0)) return null;
      return {
        shares: mcap * 1e8 / price,
        factor: 1,
        to: parseFloat(p[38]) || 0,
        qv: parseFloat(p[6]) || 0
      };
    } catch (e) { return null; }
  }

  function getTurnoverInfo(code) {
    code = hex6(code);
    if (!isAShare(code)) return Promise.resolve(null);   // 指数/ETF符号/美股不取
    if (toCache[code] !== undefined) return Promise.resolve(toCache[code]);
    // 单次 JSONP：成功 resolve({shares,factor,to,qv})，失败/超时 resolve(null)
    function fetchOnce() {
      return new Promise(function (resolve) {
        var sym = txPrefix(code) + splitSym(code).num;
        var g = 'v_' + sym;
        var sc = document.createElement('script');
        var done = false;
        var timer = setTimeout(function () { fin(null); }, 6000);
        function fin(val) {
          if (done) return;
          done = true;
          clearTimeout(timer);
          if (sc.parentNode) sc.parentNode.removeChild(sc);
          try { delete window[g]; } catch (e) { window[g] = undefined; }
          resolve(val || null);
        }
        sc.onload = function () {
          var raw = null;
          try { raw = window[g]; } catch (e) { }
          fin(raw ? parseQtQuote(raw) : null);
        };
        sc.onerror = function () { fin(null); };
        sc.src = 'https://qt.gtimg.cn/q=' + sym + '&r=' + Math.random();
        document.head.appendChild(sc);
      });
    }
    // 失败(null)不缓存、自动重试 2 次（间隔 2.5s）：qt.gtimg 偶发超时不该让整个会话丢换手黄线
    function attempt(left) {
      return fetchOnce().then(function (v) {
        if (v) { toCache[code] = v; return v; }
        if (left <= 0) return null;
        return new Promise(function (res) { setTimeout(function () { res(attempt(left - 1)); }, 2500); });
      });
    }
    return attempt(2);
  }

  /* ───────── 基本面 / 行业数据（AI 简评的「业绩」「行业景气」两段）─────────
   * 东财接口无 CORS 头，一律走 JSONP（fetch 会被拦）：
   *   ① push2 stock/get   → 名称 / 所属行业 / PE(动) / PB / 换手 / 涨跌幅 / 振幅 / ROE / 主力净流入
   *   ② datacenter F10    → 最新报告期 营收同比 / 净利同比 / ROE / 毛利率 / EPS
   *   ③ searchapi suggest → 行业名 → 行业板块 BK 代码
   *   ④ push2 ulist.np    → 行业板块当日涨跌幅 + 主力净流入
   * 结果挂到 ind.fund，供引擎 aiBrief() 使用。任一环缺失都自动降级，不影响技术面段
   * （技术面完全由本地 K 线算出）。成功才缓存；失败不缓存，下次查看会重试。
   */
  var fundCache = {};

  function jsonp(url, cbName, timeoutMs) {
    return new Promise(function (resolve) {
      var cb = 'klj' + Math.floor(Math.random() * 1e9);
      var sc = document.createElement('script');
      var done = false;
      var timer = setTimeout(function () { fin(null); }, timeoutMs || 8000);
      function fin(v) {
        if (done) return;
        done = true;
        clearTimeout(timer);
        if (sc.parentNode) sc.parentNode.removeChild(sc);
        try { delete window[cb]; } catch (e) { window[cb] = undefined; }
        resolve(v);
      }
      window[cb] = function (d) { fin(d); };
      sc.onerror = function () { fin(null); };
      sc.src = url + (url.indexOf('?') >= 0 ? '&' : '?') + cbName + '=' + cb;
      document.head.appendChild(sc);
    });
  }

  function num2(v) { var x = parseFloat(v); return isFinite(x) ? x : null; }

  /* 归一化到 6 位 A 股代码：A 页传的是 sh600111 这类全符号（splitSym 只认小写前缀，
   * 故此处不能用会大写的 hex6），指数/ETF/板块一律返回 null */
  function normAShare(code) {
    var raw = String(code == null ? '' : code).trim().toLowerCase();
    var s = splitSym(raw);
    if (!(s && s.num && /^\d{6}$/.test(s.num))) return null;
    // 显式 sh000xxx / sh999xxx = 上证指数类，不是个股（避免误取同号个股的财报）
    if (/^sh(000|999)/.test(raw)) return null;
    return s.num;
  }

  /* 东财 push2 多域名轮询：单域名在部分网络/插件环境下会被阻断（K 线取数同款兜底策略） */
  var EM_PUSH_HOSTS = ['push2.eastmoney.com', '82.push2.eastmoney.com', 'push2delay.eastmoney.com'];
  function push2Any(pathQuery, timeoutMs) {
    var i = 0;
    function next() {
      if (i >= EM_PUSH_HOSTS.length) return Promise.resolve(null);
      var host = EM_PUSH_HOSTS[i++];
      return jsonp('https://' + host + pathQuery, 'cb', timeoutMs || 6000).then(function (r) {
        return (r && r.data) ? r : next();
      });
    }
    return next();
  }

  function getFundInfo(code) {
    var a6 = normAShare(code);
    if (!a6) return Promise.resolve(null);
    code = a6;
    if (fundCache[code] !== undefined) return Promise.resolve(fundCache[code]);
    var secid = emSecid(code);
    var suf = splitSym(code).ex === 'sh' ? 'SH' : (splitSym(code).ex === 'bj' ? 'BJ' : 'SZ');
    var out = { code: code };

    // ① 公司资料（datacenter 域名稳定）：行业名取东财行业链第二段，如「有色金属-小金属-稀土」→ 小金属
    var p0 = jsonp('https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO' +
      '&columns=SECUCODE,SECURITY_NAME_ABBR,EM2016,BOARD_NAME_LEVEL,INDUSTRYCSRC1,LISTING_DATE' +
      '&filter=(SECUCODE%3D%22' + code + '.' + suf + '%22)&pageSize=1', 'callback', 9000).then(function (r) {
        var row = r && r.result && r.result.data && r.result.data[0];
        if (!row) return;
        if (row.SECURITY_NAME_ABBR) out.name = row.SECURITY_NAME_ABBR;
        var chain = String(row.BOARD_NAME_LEVEL || row.EM2016 || '');
        var seg = chain.split('-');
        if (chain) out.industryChain = chain;
        out.industry = (seg.length > 1 ? seg[1] : seg[0]) || null;
        out.industryTop = seg[0] || null;
        out.csrc = row.INDUSTRYCSRC1 || null;
        out.listed = row.LISTING_DATE ? String(row.LISTING_DATE).slice(0, 10) : null;
      });

    // ② 个股快照（估值 + 资金；push2 域名在部分网络偶发不通，失败即降级）
    var p1 = push2Any('/api/qt/stock/get?secid=' + secid +
      '&fields=f57,f58,f127,f162,f167,f168,f170,f171,f173,f116,f140', 6000).then(function (r) {
        var d = r && r.data;
        if (!d) return;
        if (d.f58) out.name = out.name || d.f58;
        if (!out.industry) out.industry = d.f127 || null;   // ① 已给行业链时以①为准
        var pe = num2(d.f162), pb = num2(d.f167), to = num2(d.f168), chg = num2(d.f170), amp = num2(d.f171);
        out.pe = pe != null ? pe / 100 : null;      // 东财未带 fltt=2 时价格类字段放大 100 倍
        out.pb = pb != null ? pb / 100 : null;
        out.turnover = to != null ? to / 100 : null;
        out.chg = chg != null ? chg / 100 : null;
        out.amp = amp != null ? amp / 100 : null;
        out.roe = num2(d.f173);                     // ROE 原值（不缩放）
        var mc = num2(d.f116); out.mcap = mc != null ? mc / 1e8 : null;
        out.mainIn = num2(d.f140);                  // 主力净流入（元）
      });

    // ③ F10 主要财务指标（最新报告期）
    var p2 = jsonp('https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_FINANCE_MAINFINADATA' +
      '&columns=SECUCODE,REPORT_DATE,TOTALOPERATEREVE,PARENTNETPROFIT,TOTALOPERATEREVETZ,PARENTNETPROFITTZ,ROEJQ,XSMLL,EPSJB' +
      '&filter=(SECUCODE%3D%22' + code + '.' + suf + '%22)&pageSize=1&sortColumns=REPORT_DATE&sortTypes=-1',
      'callback', 9000).then(function (r) {
        var row = r && r.result && r.result.data && r.result.data[0];
        if (!row) return;
        out.revTZ = num2(row.TOTALOPERATEREVETZ);
        out.profitTZ = num2(row.PARENTNETPROFITTZ);
        var roeQ = num2(row.ROEJQ);
        if (roeQ != null) out.roe = roeQ;
        out.grossMargin = num2(row.XSMLL);
        out.eps = num2(row.EPSJB);
        out.rev = num2(row.TOTALOPERATEREVE);
        var dt = String(row.REPORT_DATE || '').slice(0, 10), mm = dt.slice(5, 7), yy = dt.slice(0, 4);
        var lbl = mm === '03' ? '一季报' : (mm === '06' ? '中报' : (mm === '09' ? '三季报' : (mm === '12' ? '年报' : '')));
        if (yy) out.reportDate = yy + (lbl || ('-' + mm));
      });

    // ④ 行业名 → 板块代码 → ⑤ 板块行情（涨跌幅 + 主力净流入；ulist 不通时用板块日K 兜底算涨跌幅）
    function findBK(name) {
      if (!name) return Promise.resolve(null);
      return jsonp('https://searchapi.eastmoney.com/api/suggest/get?input=' + encodeURIComponent(name) +
        '&type=14&count=8&token=D43BF722C8E33BDC906FB84D85E326E8', 'cb', 7000).then(function (r) {
          var arr = r && r.QuotationCodeTable && r.QuotationCodeTable.Data;
          if (!arr || !arr.length) return null;
          for (var i = 0; i < arr.length; i++) if (arr[i].Classify === 'BK' && arr[i].Name === name) return arr[i].Code;
          for (var j = 0; j < arr.length; j++) if (arr[j].Classify === 'BK' && String(arr[j].SecurityType) === '9') return arr[j].Code;
          return null;
        });
    }
    var p3 = Promise.all([p0, p1]).then(function () {
      if (!out.industry) return null;
      return findBK(out.industry).then(function (bk) {
        // 二级行业名（如「白酒Ⅱ」）查不到板块时，退回一级（如「食品饮料」）
        if (bk || !out.industryTop || out.industryTop === out.industry) return bk;
        return findBK(out.industryTop);
      });
    }).then(function (bk) {
      if (!bk) return null;
      out.boardCode = bk;
      return push2Any('/api/qt/ulist.np/get?secids=90.' + bk + '&fields=f2,f3,f12,f14,f62', 6000).then(function (r) {
        var d = r && r.data && r.data.diff && r.data.diff[0];
        if (!d) return null;
        out.boardCode = d.f12 || bk;
        out.boardName = d.f14 || null;
        var c = num2(d.f3); out.boardChg = c != null ? c / 100 : null;
        out.boardMain = num2(d.f62);
      }).then(function () {
        if (out.boardChg != null) return null;
        return jsonp('https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.' + bk +
          '&fields1=f1&fields2=f51,f53&klt=101&fqt=0&end=20500101&lmt=2', 'cb', 7000).then(function (r) {
            var ks = r && r.data && r.data.klines;
            if (!ks || ks.length < 2) return null;
            var a0 = num2(String(ks[0]).split(',')[1]), b0 = num2(String(ks[1]).split(',')[1]);
            if (a0 && b0) out.boardChg = (b0 / a0 - 1) * 100;
          });
      });
    });

    return Promise.all([p0, p1, p2, p3]).then(function () {
      var ok = !!(out.industry || out.pe != null || out.revTZ != null || out.profitTZ != null || out.roe != null);
      if (!ok) return null;
      out.ts = Date.now();
      fundCache[code] = out;
      return out;
    }).catch(function () { return null; });
  }

  /* 卡片局部刷新：基本面到位后只换 .kl-ai-card，不动 K 线/副图 */
  function patchAiCard(scope, ind) {
    if (!window.TDXIndicator || typeof window.TDXIndicator.aiBrief !== 'function') return;
    var list = (scope || document).querySelectorAll('.kl-ai-card');
    if (!list.length) return;
    var tmp = document.createElement('div');
    tmp.innerHTML = window.TDXIndicator.aiBrief(ind);
    var fresh = tmp.firstChild;
    if (!fresh) return;
    for (var i = 0; i < list.length; i++) if (list[i].parentNode) list[i].parentNode.replaceChild(fresh.cloneNode(true), list[i]);
  }

  /* 渲染时先给「拉取中」占位，异步到位后局部替换；失败则回落到「未取到」文案 */
  function fundPlaceholder(code) {
    var a6 = normAShare(code);
    var c = a6 ? fundCache[a6] : null;
    return c || { pending: true };
  }

  function loadFundFor(scope, code, ind) {
    if (!window.TDXIndicator || typeof window.TDXIndicator.aiBrief !== 'function') return;
    if (!normAShare(code)) return;
    getFundInfo(code).then(function (fu) {
      ind.fund = fu;
      patchAiCard(scope, ind);
    });
  }

  // 用官方换手率校准 K 线量单位（股=1 / 手=100）；对比基准差异悬殊(100倍)，日内错位不影响判定
  function calibFactor(info, lastVol) {
    if (!info || !info.shares || !(info.to > 0) || !(lastVol > 0)) return;
    var r1 = Math.abs(lastVol / info.shares * 100 - info.to);
    var r100 = Math.abs(lastVol * 100 / info.shares * 100 - info.to);
    info.factor = r100 < r1 ? 100 : 1;
  }

  function toPctStr(v, info) {
    if (!info || !info.shares || !(v > 0)) return null;
    return (v * (info.factor || 1) / info.shares * 100).toFixed(2) + '%';
  }

  // 量展示：单位跟随校准结果（腾讯各品种量单位不统一：多为手，科创板等返回股）
  function volStr(v, info) {
    var unit = (info && info.factor === 1) ? '股' : '手';
    return v > 99999 ? (v / 10000).toFixed(1) + '万' + unit : v.toFixed(0) + unit;
  }



  /* ────────────────────────── 数据层 ────────────────────────── */



  var memCache = {};     // code -> {name, kline:[[d,o,c,l,h,v],...]} (kline_cache 格式)

  var cacheJson = null;  // kline_cache.json 整体

  var cachePromise = null;



  function loadCacheJson() {

    if (cachePromise) return cachePromise;

    cachePromise = fetch(CACHE_URL + '?t=' + Date.now())

      .then(function (r) { return r.ok ? r.json() : null; })

      .then(function (j) { cacheJson = (j && j.stocks) ? j.stocks : null; return cacheJson; })

      .catch(function () { return null; });

    return cachePromise;

  }



  // kline_cache.json 行格式：[date, open, close, low, high, volume]

  function fromCache(code) {

    var rec = cacheJson && cacheJson[code];

    if (!rec || !rec.kline || rec.kline.length < 2) return null;

    return {

      name: rec.name || '',

      bars: rec.kline.map(function (a) {

        return { d: String(a[0]), o: num(a[1]), c: num(a[2]), l: num(a[3]), h: num(a[4]), v: num(a[5]) };

      })

    };

  }



  // ── 腾讯K线：**必须走 JSONP**（<script> 不受 CORS 约束）

  //    URL: newfqkline/get?_var=NAME&param=<sym>,<per>,,,<n>,qfq

  //    返回 NAME = {data:{"sh600000":{qfqday|qfqweek|qfqmonth:[d,o,c,h,l,v,...], qt:{"sh600000":[..,名称,..]}}}}

  //    ⚠ 老接口 appstock/app/fqkline/get 已被腾讯 WAF 拦截：返回 501 HTML，

  //      浏览器侧表现为 "No 'Access-Control-Allow-Origin'" —— 不要再用 fetch 调它。

  var JSONP_SEQ = 0;



  function jsonpVar(url, timeoutMs) {

    return new Promise(function (resolve) {

      var cb = 'klq' + (++JSONP_SEQ) + '_' + Math.floor(Math.random() * 1e6);

      var sc = document.createElement('script');

      var done = false;

      var timer = setTimeout(function () { finish(null); }, timeoutMs || 9000);

      function finish(val) {

        if (done) return;

        done = true;

        clearTimeout(timer);

        try { delete window[cb]; } catch (e) { window[cb] = undefined; }

        if (sc.parentNode) sc.parentNode.removeChild(sc);

        resolve(val);

      }

      sc.onerror = function () { finish(null); };

      sc.onload = function () {

        var v = null;

        try { v = window[cb]; } catch (e) { }

        finish(v === undefined ? null : v);

      };

      sc.src = url.replace('__CB__', cb);

      document.head.appendChild(sc);

    });

  }



  function parseTencent(j, sym, per) {

    var node = j && j.data && j.data[sym];

    if (!node) return null;

    var arr = node['qfq' + per] || node[per] ||

              node.qfqday || node.qfqweek || node.qfqmonth ||

              node.day || node.week || node.month;

    if (!arr || arr.length < 2) return null;

    var bars = [];

    // mkline 分钟日期为 '202609301130'（12位无分隔），格式化为 '2026-09-30 11:30'；日/周/月原样
    function minDate(s) {
      s = String(s);
      return (/^\d{12}$/.test(s)) ? s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8) + ' ' + s.slice(8, 10) + ':' + s.slice(10, 12) : s;
    }

    for (var i = 0; i < arr.length; i++) {

      var a = arr[i];

      if (!a || a.length < 5) continue;

      var o = num(a[1]), c = num(a[2]), h = num(a[3]), l = num(a[4]);

      if (!o || !c) continue;

      // 分钟线 d 保留完整时间（'2026-09-30 11:30'）；日/周/月只有日期

      bars.push({ d: minDate(a[0]), o: o, c: c, h: h || Math.max(o, c), l: l || Math.min(o, c), v: num(a[5]) });

    }

    if (bars.length < 2) return null;

    var nm = '';

    try { nm = (node.qt && node.qt[sym] && node.qt[sym][1]) || ''; } catch (e) { }

    return { name: nm, bars: bars };

  }



  function fromTencent(code, period) {

    // 腾讯周期串：day/week/month；分钟线为 m15/m30/m60

    var per = period === 'week' ? 'week' : (period === 'month' ? 'month' :

      (period === '15m' ? 'm15' : (period === '30m' ? 'm30' : (period === '60m' ? 'm60' : 'day'))));

    var s = splitSym(code);

    var sym = s.ex + s.num;

    var n = Math.max(CFG.bars, 80);

    // 日线多取历史用于指标计算（周/月保持原样，避免响应体过大）
    if (per === 'day') n = Math.max(n, CFG.historyBars || 0);

    // 分钟线多取一些，保证副图指标有足够计算深度
    if (isMin(period)) n = Math.max(n, 320);

    // 依次尝试：日线/周/月 → 前复权(主) → 备用域名 → 不复权；分钟线 → mkline 专用端点（无复权概念）
    var urls = isMin(period) ? [
      'https://ifzq.gtimg.cn/appstock/app/kline/mkline?_var=__CB__&param=' + sym + ',' + per + ',,' + n,
      'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline?_var=__CB__&param=' + sym + ',' + per + ',,' + n
    ] : [
      'https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?_var=__CB__&param=' + sym + ',' + per + ',,,' + n + ',qfq',
      'https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?_var=__CB__&param=' + sym + ',' + per + ',,,' + n + ',qfq',
      'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?_var=__CB__&param=' + sym + ',' + per + ',,,' + n
    ];

    var i = 0;

    function step() {

      if (i >= urls.length) return Promise.resolve(null);

      return jsonpVar(urls[i++]).then(function (j) {

        return parseTencent(j, sym, per) || step();

      });

    }

    return step();

  }



  function isMin(p) { return p === '15m' || p === '30m' || p === '60m'; }

  // 东方财富K线（JSONP，跨域无限制）：data.klines = ["date,open,close,high,low,volume,amount",...]

  function fromEastmoneyOne(code, period, base, secid) {

    // klt：101日 102周 103月；分钟线直接用分钟数（15/30/60）

    var klt = period === 'week' ? 102 : (period === 'month' ? 103 :

      (period === '15m' ? 15 : (period === '30m' ? 30 : (period === '60m' ? 60 : 101))));

    var cb = 'kljsonp_' + Math.floor(Math.random() * 1e9);

    var url = base + '/api/qt/stock/kline/get?secid=' + secid +

      '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=' + klt +

      '&fqt=1&end=20500101&lmt=' + Math.max(CFG.bars, 60, (period === 'day' ? (CFG.historyBars || 0) : 0), (isMin(period) ? 320 : 0)) + '&cb=' + cb;

    return new Promise(function (resolve) {

      var timer = setTimeout(function () { cleanup(); resolve(null); }, 7000);

      var sc = document.createElement('script');

      function cleanup() {

        clearTimeout(timer);

        try { delete window[cb]; } catch (e) { window[cb] = undefined; }

        if (sc.parentNode) sc.parentNode.removeChild(sc);

      }

      window[cb] = function (j) {

        cleanup();

        var d = j && j.data;

        if (!d || !d.klines || d.klines.length < 2) { resolve(null); return; }

        var bars = [];

        for (var i = 0; i < d.klines.length; i++) {

          var p = String(d.klines[i]).split(',');

          if (p.length < 6) continue;

          bars.push({ d: p[0], o: num(p[1]), c: num(p[2]), h: num(p[3]), l: num(p[4]), v: num(p[5]) });

        }

        resolve(bars.length >= 2 ? { name: d.name || '', bars: bars } : null);

      };

      sc.src = url;

      sc.onerror = function () { cleanup(); resolve(null); };

      document.head.appendChild(sc);

    });

  }



  function fromEastmoney(code, period) {

    // 多域名 + 多 secid 兜底：东财各域名在部分网络/插件环境下会被阻断

    var secids = [emSecid(code)];

    var bk = /^bk(\d{4,6})$/i.exec(code);

    if (bk && secids[0].indexOf('90.') !== 0) secids.unshift('90.BK' + bk[1]);

    var bases = ['https://push2his.eastmoney.com', 'https://push2.eastmoney.com', 'https://push2new.eastmoney.com'];

    var p = Promise.resolve(null);

    for (var i = 0; i < bases.length; i++) {

      for (var s = 0; s < secids.length; s++) {

        (function (base, sid) {

          p = p.then(function (prev) {

            if (prev) return prev;

            return fromEastmoneyOne(code, period, base, sid);

          });

        })(bases[i], secids[s]);

      }

    }

    return p;

  }



  var ramCache = {};   // code+period -> {name, bars}



    /* ────────────────────── 板块K线（同源缓存 + 日K聚合） ────────────────────── */



  var BOARD_URL = SELF_BASE + 'ai_analysis_board_kline.json';



  // 生成版板块日K缓存（覆盖全部板块，优先生效）



  var BOARD_URL_EXTRA = SELF_BASE + 'ai_board_kline.json';



  var boardPromise = null, boardJson = null;



  function loadBoardCache() {



    if (boardPromise) return boardPromise;



    function grab(u) {



      return fetch(u + '?t=' + Date.now())



        .then(function (r) { return r.ok ? r.json() : null; })



        .then(function (j) { return (j && j.stocks) ? j.stocks : null; })



        .catch(function () { return null; });



    }



    boardPromise = Promise.all([grab(BOARD_URL_EXTRA), grab(BOARD_URL)]).then(function (arr) {



      var extra = arr[0], base = arr[1], merged = {}, k;



      if (base) { for (k in base) { merged[String(k).toLowerCase()] = base[k]; } }



      if (extra) { for (k in extra) { merged[String(k).toLowerCase()] = extra[k]; } }



      boardJson = (base || extra) ? merged : null;



      return boardJson;



    });



    return boardPromise;



  }



  // 板块日K缓存行格式：[date, open, close, low, high, volume]（前复权、指数点位）



  function fromBoardCache(code, period) {



    var rec = null;



    if (boardJson) {



      var _c = String(code);



      rec = boardJson[_c] || boardJson[_c.toLowerCase()] || boardJson[_c.toUpperCase()] || null;



    }



    if (!rec || !rec.kline || rec.kline.length < 2) return null;



    var bars = rec.kline.map(function (a) {



      return { d: String(a[0]), o: num(a[1]), c: num(a[2]), l: num(a[3]), h: num(a[4]), v: num(a[5]) };



    });



    // 板块缓存只有日K：分钟周期直接返回 null，走东财实时源；周/月由日K聚合

    if (isMin(period)) return null;

    if (period !== 'day') bars = aggBars(bars, period);



    return { name: rec.name || '', bars: bars };



  }



  // 日K -> 周/月K 聚合（板块无腾讯周月源，由日线合成）



  function aggBars(bars, period) {



    var out = [], key = '';



    for (var i = 0; i < bars.length; i++) {



      var b = bars[i], k;



      if (period === 'month') {



        k = String(b.d).slice(0, 7);



      } else {



        var dt = new Date(String(b.d).replace(/-/g, '/'));



        var mon = new Date(dt); mon.setDate(dt.getDate() - ((dt.getDay() + 6) % 7));



        k = mon.getFullYear() + '-' + ('0' + (mon.getMonth() + 1)).slice(-2) + '-' + ('0' + mon.getDate()).slice(-2);



      }



      if (k !== key) { out.push({ d: b.d, o: b.o, c: b.c, h: b.h, l: b.l, v: b.v }); key = k; }



      else {



        var t = out[out.length - 1];



        t.c = b.c; t.h = Math.max(t.h, b.h); t.l = Math.min(t.l, b.l); t.v += b.v; t.d = b.d;



      }



    }



    return out;



  }





function getKline(code, period) {

    var key = code + '|' + period;

    if (ramCache[key]) return Promise.resolve(ramCache[key]);

    // 日线优先命中 kline_cache.json（同源、秒开）

    var isBk = /^bk\d{4,6}$/i.test(code);



    var head;



    if (isBk) {



      // 板块：同源缓存(日K)优先；周/月由日K聚合；最后兜底东财(部分网络不可达)



      head = loadBoardCache().then(function () { return fromBoardCache(code, period); });



    } else {



      head = period === 'day'



        ? loadCacheJson().then(function () { return fromCache(code); })



        : Promise.resolve(null);



    }



    return head



      .then(function (hit) {



        if (hit) return hit;



        if (isBk) return fromEastmoney(code, period);



        return fromTencent(code, period).then(function (r) {



          return r || fromEastmoney(code, period);



        });



      })

      .then(function (res) {

        // 统一在这里拆分：bars = 图表显示用；allBars = 指标计算用全量历史。
        // 显示根数沿用取数下限 max(CFG.bars,80)，保持弹窗/嵌入图表观感不变。
        if (res && res.bars && res.bars.length >= 2) {
          if (!res.allBars) {
            res.allBars = res.bars;
            var showN = Math.max(CFG.bars, 80);
            if (res.bars.length > showN) res.bars = res.bars.slice(-showN);
          }
          ramCache[key] = res;
        }

        return res;

      });

  }



  /* ────────────────────────── 绘图 ────────────────────────── */



  var W = 640, H = 330;

  var PL = 6, PR = 58;                 // 左右留白（右侧留给价格刻度）

  var PT = 14, PB = 236;               // 价格区上下边界

  var VT = 254, VB = 314;              // 成交量区上下边界



  function ma(bars, n, i) {

    if (i < n - 1) return null;

    var s = 0;

    for (var k = 0; k < n; k++) s += bars[i - k].c;

    return s / n;

  }



  function buildChart(bars) {

    var n = bars.length;

    var plotW = W - PL - PR;

    var slot = plotW / n;

    var cw = Math.max(1.5, Math.min(9, slot * 0.64));

    var hi = -Infinity, lo = Infinity, vhi = 0, i, b;

    for (i = 0; i < n; i++) {

      b = bars[i];

      if (b.h > hi) hi = b.h;

      if (b.l < lo) lo = b.l;

      if (b.v > vhi) vhi = b.v;

    }

    if (!isFinite(hi) || !isFinite(lo) || hi === lo) return { svg: '' };

    var padP = (hi - lo) * 0.07 || Math.max(0.05, hi * 0.01);

    hi += padP; lo -= padP;

    var pH = PB - PT;

    function py(p) { return PT + (hi - p) / (hi - lo) * pH; }

    function px(j) { return PL + slot * j + slot / 2; }



    var s = '<svg viewBox="0 0 ' + W + ' ' + H + '" xmlns="http://www.w3.org/2000/svg">';

    s += '<rect x="0" y="0" width="' + W + '" height="' + H + '" fill="#0a0f1d"/>';



    // 横向网格 + 右侧价格刻度

    for (var g = 0; g <= 4; g++) {

      var pv = hi - (hi - lo) * g / 4, gy = py(pv);

      s += '<line x1="' + PL + '" y1="' + gy.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + gy.toFixed(1) +

        '" stroke="#1b2440" stroke-width="1"/>';

      s += '<text x="' + (W - PR + 5) + '" y="' + (gy + 3.4).toFixed(1) + '" fill="#6b7494" font-size="10">' +

        pv.toFixed(pv >= 100 ? 1 : 2) + '</text>';

    }

    // 成交量区背景与分隔

    s += '<line x1="' + PL + '" y1="' + (VT - 8) + '" x2="' + (W - PR) + '" y2="' + (VT - 8) +

      '" stroke="#1b2440" stroke-width="1"/>';

    s += '<text x="' + PL + '" y="' + (VT - 12) + '" fill="#4e5878" font-size="9">成交量</text>';



    // K线 + 成交量

    for (i = 0; i < n; i++) {

      b = bars[i];

      var up = b.c >= b.o;

      var col = up ? '#ff5252' : '#00e676';

      var x = px(i);

      var yh = py(b.h), yl = py(b.l), yo = py(b.o), yc = py(b.c);

      s += '<line x1="' + x.toFixed(1) + '" y1="' + yh.toFixed(1) + '" x2="' + x.toFixed(1) + '" y2="' + yl.toFixed(1) +

        '" stroke="' + col + '" stroke-width="1"/>';

      var top = Math.min(yo, yc), hh = Math.max(1, Math.abs(yc - yo));

      s += '<rect x="' + (x - cw / 2).toFixed(1) + '" y="' + top.toFixed(1) + '" width="' + cw.toFixed(1) +

        '" height="' + hh.toFixed(1) + '" fill="' + col + '"/>';

      var vh = vhi ? (b.v / vhi) * (VB - VT) : 0;

      s += '<rect x="' + (x - cw / 2).toFixed(1) + '" y="' + (VB - vh).toFixed(1) + '" width="' + cw.toFixed(1) +

        '" height="' + Math.max(0.6, vh).toFixed(1) + '" fill="' + col + '" opacity="0.45"/>';

      if (i === 0 || i % Math.ceil(n / 6) === 0 || i === n - 1) {

        s += '<text x="' + x.toFixed(1) + '" y="' + (H - 4) + '" fill="#565f80" font-size="9" text-anchor="middle">' +

          String(b.d).slice(5) + '</text>';

      }

    }



    // 均线

    var mas = [{ n: 5, c: '#ffb74d' }, { n: 10, c: '#4dd0e1' }, { n: 20, c: '#b07cf0' }];

    for (var m = 0; m < mas.length; m++) {

      var pts = [];

      for (i = 0; i < n; i++) {

        var v = ma(bars, mas[m].n, i);

        if (v == null) continue;

        pts.push(px(i).toFixed(1) + ',' + py(v).toFixed(1));

      }

      if (pts.length > 1) {

        s += '<polyline points="' + pts.join(' ') + '" fill="none" stroke="' + mas[m].c +

          '" stroke-width="1.3" opacity="0.9"/>';

      }

    }



    // 最新收盘虚线

    var last = bars[n - 1];

    s += '<line x1="' + PL + '" y1="' + py(last.c).toFixed(1) + '" x2="' + (W - PR) + '" y2="' + py(last.c).toFixed(1) +

      '" stroke="#ffb74d" stroke-width="1" stroke-dasharray="3,3" opacity="0.55"/>';



    // 十字光标（默认隐藏）

    s += '<g id="kl-cross" style="display:none">' +

      '<line id="kl-cx" x1="0" y1="' + PT + '" x2="0" y2="' + VB + '" stroke="#7b85a8" stroke-width="1" stroke-dasharray="2,3"/>' +

      '<line id="kl-cy" x1="' + PL + '" y1="0" x2="' + (W - PR) + '" y2="0" stroke="#7b85a8" stroke-width="1" stroke-dasharray="2,3"/>' +

      '</g>';

    s += '</svg>';

    return { svg: s, px: px, py: py, slot: slot, plotW: plotW, W: W, H: H, PL: PL, PR: PR, bars: bars };

  }

  /* 箱体主图叠加：把 ind.box（价格量纲）画成主图上的半透明箱体带 + 顶/底线。
   * 价格→y 用 buildChart 的 py；索引→x 用 chart.px。仅在存在箱体段时输出。 */
  function boxOverlaySVG(chart, ind) {
    if (!chart || !ind) return '';
    var py = chart.py, px = chart.px, W = chart.W, H = chart.H;
    // 主图固定只保留「箱体标识」= ind.box 的箱顶/箱底（黄/绿两态），与标签无关；
    // 九转 / 高顶出货 统一叠加到主图 K 线（用户要求从 3 个副图移出）：
    //   九转 = HHV(H,60)*1.03（箱体操盘WM 的 DeMark 九转 bw.jj）；高顶出货 = HHV(H,60)（ind.sigs['高顶']）
    var band = '', extra = '';
    if (ind.box) {
      var b = ind.box, n = b.top.length;
      for (var i = 0; i < n; i++) {
        if (!b.start[i]) continue;
        var top = b.top[i], bot = b.bot[i];
        if (top == null || bot == null || top <= 0) continue;
        // 箱体终点 = 其后 40 根内最近的 breakHi（突破高点）
        var end = i;
        for (var k = i; k < n && k <= i + 40; k++) { if (b.end[k]) { end = k; break; } }
        var col = (b.hasX && b.hasX[i]) ? '#077807' : '#FFA400';
        var x1 = px(i), x2 = px(end);
        var yT = py(top), yB = py(bot);
        var yTop = Math.min(yT, yB), hgt = Math.max(2, Math.abs(yT - yB));
        band += '<rect x="' + x1.toFixed(1) + '" y="' + yTop.toFixed(1) + '" width="' + Math.max(1, x2 - x1).toFixed(1) + '" height="' + hgt.toFixed(1) + '" fill="' + col + '" opacity="0.10"/>';
        band += '<line x1="' + x1.toFixed(1) + '" y1="' + yT.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + yT.toFixed(1) + '" stroke="' + col + '" stroke-width="2" opacity="0.92"/>';
        band += '<line x1="' + x1.toFixed(1) + '" y1="' + yB.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + yB.toFixed(1) + '" stroke="' + col + '" stroke-width="2" opacity="0.92"/>';
        i = end;
      }
    }
    if (ind.bars && ind.bars.length) {
      var Hb = [], hn = ind.bars.length, top60 = 0;
      for (var hi = Math.max(0, hn - 60); hi < hn; hi++) { Hb.push(ind.bars[hi].h); if (ind.bars[hi].h > top60) top60 = ind.bars[hi].h; }
      if (top60 > 0) {
        var yGd = py(top60), yJz = py(top60 * 1.03);
        // 钱龙买卖点（ZIG(3,5) 转折确认 icUp/icDn）统一画到主图九转位置：圆点，买绿#00E676 / 卖红#FF2D2D（2026-10-03 用户指定：从钱龙副图移出）
        if (ind.ql && ind.ql.icUp) for (var bi2 = 0; bi2 < hn; bi2++) {
          if (ind.ql.icUp[bi2]) extra += '<circle cx="' + px(bi2).toFixed(1) + '" cy="' + (yJz - 9).toFixed(1) + '" r="3.2" fill="#00E676" stroke="#0a0f1d" stroke-width="1"/>';
          if (ind.ql.icDn[bi2]) extra += '<circle cx="' + px(bi2).toFixed(1) + '" cy="' + (yJz - 9).toFixed(1) + '" r="3.2" fill="#FF2D2D" stroke="#0a0f1d" stroke-width="1"/>';
        }
        // 高顶出货（ind.sigs['高顶']，与四合一/箱体操盘WM 同源：PEAKBARS(Cl,0.15)<10）
        if (ind.sigs && ind.sigs['高顶']) for (var gi = 0; gi < hn; gi++) if (ind.sigs['高顶'][gi]) extra += '<text x="' + px(gi).toFixed(1) + '" y="' + yGd.toFixed(1) + '" fill="#00E676" font-size="9" text-anchor="middle" font-weight="700" style="paint-order:stroke;stroke:#0a0f1d;stroke-width:2px">高顶出货</text>';
        // 九转（箱体操盘WM bw.jj：1-9 数字 + ◇）
        if (ind.boxwm && ind.boxwm.jj) for (var ji2 = 0; ji2 < ind.boxwm.jj.length; ji2++) { var jj = ind.boxwm.jj[ji2]; if (jj && jj.i != null) extra += '<text x="' + px(jj.i).toFixed(1) + '" y="' + yJz.toFixed(1) + '" fill="' + (jj.c || '#00E676') + '" font-size="9" text-anchor="middle">' + String(jj.s != null ? jj.s : '') + '</text>'; }
      }
    }
    if (!band && !extra) return '';
    var s = '<svg class="kl-box-ov kl-fml-ov" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;z-index:2">';
    s += band + extra;
    s += '</svg>';
    return s;
  }
  /* 主图叠加注入：先清除所有 .kl-fml-ov，再插入箱体标识 SVG。
   * 主图现在固定只画箱体（不随标签切换叠加缠论/寒梅等公式线）。 */
  function injectFormulaOverlay(chartEl, svg) {
    if (!chartEl) return;
    var all = chartEl.querySelectorAll('.kl-fml-ov');
    for (var i = 0; i < all.length; i++) if (all[i].parentNode) all[i].parentNode.removeChild(all[i]);
    if (svg) chartEl.insertAdjacentHTML('beforeend', svg);
  }
  function injectBoxOverlay(chartEl, chart, ind) {
    injectFormulaOverlay(chartEl, boxOverlaySVG(chart, ind));
  }
  /* 缠论 / 寒梅傲雪 主图叠加（A 页切标签联动；引擎内两公式各自的绘制函数，互不共享）。
   * m 把 buildChart 的坐标映射交给引擎：px/py 为索引/价格→像素，top/bot 为价格区上下界，
   * x0/x1 为绘图区左右界（寒梅箱体带全宽提示条用）。 */
  function formulaOverlayMap(chart) {
    return { px: chart.px, py: chart.py, top: PT, bot: PB, x0: PL, x1: W - PR };
  }
  function fmlOverlaySVG(inner) {
    if (!inner) return '';
    return '<svg class="kl-fml-ov" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;z-index:2">' + inner + '</svg>';
  }
  function chanOverlaySVG(chart, ind) {
    if (!window.TDXIndicator || typeof window.TDXIndicator.renderChanOverlay !== 'function') return '';
    return fmlOverlaySVG(window.TDXIndicator.renderChanOverlay(ind, formulaOverlayMap(chart)));
  }
  function hanmeiOverlaySVG(chart, ind) {
    if (!window.TDXIndicator || typeof window.TDXIndicator.renderHanmeiOverlay !== 'function') return '';
    return fmlOverlaySVG(window.TDXIndicator.renderHanmeiOverlay(ind, formulaOverlayMap(chart)));
  }



  /* ────────────────────────── 弹窗 ────────────────────────── */



  var state = { code: '', name: '', period: 'day', chart: null, data: null, toInfo: null };



  function ensureModal() {

    if (document.getElementById('kl-pop-overlay')) return;

    var ov = document.createElement('div');

    ov.id = 'kl-pop-overlay';

    ov.className = 'kl-pop-overlay';

    ov.addEventListener('click', function (e) { if (e.target === ov) close(); });

    ov.innerHTML = '<div class="kl-pop" id="kl-pop-body" role="dialog" aria-modal="true"></div>';

    document.body.appendChild(ov);

  }



  function close() {

    var ov = document.getElementById('kl-pop-overlay');

    if (ov) ov.classList.remove('show');

  }

  window.closeKlineModal = close;



  function statHtml(label, val, color, sub) {

    return '<div class="kl-stat"><b style="color:' + (color || '#e6e9f2') + '">' + val + '</b><span>' +

      (sub || label) + '</span></div>';

  }



  // 把最新一日的换手率填进顶部指标卡（流通股本异步到达后调用，或渲染完成后立即刷新）
  function updateTurnoverStat() {

    var el = document.getElementById('kl-to-stat');

    var bars = state.data && state.data.bars;

    if (!el || !bars || !bars.length) return;

    if (state.period !== 'day') { el.innerHTML = '<b style="color:#cfd6ea">—</b><span>换手率</span>'; return; }

    var last = bars[bars.length - 1];

    var s = toPctStr(last.v, state.toInfo);

    if (s) el.innerHTML = '<b style="color:#cfd6ea">' + s + '</b><span>换手率·最新日</span>';

  }



  /* ───────── 副图：通达信「箱体操盘 + 四合一」指标 ─────────
   * kline_indicator.js 与本文件同目录；若未加载则按需动态引入一次，
   * 这样全站引用弹窗的页面无需逐个加 <script> 标签。
   */
  var indPromise = null;
  function ensureIndicator() {
    if (window.TDXIndicator) return Promise.resolve(true);
    if (indPromise) return indPromise;
    indPromise = new Promise(function (resolve) {
      var sc = document.createElement('script');
      sc.src = SELF_BASE + 'kline_indicator.js?v=22';
      sc.onload = function () { resolve(!!window.TDXIndicator); };
      sc.onerror = function () { resolve(false); };
      document.head.appendChild(sc);
    });
    return indPromise;
  }
  function renderIndicator() {
    var wrap = document.getElementById('kl-sub-wrap');
    if (!wrap) return;
    if (!state.data || !state.data.bars || state.data.bars.length < 2) { wrap.innerHTML = ''; return; }
    ensureIndicator().then(function (ok) {
      if (!ok || !window.TDXIndicator) {
        wrap.innerHTML = '<div class="kl-note">指标引擎未加载（kline_indicator.js）</div>';
        return;
      }
      try {
        var all = (state.data.allBars && state.data.allBars.length >= 2) ? state.data.allBars : state.data.bars;
        var ind = window.TDXIndicator.compute(all, state.toInfo);
        // ZIG 类函数需长历史计算，但只显示主图那一段，保证逐根对齐
        if (window.TDXIndicator.trim && state.data.bars && all.length > state.data.bars.length) {
          ind = window.TDXIndicator.trim(ind, state.data.bars.length);
        }
        // B（全站弹窗）= A 的阉割版：只保留「当前这批表格」，永远屏蔽后续新增表格
        // AI 简评：行业/业绩两段异步补，先用「拉取中」占位（技术面立刻出）
        ind.fund = fundPlaceholder(state.code);
        wrap.innerHTML = window.TDXIndicator.render(ind, { tables: 'current' });
        injectBoxOverlay(document.getElementById('kl-chart-wrap'), state.chart, ind);
        loadFundFor(wrap, state.code, ind);
      } catch (e) {
        wrap.innerHTML = '<div class="kl-note">副图计算异常：' + (e && e.message ? e.message : e) + '</div>';
      }
    });
  }


  /* 补拉长历史供指标计算：主图已用短序列画好，这里只补 allBars 并刷新副图。
   * 通过与主图末根日期对齐裁剪，保证副图与主图逐根对应（缓存可能滞后一天）。 */
  function fetchHistoryForIndicator(code, period) {
    fromTencent(code, period).then(function (deep) {
      if (!deep || !deep.bars || deep.bars.length < 2) return;
      if (state.code !== code || state.period !== period) return;   // 用户已切走
      var cur = state.data;
      if (!cur || !cur.bars || !cur.bars.length) return;
      var lastD = String(cur.bars[cur.bars.length - 1].d);
      var idx = -1;
      for (var i = deep.bars.length - 1; i >= 0; i--) {
        if (String(deep.bars[i].d) === lastD) { idx = i; break; }
      }
      if (idx < 0) return;                                          // 对不上就不动，宁可不换
      cur.allBars = deep.bars.slice(0, idx + 1);
      if (cur.allBars.length > cur.bars.length) renderIndicator();
    }).catch(function () { });
  }

  function render() {

    var body = document.getElementById('kl-pop-body');

    if (!body) return;

    var bars = state.data && state.data.bars;

    var name = state.name || (state.data && state.data.name) || state.code;



    if (!bars || bars.length < 2) {

      body.innerHTML = headHtml(name) +

        '<div class="kl-err">暂无K线数据<div class="kl-errsub">可能是新股 / 已停牌 / 数据源临时不可用<br>也可点下方「东方财富行情」直接查看</div></div>' + footHtml();

      bind();

      return;

    }



    var n = bars.length, last = bars[n - 1], first = bars[0];

    var prev = bars[n - 2];

    var chg = prev && prev.c ? (last.c - prev.c) / prev.c * 100 : 0;

    var chg5 = bars.length > 6 ? (last.c - bars[n - 6].c) / bars[n - 6].c * 100 : NaN;

    var chg20 = bars.length > 21 ? (last.c - bars[n - 21].c) / bars[n - 21].c * 100 : NaN;

    var hi = -Infinity, lo = Infinity;

    for (var i = 0; i < n; i++) { if (bars[i].h > hi) hi = bars[i].h; if (bars[i].l < lo) lo = bars[i].l; }

    var upCol = '#ff5252', downCol = '#00e676';

    var cCol = chg >= 0 ? upCol : downCol;



    var html = headHtml(name);

    html += '<div class="kl-stats">' +

      statHtml('最新收盘', last.c.toFixed(2), cCol, last.d) +

      statHtml('日涨跌', pct(chg), chg >= 0 ? upCol : downCol) +

      statHtml('近5日', pct(chg5), chg5 >= 0 ? upCol : downCol) +

      statHtml('近20日', pct(chg20), chg20 >= 0 ? upCol : downCol) +

      statHtml('区间高/低', hi.toFixed(2) + ' / ' + lo.toFixed(2), '#cfd6ea', '近' + n + '根') +

      (isAShare(hex6(state.code)) ? '<div class="kl-stat" id="kl-to-stat"><b style="color:#cfd6ea">—</b><span>换手率</span></div>' : '') +

      '</div>';

    html += '<div class="kl-tabs">' +

      tab('day', '日K') + tab('week', '周K') + tab('month', '月K') +
      tab('15m', '15分') + tab('30m', '30分') + tab('60m', '60分') +

      '<span style="margin-left:auto;font-size:.72em;color:#6b7494;align-self:center">' +

      (state.period === 'day' ? '日线·前复权' : (state.period === 'week' ? '周线·前复权' :
        (state.period === 'month' ? '月线·前复权' : (state.period === '15m' ? '15分钟·前复权' :
          (state.period === '30m' ? '30分钟·前复权' : '60分钟·前复权'))))) +

      '</span></div>';

    html += '<div class="kl-chart" id="kl-chart-wrap">' +

      (state.chart ? state.chart.svg : '') +

      '<div class="kl-tip" id="kl-tipbox"></div></div>';
    // 主图下方：通达信「箱体操盘 + 四合一副图」独立面板（由 kline_indicator.js 渲染）
    html += '<div class="kl-sub-label">箱体操盘 · 四合一副图（MACD/量比/换手率/RSI + 箱体/买卖点）</div>';
    html += '<div class="kl-sub" id="kl-sub-wrap"></div>';

    var mv = function (k) { return ma(bars, k, n - 1); };

    html += '<div class="kl-legend">' +

      '<em><i style="background:#ffb74d"></i>MA5 ' + (mv(5) ? mv(5).toFixed(2) : '—') + '</em>' +

      '<em><i style="background:#4dd0e1"></i>MA10 ' + (mv(10) ? mv(10).toFixed(2) : '—') + '</em>' +

      '<em><i style="background:#b07cf0"></i>MA20 ' + (mv(20) ? mv(20).toFixed(2) : '—') + '</em>' +

      '<em><i style="background:#ff5252"></i>红涨</em><em><i style="background:#00e676"></i>绿跌</em>' +

      '</div>';

    var perLabel = state.period === 'day' ? '日K·前复权'

      : (state.period === 'week' ? '周K·前复权' :
        (state.period === 'month' ? '月K·前复权' :
          (state.period === '15m' ? '15分钟K·前复权' :
            (state.period === '30m' ? '30分钟K·前复权' : '60分钟K·前复权'))));

    html += '<div class="kl-note">蜡烛=' + perLabel +

      ' · 橙线=MA5 · 虚线=最新收盘 · 悬停查看单根开高低收/换手率</div>';

    html += footHtml();

    body.innerHTML = html;

    bind();

    updateTurnoverStat();

    renderIndicator();


    // 钩子：渲染完毕后通知调用方，让调用方可以注入侧栏 / 额外数据；未定义此函数则完全无影响（保持向后兼容）。
    if (typeof window.__KLINE_POPUP_RENDERED__ === 'function') {
      try { window.__KLINE_POPUP_RENDERED__(body, state.code, state.name); }
      catch (e) { window.console && window.console.warn && window.console.warn('[kline_popup] rendered hook:', e); }
    }
  }



  function headHtml(name) {

    return '<div class="kl-head"><h3>' + name + '</h3>' +

      '<span class="kl-code">' + hex6(state.code) + '</span>' +

      '<span class="kl-live">K线弹窗</span>' +

      '<button class="kl-fsbtn" title="全屏" onclick="klToggleFullscreen(this)">⛶</button>' +

      '<button class="kl-close" title="关闭" onclick="closeKlineModal()">✕</button></div>';

  }



  // 加载态（保留日/周/月切换，避免切换时闪一下「暂无数据」）

  function loading(label) {

    var body = document.getElementById('kl-pop-body');

    if (!body) return;

    body.innerHTML = headHtml(state.name || state.code) +

      '<div class="kl-tabs">' + tab('day', '日K') + tab('week', '周K') + tab('month', '月K') +
      tab('15m', '15分') + tab('30m', '30分') + tab('60m', '60分') + '</div>' +

      '<div class="kl-loading"><div class="kl-spin"></div>' + (label || '正在获取K线…') + '</div>' + footHtml();

    bind();

  }

  function tab(p, label) {

    return '<button class="kl-tab' + (state.period === p ? ' on' : '') + '" data-kl-period="' + p + '">' + label + '</button>';

  }

  function footHtml() {

    return '<div class="kl-foot">' +

      '<a class="kl-btn" href="' + emUrl(state.code) + '" target="_blank" rel="noopener">东方财富行情 ↗</a>' +

      '<button class="kl-btn ghost" onclick="closeKlineModal()">关闭</button>' +

      '<span class="kl-warn">红涨绿跌（A股习惯）<br>仅供研究，不构成投资建议</span></div>';

  }



  function bind() {

    var body = document.getElementById('kl-pop-body');

    if (!body) return;

    var tabs = body.querySelectorAll('[data-kl-period]');

    for (var i = 0; i < tabs.length; i++) {

      tabs[i].addEventListener('click', function () {

        var p = this.getAttribute('data-kl-period');

        if (p === state.period) return;

        state.period = p;

        state.chart = null;

        state.data = null;

        loading('正在获取' + (p === 'day' ? '日K' : (p === 'week' ? '周K' : '月K')) + '…');

        loadAndRender();

      });

    }

    bindCrosshair();

  }



  function bindCrosshair() {

    var wrap = document.getElementById('kl-chart-wrap');

    var tip = document.getElementById('kl-tipbox');

    if (!wrap || !tip || !state.chart || !state.chart.svg) return;

    var svg = wrap.querySelector('svg');

    var cross = svg && svg.querySelector('#kl-cross');

    var cx = svg && svg.querySelector('#kl-cx');

    var cy = svg && svg.querySelector('#kl-cy');

    if (!svg || !cross || !cx || !cy || !state.chart.slot) return;

    var bars = state.chart.bars;

    var n = bars.length;



    function hide() { tip.style.display = 'none'; cross.style.display = 'none'; }

    wrap.addEventListener('mouseleave', hide);

    wrap.addEventListener('mousemove', function (e) {

      var r = svg.getBoundingClientRect();

      if (!r.width) return;

      var x = (e.clientX - r.left) / r.width * W;      // 还原到 viewBox 坐标

      var y = (e.clientY - r.top) / r.height * H;

      var idx = Math.floor((x - PL) / state.chart.slot);

      if (idx < 0) idx = 0; if (idx > n - 1) idx = n - 1;

      var b = bars[idx];

      if (!b) { hide(); return; }

      cross.style.display = '';

      cx.setAttribute('x1', state.chart.px(idx).toFixed(1));

      cx.setAttribute('x2', state.chart.px(idx).toFixed(1));

      cy.setAttribute('y1', y.toFixed(1));

      cy.setAttribute('y2', y.toFixed(1));



      var pc = idx > 0 && bars[idx - 1].c ? (b.c - bars[idx - 1].c) / bars[idx - 1].c * 100 : 0;

      var col = pc >= 0 ? '#ff5252' : '#00e676';

      tip.innerHTML =

        '<b>' + b.d + '</b><br>' +

        '<i>开</i>' + b.o.toFixed(2) + '　<i>高</i>' + b.h.toFixed(2) + '<br>' +

        '<i>低</i>' + b.l.toFixed(2) + '　<i>收</i><b style="color:' + col + '">' + b.c.toFixed(2) + '</b><br>' +

        '<i>涨跌</i><b style="color:' + col + '">' + pct(pc) + '</b><br>' +

        '<i>量</i>' + volStr(b.v, state.toInfo) +

        (toPctStr(b.v, state.toInfo) ? '　<i>换手</i>' + toPctStr(b.v, state.toInfo) : '');

      tip.style.display = 'block';

      var px = e.clientX - r.left, py = e.clientY - r.top;

      var tw = tip.offsetWidth, th = tip.offsetHeight;

      tip.style.left = Math.min(Math.max(6, px + 14), Math.max(6, r.width - tw - 6)) + 'px';

      tip.style.top = Math.min(Math.max(6, py - th - 10), Math.max(6, r.height - th - 6)) + 'px';

    });

  }



  function loadAndRender() {

    var code = state.code, period = state.period;

    getKline(code, period).then(function (res) {

      if (code !== state.code || period !== state.period) return;   // 期间用户切了别的

      state.data = res;

      state.chart = res && res.bars && res.bars.length >= 2 ? buildChart(res.bars) : null;

      render();

      // 日线历史不足（例如命中只存 30 根的 kline_cache.json）：后台补拉长历史专供指标计算。
      // 主图仍用即时可得的短序列先画出来，不阻塞；长历史到达后只刷新副图。
      if (res && period === 'day' && res.bars && res.bars.length >= 2 &&
        res.bars.length < (CFG.historyBars || 0)) {
        fetchHistoryForIndicator(code, period);
      }

      // 异步取流通股本（换手率）：到达后校准量单位并刷新指标卡与浮层
      if (res && res.bars && res.bars.length >= 2 && isAShare(hex6(code))) {
        getTurnoverInfo(code).then(function (info) {
          if (state.code !== code) return;
          if (info) {
            calibFactor(info, res.bars[res.bars.length - 1].v);
            state.toInfo = info;
            updateTurnoverStat();
            renderIndicator();   // 流通股本到达后刷新副图（换手率依赖它）
          }
        });
      }

    });

  }



  function open(code, name) {

    code = String(code || '').trim();

    if (!isAShare(code) && !isSym(code)) return false;      // 美股 / 非A股：不做K线弹窗

    ensureModal();

    state.code = code;

    state.name = name || '';

    state.period = 'day';

    state.data = null;

    state.chart = null;

    state.toInfo = null;

    var ov = document.getElementById('kl-pop-overlay');

    ov.classList.add('show');

    // 每次打开都回到常规尺寸（避免上次的全屏状态残留）
    ov.classList.remove('kl-fs');

    var pnl = document.getElementById('kl-pop-body');

    if (pnl) pnl.classList.remove('kl-fs');

    loading();

    loadAndRender();

    return true;

  }

  window.openKlineModal = open;

  window.showKline = open;

  /* ── 全站统一入口路由 ──────────────────────────────────────────────
   * A股（6 位 / sh|sz|bj 前缀）、东财板块（BK）→ 本弹窗；
   * 其它代码（gb_dji / hf_ / int_ / b_ / hk 等全球市场、期货、外汇）→ 旧弹窗兜底
   * （markets 的全球市场表数据源不同，属「特殊要求的K线」）。
   * 盗火线走 openKlineModal，不受此处影响。
   */
  window.openKline = function (code, name) {
    var c = String(code == null ? '' : code).trim();
    if (/^\d{6}$/.test(c) || /^(sh|sz|bj)\d{6}$/i.test(c) || /^bk\d{4,6}$/i.test(c)) { open(c, name); return true; }
    if (typeof window.__KGP_OPEN__ === 'function') { window.__KGP_OPEN__(c, name); return true; }
    return false;
  };

  window.KlinePopup = { open: window.openKline, openModal: open, isSupported: function (code) { var c = String(code == null ? '' : code).trim(); return /^\d{6}$/.test(c) || /^(sh|sz|bj)\d{6}$/i.test(c) || /^bk\d{4,6}$/i.test(c); } };

  /* 面板全屏切换：弹窗（.kl-pop）与 /k 内联面板（[data-kl-panel]）共用 */
  window.klToggleFullscreen = function (btn) {
    var panel = (btn && btn.closest) ? btn.closest('.kl-pop') : null;
    if (panel) {
      var on = panel.classList.toggle('kl-fs');
      var ov = document.getElementById('kl-pop-overlay');
      if (ov) ov.classList.toggle('kl-fs', on);
      if (btn) { btn.textContent = on ? '⤡' : '⛶'; btn.title = on ? '还原' : '全屏'; }
      return on;
    }
    var host = (btn && btn.closest) ? btn.closest('[data-kl-panel]') : null;
    if (host) {
      var on2 = host.classList.toggle('kl-fs');
      if (btn) { btn.textContent = on2 ? '⤡' : '⛶'; btn.title = on2 ? '还原' : '全屏'; }
      return on2;
    }
    return false;
  };



  /* ───────────────── 嵌入模式：把 K 线直接渲染进调用方指定的容器 ───────────────── */

  // 用法：renderKlineInto(containerEl, code, name)

  //   复用本文件的四级兜底取数 + buildChart 手绘，但不开独立弹窗，

  //   适合把 K 线嵌到其它页面已有的弹窗里（如升速排行的个股弹窗）。

  window.renderKlineInto = function (container, code, name) {

    code = String(code || '').trim();

    if (!container) return;

    if (!isAShare(code) && !isSym(code)) {

      container.innerHTML = '<div class="empty">非A股代码，无K线数据</div>';

      return;

    }

    container.innerHTML = '<div class="kl-loading"><div class="kl-spin"></div>加载K线…</div>';

    getKline(code, 'day').then(function (res) {

      if (!res || !res.bars || res.bars.length < 2) {

        container.innerHTML = '<div class="empty">暂无K线数据（新股 / 停牌 / 数据源暂不可达）</div>';

        return;

      }

      var chart = buildChart(res.bars);

      // 换手率：异步取流通股本，到达后挂到 chart 上（十字光标闭包每次读取，无需重渲染）
      if (isAShare(hex6(code))) {
        getTurnoverInfo(code).then(function (info) {
          if (info) { calibFactor(info, res.bars[res.bars.length - 1].v); chart.toInfo = info; }
        });
      }

      container.innerHTML = '<div class="kl-chart" style="margin:6px 0 0">' + chart.svg +

        '<div class="kl-tip" data-tip></div></div>';

      bindEmbedCrosshair(container, chart);

    });

  };



  /* ───────── 页面内联面板：主图 + 通达信副图（供 /k 独立页调用） ─────────
   * 与弹窗同一条取数链路（含 420 根历史 + 缓存补拉），但不开弹窗，上下结构直接铺在容器里。
   * 返回 Promise<boolean>。
   */
  window.renderKlinePanelInto = function (container, code, opts) {

    opts = opts || {};

    var period = (opts.period === 'week' || opts.period === 'month' || isMin(opts.period)) ? opts.period : 'day';

    code = String(code || '').trim();

    if (!container) return Promise.resolve(false);

    if (!isAShare(code) && !isSym(code) && !/^bk\d{4,6}$/i.test(code)) {
      container.innerHTML = '<div class="empty">代码无法识别（A股 6 位数字 / sh600000 / BK 板块代码）</div>';
      return Promise.resolve(false);
    }

    container.innerHTML = '<div class="kl-loading"><div class="kl-spin"></div>加载K线…</div>';

    return getKline(code, period).then(function (res) {

      if (!res || !res.bars || res.bars.length < 2) {
        container.innerHTML = '<div class="empty">暂无K线数据（新股 / 停牌 / 数据源暂不可达）</div>';
        return false;
      }

      var nm = (res.name || opts.name || '').trim();

      // 显示根数：本页可传 displayBars（默认用 CFG.bars，下限 80）；all = 指标计算用的全量历史
      var all = (res.allBars && res.allBars.length >= 2) ? res.allBars : res.bars;

      var showN = Math.max(80, Math.min(opts.displayBars || CFG.bars, all.length));

      var bars = all.slice(-showN);

      var chart = buildChart(bars);

      function setSub(html) { var w = container.querySelector('[data-sub]'); if (w) w.innerHTML = html; }

      var pTitle = period === 'day' ? '日线' : (period === 'week' ? '周线' :
        (period === 'month' ? '月线' : (period === '15m' ? '15分钟' :
          (period === '30m' ? '30分钟' : '60分钟'))));

      container.setAttribute('data-kl-panel', '1');

      container.innerHTML =
        '<div class="kl-embed-title">' + (nm ? escapeHtml(nm) + ' ' : '') + '<span>' + escapeHtml(code) + '</span><em>' + pTitle + ' · ' + bars.length + '根</em>' +
        '<button class="kl-fsbtn" title="全屏" onclick="klToggleFullscreen(this)" style="margin-left:auto">⛶ 全屏</button></div>' +
        '<div class="kl-chart" id="kl-embed-chart">' + chart.svg + '<div class="kl-tip" data-tip></div></div>' +
        '<div class="kl-sub-label" data-sub-label>箱体操盘 · 四合一副图（MACD/量比/换手率/RSI）</div>' +
        '<div class="kl-sub" data-sub></div>';

      bindEmbedCrosshair(container, chart);

      // ── 标签联动图表（仅 A 内联面板）：副图+表格整体标签化（引擎 renderTabbedSub，
      //    纯 CSS 切换 Tab1 四合一 / Tab2 箱体操盘WM / Tab3 寒梅傲雪 的 pane+表格）。
      //    切换只改「K线下方的副图区」；主图固定只保留箱体标识（箱顶/箱底），不叠加任何公式线。
      var curTab = 0, curInd = null;
      var SUB_LABELS = [
        '箱体操盘 · 四合一副图（MACD/量比/换手率/RSI）',
        '缠论 · 笔线/中枢/买卖点/圆弧/止盈/★擒妖量拉升',
        '寒梅傲雪 · 忘川/腾龙/伏虎 + 潮汐RSI'
      ];
      function applyOverlay(t) {
        if (!curInd) return;
        var el = container.querySelector('#kl-embed-chart');
        // 主图固定只画箱体标识；切换标签只换下方副图区，主图不叠加公式线
        try { injectBoxOverlay(el, chart, curInd); } catch (e) { /* 叠加失败不阻塞主图 */ }
        var lb = container.querySelector('[data-sub-label]');
        if (lb && SUB_LABELS[t]) lb.textContent = SUB_LABELS[t];
      }
      // 周期切换会重建面板内容，但 container 监听器只挂一次；经 container 转发到最新闭包
      container.__klApplyView = applyOverlay;
      if (!container.getAttribute('data-kl-tabwired')) {
        container.setAttribute('data-kl-tabwired', '1');
        container.addEventListener('change', function (e) {
          var t = e.target;
          if (!t || t.type !== 'radio' || String(t.name || '').indexOf('klt') !== 0) return;
          var idx = parseInt(String(t.id).split('_').pop(), 10) - 1;
          if (idx >= 0 && typeof container.__klApplyView === 'function') container.__klApplyView(idx);
        });
      }

      function paintSub() {
        if (!window.TDXIndicator) return;
        try {
          var ind = window.TDXIndicator.compute(all, chart.toInfo, nm);
          if (window.TDXIndicator.trim && all.length > bars.length) {
            ind = window.TDXIndicator.trim(ind, bars.length);
          }
          curInd = ind;
          // AI 简评：行业/业绩两段异步补（技术面立刻出，到位后只刷新卡片）
          ind.fund = fundPlaceholder(code);
          if (typeof window.TDXIndicator.renderTabbedSub === 'function' && opts.tables !== 'current') {
            // A（/k 独立页等内联面板）= 全量版：副图+表格整体标签化（四合一/缠论/寒梅傲雪/钱龙风警线）
            setSub(window.TDXIndicator.renderTabbedSub(ind));
          } else {
            setSub(window.TDXIndicator.render(ind, { tables: opts.tables === 'current' ? 'current' : 'all' }));
          }
          loadFundFor(container, code, ind);
          if (typeof container.__klApplyView === 'function') container.__klApplyView(curTab);
        } catch (e) {
          setSub('<div class="kl-note">副图计算异常：' + (e && e.message ? e.message : e) + '</div>');
        }
      }

      ensureIndicator().then(paintSub);

      // 流通股本（换手率）异步到达后重画副图，并挂到 chart 上供十字光标读取
      if (isAShare(hex6(code))) {
        getTurnoverInfo(code).then(function (info) {
          if (info) {
            calibFactor(info, bars[bars.length - 1].v);
            chart.toInfo = info;
            paintSub();
          }
        });
      }

      // 日线历史不足（命中缓存）时补拉长历史，只刷副图
      if (period === 'day' && all.length < (CFG.historyBars || 0)) {
        fromTencent(code, period).then(function (deep) {
          if (!deep || !deep.bars || deep.bars.length < 2) return;
          var lastD = String(bars[bars.length - 1].d), idx = -1;
          for (var i = deep.bars.length - 1; i >= 0; i--) {
            if (String(deep.bars[i].d) === lastD) { idx = i; break; }
          }
          if (idx < 0) return;
          all = deep.bars.slice(0, idx + 1);
          res.allBars = all;
          if (all.length > bars.length) paintSub();
        }).catch(function () { });
      }

      return true;

    });

  };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return c === '&' ? '&amp;' : c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&quot;';
    });
  }



  // 通用：把任意 bars（[{d,o,h,l,c,v}, ...]，d=YYYY-MM-DD）按本弹窗同款样式渲染进指定容器。

  // 供全球指数/贵金属弹窗（kline_global_popup.js）复用：东财拉数据 + 本文件手绘 SVG。

  window.renderKlineBarsInto = function (container, bars, period) {

    if (!container) return false;

    if (!bars || bars.length < 2) {

      container.innerHTML = '<div class="empty">暂无K线数据</div>';

      return false;

    }

    var bs = (period && period !== 'day') ? aggBars(bars, period) : bars;

    if (!bs || bs.length < 2) {

      container.innerHTML = '<div class="empty">该周期数据不足</div>';

      return false;

    }

    var chart = buildChart(bs);

    container.innerHTML = '<div class="kl-chart" style="margin:6px 0 0">' + chart.svg +

      '<div class="kl-tip" data-tip></div></div>';

    bindEmbedCrosshair(container, chart);

    return true;

  };



  // 嵌入容器的十字光标（作用域限定在 container 内，避免与独立弹窗的全局 id 冲突）

  function bindEmbedCrosshair(container, chart) {

    var wrap = container.querySelector('.kl-chart');

    var tip = container.querySelector('[data-tip]');

    var svg = wrap && wrap.querySelector('svg');

    var cross = svg && svg.querySelector('#kl-cross');

    var cx = svg && svg.querySelector('#kl-cx');

    var cy = svg && svg.querySelector('#kl-cy');

    if (!svg || !cross || !cx || !cy || !chart.slot) return;

    var bars = chart.bars, n = bars.length;

    function hide() { if (tip) tip.style.display = 'none'; cross.style.display = 'none'; }

    wrap.addEventListener('mouseleave', hide);

    wrap.addEventListener('mousemove', function (e) {

      var r = svg.getBoundingClientRect();

      if (!r.width) return;

      var x = (e.clientX - r.left) / r.width * W;

      var idx = Math.floor((x - PL) / chart.slot);

      if (idx < 0) idx = 0; if (idx > n - 1) idx = n - 1;

      var b = bars[idx];

      if (!b) { hide(); return; }

      cross.style.display = '';

      cx.setAttribute('x1', chart.px(idx).toFixed(1));

      cx.setAttribute('x2', chart.px(idx).toFixed(1));

      var my = (e.clientY - r.top) / r.height * H;

      cy.setAttribute('y1', my.toFixed(1));

      cy.setAttribute('y2', my.toFixed(1));

      var pc = idx > 0 && bars[idx - 1].c ? (b.c - bars[idx - 1].c) / bars[idx - 1].c * 100 : 0;

      var col = pc >= 0 ? '#ff5252' : '#00e676';

      if (tip) {

        tip.innerHTML = '<b>' + b.d + '</b><br>' +

          '<i>开</i>' + b.o.toFixed(2) + '　<i>高</i>' + b.h.toFixed(2) + '<br>' +

          '<i>低</i>' + b.l.toFixed(2) + '　<i>收</i><b style="color:' + col + '">' + b.c.toFixed(2) + '</b><br>' +

          '<i>涨跌</i><b style="color:' + col + '">' + pct(pc) + '</b><br>' +

          '<i>量</i>' + volStr(b.v, chart.toInfo) +

          (toPctStr(b.v, chart.toInfo) ? '　<i>换手</i>' + toPctStr(b.v, chart.toInfo) : '');

        tip.style.display = 'block';

        var px = e.clientX - r.left, py = e.clientY - r.top, tw = tip.offsetWidth, th = tip.offsetHeight;

        tip.style.left = Math.min(Math.max(6, px + 14), Math.max(6, r.width - tw - 6)) + 'px';

        tip.style.top = Math.min(Math.max(6, py - th - 10), Math.max(6, r.height - th - 6)) + 'px';

      }

    });

  }



  /* ────────────────────────── 取值与事件委托 ────────────────────────── */



  function matches(el, sels) {

    if (!el || el.nodeType !== 1) return null;

    for (var i = 0; i < sels.length; i++) {

      if (el.matches && el.matches(sels[i])) return el;

    }

    return null;

  }

  function closestAny(el, sels) {

    var node = el;

    while (node && node.nodeType === 1) {

      var hit = matches(node, sels);

      if (hit) return hit;

      node = node.parentElement;

    }

    return null;

  }

  function isExcluded(el) {

    return !!closestAny(el, CFG.excludeSelectors);

  }



  function pickCode(row, clicked) {

    // 优先：显式完整符号（指数 / ETF 等），命中即用，避免与个股 6 位代码冲突

    var node = clicked || row;

    while (node && node.nodeType === 1) {

      var sa = node.getAttribute && node.getAttribute('data-kline-sym');

      if (sa && isSym(sa)) return sa;

      if (node === row) break;

      node = node.parentElement;

    }

    var dc = row.getAttribute('data-kline-code') || row.getAttribute('data-code') || '';

    if (isAShare(dc)) return dc;

    var holder = row.querySelector('.picks-code, .stock-code, td:first-child');

    if (holder) {

      var m = (holder.textContent || '').match(/(\d{6})/);

      if (m) return m[1];

    }

    var a = (clicked && clicked.closest && clicked.closest('a[href]')) || row.querySelector('a[href]');

    if (a) {

      var m2 = (a.getAttribute('href') || '').match(/(\d{6})/);

      if (m2) return m2[1];

    }

    var m3 = (row.textContent || '').match(/(\d{6})/);

    return m3 ? m3[1] : '';

  }



  function pickName(row, code) {

    var el = row.querySelector('.stock-name, .picks-code, td:nth-child(2)');

    var t = el ? (el.textContent || '') : '';

    t = t.replace(code, '').replace(/[+\-]?\d+(\.\d+)?%/g, '').trim();

    return t;

  }



  function onClick(e) {

    if (e.defaultPrevented) return;

    if (e.button !== 0 && e.button !== undefined) return;

    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;   // 允许新窗口打开

    var trig = closestAny(e.target, CFG.triggerSelectors);

    if (!trig) return;

    if (isExcluded(trig)) return;

    var row = closestAny(trig, CFG.rowSelectors) || trig.parentElement;

    if (!row) return;

    var code = pickCode(row, trig);

    if (!isAShare(code) && !isSym(code)) return; // 关键：非6位数字/非完整符号（含美股）直接放行原链接

    e.preventDefault();

    e.stopPropagation();

    // 名称兜底：慢热板块标签 / 指数卡片等没有 .stock-name 列时，优先用 data-kline-name

    var name = pickName(row, code);

    if (!name) {

      var nm = (trig.getAttribute && trig.getAttribute('data-kline-name')) || '';

      if (!nm && trig.closest) {

        var p = trig.closest('[data-kline-name]');

        if (p) nm = p.getAttribute('data-kline-name') || '';

      }

      if (nm) name = nm;

    }

    open(code, name);

  }



  // 让可点击的行有 hover 反馈 + 提示（不依赖重复渲染）

  function decorate() {

    var rows = document.querySelectorAll(CFG.rowSelectors.join(','));

    for (var i = 0; i < rows.length; i++) {

      var r = rows[i];

      if (isExcluded(r)) { r.classList.remove('kl-clickable'); continue; }

      if (!/^\d{6}$/.test(pickCode(r, null))) continue;

      if (!r.classList.contains('kl-clickable')) r.classList.add('kl-clickable');

      r.setAttribute('data-kline-hint', '1');

    }

  }



  function boot() {

    injectStyle();

    ensureModal();

    document.addEventListener('click', onClick, true);

    document.addEventListener('keydown', function (e) {

      if (e.key === 'Escape' || e.keyCode === 27) close();

    });

    // 提示文案（hover 时补 title，避免重复渲染时反复写 DOM）

    document.addEventListener('mouseover', function (e) {

      var trig = closestAny(e.target, CFG.triggerSelectors);

      if (!trig || isExcluded(trig)) return;

      var row = closestAny(trig, CFG.rowSelectors);

      if (!row) return;

      if (!row.getAttribute('data-kl-titled')) {

        row.setAttribute('data-kl-titled', '1');

        if (!row.getAttribute('title')) row.setAttribute('title', CFG.hint);

      }

    });

    // 数据是异步渲染的：低频巡检给新出现的行加可点击标记

    decorate();

    setInterval(decorate, 4000);

    window.addEventListener('load', decorate);

  }



  if (document.readyState === 'loading') {

    document.addEventListener('DOMContentLoaded', boot);

  } else {

    boot();

  }

})();

