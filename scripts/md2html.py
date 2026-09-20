#!/usr/bin/env python3
"""把 docs/ 下的 Markdown 渲染成**单文件、好看的** HTML(双击即看,浏览器直接打开)。

它**不是** markdown → html 的一比一翻译机。设计目标:

- **图在构建期就画好**。```mermaid 围栏由 `scripts/svgkit.py` 在**本地**渲染成
  `docs/diagrams/*.svg`,网页里只是一张 `<img>`。这样:
  - 打开的瞬间图就在,不用等 CDN 下载 mermaid(约 1MB)再跑一遍 JS;
  - **没网也能看**,包括别人 clone 这个仓库之后;
  - 图和网页一起进版本库,改了图 `git diff` 能看见;
  - 不需要浏览器/Node/Chromium —— 纯 Python,`pip install -r requirements.txt` 就够。

  为什么不用 `@mermaid-js/mermaid-cli`:它要拉一个 300MB 的 Chromium 和一堆系统库
  (libnss3/libgbm1/中文字体…)。这个仓库是公开的,别人 clone 下来为了重新生成网页
  还得先配好无头浏览器,不现实。**读者**的浏览器反正已经有中文字体了,
  SVG 里的中文是 `<text>` 节点,交给读者的字体渲染就行。

  图的语法支持范围见 `svgkit.py` 的模块说明;遇到不认识的写法**构建就会失败**,
  不会画出一张看着像那么回事、其实是错的图。

- **表**:所有表格自动包一层横向滚动容器 + 表头吸顶,长表格不会把版心撑破。

- **可视化块**(在 md 里用 `:::` 围栏写,详见下面 `_BLOCKS` 的注释):
  `:::stats` 数字卡、`:::compare` 并排对比、`:::steps` 步骤条、`:::timeline` 时间线、
  `:::cards` 小卡片墙、`:::note` 提示框、`:::fold` 折叠区、`:::chart` 条形图。
  这些块在 GitHub 上会退化成"两行 `:::` + 里面本来就能读的列表",信息不丢。

- **导航**:自动抽二级/三级标题做**侧边目录**(宽屏常驻、窄屏折叠),带滚动高亮;
  每页顶部有一块 hero(标题 + 首段导语 + 本节规模),顶部还有「本页速览」胶囊条,
  右下角有回到顶部。

- **提示块**:`>` 引用块按开头 emoji 上色(⚠️ 警告 / ✅ 通过 / ❌ 禁止 / 其余中性)。

用法:
    python scripts/md2html.py docs/wechat_end_to_end.md          # 生成同名 .html
    python scripts/md2html.py docs/wechat_end_to_end.md out.html # 指定输出
    python scripts/md2html.py --all docs/                        # 目录下所有 .md 全转
    python scripts/md2html.py --all docs/ --hub                  # 再生成 docs/index.html 导航页

依赖:`markdown`(requirements.txt 里已列)。画图是仓库自带的 `scripts/svgkit.py`。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from pathlib import Path

try:
    import markdown
    from markdown.extensions.toc import slugify_unicode
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖:先 pip install markdown(或 pip install -r requirements.txt)")

# svgkit.py 和本文件在同一个目录里。这里必须把本文件所在目录塞进 sys.path ——
# 直接跑 `python scripts/md2html.py` 时 "scripts/" 本来就在 sys.path 里,
# 但测试是拿 importlib 按**文件路径**加载本模块的(scripts/ 不是包),
# 那种情况下它不在,`import svgkit` 会 ModuleNotFoundError。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import svgkit  # noqa: E402

# 文档在 HTML 首页/导航里的展示顺序(md2html 只管渲染,这里是"想让人先看哪篇")
DOC_ORDER = [
    "README", "knowledge_base", "design", "deployment",
    "wechat_end_to_end", "requirements", "claude_code_prompt",
]

DOC_BLURB = {
    "knowledge_base": "知识库里到底有什么:语料来源、索引规模、检索参数、每一轮改了什么、已知限制",
    "design": "系统设计:模块划分、数据流、关键取舍(为什么是 BM25、为什么分三档答疑)",
    "deployment": "部署与 OpenClaw 接入:目录放置、换学期怎么换、故障排查表",
    "wechat_end_to_end": "微信端到端跑通手册:实测记录、逐段排障、TS 侧踩过的坑",
    "requirements": "需求文档:功能清单、验收标准、非功能需求",
    "claude_code_prompt": "2026-04 项目启动时交给 Claude Code 的那份 prompt(历史存档,含被实测推翻之处)",
    "README": "文档目录说明与时效性提示",
}

# 图片子目录:每篇 doc 的图放 docs/diagrams/<stem>-<序号>.svg。
# 放子目录而不是和 html 平铺,是为了 docs/ 根目录别被几十个 svg 淹掉。
DIAGRAM_SUBDIR = "diagrams"

CSS = """
:root{--fg:#1f2328;--muted:#59636e;--border:#d8dee4;--bg:#fff;--page:#f6f8fa;
      --code-bg:#f6f8fa;--accent:#0969da;--accent-soft:#ddf4ff;
      --warn-bg:#fff8c5;--warn-bd:#d4a72c;--ok-bg:#dafbe1;--ok-bd:#1a7f37;
      --bad-bg:#ffebe9;--bad-bd:#cf222e;--shadow:0 1px 3px rgba(27,31,36,.08);
      --sidebar:264px}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--page);color:var(--fg);
     font-family:-apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",
                 "Hiragino Sans GB","Source Han Sans SC",sans-serif;
     font-size:16px;line-height:1.78}
#progress{position:fixed;top:0;left:0;height:3px;width:0;background:var(--accent);z-index:99}
.wrap{display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr);
      gap:28px;max-width:1280px;margin:0 auto;padding:24px 20px 96px}
@media (max-width:1080px){.wrap{grid-template-columns:1fr;gap:0}.sidebar{position:static;max-height:none}}
main{background:var(--bg);border:1px solid var(--border);border-radius:10px;
     padding:40px 48px 56px;box-shadow:var(--shadow);min-width:0}
@media (max-width:640px){main{padding:24px 18px 40px}body{font-size:15.5px}.wrap{padding:12px}}

/* ---- 侧边栏:目录 ---- */
.sidebar{position:sticky;top:24px;align-self:start;max-height:calc(100vh - 48px);
         overflow:auto;font-size:.9em}
.card{background:var(--bg);border:1px solid var(--border);border-radius:10px;
      padding:14px 16px;box-shadow:var(--shadow);margin-bottom:14px}
.card h2{margin:.1em 0 .5em;font-size:.95em;border:0;padding:0;color:var(--muted);
         letter-spacing:.06em}
.toc ul{list-style:none;margin:.2em 0;padding-left:0}
.toc ul ul{padding-left:.9em;border-left:1px solid var(--border);margin-left:.35em}
.toc li{margin:.12em 0}
.toc a{display:block;padding:.2em .5em;border-radius:5px;color:var(--fg);
       text-decoration:none;border-left:2px solid transparent}
