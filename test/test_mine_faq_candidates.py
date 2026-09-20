"""FAQ 候选挖矿脚本:脱敏、同义归并、以及"半成品草稿是惰性的"。

**为什么这些断言值得写。**
这个脚本的产出会**变成给学生看的 FAQ**,所以它的两个失败方向都很贵:

1. **脱敏漏了** → 学生姓名/学号被写进 FAQ 文件、进而进版本库。后果不可撤回。
   这一条没有任何"跑起来会报错"的兜底,只能靠测试钉住。
2. **归并错了** → 把不相干的问题凑成一条 FAQ。助教是照着输出一条条抄的,
   所以他看到的**每一条都得是对的**;这条靠"输出里列全部问法"来人工复核,
   测试则负责钉住"该并的并、不该并的不并"。

还有一条不是关于错的,而是关于**安全网**的:草稿里 `A:` 留空,
所以半成品贴进 `常问问题.txt` **不会生效**。这个性质是脚本敢让助教
"整批粘贴、再一条条填"的前提 —— 如果哪天有人"顺手"给草稿补个占位答案,
这条前提就没了,而后果是线上 FAQ 里躺着几十条空答案。
所以下面用**真的解析器**去证明它,不用我自己写的断言。
"""
from __future__ import annotations

import json

import pytest

from scripts import mine_faq_candidates as mf
from src.rag.retriever import FAQRetriever


# ─── 脱敏 ─────────────────────────────────────────────────────
def test_redact_masks_student_ids():
    assert mf.redact("我是 20231234567,想问补交", []) == "我是 [学号],想问补交"


def test_redact_keeps_short_numbers():
    """短数字不能遮:题号(1.36)、页码(188)、分值(10 分)都在这个范围里,
    遮掉等于把候选问题本身毁了。"""
    for raw in ("1.36", "188", "10分", "2026"):
        assert mf.redact(raw, []) == raw, raw


def test_redact_masks_roster_names():
    got = mf.redact("张三是电一的,想请假", ["张三", "李四"])
    assert got == "[学生]是电一的,想请假"
    assert "张三" not in got


def test_redact_does_not_require_a_roster():
    """花名册读不到时脚本仍要干活(只遮学号)—— 这是 main() 里告警后的实际行为,
    不能因为名单缺了就直接抛异常。"""
    assert mf.redact("我是 20231234567", []) == "我是 [学号]"


# ─── 实词签名与相似度 ─────────────────────────────────────────
def test_signature_drops_stopwords():
    """签名复用 FAQ 闸门的实词定义,所以"请问""可以吗"这类不出现在签名里 ——
    否则每个学生都带的客套话会把所有问题都拉得像。"""
    assert "请问" not in mf.signature("请问作业怎么提交")
    assert "作业" in mf.signature("请问作业怎么提交")


def test_jaccard_treats_empty_as_dissimilar():
    """全是虚词的问句不能和任何东西相似 —— 否则一堆「这个可以吗」会抱成一团。"""
    assert mf.jaccard(frozenset(), frozenset({"作业"})) == 0.0
    assert mf.jaccard(frozenset(), frozenset()) == 0.0


def _ask(text, kind="admin", faq_hit=False, top_score=None):
    return mf.Ask(text=text, kind=kind, faq_hit=faq_hit, top_score=top_score)


def test_cluster_merges_paraphrases():
    """两种明确同义的问法要并成一组。"""
    asks = [_ask("电路基础作业怎么提交"), _ask("电路基础作业交到哪")]
    groups = mf.cluster(asks, 0.5)
    assert sorted(len(g) for g in groups) == [2]


