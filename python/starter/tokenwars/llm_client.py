"""Minimal OpenAI-compatible v1 client (raw HTTP with `requests`) plus an offline mock mode."""
from __future__ import annotations

import json
import math
import random
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from .config import AppConfig, ConfigError, ModelConfig
from .context import tokenize

TIMEOUT_SECONDS = 60

_length_warning_lock = threading.Lock()
_length_warning_shown = False
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 4


class LlmError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0


@dataclass
class ChatResult:
    model_key: str
    text: str
    usage: Usage
    latency_ms: int
    finish_reason: str | None = None


@dataclass
class EmbeddingResult:
    model_key: str
    vectors: list[list[float]]
    usage: Usage
    latency_ms: int


class LlmClient:
    def __init__(self, config: AppConfig, use_gateway: bool = True, retry_on_throttle: bool = False,
                 mock: bool | None = None):
        self.config = config
        self.use_gateway = use_gateway
        self.retry_on_throttle = retry_on_throttle
        self.mock = config.mock if mock is None else mock
        self._local = threading.local()

    # ------------------------------------------------------------------ public API

    def chat(self, model_key: str, messages: list[dict[str, str]], temperature: float | None = None,
             max_tokens: int | None = None, response_format: dict[str, Any] | None = None,
             purpose: str = "answer") -> ChatResult:
        """purpose: "answer" | "classifier" | "judge" | "doctor" (used by the mock and for warnings)."""
        model = self.config.model(model_key)
        body: dict[str, Any] = {"model": model.deployment, "messages": messages}
        if temperature is not None and model.supports_temperature:
            body["temperature"] = temperature
        if max_tokens is not None:
            body[model.max_tokens_param] = max_tokens
        if response_format is not None:
            body["response_format"] = response_format
        body.update(model.extra_body)  # e.g. {"reasoning_effort": "none"} for the GPT-5.6 family

        started = time.perf_counter()
        if self.mock:
            data = self._send(lambda: _mock_chat(model, messages, purpose))
        else:
            url, key = self._endpoint(model)
            data = self._send(lambda: self._post(url + "chat/completions", key, body, model.extra_headers))
        latency = int((time.perf_counter() - started) * 1000)

        try:
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"Unexpected chat response from {model_key}: {str(data)[:300]}") from exc
        if not text.strip() and finish_reason == "length":
            text = ""  # recorded as an empty answer, which fails the judge
            if purpose == "answer":
                _warn_empty_length(model_key, max_tokens)
        return ChatResult(model_key, text, _parse_usage(data), latency, finish_reason)

    def embed(self, model_key: str, inputs: list[str]) -> EmbeddingResult:
        model = self.config.model(model_key)
        body = {"model": model.deployment, "input": inputs}
        started = time.perf_counter()
        if self.mock:
            data = self._send(lambda: _mock_embeddings(inputs))
        else:
            url, key = self._endpoint(model)
            data = self._send(lambda: self._post(url + "embeddings", key, body, model.extra_headers))
        latency = int((time.perf_counter() - started) * 1000)
        try:
            rows = sorted(data["data"], key=lambda r: r.get("index", 0))
            vectors = [row["embedding"] for row in rows]
        except (KeyError, TypeError) as exc:
            raise LlmError(f"Unexpected embeddings response from {model_key}: {str(data)[:300]}") from exc
        return EmbeddingResult(model_key, vectors, _parse_usage(data), latency)

    # ------------------------------------------------------------------ HTTP

    def _endpoint(self, model: ModelConfig) -> tuple[str, str]:
        if model.key != self.config.scoring.get("judge_model", "judge"):
            if not self.use_gateway or not model.via_gateway:
                raise ConfigError(f'APIM is required for "{model.key}": use_gateway and via_gateway must be true.')
            gateway = self.config.gateway
            if gateway is None or not gateway.base_url:
                raise ConfigError("APIM is required: deploy the gateway and regenerate models.json.")
            key = self.config.get_env(gateway.api_key_env)
            if not key:
                raise ConfigError(f"APIM subscription key is missing ({gateway.api_key_env}).")
            if any(name.lower() in {"api-key", "authorization", "ocp-apim-subscription-key"}
                   for name in model.extra_headers):
                raise ConfigError(f'Model "{model.key}": extra_headers must not override gateway credentials.')
            return gateway.base_url, key
        if model.via_gateway:
            raise ConfigError('The judge must use via_gateway: false; it is excluded from the team token budget.')
        if not model.base_url:
            raise LlmError(f'Model "{model.key}" has no base_url in models.json')
        return model.base_url, self.config.get_env(model.api_key_env)

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session

    def _post(self, url: str, api_key: str, body: dict[str, Any],
              extra_headers: dict[str, str] | None = None) -> requests.Response:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["api-key"] = api_key
            headers["Authorization"] = f"Bearer {api_key}"
        # Gateway credential overrides are rejected by _endpoint; affinity headers remain supported.
        for name, value in (extra_headers or {}).items():
            if name.lower() == "content-type":
                continue
            for existing in [k for k in headers if k.lower() == name.lower()]:
                del headers[existing]
            headers[name] = value
        return self._session().post(url, data=json.dumps(body), headers=headers, timeout=TIMEOUT_SECONDS)

    def _send(self, send_once: Callable[[], Any]) -> dict[str, Any]:
        """Send a request (HTTP or mock). With retry_on_throttle, retry throttled/transient errors."""
        if self.retry_on_throttle:
            response = self._send_with_retry(send_once)
        else:
            response = send_once()
        return _json_or_raise(response)

    # TODO 3.4 – Throttle-aware retry (Challenge 3 "Route & Rule")
    # Under load Azure OpenAI / APIM answer HTTP 429 (Too Many Requests) and tell you how long to wait.
    # Without a retry the item simply fails (success=false). Implement:
    #   for attempt in range(MAX_RETRIES + 1):
    #       response = send_once()
    #       if getattr(response, "status_code", 200) not in RETRYABLE_STATUS or it was the last attempt:
    #           return response
    #       wait = response.headers "retry-after-ms" (milliseconds) or "retry-after" (seconds) if present,
    #              otherwise exponential backoff 1s, 2s, 4s, 8s (2 ** attempt) + a little random jitter
    #       time.sleep(wait)
    # (Mock responses are plain dicts without status_code, so they are returned immediately.)
    # Then set "retry_on_throttle": true in strategy.json.
    def _send_with_retry(self, send_once: Callable[[], Any]) -> Any:
        raise NotImplementedError("TODO 3.4 not implemented yet – see tokenwars/llm_client.py")