.toc a:hover{background:var(--accent-soft)}
.toc a.active{background:var(--accent-soft);border-left-color:var(--accent);
              color:var(--accent);font-weight:600}
.toc .toctitle{display:none}          /* 侧边栏标题由 .card h2 提供,不要重复 */
.docs-nav a{display:block;padding:.2em .5em;border-radius:5px;text-decoration:none;
            color:var(--muted);font-size:.95em}
.docs-nav a:hover{background:var(--accent-soft);color:var(--accent)}
.docs-nav a.here{color:var(--fg);font-weight:600}

/* ---- 正文 ---- */
h1,h2,h3,h4{line-height:1.35;margin:1.9em 0 .6em;scroll-margin-top:16px}
h1{font-size:1.9em;border-bottom:2px solid var(--border);padding-bottom:.3em;margin-top:0}
h2{font-size:1.45em;border-bottom:1px solid var(--border);padding-bottom:.25em;margin-top:2.2em}
h3{font-size:1.18em}
p,li{word-break:break-word}
a{color:var(--accent)}
ul,ol{padding-left:1.5em}
li{margin:.18em 0}
code{background:var(--code-bg);padding:.15em .4em;border-radius:4px;
     font-family:"Cascadia Mono",Consolas,"SF Mono",Menlo,monospace;font-size:.9em}
pre{background:var(--code-bg);padding:14px 16px;border-radius:8px;overflow-x:auto;
     border:1px solid var(--border);line-height:1.6}
pre code{background:none;padding:0;font-size:.88em}
hr{border:none;border-top:1px solid var(--border);margin:2.4em 0}
.anchor{float:right;color:var(--border);text-decoration:none;font-weight:400;
        padding-left:.4em;opacity:0;transition:opacity .12s}
h2:hover .anchor,h3:hover .anchor{opacity:1}
.anchor:hover{color:var(--accent)}

