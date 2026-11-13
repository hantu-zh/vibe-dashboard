# -*- coding: utf-8 -*-
"""news_v6_gen.py - news.html v6 整页生成器（函数化版）
由 _gen_news_v6.py 固化而来，供 news_update.py 每 15 分钟 Actions 调用：
    import news_v6_gen
    html = news_v6_gen.generate(news_data)   # news_data 含 items / update_time
核心特性(与线上 v6 一致): 三重去重 / 宏观军事地缘清洗 / 产业链穿透 / 情绪5级 /
综合评级 / 自选高亮 / 财报x资金共振面板(#efPanel) / K线弹窗 / 市场页签(market_emtab.js)。
本地自检: python news_v6_gen.py  ->  输出 news_v6.html（读 NEWS_SRC，默认 news_data.json）
"""
import json, re, html, datetime, os

ROOT = os.path.dirname(os.path.abspath(__file__))
def L(p): return json.load(open(os.path.join(ROOT, p), encoding='utf-8'))

# ---------------- 工具 ----------------
def norm(s):
    if not s: return ''
    return re.sub(r'[^\w\u4e00-\u9fff]', '', s).lower()

def sim(a, b):
    na, nb = norm(a), norm(b)
    if not na or not nb: return 0.0
    if na in nb or nb in na: return 1.0
    def bg(s): return set(s[i:i+2] for i in range(len(s)-1))
    A, B = bg(na), bg(nb)
    if not A or not B: return 0.0
    return len(A & B) / len(A | B)

def fmt_time(it):
    t = it.get('time')
    if t:
        try: return datetime.datetime.fromtimestamp(t/1000).strftime('%m-%d %H:%M')
        except: pass
    return it.get('time_str') or ''

def esc(s): return html.escape(s or '', quote=True)

# ---------------- 去重(三重保险: 同源10min预清 / 精确指纹 / 高相似) ----------------
WIN = 10 * 60 * 1000
# ---------------- 宏观 / 军事 / 地缘 ----------------
MACRO_KW = re.compile(r'(伊朗|以色列|法国|英国|美国|俄罗斯|乌克兰|俄乌|中东|朝鲜|日本|欧盟|北约|示威|渔船|核武|大选|美联储|地缘|关税|地震|海啸|普京|特朗普|拜登|罢工|骚乱|革命卫队|拦截|船只|军舰|霍尔木兹|海峡|导弹|袭击|空袭|爆炸|武装|冲突|制裁|关税战)')
CHINA_KW = re.compile(r'(中国|央行|发改委|国常会|证监会|工信部|财政部|国务院|统计局|政治局|沪深|创业板|科创板|A股|降准|降息|内地|国内|工信部)')
FOREIGN_KW = re.compile(r'(Meta|英伟达|Nvidia|苹果|Apple|特斯拉|Tesla|微软|Google|谷歌|亚马逊|OpenAI|三星|Samsung|英特尔|Intel|标普|惠誉|穆迪|美联储|非农|美股|华尔街)')
# 军事/地缘冲突关键词: 命中后抑制产业主题误标
MILITARY_KW = re.compile(r'(伊朗|以色列|黎巴嫩|加沙|真主党|哈马斯|以军|空袭|导弹|武装|冲突|交火|击落|击毙|袭击|扣押|拦截|革命卫队|霍尔木兹|海峡|军舰|渔船|核武|地缘|俄乌|乌克兰|俄罗斯|北约|朝鲜|演习|边境|美军|战乱|难民|战乱)')
RELSECT = ['黄金','原油','军工','有色','天然气','石油','煤炭']
def macro_of(text):
    if MACRO_KW.search(text):
        if CHINA_KW.search(text): return False, []
        rel = [m for m in RELSECT if m in text]
        return True, rel
    return False, []
# 地缘冲突 -> 细分行业受益(不再简单利空)
def geo_benefit(text):
    out = []
    if any(k in text for k in ['伊朗','霍尔木兹','海峡','油轮','原油','石油','黎巴嫩','以色列','加沙','真主党','哈马斯','俄乌','乌克兰','俄罗斯','朝鲜','美军','革命卫队','空袭','导弹']):
        if any(k in text for k in ['伊朗','霍尔木兹','海峡','油轮','原油','石油']):
            out += ['石油','黄金']
        else:
            out += ['黄金','军工']
        out.append('军工')
    return list(dict.fromkeys(out))

# ---------------- 情绪 5 级 ----------------
STRONG_POS = re.compile(r'(中标|大增|暴涨|增持|回购|签约|重大突破|超预期|扭亏为盈|历史新高|订单|大单|净利.*增|预增|翻倍|上调评级|扩产)')
WEAK_POS   = re.compile(r'(利好|增长|上调|合作|获批|新订单|受益|中标|签约|扩张|加码|松绑|扶持)')
STRONG_NEG = re.compile(r'(退市|暴雷|处罚|立案|大幅下滑|亏损扩大|减持|暂停|查封|风险警示|退市风险|造假|违规|暴跌|黑天鹅)')
WEAK_NEG   = re.compile(r'(下滑|下降|承压|下调|不及预期|缩水|回落|利空|预警)')
SENT = {
    'strong_pos': ('强利好', '#e53935'),
    'weak_pos':   ('弱利好', '#FFC107'),
    'strong_neg': ('强利空', '#0078FF'),
    'weak_neg':   ('弱利空', '#2ecc71'),
    'neutral':    ('中性',   '#5a6472'),
    'geo':        ('地缘',   '#5a6472'),
}
# 预期判断：仅命中"业绩超/不及预期"类预期差关键词才打标（不再按强情绪机械打标）
EXPECT_GAP = re.compile(r'超预期|不及预期|高于预期|低于预期|超出预期|大超预期|不如预期|逊于预期|远超预期')
def sentiment_of(text, importance):
    sp, wp = bool(STRONG_POS.search(text)), bool(WEAK_POS.search(text))
    sn, wn = bool(STRONG_NEG.search(text)), bool(WEAK_NEG.search(text))
    if sp and not sn: return 'strong_pos'
    if sn and not sp: return 'strong_neg'
    if wp and not wn: return 'weak_pos'
    if wn and not wp: return 'weak_neg'
    return 'neutral'

# ---------------- 标题/正文冗余裁剪 ----------------
def trim_body(title, text):
    if not text: return ''
    if text.strip() == (title or '').strip(): return ''
    tn = re.sub(r'[【】()（）“”"\'，。、：:；;\s]', '', title or '')
    xn = re.sub(r'[【】()（）“”"\'，。、：:；;\s]', '', text)
    if tn and len(tn) >= 8 and xn.startswith(tn[:14]):
        body = text[len(title):].lstrip('，。、：:；; \n')
        return body
    return text

# ---------------- 产业链联动 ----------------
THEME_MAP = {
    '英伟达': ['算力','光模块','液冷','CPO','PCB','数据中心'],
    '光模块': ['光模块','CPO'], '液冷': ['液冷','数据中心','算力'],
    '算力': ['算力','数据中心','AI'], 'AI': ['AI','算力','数字经济'],
    '人工智能': ['AI','算力','数字经济'], '华为': ['华为概念','昇腾','鸿蒙'],
    '机器人': ['机器人','减速器','人形机器人'], '低空': ['低空经济'],
    '半导体': ['半导体','芯片'], '芯片': ['芯片','半导体'],
    '光伏': ['光伏'], '储能': ['储能'], '新能源': ['新能源车','光伏'],
    '电动车': ['新能源车'], '黄金': ['黄金'], '稀土': ['稀土','有色'],
    '医药': ['创新药','医药'], '创新药': ['创新药','医药'],
    '消费': ['白酒','消费','百货'], '白酒': ['白酒','消费'],
    '传媒': ['传媒'], '元宇宙': ['元宇宙概念'], '数字经济': ['数字经济'],
    '软件': ['软件开发','计算机'], '数据中心': ['数据中心','算力'],
    '军工': ['军工'], '一带一路': ['一带一路'], '国企': ['国企改革'],
}
def resonance(sector):
    for b in BOARDS:
        bn = b['name']
        if sector in bn or bn in sector: return b
    return None
def themes_of(text):
    out = []
    for kw, secs in THEME_MAP.items():
        if kw in text:
            for s in secs:
                if s not in out: out.append(s)
    return out