def test_default_threshold_splits_a_three_way_paraphrase_and_that_is_known():
    """**这条记录的是一处已知的、可接受的粗糙,不是"应该如此"。**

    三句话说的是同一件事,但在默认阈值 0.5 下会分成 2+1:

        '电路基础作业怎么提交' 签名 {电路,基础,作业,提交}
        '电路基础的作业交到哪' 签名 {电路,基础,作业,交到,哪}
        '电路基础作业交给谁'   签名 {电路,基础,作业,交给,谁}

    两两相似度:0.500 / 0.500 / **0.429**。前两句都够 0.5,第三句够不着 ——
    于是第三句能不能进组,取决于**哪一句当上了代表问法**(代表按频次、并列时按文本
    排序选,所以是任意的)。这一组里恰好选了个够不着它的代表,就分了家。

    **为什么不修**:两条路都有代价,而收益在当前数据上看不见 ——
    真实日志里去重后只有 25 种写法、且以"同一句话被反复问"为主(见模块开头),
    三句同义改写这种事几乎不出现;为此加一轮迭代(重选代表再重分组)会把
    "只跟代表比"这个一句话能讲清的规则变成两句话,而调小 `--similarity`
    到 0.4 就已经把这三句并成一组了(实测)。
    所以这里**钉住现状 + 钉住破解办法**,而不是假装它已经并好了。
    """
    asks = [_ask("电路基础作业怎么提交"), _ask("电路基础的作业交到哪"),
            _ask("电路基础作业交给谁")]
    assert sorted(len(g) for g in mf.cluster(asks, 0.5)) == [1, 2]
    assert sorted(len(g) for g in mf.cluster(asks, 0.4)) == [3], \
        "调小阈值就能并起来 —— 这是给助教的破解办法,别让它悄悄失效"


def test_cluster_keeps_unrelated_apart():
    asks = [_ask("电路基础作业怎么提交"), _ask("什么是叠加原理"),
            _ask("考试考什么")]
    assert len(mf.cluster(asks, 0.5)) == 3, "不相干的问题不能并到一起"


def test_cluster_never_merges_pure_stopword_questions():
    """两句都只有虚词时相似度是 0/0 —— 不并,而不是抛 ZeroDivisionError。"""
    asks = [_ask("这个可以吗"), _ask("这样可以吗")]
    assert len(mf.cluster(asks, 0.5)) == 2


def test_lower_threshold_merges_more():
    """阈值是唯一的归并旋钮,它的方向必须是对的(不然助教调了等于没调)。"""
    asks = [_ask("电路基础作业怎么提交"), _ask("电路基础实验报告格式要求")]
    assert len(mf.cluster(asks, 0.9)) == 2
    assert len(mf.cluster(asks, 0.1)) == 1


def test_cluster_does_not_chain_across_a_bridge(monkeypatch):
    """单链会并、代表问法不会并的那个构造。

    **说清楚这条测的是什么**:它不是复现某个线上事故 —— 实测两种方法在现有日志上
    结果几乎一样(阈值 0.5:单链 22 组 / 代表 23 组,最大组都是 158)。
    它测的是**防语料变大的性质**:单链只要 A~B、B~C 就并掉 A 和 C,
    哪怕两者毫不相干;而现有日志里最大的组已经占全日志三分之一,
    再长下去单链会开始把不相干的问法吸进同一个团。代表问法这一层没有链。
    """
    sigs = {
        "A": frozenset({"甲", "乙", "丙", "丁"}),
        "B": frozenset({"甲", "乙", "丙", "戊"}),   # 与 A 相似度 3/5
        "C": frozenset({"甲", "乙", "戊", "己"}),   # 与 B 3/5,但与 A 只有 2/6
    }
    monkeypatch.setattr(mf, "signature", lambda t: sigs[t])
    asks = [_ask(x) for x in ("A", "B", "C")]
    groups = mf.cluster(asks, 0.5)
    assert sorted(len(g) for g in groups) == [1, 2], \
        "C 只能靠 B 搭桥才够得着 A —— 代表问法聚类就该把这条链断掉"