/* ---- 每页顶部的 hero ---- */
.hero{background:linear-gradient(135deg,#0969da14,#0969da04 62%,rgba(255,255,255,0));
      border:1px solid var(--border);border-radius:12px;padding:22px 26px 18px;
      margin:0 0 1.5em}
.hero .kicker{color:var(--accent);font-size:.78em;font-weight:700;letter-spacing:.12em;
      text-transform:uppercase}
.hero h1{border:0;margin:.1em 0 .3em;font-size:1.72em;padding:0;line-height:1.3}
.hero .lead{color:var(--muted);margin:0;font-size:1.02em}
.hero .meta{margin-top:.85em;padding-top:.7em;border-top:1px solid var(--border);
      display:flex;flex-wrap:wrap;gap:.3em 1.5em;color:var(--muted);font-size:.84em}
.hero .meta b{color:var(--fg);font-variant-numeric:tabular-nums}

/* ---- 表格:横向滚动 + 表头吸顶 ---- */
.table-wrap{overflow-x:auto;margin:1.1em 0;border:1px solid var(--border);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:.94em;margin:0}
th,td{border:0;border-bottom:1px solid var(--border);border-right:1px solid var(--border);
      padding:8px 12px;text-align:left;vertical-align:top}
th:last-child,td:last-child{border-right:0}
tr:last-child td{border-bottom:0}
th{background:var(--code-bg);font-weight:600;position:sticky;top:0;z-index:1}
tr:nth-child(even) td{background:#fcfcfd}
tbody tr:hover td{background:var(--accent-soft)}

/* ---- 提示块(按 emoji 上色) ---- */
blockquote{margin:1.1em 0;padding:.7em 1.1em;color:var(--muted);
           border-left:4px solid var(--border);background:#fbfcfd;border-radius:0 6px 6px 0}
blockquote p{margin:.35em 0}
blockquote.warn{border-left-color:var(--warn-bd);background:var(--warn-bg);color:#3d2f00}
blockquote.ok{border-left-color:var(--ok-bd);background:var(--ok-bg);color:#0b3d1a}
blockquote.bad{border-left-color:var(--bad-bd);background:var(--bad-bg);color:#4c0519}

/* ---- 图:构建期画好的 svg ---- */
figure.diagram,figure.pic{margin:1.4em 0;padding:14px;border:1px solid var(--border);
                          border-radius:8px;background:#fcfcfd}
figure.diagram img{max-width:100%;height:auto;display:block;margin:0 auto}
/* 宽图**按原尺寸横向滚动,不缩放**。
   缩到版心宽度的后果是字号一起缩:一张 1655px 宽的图塞进 1050px 的版心,
   图里 13px 的字只剩 8px —— 一屏是放下了,但没人读得下去。
   宁可让读者横向拉一下,也不能把字缩没。
   窄图(≤860px)照旧居中自适应,那种情况下缩放比例 ≥1,不存在缩字问题。 */
figure.diagram.wide{overflow-x:auto}
figure.diagram.wide img{max-width:none;margin:0}
figure.diagram figcaption{color:var(--fg);font-size:.9em;margin-bottom:.7em;
     text-align:center;font-weight:600}
figure.diagram.wide figcaption{text-align:left}
/* 滚动提示贴在框外:它不该跟着图一起滚走 */
figure.diagram .wide-hint{position:sticky;left:0;margin:.6em 0 0;color:var(--muted);
     font-size:.84em}
figure.pic figcaption{color:var(--muted);font-size:.88em;margin-top:.6em;text-align:center}
figure.pic img{max-width:100%;display:block;margin:0 auto;border-radius:6px}
details.mermaid-src{margin-top:.8em;font-size:.85em;border-top:1px dashed var(--border);
     padding-top:.5em}
details.mermaid-src summary{cursor:pointer;color:var(--muted)}
details.mermaid-src pre{margin:.5em 0 0}
/* 图表框**贴着图**走:图是固定 620px 宽的,框撑满版心的话右边会空一大块,
   看着像图没画完 */
figure.chart{margin:1.4em auto;padding:10px 12px;border:1px solid var(--border);
     border-radius:8px;background:#fcfcfd;overflow-x:auto;width:fit-content;max-width:100%}
figure.chart svg{max-width:100%;height:auto;display:block}

/* ---- :::stats 数字卡 ---- */
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
       gap:12px;margin:1.3em 0}
.stat{border:1px solid var(--border);border-left:4px solid var(--accent);
      border-radius:8px;padding:13px 16px;background:var(--bg)}
.stat .v{font-size:1.6em;font-weight:700;line-height:1.15;color:var(--accent);
      font-variant-numeric:tabular-nums}
.stat .l{font-weight:600;margin-top:.15em;font-size:.95em}
.stat .n{color:var(--muted);font-size:.84em;margin-top:.15em;line-height:1.55}

/* ---- :::compare 并排对比 ---- */
.cmp{display:grid;gap:14px;margin:1.3em 0}
.cmp.c2{grid-template-columns:repeat(2,minmax(0,1fr))}
.cmp.c3{grid-template-columns:repeat(3,minmax(0,1fr))}
@media (max-width:900px){.cmp.c2,.cmp.c3{grid-template-columns:1fr}}
.cmp .col{border:1px solid var(--border);border-top:3px solid var(--accent);
      border-radius:8px;padding:6px 18px 10px;background:var(--bg);min-width:0}
.cmp .col.tone1{border-top-color:#1a7f37}
.cmp .col.tone2{border-top-color:#9a6700}
.cmp .col > :first-child{margin-top:.6em}
.cmp .col h3{margin:.4em 0 .5em;font-size:1.03em}
.cmp .col ul,.cmp .col ol{padding-left:1.25em}

/* ---- :::steps 步骤条 ---- */
.steps{counter-reset:st;display:grid;gap:10px;margin:1.3em 0}
.step{border:1px solid var(--border);border-radius:8px;background:var(--bg);
      padding:11px 16px 11px 54px;position:relative}
.step:before{counter-increment:st;content:counter(st);
      position:absolute;left:14px;top:11px;width:26px;height:26px;border-radius:50%;
      background:var(--accent);color:#fff;font-weight:700;font-size:.82em;
      display:flex;align-items:center;justify-content:center}
.step .t{font-weight:600}
.step .d{color:var(--muted);font-size:.91em;margin-top:.1em}

/* ---- :::timeline 时间线 ---- */
.timeline{margin:1.3em 0 1.3em 6px;padding-left:22px;border-left:2px solid var(--border)}
.tl{position:relative;padding:0 0 1.05em 18px}
.tl:last-child{padding-bottom:0}
.tl:before{content:"";position:absolute;left:-30px;top:.5em;width:11px;height:11px;
      border-radius:50%;background:var(--accent);border:2px solid var(--bg)}
.tl .when{color:var(--muted);font-size:.8em;font-variant-numeric:tabular-nums}
.tl .t{font-weight:600}
.tl .d{color:var(--muted);font-size:.91em}

/* ---- :::cards 小卡片墙 ---- */
.mini{display:grid;grid-template-columns:repeat(auto-fill,minmax(224px,1fr));
      gap:12px;margin:1.3em 0}
.mini > div{border:1px solid var(--border);border-radius:8px;padding:12px 15px;
      background:var(--bg)}
.mini .t{font-weight:600;margin-bottom:.15em}
.mini .d{color:var(--muted);font-size:.89em;line-height:1.6}

/* ---- :::note 提示框 / :::fold 折叠 ---- */
.note{border:1px solid var(--border);border-left:4px solid var(--accent);
      border-radius:0 8px 8px 0;background:#fbfcfd;padding:.75em 1.2em;margin:1.2em 0}
.note .nt{font-weight:600;margin-bottom:.15em;color:var(--accent)}
.note > :last-child{margin-bottom:.35em}
details.fold{border:1px solid var(--border);border-radius:8px;background:var(--bg);
      margin:1.1em 0;padding:0 16px}
details.fold > summary{cursor:pointer;padding:11px 0;font-weight:600;color:var(--accent)}
details.fold[open] > summary{border-bottom:1px solid var(--border);margin-bottom:.6em}

/* ---- 本页速览胶囊 ---- */
.quick{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 1.6em}
.quick a{font-size:.84em;padding:.22em .7em;border:1px solid var(--border);
         border-radius:999px;text-decoration:none;color:var(--muted);background:var(--bg)}
.quick a:hover{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}

/* ---- 页脚与回到顶部 ---- */
.foot{margin-top:3em;padding-top:1em;border-top:1px solid var(--border);
      color:var(--muted);font-size:.85em;display:flex;flex-wrap:wrap;gap:.4em 1.2em;
      justify-content:space-between}
#totop{position:fixed;right:22px;bottom:22px;width:42px;height:42px;border-radius:50%;
       border:1px solid var(--border);background:var(--bg);color:var(--accent);cursor:pointer;
       box-shadow:var(--shadow);font-size:17px;display:none}
#totop.show{display:block}

/* ---- 首页(导航页) ---- */
.hub-hero{background:linear-gradient(135deg,#0969da11,#0969da05);border:1px solid var(--border);
          border-radius:12px;padding:26px 28px;margin-bottom:22px}
.hub-hero h1{border:0;margin:0 0 .3em}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.cards a.card-link{display:block;text-decoration:none;color:var(--fg);background:var(--bg);
     border:1px solid var(--border);border-radius:10px;padding:16px 18px;box-shadow:var(--shadow);
     transition:transform .12s,border-color .12s}
.cards a.card-link:hover{transform:translateY(-2px);border-color:var(--accent)}
.cards .t{font-weight:600;margin-bottom:.35em;font-size:1.02em}
.cards .d{color:var(--muted);font-size:.88em;line-height:1.6}
.cards .m{color:var(--muted);font-size:.78em;margin-top:.6em}

@media print{body{background:#fff}#progress,#totop,.sidebar{display:none}
  .wrap{display:block;max-width:none;padding:0}main{border:0;box-shadow:none;padding:0}
  .table-wrap{overflow:visible}th{position:static}
  details.fold{border:0;padding:0}details.fold > summary{color:var(--fg)}
  details.fold > *{display:revert!important}}
"""

# 滚动高亮 + 进度条 + 回到顶部:纯原生 JS,不引任何库
SCRIPT = """
(function(){
  var bar=document.getElementById('progress'),top=document.getElementById('totop');
  function onScroll(){
    var h=document.documentElement,sh=h.scrollHeight-h.clientHeight;
    bar.style.width=(sh>0?(h.scrollTop/sh*100):0)+'%';
    top.classList.toggle('show',h.scrollTop>500);
  }
  document.addEventListener('scroll',onScroll,{passive:true});onScroll();
  top.addEventListener('click',function(){window.scrollTo({top:0,behavior:'smooth'});});
  var heads=[].slice.call(document.querySelectorAll('main h2[id],main h3[id]'));
  var links={};[].forEach.call(document.querySelectorAll('.toc a'),function(a){
    links[decodeURIComponent(a.getAttribute('href').slice(1))]=a;});
  function spy(){
    var cur=null;
    for(var i=0;i<heads.length;i++){
      if(heads[i].getBoundingClientRect().top<=90) cur=heads[i].id; else break;
    }
    for(var k in links) links[k].classList.remove('active');
    if(cur&&links[cur]) links[cur].classList.add('active');
  }
  document.addEventListener('scroll',spy,{passive:true});spy();
})();
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="generator" content="scripts/md2html.py">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><text y='14' font-size='14'>%F0%9F%93%98</text></svg>">
<style>{css}</style>
</head>
<body>
<div id="progress"></div>
<div class="wrap">
<aside class="sidebar">
{sidebar}
</aside>
<main>
{hero}
{quick}
{body}
<div class="foot">
  <span>{src} · 内容指纹 <code>{stamp}</code></span>
  <span>内容与 Markdown 一致,这里的图/表/目录是构建期渲染出来的</span>
</div>
</main>
</div>
<button id="totop" title="回到顶部">↑</button>
{tail}
</body>
</html>
"""

# 占位符:抠出来的块先换成它,等 markdown 转完再填回去。
# 用一对显眼的大写标记而**不是** HTML 注释:`markdown` 会把裸注释原样留下,
# 但 `<!-- x -->` 被包进 <p> 之后行为随版本变,不值得赌。
_PH_RE = re.compile(r"ZZPLACE(\d+)ZZPLACE")


def _esc(t: str) -> str:
    return html.escape(t, quote=False)


def _first_h1(text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else fallback


#: 不像正文段落开头的东西:标题、引用块、表格、围栏、HTML、图片、`:::` 块、
#: 以及**真正的**列表记号(`- x` / `* x` / `1. x` —— 注意后面得跟空格)。
_NOT_PARA = re.compile(r"^(?:[#>\|<`!]|:::|[-*+]\s|\d+[.)]\s)")


def _para_start(s: str) -> bool:
    """这一行像不像正文段落的开头。

    别写成"以 `*` 开头就当列表":markdown 里 `**加粗**` 也是 `*` 开头,
    照那个写法一大票段落会被当成列表跳过 —— 这个坑在 `_take_lead` 里踩实过,
    后果是整段正文被当成"导语"删掉(见下)。
    """
    return bool(s.strip()) and not _NOT_PARA.match(s.strip())


def _plain(t: str) -> str:
    """剥掉行内 markdown 记号。既给 meta description 用,也给 svg 里的
    `<text>` 用(那里只吃纯文本)—— 所以两种场合都别留反引号/星号/方括号。"""
    return re.sub(r"[`*\[\]]", "", t).strip()


def _first_para(text: str) -> str:
    """拿第一段正文当 meta description(不删任何东西)。"""
    for raw in text.splitlines():
        if _para_start(raw):
            return _plain(raw)[:120]
    return ""


def _take_lead(text: str) -> tuple[str, str]:
    """抽出一级标题后面那段当 hero 导语,并把它**从正文里删掉**。返回 (导语, 正文)。

    删这一步是必须的:hero 把首段原样显示了一遍,正文里再来一遍,
    页面顶部就成了同一句话说两遍 —— 一眼就看出是"两套东西拼起来的"。

    但**删内容这件事不能靠猜**。第一版是"从头扫,遇到第一行像段落的就删到下一个
    空行为止",结果 `deployment.md` 里那行 `**【当前】代码和数据…**` 因为
    `*` 开头被判成列表跳过,一路跳到 ` ```mermaid ` 之后,把 `flowchart LR`
    和所有节点定义当"导语"删了 —— 图直接渲染失败,而**失败信息里那句
    "第 1 张图渲染失败"完全指不到真正的原因**。

    所以现在只在**结构上可证明它就是一段独立导语**时才动手:
      1. 紧跟在一级标题后面(中间只隔空行);
      2. 它是普通段落,不是引用/列表/围栏/表格/HTML;
      3. 它自己构成一整段(前后都是空行);
      4. 它后面紧跟的是**另一个小节标题**(或者到头了)。
    四条有任意一条不满足,就一个字节都不动。
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not _para_start(lines[i]):
        return "", text
    j = i
    while j < len(lines) and lines[j].strip():
        j += 1
    k = j
    while k < len(lines) and not lines[k].strip():
        k += 1
    if k < len(lines) and not lines[k].lstrip().startswith("#"):
        return "", text
    lead = _plain(" ".join(l.strip() for l in lines[i:j]))
    return lead, "\n".join(lines[:i] + lines[j:])


# ===========================================================================
# 抠块:```mermaid 围栏 与 ::: 围栏
# ===========================================================================

def _extract_mermaid(text: str) -> tuple[str, list[tuple[str, str]]]:
    """把 ```mermaid 围栏抠出来,换成占位符。返回 (正文, [(源码, 图题), ...])。

    图题从围栏里那行 `%% caption: …` 取(mermaid 的注释语法,GitHub 也会忽略它)。
    取图题是为了给 `<img>` 写 alt —— 没有 alt 的图在**屏幕阅读器**里等于不存在,
    而这批文档里图是主要的信息载体,不是装饰。

    必须在 markdown 转换**之前**抠:否则围栏会被包成 <pre><code>,
    而我们要的是自己画出来的那张 svg。
    """
    blocks: list[tuple[str, str]] = []

    def take(m: re.Match) -> str:
        code = m.group(1).strip("\n")
        cap = ""
        cm = re.search(r"^[ \t]*%%[ \t]*caption:[ \t]*(.+?)[ \t]*$", code, re.M)
        if cm:
            cap = cm.group(1)
        blocks.append((code, cap))
        return f"\n\nZZPLACE{len(blocks) - 1}ZZPLACE\n\n"

    return re.sub(r"```mermaid[ \t]*\n(.*?)```", take, text, flags=re.S), blocks


def _extract_blocks(text: str, base: int = 0) -> tuple[str, list[tuple[str, str, str]]]:
    """抠出 `:::kind 参数` … `:::` 块,换成占位符。返回 (正文, [(kind, 参数, 内容)])。

    `base` 是占位符的起始编号。**必须传**:`_extract_mermaid` 已经用掉了
    0..n-1,这里再从 0 数就会撞号 —— 撞号的后果是**静默换错**:
    图上那个位置冒出来的是一张图而不是提示块,而且没有任何报错。
    (调用方 `_postprocess` 还有一道"每个占位符都得被用上"的断言兜底。)

    逐行扫而不用一条正则:得知道自己在不在 ``` 围栏**里面** ——
    文档里讲语法时难免写一行 `:::stats` 当例子,正则会把那个代码块从中间劈开。
    """
    lines = text.splitlines()
    out: list[str] = []
    blocks: list[tuple[str, str, str]] = []
    fence = False
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.lstrip().startswith("```"):
            fence = not fence
            out.append(ln)
            i += 1
            continue
        m = None if fence else re.match(r"^:::[ \t]*([a-z]+)[ \t]*(.*)$", ln)
        if not m:
            # 以 `:::` 开头、却不是合法块标记 —— 多半是块名拼错了(写成大写或中文),
            # 或者收尾那个 `:::` 少写了。放过去的话它**被当成普通段落**,
            # 页面上出现一行明晃晃的 `:::...`,而构建仍然"成功"。
            # 这正是本项目最忌讳的失败方式:看不出错,但确实是错的。
            if not fence and ln.startswith(":::"):
                raise ValueError(
                    f"第 {i + 1} 行以 `:::` 开头,但不是合法的块标记:{ln[:40]!r}。"
                    f"块名只能是小写英文,可用的是:{'、'.join(sorted(_BLOCKS))}"
                    f"(如果这里是想在正文里写这个记号,请缩进或加反引号)"
                )
            out.append(ln)
            i += 1
            continue
        kind, args = m.group(1), m.group(2).strip()
        body: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip() != ":::":
            if re.match(r"^:::[ \t]*[a-z]", lines[i]):
                raise ValueError(
                    f"第 {i + 1} 行:不支持 `:::` 嵌套。把一个块拆成两个,"
                    f"或者把内层那个换成普通列表。")
            body.append(lines[i])
            i += 1
        if i >= len(lines):
            raise ValueError(f"`:::{kind}`(第 {i - len(body)} 行开始)没有收尾的 `:::`")
        i += 1
        n = base + len(blocks)
        blocks.append((kind, args, "\n".join(body)))
        out.append("")
        out.append(f"ZZPLACE{n}ZZPLACE")
        out.append("")
    return "\n".join(out), blocks


# ===========================================================================
# ::: 块的渲染
# ===========================================================================

def _md_frag(text: str) -> str:
    """把一小段 markdown 单独渲染。**每次都要新建实例** —— Markdown 对象是有状态的。"""
    m = markdown.Markdown(extensions=["tables", "sane_lists", "attr_list"])
    return m.convert(text)


def _inline(text: str) -> str:
    """渲染一小段 markdown 并剥掉外层 <p>,用来做卡片标题这种行内场合。"""
    out = _md_frag(text.strip()).strip()
    if out.startswith("<p>") and out.endswith("</p>") and out.count("<p>") == 1:
        out = out[3:-4]
    return out


def _head(args: str) -> tuple[str, dict[str, str]]:
    """解析块头参数:`:::chart 检索命中率 | unit=条` → ('检索命中率', {'unit': '条'})。"""
    title, opts = "", {}
    for tok in re.split(r"[|;]", args):
        t = tok.strip()
        if not t:
            continue
        if re.match(r"^[a-z_]+=", t):
            k, v = t.split("=", 1)
            opts[k.strip()] = v.strip()
        elif not title:
            title = t
    return title, opts


def _bullets(body: str, kind: str) -> list[list[str]]:
    """把 `- 甲 | 乙 | 丙` 解析成 [['甲','乙','丙'], ...]。

    统一用竖线分列而不是缩进/markdown 表格:竖线在源码里也是一行一项,
    没渲染的 GitHub 上照样读得懂。
    """
    rows: list[list[str]] = []
    for i, raw in enumerate(body.splitlines(), 1):
        s = raw.strip()
        if not s:
            continue
        if not re.match(r"^[-*]\s+", s):
            raise ValueError(f"`:::{kind}` 里每行都得是 `- …`,第 {i} 行是 {s[:40]!r}")
        rows.append([c.strip() for c in re.sub(r"^[-*]\s+", "", s).split("|")])
    if not rows:
        raise ValueError(f"`:::{kind}` 是空的")
    return rows


def _blk_stats(args: str, body: str) -> str:
    tiles = []
    for r in _bullets(body, "stats"):
        if len(r) < 2:
            raise ValueError(f"`:::stats` 的行要写成 `- 数值 | 标签 | 说明(可省)`,现在是 {r!r}")
        note = f'<div class="n">{_inline(r[2])}</div>' if len(r) > 2 and r[2] else ""
        tiles.append(f'<div class="stat"><div class="v">{_inline(r[0])}</div>'
                     f'<div class="l">{_inline(r[1])}</div>{note}</div>')
    return '<div class="stats">' + "".join(tiles) + "</div>"


def _blk_compare(args: str, body: str) -> str:
    cols = []
    for part in re.split(r"^[ \t]*---[ \t]*$", body, flags=re.M):
        part = part.strip("\n")
        if not part.strip():
            continue
        head, rest = "", part
        m = re.match(r"^###[ \t]*(.+?)[ \t]*\n(.*)$", part, re.S)
        if m:
            head, rest = m.group(1), m.group(2)
        tone = f" tone{len(cols) % 3}" if len(cols) % 3 else ""
        cols.append(f'<div class="col{tone}">'
                    + (f"<h3>{_inline(head)}</h3>" if head else "")
                    + _md_frag(rest) + "</div>")
    if len(cols) < 2:
        raise ValueError("`:::compare` 至少要两栏 —— 用单独一行的 `---` 分隔各栏")
    return f'<div class="cmp c{min(len(cols), 3)}">' + "".join(cols) + "</div>"


def _blk_steps(args: str, body: str) -> str:
    out = []
    for r in _bullets(body, "steps"):
        d = f'<div class="d">{_inline(r[1])}</div>' if len(r) > 1 and r[1] else ""
        out.append(f'<div class="step"><div class="t">{_inline(r[0])}</div>{d}</div>')
    return '<div class="steps">' + "".join(out) + "</div>"


def _blk_timeline(args: str, body: str) -> str:
    out = []
    for r in _bullets(body, "timeline"):
        t = f'<div class="t">{_inline(r[1])}</div>' if len(r) > 1 and r[1] else ""
        d = f'<div class="d">{_inline(r[2])}</div>' if len(r) > 2 and r[2] else ""
        out.append(f'<div class="tl"><div class="when">{_inline(r[0])}</div>{t}{d}</div>')
    return '<div class="timeline">' + "".join(out) + "</div>"


def _blk_cards(args: str, body: str) -> str:
    out = []
    for r in _bullets(body, "cards"):
        d = f'<div class="d">{_inline(r[1])}</div>' if len(r) > 1 and r[1] else ""
        out.append(f'<div><div class="t">{_inline(r[0])}</div>{d}</div>')
    return '<div class="mini">' + "".join(out) + "</div>"


def _blk_note(args: str, body: str) -> str:
    title, _ = _head(args)
    nt = f'<div class="nt">{_inline(title)}</div>' if title else ""
    return f'<div class="note">{nt}{_md_frag(body)}</div>'


def _blk_fold(args: str, body: str) -> str:
    title, _ = _head(args)
    return (f'<details class="fold"><summary>{_inline(title) if title else "展开"}</summary>'
            + _md_frag(body) + "</details>")


def _blk_chart(args: str, body: str) -> str:
    title, opts = _head(args)
    pts: list[tuple[str, float]] = []
    for r in _bullets(body, "chart"):
        if len(r) < 2:
            raise ValueError(f"`:::chart` 的行要写成 `- 标签 | 数值`,现在是 {r!r}")
        try:
            v = float(r[1])
        except ValueError:
            raise ValueError(f"`:::chart` 的数值 {r[1]!r} 不是数字") from None
        pts.append((_plain(r[0]), v))
    # 图直接内联(svg 很小),不落成文件 —— 少一次请求,也不怕路径写错
    svg = svgkit.bar_chart(pts, title=title, unit=opts.get("unit", ""))
    return f'<figure class="chart">{svg}</figure>'


#: 块类型 → 渲染函数。加新块只需要往这里塞一个函数,别的地方都不用动。
_BLOCKS = {
    "stats": _blk_stats,
    "compare": _blk_compare,
    "steps": _blk_steps,
    "timeline": _blk_timeline,
    "cards": _blk_cards,
    "note": _blk_note,
    "fold": _blk_fold,
    "chart": _blk_chart,
}


def _render_block(kind: str, args: str, body: str, where: str) -> str:
    fn = _BLOCKS.get(kind)
    if fn is None:
        raise ValueError(f"{where}:不认识的块 `:::{kind}`。"
                         f"可用的是:{'、'.join(sorted(_BLOCKS))}")
    try:
        return fn(args, body)
    except ValueError as exc:
        raise ValueError(f"{where}:{exc}") from None


# ===========================================================================
# 图 / 正文后处理
# ===========================================================================

#: 正文栏的可用宽度约 852px(1280 版心 − 264 侧栏 − 间距 − 双重内边距)。
#: 超过这个值就按原尺寸横向滚动,不缩放 —— 阈值定在 1080 而不是 852:
#: 1080 的图缩到 852 是 0.79 倍,13px 的字还剩 10px,读得下去;
#: 再宽就不行了,缩下去等于把字缩没。见 CSS 里的 `figure.diagram.wide`。
_WIDE = 1080.0


def _render_diagram(code: str, caption: str, svg_rel: str, svg: str) -> str:
    """一张构建期画好的图 + 一个「Mermaid 源码」折叠区。

    折叠区留着:读者想改这张图时,复制走的就是原始语法,不用去翻 md 文件。
    """
    m = re.search(r'<svg[^>]*?\bwidth="([0-9.]+)"', svg)
    wide = bool(m) and float(m.group(1)) > _WIDE
    cap = f"<figcaption>{_esc(caption)}</figcaption>" if caption else ""
    # 宽图横向滚动,**必须**在图上或图下明说。
    # 不说的话读者只会看到图的左三分之二,以为图就长这样 ——
    # 右边被裁掉的那部分(往往是流程的终点)永远不会被发现。
    hint = ('<p class="wide-hint">↔ 这张图比正文宽:右边还有内容,'
            "把鼠标放在图上横向滚动(或按住 Shift 滚滚轮)就能看完。</p>") if wide else ""
    return (
        f'<figure class="diagram{" wide" if wide else ""}">\n'
        f"{cap}"
        f'<img src="{svg_rel}" alt="{_esc(caption or "结构图")}" loading="lazy">\n'
        f"{hint}"
        '<details class="mermaid-src"><summary>这张图的源码(想改的话复制这段)</summary>'
        f"<pre><code>{html.escape(code)}</code></pre></details>\n"
        "</figure>"
    )


def _postprocess(body: str, parts: list[str]) -> str:
    # 1) 占位符 → 图 / 可视化块
    used: set[int] = set()

    def fill(m: re.Match) -> str:
        i = int(m.group(1))
        if i >= len(parts):
            raise ValueError(f"正文里的占位符 ZZPLACE{i} 没有对应的块"
                             f"(一共只有 {len(parts)} 块)")
        used.add(i)
        return parts[i]

    # 先吃 "<p>占位符</p>" 这个整体:留着 <p> 的话图会被套进段落里,
    # figure 是块级元素,浏览器会**自动把 <p> 提前闭合** —— 出来的 DOM 和写的不一样
    body = re.sub(r"<p>\s*ZZPLACE(\d+)ZZPLACE\s*</p>", fill, body)
    body = _PH_RE.sub(fill, body)
    if "ZZPLACE" in body:
        raise ValueError("有占位符没被替换掉,说明 markdown 把它拆开了(多半是缩进问题)")
    # 每个抠出来的块都必须正好被用上一次。少了这一条,两处编号一旦撞号
    # (比如图和 `:::` 块各自从 0 开始数),页面上的表现是**一张图顶掉了提示块**,
    # 而且不报任何错 —— 只有人眼盯着才看得出来。
    if used != set(range(len(parts))):
        missing = sorted(set(range(len(parts))) - used)
        raise ValueError(f"有块没被放进正文(占位符编号撞号了):{missing}")

    # 2) 表格包一层滚动容器(不改表格本身,样式由 CSS 接)
    body = body.replace("<table>", '<div class="table-wrap"><table>')
    body = body.replace("</table>", "</table></div>")

    # 3) 标题右上角加锚点(方便把某小节链接发出去)
    def anchor(m: re.Match) -> str:
        tag, hid, inner = m.group(1), m.group(2), m.group(3)
        return (f'<{tag} id="{hid}">{inner}'
                f'<a class="anchor" href="#{hid}" title="链接到这一节">#</a></{tag}>')

    # 捕获整段标签名(h2/h3),不是只捕获那个数字 ——
    # 写成 `([23])` 时 group(1) 只是 "2",拼回去就成了 <2 id=…> 和 </2>。
    # 这个错误浏览器不会报:未知元素按行内元素处理,于是**每一节的标题都掉了样式**
    # (h2/h3 的 CSS 一条都不生效),滚动高亮找 document.querySelectorAll('h2[id]')
    # 也一个都找不到 —— 整个侧边目录变成死的,而页面看上去"只是朴素了点"。
    body = re.sub(r'<(h[23]) id="([^"]+)">(.*?)</\1>', anchor, body, flags=re.S)

    # 4) 正文里手写的图片 → 带题注的图(题注取 alt;alt 就是 markdown 里那对方括号)
    def figure(m: re.Match) -> str:
        img = m.group(1)
        alt = re.search(r'alt="([^"]*)"', img)
        cap = f"<figcaption>{alt.group(1)}</figcaption>" if alt else ""
        return f'<figure class="pic">{img}{cap}</figure>'

    body = re.sub(r"<p>(<img[^>]*?>)</p>", figure, body)

    # 5) 引用块按开头 emoji 上色
    #    注意要看**第一个 <p> 里**的第一个字符:直接看 inner 的话拿到的是 "<p",
    #    永远匹配不上 emoji(`⚠️` 只能靠"在不在前 20 字里"这种侥幸,最容易漏)
    def callout(m: re.Match) -> str:
        inner = m.group(1)
        first = re.sub(r"^\s*<p>", "", inner, count=1).lstrip()
        cls = ""
        if first.startswith("⚠"):
            cls = " warn"
        elif first.startswith("✅"):
            cls = " ok"
        elif first.startswith("❌"):
            cls = " bad"
        # 必须写全 `class="…"`:只写 `{cls}` 出来的是 `<blockquote warn>`,
        # 那是个**属性名**不是 class,CSS 的 `blockquote.warn` 一条都匹配不上
        attr = f' class="{cls.strip()}"' if cls else ""
        return f"<blockquote{attr}>{inner}</blockquote>"

    body = re.sub(r"<blockquote>(.*?)</blockquote>", callout, body, flags=re.S)
    return body


def _quick_strip(toc_html: str) -> str:
    """顶部『本页速览』胶囊条 —— 只取二级标题。"""
    items = re.findall(r'<li><a href="(#[^"]+)">([^<]+)</a>', toc_html)
    if len(items) < 3:
        return ""
    chips = "".join(f'<a href="{h}">{_esc(t)}</a>' for h, t in items[:14])
    return f'<nav class="quick">{chips}</nav>'


def _hero(stem: str, title: str, lead: str, n_dia: int, n_tbl: int, n_sec: int) -> str:
    meta = [f"<span><b>{n_sec}</b> 个小节</span>"]
    if n_dia:
        meta.append(f"<span><b>{n_dia}</b> 张图</span>")
    if n_tbl:
        meta.append(f"<span><b>{n_tbl}</b> 张表</span>")
    lead_html = f'<p class="lead">{_esc(lead)}</p>' if lead else ""
    return (f'<header class="hero"><div class="kicker">{_esc(stem)}</div>'
            f"<h1>{_esc(title)}</h1>{lead_html}"
            f'<div class="meta">{"".join(meta)}</div></header>')


def _sidebar(toc_html: str, nav: list[tuple[str, str]], here: str | None) -> str:
    out = []
    if toc_html:
        out.append(f'<div class="card toc"><h2>本页目录</h2>{toc_html}</div>')
    if nav:
        links = []
        for stem, title in nav:
            cls = ' class="here"' if stem == here else ""
            links.append(f'<a href="{stem}.html"{cls}>{_esc(title)}</a>')
        out.append('<div class="card docs-nav"><h2>文档导航</h2>' + "".join(links) + "</div>")
    return "\n".join(out)


def _tail() -> str:
    """页脚脚本。**不再引 mermaid 的 CDN** —— 图在构建期就画成 svg 了。

    原先这里会 `import("https://cdn.jsdelivr.net/npm/mermaid@11/…")`。
    去掉它不只是省 1MB:留着的话,打开的瞬间图是**空白**的,得等网络把 mermaid
    拉下来再渲染一遍;没网就只剩一个源码块。改成构建期出图之后,这两件事都没了。
    """
    return f"<script>{SCRIPT}</script>"


def _fingerprint(md_path: Path) -> str:
    """源 md 的内容短指纹,用来印在页脚。

    这里原来印的是 `datetime.now()`("生成于 2026-09-20 21:16"),两个坏处:
      1) 跑一次 `--all` 会把 **8 份 HTML 全部改写**(内容没变、只有时间戳变),
         于是"我改了 md 之后有没有忘记重新生成 HTML"没法用 `git diff` 判断 ——
         diff 里 8 个文件全红,看不出到底哪份真的过期了;
      2) 同一份 md 在两个时刻/两台机器上生成出**字节不同**的 HTML,没法比对、
         也没法验证"这两边的网页层是一致的"。
    换内容指纹后生成是幂等的:md 没变 → HTML 一个字节都不变,
    `python scripts/md2html.py --all docs/ --hub && git diff --stat docs/`
    就能直接回答"网页层是否与 md 同步"。指纹本身也是给读者的线索:
    两份 HTML 指纹相同 ⇔ 它们的源 md 逐字节相同。
    """
    return hashlib.sha256(md_path.read_bytes()).hexdigest()[:8]


def convert(
    md_path: Path,
    html_path: Path,
    nav: list[tuple[str, str]] | None = None,
    here: str | None = None,
    src_label: str | None = None,
    kicker: str | None = None,
) -> None:
    """把一个 md 渲染成一页 HTML,顺手把里面的图写成 svg。

    `here` 是"侧边栏里哪一项算当前页",默认取文件名主干。只有 hub 需要显式传:
    它的源文件叫 `_hub_input.md`,但对外是 `index.html`。

    `src_label` 是页脚里那句"源文件 …"。同样只有 hub 要改 —— 否则页脚会印出
    `源文件 docs/_hub_input.md`,而那个文件转完就删了:读者真去 docs/ 里找是找不到的。

    `kicker` 是 hero 左上角那行小字,默认取文件名主干(如 `DEPLOYMENT`)。
    这里**必须**能覆盖:hub 的 `src_label` 挡住了页脚,hero 却是从 `md_path.stem`
    直接取的,于是首页顶上一直挂着一行 `_hub_input` —— 一个内部临时文件名,
    出现在了对外发布的页面上。
    """
    text = md_path.read_text(encoding="utf-8")
    title = _first_h1(text, md_path.stem)
    where = f"docs/{md_path.name}" if md_path.parent.name == "docs" else md_path.name

    # 一级标题交给 hero 渲染,正文里就不要再来一遍了(否则页面顶部两个同名大标题)
    text = re.sub(r"^#\s+.+$", "", text, count=1, flags=re.M)
    lead, text = _take_lead(text)
    desc = _plain(lead)[:120] if lead else _first_para(text)

    text, diagrams = _extract_mermaid(text)
    # base=len(diagrams):`:::` 块的占位符编号必须**接在图的后面**,
    # 不能各自从 0 数(见 `_extract_blocks` 的说明)
    text, blocks = _extract_blocks(text, base=len(diagrams))

    # 图先把 svg 画出来。**画不出来就直接抛** —— 与其生成一张看着像那么回事、
    # 其实是错的图,不如让构建失败:读者不会怀疑一张图,他会照着错的图推下去。
    svg_dir = html_path.parent / DIAGRAM_SUBDIR
    parts: list[str] = []
    written: set[str] = set()
    for i, (code, cap) in enumerate(diagrams):
        name = f"{md_path.stem}-{i}.svg"
        try:
            svg = svgkit.render(code, title=cap or f"{title} · 图 {i + 1}")
        except Exception as exc:
            raise ValueError(f"{where}:第 {i + 1} 张图渲染失败 —— {exc}") from None
        svg_dir.mkdir(parents=True, exist_ok=True)
        (svg_dir / name).write_text(svg, encoding="utf-8", newline="\n")
        written.add(name)
        parts.append(_render_diagram(code, cap, f"{DIAGRAM_SUBDIR}/{name}", svg))

    # 清掉这个文件上一轮留下、这一轮已经不存在的图 ——
    # 少了这一步,删掉一张图之后那个 svg 会一直躺在 docs/diagrams/ 里进版本库
    if svg_dir.is_dir():
        for old in svg_dir.glob(f"{md_path.stem}-*.svg"):
            if old.name not in written:
                old.unlink()

    parts.extend(_render_block(k, a, b, where) for k, a, b in blocks)

    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list"],
        extension_configs={
            "toc": {
                "toc_depth": "2-3",
                "title": "本页目录",
                # 默认 slugify 会把中文标题的 id 压成 "0"/"1-agentsmd" 这种怪东西,
                # 换成 Unicode 版让锚点保留中文,多个中英混排标题也不会撞车
                "slugify": slugify_unicode,
            }
        },
    )
    body = _postprocess(md.convert(text), parts)
    toc_html = getattr(md, "toc", "")

    if src_label is None:
        name = f"docs/{md_path.name}" if md_path.parent.name == "docs" else md_path.name
        src_label = f"源文件 <code>{name}</code>"

    html_path.write_text(
        TEMPLATE.format(
            title=_esc(title),
            desc=_esc(desc),
            css=CSS,
            sidebar=_sidebar(toc_html, nav or [], here or md_path.stem),
            hero=_hero(kicker or md_path.stem, title, lead,
                       len(diagrams),
                       body.count("<table>"),
                       len(re.findall(r"<h2 ", body))),
            quick=_quick_strip(toc_html),
            body=body,
            src=src_label,
            stamp=_fingerprint(md_path),
            tail=_tail(),
        ),
        encoding="utf-8",
        # newline="\n" 必须显式指定:Windows 上 write_text 默认会把 \n 翻成 \r\n,
        # 结果同一个 md 在 Windows 和 WSL 生成出字节不同的 HTML(没法比对、diff 全红)
        newline="\n",
    )


def _nav_of(directory: Path) -> list[tuple[str, str]]:
    """同目录下所有 md → (文件名主干, 一级标题),按 DOC_ORDER 排序。

    跳过 `_` 开头的文件:那是**中转/草稿**的名字。hub 就是先写一个 `_hub_input.md`
    再转成 `index.html` 的,如果把它算进导航,首页侧边栏最后就会多出一条
    `<a href="_hub_input.html">` —— 指向一个转完就删掉的文件,点了 404。
    (这和知识库那边"`00_`/`_` 开头的目录类文件不进索引"是同一条约定。)
    """
    items = []
    for p in sorted(directory.glob("*.md")):
        if p.name.startswith("_"):
            continue
        t = _first_h1(p.read_text(encoding="utf-8"), p.stem)
        items.append((p.stem, t))
    rank = {s: i for i, s in enumerate(DOC_ORDER)}
    return sorted(items, key=lambda x: (rank.get(x[0], 99), x[0]))


def _build_hub(directory: Path, targets: list[Path]) -> Path:
    """导航首页 docs/index.html:卡片 + 一张"哪篇讲什么"的表。"""
    # 侧边栏第一项是"文档首页"自己(指向 index.html,并高亮成当前页)。
    # 不加这一条的话,首页的侧边栏里**没有任何一项被标成 here**,
    # 而且从别的文档点不回来 —— 看起来就像少了个东西。
    nav = [("index", "文档首页")] + _nav_of(directory)
    order = {stem: i for i, (stem, _) in enumerate(_nav_of(directory))}
    rows, cards = [], []
    n_dia = n_tbl = 0
    for md_path in sorted(targets, key=lambda p: (order.get(p.stem, 99), p.stem)):
        text = md_path.read_text(encoding="utf-8")
        title = _first_h1(text, md_path.stem)
        blurb = DOC_BLURB.get(md_path.stem, _first_para(text))
        h2 = len(re.findall(r"^## ", text, re.M))
        # 只数**独占一行的**围栏:正文里提一句「` ```mermaid ` 块」不该被算成一张图
        charts = len(re.findall(r"^```mermaid[ \t]*$", text, re.M))
        charts += len(re.findall(r"^:::(?:chart|compare|stats|steps|timeline|cards)[ \t]*$",
                                 text, re.M))
        tables = sum(1 for _ in re.finditer(r"^\|.*\|$", text, re.M))
        size = len(text)
        n_dia += charts
        n_tbl += tables
        cards.append(
            f'<a class="card-link" href="{md_path.stem}.html">'
            f'<div class="t">{_esc(title)}</div><div class="d">{_esc(blurb)}</div>'
            f'<div class="m">{h2} 节 · {tables} 行表格 · {charts} 块可视化 · 约 {size // 1000} 千字</div></a>'
        )
        rows.append(f"| [{title}]({md_path.stem}.html) | {blurb} | {h2} | {tables} | {charts} |")
    body = (
        '<div class="hub-hero"><h1>助教自动化工具 · 文档</h1>'
        f"<p>共 {len(targets)} 篇,{n_dia} 块可视化、{n_tbl} 行表格。"
        "左边随便点一篇都能直接看 —— 图是构建期就画好的 SVG(不联网也有),"
        "表是真实的表,目录在左侧跟着滚动高亮。</p></div>"
        '<div class="cards">' + "".join(cards) + "</div>"
        "\n\n## 一览\n\n"
        "| 文档 | 讲什么 | 小节 | 表格行 | 可视化块 |\n|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n> 📘 这些 HTML 都是 `python scripts/md2html.py --all docs/ --hub` 生成的,**不要手改**;"
        "要改内容就改对应的 `.md` 再重跑一次。图是 `scripts/svgkit.py` 画的,"
        "改图就改 md 里的 ```mermaid 块。\n"
    )
    out = directory / "index.html"
    tmp = directory / "_hub_input.md"
    tmp.write_text("# 文档\n\n" + body, encoding="utf-8", newline="\n")
    try:
        # here="index" —— 让侧边栏把自己那一项高亮;md_path.stem 是 _hub_input,
        # 不显式指定的话没有任何一项会亮。
        # src_label 同理:页脚不能印那个临时文件名。
        # kicker 是第三处同样的坑:hero 那行小字默认也取文件主干,
        # 不覆盖的话首页顶上会挂一行 `_hub_input`。
        convert(
            tmp,
            out,
            nav=nav,
            here="index",
            src_label="本页由 <code>md2html.py --hub</code> 汇总生成(内容取自 docs/ 下各篇 md)",
            kicker="INDEX",
        )
    finally:
        tmp.unlink(missing_ok=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown → 单文件 HTML(带目录/图/表格样式)")
    ap.add_argument("input", help="输入的 .md 文件,或配合 --all 传目录")
    ap.add_argument("output", nargs="?", help="输出的 .html(默认同名)")
    ap.add_argument("--all", action="store_true", help="把目录下所有 .md 全部转换")
    ap.add_argument("--hub", action="store_true", help="额外生成 <目录>/index.html 导航首页")
    args = ap.parse_args()

    src = Path(args.input)
    if args.all:
        if not src.is_dir():
            sys.exit(f"--all 需要传目录:{src}")
        targets = sorted(p for p in src.glob("*.md") if not p.name.startswith("_"))
        if not targets:
            sys.exit(f"{src} 下没有 .md")
        nav = _nav_of(src)
    else:
        if not src.is_file():
            sys.exit(f"文件不存在:{src}")
        targets = [src]
        nav = []

    for md_path in targets:
        html_path = Path(args.output) if args.output else md_path.with_suffix(".html")
        convert(md_path, html_path, nav=nav)
        # 只用 ASCII 输出:Windows 控制台默认 GBK,打印 ✓ / → 会 UnicodeEncodeError
        print(f"[ok] {md_path} -> {html_path} ({html_path.stat().st_size} bytes)")

    if args.hub:
        hub = _build_hub(src, targets)
        print(f"[ok] hub -> {hub} ({hub.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
