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
    '.vmr-head .vmr-dt{font-size:10px;color:#aab4c4;font-weight:600;letter-spacing:.2px;text-align:center;height:15px;line-height:15px;font-variant-numeric:tabular-nums}',
    '.vmr-head .vmr-dt.now{color:#f0dcf7;text-shadow:0 0 8px rgba(206,147,216,.5)}',
    '.vmr-head .vmr-name{color:#6b7280;font-size:10.5px;cursor:default}',
    '.vmr-head .vmr-name:hover{background:none;color:#6b7280}',
    '.vmr-head .vmr-sum{color:#6b7280;font-size:10.5px}',
    '.vmr-legend{display:flex;align-items:center;gap:6px;margin-top:10px;color:#6b7280;font-size:10.5px}',
    '.vmr-bar2{width:112px;height:9px;border-radius:2px}',
    '.vmr-tip{position:fixed;z-index:9999;pointer-events:none;display:none;background:rgba(14,16,24,.97);border:1px solid rgba(255,255,255,.14);border-radius:7px;padding:6px 10px;font-size:11.5px;line-height:1.6;color:#dfe3ec;box-shadow:0 6px 22px rgba(0,0,0,.5);white-space:nowrap}',
    '.vmr-tip b{font-variant-numeric:tabular-nums}',
    '.vmr-empty{color:#6b7280;font-size:12px;padding:18px 0;text-align:center;line-height:2}',
    '@media(max-width:900px){.vmr-row{grid-template-columns:96px 1fr 56px}.vmr-grid{min-width:640px}}',
    /* ── 板块成分股弹窗（热力图同款视图） ── */
    '.vmr-mdl-ov{position:fixed;inset:0;z-index:9998;display:none;align-items:center;justify-content:center;background:rgba(4,6,12,.66)}',
    '.vmr-mdl{width:min(940px,94vw);max-height:88vh;overflow:auto;background:#12141d;border:1px solid rgba(255,255,255,.14);border-radius:12px;padding:14px 16px;box-shadow:0 18px 60px rgba(0,0,0,.6)}',
    '.vmr-mdl-head{display:flex;align-items:center;gap:10px;margin-bottom:4px}',
    '.vmr-mdl-bname{font-size:15px;color:#f0dcf7;font-weight:700}',
    '.vmr-mdl-meta{font-size:11px;color:#7d8494;margin-left:8px}',
    '.vmr-mdl-btns{margin-left:auto;display:flex;gap:6px}',
    '.vmr-mdl-kbtn{background:rgba(206,147,216,.18);border:1px solid rgba(206,147,216,.5);color:#e8d5f0;border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer;transition:.15s}',
    '.vmr-mdl-kbtn:hover{background:rgba(206,147,216,.32)}',
    '.vmr-mdl-x{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:#9aa3b2;border-radius:6px;padding:3px 8px;font-size:12px;cursor:pointer}',
    '.vmr-mdl-tip{font-size:11px;color:#6b7280;margin-bottom:8px}',
    '.vmr-mdl-canvas{position:relative;height:56vh;min-height:320px;border-radius:8px;overflow:hidden;background:rgba(0,0,0,.28)}',
    '.vmr-mdl-box{position:absolute;border:1px solid rgba(0,0,0,.25);border-radius:3px;cursor:pointer;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff}',
    '.vmr-mdl-box:hover{outline:2px solid rgba(255,255,255,.85);z-index:2}',
    '.vmr-mdl-box .n{font-size:11px;font-weight:600;line-height:1.15;white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,.5)}',
    '.vmr-mdl-box .p{font-size:11px;margin-top:2px;font-variant-numeric:tabular-nums;text-shadow:0 1px 2px rgba(0,0,0,.5)}',
    '.vmr-mdl-loading,.vmr-mdl-err{padding:40px 0;text-align:center;color:#7d8494;font-size:12.5px;line-height:2}'
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

  /* ================================================================== */
  /* 板块成分股弹窗（热力图个股页同款视图，2026-10-16）                    */
  /* 点板块名 → 弹窗内 treemap：方块大小=流通市值，颜色=当日涨跌          */
  /* 数据：东财 b:BKxxxx（JSONP，带流通市值 f21）→ 失败按板块名切新浪      */
  /* ================================================================== */

  function sSign(v) { return v > 0 ? '+' : ''; }
  function sFmt(n, d) { return Number(n).toFixed(d === undefined ? 2 : d); }
  function toTCode(c6) {
    var c = String(c6);
    if (/^(6|9)/.test(c)) return 'sh' + c;
    if (/^(0|2|3)/.test(c)) return 'sz' + c;
    return 'sh' + c;
  }
  /* 涨红跌绿（A股口径），4% 封顶，平盘中性灰 —— 与热力图个股方块一致 */
  function stColor(pct) {
    if (!pct) return 'rgba(120,130,145,0.55)';
    var a = Math.min(Math.abs(pct) / 4, 1);
    var alpha = (0.55 + 0.4 * a).toFixed(2);
    return pct > 0 ? 'rgba(255,77,79,' + alpha + ')' : 'rgba(0,200,83,' + alpha + ')';
  }

  function loadScript(src, charset) {
    return new Promise(function (resolve) {
      var s = document.createElement('script');
      if (charset) s.charset = charset;
      var cleanup = function () { clearTimeout(timer); s.remove(); };
      var timer = setTimeout(function () { cleanup(); resolve(false); }, 4000);
      s.onload = function () { cleanup(); resolve(true); };
      s.onerror = function () { cleanup(); resolve(false); };
      s.src = src; document.head.appendChild(s);
    });
  }

  /* 东财板块成分股：JSONP 函数回调，f12 代码 / f14 名称 / f3 涨跌 / f21 流通市值 */
  function fetchStocksEM(bk) {
    return new Promise(function (resolve) {
      var cb = '__vmrc' + Date.now() + Math.floor(Math.random() * 1e5);
      var url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fs=' +
        encodeURIComponent('b:' + bk) + '&fields=f12,f14,f3,f21&fid=f21&cb=' + cb;
      var s = document.createElement('script');
      var cleanup = function () { try { delete window[cb]; } catch (e) { window[cb] = undefined; } s.remove(); clearTimeout(timer); };
      var timer = setTimeout(function () { cleanup(); resolve(null); }, 3000);
      window[cb] = function (d) {
        cleanup();
        try {
          var diff = (d && d.data && d.data.diff) || [];
          var out = diff.filter(function (x) { return x.f14 && x.f3 !== null && x.f3 !== undefined; })
            .map(function (x) { return { code: x.f12, name: x.f14, pct: parseFloat(x.f3), mv: x.f21 !== undefined ? parseFloat(x.f21) : 0 }; });
          resolve(out.length ? out : null);
        } catch (e) { resolve(null); }
      };
      s.onerror = function () { cleanup(); resolve(null); };
      s.src = url; document.head.appendChild(s);
    });
  }

  /* 新浪板块列表（GBK，var 全局赋值）：行业 + 概念两套，按板块名匹配 */
  function sinaBoardsByType(type) {
    return new Promise(function (resolve) {
      var gname = type === 'concept' ? 'S_Finance_bankuai_class' : 'S_Finance_bankuai_sinaindustry';
      try { window[gname] = null; } catch (e) { }
      loadScript('https://vip.stock.finance.sina.com.cn/q/view/' +
        (type === 'concept' ? 'newFLJK.php?param=class' : 'newSinaHy.php') + '&r=' + Date.now(), 'GBK')
        .then(function (ok) {
          var raw = ok ? window[gname] : null;
          if (!raw) { resolve(null); return; }
          var out = [];
          for (var k in raw) {
            var f = String(raw[k]).split(',');
            if (f.length < 8) continue;
            if (f[1]) out.push({ code: f[0], name: f[1] });
          }
          resolve(out.length ? out : null);
        });
    });
  }

  /* 新浪成分股（jsonp_v2，var 全局赋值——读值必须在置 undefined 之前） */
  function sinaConstituents(node) {
    return new Promise(function (resolve) {
      var vn = '__vmrsn' + Date.now() + Math.floor(Math.random() * 1e5);
      var s = document.createElement('script');
      var cleanup = function () { try { window[vn] = undefined; } catch (e) { } s.remove(); clearTimeout(timer); };
      var timer = setTimeout(function () { cleanup(); resolve(null); }, 4000);
      s.onload = function () {
        clearTimeout(timer);
        try {
          var d = window[vn];
          cleanup();
          if (!d || !d.length) { resolve(null); return; }
          var out = d.map(function (x) {
            return { code: x.code, name: x.name, pct: parseFloat(x.changepercent), mv: (parseFloat(x.nmc) || 0) * 1e4 };
          }).filter(function (x) { return x.name && !isNaN(x.pct) && x.mv > 0; });
          resolve(out.length ? out : null);
        } catch (e) { resolve(null); }
      };
      s.onerror = function () { cleanup(); resolve(null); };
      s.src = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/jsonp_v2.php/' +
        'var%20' + vn + '=/Market_Center.getHQNodeData?page=1&num=100&sort=amount&asc=0&node=' +
        encodeURIComponent(node) + '&r=' + Date.now();
      document.head.appendChild(s);
    });
  }

  function fetchStocksSina(boardName) {
    return sinaBoardsByType('industry').then(function (lst) {
      var b = lst && lst.filter(function (g) { return g.name === boardName; })[0];
      if (b) return sinaConstituents(b.code);
      return sinaBoardsByType('concept').then(function (lst2) {
        var b2 = lst2 && lst2.filter(function (g) { return g.name === boardName; })[0];
        return b2 ? sinaConstituents(b2.code) : null;
      });
    });
  }

  /* ── squarified treemap（与热力图同算法，自含实现） ── */
  function sqWorst(row, len) {
    var s = 0, i, mx = -Infinity, mn = Infinity;
    for (i = 0; i < row.length; i++) { s += row[i].v; if (row[i].v > mx) mx = row[i].v; if (row[i].v < mn) mn = row[i].v; }
    var s2 = s * s, l2 = len * len;
    return Math.max((l2 * mx) / s2, s2 / (l2 * mn));
  }
  function sqFill(items, x, y, w, h, out) {
    items = items.slice();
    while (items.length && w > 0.5 && h > 0.5) {
      var len = Math.max(w, h);
      var row = [items.shift()];
      var best = sqWorst(row, len);
      while (items.length) {
        var nx = sqWorst(row.concat(items[0]), len);
        if (nx <= best) { row.push(items.shift()); best = nx; } else break;
      }
      var total = 0, i;
      for (i = 0; i < row.length; i++) total += row[i].v;
      if (w >= h) {
        var rw = total / h, cy = y;
        row.forEach(function (it) { var hh = it.v / rw; out.push({ code: it.code, name: it.name, pct: it.pct, mv: it.mv, x: x, y: cy, w: rw, h: hh }); cy += hh; });
        x += rw; w -= rw;
      } else {
        var rh = total / w, cx = x;
        row.forEach(function (it) { var ww = it.v / rh; out.push({ code: it.code, name: it.name, pct: it.pct, mv: it.mv, x: cx, y: y, w: ww, h: rh }); cx += ww; });
        y += rh; h -= rh;
      }
    }
    return out;
  }
  function sqLayout(items, W, H) {
    var total = 0, i;
    for (i = 0; i < items.length; i++) total += items[i].v;
    if (!total) total = 1;
    var scale = W * H / total;
    return sqFill(items.map(function (it) { return { code: it.code, name: it.name, pct: it.pct, mv: it.mv, v: it.v * scale }; }), 0, 0, W, H, []);
  }

  /* ── 弹窗状态与 DOM ── */
  var MDL = null;            // 弹窗遮罩层 DOM
  var MDL_BOARD = null;      // 当前展示的板块 {code,name}
  var stockCache = {};       // BK代码 → {d:成分股, src:来源}（本页会话内缓存）
  var ESC_ON = false;

  function ensureModal() {
    if (MDL) return MDL;
    var ov = document.createElement('div');
    ov.className = 'vmr-mdl-ov';
    ov.innerHTML =
      '<div class="vmr-mdl" role="dialog">' +
        '<div class="vmr-mdl-head">' +
          '<div class="vmr-mdl-title"><b class="vmr-mdl-bname"></b><span class="vmr-mdl-meta"></span></div>' +
          '<div class="vmr-mdl-btns">' +
            '<button class="vmr-mdl-kbtn" title="查看该板块的日K线">板块K线</button>' +
            '<button class="vmr-mdl-x" title="关闭">✕</button>' +
          '</div>' +
        '</div>' +
        '<div class="vmr-mdl-tip">方块大小 = 流通市值 · 颜色 = 当日涨跌（红涨绿跌） · 点方块看个股K线 · ESC 关闭</div>' +
        '<div class="vmr-mdl-body"><div class="vmr-mdl-canvas"></div></div>' +
      '</div>';
    document.body.appendChild(ov);
    ov.addEventListener('click', function (e) { if (e.target === ov) closeModal(); });
    ov.querySelector('.vmr-mdl-x').addEventListener('click', closeModal);
    MDL = ov;
    return MDL;
  }
  function closeModal() {
    if (!MDL) return;
    MDL.style.display = 'none';
    if (ESC_ON) { document.removeEventListener('keydown', escClose); ESC_ON = false; }
  }
  function escClose(e) { if (e.key === 'Escape') closeModal(); }

  /* 对外入口：点板块名 → 拉成分股并渲染弹窗 */
  function openSectorStocks(code, name) {
    var bk = String(code || '').toUpperCase();
    if (!bk) return;
    var ov = ensureModal();
    ov.style.display = 'flex';
    MDL_BOARD = { code: bk, name: name };
    ov.querySelector('.vmr-mdl-bname').textContent = name || bk;
    ov.querySelector('.vmr-mdl-meta').textContent = '';
    ov.querySelector('.vmr-mdl-kbtn').onclick = function () { closeModal(); openKlineFor(bk, name); };
    if (!ESC_ON) { document.addEventListener('keydown', escClose); ESC_ON = true; }

    var cv = ov.querySelector('.vmr-mdl-canvas');
    if (stockCache[bk]) { renderStockTreemap(stockCache[bk].d, stockCache[bk].src); return; }
    cv.innerHTML = '<div class="vmr-mdl-loading">正在加载「' + name + '」成分股…</div>';
    fetchStocksEM(bk).then(function (d) {
      if (MDL_BOARD && MDL_BOARD.code !== bk) return;   // 期间已切到别的板块
      if (d) { stockCache[bk] = { d: d, src: '东财' }; renderStockTreemap(d, '东财'); return; }
      return fetchStocksSina(name).then(function (d2) {
        if (MDL_BOARD && MDL_BOARD.code !== bk) return;
        if (d2) { stockCache[bk] = { d: d2, src: '新浪' }; renderStockTreemap(d2, '新浪'); return; }
        cv.innerHTML = '<div class="vmr-mdl-err">「' + name + '」成分股暂时拉不到<br>（行情接口繁忙，关掉再点一次试试）</div>';
      });
    });
  }

  function renderStockTreemap(stocks, src) {
    if (!MDL) return;
    var cv = MDL.querySelector('.vmr-mdl-canvas');
    MDL.querySelector('.vmr-mdl-meta').textContent = stocks.length + ' 只 · 数据源 ' + src;
    var W = cv.clientWidth || 860, H = cv.clientHeight || 430;
    var items = stocks.filter(function (s) { return s.mv > 0; })
      .map(function (s) { return { code: s.code, name: s.name, pct: s.pct, mv: s.mv, v: s.mv }; });
    if (!items.length) items = stocks.map(function (s) { return { code: s.code, name: s.name, pct: s.pct, mv: s.mv, v: 1 }; });
    items.sort(function (a, b) { return b.v - a.v; });
    var rects = sqLayout(items, W, H);
    cv.innerHTML = rects.map(function (r) {
      var show = r.w > 16 && r.h > 26;
      return '<div class="vmr-mdl-box" style="left:' + (r.x / W * 100) + '%;top:' + (r.y / H * 100) + '%;width:' + (r.w / W * 100) +
        '%;height:' + (r.h / H * 100) + '%;background:' + stColor(r.pct) + '" title="' + r.name + ' ' + r.code + '  ' +
        sSign(r.pct) + sFmt(r.pct) + '%  流通市值:' + (r.mv / 1e8).toFixed(0) + '亿  点击看K线" data-code="' + r.code + '" data-name="' + r.name + '">' +
        (show ? '<div class="n">' + r.name + '</div><div class="p">' + sSign(r.pct) + sFmt(r.pct) + '%</div>' : '') + '</div>';
    }).join('');
    Array.prototype.forEach.call(cv.querySelectorAll('.vmr-mdl-box'), function (el) {
      el.addEventListener('click', function () {
        openKlineFor(toTCode(el.dataset.code), el.dataset.name);
      });
    });
  }

  function render() {
    if (!HOST) return;
    if (!DATA || !DATA.rows || !DATA.rows.length) {
      HOST.innerHTML = '<div class="vmr-empty">板块轮动矩阵暂无数据<br>（等待 ai_analysis_board_kline.json 积累足够的板块日K）</div>';
      return;
    }

    /* 横轴时间序：左＝最近（近→远），反转后第 0 列即最新交易日 */
    var dates = (DATA.dates || []).slice(-WIN).reverse();
    var N = dates.length;
    /* 行序：始终按「当前窗口」区间涨幅从大到小（切换窗口同步重排；无数据垫底） */
    var rows = DATA.rows.slice().sort(function (a, b) {
      var x = winCum(a, WIN), y = winCum(b, WIN);
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      return y - x;
    });
    var P = paletteCss(PAL);

    var cellsStyle = 'grid-template-columns:repeat(' + N + ',1fr)';

    /* 表头：日期标签（每 5 个显示一个，首尾必显；第 0 列 = 最近交易日） */
    var headDates = dates.map(function (d, i) {
      var show = i === 0 || i === N - 1 || i % 5 === 0;
      return '<div class="vmr-dt' + (i === 0 ? ' now' : '') + '">' + (show ? mmdd(d) : '') + '</div>';
    }).join('');

    var body = rows.map(function (r) {
      var pct = (r.pct || []).slice(-WIN).reverse();
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
        '" title="点击查看「' + r.name + '」成分股热力图">' + r.name + '</div>' +
        '<div class="vmr-cells" style="' + cellsStyle + '">' + cells + '</div>' +
        '<div class="vmr-sum ' + ccls + '" style="color:' + color + '">' + fmtPct(cum) + '</div>' +
        '</div>';
    }).join('');

    var html =
      '<div class="vmr">' +
        '<div class="vmr-bar">' +
          '<div class="vmr-desc">板块按近 <b>' + WIN + '</b> 日累计涨幅<b>从大到小</b>排列 · ' +
            '一格 = 当日涨跌幅 · 点击板块名看该板块个股热力图<br>' +
            '时间跨度 ' + (dates[N - 1] || '') + ' → ' + (dates[0] || '') + '（共 ' + N + ' 个交易日 · 左＝最近）</div>' +
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
        if (el.dataset.code) openSectorStocks(el.dataset.code, el.dataset.name);
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
