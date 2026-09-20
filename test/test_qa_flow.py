"""答疑编排层:`handle()` 走哪条通路、喂给大模型的上下文里有什么、答复怎么拼。

**为什么单独有这个文件。**
`_classify()`(29 条问法)和 `_is_problem_query()`(22 条问法)早有表驱动测试,
但它们测的都是"**判成什么档**";真正决定学生拿到什么的,是判完之后**走哪条路**:
要不要查资料、FAQ 是不是短路、`sources` 里有什么、大模型挂了怎么办。
2026-09-20 找到的两个缺陷(`参考:`顺序随进程变、admin 快路注释与行为不符)
**都住在这层**,而这层此前零测试 —— 因为以前没有注入缝,只能联网测。

`handle()` 的签名本来就支持注入 `faq_retriever` / `rag_retriever` / `llm`,
所以这里全程用假对象,**不联网、无 API Key 也能跑**(conftest 的 offline_http
还会兜住漏网的真实请求)。
"""
from __future__ import annotations

from datetime import date

import pytest

from src.handlers import qa
from src.utils.course_facts import Fact

# ─── 假对象 ───────────────────────────────────────────────────
FAQ_Q = "电路基础理论的作业怎么提交?要交给学委吗?"
FAQ_A = "交给学委,第二次及以后补交会酌情扣分。"


class FakeFAQ:
    def __init__(self, hits):
        self.hits = hits
        self.searches: list[str] = []

    def search(self, q, top_k=3):
        self.searches.append(q)
        return self.hits[:top_k]


class FakeRAG:
    """假检索器。默认**照规矩截断到 top_k**(和真检索器一样)。

    `truncate=False` 是给"顺序与去重"那个测试用的:那条测试要看到**全部**
    命中来验证排序,如果被 top_k 截掉一半,断言就得跟着 `RAG_TOP_K` 的值走 ——
    而 `RAG_TOP_K` 是会调的(5 提到 7 过),测试不该随它一起漂。
    """

    def __init__(self, hits=(), truncate=True):
        self.hits = list(hits)
        self.truncate = truncate
        self.searches: list[str] = []

    def search(self, q, top_k=5):
        self.searches.append(q)
        return self.hits[:top_k] if self.truncate else list(self.hits)


class SpyLLM:
    """记录每次调用的 prompt,方便断言"喂进去的上下文里到底有什么"。"""

    def __init__(self, reply="（模型答复）"):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def chat(self, system, user, temperature=0.3):
        self.calls.append((system, user))
        return self.reply

    @property
    def user_prompts(self) -> str:
        """把所有 user prompt 拼起来,便于 `in` 断言。"""
        return "\n".join(u for _s, u in self.calls)


def faq_hit(score=9.0):
    return [{"question": FAQ_Q, "answer": FAQ_A, "score": score}]


def rag_hit(source="第1章-作业答案.md", text="1.36 的解答……", score=12.0):
    return {"chunk_id": 0, "text": text, "source": source,
            "path": f"/x/{source}", "score": score}


# ─── 事务档 + FAQ 命中:不查资料 ───────────────────────────────
def test_admin_with_faq_hit_does_not_touch_rag():
    """事务题命中 FAQ 时不查资料:FAQ 就是权威来源,少一次检索噪声。"""
    rag = FakeRAG([rag_hit()])
    out = qa.handle("电路基础作业怎么提交?", faq_retriever=FakeFAQ(faq_hit()),
                    rag_retriever=rag, llm=SpyLLM())
    assert rag.searches == [], "admin+FAQ 命中时不该查资料"
    assert out["sources"] == ["FAQ(常问问题.txt)"]


def test_admin_faq_path_still_polishes_with_llm():
    """**这条是量过之后才钉死的,别照着旧注释"改回去"。**

    代码里原来的注释写"直接答,**不调大模型**",但实际一直在调。到底该补 return
    还是该改注释?实测:28 条能自命中的事务类 FAQ 逐条走真实通路,FAQ 答案里的
    数字**一条都没丢也没被改**;看着像"多出来"的数字经查全是「一般」「一下」
    「乱七八糟」这类中文数字当普通字用的误报。既然润色没引入可观测的失真,
    就保留调用(措辞更自然、还能把第 2 条同义 FAQ 合进来),只把注释改对。

    所以这个测试**故意**断言"仍然调了大模型,且 FAQ 原文在 prompt 里"。
    谁要改成不调大模型,得先拿出反过来的测量数据。
    """
    llm = SpyLLM()
    qa.handle("电路基础作业怎么提交?", faq_retriever=FakeFAQ(faq_hit()),
              rag_retriever=FakeRAG(), llm=llm)
    assert len(llm.calls) == 1, "当下行为:事务题也交给大模型润色一次"
    assert FAQ_A in llm.user_prompts, "FAQ 原文必须作为上下文喂进去"


