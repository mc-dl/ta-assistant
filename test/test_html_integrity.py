"""校验**已生成的** docs/*.html —— 不是"生成器能不能用",是"交付的这份网页对不对"。

`test_md2html.py` 测的是生成器:喂一个样本 md,看产物里有没有该有的构件。
这里测的是**仓库里那 8 份 HTML 本身**。两者不能互相替代,因为
「构件在不在」和「整页还能不能正常显示」是两回事 —— 下面这个真实事故就是证明:

    _postprocess() 里加锚点的正则写成了 `re.sub(r'<h([23]) id="…">…</h\1>', …)`,
    group(1) 只捕获到数字,于是产出 `<2 id="…">` 和 `</2>`。当时那条测试断言的是
    `'id="第一节"' in html` —— **过了**,因为 id 确实在,只是挂在一个叫 `<2>` 的
    未知元素上。后果:每节标题的 h2/h3 样式一条都不生效(浏览器把未知元素当行内元素),
    滚动高亮 `querySelectorAll('main h2[id]')` 一个都找不到,整个侧边目录变成死的 ——
    而页面只是"看上去朴素了点",没有任何报错。

所以这里按"人眼能看见的坏法"逐条查:标题标签名还在不在、内部锚点指不指得着、
跨文档链接和图片在不在、md 里的图跟页面上的图对不对得上。
"""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
HTMLS = sorted(DOCS.glob("*.html"))

# HTML5 里没有闭合标签的元素
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# 可以省略闭合标签的元素;夹在中间时不算"交叉嵌套"
OPTIONAL_END = {"p", "li", "td", "th", "tr", "thead", "tbody", "option", "dt", "dd"}


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.anchors: list[tuple[str, int]] = []
        self.links: list[tuple[str, int]] = []
        self.imgs: list[tuple[str, int]] = []
        self.headings: Counter[str] = Counter()
        self.diagrams = 0          # <figure class="diagram…">
        self.diagram_svgs: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        line = self.getpos()[0]
        if a.get("id"):
            self.ids.add(a["id"])
        if tag in {"h1", "h2", "h3", "h4"}:
            self.headings[tag] += 1
        cls = (a.get("class") or "").split()
        if tag == "figure" and "diagram" in cls:
            self.diagrams += 1
        if tag == "a":
            href = a.get("href") or ""
            if href.startswith("#"):
                self.anchors.append((href[1:], line))
            elif href and not href.startswith(("http://", "https://", "mailto:")):
                self.links.append((href, line))
        if tag == "img":
            self.imgs.append((a.get("src") or "", line))
        if tag not in VOID:
            self.stack.append((tag, line))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack:
            self.errors.append(f"第 {self.getpos()[0]} 行:多出一个 </{tag}>")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                skipped = [t for t, _ in self.stack[i + 1:]]
                if not all(t in OPTIONAL_END for t in skipped):
                    self.errors.append(
                        f"第 {self.getpos()[0]} 行:</{tag}> 与未闭合的 "
                        f"{'/'.join('<' + t + '>' for t in skipped)} 交叉"
                    )
                del self.stack[i:]
                return
        self.errors.append(f"第 {self.getpos()[0]} 行:多出一个 </{tag}>(没有开标签)")


def _scan(path: Path) -> _Scan:
    s = _Scan()
    s.feed(path.read_text(encoding="utf-8"))
    return s


def test_docs_html_exists():
    """先确认这个测试本身没瞎。"""
    assert HTMLS, "docs/ 下没有 HTML —— 跑 python scripts/md2html.py --all docs/ --hub"


@pytest.mark.parametrize("path", HTMLS, ids=lambda p: p.name)
def test_heading_tags_survive(path: Path):
    """标题必须还是 h1/h2/h3。

    这条是上面那个 `<2 id=…>` 事故的直接回归测试,所以单独拎出来 ——
    只查 id 在不在挡不住它。
    """
    text = path.read_text(encoding="utf-8")
    assert "<2 id=" not in text and "<3 id=" not in text, (
        "出现了 <2>/<3> 这种被吃掉标签名的标题(应为 h2/h3);"
        "看 _postprocess() 加锚点那条正则的捕获组"
    )
    s = _scan(path)
    assert s.headings["h2"] > 0, f"{path.name} 一个 h2 都没有,大纲塌了"


