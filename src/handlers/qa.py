"""答疑 Handler:先 FAQ 检索,未命中则 RAG 检索,最后调 LLM 生成答复。"""
from __future__ import annotations

from src import config
from src.llm.minmax import MinMaxClient
from src.rag.retriever import BM25Retriever, FAQRetriever

_SYSTEM_PROMPT = (
    "你是中山大学数字电路与逻辑设计实验课的助教。"
    "请用简洁、准确、友好的中文回答学生的问题。"
    "优先基于【相关资料】作答;资料里没有的内容要明确说明'这个我不确定,建议问任课老师'。"
    "回答不要超过 200 字,除非学生明确要求详细。"
)


def handle(msg: str,
           faq_retriever: FAQRetriever | None = None,
           rag_retriever: BM25Retriever | None = None,
           llm: MinMaxClient | None = None) -> dict:
    faq_retriever = faq_retriever or FAQRetriever.from_default()
    llm = llm or MinMaxClient.from_env()

    # 1) FAQ 优先
    faq_hits = faq_retriever.search(msg, top_k=3)
    if faq_hits and faq_hits[0]["score"] >= config.FAQ_HIT_THRESHOLD:
        context = "\n\n".join(
            f"Q: {h['question']}\nA: {h['answer']}" for h in faq_hits[:2]
        )
        sources = ["FAQ(常问问题.txt)"]
    else:
        # 2) RAG
        rag_retriever = rag_retriever or _try_load_rag()
        if rag_retriever is None:
            return {
                "type": "question",
                "reply": "抱歉,知识库尚未构建。请先运行 `python scripts/build_index.py`。",
                "sources": [],
            }
        rag_hits = rag_retriever.search(msg, top_k=config.RAG_TOP_K)
        if not rag_hits:
            context = "(无相关资料)"
            sources = []
        else:
            context = "\n\n---\n\n".join(
                f"【来源:{h['source']}】\n{h['text']}" for h in rag_hits
            )
            sources = list({h["source"] for h in rag_hits})

    user_prompt = (
        f"【学生问题】\n{msg}\n\n"
        f"【相关资料】\n{context}\n\n"
        "【你的回答】"
    )
    reply = llm.chat(_SYSTEM_PROMPT, user_prompt, temperature=0.3)
    if not reply:
        # LLM 失败时退化:直接把最相关片段当回复
        reply = "(LLM 暂不可用,以下是相关资料原文)\n" + context[:500]

    if sources:
        reply += f"\n\n参考:{', '.join(sources)}"
    return {"type": "question", "reply": reply, "sources": sources}


def _try_load_rag() -> BM25Retriever | None:
    try:
        return BM25Retriever.load(config.BM25_INDEX_PATH)
    except FileNotFoundError:
        return None