def _warn_empty_length(model_key: str, max_tokens: int | None) -> None:
    global _length_warning_shown
    with _length_warning_lock:
        if _length_warning_shown:
            return
        _length_warning_shown = True
    print(f"⚠️  {model_key} returned an EMPTY answer (finish_reason=length, cap={max_tokens}). Reasoning tokens count "
          "towards the output cap – raise max_output_tokens in strategy.json (or lower reasoning_effort).",
          file=sys.stderr)


def _json_or_raise(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):  # mock
        return response
    if response.status_code >= 400:
        raise LlmError(f"HTTP {response.status_code}: {response.text[:300]}", status=response.status_code)
    try:
        return response.json()
    except ValueError as exc:
        raise LlmError(f"Invalid JSON response: {response.text[:300]}") from exc


def _parse_usage(data: dict[str, Any]) -> Usage:
    usage = data.get("usage") or {}
    details = usage.get("prompt_tokens_details") or {}
    return Usage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int(details.get("cached_tokens") or 0),
    )


# ---------------------------------------------------------------------- mock mode


def _mock_chat(model: ModelConfig, messages: list[dict[str, str]], purpose: str) -> dict[str, Any]:
    if purpose == "classifier":
        text = "SIMPLE"
    elif purpose == "judge":
        text = '{"score": 5, "reason": "mock"}'
    else:
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        text = f"[mock:{model.deployment}] " + last_user[:160]
    total_chars = sum(len(m.get("content") or "") for m in messages)
    return {
        "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": math.ceil(total_chars / 4),
            "completion_tokens": math.ceil(len(text) / 4),
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }


def _fnv1a_32(text: str) -> int:
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def mock_embedding(text: str, dims: int = 256) -> list[float]:
    vector = [0.0] * dims
    for token in tokenize(text):
        vector[_fnv1a_32(token) % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else vector


def _mock_embeddings(inputs: list[str]) -> dict[str, Any]:
    return {
        "data": [{"index": i, "embedding": mock_embedding(text)} for i, text in enumerate(inputs)],
        "usage": {"prompt_tokens": math.ceil(sum(len(t) for t in inputs) / 4)},
    }
