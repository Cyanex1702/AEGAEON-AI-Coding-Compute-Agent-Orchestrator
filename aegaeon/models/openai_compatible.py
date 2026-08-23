from __future__ import annotations

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

    @property
    def model_id(self) -> str:
        return self.model

    @property
    def provider_name(self) -> str:
        return "openai-compatible"

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "AEGAEON/0.2"}
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
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self.headers,
                )
                if response.status_code == 400 and "max_completion_tokens" in response.text:
                    payload["max_tokens"] = payload.pop("max_completion_tokens")
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=self.headers,
                    )
        except httpx.TimeoutException as exc:
            raise ProviderRequestError(
                f"Model request timed out after {self.timeout:.0f} seconds"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderRequestError(f"Could not reach model endpoint: {exc}") from exc
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
            async with httpx.AsyncClient(timeout=min(self.timeout, 10)) as client:
                response = await client.get(f"{self.base_url}/models", headers=self.headers)
            if response.is_success:
                return True, None
            return False, f"HTTP {response.status_code}: {response.text[:500]}"
        except httpx.HTTPError as exc:
            return False, str(exc)
