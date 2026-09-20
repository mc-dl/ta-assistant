#!/usr/bin/env python3
"""把 mermaid 图在**构建期**渲染成 SVG 文件 —— 纯 Python,不碰浏览器、不碰网络、不碰系统字体。

# 为什么不用 mermaid-cli

这个仓库的文档是公开的,`docs/*.html` 由 `scripts/md2html.py` 重新生成。
一开始想用官方的 `@mermaid-js/mermaid-cli`,它确实最忠实(和 GitHub 上渲染的一模一样),
但实测下来它把"生成 8 张图"这件小事绑死在一整条链子上:

    Node + npx + 下载 300MB Chromium + 一堆系统共享库(libnss3/libgbm1/…)
    + 中文字体 + 一个能跑无头浏览器的地方

在开发这台 WSL 上凑齐这些是可能的(字体我确实用 `apt-get download` + `dpkg -x`
塞进了 `~/.local/share/fonts`,不需要 root),但**换一台机器就得重来一遍**:
别人 clone 这个公开仓库之后,只为了重建文档就要装一个浏览器。这个代价不合理。

还有一层更根本的原因:**SVG 里的中文是 `<text>` 文本,不是烧进像素的图形**。
浏览器读这份 SVG 时用的是**读者自己的字体** —— 所以构建机上有没有中文字体,
对最终效果**毫无影响**。而 Chromium 那条路恰恰相反:构建机上缺字体会直接
把中文画成方框(我一开始就是栽在这儿)。既然如此,何必让构建期依赖字体。

顺带的好处:SVG 是文本,`git diff` 看得见、能审、能在 GitHub 上直接点开看。

# 它支持什么

**只支持这个仓库里实际用到的语法**。不认识的写法一律抛 `DiagramError`,
**绝不"尽力画一个大概"** —— 一张画错了的架构图比一张没画出来的图危险得多
(读者不会怀疑图,他只会照着一个错的前提去理解系统)。

- `flowchart TD|TB|LR`:`subgraph ID["标题"]…end`、矩形 `[]`、菱形 `{}`、
  圆角 `()`、圆形 `(())`、子程序 `[[]]`、带标签的边 `-->|"标签"|`、
  虚线边 `-.->`、双向边 `<-->`、链式 `A --> B --> C`
- `pie showData title …`:扇形图
- `sequenceDiagram`:`autonumber`、`participant X as 名字`、`->>`/`-->>`/`->`/`-->`、
  `alt`/`else`/`end`、`Note over A,B:`

用法:
    python scripts/svgkit.py 图.mmd 图.svg      # 单张
    python scripts/svgkit.py --check 图.mmd     # 只解析,不写文件(自检用)
"""
from __future__ import annotations

import argparse
import html
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 报错
# ---------------------------------------------------------------------------


class DiagramError(ValueError):
    """图里有本渲染器不认识的写法。

    带上行号,因为写图的人需要知道**是哪一行**要改;只说"语法错误"等于没说。
    """


def _err(line_no: int, msg: str) -> DiagramError:
    return DiagramError(f"第 {line_no} 行:{msg}")


# ---------------------------------------------------------------------------
# 配色:与网页 CSS 同一套变量,图才不像"贴上去的"
# ---------------------------------------------------------------------------

INK = "#1f2328"        # 正文
MUTED = "#59636e"      # 次要说明
LINE = "#8c959f"       # 连线
BORDER = "#d0d7de"     # 节点描边
FILL = "#ffffff"       # 节点填充
PAGE = "#ffffff"       # 画布
ACCENT = "#0969da"
ACCENT_SOFT = "#ddf4ff"
OK = "#1a7f37"
OK_SOFT = "#dafbe1"
WARN = "#9a6700"
WARN_SOFT = "#fff8c5"
BAD = "#cf222e"
BAD_SOFT = "#ffebe9"
PURPLE = "#8250df"
TEAL = "#0d9488"

# 扇形图的分类型配色:够用、且相邻色差明显
CAT = [ACCENT, OK, WARN, PURPLE, BAD, TEAL, "#bc4c00", "#57606a"]

FONT = ('"Segoe UI","Microsoft YaHei","PingFang SC","Hiragino Sans GB",'
        '"Noto Sans CJK SC","Source Han Sans SC",sans-serif')
MONO = '"Cascadia Mono",Consolas,"SF Mono",Menlo,monospace'


def _n(v: float) -> str:
    """数字转字符串:掐掉多余小数位,让 SVG 输出稳定(方便 diff)。"""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# ---------------------------------------------------------------------------
# 文本度量:没有字体度量表,就按字符类别估
#
# 这里**不需要精确**。估宽只用来决定"盒子画多大、在哪换行",
# 估得偏大只是留白多一点,估得偏小才会撞车 —— 所以宁可估胖一点。
# ---------------------------------------------------------------------------

_NARROW = set("iIlj|!.,:;'`()[]{}/\\")


def _is_wide(ch: str) -> bool:
    """中日韩文字、全角标点 —— 这些按一个字号宽算。"""
    o = ord(ch)
    return o >= 0x2E80 or o in (0x2018, 0x2019, 0x201C, 0x201D, 0x2014, 0x2026, 0x00B7)


def char_w(ch: str, size: float, bold: bool = False) -> float:
    if _is_wide(ch):
        w = size
    elif ch == " ":
        w = size * 0.30
    elif ch in _NARROW:
        w = size * 0.34
    elif ch.isupper() or ch.isdigit():
        w = size * 0.62
    else:
        w = size * 0.55
    return w * (1.03 if bold else 1.0)


def text_w(s: str, size: float, bold: bool = False) -> float:
    return sum(char_w(c, size, bold) for c in s)


# 一行 = 若干段(文本, 是否粗体)
Run = tuple[str, bool]


def _tokens(s: str) -> list[str]:
    """切排版单元:ASCII 单词整块不拆,中日韩逐字可断,空格单独成块。"""
    out: list[str] = []
    buf = ""
    for ch in s:
        if _is_wide(ch) or ch == " ":
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _wrap(line: list[Run], size: float, maxw: float) -> list[list[Run]]:
    """按估算宽度贪心折行,返回若干行。"""
    toks: list[Run] = []
    for text, bold in line:
        for t in _tokens(text):
            toks.append((t, bold))
    if not toks:
        return [[]]
    out: list[list[Run]] = []
    cur: list[Run] = []
    curw = 0.0
    for t, b in toks:
        tw = text_w(t, size, b)
        if cur and curw + tw > maxw:
            out.append(cur)
            cur, curw = [], 0.0
            if t == " ":      # 折行处的空格丢掉,免得行首冒出一个空格
                continue
        cur.append((t, b))
        curw += tw
    out.append(cur)
    return out


def _wrap_balanced(line: list[Run], size: float, maxw: float) -> list[list[Run]]:
    """折行 + **行平衡**。贪心折行会把 `以 【转发学生提问】 开头?` 折成
    `…开` / `头?` —— 行数是省下来了,可最后一行只剩两个字,看着像被截断。

    做法:先贪心折一遍,知道**最少能折成几行**;再二分找"行数不变的前提下
    每行能压到多窄",按那个宽度重折。行数一样,但各行长度接近,节点也不会
    因为某一行特别长而被撑宽。
    """
    base = _wrap(line, size, maxw)
    n = len(base)
    if n <= 1:
        return base
    lo, hi, best = 0.0, maxw, base
    for _ in range(14):          # 14 次二分已经到亚像素,再多看不出来
        mid = (lo + hi) / 2
        got = _wrap(line, size, mid)
        if len(got) <= n:        # 行数没变多 → 还能再压窄
            best, hi = got, mid
        else:
            lo = mid
    return best


def line_w(line: list[Run], size: float) -> float:
    return sum(text_w(t, size, b) for t, b in line)


# ---------------------------------------------------------------------------
# SVG 画布
# ---------------------------------------------------------------------------


