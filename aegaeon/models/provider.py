from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from aegaeon.models.errors import ProviderOutputError, ProviderRequestError

StructuredResult = TypeVar("StructuredResult", bound=BaseModel)


class ModelProvider(ABC):
    @property
    @abstractmethod
    def model_id(self) -> str:
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> str:
        """Return model text without coupling the orchestrator to one vendor."""

    @abstractmethod
    def structured_formats(
        self, schema: dict[str, Any], schema_name: str
    ) -> list[dict[str, Any] | None]:
        """Return provider-supported response formats in preferred order."""

    async def complete_structured(
        self,
        messages: list[dict[str, str]],
        result_type: type[StructuredResult],
        *,
        schema_name: str,
        temperature: float = 0.1,
        retries: int = 2,
    ) -> StructuredResult:
        schema = strict_json_schema(result_type.model_json_schema())
        formats = self.structured_formats(schema, schema_name)
        last_error: Exception | None = None
        for response_format in formats:
            current_messages = list(messages)
            if response_format is None:
                current_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Return JSON only. It must match this JSON Schema exactly:\n"
                            + json.dumps(schema, separators=(",", ":"))
                        ),
                    }
                )
            for attempt in range(retries + 1):
                try:
                    output = await self.complete(
                        current_messages,
                        response_format=response_format,
                        temperature=temperature,
                    )
                except ProviderRequestError as exc:
                    last_error = exc
                    if exc.status_code in {400, 404, 415, 422}:
                        break
                    raise
                try:
                    return result_type.model_validate_json(extract_json(output))
                except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                    last_error = exc
                    if attempt >= retries:
                        break
                    current_messages.extend(
                        [
                            {"role": "assistant", "content": output[:20_000]},
                            {
                                "role": "user",
                                "content": (
                                    "The previous result failed schema validation. "
                                    "Correct it and return "
                                    f"JSON only. Validation error: {str(exc)[:2_000]}"
                                ),
                            },
                        ]
                    )
        raise ProviderOutputError(
            f"Model output did not match {schema_name}: {last_error or 'unknown error'}"
        )


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make Pydantic JSON Schema compatible with strict structured-output APIs."""

    result = deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                properties = node.get("properties", {})
                node["additionalProperties"] = False
                node["required"] = list(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


def extract_json(output: str) -> str:
    text = output.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.I)
    if fenced:
        text = fenced.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    raise ValueError("model did not return a JSON object")