def test_representative_is_the_most_asked_phrasing(monkeypatch):
    """输出的代表问法取**问得最多**的那种说法。

    这不是美观问题:助教照着代表问法写 FAQ 条目,而 FAQ 是靠字面匹配命中的 ——
    拿一句没人这么说过的话当条目,学生下次还是搜不到。
    """
    monkeypatch.setattr(mf, "signature", lambda t: frozenset({"作业"}))
    asks = [_ask("作业交到哪"), _ask("作业怎么提交"), _ask("作业怎么提交")]
    cands = mf.build_candidates(asks, mf.cluster(asks, 0.5))
    assert len(cands) == 1
    assert cands[0].rep == "作业怎么提交", "该选问过两次的那种说法"


def test_the_leader_of_a_group_is_the_most_asked_phrasing(monkeypatch):
    """**先按频次降序**是为了让问得最多的说法先当上代表。

    这条跟上面那条测的是**两件不同的事**,别合并:代表问法由
    `build_candidates` 的 `most_common` 决定,就算处理顺序写反了也还是最多的那种;
    受影响的是**分组本身** —— 组的中心会漂到最冷门的写法上,
    于是"像不像"是拿一句没人这么说过的话去量的。

    所以这里直接对 `cluster` 返回的成员顺序断言(每组的第 0 个就是代表)。
    """
    monkeypatch.setattr(mf, "signature", lambda t: frozenset({"作业"}))
    asks = [_ask("作业交到哪"), _ask("作业怎么提交"), _ask("作业怎么提交")]
    groups = mf.cluster(asks, 0.5)
    assert len(groups) == 1
    assert asks[groups[0][0]].text == "作业怎么提交", \
        "组里的头一个应当是问得最多的那种说法"


# ─── 读日志 ───────────────────────────────────────────────────
def _write_log(tmp_path, records):
    p = tmp_path / "2026-09.log"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                 encoding="utf-8")
    return p


def test_read_asks_only_takes_qa_kind(tmp_path):
    """`process_input` / `reply` 里也有文本,但只有 `qa_kind` 同时带着
    kind 和 faq_hit —— 挖矿要的就是这三个字段同时在的那一条。"""
    p = _write_log(tmp_path, [
        {"event": "process_input", "raw": "作业怎么提交"},
        {"event": "qa_kind", "message": "作业怎么提交", "kind": "admin",
         "faq_hit": False},
        {"event": "reply", "reply_len": 42, "type": "question"},
    ])
    asks, lines = mf.read_asks([p], [])
    assert len(asks) == 1 and lines == 3
    assert asks[0].kind == "admin" and asks[0].faq_hit is False


def test_read_asks_survives_a_half_written_line(tmp_path):
    """日志是边跑边追加的:最后一行可能是半句。它不该让整次挖掘失败 ——
    那意味着"正好有人在答疑"时挖不了矿,而那时才是最该挖的时候。"""
    p = tmp_path / "2026-09.log"
    p.write_text(
        json.dumps({"event": "qa_kind", "message": "好的问句", "kind": "concept",
                    "faq_hit": True}, ensure_ascii=False) + "\n"
        + '{"event": "qa_kind", "message": "被截断的一行", "ki',
        encoding="utf-8")
    asks, _ = mf.read_asks([p], [])
    assert [a.text for a in asks] == ["好的问句"]


def test_read_asks_redacts_before_returning(tmp_path):
    """脱敏必须发生在**入口**,不是在渲染时 —— 否则中间任何一次打印/写文件
    都可能是没遮的。这里直接对返回值断言。"""
    p = _write_log(tmp_path, [
        {"event": "qa_kind", "message": "我是张三,学号 20231234567,想请假",
         "kind": "admin", "faq_hit": False},
    ])
    asks, _ = mf.read_asks([p], ["张三"])
    assert asks[0].text == "我是[学生],学号 [学号],想请假"


