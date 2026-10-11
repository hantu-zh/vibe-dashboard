/*!
 * market_rotation.js — 板块轮动矩阵（vibe-dashboard）
 * ------------------------------------------------------------------
 * 与 market_heat.js 同族：自包含（自带样式 + 数据解析 + 渲染），
 * 挂载到 [data-market-rotation] 或 #marketRotation。
 *
 * 看什么：treemap 只能告诉你"此刻谁红"，矩阵告诉你"谁在连续走强 / 阴跌"。
 *   纵轴 = 板块（按近 N 日累计涨幅排序，上强下弱）
 *   横轴 = 交易日（最近 10 / 20 / 30 日可切）
 *   一格 = 该板块当日涨跌幅
 *
 * 数据：宿主内联的 window.__MARKET_ROTATION__
 *   { updated, from, dates:[YYYY-MM-DD...], rows:[{code,name,pct:[num|null...]}] }
 *   由 ai_analysis.py 从 ai_analysis_board_kline.json（板块日K缓存）裁剪而来，
 *   故不需要任何额外的网络请求。
 *
 * 色系：默认「紫青」（暖紫=涨 / 冷青=跌）—— 与上方热力图的红绿形成对比，
 *   并贴合本页霓虹紫/青主题；可一键切换回「红绿」。
 * ------------------------------------------------------------------
 */
