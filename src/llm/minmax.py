"""大模型客户端:重试、超时、日志。

模块名和类名沿用历史的 `minmax`,是为了不动业务代码里的 import
(见 src/handlers/qa.py、src/handlers/classifier.py、src/main.py)。
它现在其实是个"OpenAI 兼容"客户端:默认走 OpenCode Go,
`.env` 里没填 `LLM_API_KEY` 时自动退回旧的 MiniMax 通道。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests

from src import config


@dataclass
class MinMaxClient:
    api_key: str
    group_id: str
    model: str
    endpoint: str
    timeout: int = 20
    max_retries: int = 3
    # 下面三个是 OpenCode Go 通道才需要的;MiniMax 兜底通道留空即可
    session_header: str = ""   # 强制要求的请求头名,如 x-opencode-session
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_agent: str = ""

    @classmethod
    def from_env(cls) -> "MinMaxClient":
        """优先用新通道(OpenCode Go);`LLM_API_KEY` 为空则原样退回 MiniMax。

        这个"没配好就退回旧行为"的设计是有意的:
        万一新 key 忘了填或填错,线上不会变成哑巴,而是退回改动前的状态。
        """
        if config.LLM_API_KEY:
            return cls(
                api_key=config.LLM_API_KEY,
                group_id="",  # Go 通道不需要 group_id
                model=config.LLM_MODEL,
                endpoint=config.LLM_ENDPOINT,
                timeout=config.LLM_TIMEOUT,
                max_retries=config.LLM_MAX_RETRIES,
                session_header=config.LLM_SESSION_HEADER,
                user_agent=config.LLM_USER_AGENT,
            )
        return cls(
            api_key=config.MINMAX_API_KEY,
            group_id=config.MINMAX_GROUP_ID,
            model=config.MINMAX_MODEL,
            endpoint=config.MINMAX_ENDPOINT,
            timeout=config.LLM_TIMEOUT,
            max_retries=config.LLM_MAX_RETRIES,
        )

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, system: str, user: str, temperature: float = 0.3) -> str:
        if not self.available():
            return ""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if self.group_id:
            payload["group_id"] = self.group_id
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Cloudflare 会拦 python-requests 的默认 UA(实测 403 error code:1010)
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        # OpenCode Go 缺这个头会 400 MissingSessionID(实测)
        if self.session_header and self.session_id:
            headers[self.session_header] = self.session_id

        last_err = None
        for attempt in range(self.max_retries):
            try:
                t0 = time.time()
                resp = requests.post(
                    self.endpoint, json=payload, headers=headers,
                    timeout=self.timeout,
                )
                dt = time.time() - t0
                if resp.status_code == 200:
                    data = resp.json()
                    text = _extract_text(data)
                    _log_llm(payload, text, dt, ok=True)
                    return text
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                # 4xx 是"我们请求得不对"(key 错、缺头、模型名错),重试一百次也一样,
                # 只有 429(限流)值得等一等再试。
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    break
            except requests.RequestException as e:
                last_err = str(e)
            time.sleep(0.5 * (2 ** attempt))  # 指数退避

        _log_llm(payload, f"FAILED: {last_err}", 0.0, ok=False)
        return ""


def _extract_text(data: dict) -> str:
    """从响应里抠出文本。

    两种响应都吃:
    - OpenAI 兼容结构(OpenCode Go):choices[0].message.content
    - 旧结构(MiniMax 老版本):顶层 reply 字段
    推理型模型(content 为空、只有 reasoning_content)时退回取思维链,
    否则会"明明调通了却返回空字符串"。
    """
    try:
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message", {}) or {}
            for key in ("content", "reasoning_content"):
                val = msg.get(key)
                if val:
                    return str(val).strip()
    except Exception:
        pass
    # 兜底:老版本可能直接有 "reply" 字段
    if "reply" in data:
        return str(data["reply"]).strip()
    return ""


def _log_llm(payload: dict, output: str, elapsed: float, ok: bool) -> None:
    try:
        line = json.dumps({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ok": ok,
            "model": payload.get("model"),
            "user_len": len(payload["messages"][-1]["content"]),
            "out_len": len(output),
            "elapsed_s": round(elapsed, 2),
        }, ensure_ascii=False)
        path: Path = config.llm_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