class Svg:
    def __init__(self, fs: float = 13.5):
        self.fs = fs
        self._p: list[str] = []
        # 已经放下的标签占位框。边标签是**不透明白底**盖在连线上的,
        # 两个标签撞在一起时,后画的那个会把先画的**整个吃掉** ——
        # 画面上看不出异常,只是少了一句话。所以放下之前先查一次碰撞。
        self.labels: list[tuple[float, float, float, float]] = []

    def raw(self, s: str) -> None:
        self._p.append(s)

    def rect(self, x, y, w, h, fill=FILL, stroke=BORDER, rx=6.0, sw=1.4,
             dash=None, opacity=None) -> None:
        a = [f'x="{_n(x)}"', f'y="{_n(y)}"', f'width="{_n(w)}"', f'height="{_n(h)}"',
             f'rx="{_n(rx)}"', f'fill="{fill}"']
        if stroke:
            a += [f'stroke="{stroke}"', f'stroke-width="{_n(sw)}"']
        if dash:
            a.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            a.append(f'opacity="{_n(opacity)}"')
        self.raw("<rect " + " ".join(a) + "/>")

    def text(self, x, y, s, size=None, bold=False, fill=INK, anchor="start",
             mono=False, opacity=None) -> None:
        size = self.fs if size is None else size
        a = [f'x="{_n(x)}"', f'y="{_n(y)}"', f'font-size="{_n(size)}"', f'fill="{fill}"']
        if anchor != "start":
            a.append(f'text-anchor="{anchor}"')
        if bold:
            a.append('font-weight="600"')
        if mono:
            a.append(f'font-family={MONO!r}'.replace("'", '"'))
        if opacity is not None:
            a.append(f'opacity="{_n(opacity)}"')
        self.raw("<text " + " ".join(a) + f">{esc(s)}</text>")

    def line(self, x1, y1, x2, y2, stroke=LINE, sw=1.5, dash=None) -> None:
        a = [f'x1="{_n(x1)}"', f'y1="{_n(y1)}"', f'x2="{_n(x2)}"', f'y2="{_n(y2)}"',
             f'stroke="{stroke}"', f'stroke-width="{_n(sw)}"']
        if dash:
            a.append(f'stroke-dasharray="{dash}"')
        self.raw("<line " + " ".join(a) + "/>")

    def path(self, d: str, stroke=LINE, sw=1.5, dash=None, fill="none",
             opacity=None) -> None:
        a = [f'd="{d}"', f'stroke="{stroke}"', f'stroke-width="{_n(sw)}"', f'fill="{fill}"',
             'stroke-linejoin="round"', 'stroke-linecap="round"']
        if dash:
            a.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            a.append(f'opacity="{_n(opacity)}"')
        self.raw("<path " + " ".join(a) + "/>")

    def poly(self, pts: list[tuple[float, float]], fill=LINE, stroke=None, sw=1.0) -> None:
        d = " ".join(f"{_n(x)},{_n(y)}" for x, y in pts)
        a = [f'points="{d}"', f'fill="{fill}"']
        if stroke:
            a += [f'stroke="{stroke}"', f'stroke-width="{_n(sw)}"']
        self.raw("<polygon " + " ".join(a) + "/>")

    def to_svg(self, w: float, h: float, title: str = "", desc: str = "") -> str:
        head = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(w)} {_n(h)}"',
            f'     width="{_n(w)}" height="{_n(h)}" role="img"',
            f'     aria-label="{esc(title or "图")}">',
            f"  <title>{esc(title)}</title>",
        ]
        if desc:
            head.append(f"  <desc>{esc(desc)}</desc>")
        head.append(
            "  <style>\n"
            f"    text{{font-family:{FONT};dominant-baseline:middle}}\n"
            "    .s{font-size:.82em;fill:#59636e}\n"
            "  </style>"
        )
        head.append(f'  <rect width="{_n(w)}" height="{_n(h)}" fill="{PAGE}"/>')
        return "\n".join(head + self._p + ["</svg>", ""])


def arrow_head(x: float, y: float, angle_deg: float, color=LINE, size=9.0) -> list[tuple[float, float]]:
    """在 (x,y) 处画一个指向 angle_deg 方向的实心三角(角度制,0=向右)。"""
    a = math.radians(angle_deg)
    back = math.radians(angle_deg + 180)
    p1 = (x, y)
    p2 = (x + size * math.cos(back - math.radians(19)), y + size * math.sin(back - math.radians(19)))
    p3 = (x + size * math.cos(back + math.radians(19)), y + size * math.sin(back + math.radians(19)))
    return [p1, p2, p3]


# ---------------------------------------------------------------------------
# 标签:把 mermaid 标签里的极简 HTML 转成"若干行 × 若干段"
# ---------------------------------------------------------------------------

_ENT = {"&quot;": '"', "&amp;": "&", "&lt;": "<", "&gt;": ">", "&#39;": "'"}


def _unescape(s: str) -> str:
    for k, v in _ENT.items():
        s = s.replace(k, v)
    return s


def parse_label(raw: str, line_no: int) -> list[list[Run]]:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    out: list[list[Run]] = []
    for ln in re.split(r"<br\s*/?>", raw):
        runs: list[Run] = []
        bold = False
        i = 0
        for m in re.finditer(r"<[^>]+>", ln):
            if m.start() > i:
                runs.append((_unescape(ln[i:m.start()]), bold))
            tag = m.group(0)
            if re.fullmatch(r"</?b\s*>", tag, re.I):
                bold = not tag.startswith("</")
            elif re.fullmatch(r"</?i\s*>", tag, re.I):
                pass
            else:
                raise _err(line_no, f"标签里出现了本渲染器不处理的 HTML 标签 {tag!r}"
                                    "(目前只认 <b> 和 <br/>;要么去掉它,要么给 svgkit 加上支持)")
            i = m.end()
        if i < len(ln):
            runs.append((_unescape(ln[i:]), bold))
        out.append([r for r in runs if r[0]] or [("", False)])
    return out


# ===========================================================================
# flowchart
# ===========================================================================

# 节点形状:左界符 → (名字, 右界符)。顺序有讲究,长的要排在短的前面,
# 不然 `[[` 会被 `[` 先匹配掉。
_SHAPES = [
    ("[[", "]]", "subroutine"),
    ("((", "))", "circle"),
    ("[(", ")]", "cylinder"),
    ("[", "]", "rect"),
    ("{", "}", "rhombus"),
    ("(", ")", "stadium"),
]
SUPPORTED_SHAPES = {"rect", "rhombus", "stadium", "circle", "subroutine"}


@dataclass
class FNode:
    nid: str
    lines: list[list[Run]]
    shape: str = "rect"
    rank: int = 0
    order: int = 0
    w: float = 0.0
    h: float = 0.0
    x: float = 0.0
    y: float = 0.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class FEdge:
    src: str
    dst: str
    label: list[list[Run]] | None = None
    dash: bool = False
    both: bool = False


@dataclass
class FSub:
    sid: str
    label: list[list[Run]]
    members: list[str] = field(default_factory=list)
    box: tuple[float, float, float, float] | None = None


def _parse_node_tok(tok: str, line_no: int) -> tuple[str, list[list[Run]] | None, str | None]:
    """`A["文字"]` → ("A", 若干行, "rect")。只有 ID 时返回 (id, None, None)。"""
    tok = tok.strip()
    if not tok:
        raise _err(line_no, "边的一端是空的")
    m = re.match(r"^([A-Za-z0-9_\-]+)\s*(.*)$", tok, re.S)
    if not m:
        raise _err(line_no, f"认不出这个节点写法:{tok!r}"
                            "(节点 ID 只允许字母/数字/下划线/短横线)")
    nid, rest = m.group(1), m.group(2).strip()
    if not rest:
        return nid, None, None
    for lop, rop, name in _SHAPES:
        if rest.startswith(lop) and rest.endswith(rop) and len(rest) >= len(lop) + len(rop):
            inner = rest[len(lop):len(rest) - len(rop)]
            if name not in SUPPORTED_SHAPES:
                raise _err(line_no, f"暂不支持 {name} 形状的节点({tok!r})")
            return nid, parse_label(inner, line_no), name
    raise _err(line_no, f"认不出这个节点形状:{tok!r}")


_EDGE_OPS = ["<-->", "-.->", "-->", "---"]