def test_read_asks_skips_empty_messages(tmp_path):
    p = _write_log(tmp_path, [{"event": "qa_kind", "message": "  ", "kind": "admin"}])
    asks, _ = mf.read_asks([p], [])
    assert asks == []


def test_read_asks_ignores_a_missing_file(tmp_path, capsys):
    """文件列了但读不到(路径打错)时只告警跳过,别的文件照读。"""
    good = _write_log(tmp_path, [{"event": "qa_kind", "message": "有用的",
                                 "kind": "admin", "faq_hit": False}])
    asks, _ = mf.read_asks([tmp_path / "没有这个.log", good], [])
    assert [a.text for a in asks] == ["有用的"]
    assert "跳过" in capsys.readouterr().err


# ─── 归组与产出 ───────────────────────────────────────────────
def test_candidates_report_hits_and_scores():
    asks = [_ask("作业怎么提交", faq_hit=True, top_score=5.5),
            _ask("作业交到哪", faq_hit=False, top_score=3.2)]
    cands = mf.build_candidates(asks, [[0, 1]])
    assert len(cands) == 1
    c = cands[0]
    assert c.n_asks == 2 and c.n_variants == 2 and c.n_hit == 1
    assert not c.is_gap, "组里有一次命中就不算缺口"
    assert c.top_scores == [5.5, 3.2]


def test_n_asks_counts_occurrences_not_distinct_phrasings():
    """**这条是真实日志上炸过的坑。**

    474 条问句里去重后只有 25 种写法,其中一条的**同一种写法被问了 158 次**。
    先前 `n_asks` 数的是"不同写法数"(`len(set(...))`),那条就被算成 1 次 ——
    而 `--min-count 2` 的语义是"只问过一次的先不管",于是**全日志问得最多的问题
    被当成只问过一次丢掉了**,输出的候选列表里恰恰少了最该补的那一条。

    测试原来没抓住,是因为造的两条问句本来就是两种不同写法 ——
    "写法数 == 次数",两个定义在那时候恰好相等,改错了也看不出来。
    这里专门让**写法只有一种、次数有很多**,把两个定义掰开。
    """
    asks = [_ask("作业怎么提交")] * 40
    c = mf.build_candidates(asks, mf.cluster(asks, 0.5))[0]
    assert c.n_variants == 1, "40 次问的是同一句话"
    assert c.n_asks == 40, "但被问过 40 次 —— 筛选用的是这个数"
    assert c.phrasings == [("作业怎么提交", 40)]


def test_candidate_is_gap_only_when_nothing_hit():
    cands = mf.build_candidates([_ask("没答好的", faq_hit=False)], [[0]])
    assert cands[0].is_gap


def test_build_candidates_is_deterministic():
    """同一份日志跑两次,输出必须逐字一样 —— 否则助教没法拿两次输出做 diff,
    "上次那批我补了哪几条"就查不清了。"""
    asks = [_ask("甲问题"), _ask("乙问题"), _ask("丙问题")]
    groups = mf.cluster(asks, 0.5)
    first = mf.build_candidates(asks, groups)
    second = mf.build_candidates(asks, mf.cluster(asks, 0.5))
    assert [c.rep for c in first] == [c.rep for c in second]


def test_render_marks_gaps_and_lists_every_phrasing():
    """输出里必须列全每组的问法:归并错了助教要能当场看见 ——
    只列一个代表问法就等于把归并的错误藏起来了。

    次数与写法数都要印:只印一个的话,"一句话被问了 158 次"(该改 FAQ)
    和"158 种说法各问一次"(该改提示词或检索)看起来是一样的,而对策完全不同。
    """
    asks = [_ask("作业怎么提交"), _ask("作业交到哪", faq_hit=True)]
    out = mf.render(mf.build_candidates(asks, [[0, 1]]), 0.5, 2, "x.log", 1)
    assert "[已有]" in out
    assert "问过 2 次(2 种写法)" in out
    assert "作业交到哪" in out and "作业怎么提交" in out


