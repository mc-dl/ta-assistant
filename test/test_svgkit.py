"""`scripts/svgkit.py` 的回归测试 —— 那个"纯 Python 的 mermaid 子集渲染器"。

**为什么要有这个文件**:图渲染失败**不会**让系统答错话,但会静默产出
一份"看起来正常、其实缺图"的文档。更糟的是另一种失败:图**画出来了**,
只是画错了 —— 两条边叠在一起、标签盖住了线、节点跑出画布 ——
这些都不会报错,只有人眼盯着看才发现。测试挡不住全部,但能挡住
"该报错的没报错"这一类:

- 认不出的语法(没见过的图形、没见过的 HTML 标签、连到子图的边、自环)
  必须抛 `DiagramError`,而且要说清楚是**第几行**的什么毛病;
- 每种图(mermaid 的 flowchart / pie / sequenceDiagram)都要能画出来;

`DiagramError` 继承 `ValueError`,所以上层 `md2html.py` 能把它包成
"第 N 张图渲染失败 —— 具体原因"再抛出去。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"


def _load():
    """按路径加载:scripts/ 不是包,不能 import scripts.svgkit。"""
    spec = importlib.util.spec_from_file_location("svgkit", REPO / "scripts" / "svgkit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["svgkit"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def svgkit():
    return _load()


# ---------------------------------------------------------------------------
# 三种图都要画得出来
# ---------------------------------------------------------------------------

def test_flowchart_renders(svgkit):
    svg = svgkit.render('flowchart LR\n  A["甲"] --> B{"乙?"}\n  B -->|"是"| C["丙"]')
    assert svg.lstrip().startswith("<svg") and "viewBox=" in svg
    for word in ("甲", "乙?", "丙", "是"):
        assert word in svg, f"图里少了 {word!r}"


def test_flowchart_lr_is_wider_than_tall_and_td_is_the_other_way(svgkit):
    """方向参数得真的换向 —— 否则一张图永远横着排,宽到读不了。"""
    src = 'flowchart {}\n  A["甲"] --> B["乙"] --> C["丙"]'

    def size(svg: str) -> tuple[float, float]:
        m = re.search(r'width="([0-9.]+)" height="([0-9.]+)"', svg)
        return float(m.group(1)), float(m.group(2))

    lw, lh = size(svgkit.render(src.format("LR")))
    tw, th = size(svgkit.render(src.format("TD")))
    assert lw > lh and tw < th


def test_pie_renders(svgkit):
    svg = svgkit.render('pie showData title 来源\n  "甲" : 3\n  "乙" : 7')
    assert "来源" in svg and "甲" in svg and "乙" in svg


def test_sequence_renders_with_autonumber(svgkit):
    svg = svgkit.render(
        "sequenceDiagram\n  autonumber\n  participant A as 甲\n  participant B as 乙\n"
        "  A->>B: 你好\n  B-->>A: 收到\n"
    )
    assert "甲" in svg and "乙" in svg
    assert "1" in svg, "autonumber 没生效"


def test_render_dispatches_by_first_line(svgkit):
    """`render()` 自己认图类型 —— 三种各发一次,别认错。"""
    assert "flowchart" not in svgkit.render('pie title t\n  "a" : 1').split("\n")[0].lower()
    assert svgkit.render('flowchart LR\n  A["甲"]').startswith("<svg")
    assert svgkit.render('sequenceDiagram\n  A->>B: x').startswith("<svg")


# ---------------------------------------------------------------------------
# 认不出来的语法必须报错(而不是画一张错的图)
# ---------------------------------------------------------------------------

def test_unknown_diagram_type_fails_loudly(svgkit):
    with pytest.raises(svgkit.DiagramError, match="认不出这是什么图"):
        svgkit.render('gantt\n  title 某个别的图')


def test_unknown_node_shape_fails_loudly(svgkit):
    """认得出形状、但没实现的那种,必须说"暂不支持",不能画成方框。

    `A[("甲")]` 是 mermaid 的圆柱体(数据库)。画成普通方框是最坏的处理:
    读者根本不知道这里本该是个别的形状 —— 图看着没问题,信息已经丢了。
    """
    with pytest.raises(svgkit.DiagramError, match="暂不支持"):
        svgkit.render('flowchart LR\n  A[("甲")] --> B["乙"]')




def test_edge_to_a_subgraph_fails_loudly(svgkit):
    """边连到**子图** id 上是经典的画错来源。

    子图不是节点,它只是个框;连上去以后渲染器要么画飞、要么静默丢掉这条边。
    与其猜作者想连谁,不如报错让作者写清楚。
    """
    src = (
        "flowchart TD\n"
        '  subgraph G["一组"]\n    A["甲"]\n  end\n'
        '  B["乙"] --> G\n'
    )
    with pytest.raises(svgkit.DiagramError, match="subgraph"):
        svgkit.render(src)


def test_self_loop_fails_loudly(svgkit):
    with pytest.raises(svgkit.DiagramError, match="自环|自己"):
        svgkit.render('flowchart LR\n  A["甲"] --> A')


def test_unknown_html_tag_in_label_fails_loudly(svgkit):
    """节点文字里只允许少数几个标签(`<br/>`、`<b>`…),别的要报错。

    放过去的话,`<script>` 这类会**原样进 SVG** —— 自己生成的文档里嵌脚本,
    是那种"本地文件看着没事、发出去才发现"的问题。
    """
    with pytest.raises(svgkit.DiagramError):
        svgkit.render('flowchart LR\n  A["甲<br/><script>x</script>"] --> B["乙"]')


def test_error_message_names_the_line(svgkit):
    """报错要说清是第几行 —— 一张图几十行,不指行号等于没说。"""
    src = 'flowchart LR\n  A["甲"] --> B["乙"]\n  B --> C[("丙")]\n'
    with pytest.raises(svgkit.DiagramError) as ei:
        svgkit.render(src)
    assert "3" in str(ei.value), f"报错里没提第 3 行:{ei.value}"


# ---------------------------------------------------------------------------
# 条形图(`:::chart` 用的那个)
# ---------------------------------------------------------------------------

def test_bar_chart_renders_all_labels(svgkit):
    svg = svgkit.bar_chart([("甲", 3.0), ("乙", 7.0)], title="标题", unit="块")
    for word in ("标题", "甲", "乙", "块"):
        assert word in svg


def test_bar_chart_rejects_non_positive_values(svgkit):
    """0 或负数的条形会画成一条看不见的线,读者以为数据缺了 —— 直接报错。"""
    with pytest.raises(ValueError):
        svgkit.bar_chart([("甲", 0.0)])


# ---------------------------------------------------------------------------
# 交付的文档:每个 mermaid 块都要真画得出来
# ---------------------------------------------------------------------------

def _mermaid_blocks() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for md in sorted(DOCS.glob("*.md")):
        lines = md.read_text(encoding="utf-8").splitlines()
        i = 0
        while i < len(lines):
            if lines[i].strip() == "```mermaid":
                j = i + 1
                while j < len(lines) and lines[j].strip() != "```":
                    j += 1
                out.append((md.name, i + 2, "\n".join(lines[i + 1:j])))
                i = j
            i += 1
    return out


BLOCKS = _mermaid_blocks()


def test_there_are_mermaid_blocks_to_check():
    """先确认这个测试本身没瞎 —— 一个块都没扫到的话下面那条是空转。"""
    assert BLOCKS, "docs/*.md 里一个 ```mermaid 都没有?路径或写法变了"


@pytest.mark.parametrize(
    "name,line,src", BLOCKS, ids=[f"{n}:{ln}" for n, ln, _ in BLOCKS]
)
def test_every_mermaid_block_in_docs_renders(svgkit, name, line, src):
    """**仓库里每一个** mermaid 块都必须画得出来。

    这条是这套东西的总闸门:`md2html.py --all docs/` 会在渲染失败时抛错,
    但只有**真的去跑**才会抛。这条测试保证它在 CI / `pytest` 里也一定会被跑到 ——
    否则一次改渲染器(或改文档)就可能让某篇文档的图静默消失。
    """
    try:
        svg = svgkit.render(src)
    except Exception as exc:  # noqa: BLE001 —— 就是要把任何异常变成"哪个文件哪一行"
        pytest.fail(f"docs/{name} 第 {line} 行的 mermaid 块渲染失败:{exc}")
    assert svg.lstrip().startswith("<svg")
    assert "viewBox=" in svg