# 产业链穿透: 主题 -> A 股核心标的(名)
CHAIN = {
    '算力': ['中际旭创','新易盛','天孚通信','工业富联','浪潮信息'],
    '光模块': ['中际旭创','新易盛','天孚通信','太辰光','光库科技'],
    'CPO': ['中际旭创','新易盛','天孚通信'],
    '液冷': ['高澜股份','英维克','申菱环境'],
    '数据中心': ['浪潮信息','中科曙光','工业富联'],
    'AI': ['科大讯飞','寒武纪','海光信息','金山办公'],
    '数字经济': ['科大讯飞','金山办公','数字政通'],
    '华为概念': ['赛力斯','软通动力','润和软件','拓维信息'],
    '机器人': ['汇川技术','绿的谐波','双环传动','埃斯顿'],
    '低空经济': ['万丰奥威','宗申动力','中信海直'],
    '半导体': ['中芯国际','北方华创','韦尔股份','兆易创新'],
    '芯片': ['中芯国际','北方华创','韦尔股份','兆易创新'],
    '光伏': ['隆基绿能','阳光电源','通威股份'],
    '储能': ['宁德时代','阳光电源','亿纬锂能'],
    '新能源车': ['比亚迪','赛力斯','拓普集团','三花智控'],
    '黄金': ['山东黄金','中金黄金','紫金矿业','赤峰黄金'],
    '稀土': ['北方稀土','中国稀土','盛和资源'],
    '创新药': ['恒瑞医药','药明康德','百济神州'],
    '白酒': ['贵州茅台','五粮液','泸州老窖'],
    '消费': ['贵州茅台','五粮液','伊利股份'],
    '传媒': ['分众传媒','芒果超媒','光线传媒'],
    '元宇宙概念': ['歌尔股份','蓝色光标'],
    '软件开发': ['用友网络','金山办公','恒生电子'],
    '计算机': ['用友网络','恒生电子','广联达'],
    '军工': ['中航沈飞','航发动力','中航西飞'],
    '一带一路': ['中国中铁','中国交建','中国建筑'],
}
def chain_line(themes):
    seen = set(); parts = []
    for th in themes:
        names = CHAIN.get(th)
        if not names: continue
        for nm in names:
            if nm in seen: continue
            seen.add(nm)
            code = NAME2CODE.get(nm)
            parts.append('%s(%s)' % (nm, code) if code else nm)
            if len(parts) >= 6: break
        if len(parts) >= 6: break
    return '关联标的: ' + '、'.join(parts) if parts else ''

THEME_KW = {
    'AI算力/光通信': ['光模块', '通信', 'CPO', '算力', '数据中心', '光通信', 'PCB'],
    '半导体设备': ['半导体', '芯片', '集成电路', '电子化学品'],
    '稀土·资源': ['稀土', '小金属', '有色金属', '贵金属'],
    '化工涨价': ['化学', '化工', '塑料', '橡胶', '化纤', '化肥', '农药'],
    'PCB·电子': ['PCB', '印制电路板', '消费电子', '电子制造', '元件'],
    '医药': ['医药', '制药', '生物', '医疗', '中药'],
}
def sector_strength(ind):
    """行业方向 -> (板块名, rps60, score, change_pct, strength) 或 None"""
    if not SECTORS:
        return None
    kws = THEME_KW.get(ind or '', [])
    if not kws:
        return None
    cands = [s for s in SECTORS if any(k in s.get('name', '') for k in kws)]
    if not cands:
        return None
    cands.sort(key=lambda s: -(s.get('rps60', 0) or 0))
    b = cands[0]
    return (b.get('name'), b.get('rps60'), b.get('score'), b.get('change_pct'), b.get('strength'))

def funnel_match(it):
    code = it['code']
    for b in BOARDS:
        for s in b.get('stocks', []):
            if s['code'] == code:
                return ('board', b)
    for o in _ORPHANS:
        if o['code'] == code:
            return ('orphan', o)
    sec = it.get('industry_dir') or it.get('sector_guess') or ''
    for b in BOARDS:
        if sec and (sec in b['name'] or b['name'] in sec):
            return ('board', b)
    return None

def src_of(board, code):
    s_ = set()
    for s in board.get('stocks', []):
        for x in s.get('src', []):
            s_.add(x)
    st = [s for s in board.get('stocks', []) if s.get('code') == code]
    if st:
        for x in st[0].get('src', []):
            s_.add(x)
    return sorted(s_)

