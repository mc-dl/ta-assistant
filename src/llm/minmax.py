"""MinMax API 封装:重试、超时、日志。"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
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

    @classmethod
    def from_env(cls) -> "MinMaxClient":
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
            except requests.RequestException as e:
                last_err = str(e)
            time.sleep(0.5 * (2 ** attempt))  # 指数退避

        _log_llm(payload, f"FAILED: {last_err}", 0.0, ok=False)
        return ""


def _extract_text(data: dict) -> str:
    """尝试从不同版本的 MinMax 响应中抠出文本。"""
    try:
        choices = data.get("choices") or []
        if choices:
            msg = choices[0].get("message", {})
            if "content" in msg:
                return str(msg["content"]).strip()
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
