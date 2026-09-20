#!/usr/bin/env python3
"""把 docs/ 下的 Markdown 渲染成**单文件、好看的** HTML(双击即看,浏览器直接打开)。

它**不是** markdown → html 的一比一翻译机。设计目标见 `docs/README.html` 的说明:

- **图**:```mermaid 围栏会渲染成真图(架构图 / 流程图 / 时序图 / 饼图)。
  GitHub 也原生渲染 mermaid,所以同一个块**两边都好看**,不用维护两份图。
  离线或不联网时,mermaid 渲染不了 —— 此时网页里会退化成一张代码块 + 一个
  「Mermaid 源码」折叠区,信息不丢。
- **表**:所有表格自动包一层横向滚动容器 + 表头吸顶,长表格不会把版心撑破。
- **图**:正文里的图片自动变成带题注的 `<figure>`(题注取 alt 文案)。
- **导航**:自动抽二级/三级标题做**侧边目录**(宽屏常驻、窄屏折叠),带滚动高亮;
  顶部有「本页速览」胶囊条,右下角有回到顶部。
- **提示块**:`>` 引用块按开头 emoji 上色(⚠️ 警告 / ✅ 通过 / ❌ 禁止 / 其余中性)。

用法:
    python scripts/md2html.py docs/wechat_end_to_end.md          # 生成同名 .html
    python scripts/md2html.py docs/wechat_end_to_end.md out.html # 指定输出
    python scripts/md2html.py --all docs/                        # 目录下所有 .md 全转
    python scripts/md2html.py --all docs/ --hub                  # 再生成 docs/index.html 导航页

依赖:`markdown`(requirements.txt 里已列)。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import html
import re
import sys
from pathlib import Path

try:
    import markdown
    from markdown.extensions.toc import slugify_unicode
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖:先 pip install markdown(或 pip install -r requirements.txt)")

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

/* ---- 图:mermaid 与图片 ---- */
figure.diagram,figure.pic{margin:1.4em 0;padding:14px;border:1px solid var(--border);
                          border-radius:8px;background:#fcfcfd}
figure.diagram .mermaid{overflow-x:auto;text-align:center}
figure.diagram figcaption,figure.pic figcaption{color:var(--muted);font-size:.88em;
     margin-top:.6em;text-align:center}
figure.pic img{max-width:100%;display:block;margin:0 auto;border-radius:6px}
details.mermaid-src{margin-top:.6em;font-size:.85em}
details.mermaid-src summary{cursor:pointer;color:var(--muted)}
details.mermaid-src pre{margin:.5em 0 0}

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
  .table-wrap{overflow:visible}th{position:static}}
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

# mermaid 走 CDN(不联网就退化成代码块,信息不丢)。
# 版本锁 11 大版本:大版本升级会换渲染器,mermaid 语法兼容性得重看。
MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"

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
{quick}
{body}
<div class="foot">
  <span>源文件 <code>{src}</code> · 生成于 {stamp}</span>
  <span>内容与 Markdown 一致,这里的图/表/目录是渲染出来的</span>
</div>
</main>
</div>
<button id="totop" title="回到顶部">↑</button>
{tail}
</body>
</html>
"""


def _esc(t: str) -> str:
    return html.escape(t, quote=False)