HTML_TEMPLATE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>新闻情报中心</title>
<style>
 *{box-sizing:border-box;margin:0;padding:0}
 body{background:#0e1116;color:#c9d1d9;font:14px/1.6 -apple-system,"Microsoft YaHei",sans-serif}
 .wrap{max-width:920px;margin:0 auto;padding:14px 16px 60px}
 .wrap.ef-wide{max-width:1280px}
 header{position:sticky;top:0;background:#0e1116;padding:10px 0 8px;border-bottom:1px solid #222;z-index:5}
 h1{font-size:18px;color:#e6edf3}
 .sub{color:#7d8590;font-size:12px;margin-top:2px}
 .ctrls{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
 .btn{background:#1b2129;color:#c9d1d9;border:1px solid #2b333d;border-radius:14px;padding:4px 12px;cursor:pointer;font-size:12px}
 .btn.on{background:#2d6cdf;color:#fff;border-color:#2d6cdf}
.btn.ef-tab{color:#ffc15e;border-color:#6b5320}
.btn.ef-tab.on{background:rgba(255,193,94,.22);color:#ffd98a;border-color:#c99a35}
 .btn:hover{border-color:#3a4654}
 .sum{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0 4px;font-size:12px;color:#9aa4b2}
 .sum b{color:#e6edf3}
 .item{display:flex;background:#151a21;border:1px solid #232a33;border-radius:8px;margin-top:10px;overflow:hidden;cursor:pointer}
 .item .bar{width:6px;flex:0 0 6px}
 .item.macro{opacity:.58}
 .item.imp{background:#1b2533;border-color:#34465f}
 .cbody{flex:1;padding:9px 12px;min-width:0}
 .meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12px}
 .time{color:#6e7681}
 .badge{background:#3a2a12;color:#f0a830;border:1px solid #5a4012;border-radius:10px;padding:0 7px;font-size:11px}
 .sent{font-size:11px;padding:0 7px;border-radius:10px;border:1px solid #2b333d;color:#9aa4b2}
 .tag{font-size:11px;padding:0 7px;border-radius:10px}
 .tag.macro{background:#2a2030;color:#c08ad6;border:1px solid #4a3a55}
 .tag.macro.rel{background:#2a2c18;color:#d6c84a;border-color:#555020}
 .title{font-size:15px;color:#e6edf3;margin:4px 0;font-weight:600}
 .title.imp{font-weight:800;color:#fff}
 .body{color:#aeb6c2;font-size:13px;margin-top:2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 .item.exp .body{-webkit-line-clamp:unset;overflow:visible;white-space:pre-wrap}
 .chips{margin-top:6px;display:flex;flex-wrap:wrap;gap:5px}
 .chip{background:#10212e;color:#5bc8ff;border:1px solid #1d3a4d;border-radius:12px;padding:1px 9px;font-size:11px}
 .chip.reso{background:#102a18;color:#5be58a;border-color:#1d5a30}
 .chip.over{background:#20242b;color:#8b94a3;border-color:#333b46}
 .chip.geo{background:#2e2410;color:#f0c060;border:1px solid #5a4512}
 .chain{margin-top:5px;font-size:11px;color:#7fb0e0;background:#0f1a24;border-left:2px solid #2d6cdf;padding:3px 8px;border-radius:0 4px 4px 0}
 .tag.div{background:#3a2410;color:#f0a35a;border:1px solid #5a3a12}
 .srcs{margin-top:6px;color:#7aa2d6;font-size:11px;text-decoration:underline dotted;cursor:pointer;display:inline-block}
 .srcs:hover{color:#9ec3ef}
 .other{opacity:.5}
 .details{margin-top:6px;border-top:1px dashed #2b333d;padding-top:6px;display:none}
 .item.exp .details{display:block}
 .src{margin-bottom:5px}
 .src .t{color:#6e7681;margin-left:6px}
 .src .bt{color:#8b94a3;font-size:12px}
 .panel{margin-top:26px;background:#11161d;border:1px solid #232a33;border-radius:8px;padding:12px 14px}
 .panel h2{font-size:15px;color:#e6edf3;margin-bottom:8px}
 table{width:100%;border-collapse:collapse;font-size:12px}
 th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #1e242c}
 th{color:#7d8590;font-weight:600}
 td.num{color:#e6a23c}
 .hl{color:#5be58a}.wt{color:#d6c84a}
 .hidden{display:none!important}
/* ===== 东财7x24 顶部快讯 + 全球指数/贵金属汇率 页签(复用原 news.html 的 market_tab.js) ===== */
.em-ticker{background:#0f141a;border:1px solid #232a33;border-radius:8px;margin-bottom:10px;overflow:hidden}
.em-ticker-header{display:flex;align-items:center;justify-content:space-between;background:linear-gradient(90deg,#7a1416,#a51c1c);padding:8px 14px;color:#fff}
.em-ticker-news{font-family:Georgia,"Times New Roman",serif;font-size:20px;font-weight:700;letter-spacing:1px}
.em-ticker-date{font-size:12px;opacity:.9}
.em-ticker-tags{display:flex;gap:6px;padding:8px 12px;border-bottom:1px solid #1e242c;background:#11161d;flex-wrap:wrap}
.em-ticker-tag{border:1px solid #b91c1c;color:#e08a8a;border-radius:4px;padding:2px 10px;font-size:12px;font-weight:600;cursor:pointer;user-select:none;background:#161b22;transition:all .15s}
.em-ticker-tag:hover{background:#2a1416}
.em-ticker-tag.active{background:#b91c1c;color:#fff;border-color:#b91c1c}
.em-ticker-list{max-height:300px;overflow-y:auto;padding:0;background:#0f141a}
.em-ticker-item{display:block;border-bottom:1px solid #1e242c}
.em-item-bar{display:flex;align-items:flex-start;gap:10px;padding:8px 12px;color:#c9d1d9}
.em-item-bar .t-time{font-size:12px;color:#6e7681;white-space:nowrap;min-width:38px;margin-top:2px}
.em-item-bar .t-stock{background:#3a2a2a;color:#e08a8a;padding:1px 7px;border-radius:4px;font-size:11px;white-space:nowrap}
.em-item-bar .t-title{color:#c9d1d9;text-decoration:none;flex:1;font-weight:500;line-height:1.4;font-size:12.5px}
.em-item-bar .t-title:hover{text-decoration:underline;color:#e6edf3}
.em-ticker-empty{padding:16px;color:#7d8590;text-align:center;font-size:13px}
.em-market-table{width:100%;border-collapse:collapse;font-size:12px}
.em-market-table th{background:#11161d;color:#7d8590;padding:7px 6px;text-align:right;font-weight:600;border-bottom:1px solid #232a33;white-space:nowrap}
.em-market-table th:first-child{text-align:left;padding-left:12px}
.em-market-table td{padding:7px 6px;text-align:right;border-bottom:1px solid #181e25;white-space:nowrap;color:#c9d1d9}
.em-market-table td:first-child{text-align:left;padding-left:12px}
.em-market-table tbody tr{cursor:pointer;transition:background .15s}
.em-market-table tbody tr:hover{background:#161b22}
.em-market-table .em-name{font-weight:600;color:#e6edf3}
.em-market-table .em-up{color:#f04444}
.em-market-table .em-down{color:#2ecc71}
.em-market-table .em-unit{font-size:10px;color:#7d8590;margin-left:4px;font-weight:400}
.em-market-status{display:flex;justify-content:space-between;align-items:center;padding:7px 12px;font-size:11px;color:#7d8590;border-bottom:1px solid #181e25;background:#11161d}
.em-market-status .dot{width:6px;height:6px;border-radius:50%;background:#2ecc71;display:inline-block;margin-right:4px;vertical-align:middle}
.mt-hint{font-size:12px;color:#7d8590;margin:0 0 14px;padding:0 2px}
.mt-hint b{color:#e6edf3}
/* 综合评级徽章 */
.badge.rate{padding:0 8px;margin-left:2px}
.badge.rate-A{background:#3a1216;color:#ff6b6b;border-color:#7a1f25}
.badge.rate-B{background:#3a2410;color:#f0a35a;border-color:#5a3a12}
.badge.rate-C{background:#1a2230;color:#7fb0e0;border-color:#2b3a4d}
/* 利空扩散闪烁 */
@keyframes blink{0%,100%{box-shadow:0 0 0 0 rgba(240,68,68,0)}50%{box-shadow:0 0 10px 2px rgba(240,68,68,.7)}}
.item.alert{border-color:#f04444;animation:blink 1.2s infinite}
.tag.alertbadge{background:#3a1216;color:#ff6b6b;border:1px solid #7a1f25}
/* 自选股发光 */
.item.watch{border-color:#f0c060;box-shadow:0 0 0 1px #f0c060,0 0 12px rgba(240,192,96,.35)}
.item.watch .bar{background:#f0c060!important}
/* 昨日旧闻 */
.item.seen-old{opacity:.5}
.tag.seenold{background:#222a33;color:#8b94a3;border:1px solid #333b46}
/* 自选股输入栏 */
.watchbar{display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap}
.watchbar>span:first-child{color:#c9d1d9;font-size:13px}
.watchbar input{background:#0f141a;border:1px solid #2b333d;color:#e6edf3;border-radius:6px;padding:4px 8px;font-size:12px;width:300px}
.watchbar .btn{padding:3px 10px}
.watchbar .hint{font-size:11px;color:#7d8590}
/* 顶部自选异动浮窗 */
#watchBanner{position:sticky;top:62px;z-index:6;background:linear-gradient(90deg,#3a2c10,#5a4012);color:#f0d28a;border:1px solid #7a5a1a;border-radius:8px;padding:8px 12px;margin-top:10px;font-size:13px;display:none}
#watchBanner b{color:#fff}
/* 市场面板 */
#marketPanel{margin-top:14px}
#marketPanel .em-ticker{margin-bottom:0}
/* ===== 财报 × 资金信号 共振面板（嵌入页，作用域 #efPanel，避免与新闻表格样式冲突） ===== */
#efPanel{margin-top:26px;background:#11161d;border:1px solid #232a33;border-radius:8px;padding:12px 14px;display:none}
#efPanel h2{font-size:15px;color:#e6edf3;margin-bottom:8px}
#efPanel .ef-badge{padding:1px 9px;border-radius:10px;font-size:11px;background:#14203a;color:#5bc8ff;border:1px solid #1d3a4d;font-weight:400}
#efPanel .ef-sub{color:#7d8590;font-size:11.5px;line-height:1.6;margin-bottom:8px}
#efPanel .ef-sub b{color:#c9d1d9}
#efPanel .ef-sumbar{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#7d8590;margin-bottom:10px}
#efPanel .ef-sumbar b{color:#e6edf3;font-weight:700;font-family:ui-monospace,Consolas,monospace}
#efPanel .ef-sumbar .k{color:#5bc8ff}#efPanel .ef-sumbar .h{color:#f04444}#efPanel .ef-sumbar .w{color:#f0a830}
#efPanel .ef-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
#efPanel .ef-search{flex:1 1 200px;display:flex;background:#0f141a;border:1px solid #2b333d;border-radius:8px;padding:5px 10px}
#efPanel .ef-search input{flex:1;border:0;background:transparent;color:#e6edf3;font-size:12.5px;outline:none;font-family:inherit}
#efPanel .ef-chk{display:flex;align-items:center;gap:6px;background:#0f141a;border:1px solid #2b333d;border-radius:8px;padding:5px 10px;cursor:pointer;font-size:12px;color:#9aa4b2;user-select:none}
#efPanel .ef-chk input{accent-color:#5bc8ff;width:14px;height:14px}
#efPanel .ef-select{background:#0f141a;border:1px solid #2b333d;border-radius:8px;padding:6px 8px;color:#c9d1d9;font-size:12px;font-family:inherit;outline:none;cursor:pointer}
#efPanel .ef-scroll{overflow-x:auto;border:1px solid #232a33;border-radius:8px;background:#0f141a}
#efPanel table.ef-table{border-collapse:collapse;width:100%;min-width:1000px;font-size:12px}
#efPanel table.ef-table thead th{position:sticky;top:0;background:#141b24;color:#7d8590;font-weight:600;padding:8px 7px;text-align:center;white-space:nowrap;border-bottom:1px solid #232a33}
#efPanel table.ef-table thead th.l{text-align:left}
#efPanel table.ef-table tbody td{padding:6px 7px;text-align:center;border-bottom:1px solid #181e25;white-space:nowrap;color:#c9d1d9}
#efPanel table.ef-table tbody tr{cursor:pointer;transition:background .12s}
#efPanel table.ef-table tbody tr:hover{background:#161b22}
#efPanel td.ef-idx{color:#5f6b8f;font-family:ui-monospace,Consolas,monospace;width:34px}
#efPanel td.ef-code{color:#5bc8ff;font-family:ui-monospace,Consolas,monospace}
#efPanel td.ef-nm{text-align:left;font-weight:600;color:#e6edf3}
#efPanel td.ef-nm a{color:#e6edf3;text-decoration:none}
#efPanel td.ef-nm a:hover{color:#5bc8ff}
#efPanel td.ef-l{text-align:left}
#efPanel td.ef-dim{color:#7d8590}
#efPanel td.ef-num,#efPanel td.ef-price,#efPanel td.ef-chg{font-family:ui-monospace,Consolas,monospace;font-weight:600}
#efPanel td.ef-chg.up{color:#f04444}#efPanel td.ef-chg.down{color:#2ecc71}
#efPanel .ef-muted{color:#5f6b8f}
#efPanel .ef-tag{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11px;font-weight:600;white-space:nowrap}
#efPanel .ef-tag.reso{background:rgba(46,204,113,.16);color:#5be58a;border:1px solid rgba(46,204,113,.4)}
#efPanel .ef-tag.key{background:rgba(240,68,68,.16);color:#ff8a8a;border:1px solid rgba(240,68,68,.45);font-weight:700}
#efPanel .ef-tag.mid{background:rgba(240,168,48,.16);color:#ffce8a;border:1px solid rgba(240,168,48,.35)}
#efPanel .ef-tag.orphan{background:rgba(107,116,148,.16);color:#aeb6cf;border:1px solid #2b333d}
#efPanel .ef-rps{font-family:ui-monospace,Consolas,monospace;font-size:11px}
#efPanel .ef-rps.strong{color:#5be58a}#efPanel .ef-rps.mid{color:#ffce8a}#efPanel .ef-rps.weak{color:#aeb6cf}
#efPanel .ef-secbox{display:inline-block;text-align:left;white-space:nowrap}
#efPanel .ef-secbox .sn{color:#e6edf3;font-weight:600}
#efPanel .ef-src{display:inline-block;margin:1px 2px;padding:0 6px;border-radius:5px;font-size:10.5px;background:#14203a;color:#7d8590;white-space:nowrap}
#efPanel .ef-mini{display:inline-block;margin:1px 2px;padding:0 6px;border-radius:5px;font-size:10.5px;background:rgba(255,107,107,.14);color:#ffb3b3;white-space:nowrap}
#efPanel .ef-cd-near{color:#ff6b6b;font-weight:700}
#efPanel .ef-foot{margin-top:10px;color:#5f6b8f;font-size:11px;line-height:1.7}
#efPanel .ef-foot b{color:#9aa4b2}
</style>
<style id="m-responsive-css">/* ===== 移动端/平板适配 v2 (仅 <=1024px 生效; 桌面端零影响) ===== */
@media (max-width:1024px){
 header{position:static}
 #watchBanner{position:static}
 #marketPanel{margin-top:10px}
 .em-ticker-list{max-height:none;overflow-y:visible}
 .em-market-table th:nth-child(n+5),.em-market-table td:nth-child(n+5){display:none}
 body.mkt-view #list{display:none}
 .watchbar,.sum{display:none}
 body.tools-open .watchbar,body.tools-open .sum{display:flex}
 #mToolsBtn{display:inline-block}
}
@media (max-width:640px){
 .wrap{padding:10px 10px 40px}
 h1{font-size:17px}
 .sub{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .ctrls{gap:5px;margin-top:8px}
 .ctrls .btn{padding:3px 9px;font-size:11.5px}
 .ctrls .btn[data-mtab]{order:-1}
 .watchbar input{flex:1 1 auto;min-width:0;width:auto}
 .sum{gap:6px 12px;margin:8px 0 2px}
 #marketPanel{margin-top:8px;padding:8px 8px}
 .em-market-table th:nth-child(3),.em-market-table td:nth-child(3){display:none}
 .em-market-table th,.em-market-table td{padding:7px 4px}
 .em-market-table th:first-child,.em-market-table td:first-child{padding-left:8px}
 .em-item-bar{gap:6px;padding:8px 8px}
 .item .title{font-size:14px}
 .item .cbody{padding:9px 10px}
}
</style></head><body><div class="wrap">
<header>
 <h1>新闻情报中心</h1>
 <div class="sub">数据更新至 __UT__ · 原始 __TOTAL__ 条 → 合并后 __NCL__ 条（精确指纹+高相似+同源10min 三重去重）· 数据来源：财联社/同花顺/新浪等（延时）</div>
 <div class="ctrls">
  <span class="btn ef-tab" id="efTab">📊 财报×资金</span>
  <span class="btn on" data-f="all">全部</span>
  <span class="btn" data-f="hot">🔥热点</span>
  <span class="btn" data-f="imp">⭐重要</span>
  <span class="btn" data-f="pos">利好</span>
  <span class="btn" data-f="neg">利空</span>
  <span class="btn" data-f="reso">板块共振</span>
  <span class="btn" data-f="div">⚠预期判断</span>
  <span class="btn" id="toggleMacro">隐藏宏观</span>
  <span class="btn" data-mtab="0">📰实时快讯</span>
  <span class="btn" data-mtab="1">🌐全球指数</span>
  <span class="btn" data-mtab="2">💰贵金属汇率</span>
 </div>
 <div class="watchbar">
   <span>自选股：</span>
   <input id="watchInput" placeholder="输入代码，空格/逗号分隔，如 600519 300750" />
   <span class="btn" id="watchSave">保存</span>
   <span class="hint" id="watchHint">回车或点保存生效（存本地浏览器）</span>
 </div>
 <div class="sum">
  <span>宏观/地缘：<b>__MACRO__</b> 条（默认半透明，可隐藏）</span>
  <span>军事/地缘冲突细分：<b>__MIL__</b> 条</span>
  <span>产业链共振事件：<b>__RESO__</b> 条 · 地缘受益标注：<b>__GEO__</b> 条</span>
  <span>情绪条：<span style="color:#e53935">■</span>强利好 <span style="color:#FFC107">■</span>弱利好 <span style="color:#5a6472">■</span>中性/地缘 <span style="color:#2ecc71">■</span>弱利空 <span style="color:#0078FF">■</span>强利空</span>
 </div>
</header>
<div id="watchBanner"></div>
<div class="panel" id="marketPanel" style="display:none">
 <div class="em-ticker" id="em-ticker">
  <div class="em-ticker-header">
   <span class="em-ticker-news">MARKETS</span>
   <span class="em-ticker-date" id="em-ticker-date">--</span>
  </div>
  <div class="em-ticker-tags" id="em-ticker-tags"></div>
  <div class="em-ticker-list" id="em-ticker-list"></div>
 </div>
</div>
<div id="list">__CARDS__</div>
<div class="panel" id="efPanel">
 <h2>财报 × 资金信号 共振面板 <span class="ef-badge" id="efBadge">…</span></h2>
 <div class="ef-sub" id="efSub"></div>
 <div class="ef-sumbar" id="efSumBar"></div>
 <div class="ef-bar">
  <label class="ef-search"><input id="efQ" type="text" placeholder="搜索代码 / 名称 / 行业…" autocomplete="off" spellcheck="false"></label>
  <label class="ef-chk"><input type="checkbox" id="efReso"> 只看漏斗信号</label>
  <label class="ef-chk"><input type="checkbox" id="efHigh"> 只看高增(≥50%)</label>
  <label class="ef-chk"><input type="checkbox" id="efKey"> 只看重点</label>
  <select id="efSector" class="ef-select"><option value="">行业·全部</option></select>
 </div>
 <div class="ef-scroll">
  <table class="ef-table" id="efTable">
   <thead><tr>
    <th>#</th><th>代码</th><th class="l">名称</th><th class="l">行业方向</th>
    <th>归母同比</th><th>预告净利</th><th>状态</th><th>距披露</th>
    <th>资金信号(漏斗)</th><th>板块强度(RPS)</th><th>现价</th><th>涨跌幅</th><th class="l">研判</th>
   </tr></thead>
   <tbody id="efBody"></tbody>
  </table>
 </div>
 <div class="ef-foot">数据：<b>q3_earnings.json</b>（业绩预告/正式）× <b>sector_funnel.json</b>（信号股漏斗）× <b>sector_rankings.json</b>（板块 RPS 快照 · <span id="efLatest">—</span>）<br>
  披露期标签自动取自数据 <b>meta.window</b>，换季重新生成即可、无需改页面 · 现价/涨跌幅为腾讯行情（60s 刷新）· 点击任意行弹 K 线<br>
  「待验证」= 高增但漏斗与板块强度均未确认；<b>板块强度(估算)</b> 按上游「行业方向」关键词匹配最新板块快照，该字段属名称关键词猜测、可能偏离实际主业，仅作初筛参考。</div>
</div>
</div>
<script>var _rawData=__RAWDATA__;window.CODE2NAME=__CODE2NAME__;</script>
<script src="market_emtab.js"></script>
<script src="kline_popup.js?v=20261025b" defer></script>
<script>
 var state={f:'all',hideMacro:false};
 var efOn=false;
 var efTabBtn=document.getElementById('efTab');
 function efView(on){
   efOn=!!on;
   var L=document.getElementById('list'), E=document.getElementById('efPanel'), M=document.getElementById('marketPanel'), W=document.querySelector('.wrap');
   if(W) W.classList.toggle('ef-wide',efOn);
   if(L) L.style.display=efOn?'none':'';
   if(E) E.style.display=efOn?'block':'none';
   if(M) M.style.display='none';
   document.querySelectorAll('.btn[data-mtab]').forEach(function(x){x.classList.remove('on');});
   if(efTabBtn){efTabBtn.classList.toggle('on',efOn);efTabBtn.textContent=efOn?'📰 返回新闻':'📊 财报×资金';}
   if(efOn) window.scrollTo(0,0);
 }
 if(efTabBtn) efTabBtn.onclick=function(){efView(!efOn);};
 function apply(){
   document.querySelectorAll('.item').forEach(function(el){
     var show=true, f=state.f;
     if(f==='hot'&&el.dataset.hot!=='1') show=false;
     else if(f==='imp'&&el.dataset.imp!=='1') show=false;
     else if(f==='pos'&&!(el.dataset.sent==='strong_pos'||el.dataset.sent==='weak_pos')) show=false;
     else if(f==='neg'&&!(el.dataset.sent==='strong_neg'||el.dataset.sent==='weak_neg')) show=false;
     else if(f==='reso'&&el.dataset.reso!=='1') show=false;
     else if(f==='div'&&el.dataset.div!=='1') show=false;
     if(state.hideMacro && el.dataset.macro==='1') show=false;
     el.classList.toggle('hidden',!show);
   });
 }
 document.querySelectorAll('.btn[data-f]').forEach(function(b){
   b.onclick=function(){if(efOn)efView(false);document.querySelectorAll('.btn[data-f]').forEach(x=>x.classList.remove('on'));b.classList.add('on');state.f=b.dataset.f;apply();};
 });
 document.getElementById('toggleMacro').onclick=function(){ state.hideMacro=!state.hideMacro; this.classList.toggle('on',state.hideMacro); apply(); };
 // 市场页签(实时快讯/全球指数/贵金属汇率) -> 复用 market_emtab.js 的 switchEmTab
 document.querySelectorAll('.btn[data-mtab]').forEach(function(b){
   b.onclick=function(){
     if(efOn)efView(false);
     var p=document.getElementById('marketPanel');
     var isActive=b.classList.contains('on');
     if(isActive && p.style.display!=='none'){ p.style.display='none'; b.classList.remove('on'); return; }
     p.style.display='block';
     document.querySelectorAll('.btn[data-mtab]').forEach(x=>x.classList.remove('on')); b.classList.add('on');
     if(window.switchEmTab) switchEmTab(parseInt(b.dataset.mtab,10));
   };
 });
 // 昨日旧闻: localStorage 记录已见 id, 非当日再次出现标记为旧闻
 (function(){
   var KEY='news_seen_v1'; var today=new Date().toISOString().slice(0,10);
   var seen={}; try{seen=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){}
   document.querySelectorAll('.item').forEach(function(el){
     var id=el.dataset.id; if(!id) return;
     if(seen[id] && seen[id]!==today){
       el.classList.add('seen-old');
       var m=el.querySelector('.meta');
       if(m && !m.querySelector('.tag.seenold')){ var s=document.createElement('span'); s.className='tag seenold'; s.textContent='昨日旧闻'; m.appendChild(s); }
     }
     seen[id]=today;
   });
   try{localStorage.setItem(KEY,JSON.stringify(seen));}catch(e){}
 })();
 // 自选股高亮 + 顶部浮窗
 (function(){
   var KEY='my_watch_v1';
   var input=document.getElementById('watchInput'), hint=document.getElementById('watchHint'), banner=document.getElementById('watchBanner');
   var saved=[]; try{saved=(localStorage.getItem(KEY)||'').split(',').map(function(s){return s.trim();}).filter(Boolean);}catch(e){}
   if(saved.length){ input.value=saved.join(' '); applyWatch(saved); }
   function applyWatch(list){
     var set=list.map(function(s){return s.replace(/^(sh|sz|bj)/i,'').replace(/\D/g,'');}).filter(function(s){return s.length===6;});
     var hit={};
     document.querySelectorAll('.item').forEach(function(el){
       var codes=(el.dataset.codes||'').split(',').filter(Boolean);
       var m=codes.filter(function(c){return set.indexOf(c)>=0;});
       if(m.length){ el.classList.add('watch'); m.forEach(function(c){ if(!hit[c]) hit[c]=(window.CODE2NAME&&window.CODE2NAME[c])||c; }); }
       else el.classList.remove('watch');
     });
     var keys=Object.keys(hit);
     if(keys.length){
       banner.style.display='block';
       banner.innerHTML='🔔 自选异动（'+keys.length+'）：'+keys.map(function(c){return '<b>'+hit[c]+'</b>('+c+')';}).join('、');
     } else banner.style.display='none';
   }
   function save(){ var v=input.value.trim(); var list=v.split(/[\s,，、]+/).filter(Boolean); try{localStorage.setItem(KEY,list.join(','));}catch(e){} applyWatch(list); hint.textContent='已保存 '+list.length+' 只'; }
   document.getElementById('watchSave').onclick=save;
   input.addEventListener('keydown',function(e){ if(e.key==='Enter') save(); });
 })();
 // 点击卡片展开(正文2行->全文 + 多源详情); 点“N家媒体报道”仅展开详情, 阻止冒泡
 document.querySelectorAll('.item').forEach(function(it){
   it.onclick=function(e){ if(e.target.closest('.srcs')) return; it.classList.toggle('exp'); };
 });
 document.querySelectorAll('.srcs').forEach(function(s){
   s.title='点击展开各源标题与时间';
   s.onclick=function(e){e.stopPropagation();s.closest('.item').classList.toggle('exp');};
 });
 apply();
</script>
<script>
var EF_ROWS=__EF_ROWS__;
var EF_META=__EF_META__;
(function(){
 'use strict';
 var rows=EF_ROWS;
 function el(id){return document.getElementById(id);}
 function esc2(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
 function symOf(c){return (/^[69]/.test(c)?'sh':'sz')+c;}
 function daysTo(ds){if(!ds)return null;var t=new Date(String(ds).replace(/-/g,'/'));if(isNaN(t))return null;return Math.round((t-new Date())/86400000);}
 function seasonBadge(){var m=new Date().getMonth()+1;if(m>=1&&m<=4)return '当前披露季 · 年报/一季报';if(m>=7&&m<=8)return '当前披露季 · 中报';if(m===10)return '当前披露季 · 三季报';return '业绩空窗 · 跟踪最新预告';}
 function yoyColor(lo){if(lo==null)return '#5f6b8f';if(lo>=100)return '#2ecc71';if(lo>=50)return '#27ae60';if(lo>=0)return '#7bc6a0';return '#e74c3c';}
 function yoyStr(y){if(!y||y[0]==null||y[1]==null)return '<span class="ef-muted">—</span>';return '<span style="color:'+yoyColor(y[0])+'">+'+(+y[0]).toFixed(0)+'%~+'+(+y[1]).toFixed(0)+'%</span>';}
 function amtStr(a){if(!a||a[0]==null)return '<span class="ef-muted">—</span>';return (a[0]>=10000)?((a[0])/10000).toFixed(1)+'亿':(+a[0]).toFixed(0)+'万';}
 function rpsCls(r){if(r==null)return 'weak';if(r>=70)return 'strong';if(r>=50)return 'mid';return 'weak';}
 function funnelHtml(r){if(r.funnel_kind==='board')return '<span class="ef-tag reso">✓'+esc2(r.funnel_name)+'</span>';if(r.funnel_kind==='orphan')return '<span class="ef-tag orphan">'+esc2(r.funnel_name)+'</span>';return '<span class="ef-muted">— 待验证</span>';}
 function secHtml(r){if(!r.sec_name)return '<span class="ef-muted">—</span>';var cls=rpsCls(r.sec_rps);var chg=r.sec_chg==null?'':(r.sec_chg>=0?'+':'')+r.sec_chg.toFixed(2)+'%';var tip=r.sec_name+' · RPS60 '+r.sec_rps+(r.sec_score!=null?' · 分'+r.sec_score.toFixed(0):'')+(chg?' · '+chg:'')+(r.sec_strength?' · '+r.sec_strength:'')+'（按行业方向「'+(r.industry||'—')+'」关键词匹配，估算）';return '<span class="ef-secbox" title="'+esc2(tip)+'"><span class="sn">'+esc2(r.sec_name)+'</span> <span class="ef-rps '+cls+'">'+r.sec_rps+'</span>'+(chg?' <span class="ef-rps '+(r.sec_chg>=0?'strong':'weak')+'">'+chg+'</span>':'')+'</span>';}
 function tagHtml(r){var cls=r.tag.indexOf('重点')>=0?'key':(r.tag.indexOf('待验证')>=0?'mid':(r.tag.indexOf('弱')>=0?'orphan':'mid'));return '<span class="ef-tag '+cls+'">'+esc2(r.tag)+'</span>';}
 function build(){
   if(!el('efBody'))return;
   el('efBadge').textContent=seasonBadge();
   el('efSub').innerHTML='业绩预告 × 板块资金信号漏斗（sector_funnel）× 板块 RPS（sector_rankings）共振 · <b>名称 + 财报 + 资金确认</b> · 披露期：<b>'+esc2(EF_META.period)+'</b>';
   var lt=el('efLatest'); if(lt) lt.textContent=EF_META.latest_snapshot||'—';
   el('efSumBar').innerHTML='共 <b>'+EF_META.total+'</b> 只业绩候选 · <span class="h">高增(≥50%) '+EF_META.high+'</span> · <span class="k">漏斗信号 '+EF_META.funnel+'</span> · <span class="h">重点(高增+资金确认) '+EF_META.key+'</span> · <span class="w">待验证(高增无信号) '+EF_META.wait+'</span>';
   var sel=el('efSector'),set={};
   rows.forEach(function(r){if(r.industry)set[r.industry]=1;});
   Object.keys(set).sort().forEach(function(s){var o=document.createElement('option');o.value=s;o.textContent=s;sel.appendChild(o);});
   render(); maybeQuotes(); setInterval(maybeQuotes,60000);
 }
 function passes(r){
   var q=(el('efQ').value||'').trim().toLowerCase();
   if(el('efReso').checked&&r.funnel_kind!=='board')return false;
   if(el('efHigh').checked&&!r.high)return false;
   if(el('efKey').checked&&r.tag!=='重点·高增+资金确认')return false;
   var sec=el('efSector').value; if(sec&&r.industry!==sec)return false;
   if(q){var hay=(r.code+' '+r.name+' '+r.industry).toLowerCase();if(hay.indexOf(q)<0)return false;}
   return true;
 }
 function render(){
   var shown=rows.filter(passes),h='';
   shown.forEach(function(r,i){
     var d=daysTo(r.appr);
     var cd=d==null?'<span class="ef-muted">—</span>':(d<0?'<span class="ef-muted">已过</span>':(d<=5?'<b class="ef-cd-near">'+d+'天</b>':d+'天'));
     var st=r.status==='已出正式'?'<span class="ef-tag reso">已出正式</span>':(r.status==='仅有预告'?'<span class="ef-tag mid">仅有预告</span>':'<span class="ef-muted">'+(esc2(r.status)||'—')+'</span>');
     var kcfj=r.kcfj?'<span class="ef-tag mid" title="扣非存疑">扣非存疑</span>':'';
     var minis=(r.focus||[]).map(function(t){return '<span class="ef-mini">'+esc2(t)+'</span>';}).join(' ');
     var srcs=(r.srcs||[]).map(function(x){return '<span class="ef-src">'+esc2(x)+'</span>';}).join(' ')||'<span class="ef-muted">—</span>';
     h+='<tr data-code="'+r.code+'" data-name="'+esc2(r.name)+'">'
       +'<td class="ef-idx">'+(i+1)+'</td>'
       +'<td class="ef-code">'+r.code+'</td>'
       +'<td class="ef-nm"><a href="https://quote.eastmoney.com/'+symOf(r.code)+'.html" target="_blank" rel="noopener">'+esc2(r.name)+'</a></td>'
       +'<td class="ef-dim ef-l">'+esc2(r.industry)+'</td>'
       +'<td class="ef-num">'+yoyStr(r.yoy)+'</td>'
       +'<td class="ef-num">'+amtStr(r.amt)+'</td>'
       +'<td>'+st+'</td>'
       +'<td>'+cd+'</td>'
       +'<td>'+funnelHtml(r)+'</td>'
       +'<td>'+secHtml(r)+'</td>'
       +'<td class="ef-price" id="efpx_'+r.code+'">—</td>'
       +'<td class="ef-chg" id="efchg_'+r.code+'">—</td>'
       +'<td class="ef-l">'+tagHtml(r)+(minis?' '+minis:'')+(kcfj?' '+kcfj:'')+'</td>'
       +'</tr>';
   });
   el('efBody').innerHTML=h||'<tr><td colspan="13" class="ef-muted" style="padding:24px">无匹配项</td></tr>';
   applyQuotes();
 }
 function maybeQuotes(){var codes=rows.map(function(r){return r.code;});for(var i=0;i<codes.length;i+=50){(function(sl){var s=document.createElement('script');s.src='https://qt.gtimg.cn/q='+sl.map(symOf).join(',')+'&_t='+Date.now();s.onload=function(){applyQuotes();s.remove();};s.onerror=function(){s.remove();};document.head.appendChild(s);})(codes.slice(i,i+50));}}
 function applyQuotes(){rows.forEach(function(r){var v=window['v_'+symOf(r.code)];if(!v)return;var p=String(v).split('~');if(p.length<33)return;var price=parseFloat(p[3]),pct=parseFloat(p[32]);if(isNaN(price))return;var pe=el('efpx_'+r.code),ce=el('efchg_'+r.code);if(pe)pe.textContent=price.toFixed(2);if(ce&&!isNaN(pct)){ce.textContent=(pct>=0?'+':'')+pct.toFixed(2)+'%';ce.className='ef-chg '+(pct>=0?'up':'down');}});}
 // 点击行 → 全站统一 B 弹窗（kline_popup.js，默认阉割版副图）；脚本未就绪时兜底降级
 function openK(code,name){if(typeof window.openKline==='function'){window.openKline(code,name);}}
 if(el('efBody'))el('efBody').addEventListener('click',function(e){var tr=e.target.closest('tr[data-code]');if(!tr)return;openK(tr.getAttribute('data-code'),tr.getAttribute('data-name'));});
 ['efQ','efReso','efHigh','efKey'].forEach(function(id){var e=el(id);if(!e)return;e.addEventListener('input',render);e.addEventListener('change',render);});
 if(el('efSector'))el('efSector').addEventListener('change',render);
 build();
})();
</script><script id="m-responsive-js">
(function(){
 'use strict';
 var mq=window.matchMedia('(max-width:1024px)');
 var active=false,btn=null;
 function onClick(e){
  var t=e.target&&e.target.closest?e.target.closest('.btn'):null;
  if(!t)return;
  if(t.hasAttribute('data-mtab')){
   document.body.classList.remove('tools-open');
   if(btn){btn.textContent='⚙ 更多';btn.classList.remove('on');}
   var p=document.getElementById('marketPanel');
   setTimeout(function(){
    document.body.classList.toggle('mkt-view',!!(p&&p.style.display!=='none'));
   },0);
  }else if(t.hasAttribute('data-f')||t.id==='efTab'||t.id==='toggleMacro'){
   document.body.classList.remove('mkt-view');
   var p2=document.getElementById('marketPanel');
   if(p2)p2.style.display='none';
  }
 }
 function enable(){
  if(active)return;active=true;
  var c=document.querySelector('.ctrls');
  if(c&&!document.getElementById('mToolsBtn')){
   btn=document.createElement('span');
   btn.id='mToolsBtn';btn.className='btn';btn.textContent='⚙ 更多';
   btn.onclick=function(){
    var open=document.body.classList.toggle('tools-open');
    btn.textContent=open?'▲ 收起':'⚙ 更多';
    btn.classList.toggle('on',open);
   };
   c.appendChild(btn);
  }
  document.addEventListener('click',onClick,false);
 }
 function disable(){
  if(!active)return;active=false;
  document.removeEventListener('click',onClick,false);
  document.body.classList.remove('mkt-view','tools-open');
  if(btn&&btn.parentNode)btn.parentNode.removeChild(btn);
  btn=null;
 }
 function sync(){if(mq.matches)enable();else disable();}
 sync();
 if(mq.addEventListener)mq.addEventListener('change',sync);
 else if(mq.addListener)mq.addListener(sync);
})();
</script>
</body></html>"""

def generate(news_data):
    """整页渲染 v6 news.html。news_data: {items, update_time, ...}。异常由调用方兜底。"""
    global BOARDS, NAME2CODE, _ORPHANS, PERIOD, LATEST, SECTORS
    news = news_data
    news = L(os.environ.get('NEWS_SRC', 'news_data.json'))
    items = news['items']
    # 供 market_emtab.js(原 news.html 的东财7x24/全球指数/贵金属汇率页签) 复用的兜底数据
    raw_items = [{'id': it.get('id'), 'time': it.get('time'), 'title': it.get('title'),
                  'text': it.get('text'), 'source': it.get('source'), 'category': it.get('category'),
                  'stock': it.get('stock'), 'url': it.get('url'), 'importance': it.get('importance')}
                 for it in items]
    funnel = L('sector_funnel.json')
    BOARDS = funnel['boards']
    _earn = L('q3_earnings.json')
    q3 = _earn['items']
    sr = L('sector_rankings.json')
    en = L('em_stock_names.json')
    NAME2CODE = {v: k for k, v in en.items() if isinstance(k, str) and isinstance(v, str)}

    # ---------------- 去重(三重保险: 同源10min预清 / 精确指纹 / 高相似) ----------------
    WIN = 10 * 60 * 1000
    def fp(it):
        # 内容指纹: 归一化标题 + 正文前40字
        return norm(it.get('title','')) + '|' + norm((it.get('text') or '')[:40])
    # 0) 预处理: 同源 + 同归一化标题 + 10分钟内 -> 仅留时间最新(其余视为刷屏丢弃)
    keep_idx = set(range(len(items)))
    for i in range(len(items)):
        if i not in keep_idx: continue
        ti = items[i].get('time', 0) or 0
        ni = norm(items[i].get('title','') or '')
        si = items[i].get('source','') or ''
        for j in range(i+1, len(items)):
            if j not in keep_idx: continue
            if items[j].get('source','') == si and norm(items[j].get('title','') or '') == ni:
                tj = items[j].get('time', 0) or 0
                if ti and tj and abs(tj - ti) <= WIN:
                    if tj >= ti:
                        keep_idx.discard(i); 
                    else:
                        keep_idx.discard(j)
    # 1) 精确指纹完全相同 -> 合并(解决“同内容刷屏几十条”)
    groups = []
    fp_index = {}
    for idx in keep_idx:
        key = fp(items[idx])
        if key in fp_index:
            groups[fp_index[key]].append(idx)
        else:
            fp_index[key] = len(groups)
            groups.append([idx])
    # 2) 高相似合并: 组间 相似度>=0.9 或 包含关系, 且 24h 内
    merged = [list(g) for g in groups]
    i = 0
    while i < len(merged):
        j = i + 1
        while j < len(merged):
            a, b = items[merged[i][0]], items[merged[j][0]]
            ta, tb = a.get('time',0), b.get('time',0)
            if ta and tb and abs(ta - tb) > 24*3600*1000:
                j += 1; continue
            s = max(sim(a.get('title',''), b.get('title','')), sim(fp(a), fp(b)))
            if s >= 0.9:
                merged[i] += merged[j]
                del merged[j]
            else:
                j += 1
        i += 1
    clusters = merged

    # ---------------- 组装卡片 ----------------
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    ut = news.get('update_time') or now
    cards = []
    code_name_map = {}
    macro_count = 0
    reso_events = 0
    mil_count = 0
    for cl in clusters:
        members = [items[k] for k in cl]
        rep = sorted(members, key=lambda m: (0 if m.get('importance') else 1, m.get('time',0)))[0]
        title = rep.get('title') or ''
        text0 = rep.get('text') or ''
        body = trim_body(title, text0)
        full_text = (title + ' ' + text0)
        is_macro, rel = macro_of(full_text)
        is_mil = bool(MILITARY_KW.search(full_text))
        overseas = bool(FOREIGN_KW.search(full_text)) and not bool(CHINA_KW.search(full_text))
        if is_mil: mil_count += 1
        # 主题: 军事/地缘新闻抑制产业主题误标
        themes = [] if is_mil else themes_of(full_text)
        relevant = bool(rel) or bool(themes)
        if is_macro: macro_count += 1
        # 情绪: 宏观非相关 -> 地缘中性(灰), 不再全局利空
        if is_macro and not relevant:
            sent = 'geo'
        else:
            sent = sentiment_of(full_text, rep.get('importance'))
        slabel, scol = SENT[sent]
        imp = rep.get('importance','')
        is_hot = 'hot' in str(imp)
        is_imp = 'important' in str(imp)
        badge = ('🔥热点' if is_hot else '') + (' ⭐重要' if is_imp else '')
        # 综合评级(打分系统): 基础情绪分 × 媒体热度权重
        EMOTION = {'strong_pos':2,'weak_pos':1,'strong_neg':-2,'weak_neg':-1,'neutral':0,'geo':0}
        e = EMOTION.get(sent, 0)
        nm = len(members)
        heat = 3 if nm >= 3 else (2 if nm == 2 else 1)
        # 评级: 强情绪+多源=A(强烈关注); 有情绪+多源=B(关注); 有情绪=C(一般); 无情绪=无
        if e != 0 and heat >= 2 and abs(e) >= 2:
            rating, rlabel = 'A', 'A级·强烈关注'
        elif e != 0 and heat >= 2:
            rating, rlabel = 'B', 'B级·关注'
        elif e != 0:
            rating, rlabel = 'C', 'C级·一般'
        else:
            rating, rlabel = '', ''
        # 强利空 + 多源 -> 标红闪烁预警(利空扩散)
        alert = (e < 0 and abs(e) >= 2 and nm >= 2)
        # 提取本卡片相关 6 位股票代码(供自选股高亮)
        codes = set()
        for m in members:
            t = (m.get('title') or '') + ' ' + (m.get('text') or '')
            for c in re.findall(r'\b\d{6}\b', t):
                codes.add(c)
            if m.get('stock'):
                for c in re.findall(r'\b\d{6}\b', str(m.get('stock'))):
                    codes.add(c)
        for c in sorted(codes):  # 排序保证输出确定性（set 哈希序会漂移）
            code_name_map[c] = en.get(c)
        codes_str = ','.join(sorted(codes))
        # chips
        chips = []
        card_reso = False
        if is_mil:
            for g in geo_benefit(full_text):
                chips.append('<span class="chip geo">%s 潜在受益</span>' % esc(g))
        elif overseas:
            for s in themes:
                chips.append('<span class="chip over">%s·海外</span>' % esc(s))
        else:
            for s in themes:
                b = resonance(s)
                if b:
                    chips.append('<span class="chip reso">%s ✓共振</span>' % esc(s)); card_reso = True; reso_events += 1
                else:
                    chips.append('<span class="chip">%s</span>' % esc(s))
        # 地缘受益(非军事但有冲突词, 如伊朗油价)
        if not is_mil and (is_macro and not relevant):
            for g in geo_benefit(full_text):
                chips.append('<span class="chip geo">%s 潜在受益</span>' % esc(g))
        # 产业链穿透(非军事/有主题): 海外新闻(英伟达/特斯拉)同样联想 A 股核心标的, 仅 chip 标·海外区分
        chain = ''
        if not is_mil and themes:
            chain = chain_line(themes)
        # 预期判断：仅当标题/正文命中"业绩超/不及预期"等预期差关键词才打标
        divergent = bool(EXPECT_GAP.search(full_text))
        # 来源
        srcs = []
        for m in members:
            srcs.append('%s%s' % (esc(m.get('source','')), ('·'+esc(m.get('sub_source','')) if m.get('sub_source') else '')))
        src_line = '%d家媒体报道：%s' % (len(members), '、'.join(srcs))
        details = ''
        if len(members) > 1:
            det = []
            for m in members:
                bt = trim_body(m.get('title',''), m.get('text',''))
                det.append('<div class="src"><b>%s</b> <span class="t">%s</span><div class="bt">%s</div></div>'
                           % (esc(m.get('source','')), esc(fmt_time(m)), esc((m.get('title','')+('  '+bt if bt else ''))[:160])))
            details = '<div class="details">%s</div>' % ''.join(det)
        macro_tag = ''
        if is_macro:
            if relevant:
                extra = ('·' + '/'.join(rel)) if rel else ''
                macro_tag = '<span class="tag macro rel">宏观·关联%s%s</span>' % (('产业' if themes else ''), extra)
            else:
                macro_tag = '<span class="tag macro">宏观</span>'
        imp_cls = ' imp' if (is_hot or is_imp) else ''
        div_badge = '<span class="tag div">⚠预期判断</span>' if divergent else ''
        alert_cls = ' alert' if alert else ''
        rating_badge = ('<span class="badge rate rate-%s">%s</span>' % (rating, rlabel)) if rating else ''
        alert_badge = '<span class="tag alertbadge">⚠警惕·利空扩散</span>' if alert else ''
        chip_html = '<div class="chips">%s</div>' % ''.join(chips) if chips else ''
        chain_html = '<div class="chain">%s</div>' % esc(chain) if chain else ''
        macro_cls = ' macro' if (is_macro and not relevant) else ''
        card = (
          '<div class="item%s%s%s" data-sent="%s" data-hot="%s" data-imp="%s" data-macro="%s" data-reso="%s" data-div="%s" data-codes="%s" data-id="%s">'
          '<div class="bar" style="background:%s"></div>'
          '<div class="cbody">'
            '<div class="meta"><span class="time">%s</span>%s%s%s%s%s%s</div>'
            '<div class="title%s">%s</div>'
            '%s'
            '%s'
            '%s'
            '<div class="srcs">%s</div>'
            '%s'
          '</div></div>'
        ) % (
          macro_cls, imp_cls, alert_cls, sent, ('1' if is_hot else '0'), ('1' if is_imp else '0'), ('1' if (is_macro and not relevant) else '0'), ('1' if card_reso else '0'), ('1' if divergent else '0'), codes_str, esc(rep.get('id','')),
          scol,
          esc(fmt_time(rep)),
          ('<span class="badge">%s</span>' % badge) if badge else '',
          rating_badge,
          slabel,
          macro_tag,
          div_badge,
          alert_badge,
          imp_cls,
          esc(title),
          ('<div class="body">%s</div>' % esc(body)) if body else '',
          chip_html,
          chain_html,
          esc(src_line),
          details,
        )
        cards.append(card)

    # ---------------- 财报 × 资金信号 共振面板（嵌入页） ----------------
    # 数据源: q3_earnings(items/名称+财报) × sector_funnel(boards/orphans 漏斗) × sector_rankings(最新RPS快照 板块强度)
    PERIOD = (_earn.get('meta') or {}).get('window') or '业绩披露窗口'
    _ORPHANS = funnel.get('orphans', [])
    _SR_DATED = sorted([k for k in sr if k and k[0].isdigit()])
    LATEST = _SR_DATED[-1] if _SR_DATED else None
    SECTORS = sr.get(LATEST, []) if LATEST else []
    THEME_KW = {
        'AI算力/光通信': ['光模块', '通信', 'CPO', '算力', '数据中心', '光通信', 'PCB'],
        '半导体设备': ['半导体', '芯片', '集成电路', '电子化学品'],
        '稀土·资源': ['稀土', '小金属', '有色金属', '贵金属'],
        '化工涨价': ['化学', '化工', '塑料', '橡胶', '化纤', '化肥', '农药'],
        'PCB·电子': ['PCB', '印制电路板', '消费电子', '电子制造', '元件'],
        '医药': ['医药', '制药', '生物', '医疗', '中药'],
    }

    def sector_strength(ind):
        """行业方向 -> (板块名, rps60, score, change_pct, strength) 或 None"""
        if not SECTORS:
            return None
        kws = THEME_KW.get(ind or '', [])
        if not kws:
            return None
        cands = [s for s in SECTORS if any(k in s.get('name', '') for k in kws)]
        if not cands:
            return None
        cands.sort(key=lambda s: -(s.get('rps60', 0) or 0))
        b = cands[0]
        return (b.get('name'), b.get('rps60'), b.get('score'), b.get('change_pct'), b.get('strength'))

    def funnel_match(it):
        code = it['code']
        for b in BOARDS:
            for s in b.get('stocks', []):
                if s['code'] == code:
                    return ('board', b)
        for o in _ORPHANS:
            if o['code'] == code:
                return ('orphan', o)
        sec = it.get('industry_dir') or it.get('sector_guess') or ''
        for b in BOARDS:
            if sec and (sec in b['name'] or b['name'] in sec):
                return ('board', b)
        return None

    def src_of(board, code):
        s_ = set()
        for s in board.get('stocks', []):
            for x in s.get('src', []):
                s_.add(x)
        st = [s for s in board.get('stocks', []) if s.get('code') == code]
        if st:
            for x in st[0].get('src', []):
                s_.add(x)
        return sorted(s_)

    EF_ROWS = []
    for it in q3:
        fm = funnel_match(it)
        yoy = it.get('forecast_np_yoy') or []
        ymin = yoy[0] if yoy else None
        high = (ymin is not None and ymin >= 50)
        ss = sector_strength(it.get('industry_dir') or it.get('sector_guess'))
        fk = fn = fs = fr = ff = None
        srcs = []
        if fm and fm[0] == 'board':
            b = fm[1]; fk = 'board'; fn = b['name']; fs = b.get('score')
            fr = b.get('rps60'); ff = b.get('fresh'); srcs = src_of(b, it['code'])
        elif fm and fm[0] == 'orphan':
            o = fm[1]; fk = 'orphan'; fn = '孤军·' + (o.get('board') or ''); fr = o.get('rps60')
        sn, srp, ssc, schg, sstr = ss if ss else (None, None, None, None, None)
        if high:
            if fk == 'board' or (srp is not None and srp >= 70):
                tag = '重点·高增+资金确认'
            elif srp is not None and srp >= 50:
                tag = '高增·板块偏强'
            else:
                tag = '待验证·资金未确认'
        else:
            tag = '共振·温和' if fk == 'board' else ('弱板块·观察' if fk == 'orphan' else '观察')
        rank = {'重点·高增+资金确认': 0, '高增·板块偏强': 1, '共振·温和': 2,
                '弱板块·观察': 3, '待验证·资金未确认': 4, '观察': 5}[tag]
        EF_ROWS.append({
            'code': it['code'], 'name': it['name'],
            'industry': it.get('industry_dir') or it.get('sector_guess') or '',
            'yoy': yoy, 'amt': it.get('np_amt_wan') or [],
            'status': it.get('disclosure_status') or it.get('report_type') or '',
            'appr': it.get('appointment_date'), 'core': it.get('core'),
            'high': high, 'kcfj': bool(it.get('kcfj_suspect')), 'focus': it.get('focus_tags') or [],
            'funnel_kind': fk, 'funnel_name': fn, 'funnel_score': fs, 'funnel_rps': fr, 'funnel_fresh': ff,
            'srcs': srcs, 'sec_name': sn, 'sec_rps': srp, 'sec_score': ssc, 'sec_chg': schg, 'sec_strength': sstr,
            'tag': tag, 'rank': rank,
        })
    EF_ROWS.sort(key=lambda r: (r['rank'], -((r['yoy'][0]) if (r['yoy'] and r['yoy'][0] is not None) else -1e9)))
    EF_META = {
        'period': PERIOD,
        'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'latest_snapshot': LATEST,
        'total': len(EF_ROWS),
        'high': sum(1 for r in EF_ROWS if r['high']),
        'funnel': sum(1 for r in EF_ROWS if r['funnel_kind'] == 'board'),
        'key': sum(1 for r in EF_ROWS if r['tag'] == '重点·高增+资金确认'),
        'wait': sum(1 for r in EF_ROWS if r['tag'] == '待验证·资金未确认'),
    }

    cards_html = '\n'.join(cards)
    total = len(items)
    nclusters = len(clusters)
    reso_clusters = sum(1 for c in cards if 'data-reso="1"' in c)
    geo_cards = sum(1 for c in cards if 'chip geo' in c)

    HTML = (HTML_TEMPLATE
        .replace('__UT__', esc(ut)).replace('__TOTAL__', str(total))
        .replace('__NCL__', str(nclusters)).replace('__MACRO__', str(macro_count))
        .replace('__MIL__', str(mil_count)).replace('__RESO__', str(reso_clusters))
        .replace('__GEO__', str(geo_cards)).replace('__CARDS__', cards_html)
        .replace('__EF_ROWS__', json.dumps(EF_ROWS, ensure_ascii=False).replace('</', '<\\/'))
        .replace('__EF_META__', json.dumps(EF_META, ensure_ascii=False).replace('</', '<\\/'))
        .replace('__RAWDATA__', json.dumps(raw_items, ensure_ascii=False).replace('</', '<\\/'))
        .replace('__CODE2NAME__', json.dumps(code_name_map, ensure_ascii=False).replace('</', '<\\/')))
    return HTML


if __name__ == '__main__':
    _news = L(os.environ.get('NEWS_SRC', 'news_data.json'))
    _html = generate(_news)
    _out = os.path.join(ROOT, 'news_v6.html')
    with open(_out, 'w', encoding='utf-8') as f:
        f.write(_html)
    print('OK ->', _out)
