/**
 * kline_global_popup.js — 全球市场/贵金属汇率 K 线弹窗
 * 数据源：东方财富 / 新浪财经（多源回退，JSONP）
 */
(function () {
  'use strict';

  var STYLE_ID = 'kgp-style';
  var OVERLAY_ID = 'kgp-overlay';

  // 每个 code 对应的 K 线数据源（按优先级）
  var KLINE_SOURCES = {
    'gb_dji': [{ type: 'sina_us', symbol: '.DJI' }],
    'gb_ixic': [{ type: 'sina_us', symbol: '.IXIC' }, { type: 'sina_us', symbol: '.NDX' }],
    'gb_inx': [{ type: 'sina_us', symbol: '.INX' }],
    'hf_CHA50CFD': [{ type: 'sina_gi', symbol: 'FTXIN9' }],
    'int_ftse': [{ type: 'sina_gi', symbol: 'FTSE' }],
    'b_DAX': [{ type: 'sina_gi', symbol: 'DAX' }],
    'b_CAC': [{ type: 'sina_gi', symbol: 'CAC' }],
    'int_nikkei': [{ type: 'sina_gi', symbol: 'NKY' }],
    'hkHSI': [{ type: 'sina_futures', symbol: 'HSI' }],
    'b_KOSPI': [{ type: 'sina_gi', symbol: 'KOSPI' }],
    'b_AS51': [{ type: 'sina_gi', symbol: 'AS51' }],
    'b_SENSEX': [{ type: 'sina_gi', symbol: 'SENSEX' }],
    'b_TWSE': [{ type: 'em', secid: '100.TWII' }, { type: 'em', secid: '100.TWSE' }],
    'DINIW': [{ type: 'sina_forex', symbol: 'DINIW' }],
    'hf_XAU': [{ type: 'sina_futures', symbol: 'XAU' }, { type: 'sina_futures', symbol: 'GC' }],
    'hf_XAG': [{ type: 'sina_futures', symbol: 'XAG' }, { type: 'sina_futures', symbol: 'SI' }],
    'hf_XAU_icbc': [{ type: 'sina_futures', symbol: 'XAU' }, { type: 'sina_futures', symbol: 'GC' }],
    'hf_XAG_ccb': [{ type: 'sina_futures', symbol: 'XAG' }, { type: 'sina_futures', symbol: 'SI' }],
    'fx_susdcny': [{ type: 'sina_forex', symbol: 'USDCNY' }, { type: 'sina_forex', symbol: 'USDCNH' }],
    'fx_susdjpy': [{ type: 'sina_forex', symbol: 'USDJPY' }],
    'fx_susdeur': [{ type: 'sina_forex', symbol: 'EURUSD' }],
    'fx_susdgbp': [{ type: 'sina_forex', symbol: 'GBPUSD' }],
    'fx_susdaud': [{ type: 'sina_forex', symbol: 'AUDUSD' }],
    'fx_susdnzd': [{ type: 'sina_forex', symbol: 'NZDUSD' }],
    'fx_susdhkd': [{ type: 'sina_forex', symbol: 'USDHKD' }],
    'fx_susdchf': [{ type: 'sina_forex', symbol: 'USDCHF' }],
    'fx_susdcad': [{ type: 'sina_forex', symbol: 'USDCAD' }],
    'fx_susdrub': [{ type: 'sina_forex', symbol: 'USDRUB' }]
  };

  // 东财 K 线 secid（与行情 EM_SECIDS 一致；东财在你网络下可用，优先于新浪）
  var EM_KLINE = {
    'gb_dji': ['100.DJI'],
    'gb_ixic': ['100.IXIC'],
    'gb_inx': ['100.INX'],
    'hf_CHA50CFD': ['100.XIN9'],
    'int_ftse': ['100.FTSE'],
    'b_DAX': ['100.DAX'],
    'b_CAC': ['100.CAC'],
    'int_nikkei': ['100.N225'],
    'hkHSI': ['100.HSI'],
    'b_KOSPI': ['100.KS11'],
    'b_AS51': ['100.AS51'],
    'b_SENSEX': ['100.SENSEX'],
    'b_TWSE': ['100.TWII'],
    'DINIW': ['100.UDI'],
    'hf_XAU': ['122.XAU'],
    'hf_XAG': ['122.XAG'],
    'hf_XAU_icbc': ['122.XAU'],
    'hf_XAG_ccb': ['122.XAG'],
    'fx_susdcny': ['119.USDCNY', '119.USDCNH'],
    'fx_susdjpy': ['119.USDJPY'],
    'fx_susdeur': ['119.USDEUR'],
    'fx_susdgbp': ['119.USDGBP'],
    'fx_susdaud': ['119.USDAUD'],
    'fx_susdnzd': ['119.USDNZD'],
    'fx_susdhkd': ['119.USDHKD'],
    'fx_susdchf': ['119.USDCHF'],
    'fx_susdcad': ['119.USDCAD'],
    'fx_susdrub': ['119.USDRUB']
  };

  // 汇率品种 -> Frankfurter(ECB) 符号（与 news.html 的 erCode 一致）
  // 这些品种无东财/新浪 K 线，改用 ECB 每日历史画「汇率走势」
  var FX_ER = {
    'fx_susdcny': 'CNY',
    'fx_susdjpy': 'JPY',
    'fx_susdeur': 'EUR',
    'fx_susdgbp': 'GBP',
    'fx_susdaud': 'AUD',
    'fx_susdnzd': 'NZD',
    'fx_susdhkd': 'HKD',
    'fx_susdchf': 'CHF',
    'fx_susdcad': 'CAD',
    'fx_susdrub': 'RUB'
  };

  var TODAY_STR = (function () {
    var d = new Date();
    return d.getFullYear() + '_' + (d.getMonth() + 1) + '_' + d.getDate();
  })();

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var css = [
      '#kgp-overlay{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.85);backdrop-filter:blur(4px);z-index:9999;display:none;align-items:center;justify-content:center;padding:16px;font-family:"Noto Sans SC",sans-serif}',
      '#kgp-overlay.show{display:flex}',
      '#kgp-box{background:#0f0f19;border:1px solid rgba(255,45,106,.4);border-radius:12px;width:100%;max-width:900px;max-height:90vh;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.6);display:flex;flex-direction:column}',
      '#kgp-head{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;background:rgba(255,255,255,.04);border-bottom:1px solid rgba(255,255,255,.08)}',
      '#kgp-title{color:#fff;font-size:1.1rem;font-weight:700;margin:0}',
      '#kgp-title small{font-size:.7rem;color:rgba(255,255,255,.5);margin-left:8px;font-weight:400}',
      '#kgp-close{background:transparent;border:none;color:rgba(255,255,255,.6);font-size:1.4rem;cursor:pointer;line-height:1;padding:0 4px}',
      '#kgp-close:hover{color:#fff}',
      '#kgp-body{position:relative;flex:1;min-height:360px;padding:16px;display:flex;align-items:center;justify-content:center;background:#0a0a12}',
      '#kgp-chart{width:100%;height:100%;min-height:340px}',
      '#kgp-info{display:flex;gap:16px;padding:10px 18px;background:rgba(255,255,255,.03);font-size:.78rem;color:rgba(255,255,255,.65);border-top:1px solid rgba(255,255,255,.06);flex-wrap:wrap}',
      '#kgp-info span{white-space:nowrap}',
      '#kgp-info .up{color:#ff2d2d}',
      '#kgp-info .down{color:#39ff14}',
      '#kgp-loading{color:rgba(255,255,255,.6);font-size:.9rem}',
      '#kgp-empty{color:rgba(255,255,255,.5);text-align:center;padding:40px}',
      '@media(max-width:640px){#kgp-box{max-height:96vh}#kgp-chart{min-height:260px}}'
    ].join('\n');
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = css;
    document.head.appendChild(s);
  }

  function ensureModal() {
    if (document.getElementById(OVERLAY_ID)) return;
    var ov = document.createElement('div');
    ov.id = OVERLAY_ID;
    ov.innerHTML = '<div id="kgp-box">' +
      '<div id="kgp-head"><h3 id="kgp-title">K线 <small></small></h3><button id="kgp-close">✕</button></div>' +
      '<div id="kgp-body"><div id="kgp-loading">正在加载 K 线…</div></div>' +
      '<div id="kgp-info"></div>' +
      '</div>';
    document.body.appendChild(ov);
    ov.addEventListener('click', function (e) { if (e.target === ov) closeModal(); });
    document.getElementById('kgp-close').addEventListener('click', closeModal);
  }

  function closeModal() {
    var ov = document.getElementById(OVERLAY_ID);
    if (ov) ov.classList.remove('show');
  }
  window.closeKlineModal = closeModal;

  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : NaN; }

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  // ---------- JSONP helpers ----------
  function jsonpCallback(url, cbName, timeout) {
    return new Promise(function (resolve) {
      var script = document.createElement('script');
      var timer = setTimeout(function () { cleanup(); resolve(null); }, timeout || 9000);
      window[cbName] = function (data) {
        clearTimeout(timer);
        cleanup();
        resolve(data);
      };
      function cleanup() {
        try { delete window[cbName]; } catch (e) {}
        if (script && script.parentNode) script.parentNode.removeChild(script);
      }
      script.onerror = function () { clearTimeout(timer); cleanup(); resolve(null); };
      script.src = url;
      document.head.appendChild(script);
    });
  }

  function jsonpVar(url, varName, timeout) {
    return new Promise(function (resolve) {
      var script = document.createElement('script');
      var timer = setTimeout(function () { cleanup(); resolve(null); }, timeout || 9000);
      function cleanup() {
        var val = null;
        try { val = window[varName]; delete window[varName]; } catch (e) {}
        if (script && script.parentNode) script.parentNode.removeChild(script);
        return val;
      }
      script.onload = function () { clearTimeout(timer); resolve(cleanup()); };
      script.onerror = function () { clearTimeout(timer); resolve(cleanup()); };
      script.src = url;
      document.head.appendChild(script);
    });
  }

  // ---------- parsers ----------
  function parseEmKlines(j) {
    try {
      var d = j && j.data;
      var klines = d && d.klines;
      if (!klines || !klines.length) return null;
      return klines.map(function (line) {
        var p = String(line).split(',');
        return { date: p[0], open: num(p[1]), close: num(p[2]), high: num(p[3]), low: num(p[4]), vol: num(p[5]) };
      }).filter(function (x) { return isFinite(x.open + x.close + x.high + x.low); });
    } catch (e) { return null; }
  }

  function parseSinaUSArr(arr) {
    if (!arr || !arr.length) return null;
    return arr.map(function (it) {
      return { date: it.d, open: num(it.o), high: num(it.h), low: num(it.l), close: num(it.c), vol: num(it.v) };
    }).filter(function (x) { return isFinite(x.open + x.close + x.high + x.low); });
  }

  function parseSinaGi(j) {
    try {
      var arr = j && j.result && j.result.data;
      if (!arr || !arr.length) return null;
      return arr.map(function (it) {
        return { date: it.d, open: num(it.o), high: num(it.h), low: num(it.l), close: num(it.c), vol: num(it.v) };
      }).filter(function (x) { return isFinite(x.open + x.close + x.high + x.low); });
    } catch (e) { return null; }
  }

  function parseSinaFuturesArr(arr) {
    if (!arr || !arr.length) return null;
    return arr.map(function (it) {
      return { date: it.date, open: num(it.open), high: num(it.high), low: num(it.low), close: num(it.close), vol: num(it.volume) };
    }).filter(function (x) { return isFinite(x.open + x.close + x.high + x.low); });
  }

  function parseSinaForexStr(str) {
    if (!str || typeof str !== 'string') return null;
    var days = str.split('|');
    var out = [];
    for (var i = 0; i < days.length; i++) {
      var p = days[i].split(',');
      if (p.length < 5 || !p[0]) continue;
      var o = num(p[1]), h = num(p[2]), l = num(p[3]), c = num(p[4]);
      if (isFinite(o + h + l + c)) out.push({ date: p[0], open: o, high: h, low: l, close: c, vol: NaN });
    }
    return out.length ? out : null;
  }

  // ---------- fetchers ----------
  var EM_KLINE_HOSTS = [
    'https://push2his.eastmoney.com/api/qt/stock/kline/get',
    'https://push2.eastmoney.com/api/qt/stock/kline/get'
  ];

  function fetchEmOne(secid, host) {
    var cb = '__kgp_em_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    var url = host + '?secid=' + encodeURIComponent(secid) +
      '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57' +
      '&klt=101&fqt=0&end=20500101&lmt=20&_=' + Date.now() + '&cb=' + cb;
    return jsonpCallback(url, cb).then(parseEmKlines);
  }

  function fetchEm(secid) {
    function next(i) {
      if (i >= EM_KLINE_HOSTS.length) return Promise.resolve(null);
      return fetchEmOne(secid, EM_KLINE_HOSTS[i]).then(function (bars) {
        return bars || next(i + 1);
      });
    }
    return next(0);
  }

  function fetchSinaUS(symbol) {
    var v = 'kgpus_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    var url = 'https://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20' + v + '=/US_MinKService.getDailyK?symbol=' + encodeURIComponent(symbol) + '&datalen=120';
    return jsonpVar(url, v).then(function (arr) {
      return parseSinaUSArr(arr);
    });
  }

  function fetchSinaGi(symbol) {
    var cb = 'kgpgi_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    var url = 'https://gi.finance.sina.com.cn/hq/daily?symbol=' + encodeURIComponent(symbol) + '&num=120&callback=' + cb;
    return jsonpCallback(url, cb).then(parseSinaGi);
  }

  function fetchSinaFutures(symbol) {
    var v = 'kgpf_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    var url = 'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20' + v + '=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=' + encodeURIComponent(symbol) + '&_=' + TODAY_STR + '&source=web';
    return jsonpVar(url, v).then(function (arr) {
      return parseSinaFuturesArr(arr);
    });
  }

  function fetchSinaForex(symbol) {
    var v = 'kgpfx_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    var url = 'https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var_' + v + '=/NewForexService.getDayKLine?symbol=' + encodeURIComponent(symbol) + '&_=' + TODAY_STR;
    return jsonpVar(url, v).then(function (s) {
      return parseSinaForexStr(s);
    });
  }

  function fetchSource(spec) {
    if (spec.type === 'em') return fetchEm(spec.secid);
    if (spec.type === 'sina_us') return fetchSinaUS(spec.symbol);
    if (spec.type === 'sina_gi') return fetchSinaGi(spec.symbol);
    if (spec.type === 'sina_futures') return fetchSinaFutures(spec.symbol);
    if (spec.type === 'sina_forex') return fetchSinaForex(spec.symbol);
    return Promise.resolve(null);
  }

  function getSources(code) {
    var list = [];
    // 东财优先（与你已验证可用的行情同源）
    var em = EM_KLINE[code];
    if (em) em.forEach(function (s) { list.push({ type: 'em', secid: s }); });
    // 新浪兜底
    var sina = KLINE_SOURCES[code] ? KLINE_SOURCES[code].slice() : [];
    if (!list.length && !sina.length) {
      list.push({ type: 'em', secid: '100.' + code.replace(/^[^_]+_/, '').toUpperCase() });
    }
    return list.concat(sina);
  }

  function fetchKline(code) {
    var specs = getSources(code);
    function next(i) {
      if (i >= specs.length) return Promise.resolve(null);
      return fetchSource(specs[i]).then(function (bars) { return bars || next(i + 1); });
    }
    return next(0);
  }

  function ma(bars, n, idx) {
    var sum = 0, c = 0;
    for (var i = idx - n + 1; i <= idx; i++) {
      if (i >= 0) { sum += bars[i].close; c++; }
    }
    return c ? sum / c : NaN;
  }

  function renderChart(bars, name) {
    var body = document.getElementById('kgp-body');
    if (!body) return;
    if (!bars || bars.length < 2) {
      body.innerHTML = '<div id="kgp-empty">暂无 K 线数据</div>';
      document.getElementById('kgp-info').innerHTML = '';
      return;
    }
    var W = 1200, H = 400, M = { t: 18, r: 52, b: 30, l: 58 };
    var cw = W - M.l - M.r, ch = H - M.t - M.b;
    var lo = Infinity, hi = -Infinity;
    bars.forEach(function (b) {
      lo = Math.min(lo, b.low);
      hi = Math.max(hi, b.high);
    });
    var pad = (hi - lo) * 0.06 || hi * 0.02;
    lo -= pad; hi += pad;
    if (lo <= 0 && bars[0].close > 0) lo = Math.max(lo, hi * 0.0001);

    function px(i) { return M.l + (i / (bars.length - 1)) * cw; }
    function py(p) { return M.t + (hi - p) / (hi - lo) * ch; }

    var last = bars[bars.length - 1];
    var prev = bars[bars.length - 2];
    var change = last.close - prev.close;
    var changePct = prev.close ? change / prev.close * 100 : 0;
    var volSum = bars.reduce(function (s, b) { return s + (b.vol || 0); }, 0);
    var avgVol = volSum / bars.length;

    var svgParts = [];
    svgParts.push('<svg id="kgp-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">');

    for (var g = 0; g <= 4; g++) {
      var y = M.t + (ch / 4) * g;
      var price = hi - (hi - lo) * (g / 4);
      svgParts.push('<line x1="' + M.l + '" y1="' + y + '" x2="' + (W - M.r) + '" y2="' + y + '" stroke="rgba(255,255,255,.08)" stroke-dasharray="2,2"/>');
      svgParts.push('<text x="' + (W - M.r + 6) + '" y="' + (y + 4) + '" fill="rgba(255,255,255,.5)" font-size="11">' + price.toFixed(2) + '</text>');
    }
    for (g = 0; g < bars.length; g += Math.ceil(bars.length / 6)) {
      var x = px(g);
      svgParts.push('<text x="' + x + '" y="' + (H - 8) + '" fill="rgba(255,255,255,.4)" font-size="10" text-anchor="middle">' + bars[g].date.slice(5) + '</text>');
    }

    var slot = cw / (bars.length - 1 || 1);
    var wickW = 2, bodyW = Math.max(7, slot * 0.6);
    bars.forEach(function (b, i) {
      var x = px(i);
      var up = b.close >= b.open;
      var color = up ? '#ff2d2d' : '#39ff14'; // 中红绿：涨红跌绿
      var yTop = py(Math.max(b.open, b.close));
      var yBot = py(Math.min(b.open, b.close));
      var yHigh = py(b.high);
      var yLow = py(b.low);
      svgParts.push('<line x1="' + x + '" y1="' + yHigh + '" x2="' + x + '" y2="' + yLow + '" stroke="' + color + '" stroke-width="' + wickW + '"/>');
      svgParts.push('<rect x="' + (x - bodyW / 2) + '" y="' + yTop + '" width="' + bodyW + '" height="' + Math.max(1, yBot - yTop) + '" fill="' + color + '" rx="1"/>');
    });

    [5, 10].forEach(function (n, idx) {
      var pts = [];
      for (var i = 0; i < bars.length; i++) {
        var v = ma(bars, n, i);
        if (isFinite(v)) pts.push(px(i) + ',' + py(v));
      }
      if (pts.length > 1) {
        var color = idx === 0 ? '#00fff2' : '#b829ff';
        svgParts.push('<polyline points="' + pts.join(' ') + '" fill="none" stroke="' + color + '" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>');
      }
    });

    svgParts.push('</svg>');
    body.innerHTML = svgParts.join('');

    var cls = change >= 0 ? 'up' : 'down';
    var info = '<span>最新: <b class="' + cls + '">' + last.close.toFixed(2) + '</b></span>' +
      '<span>涨跌: <b class="' + cls + '">' + (change >= 0 ? '+' : '') + change.toFixed(2) + ' (' + changePct.toFixed(2) + '%)</b></span>' +
      '<span>最高: ' + last.high.toFixed(2) + '</span>' +
      '<span>最低: ' + last.low.toFixed(2) + '</span>' +
      '<span>成交量均值: ' + (avgVol >= 1e8 ? (avgVol / 1e8).toFixed(2) + '亿' : (avgVol / 1e4).toFixed(2) + '万') + '</span>' +
      '<span style="margin-left:auto;color:rgba(255,255,255,.35)">MA5 青 · MA10 紫</span>';
    document.getElementById('kgp-info').innerHTML = info;
  }

  // ---------- 汇率走势（Frankfurter / ECB 每日历史，CORS 开放） ----------
  function fetchFrankfurter(sym) {
    var end = new Date();
    var start = new Date();
    start.setDate(end.getDate() - 60);
    function fmt(d) {
      var m = d.getMonth() + 1, day = d.getDate();
      return d.getFullYear() + '-' + (m < 10 ? '0' + m : m) + '-' + (day < 10 ? '0' + day : day);
    }
    var url = 'https://api.frankfurter.dev/v1/' + fmt(start) + '..' + fmt(end) + '?base=USD&symbols=' + encodeURIComponent(sym);
    return new Promise(function (resolve) {
      var done = false;
      var timer = setTimeout(function () { if (!done) { done = true; resolve(null); } }, 9000);
      fetch(url).then(function (r) { return r && r.ok ? r.json() : null; }).then(function (j) {
        if (done) return;
        clearTimeout(timer); done = true;
        if (!j || !j.rates) return resolve(null);
        var dates = Object.keys(j.rates).sort();
        var pts = [];
        dates.forEach(function (d) {
          var v = num(j.rates[d][sym]);
          if (isFinite(v)) pts.push({ date: d, value: v });
        });
        resolve(pts.length >= 2 ? pts : null);
      }).catch(function () { if (!done) { clearTimeout(timer); done = true; resolve(null); } });
    });
  }

  function renderLineChart(pts, name) {
    var body = document.getElementById('kgp-body');
    if (!body) return;
    if (!pts || pts.length < 2) {
      body.innerHTML = '<div id="kgp-empty">暂无历史走势数据</div>';
      document.getElementById('kgp-info').innerHTML = '';
      return;
    }
    var W = 1200, H = 400, M = { t: 18, r: 52, b: 30, l: 64 };
    var cw = W - M.l - M.r, ch = H - M.t - M.b;
    var lo = Infinity, hi = -Infinity;
    pts.forEach(function (p) { lo = Math.min(lo, p.value); hi = Math.max(hi, p.value); });
    var pad = (hi - lo) * 0.14 || hi * 0.02;
    lo -= pad; hi += pad;

    function px(i) { return M.l + (i / (pts.length - 1)) * cw; }
    function py(v) { return M.t + (hi - v) / (hi - lo) * ch; }

    var first = pts[0].value, last = pts[pts.length - 1].value;
    var change = last - first;
    var changePct = first ? change / first * 100 : 0;
    var avg = pts.reduce(function (s, p) { return s + p.value; }, 0) / pts.length;
    var maxV = -Infinity, minV = Infinity;
    pts.forEach(function (p) { maxV = Math.max(maxV, p.value); minV = Math.min(minV, p.value); });
    var up = change >= 0;
    var lineColor = up ? '#ff2d2d' : '#39ff14';

    var svgParts = [];
    svgParts.push('<svg id="kgp-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">');
    svgParts.push('<defs><linearGradient id="kgpFill" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + lineColor + '" stop-opacity="0.32"/>' +
      '<stop offset="100%" stop-color="' + lineColor + '" stop-opacity="0"/></linearGradient></defs>');

    for (var g = 0; g <= 4; g++) {
      var y = M.t + (ch / 4) * g;
      var price = hi - (hi - lo) * (g / 4);
      svgParts.push('<line x1="' + M.l + '" y1="' + y + '" x2="' + (W - M.r) + '" y2="' + y + '" stroke="rgba(255,255,255,.08)" stroke-dasharray="2,2"/>');
      svgParts.push('<text x="' + (W - M.r + 6) + '" y="' + (y + 4) + '" fill="rgba(255,255,255,.5)" font-size="11">' + price.toFixed(4) + '</text>');
    }
    var stepLbl = Math.max(1, Math.floor(pts.length / 6));
    for (var k = 0; k < pts.length; k += stepLbl) {
      var x = px(k);
      svgParts.push('<text x="' + x + '" y="' + (H - 8) + '" fill="rgba(255,255,255,.4)" font-size="10" text-anchor="middle">' + pts[k].date.slice(5) + '</text>');
    }

    var linePts = [];
    for (var i = 0; i < pts.length; i++) linePts.push(px(i) + ',' + py(pts[i].value));
    var areaD = 'M' + px(0) + ',' + py(lo) + ' L' + linePts.join(' L') + ' L' + px(pts.length - 1) + ',' + py(lo) + ' Z';
    svgParts.push('<path d="' + areaD + '" fill="url(#kgpFill)" stroke="none"/>');
    svgParts.push('<polyline points="' + linePts.join(' ') + '" fill="none" stroke="' + lineColor + '" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>');
    svgParts.push('<circle cx="' + px(pts.length - 1) + '" cy="' + py(last) + '" r="3.6" fill="' + lineColor + '"/>');
    svgParts.push('</svg>');
    body.innerHTML = svgParts.join('');

    var cls = up ? 'up' : 'down';
    var info = '<span>最新: <b class="' + cls + '">' + last.toFixed(4) + '</b></span>' +
      '<span>区间涨跌: <b class="' + cls + '">' + (up ? '+' : '') + change.toFixed(4) + ' (' + changePct.toFixed(2) + '%)</b></span>' +
      '<span>区间最高: ' + maxV.toFixed(4) + '</span>' +
      '<span>区间最低: ' + minV.toFixed(4) + '</span>' +
      '<span>区间均值: ' + avg.toFixed(4) + '</span>' +
      '<span style="margin-left:auto;color:rgba(255,255,255,.35)">' + pts.length + ' 个交易日 · Frankfurter(ECB)</span>';
    document.getElementById('kgp-info').innerHTML = info;
  }

  window.openKline = function (code, name) {
    injectStyle();
    ensureModal();
    var ov = document.getElementById(OVERLAY_ID);
    var body = document.getElementById('kgp-body');
    var title = document.getElementById('kgp-title');
    var erSym = FX_ER[code];
    title.innerHTML = esc(name) + ' <small>' + (erSym ? '汇率日走势' : '日K线') + '</small>';
    body.innerHTML = '<div id="kgp-loading">正在加载' + (erSym ? '汇率走势' : ' K 线') + '…</div>';
    document.getElementById('kgp-info').innerHTML = '';
    ov.classList.add('show');

    if (erSym) {
      fetchFrankfurter(erSym).then(function (pts) {
        if (pts) { renderLineChart(pts, name); return; }
        // 兜底：仍尝试东财/新浪 K 线（多数汇率品种无，会显示无数据）
        fetchKline(code).then(function (bars) {
          if (bars) renderChart(bars, name);
          else body.innerHTML = '<div id="kgp-empty">暂无历史走势数据（该币种可能无公开日线）</div>';
        }).catch(function () {
          body.innerHTML = '<div id="kgp-empty">暂无历史走势数据（该币种可能无公开日线）</div>';
        });
      }).catch(function () {
        body.innerHTML = '<div id="kgp-empty">汇率走势加载异常</div>';
      });
      return;
    }

    fetchKline(code).then(function (bars) {
      if (!bars) {
        body.innerHTML = '<div id="kgp-empty">暂无 K 线数据（该品种可能未在东财开放 K 线）</div>';
        return;
      }
      renderChart(bars, name);
    }).catch(function () {
      body.innerHTML = '<div id="kgp-empty">K 线加载异常</div>';
    });
  };
})();
