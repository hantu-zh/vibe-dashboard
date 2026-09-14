/**
 * kline_global_popup.js — 全球市场/贵金属汇率 K 线弹窗
 * 支持：全球指数、外汇、贵金属
 * 数据源：东方财富历史 K 线（JSONP，浏览器端）
 */
(function () {
  'use strict';

  // Sina 代码 → 东财 secid 候选列表（按优先级尝试）
  var SECID_CANDIDATES = {
    'gb_dji': ['100.DJI', '100.DJIA'],
    'gb_ixic': ['100.IXIC', '100.NDX'],
    'gb_inx': ['100.SPX', '100.INX'],
    'hf_CHA50CFD': ['100.XIN9', '100.FTSEA50', '100.A50'],
    'int_ftse': ['100.FTSE'],
    'b_DAX': ['100.DAX', '100.GDAXI'],
    'b_CAC': ['100.CAC', '100.FCHI'],
    'int_nikkei': ['100.N225'],
    'hkHSI': ['100.HSI'],
    'b_KOSPI': ['100.KS11'],
    'b_AS51': ['100.AS51'],
    'b_SENSEX': ['100.SENSEX', '100.BSE30'],
    'b_TWSE': ['100.TWII', '100.TWSE'],
    'DINIW': ['100.UDI', '133.UDI'],
    'hf_XAU': ['101.XAU', '100.XAU', '100.GC', '101.GC'],
    'hf_XAG': ['101.XAG', '100.XAG', '100.SI', '101.SI'],
    'hf_XAU_icbc': ['101.XAU', '100.XAU', '100.GC', '101.GC'],
    'hf_XAG_ccb': ['101.XAG', '100.XAG', '100.SI', '101.SI'],
    'fx_susdcny': ['133.USDCNY', '100.USDCNY'],
    'fx_susdjpy': ['133.USDJPY', '100.USDJPY'],
    'fx_susdeur': ['133.USDEUR', '100.USDEUR'],
    'fx_susdgbp': ['133.USDGBP', '100.USDGBP'],
    'fx_susdaud': ['133.USDAUD', '100.USDAUD'],
    'fx_susdnzd': ['133.USDNZD', '100.USDNZD'],
    'fx_susdhkd': ['133.USDHKD', '100.USDHKD'],
    'fx_susdchf': ['133.USDCHF', '100.USDCHF'],
    'fx_susdcad': ['133.USDCAD', '100.USDCAD'],
    'fx_susdrub': ['133.USDRUB', '100.USDRUB']
  };

  var STYLE_ID = 'kgp-style';
  var OVERLAY_ID = 'kgp-overlay';

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

  // 取候选 secid
  function getCandidates(code) {
    return SECID_CANDIDATES[code] || ['100.' + code.replace(/^[^_]+_/, '').toUpperCase()];
  }

  // JSONP 拉取单条 K 线
  function fetchOne(secid) {
    return new Promise(function (resolve) {
      var cb = '__kgp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
      var url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=' + encodeURIComponent(secid) +
        '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57' +
        '&klt=101&fqt=0&end=20500101&lmt=120&_=' + Date.now() + '&cb=' + cb;
      var script = document.createElement('script');
      var timer = setTimeout(function () { cleanup(); resolve(null); }, 9000);
      window[cb] = function (j) {
        clearTimeout(timer);
        cleanup();
        try {
          var d = j && j.data;
          if (d && d.klines && d.klines.length) {
            resolve({ secid: secid, name: d.name || '', klines: d.klines });
            return;
          }
        } catch (e) {}
        resolve(null);
      };
      function cleanup() {
        try { delete window[cb]; } catch (e) {}
        if (script.parentNode) script.parentNode.removeChild(script);
      }
      script.onerror = function () { clearTimeout(timer); cleanup(); resolve(null); };
      script.src = url;
      document.head.appendChild(script);
    });
  }

  // 顺序尝试候选 secid
  function fetchKline(code) {
    var cands = getCandidates(code);
    function next(i) {
      if (i >= cands.length) return Promise.resolve(null);
      return fetchOne(cands[i]).then(function (r) { return r || next(i + 1); });
    }
    return next(0);
  }

  // 解析 K 线数据
  function parseKlines(klines) {
    return klines.map(function (line) {
      var p = String(line).split(',');
      return {
        date: p[0],
        open: num(p[1]),
        close: num(p[2]),
        high: num(p[3]),
        low: num(p[4]),
        vol: num(p[5])
      };
    }).filter(function (x) { return isFinite(x.open + x.close + x.high + x.low); });
  }

  function ma(bars, n, idx) {
    var sum = 0, c = 0;
    for (var i = idx - n + 1; i <= idx; i++) {
      if (i >= 0) { sum += bars[i].close; c++; }
    }
    return c ? sum / c : NaN;
  }

  // 渲染 SVG K 线
  function renderChart(bars, name) {
    var body = document.getElementById('kgp-body');
    if (!body) return;
    if (!bars || bars.length < 2) {
      body.innerHTML = '<div id="kgp-empty">暂无 K 线数据</div>';
      document.getElementById('kgp-info').innerHTML = '';
      return;
    }
    var W = 860, H = 360, M = { t: 16, r: 50, b: 28, l: 56 };
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

    // 最新统计
    var last = bars[bars.length - 1];
    var prev = bars[bars.length - 2];
    var change = last.close - prev.close;
    var changePct = prev.close ? change / prev.close * 100 : 0;
    var volSum = bars.reduce(function (s, b) { return s + (b.vol || 0); }, 0);
    var avgVol = volSum / bars.length;

    var svgParts = [];
    svgParts.push('<svg id="kgp-chart" viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet">');

    // 网格
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

    // 蜡烛
    var slot = cw / (bars.length - 1 || 1);
    var wickW = 1, bodyW = Math.max(3, slot * 0.55);
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

    // MA5 / MA10
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

  // 对外入口
  window.openKline = function (code, name) {
    injectStyle();
    ensureModal();
    var ov = document.getElementById(OVERLAY_ID);
    var body = document.getElementById('kgp-body');
    var title = document.getElementById('kgp-title');
    title.innerHTML = esc(name) + ' <small>日K线</small>';
    body.innerHTML = '<div id="kgp-loading">正在加载 K 线…</div>';
    document.getElementById('kgp-info').innerHTML = '';
    ov.classList.add('show');

    fetchKline(code).then(function (res) {
      if (!res) {
        body.innerHTML = '<div id="kgp-empty">暂无 K 线数据（该品种可能未在东财开放 K 线）</div>';
        return;
      }
      var bars = parseKlines(res.klines);
      renderChart(bars, name);
    }).catch(function () {
      body.innerHTML = '<div id="kgp-empty">K 线加载异常</div>';
    });
  };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
})();