def test_admin_without_faq_hit_falls_back_to_rag():
    """事务题没命中 FAQ → 老老实实查资料(提示词第②条会拦住它瞎猜)。"""
    rag = FakeRAG([rag_hit(text="补交规则:……")])
    out = qa.handle("电路基础作业怎么提交?", faq_retriever=FakeFAQ([]),
                    rag_retriever=rag, llm=SpyLLM())
    assert rag.searches, "没命中 FAQ 就该查资料"
    assert "第1章-作业答案.md" in out["sources"]


# ─── 现行事务口径(课程事务事实表)─────────────────────────────
# 事实表能**覆盖** FAQ,而 FAQ 对事务题的命中率本来就很高(90.3% 那个数是拿
# 被单测污染的日志算出来的,见 src/utils/course_facts.py 顶部的勘误)。
# 所以"什么时候注入、注入在哪、冲突时谁在前"这几件事都得钉住。
def facts_block(text="补交需在截止后 7 天内联系助教登记"):
    return [Fact(text=text, source="张老师通知",
                 effective_from=date(2026, 9, 1), effective_until=date(2027, 1, 31))]


def test_admin_facts_go_in_above_the_faq():
    """事务题:**即使 FAQ 命中了**也要注入现行事实,且排在 FAQ 前面。

    这条是针对"FAQ 覆盖率越高越危险"设计的:90% 的事务提问会命中 FAQ,
    而 FAQ 条目不会过期 —— 只要注入条件写成"FAQ 没命中才注入",
    改截止时间这种最常见的情况就完全没被覆盖到。
    顺序也不是装饰:提示词里写的是"冲突时以【课程事务(现行)】为准",
    事实排在后面的话,模型先读到的是那句旧的 FAQ。
    """
    llm = SpyLLM()
    out = qa.handle("电路基础作业怎么提交?", faq_retriever=FakeFAQ(faq_hit()),
                    rag_retriever=FakeRAG(), llm=llm, facts=facts_block())
    prompts = llm.user_prompts
    assert "【课程事务(现行)】" in prompts, "事务题必须带上现行口径"
    assert prompts.index("【课程事务(现行)】") < prompts.index(FAQ_A), \
        "现行事实必须排在 FAQ 前面"
    assert out["sources"][0] == "课程事务(现行)", "用了它就得在「参考:」里说出来"


def test_admin_without_faq_hit_also_gets_facts():
    """事务题没命中 FAQ 时同样要注入(这条覆盖的是那 9.7%)。"""
    llm = SpyLLM()
    qa.handle("补交怎么办?", faq_retriever=FakeFAQ([]),
              rag_retriever=FakeRAG([rag_hit(text="补交规则……")]),
              llm=llm, facts=facts_block())
    prompts = llm.user_prompts
    assert "【课程事务(现行)】" in prompts
    assert prompts.index("【课程事务(现行)】") < prompts.index("【课程资料】"), \
        "现行事实要排在课程资料前面"


def test_concept_and_problem_never_get_admin_facts():
    """概念题/题目类**不注入**现行事务口径:那是事务档的依据,
    塞进概念题的上下文只会让模型扯到截止时间上去。"""
    for msg in ("什么是叠加原理", "1.36 怎么做"):
        llm = SpyLLM()
        qa.handle(msg, faq_retriever=FakeFAQ([]),
                  rag_retriever=FakeRAG([rag_hit()]), llm=llm, facts=facts_block())
        assert "【课程事务(现行)】" not in llm.user_prompts, msg


def test_no_facts_file_changes_nothing():
    """事实表为空(文件不存在)时,行为与加这个功能之前**完全一致** ——
    它是"有则更好",不是"没有就跑不了"。"""
    llm = SpyLLM()
    out = qa.handle("电路基础作业怎么提交?", faq_retriever=FakeFAQ(faq_hit()),
                    rag_retriever=FakeRAG(), llm=llm, facts=[])
    assert "【课程事务(现行)】" not in llm.user_prompts
    assert out["sources"] == ["FAQ(常问问题.txt)"]


def test_system_prompt_actually_grants_facts_their_authority():
    """注入的块必须在**提示词里有对应的效力规则**,否则它只是一段背景文字。

    这条是防止"两头脱节":有人删掉提示词里那两句话,注入还在、效力没了,
    事务答复会退回"资料里没有就说不确定" —— 功能看着还在,其实已经废了。
    """
    assert "【课程事务(现行)】" in qa._SYSTEM_PROMPT
    assert "一律以它为准" in qa._SYSTEM_PROMPT