def _first_h1(text: str, fallback: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else fallback


def _first_para(text: str) -> str:
    """拿第一段非引用、非标题的正文当 meta description。"""
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith(("#", ">", "|", "```", "<", "!", "-", "*", "1.")):
            continue
        return re.sub(r"[`*\[\]]", "", s)[:120]
    return ""


def _extract_mermaid(text: str) -> tuple[str, list[str]]:
    """把 ```mermaid 围栏抠出来,换成占位符。

    必须在 markdown 转换**之前**抠 —— 否则代码块会被包成 <pre><code>,
    而 mermaid 要的是干净的 <div class="mermaid">。
    """
    blocks: list[str] = []

    def take(m: re.Match) -> str:
        blocks.append(m.group(1).strip("\n"))
        return f"\n\nMERMAIDBLOCK{len(blocks) - 1}ENDMERMAIDBLOCK\n\n"

    return re.sub(r"```mermaid[ \t]*\n(.*?)```", take, text, flags=re.S), blocks


def _render_diagram(code: str) -> str:
    return (
        '<figure class="diagram">\n'
        f'<div class="mermaid">{html.escape(code)}</div>\n'
        '<details class="mermaid-src"><summary>Mermaid 源码(离线时看这里)</summary>'
        f"<pre><code>{html.escape(code)}</code></pre></details>\n"
        "</figure>"
    )


def _postprocess(body: str, mermaid: list[str]) -> str:
    # 1) 占位符 → 图
    for i, code in enumerate(mermaid):
        fig = _render_diagram(code)
        body = body.replace(f"<p>MERMAIDBLOCK{i}ENDMERMAIDBLOCK</p>", fig)
        body = body.replace(f"MERMAIDBLOCK{i}ENDMERMAIDBLOCK", fig)

    # 2) 表格包一层滚动容器(不改表格本身,样式由 CSS 接)
    body = body.replace("<table>", '<div class="table-wrap"><table>')
    body = body.replace("</table>", "</table></div>")

    # 3) 标题右上角加锚点(方便把某小节链接发出去)
    def anchor(m: re.Match) -> str:
        tag, hid, inner = m.group(1), m.group(2), m.group(3)
        return (f'<{tag} id="{hid}">{inner}'
                f'<a class="anchor" href="#{hid}" title="链接到这一节">#</a></{tag}>')

    body = re.sub(r'<h([23]) id="([^"]+)">(.*?)</h\1>', anchor, body, flags=re.S)

    # 4) 图片 → 带题注的图(题注取 alt;alt 就是 markdown 里那对方括号)
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


def _tail(has_mermaid: bool) -> str:
    if not has_mermaid:
        return f"<script>{SCRIPT}</script>"
    return (
        f"<script>{SCRIPT}</script>\n"
        f'<script type="module">\n'
        f"  // startOnLoad 关掉,只跑一次 run():两者同时开会重复渲染同一张图\n"
        f'  import("{MERMAID_CDN}")\n'
        f"    .then(function(m){{var M=m.default||m;"
        f"M.initialize({{startOnLoad:false,theme:'neutral',securityLevel:'loose',"
        f"flowchart:{{useMaxWidth:true}},sequence:{{useMaxWidth:true}}}});"
        f"return M.run({{nodes:document.querySelectorAll('.mermaid')}});}})\n"
        f"    .catch(function(){{/* 没网:图保持源码原样,折叠区里也有一份 */}});\n"
        f"</script>"
    )


def convert(md_path: Path, html_path: Path, nav: list[tuple[str, str]] | None = None) -> None:
    text = md_path.read_text(encoding="utf-8")
    title = _first_h1(text, md_path.stem)
    desc = _first_para(text)

    text, mermaid = _extract_mermaid(text)
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
    body = _postprocess(md.convert(text), mermaid)
    toc_html = getattr(md, "toc", "")

    html_path.write_text(
        TEMPLATE.format(
            title=_esc(title),
            desc=_esc(desc),
            css=CSS,
            sidebar=_sidebar(toc_html, nav or [], md_path.stem),
            quick=_quick_strip(toc_html),
            body=body,
            src=f"docs/{md_path.name}" if md_path.parent.name == "docs" else md_path.name,
            stamp=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            tail=_tail(bool(mermaid)),
        ),
        encoding="utf-8",
        # newline="\n" 必须显式指定:Windows 上 write_text 默认会把 \n 翻成 \r\n,
        # 结果同一个 md 在 Windows 和 WSL 生成出字节不同的 HTML(没法比对、diff 全红)
        newline="\n",
    )


def _nav_of(directory: Path) -> list[tuple[str, str]]:
    """同目录下所有 md → (文件名主干, 一级标题),按 DOC_ORDER 排序。"""
    items = []
    for p in sorted(directory.glob("*.md")):
        t = _first_h1(p.read_text(encoding="utf-8"), p.stem)
        items.append((p.stem, t))
    rank = {s: i for i, s in enumerate(DOC_ORDER)}
    return sorted(items, key=lambda x: (rank.get(x[0], 99), x[0]))


def _build_hub(directory: Path, targets: list[Path]) -> Path:
    """导航首页 docs/index.html:卡片 + 一张"哪篇讲什么"的表。"""
    order = {stem: i for i, (stem, _) in enumerate(_nav_of(directory))}
    rows, cards = [], []
    for md_path in sorted(targets, key=lambda p: (order.get(p.stem, 99), p.stem)):
        text = md_path.read_text(encoding="utf-8")
        title = _first_h1(text, md_path.stem)
        blurb = DOC_BLURB.get(md_path.stem, _first_para(text))
        h2 = len(re.findall(r"^## ", text, re.M))
        # 只数**独占一行的**围栏:正文里提一句「` ```mermaid ` 块」不该被算成一张图
        charts = len(re.findall(r"^```mermaid[ \t]*$", text, re.M))
        tables = sum(1 for _ in re.finditer(r"^\|.*\|$", text, re.M))
        size = len(text)
        cards.append(
            f'<a class="card-link" href="{md_path.stem}.html">'
            f'<div class="t">{_esc(title)}</div><div class="d">{_esc(blurb)}</div>'
            f'<div class="m">{h2} 节 · {tables} 行表格 · {charts} 张图 · 约 {size // 1000} 千字</div></a>'
        )
        rows.append(f"| [{title}]({md_path.stem}.html) | {blurb} | {h2} | {tables} | {charts} |")
    body = (
        '<div class="hub-hero"><h1>助教自动化工具 · 文档</h1>'
        f"<p>共 {len(targets)} 篇。左边随便点一篇都能直接看 —— "
        "图是 mermaid 现渲染的,表是真实的表,目录在右侧跟着滚动高亮。</p></div>"
        '<div class="cards">' + "".join(cards) + "</div>"
        "\n\n## 一览\n\n"
        "| 文档 | 讲什么 | 小节 | 表格行 | 图 |\n|---|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n\n> 📘 这些 HTML 都是 `python scripts/md2html.py --all docs/ --hub` 生成的,**不要手改**;"
        "要改内容就改对应的 `.md` 再重跑一次。\n"
    )
    out = directory / "index.html"
    tmp = directory / "_hub_input.md"
    tmp.write_text("# 文档\n\n" + body, encoding="utf-8", newline="\n")
    try:
        convert(tmp, out, nav=_nav_of(directory))
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
        targets = sorted(p for p in src.glob("*.md"))
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