def _split_chain(line: str, line_no: int) -> tuple[list[str], list[tuple[str, str | None]]]:
    """把 `A -->|"x"| B -.-> C` 拆成节点串 [A,B,C] 与边串 [(-->,"x"),(-.->,None)]。

    要一个字符一个字符地扫,不能直接对整个字符串做正则替换 ——
    `["..."]` 里的文字完全可能包含 `--`(比如 `--dry-run`),
    按正则切会把它当成边。这里靠"括号深度 + 引号状态"两个游标来区分。
    """
    parts: list[str] = []
    ops: list[tuple[str, str | None]] = []
    buf: list[str] = []
    depth = 0
    inq = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if inq:
            buf.append(c)
            if c == '"':
                inq = False
            i += 1
            continue
        if c == '"':
            inq = True
            buf.append(c)
            i += 1
            continue
        if c in "[{(":
            depth += 1
            buf.append(c)
            i += 1
            continue
        if c in "]})":
            depth -= 1
            buf.append(c)
            i += 1
            continue
        if depth == 0:
            for op in _EDGE_OPS:
                if line.startswith(op, i):
                    parts.append("".join(buf))
                    buf = []
                    i += len(op)
                    lm = re.match(r'\s*\|([^|]*)\|', line[i:])
                    lab = None
                    if lm:
                        lab = lm.group(1)
                        i += lm.end()
                    ops.append((op, lab))
                    break
            else:
                buf.append(c)
                i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    if len(parts) != len(ops) + 1:
        raise _err(line_no, f"边和节点对不上:{line!r}")
    return [p.strip() for p in parts], ops


def parse_flowchart(src: str) -> tuple[dict[str, FNode], list[FEdge], list[FSub], str]:
    lines = [r.rstrip() for r in src.splitlines() if r.strip()]
    if not lines:
        raise _err(0, "空的 mermaid 块")
    m = re.match(r"^(flowchart|graph)\s+(TD|TB|LR|RL|BT)\s*$", lines[0].strip())
    if not m:
        raise _err(1, f"第一行必须是 `flowchart TD` / `flowchart LR` 之类,现在是 {lines[0]!r}")
    direction = m.group(2)
    if direction == "TB":
        direction = "TD"          # TB 和 TD 是一回事,mermaid 自己也把 TB 当 TD
    if direction in ("RL", "BT"):
        raise _err(1, f"暂不支持 {direction} 方向(只做了 TD/LR;真要就翻转语义再改这里)")

    nodes: dict[str, FNode] = {}
    edges: list[FEdge] = []
    subs: list[FSub] = []
    open_sub: FSub | None = None
    n_ord = 0

    def ensure(tok: str, no: int) -> str:
        nonlocal n_ord
        nid, lab, shape = _parse_node_tok(tok, no)
        if nid not in nodes:
            nodes[nid] = FNode(nid=nid,
                               lines=lab or [[(nid, False)]],
                               shape=shape or "rect",
                               order=n_ord)
            n_ord += 1
        else:
            if lab is not None:                     # 后面再给一次定义(带文字)也算
                nodes[nid].lines = lab
                nodes[nid].shape = shape or nodes[nid].shape
        if open_sub is not None and nid not in open_sub.members:
            open_sub.members.append(nid)
        return nid

    for idx, raw in enumerate(lines[1:], start=2):
        s = raw.strip()
        if not s or s.startswith("%%"):
            continue
        sm = re.match(r"^subgraph\s+(.*)$", s)
        if sm:
            if open_sub is not None:
                raise _err(idx, "subgraph 不能嵌套(本渲染器没做嵌套布局)")
            body = sm.group(1).strip()
            nm = re.match(r"^([A-Za-z0-9_\-]+)\s*(\[.*\])?$", body, re.S)
            if nm and nm.group(2):
                # 这里必须**剥掉方括号**再解析。直接把 `["准入"]` 丢给 parse_label,
                # 它看到的首尾字符是 `[` 和 `]`(不是引号),于是不剥引号,
                # 框上就原样印出 `["准入"]` —— 少剥一层,图里就多两个方括号。
                sid, lab = nm.group(1), parse_label(nm.group(2)[1:-1], idx)
            else:
                sid, lab = None, parse_label(body, idx)
            open_sub = FSub(sid=sid or f"sub{len(subs)}", label=lab)
            subs.append(open_sub)
            continue
        if s == "end":
            if open_sub is None:
                raise _err(idx, "多了一个 end")
            open_sub = None
            continue

        parts, ops = _split_chain(s, idx)
        if not ops:                                  # 单独一行定义节点
            ensure(parts[0], idx)
            continue
        for k, (op, lab) in enumerate(ops):
            a = ensure(parts[k], idx)
            b = ensure(parts[k + 1], idx)
            if a in ("end",) or b in ("end",):
                raise _err(idx, "边连到了 end")
            edges.append(FEdge(src=a, dst=b,
                               label=parse_label(lab, idx) if lab is not None else None,
                               dash=op == "-.->",
                               both=op == "<-->"))

    sub_ids = {s.sid for s in subs}
    for e in edges:
        for side in (e.src, e.dst):
            if side in sub_ids:
                raise _err(0, f"边连到了 subgraph({side})。本渲染器只给 subgraph 画框,"
                              "不把它当节点连 —— 把这条边改连到框里的某个节点,"
                              "或者把这句话改写成图旁边的提示块")
    return nodes, edges, subs, direction


def _ranks(nodes: dict[str, FNode], edges: list[FEdge]) -> dict[str, int]:
    """算层级。**先剔除回边**,否则有环时层级会一圈圈往上爬。

    回边 = DFS 时指向"当前递归栈里"的节点的那条边。剔除它再做最长路,
    图里有环也照样能得到一个稳定的分层。
    """
    adj: dict[str, list[str]] = {k: [] for k in nodes}
    for e in edges:
        if e.src != e.dst and e.dst not in adj[e.src]:
            adj[e.src].append(e.dst)

    on_stack: set[str] = set()
    done: set[str] = set()
    back: set[tuple[str, str]] = set()
    for root in nodes:
        if root in done:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        on_stack.add(root)
        while stack:
            v, i = stack[-1]
            if i < len(adj[v]):
                stack[-1] = (v, i + 1)
                w = adj[v][i]
                if w in on_stack:
                    back.add((v, w))
                elif w not in done:
                    on_stack.add(w)
                    stack.append((w, 0))
            else:
                stack.pop()
                on_stack.discard(v)
                done.add(v)

    r = {k: 0 for k in nodes}
    fwd = [(e.src, e.dst) for e in edges if (e.src, e.dst) not in back and e.src != e.dst]
    for _ in range(len(nodes) + 1):
        changed = False
        for a, b in fwd:
            if r[b] < r[a] + 1:
                r[b] = r[a] + 1
                changed = True
        if not changed:
            break
    return r


PADX, PADY, LH = 15.0, 11.0, 1.44
GAP_NODE, GAP_RANK = 30.0, 62.0


def _node_size(nd: FNode) -> tuple[float, float]:
    if nd.shape == "dummy":     # 只为把连线"顶"到两列之间,不画任何东西
        return 10.0, 10.0
    fs = 13.0
    maxw = 250.0 if nd.shape != "rhombus" else 150.0
    lines: list[list[Run]] = []
    for ln in nd.lines:
        lines.extend(_wrap_balanced(ln, fs, maxw))
    nd.lines = lines
    tw = max(line_w(ln, fs) for ln in lines)
    th = len(lines) * fs * LH
    if nd.shape == "rhombus":
        return tw * 1.75 + 34, th + 34
    if nd.shape == "circle":
        d = max(tw * 1.5, th * 1.6) + 30
        return d, d
    return tw + PADX * 2, th + PADY * 2


