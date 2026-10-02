/*!
 * kline_indicator.js — 把用户提供的通达信「箱体操盘WM + 四合一副图」指标
 * 在浏览器端用纯 JS 忠实复算，并渲染成主图下方的独立副图 SVG。
 *
 * 设计要点：
 *  1) 零依赖；同时挂到 window.TDXIndicator 与 module.exports（便于 Node 单测）。
 *  2) 复刻通达信专有函数：MA/EMA/SMA/LLV/HHV/REF/CROSS/BARSLAST/
 *     BARSLASTCOUNT/COUNT/EXIST/ZIG/PEAKBARS/TROUGHBARS/BACKSET/
 *     BARSNEXT/REFX/FINANCE(7)/CAPITAL，并自带 DKX（多空线）算法。
 *  3) 原公式顶部的授权校验块（ZBXH/YMD/Y1..Y3/GQ0/GQ/GQ10）为死代码，
 *     已按用户要求整段删除；箱体画线由突破信号（DINGWEI/突破高点）驱动。
 *  4) FROMOPEN/CURRBARSCOUNT/MTIME/CTIME 为盘中实时函数，历史日K无对应值，
 *     已退化处理，仅影响 V预估 这类实时量比，不影响信号。
 */

(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else { root.TDXIndicator = factory(); }
})(typeof self !== 'undefined' ? self : this, function () {

  'use strict';

  /* ───────────────────────── 通达信颜色 → hex ───────────────────────── */
  var C = {
    COLOR2F2F5F: '#2F2F5F', COLOR40FF00: '#40FF00', COLOR8F00FF: '#8F00FF',
    COLORYELLOW: '#FFD400', COLORFFA400: '#FFA400', COLOR077807: '#077807',
    COLORWHITE: '#FFFFFF', COLORMAGENTA: '#FF00FF', COLORCYAN: '#00E5FF',
    COLORGRAY: '#8A93B0', COLORGREEN: '#00E676', COLORFF0080: '#FF0080',
    COLOR9AFF02: '#9AFF02', COLORRED: '#FF5252', COLORLIMAGENTA: '#FF66FF',
    COLOR02F78E: '#02F78E', COLORFF2D2D: '#FF2D2D', COLORF00FF0: '#F00FF0',
    COLORBLUE: '#3D7BFF', COLORDK: '#9AA4C4'
  };

  /* ───────────────────────── 数组工具（返回整列数组） ───────────────────────── */
  function nul(n) { var a = new Array(n); for (var i = 0; i < n; i++) a[i] = null; return a; }
  function MA(a, n) { var o = nul(a.length), s = 0; for (var i = 0; i < a.length; i++) { s += (a[i] == null ? 0 : a[i]); if (i >= n) s -= (a[i - n] == null ? 0 : a[i - n]); o[i] = i >= n - 1 ? s / n : null; } return o; }
  function EMA(a, n) { var o = nul(a.length), k = 2 / (n + 1), p = null; for (var i = 0; i < a.length; i++) { var v = a[i] == null ? 0 : a[i]; p = (i === 0 || p == null) ? v : v * k + p * (1 - k); o[i] = p; } return o; }
  function SMA(a, n, m) { var o = nul(a.length), p = null; for (var i = 0; i < a.length; i++) { var v = a[i] == null ? 0 : a[i]; p = (i === 0 || p == null) ? v : (m * v + (n - m) * p) / n; o[i] = p; } return o; }
  function LLV(a, n) { var o = nul(a.length); for (var i = 0; i < a.length; i++) { var lo = Infinity; for (var j = Math.max(0, i - n + 1); j <= i; j++) if (a[j] != null && a[j] < lo) lo = a[j]; o[i] = isFinite(lo) ? lo : null; } return o; }
  function HHV(a, n) { var o = nul(a.length); for (var i = 0; i < a.length; i++) { var hi = -Infinity; for (var j = Math.max(0, i - n + 1); j <= i; j++) if (a[j] != null && a[j] > hi) hi = a[j]; o[i] = isFinite(hi) ? hi : null; } return o; }
  function REF(a, k) { var o = nul(a.length); for (var i = 0; i < a.length; i++) o[i] = (i >= k && a[i - k] != null) ? a[i - k] : null; return o; }
  function CROSS(a, b) { var o = nul(a.length); for (var i = 1; i < a.length; i++) o[i] = (a[i - 1] != null && b[i - 1] != null && a[i] != null && b[i] != null && a[i - 1] <= b[i - 1] && a[i] > b[i]); return o; }
  function BARSLAST(cond) { var o = nul(cond.length), last = -1; for (var i = 0; i < cond.length; i++) { if (cond[i]) { last = i; o[i] = 0; } else o[i] = last < 0 ? cond.length : i - last; } return o; }
  function BARSLASTCOUNT(cond) { var o = nul(cond.length), c = 0; for (var i = 0; i < cond.length; i++) { c = cond[i] ? c + 1 : 0; o[i] = c; } return o; }
  function COUNT_N(cond, n) { var o = nul(cond.length); for (var i = 0; i < cond.length; i++) { var c = 0; for (var j = Math.max(0, i - n + 1); j <= i; j++) if (cond[j]) c++; o[i] = c; } return o; }
  function EXIST_N(cond, n) { var o = nul(cond.length); for (var i = 0; i < cond.length; i++) { var f = false; for (var j = Math.max(0, i - n + 1); j <= i; j++) if (cond[j]) { f = true; break; } o[i] = f; } return o; }
  function BACKSET(cond, n) { var o = nul(cond.length); for (var i = 0; i < cond.length; i++) { var f = false; for (var j = Math.max(0, i - n + 1); j <= i; j++) if (cond[j]) { f = true; break; } o[i] = f; } return o; }
  function BARSNEXT(cond) { var o = nul(cond.length), nxt = -1; for (var i = cond.length - 1; i >= 0; i--) { if (cond[i]) { nxt = i; o[i] = 0; } else o[i] = nxt < 0 ? cond.length : nxt - i; } return o; }
  function REFX(x, k) { if (x == null || typeof x === 'number') return x; var o = nul(x.length); for (var i = 0; i < x.length; i++) o[i] = (i + k < x.length && x[i + k] != null) ? x[i + k] : null; return o; }
  function AND() { var arr = arguments, o = nul(arr[0].length); for (var i = 0; i < o.length; i++) { var t = true; for (var a = 0; a < arr.length; a++) if (!arr[a][i]) { t = false; break; } o[i] = t; } return o; }
  function OR() { var arr = arguments, o = nul(arr[0].length); for (var i = 0; i < o.length; i++) { var t = false; for (var a = 0; a < arr.length; a++) if (arr[a][i]) { t = true; break; } o[i] = t; } return o; }
  function NOT(a) { var o = nul(a.length); for (var i = 0; i < a.length; i++) o[i] = !a[i]; return o; }
  function GT(a, b) { var o = nul(a.length); for (var i = 0; i < a.length; i++) o[i] = (a[i] != null && b[i] != null && a[i] > b[i]); return o; }
  function LT(a, b) { var o = nul(a.length); for (var i = 0; i < a.length; i++) o[i] = (a[i] != null && b[i] != null && a[i] < b[i]); return o; }
  function mapGT(a, v) { var o = nul(a.length); for (var i = 0; i < a.length; i++) o[i] = (a[i] != null && a[i] > v); return o; }
  function mapLT(a, v) { var o = nul(a.length); for (var i = 0; i < a.length; i++) o[i] = (a[i] != null && a[i] < v); return o; }
  function constArr(v, n) { var o = nul(n); for (var i = 0; i < n; i++) o[i] = v; return o; }

  /* ───────────────────────── ZIG 之字转向 ───────────────────────── */
  function zigPivots(v, pct) {
    var n = v.length, piv = [{ i: 0, p: v[0], d: 0 }], dir = 0, extI = 0, extP = v[0];
    for (var i = 1; i < n; i++) {
      var p = v[i];
      if (dir >= 0) { if (p > extP) { extP = p; extI = i; } else if (p <= extP * (1 - pct)) { if (piv[piv.length - 1].i !== extI) piv.push({ i: extI, p: extP, d: 1 }); dir = -1; extP = p; extI = i; } }
      else { if (p < extP) { extP = p; extI = i; } else if (p >= extP * (1 + pct)) { if (piv[piv.length - 1].i !== extI) piv.push({ i: extI, p: extP, d: -1 }); dir = 1; extP = p; extI = i; } }
    }
    if (piv[piv.length - 1].i !== extI) piv.push({ i: extI, p: extP, d: dir });
    return piv;
  }
  function ZIG(v, pct) {
    var n = v.length, out = nul(n); if (n === 0) return out;
    var piv = zigPivots(v, pct);
    for (var k = 0; k < piv.length; k++) {
      var a = piv[k], b = piv[k + 1]; if (!b) { out[a.i] = a.p; break; }
      var seg = b.i - a.i; for (var j = a.i; j <= b.i; j++) out[j] = a.p + (b.p - a.p) * (j - a.i) / seg;
    }
    return out;
  }
  function peakBars(v, pct) {
    var n = v.length, out = nul(n), piv = zigPivots(v, pct), last = -1;
    for (var i = 0; i < n; i++) { for (var k = 0; k < piv.length; k++) if (piv[k].i <= i && piv[k].d === 1) last = piv[k].i; out[i] = last < 0 ? n : i - last; }
    return out;
  }
  function troughBars(v, pct) {
    var n = v.length, out = nul(n), piv = zigPivots(v, pct), last = -1;
    for (var i = 0; i < n; i++) { for (var k = 0; k < piv.length; k++) if (piv[k].i <= i && piv[k].d === -1) last = piv[k].i; out[i] = last < 0 ? n : i - last; }
    return out;
  }

  /* ───────────────────────── DKX 多空线（自带） ─────────────────────────
   * X = MA((2*C+H+L)/4, N); DKX = SMA(X, N, 1); MADKX = MA(DKX, M2)
   */
  function DKX(OHLC, N, M2) {
    N = N || 10; M2 = M2 || 10; var base = new Array(OHLC.length);
    for (var i = 0; i < OHLC.length; i++) base[i] = (2 * OHLC[i].c + OHLC[i].h + OHLC[i].l) / 4;
    return { dkx: SMA(MA(base, N), N, 1), mad: MA(SMA(MA(base, N), N, 1), M2) };
  }

  /* ───────────────────────── 主计算：逐行翻译用户公式 ───────────────────────── */
  function compute(bars, toInfo) {
    var n = bars.length, O = [], H = [], L = [], Cl = [], V = [];
    for (var i = 0; i < n; i++) { O[i] = bars[i].o; H[i] = bars[i].h; L[i] = bars[i].l; Cl[i] = bars[i].c; V[i] = bars[i].v; }

    var CAPITAL = (toInfo && toInfo.shares) ? toInfo.shares : null;
    var factor = (toInfo && toInfo.factor) || 1;
    var TOok = CAPITAL && CAPITAL > 0;
    var TO = nul(n);
    for (i = 0; i < n; i++) if (TOok) TO[i] = V[i] * factor / CAPITAL * 100;

    var MA5V = MA(V, 5), MA5V_prev = REF(MA5V, 1), LB = nul(n);
    for (i = 0; i < n; i++) LB[i] = (MA5V_prev[i] && MA5V_prev[i] > 0) ? V[i] / MA5V_prev[i] : null;

    // 顶部装饰柱
    var ZIG15 = ZIG(Cl, 0.15), ZIG10 = ZIG(Cl, 0.10);
    var topCond = AND(GT(ZIG15, REF(ZIG15, 1)), GT(ZIG10, REF(ZIG10, 1)), GT(ZIG10, MA(ZIG10, 2)));

    // CCI(25)
    var TP = new Array(n), CCI = nul(n);
    for (i = 0; i < n; i++) TP[i] = (H[i] + L[i] + Cl[i]) / 3;
    var MA20TP = MA(TP, 20), MD = nul(n);
    for (i = 0; i < n; i++) {
      if (i < 19 || MA20TP[i] == null) continue;
      var s = 0; for (var j = i - 19; j <= i; j++) s += Math.abs(TP[j] - MA20TP[i]);
      MD[i] = s / 20; CCI[i] = (MD[i] > 0) ? (TP[i] - MA20TP[i]) / (0.015 * MD[i]) : 0;
    }

    // 强弱线 EMA(100*(C-LLV(LOW,30))/(HHV(H,30)-LLV(LOW,30)),3)
    var llvL30 = LLV(L, 30), hhvH30 = HHV(H, 30), rng30 = nul(n);
    for (i = 0; i < n; i++) rng30[i] = (hhvH30[i] != null && llvL30[i] != null && hhvH30[i] > llvL30[i]) ? 100 * (Cl[i] - llvL30[i]) / (hhvH30[i] - llvL30[i]) : null;
    var QX = EMA(rng30, 3), strongCond = GT(QX, 65);

    // 箱体（突破信号驱动，已删除授权块）
    var XTN = 40, boxRatio = 18;
    var hhvC40 = HHV(Cl, XTN), llvC40 = LLV(Cl, XTN);
    var boxIn = nul(n);
    for (i = 0; i < n; i++) boxIn[i] = (hhvC40[i] != null && llvC40[i] != null && hhvC40[i] / llvC40[i] < 1 + boxRatio / 100);
    // 突破高点：收盘价创 40 根新高且 40 根内首次
    var breakHi = nul(n);
    for (i = 0; i < n; i++) {
      if (i < 1 || hhvC40[i] == null) continue;
      if (Cl[i] / hhvC40[i] <= 1.02) continue;
      var cnt = 0; for (var k = Math.max(0, i - (XTN - 2)); k <= i - 1; k++) if (breakHi[k]) cnt++;
      breakHi[i] = (cnt === 0);
    }
    var breakHiN = BARSLAST(breakHi);
    // 箱顶M/箱底M 保持公式字面值 -2/-3（通达信实际显示就是贴底的细线；
    // 源码注释里的 REF(HHV(C,40),…) 真实算法在原公式中已被注释、未启用）
    var boxTopM = constArr(-2, n), boxBotM = constArr(-3, n);
    var dingwei = BACKSET(breakHi, 40);
    var boxStart = nul(n);
    for (i = 1; i < n; i++) boxStart[i] = (dingwei[i] && !dingwei[i - 1]);

    // DKX + 放巨量
    var dk = DKX(bars, 10, 10), DK = dk.dkx, MADK = dk.mad;
    var MAVOL1 = MA(V, 5);
    var 放巨 = nul(n);
    for (i = 0; i < n; i++) {
      放巨[i] = (TO[i] != null && MA(TO, 5)[i] != null && MA(TO, 5)[i] > 0 && (TO[i] / MA(TO, 5)[i] > 2) &&
        (V[i] / (MAVOL1[i] || 1) > 1.5) && (Cl[i] > DK[i]) && (Cl[i] > (i > 0 ? Cl[i - 1] : Cl[i]))) ? true : false;
    }
    var XTTJ3 = COUNT_N(放巨, XTN); var XTTJ3b = nul(n);
    for (i = 0; i < n; i++) XTTJ3b[i] = XTTJ3[i] >= 2;

    // 四合一：MACD / 量比 / 换手 / RSI
    var e12 = EMA(Cl, 12), e26 = EMA(Cl, 26), DIF = nul(n); for (i = 0; i < n; i++) DIF[i] = e12[i] - e26[i];
    var DEA = EMA(DIF, 9), MACD = nul(n); for (i = 0; i < n; i++) MACD[i] = (DIF[i] != null && DEA[i] != null) ? (DIF[i] - DEA[i]) * 2 : null;
    var RSI14 = nul(n);
    for (i = 0; i < n; i++) {
      if (i < 14) continue; var gain = 0, loss = 0;
      for (var k = i - 13; k <= i; k++) { var ch = Cl[k] - (k > 0 ? Cl[k - 1] : Cl[k]); if (ch > 0) gain += ch; else loss -= ch; }
      var rs = loss > 0 ? gain / loss : (gain > 0 ? 999 : 1); RSI14[i] = 100 - 100 / (1 + rs);
    }
    var RSI线 = nul(n); for (i = 0; i < n; i++) RSI线[i] = RSI14[i] != null ? RSI14[i] / 10 * 5 : null;

    // 买卖点（ZIG(3,5)）
    var ZIG5 = ZIG(Cl, 0.05), buyPt = nul(n), sellPt = nul(n);
    for (i = 3; i < n; i++) {
      buyPt[i] = (ZIG5[i] != null && ZIG5[i - 1] != null && ZIG5[i - 2] != null && ZIG5[i - 3] != null &&
        ZIG5[i] > ZIG5[i - 1] && ZIG5[i - 1] <= ZIG5[i - 2] && ZIG5[i - 2] <= ZIG5[i - 3]);
      sellPt[i] = (ZIG5[i] != null && ZIG5[i - 1] != null && ZIG5[i - 2] != null && ZIG5[i - 3] != null &&
        ZIG5[i] < ZIG5[i - 1] && ZIG5[i - 1] >= ZIG5[i - 2] && ZIG5[i - 2] >= ZIG5[i - 3]);
    }

    // 高顶出货
    var headBars = peakBars(Cl, 0.15), V10 = nul(n); for (i = 0; i < n; i++) V10[i] = headBars[i] < 10;
    var 头部 = nul(n); for (i = 0; i < n; i++) 头部[i] = V10[i] ? 30 : 0;
    var 头部Signal = GT(头部, REF(头部, 1));

    // 注意牛回头（CROSS(82, GUP7)）
    var GUP7 = nul(n);
    for (i = 0; i < n; i++) {
      if (i < 6) continue; var mx = 0, ls = 0;
      for (var k = i - 5; k <= i; k++) { var ch = Cl[k] - (k > 0 ? Cl[k - 1] : Cl[k]); if (ch > 0) mx += ch; else ls -= ch; }
      var r2 = ls > 0 ? mx / ls : (mx > 0 ? 999 : 1); GUP7[i] = 100 - 100 / (1 + r2);
    }
    var 牛回头 = nul(n);
    for (i = 1; i < n; i++) 牛回头[i] = (GUP7[i - 1] != null && GUP7[i - 1] >= 82 && GUP7[i] != null && GUP7[i] < 82);

    // 机构进场 / 清仓
    var 买线J = ZIG(Cl, 0.10), 卖线J = MA(买线J, 2);
    var BUL = CROSS(买线J, 卖线J), SEL = CROSS(卖线J, 买线J);
    // IF(买线J>卖线J,15,DRAWNULL),COLORLIMAGENTA,LINETHICK5 —— 多头持仓期在 y=15 画粗紫线
    var duotouLine = nul(n);
    for (i = 0; i < n; i++) if (买线J[i] != null && 卖线J[i] != null && 买线J[i] > 卖线J[i]) duotouLine[i] = 15;

    // 三阳
    var 基准 = nul(n);
    for (i = 3; i < n; i++) 基准[i] = (Cl[i] > Cl[i - 1] && Cl[i - 1] > Cl[i - 2] && Cl[i - 2] > Cl[i - 3]);

    // 最后逃亡（生命指数 EMA(100*(C-LLV34)/(HHV34-LLV34),3)/4）
    var lo34 = LLV(L, 34), hi34 = HHV(H, 34), raw = nul(n);
    for (i = 0; i < n; i++) raw[i] = (hi34[i] != null && lo34[i] != null && hi34[i] > lo34[i]) ? 100 * (Cl[i] - lo34[i]) / (hi34[i] - lo34[i]) : null;
    var 生命 = EMA(raw, 3); for (i = 0; i < n; i++) 生命[i] = (生命[i] != null) ? 生命[i] / 4 : null;
    var 逃亡 = nul(n);
    for (i = 1; i < n; i++) 逃亡[i] = (生命[i - 1] != null && 生命[i - 1] >= 10 && 生命[i] != null && 生命[i] < 10);

    // 开天之剑
    var trBars = troughBars(Cl, 0.15), VARKF = nul(n); for (i = 0; i < n; i++) VARKF[i] = trBars[i] < 4;
    var 开天 = nul(n);
    for (i = 0; i < n; i++) {
      if (!VARKF[i]) continue; var recent = false; for (var k = Math.max(0, i - 2); k <= i - 1; k++) if (VARKF[k]) { recent = true; break; }
      开天[i] = !recent;
    }

    // 连续涨跌收盘计数（1-9 文字）
    function chainDown(idx) { var c = 0; for (var q = idx; q >= 0; q--) { if (Cl[q] < Cl[Math.max(0, q - 4)]) c++; else break; } return c; }
    function chainUp(idx) { var c = 0; for (var q = idx; q >= 0; q--) { if (Cl[q] > Cl[Math.max(0, q - 4)]) c++; else break; } return c; }
    var TJnum = nul(n), DJnum = nul(n);
    for (i = 0; i < n; i++) { TJnum[i] = chainDown(i); DJnum[i] = chainUp(i); }

    /* ───────── 收集绘图原语 ───────── */
    var draws = [], icons = [], texts = [];
    function addStick(cond, y1, y2, w, empty, color, scale) { draws.push({ kind: 'stick', cond: cond, y1: y1, y2: y2, w: w, empty: empty, color: color, scale: scale || null }); }
    function addLine(series, color, width, dot) { draws.push({ kind: 'line', series: series, color: color, width: width || 1.3, dot: !!dot }); }
    function addDrawLine(c1, y1, c2, y2, color, width) { draws.push({ kind: 'drawline', c1: c1, y1: y1, c2: c2, y2: y2, color: color, width: width || 2 }); }

    // 顶部装饰柱
    addStick(topCond, 24.5, 15.5, 8, 0, C.COLOR2F2F5F);
    addStick(AND(GT(CCI, 0), topCond), 24.5, 15.5, 8, 0, C.COLOR8F00FF);
    addStick(strongCond, 24.5, 24, 8, 0, C.COLORYELLOW);

    // 箱体带（突破信号驱动）
    draws.push({ kind: 'box', start: boxStart, end: breakHi, top: boxTopM, bot: boxBotM, color: C.COLORFFA400, color2: C.COLOR077807, hasX: XTTJ3b });
    addDrawLine(boxStart, boxTopM, breakHi, boxTopM, C.COLORFFA400, 3);
    addDrawLine(boxStart, boxBotM, breakHi, boxBotM, C.COLORFFA400, 3);
    addDrawLine(AND(boxStart, XTTJ3b), boxTopM, breakHi, boxTopM, C.COLOR077807, 3);
    addDrawLine(AND(boxStart, XTTJ3b), boxBotM, breakHi, boxBotM, C.COLOR077807, 3);

    // 四合一 MACD 柱（scale=10）
    var macdUp = GT(MACD, 0), macdUpPrev = GT(MACD, REF(MACD, 1));
    addStick(AND(macdUp, macdUpPrev), 0, null, 5, 1, C.COLORGRAY, 10);
    addStick(AND(macdUp, NOT(macdUpPrev)), 0, null, 0.3, 0, C.COLORCYAN, 10);
    addStick(AND(NOT(macdUp), macdUpPrev), 0, null, 0.3, 0, C.COLORGREEN, 10);
    addStick(AND(NOT(macdUp), NOT(macdUpPrev)), 0, null, 2, 0, C.COLOR8F00FF, 10);

    // 曲线
    addLine(LB, C.COLORWHITE, 2, false);
    addLine(TO, C.COLORYELLOW, 2, false);
    addLine(RSI线, C.COLORCYAN, 1.3, true);
    var zUp = nul(n), zDn = nul(n);
    for (i = 0; i < n; i++) { if (ZIG15[i] != null && ZIG15[i] > (i > 0 ? ZIG15[i - 1] : ZIG15[i])) zUp[i] = RSI线[i]; else if (ZIG15[i] != null) zDn[i] = RSI线[i]; }
    addLine(zUp, C.COLORFF0080, 5, false);
    addLine(zDn, C.COLOR9AFF02, 5, false);
    addLine(duotouLine, C.COLORLIMAGENTA, 5, false);

    // CC1:=CCI(25) 为 := 隐藏赋值，通达信不绘制 CCI 柱；CCI 仅参与顶部紫柱条件

    // 参考线（标签只保留 超买/超卖，避免右侧标签互相重叠）
    draws.push({ kind: 'ref', y: 1, color: C.COLORGRAY, label: '' });
    draws.push({ kind: 'ref', y: 0, color: C.COLORWHITE, label: '' });
    draws.push({ kind: 'ref', y: 35, color: C.COLORRED, label: '超买70' });
    draws.push({ kind: 'ref', y: 15, color: C.COLORGREEN, label: '超卖30' });
    draws.push({ kind: 'ref', y: 25, color: C.COLORGRAY, label: '' });

    // 换手>5 且 量比>2 高亮 + 择机入场
    var pickCond = AND(mapGT(TO, 5), mapGT(LB, 2));
    addStick(pickCond, 0, null, 5, 0, C.COLORBLUE, 11);
    for (i = 0; i < n; i++) if (pickCond[i]) texts.push({ i: i, y: 13, str: '择机入场', color: C.COLORYELLOW });

    // 买卖点图标
    for (i = 0; i < n; i++) { if (buyPt[i]) icons.push({ i: i, y: 28, color: C.COLOR9AFF02 }); if (sellPt[i]) icons.push({ i: i, y: 28, color: C.COLORFF2D2D }); }
    var rsiCross = CROSS(RSI线, constArr(35, n));
    for (i = 0; i < n; i++) if (rsiCross[i]) icons.push({ i: i, y: 35, color: C.COLORYELLOW });

    // 高顶出货
    for (i = 0; i < n; i++) if (头部Signal[i]) { var yy = (RSI线[i] != null) ? RSI线[i] : 35; icons.push({ i: i, y: yy, color: C.COLORGREEN }); texts.push({ i: i, y: yy, str: '高顶出货 60F', color: C.COLORGREEN }); }
    // 注意牛回头
    for (i = 0; i < n; i++) if (牛回头[i]) texts.push({ i: i, y: 32, str: '牛回头', color: C.COLORYELLOW });
    // 机构进场 / 清仓
    for (i = 0; i < n; i++) {
      if (BUL[i]) { texts.push({ i: i, y: 16, str: '▲机构进场', color: C.COLORYELLOW }); icons.push({ i: i, y: 15, color: C.COLORLIMAGENTA }); }
      if (SEL[i]) texts.push({ i: i, y: 14, str: '▲机构清仓', color: C.COLORGREEN });
    }
    // 三阳（原公式两条 DRAWTEXT：y=12 画「三」、y=10 画「阳」，上下排列）
    for (i = 0; i < n; i++) if (基准[i]) {
      texts.push({ i: i, y: 12, str: '三', color: C.COLORYELLOW });
      texts.push({ i: i, y: 10, str: '阳', color: C.COLORYELLOW });
    }
    // 最后逃亡
    for (i = 0; i < n; i++) if (逃亡[i]) texts.push({ i: i, y: 6, str: '最后逃亡', color: C.COLORGREEN });
    // 开天之剑
    for (i = 0; i < n; i++) if (开天[i]) texts.push({ i: i, y: 20, str: '←开天★之剑', color: C.COLORRED });
    // 连续涨跌数字 1-9
    for (i = 0; i < n; i++) {
      if (TJnum[i] >= 1 && TJnum[i] <= 14) texts.push({ i: i, y: 39, str: String(Math.min(TJnum[i], 9)), color: TJnum[i] >= 9 ? C.COLORWHITE : C.COLORGREEN });
      if (DJnum[i] >= 1 && DJnum[i] <= 14) texts.push({ i: i, y: 39, str: String(Math.min(DJnum[i], 9)), color: DJnum[i] >= 9 ? C.COLORYELLOW : C.COLORFF2D2D });
    }

    return {
      n: n, bars: bars, draws: draws, icons: icons, texts: texts,
      series: { CCI: CCI, MACD: MACD, LB: LB, TO: TO, RSI: RSI14, ZIG15: ZIG15, ZIG10: ZIG10, DKX: DK, MADKX: MADK, DIF: DIF, DEA: DEA },
      meta: { hasTO: TOok }
    };
  }

  /* ───────────────────────── 副图 SVG 渲染 ───────────────────────── */
  var W = 640, PL = 6, PR = 58, Hsub = 250, PTs = 12, PBs = Hsub - 22;
  function render(ind) {
    if (!ind || !ind.n || ind.n < 2) return '';
    var n = ind.n, slot = (W - PL - PR) / n;
    function px(j) { return PL + slot * j + slot / 2; }
    var vals = [];
    function pushV(v) { if (v != null && isFinite(v)) vals.push(v); }
    [0, 1, 15, 25, 35, 15.5, 24.5, 39, 28, 6, 10, 12, 14, 16, 20, 32].forEach(pushV);
    ind.draws.forEach(function (d) {
      if (d.kind === 'ref') pushV(d.y);
      else if (d.kind === 'stick') { pushV(d.y1); if (d.y2) pushV(d.y2); }
      else if (d.kind === 'line' && d.series) d.series.forEach(pushV);
      else if (d.kind === 'drawline') { (Array.isArray(d.y1) ? d.y1 : [d.y1]).forEach(pushV); (Array.isArray(d.y2) ? d.y2 : [d.y2]).forEach(pushV); }
      else if (d.kind === 'box') { if (d.top) d.top.forEach(pushV); if (d.bot) d.bot.forEach(pushV); }
    });
    ind.icons.forEach(function (ic) { pushV(ic.y); });
    ind.texts.forEach(function (t) { if (t.str) pushV(t.y); });
    var hi = Math.max.apply(null, vals), lo = Math.min.apply(null, vals);
    if (!isFinite(hi) || !isFinite(lo) || hi === lo) { hi = 50; lo = -10; }
    var pad = (hi - lo) * 0.08 || 2; hi += pad; lo -= pad;
    function py(v) { return PTs + (hi - v) / (hi - lo) * (PBs - PTs); }

    var s = '<svg viewBox="0 0 ' + W + ' ' + Hsub + '" xmlns="http://www.w3.org/2000/svg" class="kl-sub-svg">';
    s += '<rect x="0" y="0" width="' + W + '" height="' + Hsub + '" fill="#0a0f1d"/>';
    ind.draws.forEach(function (d) {
      if (d.kind !== 'ref') return; var y = py(d.y);
      s += '<line x1="' + PL + '" y1="' + y.toFixed(1) + '" x2="' + (W - PR) + '" y2="' + y.toFixed(1) + '" stroke="' + d.color + '" stroke-width="1" stroke-dasharray="2,3" opacity="0.5"/>';
      if (d.label) s += '<text x="' + (W - PR + 4) + '" y="' + (y + 3).toFixed(1) + '" fill="' + d.color + '" font-size="9" opacity="0.8">' + d.label + '</text>';
    });
    ind.draws.forEach(function (d) {
      if (d.kind !== 'stick') return;
      for (var i = 0; i < n; i++) {
        if (!d.cond[i]) continue;
        var y1 = d.y1 == null ? 0 : d.y1, y2 = d.y2;
        if (y2 == null && d.scale) y2 = (ind.series.MACD ? (ind.series.MACD[i] != null ? ind.series.MACD[i] * d.scale : 0) : 0);
        if (y2 == null) y2 = 0;
        var top = Math.min(py(y1), py(y2)), h = Math.max(1, Math.abs(py(y1) - py(y2)));
        var w = Math.max(1, slot * (d.w && d.w > 1 ? 0.7 : 0.5));
        s += '<rect x="' + (px(i) - w / 2).toFixed(1) + '" y="' + top.toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + h.toFixed(1) + '" fill="' + d.color + '" opacity="' + (d.isCci ? 0.55 : 0.85) + '"/>';
      }
    });
    ind.draws.forEach(function (d) {
      if (d.kind !== 'box') return; var start = -1;
      for (var i = 0; i < n; i++) {
        if (d.start[i] && start < 0) start = i;
        if (d.end[i] && start >= 0) {
          var x1 = px(start), x2 = px(i);
          var yt = (d.top[i] != null) ? py(d.top[i]) : py(20), yb = (d.bot[i] != null) ? py(d.bot[i]) : py(10);
          var topp = Math.min(yt, yb), hh = Math.max(2, Math.abs(yt - yb));
          s += '<rect x="' + x1.toFixed(1) + '" y="' + topp.toFixed(1) + '" width="' + Math.max(1, x2 - x1).toFixed(1) + '" height="' + hh.toFixed(1) + '" fill="' + d.color + '" opacity="0.12"/>';
          s += '<line x1="' + x1.toFixed(1) + '" y1="' + yt.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + yt.toFixed(1) + '" stroke="' + d.color + '" stroke-width="2"/>';
          s += '<line x1="' + x1.toFixed(1) + '" y1="' + yb.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + yb.toFixed(1) + '" stroke="' + d.color + '" stroke-width="2"/>';
          if (d.hasX[i]) { s += '<line x1="' + x1.toFixed(1) + '" y1="' + yt.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + yt.toFixed(1) + '" stroke="' + d.color2 + '" stroke-width="2"/>'; s += '<line x1="' + x1.toFixed(1) + '" y1="' + yb.toFixed(1) + '" x2="' + x2.toFixed(1) + '" y2="' + yb.toFixed(1) + '" stroke="' + d.color2 + '" stroke-width="2"/>'; }
          start = -1;
        }
      }
    });
    ind.draws.forEach(function (d) {
      if (d.kind !== 'drawline') return; var i2 = -1, i1 = -1;
      for (var i = n - 1; i >= 0; i--) { if (d.c2[i]) { i2 = i; break; } }
      for (var j = i2; j >= 0; j--) { if (d.c1[j]) { i1 = j; break; } }
      if (i1 >= 0 && i2 >= 0) {
        var yv1 = Array.isArray(d.y1) ? d.y1[i1] : d.y1, yv2 = Array.isArray(d.y2) ? d.y2[i2] : d.y2;
        if (yv1 != null && yv2 != null) s += '<line x1="' + px(i1).toFixed(1) + '" y1="' + py(yv1).toFixed(1) + '" x2="' + px(i2).toFixed(1) + '" y2="' + py(yv2).toFixed(1) + '" stroke="' + d.color + '" stroke-width="' + (d.width || 2) + '"/>';
      }
    });
    ind.draws.forEach(function (d) {
      if (d.kind !== 'line' || !d.series) return; var pts = [];
      for (var i = 0; i < n; i++) if (d.series[i] != null) pts.push(px(i).toFixed(1) + ',' + py(d.series[i]).toFixed(1));
      if (pts.length > 1) s += '<polyline points="' + pts.join(' ') + '" fill="none" stroke="' + d.color + '" stroke-width="' + d.width + '"' + (d.dot ? ' stroke-dasharray="1,3"' : '') + ' opacity="0.95"/>';
    });
    ind.icons.forEach(function (ic) { if (ic.i < 0 || ic.i >= n) return; s += '<circle cx="' + px(ic.i).toFixed(1) + '" cy="' + py(ic.y).toFixed(1) + '" r="3.2" fill="' + ic.color + '"/>'; });
    // 文字信号美化：同一信号连续多日只画首次；数字与文字居中、分级字号
    var lastByKey = {};
    var sortedTexts = ind.texts.slice().sort(function (a, b) { return a.i - b.i; });
    sortedTexts.forEach(function (t) {
      if (!t.str || t.i < 0 || t.i >= n) return;
      var key = t.str + '|' + t.color;
      if (lastByKey[key] === t.i - 1) { lastByKey[key] = t.i; return; }
      lastByKey[key] = t.i;
      var isNum = /^\d$/.test(t.str);
      s += '<text x="' + px(t.i).toFixed(1) + '" y="' + py(t.y).toFixed(1) + '" fill="' + t.color + '" font-size="' + (isNum ? 7 : 9) + '" text-anchor="middle"' + (isNum ? ' opacity="0.9"' : '') + '>' + escapeXml(t.str) + '</text>';
    });
    for (var i = 0; i < n; i++) if (i === 0 || i % Math.ceil(n / 6) === 0 || i === n - 1) s += '<text x="' + px(i).toFixed(1) + '" y="' + (Hsub - 5) + '" fill="#565f80" font-size="9" text-anchor="middle">' + String(ind.bars[i].d).slice(5) + '</text>';
    s += '</svg>';
    s += legendTable();
    return s;
  }
  /* 信号说明表：两列配色图例，直接内联样式、不依赖外部 CSS */
  function legendTable() {
    var items = [
      ['##FF0080', 'RSI 强势段（ZIG上行）', '##8F00FF', '上行趋势 + CCI>0（紫柱）'],
      ['##9AFF02', 'RSI 弱势段（ZIG下行）', '##2F2F5F', '上行趋势段（深蓝柱）'],
      ['##3D7BFF', '换手>5 且 量比>2 → 择机入场', '##FFD400', '强势区（强度>65，黄短柱）'],
      ['##FF66FF', '▲ 多头持仓线（紫粗线）', '##00E676', '▲机构清仓 · 最后逃亡（绿）'],
      ['##FFD400', '▲机构进场 · 牛回头', '##FF5252', '超买70 / 超卖30 参考线'],
      ['##FF00FF', '←开天★之剑（底部反转）', '##8A93B0', '白线=量比 · 黄线=换手率']
    ];
    var h = '<table class="kl-sub-legend" style="width:100%;border-collapse:collapse;font-size:11px;color:#c7cee4;margin-top:6px;background:#101728;border:1px solid #1e2a44;border-radius:6px;overflow:hidden">';
    for (var r = 0; r < items.length; r++) {
      h += '<tr style="' + (r % 2 ? 'background:#0d1420;' : '') + '">';
      for (var c = 0; c < 4; c += 2) {
        h += '<td style="padding:4px 8px;white-space:nowrap;width:14px"><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:' + items[r][c] + ';vertical-align:middle"></span></td>';
        h += '<td style="padding:4px 8px 4px 2px;width:50%">' + items[r][c + 1] + '</td>';
      }
      h += '</tr>';
    }
    h += '</table>';
    return h;
  }
  function escapeXml(s) { return String(s).replace(/[<>&]/g, function (c) { return c === '<' ? '&lt;' : c === '>' ? '&gt;' : '&amp;'; }); }

  /* ───────────────────────── 裁剪到最近 k 根 ─────────────────────────
   * ZIG/PEAKBARS/TROUGHBARS 依赖完整历史，必须先用长历史 compute，
   * 再把结果裁到「图表实际显示」的那一段，保证与主图逐根对齐。
   */
  function trim(ind, k) {
    if (!ind || !ind.n || ind.n <= k) return ind;
    var off = ind.n - k;
    function sl(a) { return Array.isArray(a) ? a.slice(off) : a; }
    var draws = ind.draws.map(function (d) { var o = {}; for (var key in d) o[key] = sl(d[key]); return o; });
    var icons = ind.icons.filter(function (x) { return x.i >= off; })
      .map(function (x) { return { i: x.i - off, y: x.y, color: x.color }; });
    var texts = ind.texts.filter(function (x) { return x.i >= off; })
      .map(function (x) { return { i: x.i - off, y: x.y, str: x.str, color: x.color }; });
    var series = {}; for (var s in ind.series) series[s] = sl(ind.series[s]);
    return { n: k, bars: ind.bars.slice(off), draws: draws, icons: icons, texts: texts, series: series, meta: ind.meta };
  }

  return { compute: compute, render: render, trim: trim, _utils: { MA: MA, EMA: EMA, SMA: SMA, LLV: LLV, HHV: HHV, ZIG: ZIG, DKX: DKX, REF: REF, CROSS: CROSS } };
});
