# -*- coding: utf-8 -*-
"""评测基线本身要有人守:它不在了,`--compare` 就是一道假闸门。

**为什么单独一个文件(而不是塞进 `test_retrieval_recall.py`)。**
那个文件整模块 `skipif(没有索引)`,而基线是个 **JSON 文件、不依赖索引** ——
塞进去会让"基线没了"这件事在没有索引的机器上被一起跳过,等于白写。

**它是怎么被发现的(2026-09-20 深夜,值得记)。**
基线原本只在 WSL 侧生成,而 Windows → WSL 的同步带 `--delete`
(`rsync -a --delete … /mnt/c/…/ /home/jj/ta-assistant/`)——
于是下一次同步**把只存在于 WSL 的基线删掉了**,而 `--compare` 在基线不存在时
**返回 0**:闸门整个失效,却一路绿灯。这正是这个项目里反复出现的那类错误:
**静默失败比报错危险得多**(对照 `_parse_faq` 的全角冒号、FAQ 闸门那两条假绿测试)。

所以现在有三道:
  1. `compare_with()` 在基线缺失时**返回 1**(不再返回 0);
  2. 这个文件钉住「基线文件在、能解析、内容形状对、用例表里的提问都量过」;
  3. 基线文件**不进 `.gitignore`**(`data/logs/`、`data/index/` 进,`data/eval/` 不进),
     它属于「随代码走的黄金文件」,必须留在 Windows 侧那份工作副本里 ——
     只在 WSL 一侧存在的文件,下次同步就被 `--delete` 清掉了。
"""
from __future__ import annotations

import json

import pytest

from scripts.eval_retrieval import CASES, DEFAULT_BASELINE


@pytest.fixture(scope="module")
def baseline() -> dict:
    if not DEFAULT_BASELINE.exists():
        pytest.fail(
            f"检索评测基线不存在:{DEFAULT_BASELINE}\n"
            f"`--compare` 现在会因为缺基线而返回 1(以前是 0,等于闸门失效)。\n"
            f"补法:`python scripts/eval_retrieval.py --save-baseline` —— "
            f"但**只能在自己已经核对过结果的前提下存**,别拿它去盖掉一个真实的回归。\n"
            f"另一个常见原因:这个文件只在 WSL 一侧存在,被 Windows → WSL 的 "
            f"`rsync --delete` 删掉了。它该待在 Windows 那份工作副本里。"
        )
    return json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))


def test_baseline_path_is_the_one_that_travels_with_the_code():
    """基线得放在「跟着代码走」的位置(`config.PROJECT_ROOT/data/eval/`)。

    放在别处(比如 WSL 的某个绝对路径)就会变成「只有那台机器上有」的东西,
    下一次同步/换机器就没了 —— 这一条就是那次事故的直接教训。
    """
    assert DEFAULT_BASELINE.parent.name == "eval", \
        f"基线位置变了({DEFAULT_BASELINE}),确认它还有没有随代码同步"
    assert DEFAULT_BASELINE.suffix == ".json", "基线得是 JSON,`--compare` 直接读它"


def test_baseline_is_parseable_and_records_when_it_was_taken(baseline):
    """基线要能自证「什么时候、对着多大的索引量的」—— 否则没法判断它还作不作数。"""
    assert baseline.get("generated_at"), "基线没记时间,查不出它有多旧"
    assert isinstance(baseline.get("index_chunks"), int), "基线没记索引规模"
    assert baseline["results"], "基线里一条用例都没有"


def test_baseline_results_have_the_shape_compare_relies_on(baseline):
    """`compare_with()` 按这些字段做 diff —— 缺一个就会静默地比不出东西。

    尤其是 `foreign`:`--compare` 用它算「无关教材章增减」,缺了要么 `KeyError`,
    要么更糟 —— 拿默认空列表去比,永远显示「没变化」。
    """
    for r in baseline["results"]:
        for k in ("q", "rank", "foreign", "violated"):
            assert k in r, f"基线里 {r.get('q', '?')} 缺字段 {k},`--compare` 会出问题"


def test_every_case_in_the_table_has_been_measured(baseline):
    """表里的每条用例都得进过基线 —— 否则它是「写了但没人守」的状态。

    加了用例却忘了重存基线时,`--compare` 只会在那条上打一个 ➕(新加的用例),
    **不会**因为它变差而报错:闸门对这条新用例根本不存在。
    (反过来允许:基线里有、表里没有 —— 用例被删了,`--compare` 会打 ➖ 提醒。)
    """
    known = {c["q"] for c in CASES}
    never_measured = sorted(known - {r["q"] for r in baseline["results"]})
    assert not never_measured, (
        f"这些用例从来没进过基线:{never_measured}\n"
        f"说明加了用例没重存基线,它们现在是「写了但没人守」。"
        f"跑一次 `--save-baseline`(先确认它们的结果确实是对的,再存)。"
    )