# ─── 检索诊断日志(挖矿脚本的素材)─────────────────────────────
# 这些字段的存在理由是"让日志能被挖":`scripts/mine_faq_candidates.py` 靠
# kind / faq_hit / top_score 挑出"学生真问过、但库没答好"的问法。
# 所以它们不是装饰,少一个字段挖矿就瞎一角 —— 于是也钉进测试。
@pytest.fixture
def logged(monkeypatch):
    """把 qa 模块里的 log_event 换成收集器(它是 `from ... import` 进来的,改 qa 上的名字)。"""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(qa, "log_event", lambda ev, **kw: events.append((ev, kw)))
    return events


def _one_kind_event(logged) -> dict:
    kinds = [kw for name, kw in logged if name == "qa_kind"]
    assert len(kinds) == 1, f"每次答疑应当只落一条 qa_kind,实际 {len(kinds)} 条"
    return kinds[0]


def test_diagnostics_for_the_faq_path(logged):
    qa.handle("电路基础作业怎么提交?", faq_retriever=FakeFAQ(faq_hit()),
              rag_retriever=FakeRAG(), llm=SpyLLM(), facts=[])
    kw = _one_kind_event(logged)
    assert kw["path"] == "faq"
    assert kw["n_hits"] == 0 and kw["top_score"] is None, "走 FAQ 快路时没查资料"
    assert kw["faq_top"] == 9.0
    assert kw["message"].startswith("电路基础作业怎么提交")


def test_diagnostics_carry_top_score_and_sources(logged):
    qa.handle("什么是叠加原理", faq_retriever=FakeFAQ([]),
              rag_retriever=FakeRAG([rag_hit(source="教材-第4章-电路定理.md", score=12.3456)]),
              llm=SpyLLM(), facts=[])
    kw = _one_kind_event(logged)
    assert kw["path"] == "rag"
    assert kw["n_hits"] == 1
    assert kw["top_score"] == 12.346, "分数要落盘成能比较的数值(保留 3 位)"
    assert kw["sources"] == ["教材-第4章-电路定理.md"]


def test_diagnostics_record_the_no_index_case(logged, monkeypatch):
    """"索引没建"也要落一条 —— 否则线上出现这种答复时日志里什么都看不到。"""
    monkeypatch.setattr(qa, "_try_load_rag", lambda: None)
    qa.handle("什么是叠加原理", faq_retriever=FakeFAQ([]),
              rag_retriever=None, llm=SpyLLM(), facts=[])
    assert _one_kind_event(logged)["path"] == "no_index"


def test_diagnostics_count_facts(logged):
    """事实表有没有被用上,必须能从日志里区分 —— 否则"改了规则没生效"查不出来。"""
    qa.handle("补交怎么办?", faq_retriever=FakeFAQ([]), rag_retriever=FakeRAG(),
              llm=SpyLLM(), facts=facts_block("补交需 7 天内登记"))
    kw = _one_kind_event(logged)
    assert kw["n_facts"] == 1 and kw["facts_used"] is True


# ─── 概念档:FAQ 是加成不是短路 ────────────────────────────────
def test_concept_with_faq_hit_is_additive_not_short_circuit():
    """概念题即使命中 FAQ 也**必须继续查资料** —— 概念讲解要能展开,不能被短路。"""
    rag = FakeRAG([rag_hit(source="教材-第4章-电路定理.md", text="叠加……")])
    llm = SpyLLM()
    out = qa.handle("什么是叠加原理", faq_retriever=FakeFAQ(faq_hit()),
                    rag_retriever=rag, llm=llm)
    assert rag.searches, "概念题必须查资料"
    assert "教材-第4章-电路定理.md" in llm.user_prompts, "资料要喂进去"
    assert FAQ_A in llm.user_prompts, "命中的 FAQ 作为补充材料保留"
    # 资料在前、FAQ 在后(提示词按【课程资料】【常问问题】两块读)
    assert llm.user_prompts.index("教材-第4章-电路定理.md") < llm.user_prompts.index(FAQ_A)
    assert "教材-第4章-电路定理.md" in out["sources"]
    assert "FAQ(常问问题.txt)" in out["sources"]


def test_problem_query_never_gets_faq_noise():
    """带题号的提问不加 FAQ:问 1.36 时那条"作业怎么提交"纯属噪声,只会误导。"""
    rag = FakeRAG([rag_hit(source="1.36.md", text="I = 20/0.25")])
    llm = SpyLLM()
    out = qa.handle("电路基础第一章作业的1.36怎么做", faq_retriever=FakeFAQ(faq_hit()),
                    rag_retriever=rag, llm=llm)
    assert rag.searches, "带题号必须先查资料"
    assert FAQ_A not in llm.user_prompts, "题目类不该混进 FAQ 噪声"
    assert out["sources"] == ["1.36.md"]


