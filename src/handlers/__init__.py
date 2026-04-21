"""Handlers 包:classifier、submission、qa。"""
from src.handlers.classifier import classify
from src.handlers.qa import handle as qa_handle
from src.handlers.submission import handle as submission_handle

__all__ = ["classify", "qa_handle", "submission_handle"]
