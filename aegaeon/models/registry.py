from __future__ import annotations

from sqlalchemy import select

from aegaeon.database.models import ModelRecord
from aegaeon.database.session import Database
from aegaeon.protocol.schemas import ModelRead

DEFAULT_MODELS = [
    {
        "id": "code-7b",
        "name": "Code Model 7B",
        "type": "llm",
        "provider": "openai-compatible",
        "capabilities": ["code", "reasoning"],
        "minimum_vram_mb": 6000,
        "context_length": 32768,
        "status": "available in demo",
    },
    {
        "id": "reasoning-32b",
        "name": "Reasoning 32B",
        "type": "llm",
        "provider": "openai-compatible",
        "capabilities": ["code", "reasoning", "review"],
        "minimum_vram_mb": 24000,
        "context_length": 65536,
        "status": "not connected",
    },
    {
        "id": "vision-model",
        "name": "Vision Model",
        "type": "vision",
        "provider": "custom-http",
        "capabilities": ["vision", "image"],
        "minimum_vram_mb": 12000,
        "context_length": 16384,
        "status": "not connected",
    },
]


class ModelRegistry:
    def __init__(self, database: Database) -> None:
        self.database = database

    def seed(self) -> None:
        with self.database.session() as session:
            for item in DEFAULT_MODELS:
                if not session.get(ModelRecord, item["id"]):
                    session.add(ModelRecord(**item))

    def list(self) -> list[ModelRead]:
        with self.database.session() as session:
            return [
                ModelRead.model_validate(item)
                for item in session.scalars(select(ModelRecord).order_by(ModelRecord.name))
            ]
