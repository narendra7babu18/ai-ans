from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote, urlparse

import httpx


Protocol = Literal["openai", "gemini", "anthropic"]

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "gemini": {
        "name": "Google Gemini",
        "protocol": "gemini",
        "base_url": "https://generativelanguage.googleapis.com",
    },
    "openai": {
        "name": "OpenAI",
        "protocol": "openai",
        "base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
    },
    "deepseek": {
        "name": "DeepSeek",
        "protocol": "openai",
        "base_url": "https://api.deepseek.com/v1",
    },
    "mistral": {
        "name": "Mistral AI",
        "protocol": "openai",
        "base_url": "https://api.mistral.ai/v1",
    },
    "groq": {
        "name": "Groq",
        "protocol": "openai",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "openrouter": {
        "name": "OpenRouter",
        "protocol": "openai",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "together": {
        "name": "Together AI",
        "protocol": "openai",
        "base_url": "https://api.together.xyz/v1",
    },
    "fireworks": {
        "name": "Fireworks AI",
        "protocol": "openai",
        "base_url": "https://api.fireworks.ai/inference/v1",
    },
    "huggingface": {
        "name": "Hugging Face Inference",
        "protocol": "openai",
        "base_url": "https://router.huggingface.co/v1",
    },
    "cohere": {
        "name": "Cohere",
        "protocol": "openai",
        "base_url": "https://api.cohere.com/compatibility/v1",
    },
    "xai": {
        "name": "xAI Grok",
        "protocol": "openai",
        "base_url": "https://api.x.ai/v1",
    },
    "perplexity": {
        "name": "Perplexity",
        "protocol": "openai",
        "base_url": "https://api.perplexity.ai",
    },
    "ollama": {
        "name": "Ollama",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:11434/v1",
    },
    "custom": {
        "name": "Custom OpenAI-compatible",
        "protocol": "openai",
        "base_url": "",
    },
}


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    provider_id: str
    display_name: str
    protocol: Protocol
    base_url: str
    model_id: str
    api_key: str
    organization: str
    enabled: bool = True


class ProviderError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_base_url(value: str, protocol: Protocol) -> str:
    if protocol == "gemini":
        return "https://generativelanguage.googleapis.com"
    parsed = urlparse(value.strip())
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in local_hosts)
    ):
        raise ValueError("Use an HTTPS provider URL. HTTP is allowed only for localhost Ollama.")
    return value.strip().rstrip("/")


def safe_provider_error(status_code: int) -> ProviderError:
    messages = {
        400: "The provider rejected the request. Check the model ID and provider settings.",
        401: "The provider rejected this API key. Replace it with a valid key.",
        402: "The provider reports a billing or account balance problem.",
        403: "The provider denied access. Check account permissions, project access, or billing eligibility.",
        404: "The provider endpoint or model was not found. Check the base URL and exact model ID.",
        408: "The provider timed out while processing the request.",
        429: "The provider rate limit or usage quota was reached.",
    }
    if status_code >= 500:
        return ProviderError("The provider is temporarily unavailable. Try again later.", 502)
    return ProviderError(
        messages.get(status_code, f"The provider returned HTTP {status_code}. Check its settings."),
        502 if status_code < 400 else status_code,
    )