(function () {
  'use strict';

  var STYLE_ID = 'vmr-style';

  /* 色系：hue + 亮度区间。up=涨，dn=跌 */
  var PALETTES = {
    violet: { label: '紫青', up: { h: 291, s: 88, l0: 20, l1: 53 }, dn: { h: 188, s: 85, l0: 18, l1: 49 } },
    redgreen: { label: '红绿', up: { h: 358, s: 78, l0: 20, l1: 52 }, dn: { h: 152, s: 60, l0: 17, l1: 46 } }
  };

  var CSS = [
    '.vmr{font-size:12px;color:#c8ccd6}',
    '.vmr-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}',
    '.vmr-desc{color:#7d8494;font-size:11.5px;line-height:1.7}',
    '.vmr-desc b{color:#ce93d8;font-weight:600}',
    '.vmr-sp{flex:1}',
    '.vmr-grp{display:flex;align-items:center;gap:4px}',
    '.vmr-grp>span{color:#6b7280;font-size:11px;margin-right:2px}',
    '.vmr-chip{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.09);color:#9aa3b2;border-radius:6px;padding:3px 8px;font-size:11px;cursor:pointer;transition:.15s}',
    '.vmr-chip:hover{background:rgba(206,147,216,.16);color:#e8d5f0}',
    '.vmr-chip.on{background:rgba(206,147,216,.24);border-color:rgba(206,147,216,.5);color:#f0dcf7}',
    '.vmr-scroll{overflow-x:auto;overflow-y:hidden;padding-bottom:2px}',
    '.vmr-scroll::-webkit-scrollbar{height:8px}',
    '.vmr-scroll::-webkit-scrollbar-thumb{background:rgba(255,255,255,.14);border-radius:4px}',
    '.vmr-grid{min-width:720px}',
    '.vmr-row{display:grid;grid-template-columns:126px 1fr 62px;gap:3px;align-items:center;margin-bottom:3px}',
    '.vmr-cells{display:grid;gap:2px}',
    '.vmr-name{font-size:11.5px;color:#aab2c0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer;border-radius:4px;padding:2px 4px;transition:.15s}',
    '.vmr-name:hover{background:rgba(206,147,216,.18);color:#fff}',
    '.vmr-sum{font-size:11px;text-align:right;font-variant-numeric:tabular-nums;font-weight:600}',
    '.vmr-c{height:17px;border-radius:2px;cursor:crosshair;transition:outline .1s}',
    '.vmr-c:hover{outline:1px solid rgba(255,255,255,.55);outline-offset:-1px}',
    '.vmr-head .vmr-dt{font-size:9.5px;color:#5d6472;text-align:center;height:14px;line-height:14px;font-variant-numeric:tabular-nums}',
    '.vmr-head .vmr-name{color:#6b7280;font-size:10.5px;cursor:default}',
    '.vmr-head .vmr-name:hover{background:none;color:#6b7280}',
    '.vmr-head .vmr-sum{color:#6b7280;font-size:10.5px}',
    '.vmr-legend{display:flex;align-items:center;gap:6px;margin-top:10px;color:#6b7280;font-size:10.5px}',
    '.vmr-bar2{width:112px;height:9px;border-radius:2px}',
    '.vmr-tip{position:fixed;z-index:9999;pointer-events:none;display:none;background:rgba(14,16,24,.97);border:1px solid rgba(255,255,255,.14);border-radius:7px;padding:6px 10px;font-size:11.5px;line-height:1.6;color:#dfe3ec;box-shadow:0 6px 22px rgba(0,0,0,.5);white-space:nowrap}',
    '.vmr-tip b{font-variant-numeric:tabular-nums}',
    '.vmr-empty{color:#6b7280;font-size:12px;padding:18px 0;text-align:center;line-height:2}',
    '@media(max-width:900px){.vmr-row{grid-template-columns:96px 1fr 56px}.vmr-grid{min-width:640px}}'
  ].join('');

  var HOST = null;      // 挂载容器
  var DATA = null;      // 轮动数据
  var WIN = 30;         // 当前窗口（交易日数）
  var PAL = 'violet';   // 当前色系
  var TIP = null;

  /* ------------------------------------------------------------------ */
  /* 工具                                                               */
  /* ------------------------------------------------------------------ */

  function injectCss() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement('style');
    s.id = STYLE_ID;
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  /** 涨跌幅 -> 背景色（null/缺数据 -> 浅灰底） */
  function colorOf(p) {
    var P = PALETTES[PAL] || PALETTES.violet;
    if (p === null || p === undefined || isNaN(p)) return 'rgba(255,255,255,.045)';
    var t = clamp(Math.abs(p) / 3.5, 0, 1);          // ±3.5% 达到满饱和
    var cfg = p > 0.004 ? P.up : (p < -0.004 ? P.dn : null);
    if (!cfg) return 'rgba(255,255,255,.075)';
    var l = cfg.l0 + t * (cfg.l1 - cfg.l0);
    var s = cfg.s + t * 10;
    return 'hsl(' + cfg.h + ',' + s.toFixed(0) + '%,' + l.toFixed(1) + '%)';
  }

  function paletteCss(pal) {
    /* 图例渐变条：暗 -> 亮 */
    var P = PALETTES[pal];
    var up = 'hsl(' + P.up.h + ',85%,23%)', up2 = 'hsl(' + P.up.h + ',95%,55%)';
    var dn = 'hsl(' + P.dn.h + ',80%,20%)', dn2 = 'hsl(' + P.dn.h + ',88%,50%)';
    return {
      dn: 'linear-gradient(90deg,' + dn + ',' + dn2 + ')',
      up: 'linear-gradient(90deg,' + up + ',' + up2 + ')'
    };
  }

  function fmtPct(p) {
    if (p === null || p === undefined || isNaN(p)) return '—';
    return (p > 0 ? '+' : '') + p.toFixed(2) + '%';
  }

  function clsOf(p) {
    if (p === null || p === undefined || isNaN(p)) return '';
    return p > 0.004 ? 'up' : (p < -0.004 ? 'down' : '');
  }

  /** 窗口内区间涨幅（复利） */
  function winCum(row, n) {
    var arr = (row.pct || []).slice(-n), acc = 1, hit = 0;
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (v === null || v === undefined || isNaN(v)) continue;
      acc *= (1 + v / 100); hit++;
    }
    if (!hit) return null;
    return (acc - 1) * 100;
  }

  function mmdd(d) { return String(d).slice(5); }

  /* ------------------------------------------------------------------ */
  /* 渲染                                                               */
  /* ------------------------------------------------------------------ */

  function tipEl() {
    if (!TIP) { TIP = document.createElement('div'); TIP.className = 'vmr-tip'; document.body.appendChild(TIP); }
    return TIP;
  }

  function bindTip(el) {
    var tip = tipEl();
    el.addEventListener('mouseover', function (e) {
      var cell = e.target.closest ? e.target.closest('.vmr-c') : null;
      if (!cell || !cell.dataset.t) { tip.style.display = 'none'; return; }
      tip.innerHTML = cell.dataset.t;
      tip.style.display = 'block';
    });
    el.addEventListener('mousemove', function (e) {
      if (tip.style.display !== 'block') return;
      var w = tip.offsetWidth, h = tip.offsetHeight;
      var x = e.clientX + 14, y = e.clientY + 16;
      if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
      if (y + h > window.innerHeight - 8) y = e.clientY - h - 12;
      tip.style.left = x + 'px'; tip.style.top = y + 'px';
    });
    el.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
    window.addEventListener('scroll', function () { tip.style.display = 'none'; }, true);
  }

  function openKlineFor(code, name) {
    /* 复用宿主（ai_analysis 页）的通用 K 线弹窗；板块代码用 bk#### */
    if (typeof window.openKline === 'function') { window.openKline(code, name); return; }
    if (window.KlinePopup && typeof window.KlinePopup.open === 'function') { window.KlinePopup.open(code, name); return; }
  }

  function render() {
    if (!HOST) return;
    if (!DATA || !DATA.rows || !DATA.rows.length) {
      HOST.innerHTML = '<div class="vmr-empty">板块轮动矩阵暂无数据<br>（等待 ai_analysis_board_kline.json 积累足够的板块日K）</div>';
      return;
    }

    var dates = (DATA.dates || []).slice(-WIN);
    var N = dates.length;
    var rows = DATA.rows;
    var P = paletteCss(PAL);

    var cellsStyle = 'grid-template-columns:repeat(' + N + ',1fr)';

    /* 表头：日期标签（每 5 个显示一个，首尾必显） */
    var headDates = dates.map(function (d, i) {
      var show = i === 0 || i === N - 1 || (N - 1 - i) % 5 === 0;
      return '<div class="vmr-dt">' + (show ? mmdd(d) : '') + '</div>';
    }).join('');

    var body = rows.map(function (r) {
      var pct = (r.pct || []).slice(-WIN);
      var cells = '';
      for (var i = 0; i < N; i++) {
        var v = pct[i];
        var has = !(v === null || v === undefined || isNaN(v));
        var t = has
          ? '<b>' + r.name + '</b><br>' + dates[i] + ' &nbsp; <b style="color:' +
            (v > 0.004 ? '#e879f9' : v < -0.004 ? '#22d3ee' : '#9aa3b2') + '">' + fmtPct(v) + '</b>'
          : '<b>' + r.name + '</b><br>' + dates[i] + ' &nbsp; <span style="color:#6b7280">无数据</span>';
        cells += '<div class="vmr-c" style="background:' + colorOf(has ? v : null) +
          '" data-t="' + t.replace(/"/g, '&quot;') + '"></div>';
      }
      var cum = winCum(r, WIN);
      var ccls = cum === null ? '' : (cum > 0.004 ? 'up' : cum < -0.004 ? 'down' : '');
      var color = cum === null ? '#6b7280' : (cum > 0.004 ? '#e879f9' : cum < -0.004 ? '#22d3ee' : '#9aa3b2');
      return '<div class="vmr-row">' +
        '<div class="vmr-name" data-code="' + (r.code || '') + '" data-name="' + String(r.name).replace(/"/g, '&quot;') +
        '" title="点击查看「' + r.name + '」板块K线">' + r.name + '</div>' +
        '<div class="vmr-cells" style="' + cellsStyle + '">' + cells + '</div>' +
        '<div class="vmr-sum ' + ccls + '" style="color:' + color + '">' + fmtPct(cum) + '</div>' +
        '</div>';
    }).join('');

    var html =
      '<div class="vmr">' +
        '<div class="vmr-bar">' +
          '<div class="vmr-desc">纵轴板块按近 <b>' + WIN + '</b> 日累计涨幅排序（上强下弱）· ' +
            '一格 = 当日涨跌幅 · 点击板块名看K线<br>' +
            '时间跨度 ' + (dates[0] || '') + ' → ' + (dates[N - 1] || '') + '（共 ' + N + ' 个交易日）</div>' +
          '<div class="vmr-sp"></div>' +
          '<div class="vmr-grp"><span>窗口</span>' +
            [10, 20, 30].map(function (w) {
              return '<button class="vmr-chip' + (w === WIN ? ' on' : '') + '" data-w="' + w + '">' + w + '日</button>';
            }).join('') + '</div>' +
          '<div class="vmr-grp"><span>色系</span>' +
            Object.keys(PALETTES).map(function (k) {
              return '<button class="vmr-chip' + (k === PAL ? ' on' : '') + '" data-p="' + k + '">' + PALETTES[k].label + '</button>';
            }).join('') + '</div>' +
        '</div>' +
        '<div class="vmr-scroll"><div class="vmr-grid">' +
          '<div class="vmr-row vmr-head"><div class="vmr-name">板块</div>' +
            '<div class="vmr-cells" style="' + cellsStyle + '">' + headDates + '</div>' +
            '<div class="vmr-sum">区间</div></div>' +
          body +
        '</div></div>' +
        '<div class="vmr-legend">' +
          '<span>跌</span><div class="vmr-bar2" style="background:' + P.dn + '"></div>' +
          '<span style="color:#4b5563">-3.5%</span><span style="color:#4b5563">0</span><span style="color:#4b5563">+3.5%</span>' +
          '<div class="vmr-bar2" style="background:' + P.up + '"></div><span>涨</span>' +
          '<span style="margin-left:10px;color:#4b5563">灰格 = 当日无数据</span>' +
        '</div>' +
      '</div>';

    HOST.innerHTML = html;

    /* 交互 */
    HOST.querySelectorAll('.vmr-row .vmr-name[data-code]').forEach(function (el) {
      el.addEventListener('click', function (e) {
        e.stopPropagation();
        if (el.dataset.code) openKlineFor(el.dataset.code, el.dataset.name);
      });
    });
    HOST.querySelectorAll('.vmr-chip[data-w]').forEach(function (b) {
      b.addEventListener('click', function () { WIN = parseInt(b.dataset.w, 10) || 30; render(); });
    });
    HOST.querySelectorAll('.vmr-chip[data-p]').forEach(function (b) {
      b.addEventListener('click', function () { PAL = b.dataset.p; render(); });
    });
    bindTip(HOST);
  }

  /* ------------------------------------------------------------------ */
  /* 挂载                                                               */
  /* ------------------------------------------------------------------ */

  function mount(el) {
    injectCss();
    HOST = (typeof el === 'string') ? document.querySelector(el) : el;
    if (!HOST) return;
    if (!DATA && window.__MARKET_ROTATION__) DATA = window.__MARKET_ROTATION__;
    render();
  }

  function boot() {
    var el = document.getElementById('marketRotation') || document.querySelector('[data-market-rotation]');
    if (el) {
      if (!DATA && window.__MARKET_ROTATION__) DATA = window.__MARKET_ROTATION__;
      if (DATA) mount(el);
      else HOST = el;   /* 数据可能稍后就位，先挂上等 setData */
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  window.VibeMarketRotation = {
    mount: mount,
    render: render,
    setData: function (d) { DATA = d; if (HOST) render(); },
    getData: function () { return DATA; }
  };
})();
