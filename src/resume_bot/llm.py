from __future__ import annotations

import base64
import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import requests
from requests import Response

from .config import AppConfig


def _chat_completions_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _anthropic_messages_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if normalized.endswith("/v1/messages"):
        return normalized
    return f"{normalized}/v1/messages"


def _extract_anthropic_text_chunks(payload: dict[str, Any]) -> tuple[str, list[str]]:
    chunks = payload.get("content", [])
    text_blocks: list[str] = []
    content_types: list[str] = []
    for item in chunks:
        item_type = item.get("type", "")
        if item_type:
            content_types.append(item_type)
        if item_type == "text" and item.get("text"):
            text_blocks.append(item["text"])
    return "\n".join(text_blocks).strip(), content_types


def _extract_json_block(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _raise_for_status(response: Response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text.strip()
        if len(body) > 800:
            body = body[:800] + "..."
        message = f"{exc}"
        if body:
            message = f"{message}; response={body}"
        raise requests.HTTPError(message, response=response) from exc


class TextModelClient(ABC):
    @abstractmethod
    def complete_text(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
        raise NotImplementedError

    def complete_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> dict[str, Any]:
        return _extract_json_block(self.complete_text(system_prompt, user_prompt, max_tokens=max_tokens))


class NoopTextClient(TextModelClient):
    def complete_text(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
        raise RuntimeError("LLM client is not configured")


class MiniMaxAnthropicClient(TextModelClient):
    def __init__(self, base_url: str, api_key: str, model: str, *, auth_scheme: str = "x-api-key"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.auth_scheme = auth_scheme

    def _headers(self) -> dict[str, str]:
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self.auth_scheme == "bearer":
            headers["authorization"] = f"Bearer {self.api_key}"
        else:
            headers["x-api-key"] = self.api_key
        return headers

    def _post_messages(self, system_prompt: str, user_prompt: str, max_tokens: int) -> dict[str, Any]:
        response = requests.post(
            _anthropic_messages_url(self.base_url),
            headers=self._headers(),
            json={
                "model": self.model,
                "system": system_prompt,
                "max_tokens": max_tokens,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": user_prompt,
                            }
                        ],
                    }
                ],
            },
            timeout=120,
        )
        _raise_for_status(response)
        return response.json()

    def _extract_text_chunks(self, payload: dict[str, Any]) -> tuple[str, list[str]]:
        return _extract_anthropic_text_chunks(payload)

    def complete_text(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
        token_budget = max(128, max_tokens)
        last_payload: dict[str, Any] | None = None
        last_types: list[str] = []
        for _ in range(3):
            payload = self._post_messages(system_prompt, user_prompt, token_budget)
            last_payload = payload
            text, content_types = self._extract_text_chunks(payload)
            last_types = content_types
            if text:
                return text
            if payload.get("stop_reason") == "max_tokens" and "thinking" in content_types:
                token_budget = min(token_budget * 4, 8192)
                continue
            break
        stop_reason = (last_payload or {}).get("stop_reason", "unknown")
        content_types = ",".join(last_types) or "none"
        raise RuntimeError(
            f"MiniMax 返回了空文本。stop_reason={stop_reason} content_types={content_types}"
        )


class OpenAICompatibleTextClient(TextModelClient):
    def __init__(self, base_url: str, api_key: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    def complete_text(self, system_prompt: str, user_prompt: str, max_tokens: int = 2000) -> str:
        token_budget = max(512, max_tokens)
        response = requests.post(
            _chat_completions_url(self.base_url),
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": 0,
                "max_tokens": token_budget,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=120,
        )
        _raise_for_status(response)
        payload = response.json()
        return payload["choices"][0]["message"]["content"].strip()


class VisionClient:
    def __init__(self, base_url: str, api_key: str, model: str, provider_name: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider_name = provider_name

    def extract_text(self, prompt: str, image_paths: list[Path]) -> str:
        content = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            mime_type = "image/png"
            suffix = image_path.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                mime_type = "image/jpeg"
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )
        response = requests.post(
            _chat_completions_url(self.base_url),
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=180,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"].strip()


class AnthropicVisionClient:
    def __init__(self, base_url: str, api_key: str, model: str, provider_name: str = "", *, auth_scheme: str = "bearer"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.provider_name = provider_name
        self.auth_scheme = auth_scheme

    def _headers(self) -> dict[str, str]:
        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self.auth_scheme == "x-api-key":
            headers["x-api-key"] = self.api_key
        else:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def extract_text(self, prompt: str, image_paths: list[Path]) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_path in image_paths:
            mime_type = "image/png"
            suffix = image_path.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                mime_type = "image/jpeg"
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": encoded,
                    },
                }
            )
        response = requests.post(
            _anthropic_messages_url(self.base_url),
            headers=self._headers(),
            json={
                "model": self.model,
                "max_tokens": 512,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=180,
        )
        _raise_for_status(response)
        payload = response.json()
        text, content_types = _extract_anthropic_text_chunks(payload)
        if text:
            return text
        stop_reason = payload.get("stop_reason", "unknown")
        content_type_summary = ",".join(content_types) or "none"
        raise RuntimeError(f"Anthropic-compatible vision returned empty text. stop_reason={stop_reason} content_types={content_type_summary}")


def build_text_client(config: AppConfig) -> TextModelClient:
    if not (config.llm_provider and config.llm_api_key and config.llm_base_url and config.llm_model):
        return NoopTextClient()
    provider = config.llm_provider.strip().lower()
    if provider == "minimax-anthropic":
        return MiniMaxAnthropicClient(config.llm_base_url, config.llm_api_key, config.llm_model)
    if provider == "anthropic-compatible":
        return MiniMaxAnthropicClient(
            config.llm_base_url,
            config.llm_api_key,
            config.llm_model,
            auth_scheme="bearer",
        )
    if provider == "openai-compatible":
        return OpenAICompatibleTextClient(config.llm_base_url, config.llm_api_key, config.llm_model)
    return NoopTextClient()


def build_vision_client(config: AppConfig) -> VisionClient | AnthropicVisionClient | None:
    if not (config.vision_provider and config.vision_api_key and config.vision_base_url and config.vision_model):
        return None
    if config.vision_provider.strip().lower() == "openai-compatible":
        return VisionClient(
            config.vision_base_url,
            config.vision_api_key,
            config.vision_model,
            provider_name=config.vision_provider,
        )
    if config.vision_provider.strip().lower() in {"anthropic-compatible", "minimax-anthropic"}:
        auth_scheme = "x-api-key" if config.vision_provider.strip().lower() == "minimax-anthropic" else "bearer"
        return AnthropicVisionClient(
            config.vision_base_url,
            config.vision_api_key,
            config.vision_model,
            provider_name=config.vision_provider,
            auth_scheme=auth_scheme,
        )
    return None