class ProviderAdapter:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.base_url = validate_base_url(config.base_url, config.protocol)

    def _headers(self) -> dict[str, str]:
        if self.config.protocol == "gemini":
            return {"x-goog-api-key": self.config.api_key}
        if self.config.protocol == "anthropic":
            return {
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            }
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        if self.config.organization:
            headers["OpenAI-Organization"] = self.config.organization
        return headers

    async def _ensure_safe_endpoint(self) -> None:
        parsed = urlparse(self.base_url)
        hostname = parsed.hostname or ""
        if (
            self.config.provider_id == "ollama"
            and hostname in {"localhost", "127.0.0.1", "::1"}
            and parsed.port == 11434
        ):
            return
        try:
            address = ipaddress.ip_address(hostname)
            addresses = [address]
        except ValueError:
            known_provider_hosts = {
                urlparse(preset["base_url"]).hostname
                for preset in PROVIDER_PRESETS.values()
                if preset["base_url"]
            }
            if hostname in known_provider_hosts:
                return
            try:
                loop = asyncio.get_running_loop()
                results = await asyncio.wait_for(
                    loop.getaddrinfo(hostname, parsed.port or 443, type=0),
                    timeout=5,
                )
            except (OSError, TimeoutError) as error:
                raise ProviderError("Could not safely resolve the provider endpoint host.", 400) from error
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in results
            ]
        if not addresses or any(not address.is_global for address in addresses):
            raise ProviderError("Provider endpoints must resolve to public internet addresses.", 400)

    def _raise_provider_error(self, status_code: int) -> None:
        if self.config.protocol == "gemini" and status_code == 403:
            raise ProviderError(
                "Google says the Gemini API free tier is unavailable for this project. "
                "Check project eligibility and billing at https://aistudio.google.com/billing. "
                "Do not enable paid billing unless you approve it.",
                403,
            )
        raise safe_provider_error(status_code)

    @staticmethod
    def _decode_image_data(image_data: str) -> tuple[str, str]:
        header, encoded = image_data.split(",", 1)
        media_type = header[5:].split(";", 1)[0]
        return media_type, encoded

    async def list_models(self, client: httpx.AsyncClient) -> list[dict[str, str]]:
        await self._ensure_safe_endpoint()
        if self.config.protocol == "gemini":
            url = f"{self.base_url}/v1beta/models"
        else:
            url = f"{self.base_url}/models"
        response = await client.get(url, headers=self._headers())
        if response.status_code >= 400:
            self._raise_provider_error(response.status_code)
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderError("The provider returned an invalid model list.") from error
        items = payload.get("models", payload.get("data", []))
        models: list[dict[str, str]] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                model_id = item.get("name") or item.get("id")
                if not isinstance(model_id, str):
                    continue
                model_id = model_id.removeprefix("models/")
                methods = item.get("supportedGenerationMethods")
                if isinstance(methods, list) and "generateContent" not in methods:
                    continue
                display_name = item.get("displayName")
                models.append(
                    {
                        "id": model_id,
                        "name": display_name if isinstance(display_name, str) else model_id,
                    }
                )
        return models

    async def stream_chat(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        image_data: str | None = None,
    ) -> AsyncIterator[str]:
        if not self.config.api_key and self.config.provider_id != "ollama":
            raise ProviderError("This provider needs an API key.", 400)
        if self.config.protocol == "gemini":
            async for text in self._stream_gemini(client, messages, image_data):
                yield text
            return
        if self.config.protocol == "anthropic":
            async for text in self._stream_anthropic(client, messages, image_data):
                yield text
            return
        async for text in self._stream_openai_compatible(client, messages, image_data):
            yield text

    async def _stream_gemini(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        image_data: str | None = None,
    ) -> AsyncIterator[str]:
        await self._ensure_safe_endpoint()
        url = (
            f"{self.base_url}/v1beta/models/{quote(self.config.model_id, safe='')}"
            ":streamGenerateContent?alt=sse"
        )
        system_instruction = "\n\n".join(
            item["content"] for item in messages if item["role"] == "system"
        )
        contents = []
        conversation_messages = [item for item in messages if item["role"] != "system"]
        for index, item in enumerate(conversation_messages):
            parts: list[dict[str, str | dict[str, str]]] = [{"text": item["content"]}]
            if image_data and index == len(conversation_messages) - 1 and item["role"] == "user":
                media_type, encoded = self._decode_image_data(image_data)
                parts.append({"inlineData": {"mimeType": media_type, "data": encoded}})
            contents.append(
                {
                    "role": "model" if item["role"] == "assistant" else "user",
                    "parts": parts,
                }
            )
        payload: dict[str, object] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        async with client.stream(
            "POST",
            url,
            headers=self._headers(),
            json=payload,
        ) as response:
            if response.status_code >= 400:
                self._raise_provider_error(response.status_code)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    payload = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                candidates = payload.get("candidates", [])
                if candidates and isinstance(candidates[0], dict):
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if isinstance(part, dict) and isinstance(part.get("text"), str):
                            yield part["text"]

    async def _stream_openai_compatible(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        image_data: str | None = None,
    ) -> AsyncIterator[str]:
        await self._ensure_safe_endpoint()
        request_messages: list[dict[str, object]] = []
        for index, item in enumerate(messages):
            content: str | list[dict[str, object]] = item["content"]
            if image_data and index == len(messages) - 1 and item["role"] == "user":
                content = [
                    {"type": "text", "text": item["content"]},
                    {"type": "image_url", "image_url": {"url": image_data}},
                ]
            request_messages.append({"role": item["role"], "content": content})
        async with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.config.model_id,
                "messages": request_messages,
                "stream": True,
            },
        ) as response:
            if response.status_code >= 400:
                self._raise_provider_error(response.status_code)
            async for line in response.aiter_lines():
                if not line.startswith("data: ") or line[6:] == "[DONE]":
                    continue
                try:
                    payload = json.loads(line[6:])
                    choices = payload.get("choices", [])
                    delta = choices[0].get("delta", {}) if choices else {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        yield content
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and isinstance(item.get("text"), str):
                                yield item["text"]
                except (json.JSONDecodeError, IndexError, AttributeError, TypeError):
                    continue

    async def _stream_anthropic(
        self,
        client: httpx.AsyncClient,
        messages: list[dict[str, str]],
        image_data: str | None = None,
    ) -> AsyncIterator[str]:
        await self._ensure_safe_endpoint()
        system_instruction = "\n\n".join(
            item["content"] for item in messages if item["role"] == "system"
        )
        conversation_messages = [item for item in messages if item["role"] != "system"]
        request_messages: list[dict[str, object]] = []
        for index, item in enumerate(conversation_messages):
            content: str | list[dict[str, object]] = item["content"]
            if image_data and index == len(conversation_messages) - 1 and item["role"] == "user":
                media_type, encoded = self._decode_image_data(image_data)
                content = [
                    {"type": "text", "text": item["content"]},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    },
                ]
            request_messages.append({"role": item["role"], "content": content})
        payload: dict[str, object] = {
            "model": self.config.model_id,
            "max_tokens": 2048,
            "messages": request_messages,
            "stream": True,
        }
        if system_instruction:
            payload["system"] = system_instruction
        async with client.stream(
            "POST",
            f"{self.base_url}/messages",
            headers=self._headers(),
            json=payload,
        ) as response:
            if response.status_code >= 400:
                self._raise_provider_error(response.status_code)
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    payload = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "content_block_delta":
                    text = payload.get("delta", {}).get("text")
                    if isinstance(text, str):
                        yield text