def render_flowchart(src: str, title: str = "") -> str:
    nodes, edges, subs, direction = parse_flowchart(src)
    ranks = _ranks(nodes, edges)
    for k, v in ranks.items():
        nodes[k].rank = v

    # ---- 跨层的边要"打点"穿过中间层 ----
    # 一条边从第 0 列直接连到第 3 列时,如果只画一条折线,中间的竖直段
    # 会**正好落在第 1、2 列的节点上** —— 实测里 `下一条提问即可命中` 那条
    # 就是横穿了 补交表 那个框。老牌布局器(dot)的解法是给跨层边在每一层
    # 插一个"虚拟节点",让布局**为它留出通道**。这里照做。
    routed: list[FEdge] = []
    n_dummy = 0
    for e in edges:
        a, b = nodes[e.src], nodes[e.dst]
        if e.src == e.dst or b.rank - a.rank <= 1:
            routed.append(e)
            continue
        prev = e.src
        for k, r in enumerate(range(a.rank + 1, b.rank)):
            d = FNode(nid=f"~d{n_dummy}", lines=[[("", False)]], shape="dummy",
                      rank=r, order=nodes[prev].order)
            n_dummy += 1
            nodes[d.nid] = d
            routed.append(FEdge(src=prev, dst=d.nid,
                                label=e.label if k == 0 else None, dash=e.dash))
            prev = d.nid
        routed.append(FEdge(src=prev, dst=e.dst, dash=e.dash, both=e.both))
    edges = routed

    for nd in nodes.values():
        nd.w, nd.h = _node_size(nd)

    by_rank: dict[int, list[FNode]] = {}
    for nd in sorted(nodes.values(), key=lambda n: n.order):
        by_rank.setdefault(nd.rank, []).append(nd)

    # 同一层内先按**书写顺序**排(作者写的先后基本就是他想看到的左右顺序),
    # 再跑**一趟重心法**把跨层的虚拟节点摆到就近的位置。
    # 不跑这一趟的话,虚拟节点全被排到所在列的末尾(最下面),
    # 于是每条跨层边都会绕一个大弯 —— 通道是有了,但看着还是乱。
    pos = {n.nid: i for i, n in enumerate(sorted(nodes.values(), key=lambda x: x.order))}
    preds: dict[str, list[str]] = {}
    for e in edges:
        preds.setdefault(e.dst, []).append(e.src)
    # 同一个 subgraph 的成员在每一层里要**挨在一起**,否则它们的框一定拧在一起。
    # 所以第一关键字是"属于哪个 subgraph",第二关键字才是重心。
    grp: dict[str, int] = {}
    for i, sb in enumerate(subs):
        for m in sb.members:
            grp[m] = i
    for rk in sorted(by_rank):
        col = by_rank[rk]

        def key(nd: FNode) -> tuple[int, float]:
            ps = [pos[p] for p in preds.get(nd.nid, []) if p in pos and nodes[p].rank < rk]
            bary = sum(ps) / len(ps) if ps else pos[nd.nid] + 0.5
            return (grp.get(nd.nid, -1), bary)

        col.sort(key=key)
        for i, nd in enumerate(col):
            pos[nd.nid] = i

    # 层间距由**最宽的边标签**决定,不能写死。
    # 写死 62px 的时候,标签(白底不透明)会盖住左右两个节点框的边 ——
    # 实测里 `子进程 bridge 只写一行 JSON` 那张图就是这么被啃掉两口的。
    lab_w = max((max(line_w(ln, 11.5) for ln in e.label) for e in edges if e.label),
                default=0.0)
    lab_h = max((len(e.label) * 11.5 * 1.3 for e in edges if e.label), default=0.0)
    gap_rank = max(GAP_RANK, lab_w + 24) if direction == "LR" else max(GAP_RANK, lab_h + 26)

    M = 26.0
    row_w = {r: sum(n.w for n in row) + GAP_NODE * (len(row) - 1)
             for r, row in by_rank.items()}
    col_h = {r: sum(n.h for n in col) + GAP_NODE * (len(col) - 1)
             for r, col in by_rank.items()}
    widest = max(row_w.values())
    tallest = max(col_h.values())

    def layout(gap_by: dict[int, float]) -> dict[int, float]:
        """摆一遍,顺手返回"每一层之后那条通道"的起点坐标(TD 是 y,LR 是 x)。"""
        chan: dict[int, float] = {}
        if direction == "TD":
            y = M
            for rk in sorted(by_rank):
                row = by_rank[rk]
                # 每层横向居中:一律左对齐的话,窄层会在右边留一大块空,
                # 整张图看上去像被啃掉一角
                x = M + (widest - row_w[rk]) / 2
                for nd in row:
                    nd.x, nd.y = x, y
                    x += nd.w + GAP_NODE
                y += max(n.h for n in row)
                chan[rk] = y
                y += gap_by.get(rk, gap_rank)
        else:
            x = M
            for rk in sorted(by_rank):
                col = by_rank[rk]
                y = M + (tallest - col_h[rk]) / 2
                for nd in col:
                    nd.x, nd.y = x, y
                    y += nd.h + GAP_NODE
                x += max(n.w for n in col)
                chan[rk] = x
                x += gap_by.get(rk, gap_rank)
        return chan

    chan = layout({})

    # ---- TD 的边标签要**分行**,不能挤在通道中线上 ----
    # TD 的通道是横的,同一个通道里几条边的标签都在同一个 y 上,
    # 只要横向压住一点点就会互相盖(实测里 `admin` 和 `problem` 只压了 7 像素,
    # 结果一个字被另一个整个吃掉)。
    # 解法:先按默认间距摆一遍拿到每个标签的横向位置 ——
    # **改通道高度不会挪动任何 x**,所以第一遍的分行结果第二遍依然成立 ——
    # 把互相压住的标签分到不同的行,再把通道加高,重摆一遍竖坐标。
    label_row: dict[int, int] = {}
    label_y: dict[int, float] = {}
    if direction == "TD":
        per_bound: dict[int, list[tuple[float, float, int]]] = {}
        for e in edges:
            a, b = nodes[e.src], nodes[e.dst]
            if not e.label or b.rank - a.rank != 1:
                continue
            w = max(line_w(ln, 11.5) for ln in e.label) + 10
            xc = (a.cx + b.cx) / 2
            per_bound.setdefault(a.rank, []).append((xc - w / 2, xc + w / 2, id(e)))
        gap_by: dict[int, float] = {}
        nrows: dict[int, int] = {}
        for rk, items in per_bound.items():
            items.sort()
            slot_right: list[float] = []          # 每一行目前排到的最右边
            for x0, x1, eid in items:
                for i, right in enumerate(slot_right):
                    if x0 >= right + 6:
                        slot_right[i] = x1
                        label_row[eid] = i
                        break
                else:
                    slot_right.append(x1)
                    label_row[eid] = len(slot_right) - 1
            nrows[rk] = len(slot_right)
            gap_by[rk] = max(GAP_RANK, nrows[rk] * (lab_h + 8) + 16)
        chan = layout(gap_by)

        # 标签的"第 0 行"就落在它那条横线上 ——
        # 横线的位置是 `_edge_path` 取的通道中线的 `my`,即 chan[rk] + 间距/2。
        # 一开始我是拿"通道顶部 + 行号"当行位的,结果单行标签会飘在自己那条线**上方
        # 整整 60 像素**,看图的人根本认不出它标的是哪条边。
        # 多行时以中线为基准上下对称铺开,这样 1 行和 3 行看上去都以线为中心。
        for rk in sorted(by_rank):
            if rk not in nrows:
                continue
            mid_y = chan[rk] + gap_by[rk] / 2
            for e in edges:
                if id(e) not in label_row or nodes[e.src].rank != rk:
                    continue
                k = label_row[id(e)] - (nrows[rk] - 1) / 2
                label_y[id(e)] = mid_y + k * (lab_h + 8)

    # subgraph 的框 = 成员的外接矩形(在节点摆好之后再算)
    for sb in subs:
        pts = [nodes[m] for m in sb.members if m in nodes]
        if not pts:
            continue
        x0 = min(p.x for p in pts) - 16
        y0 = min(p.y for p in pts) - 30
        x1 = max(p.x + p.w for p in pts) + 16
        y1 = max(p.y + p.h for p in pts) + 16
        sb.box = (x0, y0, x1, y1)

    # 两个 subgraph 的框叠在一起 = **图是错的**,不是"有点丑"。
    # 外接矩形这个画法有个前提:同一个 subgraph 的成员得挨在一起。
    # 成员一旦被边拆到不同层(比如 F-7 被 F-1 拉到上一行、F-3 还在下一行),
    # 框就会横跨别的 subgraph。这时候与其画个让人误会的图,
    # 不如直接报错,把怎么改说清楚。
    for i in range(len(subs)):
        for j in range(i + 1, len(subs)):
            if subs[i].box and subs[j].box and _overlap(
                    (subs[i].box[0], subs[i].box[1], subs[i].box[2] - subs[i].box[0],
                     subs[i].box[3] - subs[i].box[1]),
                    (subs[j].box[0], subs[j].box[1], subs[j].box[2] - subs[j].box[0],
                     subs[j].box[3] - subs[j].box[1])):
                raise DiagramError(
                    f"subgraph「{subs[i].sid}」和「{subs[j].sid}」的框重叠了:它们的成员"
                    "被边拆到了不同的层,而本渲染器只能给 subgraph 画外接矩形。"
                    "改法:把每个 subgraph 的成员改成落在同一层(让它们之间没有先后依赖),"
                    "或者干脆去掉 subgraph、改用节点文字里的【阶段】前缀。"
                )

    base_w = max([n.x + n.w for n in nodes.values()]
                 + [b[2] for b in (s.box for s in subs) if b]) + M
    base_h = max([n.y + n.h for n in nodes.values()]
                 + [b[3] for b in (s.box for s in subs) if b]) + M

    # 回边(= 指向更靠前层级的边,比如 `LLM --> reply --> OpenClaw`)不走在节点堆里,
    # 而是**贴着整张图的外侧绕一条专用车道**。而且一条一条错开 ——
    # 不错开的话,两条回边会画在同一条线上叠成一条,看起来像只有一条边。
    for e in edges:
        if e.src == e.dst:
            raise DiagramError(f"自环({e.src} → 自己)没有支持")
    backs = [e for e in edges if nodes[e.dst].rank <= nodes[e.src].rank]
    if direction == "TD":
        lane0 = lab_w / 2 + 24
        lane_step = max(lab_w + 24, 32.0)
        W = base_w + (lane0 + (len(backs) - 1) * lane_step + M if backs else 0)
        H = base_h
    else:
        lane0 = 22.0
        lane_step = max(lab_h + 26, 32.0)
        W = base_w
        # 末尾那个 `+ M` 是车道下方的留白。少了它,最后一条车道的标签
        # 会被画到画布外 —— SVG 不报错,只是**安静地把它裁掉**(实测中过招)。
        H = base_h + (lane0 + (len(backs) - 1) * lane_step + M if backs else 0)

    svg = Svg()

    for sb in subs:                                   # 框先画,压在节点下面
        if not sb.box:
            continue
        x0, y0, x1, y1 = sb.box
        svg.rect(x0, y0, x1 - x0, y1 - y0, fill="#f6f8fa", stroke=BORDER,
                 rx=10, dash="5 4", sw=1.2)
        svg.text(x0 + 12, y0 + 15, "".join(t for t, _ in sb.label[0]), size=12.5,
                 bold=True, fill=MUTED)

    back_ids = {id(e) for e in backs}
    for e in edges:
        if id(e) in back_ids:
            continue
        _draw_edge(svg, nodes[e.src], nodes[e.dst], e, direction,
                   label_y=label_y.get(id(e)))

    for k, e in enumerate(backs):
        lane = (base_w + lane0 + k * lane_step) if direction == "TD" \
            else (base_h + lane0 + k * lane_step)
        _draw_back_edge(svg, nodes[e.src], nodes[e.dst], e, direction, lane)

    for nd in nodes.values():
        if nd.shape == "dummy":
            continue
        _draw_node(svg, nd)

    return svg.to_svg(W, H, title=title or "流程图", desc=src.strip()[:300])


