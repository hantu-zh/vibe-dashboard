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
 *  2) 数据三级兜底：kline_cache.json → 腾讯前复权K线 → 东方财富K线(JSONP)；
 *  3) 红涨绿跌（A股习惯）、MA5/MA10/MA20、成交量、十字光标悬浮读数；
 *  4) 支持 日K / 周K / 月K 切换，同一次会话内缓存结果。
 */
(function () {
  'use strict';
  if (window.__KLINE_POPUP_READY__) return;
  window.__KLINE_POPUP_READY__ = true;

  /* ────────────────────────── 配置 ────────────────────────── */

  var CFG = Object.assign({
    // 「一行股票」的容器（点击后据此取代码/名称）
    rowSelectors: ['.picks-row', '.stock-item', 'tr[data-kline-code]', '[data-kline-row]'],
    // 行内「可以被点击」的元素；命中后才打开弹窗，避免误伤同页其它表格
    triggerSelectors: [
      'a.picks-link', '.picks-code',
      'a.stock-name', 'a.stock-code', '.stock-code',
      'a[href*="quote.eastmoney.com"]',
      'a[href*="finance.sina.com.cn/realstock"]'
    ],
    // 明确不处理的区域（美股面板等）
    excludeSelectors: [
      '#us-stock-list', '.us-stock-panel', '.us-stock-container',
      '.us-table-wrap', '.us-table', '.us-picks-header', '[data-no-kline]'
    ],
    bars: 60,          // 默认取最近多少个交易日
    hint: '点击查看K线'
  }, window.KLINE_CONFIG || {});

  var CACHE_URL = 'kline_cache.json';

  /* ────────────────────────── 样式 ────────────────────────── */

  var CSS = [
    '.kl-pop-overlay{display:none;position:fixed;inset:0;z-index:99999;background:rgba(3,6,14,.74);',
    '-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px);align-items:center;justify-content:center;padding:14px}',
    '.kl-pop-overlay.show{display:flex}',
    '.kl-pop{background:linear-gradient(165deg,#141b30 0%,#0b1020 100%);border:1px solid #27314f;border-radius:14px;',
    'width:100%;max-width:660px;max-height:90vh;overflow-y:auto;color:#e6e9f2;box-shadow:0 26px 64px rgba(0,0,0,.62);',
    'animation:klIn .16s ease-out}',
    '@keyframes klIn{from{transform:translateY(12px) scale(.985);opacity:0}to{transform:none;opacity:1}}',
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
    '@media(max-width:560px){.kl-stat{min-width:62px;padding:4px 8px}.kl-tip{font-size:.68em}}'
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

  // 交易所前缀（腾讯用）
  function txPrefix(code) {
    if (/^(4|8|92)/.test(code)) return 'bj';
    return code.charAt(0) === '6' ? 'sh' : 'sz';
  }
  // 东方财富 secid
  function emSecid(code) {
    if (/^(4|8|92)/.test(code)) return '0.' + code;
    return (code.charAt(0) === '6' ? '1.' : '0.') + code;
  }
  function emUrl(code) {
    if (/^(4|8|92)/.test(code)) return 'https://quote.eastmoney.com/bj/' + code + '.html';
    return 'https://quote.eastmoney.com/' + (code.charAt(0) === '6' ? 'sh' : 'sz') + code + '.html';
  }
  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : 0; }
  function cls(v) { return v > 0 ? 'kl-up' : (v < 0 ? 'kl-down' : ''); }
  function pct(v, nd) {
    if (!isFinite(v)) return '—';
    return (v > 0 ? '+' : '') + v.toFixed(nd == null ? 2 : nd) + '%';
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

  // 腾讯前复权K线：data["<mk><code>"]["qfqday"|"day"] = [date,open,close,high,low,volume]
  function fromTencent(code, period) {
    var per = period === 'week' ? 'week' : (period === 'month' ? 'month' : 'day');
    var sym = txPrefix(code) + code;
    var url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=' +
      sym + ',' + per + ',,,' + Math.max(CFG.bars, 60) + ',qfq';
    return fetch(url, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        var node = j && j.data && j.data[sym];
        if (!node) return null;
        // 腾讯不同周期/是否复权的返回键名不同：qfqday / qfqweek / qfqmonth / day / week / month
        var arr = node.qfqday || node.qfqweek || node.qfqmonth ||
                  node[per] || node.day || node.week || node.month;
        if (!arr || arr.length < 2) return null;
        var bars = [];
        for (var i = 0; i < arr.length; i++) {
          var a = arr[i];
          var o = num(a[1]), c = num(a[2]), h = num(a[3]), l = num(a[4]);
          if (!o || !c) continue;
          bars.push({ d: String(a[0]).split(' ')[0], o: o, c: c, h: h || Math.max(o, c), l: l || Math.min(o, c), v: num(a[5]) });
        }
        if (bars.length < 2) return null;
        var nm = '';
        try { nm = (node.qt && node.qt[sym] && node.qt[sym][1]) || ''; } catch (e) { }
        return { name: nm, bars: bars };
      })
      .catch(function () { return null; });
  }

  // 东方财富K线（JSONP，跨域无限制）：data.klines = ["date,open,close,high,low,volume,amount",...]
  function fromEastmoney(code, period) {
    var klt = period === 'week' ? 102 : (period === 'month' ? 103 : 101);
    var cb = 'kljsonp_' + Math.floor(Math.random() * 1e9);
    var url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=' + emSecid(code) +
      '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=' + klt +
      '&fqt=1&end=20500101&lmt=' + Math.max(CFG.bars, 60) + '&cb=' + cb;
    return new Promise(function (resolve) {
      var timer = setTimeout(function () { cleanup(); resolve(null); }, 9000);
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
      var sc = document.createElement('script');
      sc.src = url;
      sc.onerror = function () { cleanup(); resolve(null); };
      document.head.appendChild(sc);
    });
  }

  var ramCache = {};   // code+period -> {name, bars}

  function getKline(code, period) {
    var key = code + '|' + period;
    if (ramCache[key]) return Promise.resolve(ramCache[key]);
    // 日线优先命中 kline_cache.json（同源、秒开）
    var head = period === 'day'
      ? loadCacheJson().then(function () { return fromCache(code); })
      : Promise.resolve(null);
    return head
      .then(function (hit) {
        if (hit) return hit;
        return fromTencent(code, period).then(function (r) {
          return r || fromEastmoney(code, period);
        });
      })
      .then(function (res) {
        if (res && res.bars && res.bars.length >= 2) ramCache[key] = res;
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
    return { svg: s, px: px, slot: slot, plotW: plotW, bars: bars };
  }

  /* ────────────────────────── 弹窗 ────────────────────────── */

  var state = { code: '', name: '', period: 'day', chart: null, data: null };

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
      '</div>';
    html += '<div class="kl-tabs">' +
      tab('day', '日K') + tab('week', '周K') + tab('month', '月K') +
      '<span style="margin-left:auto;font-size:.72em;color:#6b7494;align-self:center">' +
      (state.period === 'day' ? '日线·前复权' : (state.period === 'week' ? '周线·前复权' : '月线·前复权')) +
      '</span></div>';
    html += '<div class="kl-chart" id="kl-chart-wrap">' +
      (state.chart ? state.chart.svg : '') +
      '<div class="kl-tip" id="kl-tipbox"></div></div>';
    var mv = function (k) { return ma(bars, k, n - 1); };
    html += '<div class="kl-legend">' +
      '<em><i style="background:#ffb74d"></i>MA5 ' + (mv(5) ? mv(5).toFixed(2) : '—') + '</em>' +
      '<em><i style="background:#4dd0e1"></i>MA10 ' + (mv(10) ? mv(10).toFixed(2) : '—') + '</em>' +
      '<em><i style="background:#b07cf0"></i>MA20 ' + (mv(20) ? mv(20).toFixed(2) : '—') + '</em>' +
      '<em><i style="background:#ff5252"></i>红涨</em><em><i style="background:#00e676"></i>绿跌</em>' +
      '</div>';
    var perLabel = state.period === 'day' ? '日K·前复权'
      : (state.period === 'week' ? '周K·前复权' : '月K·前复权');
    html += '<div class="kl-note">蜡烛=' + perLabel +
      ' · 橙线=MA5 · 虚线=最新收盘 · 悬停查看单根开高低收</div>';
    html += footHtml();
    body.innerHTML = html;
    bind();
  }

  function headHtml(name) {
    return '<div class="kl-head"><h3>' + name + '</h3>' +
      '<span class="kl-code">' + hex6(state.code) + '</span>' +
      '<span class="kl-live">K线弹窗</span>' +
      '<button class="kl-close" title="关闭" onclick="closeKlineModal()">✕</button></div>';
  }

  // 加载态（保留日/周/月切换，避免切换时闪一下「暂无数据」）
  function loading(label) {
    var body = document.getElementById('kl-pop-body');
    if (!body) return;
    body.innerHTML = headHtml(state.name || state.code) +
      '<div class="kl-tabs">' + tab('day', '日K') + tab('week', '周K') + tab('month', '月K') + '</div>' +
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
        '<i>量</i>' + (b.v > 99999 ? (b.v / 10000).toFixed(1) + '万手' : b.v.toFixed(0) + '手');
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
    });
  }

  function open(code, name) {
    code = String(code || '').trim();
    if (!isAShare(code)) return false;      // 美股 / 非A股：不做K线弹窗
    ensureModal();
    state.code = code;
    state.name = name || '';
    state.period = 'day';
    state.data = null;
    state.chart = null;
    var ov = document.getElementById('kl-pop-overlay');
    ov.classList.add('show');
    loading();
    loadAndRender();
    return true;
  }
  window.openKlineModal = open;
  window.showKline = open;

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
    if (!isAShare(code)) return;                 // 关键：非6位数字（含美股）直接放行原链接
    e.preventDefault();
    e.stopPropagation();
    open(code, pickName(row, code));
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