# ─── sources 的顺序与去重(2026-09-20 修的那个 bug)────────────
def test_sources_follow_retrieval_order_and_dedupe():
    """`参考:`里的文件必须**按检索得分降序**、且去重。

    原来写的是 `list({h["source"] for h in rag_hits})`,集合顺序取决于字符串哈希,
    而 Python 每进程的哈希种子随机 → 同一句话两次运行的文件顺序不一样。
    这里用 12 个来源:随机顺序碰巧等于入参顺序的概率可以忽略,所以只要有人改回
    `list(set(...))`,这个测试就会红。
    """
    names = [f"教材-第{i}章-单元{i}.md" for i in range(1, 13)]
    hits = [rag_hit(source=n, score=20.0 - i) for i, n in enumerate(names)]
    hits.append(rag_hit(source=names[3], score=1.0))   # 同一文件再来一块
    out = qa.handle("随便问问", faq_retriever=FakeFAQ([]),
                    rag_retriever=FakeRAG(hits, truncate=False), llm=SpyLLM())
    assert out["sources"] == names, "应按得分降序且去重"


def test_sources_dedupe_keeps_first_occurrence():
    """同一文件的多个分块只出现一次,且保留它**最高分**那次的位置。"""
    hits = [rag_hit(source="A.md", score=9.0),
            rag_hit(source="B.md", score=8.0),
            rag_hit(source="A.md", score=7.0)]
    out = qa.handle("随便问问", faq_retriever=FakeFAQ([]),
                    rag_retriever=FakeRAG(hits), llm=SpyLLM())
    assert out["sources"] == ["A.md", "B.md"]


# ─── 边界情况 ─────────────────────────────────────────────────
def test_missing_index_gives_actionable_message(monkeypatch):
    """索引没建时要说清"去跑 build_index",而不是抛异常或答非所问。"""
    monkeypatch.setattr(qa, "_try_load_rag", lambda: None)
    out = qa.handle("什么是叠加原理", faq_retriever=FakeFAQ([]),
                    rag_retriever=None, llm=SpyLLM())
    assert "build_index" in out["reply"]
    assert out["sources"] == []


def test_no_hits_at_all_still_answers():
    """检索一无所获也要给模型一个明确的"(无相关资料)",不能拼出空 prompt。"""
    llm = SpyLLM()
    out = qa.handle("什么是叠加原理", faq_retriever=FakeFAQ([]),
                    rag_retriever=FakeRAG([]), llm=llm)
    assert "(无相关资料)" in llm.user_prompts
    assert out["reply"]


def test_llm_failure_degrades_to_raw_context():
    """大模型返回空串(超时/没 key)时退化:把最相关的原文片段当回复,别给学生空白。"""
    out = qa.handle("什么是叠加原理", faq_retriever=FakeFAQ([]),
                    rag_retriever=FakeRAG([rag_hit(text="叠加原理的原文……")]),
                    llm=SpyLLM(reply=""))
    assert "LLM 暂不可用" in out["reply"]
    assert "叠加原理的原文" in out["reply"]


def test_faq_context_caps_at_two_entries():
    """FAQ 命中最多取 2 条(第 2 条常常是同义问法),多了会挤掉资料。"""
    hits = [dict(question=f"问题{i}", answer=f"答案{i}", score=9.0 - i)
            for i in range(4)]
    llm = SpyLLM()
    qa.handle("什么是叠加原理", faq_retriever=FakeFAQ(hits),
              rag_retriever=FakeRAG([]), llm=llm)
    assert "答案0" in llm.user_prompts and "答案1" in llm.user_prompts
    assert "答案2" not in llm.user_prompts


def test_reply_ends_with_sources_line():
    """答复末尾要带「参考:」,让学生知道去哪儿翻。"""
    out = qa.handle("什么是叠加原理", faq_retriever=FakeFAQ([]),
                    rag_retriever=FakeRAG([rag_hit(source="教材-第4章-电路定理.md")]),
                    llm=SpyLLM(reply="叠加原理是……"))
    assert out["reply"].endswith("参考:教材-第4章-电路定理.md")


def test_no_sources_line_when_nothing_retrieved():
    """什么都没检索到时不该硬凑一行空的「参考:」。"""
    out = qa.handle("什么是叠加原理", faq_retriever=FakeFAQ([]),
                    rag_retriever=FakeRAG([]), llm=SpyLLM(reply="叠加原理是……"))
    assert "参考:" not in out["reply"]


@pytest.mark.parametrize("msg,expect_kind", [
    ("电路基础作业怎么提交?", "admin"),
    ("什么是叠加原理", "concept"),
    ("1.36 怎么做", "problem"),
    ("考试会考叠加定理吗", "admin"),   # 事务强信号盖过概念词
    ("作业里的叠加定理怎么理解", "concept"),  # 概念词盖过事务弱信号
])
def test_kind_routes_to_the_expected_path(msg, expect_kind):
    """分档 → 通路:这里只钉"判成什么档",通路行为由上面的用例覆盖。"""
    assert qa._classify(msg) == expect_kind