def _attach(a: FNode, b: FNode, direction: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """算一条边从 a 的哪儿出、进 b 的哪儿。

    方向**不能写反**:TD 是"上→下",边就该从下底边出、从上顶边进;
    LR 是"左→右",边就该从右边出、从左边进。这两支一开始写反了 ——
    左→右的图里每条边都从底边出、绕到顶边进,画出来像一堆回形针,
    乍看"也连上了",细看每条线都在往反方向拐。
    """
    if direction == "TD":
        return (a.cx, a.y + a.h), (b.cx, b.y)
    return (a.x + a.w, a.cy), (b.x, b.cy)


def _edge_path(p0, p1, direction: str,
               mid: float | None = None) -> tuple[str, tuple[float, float], float]:
    """返回 (路径 d, 标签锚点, 进入角)。折线走法:先横后纵 / 先纵后横。

    `mid` 是"拐弯那一刀切在哪儿",不给就取两端的中点。
    调用方在算过通道中线之后会把中线传进来:一来同一层的几条边拐在同一根线上
    (不传的话,同一层里**高矮不同的节点**会让每条边各拐各的,横线互相错开像台阶),
    二来标签的落座位置就是这个值,线和标签对不上就白算了。
    """
    x0, y0 = p0
    x1, y1 = p1
    if direction == "TD":
        my = (y0 + y1) / 2 if mid is None else mid
        d = f"M {_n(x0)},{_n(y0)} L {_n(x0)},{_n(my)} L {_n(x1)},{_n(my)} L {_n(x1)},{_n(y1)}"
        ang = 90.0 if y1 >= y0 else -90.0
        return d, ((x0 + x1) / 2, my), ang
    mx = (x0 + x1) / 2 if mid is None else mid
    d = f"M {_n(x0)},{_n(y0)} L {_n(mx)},{_n(y0)} L {_n(mx)},{_n(y1)} L {_n(x1)},{_n(y1)}"
    ang = 0.0 if x1 >= x0 else 180.0
    return d, (mx, (y0 + y1) / 2), ang


def _overlap(a, b) -> bool:
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def _draw_label(svg: Svg, lines: list[list[Run]], cx: float, cy: float,
                fill=PAGE, color=INK, axis: str = "y") -> None:
    """把标签压在它那条线上(白底盖住线,不然线会从字缝里穿过去)。

    撞车时**沿着通道方向**让位:`axis="y"` 是上下让(LR 的通道是竖的),
    `axis="x"` 是左右让(TD 的通道是横的)。

    这一条是被坑出来的:一开始不管什么方向都往下推,结果 TD 图里
    `admin` 和 `problem` 两个标签只横向压了 7 像素,却把 `admin` 整整推下去 36 像素 ——
    **正好推进下面那个菱形节点里**。让位的方向错了,比不让还糟。
    横向让位步长小、次数多,因为横向的空隙本来就窄。
    """
    fs = 11.5
    w = max(line_w(ln, fs) for ln in lines)
    h = len(lines) * fs * 1.3
    offsets = ([0, 8, -8, 16, -16, 24, -24, 32, -32, 40, -40] if axis == "x"
               else [0] + [(h + 6) * k for k in range(1, 5)])
    for d in offsets:
        box = (cx + (d if axis == "x" else 0) - w / 2 - 5,
               cy + (0 if axis == "x" else d) - h / 2 - 3, w + 10, h + 6)
        if not any(_overlap(box, b) for b in svg.labels):
            break
    cx, cy = box[0] + w / 2 + 5, box[1] + h / 2 + 3
    svg.labels.append(box)
    svg.rect(box[0], box[1], box[2], box[3], fill=fill, stroke=None, rx=4)
    y0 = cy - h / 2 + fs * 0.65
    for i, ln in enumerate(lines):
        x = cx - line_w(ln, fs) / 2
        for t, b in ln:
            svg.text(x, y0 + i * fs * 1.3, t, size=fs, bold=b, fill=color)
            x += text_w(t, fs, b)


def _draw_edge(svg: Svg, a: FNode, b: FNode, e: FEdge, direction: str,
               label_y: float | None = None) -> None:
    """`label_y` 是调用方（TD 的分行逻辑）指定好的标签纵坐标。

    给了就用它,不给才按通道中线放 —— 分行过的标签不该再被 `_draw_label`
    左右挪:那一挪就会**把刚分好的行又推乱**。
    """
    p0, p1 = _attach(a, b, direction)
    # label_y 就是那条通道的中线,拐弯也拐在同一根线上
    d, mid, ang = _edge_path(p0, p1, direction, mid=label_y)
    color = LINE
    svg.path(d, stroke=color, sw=1.6, dash="5 4" if e.dash else None)
    if e.both:
        svg.poly(arrow_head(p0[0], p0[1], ang + 180, color), fill=color)
    else:
        svg.poly(arrow_head(p1[0], p1[1], ang, color), fill=color)
    if e.label:
        if label_y is not None:
            # 已经分好行了,让位只能**左右**让 —— 上下让会把自己那行推乱
            _draw_label(svg, e.label, mid[0], label_y, axis="x")
        else:
            # TD 的通道是横的,标签左右让位;LR 的通道是竖的,上下让位
            _draw_label(svg, e.label, mid[0], mid[1],
                        axis="x" if direction == "TD" else "y")


def _draw_back_edge(svg: Svg, a: FNode, b: FNode, e: FEdge, direction: str,
                    lane: float) -> None:
    """回边沿整张图外侧的车道绕行,别从节点堆里穿过。

    出口和目标都取**底边**(LR)/**右边**(TD)而不是原先那个"从侧边插进去":
    从底边进、箭头朝上,读者一眼能看出"这是往回走的",不会和正向边混在一起。
    """
    color = BAD
    if direction == "LR":
        x0, y0 = a.cx, a.y + a.h
        x1, y1 = b.cx, b.y + b.h
        svg.path(f"M {_n(x0)},{_n(y0)} L {_n(x0)},{_n(lane)} "
                 f"L {_n(x1)},{_n(lane)} L {_n(x1)},{_n(y1)}",
                 stroke=color, sw=1.6, dash="6 4")
        svg.poly(arrow_head(x1, y1, -90, color), fill=color)
        if e.label:
            # 回边的车道是**横着**铺在图下面的,所以标签要左右让位
            _draw_label(svg, e.label, (x0 + x1) / 2, lane, color=color, axis="x")
    else:
        x0, y0 = a.x + a.w, a.cy
        x1, y1 = b.x + b.w, b.cy
        svg.path(f"M {_n(x0)},{_n(y0)} L {_n(lane)},{_n(y0)} "
                 f"L {_n(lane)},{_n(y1)} L {_n(x1)},{_n(y1)}",
                 stroke=color, sw=1.6, dash="6 4")
        svg.poly(arrow_head(x1, y1, 180, color), fill=color)
        if e.label:
            # 回边车道是**竖着**铺在图右边的,标签上下让位
            _draw_label(svg, e.label, lane, (y0 + y1) / 2, color=color, axis="y")


def _draw_node(svg: Svg, nd: FNode) -> None:
    fill, stroke = FILL, BORDER
    if nd.shape == "rect":
        svg.rect(nd.x, nd.y, nd.w, nd.h, fill=fill, stroke=stroke)
    elif nd.shape == "stadium":
        svg.rect(nd.x, nd.y, nd.w, nd.h, fill=fill, stroke=stroke, rx=nd.h / 2)
    elif nd.shape == "subroutine":
        svg.rect(nd.x, nd.y, nd.w, nd.h, fill=fill, stroke=stroke)
        svg.line(nd.x + 9, nd.y, nd.x + 9, nd.y + nd.h, stroke=stroke, sw=1.2)
        svg.line(nd.x + nd.w - 9, nd.y, nd.x + nd.w - 9, nd.y + nd.h, stroke=stroke, sw=1.2)
    elif nd.shape == "circle":
        svg.raw(f'<ellipse cx="{_n(nd.cx)}" cy="{_n(nd.cy)}" rx="{_n(nd.w / 2)}" '
                f'ry="{_n(nd.h / 2)}" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
    elif nd.shape == "rhombus":
        svg.poly([(nd.cx, nd.y), (nd.x + nd.w, nd.cy), (nd.cx, nd.y + nd.h), (nd.x, nd.cy)],
                 fill=fill, stroke=stroke, sw=1.4)

    fs = 13.0
    h = len(nd.lines) * fs * LH
    y0 = nd.cy - h / 2 + fs * LH / 2 + 1
    for i, ln in enumerate(nd.lines):
        x = nd.cx - line_w(ln, fs) / 2
        for t, b in ln:
            svg.text(x, y0 + i * fs * LH, t, size=fs, bold=b, fill=INK)
            x += text_w(t, fs, b)


# ===========================================================================
# pie
# ===========================================================================


def render_pie(src: str, title: str = "") -> str:
    lines = [r.strip() for r in src.splitlines() if r.strip()]
    if not lines or not lines[0].startswith("pie"):
        raise _err(1, "第一行必须是 `pie` 或 `pie showData`")
    head = lines[0]
    t = ""
    tm = re.search(r"title\s+(.*)$", head)
    if tm:
        t = tm.group(1).strip()
    rows: list[tuple[str, float]] = []
    for i, s in enumerate(lines[1:], start=2):
        m = re.match(r'^"([^"]*)"\s*:\s*([0-9.]+)$', s)
        if not m:
            raise _err(i, f"饼图的数据行要写成 `\"标签\" : 数值`,现在是 {s!r}")
        rows.append((m.group(1), float(m.group(2))))
    if not rows:
        raise _err(0, "饼图里一条数据都没有")
    total = sum(v for _, v in rows)
    if total <= 0:
        raise _err(0, "饼图的数值加起来是 0")

    fs = 13.0
    R = 110.0
    lw = max(text_w(f"{lab}  {int(v)}  ({v / total * 100:.0f}%)", fs) for lab, v in rows)
    top = 26 + (24 if t else 0)
    W = 26 + R * 2 + 34 + lw + 26
    H = top + max(R * 2 + 26, len(rows) * 24 + 20) + 22
    cx = 26 + R
    cy = top + R + 13

    svg = Svg()
    if t:
        svg.text(26, 26, t, size=15, bold=True, fill=INK)

    a0 = -90.0
    for i, (lab, v) in enumerate(rows):
        sweep = v / total * 360.0
        a1 = a0 + sweep
        color = CAT[i % len(CAT)]
        if sweep >= 359.99:
            svg.raw(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(R)}" fill="{color}"/>')
        else:
            x0 = cx + R * math.cos(math.radians(a0))
            y0 = cy + R * math.sin(math.radians(a0))
            x1 = cx + R * math.cos(math.radians(a1))
            y1 = cy + R * math.sin(math.radians(a1))
            large = 1 if sweep > 180 else 0
            svg.path(f"M {_n(cx)},{_n(cy)} L {_n(x0)},{_n(y0)} "
                     f"A {_n(R)},{_n(R)} 0 {large} 1 {_n(x1)},{_n(y1)} Z",
                     fill=color, stroke=PAGE, sw=1.6)
        if sweep >= 22:                      # 太窄的扇形就不在图上塞字了,留给图例
            mid = math.radians(a0 + sweep / 2)
            svg.text(cx + R * 0.62 * math.cos(mid), cy + R * 0.62 * math.sin(mid),
                     f"{v / total * 100:.0f}%", size=12.5, bold=True,
                     fill=PAGE, anchor="middle")
        a0 = a1

    ly = cy - (len(rows) * 24) / 2 + 12
    lx = cx + R + 30
    for i, (lab, v) in enumerate(rows):
        color = CAT[i % len(CAT)]
        svg.rect(lx, ly - 6, 12, 12, fill=color, stroke=None, rx=3)
        svg.text(lx + 20, ly, lab, size=fs, fill=INK)
        svg.text(lx + lw + 18, ly, f"{int(v)}  ({v / total * 100:.1f}%)", size=fs,
                 fill=MUTED, anchor="end")
        ly += 24
    return svg.to_svg(W, H, title=t or title or "占比图", desc=src.strip()[:300])


def bar_chart(rows: list[tuple[str, float]], title: str = "",
              unit: str = "", width: float = 620.0) -> str:
    """横向条形图。**不是** mermaid 的一部分 —— 由 md2html 的 `:::chart` 块调用。

    横向而不是纵向:标签是中文短语,竖着放要么转 90 度(读者得歪头),
    要么挤成一团。横着放标签有整条左边栏,想写多长写多长。
    条长按**最大值**归一,不从 0 到某个凑整的刻度 —— 这张图是用来看相对大小的,
    不是用来读绝对刻度的(绝对数字就写在每条的右边)。
    """
    if not rows:
        raise DiagramError("条形图一条数据都没有")
    vals = [v for _, v in rows]
    if max(vals) <= 0:
        raise DiagramError("条形图的数值全是 0 或负数,画不出长度")
    if min(vals) < 0:
        raise DiagramError("条形图不支持负数(有负值时请改用表格)")

    fs = 13.0
    bar_h, row_gap = 17.0, 13.0
    gutter = max(text_w(lab, fs) for lab, _ in rows) + 14
    vw = max(text_w(f"{v:g}{unit}", fs) for _, v in rows) + 12
    plot = max(60.0, width - 26 - gutter - vw - 16)

    top = 26 + (26 if title else 0)
    H = top + len(rows) * (bar_h + row_gap) - row_gap + 26
    W = 26 + gutter + plot + 16 + vw + 26

    svg = Svg()
    if title:
        svg.text(26, 26, title, size=15, bold=True, fill=INK)
    # 竖直的基线:没有它,读者看不出"条的起点在哪",短条会像浮在空中
    svg.line(26 + gutter, top - 6, 26 + gutter, H - 20, stroke=BORDER, sw=1)

    vmax = max(vals)
    y = top
    for i, (lab, v) in enumerate(rows):
        color = CAT[i % len(CAT)]
        svg.text(26 + gutter - 10, y + bar_h * 0.72, lab, size=fs, fill=INK, anchor="end")
        w = plot * (v / vmax)
        # 长度为 0 的值也留 2px:否则那一行看上去像"漏了数据"而不是"值是 0"
        svg.rect(26 + gutter, y, max(w, 2.0), bar_h, fill=color, stroke=None, rx=4)
        svg.text(26 + gutter + plot + 16, y + bar_h * 0.72, f"{v:g}{unit}",
                 size=fs, fill=MUTED)
        y += bar_h + row_gap
    return svg.to_svg(W, H, title=title or "数据图",
                      desc="; ".join(f"{lab}={v:g}{unit}" for lab, v in rows)[:300])


# ===========================================================================
# sequenceDiagram
# ===========================================================================


@dataclass
class _Msg:
    a: str
    b: str
    text: list[list[Run]]
    dash: bool
    num: int = 0


@dataclass
class _Note:
    targets: list[str]
    text: list[list[Run]]


@dataclass
class _Frame:
    kind: str          # 'alt' | 'else' | 'end'
    text: list[list[Run]]


# 消息行的正则。两个细节都是**踩过坑才这么写的**:
#
#  1. ID 用**非贪婪** `+?`。ID 的字符集里有 `-`,而箭头也以 `-` 开头。
#     贪婪匹配时 `TA-->>BR` 会被切成 ID=`TA-` + 箭头=`-->` + `BR` ——
#     正则**每一步都成功**,于是不报错,只在图里凭空多出一个叫 `TA-` 的参与者,
#     而且那条消息被挂到了错的人身上。这不是"画得难看",是**画错了**。
#  2. 箭头按**长的排前面**。非贪婪拿到 `TA` 之后,必须先试 `-->>` 再试 `-->`,
#     否则 `-->>` 永远被 `-->` 抢先匹配掉。
_MSG_RE = re.compile(
    r"^([A-Za-z0-9_\-]+?)\s*(-->>|-->|->>|->)\s*([A-Za-z0-9_\-]+)\s*:\s*(.*)$", re.S)


def render_sequence(src: str, title: str = "") -> str:
    lines = [r.strip() for r in src.splitlines() if r.strip()]
    if not lines or not lines[0].startswith("sequenceDiagram"):
        raise _err(1, "第一行必须是 `sequenceDiagram`")

    auto = False
    decl: dict[str, list[list[Run]]] = {}
    order: list[str] = []
    events: list[object] = []

    def pid(x: str) -> str:
        if x not in order:
            order.append(x)
        return x

    for i, s in enumerate(lines[1:], start=2):
        if s.startswith("%%"):
            continue
        if s == "autonumber":
            auto = True
            continue
        m = re.match(r"^participant\s+([A-Za-z0-9_\-]+)(?:\s+as\s+(.*))?$", s)
        if m:
            # 名字可能是两行(`<br/>`),整段留着 —— 只取第一行的话
            # `openclaw_bridge.py<br/>(子进程)` 会在图上变成 `openclaw_bridge.py`,
            # 括号里那句"它是子进程"就丢了,而那恰恰是这张图要说明的事。
            decl[m.group(1)] = parse_label(m.group(2) or m.group(1), i)
            pid(m.group(1))
            continue
        if re.match(r"^(actor|loop|opt|par|critical|rect|activate|deactivate)\b", s):
            raise _err(i, f"暂不支持这条时序图指令:{s!r}"
                          "(目前只做了 autonumber / participant / alt / else / Note over)")
        m = re.match(r"^(alt|else|end)\b\s*(.*)$", s)
        if m:
            events.append(_Frame(kind=m.group(1), text=parse_label(m.group(2), i) if m.group(2) else []))
            continue
        m = re.match(r"^Note\s+(over|left of|right of)\s+([A-Za-z0-9_\-, ]+)\s*:\s*(.+)$", s, re.S)
        if m:
            tg = [t.strip() for t in m.group(2).split(",") if t.strip()]
            for t in tg:
                pid(t)
            events.append(_Note(targets=tg, text=parse_label(m.group(3), i)))
            continue
        m = _MSG_RE.match(s)
        if m:
            events.append(_Msg(a=pid(m.group(1)), b=pid(m.group(3)),
                               text=parse_label(m.group(4), i),
                               dash=m.group(2).startswith("--")))
            continue
        raise _err(i, f"时序图里认不出这一行:{s!r}")

    fs = 13.0
    head_w: dict[str, float] = {}
    head_lines: dict[str, list[list[Run]]] = {}
    for k in order:
        lines = decl.get(k) or [[(k, False)]]
        lines = [w for ln in lines for w in _wrap(ln, fs, 220)]
        head_lines[k] = lines
        head_w[k] = max(max(line_w(ln, fs) for ln in lines) + 30, 96.0)
    head_h = max(len(head_lines[k]) for k in order) * fs * 1.3 + 16

    # 自消息(自己发给自己)画成一个回形,它占的横向空间和普通消息不一样 ——
    # 不计进来的话,那种"抽学号 → 校验 → 落盘"的长标签会顶出画布右边。
    msg_w = 0.0
    for ev in events:
        if isinstance(ev, _Msg):
            w = max(line_w(ln, 11.5) for ln in ev.text)
            msg_w = max(msg_w, w + (110 if ev.a == ev.b else 44))
        elif isinstance(ev, _Note):
            msg_w = max(msg_w, max(line_w(ln, 11.5) for ln in ev.text) + 34)
    colw = max(max(head_w.values()), msg_w)
    xs: dict[str, float] = {}
    M = 24.0
    for i, k in enumerate(order):
        xs[k] = M + colw / 2 + i * colw

    # 先算纵向位置,才能知道 alt 框要框到哪儿
    y = M
    head_y = y
    y += head_h + 26
    rows: list[tuple[float, object]] = []
    frames: list[dict] = []
    stack: list[dict] = []
    num = 0
    for ev in events:
        if isinstance(ev, _Frame) and ev.kind == "alt":
            fr = {"y0": y - 14, "label": ev.text, "seps": [], "y1": 0.0}
            stack.append(fr)
            frames.append(fr)
            y += 12
        elif isinstance(ev, _Frame) and ev.kind == "else":
            if not stack:
                raise _err(0, "else 没有对应的 alt")
            stack[-1]["seps"].append((y - 8, ev.text))
            y += 10
        elif isinstance(ev, _Frame) and ev.kind == "end":
            if not stack:
                raise _err(0, "end 没有对应的 alt")
            stack.pop()["y1"] = y + 6
            y += 16
        else:
            rows.append((y, ev))
            if isinstance(ev, _Msg):
                num += 1
                ev.num = num if auto else 0
            y += 52
    for fr in frames:
        if not fr["y1"]:
            fr["y1"] = y + 6
    H = y + M
    W = M + colw * len(order) + M

    svg = Svg()
    for fr in frames:                                  # 框在底层
        x0, x1 = M - 8, W - M + 8
        svg.rect(x0, fr["y0"], x1 - x0, fr["y1"] - fr["y0"], fill="#fbfcfd",
                 stroke=BORDER, rx=6, sw=1.2)
        if fr["label"]:
            # 顺序要紧:先画 `alt` 那个小签,再把条件文字放到它**右边**。
            # 反过来的话,小签的底色会把条件文字整块盖掉 ——
            # 图上只剩一个孤零零的 "alt",读者不知道这个分支到底是"补交"还是"答疑"。
            svg.rect(x0, fr["y0"], 46, 26, fill="#eef2f5", stroke=BORDER, rx=6, sw=1.2)
            svg.text(x0 + 23, fr["y0"] + 13, "alt", size=12, bold=True,
                     fill=MUTED, anchor="middle")
            lw = max(line_w(ln, 11.5) for ln in fr["label"])
            _draw_label(svg, fr["label"], x0 + 58 + lw / 2, fr["y0"] + 13,
                        fill="#fbfcfd", color=INK)
        for sy, sl in fr["seps"]:
            svg.line(x0, sy, x1, sy, stroke=BORDER, sw=1.2, dash="5 4")
            if sl:
                lw = max(line_w(ln, 11.5) for ln in sl)
                _draw_label(svg, sl, x0 + 58 + lw / 2, sy, fill="#fbfcfd", color=MUTED)

    for k in order:                                    # 生命线
        svg.line(xs[k], head_y + head_h, xs[k], H - M + 6,
                 stroke=BORDER, sw=1.4, dash="5 5")

    for k in order:                                    # 顶部角色框(名字可以有两行)
        w = head_w[k]
        lines = head_lines[k]
        svg.rect(xs[k] - w / 2, head_y, w, head_h, fill="#eef2f5", stroke=BORDER, rx=6)
        yy = head_y + head_h / 2 - (len(lines) - 1) * fs * 1.3 / 2 + fs * 0.42
        for ln in lines:
            x = xs[k] - line_w(ln, fs) / 2
            for t, b in ln:
                svg.text(x, yy, t, size=fs, bold=True)
                x += text_w(t, fs, b)
            yy += fs * 1.3

    for ry, ev in rows:
        if isinstance(ev, _Note):
            x0 = min(xs[t] for t in ev.targets) - 40
            x1 = max(xs[t] for t in ev.targets) + 40
            tw = max(line_w(ln, 11.5) for ln in ev.text) + 26
            mid = (x0 + x1) / 2
            x0 = min(x0, mid - tw / 2)
            x1 = x0 + max(x1 - x0, tw)
            h = len(ev.text) * 11.5 * 1.4 + 16
            svg.rect(x0, ry - h / 2, x1 - x0, h, fill=WARN_SOFT, stroke="#d4a72c", rx=6)
            yy = ry - h / 2 + 8 + 11.5 * 0.7
            for ln in ev.text:
                x = (x0 + x1) / 2 - line_w(ln, 11.5) / 2
                for t, b in ln:
                    svg.text(x, yy, t, size=11.5, bold=b, fill="#3d2f00")
                    x += text_w(t, 11.5, b)
                yy += 11.5 * 1.4
            continue

        a, b = xs[ev.a], xs[ev.b]
        color = ACCENT if not ev.dash else MUTED
        if a == b:
            # 自己发给自己的消息画成**回形针**。原来按"零长度的普通消息"处理,
            # 只在生命线上戳了一个反方向的小箭头 —— 图上完全看不出
            # "这一步是这个角色自己干的",而那正是自消息想表达的意思。
            #
            # 往哪边绕要看角色的位置:最右边那个角色(ta-assistant)右边没有地方了,
            # 一律往右绕的话标签会被画出画布外 —— SVG 不报错,只是安静裁掉。
            L = 34.0
            d = 1.0 if a < (W - M) - 120 else -1.0
            svg.path(f"M {_n(a)},{_n(ry - 10)} L {_n(a + d * L)},{_n(ry - 10)} "
                     f"L {_n(a + d * L)},{_n(ry + 10)} L {_n(a + d * 12)},{_n(ry + 10)}",
                     stroke=color, sw=1.6, dash="5 4" if ev.dash else None)
            svg.poly(arrow_head(a, ry + 10, 0 if d > 0 else 180, color), fill=color)
            if ev.num:
                _num_badge(svg, a + d * 17, ry - 24, ev.num)
            lw = max(line_w(ln, 11.5) for ln in ev.text)
            _draw_label(svg, ev.text, a + d * (L + 12 + lw / 2), ry, fill=PAGE, color=INK)
        else:
            # `dir` 是箭头指向。线的终点要**退回一个箭头长度**,
            # 否则实心三角会盖住线尾,看着像断了一截。
            d = 1.0 if b > a else -1.0
            svg.line(a, ry, b - d * 9, ry, stroke=color, sw=1.6,
                     dash="5 4" if ev.dash else None)
            svg.poly(arrow_head(b, ry, 0 if d > 0 else 180, color), fill=color)
            if ev.num:
                _num_badge(svg, a + d * 20, ry, ev.num)
            _draw_label(svg, ev.text, (a + b) / 2, ry - 12, fill=PAGE, color=INK)

    return svg.to_svg(W, H, title=title or "时序图", desc=src.strip()[:300])


def _num_badge(svg: Svg, cx: float, cy: float, n: int) -> None:
    """autonumber 的小圆牌。

    顺手把它登记进 `svg.labels`:圆牌和边标签挤在一起时,
    让后画的标签**绕开它**。不登记的话圆牌会被标签的白底吃掉半个数字。
    """
    svg.raw(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="9.5" fill="{ACCENT_SOFT}" '
            f'stroke="{ACCENT}" stroke-width="1"/>')
    svg.text(cx, cy, str(n), size=11, bold=True, fill=ACCENT, anchor="middle")
    svg.labels.append((cx - 10, cy - 10, 20, 20))


# ===========================================================================
# 入口
# ===========================================================================

_KINDS = [
    (re.compile(r"^\s*(?:flowchart|graph)\s", re.M), render_flowchart, "flowchart"),
    (re.compile(r"^\s*pie\b", re.M), render_pie, "pie"),
    (re.compile(r"^\s*sequenceDiagram\b", re.M), render_sequence, "sequence"),
]


def render(src: str, title: str = "", kind: str | None = None) -> str:
    """把一段 mermaid 源码渲染成 SVG 文本。认不出类型就抛 DiagramError。"""
    if kind:
        for rx, fn, k in _KINDS:
            if k == kind:
                return fn(src, title)
        raise DiagramError(f"没有这种图:{kind}")
    for _rx, fn, _k in _KINDS:
        if _rx.search(src):
            return fn(src, title)
    first = next((r for r in src.splitlines() if r.strip()), "")
    raise DiagramError(
        f"认不出这是什么图:{first!r}。目前只做了 flowchart / pie / sequenceDiagram 三种。"
        "要加新类型就在 scripts/svgkit.py 的 _KINDS 里登记。"
    )


def render_file(src_path: Path, out_path: Path, title: str = "") -> None:
    code = src_path.read_text(encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(code, title), encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="mermaid(子集)→ SVG,纯 Python,离线可用")
    ap.add_argument("input", help="输入的 .mmd(mermaid 源码)")
    ap.add_argument("output", nargs="?", help="输出的 .svg")
    ap.add_argument("--check", action="store_true", help="只解析,不写文件")
    ap.add_argument("--title", default="", help="图题(进 <title>,也是无障碍名)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.is_file():
        sys.exit(f"文件不存在:{src}")
    code = src.read_text(encoding="utf-8")
    try:
        svg = render(code, args.title)
    except DiagramError as exc:
        sys.exit(f"[FAIL] {src}:{exc}")
    if args.check:
        print(f"[ok] {src} 解析通过(未写文件)")
        return 0
    out = Path(args.output) if args.output else src.with_suffix(".svg")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8", newline="\n")
    print(f"[ok] {src} -> {out} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
