/* ============================================================================
   market_heat.js — 市场热力 treemap（板块 / 个股）· 自包含模块
   ----------------------------------------------------------------------------
   从 terminal.html 抽出，自带样式 + 数据层 + 渲染，不依赖宿主页面的全局变量。

   数据源链（逐级自动兜底）：
     ① 东财 push2.eastmoney.com   —— 主源，板块带流通市值 f21、成分股带 f21
     ② 新浪 vip.stock.finance.sina.com.cn —— jsonp_v2（var 变量赋值），
        成分股带 changepercent + nmc（流通市值，万元）；板块无市值 → 方块降级为成交额
     ③ 腾讯 web.ifzq.gtimg.cn/appstock/app/mktHs/rank（_var 变量赋值）
        —— 仅板块列表（无市值/成分股）→ 方块等大；下钻时按板块名匹配新浪/东财

   挂载：
     <div id="marketHeat"></div>
     <script src="market_heat.js?v=20261011a" defer></script>

   可选配置（在脚本加载前定义 window.MARKET_HEAT_OPTIONS）：
     defaultView : 'sectors' | 'stocks'     默认 'sectors'
     onPick      : function(code6, name){}  点击个股方块时触发；
                   默认调用宿主页面的 window.openKline(code, name)（若存在）
   ============================================================================ */