def test_render_separates_group_count_from_listed_count():
    """**"聚成 0 组"和"筛完剩 0 组"是两回事。**

    原来报告里只有一个数,而且打的是筛过之后的 —— 于是真实日志那次(聚出 13 组、
    筛完 0 条)报告写的是"聚成 0 组",看着像"这条日志没东西可挖",
    真相却是"聚出来了但全被筛掉了"。两种情况的下一步动作完全不同:
    前者去查日志/问法够不够,后者去调 --min-count 或 --similarity。
    """
    out = mf.render([], 0.5, 451, "x.log", 13)
    assert "聚成 13 组" in out
    assert "列出 0 组" in out
    assert "--min-count 1" in out, "空结果要给下一步动作,不能让人自己猜"


# ─── 草稿是惰性的(用真解析器证明)──────────────────────────────
def test_draft_is_inert_because_the_answer_is_empty(tmp_path):
    """**这是整个脚本的安全网。** 草稿的 `A:` 是空的,而 `_parse_faq`
    要求 Q/A 都非空才算一条 —— 于是助教可以整批粘贴候选、再一条条填答案,
    没填的那些不会被检索到、也不会以空答案误答学生。

    这里用**真的** `FAQRetriever.from_file` 走一遍加载,而不是我自己写个断言 ——
    要证明的正是"线上那条解析路径会把它丢掉"。
    """
    cands = mf.build_candidates([_ask("作业怎么提交"), _ask("考试考什么")],
                                [[0], [1]])
    draft = tmp_path / "常问问题_草稿.txt"
    draft.write_text(mf.render_draft(cands), encoding="utf-8")

    assert FAQRetriever.from_file(draft).qa_pairs == [], \
        "A 留空的草稿绝不能被检索到"
    assert draft.read_text(encoding="utf-8").count("Q:") == 2, "但候选本身要都在"

    # 对照组:填上答案就生效 —— 证明上面那条不是"解析器永远返回空"造成的假绿。
    filled = tmp_path / "填好的.txt"
    filled.write_text(mf.render_draft(cands).replace("A:", "A: 交给学委"),
                      encoding="utf-8")
    assert len(FAQRetriever.from_file(filled).qa_pairs) == 2


def test_a_comment_in_the_draft_would_make_it_live(tmp_path):
    """**为什么草稿里一行多余的字都不能有**(不是洁癖,是有具体的坏法)。

    `_parse_faq` 在 `A:` 之后遇到非空行会**把它并进答案**(只有"空行+下一条 Q"
    或者"下一条 Q"才收尾)。所以 `A:` 后面跟一行 `# 待填`,那行注释就顶替了空答案 ——
    草稿从"惰性"变成"活着,而且答的是那行注释"。

    也就是说 `render_draft` 里"不许加注释、不许加说明、只放代表问法"这条约束
    是**为了让惰性成立**,而不是格式偏好。这条测试把那个坏法钉出来。
    """
    commented = tmp_path / "带注释.txt"
    commented.write_text("Q: 作业怎么提交\nA:\n# 待填\n", encoding="utf-8")
    pairs = FAQRetriever.from_file(commented).qa_pairs
    assert pairs, "一行注释就足以让草稿生效"
    assert "#" in pairs[0][1], "而且那行注释成了答案本身"


def test_render_draft_one_block_per_candidate():
    cands = mf.build_candidates([_ask("甲"), _ask("乙")], [[0], [1]])
    blocks = mf.render_draft(cands).strip().split("\n\n")
    assert len(blocks) == 2
    assert all(b.startswith("Q: ") and b.endswith("A:") for b in blocks)


