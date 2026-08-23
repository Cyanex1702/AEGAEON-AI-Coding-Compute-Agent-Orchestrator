from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from aegaeon.models.provider import ModelProvider, extract_json, strict_json_schema


class TinyResult(BaseModel):
    value: str
    labels: list[str]


class QueueProvider(ModelProvider):
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "test-model"

    @property
    def provider_name(self) -> str:
        return "test"

    def structured_formats(
        self, schema: dict[str, Any], schema_name: str
    ) -> list[dict[str, Any] | None]:
        return [{"type": "json_schema", "schema": schema, "name": schema_name}]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> str:
        self.calls += 1
        return self.outputs.pop(0)


def test_strict_schema_closes_objects_and_requires_fields() -> None:
    schema = strict_json_schema(TinyResult.model_json_schema())
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["value", "labels"]


def test_extract_json_accepts_fences_and_prefixes() -> None:
    assert extract_json('```json\n{"value":"ok"}\n```') == '{"value":"ok"}'
    assert extract_json('Result: {"value":"ok"}') == '{"value":"ok"}'


@pytest.mark.asyncio
async def test_structured_completion_repairs_invalid_output() -> None:
    provider = QueueProvider(
        [
            '{"value": 3, "labels": []}',
            '```json\n{"value":"corrected","labels":["safe"]}\n```',
        ]
    )
    result = await provider.complete_structured(
        [{"role": "user", "content": "Return a tiny result"}],
        TinyResult,
        schema_name="tiny_result",
        retries=1,
    )
    assert result.value == "corrected"
    assert provider.calls == 2