(function () {
  "use strict";

  var OPT = (typeof window !== "undefined" && window.MARKET_HEAT_OPTIONS) || {};
  var CSS_ID = "vmh-style";

  /* ---------------------------- 样式（自带，作用域 .vmh） ---------------------------- */
  var CSS = [
    ".vmh{--vmh-line:rgba(255,255,255,.10);--vmh-panel:rgba(255,255,255,.045);",
    "--vmh-txt:#e3e6ee;--vmh-dim:#8b95a5;--vmh-accent:#b829ff;--vmh-up:#ef5350;--vmh-down:#26c281;",
    "color:var(--vmh-txt);font-size:14px;font-family:inherit;}",
    ".vmh *{box-sizing:border-box;}",
    ".vmh-head{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap;}",
    ".vmh-seg{display:flex;gap:3px;background:var(--vmh-panel);border:1px solid var(--vmh-line);border-radius:8px;padding:2px;}",
    ".vmh-seg button{background:transparent;border:0;color:var(--vmh-dim);padding:4px 12px;border-radius:6px;cursor:pointer;font-size:13px;font-family:inherit;}",
    ".vmh-seg button.on{background:var(--vmh-accent);color:#fff;}",
    ".vmh-src{margin-left:auto;font-size:11px;color:var(--vmh-dim);border:1px solid var(--vmh-line);border-radius:5px;padding:1px 6px;white-space:nowrap;}",
    ".vmh-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px;}",
    ".vmh-group{background:var(--vmh-panel);border:1px solid var(--vmh-line);border-radius:10px;padding:8px;}",
    ".vmh-title{font-size:12px;font-weight:600;color:var(--vmh-dim);margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;gap:8px;}",
    ".vmh-back{background:var(--vmh-panel);border:1px solid var(--vmh-line);color:var(--vmh-accent);border-radius:6px;padding:2px 10px;cursor:pointer;font-size:12px;font-family:inherit;}",
    ".vmh-canvas{position:relative;height:200px;border-radius:6px;overflow:hidden;background:rgba(0,0,0,.28);}",
    ".vmh-canvas.big{height:340px;}",
    ".vmh-box{position:absolute;border:1px solid rgba(0,0,0,.22);border-radius:3px;cursor:pointer;overflow:hidden;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;}",
    ".vmh-box:hover{outline:2px solid rgba(255,255,255,.85);z-index:2;}",
    ".vmh-box .tn{font-size:11px;font-weight:600;line-height:1.15;white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,.5);}",
    ".vmh-box .tp{font-size:11px;margin-top:2px;font-variant-numeric:tabular-nums;text-shadow:0 1px 2px rgba(0,0,0,.5);}",
    ".vmh-loading{grid-column:1/-1;padding:30px;text-align:center;color:var(--vmh-dim);font-size:13px;}",
    ".vmh-err{grid-column:1/-1;padding:26px;text-align:center;color:var(--vmh-dim);font-size:13px;line-height:2;}"
  ].join("");

  function injectCss() {
    if (document.getElementById(CSS_ID)) return;
    var st = document.createElement("style");
    st.id = CSS_ID; st.textContent = CSS;
    document.head.appendChild(st);
  }

  /* ---------------------------- 工具 ---------------------------- */
  function sign(v) { return v > 0 ? "+" : ""; }
  function fmt(n, d) { return Number(n).toFixed(d === undefined ? 2 : d); }
  function toTCode(c6) {
    var c = String(c6);
    if (/^(6|9)/.test(c)) return "sh" + c;
    if (/^(0|2|3)/.test(c)) return "sz" + c;
    return "sh" + c;
  }
  /* 涨红跌绿（A股口径），4% 封顶，平盘中性灰 */
  function tmColor(pct) {
    if (!pct) return "rgba(120,130,145,0.55)";
    var a = Math.min(Math.abs(pct) / 4, 1);
    var alpha = (0.55 + 0.4 * a).toFixed(2);
    return pct > 0 ? "rgba(255,77,79," + alpha + ")" : "rgba(0,200,83," + alpha + ")";
  }

  /* RPS 分位色阶（与 rps.html 的 rpsTone 同一套 hue）：红=强主线，灰=中性，青/蓝=弱 */
  function rpsFill(v) {
    if (v === null || v === undefined || isNaN(v)) return "rgba(120,130,145,0.55)";
    if (v >= 90) return "rgba(255,45,45,0.88)";
    if (v >= 80) return "rgba(255,107,53,0.78)";
    if (v >= 70) return "rgba(255,179,51,0.68)";
    if (v >= 50) return "rgba(118,132,148,0.50)";
    if (v >= 40) return "rgba(34,187,170,0.55)";
    return "rgba(68,136,204,0.62)";
  }
  function rpsText(v) {
    if (v === null || v === undefined || isNaN(v)) return "#8b95a5";
    if (v >= 90) return "#ff2d2d";
    if (v >= 80) return "#ff6b35";
    if (v >= 70) return "#ffb333";
    if (v >= 50) return "#39ff14";
    if (v >= 40) return "#22bbaa";
    return "#4488cc";
  }
  /* 按板块代码查 RPS（东财 BK#### 归一化小写）；新浪源代码对不上时退回按名称匹配 */
  function rpsOf(code, name) {
    if (!rpsMap) return null;
    var k = String(code || "").toLowerCase();
    if (rpsMap[k]) return rpsMap[k];
    if (name && rpsNameIdx) {
      var nk = String(name).replace(/\s+/g, "");
      if (rpsNameIdx[nk]) return rpsNameIdx[nk];
    }
    return null;
  }

  /* ---------------------------- squarified treemap ---------------------------- */
  function tmWorst(row, len) {
    var s = 0, i;
    for (i = 0; i < row.length; i++) s += row[i].v;
    var mx = -Infinity, mn = Infinity;
    for (i = 0; i < row.length; i++) { if (row[i].v > mx) mx = row[i].v; if (row[i].v < mn) mn = row[i].v; }
    var s2 = s * s, l2 = len * len;
    return Math.max((l2 * mx) / s2, s2 / (l2 * mn));
  }
  function squarify(items, x, y, w, h, out) {
    items = items.slice();
    while (items.length && w > 0.5 && h > 0.5) {
      var len = Math.max(w, h);
      var row = [items.shift()];
      var best = tmWorst(row, len);
      while (items.length) {
        var nx = tmWorst(row.concat(items[0]), len);
        if (nx <= best) { row.push(items.shift()); best = nx; } else break;
      }
      var total = 0;
      for (var i = 0; i < row.length; i++) total += row[i].v;
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
  function tmLayout(items, W, H) {
    var total = 0;
    for (var i = 0; i < items.length; i++) total += items[i].v;
    if (!total) total = 1;
    var scale = W * H / total;
    return squarify(items.map(function (i) {
      return { code: i.code, name: i.name, pct: i.pct, mv: i.mv, v: i.v * scale };
    }), 0, 0, W, H, []);
  }

  /* ---------------------------- 状态 ---------------------------- */
  var elWrap = null;                 // 本次挂载的 .vmh-wrap 容器
  var heatView = OPT.defaultView === "stocks" ? "stocks" : "sectors";
  var focusBoard = null;
  var sectorType = "industry";
  var sectorCache = { industry: [], concept: [] };
  var sectorSrc = { industry: "em", concept: "em" };
  var rpsEmTried = 0;      /* RPS 模式下拉回东财源的时间戳（60s 内不重复） */
  var srcStatus = { em: "—", sina: "—", tx: "—" };
  var sectorLoaded = false;
  var heatAutoRetried = false;
  var stockHeatCache = null;
  var boardCloseFn = null;           // 单板块弹窗模式下的「关闭」回调（替代「返回全部板块」）
  var colorMode = "rps";             // 板块上色口径：'rps'=RPS60 分位 / 'pct'=当日涨跌幅
  var rpsMap = null;                 // { codeLower: {n,r60,r20,r5,r120,f} }，由宿主页面内联
  var rpsNameIdx = null;             // 名称 → 条目（新浪源的板块代码与东财不同，按名称兜底）
  var rpsAsOf = "";                  // RPS 基准日（仅用于标注）
  (function initRps() {
    var raw = (typeof window !== "undefined" && window.__BOARD_RPS__) || null;
    if (!raw) return;
    var boards = raw.boards || raw;
    rpsMap = {}; rpsNameIdx = {};
    for (var k in boards) {
      if (!Object.prototype.hasOwnProperty.call(boards, k)) continue;
      var it = boards[k];
      if (!it || typeof it !== "object") continue;
      if (typeof it === "number") it = { r60: it };
      rpsMap[String(k).toLowerCase()] = it;
      if (it.n) rpsNameIdx[String(it.n).replace(/\s+/g, "")] = it;
    }
    rpsAsOf = raw.asof || raw.date || "";
    if (OPT.defaultColorMode === "pct") colorMode = "pct";
  })();
  var timers = [];

  /* ---------------------------- 数据层：东财 ---------------------------- */
  /* 通用东财 clist 拉取（JSONP 函数回调）：fs 如 m:90+t:2 / b:BK0475 */
  function fetchEMList(fs, fields, fid, pz) {
    return new Promise(function (resolve) {
      var cb = "__vmh" + Date.now() + Math.floor(Math.random() * 1e5);
      var url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=" + pz + "&po=1&np=1&fltt=2&invt=2&fs=" +
        encodeURIComponent(fs) + "&fields=" + fields + "&fid=" + fid + "&cb=" + cb;
      var s = document.createElement("script");
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
  function fetchSectors(type) {
    return new Promise(function (resolve) {
      var fs = type === "concept" ? "m:90+t:3" : "m:90+t:2";
      var cb = "__vmhs" + Date.now() + Math.floor(Math.random() * 1e4);
      var url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=80&po=1&np=1&fltt=2&invt=2&fs=" +
        encodeURIComponent(fs) + "&fields=f12,f14,f3,f21&fid=f3&cb=" + cb;
      var s = document.createElement("script");
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

  /* ---------------------------- 数据层：新浪（jsonp_v2，var 变量赋值） ----------------------------
     - 行业列表 /q/view/newSinaHy.php          → var S_Finance_bankuai_sinaindustry={...}（GBK）
     - 概念列表 /q/view/newFLJK.php?param=class → var S_Finance_bankuai_class={...}（GBK）
       值格式 "code,名称,家数,均价,?,涨跌%,成交量,成交额,领涨股代码,..."
     - 成分股   /quotes_service/api/jsonp_v2.php/var%20<名>=/Market_Center.getHQNodeData?node=...
       返回 var <名>=([...])，字段 changepercent(涨跌%) + nmc(流通市值,万元)
     注意：var 声明的全局属性不可 delete，只能置 undefined；且读值必须在 cleanup 之前 */
  function fetchScript(src, charset) {
    return new Promise(function (resolve) {
      var s = document.createElement("script");
      if (charset) s.charset = charset;
      var cleanup = function () { clearTimeout(timer); s.remove(); };
      var timer = setTimeout(function () { cleanup(); resolve(false); }, 4000);
      s.onload = function () { cleanup(); resolve(true); };
      s.onerror = function () { cleanup(); resolve(false); };
      s.src = src; document.head.appendChild(s);
    });
  }
  function fetchSinaBoards(type) {
    return new Promise(function (resolve) {
      var gname = type === "concept" ? "S_Finance_bankuai_class" : "S_Finance_bankuai_sinaindustry";
      try { window[gname] = null; } catch (e) { }
      fetchScript("https://vip.stock.finance.sina.com.cn/q/view/" +
        (type === "concept" ? "newFLJK.php?param=class" : "newSinaHy.php") + "&r=" + Date.now(), "GBK")
        .then(function (ok) {
          var raw = ok ? window[gname] : null;
          if (!raw) { resolve(null); return; }
          var out = [];
          for (var k in raw) {
            var f = String(raw[k]).split(",");
            if (f.length < 8) continue;
            var pct = parseFloat(f[5]), amt = parseFloat(f[7]);
            if (f[1] && !isNaN(pct)) out.push({ code: f[0], name: f[1], pct: pct, mv: isNaN(amt) ? 0 : amt });
          }
          resolve(out.length ? out : null);
        });
    });
  }
  function fetchSinaConstituents(node) {
    return new Promise(function (resolve) {
      var vn = "__vmhsn" + Date.now() + Math.floor(Math.random() * 1e5);
      var s = document.createElement("script");
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
      s.src = "https://vip.stock.finance.sina.com.cn/quotes_service/api/jsonp_v2.php/" +
        "var%20" + vn + "=/Market_Center.getHQNodeData?page=1&num=100&sort=amount&asc=0&node=" +
        encodeURIComponent(node) + "&r=" + Date.now();
      document.head.appendChild(s);
    });
  }

  /* ---------------------------- 数据层：腾讯 mktHs（第三源，仅板块列表） ----------------------------
     t=01/averatio=行业、t=02/averatio=概念。返回 {code,msg,data:[{bd_name,bd_code,bd_zdf,...}]} 整包 */
  function fetchTencentBoards(type) {
    return new Promise(function (resolve) {
      var vn = "__vmhtb" + Date.now() + Math.floor(Math.random() * 1e5);
      var s = document.createElement("script");
      var t = type === "concept" ? "02" : "01";
      var cleanup = function () { try { window[vn] = undefined; } catch (e) { } s.remove(); clearTimeout(timer); };
      var timer = setTimeout(function () { cleanup(); resolve(null); }, 4000);
      s.onload = function () {
        clearTimeout(timer);
        try {
          var d = window[vn]; cleanup();
          var arr = d && d.data;
          if (!arr || !arr.length || !arr[0] || !arr[0].bd_code) { resolve(null); return; }
          var out = arr.map(function (x) {
            var pct = parseFloat(x.bd_zdf);
            return (x.bd_code && x.bd_name && !isNaN(pct)) ? { code: x.bd_code, name: x.bd_name, pct: pct, mv: 0 } : null;
          }).filter(Boolean);
          resolve(out.length ? out : null);
        } catch (e) { resolve(null); }
      };
      s.onerror = function () { cleanup(); resolve(null); };
      s.src = "https://web.ifzq.gtimg.cn/appstock/app/mktHs/rank?l=80&p=1&t=" + t +
        "/averatio&ordertype=zdf&direct=down&_var=" + vn + "&r=" + Date.now();
      document.head.appendChild(s);
    });
  }

  /* ---------------------------- 板块列表刷新（三源轮换 + 自动重试一次） ---------------------------- */
  function refreshSectors(isAutoRetry) {
    var tryEM = function () { return fetchSectors(sectorType).then(function (d) { srcStatus.em = d && d.length ? "OK" : "无响应(被服务端断连)"; return d && d.length ? { src: "em", d: d } : null; }); };
    var trySina = function () { return fetchSinaBoards(sectorType).then(function (d) { srcStatus.sina = d && d.length ? "OK" : "脚本加载失败/超时"; return d && d.length ? { src: "sina", d: d } : null; }); };
    var tryTx = function () { return fetchTencentBoards(sectorType).then(function (d) { srcStatus.tx = d && d.length ? "OK" : "接口无数据/超时"; return d && d.length ? { src: "tx", d: d } : null; }); };
    var order = sectorSrc[sectorType] === "sina" ? [trySina, tryEM, tryTx]
      : sectorSrc[sectorType] === "tx" ? [tryTx, trySina, tryEM]
        : [tryEM, trySina, tryTx];
    function step(i, acc) {
      if (i >= order.length) return Promise.resolve(acc);
      return order[i]().then(function (r) { return r || step(i + 1, acc); });
    }
    return step(0, null).then(function (r) {
      if (!r && !isAutoRetry && !heatAutoRetried) {          // 三源全挂 → 1.5s 后自动重试一次（瞬时抖动）
        heatAutoRetried = true;
        if (heatView === "sectors") renderBoardTreemap();
        setTimeout(function () { refreshSectors(true); }, 1500);
        return;
      }
      if (r) { heatAutoRetried = false; sectorSrc[sectorType] = r.src; sectorCache[sectorType] = r.d; sectorLoaded = true; }
      if (heatView === "sectors") renderBoardTreemap();
    });
  }

  /* ---------------------------- 渲染：错误面板（不放假数据） ---------------------------- */
  function renderHeatError(name) {
    if (!elWrap) return;
    var detail = ["em", "sina", "tx"].map(function (k) {
      var label = k === "em" ? "东财" : (k === "sina" ? "新浪" : "腾讯");
      var st = srcStatus[k];
      return st && st !== "OK" ? label + ":" + st : null;
    }).filter(Boolean).join(" · ") || "东财 · 新浪 · 腾讯 均未返回数据";
    elWrap.innerHTML = '<div class="vmh-group" style="grid-column:1/-1">' +
      '<div class="vmh-title"><span>' + (name ? name + " · " : "") + '数据源不可达</span>' +
      '<button class="vmh-back">↻ 重试</button></div>' +
      '<div class="vmh-canvas big vmh-err">' + detail + '<br>请检查网络/代理/广告拦截插件后点击「重试」</div></div>';
    var btn = elWrap.querySelector(".vmh-back");
    if (btn) btn.onclick = function () {
      if (focusBoard) refreshStockHeatmap();
      else { refreshSectors(); if (heatView === "stocks") refreshStockHeatmap(); }
    };
  }

  /* ---------------------------- 渲染：板块 treemap ---------------------------- */
  function renderBoardTreemap() {
    if (!elWrap) return;
    var list = sectorCache[sectorType] || [];
    if (!list.length) {
      elWrap.innerHTML = sectorLoaded
        ? '<div class="vmh-loading">板块数据源不可达（东财/新浪/腾讯 均未返回）· <button class="vmh-back" style="display:inline">↻ 重试</button></div>'
        : '<div class="vmh-loading">加载板块数据中…</div>';
      var rb = elWrap.querySelector(".vmh-back");
      if (rb) rb.onclick = function () { refreshSectors(); };
      return;
    }
    var items = list.map(function (s) { return { code: s.code, name: s.name, pct: s.pct, mv: s.mv || 0, v: s.mv || 1 }; })
      .sort(function (a, b) { return b.v - a.v; });
    var src = sectorSrc[sectorType];
    var mvLabel = src === "sina" ? "成交额(新浪源)" : (src === "tx" ? "等大(腾讯源)" : "流通市值");
    var useRps = (colorMode === "rps" && !!rpsMap);
    var matched = 0;
    if (useRps) {
      items.forEach(function (it) { if (rpsOf(it.code, it.name)) matched++; });
      if (src !== "em" && Date.now() - rpsEmTried > 60000) {   /* RPS 池是东财 BK 代码，回退源对不上 → 拉回东财源 */
        rpsEmTried = Date.now();
        fetchSectors(sectorType).then(function (d) {
          if (d && d.length) { sectorSrc[sectorType] = "em"; sectorCache[sectorType] = d; sectorLoaded = true; renderBoardTreemap(); }
        });
      }
    }
    var srcLabel = src === "sina" ? "新浪" : (src === "tx" ? "腾讯" : "东财");
    var colorLabel = useRps
      ? ("RPS60 强弱" + (rpsAsOf ? "（基准 " + rpsAsOf + "）" : "")
        + (matched < items.length * 0.6 && src !== "em"
          ? " · 板块源=" + srcLabel + "，仅名称匹配 " + matched + "/" + items.length + "，尝试东财源中…"
          : (matched < items.length ? " · RPS 已匹配 " + matched + "/" + items.length : "")))
      : "当日涨跌幅";
    elWrap.innerHTML =
      '<div class="vmh-group" style="grid-column:1/-1">' +
      '<div class="vmh-title"><span>' + (sectorType === "industry" ? "行业" : "概念") + '板块 · ' + items.length +
      '个 · 方块大小=' + mvLabel + ' / 颜色=' + colorLabel + '</span>' +
      '<span style="display:flex;align-items:center;gap:8px;margin-left:auto">' +
      '<span class="vmh-seg" style="display:inline-flex;border:1px solid var(--vmh-line);border-radius:7px;overflow:hidden">' +
      '<button data-cm="rps"' + (useRps ? ' class="on"' : '') + (rpsMap ? '' : ' disabled title="未内联 RPS 数据"') + '>RPS60</button>' +
      '<button data-cm="pct"' + (useRps ? '' : ' class="on"') + '>当日涨幅</button></span>' +
      '<span class="vmh-src">点击板块 → 成分股</span></span></div>' +
      '<div class="vmh-canvas big"></div></div>';
    var cv = elWrap.querySelector(".vmh-canvas");
    var W = cv.clientWidth || 900, H = cv.clientHeight || 340;
    var rects = tmLayout(items, W, H);
    cv.innerHTML = rects.map(function (r) {
      var show = r.w > 36 && r.h > 28;
      var ri = rpsOf(r.code, r.name);
      var bg = useRps ? rpsFill(ri && ri.r60) : tmColor(r.pct);
      var txt = useRps
        ? ("RPS " + ((ri && ri.r60 != null) ? ri.r60 : "—"))
        : (sign(r.pct) + fmt(r.pct) + "%");
      var tip = r.name + "  " + txt
        + (useRps && ri && ri.r20 != null ? ("  (20日 " + ri.r20 + (ri.f != null ? "  动能 " + (ri.f > 0 ? "+" : "") + ri.f : "") + ")") : "")
        + "  " + mvLabel + ":" + (r.mv ? (r.mv / 1e8).toFixed(0) + "亿" : "—") + "  点击看成分股";
      return '<div class="vmh-box" style="left:' + (r.x / W * 100) + '%;top:' + (r.y / H * 100) + '%;width:' + (r.w / W * 100) +
        '%;height:' + (r.h / H * 100) + '%;background:' + bg + '" title="' + tip + '" data-code="' + r.code + '" data-name="' + r.name + '">' +
        (show ? '<div class="tn">' + r.name + '</div><div class="tp">' + txt + '</div>' : "") + '</div>';
    }).join("");
    Array.prototype.forEach.call(cv.querySelectorAll(".vmh-box"), function (el) {
      el.onclick = function () { focusBoard = { code: el.dataset.code, name: el.dataset.name, src: sectorSrc[sectorType] }; setHeatView("stocks"); };
    });
    Array.prototype.forEach.call(elWrap.querySelectorAll(".vmh-seg button"), function (btn) {
      btn.onclick = function (ev) {
        ev.stopPropagation();
        if (btn.disabled) return;
        colorMode = btn.dataset.cm;
        renderBoardTreemap();
      };
    });
  }

  /* ---------------------------- 渲染：个股 treemap（成分股） ---------------------------- */
  function refreshStockHeatmap() {
    var src = focusBoard ? (focusBoard.src || sectorSrc[sectorType]) : sectorSrc[sectorType];
    if (focusBoard) {
      var p;
      if (src === "sina") {
        p = fetchSinaConstituents(focusBoard.code);
      } else if (src === "tx") {
        p = fetchSinaBoards(sectorType).then(function (sb) {
          var b = sb && sb.filter(function (g) { return g.name === focusBoard.name; })[0];
          if (b) {
            sectorSrc[sectorType] = "sina"; sectorCache[sectorType] = sb;
            focusBoard = { code: b.code, name: b.name, src: "sina" };
            return fetchSinaConstituents(b.code);
          }
          return fetchEMList(sectorType === "industry" ? "m:90+t:2" : "m:90+t:3", "f12,f14,f3,f21", "f21", 90).then(function (eb) {
            var e = eb && eb.filter(function (g) { return g.name === focusBoard.name; })[0];
            if (!e) return null;
            return fetchEMList("b:" + e.code, "f12,f14,f3,f21", "f21", 30).then(function (st) {
              if (!st) return null;
              sectorSrc[sectorType] = "em"; sectorCache[sectorType] = eb;
              focusBoard = { code: e.code, name: e.name, src: "em" };
              return st;
            });
          });
        });
      } else {
        p = fetchEMList("b:" + focusBoard.code, "f12,f14,f3,f21", "f21", 30).then(function (stocks) {
          if (stocks) return stocks;
          return fetchSinaBoards(sectorType).then(function (sb) {          // 东财单板块失败 → 整体切新浪源
            var b = sb && sb.filter(function (g) { return g.name === focusBoard.name; })[0];
            if (sb) { sectorSrc[sectorType] = "sina"; sectorCache[sectorType] = sb; }
            if (!b) return null;
            focusBoard = { code: b.code, name: b.name, src: "sina" };
            return fetchSinaConstituents(b.code);
          });
        });
      }
      return p.then(function (stocks) {
        if (!stocks) { renderHeatError(focusBoard.name); return; }
        stockHeatCache = [{ code: focusBoard.code, name: focusBoard.name, stocks: stocks, big: true }];
        renderStockHeatmap();
      });
    }
    /* 全部板块：取前 9 个板块的头部成分股 */
    var s2 = src, head;
    if (s2 === "sina") {
      head = fetchSinaBoards(sectorType);
    } else if (s2 === "tx") {
      head = fetchSinaBoards(sectorType).then(function (sb) {                      // 腾讯源无成分股 → 先试新浪恢复
        if (sb) { s2 = "sina"; sectorSrc[sectorType] = "sina"; sectorCache[sectorType] = sb; return sb; }
        return null;
      });
    } else {
      head = fetchEMList(sectorType === "industry" ? "m:90+t:2" : "m:90+t:3", "f12,f14,f3,f21", "f21", 9)
        .then(function (inds) {
          if (inds) return inds;
          return fetchSinaBoards(sectorType).then(function (sb) {                  // 东财失败 → 切新浪源
            if (sb) { s2 = "sina"; sectorSrc[sectorType] = "sina"; sectorCache[sectorType] = sb; }
            return sb;
          });
        });
    }
    return head.then(function (inds) {
      if (!inds) { renderHeatError(null); return; }
      return Promise.all(inds.slice(0, 9).map(function (ind) {
        var q = s2 === "sina"
          ? fetchSinaConstituents(ind.code)
          : fetchEMList("b:" + ind.code, "f12,f14,f3,f21", "f21", 10);
        return q.then(function (stocks) { return stocks ? { code: ind.code, name: ind.name, stocks: stocks } : null; });
      })).then(function (groups) {
        groups = groups.filter(Boolean);            // 拉不到的板块直接跳过，不放假数据
        if (!groups.length) { renderHeatError(null); return; }
        stockHeatCache = groups;
        renderStockHeatmap();
      });
    });
  }
  function renderStockHeatmap() {
    if (!elWrap) return;
    if (!stockHeatCache) { elWrap.innerHTML = '<div class="vmh-loading">加载个股热力图中…</div>'; return; }
    elWrap.innerHTML = (focusBoard ? '<div class="vmh-title" style="grid-column:1/-1"><span>' + focusBoard.name +
      ' · 成分股</span><button class="vmh-back">← 返回全部板块</button></div>' : "") +
      stockHeatCache.map(function (g, gi) {
        return '<div class="vmh-group"' + (g.big ? ' style="grid-column:1/-1"' : "") + '><div class="vmh-title"><span>' + g.name +
          '</span><span>' + g.stocks.length + ' 只 · 点击看K线</span></div><div class="vmh-canvas' + (g.big ? " big" : "") + '" data-gi="' + gi + '"></div></div>';
      }).join("");
    var back = elWrap.querySelector(".vmh-back");
    if (back) back.onclick = boardCloseFn ? boardCloseFn : function () { focusBoard = null; refreshStockHeatmap(); };
    stockHeatCache.forEach(function (g, gi) {
      var cv = elWrap.querySelector('.vmh-canvas[data-gi="' + gi + '"]');
      if (!cv) return;
      var W = cv.clientWidth || 310, H = cv.clientHeight || 200;
      var items = g.stocks.filter(function (s) { return s.mv > 0; }).map(function (s) { return { code: s.code, name: s.name, pct: s.pct, mv: s.mv, v: s.mv }; });
      if (!items.length) items = g.stocks.map(function (s) { return { code: s.code, name: s.name, pct: s.pct, mv: s.mv, v: 1 }; });
      items.sort(function (a, b) { return b.v - a.v; });
      var rects = tmLayout(items, W, H);
      cv.innerHTML = rects.map(function (r) {
        var show = r.w > 14 && r.h > 24;
        return '<div class="vmh-box" style="left:' + (r.x / W * 100) + '%;top:' + (r.y / H * 100) + '%;width:' + (r.w / W * 100) +
          '%;height:' + (r.h / H * 100) + '%;background:' + tmColor(r.pct) + '" title="' + r.name + ' ' + r.code + '  ' + sign(r.pct) + fmt(r.pct) +
          '%  流通市值:' + (r.mv / 1e8).toFixed(0) + '亿  点击看K线" data-code="' + r.code + '" data-name="' + r.name + '">' +
          (show ? '<div class="tn">' + r.name + '</div><div class="tp">' + sign(r.pct) + fmt(r.pct) + '%</div>' : "") + '</div>';
      }).join("");
      Array.prototype.forEach.call(cv.querySelectorAll(".vmh-box"), function (el) {
        el.onclick = function () {
          var code6 = String(el.dataset.code), name = el.dataset.name;
          if (typeof OPT.onPick === "function") { OPT.onPick(code6, name); return; }
          if (typeof window.openKline === "function") { window.openKline(toTCode(code6), name); }
        };
      });
    });
  }

  /* ---------------------------- 视图切换 ---------------------------- */
  function setHeatView(v) {
    heatView = v;
    var a = elWrap && elWrap.parentNode.querySelector(".vmh-seg-view");
    if (a) Array.prototype.forEach.call(a.querySelectorAll("button"), function (b) { b.classList.toggle("on", b.dataset.v === v); });
    if (v === "stocks") {
      if (focusBoard || !stockHeatCache) refreshStockHeatmap();
      else renderStockHeatmap();
    } else {
      renderBoardTreemap();
    }
  }

  /* ---------------------------- 挂载 ---------------------------- */
  function mount(host) {
    if (typeof host === "string") host = document.getElementById(host);
    if (!host) { return null; }
    injectCss();
    host.innerHTML =
      '<div class="vmh">' +
      '<div class="vmh-head">' +
      '<div class="vmh-seg vmh-seg-view">' +
      '<button data-v="sectors" class="' + (heatView === "sectors" ? "on" : "") + '">板块</button>' +
      '<button data-v="stocks" class="' + (heatView === "stocks" ? "on" : "") + '">个股</button></div>' +
      '<div class="vmh-seg vmh-seg-type">' +
      '<button data-t="industry" class="on">行业</button>' +
      '<button data-t="concept">概念</button></div>' +
      '<span class="vmh-src">东财 / 新浪 / 腾讯 三源自动切换</span>' +
      '</div>' +
      '<div class="vmh-wrap"></div>' +
      '</div>';
    elWrap = host.querySelector(".vmh-wrap");

    var segView = host.querySelector(".vmh-seg-view");
    var segType = host.querySelector(".vmh-seg-type");
    Array.prototype.forEach.call(segView.querySelectorAll("button"), function (b) {
      b.onclick = function () { focusBoard = null; setHeatView(b.dataset.v); };
    });
    Array.prototype.forEach.call(segType.querySelectorAll("button"), function (b) {
      b.onclick = function () {
        sectorType = b.dataset.t; focusBoard = null;
        Array.prototype.forEach.call(segType.querySelectorAll("button"), function (x) { x.classList.toggle("on", x === b); });
        refreshSectors();
        if (heatView === "stocks") refreshStockHeatmap();
      };
    });

    renderBoardTreemap();
    refreshSectors();
    /* 定时刷新：板块 30s、个股热力 60s；窗口尺寸变化时重排 treemap */
    timers.push(setInterval(function () { if (heatView === "sectors") refreshSectors(); }, 30000));
    timers.push(setInterval(function () { if (heatView === "stocks") refreshStockHeatmap(); }, 60000));
    var rz;
    window.addEventListener("resize", function () {
      clearTimeout(rz);
      rz = setTimeout(function () {
        if (!elWrap || !elWrap.isConnected) return;
        if (heatView === "sectors") renderBoardTreemap();
        else if (stockHeatCache) renderStockHeatmap();
      }, 250);
    });
    return api;
  }

  /* ---------------------------- 对外 API ---------------------------- */
  var api = {
    mount: mount,
    refresh: function () { return refreshSectors(); },
    setView: setHeatView,
    setType: function (t) { sectorType = t === "concept" ? "concept" : "industry"; },
    destroy: function () { timers.forEach(clearInterval); timers = []; elWrap = null; boardCloseFn = null; },
    /* 单板块弹窗：直接渲染某个板块的成分股热力图（东财→新浪兜底），点击个股弹K线。
       opts: { code, name, type:'industry'|'concept', onClose } */
    mountBoard: function (host, opts) {
      opts = opts || {};
      if (typeof host === "string") host = document.getElementById(host);
      if (!host) return null;
      injectCss();
      boardCloseFn = (typeof opts.onClose === "function") ? opts.onClose : null;
      host.innerHTML = '<div class="vmh"><div class="vmh-wrap"></div></div>';
      elWrap = host.querySelector(".vmh-wrap");
      focusBoard = { code: String(opts.code || ""), name: String(opts.name || ""), src: "em" };
      sectorType = opts.type === "concept" ? "concept" : "industry";
      heatView = "stocks";
      stockHeatCache = null;
      elWrap.innerHTML = '<div class="vmh-loading">加载个股热力图中…</div>';
      refreshStockHeatmap();
      return api;
    }
  };
  window.VibeMarketHeat = api;

  /* ---------------------------- 自动挂载 ---------------------------- */
  function autoMount() {
    var host = document.getElementById("marketHeat") || document.querySelector("[data-market-heat]");
    if (host) mount(host);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoMount);
  else autoMount();
})();