# ─── 端到端(喂一个临时日志)──────────────────────────────────
@pytest.fixture
def log_file(tmp_path):
    """一份小日志,刻意让"FAQ 已覆盖"的那两条分别踩中**不同的**筛子。

    这里有个容易写错的地方(第一版就写错了):如果已覆盖的问题只出现**一次**,
    那它会被 `--min-count 2` 挡掉 —— 于是"默认只列缺口"这条断言通过了,
    但通过的**原因不是** is_gap 那道筛,而是 min-count 那道。
    实测:把"筛掉已覆盖"整段删掉,那条测试照样绿。所以下面必须有一条
    已覆盖、但被问过多次的问题(只可能被 is_gap 拦住),再配一条只问过一次的
    (只可能被 min-count 拦住)。
    """
    return _write_log(tmp_path, [
        # 三次同一件事、FAQ 一次没接住 → 正是要挖出来的缺口
        {"event": "qa_kind", "message": "电路基础作业怎么提交", "kind": "admin",
         "faq_hit": False, "top_score": 2.1},
        {"event": "qa_kind", "message": "电路基础作业交到哪", "kind": "admin",
         "faq_hit": False, "top_score": 1.8},
        {"event": "qa_kind", "message": "电路基础作业交给谁", "kind": "admin",
         "faq_hit": False, "top_score": 2.4},
        # 已覆盖、且被问过两次 → 只该被 is_gap 那道筛拦住
        {"event": "qa_kind", "message": "考试考什么范围", "kind": "admin",
         "faq_hit": True, "top_score": 9.9},
        {"event": "qa_kind", "message": "考试考什么范围", "kind": "admin",
         "faq_hit": True, "top_score": 9.7},
        # 已覆盖、只问过一次 → 只该被 min-count 拦住
        {"event": "qa_kind", "message": "教材用哪一本", "kind": "admin",
         "faq_hit": True, "top_score": 8.0},
    ])


def test_main_finds_the_gap_and_hides_covered_ones(log_file, monkeypatch, capsys):
    """默认参数:只列缺口(FAQ 一次没接住的),已覆盖的不列。

    "考试考什么范围"被问过两次、够得着 min-count 的门槛,所以它只可能是被
    is_gap 那道筛拦下的 —— 断言因此真的在测"筛掉已覆盖"这件事。
    """
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    monkeypatch.setattr("sys.argv", ["mine", "--logs", str(log_file), "--min-count", "2"])
    assert mf.main() == 0
    out = capsys.readouterr().out
    assert "电路基础作业" in out
    assert "考试考什么范围" not in out, "FAQ 已接住的不该出现,哪怕问过多次"


def test_main_all_flag_shows_covered_ones(log_file, monkeypatch, capsys):
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    monkeypatch.setattr("sys.argv",
                        ["mine", "--logs", str(log_file), "--min-count", "1", "--all"])
    assert mf.main() == 0
    out = capsys.readouterr().out
    assert "考试考什么范围" in out and "教材用哪一本" in out


def test_main_min_count_filters_singletons(log_file, monkeypatch, capsys):
    """默认 min-count=2:只问过一次的先不管(可能只是一个人打错字)。

    断言挑的是"教材用哪一本"(只问过一次),而不是"考试考什么范围" ——
    后者在 `--all` 下被问过两次,本来就该出现。
    """
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    monkeypatch.setattr("sys.argv", ["mine", "--logs", str(log_file), "--all"])
    assert mf.main() == 0
    out = capsys.readouterr().out
    assert "教材用哪一本" not in out, "只问过一次的默认不列"
    assert "考试考什么范围" in out, "问过两次的即使已覆盖,在 --all 下也该列出来"


def test_main_kind_filter(log_file, monkeypatch, capsys):
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    monkeypatch.setattr("sys.argv", ["mine", "--logs", str(log_file),
                                     "--kind", "concept", "--all"])
    assert mf.main() == 1, "没有 concept 记录时应当明说、而不是打一份空报告"
    assert "没有可用的问句" in capsys.readouterr().err