@pytest.mark.parametrize("path", HTMLS, ids=lambda p: p.name)
def test_anchors_links_and_images_resolve(path: Path):
    """侧边目录/交叉引用/图片都得指得着。

    锚点指空了不会报错 —— 点了没反应而已,所以必须主动查。
    """
    s = _scan(path)
    problems: list[str] = []
    for a, line in s.anchors:
        if a not in s.ids:
            problems.append(f"第 {line} 行:锚点 #{a} 没有对应的 id")
    for href, line in s.links:
        target = href.split("#")[0]
        if target and not (DOCS / target).exists():
            problems.append(f"第 {line} 行:链接 {href} 指不到文件")
    for src, line in s.imgs:
        if src and not (DOCS / src).exists():
            problems.append(f"第 {line} 行:图片 {src} 不存在")
    assert not problems, f"{path.name}:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("path", HTMLS, ids=lambda p: p.name)
def test_well_formed(path: Path):
    """标签配平 —— _postprocess() 是拿正则改 HTML 的,产物必须自己验一遍。"""
    s = _scan(path)
    left = [t for t, _ in s.stack if t not in OPTIONAL_END]
    assert not s.errors and not left, (
        f"{path.name}:{s.errors} 未闭合:{left}"
    )


@pytest.mark.parametrize("path", HTMLS, ids=lambda p: p.name)
def test_no_runtime_diagram_rendering(path: Path):
    """页面**不许**再有任何"运行时才把图画出来"的机制。

    以前是内联 module 里 `import("https://cdn.jsdelivr.net/npm/mermaid@11/…")`,
    打开页面时现画。坏处两条:断网就只剩一个源码块;有网时图也先是空白、
    要等 1MB 拉下来才出现。现在图是构建期画好的 SVG。

    这条测试**必须**存在,因为退回去是"悄悄地"退:页面上还是有图,
    只是变成离线看不见 —— 只看截图发现不了。
    """
    text = path.read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in text, f"{path.name} 又去拉 CDN 了"
    assert "mermaid@11" not in text, f"{path.name} 又引了 mermaid 运行库"
    assert '<div class="mermaid">' not in text, f"{path.name} 还有交给前端现画的图"
    assert not _scan(path).diagrams or "diagrams/" in text, (
        f"{path.name} 有图却没有 diagrams/ 引用"
    )


def _md_fence_count(md: Path) -> int:
    """md 里 ```mermaid 围栏的数量(和 test_svgkit.py 里扫描的写法保持一致)。"""
    return sum(1 for ln in md.read_text(encoding="utf-8").splitlines()
               if ln.strip() == "```mermaid")


@pytest.mark.parametrize("path", HTMLS, ids=lambda p: p.name)
def test_md_diagram_count_matches_the_page(path: Path):
    """md 里写了几张图,页面上就得有几张图 —— **一张都不能少**。

    这是"构建期出图"这套的总闸门。图没画出来的失败方式是**静默**的:
    页面照常生成、构建照常退出 0,只是少了一张图(或者裂图)。
    只有拿 md 侧的围栏数和 html 侧的 figure 数对一遍才拦得住。

    `index.html` 是从 md 汇总出来的,没有对应的 md,跳过。
    """
    md = path.with_suffix(".md")
    if not md.exists():
        pytest.skip(f"{path.name} 不是从同名 md 生成的(首页)")
    want = _md_fence_count(md)
    got = _scan(path).diagrams
    assert got == want, (
        f"{path.name} 里 {got} 张图,{md.name} 里写了 {want} 张 —— "
        f"{'有图没画出来' if got < want else '凭空多出了图'}"
    )


@pytest.mark.parametrize("path", HTMLS, ids=lambda p: p.name)
def test_referenced_svgs_are_real_svgs(path: Path):
    """引到的 .svg 得真是一份画好的图,不是空文件/半截文件。"""
    for src, line in _scan(path).imgs:
        if not src.endswith(".svg"):
            continue
        f = DOCS / src
        text = f.read_text(encoding="utf-8")
        assert "<svg" in text and "viewBox=" in text, f"{path.name} 第 {line} 行:{src} 不是像样的 SVG"
