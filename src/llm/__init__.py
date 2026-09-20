"""LLM 包:大模型客户端(默认走 OpenCode Go,可退回 MiniMax)。"""
from src.llm.minmax import MinMaxClient

__all__ = ["MinMaxClient"]
