from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from aegaeon.models.errors import ProviderOutputError, ProviderRequestError
from aegaeon.models.provider import ModelProvider


class OpenAICompatibleProvider(ModelProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 180,
        max_output_tokens: int = 16_384,
        structured_output_mode: str = "auto",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.structured_output_mode = structured_output_mode
        self.client = httpx.AsyncClient(timeout=self.timeout)

    @property
    def model_id(self) -> str:
        return self.model

    @property
    def provider_name(self) -> str:
        return "openai-compatible"

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "AEGAEON/0.3"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def structured_formats(
        self, schema: dict[str, Any], schema_name: str
    ) -> list[dict[str, Any] | None]:
        json_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        }
        json_object = {"type": "json_object"}
        modes: dict[str, list[dict[str, Any] | None]] = {
            "auto": [json_schema, json_object, None],
            "json_schema": [json_schema],
            "json_object": [json_object],
            "prompt": [None],
        }
        return modes[self.structured_output_mode]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": self.max_output_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        try:
            response: httpx.Response | None = None
            for attempt in range(3):
                response = await self.client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self.headers,
                )
                if response.status_code == 400 and "max_completion_tokens" in response.text:
                    payload["max_tokens"] = payload.pop("max_completion_tokens")
                    response = await self.client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=self.headers,
                    )
                if response.status_code not in {429, 502, 503, 504} or attempt == 2:
                    break
                retry_after = response.headers.get("retry-after", "")
                try:
                    delay = min(30.0, max(0.5, float(retry_after)))
                except ValueError:
                    delay = min(8.0, 1.0 * 2**attempt)
                await asyncio.sleep(random.uniform(delay / 2, delay))
        except httpx.TimeoutException as exc:
            raise ProviderRequestError(
                f"Model request timed out after {self.timeout:.0f} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"Could not reach model endpoint: {exc}") from exc
        if response is None:
            raise ProviderRequestError("Model endpoint returned no response")
        if response.is_error:
            detail = response.text[:2_000]
            raise ProviderRequestError(
                f"Model endpoint returned HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
            )
        try:
            message = response.json()["choices"][0]["message"]
            if message.get("refusal"):
                raise ProviderOutputError(f"Model refused the request: {message['refusal']}")
            content = message.get("content")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderOutputError("Model endpoint returned an invalid chat completion") from exc
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
            if text.strip():
                return text
        raise ProviderOutputError("Model endpoint returned no text content")

    async def probe(self) -> tuple[bool, str | None]:
        try:
            response = await self.client.get(
                f"{self.base_url}/models",
                headers=self.headers,
                timeout=min(self.timeout, 10),
            )
            if response.is_success:
                return True, None
            return False, f"HTTP {response.status_code}: {response.text[:500]}"
        except httpx.HTTPError as exc:
            return False, str(exc)

    async def aclose(self) -> None:
        await self.client.aclose()