def test_main_warns_loudly_when_roster_is_missing(log_file, monkeypatch, capsys):
    """**告警必须刺耳。** 花名册读不到 = 姓名没被遮,而输出会被抄进 FAQ 文件。
    静默降级在这里等于"以为遮了、其实没遮"。"""
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    monkeypatch.setattr("sys.argv", ["mine", "--logs", str(log_file)])
    mf.main()
    err = capsys.readouterr().err
    assert "花名册读不到" in err and "不遮姓名" in err


def test_main_writes_the_draft(tmp_path, log_file, monkeypatch):
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    out = tmp_path / "draft.txt"
    monkeypatch.setattr("sys.argv", ["mine", "--logs", str(log_file), "--out", str(out)])
    assert mf.main() == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("Q: ") and "A:" in text
    assert FAQRetriever.from_file(out).qa_pairs == [], "写出去的草稿同样是惰性的"


def test_main_says_so_when_there_are_no_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mf.config, "LOGS_DIR", tmp_path / "空的")
    monkeypatch.setattr("sys.argv", ["mine"])
    assert mf.main() == 1
    assert "没找到日志文件" in capsys.readouterr().err


def test_default_glob_skips_the_text_log(tmp_path, monkeypatch, capsys):
    """`data/logs/` 里除了 `YYYY-MM.log`,还躺着 `llm.log` —— 那是**文本**日志,
    不是事件 JSONL,每行都 parse 不出来。把它一起 glob 进来会让"读入 N 行"
    这个数虚高(看着像有数据,其实一条都用不上),所以默认只吃 `YYYY-MM.log`。
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "llm.log").write_text("[2026-09-20] call → 200\n不是 JSON\n",
                                  encoding="utf-8")
    monkeypatch.setattr(mf.config, "LOGS_DIR", logs)
    monkeypatch.setattr("sys.argv", ["mine"])
    assert mf.main() == 1, "只有 llm.log 时应当当作没有可用日志"
    assert "没找到日志文件" in capsys.readouterr().err


def test_main_surfaces_a_repeatedly_asked_uncovered_question(tmp_path, monkeypatch,
                                                             capsys):
    """端到端:一句话被问了 40 次、FAQ 一次没接住 → 必须出现在候选里。

    这条走的是完整通路(读日志 → 归并 → 筛 → 渲染),正是真实日志上
    "最该补的那条被丢掉"的那个场景。
    """
    log = _write_log(tmp_path, [
        {"event": "qa_kind", "message": "平时分怎么算", "kind": "admin",
         "faq_hit": False, "top_score": 3.0} for _ in range(40)
    ])
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    monkeypatch.setattr("sys.argv", ["mine", "--logs", str(log)])
    assert mf.main() == 0
    out = capsys.readouterr().out
    assert "平时分怎么算" in out
    assert "问过 40 次(1 种写法)" in out, "40 次、1 种写法,不能说成 1 次"


def test_main_reports_a_real_total_on_a_real_shaped_log(tmp_path, monkeypatch, capsys):
    """端到端确认报告里的组数**不是**筛完之后剩下的那个数。

    五个题目刻意各自用不同的实词(只共享「问题」两个字),这样它们确实分成五组 ——
    如果写成「第 i 个问题」,共享的实词会让它们并成一组,这条测试就变成
    "1 组 / 列出 0 组",验不出想验的东西了。
    """
    log = _write_log(tmp_path, [
        {"event": "qa_kind", "message": f"{topic}的问题", "kind": "concept",
         "faq_hit": True}
        for topic in ("叠加原理", "戴维南定理", "功率因数", "三相电路", "磁路分析")
    ])
    monkeypatch.setattr(mf, "load_roster", lambda *a, **k: {})
    monkeypatch.setattr("sys.argv", ["mine", "--logs", str(log)])
    assert mf.main() == 0
    out = capsys.readouterr().out
    assert "聚成 5 组" in out and "列出 0 组" in out, "5 组全命中 FAQ → 筛完 0 条"
