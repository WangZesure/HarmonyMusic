"""DeepSeek client for the Agent (phase 3). Retry + on-disk cache + token stats.

Model name always comes from .env (`DEEPSEEK_CHAT_MODEL`) — never hardcoded.
Responses are cached by prompt hash so re-running the eval costs nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from openai import OpenAI

import agent.config as cfg

_CACHE = cfg.LOG_DIR.parent / "cache" / "deepseek_agent"
_CACHE.mkdir(parents=True, exist_ok=True)


class LLM:
    def __init__(self, model: str | None = None):
        import config as dp
        if not dp.DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY not set in .env")
        self.model = model or dp.DEEPSEEK_CHAT_MODEL
        self.base_url = dp.DEEPSEEK_BASE_URL
        self.client = OpenAI(api_key=dp.DEEPSEEK_API_KEY, base_url=dp.DEEPSEEK_BASE_URL)
        # strict/json modes need the beta endpoint (phase3 §2.6)
        self.beta_client = OpenAI(api_key=dp.DEEPSEEK_API_KEY, base_url=dp.DEEPSEEK_BASE_URL.rstrip("/") + "/beta")
        self.stats = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def _cache_path(self, key: str) -> Path:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]
        return _CACHE / f"{h}.json"

    def complete(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.2,
                 use_cache: bool = True, beta: bool = False, **kwargs) -> str:
        key = json.dumps({"m": self.model, "msgs": messages, "json": json_mode,
                          "t": temperature, **kwargs}, ensure_ascii=False, sort_keys=True)
        cp = self._cache_path(key)
        if use_cache and cp.exists():
            return json.loads(cp.read_text(encoding="utf-8"))["content"]

        client = self.beta_client if (beta or json_mode) else self.client
        last_err = None
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=self.model, messages=messages, temperature=temperature,
                    response_format={"type": "json_object"} if json_mode else None, **kwargs)
                content = resp.choices[0].message.content or ""
                self.stats["calls"] += 1
                if resp.usage:
                    self.stats["prompt_tokens"] += resp.usage.prompt_tokens
                    self.stats["completion_tokens"] += resp.usage.completion_tokens
                if use_cache:
                    cp.write_text(json.dumps({"content": content}, ensure_ascii=False), encoding="utf-8")
                return content
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"DeepSeek call failed: {last_err}")

    def complete_json(self, messages: list[dict], **kw) -> dict:
        return json.loads(self.complete(messages, json_mode=True, **kw))
