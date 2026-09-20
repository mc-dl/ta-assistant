"""课程事务事实表:一份**会过期**的、助教手工维护的「现行事务口径」。

## 为什么要有这个东西(2026-09-20 定的)

先把线上日志里的事务类提问量了一遍。当时记下的数是 265 条 `qa_kind` 事件、其中
事务题 124 条、**112 条(90.3%)命中了 FAQ**。也就是说 FAQ 对**事务类**的覆盖率
本来就很高 —— 那再往 FAQ 里加条目,边际收益很小。

但同一个数字反过来指出了真正的风险:**FAQ 条目不会过期。**

【勘误 2026-09-20 当晚】**上面那组数不可信,别引用。** 写
`scripts/mine_faq_candidates.py` 时才发现,那份日志里 **96% 的 `qa_kind` 是单测
自己写进去的** —— 归档文件里 550 条有 528 条的原文恰好等于 `test/` 里的字符串
字面量(实测泄漏路径与修法见 `test/conftest.py` 的 `isolated_logs`)。
同一份日志里能认出来的真学生提问只有 **22 条**,其中真·事务题 **13 条、
命中 FAQ 11 条**。

数字变了,但**方向没变,而且这个功能的理由本来就不靠那个数**:
"FAQ 条目不会过期、所以要有一个会过期的权威版本"是结构性理由,
覆盖率是 85% 还是 90% 都成立 —— 覆盖率越高,旧答案继续被命中的面越大。
22 条样本也给不出可信的百分数,所以这里**不写新数**,只说旧数不可信、方向一致。
真正该重新量的是等干净日志攒够量之后,而且该由 `mine_faq_candidates.py` 来量。

截止时间、提交方式、补交规则这类事**每学期都会变**,而 FAQ 是"写下就不再变"的。
覆盖率 90% 意味着:改一次规则,那条旧答案会以 90% 的概率继续被命中,
而且说得很自信 —— 而提示词第②条(事务类一个字都不许猜)防的正是"说错"。
"说错一个过期的截止时间"比"说不知道"严重得多,这是当初写下第②条的原因。

所以这张表要解决的不是"多答几道",而是**给事务口径一个会过期的版本**:

- 每条事实带「生效 / 失效」日期,**过期或还没生效的块不会被使用**;
- 它**高于** FAQ 与课程资料 —— 冲突时以现行事实为准;
- 于是改规则只改这一处,不用去几十条 FAQ 里翻出那句旧话(翻不干净就等于没改)。

## 格式

块之间用**空行**分隔,`#` 开头是整行注释,半角 `:` 与全角 `：` 都认:

    生效: 2026-09-01
    失效: 2027-01-31
    事实: 本课程作业补交需在截止后 7 天内联系助教登记
    来源: 张曰理老师 2026-09-01 课间通知

- 「事实」必填,其余三个都可以省;
- 省掉「生效」= 一直都算数,省掉「失效」= 一直有效(长期规则就这么写);
- 日期支持 `2026-09-01` / `2026/9/1` / `2026.9.1` / `2026年9月1日`;
- **日期写错、缺「事实」、失效早于生效 —— 整块跳过并告警**。
  宁可这条规则不生效(退回原来的"资料里没有"),也不能拿一条自己都算不清
  还算不算数的规则去回答事务问题。

这张表要**保持小**(几十条以内):事务类提问每次都会把它整块塞进 prompt,
它越长越贵、也越容易让模型扯到不相关的事。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src import config
from src.utils.stderr_log import warn as _warn

_KEY_RE = re.compile(r"^\s*(生效|失效|事实|来源)\s*[:：]\s*(.*)$")

# 日期的宽容写法。刻意**自己解析**而不是用 `date.fromisoformat`:
# 后者在 Python 3.11 起才接受 `2026-9-1` 这种不补零的写法,而助教手打的日期
# 大概率就是不补零的;写死正则就不用管解释器版本。
_DATE_RE = re.compile(
    r"^\s*(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?\s*$"
)


@dataclass(frozen=True)
class Fact:
    """一条事务事实。`effective_from` / `effective_until` 为 None 表示该端不限。"""

    text: str
    source: str = ""
    effective_from: date | None = None
    effective_until: date | None = None

    def is_effective(self, today: date) -> bool:
        """生效日与失效日**两端都算有效**(含当天)。

        边界含不含当天这件事,平时看不出来,学期末最后一天就会有人踩 ——
        所以钉成"含",并且有测试盯着。
        """
        if self.effective_from is not None and today < self.effective_from:
            return False
        if self.effective_until is not None and today > self.effective_until:
            return False
        return True


def _to_date(val: str) -> date | None:
    m = _DATE_RE.match(val)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        # 2026-13-45 这种正则过得去、日历上不存在的,也当解析失败
        return None


def _split_blocks(text: str) -> list[list[str]]:
    """按空行分块;整行 `#` 注释直接丢掉(它不属于任何一块)。"""
    blocks: list[list[str]] = []
    cur: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not line:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def _parse_block(lines: list[str]) -> Fact | None:
    """解析一块。任何一处不对劲就返回 None(调用方不需要再判断)。"""
    text = ""
    source = ""
    d_from: date | None = None
    d_until: date | None = None
    bad = False

    for line in lines:
        m = _KEY_RE.match(line)
        if not m:
            _warn(f"[课程事务] 认不出的行,已忽略:{line!r}")
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "事实":
            text = val
        elif key == "来源":
            source = val
        else:
            d = _to_date(val)
            if d is None:
                _warn(
                    f"[课程事务] {key}的日期看不懂({val!r}),这一块整块跳过 —— "
                    f"生效期算不清的事实不能拿去回答事务问题"
                )
                bad = True
            elif key == "生效":
                d_from = d
            else:
                d_until = d

    if bad:
        return None
    if not text:
        _warn(f"[课程事务] 有一块没有「事实:」,已跳过:{lines!r}")
        return None
    if d_from is not None and d_until is not None and d_until < d_from:
        _warn(f"[课程事务] 失效日期早于生效日期,这一块跳过:{text!r}")
        return None
    return Fact(text=text, source=source, effective_from=d_from, effective_until=d_until)


def parse_facts(text: str) -> list[Fact]:
    """解析全部事实,**不做日期过滤**(工具脚本要用它看过期的那几条)。"""
    out: list[Fact] = []
    for block in _split_blocks(text):
        fact = _parse_block(block)
        if fact is not None:
            out.append(fact)
    return out


def load_facts(path: Path | None = None, today: date | None = None) -> list[Fact]:
    """读文件,只返回**当前有效**的事实。

    文件不存在 → 返回空列表(功能静默休眠,不是错误):这个项目里
    "数据文件还没放上去"是常态,不能因此让答疑整条挂掉。
    """
    path = path or config.COURSE_FACTS_PATH
    today = today or date.today()
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        _warn(f"[课程事务] 读不了 {path}:{exc}")
        return []
    return [f for f in parse_facts(text) if f.is_effective(today)]


def render_facts(facts: list[Fact]) -> str:
    """渲染成塞进 prompt 的一块。只给事实本身,效力规则写在 `_SYSTEM_PROMPT` 里。"""
    lines = ["【课程事务(现行)】"]
    for f in facts:
        lines.append(f"· {f.text}" + (f"(来源:{f.source})" if f.source else ""))
    return "\n".join(lines)
